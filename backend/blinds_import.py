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
    """Every row between the header and the Sub Total, read per-row.

    CHANGED Sept 2026, against two real quotes (Stegman, Costa). The
    previous version demanded type + width + drop + qty + price on
    every row and rejected the whole file otherwise — which threw out
    both sheets, because a real blinds quote is not a uniform grid:

      * a heading row carrying only a description ("East Wing
        Downstairs", "Living Areas") — a grouping label, not a blind;
      * a real line with no spec at all ("Pelmets", qty 2, R3 300;
        "Valance brackets", qty 113, R1 356) — an add-on that has no
        width, drop, type or colour and never will;
      * a real line with no price yet (the four Somfy motor/remote
        items on Costa's sheet) — quoted later, not never;
      * rows carrying nothing but a leftover item number in column B
        (Stegman rows 27-41, Costa 74 and 77) — the template's unused
        numbering, not data.

    Burgert's rule, and the one applied here: **"does this row have
    enough to be usefully imported", not "does this row have every
    column filled"**. A row is only reported when it is genuinely
    ambiguous — money or measurements with nothing to say what they
    are for — because that is the one case where guessing would put a
    figure on a quote nobody can identify.
    """
    lines: List[Dict[str, Any]] = []
    problems: List[str] = []
    section = ""          # the most recent heading, carried onto the lines under it
    for row in range(FIRST_LINE_ROW, stop_before_row):
        room = _text(ws[f"{COL_ROOM}{row}"].value)
        blind_type = _text(ws[f"{COL_BLIND_TYPE}{row}"].value)
        colour = _text(ws[f"{COL_COLOUR}{row}"].value)
        side = _text(ws[f"{COL_SIDE}{row}"].value).upper()
        width = _number(ws[f"{COL_WIDTH}{row}"].value)
        drop = _number(ws[f"{COL_DROP}{row}"].value)
        qty = _number(ws[f"{COL_QTY}{row}"].value)
        total = _number(ws[f"{COL_LINE_TOTAL}{row}"].value)
        # A measurement that isn't a plain number — Costa's "85,4LM"
        # for a run of valance. Kept verbatim rather than discarded;
        # it is the only size that line has.
        width_raw = _text(ws[f"{COL_WIDTH}{row}"].value)
        drop_raw = _text(ws[f"{COL_DROP}{row}"].value)

        spec = [width, drop, qty, total]
        has_spec = (any(v is not None for v in spec) or bool(blind_type)
                    or bool(colour) or bool(side) or bool(width_raw) or bool(drop_raw))

        # Nothing but a description: a section heading. Held and applied
        # to the lines that follow, so the grouping survives into the
        # quote instead of being thrown away.
        if room and not has_spec:
            section = room
            continue
        # Nothing at all (or nothing but the template's own leftover
        # item number in column B, which is never read here). It also
        # ENDS the current section: on Costa's sheet the add-ons
        # (valances, brackets, the Somfy motors) sit below a blank row,
        # and without this they would inherit "East Wing Upstairs" and
        # read as if they belonged to that room. An inference, and a
        # deliberately cheap one to get wrong — the worst case is a
        # missing word in a note, never a wrong number.
        if not has_spec and not room:
            section = ""
            continue
        # Money or measurements with nothing naming them. The one real
        # ambiguity, and the only thing still worth stopping for.
        description = room or blind_type or colour
        if not description:
            bits = []
            if total is not None:
                bits.append(f"a price of R{total:,.2f}")
            if width is not None or drop is not None:
                bits.append("dimensions")
            if qty is not None:
                bits.append(f"a quantity of {qty:g}")
            problems.append(f"row {row}: has {' and '.join(bits)} but nothing in columns "
                            f"{COL_ROOM}, {COL_BLIND_TYPE} or {COL_COLOUR} saying what it is")
            continue

        # "TBC" written into the Colour column is the sheet saying the
        # colour isn't chosen yet — stored as no colour, which is the
        # same state the quote builder's own TBC placeholder produces
        # and which the send-time check already looks for.
        if _norm(colour) == "tbc":
            colour = ""
        lines.append({
            "row": row,
            "section": section,
            "item_no": _text(ws[f"{COL_ITEM_NO}{row}"].value),
            "room": room,
            "width_mm": width,
            "drop_mm": drop,
            "width_raw": width_raw if width is None else "",
            "drop_raw": drop_raw if drop is None else "",
            "side": side,
            "blind_type": blind_type,
            "colour": colour,
            "qty": qty if (qty is not None and qty > 0) else 1,
            "qty_stated": qty is not None,
            "book_price_ex_vat": round(total, 2) if total is not None else None,
            "price_tbc": total is None,
        })
    if problems:
        raise BlindsImportError(
            "This sheet has rows that can't be read: " + "; ".join(problems)
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
    # The lines that carry a price must add up to the sheet's own Sub
    # Total. Unpriced lines are excluded from this on purpose — the
    # sheet's subtotal doesn't include them either, so counting them
    # would guarantee a mismatch on every quote that has one.
    priced = [l for l in lines if not l["price_tbc"]]
    strict_sum = round(sum(l["book_price_ex_vat"] for l in priced), 2)
    if not _close(strict_sum, totals["subtotal_ex_vat"]):
        raise BlindsImportError(
            f"The {len(priced)} priced line(s) read add up to R{strict_sum:,.2f}, but the Sub Total "
            f"on the sheet (row {totals['row']}) is R{totals['subtotal_ex_vat']:,.2f}. Nothing was "
            f"imported — a line is being misread."
        )

    # Rep (brief's own open item): E48 is a formula pulling D15 back
    # again, so it holds the client reference, not a person. Detected
    # rather than assumed — if the template is fixed later so reps type
    # a real name over it, this starts working with no code change.
    rep_cell = f"{COL_REP}{rep_row}"
    rep_raw = _text(ws[rep_cell].value)
    client_reference = _text(_cell(ws, CELL_CLIENT_REFERENCE))
    # A name has letters in it. Stegman's sheet has 0 in this cell —
    # the =D15 formula resolving against an empty Client Reference —
    # and "0" is not a rep. Confirmed against the real file, which is
    # the only reason this case is known about at all.
    rep_has_letters = any(ch.isalpha() for ch in rep_raw)
    rep_usable = (bool(rep_raw) and rep_has_letters
                  and rep_raw.casefold() != client_reference.casefold())
    rep_reason = ""
    if not rep_raw:
        rep_reason = f"{rep_cell} is empty."
    elif not rep_has_letters:
        rep_reason = (f"{rep_cell} reads {rep_raw!r} — the template's Rep cell is a formula "
                      f"(={CELL_CLIENT_REFERENCE}) and the Client Reference it points at is "
                      f"empty, so it resolves to a number rather than a name.")
    elif not rep_usable:
        rep_reason = (f"{rep_cell} reads {rep_raw!r}, which is the client reference from "
                      f"{CELL_CLIENT_REFERENCE} — the template's Rep cell is a formula (=D15), "
                      f"not a typed name.")

    keep = 1.0 - trade_discount_pct
    settle = 1.0 - settlement_discount_pct
    for line in lines:
        book = line["book_price_ex_vat"]
        if book is None:
            # No price on the sheet yet. Zero, not a guess — and carried
            # with price_tbc so it can be seen, reported, and blocked
            # before the quote goes out.
            line["cost_ex_vat"] = 0.0
            line["cost_incl_vat"] = 0.0
            line["margin_pct"] = 0.0
        else:
            cost_ex_vat = round(book * keep * settle, 2)
            line["cost_ex_vat"] = cost_ex_vat
            line["cost_incl_vat"] = round(cost_ex_vat * (1 + vat_pct), 2)
            line["margin_pct"] = round((book - cost_ex_vat) / book * 100, 2) if book else 0.0

        # The description leads with the ALLOCATION (column C), because
        # that is what the sheet itself leads with and what the client
        # reads — "Living Room East side Stack Left", "Pelmets",
        # "Valance brackets". Type and colour are supporting detail
        # rather than the name, which also stops a row whose Type column
        # holds a note ("R4600 Ex Vat Per motor", Costa rows 78-81) from
        # becoming the product name. Falls back to type, then colour,
        # for a row with no allocation.
        line["product_name"] = line["room"] or line["blind_type"] or line["colour"]
        note_bits = []
        if line["section"]:
            note_bits.append(line["section"])
        detail = " ".join(x for x in (line["blind_type"], line["colour"]) if x)
        if detail:
            note_bits.append(detail)
        size = ""
        if line["width_mm"] is not None and line["drop_mm"] is not None:
            size = f"{line['width_mm']:g}\u00d7{line['drop_mm']:g}mm"
        elif line["width_raw"] or line["drop_raw"]:
            size = " ".join(x for x in (line["width_raw"], line["drop_raw"]) if x)
        if size:
            note_bits.append(size + (f" {line['side']}" if line["side"] else ""))
        elif line["side"]:
            note_bits.append(f"{line['side']} side")
        if line["qty_stated"] and line["qty"] != 1:
            note_bits.append(f"{line['qty']:g} units")
        if line["price_tbc"]:
            note_bits.append("price TBC")
        line["line_notes"] = ", ".join(note_bits)

    cost_ex_vat_total = round(sum(l["cost_ex_vat"] for l in lines), 2)
    unpriced = [l for l in lines if l["price_tbc"]]
    # A moved totals block is NORMAL, not a warning — the rows shift with
    # every quote length, which is the whole reason these are found by
    # label. Where things were found is reported as plain information
    # below (`rows`) so it can be checked against the sheet, without
    # crying wolf on every import.
    warnings: List[str] = []
    if not client_reference:
        # _blinds_import_match() keys re-imports on this. Without one,
        # a revised sheet has nothing to match against and would come in
        # as a second job beside the first rather than replacing it.
        warnings.append(
            f"No Client Reference in {CELL_CLIENT_REFERENCE}. This imports fine, but a later "
            f"re-import of the same job won't be able to find it to replace — it would come in "
            f"as a second job. Fill in the reference on the sheet if this quote is likely to change."
        )
    if unpriced:
        # Said out loud, because the quote total will be short by
        # whatever these turn out to cost. The send-time TBC check
        # blocks the document until they're filled in.
        warnings.append(
            f"{len(unpriced)} line(s) have no price on the sheet and come in at R0 — "
            + "; ".join(f"row {l['row']} {l['product_name']}" for l in unpriced[:6])
            + (f" and {len(unpriced) - 6} more" if len(unpriced) > 6 else "")
            + ". Price them on the quote before sending it."
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
        "unpriced_count": len(unpriced),
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
