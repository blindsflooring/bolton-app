# -*- coding: utf-8 -*-
"""
Ask Bolton — plain-language questions about the business (confirmed
Sept 2026).

WHAT THE MODEL IS ALLOWED TO DO, AND WHY IT IS THIS AND NOTHING MORE.

Claude's only job here is to read a question and say which of a fixed
list of pre-written answerers should run, and with what arguments. It
never writes a query, never sees a row of data, and never decides what
anybody may look at. The answerers are ordinary Python in this file,
reviewed and tested like any other endpoint.

That is the whole answer to "how do we guarantee it never leaks
Financial Records data into a Sales answer, even via an indirect or
cleverly-phrased question", and it is structural rather than hopeful:

  1. Every answerer declares the roles that may run it, and
     answer_question() checks that AFTER classification and BEFORE
     execution, through the caller's real role. A cleverly-phrased
     question can at most select an answerer it is not allowed to run,
     and then be refused. Selecting is not running.
  2. The catalogue sent to the model is filtered to the asker's role
     first, so an owner-only answerer is not even nameable by a Sales
     user. That is defence in depth; point 1 is the actual guarantee.
  3. The model is never given data to be indiscreet with. There is
     nothing in its context to leak — the classifier turn contains the
     catalogue and the question, and nothing else.

Compare text-to-SQL, which was considered and rejected: the model would
write arbitrary queries against every table, see arbitrary rows, and the
only defence would be instructions in a prompt — which is precisely the
class of defence that loses to a cleverly-phrased question.

This is the same decision this codebase has already made twice.
ai_import.py extracts a price sheet but stages the rows for a human
rather than writing to the price book. Financial Records figures are
typed in rather than parsed off the PDF. AI proposes; code with a
permission check disposes.

READ-ONLY, STRUCTURALLY. Every answerer takes a Session and only ever
reads. Nothing in this module calls session.add/delete/commit, and no
answerer is given a write helper to call.

NO SECOND DEFINITION OF ANY NUMBER. Where Bolton already computes a
figure, the answerer calls that same code rather than re-deriving it —
_quote_payment_state() for what a job has paid and still owes,
_quote_totals_for() for its totals, line_real_cost() for cost basis,
PENDING_INSTALL_STATUSES for what "not installed yet" means. A second,
slightly different copy of one of those is exactly how two screens end
up disagreeing, and this file would be the place it happened.

Deliberately built on stdlib urllib, same as ai_import.py, for the same
reason: two JSON POSTs do not justify a new dependency.
"""
import json
import os
import urllib.error
import urllib.request
from datetime import date, timedelta

from sqlmodel import select

from models import (
    Quote,
    QuoteLineItem,
    FlooringProduct,
    HistoricalYearTotal,
)

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"

# Two models on purpose, each doing the job it is cheapest at.
#
# Classification is picking one of ten labelled options — easy, and the
# thing every question pays for, so it runs on Haiku. Phrasing turns
# figures that have ALREADY been computed into a sentence; it runs on
# the same Sonnet constant ai_import.py uses, because a misstated number
# in the sentence is the one failure here a person might not notice.
#
# Neither model is ever the source of a figure. Every number in a
# phrased answer also appears in the structured payload the UI renders
# beside it, which is what makes a phrasing slip visible rather than
# silent.
CLASSIFIER_MODEL = "claude-haiku-4-5"
PHRASING_MODEL = "claude-sonnet-5"

# Shorter than ai_import's 150s: that module sends a whole price sheet
# to be read, this one sends a question and a short list. A question
# that has not been classified in 30 seconds is not going to be.
CLASSIFY_TIMEOUT_SECONDS = 30
PHRASE_TIMEOUT_SECONDS = 45

# Not a safety control — the role check is. This only stops a pasted
# essay from becoming an expensive classification.
MAX_QUESTION_CHARS = 500


# ---------------------------------------------------------------------
# Helpers borrowed from main.py.
#
# Passed in rather than imported, because main.py imports this module —
# importing back would be a cycle. main.py calls register_helpers() once
# at import time, immediately after the helpers themselves are defined.
# ---------------------------------------------------------------------
_H = {}


def register_helpers(**kwargs):
    _H.update(kwargs)


# ---------------------------------------------------------------------
# The catalogue.
# ---------------------------------------------------------------------
CATALOGUE = []

OPERATIONAL = ("owner", "sales", "admin")
OWNER_ONLY = ("owner",)


def answerer(name, roles, asks, args=(), sources=()):
    """Register one answerer.

    `roles` is the access control, and it is data rather than a check
    inside the function body on purpose: answer_question() can then
    enforce every answerer's rule in one place, and a new answerer
    cannot be added that forgets to check.
    """
    def wrap(fn):
        CATALOGUE.append({
            "name": name, "roles": tuple(roles), "asks": asks,
            "args": tuple(args), "sources": tuple(sources), "fn": fn,
        })
        return fn
    return wrap


def _catalogue_for(role):
    return [a for a in CATALOGUE if role in a["roles"]]


def _find(name):
    for a in CATALOGUE:
        if a["name"] == name:
            return a
    return None


# ---------------------------------------------------------------------
# Shared query pieces.
# ---------------------------------------------------------------------
def _live_quotes(session, tenant_id, statuses=None):
    """Real, tracked jobs — the same three exclusions every dashboard
    figure already applies (list_quotes(), analytics_overview()): never a
    Price Check, never a quote the client declined, never a trusted
    tester's.

    REAL BUG FOUND IN TESTING, and worth naming because the first
    version of this function got it wrong on purpose. Trusted-tester
    quotes were excluded only from money figures, on the reasoning that
    an operational list should show every row somebody might be looking
    at. That is backwards: a tester's job is not a real job, and the
    damage from a fake one is worse in an operational answer than in a
    KPI. In the test data a tester's job appeared as R402 500 of
    outstanding deposit and added 200 m2 of flooring that nobody needs
    to order — a number somebody would have acted on.

    Excluded HERE rather than in each answerer, so a new answerer cannot
    be added that forgets. The answerers that also need tester filtering
    for their own sums get it from this one place.
    """
    q = select(Quote).where(
        Quote.tenant_id == tenant_id,
        Quote.is_price_check == False,  # noqa: E712
    )
    if statuses:
        q = q.where(Quote.workflow_status.in_(tuple(statuses)))
    rows = [r for r in session.exec(q).all() if r.declined_at is None]
    testers = _H["trusted_tester_usernames"](session, tenant_id)
    if testers:
        rows = [r for r in rows if r.sales_owner not in testers]
    return rows


def _job_ref(quote):
    """How a job is named in an answer. job_number where there is one —
    it is assigned once at acceptance and never reused — and the quote
    id otherwise, never a placeholder that looks like a job number.
    """
    return {
        "quote_id": quote.id,
        "job_number": quote.job_number,
        "client_name": quote.client_name,
        "branch": quote.branch,
        "workflow_status": quote.workflow_status,
        "on_hold_reason": quote.on_hold_reason,
    }


def _money(value):
    return round(float(value or 0.0), 2)


def _pending_lines(session, tenant_id, predicate):
    """Every quote line on a job that is accepted or scheduled and not
    installed yet, where `predicate(line)` is true.

    PENDING_INSTALL_STATUSES is imported from main.py rather than spelled
    out here — it is the same boundary the pending-jobs feature already
    uses, and the one Burgert confirmed for the stock questions. Two
    copies of "not installed yet" is how they drift.
    """
    statuses = _H["pending_install_statuses"]
    quotes = {q.id: q for q in _live_quotes(session, tenant_id, statuses)}
    if not quotes:
        return []
    lines = session.exec(
        select(QuoteLineItem).where(QuoteLineItem.tenant_id == tenant_id)
    ).all()
    return [(l, quotes[l.quote_id]) for l in lines
            if l.quote_id in quotes and predicate(l)]


# =====================================================================
# 2. Jobs waiting on a product / colour
# =====================================================================
@answerer(
    "jobs_waiting_on_product", OPERATIONAL,
    "Which jobs are waiting on a particular flooring product or colour and "
    "have not been installed yet. Use when the question names a product, "
    "range or colour.",
    args=("product", "colour"),
    sources=("Quote lines", "Job workflow status"),
)
def _jobs_waiting_on_product(session, tenant_id, args, ctx):
    wanted = (args.get("product") or "").strip()
    colour = (args.get("colour") or "").strip()
    if not wanted and not colour:
        return {"clarify": "Which product or colour did you mean?"}

    products = session.exec(
        select(FlooringProduct).where(FlooringProduct.tenant_id == tenant_id)
    ).all()

    def hit(p):
        name = ("%s %s %s" % (p.product_name or "", p.product_variant or "", p.colour or "")).lower()
        if wanted and wanted.lower() not in name:
            return False
        if colour and colour.lower() not in (p.colour or "").lower():
            return False
        return True

    matches = [p for p in products if hit(p)]
    if not matches:
        return {"gaps": ["Nothing in the price book matches %s." %
                         (" / ".join(x for x in (wanted, colour) if x))]}

    # A clarifying question rather than a guessed assumption — this
    # feature's own standing rule. Distinct product NAMES, not rows: one
    # range in eight colours is one product as far as the asker is
    # concerned, and asking them to pick between eight rows of the same
    # range would be noise rather than clarity.
    names = sorted({p.product_name for p in matches})
    if len(names) > 1 and not colour:
        return {"clarify": "Which one did you mean — %s?" % ", ".join(names[:6])}

    ids = {p.id for p in matches}
    pairs = _pending_lines(session, tenant_id,
                           lambda l: l.category == "flooring" and l.product_id in ids)

    by_job = {}
    for line, quote in pairs:
        job = by_job.setdefault(quote.id, dict(_job_ref(quote), **{
            "installation_date": quote.installation_date,
            "colours": [], "quantity_m2": None, "boxes_needed": None,
        }))
        c = (line.colour or "").strip()
        if c and c not in job["colours"]:
            job["colours"].append(c)
        for field, value in (("quantity_m2", line.quantity_m2), ("boxes_needed", line.boxes_needed)):
            if value:
                job[field] = (job[field] or 0) + value

    jobs = sorted(by_job.values(), key=lambda j: (
        j["installation_date"] is None, j["installation_date"] or date.max, j["quote_id"]))
    return {
        "headline": "%d job%s waiting on %s" % (
            len(jobs), "" if len(jobs) == 1 else "s", names[0] if len(names) == 1 else "that"),
        "rows": jobs,
        "figures": {"jobs": len(jobs),
                    "total_m2": round(sum(j["quantity_m2"] or 0 for j in jobs), 2)},
        "gaps": ([] if jobs else
                 ["No accepted or scheduled job currently has that on it."]),
    }


# =====================================================================
# 3. Deposits outstanding
# =====================================================================
@answerer(
    "deposits_outstanding", OPERATIONAL,
    "Which jobs still owe their deposit. Use for questions about deposits "
    "not yet paid or clients outstanding on a deposit.",
    sources=("Recorded payments", "Job totals"),
)
def _deposits_outstanding(session, tenant_id, args, ctx):
    rows = []
    for quote in _live_quotes(session, tenant_id, ("accepted", "scheduled", "completed")):
        totals = _H["quote_totals_for"](session, quote, tenant_id)
        payments = _H["quote_payments"](session, quote.id, tenant_id)
        state = _H["payment_state"](quote, totals, payments)
        if not state["deposit_required"] or state["deposit_settled"]:
            continue
        # A job that has settled in full without a deposit ever being
        # recorded separately does NOT owe a deposit, whatever the
        # milestone dates say. Deliberately checked against what is
        # outstanding overall rather than against deposit_paid_date
        # alone, which is only a milestone and can legitimately be
        # empty on a job paid in one go.
        if state["amount_outstanding"] <= 0:
            continue
        rows.append(dict(_job_ref(quote), **{
            "deposit_due": _money(min(totals["deposit_amount"], state["amount_outstanding"])),
            "total_incl_vat": _money(totals["total_incl_vat"]),
            "amount_paid": _money(state["amount_paid"]),
            "accepted_at": quote.accepted_at.date() if quote.accepted_at else None,
            "installation_date": quote.installation_date,
        }))

    rows.sort(key=lambda r: (r["accepted_at"] is None, r["accepted_at"] or date.max))
    return {
        "headline": "%d job%s still owing a deposit" % (len(rows), "" if len(rows) == 1 else "s"),
        "rows": rows,
        "figures": {"jobs": len(rows),
                    "total_deposit_due": _money(sum(r["deposit_due"] for r in rows))},
        "gaps": [] if rows else ["Every accepted job has its deposit recorded."],
    }


# =====================================================================
# 4. Final payments outstanding
# =====================================================================
@answerer(
    "final_payments_outstanding", OPERATIONAL,
    "Which jobs still owe their final payment or balance. Use for questions "
    "about money still owed on finished or invoiced work.",
    sources=("Recorded payments", "Job totals"),
)
def _final_payments_outstanding(session, tenant_id, args, ctx):
    rows = []
    for quote in _live_quotes(session, tenant_id, ("accepted", "scheduled", "completed")):
        # Installed, or invoiced. A job still being fitted has a balance
        # by definition and is not "outstanding on final payment" in the
        # sense anybody means when they ask this.
        if quote.workflow_status != "completed" and not quote.invoice_sent_date:
            continue
        totals = _H["quote_totals_for"](session, quote, tenant_id)
        payments = _H["quote_payments"](session, quote.id, tenant_id)
        state = _H["payment_state"](quote, totals, payments)
        if state["amount_outstanding"] <= 0:
            continue
        since = quote.invoice_sent_date or quote.installation_date
        rows.append(dict(_job_ref(quote), **{
            "amount_outstanding": _money(state["amount_outstanding"]),
            "amount_paid": _money(state["amount_paid"]),
            "total_incl_vat": _money(totals["total_incl_vat"]),
            "invoice_sent_date": quote.invoice_sent_date,
            "installation_date": quote.installation_date,
            "days_waiting": (ctx["today"] - since).days if since else None,
        }))

    rows.sort(key=lambda r: -(r["days_waiting"] or 0))
    return {
        "headline": "%d job%s still owing money" % (len(rows), "" if len(rows) == 1 else "s"),
        "rows": rows,
        "figures": {"jobs": len(rows),
                    "total_outstanding": _money(sum(r["amount_outstanding"] for r in rows))},
        "gaps": [] if rows else ["Nothing invoiced or installed is still owing."],
    }


# =====================================================================
# 5. Installations outstanding
# =====================================================================
@answerer(
    "installations_outstanding", OPERATIONAL,
    "Which jobs are accepted or scheduled but not installed yet, and when "
    "each is booked for. Use for questions about pending or outstanding "
    "installations and what is still to be fitted.",
    sources=("Job workflow status", "Installation dates"),
)
def _installations_outstanding(session, tenant_id, args, ctx):
    rows = []
    for quote in _live_quotes(session, tenant_id, _H["pending_install_statuses"]):
        rows.append(dict(_job_ref(quote), **{
            "installation_date": quote.installation_date,
            # A date being present is not the same as it being booked —
            # these are two separate fields on Quote for exactly that
            # reason, and collapsing them here would turn a pencilled-in
            # date into a commitment.
            "confirmed": bool(quote.installation_confirmed_date),
            "installer_team": quote.installer_team,
            "days_away": ((quote.installation_date - ctx["today"]).days
                          if quote.installation_date else None),
        }))

    rows.sort(key=lambda r: (r["installation_date"] is None,
                             r["installation_date"] or date.max, r["quote_id"]))
    undated = [r for r in rows if r["installation_date"] is None]
    unconfirmed = [r for r in rows if r["installation_date"] and not r["confirmed"]]
    overdue = [r for r in rows if r["days_away"] is not None and r["days_away"] < 0]

    gaps = []
    if undated:
        gaps.append("%d of them have no installation date set yet." % len(undated))
    if overdue:
        gaps.append("%d have an installation date that has already passed and are "
                    "still not marked completed." % len(overdue))
    return {
        "headline": "%d job%s still to be installed" % (len(rows), "" if len(rows) == 1 else "s"),
        "rows": rows,
        "figures": {"jobs": len(rows), "no_date_yet": len(undated),
                    "date_not_confirmed": len(unconfirmed), "date_passed": len(overdue)},
        "gaps": gaps if rows else ["Nothing is waiting on an installation."],
    }


# =====================================================================
# 6. Colours committed to jobs not yet installed
# =====================================================================
@answerer(
    "colours_awaiting_installation", OPERATIONAL,
    "Which flooring colours are committed to accepted or scheduled jobs that "
    "have not been installed yet, and how much of each. Use for questions "
    "about what flooring or colours are still to go down.",
    sources=("Quote lines", "Job workflow status"),
)
def _colours_awaiting_installation(session, tenant_id, args, ctx):
    pairs = _pending_lines(session, tenant_id,
                           lambda l: l.category == "flooring" and (l.colour or "").strip())

    groups = {}
    for line, quote in pairs:
        key = (line.product_name or "", (line.colour or "").strip())
        g = groups.setdefault(key, {
            "product_name": key[0], "colour": key[1],
            "quantity_m2": 0.0, "boxes_needed": 0, "jobs": set(),
        })
        g["quantity_m2"] += line.quantity_m2 or 0.0
        g["boxes_needed"] += line.boxes_needed or 0
        g["jobs"].add(quote.id)

    rows = sorted(
        ({"product_name": g["product_name"], "colour": g["colour"],
          "quantity_m2": round(g["quantity_m2"], 2),
          "boxes_needed": g["boxes_needed"] or None,
          "jobs": len(g["jobs"])} for g in groups.values()),
        key=lambda r: -r["quantity_m2"])

    return {
        "headline": "%d colour%s committed to jobs not yet installed" % (
            len(rows), "" if len(rows) == 1 else "s"),
        "rows": rows,
        "figures": {"colours": len(rows),
                    "total_m2": round(sum(r["quantity_m2"] for r in rows), 2)},
        # Stated on every answer, not only when it bites. Bolton has no
        # stock level anywhere — StockPurchase records what was bought
        # and deliberately keeps no running balance — so this is what is
        # committed to jobs, which is a different thing from what is on
        # the rack, and the difference matters when ordering.
        "gaps": ["This is what is committed to jobs, not what is physically in "
                 "stock — Bolton doesn't hold stock levels."],
    }


# =====================================================================
# 7. Screed bags needed
# =====================================================================
@answerer(
    "screed_bags_needed", OPERATIONAL,
    "How many bags of screed or floor-levelling compound are needed across "
    "jobs that are accepted or scheduled and not installed yet.",
    sources=("Quote lines (bags allowed)", "Job workflow status"),
)
def _screed_bags_needed(session, tenant_id, args, ctx):
    pairs = _pending_lines(session, tenant_id, lambda l: (l.bags_allowed or 0) > 0)

    groups = {}
    for line, quote in pairs:
        g = groups.setdefault(line.product_name or "Screed", {
            "product_name": line.product_name or "Screed", "bags": 0,
            "quantity_m2": 0.0, "jobs": set()})
        g["bags"] += line.bags_allowed or 0
        g["quantity_m2"] += line.quantity_m2 or 0.0
        g["jobs"].add(quote.id)

    rows = sorted(({"product_name": g["product_name"], "bags": g["bags"],
                    "quantity_m2": round(g["quantity_m2"], 2), "jobs": len(g["jobs"])}
                   for g in groups.values()), key=lambda r: -r["bags"])
    total = sum(r["bags"] for r in rows)
    return {
        "headline": "%d bag%s of screed needed across %d job%s" % (
            total, "" if total == 1 else "s",
            len({q.id for _, q in pairs}), "" if len({q.id for _, q in pairs}) == 1 else "s"),
        "rows": rows,
        "figures": {"total_bags": total, "jobs": len({q.id for _, q in pairs})},
        # bags_allowed is the quoted allowance, and site variance is
        # billed on top at BusinessSettings.bag_overage_rate — so this
        # is what was priced, not a promise about what the floor will
        # actually drink.
        "gaps": (["This is the quoted bag allowance. Site variance is billed on "
                  "top, so a difficult floor can need more."] if rows
                 else ["No accepted or scheduled job has screed on it."]),
    }


# =====================================================================
# 8. Trims needed
# =====================================================================
@answerer(
    "trims_needed", OPERATIONAL,
    "How many metres of trim or skirting are needed across jobs that are "
    "accepted or scheduled and not installed yet.",
    sources=("Quote lines (trim and skirting)", "Job workflow status"),
)
def _trims_needed(session, tenant_id, args, ctx):
    pairs = _pending_lines(session, tenant_id,
                           lambda l: l.category in ("trim", "skirting"))

    groups = {}
    for line, quote in pairs:
        key = (line.product_name or "", (line.colour or "").strip(), line.category)
        g = groups.setdefault(key, {
            "product_name": key[0], "colour": key[1], "category": key[2],
            "length_m": 0.0, "jobs": set()})
        g["length_m"] += line.length_m or 0.0
        g["jobs"].add(quote.id)

    rows = sorted(({"product_name": g["product_name"], "colour": g["colour"],
                    "category": g["category"], "length_m": round(g["length_m"], 2),
                    "jobs": len(g["jobs"])} for g in groups.values()),
                  key=lambda r: -r["length_m"])
    total = round(sum(r["length_m"] for r in rows), 2)
    jobs = len({q.id for _, q in pairs})
    return {
        "headline": "%.2f lm of trim and skirting needed across %d job%s" % (
            total, jobs, "" if jobs == 1 else "s"),
        "rows": rows,
        "figures": {"total_length_m": total, "jobs": jobs},
        # Lengths, not stock lengths. Supertrim sells fixed lengths and
        # 47.5 lm is not 47.5 lm of cuttable trim — that conversion is a
        # real decision about waste and it is not being guessed here.
        "gaps": (["These are required metres, not stock lengths — how many "
                  "lengths to order depends on cutting."] if rows
                 else ["No accepted or scheduled job has trim or skirting on it."]),
    }


# =====================================================================
# 1 / 10. Sales comparison
# =====================================================================
def _fiscal_year_of(d):
    """Bolton's fiscal year runs March to February, named for the year
    it STARTS in — the same convention HistoricalYearTotal stores under.
    """
    return d.year if d.month >= 3 else d.year - 1


@answerer(
    "sales_comparison", OPERATIONAL,
    "How this year's sales compare with a previous year or an earlier "
    "period. Use for any question comparing turnover now against the past.",
    args=("years", "from_date", "to_date"),
    sources=("Accepted quotes", "Imported Order Index history"),
)
def _sales_comparison(session, tenant_id, args, ctx):
    """One answerer for both the operational and the Owner framing of
    this question, deliberately.

    They are the same arithmetic; what differs is which sources an
    answer may cite. Everybody gets live Quote data and the imported
    Order Index history. Only the Owner's answer may additionally carry
    audited figures, and that is a source list difference rather than a
    different calculation — two answerers would mean maintaining the
    same year-on-year maths twice and hoping the copies agreed.
    """
    today = ctx["today"]
    quotes = [q for q in _live_quotes(session, tenant_id) if q.accepted_at is not None]

    lines_by_quote = {}
    for line in session.exec(
        select(QuoteLineItem).where(QuoteLineItem.tenant_id == tenant_id)
    ).all():
        lines_by_quote.setdefault(line.quote_id, []).append(line)

    offset = _H["sast_offset"]
    vat_pct = _H["get_settings"](session, tenant_id).vat_pct

    def won_on(q):
        return (q.accepted_at + offset).date()

    # An explicit window if one was asked for, otherwise the fiscal year
    # to date — the period "this year's sales" means in a business whose
    # year starts in March.
    from_date = _parse_date(args.get("from_date"))
    to_date = _parse_date(args.get("to_date")) or today
    this_fy = _fiscal_year_of(today)
    if from_date is None:
        from_date = date(this_fy, 3, 1)

    def totals_between(a, b):
        sales = profit = 0.0
        count = 0
        for q in quotes:
            d = won_on(q)
            if not (a <= d <= b):
                continue
            subtotal = sum(l.line_total for l in lines_by_quote.get(q.id, [])) + q.transport_levy
            t = _H["quote_totals"](subtotal, q, vat_pct)
            sales += t["total_incl_vat"]
            profit += t["total_ex_vat"] - sum(_H["line_real_cost"](l)
                                              for l in lines_by_quote.get(q.id, []))
            count += 1
        return {"sales_incl_vat": _money(sales), "gross_profit": _money(profit), "jobs": count}

    current = dict(totals_between(from_date, to_date),
                   **{"label": "%s to %s" % (from_date.isoformat(), to_date.isoformat())})

    # The same span, shifted back a year at a time. Same calendar window
    # rather than a whole year, so a part-year is compared with a
    # part-year and not against twelve months of somebody else's.
    wanted_years = args.get("years") or []
    spans = []
    back = [y for y in wanted_years if isinstance(y, int)] or [1, 2]
    for n in (sorted({this_fy - y for y in back if y > 1900}) or []):
        spans.append(n)
    if not spans:
        spans = [this_fy - 1, this_fy - 2]

    comparisons = []
    gaps = []
    for fy in sorted(spans, reverse=True):
        shift = this_fy - fy
        a = date(from_date.year - shift, from_date.month, min(from_date.day, 28))
        b = date(to_date.year - shift, to_date.month, min(to_date.day, 28))
        live = totals_between(a, b)
        row = {"fiscal_year": fy, "label": "%s to %s" % (a.isoformat(), b.isoformat()),
               "sales_incl_vat": live["sales_incl_vat"],
               "gross_profit": live["gross_profit"], "jobs": live["jobs"],
               "source": "Bolton"}
        if live["jobs"] == 0:
            hist = session.exec(
                select(HistoricalYearTotal).where(
                    HistoricalYearTotal.tenant_id == tenant_id,
                    HistoricalYearTotal.fiscal_year == fy)
            ).first()
            if hist:
                row = {"fiscal_year": fy, "label": "Full year %d/%d" % (fy, fy + 1),
                       "sales_incl_vat": _money(hist.total_sales),
                       "gross_profit": _money(hist.gross_profit),
                       "jobs": None, "source": "Order Index import (full year)"}
                gaps.append("%d/%d comes from the imported Order Index and is a FULL "
                            "year, not the same months as above." % (fy, fy + 1))
            else:
                gaps.append("Nothing recorded for %d/%d in that window." % (fy, fy + 1))
        comparisons.append(row)

    if current["jobs"] == 0:
        gaps.append("No accepted jobs in the current window at all.")

    return {
        "headline": "R%s won in %s" % ("{:,.0f}".format(current["sales_incl_vat"]), current["label"]),
        "rows": [current] + comparisons,
        "figures": {"current_sales_incl_vat": current["sales_incl_vat"],
                    "current_gross_profit": current["gross_profit"],
                    "current_jobs": current["jobs"]},
        "gaps": gaps,
    }


def _parse_date(value):
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


# =====================================================================
# 9. Monthly gross-profit target (owner only)
# =====================================================================
@answerer(
    "monthly_gp_target_progress", OWNER_ONLY,
    "Whether the business is on target for this month's profit. Use for "
    "questions about being on target, behind or ahead for the month.",
    args=("month",),
    sources=("Accepted quotes", "Business Settings monthly target"),
)
def _monthly_gp_target_progress(session, tenant_id, args, ctx):
    """Gross profit against the target on BusinessSettings.

    Every figure here comes from the same recipe the Business Overview
    dashboard already uses, and that is not a stylistic preference: the
    dashboard and this answer WILL be read side by side, and a second
    definition of monthly profit would have them disagree about the same
    month. Profit is total_ex_vat minus line_real_cost(); a job counts
    in the month it was ACCEPTED, in SAST; Price Checks and
    trusted-tester jobs are excluded at source.
    """
    today = ctx["today"]
    month = _parse_month(args.get("month")) or (today.year, today.month)
    year, mon = month
    start = date(year, mon, 1)
    end = (date(year + (mon == 12), (mon % 12) + 1, 1) - timedelta(days=1))

    settings = _H["get_settings"](session, tenant_id)
    target = float(settings.monthly_gp_target or 0.0)

    quotes = [q for q in _live_quotes(session, tenant_id) if q.accepted_at is not None]

    lines_by_quote = {}
    for line in session.exec(
        select(QuoteLineItem).where(QuoteLineItem.tenant_id == tenant_id)
    ).all():
        lines_by_quote.setdefault(line.quote_id, []).append(line)

    offset = _H["sast_offset"]
    vat_pct = settings.vat_pct
    profit = sales = 0.0
    counted = []
    for q in quotes:
        won = (q.accepted_at + offset).date()
        if not (start <= won <= end):
            continue
        subtotal = sum(l.line_total for l in lines_by_quote.get(q.id, [])) + q.transport_levy
        t = _H["quote_totals"](subtotal, q, vat_pct)
        gp = t["total_ex_vat"] - sum(_H["line_real_cost"](l) for l in lines_by_quote.get(q.id, []))
        profit += gp
        sales += t["total_incl_vat"]
        counted.append(dict(_job_ref(q), **{"won_on": won, "gross_profit": _money(gp),
                                            "value_incl_vat": _money(t["total_incl_vat"])}))

    profit = _money(profit)
    counted.sort(key=lambda r: -r["gross_profit"])

    # Elapsed days, not calendar days, for a mid-month answer: "you are
    # at 40% with a third of the month gone" is the useful shape, and
    # pacing against the whole month before it has happened is not.
    days_in_month = (end - start).days + 1
    elapsed = min(days_in_month, max(1, (min(today, end) - start).days + 1))
    pace = target * (elapsed / days_in_month) if target else 0.0

    gaps = []
    if target <= 0:
        gaps.append("No monthly profit target is set in Business Settings, so there "
                    "is nothing to compare against.")
    return {
        "headline": ("R%s gross profit so far this month against a R%s target"
                     % ("{:,.0f}".format(profit), "{:,.0f}".format(target))
                     if target else "R%s gross profit so far this month" % "{:,.0f}".format(profit)),
        "rows": counted[:20],
        "figures": {
            "month": "%04d-%02d" % (year, mon),
            "gross_profit": profit,
            "sales_incl_vat": _money(sales),
            "target": _money(target),
            "shortfall": _money(target - profit) if target else None,
            "pct_of_target": round(profit / target * 100, 1) if target else None,
            "pct_of_month_elapsed": round(elapsed / days_in_month * 100, 1),
            "on_pace": (profit >= pace) if target else None,
            "jobs": len(counted),
        },
        "gaps": gaps,
    }


def _parse_month(value):
    if not value:
        return None
    text = str(value)[:7]
    try:
        year, mon = text.split("-")
        return int(year), int(mon)
    except (ValueError, TypeError):
        return None


# =====================================================================
# Classification
# =====================================================================
CLASSIFIER_SYSTEM = """You route a question about a flooring and blinds business to \
exactly one pre-written answerer. You never answer the question yourself, and you \
never see any business data.

Return the name of the single best-matching answerer and any arguments it needs.

Rules:
- Pick from the provided list only. If nothing fits, return answerer: null.
- If the question is ambiguous in a way that changes which answerer runs, or a \
required argument is genuinely unclear, return a short clarifying question in \
"clarify" and leave answerer null. Ask rather than guess.
- Only fill an argument the question actually supplies. Never invent a product \
name, a colour, a date or a year that the person did not mention.
- "years" is how many years BACK to compare (e.g. [1, 2] for last year and the \
year before), not calendar years.
- "month" is YYYY-MM."""

CLASSIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "answerer": {"type": ["string", "null"]},
        "clarify": {"type": ["string", "null"]},
        "args": {
            "type": "object",
            "properties": {
                "product": {"type": ["string", "null"]},
                "colour": {"type": ["string", "null"]},
                "month": {"type": ["string", "null"]},
                "from_date": {"type": ["string", "null"]},
                "to_date": {"type": ["string", "null"]},
                "years": {"type": ["array", "null"], "items": {"type": "integer"}},
            },
            "required": ["product", "colour", "month", "from_date", "to_date", "years"],
            "additionalProperties": False,
        },
    },
    "required": ["answerer", "clarify", "args"],
    "additionalProperties": False,
}


def _post(body, timeout):
    if not ANTHROPIC_API_KEY:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set on this server — Ask Bolton needs it. "
            "Set it in Render's environment (never committed to source)."
        )
    req = urllib.request.Request(
        ANTHROPIC_API_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={"content-type": "application/json",
                 "x-api-key": ANTHROPIC_API_KEY,
                 "anthropic-version": "2023-06-01"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError("Claude API error (%s): %s"
                           % (e.code, e.read().decode("utf-8", errors="replace")[:300]))
    except TimeoutError:
        raise RuntimeError("Claude didn't answer within %ss — try again." % timeout)
    except urllib.error.URLError as e:
        if isinstance(e.reason, TimeoutError):
            raise RuntimeError("Claude didn't answer within %ss — try again." % timeout)
        raise RuntimeError("Could not reach the Claude API: %s" % e.reason)


def _text_of(api_result):
    return "".join(b.get("text", "") for b in api_result.get("content", [])
                   if b.get("type") == "text")


def classify(question, role):
    """Question -> which answerer, with what arguments.

    The catalogue is filtered to the asker's role BEFORE it is sent, so
    an owner-only answerer is not even nameable by a Sales user. The
    real gate is still the check in answer_question() — this only means
    the model is never invited to reach for something it cannot have.
    """
    options = [{"name": a["name"], "answers": a["asks"], "arguments": list(a["args"])}
               for a in _catalogue_for(role)]
    body = {
        "model": CLASSIFIER_MODEL,
        "max_tokens": 1000,
        "system": CLASSIFIER_SYSTEM,
        "output_config": {"format": {"type": "json_schema", "schema": CLASSIFY_SCHEMA}},
        "messages": [{"role": "user", "content": json.dumps(
            {"answerers": options, "question": question[:MAX_QUESTION_CHARS]})}],
    }
    result = _post(body, CLASSIFY_TIMEOUT_SECONDS)
    if result.get("stop_reason") == "refusal":
        raise RuntimeError("Claude declined to classify that question.")
    try:
        parsed = json.loads(_text_of(result))
    except ValueError:
        raise RuntimeError("Claude's reply wasn't usable — try rephrasing the question.")
    args = {k: v for k, v in (parsed.get("args") or {}).items() if v not in (None, "", [])}
    return parsed.get("answerer"), args, parsed.get("clarify")


PHRASING_SYSTEM = """You turn figures that have already been calculated into one or \
two plain sentences for the owner or staff of a South African flooring and blinds \
business.

Absolute rules:
- Use ONLY the numbers given to you. Never calculate a new one, never estimate, \
never round in a way that changes the figure.
- Rands are written like R12 500 or R12 500,40.
- If "gaps" is non-empty, say the relevant one plainly in your answer. Do not \
soften it or work around it.
- No preamble, no restating the question, no offer of further help. Two sentences \
at most. The table of figures is shown to the reader underneath your sentence, so \
do not list every row."""


def phrase(question, payload):
    body = {
        "model": PHRASING_MODEL,
        "max_tokens": 400,
        "system": PHRASING_SYSTEM,
        "messages": [{"role": "user", "content": json.dumps(
            {"question": question, "result": payload}, default=str)}],
    }
    result = _post(body, PHRASE_TIMEOUT_SECONDS)
    if result.get("stop_reason") == "refusal":
        return ""
    return _text_of(result).strip()


# =====================================================================
# The one entry point.
# =====================================================================
def answer_question(session, tenant_id, role, question, today, want_phrasing=True):
    """Classify, CHECK THE ROLE, run, then phrase.

    The order is the security property. Classification is allowed to
    select anything; execution is not allowed to happen until the
    answerer's own declared roles have been checked against the caller's
    real role, which arrives here from get_current_role() and therefore
    already honours Owner Preview Mode.
    """
    question = (question or "").strip()
    if not question:
        return {"ok": False, "error": "Ask a question first."}
    if len(question) > MAX_QUESTION_CHARS:
        return {"ok": False, "error": "That question is too long — keep it under %d characters."
                                      % MAX_QUESTION_CHARS}

    name, args, clarify = classify(question, role)

    if clarify and not name:
        return {"ok": True, "kind": "clarify", "question": question, "clarify": clarify}

    entry = _find(name) if name else None
    if entry is None:
        return {
            "ok": True, "kind": "unsupported", "question": question,
            "message": "I can't answer that one yet.",
            "can_answer": [a["asks"] for a in _catalogue_for(role)],
        }

    # THE GATE. Deliberately re-checked against the catalogue entry
    # rather than trusting that the filtered list sent to the model was
    # respected — the model's output is a request, and a request is
    # checked.
    if role not in entry["roles"]:
        return {"ok": False, "error": "That information is only available to the Owner."}

    ctx = {"today": today, "role": role}
    result = entry["fn"](session, tenant_id, args, ctx)

    if result.get("clarify"):
        return {"ok": True, "kind": "clarify", "question": question,
                "clarify": result["clarify"], "answerer": entry["name"]}

    payload = {
        "headline": result.get("headline") or "",
        "rows": result.get("rows") or [],
        "figures": result.get("figures") or {},
        "gaps": result.get("gaps") or [],
        # Shown with every answer, never optional: the person reading it
        # can see which part of Bolton the figure came from without
        # having to trust the sentence.
        "sources": list(entry["sources"]),
    }
    out = {"ok": True, "kind": "answer", "question": question,
           "answerer": entry["name"], "args": args, **payload}
    if want_phrasing:
        try:
            out["answer"] = phrase(question, payload)
        except RuntimeError:
            # The figures are the answer; the sentence is a convenience.
            # A phrasing failure must never lose a correct result.
            out["answer"] = ""
    return out
