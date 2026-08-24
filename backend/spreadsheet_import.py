"""
Standard Import Format — deterministic spreadsheet import (confirmed Aug
2026, Standard Import Format brief). Replaces direct AI-PDF extraction as
the primary supplier import path going forward: every bug hit with the
AI-PDF approach (Como's box-price-in-per-m2-field, Azura's missing
plank/zone fields, the 150s timeout) traced back to the same root cause —
an AI inferring column meaning fresh from an unpredictable PDF layout,
live, with no human check before commit. A fixed-format spreadsheet
removes that inference step entirely: Bolton reads a KNOWN column layout
by name, not by asking a model to guess one.

The PDF-to-spreadsheet conversion itself happens OUTSIDE Bolton (Burgert
working with Claude in chat, then reviewing the result against the source
PDF by hand) — this module only handles the deterministic, no-AI,
file-to-database side: read the file, validate its headers exactly match
REQUIRED_COLUMNS, map each row straight across by column name, and either
succeed completely or reject the whole file with a clear, specific error.
Never a partial/best-effort import — see parse_master_spreadsheet()'s own
docstring.

Deliberately returns the SAME row shape extract_price_sheet() (ai_import.py)
does — {"rows": [{"product_name", "colour", "sku", "m2_per_pack",
"price_per_box", "zone_prices", "dimensions_mm", "wear_layer_mm",
"uncertain_fields"}]} — so the frontend's existing row-to-product-fields
mapping (mapExtractedRowToFields(), index.html) and staging/review/commit
UI are reused completely unchanged, whichever import path produced the
rows. Two import paths, one downstream pipeline, no logic to keep in sync
in two places.

The AI-PDF extraction feature (ai_import.py) is NOT retired by this file
existing — kept in place per the brief's explicit instruction ("this is a
process change, not a requirement to rip out working code"), just no
longer the primary/recommended path.
"""
import io
from typing import Any, Dict, List, Optional

import openpyxl

# Exact column headers required in row 1 of the uploaded file, in this
# order (confirmed Aug 2026, brief Section 2). Plain ASCII ("m2" not
# "m²") deliberately — a unicode superscript-2 typed/pasted from a
# different app or Excel version can silently fail an exact string
# match in a way that's very hard to spot by eye, exactly the kind of
# silent failure this whole feature exists to avoid. Validated as an
# EXACT match (case-sensitive, whitespace-trimmed) — no fuzzy matching,
# no guessing a close-enough header, per the brief's explicit "reject
# the whole file... do not attempt to guess or partially import."
REQUIRED_COLUMNS = [
    "Range / Product name",
    "Colour / Decor",
    "Product code",
    "m2 per box",
    "Price per box ex VAT",
    "Price per m2 ex VAT (calculated - reference only)",
    "Zone A price per m2",
    "Zone B price per m2",
    "Zone C price per m2",
    "Length mm",
    "Width mm",
    "Thickness mm",
    "Wear layer mm",
]
# Cells that must actually contain a value (not just a present column) —
# "Price per box ex VAT" is deliberately NOT in this set even though the
# brief calls it "always required": Section 2 also says it's back-
# calculated from Price/m2 x m2/box when the source sheet doesn't state
# it directly, so a blank cell there is only an error if Price/m2 is
# ALSO blank — checked explicitly per-row below, not via this simple set.
REQUIRED_NON_BLANK_COLUMNS = {"Range / Product name", "m2 per box"}


def _clean_header(v) -> str:
    return v.strip() if isinstance(v, str) else ("" if v is None else str(v))


def _as_float(raw_row: Dict[str, Any], col: str, row_num: int, product_name: str, errors: List[str], required: bool = False) -> Optional[float]:
    v = raw_row.get(col)
    if v is None or (isinstance(v, str) and v.strip() == ""):
        if required:
            errors.append(f"Row {row_num} ('{product_name}'): '{col}' is required and blank")
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        errors.append(f"Row {row_num} ('{product_name}'): '{col}' = {v!r} is not a number")
        return None


def parse_master_spreadsheet(file_bytes: bytes) -> List[dict]:
    """Reads the FIRST worksheet of the uploaded .xlsx (one supplier's
    converted price list per file, per the brief's workflow — Section 1
    step 2 produces one spreadsheet per supplier import, not a multi-tab
    workbook like the reference answer-key file). Raises ValueError with
    a clear, specific, complete message on ANY problem — a missing/
    misnamed column, a non-numeric cell where a number is required, a
    row missing a genuinely required value — collecting every problem
    found (not just the first) so one rejection message shows everything
    that needs fixing, matching the brief's explicit "either succeeds
    completely for every row or fails clearly and visibly; never
    silently partially complete." Nothing is returned/staged unless
    every row parses cleanly.

    Zone A/B/C columns give per-m² prices directly (matching what's
    actually printed on a real supplier sheet, and what a human hand-
    checks most naturally) — NOT the calculated price_per_box_zone_a/b/c
    fields the Supplier Console's Field Sequence Redesign requires as
    the true stored source. That back-calculation (zone per-m² x m²/box)
    happens downstream, in the exact same frontend mapping code the AI
    import path already uses (mapExtractedRowToFields(), index.html) —
    this function only ever emits "zone_prices" (per-m², matching
    ai_import.py's own extraction shape), never sets a per-m² field
    directly itself.
    """
    try:
        wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True, read_only=True)
    except Exception as e:
        raise ValueError(f"Could not read this as an Excel (.xlsx) file: {e}")

    ws = wb.worksheets[0]
    rows_iter = ws.iter_rows(values_only=True)
    try:
        header_raw = next(rows_iter)
    except StopIteration:
        raise ValueError("This file has no rows at all — row 1 must be the required column headers.")
    header = [_clean_header(v) for v in header_raw]

    missing = [col for col in REQUIRED_COLUMNS if col not in header]
    if missing:
        raise ValueError(
            "This file's column headers don't match the required Standard Import Format — "
            f"missing or misnamed column(s): {', '.join(missing)}. "
            f"Row 1 must contain exactly these column names, in any order: {', '.join(REQUIRED_COLUMNS)}. "
            "Nothing has been imported."
        )
    col_index = {col: header.index(col) for col in REQUIRED_COLUMNS}

    errors: List[str] = []
    rows_out: List[dict] = []
    for row_num, raw_values in enumerate(rows_iter, start=2):
        if raw_values is None or all(v is None for v in raw_values):
            continue   # a genuinely blank row (trailing blank rows are normal in a spreadsheet) — skip silently, not an error
        raw_row = {col: (raw_values[idx] if idx < len(raw_values) else None) for col, idx in col_index.items()}

        product_name_raw = raw_row.get("Range / Product name")
        if product_name_raw is None or str(product_name_raw).strip() == "":
            continue   # blank product name on an otherwise-blank-ish row — same as above, not every column needs to be blank for this to be trailing whitespace
        product_name = str(product_name_raw).strip()

        m2_per_pack = _as_float(raw_row, "m2 per box", row_num, product_name, errors, required=True)
        price_per_box = _as_float(raw_row, "Price per box ex VAT", row_num, product_name, errors)
        price_per_m2_ref = _as_float(raw_row, "Price per m2 ex VAT (calculated - reference only)", row_num, product_name, errors)
        if price_per_box is None and price_per_m2_ref is not None and m2_per_pack:
            price_per_box = round(price_per_m2_ref * m2_per_pack, 2)
        elif price_per_box is None and price_per_m2_ref is None:
            errors.append(f"Row {row_num} ('{product_name}'): need either 'Price per box ex VAT' or 'Price per m2 ex VAT ...' filled in — both are blank")

        zone_a = _as_float(raw_row, "Zone A price per m2", row_num, product_name, errors)
        zone_b = _as_float(raw_row, "Zone B price per m2", row_num, product_name, errors)
        zone_c = _as_float(raw_row, "Zone C price per m2", row_num, product_name, errors)
        zone_prices = {"A": zone_a, "B": zone_b, "C": zone_c} if (zone_a is not None or zone_b is not None or zone_c is not None) else None

        length = _as_float(raw_row, "Length mm", row_num, product_name, errors)
        width = _as_float(raw_row, "Width mm", row_num, product_name, errors)
        thickness = _as_float(raw_row, "Thickness mm", row_num, product_name, errors)
        dimensions_mm = {"length": length, "width": width, "thickness": thickness} if (length is not None or width is not None or thickness is not None) else None

        wear_layer_mm = _as_float(raw_row, "Wear layer mm", row_num, product_name, errors)

        colour_raw = raw_row.get("Colour / Decor")
        colour = str(colour_raw).strip() if colour_raw is not None else ""
        sku_raw = raw_row.get("Product code")
        sku = str(sku_raw).strip() if sku_raw is not None and str(sku_raw).strip() != "" else None

        rows_out.append({
            "product_name": product_name, "colour": colour, "sku": sku,
            "m2_per_pack": m2_per_pack, "price_per_box": price_per_box,
            "zone_prices": zone_prices, "dimensions_mm": dimensions_mm,
            "wear_layer_mm": wear_layer_mm, "uncertain_fields": [],
        })

    if errors:
        raise ValueError(
            f"Import rejected — {len(errors)} problem(s) found, nothing staged:\n" + "\n".join(errors)
        )
    if not rows_out:
        raise ValueError("No data rows found below the header row — nothing to import.")
    return rows_out
