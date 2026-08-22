"""
AI-Assisted Price Sheet Import (confirmed Aug 2026 — banked brief, built
ahead of its own stated precondition ["do not start until the Supplier
Console has been used manually for a few real supplier updates"] per
Burgert's explicit instruction to proceed anyway).

Sends a supplier's price sheet (PDF or photo) to the Claude API with a
structured extraction prompt and returns proposed staging rows for the
Supplier Console's EXISTING staging/commit/log workflow — this module
never writes to the price book itself. Extraction results land in the
console's staging area exactly like a manual entry, requiring the same
explicit "Commit Changes" click before anything is saved.

Every supplier uses a different sheet layout (Aspen: flat per-range
list; Azura: zone-column table per range) — the extraction prompt
describes the FIELDS to find, not a fixed table shape, per the brief's
explicit instruction, since a rigid per-column parser would break the
moment a supplier changes their layout.

Deliberately built on stdlib urllib, not a new httpx/requests/anthropic-
sdk dependency — same reasoning auth.py used for PBKDF2 over bcrypt: a
single, simple JSON POST doesn't justify a new dependency.
"""
import base64
import json
import os
import urllib.request
import urllib.error

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_MODEL = "claude-sonnet-5"

SUPPORTED_MEDIA_TYPES = {"application/pdf", "image/png", "image/jpeg", "image/webp"}

# Confirmed Aug 2026: describes fields to find, not a fixed table shape —
# every supplier so far has used a different layout, and the whole point
# of an AI-read approach (vs. a rigid per-column parser) is not breaking
# the moment a supplier's sheet changes. Explicitly told NOT to do the
# price/m2-per-box conversion itself — extract the raw price and its
# unit basis separately, and let the human reviewer do that math, since
# an AI doing that silently is exactly the shape of bug that caused the
# real Aspen price book mix-up this project already found and fixed.
EXTRACTION_SYSTEM_PROMPT = """You are extracting structured product pricing data from a flooring/blinds supplier's price sheet (PDF or photo).

Every supplier uses a different layout — some are a flat per-range list (one price per range), others use zone-column tables (e.g. Azura's Zone A/B/C pricing side by side for the same product). Read the actual structure of THIS document; do not assume a fixed table shape.

For each distinct product/range/colour combination found, extract:
- product_name: the range or product name (not the colour)
- colour: the colour or variant name, if shown (empty string if not applicable/not shown)
- unit_basis: one of "per_m2", "per_box", "per_piece" — exactly how the price is quoted ON THE SHEET
- price: the numeric price exactly as shown. Do NOT convert or derive it yourself — critically, if the sheet shows a per-box price, do NOT divide it by m2 per box to guess a per-m2 rate. Extract exactly the number shown, and separately extract m2_per_pack as its own field, so a human reviewer does that conversion deliberately, not the extraction step. (A previous real bug in this business happened from exactly this kind of silent unit conversion — treat it as a hard rule, not a suggestion.)
- m2_per_pack: m2 per box/pack, if shown on the sheet, else null
- zone_prices: if the sheet shows multiple zone/region prices for the same product (e.g. Zone A/B/C columns), an object like {"A": 218.00, "B": 228.00, "C": 238.00} — else null
- dimensions_mm: if plank/tile dimensions are shown, an object {"length": 1219.2, "width": 184.15, "thickness": 2.0} — else null
- sku: any product code/SKU shown, else null
- uncertain_fields: a list of the field names above you are NOT confident about (unclear scan, ambiguous unit, merged/damaged cells, a value that could reasonably be read more than one way). Be honest and specific — flag anything genuinely uncertain rather than silently guessing a confident-looking value.

Return ONLY a JSON object of this exact shape, no other text, no markdown code fences:
{"rows": [{"product_name": "", "colour": "", "unit_basis": "", "price": 0.0, "m2_per_pack": null, "zone_prices": null, "dimensions_mm": null, "sku": null, "uncertain_fields": []}]}"""


def extract_price_sheet(file_bytes: bytes, media_type: str, supplier: str, instructions: str = "") -> dict:
    """Calls the Claude API with the uploaded file as a document/image
    content block. Raises RuntimeError with a clear, specific message on
    any failure (missing API key, API error, unparseable response) — the
    caller (main.py's import endpoint) turns this into a clean HTTP
    error. Never returns a silently-empty or fabricated result on
    failure.

    instructions (confirmed Aug 2026): optional free text from the
    owner — e.g. "skip the clearance section" or "only Zone A/B, ignore
    Zone C" — appended to the user turn as extra guidance for THIS
    import only. Blank behaves exactly as before this was added: the
    extraction prompt is unchanged, nothing is appended."""
    if not ANTHROPIC_API_KEY:
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set on this server — required for AI-assisted "
            "price sheet import. Set it in Render's environment (never committed to source)."
        )
    if media_type not in SUPPORTED_MEDIA_TYPES:
        raise RuntimeError(f"Unsupported file type '{media_type}' — upload a PDF, PNG, JPEG, or WEBP.")

    b64 = base64.b64encode(file_bytes).decode("ascii")
    is_pdf = media_type == "application/pdf"
    content_block = {
        "type": "document" if is_pdf else "image",
        "source": {"type": "base64", "media_type": media_type, "data": b64},
    }

    user_text = f"This is {supplier}'s price sheet. Extract every product/range/colour row per the instructions."
    if instructions and instructions.strip():
        user_text += f"\n\nAdditional instructions for this import specifically: {instructions.strip()}"

    body = {
        "model": ANTHROPIC_MODEL,
        "max_tokens": 8000,
        "system": EXTRACTION_SYSTEM_PROMPT,
        "messages": [
            {"role": "user", "content": [
                content_block,
                {"type": "text", "text": user_text},
            ]},
        ],
    }

    req = urllib.request.Request(
        ANTHROPIC_API_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "content-type": "application/json",
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            api_result = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Claude API error ({e.code}): {detail[:500]}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Could not reach the Claude API: {e.reason}")

    text = "".join(block.get("text", "") for block in api_result.get("content", []) if block.get("type") == "text")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        raise RuntimeError(f"Claude's response wasn't valid JSON — got: {text[:500]}")
    if "rows" not in parsed or not isinstance(parsed["rows"], list):
        raise RuntimeError(f"Claude's response was missing the expected 'rows' list — got: {text[:500]}")
    return parsed
