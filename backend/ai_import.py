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
# Confirmed Aug 2026, real bug: this used to be the exact same 120s as
# the frontend's own outer timeout on the whole request (index.html) —
# a race, not a deliberate margin. If Claude genuinely took close to
# 120s, whichever timeout fired first was luck of the draw, and the
# frontend's own abort (firing at essentially the same wall-clock
# moment) would kill the connection before this backend timeout's own,
# more specific error could ever be received. Now shorter than the
# frontend's outer timeout on purpose, with real margin — so THIS
# timeout (with a clear, specific message) fires first if Claude is
# genuinely slow, rather than a generic "could not reach the server"
# on the frontend masking what actually happened.
CLAUDE_API_TIMEOUT_SECONDS = 150

SUPPORTED_MEDIA_TYPES = {"application/pdf", "image/png", "image/jpeg", "image/webp"}

# Confirmed Aug 2026: describes fields to find, not a fixed table shape —
# every supplier so far has used a different layout, and the whole point
# of an AI-read approach (vs. a rigid per-column parser) is not breaking
# the moment a supplier's sheet changes. Explicitly told NOT to do the
# price/m2-per-box conversion itself — extract the raw numbers exactly as
# shown, and let Bolton's own code (never the AI) do that division, since
# an AI doing that silently is exactly the shape of bug that caused the
# real Aspen price book mix-up this project already found and fixed.
#
# Field Sequence Redesign (confirmed Aug 2026 — the actual root cause of
# the Como Flooring pricing bug): price_per_box/zone_box_prices are now
# the PRIMARY price fields this prompt asks for — Bolton's Supplier
# Console calculates every per-m² price itself, always as box price ÷
# m2_per_pack (see recompute_calculated_prices() in main.py), and never
# accepts a per-m² figure as a direct input again. price/zone_prices are
# kept as a secondary cross-check, not the field the mapping code trusts.
EXTRACTION_SYSTEM_PROMPT = """You are extracting structured product pricing data from a flooring/blinds supplier's price sheet (PDF or photo).

Every supplier uses a different layout — some are a flat per-range list (one price per range), others use zone-column tables (e.g. Azura's Zone A/B/C pricing side by side for the same product). Read the actual structure of THIS document; do not assume a fixed table shape.

CRITICAL, confirmed real bug (Azura's price sheet, 40 products — Plank Length/Width/Thickness and Zone A/B/C prices came back blank for every single product, despite being clearly present and readable on the sheet): once you determine this document uses a zone-column structure and/or states plank dimensions, apply that SAME extraction to EVERY row, not just the first few. Do not let a long/dense sheet cause you to extract structured fields fully for early rows and then quietly fall back to only the simplest fields (name/price) for later ones to save effort — every row gets the same full treatment as the first.

For each distinct product/range/colour combination found, extract:
- product_name: the range or product name (not the colour). CRITICAL, a real confirmed bug happened from getting this wrong (Como Flooring's price list): some sheets list pricing in one table (one row per RANGE) and colours/decor codes in a SEPARATE table elsewhere in the document, grouped under each range's own heading. Only attach a colour to the range it's ACTUALLY listed under in that colour table — never a visually-nearby or similarly-named range. A real mix-up happened this way: "Como Bellagio" (a colour code listed under the "Como Lake" heading) got attached to the "Como Bonsai 2.0" product instead, producing a garbled product_name like "Como Bonsai 2.0 / Como Bellagio" that is two different products' names slammed together. If a document splits pricing and colours across separate tables, cross-check every colour against exactly one range heading before pairing them — if genuinely unclear which range a colour belongs to, leave colour blank and flag "colour" in uncertain_fields rather than guessing.
- colour: the colour or variant name, if shown (empty string if not applicable/not shown)
- unit_basis: one of "per_m2", "per_box", "per_piece" — exactly how the price field below is quoted ON THE SHEET
- price: the numeric price exactly as shown, matching unit_basis. Do NOT convert or derive it yourself. Extract exactly the number shown, and separately extract m2_per_pack as its own field, so any needed conversion happens deliberately in Bolton's own code, not the extraction step. (A previous real bug in this business happened from exactly this kind of silent unit conversion — treat it as a hard rule, not a suggestion.)
- m2_per_pack: m2 per box/pack, if shown on the sheet, else null
- price_per_box: CRITICAL, root cause of a real confirmed bug in this business (Como Flooring's price list — a box price ended up stored as a per-m² price because this field didn't exist yet and the box price was mapped into the per-m² one instead). Bolton now calculates per-m² prices itself as price_per_box ÷ m2_per_pack — it NEVER stores a value here as a per-m² price, no matter what. So: if the sheet states a box/pack price directly for this product (whether or not it's zone-priced — see zone_box_prices below for the zoned case), put that exact number here. Else null.
- zone_box_prices: if the sheet shows multiple zone/region prices for the same product AS BOX PRICES (e.g. Zone A/B/C box-price columns), an object like {"A": 1104.15, "B": 1155.15, "C": 1179.65} — else null. Use this instead of price_per_box when the product is zone-priced.
- zone_prices: if the sheet ALSO shows (or ONLY shows) per-m² zone prices for the same product, an object like {"A": 219.95, "B": 228.95, "C": 235.95} — else null. This is a secondary/cross-check field now, not the primary source of truth — Bolton prefers zone_box_prices when both are present. CRITICAL, a real confirmed bug happened from getting this wrong (Como Flooring's price list): some sheets show BOTH a per-m² price AND a per-box price for the SAME zone, right next to each other (e.g. "Zone A: R219.95/m² | R1104.15/box" — two clearly different numbers for the one zone). zone_prices must ALWAYS hold the per-m² figure for each zone, never the per-box one — that one goes in zone_box_prices above instead. If you cannot confidently tell which of two nearby numbers for the same zone is the per-m² one and which is the per-box one, record whichever you're MORE confident is the box price into zone_box_prices (the box number is usually the more directly/plainly printed one), leave zone_prices for that zone out, and flag "zone_prices" in uncertain_fields.
  SELF-CHECK before finalizing, whenever both a per-m² and a per-box number are visible for the same zone: the per-m² number x m2_per_pack should land close to the per-box number for that same zone (e.g. 219.95 x 5.020 ≈ 1104.15, confirmed real example). If they don't roughly reproduce each other this way, you likely have the two numbers backwards — re-read the columns.
- dimensions_mm: if plank/tile dimensions are shown, an object {"length": 1219.2, "width": 184.15, "thickness": 2.0} — else null. CRITICAL: many sheets print all three as ONE combined string in a single "Dimensions (mm)" column (e.g. "229 x 1219.2 x 2.0") instead of three separate columns — split that string into its three numbers and assign each to the correct field by reading THIS column's own header/label for what order it's printed in (e.g. "L x W x T" vs "W x L x T" — sheets are not consistent about this, do not assume one fixed order across different suppliers or even different sections of the same sheet). If the header doesn't state the order and you genuinely cannot tell which number is length vs width from context, flag "dimensions_mm" in uncertain_fields rather than guessing — a silent length/width swap is exactly the kind of error this business has been burned by before with other fields.
- sku: any product code/SKU shown, else null
- uncertain_fields: a list of the field names above you are NOT confident about (unclear scan, ambiguous unit, merged/damaged cells, a value that could reasonably be read more than one way). Be honest and specific — flag anything genuinely uncertain rather than silently guessing a confident-looking value.

Return ONLY a JSON object of this exact shape, no other text, no markdown code fences. Output it COMPACT — no pretty-printing, no indentation, no line breaks between fields or rows, minimal whitespace throughout. This is machine-parsed, not read by a person; every character spent on formatting is an output-token limit you're competing against yourself for, for zero benefit (a compact and a pretty-printed version parse to the exact same data). CRITICAL: "compact" means whitespace only — no indentation, no line breaks. It does NOT mean fewer fields, shortened field names, or skipping a field/row that the sheet actually shows just to save space. Every field this document has data for must still be populated, for every row, exactly as specified above — compactness is purely about formatting characters, never about completeness.
{"rows": [{"product_name": "", "colour": "", "unit_basis": "", "price": 0.0, "m2_per_pack": null, "price_per_box": null, "zone_box_prices": null, "zone_prices": null, "dimensions_mm": null, "sku": null, "uncertain_fields": []}]}"""


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
        # Confirmed Aug 2026, real bug, two rounds: first raised 8000 ->
        # 16000, which turned out to still be too tight for a genuinely
        # single-page sheet (Como Flooring) — confirming the ceiling
        # itself was just set too low, not that sheets were unusually
        # large. Investigated the OTHER lever (not just raising the
        # number blindly, per explicit request): the JSON schema itself
        # (9 keys/row, no repeated field names beyond normal per-object
        # JSON structure) isn't unusually verbose — but the prompt never
        # told the model to output COMPACT JSON, and Claude's default
        # instinct for "return a JSON object" is to pretty-print it
        # (indentation, line breaks) — pure whitespace overhead that
        # json.loads() below discards entirely, competing against this
        # exact token ceiling for zero benefit. Added an explicit
        # compact-output instruction to the prompt (see above) to
        # address the real waste, AND raised this further as a safety
        # margin on top of that — not either/or. Not a parsing bug
        # (json.loads below already receives and parses the ENTIRE
        # response text, no truncation of its own). See the stop_reason
        # check further down for a clear, specific error instead of a
        # confusing "wasn't valid JSON" if a sheet is ever large enough
        # to hit even this ceiling.
        "max_tokens": 32000,
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
        with urllib.request.urlopen(req, timeout=CLAUDE_API_TIMEOUT_SECONDS) as resp:
            api_result = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Claude API error ({e.code}): {detail[:500]}")
    except TimeoutError:
        # Confirmed Aug 2026: caught separately from the generic URLError
        # below, and BEFORE it — a socket timeout on urlopen() is a
        # TimeoutError (socket.timeout is the same class since Python
        # 3.10), which URLError's own except clause would otherwise catch
        # too (TimeoutError is an OSError subclass, same family as the
        # connection-refused/DNS-failure cases URLError normally reports)
        # and describe with the same vague "Could not reach the Claude
        # API" wording — misleading for a genuine timeout, which is a
        # completely different situation (the request WAS reaching
        # Claude, it just wasn't finishing in time) needing a different
        # fix (raise the timeout / reduce what's being extracted), not
        # "check your network connection."
        raise RuntimeError(
            f"Claude API didn't respond within {CLAUDE_API_TIMEOUT_SECONDS}s — the extraction is taking "
            "longer than that for this specific document, not a connectivity problem. A dense sheet "
            "with many rows can genuinely take a while to fully extract; try again, or a smaller/simpler sheet."
        )
    except urllib.error.URLError as e:
        if isinstance(e.reason, TimeoutError):
            raise RuntimeError(
                f"Claude API didn't respond within {CLAUDE_API_TIMEOUT_SECONDS}s — the extraction is taking "
                "longer than that for this specific document, not a connectivity problem. A dense sheet "
                "with many rows can genuinely take a while to fully extract; try again, or a smaller/simpler sheet."
            )
        raise RuntimeError(f"Could not reach the Claude API: {e.reason}")

    # Confirmed Aug 2026: checked BEFORE attempting to parse, so a
    # too-large sheet gets a clear, specific, actionable error ("hit the
    # token limit, contact support to raise it") instead of a confusing
    # "wasn't valid JSON" that doesn't explain why — exactly the
    # ambiguity a real truncated response from a larger supplier sheet
    # (Como Flooring) hit and took real investigation to diagnose.
    if api_result.get("stop_reason") == "max_tokens":
        raise RuntimeError(
            "Claude's response was cut off — this price sheet has more products than the current "
            f"extraction limit ({body['max_tokens']} tokens) supports. Increase max_tokens in ai_import.py, "
            "or split this sheet into smaller uploads."
        )

    text = "".join(block.get("text", "") for block in api_result.get("content", []) if block.get("type") == "text")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        raise RuntimeError(f"Claude's response wasn't valid JSON — got: {text[:500]}")
    if "rows" not in parsed or not isinstance(parsed["rows"], list):
        raise RuntimeError(f"Claude's response was missing the expected 'rows' list — got: {text[:500]}")
    return parsed
