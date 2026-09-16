# -*- coding: utf-8 -*-
"""Phase-1 fixture: imported Order Index history, plus the tables the
agent must NOT be able to reach at phase 1 so a bypass has something
real to try to steal.

EXPECTED holds answers computed here in plain Python, independently of
any SQL, so a correctness test compares the agent's query against
arithmetic rather than against another query.
"""
import os
import sys
from datetime import date, datetime

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SP = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(SP, "bolton-askq.db")

_built = False

# Nine fiscal years, shaped like the real import: two years with an
# incomplete monthly breakdown, printed_* figures that disagree with the
# order rows, and one cutover year.
YEARS = [
    # fy,   sales,      cost,       orders, coverage, complete
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
# A deliberately uneven shape so "best month" is a real answer rather
# than whichever row happens to sort first.
MONTH_SHARE = [0.07, 0.08, 0.09, 0.07, 0.06, 0.08, 0.09, 0.10, 0.12, 0.09, 0.07, 0.08]


def _expected():
    total_sales = sum(y[1] for y in YEARS)
    best_gp = max(YEARS, key=lambda y: y[1] - y[2])
    margins = [(y[1] - y[2]) / y[1] for y in YEARS]
    best_month = 0.0
    month_count = 0
    for fy, sales, cost, orders, cov, complete in YEARS:
        for share in MONTH_SHARE:
            best_month = max(best_month, round(sales * share, 2))
            month_count += 1
    return {
        "year_count": len(YEARS),
        "month_count": month_count,
        "total_sales_all_years": round(total_sales, 2),
        "best_gp_year": best_gp[0],
        "years_above_35pct": sum(1 for m in margins if m > 0.35),
        "best_month_sales": best_month,
        "incomplete_years": sum(1 for y in YEARS if not y[5]),
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
                        FinancialStatement, Quote)
    from sqlmodel import Session

    main.on_startup()
    T = "1"
    with Session(main.engine) as s:
        s.add(BusinessSettings(tenant_id=T, business_name="Blinds & Flooring Studio"))
        for fy, sales, cost, orders, cov, complete in YEARS:
            gp = sales - cost
            s.add(HistoricalYearTotal(
                tenant_id=T, fiscal_year=fy, total_sales=sales, total_cost=cost,
                gross_profit=gp, margin_pct=gp / sales, order_count=orders,
                # Deliberately disagreeing with the order-row total, the
                # way the real import does - a correctness test that
                # accidentally used printed_sales would come out wrong.
                printed_sales=round(sales * 0.94, 2),
                printed_cost=round(cost * 0.94, 2),
                printed_gross_profit=round(gp * 0.94, 2),
                monthly_coverage_pct=cov, monthly_complete=complete,
                covers_until=date(2026, 9, 1) if fy == 2026 else None,
                source_file="OrderIndex_%d.xlsx" % fy))
            for i, share in enumerate(MONTH_SHARE):
                s.add(HistoricalMonthTotal(
                    tenant_id=T, fiscal_year=fy, fiscal_month_index=i,
                    month_label=MONTH_LABELS[i], sales=round(sales * share, 2),
                    cost=round(cost * share, 2),
                    gross_profit=round((sales - cost) * share, 2),
                    order_count=int(orders * share)))

        # Present so a red-team bypass has something real to reach for.
        # If the allow-list ever leaks, these rows are what leaks.
        s.add(FinancialStatement(
            tenant_id=T, fiscal_year=2022, entity_name="Blinds & Flooring Studio",
            status="final", statement_type="reviewed", revenue=5271071.0,
            cost_of_sales=3735152.0, gross_profit=1535919.0,
            operating_expenses=1546297.0, net_profit=-76746.0,
            figures_entered_at=datetime.utcnow()))
        s.add(Quote(tenant_id=T, client_name="Phase 2 Client", sales_owner="ask_sales",
                    workflow_status="accepted", deposit_pct=0.70))
        s.commit()
    _built = True
    return DB


build()
