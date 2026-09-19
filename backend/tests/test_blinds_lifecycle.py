# -*- coding: utf-8 -*-
"""Can a BLINDS job be walked all the way to closed, like a flooring one?

Run:  python backend/tests/test_blinds_lifecycle.py

WHY THIS EXISTS. In September 2026, 38 blinds jobs sat in production and
not one had ever passed "accepted" - no blinds job had ever been
scheduled or closed, while flooring jobs reached closed routinely. That
is the shape of a blocked path, so it was worth proving rather than
assuming either way.

It is not blocked. accept -> schedule -> complete carries a blinds-only
job exactly as it carries a flooring one; none of those three endpoints
looks at job_category at all, and the only place is_blinds_only appears
in the workflow UI changes a word ("blinds" vs "materials"), never a
button. The gap was operational, not mechanical.

This test exists so that stays true: if a product gate is ever added to
that path, this fails instead of 38 more jobs quietly piling up.

Runs against a throwaway SQLite file - never production, never the dev
database.
"""

import os
import sys
import tempfile

BE = r"C:\Users\burge\blinds-flooring-bolton\bolton\backend"
sys.path.insert(0, BE)
os.chdir(BE)

tmp = os.path.join(tempfile.gettempdir(), "blinds_close_test.db")
if os.path.exists(tmp):
    os.remove(tmp)
os.environ["DATABASE_URL"] = "sqlite:///" + tmp.replace("\\", "/")

import main  # noqa: E402
from sqlmodel import Session, select  # noqa: E402
from models import Quote, QuoteLineItem, BusinessSettings, BlindsProduct  # noqa: E402

main.on_startup()
T = "1"

with Session(main.engine) as s:
    if not s.exec(select(BusinessSettings).where(BusinessSettings.tenant_id == T)).first():
        s.add(BusinessSettings(tenant_id=T, business_name="Test"))
        s.commit()

    # A blinds-only quote, exactly the shape the business produces:
    # "I will always dish out two separate quotes for flooring and one
    # for blinds."
    q = Quote(tenant_id=T, client_name="Blinds Close Test", workflow_status="quoted",
              status="sent", branch="hermanus", sales_owner="ryno")
    s.add(q)
    s.flush()
    prod = s.exec(select(BlindsProduct)).first()
    s.add(QuoteLineItem(tenant_id=T, quote_id=q.id, category="blinds",
                        product_id=prod.id if prod else 1,
                        product_name="Test Roller Blind", blind_qty=3,
                        width_mm=1200, drop_mm=1500))
    s.commit()
    qid = q.id

print("blinds-only quote created, id=%s" % qid)


def stage_now():
    with Session(main.engine) as s:
        q = s.get(Quote, qid)
        return q.workflow_status, q.job_number, q.completion_date


def step(name, fn):
    try:
        fn()
        st, jn, cd = stage_now()
        print("  %-34s -> status=%-10s job=%-8s completion=%s"
              % (name, st, jn or "-", cd or "-"))
        return True
    except Exception as e:
        msg = getattr(e, "detail", None) or str(e)
        print("  %-34s -> BLOCKED: %s" % (name, str(msg)[:90]))
        return False


print()
print("walking a BLINDS-ONLY job through the same path a flooring job takes:")
st, _, _ = stage_now()
print("  %-34s -> status=%s" % ("(start)", st))

ok = True
ok &= step("accept_quote()", lambda: main.accept_quote(qid, tenant_id=T))
ok &= step("schedule_quote(installation_date)",
           lambda: main.schedule_quote(qid, installation_date="2026-09-25", tenant_id=T))
ok &= step("update_quote_materials(ready=True)",
           lambda: main.update_quote_materials(qid, ready_for_installation=True, tenant_id=T))
ok &= step("complete_quote()", lambda: main.complete_quote(qid, tenant_id=T))

final, job_no, comp = stage_now()
print()
print("FINAL STATUS: %s   job number: %s   completion date: %s" % (final, job_no, comp))
print()
if final == "completed" and ok:
    print("RESULT: a blinds job CAN be closed, by exactly the same path as flooring.")
else:
    print("RESULT: a blinds job CANNOT be closed - something in the path refused it.")

# and what the Order Index would show for it
with Session(main.engine) as s:
    q = s.get(Quote, qid)
    lines = s.exec(select(QuoteLineItem).where(QuoteLineItem.quote_id == qid)).all()
    print()
    print("job_category derived from its lines: %r"
          % main._job_category({l.category for l in lines}))
    print("ready_for_installation (stock arrived): %s" % q.ready_for_installation)

try:
    os.remove(tmp)
    print()
    print("throwaway database removed")
except Exception:
    pass
