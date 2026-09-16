# -*- coding: utf-8 -*-
"""Fixture for the Ask Bolton agent tests.

Two halves, because the boundary now runs between them:

  * Imported Order Index history (2017-2025) - OWNER ONLY.
  * Live 2026 jobs, shaped like the real Order Index - everyone.

Plus a FinancialStatement row, present so a red-team bypass has
something real to try to steal. If the allow-list ever leaks, that row
is what leaks.

EXPECTED holds answers computed here in plain Python, independently of
any SQL, so a correctness test compares the agent's query against
arithmetic rather than against another query. Every live job below
exists to make one of the brief's five example questions either right or
wrong.
"""
import os
import sys
from datetime import date, datetime, timedelta

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SP = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(SP, "bolton-askq.db")

_built = False

# ---------------------------------------------------------------- history
YEARS = [
    (2017, 2859814.0, 1801005.0, 412, 1.00, True),
    (2018, 4019285.0, 2679872.0, 548, 1.00, True),
    (2019, 3734453.0, 2552311.0, 501, 1.00, True),
    (2020, 3102900.0, 2011880.0, 398, 1.00, True),
    (2021, 4805041.0, 3069136.0, 612, 1.00, True),
    (2022, 5271071.0, 3735152.0, 702, 0.52, False),
    (2023, 5980220.0, 3990110.0, 744, 0.83, False),
    (2024, 6410500.0, 4102720.0, 801, 1.00, True),
    (2025, 6912340.0, 4356870.0, 838, 1.00, True),
]
MONTH_LABELS = ["Mar", "Apr", "May", "Jun", "Jul", "Aug",
                "Sep", "Oct", "Nov", "Dec", "Jan", "Feb"]
MONTH_SHARE = [0.07, 0.08, 0.09, 0.07, 0.06, 0.08, 0.09, 0.10, 0.12, 0.09, 0.07, 0.08]

VAT = 0.15

# ------------------------------------------------------------- live jobs
#
# (job_no, client, status, branch, bags, trim_lm, line_ex_vat,
#  deposit_paid, final_paid, order_placed, invoiced)
#
# "open and queued" = accepted or scheduled, i.e. not installed yet -
# the same boundary the Order Index screen itself uses.
LIVE = [
    ("J-101", "Van Wyk",   "accepted",  "Gansbaai", 25,  18.5, 49200.0, False, False, False, False),
    ("J-102", "Botha",     "scheduled", "Hermanus", 12,  40.0, 27100.0, True,  False, True,  False),
    ("J-103", "Nel",       "completed", "Gansbaai",  0,   0.0, 18000.0, True,  True,  True,  True),
    ("J-104", "Meyer",     "accepted",  "Hermanus",  8,  12.0, 15400.0, False, False, False, False),
    ("J-105", "Louw",      "scheduled", "Gansbaai", 15,  22.5, 31800.0, True,  False, True,  False),
    ("J-106", "Pretorius", "completed", "Hermanus",  0,   6.0, 22000.0, True,  False, True,  True),
]
PENDING = ("accepted", "scheduled")


def _expected():
    open_queued = [j for j in LIVE if j[2] in PENDING]

    def incl(ex):
        return round(ex * (1 + VAT), 2)

    # Outstanding deposit: an open/queued job whose deposit has not landed.
    deposits_out = [j for j in open_queued if not j[7]]
    # Outstanding final: invoiced or installed, and not settled.
    finals_out = [j for j in LIVE if j[10] and not j[8]]
    return {
        "history_year_count": len(YEARS),
        "history_total_sales": round(sum(y[1] for y in YEARS), 2),

        "live_job_count": len(LIVE),
        "open_queued_count": len(open_queued),
        # Q: how many bags of screed across all open and queued jobs?
        "screed_bags_open_queued": sum(j[4] for j in open_queued),
        # Q: how many trims across jobs still needing installation?
        "trim_lm_open_queued": round(sum(j[5] for j in open_queued), 2),
        # Q: who hasn't paid their deposit yet?
        "deposits_outstanding_jobs": sorted(j[0] for j in deposits_out),
        "deposits_outstanding_value": round(sum(incl(j[6]) * 0.70 for j in deposits_out), 2),
        # Q: who still owes final payment?
        "finals_outstanding_jobs": sorted(j[0] for j in finals_out),
        # Q: which jobs still need ordering? (open/queued, no placed sheet)
        "needs_ordering_jobs": sorted(j[0] for j in open_queued if not j[9]),
        # Q: which jobs still need installing?
        "needs_installing_jobs": sorted(j[0] for j in open_queued),
        # ...and the same, narrowed to one branch.
        "deposits_outstanding_hermanus": sorted(j[0] for j in deposits_out if j[3] == "Hermanus"),
    }


EXPECTED = _expected()


def build():
    global _built
    if _built:
        return DB
    if os.path.exists(DB):
        os.remove(DB)
    os.environ["DATABASE_URL"] = "sqlite:///" + DB.replace("\\", "/")
    if BACKEND not in sys.path:
        sys.path.insert(0, BACKEND)

    import main
    from models import (BusinessSettings, HistoricalYearTotal, HistoricalMonthTotal,
                        FinancialStatement, Quote, QuoteLineItem, QuotePayment,
                        OrderSheet, FlooringProduct)
    from sqlmodel import Session

    main.on_startup()
    T = "1"
    with Session(main.engine) as s:
        s.add(BusinessSettings(tenant_id=T, business_name="Blinds & Flooring Studio", vat_pct=VAT))

        for fy, sales, cost, orders, cov, complete in YEARS:
            gp = sales - cost
            s.add(HistoricalYearTotal(
                tenant_id=T, fiscal_year=fy, total_sales=sales, total_cost=cost,
                gross_profit=gp, margin_pct=gp / sales, order_count=orders,
                # Deliberately disagreeing with the order-row total, the way
                # the real import does - a query reaching for printed_sales
                # comes out wrong rather than coincidentally right.
                printed_sales=round(sales * 0.94, 2),
                printed_cost=round(cost * 0.94, 2),
                printed_gross_profit=round(gp * 0.94, 2),
                monthly_coverage_pct=cov, monthly_complete=complete,
                source_file="OrderIndex_%d.xlsx" % fy))
            for i, share in enumerate(MONTH_SHARE):
                s.add(HistoricalMonthTotal(
                    tenant_id=T, fiscal_year=fy, fiscal_month_index=i,
                    month_label=MONTH_LABELS[i], sales=round(sales * share, 2),
                    cost=round(cost * share, 2),
                    gross_profit=round((sales - cost) * share, 2),
                    order_count=int(orders * share)))

        screed = FlooringProduct(tenant_id=T, product_name="deZIGN S200 Screed", colour="",
                                 supplier="Azura", flooring_category="screed",
                                 pricing_type="screed", base_cost_ex_vat=130.0)
        vinyl = FlooringProduct(tenant_id=T, product_name="Como Vinyl", colour="Ash Grey",
                                supplier="Aspen", flooring_category="vinyl",
                                pricing_type="material", m2_per_pack=3.5, wastage_pct=0.1,
                                base_cost_ex_vat=95.0)
        s.add(screed)
        s.add(vinyl)
        s.flush()
        TRIM_ID = 9001

        for (job_no, client, status, branch, bags, trim_lm, line_ex,
             dep_paid, fin_paid, order_placed, invoiced) in LIVE:
            total_incl = round(line_ex * (1 + VAT), 2)
            q = Quote(tenant_id=T, client_name=client, job_number=job_no,
                      workflow_status=status, branch=branch, sales_owner="ryno",
                      deposit_pct=0.70,
                      accepted_at=datetime.utcnow() - timedelta(days=20),
                      installation_date=(date.today() + timedelta(days=7)
                                         if status == "scheduled" else None),
                      invoice_sent_date=(date.today() - timedelta(days=10)) if invoiced else None)
            s.add(q)
            s.flush()

            # The flooring line carries the whole sell price; screed and
            # trim lines carry the quantities the questions ask about.
            s.add(QuoteLineItem(tenant_id=T, quote_id=q.id, category="flooring",
                                product_id=vinyl.id, product_name=vinyl.product_name,
                                colour=vinyl.colour, quantity_m2=round(line_ex / 700.0, 2),
                                line_total=line_ex, flooring_pricing_type="material",
                                total_job_cost=round(line_ex * 0.65, 2)))
            if bags:
                s.add(QuoteLineItem(tenant_id=T, quote_id=q.id, category="flooring",
                                    product_id=screed.id, product_name=screed.product_name,
                                    quantity_m2=float(bags) * 2.0, bags_allowed=bags,
                                    line_total=0.0, flooring_pricing_type="screed"))
            if trim_lm:
                s.add(QuoteLineItem(tenant_id=T, quote_id=q.id, category="trim",
                                    product_id=TRIM_ID, product_name="Supertrim Reducer S299",
                                    colour="Silver", length_m=trim_lm, line_total=0.0,
                                    trim_sub_category="reducer", unit_cost=79.0))

            if dep_paid:
                s.add(QuotePayment(tenant_id=T, quote_id=q.id,
                                   amount=round(total_incl * 0.70, 2),
                                   paid_date=date.today() - timedelta(days=15),
                                   method="EFT", payment_type="deposit", recorded_by="seed"))
            if fin_paid:
                s.add(QuotePayment(tenant_id=T, quote_id=q.id,
                                   amount=round(total_incl * 0.30, 2),
                                   paid_date=date.today() - timedelta(days=2),
                                   method="EFT", payment_type="final", recorded_by="seed"))
            if order_placed:
                s.add(OrderSheet(tenant_id=T, quote_id=q.id,
                                 order_number="O-%s" % job_no[-3:],
                                 supplier="Azura", sheet_type="flooring",
                                 created_by="seed", status="placed"))

        # Must never appear in any live answer.
        declined = Quote(tenant_id=T, client_name="Declined Client", workflow_status="quoted",
                         sales_owner="ryno", deposit_pct=0.70,
                         declined_at=datetime.utcnow() - timedelta(days=3))
        price_check = Quote(tenant_id=T, client_name="Price Check", workflow_status="accepted",
                            sales_owner="ryno", deposit_pct=0.70, is_price_check=True,
                            accepted_at=datetime.utcnow() - timedelta(days=1))
        s.add(declined)
        s.add(price_check)
        s.flush()
        for bad in (declined, price_check):
            s.add(QuoteLineItem(tenant_id=T, quote_id=bad.id, category="flooring",
                                product_id=screed.id, product_name=screed.product_name,
                                quantity_m2=400.0, bags_allowed=999, line_total=0.0,
                                flooring_pricing_type="screed"))

        s.add(FinancialStatement(
            tenant_id=T, fiscal_year=2022, entity_name="Blinds & Flooring Studio",
            status="final", statement_type="reviewed", revenue=5271071.0,
            cost_of_sales=3735152.0, gross_profit=1535919.0,
            operating_expenses=1546297.0, net_profit=-76746.0,
            figures_entered_at=datetime.utcnow()))
        s.commit()

        # Payments only become real through the app's own write path: the
        # five flat fields on Quote are a SHADOW of the payment rows, and a
        # fixture that inserts rows and stops there leaves a job shape
        # production can never produce.
        for quote in s.exec(__import__("sqlmodel").select(Quote).where(Quote.tenant_id == T)).all():
            main._refresh_payment_shadow(s, quote, T)
        s.commit()

    _built = True
    return DB


build()
