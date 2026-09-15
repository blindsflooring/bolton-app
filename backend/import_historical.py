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

WHERE THE SPREADSHEET STOPS AND BOLTON STARTS -- the thing that matters
more than anything else here. Bolton went live for real job entry on
1 September 2026, six months into fiscal 2026/27, so that year is split
between the two systems and every other year is not.

  fiscal 2017/18 .. 2025/26   wholly imported, nothing live overlaps
  fiscal 2026/27              1 Mar - 31 Aug imported from the sheet;
                              1 Sep onward is Bolton's, never imported
  fiscal 2027/28 and later    never imported at all

The workbook's own "2026 YTD" column runs Mar-Sep, so it DOES contain
September rows that Bolton also holds -- 22 orders, R217 745,39. Those
are skipped by CUTOVER_DATE below. That is the exact figure that would
otherwise be counted twice, and it is the whole reason the boundary is
enforced in the reader rather than trusted to the sheet.

See models.py's HistoricalYearTotal for why the ORDER-ROW totals are
stored as the real figures rather than the spreadsheet's own printed
annual row (2017 printed = Blinds only; 2018 printed = Gansbaai only).
"""
import datetime
import sys

import openpyxl
from sqlmodel import Session, SQLModel, select

from main import engine, _ensure_new_columns
from models import DEFAULT_TENANT_ID, HistoricalMonthTotal, HistoricalYearTotal

# Fiscal year runs March-February. 2025 == Mar 2025 - Feb 2026, the last
# year that completed before the current one. Nothing at or after 2026
# may ever be imported - that is live Quote territory.
LAST_IMPORTED_FISCAL_YEAR = 2025
FIRST_IMPORTED_FISCAL_YEAR = 2017

# ---- The cutover (confirmed Sept 2026) ----
# Bolton went live for real job entry on 1 September 2026, six months
# into fiscal 2026/27. That year is therefore split down the middle: the
# old Excel system ran March to August, Bolton runs September onward.
#
# So fiscal 2026 IS imported after all, but ONLY the part before this
# date. Orders dated on or after it are Bolton's to report and are
# skipped here — on the real sheet that is 22 orders worth R217 745,39,
# which is precisely the figure that would otherwise be counted twice.
# Nothing after this date is ever imported, for this year or any later
# one (LAST_IMPORTED_FISCAL_YEAR still bars 2027 and beyond outright).
CUTOVER_FISCAL_YEAR = 2026
CUTOVER_DATE = datetime.date(2026, 9, 1)

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
        if not (FIRST_IMPORTED_FISCAL_YEAR <= fy <= CUTOVER_FISCAL_YEAR):
            continue   # anything past the cutover year is Bolton's, permanently
        d = _parse_date(row[3])
        if fy == CUTOVER_FISCAL_YEAR:
            # The split year takes ONLY dated rows before the cutover.
            #
            # An undated row is excluded outright here, unlike in a
            # complete year where it still counts toward the annual total
            # and merely misses the monthly view. The difference is that
            # this year has a live system on the other side of the
            # boundary: a row that cannot be dated cannot be shown to
            # belong before the cutover, and putting it there anyway
            # would risk counting a September job that Bolton already
            # holds. On the real sheet that is 17 orders worth R37 477,35
            # left out — reported in the year's notes rather than
            # silently dropped, and the conservative direction, because
            # understating by a known amount is recoverable and
            # double-counting is not.
            if d is None or d >= CUTOVER_DATE:
                if d is None:
                    cut = years.setdefault(fy, {
                        "sales": 0.0, "cost": 0.0, "gross_profit": 0.0, "order_count": 0,
                        "dated_sales": 0.0,
                        "excluded_undated_sales": 0.0, "excluded_undated_orders": 0,
                        "months": [{"sales": 0.0, "cost": 0.0, "gross_profit": 0.0, "order_count": 0}
                                   for _ in range(12)],
                    })
                    cut["excluded_undated_sales"] = round(cut["excluded_undated_sales"] + (row[10] or 0.0), 2)
                    cut["excluded_undated_orders"] += 1
                continue
        y = years.setdefault(fy, {
            "sales": 0.0, "cost": 0.0, "gross_profit": 0.0, "order_count": 0,
            "dated_sales": 0.0,
            "excluded_undated_sales": 0.0, "excluded_undated_orders": 0,
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
    #
    # _ensure_new_columns() FIRST, and it is not optional: create_all()
    # only creates tables that are absent, and can never add a column to
    # one that already exists. A database where a previous import already
    # created historicalyeartotal therefore has the table but not
    # covers_until, and the very first query here dies on a missing
    # column. Hit exactly that, which is also the state production is in
    # right now. Running the app's own migration means this script no
    # longer depends on the backend having been redeployed first.
    _ensure_new_columns()
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

            is_cutover = (fy == CUTOVER_FISCAL_YEAR)
            covers_until = CUTOVER_DATE if is_cutover else None

            note = ""
            printed_sales = y["printed"].get("sales")
            if is_cutover:
                # The printed annual figure for this year covers the whole
                # Mar-Sep span the sheet holds, so it is EXPECTED to differ
                # from what is stored — comparing them would raise a
                # discrepancy that is really just the cutover doing its
                # job. Said plainly instead.
                printed_sales = None
                note = ("Part-year: covers 1 March to 31 August 2026 only, from the "
                        "spreadsheet. Everything from 1 September 2026 comes live from "
                        "Bolton and is never imported here.")
                if y["excluded_undated_sales"]:
                    note += (" %d order(s) worth R%.2f in this period carry no readable "
                             "date and are left out, since a row that cannot be dated "
                             "cannot be shown to fall before the cutover."
                             % (y["excluded_undated_orders"], y["excluded_undated_sales"]))
            elif printed_sales and abs(printed_sales - y["sales"]) > 1.0:
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
                    covers_until=covers_until,
                    source_file=path.replace("\\", "/").split("/")[-1], notes=note.strip(),
                ))
                # An incomplete year gets NO monthly rows at all, rather
                # than a partial curve nobody can safely read.
                if complete:
                    for i, m in enumerate(y["months"]):
                        # The cutover year stops at the boundary. Writing
                        # Sep-Feb as zeroes would be harmless to the blend
                        # (the endpoint ignores any month at or past the
                        # cutover) but reads as "the spreadsheet says
                        # September was R0" to anyone looking at the table
                        # directly, which is not what it says at all — it
                        # has nothing to say about September.
                        if is_cutover and i >= _fiscal_month_index(CUTOVER_DATE):
                            continue
                        session.add(HistoricalMonthTotal(
                            tenant_id=tenant_id, fiscal_year=fy, fiscal_month_index=i,
                            month_label=FISCAL_MONTHS[i], sales=round(m["sales"], 2),
                            cost=round(m["cost"], 2),
                            gross_profit=round(m["gross_profit"], 2),
                            order_count=m["order_count"],
                        ))
            summary.append({
                "fiscal_year": fy, "sales": round(y["sales"], 2),
                # printed_sales is None for the cutover year on purpose —
                # the sheet's printed figure covers Mar-Sep, so showing it
                # beside a Mar-Aug total invites a comparison that is
                # meaningless by construction.
                "printed_sales": printed_sales,
                "coverage": round(coverage, 4), "monthly_complete": complete,
                "orders": y["order_count"], "covers_until": covers_until,
                "excluded_undated_sales": y.get("excluded_undated_sales", 0.0),
                "excluded_undated_orders": y.get("excluded_undated_orders", 0),
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
    print("%-6s %14s %14s %10s  %-18s %s" % (
        "FY", "stored sales", "printed", "coverage", "monthly curve", "covers"))
    for y in result["years"]:
        until = y["covers_until"]
        print("%-6d %14.2f %14s %9.1f%%  %-18s %s" % (
            y["fiscal_year"], y["sales"],
            ("%.2f" % y["printed_sales"]) if y["printed_sales"] else "-",
            y["coverage"] * 100,
            "yes" if y["monthly_complete"] else "NO - annual only",
            ("1 Mar - %s (PART YEAR)" % (until - datetime.timedelta(days=1)).strftime("%d %b %Y"))
            if until else "full year"))
    for y in [row for row in result["years"] if row["covers_until"]]:
        until = y["covers_until"]
        print("\nfiscal %d is the CUTOVER year." % y["fiscal_year"])
        print("  spreadsheet supplies 1 Mar - %s"
              % (until - datetime.timedelta(days=1)).strftime("%d %b %Y"))
        print("  Bolton supplies %s onward, and it is never imported here."
              % until.strftime("%d %b %Y"))
        if y["excluded_undated_orders"]:
            print("  %d order(s) worth R%.2f in that window carry no readable date "
                  "and are left out." % (y["excluded_undated_orders"], y["excluded_undated_sales"]))
    print("\nfiscal %d onward is never imported at all - that is live Quote data."
          % (CUTOVER_FISCAL_YEAR + 1))
