# -*- coding: utf-8 -*-
"""Are a quote's stored totals right, and do they STAY right?

Run:  python backend/tests/test_quote_totals_stored.py

WHY THIS EXISTS. Ask Bolton answers questions by writing real SQL. A figure
that only exists inside a Python function while a page renders is invisible to
SQL, so "who still owes me money?" was a question Bolton could not answer about
its own business -- it refused, correctly but uselessly. Quote.total_incl_vat
and Quote.amount_outstanding fix that by writing the answer down.

Which means Bolton now has a CACHE of a number it used to compute fresh every
time, and a stale cache answers confidently and wrongly. That is strictly worse
than the honest refusal it replaced. So the thing worth testing is not "can it
store a number" -- it is:

    does the stored number track every way the real one can change,
    and does the safety net actually fire when it does not?

The mechanism is a session-level hook (_collect_quotes_needing_totals /
_write_quote_totals, main.py) rather than a call at each of the 57
quote-mutating endpoints, for the same reason _reconcile_model_columns()
derives its work from the models instead of a hand-written list: a list of
places to remember IS the bug. This test leans on that -- it drives real
Session commits, which is the one path every endpoint in the app goes through
-- and section A checks that premise directly rather than assuming it.

Runs against a throwaway SQLite file - never production, never the dev database.
"""

import os
import re
import sys
import tempfile

BE = r"C:\Users\burge\blinds-flooring-bolton\bolton\backend"
sys.path.insert(0, BE)
os.chdir(BE)

tmp = os.path.join(tempfile.gettempdir(), "bolton_stored_totals_test.db")
if os.path.exists(tmp):
    os.remove(tmp)
os.environ["DATABASE_URL"] = "sqlite:///" + tmp.replace("\\", "/")

import main                                                       # noqa: E402
from sqlalchemy import text                                       # noqa: E402
from sqlmodel import Session, select                              # noqa: E402
from models import (BusinessSettings, Quote, QuoteLineItem,       # noqa: E402
                    QuotePayment)

main.on_startup()
T = "1"

failures = []


def check(label, condition, detail=""):
    print(("  ok   " if condition else "  FAIL ") + label + (("  " + detail) if detail else ""))
    if not condition:
        failures.append(label + ((" -- " + detail) if detail else ""))


def money(value):
    return "-" if value is None else ("R%0.2f" % value)


def real_totals(session, quote):
    """What the SCREEN would show -- computed from scratch, never read."""
    totals = main._quote_totals_for(session, quote, T)
    state = main._quote_payment_state(
        quote, totals, main._quote_payments(session, quote.id, T))
    return totals["total_incl_vat"], state["amount_outstanding"]


def agrees(quote_id, label):
    """Stored == computed, for one quote, read back fresh from the database."""
    with Session(main.engine) as s:
        quote = s.get(Quote, quote_id)
        real_total, real_out = real_totals(s, quote)
        ok = (quote.total_incl_vat is not None
              and abs(quote.total_incl_vat - real_total) <= 0.01
              and quote.amount_outstanding is not None
              and abs(quote.amount_outstanding - real_out) <= 0.01)
        check(label, ok,
              "stored %s / %s  vs real %s / %s"
              % (money(quote.total_incl_vat), money(quote.amount_outstanding),
                 money(real_total), money(real_out)))
        return quote.total_incl_vat, quote.amount_outstanding


def new_quote(session, name, line_totals, **kwargs):
    quote = Quote(tenant_id=T, client_name=name, workflow_status="quoted",
                  status="sent", branch="hermanus", sales_owner="ryno", **kwargs)
    session.add(quote)
    session.flush()
    for amount in line_totals:
        session.add(QuoteLineItem(
            tenant_id=T, quote_id=quote.id, category="flooring", product_id=1,
            product_name="Aspen Herringbone Range 2mm", line_total=amount))
    return quote


with Session(main.engine) as s:
    settings = s.exec(select(BusinessSettings).where(BusinessSettings.tenant_id == T)).first()
    if settings is None:
        settings = BusinessSettings(tenant_id=T, business_name="Test")
        s.add(settings)
        s.commit()
    VAT = settings.vat_pct

print()
print("=" * 72)
print("A. THE PREMISE -- every endpoint really does go through one door")
print("=" * 72)
print("""  The hook is registered on sqlmodel's Session. That only covers all 57
  quote-mutating endpoints if all 57 actually build their session that way.
  If somebody ever opens a raw SQLAlchemy session instead, their writes skip
  the refresh silently and Ask Bolton starts quoting a stale number -- with
  nothing anywhere saying so. Checked, not assumed.""")
print()

with open(os.path.join(BE, "main.py"), encoding="utf-8") as fh:
    main_source = fh.read()
standard = len(re.findall(r"with Session\(engine\)", main_source))
# Any OTHER way of constructing a database session in this file.
others = [m for m in re.findall(r"^\s*(?:\w+\s*=\s*)?Session\((?!engine\))[^)]*\)",
                                main_source, re.M)]
print("  with Session(engine) : %d" % standard)
print("  any other session    : %d   %s" % (len(others), others or ""))
check("every database session in main.py is one the hook is attached to",
      not others, "" if not others else "these bypass the refresh: %s" % others)

print()
print("=" * 72)
print("B. NOBODY CALLS THE REFRESHER -- the hook does")
print("=" * 72)
print("  Every mutation below is a plain Session commit, exactly as an endpoint")
print("  does it. _refresh_quote_totals() is never called by this test.")
print()

with Session(main.engine) as s:
    q = new_quote(s, "Huis Stored Totals", [1000.0])
    s.commit()
    quote_id = q.id

expected = round(1000.0 * (1 + VAT), 2)
with Session(main.engine) as s:
    stored = s.get(Quote, quote_id)
    check("a new quote gets a stored total on the same commit",
          stored.total_incl_vat is not None, money(stored.total_incl_vat))
    check("and it is the real figure (R1000 + %.0f%% VAT)" % (VAT * 100),
          stored.total_incl_vat is not None and abs(stored.total_incl_vat - expected) <= 0.01,
          "expected %s, got %s" % (money(expected), money(stored.total_incl_vat)))
    check("nothing is paid yet, so the whole total is outstanding",
          stored.amount_outstanding is not None
          and abs(stored.amount_outstanding - expected) <= 0.01,
          money(stored.amount_outstanding))
    check("and the write is timestamped", stored.totals_refreshed_at is not None,
          str(stored.totals_refreshed_at))

with Session(main.engine) as s:
    s.add(QuoteLineItem(tenant_id=T, quote_id=quote_id, category="flooring",
                        product_id=1, product_name="Trim", line_total=500.0))
    s.commit()
agrees(quote_id, "adding a line moves the total")

with Session(main.engine) as s:
    line = s.exec(select(QuoteLineItem).where(QuoteLineItem.quote_id == quote_id)).first()
    line.line_total = 2000.0
    s.add(line)
    s.commit()
agrees(quote_id, "editing a line moves the total")

with Session(main.engine) as s:
    line = s.exec(select(QuoteLineItem).where(QuoteLineItem.quote_id == quote_id)).first()
    s.delete(line)
    s.commit()
agrees(quote_id, "deleting a line moves the total")

with Session(main.engine) as s:
    quote = s.get(Quote, quote_id)
    quote.discount_pct = 0.10
    s.add(quote)
    s.commit()
agrees(quote_id, "a discount moves the total")

with Session(main.engine) as s:
    quote = s.get(Quote, quote_id)
    quote.transport_levy = 250.0
    s.add(quote)
    s.commit()
agrees(quote_id, "a transport levy moves the total")

print()
print("=" * 72)
print("C. PAYMENTS -- the outstanding figure is the point of the exercise")
print("=" * 72)

before_total, before_out = agrees(quote_id, "starting point")

with Session(main.engine) as s:
    s.add(QuotePayment(tenant_id=T, quote_id=quote_id, amount=400.0,
                       paid_date=main.datetime.utcnow().date(), payment_type="deposit"))
    s.commit()
after_total, after_out = agrees(quote_id, "recording a payment reduces what is owed")
check("and it reduced it by exactly what was paid",
      after_out is not None and before_out is not None
      and abs((before_out - after_out) - 400.0) <= 0.01,
      "%s -> %s" % (money(before_out), money(after_out)))
check("while the total itself did not move",
      abs(after_total - before_total) <= 0.01)

with Session(main.engine) as s:
    s.add(QuotePayment(tenant_id=T, quote_id=quote_id, amount=round(after_out, 2),
                       paid_date=main.datetime.utcnow().date(), payment_type="final"))
    s.commit()
_, settled = agrees(quote_id, "paying the rest settles it")
check("a fully paid job owes exactly zero, not a rounding crumb",
      settled is not None and abs(settled) <= 0.01, money(settled))

with Session(main.engine) as s:
    payment = s.exec(select(QuotePayment).where(
        QuotePayment.quote_id == quote_id, QuotePayment.payment_type == "final")).first()
    s.delete(payment)
    s.commit()
_, reopened = agrees(quote_id, "deleting a payment puts the money back on the books")
check("and it is owed again, not stuck at zero",
      reopened is not None and reopened > 0.01, money(reopened))

print()
print("=" * 72)
print("D. THE AWKWARD CASES")
print("=" * 72)

with Session(main.engine) as s:
    quote = s.get(Quote, quote_id)
    quote.manual_override_total_incl_vat = 9999.00
    s.add(quote)
    s.commit()
overridden, _ = agrees(quote_id, "a manually agreed total overrides the calculated one")
check("and the stored figure IS the agreed number, not the calculation",
      overridden is not None and abs(overridden - 9999.00) <= 0.01, money(overridden))

with Session(main.engine) as s:
    quote = s.get(Quote, quote_id)
    quote.manual_override_total_incl_vat = None
    s.add(quote)
    s.commit()
agrees(quote_id, "removing the override returns to the calculated total")

with Session(main.engine) as s:
    doomed = new_quote(s, "Deleted Before Anyone Looked", [300.0])
    s.commit()
    doomed_id = doomed.id
with Session(main.engine) as s:
    quote = s.get(Quote, doomed_id)
    for line in s.exec(select(QuoteLineItem).where(QuoteLineItem.quote_id == doomed_id)).all():
        s.delete(line)
    s.delete(quote)
    s.commit()
with Session(main.engine) as s:
    check("deleting a quote does not resurrect it via a totals refresh",
          s.get(Quote, doomed_id) is None)

with Session(main.engine) as s:
    empty = new_quote(s, "Empty Draft", [])
    s.commit()
    empty_id = empty.id
with Session(main.engine) as s:
    quote = s.get(Quote, empty_id)
    check("an empty draft stores a real 0.00, which is not the same as NULL",
          quote.total_incl_vat == 0.0 and quote.totals_refreshed_at is not None,
          "total %s, refreshed %s" % (money(quote.total_incl_vat), quote.totals_refreshed_at))

print()
print("  VAT is not a property of a quote -- it is a business-wide setting every")
print("  stored total was computed through. Changing it invalidates all of them")
print("  at once, which is the one change a per-quote hook would miss.")
print()

with Session(main.engine) as s:
    other = new_quote(s, "Another Job Entirely", [750.0])
    s.commit()
    other_id = other.id

with Session(main.engine) as s:
    settings = s.exec(select(BusinessSettings).where(BusinessSettings.tenant_id == T)).first()
    settings.vat_pct = 0.20
    s.add(settings)
    s.commit()

agrees(quote_id, "the quote we have been editing follows a VAT change")
agrees(other_id, "and so does one nothing else touched")
with Session(main.engine) as s:
    quote = s.get(Quote, other_id)
    check("and the new VAT is really in the figure",
          abs(quote.total_incl_vat - round(750.0 * 1.20, 2)) <= 0.01,
          "expected %s, got %s" % (money(round(750.0 * 1.20, 2)), money(quote.total_incl_vat)))

with Session(main.engine) as s:
    settings = s.exec(select(BusinessSettings).where(BusinessSettings.tenant_id == T)).first()
    settings.vat_pct = VAT
    s.add(settings)
    s.commit()

print()
print("=" * 72)
print("E. THE SAFETY NET -- shown to fire, not merely present")
print("=" * 72)


def stored_totals_findings(session):
    out = []
    for f in main._run_consistency_checks(session, T):
        if f["entity_type"] == "Quote" or "Stored totals" in f["note"]:
            out.append(f)
    return out


with Session(main.engine) as s:
    clean = stored_totals_findings(s)
check("a healthy database raises nothing about stored totals", not clean,
      "" if not clean else str([f["note"][:70] for f in clean]))

# Corrupt a stored figure BEHIND the ORM's back, with raw SQL, so no event
# fires and nothing gets a chance to quietly put it right. This is what a real
# drift looks like: the number in the column is simply not the truth any more,
# and nothing in the app knows.
with main.engine.begin() as conn:
    conn.execute(text("UPDATE quote SET total_incl_vat = 123456.78 WHERE id = :i"),
                 {"i": quote_id})

with Session(main.engine) as s:
    drift = stored_totals_findings(s)
flagged = [f for f in drift if f["entity_id"] == quote_id]
check("the monitor catches a drifted total", bool(flagged))
if flagged:
    print("     %s" % flagged[0]["note"][:150])
    check("and the flag names both the stored figure and the real one",
          "123,456.78" in flagged[0]["note"] and "stored" in flagged[0]["note"])

with Session(main.engine) as s:
    quote = s.get(Quote, quote_id)
    check("the monitor did NOT quietly repair it -- detect and alert, never correct",
          abs(quote.total_incl_vat - 123456.78) <= 0.01, money(quote.total_incl_vat))

with main.engine.begin() as conn:
    conn.execute(text("UPDATE quote SET amount_outstanding = 0 WHERE id = :i"),
                 {"i": quote_id})
with Session(main.engine) as s:
    drift = [f for f in stored_totals_findings(s) if f["entity_id"] == quote_id]
check("a job wrongly showing as paid up is caught too", bool(drift),
      "" if drift else "a zeroed outstanding balance went unnoticed")

print()
print("  A row with no stored totals at all is a different problem with a")
print("  different fix, so it is reported differently -- once, not per quote.")
print()

with main.engine.begin() as conn:
    conn.execute(text("UPDATE quote SET total_incl_vat = NULL, amount_outstanding = NULL, "
                      "totals_refreshed_at = NULL"))
with Session(main.engine) as s:
    never = [f for f in stored_totals_findings(s) if "never written" in f["note"]]
check("quotes that have never had totals written are reported", bool(never))
if never:
    print("     %s" % never[0]["note"][:150])
    check("as ONE finding, not one per quote", len(never) == 1)
    check("and it is not called drift, because it is not",
          "drifted" not in never[0]["note"])

main._backfill_quote_totals()
with Session(main.engine) as s:
    still_null = s.exec(select(Quote).where(Quote.totals_refreshed_at.is_(None))).all()
check("the startup backfill fills them in", not still_null,
      "" if not still_null else "%d still NULL" % len(still_null))
with Session(main.engine) as s:
    after_backfill = stored_totals_findings(s)
check("and the database is clean again afterwards", not after_backfill,
      "" if not after_backfill else str([f["note"][:70] for f in after_backfill]))

print()
print("=" * 72)
print("F. ASK BOLTON CAN ACTUALLY REACH THEM")
print("=" * 72)

import ask_query                                                  # noqa: E402

quote_entry = next(t for t in ask_query.CATALOGUE if t["table"] == "quote")
for column in ("total_incl_vat", "amount_outstanding"):
    check("%s is in the catalogue" % column, column in quote_entry["columns"])
    described = quote_entry["columns"].get(column, "")
    check("and its description warns that NULL is not zero" % (),
          "NULL" in described and "not zero" in described.lower().replace("NOT zero", "not zero"),
          column)

check("the catalogue no longer claims this table has no money on it",
      "not available to you" not in quote_entry["what"])

for role in ("owner", "admin", "sales"):
    sql = ("SELECT client_name, amount_outstanding FROM quote "
           "WHERE tenant_id = :tenant_id AND amount_outstanding > 0 "
           "AND accepted_at IS NOT NULL AND declined_at IS NULL")
    if role == "sales":
        sql += " AND sales_owner = :sales_owner"
    ok, why = True, ""
    try:
        result = ask_query.validate_sql(sql, role)
        if isinstance(result, tuple):
            ok, why = result[0], str(result[1:])
        elif result is False:
            ok = False
    except Exception as exc:                                       # noqa: BLE001
        ok, why = False, str(exc)
    check("the validator accepts an outstanding-balance query as %s" % role, ok, why)

terms = [t for t in ask_query.VOCABULARY if "outstanding" in t[0]]
check("the business's own words for this map onto the column", bool(terms))
if terms:
    check("and the guidance says to exclude quotes nobody accepted",
          "accepted_at IS NOT NULL" in terms[0][1])

print()
print("=" * 72)
if failures:
    print("FAILURES: %d" % len(failures))
    for item in failures:
        print("  - " + item)
    print("=" * 72)
    sys.exit(1)
print("ALL CHECKS PASSED")
print()
print("A quote's stored total and outstanding balance track every way the real")
print("figures can change -- lines, discounts, levies, overrides, payments and")
print("even a VAT change -- without a single endpoint remembering to ask. When")
print("they are wrong anyway, the nightly monitor says so and does not pretend")
print("to fix it.")
print("=" * 72)
