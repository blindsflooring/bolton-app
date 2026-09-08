"""
Blinds Quote Import — deterministic Excel → Bolton (confirmed Sept 2026,
"Blinds Quote Import (Excel → Order Index)" brief).

Blinds are quoted in an existing, fixed Excel template and will go on
being quoted there. This module does NOT price blinds and must never
start: Burgert's own framing is to get finalized blinds quotes into
Bolton "without moving blinds pricing/calculation logic into Bolton
itself", so the sheet stays the single source of truth for what a blind
costs. Everything here reads numbers the sheet already worked out.

Deterministic cell-position reading, no AI — the same lesson
spreadsheet_import.py was built on, where every bug in the earlier
AI-PDF path traced back to a model inferring layout live with no human
check. Here the layout is known, so it is simply read.

Two things this module refuses to do, both learned from that same file:
  - Guess. A cell that isn't what the template says it is fails the
    whole import with a specific message, rather than importing a
    plausible wrong number.
  - Trust a fixed row number for anything below the line items.
    CONFIRMED Sept 2026 by two real quotes: the template's rows are not
    fixed at all, they slide with the number of blinds. Simon's sheet
    has its totals on row 42 and Rep on 48; Ilse's has them on 34 and
    40. Everything below row 20 moves.

    So the money and the Rep are located BY LABEL — find the row whose
    column I says "Sub Total" and read column L beside it; find the row
    whose column B says "Rep" and read columns D and E beside it. That
    is how a person reads the sheet: look for the words, then read
    across. Row numbers are only ever reported, never assumed.
"""
import io
from datetime import datetime
from typing import Any, Dict, List, Optional

import openpyxl

# ---------------------------------------------------------------- cells
# Confirmed cell mapping (brief, Sept 2026, corrected same month once
# two real quotes showed the layout shifting). Only the client block and
# the line-item COLUMNS are fixed; every row below the line items is
# found by its label.
# The client block sits ABOVE the line items (rows 12-16), so it does
# not move when the sheet compresses — the shift starts at row 20 and
# pushes everything below it. These four stay fixed for that reason,
# not by assumption.
CELL_CLIENT_NAME = "D12"
CELL_CLIENT_ADDRESS = "D13"
CELL_CLIENT_REFERENCE = "D15"
CELL_CLIENT_PHONE = "D16"

# Branch and Rep are read from the row LABELLED "Rep" in column B, not
# from fixed cells (confirmed Sept 2026 — see LABELS below). Branch is
# column D on that row, Rep is column E.
COL_BRANCH = "D"
COL_REP = "E"
COL_TOTALS_LABEL = "I"      # "Sub Total", "VAT", "Total", "Deposit"
COL_REP_LABEL = "B"         # "Rep"

FIRST_LINE_ROW = 20
# Column letters for a line item.
COL_ITEM_NO = "B"
COL_ROOM = "C"
COL_WIDTH = "E"
COL_DROP = "F"
COL_SIDE = "G"
COL_BLIND_TYPE = "H"
COL_COLOUR = "I"
COL_QTY = "K"
COL_LINE_TOTAL = "L"

SEARCH_LIMIT = 400          # rows below FIRST_LINE_ROW worth scanning
TOTALS_BLOCK_WINDOW = 12    # rows below Sub Total that VAT/Total/Deposit sit within

BRANCH_CODES = {"HER": "hermanus", "GAN": "gansbaai"}

# Money tolerance. Real sheets round per line, so an exact tie is not
# expected; anything outside this is a mapping problem, not rounding.
MONEY_ABS_TOLERANCE = 0.05
MONEY_REL_TOLERANCE = 0.005


class BlindsImportError(ValueError):
    """Rejects the whole file. Never a partial import — a blinds quote is
    a single commercial document, and half of one in the Order Index is
    worse than none, because it looks complete."""


def _cell(ws, ref: str):
    return ws[ref].value


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _number(value: Any) -> Optional[float]:
    """A cell that must be a number. Returns None for anything that
    isn't one — including a formula string, which is what openpyxl hands
    back when the file has never been opened and saved by Excel and so
    carries no cached results. That case is caught and explained at the
    top level rather than surfacing as a confusing per-cell error."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace("R", "").replace(" ", "").replace(" ", "")
    # South African sheets can carry "1 234,56" or "1,234.56".
    if "," in text and "." in text:
        text = text.replace(",", "") if text.rfind(".") > text.rfind(",") else text.replace(".", "").replace(",", ".")
    elif "," in text:
        text = text.replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= max(MONEY_ABS_TOLERANCE, abs(b) * MONEY_REL_TOLERANCE)


def _norm(value) -> str:
    """Label text, reduced to just its letters and digits, lowercased.
    Makes "Sub Total", "SUB-TOTAL", "Sub Total:" and " Subtotal " all
    the same string, so a formatting change in the template doesn't
    break the import."""
    return "".join(ch for ch in _text(value).lower() if ch.isalnum())


# What each label looks like once normalised. Order matters for the
# totals block: "subtotal" also ends in "total", so it is tested first
# and the plain-total matcher explicitly excludes it.
def _is_subtotal(n: str) -> bool:
    return n.startswith("subtotal")


def _is_vat(n: str) -> bool:
    return "vat" in n and not n.startswith("total") and not n.startswith("subtotal")


def _is_total(n: str) -> bool:
    return n.startswith("total") and not n.startswith("subtotal")


def _is_deposit(n: str) -> bool:
    return "deposit" in n


def _find_label_row(ws, col: str, matcher, first: int, last: int):
    """First row in [first, last) whose cell in `col` matches."""
    for row in range(first, last):
        if matcher(_norm(ws[f"{col}{row}"].value)):
            return row
    return None


def _find_totals(ws) -> Dict[str, Any]:
    """The money block, located by its own labels.

    CHANGED Sept 2026 after the import failed on a real quote. The
    previous version found the subtotal by adding up the blinds and
    looking for a row matching that figure. Clever, and wrong for the
    right reason: it worked on a sheet whose lines are contiguous, and
    Ilse's quote has blank rows mid-list (B24, B28-32), so the running
    total at any given row didn't correspond to anything and the block
    was never found. Burgert's own instruction is the fix, and it is
    also the simpler thing: "search each sheet for the row where column
    I contains 'Sub Total', then read L on that row."

    The sum of the lines is still checked against what is found — but as
    a VERIFICATION now, not as the way of finding it. Those are
    different jobs and conflating them is what broke.
    """
    last = FIRST_LINE_ROW + SEARCH_LIMIT
    row = _find_label_row(ws, COL_TOTALS_LABEL, _is_subtotal, FIRST_LINE_ROW, last)
    if row is None:
        raise BlindsImportError(
            f"No \"Sub Total\" label found in column {COL_TOTALS_LABEL} anywhere between rows "
            f"{FIRST_LINE_ROW} and {last}. That label is how this import finds the money on a "
            f"sheet, since the rows move with the number of blinds. Either this isn't the blinds "
            f"quote template, or that label has been renamed \u2014 nothing was imported."
        )
    subtotal = _number(ws[f"{COL_LINE_TOTAL}{row}"].value)
    if subtotal is None:
        raise BlindsImportError(
            f"Found the \"Sub Total\" label on row {row}, but column {COL_LINE_TOTAL} beside it "
            f"holds {_text(ws[f'{COL_LINE_TOTAL}{row}'].value)!r} rather than an amount. If this "
            f"file has never been opened and saved in Excel, its formulas carry no results yet \u2014 "
            f"open it, save it, and try again."
        )

    # VAT / Total / Deposit sit under the subtotal. Located by their own
    # labels too, with the immediate next rows as a fallback for a sheet
    # that leaves them unlabelled.
    window_end = min(row + TOTALS_BLOCK_WINDOW, last)
    vat_row = _find_label_row(ws, COL_TOTALS_LABEL, _is_vat, row + 1, window_end) or row + 1
    total_row = _find_label_row(ws, COL_TOTALS_LABEL, _is_total, row + 1, window_end) or row + 2
    dep_row = _find_label_row(ws, COL_TOTALS_LABEL, _is_deposit, row + 1, window_end) or row + 3

    vat = _number(ws[f"{COL_LINE_TOTAL}{vat_row}"].value)
    total = _number(ws[f"{COL_LINE_TOTAL}{total_row}"].value)
    deposit = _number(ws[f"{COL_LINE_TOTAL}{dep_row}"].value)
    if vat is None or total is None:
        raise BlindsImportError(
            f"Found the Sub Total on row {row} (R{subtotal:,.2f}) but couldn't read the VAT "
            f"(row {vat_row}) and Total (row {total_row}) under it in column {COL_LINE_TOTAL}. "
            f"Nothing was imported."
        )
    if not _close(subtotal + vat, total):
        raise BlindsImportError(
            f"The totals on this sheet don't add up: Sub Total R{subtotal:,.2f} + VAT "
            f"R{vat:,.2f} = R{subtotal + vat:,.2f}, but the Total reads R{total:,.2f}. "
            f"Nothing was imported \u2014 check the sheet."
        )
    return {
        "row": row, "vat_row": vat_row, "total_row": total_row, "deposit_row": dep_row,
        "subtotal_ex_vat": round(subtotal, 2), "vat": round(vat, 2),
        "total_incl_vat": round(total, 2),
        "deposit": round(deposit, 2) if deposit is not None else None,
    }


def _find_rep_row(ws, after_row: int):
    """The row labelled "Rep" in column B — it carries the branch in
    column D and the rep in column E.

    Searched from below the totals first, which is where it sits on both
    real quotes seen (six rows under the Sub Total on each), then across
    the whole sheet as a fallback. Searching below first matters: it
    keeps the match away from the line items, where a room description
    could otherwise start with the same three letters.
    """
    last = FIRST_LINE_ROW + SEARCH_LIMIT

    def is_rep(n: str) -> bool:
        # Deliberately tight. "rep" and "repname" only — not merely
        # "starts with rep", which would match a room called
        # "Replacement" or a note beginning "Repeat".
        return n in ("rep", "repname", "reps")

    return (_find_label_row(ws, COL_REP_LABEL, is_rep, after_row + 1, last)
            or _find_label_row(ws, COL_REP_LABEL, is_rep, 1, after_row + 1))


def _read_lines(ws, stop_before_row: int) -> List[Dict[str, Any]]:
    """Every blind above the totals block, read strictly.

    Anything in this range carrying SOME data but not enough to be a
    blind is an error, never a skip: silently dropping a half-filled row
    loses a blind off a quote, which is the worst thing this import
    could do — the total would still reconcile against the remaining
    lines and nothing would look wrong.
    """
    lines: List[Dict[str, Any]] = []
    problems: List[str] = []
    for row in range(FIRST_LINE_ROW, stop_before_row):
        total = _number(ws[f"{COL_LINE_TOTAL}{row}"].value)
        blind_type = _text(ws[f"{COL_BLIND_TYPE}{row}"].value)
        width = _number(ws[f"{COL_WIDTH}{row}"].value)
        drop = _number(ws[f"{COL_DROP}{row}"].value)
        room = _text(ws[f"{COL_ROOM}{row}"].value)
        # A row counts as a line ATTEMPT only if it carries one of the
        # four things a blind is made of. Column C is deliberately not
        # part of that test (confirmed Sept 2026): real quotes have gap
        # rows and section text in the description column, and treating
        # those as broken blinds would reject a perfectly good sheet.
        # Gaps are skipped, never treated as the end of the list —
        # Ilse's quote has blanks at B24 and B28-32 with real blinds
        # below them.
        if total is None and not blind_type and width is None and drop is None:
            continue
        if not blind_type:
            problems.append(f"row {row}: no blind type in column {COL_BLIND_TYPE}")
            continue
        if width is None or drop is None:
            problems.append(f"row {row}: width/drop missing in columns {COL_WIDTH}/{COL_DROP}")
            continue
        if total is None:
            problems.append(f"row {row}: no line total in column {COL_LINE_TOTAL}")
            continue
        qty = _number(ws[f"{COL_QTY}{row}"].value)
        if qty is None or qty <= 0:
            problems.append(f"row {row}: quantity missing or not a positive number in column {COL_QTY}")
            continue
        lines.append({
            "row": row,
            "item_no": _text(ws[f"{COL_ITEM_NO}{row}"].value),
            "room": room,
            "width_mm": width,
            "drop_mm": drop,
            "side": _text(ws[f"{COL_SIDE}{row}"].value).upper(),
            "blind_type": blind_type,
            "colour": _text(ws[f"{COL_COLOUR}{row}"].value),
            "qty": qty,
            "book_price_ex_vat": round(total, 2),
        })
    if problems:
        raise BlindsImportError(
            "This sheet has rows that look like blinds but can't be read: "
            + "; ".join(problems)
            + ". Fix them in Excel and re-upload — nothing was imported."
        )
    return lines


def parse_blinds_quote(file_bytes: bytes, trade_discount_pct: float,
                       settlement_discount_pct: float, vat_pct: float,
                       filename: str = "") -> Dict[str, Any]:
    """Read one finalized blinds quote. Returns the parsed quote only —
    writes nothing, touches no database. Same contract as
    parse_master_spreadsheet(): either the whole file reads cleanly or
    it is rejected with a specific reason.

    Cost model (brief, Sept 2026): "Actual cost to Burgert = book price
    - 45% trade discount, + VAT, - 7.5% settlement discount."

    Bolton's own cost figures are all EX VAT — margin_pct everywhere
    else compares an ex-VAT line total against an ex-VAT cost, so an
    incl-VAT cost stored here would understate blinds margin by the VAT
    rate and make blinds look worse than flooring on the very KPI
    screens this import exists to feed. cost_ex_vat is therefore what
    goes on the line, and cost_incl_vat is carried alongside purely for
    review, because the incl-VAT number is what Burgert actually pays
    the supplier and so is what he can check this against.

    The two discounts multiply rather than add (0.55 x 0.925 = 0.50875,
    a 49.125% total reduction — NOT 52.5%): a settlement discount is
    taken off what is already the trade-discounted invoice. Settlement
    discount reducing cost rather than the client price matches the
    price book's own documented rule for the same field on flooring
    products — "kept entirely as margin, never passed through to a
    lower client price".
    """
    try:
        wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
    except Exception as e:
        raise BlindsImportError(f"Couldn't open that file as an Excel workbook ({e}).")
    ws = wb[wb.sheetnames[0]]

    client_name = _text(_cell(ws, CELL_CLIENT_NAME))
    if not client_name:
        raise BlindsImportError(
            f"No client name in {CELL_CLIENT_NAME}. Either this isn't the blinds quote template, "
            f"or the sheet was saved before the client details were filled in."
        )

    # Totals FIRST. It fixes the boundary for the line read below, and
    # the Rep row is found relative to it.
    totals = _find_totals(ws)

    # Branch and Rep both live on the row labelled "Rep" — column D and
    # column E of it. Neither is at a fixed cell: on the two real quotes
    # seen, that row is 48 on one sheet and 40 on the other.
    rep_row = _find_rep_row(ws, totals["row"])
    if rep_row is None:
        raise BlindsImportError(
            f'No "Rep" label found in column {COL_REP_LABEL}. That row carries the branch '
            f'(column {COL_BRANCH}) as well as the rep, and a job with no branch can\'t be '
            f'reported on. Nothing was imported.'
        )
    branch_raw = _text(ws[f"{COL_BRANCH}{rep_row}"].value).upper()
    branch = BRANCH_CODES.get(branch_raw)
    if not branch:
        raise BlindsImportError(
            f"Branch in {COL_BRANCH}{rep_row} (the row labelled \"Rep\") reads "
            f"{branch_raw or '(blank)'!r} — expected {' or '.join(repr(k) for k in BRANCH_CODES)}. "
            f"Nothing was imported."
        )

    lines = _read_lines(ws, stop_before_row=totals["row"])
    if not lines:
        raise BlindsImportError(
            f"No blinds found on this sheet. Line items are read from row {FIRST_LINE_ROW} down, "
            f"and a row needs a blind type, width, drop, quantity and line total."
        )
    # Belt and braces: the strict read and the totals scan are two
    # different passes over the same rows, so they must agree.
    strict_sum = round(sum(l["book_price_ex_vat"] for l in lines), 2)
    if not _close(strict_sum, totals["subtotal_ex_vat"]):
        raise BlindsImportError(
            f"The {len(lines)} blind(s) read add up to R{strict_sum:,.2f}, but the subtotal on the "
            f"sheet (row {totals['row']}) is R{totals['subtotal_ex_vat']:,.2f}. Nothing was imported — "
            f"a line is being misread."
        )

    # Rep (brief's own open item): E48 is a formula pulling D15 back
    # again, so it holds the client reference, not a person. Detected
    # rather than assumed — if the template is fixed later so reps type
    # a real name over it, this starts working with no code change.
    rep_cell = f"{COL_REP}{rep_row}"
    rep_raw = _text(ws[rep_cell].value)
    client_reference = _text(_cell(ws, CELL_CLIENT_REFERENCE))
    rep_usable = bool(rep_raw) and rep_raw.casefold() != client_reference.casefold()
    rep_reason = ""
    if not rep_raw:
        rep_reason = f"{rep_cell} is empty."
    elif not rep_usable:
        rep_reason = (f"{rep_cell} reads {rep_raw!r}, which is the client reference from "
                      f"{CELL_CLIENT_REFERENCE} — the template's Rep cell is a formula (=D15), "
                      f"not a typed name.")

    keep = 1.0 - trade_discount_pct
    settle = 1.0 - settlement_discount_pct
    for line in lines:
        cost_ex_vat = round(line["book_price_ex_vat"] * keep * settle, 2)
        line["cost_ex_vat"] = cost_ex_vat
        line["cost_incl_vat"] = round(cost_ex_vat * (1 + vat_pct), 2)
        line["margin_pct"] = round(
            (line["book_price_ex_vat"] - cost_ex_vat) / line["book_price_ex_vat"] * 100, 2
        ) if line["book_price_ex_vat"] else 0.0
        # The description a human reads on the quote line.
        bits = [line["blind_type"]]
        if line["colour"]:
            bits.append(line["colour"])
        line["product_name"] = " — ".join(bits)
        note_bits = []
        if line["room"]:
            note_bits.append(line["room"])
        if line["side"]:
            note_bits.append(f"{line['side']} side")
        if line["qty"] and line["qty"] != 1:
            note_bits.append(f"{line['qty']:g} units")
        line["line_notes"] = ", ".join(note_bits)

    cost_ex_vat_total = round(sum(l["cost_ex_vat"] for l in lines), 2)
    # A moved totals block is NORMAL, not a warning — the rows shift with
    # every quote length, which is the whole reason these are found by
    # label. Where things were found is reported as plain information
    # below (`rows`) so it can be checked against the sheet, without
    # crying wolf on every import.
    warnings: List[str] = []
    if totals["deposit"] is not None and not _close(totals["deposit"], totals["total_incl_vat"] * 0.70):
        warnings.append(
            f"The deposit on the sheet (R{totals['deposit']:,.2f}) isn't 70% of the total "
            f"(R{totals['total_incl_vat'] * 0.70:,.2f}). The sheet's own figure is used."
        )
    return {
        "source_file": filename,
        "parsed_at": datetime.utcnow().isoformat(),
        "client": {
            "name": client_name,
            "address": _text(_cell(ws, CELL_CLIENT_ADDRESS)),
            "reference": client_reference,
            "phone": _text(_cell(ws, CELL_CLIENT_PHONE)),
        },
        "branch": branch,
        "branch_code": branch_raw,
        # Where each thing was actually found. Shown on the review
        # screen so the mapping can be checked against the open
        # spreadsheet at a glance — the one thing that can't be verified
        # from the numbers alone.
        "rows": {
            "first_line": lines[0]["row"] if lines else None,
            "last_line": lines[-1]["row"] if lines else None,
            "sub_total": totals["row"],
            "vat": totals["vat_row"],
            "total": totals["total_row"],
            "deposit": totals["deposit_row"],
            "rep": rep_row,
        },
        "rep": {"raw": rep_raw, "usable": rep_usable, "reason": rep_reason},
        "lines": lines,
        "totals": {
            "subtotal_ex_vat": totals["subtotal_ex_vat"],
            "vat": totals["vat"],
            "total_incl_vat": totals["total_incl_vat"],
            "deposit": totals["deposit"],
            "row": totals["row"],
        },
        "cost": {
            "trade_discount_pct": trade_discount_pct,
            "settlement_discount_pct": settlement_discount_pct,
            "cost_ex_vat": cost_ex_vat_total,
            "cost_incl_vat": round(cost_ex_vat_total * (1 + vat_pct), 2),
            "gross_profit_ex_vat": round(totals["subtotal_ex_vat"] - cost_ex_vat_total, 2),
            "margin_pct": round(
                (totals["subtotal_ex_vat"] - cost_ex_vat_total) / totals["subtotal_ex_vat"] * 100, 2
            ) if totals["subtotal_ex_vat"] else 0.0,
        },
        "warnings": warnings,
    }
