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
  - Trust a fixed row number for the money. The brief says the sheet
    "grows with more blinds", which pushes the totals block down, so
    the totals are FOUND and cross-checked against the sum of the
    lines rather than read from row 42 on faith — see _find_totals().
"""
import io
from datetime import datetime
from typing import Any, Dict, List, Optional

import openpyxl

# ---------------------------------------------------------------- cells
# Confirmed cell mapping (brief, Sept 2026). The header block and the
# line columns are fixed positions; the totals row is NOT read from a
# fixed position — see _find_totals() for why and how.
CELL_CLIENT_NAME = "D12"
CELL_CLIENT_ADDRESS = "D13"
CELL_CLIENT_REFERENCE = "D15"
CELL_CLIENT_PHONE = "D16"
CELL_BRANCH = "D48"
CELL_REP = "E48"

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

EXPECTED_TOTALS_ROW = 42          # L42 subtotal, L43 VAT, L44 incl, L45 deposit
TOTALS_SEARCH_LIMIT = 400         # how far below row 20 to look if the block moved

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


def _looks_like_blind(ws, row: int) -> bool:
    """A row is a blind when it carries all four things a blind cannot
    be without: a type, a width, a drop and a line total. Used to walk
    past spacers and to tell a blind apart from the totals block."""
    return (_text(ws[f"{COL_BLIND_TYPE}{row}"].value) != ""
            and _number(ws[f"{COL_WIDTH}{row}"].value) is not None
            and _number(ws[f"{COL_DROP}{row}"].value) is not None
            and _number(ws[f"{COL_LINE_TOTAL}{row}"].value) is not None)


def _find_totals(ws) -> Dict[str, Any]:
    """Locate the subtotal / VAT / total / deposit block.

    NOT read from a fixed row. The brief says the sheet "grows with more
    blinds", which pushes the totals down, and reading L42 on a grown
    sheet would take a BLIND as the quote subtotal — a wrong number that
    looks entirely reasonable. So the block is found by what it IS:

      - walk down from the first line row, adding up the blinds;
      - the totals row is the first row that has a value in the total
        column but is NOT a blind (no type, no width, no drop),
        whose value equals the blinds added up so far, and which has a
        VAT figure under it that brings it to the row below that.

    Three independent conditions have to agree — the running sum, the
    blank item columns, and subtotal + VAT = total — which is what makes
    this safe to do without a fixed row number. If nothing satisfies all
    three, the import is rejected rather than guessed at.
    """
    running = 0.0
    counted = 0
    last_row = FIRST_LINE_ROW + TOTALS_SEARCH_LIMIT
    for row in range(FIRST_LINE_ROW, last_row):
        if _looks_like_blind(ws, row):
            running = round(running + _number(ws[f"{COL_LINE_TOTAL}{row}"].value), 2)
            counted += 1
            continue
        subtotal = _number(ws[f"{COL_LINE_TOTAL}{row}"].value)
        if subtotal is None or counted == 0:
            continue
        # Must look nothing like a line item, or a broken blind row
        # could pass itself off as the subtotal.
        if (_text(ws[f"{COL_BLIND_TYPE}{row}"].value)
                or _number(ws[f"{COL_WIDTH}{row}"].value) is not None
                or _number(ws[f"{COL_DROP}{row}"].value) is not None):
            continue
        if not _close(subtotal, running):
            continue
        vat = _number(ws[f"{COL_LINE_TOTAL}{row + 1}"].value)
        total = _number(ws[f"{COL_LINE_TOTAL}{row + 2}"].value)
        if vat is None or total is None or not _close(subtotal + vat, total):
            continue
        deposit = _number(ws[f"{COL_LINE_TOTAL}{row + 3}"].value)
        return {
            "row": row, "subtotal_ex_vat": round(subtotal, 2), "vat": round(vat, 2),
            "total_incl_vat": round(total, 2),
            "deposit": round(deposit, 2) if deposit is not None else None,
            "moved": row != EXPECTED_TOTALS_ROW,
            "blinds_counted": counted,
        }
    raise BlindsImportError(
        f"Couldn't find the totals block. {counted} blind(s) were read from row {FIRST_LINE_ROW} down, "
        f"adding up to R{running:,.2f}, but no row in column {COL_LINE_TOTAL} matches that with a VAT "
        f"line under it. Either a blind was misread or the sheet doesn't follow the template — "
        f"nothing was imported."
    )


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
        if total is None and not blind_type and width is None and drop is None and not room:
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

    branch_raw = _text(_cell(ws, CELL_BRANCH)).upper()
    branch = BRANCH_CODES.get(branch_raw)
    if not branch:
        raise BlindsImportError(
            f"Branch in {CELL_BRANCH} reads {branch_raw or '(blank)'!r} — expected "
            f"{' or '.join(repr(k) for k in BRANCH_CODES)}. Nothing was imported."
        )

    # Totals FIRST — that fixes the boundary, so the strict line read
    # below knows exactly where the blinds stop and can treat anything
    # unreadable inside that range as an error rather than a spacer.
    totals = _find_totals(ws)
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
    rep_raw = _text(_cell(ws, CELL_REP))
    client_reference = _text(_cell(ws, CELL_CLIENT_REFERENCE))
    rep_usable = bool(rep_raw) and rep_raw.casefold() != client_reference.casefold()
    rep_reason = ""
    if not rep_raw:
        rep_reason = f"{CELL_REP} is empty."
    elif not rep_usable:
        rep_reason = (f"{CELL_REP} reads {rep_raw!r}, which is the client reference from "
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
    warnings: List[str] = []
    if totals["moved"]:
        warnings.append(
            f"The totals block is at row {totals['row']}, not the template's row {EXPECTED_TOTALS_ROW} — "
            f"this sheet has grown. Figures were read from row {totals['row']} and reconcile against the lines."
        )
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
