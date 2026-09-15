# -*- coding: utf-8 -*-
"""One-time load of the annual financial statements (confirmed Sept 2026).

Stores the four AFS PDFs and the figures read off them. Run by hand:

    python populate_financial_records.py                # report, write nothing
    python populate_financial_records.py --apply        # store them
    python populate_financial_records.py --apply --replace   # reload from clean

NOT wired into startup. Future years are added through the form on the
Financial Records screen — this script exists only to load the backlog
that predates the feature.

THE FIGURES BELOW WERE TRANSCRIBED FROM THE PDFs, NOT PARSED AT RUNTIME.
That distinction matters and is the whole reason this is a table of
literals rather than a reader: the numbers were read once, checked
against a second source (see the fiscal-year note below), and written
down. Nothing re-derives them on a later run, so they cannot silently
change when a library updates or a layout differs. Each year carries the
page it came from, so any figure can be checked in one place.

THE FISCAL YEAR IS THE YEAR THE STATEMENT STARTS IN, NOT THE YEAR ON ITS
COVER. This is the trap in the whole job. Every one of these documents
says "for the year ended 28 February N", which covers March N-1 to
February N — and Bolton's fiscal_year is the year it STARTS in, matching
HistoricalYearTotal exactly. So:

    "2018 AFS"  (year ended 28 Feb 2018)  ->  fiscal_year 2017
    "2019 AFS"  (year ended 28 Feb 2019)  ->  fiscal_year 2018
    "2020 AFS"  (year ended 29 Feb 2020)  ->  fiscal_year 2019
    "2022 AFS"  (year ended 28 Feb 2022)  ->  fiscal_year 2021

Storing them under the cover year would misalign every statement by one
against the Order Index figures they will eventually sit beside, and it
would do it silently. The mapping was CHECKED rather than assumed, by
comparing each statement's revenue against the Order Index total for
both candidate years, ex VAT: 2018 lands +1.1% on fiscal 2017 versus
-29.1% on 2018; 2019 lands -0.3% on fiscal 2018 versus +75.6%; 2022
lands -1.6% on fiscal 2021 versus -9.8%. The correct mapping is within
2% every time and the wrong one never is.

The 2020 statement is the exception and it is not a mapping problem: it
reads +63% against fiscal 2019's Order Index total because that year of
the spreadsheet is GANSBAAI ONLY — Hermanus is absent from it entirely,
though present in 2018 and 2020. The statement covers the whole company;
that year of the spreadsheet does not.

SIGNS. Costs and expenses are stored POSITIVE, as magnitudes, the same
way HistoricalYearTotal.total_cost already is — the statements bracket
them, which is presentation, not sign. net_profit is stored SIGNED, so
three of these four years are correctly negative. Cash flow lines keep
the statement's own signs, an outflow being negative.
"""
import json
import os
import sys

from sqlmodel import Session, SQLModel, select

from main import engine, _ensure_new_columns
from models import DEFAULT_TENANT_ID, FinancialStatement

DOWNLOADS = r"C:\Users\burge\Downloads"

# fiscal_year is the year the statement STARTS in — see the module
# docstring. "cover_year" is only what the filename and title page say.
STATEMENTS = [
    {
        "cover_year": 2018, "fiscal_year": 2017,
        "file": "Blinds & Flooring Studio (Pty) ltd - 2018 AFS.pdf",
        # The only one of the four that is not marked draft.
        "status": "final", "statement_type": "reviewed",
        "source_note": "Year ended 28 Feb 2018. Statement of Financial Position p.7, "
                       "Comprehensive Income p.8, Cash Flows p.10, Detailed Income Statement p.19.",
        "figures": {
            "revenue": 2859814.0, "cost_of_sales": 1801005.0, "gross_profit": 1058809.0,
            "other_income": 35.0, "operating_expenses": 1073513.0,
            "depreciation": 11583.0, "finance_costs": 8983.0, "net_profit": -23652.0,
            "total_assets": 304397.0, "total_liabilities": 335730.0, "total_equity": -31333.0,
            "cash_from_operations": -122099.0, "cash_from_investing": -115116.0,
            "cash_from_financing": 139081.0, "cash_at_year_end": -86795.0,
        },
        "expenses": [
            ("Accounting fees", 17845), ("Advertising", 25174), ("Bank charges", 12402),
            ("Cleaning", 1415), ("Computer expenses", 4973),
            ("Consulting and professional fees", 8925),
            ("Depreciation, amortisation and impairments", 11583),
            ("Director's remuneration", 241558), ("Donations", 391),
            ("Employee costs", 386096), ("Entertainment", 15397),
            ("Fines and penalties", 7263), ("General expenses", 7876), ("Insurance", 22135),
            ("Internet expenses", 3745), ("Lease rentals on operating lease", 91950),
            ("Legal expenses", 750), ("Loss on sale of assets and liabilities", 585),
            ("Membership fees", 200), ("Municipal expenses", 9342), ("Office expenses", 3432),
            ("Petrol and oil", 73641), ("Postage", 14228), ("Printing and stationery", 1825),
            ("Protective clothing", 5708), ("Recovery account", 4342),
            ("Repairs and maintenance", 63589), ("Security", 6490), ("Small tools", 8967),
            ("Staff welfare", 5189), ("Telephone and fax", 15011), ("Travel - local", 1486),
        ],
    },
    {
        "cover_year": 2019, "fiscal_year": 2018,
        "file": "Blinds & Flooring Studio (Pty) Ltd - 2019 Draft AFS.pdf",
        "status": "draft", "statement_type": "reviewed",
        "source_note": "Year ended 28 Feb 2019. DRAFT. Financial Position p.7, "
                       "Comprehensive Income p.8, Cash Flows p.10, Detailed Income Statement p.19.",
        "figures": {
            "revenue": 4019285.0, "cost_of_sales": 2679872.0, "gross_profit": 1339413.0,
            "other_income": 23.0, "operating_expenses": 1311751.0,
            "depreciation": 36497.0, "finance_costs": 37930.0, "net_profit": -10245.0,
            "total_assets": 500591.0, "total_liabilities": 542169.0, "total_equity": -41578.0,
            "cash_from_operations": -24402.0, "cash_from_investing": -129679.0,
            "cash_from_financing": 87880.0, "cash_at_year_end": -152996.0,
        },
        "expenses": [
            ("Accounting fees", 19844), ("Advertising", 41454), ("Bad debts", 1691),
            ("Bank charges", 21128), ("Cleaning", 827), ("Computer expenses", 8005),
            ("Consulting and professional fees", 7980),
            ("Depreciation, amortisation and impairments", 36497),
            ("Director's remuneration", 267467), ("Donations", 352),
            ("Employee costs", 449033), ("Entertainment", 20138), ("Fines and penalties", 538),
            ("Insurance", 46768), ("Internet expenses", 6761),
            ("Lease rentals on operating lease", 128650), ("Municipal expenses", 12881),
            ("Office expenses", 8752), ("Petrol and oil", 109890), ("Postage", 36940),
            ("Printing and stationery", 4367), ("Protective clothing", 5061),
            ("Repairs and maintenance", 22148), ("Security", 228), ("Small tools", 23411),
            ("Staff welfare", 7206), ("Subscriptions", 1760), ("Telephone and fax", 13867),
            ("Travel - local", 8107),
        ],
    },
    {
        "cover_year": 2020, "fiscal_year": 2019,
        "file": "Blinds and Flooring Studio (Pty) Ltd - 2020 AFS - Draft V2.pdf",
        "status": "draft", "statement_type": "reviewed",
        "source_note": "Year ended 29 Feb 2020. DRAFT V2. Financial Position p.7, "
                       "Comprehensive Income p.8, Cash Flows p.10, Detailed Income Statement p.19. "
                       "Note: the Order Index for this fiscal year covers Gansbaai only, so it "
                       "reads far below this statement - the gap is the spreadsheet, not the AFS.",
        "figures": {
            "revenue": 3734453.0, "cost_of_sales": 2552311.0, "gross_profit": 1182142.0,
            "other_income": 50.0, "operating_expenses": 1356588.0,
            "depreciation": 53743.0, "finance_costs": 71618.0, "net_profit": -246014.0,
            "total_assets": 403405.0, "total_liabilities": 690997.0, "total_equity": -287592.0,
            "cash_from_operations": -149022.0, "cash_from_investing": 0.0,
            "cash_from_financing": -62048.0, "cash_at_year_end": -364066.0,
        },
        "expenses": [
            ("Accounting fees", 22717), ("Advertising", 37492), ("Bad debts", 1583),
            ("Bank charges", 32165), ("Cleaning", 1313), ("Computer expenses", 9724),
            ("Consulting and professional fees", 14480),
            ("Depreciation, amortisation and impairments", 53743),
            ("Director's remuneration", 229289), ("Donations", 350),
            ("Employee costs", 429198), ("Entertainment", 29216), ("Fines and penalties", 2782),
            ("Insurance", 35760), ("Internet expenses", 7403),
            ("Lease rentals on operating lease", 181585), ("Municipal expenses", 12062),
            ("Office expenses", 1800), ("Petrol and oil", 104065), ("Postage", 31108),
            ("Printing and stationery", 5265), ("Protective clothing", 8181),
            ("Repairs and maintenance", 55331), ("Security", 599), ("Small tools", 8701),
            ("Staff welfare", 15605), ("Subscriptions", 9373), ("Telephone and fax", 10918),
            ("Travel - local", 4780),
        ],
    },
    {
        "cover_year": 2022, "fiscal_year": 2021,
        "file": "Blinds and Flooring Studio (Pty) Ltd - 2022 Draft AFS.pdf",
        "status": "draft", "statement_type": "reviewed",
        "source_note": "Year ended 28 Feb 2022. DRAFT. Financial Position p.7, "
                       "Comprehensive Income p.8, Cash Flows p.10, Detailed Income Statement p.19.",
        "figures": {
            "revenue": 4805041.0, "cost_of_sales": 3069136.0, "gross_profit": 1735905.0,
            # 13 454 insurance claims + 27 interest received = 13 481, the
            # figure the Detailed Income Statement itself totals.
            "other_income": 13481.0, "operating_expenses": 1330069.0,
            "depreciation": 91907.0, "finance_costs": 62183.0,
            # After R5 069 taxation. Profit before tax was R357 134.
            "net_profit": 352065.0,
            "total_assets": 773517.0, "total_liabilities": 633654.0, "total_equity": 139863.0,
            "cash_from_operations": 194900.0, "cash_from_investing": -11000.0,
            "cash_from_financing": -98342.0, "cash_at_year_end": -266233.0,
        },
        "expenses": [
            ("Accounting fees", 22685), ("Advertising", 55370), ("Bad debts", 9662),
            ("Bank charges", 17171), ("Cleaning", 4181), ("Computer expenses", 5332),
            ("Consulting and professional fees", 9075),
            ("Depreciation, amortisation and impairments", 91907),
            ("Director's remuneration", 265572), ("Donations", 50),
            ("Employee costs", 467846), ("Entertainment", 39864), ("Fines and penalties", 2198),
            ("Insurance", 21737), ("Internet expenses", 4385),
            ("Lease rentals on operating lease", 52490), ("Municipal expenses", 10606),
            ("Office expenses", 1330), ("Petrol and oil", 109961), ("Postage", 12869),
            ("Printing and stationery", 2129), ("Protective clothing", 8084),
            ("Repairs and maintenance", 56805), ("Small tools", 13383), ("Staff welfare", 13522),
            ("Telephone and fax", 17743), ("Travel - local", 3660),
            ("Workmans compensation", 10452),
        ],
    },
]

ENTITY = "Blinds & Flooring Studio (Pty) Ltd"


def _self_check(s):
    """Arithmetic the statement itself asserts. Catches a transcription
    slip before it reaches the database, which is the failure this whole
    manual-entry approach exists to avoid."""
    f = s["figures"]
    problems = []
    gp = f["revenue"] - f["cost_of_sales"]
    if abs(gp - f["gross_profit"]) > 1.0:
        problems.append("revenue - cost of sales = %.0f, but gross profit says %.0f" % (gp, f["gross_profit"]))
    exp_total = sum(a for _, a in s["expenses"])
    if abs(exp_total - f["operating_expenses"]) > 1.0:
        problems.append("expense lines total %.0f, but operating expenses says %.0f"
                        % (exp_total, f["operating_expenses"]))
    bs = f["total_liabilities"] + f["total_equity"]
    if abs(bs - f["total_assets"]) > 1.0:
        problems.append("liabilities + equity = %.0f, but total assets says %.0f" % (bs, f["total_assets"]))
    cash = f["cash_from_operations"] + f["cash_from_investing"] + f["cash_from_financing"]
    return problems, exp_total, cash


def run(apply_changes=False, replace=False, tenant_id=DEFAULT_TENANT_ID):
    _ensure_new_columns()
    SQLModel.metadata.create_all(engine)

    print("APPLIED\n" if apply_changes else "REPORT ONLY - nothing written (re-run with --apply)\n")
    ok = True
    with Session(engine) as session:
        if replace and apply_changes:
            for row in session.exec(select(FinancialStatement)
                                    .where(FinancialStatement.tenant_id == tenant_id)).all():
                session.delete(row)
            session.commit()

        for s in STATEMENTS:
            path = os.path.join(DOWNLOADS, s["file"])
            if not os.path.exists(path):
                print("  MISSING FILE: %s" % s["file"])
                ok = False
                continue
            with open(path, "rb") as fh:
                data = fh.read()
            if not data.startswith(b"%PDF-"):
                print("  NOT A PDF: %s" % s["file"])
                ok = False
                continue

            problems, exp_total, cash_move = _self_check(s)
            f = s["figures"]
            print("  AFS %d  ->  fiscal_year %d   [%s]" % (s["cover_year"], s["fiscal_year"], s["status"].upper()))
            print("     revenue R%12s   cost of sales R%12s   gross profit R%12s"
                  % ("{:,.0f}".format(f["revenue"]), "{:,.0f}".format(f["cost_of_sales"]),
                     "{:,.0f}".format(f["gross_profit"])))
            print("     opex    R%12s   (%d lines, sum R%s)   net %s R%s"
                  % ("{:,.0f}".format(f["operating_expenses"]), len(s["expenses"]),
                     "{:,.0f}".format(exp_total),
                     "LOSS" if f["net_profit"] < 0 else "profit",
                     "{:,.0f}".format(abs(f["net_profit"]))))
            print("     assets  R%12s   liabilities R%12s   equity R%s"
                  % ("{:,.0f}".format(f["total_assets"]), "{:,.0f}".format(f["total_liabilities"]),
                     "{:,.0f}".format(f["total_equity"])))
            print("     cash    ops R%s  inv R%s  fin R%s  ->  year end R%s"
                  % ("{:,.0f}".format(f["cash_from_operations"]),
                     "{:,.0f}".format(f["cash_from_investing"]),
                     "{:,.0f}".format(f["cash_from_financing"]),
                     "{:,.0f}".format(f["cash_at_year_end"])))
            if problems:
                ok = False
                for p in problems:
                    print("     *** CHECK FAILED: %s" % p)
            else:
                print("     checks: gross profit, expense lines and balance sheet all reconcile")
            print()

            if apply_changes:
                row = FinancialStatement(
                    tenant_id=tenant_id, fiscal_year=s["fiscal_year"], entity_name=ENTITY,
                    status=s["status"], statement_type=s["statement_type"],
                    original_filename=s["file"], content_type="application/pdf",
                    size_bytes=len(data), pdf_bytes=data, uploaded_by="backfill",
                    source_note=s["source_note"],
                    notes="Cover year %d (year ended Feb %d). Stored under fiscal_year %d, the year it "
                          "starts in, to line up with the Order Index figures."
                          % (s["cover_year"], s["cover_year"], s["fiscal_year"]),
                    expense_breakdown_json=json.dumps(
                        [{"label": l, "amount": float(a)} for l, a in s["expenses"]]),
                    **f)
                from datetime import datetime
                row.figures_entered_at = datetime.utcnow()
                row.figures_entered_by = "backfill"
                session.add(row)
        if apply_changes:
            session.commit()
    return ok


if __name__ == "__main__":
    good = run(apply_changes="--apply" in sys.argv, replace="--replace" in sys.argv)
    if not good:
        print("One or more checks failed - nothing should be trusted until they are resolved.")
        raise SystemExit(1)
    print("All four statements reconcile against their own arithmetic.")
    if "--apply" not in sys.argv:
        print("Re-run with --apply to store them.")
