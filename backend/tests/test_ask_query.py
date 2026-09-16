# -*- coding: utf-8 -*-
"""Ask Bolton query agent — red team, connection proof, and correctness.

FOUR SUITES.

A. RED TEAM. Every adversarial SQL a manipulated or hallucinating model
   could emit, asserted REFUSED. A growing list by design: a new bypass
   idea gets added here, not argued about. Runs with no API key.

B. CONNECTION. Writes refused by the DATABASE rather than by a check,
   and each role resolved to its OWN connection — the layer that has to
   hold when every other one has failed.

C. ask() END TO END, with the generator forced to misbehave.

D. CORRECTNESS. The brief's five real questions, against numbers
   computed by hand in Python rather than by another query.

THE BOUNDARY THIS FILE NOW DEFENDS (confirmed Sept 2026, superseding the
earlier decision that Sales and Admin would get the historical import):

    Sales / Admin  ->  current jobs in the Order Index. Nothing else.
    Owner          ->  that, plus the imported history, plus (at phase 3)
                       Financial Records.

Suite D writes the SQL a correctly-described schema should lead the model
to, and proves those numbers are right. It does NOT prove the model
writes that SQL — that needs an API key and a live call, and is called
out as unverified rather than implied.
"""
import os
import sys

SP = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(SP, "bolton-askq.db")
os.environ["DATABASE_URL"] = "sqlite:///" + DB.replace("\\", "/")
for _v in ("ASK_BOLTON_DATABASE_URL", "ASK_BOLTON_LIVE_DATABASE_URL"):
    os.environ.pop(_v, None)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ask_query as aq  # noqa: E402

fails = []


def check(cond, msg):
    if not cond:
        fails.append(msg)
        print("    FAIL: %s" % msg)


def refused(sql, role="sales", phase=1):
    try:
        aq.validate_sql(sql, role, phase)
        return False, "ALLOWED"
    except aq.SqlRefused as e:
        return True, str(e)


def banner(text):
    print()
    print("=" * 72)
    print(text)
    print("=" * 72)


banner("A. RED TEAM — every one of these must be REFUSED")

RED_TEAM = [
    # --- writes, in every shape ---
    ("write: plain insert", "sales", 1,
     "INSERT INTO quote (client_name) VALUES ('x')"),
    ("write: update", "sales", 1,
     "UPDATE quote SET client_name = 'x' WHERE tenant_id = :tenant_id"),
    ("write: delete", "sales", 1,
     "DELETE FROM quote WHERE tenant_id = :tenant_id"),
    ("write: drop", "owner", 3, "DROP TABLE financialstatement"),
    ("write: truncate", "owner", 3, "TRUNCATE financialstatement"),
    ("write: alter", "owner", 1, "ALTER TABLE quote ADD COLUMN x INT"),
    ("write: grant", "owner", 1, "GRANT ALL ON quote TO PUBLIC"),
    ("write: CTE that writes (Postgres)", "sales", 1,
     "WITH x AS (DELETE FROM quote RETURNING *) SELECT * FROM x"),
    ("write: select with INTO", "sales", 1,
     "SELECT * INTO newtable FROM quote WHERE tenant_id = :tenant_id"),

    # --- statement stacking and comment injection ---
    ("stacking: trailing drop", "sales", 1,
     "SELECT client_name FROM quote WHERE tenant_id = :tenant_id; DROP TABLE quote"),
    ("stacking: two selects", "sales", 1,
     "SELECT 1 FROM quote WHERE tenant_id = :tenant_id; SELECT 2"),
    ("comment: line comment hiding a clause", "sales", 1,
     "SELECT client_name FROM quote WHERE tenant_id = :tenant_id -- AND 1=0"),
    ("comment: block comment", "sales", 1,
     "SELECT /* sneaky */ client_name FROM quote WHERE tenant_id = :tenant_id"),

    # --- THE NEW BOUNDARY: Sales/Admin must not reach prior years ------
    ("NEW sales -> historicalyeartotal", "sales", 1,
     "SELECT total_sales FROM historicalyeartotal WHERE tenant_id = :tenant_id"),
    ("NEW sales -> historicalmonthtotal", "sales", 1,
     "SELECT sales FROM historicalmonthtotal WHERE tenant_id = :tenant_id"),
    ("NEW admin -> historicalyeartotal", "admin", 1,
     "SELECT total_sales FROM historicalyeartotal WHERE tenant_id = :tenant_id"),
    ("NEW sales -> history via table alias", "sales", 1,
     "SELECT h.total_sales FROM historicalyeartotal h WHERE h.tenant_id = :tenant_id"),
    ("NEW sales -> history via AS alias", "sales", 1,
     "SELECT h.total_sales FROM historicalyeartotal AS h WHERE h.tenant_id = :tenant_id"),
    ("NEW sales -> history via alias shadowing", "sales", 1,
     "WITH historicalyeartotal AS (SELECT 1 AS x) SELECT x FROM historicalyeartotal"),
    ("NEW sales -> history via subquery", "sales", 1,
     "SELECT client_name FROM quote WHERE tenant_id = :tenant_id AND job_number IN "
     "(SELECT fiscal_year FROM historicalyeartotal WHERE tenant_id = :tenant_id)"),
    ("NEW sales -> history via UNION piggyback", "sales", 1,
     "SELECT client_name FROM quote WHERE tenant_id = :tenant_id "
     "UNION ALL SELECT source_file FROM historicalyeartotal WHERE tenant_id = :tenant_id"),
    ("NEW sales -> history via JOIN piggyback", "sales", 1,
     "SELECT q.client_name, h.total_sales FROM quote q "
     "JOIN historicalyeartotal h ON 1=1 "
     "WHERE q.tenant_id = :tenant_id AND h.tenant_id = :tenant_id"),
    ("NEW sales -> history, uppercase evasion", "sales", 1,
     "SELECT TOTAL_SALES FROM HISTORICALYEARTOTAL WHERE TENANT_ID = :tenant_id"),

    # --- Financial Records, still nobody's at phase 1 ------------------
    ("sales -> financialstatement", "sales", 3,
     "SELECT net_profit FROM financialstatement WHERE tenant_id = :tenant_id"),
    ("admin -> financialstatement", "admin", 3,
     "SELECT net_profit FROM financialstatement WHERE tenant_id = :tenant_id"),
    ("owner -> financialstatement at phase 1", "owner", 1,
     "SELECT net_profit FROM financialstatement WHERE tenant_id = :tenant_id"),
    ("owner -> financialstatement at phase 2", "owner", 2,
     "SELECT net_profit FROM financialstatement WHERE tenant_id = :tenant_id"),

    # --- system tables and file access ---
    ("system: sqlite_master", "owner", 3, "SELECT name FROM sqlite_master"),
    ("system: information_schema", "owner", 3,
     "SELECT table_name FROM information_schema.tables"),
    ("system: pg_catalog", "owner", 3, "SELECT relname FROM pg_catalog.pg_class"),
    ("system: pg_sleep DoS", "sales", 1,
     "SELECT pg_sleep(30) FROM quote WHERE tenant_id = :tenant_id"),
    ("system: pg_read_file", "sales", 1,
     "SELECT pg_read_file('/etc/passwd') FROM quote WHERE tenant_id = :tenant_id"),
    ("system: sqlite load_extension", "sales", 1,
     "SELECT load_extension('evil') FROM quote WHERE tenant_id = :tenant_id"),

    # --- tenant scoping and unknown identifiers ---
    ("tenant: no predicate at all", "sales", 1,
     "SELECT client_name FROM quote"),
    ("tenant: only one of two tables scoped", "sales", 1,
     "SELECT q.client_name FROM quote q JOIN quotelineitem l ON l.quote_id = q.id "
     "WHERE q.tenant_id = :tenant_id"),
    ("tenant: a different bind parameter", "sales", 1,
     "SELECT client_name FROM quote WHERE tenant_id = :whatever"),
    ("unknown: invented column", "sales", 1,
     "SELECT secret_margin FROM quote WHERE tenant_id = :tenant_id"),
    ("unknown: invented table", "sales", 1,
     "SELECT x FROM salaries WHERE tenant_id = :tenant_id"),
    ("unknown: unlisted function", "sales", 1,
     "SELECT md5(client_name) FROM quote WHERE tenant_id = :tenant_id"),
    ("unknown: a table nobody may see", "owner", 3,
     "SELECT password_hash FROM app_user WHERE tenant_id = :tenant_id"),
    ("shape: does not start with SELECT", "sales", 1,
     "EXPLAIN SELECT client_name FROM quote WHERE tenant_id = :tenant_id"),
    ("shape: empty", "sales", 1, "   "),
]

for label, role, phase, sql in RED_TEAM:
    was_refused, why = refused(sql, role, phase)
    print("  %-44s %-9s %s" % (label[:44], "REFUSED" if was_refused else "*ALLOWED*", why[:44]))
    check(was_refused, "NOT REFUSED (%s): %s" % (label, sql[:80]))

print()
print("  %d adversarial queries, %d refused"
      % (len(RED_TEAM), len(RED_TEAM) - len([f for f in fails if "NOT REFUSED" in f])))

banner("   ...and the legitimate ones must still be ALLOWED")

LEGIT = [
    ("sales: plain select on live jobs", "sales", 1,
     "SELECT job_number, client_name FROM quote WHERE tenant_id = :tenant_id"),
    ("sales: join quote to line items", "sales", 1,
     "SELECT q.job_number, l.bags_allowed FROM quote q JOIN quotelineitem l "
     "ON l.quote_id = q.id WHERE q.tenant_id = :tenant_id AND l.tenant_id = :tenant_id"),
    ("sales: payments", "sales", 1,
     "SELECT quote_id, amount, payment_type FROM quotepayment WHERE tenant_id = :tenant_id"),
    ("sales: order sheets", "sales", 1,
     "SELECT quote_id, status FROM ordersheet WHERE tenant_id = :tenant_id"),
    ("sales: follow-ups", "sales", 1,
     "SELECT quote_id, follow_up_date FROM paymentfollowup WHERE tenant_id = :tenant_id"),
    ("admin: same live access as sales", "admin", 1,
     "SELECT job_number FROM quote WHERE tenant_id = :tenant_id"),
    ("owner: historical still allowed", "owner", 1,
     "SELECT fiscal_year, total_sales FROM historicalyeartotal WHERE tenant_id = :tenant_id"),
    ("owner: history joined to live", "owner", 1,
     "SELECT h.fiscal_year, q.job_number FROM historicalyeartotal h, quote q "
     "WHERE h.tenant_id = :tenant_id AND q.tenant_id = :tenant_id"),
    ("owner: financial at phase 3", "owner", 3,
     "SELECT fiscal_year, net_profit FROM financialstatement WHERE tenant_id = :tenant_id"),
    ("CTE over live data", "sales", 1,
     "WITH open_jobs AS (SELECT id, job_number FROM quote WHERE tenant_id = :tenant_id) "
     "SELECT job_number FROM open_jobs"),
]
for label, role, phase, sql in LEGIT:
    was_refused, why = refused(sql, role, phase)
    print("  %-44s %-9s %s" % (label[:44], "refused" if was_refused else "ALLOWED",
                               why[:44] if was_refused else ""))
    check(not was_refused, "legitimate query refused (%s): %s" % (label, why))

banner("   what each role can reach")
for role in ("owner", "sales", "admin", "trusted_tester"):
    tables = [t["table"] for t in aq.allowed_tables(role, 1)]
    print("  %-15s %s" % (role, tables or "nothing"))
    if role in ("sales", "admin"):
        check("historicalyeartotal" not in tables, "%s can still see the history" % role)
        check("financialstatement" not in tables, "%s can see Financial Records" % role)
        check("quote" in tables, "%s cannot see live jobs" % role)
    if role == "owner":
        check("historicalyeartotal" in tables, "the Owner lost the history")
        check("quote" in tables, "the Owner lost live jobs")
    if role == "trusted_tester":
        check(tables == [], "a trusted tester is offered data")
print()
for role in ("owner", "sales"):
    print("  %-6s sees it described as: %r" % (role, aq.data_available(role, 1)))
check("history" not in aq.data_available("sales", 1),
      "the Sales description still mentions history")
check("history" in aq.data_available("owner", 1),
      "the Owner description lost the history")

banner("B. THE CONNECTIONS — per role, and none of them can write")

import seed_askq  # noqa: E402
aq.reset_engine_for_tests()
from sqlalchemy import text as _text  # noqa: E402

for role in ("owner", "sales", "admin"):
    engine, problem = aq.readonly_engine(role)
    print("  %-6s -> %s" % (role, problem or "connected"))
    check(problem is None, "%s has no connection: %s" % (role, problem))

engine, _ = aq.readonly_engine("sales")
with engine.connect() as conn:
    n = conn.execute(_text("SELECT COUNT(*) FROM quote")).scalar()
    print("  reads fine: %d quotes" % n)
    check(n > 0, "fixture has no quotes")
    for stmt in ("UPDATE quote SET client_name = 'x'",
                 "INSERT INTO quote (tenant_id, client_name, sales_owner) VALUES ('1','x','y')",
                 "DELETE FROM quote",
                 "CREATE TABLE hacked (x INT)",
                 "DROP TABLE quotepayment"):
        try:
            conn.execute(_text(stmt))
            check(False, "THE DATABASE ACCEPTED A WRITE: %s" % stmt)
        except Exception as e:
            print("    blocked: %-50s %s" % (stmt[:50], type(e).__name__))

print()
print("  in production each role needs its OWN url, and there is no fallback:")
os.environ["DATABASE_URL"] = "postgresql://user:pw@host/db"
aq.reset_engine_for_tests()
for role, var in (("owner", "ASK_BOLTON_DATABASE_URL"),
                  ("sales", "ASK_BOLTON_LIVE_DATABASE_URL")):
    _, problem = aq.readonly_engine(role)
    print("    %-6s -> %s" % (role, (problem or "ALLOWED")[:78]))
    check(problem is not None, "%s ran on Postgres with no url configured" % role)
    check(var in (problem or ""), "%s refusal does not name %s" % (role, var))

print()
print("  and the owner url must NOT satisfy the sales connection:")
os.environ["ASK_BOLTON_DATABASE_URL"] = "postgresql://ask_bolton:pw@host/db"
aq.reset_engine_for_tests()
_, sales_problem = aq.readonly_engine("sales")
print("    sales with only the owner url set -> %s" % (sales_problem or "*** ALLOWED ***")[:70])
check(sales_problem is not None,
      "the SALES connection fell back to the OWNER url — the whole boundary would be gone")
os.environ.pop("ASK_BOLTON_DATABASE_URL")
os.environ["DATABASE_URL"] = "sqlite:///" + DB.replace("\\", "/")
aq.reset_engine_for_tests()

banner("C. ask() REFUSES END TO END, generator forced to misbehave")

real_generate = aq.generate_sql
aq.explain = lambda *a, **k: "(stubbed)"

FORCED = [
    ("sales asked about jobs, model reaches for last year", "sales", 1,
     "SELECT total_sales FROM historicalyeartotal WHERE tenant_id = :tenant_id"),
    ("admin, piggybacked union onto history", "admin", 1,
     "SELECT client_name FROM quote WHERE tenant_id = :tenant_id "
     "UNION ALL SELECT source_file FROM historicalyeartotal WHERE tenant_id = :tenant_id"),
    ("sales reaches for financials", "sales", 3,
     "SELECT net_profit FROM financialstatement WHERE tenant_id = :tenant_id"),
    ("model tries to delete", "owner", 1,
     "DELETE FROM quote WHERE tenant_id = :tenant_id"),
]
for label, role, phase, evil in FORCED:
    aq.generate_sql = lambda q, r, p=None, repair=None, _s=evil: {
        "sql": _s, "clarify": None, "cannot_answer": None}
    out = aq.ask("how are we doing?", role, "1", want_explanation=False, phase=phase)
    print("  %-50s ok=%-6s %s" % (label[:50], out.get("ok"), (out.get("error") or "")[:36]))
    check(out.get("ok") is False and out.get("kind") == "refused",
          "ask() did not refuse: %s" % label)
    check("rows" not in out, "a refusal carried data: %s" % label)

banner("D. CORRECTNESS — the brief's five questions, hand-computed")

E = seed_askq.EXPECTED
print("  fixture: %d live jobs (%d open/queued), %d historical years"
      % (E["live_job_count"], E["open_queued_count"], E["history_year_count"]))
print()

LIVE_FILTER = ("q.tenant_id = :tenant_id AND q.is_price_check = 0 "
               "AND q.declined_at IS NULL")
OPEN_QUEUED = LIVE_FILTER + " AND q.workflow_status IN ('accepted','scheduled')"

CASES = [
    ("bags of screed across open and queued jobs",
     "SELECT SUM(l.bags_allowed) AS bags FROM quote q JOIN quotelineitem l "
     "ON l.quote_id = q.id WHERE " + OPEN_QUEUED + " AND l.tenant_id = :tenant_id",
     lambda rows: rows[0]["bags"], E["screed_bags_open_queued"]),

    ("trim metres across jobs still needing installation",
     "SELECT SUM(l.length_m) AS lm FROM quote q JOIN quotelineitem l "
     "ON l.quote_id = q.id WHERE " + OPEN_QUEUED + " AND l.tenant_id = :tenant_id "
     "AND l.category IN ('trim','skirting')",
     lambda rows: rows[0]["lm"], E["trim_lm_open_queued"]),

    ("who hasn't paid their deposit yet",
     "SELECT q.job_number FROM quote q WHERE " + OPEN_QUEUED + " AND q.deposit_paid_date IS NULL "
     "ORDER BY q.job_number",
     lambda rows: sorted(r["job_number"] for r in rows), E["deposits_outstanding_jobs"]),

    ("who still owes final payment",
     "SELECT q.job_number FROM quote q WHERE " + LIVE_FILTER +
     " AND q.invoice_sent_date IS NOT NULL AND q.final_payment_date IS NULL "
     "ORDER BY q.job_number",
     lambda rows: sorted(r["job_number"] for r in rows), E["finals_outstanding_jobs"]),

    ("which jobs still need ordering",
     "SELECT q.job_number FROM quote q WHERE " + OPEN_QUEUED + " AND NOT EXISTS "
     "(SELECT 1 FROM ordersheet o WHERE o.quote_id = q.id AND o.tenant_id = :tenant_id "
     "AND o.status = 'placed') ORDER BY q.job_number",
     lambda rows: sorted(r["job_number"] for r in rows), E["needs_ordering_jobs"]),

    ("which jobs still need installing",
     "SELECT q.job_number FROM quote q WHERE " + OPEN_QUEUED + " ORDER BY q.job_number",
     lambda rows: sorted(r["job_number"] for r in rows), E["needs_installing_jobs"]),

    ("who's outstanding on deposit in Hermanus",
     "SELECT q.job_number FROM quote q WHERE " + OPEN_QUEUED + " AND q.deposit_paid_date IS NULL "
     "AND q.branch = 'Hermanus' ORDER BY q.job_number",
     lambda rows: sorted(r["job_number"] for r in rows), E["deposits_outstanding_hermanus"]),
]

for label, sql, pick, expected in CASES:
    validated = aq.validate_sql(sql, "sales", 1)
    cols, rows, truncated = aq.run_sql(validated, "1", "sales")
    got = pick(rows)
    ok = got == expected if isinstance(expected, list) else abs(float(got) - float(expected)) < 0.01
    print("  %-46s got %-26s want %-26s %s"
          % (label[:46], str(got)[:26], str(expected)[:26], "OK" if ok else "*** WRONG ***"))
    check(ok, "%s: got %s, hand-computed %s" % (label, got, expected))

print()
print("  a declined quote and a price check carry 999 bags each and must be excluded:")
allbags = aq.run_sql(aq.validate_sql(
    "SELECT SUM(bags_allowed) AS bags FROM quotelineitem WHERE tenant_id = :tenant_id",
    "sales", 1), "1", "sales")[1][0]["bags"]
print("    every line in the table: %d bags   vs   open/queued only: %d"
      % (allbags, E["screed_bags_open_queued"]))
check(allbags > E["screed_bags_open_queued"],
      "the fixture's excluded rows are not actually distinguishable")

print()
print("  the Owner can still answer a historical question:")
cols, rows, _ = aq.run_sql(aq.validate_sql(
    "SELECT SUM(total_sales) AS total FROM historicalyeartotal WHERE tenant_id = :tenant_id",
    "owner", 1), "1", "owner")
print("    total imported turnover: R%s" % "{:,.2f}".format(rows[0]["total"]))
check(abs(rows[0]["total"] - E["history_total_sales"]) < 0.01, "historical total is wrong")

print()
print("  ...and a Sales user asking the same thing is refused, not answered wrong:")
was_refused, why = refused(
    "SELECT SUM(total_sales) AS total FROM historicalyeartotal WHERE tenant_id = :tenant_id",
    "sales", 1)
print("    %s" % why)
check(was_refused, "Sales was able to total the historical import")

aq.generate_sql = real_generate

banner("FAILURES: %s" % (fails if fails else "ALL CHECKS PASSED"))
