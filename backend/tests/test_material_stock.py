# -*- coding: utf-8 -*-
"""Does Bolton actually know how much screed, glue, slurry and bondite is left?

Run:  python backend/tests/test_material_stock.py

WHY THIS EXISTS. The business ran short of bonding liquid and slurry with no
warning, and had no idea how much glue or screed was on hand. That stops jobs.

The feature that answers it is a CACHE OF REALITY, which is the dangerous kind
of feature: a tile that confidently says "8 bags" when there are two is worse
than a tile that says nothing, because somebody books work against it. So what
is worth testing is not "can it store a number" — it is:

  1. does on-hand actually FALL as jobs consume material, on its own;
  2. is "nobody has counted this" kept distinct from "there is none left";
  3. does a count that disagrees with Bolton get FLAGGED rather than accepted
     (the brief's own example: someone re-entering yesterday's number without
     going to look).

Point 3 is the one the brief cares most about, and it is the one a naive
implementation gets wrong by simply overwriting the balance.

Runs against a throwaway SQLite file - never production, never the dev database.
"""

import os
import sys
import tempfile
from datetime import date, timedelta

BE = r"C:\Users\burge\blinds-flooring-bolton\bolton\backend"
sys.path.insert(0, BE)
os.chdir(BE)

tmp = os.path.join(tempfile.gettempdir(), "bolton_material_stock_test.db")
if os.path.exists(tmp):
    os.remove(tmp)
os.environ["DATABASE_URL"] = "sqlite:///" + tmp.replace("\\", "/")

import main                                                          # noqa: E402
from sqlmodel import Session, select                                 # noqa: E402
from models import (BusinessSettings, Quote, QuoteLineItem, OrderSheet,   # noqa: E402
                    OrderSheetLine, StockPurchase, StockPurchaseLine,
                    StockMaterial)

main.on_startup()
T = "1"
TODAY = date(2026, 9, 21)
failures = []


def check(label, condition, detail=""):
    print(("  ok   " if condition else "  FAIL ") + label + (("  " + detail) if detail else ""))
    if not condition:
        failures.append(label + ((" -- " + detail) if detail else ""))


def overview():
    with Session(main.engine) as s:
        return {m["key"]: m for m in
                [main._stock_state(s, T, mat) for mat in main._stock_materials(s, T)]}


def count(key, qty, on=None, note=""):
    """Drives the real endpoint, not the helper — the reconciliation logic
    lives in the endpoint and testing around it would prove nothing."""
    body = main.StockCountRequest(material_key=key, counted_qty=qty,
                                  counted_on=on or TODAY, note=note)
    return main.record_stock_count(body, tenant_id=T, username="burgert")


def make_job(name, status, bags=0, glue=0, completion=None, floor_prep=None):
    with Session(main.engine) as s:
        q = Quote(tenant_id=T, client_name=name, workflow_status=status, status="accepted",
                  branch="hermanus", sales_owner="ryno", completion_date=completion)
        s.add(q)
        s.flush()
        s.add(QuoteLineItem(tenant_id=T, quote_id=q.id, category="flooring", product_id=1,
                            product_name="Screed / Vinyl", bags_allowed=bags,
                            glue_units_needed=glue))
        if floor_prep:
            sheet = OrderSheet(tenant_id=T, quote_id=q.id, sheet_type="floor_prep",
                               supplier="Azura", order_number="O-%d" % q.id,
                               created_by="burgert")
            s.add(sheet)
            s.flush()
            for product_name, qty in floor_prep:
                s.add(OrderSheetLine(tenant_id=T, order_sheet_id=sheet.id,
                                     product_name=product_name, quantity=qty, unit="drums"))
        s.commit()
        return q.id


with Session(main.engine) as s:
    if not s.exec(select(BusinessSettings).where(BusinessSettings.tenant_id == T)).first():
        s.add(BusinessSettings(tenant_id=T, business_name="Test"))
        s.commit()

print()
print("=" * 72)
print("A. THE FOUR MATERIALS, IN THE UNITS THAT WERE CONFIRMED")
print("=" * 72)

state = overview()
for key, label, unit in [("screed", "Screed", "bags"), ("glue", "Glue", "drums"),
                         ("slurry", "Slurry", "drums"), ("bondite", "Bondite", "drums")]:
    check("%s is tracked" % label, key in state)
    if key in state:
        check("  counted in %s" % unit, state[key]["unit_label"] == unit,
              state[key]["unit_label"])
        check("  in quarter increments", state[key]["increment"] == 0.25,
              str(state[key]["increment"]))
print("  glue tile reads: %r (%s)" % (state["glue"]["label"], state["glue"]["pack_note"]))
check("the glue tile names Teck 70/70, not GRIPiTe",
      "Teck" in state["glue"]["label"] and "GRIP" not in state["glue"]["label"])
check("slurry is the 30 kg drum", "30 kg" in state["slurry"]["pack_note"],
      state["slurry"]["pack_note"])

print()
print("=" * 72)
print("B. 'NOBODY HAS COUNTED' IS NOT 'THERE IS NONE LEFT'")
print("=" * 72)
print("  A tile reading 0 bags because nobody has looked is the exact false")
print("  confidence this feature exists to remove.")
print()

check("on-hand is unknown, not zero, before the first count",
      state["screed"]["on_hand"] is None, repr(state["screed"]["on_hand"]))
check("and it says so", state["screed"]["never_counted"] is True)
check("so it is not reported as short either",
      state["screed"]["is_short"] is False)

print()
print("=" * 72)
print("C. STOCK FALLS AS JOBS CONSUME IT, WITH NOBODY TOUCHING IT")
print("=" * 72)
print()

result = count("screed", 40)
print("  counted 40 bags -> %s" % result["message"][:96])
check("the first count has nothing to compare against", result["expected_qty"] is None)
check("so no variance is invented", result["variance"] is None)
check("and it is not flagged as a mismatch", result["variance_flagged"] is False)
check("on-hand is now the counted figure", overview()["screed"]["on_hand"] == 40)

count("glue", 3.0)
count("slurry", 2.0)
count("bondite", 1.5)

# Work finished AFTER the count: the material has left the shelf.
make_job("Finished Yesterday", "completed", bags=12, glue=0.0,
         completion=TODAY + timedelta(days=1))
state = overview()
print()
print("  a completed job used 12 bags -> on hand %s" % state["screed"]["on_hand"])
check("on-hand dropped by exactly what the job used",
      state["screed"]["on_hand"] == 28, str(state["screed"]["on_hand"]))
check("and Bolton can say how much has gone since the count",
      state["screed"]["consumed_since_count"] == 12)

# Work NOT finished: nothing has left the shelf, but it is coming.
make_job("Booked For Friday", "scheduled", bags=20, glue=2)
state = overview()
check("an unfinished job does NOT deduct from on-hand",
      state["screed"]["on_hand"] == 28, str(state["screed"]["on_hand"]))
check("it shows up as needed instead", state["screed"]["needed"] == 20,
      str(state["screed"]["needed"]))
check("glue needed is counted in drums off the same job",
      state["glue"]["needed"] == 2, str(state["glue"]["needed"]))

make_job("Declined, not real work", "accepted", bags=999)
with Session(main.engine) as s:
    q = s.exec(select(Quote).where(Quote.client_name == "Declined, not real work")).first()
    q.declined_at = main.datetime.utcnow()
    s.add(q)
    s.commit()
check("a declined quote is not counted as demand",
      overview()["screed"]["needed"] == 20, str(overview()["screed"]["needed"]))

print()
print("=" * 72)
print("D. SLURRY AND BONDITE, WHICH LIVE SOMEWHERE ELSE ENTIRELY")
print("=" * 72)
print("  Neither has a quantity on the quote line. They are read off")
print("  floor-prep order sheets, matched by product name as a supplier")
print("  typed it.")
print()

make_job("Floor Prep Job", "scheduled", floor_prep=[("iTe SLURRY (30kg)", 3),
                                                    ("BONDiTe (25L)", 2),
                                                    ("Aspen Herringbone 2mm", 40)])
state = overview()
check("slurry needed is read off the floor-prep sheet",
      state["slurry"]["needed"] == 3, str(state["slurry"]["needed"]))
check("bondite too", state["bondite"]["needed"] == 2, str(state["bondite"]["needed"]))
check("and a flooring line on the same sheet is not mistaken for either",
      state["slurry"]["needed"] == 3 and state["bondite"]["needed"] == 2)

with Session(main.engine) as s:
    mat = s.exec(select(StockMaterial).where(StockMaterial.key == "slurry")).first()
    for spelling in ["iTe SLURRY (30kg)", "Slurry 30kg", "ITE SLURRY", "slurry"]:
        check("  matches %r as a supplier might type it" % spelling,
              main._stock_matches(mat, spelling))
    check("  and does not match something else entirely",
          not main._stock_matches(mat, "Aspen Herringbone 2mm"))

print()
print("=" * 72)
print("E. ON ORDER, SO on-hand + on-order vs NEEDED IS VISIBLE")
print("=" * 72)
print()

with Session(main.engine) as s:
    po = StockPurchase(tenant_id=T, supplier="Azura", ordered_on=TODAY, status="ordered")
    s.add(po)
    s.flush()
    s.add(StockPurchaseLine(tenant_id=T, stock_purchase_id=po.id,
                            description="iTe SLURRY (30kg)", qty=6, unit="drums"))
    delivered = StockPurchase(tenant_id=T, supplier="Azura", ordered_on=TODAY, status="received")
    s.add(delivered)
    s.flush()
    s.add(StockPurchaseLine(tenant_id=T, stock_purchase_id=delivered.id,
                            description="iTe SLURRY (30kg)", qty=99, unit="drums"))
    cancelled = StockPurchase(tenant_id=T, supplier="Azura", ordered_on=TODAY, status="cancelled")
    s.add(cancelled)
    s.flush()
    s.add(StockPurchaseLine(tenant_id=T, stock_purchase_id=cancelled.id,
                            description="iTe SLURRY (30kg)", qty=50, unit="drums"))
    s.commit()

state = overview()
check("an ordered-but-not-delivered purchase shows as on order",
      state["slurry"]["on_order"] == 6, str(state["slurry"]["on_order"]))
check("a RECEIVED order does not double-count (it is already on the shelf)",
      state["slurry"]["on_order"] == 6, str(state["slurry"]["on_order"]))
check("and a cancelled one was never coming",
      state["slurry"]["on_order"] == 6, str(state["slurry"]["on_order"]))
check("available is on-hand plus on-order",
      state["slurry"]["available"] == 8, str(state["slurry"]["available"]))

print()
print("=" * 72)
print("F. THE WARNING FIRES WHEN IT SHOULD, AND NOT WHEN IT SHOULD NOT")
print("=" * 72)
print()

state = overview()
print("  slurry  on hand %s + on order %s = %s, needed %s"
      % (state["slurry"]["on_hand"], state["slurry"]["on_order"],
         state["slurry"]["available"], state["slurry"]["needed"]))
check("plenty of slurry is not a warning", state["slurry"]["is_short"] is False)

print("  bondite on hand %s + on order %s = %s, needed %s"
      % (state["bondite"]["on_hand"], state["bondite"]["on_order"],
         state["bondite"]["available"], state["bondite"]["needed"]))
check("bondite IS short (1.5 on hand, none on order, 2 needed)",
      state["bondite"]["is_short"] is True)
check("and says by how much", state["bondite"]["short_by"] == 0.5,
      str(state["bondite"]["short_by"]))

with Session(main.engine) as s:
    result = main.stock_overview(tenant_id=T)
check("the overview endpoint surfaces the short list",
      [m["key"] for m in result["short"]] == ["bondite"],
      str([m["key"] for m in result["short"]]))
check("and counts what needs attention", result["needs_attention"] >= 1)

print()
print("=" * 72)
print("G. THE RECONCILIATION CHECK -- the point of the whole thing")
print("=" * 72)
print("  The brief's own example: somebody re-enters yesterday's number")
print("  without going to look. Bolton knows 12 bags were used, so the")
print("  same number cannot also be true.")
print()

result = count("screed", 40, on=TODAY + timedelta(days=2))
print("  re-entered 40 -> %s" % result["message"])
print()
check("the mismatch is caught, not accepted", result["variance_flagged"] is True)
check("Bolton expected 28", result["expected_qty"] == 28, str(result["expected_qty"]))
check("and reports the gap as +12", result["variance"] == 12, str(result["variance"]))
check("the message says MORE than it can account for",
      "MORE" in result["message"])
check("it is not silently corrected -- the count stands as entered",
      overview()["screed"]["on_hand"] == 40, str(overview()["screed"]["on_hand"]))
check("and the flag survives on the tile for anyone looking",
      overview()["screed"]["last_variance_flagged"] is True)

make_job("Used Three Drums", "completed", glue=3, completion=TODAY + timedelta(days=3))
result = count("glue", 0.0, on=TODAY + timedelta(days=4))
print("  glue counted 0 after 3 drums used, expected %s -> %s"
      % (result["expected_qty"], result["message"][:90]))
check("a count matching expectation is NOT flagged", result["variance_flagged"] is False,
      str(result["variance"]))
check("and a real zero is accepted as a real figure",
      overview()["glue"]["on_hand"] == 0.0, str(overview()["glue"]["on_hand"]))
check("which is different from never having counted",
      overview()["glue"]["never_counted"] is False)

print()
print("=" * 72)
print("H. THE UNIT IS ENFORCED, SO A TYPO IS NOT A SILENT FIGURE")
print("=" * 72)
print()

for bad in (0.3, 1.1, 2.6):
    try:
        count("bondite", bad, on=TODAY + timedelta(days=5))
        check("%g drums is rejected" % bad, False, "it was accepted")
    except main.HTTPException as exc:
        check("%g drums is rejected" % bad, exc.status_code == 400,
              str(exc.detail)[:70])
for good in (0.25, 0.5, 2.75, 3.0):
    try:
        count("bondite", good, on=TODAY + timedelta(days=5))
        check("%g drums is accepted" % good, True)
    except main.HTTPException as exc:
        check("%g drums is accepted" % good, False, str(exc.detail)[:70])

try:
    count("unobtanium", 1)
    check("an unknown material is refused", False, "it was accepted")
except main.HTTPException as exc:
    check("an unknown material is refused", exc.status_code == 404)
try:
    count("screed", -5)
    check("a negative count is refused", False, "it was accepted")
except main.HTTPException as exc:
    check("a negative count is refused", exc.status_code == 400)

print()
print("=" * 72)
print("I. THE DISAGREEMENT IS KEPT, NOT RECOMPUTED AWAY")
print("=" * 72)
print()

history = main.stock_count_history("screed", tenant_id=T)
print("  screed history: %s" % [(h["counted_on"], h["counted_qty"], h["variance"])
                                for h in history])
check("every count is kept, including the ones that disagreed", len(history) == 2)
check("newest first", history[0]["counted_qty"] == 40 and history[0]["variance"] == 12)
check("the first count still records no expectation",
      history[-1]["expected_qty"] is None)
check("and who counted it", history[0]["counted_by"] == "burgert")

# The stored variance must be a fact about the moment of counting. If it were
# recomputed against today's data it would quietly change, and the record of
# "this count was wrong" would erase itself.
make_job("Later Job", "completed", bags=5, completion=TODAY + timedelta(days=9))
after = main.stock_count_history("screed", tenant_id=T)
check("a later job does not rewrite an old count's variance",
      after[0]["variance"] == 12, str(after[0]["variance"]))

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
print("On-hand falls as jobs finish, without anyone touching it. A count that")
print("disagrees with Bolton is reported rather than accepted. And 'nobody has")
print("looked' never renders as 'there is none left'.")
print("=" * 72)
