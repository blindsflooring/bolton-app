# Blinds & Flooring Studio — Bolt-on (Phase 1 build)

**New developer? Read `CHANGELOG.md` first.** It explains what's been
built, in what order, and why — several entries are bug fixes that
changed real pricing numbers, and the reasoning matters as much as the
fix (some of these look like they could be "simplified" back to the
buggy version if you don't know the history). Also check
`BANKED-DECISIONS.md` before touching multi-company, auth, or supplier
price-catalog work — confirmed future direction that isn't built yet,
recorded so nothing built in the meantime accidentally conflicts with
it.

This is the first working slice of the app described in the full brief:
**Price Book + Quote Builder with live calculation.** No Xero, no PDF import,
no AI assistant yet — those are later phases (see brief §17).

## What's actually working right now

- Flooring price book: add products, tagged as **screed** (job-type multiplier
  applies: Smooth ×1, Over Tiles ×1.5, Removed Tiles ×2) or **material**
  (vinyl/laminate/oak — flat price regardless of job type; substrate prep is
  its own separate screed line item on the same quote). Confirmed with
  Burgert Aug 2026 after the Azura vinyl price list import surfaced the
  distinction.
- Blinds price book: add products, cost/margin calculated from your confirmed
  formula (book price less 45% trade discount, less 7.5% settlement discount;
  sell at book price ex VAT — verified this gives ~49.1% margin, matching
  your number exactly)
- Quote builder: add flooring (screed or material) and blinds line items,
  live pricing per line, running total
- **Pack quantity calculation** — if a flooring product has `m2_per_pack` set
  (from the supplier's price list), the app calculates packs needed for a
  given job, wastage included, rounded up. Feeds directly into the Purchase
  Order auto-build feature when that's built (§13 of the brief).
- **Screed / smoothing compound job cost** — bag allowance calculated per
  substrate type (Smooth 4m²/bag, Over Tiles 3m²/bag, Removed Tiles 2m²/bag —
  your confirmed numbers, default bag cost R235 for iTe LEVELiTe F10),
  shown as "X bags included" on the quote line. Removed Tiles automatically
  adds the confirmed R45/m² tile removal fee as its own visible charge to
  the client, on top of the smoothing compound price.
- **Vinyl material job cost** — glue and labour calculated on the actual
  job m² (not the wastage-inflated purchasing quantity), defaults pre-filled
  for Techem Tek 70/70 (R1,193.50/20L drum, 70m² coverage), fully editable
  per line.
- **"At a glance" margin check** — the quote total shows overall cost and
  overall margin across the whole job, with a ✓ or ⚠️ flag, visible to
  Owner/Admin only.
- **Glue costing — corrected to match how you actually buy it** (confirmed
  Aug 2026): you draw glue from stock rather than buying a fresh drum per
  job, so cost is no longer drum-rounded — it's the same clean per-m² rate
  as what's charged. `glue_units_needed` is kept purely as a reference
  figure (roughly how much of a drum a job represents), not used in any
  cost calculation. Applies to both vinyl material lines and the stairwell
  calculator.
- **Labour cost now reflects who actually does the work** (confirmed Aug
  2026): your own salaried staff don't create new job cost when they build
  a stairs job or lay flooring — you pay them regardless, so the labour
  charge to the client is treated as pure margin. Outside/subcontracted
  labour is treated as real pass-through cost, same as before. New
  "Labour source" toggle on both the vinyl material line and the stairwell
  calculator, defaulting to "Own staff". This is a big swing: the same
  10-stair job went from 9.0% margin (assumed outside labour) to 63.6%
  margin (own staff) — same price to the client either way, very different
  reality depending on who's actually building it.
- **R45/m² labour is now standard on ALL floors** (confirmed: "this is
  part of my pricing structure") — no longer an optional zero-default
  field, applies to both material and screed lines, and is now genuinely
  included in the client-facing price, not just cost-only.
- **Trim markup formula corrected**: `cost x (1 + VAT) x 1.5` (confirmed:
  "use trim book price plus vat then add 50%") — VAT added to trade cost
  first, then the 50% markup on top. Cost used for margin stays the raw
  ex-VAT figure. New `vat_pct` field on trim products, defaulting to 15%.
- **Fixed a real persistence bug**: glue/labour/stairwell breakdown fields
  (units needed, sell totals, cost totals) were only ever returned in the
  immediate response when a line was added — never saved. Reloading a
  quote showed "undefined" instead of the real numbers. All of these are
  now proper database columns and survive a reload correctly.
- **Delete buttons** — price book entries (flooring/blinds/trims) and
  quote lines can now be removed, both via the API and in the UI, with a
  confirmation prompt before anything's deleted.
- **Collapsible price book dashboard** — Flooring now organizes as
  Category (Vinyl/Laminate/SPC/Novilon/Carpet/Engineered Wood/Screed) →
  Supplier → Products, using native collapsible sections. Trims follow the
  same pattern (Category → Supplier → Products). Matches the "back office"
  structure you asked for — everything collapsed by default except what
  you're looking at.
- **Vinyl material markup — the missing lever for hitting turnkey targets**
  (confirmed Aug 2026): material sell price was previously just the flat
  Zone A rate with no way to price above it. New `sell_markup_multiplier`
  field (material only — screed keeps its own job-type multiplier). Solved
  a real gap: your R480/m² turnkey target for series 200 + smooth screed
  wasn't reachable at Zone A pricing (R284.05/m² for vinyl alone, material+
  glue+labour). Worked backward from your R330-340 (vinyl) / R130-140
  (screed) sub-targets, both confirmed to already include R45 labour:
  markup ×1.23 gets vinyl to R335.11/m², screed base R90 gets screed to
  R135.00/m², turnkey total R470.11/m² — landing right in your target
  range. Tested and verified end-to-end.
- **Screed bundled into the vinyl line automatically** (confirmed Aug 2026:
  "screed almost always gets added to a floor") — a "Include screed prep"
  checkbox on the vinyl/material line, checked by default. When adding a
  material line, if checked, a second call adds the matching screed line
  in the same click, using the same quantity and job type — no more adding
  two separate line items by hand for what's really one job. Verified:
  100m² vinyl+screed added in one click lands at R470.11/m² total, 29.0%
  overall margin, both lines visible and individually editable/deletable
  afterward.
- **Verified against your real 8-year series 200 calculator** (confirmed
  Aug 2026) — pulled the actual formulas from your spreadsheet, not just
  the displayed numbers, and found four real mismatches, all now fixed:
  1. **Screed multipliers are now per-product and editable**, not a fixed
     1.5x/2x — your real deZIGN S200 rates (130/160/250) are ~1.23x/1.92x,
     not a clean multiple. Verified exact match: R130.00/R160.00/R250.00
     for Smooth/Over Tiles/Removed Tiles.
  2. **Glue confirmed at R17.05/m²** (Techem drum-based) over the
     spreadsheet's older flat R20/m² — your call, more accurate now.
  3. **Box rounding confirmed**: round up to whole boxes, wastage-adjusted
     — already matched what was built, no change needed.
  4. **VAT architecture fixed app-wide**: the trim formula was baking VAT
     into `unit_price` (`cost × 1.15 × 1.5`), inconsistent with material/
     blinds/screed which were already ex-VAT throughout. Reverted trim to
     `cost × markup_multiplier` only, with VAT applied exactly once,
     later, at invoice time — closes the double-VAT risk flagged earlier.
     Verified: R55.55 cost → R83.32/lm (not R95.82).
- **Trim/skirting product type** — pine skirting (fixed sell price you set
  directly) and aluminium trims (cost × 1.5 markup, giving a consistent
  33.3% margin), priced per linear metre.
- **Stairwell calculator** — uses TWO different area bases, deliberately:
  vinyl is billed on tile-count (2 tiles/stair minimum, rounded up to whole
  boxes for cost, billed area = actual tiles used since offcuts can't be
  reused — confirmed example: 10 stairs = 20 tiles = 5.58m² on deZIGN
  series 200). Glue uses the raw geometric stair footprint instead (900mm
  wide tread × (300mm going + 200mm riser) = 0.45m²/stair, confirmed
  default, overridable per line) — confirmed 10 stairs = 4.5m² of glue
  coverage, genuinely smaller than the 5.58m² vinyl figure, since glue
  covers the real substrate, not tile offcuts. Stair nosing (default: S2525
  25×25mm Aluminium Equal Angle) scales by stairwell type: 900mm/R250/stair
  closed, 1400mm/R300/stair one side open, 1900mm/R350/stair both sides
  open. Labour is confirmed pass-through, deliberately absorbed into the
  overall stairwell margin rather than marked up on its own. **Real test
  result: a standalone 10-stair job comes out to -17.4% margin** — worth
  knowing before quoting stairs as their own job vs. bundled into a larger
  floor job that's already buying the glue and vinyl.
- **Trim wastage** — 8% buffer (your confirmed number) applied to trim/
  skirting cost when ordering, for offcuts and mitres. Affects cost only —
  you're still charged for actual length required on the client-facing price.
- **Real per-person login (confirmed Aug 2026)** — replaces the earlier
  self-reported "Viewing as" role dropdown, which let anyone claim to be
  Owner just by picking it from a `<select>`. Three real accounts
  (Burgert/owner, Ryno/sales, Madri/admin), PBKDF2-hashed passwords
  (stdlib `hashlib`, no bcrypt dependency), server-side sessions in the
  `app_user`/`usersession` tables backing an httponly cookie (24h fixed
  length). Every endpoint that used to accept a client-supplied `role`
  query param now derives it exclusively from the validated session via
  the `get_current_role` dependency in `main.py` — the frontend can no
  longer choose its own role.
- **Role-based visibility, enforced server-side** — logged in as Sales
  (Ryno), the API itself never sends back `unit_cost` or `margin_pct` for
  any line item. This isn't just hidden in the UI; a Sales-role API call
  physically doesn't receive that data. Sales also doesn't see the
  Business Overview, Business Settings, HR & Commission, or Supplier
  Price Book tiles in the UI (default split — Owner/Admin unaffected;
  adjust `SALES_HIDDEN_TILES` in shared.js if this needs changing).
- **Stairwell tread coverage** (confirmed Aug 2026): 3 tiles/stair
  (`TILES_PER_STAIR` in models.py) — tread width per stair = 3 planks x
  standard plank width, corrected from the earlier 2/stair figure.
- **Stairwell landings** — staircases with a turn/half-landing can have
  multiple landing platforms; the Quote Builder lets you add one row per
  landing, sums the total area, and bills it as a normal flooring
  material line (same vinyl product as the stairs, standard per-m² rate)
  — not part of the stairwell tile/glue formula.
- **Blinds measurement toggle** — per quote, controls whether width/drop
  show on the client-facing view. Full data is always kept internally
  regardless of the toggle.
- Flooring margin warning — flags if a discount pushes a flooring line's
  margin below 30%

## What's deliberately NOT built yet (by design, per the phased plan)

- Xero integration (Phase 2) — quotes stay local drafts for now
- PDF price list import (Phase 2)
- Receipt capture, Job Photos, Purchase Orders (Phase 3+)
- AI Assistant (later phase)
- Analytics Dashboard / breakeven tracker (later phase)
- Multi-tenant / selling to other companies (later phase) — see
  `BANKED-DECISIONS.md` for the master supplier price catalog design
  banked against this, plus a pointer to the editions/module-toggle
  work from an earlier strategy conversation

## Running it locally

**Backend:**
```bash
cd backend
pip install -r requirements.txt --break-system-packages
uvicorn main:app --reload --port 8000
```
This creates `bolton.db` (SQLite) automatically on first run. API docs at
`http://localhost:8000/docs`.

**Frontend:**
Open `frontend/index.html` directly in a browser, or serve it with any
static file server. It's currently pointed at `http://127.0.0.1:8020` in
the `API` constant near the top of the `<script>` block — **update this to
match whichever port you actually run the backend on**, or to your deployed
backend URL once it's hosted.

## Moving to Supabase (when you're ready for Phase 2)

`backend/supabase_schema.sql` has the Postgres schema matching the current
SQLModel models exactly. Run it in Supabase's SQL editor, then swap the
`DATABASE_URL` in `main.py` from the local SQLite string to your Supabase
Postgres connection string.

## A correction worth knowing about before Phase 2 (Xero)

Research turned up an important nuance: **Xero does not have a separate
scope for excluding bank transactions.** `accounting.transactions` covers
invoices, quotes, purchase orders, AND bank transactions together — there's
no way to request "invoicing but not banking" at the scope level.

So the "no banking access" rule from the brief needs to be enforced as a
**code-level rule, not a permissions toggle**: when the Xero client gets
built in Phase 2, it should only ever call the specific endpoints it needs
(Quotes, Contacts, Invoices, Items, Purchase Orders) and never call bank
transaction or bank feed endpoints, and never request the separate
`bankfeeds` scope at all. This is noted directly in `models.py` so it isn't
lost by the time that phase starts.

## Files in this build

```
backend/
  models.py            — data models (price book, quotes, line items)
  calculations.py       — the actual pricing formulas (your proprietary logic)
  main.py               — FastAPI app, all endpoints
  requirements.txt
  supabase_schema.sql   — Postgres schema for Phase 2 migration
frontend/
  index.html             — Price Book + Quote Builder UI (vanilla JS, mobile-friendly)
  styles.css              — extracted from index.html at v51 (Foundation Refactor Stage 1);
                            JS is next to split, per the Stage 2 handoff brief — not started yet
```
