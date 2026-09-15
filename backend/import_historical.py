# -*- coding: utf-8 -*-
"""One-time historical Order Index import (confirmed Sept 2026).

NOT wired into startup, and deliberately so - this is a one-time load of
years of manually-kept spreadsheets, not a recurring job. Run it by hand:

    python import_historical.py "C:/path/Bolton_Historical_Order_Data.xlsx"
    python import_historical.py <file> --dry-run     # report, write nothing
    python import_historical.py <file> --replace     # re-import from clean

Idempotent by default: it refuses to run if historical rows already
exist, rather than silently doubling every figure in the table. Use
--replace to deliberately reload.

WHAT IT REFUSES TO IMPORT, and why that matters more than anything else
here: fiscal 2026. The workbook carries a "2026 YTD" column covering
Mar-Sep 2026. That is the CURRENT fiscal year, and Bolton's live Quote
table already holds those same real jobs. Importing it would double-count
every rand of the current year against itself. The cutoff is enforced
below by LAST_IMPORTED_FISCAL_YEAR and is not a matter of opinion: from
March 2026 onward the number comes from live data, permanently.

See models.py's HistoricalYearTotal for why the ORDER-ROW totals are
stored as the real figures rather than the spreadsheet's own printed
annual row (2017 printed = Blinds only; 2018 printed = Gansbaai only).
"""
import datetime
import sys

import openpyxl
from sqlmodel import Session, SQLModel, select

from main import engine
from models import DEFAULT_TENANT_ID, HistoricalMonthTotal, HistoricalYearTotal

# Fiscal year runs March-February. 2025 == Mar 2025 - Feb 2026, the last
# year that completed before the current one. Nothing at or after 2026
# may ever be imported - that is live Quote territory.
LAST_IMPORTED_FISCAL_YEAR = 2025
FIRST_IMPORTED_FISCAL_YEAR = 2017

# A year whose monthly rows account for less than this share of its own
# annual sales does not get a running-total curve - see
# HistoricalYearTotal.monthly_coverage_pct for the reasoning.
MONTHLY_COMPLETE_THRESHOLD = 0.95

FISCAL_MONTHS = ["Mar", "Apr", "May", "Jun", "Jul", "Aug",
                 "Sep", "Oct", "Nov", "Dec", "Jan", "Feb"]


def _parse_date(value):
    """The Date column is a mix of real datetimes and free text, and 495
    of the 6296 rows are neither. An unreadable date is returned as None
    and costs the row its place in the MONTHLY view only - it still
    counts in full toward the annual total, which is why annual figures
    stay correct even for the two badly-dated years."""
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    if isinstance(value, str):
        try:
            return datetime.date.fromisoformat(value.strip()[:10])
        except ValueError:
            return None
    return None


def _fiscal_month_index(d):
    """0 = March ... 11 = February."""
    return d.month - 3 if d.month >= 3 else d.month + 9


def read_workbook(path):
    """Returns {fiscal_year: {totals..., months: [12 dicts]}} for the
    importable years only."""
    wb = openpyxl.load_workbook(path, data_only=True)

    printed = {}
    for row in list(wb["Yearly Summary"].iter_rows(values_only=True))[1:]:
        label = row[0]
        if label and str(label)[0].isdigit():
            printed[int(str(label).split()[0])] = {
                "sales": row[1], "cost": row[2], "gross_profit": row[3],
            }

    years = {}
    for row in list(wb["All Orders"].iter_rows(values_only=True))[1:]:
        fy_cell = row[2]
        if fy_cell is None:
            continue
        fy = int(fy_cell)
        if not (FIRST_IMPORTED_FISCAL_YEAR <= fy <= LAST_IMPORTED_FISCAL_YEAR):
            continue   # the 2026 guard, and any stray out-of-range year
        y = years.setdefault(fy, {
            "sales": 0.0, "cost": 0.0, "gross_profit": 0.0, "order_count": 0,
            "dated_sales": 0.0,
            "months": [{"sales": 0.0, "cost": 0.0, "gross_profit": 0.0, "order_count": 0}
                       for _ in range(12)],
        })
        sales = row[10] or 0.0
        cost = row[11] or 0.0
        gross = row[12] or 0.0
        y["sales"] += sales
        y["cost"] += cost
        y["gross_profit"] += gross
        y["order_count"] += 1

        d = _parse_date(row[3])
        if d is None:
            continue
        m = y["months"][_fiscal_month_index(d)]
        m["sales"] += sales
        m["cost"] += cost
        m["gross_profit"] += gross
        m["order_count"] += 1
        y["dated_sales"] += sales

    for fy, y in years.items():
        y["printed"] = printed.get(fy, {})
        y["coverage"] = (y["dated_sales"] / y["sales"]) if y["sales"] else 0.0
    return years


def import_historical(path, dry_run=False, replace=False, tenant_id=DEFAULT_TENANT_ID):
    years = read_workbook(path)
    if not years:
        raise SystemExit("No importable fiscal years found in that workbook.")

    # The app creates its tables at startup; this script is run by hand
    # and may well be the first thing to touch these two. Idempotent, and
    # it only ever CREATES what is missing - it cannot alter or drop an
    # existing table, so running it against a live database is safe.
    SQLModel.metadata.create_all(engine)

    with Session(engine) as session:
        existing = session.exec(
            select(HistoricalYearTotal).where(HistoricalYearTotal.tenant_id == tenant_id)
        ).all()
        if existing and not replace:
            raise SystemExit(
                "%d historical year(s) are already imported. Re-run with --replace "
                "to reload them, or leave them alone." % len(existing)
            )
        if existing and replace and not dry_run:
            for row in existing:
                session.delete(row)
            for row in session.exec(
                select(HistoricalMonthTotal).where(HistoricalMonthTotal.tenant_id == tenant_id)
            ).all():
                session.delete(row)
            session.commit()

        summary = []
        for fy in sorted(years):
            y = years[fy]
            coverage = y["coverage"]
            complete = coverage >= MONTHLY_COMPLETE_THRESHOLD
            margin = (y["gross_profit"] / y["sales"]) if y["sales"] else 0.0

            note = ""
            printed_sales = y["printed"].get("sales")
            if printed_sales and abs(printed_sales - y["sales"]) > 1.0:
                note = ("Source sheet's printed annual total was R%.2f, which is "
                        "incomplete for this year; the stored figure is the sum of its "
                        "%d order rows." % (printed_sales, y["order_count"]))
            if not complete:
                note = (note + " " if note else "") + (
                    "Only %.1f%% of this year's sales carry a readable order date, so "
                    "it has no month-by-month curve." % (coverage * 100))

            if not dry_run:
                session.add(HistoricalYearTotal(
                    tenant_id=tenant_id, fiscal_year=fy,
                    total_sales=round(y["sales"], 2), total_cost=round(y["cost"], 2),
                    gross_profit=round(y["gross_profit"], 2), margin_pct=round(margin, 6),
                    printed_sales=y["printed"].get("sales"),
                    printed_cost=y["printed"].get("cost"),
                    printed_gross_profit=y["printed"].get("gross_profit"),
                    order_count=y["order_count"],
                    monthly_coverage_pct=round(coverage, 4), monthly_complete=complete,
                    source_file=path.replace("\\", "/").split("/")[-1], notes=note.strip(),
                ))
                # An incomplete year gets NO monthly rows at all, rather
                # than a partial curve nobody can safely read.
                if complete:
                    for i, m in enumerate(y["months"]):
                        session.add(HistoricalMonthTotal(
                            tenant_id=tenant_id, fiscal_year=fy, fiscal_month_index=i,
                            month_label=FISCAL_MONTHS[i], sales=round(m["sales"], 2),
                            cost=round(m["cost"], 2),
                            gross_profit=round(m["gross_profit"], 2),
                            order_count=m["order_count"],
                        ))
            summary.append({
                "fiscal_year": fy, "sales": round(y["sales"], 2),
                "printed_sales": y["printed"].get("sales"),
                "coverage": round(coverage, 4), "monthly_complete": complete,
                "orders": y["order_count"],
            })
        if not dry_run:
            session.commit()
    return {"years": summary, "dry_run": dry_run}


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        raise SystemExit(__doc__)
    result = import_historical(
        args[0], dry_run="--dry-run" in sys.argv, replace="--replace" in sys.argv)
    print("DRY RUN - nothing written\n" if result["dry_run"] else "IMPORTED\n")
    print("%-6s %14s %14s %10s  %s" % ("FY", "stored sales", "printed", "coverage", "monthly curve"))
    for y in result["years"]:
        print("%-6d %14.2f %14s %9.1f%%  %s" % (
            y["fiscal_year"], y["sales"],
            ("%.2f" % y["printed_sales"]) if y["printed_sales"] else "-",
            y["coverage"] * 100,
            "yes" if y["monthly_complete"] else "NO - annual only"))
    print("\nfiscal 2026 onward deliberately NOT imported - that is live Quote data.")
