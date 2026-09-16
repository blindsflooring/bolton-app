# -*- coding: utf-8 -*-
"""
Ask Bolton — natural-language query agent (confirmed Sept 2026).

Any question, in plain English, answered from Bolton's real current
data. Claude is given a description of the data it is allowed to see,
writes a real SQL query, and that query is run against a connection
that CANNOT WRITE. No developer pre-builds a question first, which is
the point: Bolton's data keeps accumulating, and a fixed question list
caps the tool's usefulness at whatever somebody thought to build.

That flexibility is exactly why the safety here is architectural rather
than instructional. Four independent layers, each of which would have
to fail on its own for anything to go wrong:

  1. THE CONNECTION CANNOT WRITE. Queries run on a separate engine
     opened read-only at the driver level - a Postgres role with only
     SELECT granted, or SQLite's own mode=ro URI. A malformed, a
     hallucinated or a deliberately manipulated query still cannot
     insert, update, delete or alter anything, because the database
     refuses it. This module holds no handle to the read-write engine
     at all. In production the agent REFUSES TO RUN unless a read-only
     URL is configured - falling back to the ordinary connection would
     silently defeat the whole guarantee, so it is treated as a
     misconfiguration rather than papered over.

  2. THE ALLOW-LIST DECIDES WHAT EXISTS. Validation is a whitelist, not
     a blacklist: every identifier in the generated SQL must appear in
     the set of tables and columns this ROLE may see at this PHASE.
     Anything else - a table name, a column, a function, a typo - is
     refused by name. A blacklist has to anticipate the attack; a
     whitelist only has to know what is permitted, so a bypass nobody
     thought of still fails. This is what makes a Sales user's question
     structurally incapable of touching Financial Records: the string
     `financialstatement` is not in their allow-list, however the
     question was phrased, and a query mentioning it never reaches the
     database.

  3. THE SCHEMA THE MODEL SEES IS ROLE-FILTERED. Claude is not told
     owner-only tables exist when a Sales user is asking. That is
     defence in depth - layer 2 is the guarantee - but it also means
     the model is never invited to reach for something it cannot have,
     which is the difference between a refusal and a wrong answer.

  4. ONE STATEMENT, SCOPED AND BOUNDED. Single statement, no comments,
     must begin SELECT or WITH, every table carries a bound tenant
     predicate, a LIMIT is enforced, and the database is given a
     statement timeout. A query cannot run away even if it is valid.

WHO SEES WHAT (confirmed Sept 2026, superseding the earlier decision
that Sales and Admin would get the historical import too):

  Sales and Admin - current jobs in the Order Index only. No prior
  years, no Financial Records. This is what they already see on the
  Order Index screen, so Ask Bolton gives them no reach they did not
  already have; it just stops them hunting across screens for it.

  Owner - all of it: current jobs, the imported history, and (at phase
  3) Financial Records.

Enforced in TWO places that would both have to fail: the catalogue's own
`roles`, which decides what the validator will accept and what the model
is even told exists, and - the real guarantee - a SEPARATE DATABASE ROLE
per connection, so a Sales question runs on a login that holds no
privilege on the historical or financial tables at all.

ASK_BOLTON_PHASE remains as a blast-radius control on top of that:
Financial Records stay at phase 3 and are not reachable by anyone until
it is raised.

WHAT THIS MODULE WILL NOT DO. It will not answer from the model's own
knowledge. Every figure comes from a row the database returned, and the
rows are shown alongside the answer, so a confident sentence can always
be checked against them. Where the data cannot answer the question, it
says so rather than approximating.
"""
import json
import os
import re
import urllib.error
import urllib.request

from sqlalchemy import create_engine, text

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"

# Its own key where one is provided, the shared one otherwise.
#
# Deliberately a FALLBACK rather than a hard requirement, unlike the
# read-only database URL above. That refuses to fall back because doing
# so would defeat a safety guarantee; this is an operational preference
# and falling back costs nothing but visibility, so the feature works on
# day one and can be separated whenever it suits.
#
# Why separating it is worth doing anyway: Anthropic reports usage per
# key, so a key of its own makes Ask Bolton's spend visible without any
# instrumentation; it can carry its own spend cap and rate limit in its
# own Console workspace, so a runaway or abused query loop cannot starve
# the price-sheet import; and revoking it turns off exactly this feature
# and nothing else. It also has a genuinely different exposure profile -
# the price-sheet import is owner-only and reads a file Burgert chose,
# while this is the first thing in Bolton where Sales and Admin can
# spend money on free text, many times a day.
#
# Read at call time rather than frozen at import, so which key is in use
# is a fact about now rather than about whenever the process started.
ASK_KEY_VARS = ("ASK_BOLTON_ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY")


def api_key():
    """(key, which environment variable it came from). ("", None) when
    neither is set."""
    for name in ASK_KEY_VARS:
        value = os.environ.get(name, "").strip()
        if value:
            return value, name
    return "", None

# Writing correct SQL against a described schema is the step where being
# wrong actually costs something - a bad query is a wrong answer that
# looks right. It gets the stronger model and adaptive thinking.
# Explaining figures that are already on screen next to the sentence is
# not that job, and runs on Haiku.
SQL_MODEL = "claude-sonnet-5"
EXPLAIN_MODEL = "claude-haiku-4-5"

SQL_TIMEOUT_SECONDS = 60
EXPLAIN_TIMEOUT_SECONDS = 30

MAX_QUESTION_CHARS = 500
MAX_ROWS = 500
# Postgres only; SQLite gets a Python-side interrupt instead (see below).
STATEMENT_TIMEOUT_MS = 10000

# One retry, and only for a query the VALIDATOR rejected - the refusal
# reason is handed back so the model can correct a column name or a
# missing tenant predicate. A query the database itself rejected is not
# retried: that is either a real error worth reporting or an attempt to
# do something the read-only connection refused, and quietly having
# another go at it is the wrong instinct in both cases.
MAX_REPAIR_ATTEMPTS = 1


# =====================================================================
# Phases
# =====================================================================
def current_phase():
    try:
        return max(1, min(3, int(os.environ.get("ASK_BOLTON_PHASE", "1"))))
    except ValueError:
        return 1


# What this role can currently reach, in words, for the screen and for
# an honest refusal. Derived from the catalogue rather than written out,
# so it can never describe access that allowed_tables() does not actually
# grant - the two drifting apart is how a refusal ends up contradicting
# the sentence above it.
_TABLE_GROUPS = [
    ("current jobs in the Order Index", {"quote", "quotelineitem", "quotepayment",
                                         "ordersheet", "paymentfollowup"}),
    ("the imported Order Index history (2017-2025)", {"historicalyeartotal",
                                                      "historicalmonthtotal"}),
    ("the annual Financial Records", {"financialstatement"}),
]


def data_available(role, phase=None):
    names = {t["table"] for t in allowed_tables(role, phase)}
    have = [label for label, group in _TABLE_GROUPS if group & names]
    if not have:
        return "nothing yet"
    if len(have) == 1:
        return have[0]
    return "%s and %s" % (", ".join(have[:-1]), have[-1])


# =====================================================================
# The data catalogue.
#
# This is the whole of what the agent can ever see. A table absent from
# here does not exist as far as this module is concerned - not hidden,
# not discouraged, absent: its name is not in any allow-list, so a query
# naming it is refused before the database is opened.
#
# `roles` and `phase` are enforced in code (allowed_tables() below),
# never merely described to the model.
# =====================================================================
ALL_ROLES = ("owner", "sales", "admin")
OWNER = ("owner",)

# Roles that may only ever see their OWN jobs.
#
# Set once at import by main.py from its own PERSON_SCOPED_ROLES, so
# there is exactly one definition of who is restricted and this cannot
# drift from the rest of the app. Defaulted rather than left empty: a
# module imported without that call must fail CLOSED, not hand a rep
# everybody's jobs.
_PERSON_SCOPED = {"sales"}


def set_person_scoped_roles(roles):
    """Called by main.py at import with its own PERSON_SCOPED_ROLES."""
    global _PERSON_SCOPED
    _PERSON_SCOPED = set(roles)


def is_person_scoped(role):
    return role in _PERSON_SCOPED

CATALOGUE = [
    {
        "table": "historicalyeartotal",
        "phase": 1,
        "roles": OWNER,
        "what": "One row per fiscal year of imported Order Index history, 2017 to 2025. "
                "The fiscal year runs March to February and is named for the year it STARTS in, "
                "so fiscal_year 2017 means March 2017 to February 2018.",
        "columns": {
            "fiscal_year": "Integer. The year the fiscal year starts in.",
            "total_sales": "Turnover for the year, from the individual order rows. This is the real figure to use.",
            "total_cost": "Cost of sales for the year.",
            "gross_profit": "total_sales minus total_cost.",
            "margin_pct": "gross_profit / total_sales, as a fraction (0.36 = 36%).",
            "order_count": "How many orders that year.",
            "printed_sales": "AUDIT ONLY. The source spreadsheet's own printed annual figure, which is "
                             "known to disagree with the order rows in some years. Never use as the headline number.",
            "printed_cost": "AUDIT ONLY, as printed_sales.",
            "printed_gross_profit": "AUDIT ONLY, as printed_sales.",
            "monthly_coverage_pct": "What share of the year's sales sit on an order row with a readable date, "
                                    "as a fraction. Below 1.0 means the month-by-month breakdown for that year is incomplete.",
            "monthly_complete": "Boolean. False where the monthly breakdown is too incomplete to compare fairly.",
            "covers_until": "Date, usually NULL. Set only on the one fiscal year split between the old spreadsheet "
                            "and Bolton: imported figures cover up to but NOT INCLUDING this date.",
            "notes": "Free text about the year.",
            "source_file": "Which spreadsheet the year was imported from.",
        },
    },
    {
        "table": "historicalmonthtotal",
        "phase": 1,
        "roles": OWNER,
        "what": "One row per fiscal month of an imported year, built by summing order rows that carry a "
                "readable date. Incomplete for some years - check historicalyeartotal.monthly_complete "
                "before comparing months across years.",
        "columns": {
            "fiscal_year": "Integer, joins to historicalyeartotal.fiscal_year.",
            "fiscal_month_index": "0-based FROM MARCH: 0 = March, 1 = April, ... 11 = February.",
            "month_label": "Short month name, e.g. 'Mar'.",
            "sales": "Turnover for that month.",
            "cost": "Cost for that month.",
            "gross_profit": "sales minus cost.",
            "order_count": "How many orders that month.",
        },
    },
    {
        "table": "quote",
        "phase": 1,
        "roles": ALL_ROLES,
        "what": "One row per quote or job in Bolton, from 1 September 2026 onward. "
                "This table carries DATES and STATUS, not money. The quote total, the "
                "deposit due, the balance and the VAT are all worked out by Bolton at "
                "the moment it draws the screen, and are not stored here - so they are "
                "not available to you and cannot be rebuilt from these columns. For any "
                "question about money, use quotepayment, which holds what really "
                "arrived. "
                "A quote becomes a job when it is accepted.",
        "columns": {
            "client_name": "Who the job is for.",
            "job_number": "Assigned once at acceptance, never reused. NULL before acceptance.",
            "workflow_status": "One of 'quoted', 'accepted', 'scheduled', 'completed'. "
                               "Not installed yet means 'accepted' or 'scheduled'.",
            "accepted_at": "Timestamp the quote was won. NULL if never accepted. "
                           "This is the date turnover is counted on.",
            "declined_at": "Timestamp the client turned it down. NULL otherwise. "
                           "A declined quote keeps workflow_status 'quoted', so ALWAYS exclude "
                           "declined_at IS NOT NULL from any figure.",
            "is_price_check": "Boolean. A price check is not a real tracked job - always exclude it.",
            "installation_date": "Booked or tentative installation date.",
            "installation_confirmed_date": "Set only when the booking is confirmed. A date in "
                                           "installation_date without this is tentative.",
            "invoice_sent_date": "When the invoice went out.",
            "deposit_paid_date": "When the deposit landed. NULL if not paid.",
            "final_payment_date": "Set only when the job is FULLY paid.",
                    "branch": "Which branch the job belongs to.",
            "sales_owner": "Username of the rep the job is attributed to.",
            "installer_team": "Who is fitting it.",
            "on_hold_reason": "Free text. Non-empty means the job is paused.",
                        "materials_ordered": "A legacy hand-ticked checkbox. Do NOT trust it - whether materials were really ordered is decided by a placed ordersheet row.",
        },
    },
    {
        "table": "quotelineitem",
        "phase": 1,
        "roles": ALL_ROLES,
        "what": "The lines on a quote. Join to quote via quote_id.",
        "columns": {
            "quote_id": "Joins to quote.id.",
            "category": "'flooring', 'blinds', 'trim', 'skirting', 'stairwell' or 'misc'.",
            "product_name": "Snapshot of the product name as quoted.",
            "colour": "Snapshot of the colour as quoted. This is what gets ordered.",
            "quantity_m2": "Square metres, on flooring lines.",
            "length_m": "Linear metres, on trim and skirting lines.",
            "boxes_needed": "Boxes to order, on material flooring lines.",
            "bags_allowed": "Bags of screed allowed, on screed lines. Greater than 0 identifies a screed line.",
                        "flooring_pricing_type": "'material' or 'screed', on flooring lines.",
            "trim_sub_category": "'skirting', 'stair_nose', 'reducer', 'carpet_strip' or 'quarter_round'.",
        },
    },
    {
        "table": "quotepayment",
        "phase": 1,
        "roles": ALL_ROLES,
        "what": "THE ONLY SOURCE OF MONEY FIGURES. Money actually received against a job. "
                "Every amount here is a real recorded receipt, not a calculation. "
                "A job's payments are a LIST, not a "
                "fixed deposit/final pair - a client can pay in several tranches. Join to "
                "quote via quote_id.",
        "columns": {
            "quote_id": "Joins to quote.id.",
            "amount": "What actually arrived. Never a percentage of anything.",
            "paid_date": "The date the money landed.",
            "method": "EFT, Cash, Card, Yoco - free text.",
            "payment_type": "'deposit', 'final' or 'extra'.",
        },
    },
    {
        "table": "ordersheet",
        "phase": 1,
        "roles": ALL_ROLES,
        "what": "A supplier order raised for a job. A job with no ordersheet row, or only "
                "draft ones, has NOT had its materials ordered - which is what 'still needs "
                "ordering' means.",
        "columns": {
            "quote_id": "Joins to quote.id.",
            "supplier": "Who the order went to.",
            "status": "'draft' or 'placed'. Only 'placed' counts as actually ordered.",
            "created_at": "When the sheet was raised.",
        },
    },
    {
        "table": "paymentfollowup",
        "phase": 1,
        "roles": ALL_ROLES,
        "what": "An append-only log of payment chases. A job can be chased several times, so "
                "there can be many rows per job.",
        "columns": {
            "quote_id": "Joins to quote.id.",
            "follow_up_date": "When the chase happened.",
            "notes": "What was said.",
        },
    },
    {
        "table": "financialstatement",
        "phase": 3,
        "roles": OWNER,
        "what": "OWNER ONLY. Figures taken off the annual financial statements prepared by the "
                "accountant. One row per fiscal year per entity.",
        "columns": {
            "fiscal_year": "The year the statement's fiscal year starts in.",
            "entity_name": "Which legal entity the statement covers.",
            "status": "'final' or 'draft'. A draft figure must never be presented as signed off.",
            "statement_type": "'audited', 'reviewed', 'compiled' or 'management'.",
            "revenue": "Turnover per the statement.",
            "cost_of_sales": "Cost of sales per the statement.",
            "gross_profit": "revenue minus cost_of_sales.",
            "operating_expenses": "Total overheads.",
            "depreciation": "Depreciation for the year.",
            "finance_costs": "Interest and finance charges.",
            "net_profit": "Signed - negative is a loss and must be shown as one.",
            "total_assets": "Balance sheet total assets.",
            "total_liabilities": "Balance sheet total liabilities.",
            "total_equity": "Balance sheet equity.",
            "figures_entered_at": "NULL means nobody has typed this year's figures in yet.",
        },
    },
]


def allowed_tables(role, phase=None):
    """The tables this role may see at this phase. The single source of
    truth for both the schema shown to the model and the validator - so
    the two can never disagree about what is permitted."""
    phase = current_phase() if phase is None else phase
    return [t for t in CATALOGUE if t["phase"] <= phase and role in t["roles"]]


def schema_prompt(role, phase=None):
    tables = allowed_tables(role, phase)
    if not tables:
        return ""
    out = []
    if is_person_scoped(role):
        out.append("SCOPE: you may only see YOUR OWN jobs. Every query must read `quote` "
                   "and filter it with `quote.sales_owner = :sales_owner`, joining any "
                   "other table through quote.")
    for t in tables:
        cols = "\n".join("    %s - %s" % (c, d) for c, d in t["columns"].items())
        out.append("TABLE %s\n  %s\n  Columns:\n%s" % (t["table"], t["what"], cols))
    return "\n\n".join(out)


# =====================================================================
# The read-only connection.
# =====================================================================
# TWO CONNECTIONS, CHOSEN BY WHO IS ASKING.
#
# This is the whole of the access boundary, and it is deliberately not
# written in Python. The Owner's questions run on a connection granted
# everything; Sales and Admin run on one granted ONLY the live job
# tables. A total failure of the validator still cannot let Ryno read a
# historical year or a financial statement, because his connection holds
# no privilege on those tables. Code can have bugs; a GRANT that was
# never made cannot.
#
# Selected by the role from get_current_role(), so an Owner previewing as
# Sales is answered on the Sales connection - a preview that still read
# owner-only tables would not be a preview.
CONNECTION_FOR_ROLE = {
    "owner": "ASK_BOLTON_DATABASE_URL",
    "sales": "ASK_BOLTON_LIVE_DATABASE_URL",
    "admin": "ASK_BOLTON_LIVE_DATABASE_URL",
}

_ENGINES = {}
_ENGINE_ERRORS = {}


def _readonly_url(role):
    """The read-only URL for this role, or None with a reason.

    In production each variable must point at a Postgres login granted
    SELECT on exactly that role's tables and nothing else. There is
    deliberately NO fallback to DATABASE_URL, and no fallback from the
    live connection to the owner one - either would silently hand a Sales
    question a connection that reads more than Sales may see, which is
    the precise thing this design exists to prevent.

    SQLite is the one case needing no configuration, because the driver
    itself takes a mode=ro URI. Local development then has real write
    protection but NOT the per-table boundary, because SQLite has no
    per-table privileges at all. self_check() says so plainly rather
    than implying a guarantee that is not there.
    """
    var = CONNECTION_FOR_ROLE.get(role)
    if var is None:
        return None, "Ask Bolton isn't available to your role."
    explicit = os.environ.get(var, "").strip()
    if explicit:
        return explicit, None

    main_url = os.environ.get("DATABASE_URL", "sqlite:///./bolton.db")
    if main_url.startswith("sqlite"):
        path = main_url.split("///", 1)[1] if "///" in main_url else main_url
        if path.startswith("file:"):
            return main_url, None
        return "sqlite:///file:%s?mode=ro&uri=true" % path.replace(chr(92), "/"), None

    return None, (
        "Ask Bolton is not configured for your role. It needs %s set to a READ-ONLY "
        "database user - a Postgres role granted SELECT on only the tables that role "
        "may see, and nothing else. It deliberately will not fall back to any other "
        "connection, because a query the AI wrote must never reach a connection that "
        "can read more than the person asking may see." % var)


def readonly_engine(role):
    if role in _ENGINES:
        return _ENGINES[role], None
    if role in _ENGINE_ERRORS:
        return None, _ENGINE_ERRORS[role]
    url, problem = _readonly_url(role)
    if problem:
        _ENGINE_ERRORS[role] = problem
        return None, problem
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    _ENGINES[role] = create_engine(url, echo=False, connect_args=connect_args,
                                   pool_pre_ping=not url.startswith("sqlite"))
    return _ENGINES[role], None


def reset_engine_for_tests():
    """Tests point the URLs at a fixture and need the cached engines
    rebuilt. Named so nothing production-facing calls it by accident."""
    _ENGINES.clear()
    _ENGINE_ERRORS.clear()


def dialect(role="owner"):
    url, _ = _readonly_url(role)
    return "sqlite" if (url or "").startswith("sqlite") else "postgresql"


# Probes for self_check(), derived from the catalogue rather than listed.
#
# A table this role may never see must be BLOCKED BY THE DATABASE, not
# merely absent from its allow-list - that is the whole point of giving
# each role its own login. Expectation comes from the catalogue's own
# `roles`, so adding a table there gets it probed automatically and the
# two can never disagree about what should be reachable.
#
# Entitlement here is deliberately phase-independent: the GRANT reflects
# what a role may EVER see, and ASK_BOLTON_PHASE is an app-level control
# layered on top of it, not a second thing to re-grant.
def _self_check_probes(role):
    probes = []
    for entry in CATALOGUE:
        table = entry["table"]
        may_read = role in entry["roles"]
        probes.append((
            "read %s%s" % (table, "" if may_read else " (must be blocked)"),
            "SELECT count(*) FROM %s" % table,
            not may_read,          # must_be_blocked
            not may_read,          # postgres_only: SQLite has no per-table grants
        ))
    # The write probe carries WHERE 1=0 on purpose. If the grant is wrong
    # and the statement is NOT blocked, it still matches no rows and
    # changes nothing - the probe reports a failure instead of causing
    # one. It is rolled back regardless.
    readable = [e["table"] for e in CATALOGUE if role in e["roles"]]
    target = readable[0] if readable else "quote"
    probes.append((
        "UPDATE %s (must be blocked)" % target,
        "UPDATE %s SET tenant_id = tenant_id WHERE 1=0" % target,
        True, False))
    return probes


_APP_ENGINE = [None]


def _app_count(sql):
    """What the APP's own connection sees for the same probe, or None.

    Used for one comparison and nothing else: a read-only role that holds
    SELECT but is filtered to zero rows by RLS looks identical to a role
    reading an empty table, and the difference is the whole boundary. The
    only way to tell them apart is to ask a connection that is not
    filtered. Read-only use, inside a transaction that is always rolled
    back; this is a measuring stick, never a path a question can travel.
    """
    if _APP_ENGINE[0] is None:
        url = os.environ.get("DATABASE_URL", "").strip()
        if not url:
            return None
        try:
            _APP_ENGINE[0] = create_engine(url, pool_pre_ping=True)
        except Exception:
            _APP_ENGINE[0] = False
    if not _APP_ENGINE[0]:
        return None
    try:
        with _APP_ENGINE[0].connect() as conn:
            trans = conn.begin()
            try:
                return conn.execute(text(sql)).scalar()
            finally:
                trans.rollback()
    except Exception:
        return None


def self_check(role="owner"):
    """Prove the boundary against the REAL connection, as the real role.

    The red-team suite in tests/ exercises the validator (layer 2) and
    runs against a local fixture, so it is connection-independent and
    says nothing about production. This says the other half: whether the
    database itself would refuse, which is layer 1 and the layer that has
    to hold when every other one has failed.

    IT DELIBERATELY DOES NOT SET `SET TRANSACTION READ ONLY` around the
    write probe. That guard is layer 4 and would block the write on its
    own, which would produce a confident pass while the underlying GRANT
    was wide open - testing our own guard instead of the thing we came to
    test. The probe runs bare, so a pass means the DATABASE refused.

    Each probe gets its own connection: in Postgres a failed statement
    aborts the transaction, so sharing one would make every probe after
    the first failure report a meaningless error.

    connected_as and is_superuser are reported because of a specific,
    realistic way this goes wrong. Supabase's pooler expects the username
    as `<role>.<project-ref>`, and the dashboard pre-fills
    `postgres.<project-ref>`. Change only the password in that string and
    the agent connects as the postgres SUPERUSER: every validator test
    still passes, nothing is blocked, and the read-only guarantee is gone
    with no visible symptom. That is the single most valuable line in
    this report.
    """
    engine, problem = readonly_engine(role)
    if problem:
        return {"ok": False, "role": role, "configured": False,
                "problem": problem, "checks": []}

    url = str(engine.url)
    is_pg = not url.startswith("sqlite")
    report = {"ok": True, "role": role, "configured": True,
              "backend": "postgresql" if is_pg else "sqlite",
              # True only where the whole boundary can actually be proven at
              # the connection. False on SQLite, and the report says why.
              "full_boundary_enforceable": is_pg,
              "connected_as": None, "is_superuser": None, "checks": []}

    if is_pg:
        try:
            with engine.connect() as conn:
                report["connected_as"] = conn.execute(text("SELECT current_user")).scalar()
                report["is_superuser"] = (
                    conn.execute(text("SELECT current_setting('is_superuser')")).scalar() == "on")
        except Exception as e:
            report["ok"] = False
            report["problem"] = "Could not reach the read-only database: %s" % str(e).split("\n")[0][:200]
            return report

    if not is_pg:
        report["note"] = (
            "Running on SQLite. Writes are blocked at the driver, but SQLite has no "
            "per-table privileges, so the table boundary here rests on the validator "
            "alone and cannot be proven at the connection. Only a Postgres deployment "
            "can confirm the full boundary — run this there before trusting it.")

    for label, sql, must_block, postgres_only in _self_check_probes(role):
        entry = {"check": label, "must_be_blocked": must_block}
        if postgres_only and not is_pg:
            entry.update({"blocked": None, "pass": None,
                          "skipped": "not enforceable on SQLite — Postgres GRANT only"})
            report["checks"].append(entry)
            continue
        try:
            with engine.connect() as conn:
                trans = conn.begin()
                try:
                    result = conn.execute(text(sql))
                    entry["blocked"] = False
                    if not must_block:
                        try:
                            entry["value"] = result.scalar()
                        except Exception:
                            entry["value"] = None
                finally:
                    trans.rollback()
        except Exception as e:
            entry["blocked"] = True
            entry["reason"] = str(e).split("\n")[0][:160]
        entry["pass"] = (entry["blocked"] == must_block)

        # Not blocked, but nothing comes back. Either the table really is
        # empty, or the role has SELECT and no RLS policy and is filtered
        # to zero - which reads to the person asking as "there is nothing
        # recorded for that job", a confident answer that happens to be
        # false. Only a connection that is not filtered can tell those
        # two apart, so go and ask one rather than reporting a pass.
        if entry["pass"] and not must_block and entry.get("value") == 0:
            app_sees = _app_count(sql)
            if app_sees:
                entry["app_sees"] = app_sees
                entry["pass"] = False
                entry["reason"] = (
                    "readable but returns NO ROWS - the app sees %d. The role has "
                    "SELECT and no row-level-security policy, so every row is "
                    "filtered out. Questions about this table will be answered "
                    "'there is nothing recorded', which is false." % app_sees)
            elif app_sees == 0:
                entry["note"] = "empty for everyone, not filtered"

        if not entry["pass"]:
            report["ok"] = False
        report["checks"].append(entry)

    # A superuser passes nothing, whatever the probes said - it is simply
    # not being stopped by anything except our own code.
    if report["is_superuser"]:
        report["ok"] = False
        report["problem"] = (
            "Connected as a SUPERUSER (%s). The read-only guarantee is not in force: this role "
            "can do anything, and only Bolton's own validator is standing in the way. On the "
            "Supabase pooler the username must be `ask_bolton.<project-ref>`, not "
            "`postgres.<project-ref>` - check %s." % (report["connected_as"],
                                                       CONNECTION_FOR_ROLE.get(role, "the URL")))
    return report


# =====================================================================
# Validation — the allow-list.
# =====================================================================
#
# A whitelist rather than a blacklist, and that is the entire point. A
# blacklist has to anticipate every way of writing something dangerous;
# a whitelist only has to know what is allowed, so a bypass nobody
# thought of is refused by default rather than permitted by oversight.
#
# Kept deliberately modest. A legitimate query using a function that is
# not listed gets refused with the token named, the model is told, and
# it tries once more - which is a good failure. The bad failure is the
# other direction.
SQL_KEYWORDS = {
    "select", "from", "where", "and", "or", "not", "in", "is", "null", "as",
    "join", "inner", "left", "right", "full", "outer", "on", "using",
    "group", "by", "order", "having", "limit", "offset", "distinct",
    "case", "when", "then", "else", "end", "between", "like", "ilike",
    "asc", "desc", "union", "all", "with", "cast", "exists",
    "true", "false", "over", "partition", "rows", "range", "preceding",
    "following", "unbounded", "current", "row", "nulls", "first", "last",
}
SQL_FUNCTIONS = {
    "sum", "avg", "count", "min", "max", "round", "abs", "coalesce",
    "nullif", "greatest", "least", "length", "upper", "lower", "trim",
    "cast", "concat", "substr", "replace", "floor", "ceil", "ceiling",
    "power", "sqrt", "rank", "dense_rank", "row_number", "lag", "lead",
    # Type names, which appear as bare identifiers inside CAST(...)
    "int", "integer", "float", "real", "numeric", "decimal", "text",
    "varchar", "boolean", "date", "bigint", "double", "precision",
}
# A second net under the whitelist. Everything here would already be
# refused as an unknown identifier; listed anyway so that if the
# whitelist is ever widened carelessly, the things that must never be
# reachable are still named explicitly and still refused.
FORBIDDEN = {
    "insert", "update", "delete", "drop", "alter", "create", "truncate",
    "grant", "revoke", "attach", "detach", "pragma", "vacuum", "copy",
    "call", "do", "execute", "prepare", "merge", "replace_into", "upsert",
    "returning", "into", "set", "commit", "rollback", "savepoint",
    "pg_sleep", "pg_read_file", "pg_read_binary_file", "pg_ls_dir",
    "lo_import", "lo_export", "dblink", "dblink_exec", "current_setting",
    "set_config", "load_extension", "readfile", "writefile", "system",
    "information_schema", "pg_catalog", "sqlite_master", "sqlite_schema",
    "pg_class", "pg_tables", "pg_user", "pg_shadow", "pg_authid",
}

_TOKEN_RE = re.compile(r"""
    (?P<string>'(?:[^']|'')*')     # single-quoted literal, '' escape
  | (?P<param>:[A-Za-z_][A-Za-z0-9_]*)
  | (?P<number>\b\d+(?:\.\d+)?\b)
  | (?P<ident>[A-Za-z_][A-Za-z0-9_]*)
  | (?P<op>[(),.*<>=+\-/%|!\[\]]+)
  | (?P<ws>\s+)
""", re.VERBOSE)


class SqlRefused(Exception):
    """Raised with a message written for the MODEL to correct, not for
    the end user - it names the offending token so a retry can fix it."""


def _tokenize(sql):
    out, pos = [], 0
    while pos < len(sql):
        m = _TOKEN_RE.match(sql, pos)
        if not m:
            raise SqlRefused("Unrecognised character at position %d: %r" % (pos, sql[pos]))
        pos = m.end()
        if m.lastgroup != "ws":
            out.append((m.lastgroup, m.group()))
    return out


def _defined_names(tokens, table_names):
    """Names the query itself introduces - CTE names, column aliases and
    table aliases - which are legal to use later even though they are
    not in the schema.

    Collected in their own pass, and deliberately consulted LAST in the
    main pass below. That order closes a real bypass: without it,
    `SELECT 1 AS financialstatement` would register that name as an
    alias and a later reference to it would sail through the check that
    is supposed to stop exactly that word.
    """
    names = set()
    for i, (kind, val) in enumerate(tokens):
        if kind != "ident":
            continue
        prev = tokens[i - 1] if i else None
        nxt = tokens[i + 1] if i + 1 < len(tokens) else None
        prev_ident = prev[1].lower() if prev and prev[0] == "ident" else None
        next_ident = nxt[1].lower() if nxt and nxt[0] == "ident" else None
        # `... AS x` - a column alias or the name of a CTE's output
        if prev_ident == "as":
            names.add(val.lower())
        # `x AS (...)` - the name of a common table expression
        if next_ident == "as":
            names.add(val.lower())
        # `historicalyeartotal h` - a table alias with AS left out
        if prev_ident in table_names:
            names.add(val.lower())
    return names


def validate_sql(sql, role, phase=None, person_scoped=None):
    """Refuse anything that is not a single, read-only, tenant-scoped
    SELECT over tables this role may see at this phase.

    Returns the normalised SQL, or raises SqlRefused with a message
    written for the MODEL to correct - it names the offending token, so
    a repair attempt can fix a wrong column rather than guess.
    """
    if person_scoped is None:
        person_scoped = is_person_scoped(role)
    if not sql or not sql.strip():
        raise SqlRefused("No query was produced.")
    sql = sql.strip().rstrip(";").strip()

    if "--" in sql or "/*" in sql or "*/" in sql:
        raise SqlRefused("Comments are not allowed in the query.")
    if ";" in sql:
        raise SqlRefused("Only a single statement is allowed; remove the semicolon.")
    if not re.match(r"(?is)^\s*(select|with)\b", sql):
        raise SqlRefused("The query must start with SELECT or WITH.")

    tables = {t["table"]: t for t in allowed_tables(role, phase)}
    if not tables:
        raise SqlRefused("No data is available to this role at this phase.")
    allowed_columns = set()
    for t in tables.values():
        allowed_columns.update(c.lower() for c in t["columns"])
    # Not described to the model - it does not need them - but permitted:
    # tenant_id is required for scoping and id gives a stable sort.
    allowed_columns.update({"tenant_id", "id"})

    every_known_table = {t["table"] for t in CATALOGUE}
    tokens = _tokenize(sql)
    defined = _defined_names(tokens, set(tables))

    referenced, tenant_params, owner_params = set(), 0, 0
    for kind, val in tokens:
        if kind == "param":
            if val == ":tenant_id":
                tenant_params += 1
                continue
            if val == ":sales_owner" and person_scoped:
                owner_params += 1
                continue
            raise SqlRefused(
                "The only bind parameter allowed is :tenant_id."
                if not person_scoped else
                "The only bind parameters allowed are :tenant_id and :sales_owner.")
        if kind != "ident":
            continue
        tok = val.lower()
        # Order is the security property. Forbidden words first, then
        # what the schema really permits, then the refusal for a real
        # Bolton table this role may not see - and only after all of
        # that, the names the query invented for itself.
        if tok in FORBIDDEN:
            raise SqlRefused("`%s` is not allowed." % val)
        if tok in SQL_KEYWORDS or tok in SQL_FUNCTIONS:
            continue
        if tok in tables:
            referenced.add(tok)
            continue
        if tok in every_known_table:
            # Named honestly rather than dismissed as an unknown word:
            # this table exists, and the reason it is refused is the
            # asker's role or the current phase, not a typo.
            raise SqlRefused("`%s` is not available to you." % val)
        if tok in allowed_columns or tok in defined:
            continue
        raise SqlRefused("`%s` is not a table or column you can use." % val)

    if not referenced:
        raise SqlRefused("The query does not read any available table.")
    # A floor, not a proof, and the weakest layer in this module - said
    # plainly rather than overclaimed. Counting bound tenant parameters
    # catches a query that forgot to scope a table; it does not prove
    # each predicate is attached to the right one. Layers 1 and 2 do not
    # depend on it, and Bolton is single-tenant in practice today.
    if tenant_params < len(referenced):
        raise SqlRefused(
            "Every table must be filtered by tenant, e.g. `WHERE tenant_id = :tenant_id`. "
            "You referenced %d table(s) but used :tenant_id %d time(s)."
            % (len(referenced), tenant_params))

    # A person-scoped role sees only its own jobs, here as everywhere
    # else in Bolton. Enforced by requiring the bound predicate rather
    # than by trusting the prompt - and by requiring `quote` itself,
    # because sales_owner lives only there: a query over quotelineitem
    # alone would otherwise sum every rep's lines.
    if person_scoped:
        if "quote" not in referenced:
            raise SqlRefused(
                "Your questions can only cover your own jobs, so every query must read the "
                "`quote` table and filter it with `WHERE quote.sales_owner = :sales_owner`.")
        if owner_params < 1:
            raise SqlRefused(
                "Add `AND quote.sales_owner = :sales_owner` - you can only see your own jobs.")

    if not re.search(r"(?is)\blimit\b\s+\d+", sql):
        sql = "%s LIMIT %d" % (sql, MAX_ROWS)
    return sql


# =====================================================================
# Running it.
# =====================================================================
def run_sql(sql, tenant_id, role, username=None):
    """Execute on the read-only engine, inside a read-only transaction,
    with a statement timeout. Layer 1 of 4 - by the time anything gets
    here the query has already been validated, and the connection still
    could not write if it had not been."""
    engine, problem = readonly_engine(role)
    if problem:
        raise RuntimeError(problem)
    with engine.connect() as conn:
        if not str(engine.url).startswith("sqlite"):
            # Belt and braces on top of the read-only role: if the role
            # is ever mis-granted, the transaction itself still refuses
            # to write, and nothing can run away.
            conn.execute(text("SET TRANSACTION READ ONLY"))
            conn.execute(text("SET LOCAL statement_timeout = %d" % STATEMENT_TIMEOUT_MS))
        result = conn.execute(text(sql),
                              {"tenant_id": tenant_id, "sales_owner": username})
        columns = list(result.keys())
        rows = [dict(zip(columns, r)) for r in result.fetchmany(MAX_ROWS)]
        truncated = len(rows) >= MAX_ROWS
    return columns, rows, truncated


# =====================================================================
# Claude
# =====================================================================
def _post(body, timeout):
    key, _source = api_key()
    if not key:
        raise RuntimeError(
            "No Anthropic API key is set on this server - Ask Bolton needs one. "
            "Set ASK_BOLTON_ANTHROPIC_API_KEY (its own key, recommended: its spend "
            "is then reported separately and can be capped on its own) or "
            "ANTHROPIC_API_KEY (shared with AI price-sheet import) in Render's "
            "environment. Never committed to source.")
    req = urllib.request.Request(
        ANTHROPIC_API_URL, data=json.dumps(body).encode("utf-8"),
        headers={"content-type": "application/json",
                 "x-api-key": key,
                 "anthropic-version": "2023-06-01"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError("Claude API error (%s): %s"
                           % (e.code, e.read().decode("utf-8", errors="replace")[:300]))
    except TimeoutError:
        raise RuntimeError("Claude didn't answer within %ss - try again." % timeout)
    except urllib.error.URLError as e:
        if isinstance(e.reason, TimeoutError):
            raise RuntimeError("Claude didn't answer within %ss - try again." % timeout)
        raise RuntimeError("Could not reach the Claude API: %s" % e.reason)


def _text_of(result):
    return "".join(b.get("text", "") for b in result.get("content", [])
                   if b.get("type") == "text")


SQL_SCHEMA = {
    "type": "object",
    "properties": {
        "sql": {"type": ["string", "null"]},
        "clarify": {"type": ["string", "null"]},
        "cannot_answer": {"type": ["string", "null"]},
    },
    "required": ["sql", "clarify", "cannot_answer"],
    "additionalProperties": False,
}

SQL_SYSTEM = """You write a single read-only SQL query answering a question about a \
South African flooring and blinds business, against the schema you are given.

You are given the WHOLE of the data you may use. There is other data in this system \
that you are not shown; it is not available for this question, and a query naming it \
will be rejected. Do not guess at table or column names - use only what is described.

Return exactly one of:
- "sql": a single SELECT (or WITH ... SELECT) that answers the question.
- "clarify": a short question back, when the question is genuinely ambiguous in a way \
that changes the answer. Ask rather than assume.
- "cannot_answer": a plain statement of what is missing, when the data described \
cannot answer the question. Never approximate, never substitute a different figure \
and present it as the answer.

Rules for the SQL:
- One statement. No semicolon. No comments.
- Every table must be filtered by tenant: `WHERE tenant_id = :tenant_id`. You need \
one per table.
- If the schema note says your questions cover only your own jobs, every query must \
also read `quote` and filter it with `AND quote.sales_owner = :sales_owner`. Join the \
other tables through quote - they carry no owner of their own.
- :tenant_id and :sales_owner are the only bind parameters allowed.
- Always include an explicit LIMIT.
- Return the columns a person would want to read, with clear names, and prefer a \
small number of rows that answer the question over a raw dump.
- When the answer is about specific JOBS, always include `quote.id AS quote_id` \
alongside `quote.job_number`, so the person can open the job straight from the answer. \
Put quote_id first.
- Aggregate in SQL rather than returning everything for someone else to add up.

About money, which is the one thing you must never work out for yourself:
- The only rand figures you may report are the ones recorded in `quotepayment`. Those are real receipts. Summing a job's payments is fine - that is adding up money that genuinely arrived.
- Everything else about money on a live job - what the job is worth, what the deposit should have been, what is still owed, the VAT, a discount, a margin - is worked out by Bolton from rules you have not been given, and is not in your schema. You cannot reconstruct it, and an answer that looks about right is worse than no answer, because nobody can tell it is wrong.
- So if a question needs a figure that is not a recorded payment, use "cannot_answer" and say plainly which figure is not stored. Do not substitute a number you can reach for one you cannot. Do not multiply anything by a VAT rate.
- "Has the deposit been paid?" is answerable - deposit_paid_date, final_payment_date and the payment rows are all real. "How much is still owed?" is not."""

EXPLAIN_SYSTEM = """You turn the result of a database query into one or two plain \
sentences for the owner or staff of a South African flooring and blinds business.

Absolute rules:
- Use ONLY the rows you are given. Never calculate a figure that is not in them, \
never estimate, never bring in outside knowledge.
- Rands are written like R12 500 or R12 500,40.
- If the result is empty, say plainly that there is nothing recorded for it.
- If the rows were truncated, say so.
- No preamble, no restating the question, no offer of further help. Two sentences at \
most. The rows are shown to the reader underneath, so do not list them all."""


def generate_sql(question, role, phase=None, repair=None):
    schema = schema_prompt(role, phase)
    if not schema:
        raise RuntimeError("No data is available to your role yet.")
    user = {"question": question, "sql_dialect": dialect(role), "schema": schema}
    if repair:
        user["your_previous_query_was_rejected"] = repair
    body = {
        "model": SQL_MODEL,
        "max_tokens": 4000,
        "system": SQL_SYSTEM,
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": "medium",
                          "format": {"type": "json_schema", "schema": SQL_SCHEMA}},
        "messages": [{"role": "user", "content": json.dumps(user)}],
    }
    result = _post(body, SQL_TIMEOUT_SECONDS)
    if result.get("stop_reason") == "refusal":
        raise RuntimeError("Claude declined to answer that question.")
    try:
        return json.loads(_text_of(result))
    except ValueError:
        raise RuntimeError("Claude's reply wasn't usable - try rephrasing the question.")


def explain(question, columns, rows, truncated):
    body = {
        "model": EXPLAIN_MODEL,
        "max_tokens": 500,
        "system": EXPLAIN_SYSTEM,
        "messages": [{"role": "user", "content": json.dumps(
            {"question": question, "columns": columns,
             "rows": rows[:40], "row_count": len(rows), "truncated": truncated},
            default=str)}],
    }
    result = _post(body, EXPLAIN_TIMEOUT_SECONDS)
    if result.get("stop_reason") == "refusal":
        return ""
    return _text_of(result).strip()


# =====================================================================
# The one entry point.
# =====================================================================
def ask(question, role, tenant_id, username=None, want_explanation=True, phase=None):
    """Question in, answer out. The role arrives from get_current_role()
    and is therefore already the previewed role for an Owner in preview
    mode - a preview that still saw owner-only data would not be a
    preview."""
    question = (question or "").strip()
    phase = current_phase() if phase is None else phase
    if not question:
        return {"ok": False, "error": "Ask a question first."}
    if len(question) > MAX_QUESTION_CHARS:
        return {"ok": False, "error": "That question is too long - keep it under %d characters."
                                      % MAX_QUESTION_CHARS}
    if not allowed_tables(role, phase):
        return {"ok": False, "error": "Ask Bolton isn't available to your role yet."}

    # Checked BEFORE spending a model call: a misconfigured server should
    # say so immediately rather than bill somebody for a query it was
    # never going to be allowed to run.
    _, problem = readonly_engine(role)
    if problem:
        return {"ok": False, "error": problem}

    attempts = []
    repair = None
    for attempt in range(MAX_REPAIR_ATTEMPTS + 1):
        plan = generate_sql(question, role, phase, repair)
        if plan.get("clarify") and not plan.get("sql"):
            return {"ok": True, "kind": "clarify", "question": question,
                    "clarify": plan["clarify"]}
        if plan.get("cannot_answer") and not plan.get("sql"):
            return {"ok": True, "kind": "cannot_answer", "question": question,
                    "message": plan["cannot_answer"],
                    "data_available": data_available(role, phase)}
        try:
            sql = validate_sql(plan.get("sql"), role, phase)
            break
        except SqlRefused as e:
            attempts.append({"sql": plan.get("sql"), "refused_because": str(e)})
            if attempt >= MAX_REPAIR_ATTEMPTS:
                # Reported as a refusal, never as an answer. The rejected
                # SQL is carried so a red-team run can assert on exactly
                # what was attempted.
                return {"ok": False, "kind": "refused", "question": question,
                        "error": "I can't run that. %s" % e,
                        "attempts": attempts}
            repair = {"sql": plan.get("sql"), "why_it_was_rejected": str(e)}

    try:
        columns, rows, truncated = run_sql(sql, tenant_id, role, username)
    except RuntimeError:
        raise
    except Exception as e:
        # The database refused it. Reported as what it is - not retried,
        # and never smoothed into an empty-looking answer.
        return {"ok": False, "kind": "query_failed", "question": question,
                "sql": sql, "error": "The database refused that query: %s"
                                     % str(e).split("\n")[0][:200]}

    out = {
        "ok": True, "kind": "answer", "question": question,
        "columns": columns, "rows": rows, "row_count": len(rows),
        "truncated": truncated,
        # Shown with every answer, always: the query that produced the
        # figures, and which tables it read. A sentence nobody can check
        # is the failure mode this whole feature has to avoid.
        "sql": sql,
        "tables": sorted({t["table"] for t in allowed_tables(role, phase)
                          if re.search(r"\b%s\b" % t["table"], sql, re.I)}),
        "phase": phase,
        "data_available": data_available(role, phase),
        "repairs": attempts,
    }
    if not rows:
        out["gap"] = "Nothing in %s matches that." % data_available(role, phase)
    if want_explanation:
        try:
            out["answer"] = explain(question, columns, rows, truncated)
        except RuntimeError:
            # The rows are the answer; the sentence is a convenience.
            out["answer"] = ""
    return out
