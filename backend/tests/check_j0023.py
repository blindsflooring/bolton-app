# -*- coding: utf-8 -*-
"""J-0023, replayed.

The real row values for J-0023 (Marlize Louw), read out of production this
morning, inserted into the local fixture and put through the real pipeline:
validate_sql -> run_sql, at the Sales role, as Ryno, who owns the job.
Nothing here is invented; every number came off the live record.
"""
import os, sys, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
BE = r"C:\Users\burge\blinds-flooring-bolton\bolton\backend"
sys.path.insert(0, BE)
sys.path.insert(0, os.path.join(BE, "tests"))

import seed_askq
seed_askq.build()

from sqlmodel import Session, select
import main
from models import (Quote, QuoteLineItem, QuotePayment,
                    FlooringProduct, TrimProduct)

T = "1"
D = datetime.date

with Session(main.engine) as s:
    if s.exec(select(Quote).where(Quote.job_number == "J-0023")).first() is None:
        q = Quote(tenant_id=T, client_name="Marlize Louw", job_number="J-0023",
                  workflow_status="accepted", status="accepted", branch="gansbaai",
                  sales_owner="ryno", deposit_pct=0.70,
                  accepted_at=datetime.datetime(2026, 9, 15),
                  deposit_paid_date=D(2026, 9, 15),
                  final_payment_date=None, invoice_sent_date=None,
                  installation_date=None)
        s.add(q); s.flush()
        L = [  # category, m2, length_m, boxes, bags, pricing, sub, name
            ("flooring", 220.0, None, 72, 0, "material", None, "deZIGN series 200"),
            ("flooring", 220.0, None, None, 55, "screed", None, "ITE F10"),
            ("trim", None, 5.4, None, 0, None, "reducer",
             "Aluminium reducer, short length - Anodized Champagne"),
            ("trim", None, 3.7, None, 0, None, "angle",
             "Aluminium Equal Angle - Anodized Champagne"),
        ]
        # product_id is NOT NULL on this table - point the lines at the
        # fixture's own products; the questions are about quantities, not
        # which product row they hang off.
        floor_id = s.exec(select(FlooringProduct)).first().id
        trim_row = s.exec(select(TrimProduct)).first()
        trim_id = trim_row.id if trim_row else floor_id
        for cat, m2, lm, boxes, bags, pricing, sub, name in L:
            s.add(QuoteLineItem(tenant_id=T, quote_id=q.id, category=cat,
                                product_id=(trim_id if cat == "trim" else floor_id),
                                product_name=name, quantity_m2=m2, length_m=lm,
                                boxes_needed=boxes, bags_allowed=bags,
                                flooring_pricing_type=pricing, trim_sub_category=sub))
        s.add(QuotePayment(tenant_id=T, quote_id=q.id, amount=74081.88,
                           paid_date=D(2026, 9, 15), method="EFT",
                           payment_type="deposit", recorded_by="replay"))
        s.commit()

import ask_query as aq

TRUTH = {
    "screed_bags": 55,
    "trim_m": 9.1,
    "deposit_paid": "2026-09-15",
    "deposit_amount": 74081.88,
    "final_paid": None,
    "invoice_sent": None,
}

Q = ("SELECT q.id AS quote_id, q.job_number, q.client_name, "
     "SUM(CASE WHEN l.bags_allowed > 0 THEN l.bags_allowed ELSE 0 END) AS screed_bags, "
     "SUM(CASE WHEN l.category IN ('trim','skirting') THEN l.length_m ELSE 0 END) AS trim_m, "
     "q.deposit_paid_date, q.final_payment_date, q.invoice_sent_date "
     "FROM quote q JOIN quotelineitem l ON l.quote_id = q.id "
     "WHERE q.tenant_id = :tenant_id AND l.tenant_id = :tenant_id "
     "AND q.sales_owner = :sales_owner AND q.job_number = 'J-0023' "
     "GROUP BY q.id, q.job_number, q.client_name, q.deposit_paid_date, "
     "q.final_payment_date, q.invoice_sent_date LIMIT 10")

PAY = ("SELECT q.id AS quote_id, q.job_number, p.amount, p.paid_date, p.method, "
       "p.payment_type FROM quote q JOIN quotepayment p ON p.quote_id = q.id "
       "WHERE q.tenant_id = :tenant_id AND p.tenant_id = :tenant_id "
       "AND q.sales_owner = :sales_owner AND q.job_number = 'J-0023' LIMIT 20")

fails = []


def show(title, sql):
    print("\n--- %s" % title)
    validated = aq.validate_sql(sql, "sales", 1)
    cols, rows, trunc = aq.run_sql(validated, str(T), "sales", "ryno")
    for r in rows:
        for k in cols:
            print("    %-20s %s" % (k, r[k]))
    return rows


rows = show("the job, as Ask Bolton would read it", Q)
r = rows[0]

def chk(label, got, want):
    ok = got == want
    print("  %-26s got %-14s want %-14s %s"
          % (label, got, want, "MATCH" if ok else "*** DIFFERENT ***"))
    if not ok:
        fails.append(label)

print("\n=== COMPARISON WITH THE REAL JOB ===")
chk("screed bags", int(r["screed_bags"]), TRUTH["screed_bags"])
chk("trim metres", round(float(r["trim_m"]), 2), TRUTH["trim_m"])
chk("deposit paid date", str(r["deposit_paid_date"])[:10], TRUTH["deposit_paid"])
chk("final payment", r["final_payment_date"], TRUTH["final_paid"])
chk("invoice sent", r["invoice_sent_date"], TRUTH["invoice_sent"])

prows = show("the money, from the payment record only", PAY)
chk("deposit amount", round(float(prows[0]["amount"]), 2), TRUTH["deposit_amount"])
chk("payment count", len(prows), 1)

print("\n=== AND THE FIGURE IT MUST REFUSE TO INVENT ===")
for label, sql in [
    ("what the job is worth",
     "SELECT SUM(line_total) * 1.15 AS total FROM quotelineitem WHERE tenant_id = :tenant_id"),
    ("what is still owed",
     "SELECT SUM(l.line_total) * 1.15 - SUM(p.amount) AS owed FROM quote q "
     "JOIN quotelineitem l ON l.quote_id = q.id JOIN quotepayment p ON p.quote_id = q.id "
     "WHERE q.tenant_id = :tenant_id AND l.tenant_id = :tenant_id AND p.tenant_id = :tenant_id"),
]:
    try:
        aq.validate_sql(sql, "sales", 1)
        print("  %-24s *** ALLOWED - THIS IS A HOLE ***" % label)
        fails.append(label)
    except Exception as e:
        print("  %-24s refused: %s" % (label, str(e)[:60]))

print("\n" + "=" * 62)
print("RESULT: %s" % ("ALL MATCH" if not fails else "DIFFERENCES: %s" % fails))
print("=" * 62)
