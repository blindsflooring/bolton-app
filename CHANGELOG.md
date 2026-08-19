# Changelog — Blinds & Flooring Studio Bolt-on

This file exists so any future developer (including future-you) can see
what's been built, in what order, and — critically — *why* certain
decisions were made. Several entries below are bug fixes that materially
changed real numbers; understanding why the fix was made matters as much
as the fix itself, since "correcting" it back would reintroduce the bug.

**Practice going forward:** every version handed to Claude Code gets an
entry here first. If you're a developer picking this up cold, read this
top to bottom before touching `calculations.py` — most of the tricky
decisions in that file are explained by something that happened here.

---

## v1–v9 — Initial scaffold
- FastAPI + SQLModel backend, vanilla JS frontend, Supabase-ready schema
- Price Book (flooring, blinds) and Quote Builder tabs
- Core formulas: screed job-type multiplier, blinds trade/settlement
  discount margin, role-based visibility (Owner/Admin/Sales)
- Confirmed: Sales role never receives cost or margin data — enforced
  server-side, not just hidden in the UI

## v10 — Major feature batch
- Trim/skirting price book (pine skirting fixed-price, aluminium
  Supertrim markup-mode), stairwell calculator, delete buttons throughout,
  collapsible price book dashboard (Category → Supplier → Products)
- **Security fix:** a bug briefly let Sales role see `glue_cost_total` —
  caught and fixed before shipping
- Brand visual refresh (logo, Poppins/Figtree, teal/coral/navy)

## v13 — Vinyl markup lever added
- Material sell price was previously just the flat Zone A rate with no
  way to price above it — added `sell_markup_multiplier`

## v14 — Xero banking-scope correction
- Confirmed Xero has no separate "exclude bank transactions" scope —
  `accounting.transactions` covers invoicing and banking together.
  Banking exclusion is enforced as a code-level rule (never call bank
  endpoints), not a permissions toggle, since Xero's model doesn't
  support the toggle approach at all

## v15–v16 — Turnkey pricing target work
- Solved a real gap: R480/m² turnkey target for series 200 + smooth
  screed wasn't reachable at flat Zone A pricing — worked backward from
  confirmed sub-targets to find the right markup
- Screed bundled into the vinyl line automatically ("almost every floor
  needs this") — one click adds both instead of two manual steps

## v17 — Verified against the real 8-year series 200 spreadsheet
Pulled actual formulas (not displayed values) from Burgert's real
calculator and found four real mismatches:
1. Screed multipliers made per-product and editable — real rates
   (130/160/250) are NOT a clean 1.5x/2x
2. Glue confirmed at R17.05/m² (Techem), not the spreadsheet's older R20
3. Box rounding confirmed already correct
4. **VAT architecture bug found and fixed app-wide** — trim was baking
   VAT into `unit_price` (double-counting risk once Xero connects).
   Reverted to ex-VAT everywhere internally, VAT applied exactly once,
   at invoice time

## v19 — Box-by-box formula introduced (⚠️ shipped with a bug, see v20)
- Rewrote material line pricing to match the real spreadsheet's
  box-by-box buildup (boxes needed → net cost/box → +glue → ×markup →
  +labour) instead of a flat base×markup

## v20 — Bug fix: box cost formula
- **The v19 rewrite had a real bug**: `base_cost_ex_vat` is documented
  and used everywhere else as a per-m² rate, but the new formula treated
  it as if it were already a per-box price — multiplying box count
  directly against a per-m² figure with no conversion. Under-charged by
  more than half. Caught by Claude Code independently re-deriving the
  formula, not by the original test (which had "passed" only because it
  was fed the per-box number directly into a per-m² field, masking the
  bug). Fixed: multiply by `m2_per_pack` to convert correctly

## v21 — Frontend rebuilt to match the confirmed calculator
- The Quote Builder had drifted from the tested, approved calculator
  design into a generic dropdown form. Rebuilt from scratch as a literal
  port of the calculator's layout (two-column, live-updating, per-m²
  check box, Margin/GP panel) wired to real price book data

## v22 — Landing page
- 8-tile dashboard: Business Overview, Order Index, Flooring Quotes,
  Blinds, Clients, Supplier Prices, Supplier Uploads, Print Invoice.
  Only tiles with real backend functionality are clickable — the rest
  show "Coming soon" rather than faking behaviour

## v23–v27 — UI and workflow fixes
- Removed redundant top-level Price Book/Quote Builder nav tabs (now
  only reachable via their tile)
- Quote-level discount (applied ex-VAT, before VAT)
- Save Quote (genuinely updates client/owner/branch) and Print/PDF
  (builds a real document, not a screenshot of the input form)
- Amending an existing quote now pre-fills its real data and locks
  "Start Quote" to prevent accidental duplicates — with a "New Quote
  (different client)" escape hatch added after that lock caused its own
  confusion
- Markup adjustments in the quote form now genuinely commit to the saved
  line via a `markup_override` parameter, instead of only affecting the
  live preview

## v28–v29 — Print output fixes
- Bag allowance + R350/bag overage note added to the printed quote
- **Print CSS bug found and fixed:** the print container was nested
  inside `<main>`, so hiding `<main>` for printing hid the print content
  along with it — resulting in a blank printed page regardless of what
  data was populated into it

## v30 — ⚠️ Critical bug fix: screed cost double-counting
Screed's cost formula was copying vinyl's "wholesale price minus trade
discount" logic, but `base_cost_ex_vat` for screed **is the confirmed
sell rate** (e.g. R130/m² smooth), not a wholesale price needing a
discount applied. The bug charged the *entire sell price* as material
cost, then added the real bag cost **on top of that** — a real example
(ITE F10, R130/m² smooth, 100m², 25 bags) showed a fabricated **-53.2%
margin** instead of the correct **+54.8%**. Every screed line's margin
shown before this fix was wrong. Fixed: screed's real cost is the
compound bags only, nothing else.

## v31 — Reconfirmed print fix landed correctly
No code change — repackaged and explicitly verified the v29 fix was
actually present in the shipped file, after a suspected version-sync
issue with Claude Code.

## v32 — ⚠️ Critical bug fix: stairwell vinyl missing markup entirely
Found proactively (before it was reported) while double-checking
stairwell for the same bug pattern as v30. `sell_markup_multiplier` was
never referenced anywhere in `calculate_stairwell_line()` — vinyl on
every stair job was selling at the raw Zone A rate with **zero markup
applied**, a straight 30% underprice (at the confirmed ×1.3 markup) on
every stair quote since the feature was built. Fixed: same markup logic
as regular flooring lines now applies.

## v33 — Skirting/Trim category split
- "Trim / Skirting" was one combined dropdown option showing all trim
  products mixed together. Split into two real categories — Skirting
  (pine skirting + quarter round) and Trim (aluminium Supertrim range) —
  each with its own filtered product list
- Found and fixed two related bugs while doing this: the length input
  field only showed for the old "trim" value (not the new "skirting"
  one), and the actual line-submission logic would have silently routed
  a skirting selection down the wrong code path (toward stairwell fields)

## v34 — Client CRM + Order Index search
- Real `Client` records (name, phone, email, address, preferred branch)
  replace the plain-text client name as the source of truth — quotes
  keep a `client_name` snapshot too, so walk-in/one-off quotes without a
  CRM entry still work
- Live client search in the New Quote form, client detail view with full
  order history, Order Index search by client name or quote number

## v35 — This changelog
- `CHANGELOG.md` added to the project — practice going forward is an
  entry here with every version handed to Claude Code

## v36–v38 merge, plus direct fixes and additions by Claude Code
Applied on top of the v35 baseline this file already covered — v36
(Business Overview dashboard), v37–v38 (HR & Commission backend), merged
alongside work already in progress in the live app this session (screed
cost fix, stairwell markup fix, tile-removal-fee decoupling, Client CRM,
Order Index, Business Details). All confirmed intact after the merge.

- **Two real security bugs found and fixed** while reviewing the new
  document upload endpoint: (1) `file.filename` was used unsanitized in
  the storage path — a crafted filename via a direct API call could have
  written outside the uploads folder (path traversal). Fixed with
  `os.path.basename()`. (2) `download_document` had no role check at
  all — an `owner_only` document was hidden from Sales in the list view
  but still directly downloadable if the doc ID was known/guessed.
  Fixed to match the list endpoint's restriction.
- **Business Details is now Owner-only to edit** — confirmed requirement:
  Admin/Sales can still view (it prints on every quote regardless of who
  prints it), but only Owner can change it. Enforced both server-side
  (403 for any other role) and in the UI (fields disabled, Save button
  hidden for non-owner roles) — same pattern as cost/margin stripping
  elsewhere in this app.

## HR Frontend UI (Phase A) — brief-scoped build
Built the frontend for the HR & Commission backend from v37–v38, per a
dedicated UI brief. Strictly backend-only until now; this is UI on top of
already-tested logic, no new business rules invented here. Explicitly
out of scope per the brief and NOT built: real authentication, the
Builder-Rep Portal, a commission rate-card editor, advanced reporting.

- New "HR & Commission" landing tile with its own sub-navigation
  (Employees / Hours / Leave / Documents / Commission), reusing the
  existing `nav button` styling rather than inventing new CSS.
- **Employees**: list + create/edit form matching the brief's field list
  exactly, with one necessary addition beyond the brief's list — a
  "Commission key" field (maps to `Employee.sales_owner_key`). Without
  it the Commission screen has no way to look up an employee's
  statement at all, since the backend's `/commission/statement/{key}`
  endpoint is keyed off that field, not the employee ID. `id_number`
  was left off the form (in the model, but not requested in the brief —
  strict scope).
- **Hours**: fast capture form (employee, date, hours, type, optional
  quote reference, notes) plus a monthly summary with per-type totals
  and a print view, per the brief's "accountant-ready" requirement.
- **Leave**: balances view, a cycle-setup form (since the backend has no
  auto-provisioning — someone has to create the first balance per
  employee per leave type), and the request → approve/reject workflow.
  Days requested auto-calculates from the date range (inclusive) but
  stays editable, since real leave doesn't always match calendar days
  cleanly. Sick note upload is wired directly into the request form when
  type=Sick — it uploads to Documents first, then links the resulting
  document ID to the leave request in the same submit action, matching
  the model's `sick_note_document_id` field.
- **Documents**: upload (multipart, confirmed against the backend
  directly before writing the JS — metadata fields are query params, not
  form fields, since the endpoint mixes `UploadFile` with plain-typed
  params rather than declaring them as `Form(...)`), list with
  owner_only badge, download, delete.
- **Commission**: read-only statement view exactly as scoped — employee
  + year/month picker, shows the backend's calculated turnover/GP/rate/
  commission-due (pure_sales) or per-category breakdown with a clear
  "no rate configured" flag (builder_rep), plus a print view. No rate
  editing UI, per the brief.
- **Role visibility**: no new permission rules invented — every screen
  passes `role=currentRole()` through to the endpoints that already
  enforce something (employee notes/id_number stripped for Sales,
  owner_only documents hidden for Sales), and the Notes field is hidden
  entirely from the employee form when the current role is Sales.
- Verified every write path against the live backend directly (not just
  read the code) before considering this done: employee create/edit with
  date fields, hours logging + monthly summary math, a full leave
  balance → request → approve cycle (confirmed 21→16 days remaining),
  document upload/list, and a commission statement against a real paid
  test quote (R1,073.78 GP → 8% tier → R85.90 commission, exact match to
  the formula). All test data cleaned up afterward, including orphaned
  leave records left behind by a delete-employee call (there's no
  cascade delete for leave records yet — worth adding if this becomes a
  real pain point, not urgent for Phase A).

## v39–v44 merge, plus direct fixes and additions by Claude Code
A zip (`bolton-phase1-v44.zip`) built outside this session was handed over
covering v39–v44 of the parallel numbered-zip workflow this project uses
alongside live Claude Code sessions — same pattern as the v36–v38 merge
above. Diffed carefully against the live app rather than overwritten
wholesale, since the zip's own lineage had silently dropped real,
already-shipped work from this side: **Business Settings** (whole feature
missing), the **path-traversal and owner_only-download security fixes**,
`GET /clients/{id}`, `DELETE /quotes/{id}`, and the **explicit
tile-removal-fee toggle + incl-VAT correction** (the zip had reverted to
auto-applying the fee on Removed Tiles and treating R45 as ex-VAT — the
exact bug already fixed and documented earlier in this file). All of that
was preserved through the merge; nothing on the live side was regressed.

**From the zip (v40–v44):**
- **v40 — Real Aspen Flooring range + delivery fee**: 35 products across
  5 ranges loaded from Burgert's actual Feb 2025 wholesale list. New
  `delivery_fee_per_m2` on `FlooringProduct` (Aspen charges R15/m²
  delivery, no trade discount to offset it) — bundled into the same
  pre-markup subtotal as glue, so it's recovered AND earns normal margin.
  New `POST /price-book/flooring/bulk-import` endpoint (all-or-nothing)
  plus a JSON upload in the Price Book UI, since loading a full supplier
  range one product at a time doesn't scale.
- **v41 — Colour as a real structured field**: new `colour` field on
  `FlooringProduct`, separate from `product_name` (e.g. "Aspen Premium
  Range 2.5mm" + "GD Bleached Oak" as two fields, not baked into one
  string). Denormalized onto `QuoteLineItem` too as a locked snapshot —
  editing or discontinuing a price book colour after quoting never
  changes what an already-saved quote line shows.
- **v42 — Colour change audit trail**: `original_colour` on
  `QuoteLineItem` (set once, never touched again) plus a new
  `ColourChangeLog` table — every substitution (e.g. out of stock) is its
  own permanent row (old/new colour, reason, who, when), not just the
  latest value overwriting the last. New `PUT .../colour` and `GET
  .../colour-history` endpoints; the printed/PDF quote only ever shows
  the current colour, never the change history.
- **v43 — Azura vinyl colours loaded**: 40 products across 5 deZIGN
  dry-back series (120/200/250/XL/Herringbone) from the June 2026 Cape
  price list, Zone A pricing, same colour/audit-trail system as Aspen.
- **v44 — Version badge**: a small teal pill next to "Bolt-on" showing
  the running version at a glance — the recurring problem this solves
  (an old cached build looking like a fix "didn't work") is real and
  already being felt in this project. Kept the zip's styling; superseded
  the plainer text-only badge built independently earlier this session.

**Data actually imported into the live `bolton.db` this session** (the
zip only ships the JSON files — importing them is a separate, real step):
`aspen_products_import.json` (35 products) and `azura_vinyl_import.json`
(40 products) both loaded via the new bulk-import endpoint — confirmed
**80 total flooring products** in the live price book afterward. The 4
old generic Azura entries (no colour, e.g. plain "deZIGN series 200")
were **deliberately left in place, not deleted** as the zip's own v43
notes suggested — checked the live database first and found one of them
(id 2) is actively referenced by Burgert's real draft quote #1 (both a
flooring line and a stairwell line). Deleting it would have orphaned
that quote's product reference. Flagging this for a manual decision
later rather than guessing.

**Database migration** (existing `bolton.db` predates the new columns —
SQLModel's `create_all` only creates missing tables, not missing columns
on existing ones): backend stopped, `bolton.db` backed up
(`bolton.db.bak-pre-v44`), `colour`/`delivery_fee_per_m2` added to
`flooringproduct` and `colour`/`original_colour`/`delivery_fee_total`
added to `quotelineitem` via manual `ALTER TABLE`, `colourchangelog`
table created fresh on restart. Verified after restart: existing clients/
quotes/price book data all intact, no data loss.

**Preserved from the live app (not in the zip, ported across):**
- **Print Invoice landing tile**, actually built this session (was
  `ready: false` in both the live app and the zip) — pick any quote,
  print it as a Tax Invoice from the same real line-item data the
  existing "Print / PDF" button uses (now a shared `renderPrintDoc
  (quoteId, docType)` function), no separate invoice data entry, no new
  backend endpoint needed. No real invoice numbering/dating exists yet
  (no Xero — Phase 2) — invoice number is `INV-` + the quote's own ID.
- **"Material only" toggle on the vinyl line** (confirmed: "a client to
  just buy the vinyl alone... only the box price plus markup plus vat")
  — checkbox on the Floor Job vinyl card; when checked, glue and labour
  inputs are disabled and zeroed (prior values remembered for toggling
  back off), forced to 0 in both the live preview and the actual save
  regardless of field state. Verified end-to-end: 100m² series 120 (30%
  trade discount, 4.94m²/box, ×1.3 markup) → 22 boxes × R691.60 net/box ×
  1.3 = **R19,779.76**, glue and labour both confirmed R0.
- **Business Details** (whole feature — see above).
- Colour snapshot now shown on the printed quote/invoice too (bold,
  teal, under the product name) — ported the zip's print-doc addition
  into the richer letterhead-aware print function rather than the zip's
  plainer one, so both features are present together.
- Order Index/Clients "not loading" (reported earlier this session) was
  the backend simply not running, not a code bug — noted here since it
  falls in the same session's work, not a separate version.

## v45–v46 — Range → Colour selection, Net-row cleanup, back-to-top
Another zip (`bolton-phase1-v46.zip`, no v45 zip shipped separately —
folded into this one) handed over from the same parallel workflow as
v39–v44. Diffed against the live app the same way: **frontend-only**
this round — `models.py`/`main.py`/`calculations.py` diffs showed no new
content versus the merged v44 state, just the same already-known gap
(Business Settings, security fixes, tile-removal toggle) this zip
lineage still doesn't have. Nothing new to port on the backend; no
database migration needed.

- **v45 — Print quote "Net" row hidden when there's no discount**:
  Subtotal and Net showed the identical figure with no discount applied
  — pure noise. Now the Net row only appears alongside the Discount row,
  inside the same conditional.
- **v46 — Range → Colour two-step selection**: the Vinyl card's single
  "Product" dropdown (a flat list of every range+colour combination —
  79 entries once real colour data loaded, genuinely hard to scan) is
  now a **Range** select feeding a **Colour** select, colour options
  populated from whichever range is chosen. `fj_vinyl_product` survives
  as a hidden input underneath, still what actually gets submitted —
  `onVinylProductChange()` (autofill of price/wastage/markup fields) is
  unchanged, just now triggered via the colour step instead of directly.
- **Flooring Quotes drill-down regrouped to match**: one row per range
  (e.g. "Aspen Herringbone Range 2mm — 6 colours") instead of one row
  per individual colour. Clicking a range jumps to Quote Builder with
  the range pre-selected and lets you pick the colour there — colour
  selection lives in exactly one place now (Quote Builder), not
  duplicated into the drill-down too. `startQuoteWithVinylProduct(id)` /
  `pendingVinylProduct` renamed throughout to `startQuoteWithVinylRange
  (range)` / `pendingVinylRange` to match.
- **New floating "back to top" button** — appears past ~300px of scroll,
  works globally, smooth-scrolls to top. Self-contained addition at the
  end of the file, no interaction with anything else.
- Verified against the live 80-product price book (confirmed after the
  v44 merge's real Aspen/Azura import): the new grouping produces
  correct range/colour counts per supplier (e.g. Aspen Project Range
  2.5mm → 8 colours, Azura deZIGN series 200 → 15 colours) with no
  errors.
- Version badge bumped to **v46**.

## v47–v49 — Race-condition fix, colour-add fix, Order Index as real job tracking
Another zip (`bolton-phase1-v49.zip`, no separate v47/v48 zips — folded
into this one). Same diff-and-merge discipline as before: backend/
calculations diffs showed only the same already-known gap (Business
Settings, security fixes, tile-removal toggle) — nothing new lost this
round, but did catch and fix a **new regression of my own** while
merging: the v44 merge had silently dropped the Order Index's "New
Client → Start Quote" panel and per-row Delete button when I swapped in
the zip's base file — neither was in the zip's lineage, and I didn't
verify they'd carried over at the time. Both restored here, alongside
the v49 rebuild.

- **v47 — Fixed: all-zero results after the Range/Colour rebuild** (real
  bug in what I merged last round, not introduced by this zip — same
  race condition would have surfaced in the v46 merge too). Root cause:
  `toggleLineFields()` called `populateFloorProductDropdowns()` against
  the cached `flooringProducts` array without confirming it had loaded
  yet; the two-step Range → Colour chain made this far more likely to
  hit than the old flat dropdown. Fixed: `toggleLineFields()` is now
  `async` and explicitly `await`s `loadFlooring()` first; both call
  sites that depend on it (`startQuoteWithVinylRange`, `createQuote`)
  now `await` it too, instead of firing and moving on. Added a visible
  "No vinyl products in price book" fallback for a genuinely empty range
  list, instead of a silent blank dropdown.
- **v48 — Fixed: couldn't add a colour to a line that had none.** Two
  bugs blocking the same workflow: backend `change_line_colour`
  explicitly 400'd when the line's colour was empty (written assuming
  "change" only ever means swapping an existing colour); frontend's
  "Change colour" link was itself hidden whenever there was no colour to
  click it from. Fixed both — backend now allows it (logged as changing
  from "(none)"), frontend now shows a flagged "No colour set — Add
  colour" affordance on colourless flooring lines. Verified end-to-end:
  added a no-colour product to a quote, confirmed the line truly had
  none, called the same endpoint that used to 400, confirmed success and
  the colour history correctly showing `(none) → Borough Oak`.
- **v49 — Order Index rebuilt as real job tracking, per the brief.** New
  `Quote` fields (`site_address`, `installation_date`,
  `invoice_sent_date`, `deposit_paid_date`, `deposit_payment_method`,
  `final_payment_date`, `final_payment_method`) plus a new
  `PaymentFollowUp` table (its own append-only log, since a quote can
  need more than one reminder over time — nothing overwrites a prior
  entry). New "Order Details" card on the Quote Builder page is where
  these actually get entered, plus the follow-up log UI (list + add
  form). Order Index now shows client/address/colour-coded status
  (Not Invoiced → Awaiting Deposit → Overdue past 7 days, a reasonable
  default not a confirmed rule → Deposit Paid/Balance Due → Paid in
  Full)/install date/deposit+final payment amounts and dates/invoice
  sent date, all at a glance — restructured columns replace the old
  Sales Owner/Branch/raw-status/created-date table, per the brief's
  explicit ask, not an accidental removal. `GET /quotes` now computes
  real per-quote totals (deposit/balance amounts) using the exact same
  VAT/discount math as the single-quote endpoint, not a second copy of
  it — it previously only returned bare quote records.
- **Real bug found and fixed while testing this round's merge** (not in
  either zip): `DELETE /quotes/{id}` never cascaded to
  `PaymentFollowUp` or `ColourChangeLog` rows, only to `QuoteLineItem`s
  — deleting a quote orphaned its follow-up/colour history, and because
  SQLite reuses a deleted row's rowid, a brand-new unrelated quote could
  resurface a prior quote's "deleted" follow-up log under its own id.
  Caught by an actual delete-and-recreate test cycle, not inspection.
  Fixed: both now included in the cascade. Orphaned rows already sitting
  in the live database from before this fix were identified and cleaned
  up directly.
- Back-to-top button moved to the left side (confirmed preference).
- Full lifecycle verified against the live backend: quote → line added
  → order details set (address, install date, invoice sent 16 days ago)
  → confirmed computed status correctly reads "Overdue" → follow-up
  logged → `GET /quotes` totals matched hand-calculated deposit/balance
  exactly. Test data cleaned up afterward. Database migrated in place
  (`bolton.db.bak-pre-v49` backup taken first) — existing data intact.
- Version badge bumped to **v49**.

## Trim markup bulk-update endpoint — direct fix by Claude Code
Requested directly (not from a zip): a way to bulk-adjust the markup
multiplier across the whole aluminium/markup-mode trim range in one
call, instead of editing 11 products one at a time.

- **New `PUT /price-book/trims/bulk-update-markup?new_markup=X`** —
  only touches `TrimProduct` rows with `pricing_mode == "markup"`
  (the aluminium Supertrim range, stair nosing, reducers, carpet strip);
  fixed-price pine skirting doesn't use `markup_multiplier` at all, so
  it's deliberately left untouched rather than silently rewritten.
- **Had to be registered ABOVE the existing `PUT
  /price-book/trims/{product_id}` route**, not below — confirmed by
  testing the exact request the "wrong order" would produce: FastAPI/
  Starlette matches routes in registration order, and `{product_id}: int`
  isn't actually type-checked at the routing layer (only at parameter-
  validation time, after a route is already selected), so the generic
  route would otherwise match first and 422 trying to parse
  "bulk-update-markup" as an integer, never reaching this endpoint.
- Verified against the live price book: all 11 markup-mode products
  moved 1.5 → 1.725 (50% → 72.5% markup) in one call, all 4 fixed-price
  skirting products confirmed unchanged at 1.5 (their default, unused).

## v50 — Large batch: labels, prefill, misc line, trim margin
Zip (`bolton-phase1-v50.zip`) handed over shortly after the trim
bulk-update endpoint above was built directly. Turns out the zip
lineage had independently built the exact same endpoint, same bug,
same fix — good cross-check that the direct fix was correct. Adopted
the zip's slightly richer version (adds an optional `category` filter)
over my own. Same diff discipline as every round: backend diff showed
only the same already-known gap (Business Settings, security fixes,
tile-removal toggle) — nothing new lost, nothing new to preserve beyond
what's already been carried forward.

- **"Box list price" was mislabeled** — the input showed the per-m²
  rate (e.g. R222) labeled as if it were a box price, which is exactly
  why Aspen's real per-m² numbers (e.g. R50/box) looked "totally wrong"
  at a glance — R50/box is absurd for flooring, but it was never meant
  to be read that way. The underlying math was always correct. Relabeled
  to "Price per m²", and added an explicit "Box list price" reference
  line so the real per-box number (e.g. R742.59) is always visible too.
- **New client → new quote now pre-fills site address**, not just the
  client's name — `POST /quotes` now carries `client.address` through
  to `Quote.site_address` when `client_id` is given (real gap: the
  client record already had the field, it just wasn't being used).
  Verified directly: new client with a saved address → new quote →
  `site_address` correctly populated without re-typing.
- **New Miscellaneous line type** — freeform description + amount ex
  VAT + optional cost (defaults to 0, pure margin), for anything that
  doesn't fit an existing category: extra weekend labour, a one-off
  request, anything not in the price book. New `POST
  /quotes/{id}/lines/misc` endpoint, new `misc` option in the line
  category dropdown with its own form fields, its own badge colour.
  Verified: R750 weekend-labour line with no cost entered correctly
  shows 100% margin. Also fixed a small rough edge the zip itself left
  in: misc lines were falling through the Quote Lines table's detail
  column into blinds' "measurements hidden" copy, which doesn't apply
  to them — now shows a plain "—" instead.
- **Aluminium trim margin raised to ×1.725** (50% → 72.5%, same final
  price as "cost × VAT × 1.5" but as a single ex-VAT multiplier, not by
  baking VAT in again — the exact double-VAT bug fixed back in v17).
  Existing products updated via the bulk-update endpoint above, not a
  model default change (new products still default to 1.5 until
  explicitly set — confirmed this matches the zip's own behaviour too).
- **Home nav button now genuinely resets to the tile screen** instead
  of reopening whichever sub-view (Orders, HR, etc.) was last open —
  `landingView` is now reset to `'tiles'` before re-rendering.
- Back-to-top-on-the-left and adjustable stairwell width: both already
  correct from the v49 merge / an earlier round respectively — verified
  intact, nothing to change.
- Version badge bumped to **v50**.

## v51–v52 — Foundation refactor Stage 1 (CSS extraction) + three alignment fixes
Merged from a two-zip handoff plus a Stage 2 planning brief.
Two zips this round (`bolton-phase1-v51.zip`, `bolton-phase1-v52.zip`),
plus `bolton-brief-stage2-handoff.md` — a catch-up brief written against
the zip lineage's own state, meant to set up the next stage (splitting
`index.html`'s JS into responsibility-based files). Same diff-and-merge
discipline as every round: backend diffs showed the same already-known
gap (Business Settings, tile-removal-fee VAT correction + explicit
toggle, `material_only` toggle, the two security fixes, `GET
/clients/{id}`, the `DELETE /quotes` cascade fix, the Order Index "New
Client → Start Quote" panel + per-row Delete, Print Invoice) — none of
it in the zip lineage, all of it preserved here exactly as before.
Stage 2 (the JS split) is **not started** — this round only applies
v52, per the brief's own instruction ("apply v52 first, then this is
your next-step context").

- **v51 — CSS genuinely extracted**: all 163 lines of the inline
  `<style>` block moved into a new `frontend/styles.css`, loaded via
  `<link rel="stylesheet">`. Confirmed byte-identical to what was
  inline before extracting (diffed directly, not assumed). `index.html`
  drops from 2,528 to 2,374 lines.
- **v52 — three real gaps, found by checking the brief's claims against
  the actual code instead of trusting them**:
  1. Vinyl no longer shows a Job Type dropdown — material pricing is
     flat regardless of job type (only screed's multiplier genuinely
     uses it), so the dropdown never did anything for vinyl. Replaced
     with a hidden fixed `"smooth"` field since the submit endpoint
     still expects the parameter.
  2. Sales role can no longer edit list price, trade discount, m²/box,
     or markup on the calculator — `applyRoleVisibility()` now disables
     `fj_box_price` / `fj_trade_discount` / `fj_m2_per_box` / `fj_markup`
     / `fj_screed_rate` for Sales, same central place cost/margin
     visibility was already controlled from. Sales still sees the
     resulting price.
  3. Aluminium trim margin corrected to exactly 45%: the ×1.725 set in
     v50 actually gave 42.0%, not ~45% — and the *first* correction
     attempt (×1.818) was also wrong, since it missed that the real
     margin calculation already nets out the 8% wastage cost
     (`calculations.py`'s `calculate_trim_line` has always included
     this — genuinely a markup-value bug, not a code bug). Solved
     properly for the multiplier that hits 45% *after* wastage: **×1.9636**.
     Verified live: a 10lm trim line came back with `margin_pct: 0.45`
     exactly, not just close.
- **Bulk-update run against the live price book, not just the default**:
  `PUT /price-book/trims/bulk-update-markup?new_markup=1.9636` — all 11
  markup-mode products moved 1.725 → 1.9636 (the same endpoint built
  directly, then adopted into the v50 zip). The 4 fixed-price pine
  skirting products confirmed still untouched at 1.5 (unused for their
  pricing mode).
- **Real gap found and fixed while merging** (not in either zip):
  `startQuoteForClient()` already had a third `preferredBranch`
  parameter being *passed* at one call site (the "New Client → Start
  Quote" flow) but the function only ever declared two — the branch was
  silently dropped, never applied to `q_branch`. Site address already
  pre-fills correctly (via `client_id` server-side, since v50), but
  branch never did. Fixed: the function now applies it when given, and
  the Client Detail page's "+ New Quote" button now passes the client's
  `preferred_branch` too, not just id/name.
- **Real bug caught in the v52 zip's own `main.py`, not carried over**:
  a duplicated, unreachable copy of the bulk-update loop was left sitting
  after `delete_trim`'s `return` statement — dead code from what looks
  like a bad copy-paste on their end. Harmless (unreachable), but not
  something to propagate; not present in the merged file since this was
  a targeted diff-and-apply merge, not a wholesale file swap.
- **`backend/supabase_schema.sql` (Phase 2 reference schema) was stale**
  — a real, separate gap, unrelated to this round's zips: it never
  picked up `colour`/`original_colour` on quote line items, `colour` on
  flooring products, the `colour_change_log` / `payment_follow_up`
  tables, or the Order Index's Quote date/payment fields, even though
  `models.py` has carried all of these since v48–v49. This file isn't
  executed against the live SQLite app (SQLModel builds the real schema
  from `models.py` directly), so it never caused a live bug — but it
  would have handed Phase 2's Postgres migration an incomplete schema.
  Caught while reconciling the zip's own (differently-stale, missing
  `business_settings`) version of this file against ours. Fixed to match
  `models.py`, `business_settings` kept. Also fixed a real FK typo the
  zip's version had introduced — `quotelineitem(id)` instead of this
  file's actual `quote_line_item(id)` table name — while adding it, not
  copied in.
- **A CSS verification lesson (carried over from v51, still true)**: an
  automated "every class used in HTML has a matching CSS rule" check
  flags `blinds-field`, `cost-col`, `misc-field`, `per-m2-check`,
  `stairwell-field`, `tab-btn`, `trim-field` — all confirmed, individually,
  to be either pure JS show/hide toggle markers (styling comes from the
  base `.field` class) or styled via element selectors rather than a
  class selector. Nothing actually missing.
- Verified live end-to-end: fresh quote → deZIGN series 200, 100m²,
  smooth, matching the frontend's real defaults (30% trade discount,
  30% markup, R17.05/m² glue, R45/m² labour) → **`line_total` came back
  R29,016.48 ex VAT**, the brief's confirmed known-good figure, exact
  match. Sales-role fetch of the same quote confirmed `unit_cost` and
  `margin_pct` still physically absent from the response. Test quote
  and its lines deleted afterward via the existing cascade-safe delete.
- No backend `.py` files needed functional changes this round — v51
  touched no backend code by design (pure CSS move), and everything
  v52's zip changed in `main.py`/`models.py`/`calculations.py` was a
  reversion of fixes already merged in from earlier rounds, not
  adopted. `frontend/index.html.pre-v52-merge` kept as a pre-merge
  backup, same convention as `.pre-v44-merge`.
- Version badge bumped to **v52**.

## v53 — Foundation refactor, Stage 2 begins: shared.js extracted
First JS extraction of Stage 2 — the foundation file everything else
depends on. Done directly against this repo's own (feature-richer)
`index.html`, not by importing a zip — no `bolton-phase1-v53.zip` was
handed over this round, only v54's, which already contained its own
independent `shared.js` (same first step, done on the zip's own thinner
lineage). Used that as a structural reference for scope, not as
something to copy in — this repo carries real features the zip lineage
still doesn't (Business Details/Print Invoice tiles, the tile-removal
toggle, `material_only`, the security fixes, etc.), so a wholesale copy
would have silently dropped all of it, same risk as every prior merge.
`index.html` down to 2,327 lines; `shared.js` (81 lines) now holds the
genuinely cross-feature code.

- **Extracted to `shared.js`**: the `API` constant, `currentRole()`,
  `applyRoleVisibility()` (including the Sales price-locking added in
  v52), `R()` money formatting, `dateOrDash()`, a new `triggerPrint()`
  helper, `LANDING_TILES` (this repo's real 10-tile version — Print
  Invoice and Business Details included, both still `ready: true`
  here), and the cross-feature state variables (`currentQuoteId`, the
  three price book caches, `landingView`, and the "pending" handoff
  variables used when jumping from a landing tile into Quote Builder or
  Clients).
- **Deliberately left in `index.html` for now**: feature-specific state
  (`hrEmployeesCache`, `currentClientDetailId`, `hrView`,
  `currentEmployeeDetailId`, `qClientSearchTimeout`, `CATEGORY_LABELS`)
  — these belong with their own feature file when THAT split happens in
  a later step, not in the shared foundation.
- **Two real, safe duplications closed**, found independently in this
  file (not just assumed present because the zip's v53 mentioned them —
  checked and confirmed each one here first):
  - The exact same money-formatting formula existed identically in
    three places (the global `R()`, plus separately redefined inside
    both `renderOrderIndex` and `renderBusinessOverview`). Consolidated
    to the one definition in `shared.js`; the two local copies now
    alias to it (`const money = R;`) rather than being deleted outright
    — keeps every existing call site working unchanged. Also picked up
    the `|| 0` null-safety `R()` was missing (the local copies had it,
    the original global one didn't) — safe, behaviour-preserving for
    every valid numeric input, only changes a previously-crashing
    null/undefined case to showing R0.00.
  - `renderOrderIndex` also locally redefined `dateOrDash` (only used
    in that one function, so not a triple-duplication like `R()`, but
    the same genuinely cross-feature shape, and worth having centrally
    before Order Index becomes its own file). Consolidated to
    `shared.js`, local copy removed.
  - The "write content into printArea, then trigger the browser print
    dialog" pattern was repeated identically three times across
    `renderPrintDoc` (shared by the Quote Builder's Print button and
    the Print Invoice tile), `printHoursSummary`, and
    `printCommissionStatement`. Consolidated the shared mechanics into
    `triggerPrint(html)`; each caller still builds its own document
    content, since that part is genuinely different per document type.
- **Verified thoroughly before considering this done**, per the
  refactor brief's rules: real Node syntax check on `shared.js` and the
  remaining inline script independently, plus a concatenated check
  approximating what a real browser enforces across separate `<script>`
  tags sharing one top-level lexical scope (would throw a real
  "already declared" `SyntaxError` on any redeclared `let`/`const`  —
  confirmed none); every `getElementById` reference across both files
  still resolves in the HTML (219, same count as before the split —
  nothing lost); every inline `onclick`/`onchange`/`oninput` handler
  call resolves to a real declared function; no duplicate top-level
  function names anywhere in `index.html`; a live backend test
  reproducing the exact confirmed series 200 number (R29,016.48) to
  confirm none of this file movement disturbed anything that actually
  calculates a price.
- Next: continue Stage 2 with the remaining feature files
  (`price-book.js`, `clients.js`, `order-index.js`, `hr.js`, then
  `quote-builder.js` last, since it's the biggest and highest-risk).

## v54 — Business Settings: one source of truth for business-wide values
`bolton-phase1-v54.zip` (no separate v53 zip — its lineage's own Stage 2
first step, shared.js, arrived bundled inside this one; done here
independently instead, see v53 above). Same diff-and-merge discipline
as every round, with one real design decision on top: the zip's
`BusinessSettings` redesign renamed and restructured fields that
already held real production data in this app's live database (letter-
head `address`, `phone`, `email`, free-text `bank_details` with a real
bank account already filled in) — adopted the new operational fields
outright (nothing to lose there) but kept this repo's existing field
names and shapes for the letterhead data rather than the zip's rename
(`company_name`/`contact_phone`/`contact_email`) and 4-way bank field
split, since that split had nowhere to put the real "Send Proof of
Payment to" line already sitting in `bank_details` — see the reasoning
recorded directly on the model.

- **New operational settings, closing three real hardcoded/duplicated-
  value bugs**:
  - `VAT_PCT = 0.15` was hardcoded identically in two separate places in
    `main.py` (the single-quote endpoint and the Order Index list
    endpoint) — a genuine pre-existing bug in this repo too, not just
    the zip lineage; a VAT change or a typo landing in only one spot
    could have made the quote screen and Order Index quietly disagree.
    Both now read `vat_pct` from the same `BusinessSettings` row via a
    new `get_settings(session)` helper.
  - The R350 screed bag-overage rate was hardcoded as literal text in
    two frontend locations (the bag-note shown on flooring lines in
    both the Quote Lines table and the print document). Both now read
    `businessSettings.bag_overage_rate`.
  - `createQuote()` never actually sent a `deposit_pct` parameter to
    `POST /quotes` at all — every quote silently got the backend
    model's own hardcoded 70%, regardless of anything configured
    anywhere. Now sends `businessSettings.default_deposit_pct`.
- **Two more real gaps found while merging, beyond what the zip's own
  v54 covered** (checked by grepping for the actual hardcoded values
  after wiring the settings through, not assumed fixed just because the
  model existed now):
  - The Order Index's `computeOrderStatus()` "Overdue" threshold had its
    own local `OVERDUE_DAYS = 7` constant, wired to nothing — the new
    `order_overdue_days` setting existed and was editable on the
    Business Settings page, but changing it would have silently done
    nothing. Fixed: now reads `businessSettings?.order_overdue_days`.
  - **A real correctness bug, not just a label**: the Quote Builder's
    own on-screen total (`loadQuote()`) computed its "incl. VAT" figure
    as `total_ex_vat * 1.15`, hardcoded, instead of using
    `total_incl_vat` already returned by the same `GET /quotes/{id}`
    call (which the print view correctly uses). If VAT were ever
    changed from 15%, the on-screen total and the printed total could
    have silently disagreed — a direct violation of critical path #10
    in the Stage 2 brief ("print quote matches on-screen totals
    exactly"). Fixed by using the backend-computed figure directly
    instead of a second, drift-prone copy of the same calculation. Also
    fixed two more spots that hardcoded the literal text "VAT (15%)" as
    a label (present in the zip's own v54 too, unfixed) to show the
    real configured percentage.
- **New `default_labour_rate_per_m2`**, deliberately wired to apply only
  ONCE, in `toggleLineFields()` when the Flooring category first
  activates — not inside `onVinylProductChange()`, since labour rate is
  a genuinely per-quote adjustable value (weekend labour, own-staff
  overrides); resetting it on every colour/range change would have
  silently wiped out a manual override mid-quote.
- **Business Details tile/page merged into the new Business Settings
  page** — one place for pricing config (VAT %, default deposit %, bag
  overage rate, default labour rate, overdue threshold) and the
  original letterhead fields (business name, address, phone, email, VAT
  number, banking details, plus new `yoco_payment_link`), instead of
  two separate pages. **Owner-only write restriction preserved** on
  `PUT /business-settings` (the zip's own v54 dropped this check
  entirely — not adopted; same security-fix pattern as every other role
  check in this file, and this endpoint now controls real pricing
  levers, not just letterhead text).
- **Real production data migrated in place, not reset**: the live
  `business_settings` row already had a real address, phone, email, VAT
  number, and bank details filled in. `bolton.db.bak-pre-v54` backed up
  first, then the six new columns added via `ALTER TABLE` with their
  model defaults — confirmed after migration that all six original
  values were untouched and the new columns landed correctly.
- **Verified live, same test the zip's own v54 changelog described**:
  changed VAT to 16% and deposit to 60% via `PUT /business-settings` —
  confirmed the single-quote endpoint and the Order Index list endpoint
  agree exactly (R33,659.12 total, matching the zip's own claimed
  figure to the cent) and the deposit split is genuinely 60%, not the
  old hardcoded 70%. Settings restored to the real values (15%/70%)
  immediately after — this was a test, not a real change to make on the
  live business data. Re-verified the confirmed series 200 number
  (R29,016.48) still reproduces exactly afterward.
- `supabase_schema.sql`'s `business_settings` table updated to match.
- Version badge bumped to **v54**.

## v55 — One more hardcoded-VAT spot, confirms convergence with v54's merge
`bolton-phase1-v55.zip`. Mostly a non-event for this repo: its
`BusinessSettings` schema now matches exactly what got merged in here
at v54 (`business_name`/`address`/`phone`/`email`/`bank_details` free
text, not the original guessed rename+restructure) — the zip's own
changelog credits the correction directly to the v54 merge notes handed
back, real convergence, nothing to reconcile. Its "Order Index overdue
threshold" fix is also already present here from the same round. One
genuinely new thing surfaced:

- **A fourth hardcoded-VAT spot, missed in the v54 merge**: `fjCalc()`
  — the live, as-you-type preview calculator shown while building a
  vinyl/screed line, *before* anything is saved — had its own `const
  vat = 0.15;`, entirely separate from the three spots already fixed at
  v54 (the on-screen quote total, and the two printed-quote VAT
  labels). A VAT change in Business Settings would have made this
  preview quietly disagree with the number that actually gets saved and
  charged — arguably the most visible of the four, since it's what
  shows while a quote is actively being built. Fixed the same way as
  the others: `businessSettings?.vat_pct ?? 0.15`.
- Confirmed no other hardcoded VAT references remain anywhere in
  `index.html` — grepped for both `0.15` and `1.15` after this fix and
  checked every remaining hit resolves to the same defensive fallback
  pattern, not a real calculation.
- No backend changes this round — `main.py`/`models.py`/
  `calculations.py` diffs were the same already-known reversions as
  every prior round (Business Settings' Owner-only write check, the
  security fixes, tile-removal VAT correction, etc. — all already
  preserved, nothing new lost or gained).
- Verified: real Node syntax check on both JS files, every
  `getElementById` reference still resolves.
- Version badge bumped to **v55**.

## v56 — Foundation refactor, Stage 2 continues: price-book.js extracted
`bolton-phase1-v56.zip`. Second JS extraction, done directly against
this repo's own `index.html` (same approach as v53's `shared.js` — used
the zip's version as a structural/scope reference, not something to
copy in, since this repo carries real features its lineage still
doesn't). `index.html` down to 2,190 lines; new `price-book.js` (208
lines) holds flooring/blinds/trim price book management.

- **Extracted to `price-book.js`**: `loadFlooring`, `loadBlinds`,
  `loadTrims`, `renderFlooringTree`, `renderTrimTree`, `deleteFlooring`,
  `deleteBlinds`, `deleteTrim`, `addFlooring`, `addBlinds`, `addTrim`,
  `bulkImportFlooring`, `toggleTrimPricingFields`, and
  `TRIM_CATEGORY_LABELS`.
- **`CATEGORY_LABELS` moved to `shared.js`, not `price-book.js`** —
  checked every usage site first (not assumed from the zip's own
  changelog) and confirmed it's genuinely used by both
  `renderFlooringTree` (now in `price-book.js`) AND `renderFlooringDrill`
  (the landing page's browsing view, staying in `index.html`) — a real
  cross-file dependency, not something that could live in only one
  feature file.
- **Deliberately NOT moved**: `renderFlooringDrill()` stays in
  `index.html` — a landing-page browsing view used to start a quote,
  a different concern from price book management even though it reads
  the same `flooringProducts` cache. `refreshLineProductOptions()` also
  stays — it populates a Quote Builder dropdown, not a price book
  concern. Same reasoning as the zip's own v56, verified independently
  against this file rather than trusted at face value.
- **Verified thoroughly, per the refactor brief's rules**: independent
  syntax checks on `shared.js`, `price-book.js`, and the remaining
  inline script; a genuine 3-way cross-file collision check (all three
  concatenated in real load order — `shared.js` → `price-book.js` →
  inline — checked with Node's parser for the same "already declared"
  `SyntaxError` a browser would throw; confirmed none); no duplicate
  top-level function names across all three files; every
  `getElementById` reference and every inline `onclick`/`onchange`/
  `oninput` handler call resolves; a live backend test — a real price
  book listing call (80 flooring products, matching what's actually in
  the live price book) plus the confirmed series 200 figure
  (R29,016.48) — proving the extraction didn't disturb the data flow
  between the price book and the quote calculator. Test quote deleted
  afterward.
- No backend changes this round — `main.py`/`models.py`/
  `calculations.py` diffs were the same already-known reversions as
  every prior round.
- Next: continue Stage 2 with `clients.js`, `order-index.js`, `hr.js`,
  then `quote-builder.js` last (biggest, highest-risk).
- Version badge bumped to **v56**.

## v57 — Foundation refactor, Stage 2 continues: clients.js extracted
`bolton-phase1-v57.zip`. Third JS extraction, same approach as v53/v56
— done directly against this repo's own `index.html`, the zip used as a
structural/scope reference only. `index.html` down to 2,106 lines; new
`clients.js` (96 lines) holds client CRM management.

- **Extracted to `clients.js`**: `renderClients`, `addClient`,
  `openClientDetail`, `renderClientDetail`, and `currentClientDetailId`
  (checked its only usages first — confined entirely to these four
  functions, moves cleanly).
- **Deliberately NOT moved, consistent with the price-book.js
  decisions**: `startQuoteForClient()` stays in `index.html` — same
  "handoff into Quote Builder" category as `startQuoteWithVinylRange`,
  which also stayed put at v56 even though it's triggered from a
  product page. `onQClientInput()`/`selectQClient()` stay too — New
  Quote form's own client-search-while-typing, a Quote Builder concern,
  not client CRM. **Also kept in `index.html`, a call this repo had to
  make on its own since the zip lineage doesn't have the feature at
  all**: `addClientAndStartQuote()` (the Order Index's "New Client →
  Start Quote" panel) — same handoff category as the others, just a
  different entry point.
- **Verified with a full real client-to-quote workflow, live**: added a
  real client via `POST /clients`, confirmed `GET /clients?search=`
  finds it, started a quote with `client_id` set (mirroring what
  `startQuoteForClient` → `createQuote` actually does), confirmed
  `site_address` came back correctly pre-filled from the client
  record — proving the split between `clients.js` (management) and
  `index.html` (the handoff function) didn't break the one feature
  built specifically to bridge them. Client and quote both deleted
  afterward.
- Regression check: series 200 still produces the exact confirmed
  R29,016.48.
- Full verification per the refactor brief's rules: independent syntax
  checks on all three extracted files, a 4-way cross-file collision
  check (`shared.js` → `price-book.js` → `clients.js` → inline, real
  load order, checked for the "already declared" error a browser would
  throw — none), no duplicate top-level function names, every
  `getElementById` reference and inline handler call resolves (scanned
  all four files together from the start this round, not just
  `index.html` — the zip's own v57 changelog flagged this exact gap
  after a false-positive scare with client-form field IDs defined
  inside `clients.js`'s dynamically-generated HTML; this repo's checks
  were already scanning every file, so no false positive here, but
  worth confirming deliberately rather than assuming).
- No backend changes this round.
- Next: `order-index.js`, then `hr.js`, then `quote-builder.js` last.
- Version badge bumped to **v57**.

## v58 — Investigated, doesn't apply here: the zip's "New Client → Start Quote" bug is specific to its own separate lineage
`bolton-phase1-v58.zip`. Its headline fix is for a real bug — but one
that only exists in the zip's own parallel `index.html`, not this repo.
Checked directly rather than applied on faith, since blindly applying
it here would have broken working code.

- **What the zip's bug actually is**: their `startQuoteForClient()`
  takes a single client *object* (`function startQuoteForClient(client)
  { pendingClientId = client.id; ... }` — their own v52-era redesign).
  Their `addClientAndStartQuote()` called it the old way, with three
  positional arguments — `client.id` silently became the whole `client`
  parameter, and every `client.xxx` lookup inside resolved to
  `undefined`. Real bug, in their lineage.
- **Why it doesn't apply here**: this repo's `startQuoteForClient()`
  was deliberately kept on **three separate positional arguments**
  (`clientId, clientName, preferredBranch`) back at the v52 merge —
  documented at the time as a conscious choice, not an oversight (see
  the v51–v52 changelog entry). This repo's `addClientAndStartQuote()`
  has always called it correctly, with three positional arguments
  matching that signature exactly — confirmed by reading both function
  bodies directly, not inferred from the zip's description of its own
  bug.
- **Proven with a deterministic test, same methodology as the zip's own
  v58 changelog describes**: wrote an isolated Node script with a
  stubbed DOM and `fetch`, running this repo's real
  `addClientAndStartQuote()`/`startQuoteForClient()` function bodies
  (copied verbatim, not paraphrased) against a simulated "create a new
  client" click-through. All six checks passed — the POST body, the
  created client's id/name flowing into `pendingClientId`/
  `pendingClientName`, and critically, `q_client` and `q_branch`
  ending up genuinely filled with the new client's name and branch, not
  `undefined`. This repo's version of the feature was never broken.
- Diffed the rest of v58 against `main.py`/`models.py`/`calculations.py`
  and the three already-extracted files (`shared.js`/`price-book.js`/
  `clients.js`) for anything else worth adopting — nothing found beyond
  the same already-known reversions and cosmetic comment rewording seen
  every round.
- No code changes this round. Version badge bumped to **v58** anyway,
  per the standing rule that every version handed over gets an entry
  here — the entry itself is the value this round, so a future session
  doesn't waste time "fixing" working code against a bug report that
  doesn't describe this codebase.

## v59 — Confirms full convergence: the zip lineage reverted its v58 "fix" after seeing this repo's real code
`bolton-phase1-v59.zip`. Same conclusion as v58's entry above, arrived
at independently by the zip's own author after being shown this repo's
actual `startQuoteForClient()` body and both real call sites directly —
not told the conclusion, shown the evidence. Their v59 changelog
corrects their own v58 in the other direction: reverts their copy back
to the three-positional-argument signature this repo has used since
v52, and removes `pendingClientAddress` as dead code (confirmed by
their own search: only ever set, never read — this repo never had that
variable in the first place, since address pre-fill has always worked
server-side via `client_id`, nothing to remove here).

- **Diffed everything for anything beyond the convergence**: `main.py`
  (no new endpoints), `models.py`/`calculations.py` (same already-known
  docstring/reversion noise as every round), and all three extracted
  files (`shared.js`/`price-book.js`/`clients.js` — no function-level
  changes at all). Nothing to adopt.
- No code changes this round — this repo's `startQuoteForClient()` was
  never wrong, so there was nothing to converge toward. Version badge
  bumped to **v59** anyway, same standing rule as v58.

## v60 — Foundation refactor, Stage 2 continues: order-index.js extracted
`bolton-phase1-v60.zip`. Fourth JS extraction, same approach as every
prior round — done directly against this repo's own `index.html`, the
zip used as a structural/scope reference only. `index.html` down to
2,014 lines; new `order-index.js` (112 lines) holds the Order Index
list page.

- **Extracted to `order-index.js`**: `computeOrderStatus`,
  `renderOrderIndex`, `deleteQuoteFromIndex`, `addClientAndStartQuote`
  — exactly the function list confirmed directly beforehand (asked to
  list every Order-Index function with line numbers, then to show
  `deleteQuoteFromIndex`'s real source and call site, before touching
  anything).
- **`DELETE /quotes/{quote_id}`, which the zip's own v60 flagged as a
  "new backend endpoint it had to build" — already existed here**, with
  full cascade cleanup (line items, payment follow-ups, colour change
  logs), built back at v49 and hardened at the v52 merge. Same
  already-known-gap pattern as every round; nothing to add. Re-verified
  live anyway rather than trusted from memory: created a quote, added a
  line and a follow-up, deleted it through the same endpoint
  `deleteQuoteFromIndex()` actually calls, confirmed the quote 404s
  afterward and drops out of `GET /quotes` — zero orphaned records.
- **Deliberately NOT moved, matching the zip's own reasoning, verified
  independently against this file**: `saveOrderDetails()`,
  `logFollowUp()`, `loadFollowUps()` stay in `index.html` — they serve
  the Order Details card that still lives on the Quote Builder page,
  not Order Index. Moving them now, before that card relocates, would
  put them in the wrong file relative to the UI they actually serve.
  That relocation (Quote Builder → Order Index) is flagged as a real,
  separate, still-open UX concern — order/payment info doesn't make
  sense to ask for at quoting time, when none of it exists yet — not
  something to fold silently into this extraction.
- **Verified per the refactor brief's rules**: independent syntax
  checks on all four extracted files; a genuine 5-way cross-file
  collision check (`shared.js` → `price-book.js` → `clients.js` →
  `order-index.js` → inline, real load order, checked for the
  "already declared" error a browser would throw — none); no duplicate
  top-level function names; every `getElementById` reference and inline
  handler call resolves, scanning the complete `index.html` file
  (all its real HTML markup, not just the extracted script content —
  the zip's own v60 changelog flagged this exact false-positive risk
  after a ~130-ID scare from checking the wrong slice; this repo's
  checks already scan the whole file every round, confirmed
  deliberately rather than assumed safe).
- Regression check: series 200 still produces the exact confirmed
  R29,016.48.
- Next: `hr.js`, then `quote-builder.js` last. The Order Details
  relocation (Quote Builder → Order Index) is a separate, still-open
  task worth its own dedicated round.
- Version badge bumped to **v60**.

## v61 — Foundation refactor, Stage 2 continues: quote-builder.js extracted, after a full pre-extraction audit
Not a zip round — direct work, requested only after a dedicated audit
(function inventory of both HR and Quote Builder, direct-build
questions, live commission rate values) confirmed nothing else was
hiding in this file undiscovered, given four separate divergences had
already surfaced in prior rounds. Fifth JS extraction, the biggest and
highest-risk one — the pricing calculator itself. `index.html` down to
1,352 lines; new `quote-builder.js` (605 lines) holds the floor job
calculator and the quote lifecycle.

- **Scoping confirmed explicitly before extracting, not assumed**:
  - `renderPrintDoc()` moved to `shared.js`, not `quote-builder.js` —
    genuinely shared between this file's `printQuote()` and
    `index.html`'s Print Invoice tile (`renderPrintInvoicePicker`/
    `printInvoiceForQuote`), confirmed by reading both call sites
    directly beforehand.
  - `renderPrintInvoicePicker()`/`printInvoiceForQuote()` stay in
    `index.html` — Print-Invoice-tile-specific, not a Quote Builder
    concern, same pattern as other landing-tile functions that stayed
    put in earlier rounds (`renderFlooringDrill`, etc.).
  - `saveOrderDetails()`/`logFollowUp()`/`loadFollowUps()` stay in
    `index.html` — still serve the Order Details card sitting on the
    Quote Builder page, but that relocation to Order Index is its own
    separate, still-open task (flagged back at v60); `loadQuote()`
    (now in `quote-builder.js`) still calls the external
    `loadFollowUps()` directly, same kind of cross-file call as every
    other extraction round.
- **Extracted to `quote-builder.js`**: `refreshLineProductOptions`,
  `toggleLineFields`, `populateFloorProductDropdowns`,
  `populateVinylRangeDropdown`, `onVinylRangeChange`,
  `onVinylColourChange`, `fjOnIncludeChange`, `onVinylProductChange`,
  `onScreedProductChange`, `applyScreedRateForJobType` (plus its
  top-level `addEventListener` wiring), `fjOnMaterialOnlyChange`,
  `fjCalc`, `addFloorJob`, `toggleScreedSubfields`, `createQuote`,
  `startFreshQuote`, `saveQuote`, `printQuote`, `addLine`,
  `deleteQuoteLine`, `changeLineColour`, `viewColourHistory`,
  `loadQuote`.
- **A real cross-file coupling found during the pre-extraction audit,
  fixed before it caused a problem**: `sortByPriority()` was defined in
  `index.html`'s inline script but called by `price-book.js` (extracted
  three rounds ago) — it only worked because of script-load timing
  (price-book.js never actually called it until after every script tag
  had finished loading, not because it was genuinely available where
  needed). Now that `quote-builder.js` needs it too, moved to
  `shared.js` where both real callers can reach it properly.
- **Verified far more thoroughly than prior rounds, given the stakes**:
  independent syntax checks on all five extracted files; a genuine
  6-way cross-file collision check (`shared.js` → `price-book.js` →
  `clients.js` → `order-index.js` → `quote-builder.js` → inline, real
  load order, checked for the "already declared" error a browser would
  throw — none); no duplicate top-level function names; every
  `getElementById` reference and inline handler call resolves against
  the complete `index.html`; a live backend regression test reproducing
  the confirmed series 200 figure (R29,016.48) plus the confirmed
  Material-Only figure from the v39–v44 changelog (series 120, glue and
  labour forced to zero → R19,779.76, exact match) plus a misc-line
  dispatch test (100% margin, zero cost) covering `addLine()`'s
  category-branching logic; and — since `fjCalc()` is pure client-side
  preview logic with no backend round-trip to check it against — a
  deterministic Node simulation running the actual extracted function
  body (copied verbatim, not paraphrased) against the same inputs as
  the backend regression test, confirming it independently computes the
  identical R29,016.48 ex VAT / R33,368.95 incl VAT. All test data
  cleaned up afterward.
- Next: `hr.js` is the only Stage 2 file left, followed by the Order
  Details relocation (Quote Builder → Order Index) as its own dedicated
  round.
- Version badge bumped to **v61**.

## v62 — Flooring settlement discount + tile reference dimensions (backend), plus a claim caught and rejected before it caused damage
Requested directly, not from a zip. First pass claimed `FlooringProduct`
already had `settlement_discount_pct`/`tile_width_mm`/`tile_length_mm`/
`tile_thickness_mm` and asked for frontend JS referencing three HTML
fields that weren't part of the same request — checked `models.py`
directly before touching anything and found none of the four fields
existed anywhere in the backend (`settlement_discount_pct` existed, but
on `BlindsProduct`, not `FlooringProduct`). Applying the JS as given
would have thrown a null-reference error the instant anyone tried to
add a flooring product — reported back instead of applying. This entry
covers the follow-up request: build it for real, backend first.

- **New `FlooringProduct` fields**: `settlement_discount_pct` (float,
  default 0.0 — a further discount some suppliers offer on top of trade
  discount) and `tile_width_mm`/`tile_length_mm`/`tile_thickness_mm`
  (reference data from the supplier price list, not used in any
  calculation).
- **Settlement discount wired into `calculate_flooring_line()`,
  cost-only, verified by tracing the code before trusting the
  description**: `box_total_cost` (which feeds `subtotal` →
  `marked_up` → `line_total`, the actual client-facing price) is
  deliberately left untouched. A new `box_total_true_cost` applies the
  settlement discount on top of the already-trade-discounted cost and
  feeds only `material_cost_total` (cost/margin reporting). Confirmed
  this is the exact same pattern already used for `BlindsProduct`
  (`net_cost = book_price × (1 - trade_discount) × (1 -
  settlement_discount)`, while sell price stays book price,
  independent of both discounts) before writing a line of code.
- **Verified live with a real side-by-side comparison**, not just
  reasoning about it: created two otherwise-identical flooring products
  differing only in `settlement_discount_pct` (0% vs 7.5%), added the
  same 100m² smooth line for both. `line_total` came back **identical**
  (R29,016.48) in both cases — client price genuinely untouched.
  `unit_cost` dropped R188.59 → R175.72, `margin_pct` improved 35.01% →
  39.44%. Checked the arithmetic by hand: R17,153.83 box cost × 0.925 =
  R15,867.29, + R1,705 unchanged glue cost = R17,572.29 total job cost
  — matches exactly. Glue cost confirmed untouched, as it should be
  (settlement discount only applies to the box cost).
- **Real production data migrated in place**: `bolton.db` backed up
  first (`bolton.db.bak-pre-settlement-discount`), four new columns
  added via `ALTER TABLE` with model defaults. Confirmed all 80 real
  flooring products intact afterward, all four new columns correctly
  defaulted (0.0 / NULL). Test products and quote cleaned up after
  verification; confirmed back to exactly 80 real products and the
  standard series 200 regression figure (R29,016.48) still holds.
- `supabase_schema.sql`'s `flooring_product` table updated to match.
- **Not built this round**: the frontend form fields (Add Flooring
  Product UI, `price-book.js` submit logic) — the backend now genuinely
  supports what the original request asked for, so that frontend work
  is real and unblocked, just not part of this round.
- Version badge bumped to **v62**.

## v63 — Order Details card removed from Quote Builder (owner decision), data/functions kept intact for reuse
Requested directly. Owner decided this card doesn't belong on the
Quote Builder page — asking for site address, install date, and
payment/invoice info while quoting, before any of it exists, doesn't
make sense (the exact concern flagged as a still-open task since v60).
The owner wants this data accessible a different way, likely from Order
Index, as a separate future task — this round only disconnects it from
Quote Builder, deliberately not rebuilding it anywhere yet.

- **Removed**: the entire `orderDetailsCard` HTML block (site address,
  install date, invoice/deposit/final-payment fields, and the
  follow-up log section) from the Quote Builder page.
- **`loadQuote()`**: no longer shows the card, no longer populates the
  `od_*` fields from the quote record, no longer calls
  `loadFollowUps()`.
- **Kept fully intact, exactly as instructed**: `saveOrderDetails()`,
  `logFollowUp()`, `loadFollowUps()`, and every backend field/endpoint
  they use (`Quote.site_address` and friends, `PaymentFollowUp`,
  `POST /quotes/{id}/follow-ups`, etc.) — untouched, ready for whatever
  Order Index rebuild comes next.
- **A real break found and fixed, beyond what was asked for**:
  `startFreshQuote()` also referenced `orderDetailsCard` (to hide it on
  reset) — not mentioned in the request, but leaving it in would have
  thrown a null-reference error the next time anyone started a new
  quote, since `document.getElementById()` now returns `null` for that
  id. Worse than a cosmetic miss: the error would have stopped
  `startFreshQuote()` partway through, silently skipping its later
  lines too (re-enabling the Start Quote button, clearing the status
  text). Removed that one line; the rest of the reset behaviour is
  unaffected.
- **Confirmed the three kept-but-disconnected functions are genuinely
  unreachable now, not just visually hidden** — grepped every file for
  any remaining caller of `saveOrderDetails()`/`logFollowUp()`/
  `loadFollowUps()` beyond their own definitions and logFollowUp's
  internal call to loadFollowUps; none exist. Their internal
  `getElementById()` calls for the now-deleted `od_*`/`fu_*`/
  `orderDetailsSaveStatus`/`followUpList` ids will never actually
  execute as a result — confirmed this is safe dead code, not a latent
  bug, before leaving it in place per the explicit instruction not to
  delete these functions.
- **Verified**: JS syntax check, cross-file collision check, every
  `getElementById`/handler call resolves except the 11 ids inside the
  three now-dormant functions (exactly the expected set, nothing
  else) — checked and accounted for, not just "the checker flagged
  something." Live regression test: series 200 still produces
  R29,016.48, and confirmed `Quote.site_address` and the rest are still
  present and populated in the API response, proving the backend data
  itself is completely untouched — only the Quote Builder UI stopped
  reading/writing it.
- No backend changes this round.
- Version badge bumped to **v63**.

## v64 — Real security fix: document download role check, closed properly this time
Requested directly, following on from investigating a gap flagged
while auditing before the `hr.js` extraction. Two changes, both
confirmed necessary before applying either.

- **Frontend**: `loadDocuments()`'s download link now sends
  `?role=${currentRole()}`, same as the list fetch two lines above it
  already correctly does. Confirmed via grep this is the only place in
  the entire frontend (all six files) that builds a link to
  `/documents/{id}/download` — one fix, no other call site to miss.
  Worth recording: `index.html.pre-v44-merge` (a static backup, not
  live code) shows this link used to pass `role` correctly via
  `window.open()` — the param was lost at some point when the link got
  rewritten as a plain `<a href>`, not something that was always
  missing.
- **Backend**: `download_document`'s `role` parameter default changed
  from `UserRole.owner` to `UserRole.sales` — confirmed real
  defense-in-depth, not a replacement for the frontend fix. The old
  default failed open: any caller (present or future) that forgot to
  pass `role` silently got Owner-level access, the most dangerous
  direction to fail in. The new default fails closed: a forgotten
  `role` now denies Owner-level material by default, which breaks
  loudly (a wrongly-denied Owner reports it immediately) instead of
  leaking quietly (a wrongly-allowed Sales user might never be
  noticed) — exactly the failure mode that let the frontend gap sit
  unnoticed as long as it did.
- **Verified live, all three cases explicitly**: created a real
  Owner-only test document. Owner role → 200 (downloads correctly).
  Sales role → 403 (refused, matches the existing message). No `role`
  param at all → **403**, where it previously silently succeeded as
  Owner — the exact gap, confirmed closed. Also checked the fix doesn't
  over-restrict: a non-restricted (`owner_only=false`) document
  downloads fine both with no role param and with `role=sales` — the
  check only ever fires for documents actually marked restricted.
  Both test documents deleted afterward.
- Version badge bumped to **v64**.

## v65 — Foundation refactor, Stage 2 complete: hr.js extracted
Sixth and final extraction. Preceded by a dedicated pre-extraction
audit (function inventory, direct-build questions, live commission
rate values, per the owner's explicit request before touching this
file, given how many real divergences earlier rounds had surfaced).
`index.html` down to 796 lines; new `hr.js` (580 lines) holds
Employees, Hours Worked, Leave, Documents, and Commission.

- **Status check confirmed before starting, as required**: v64 (the
  document-download role-default fix) verified fully applied and
  live — backend default is `UserRole.sales`, the frontend link
  carries `?role=${currentRole()}`, and the running server process
  postdates both file edits. Nothing left open from the security
  detour; proceeded.
- **Function inventory matched exactly, first time this session with no
  divergence found**: every function listed in the extraction brief
  was present and accounted for, plus `employeeOptionsHtml`,
  `reloadHoursSummary`, and `businessDaysBetween` (already known from
  the earlier audit, just not repeated in the brief's shorthand list).
  Nothing built directly outside the chat turned up this round.
- **Scoping checked explicitly, not assumed**: grepped every candidate
  name (`employeeOptionsHtml`, `hrEmployeesCache`, `hrView`,
  `currentEmployeeDetailId`, and the five main HR render/load
  functions) across every other file, plus checked Quote Builder/Order
  Index/Clients/Price Book directly for any employee or commission
  reference. Two real findings:
  - **`hrView` moved to `shared.js`, not `hr.js`** — a genuine
    cross-file dependency: set from `index.html`'s `onTileClick()`
    (landing tile dispatcher, stays there), read from `hr.js`'s
    `renderHR()`/`hrSubnav()`. Same category as `CATEGORY_LABELS` and
    `sortByPriority` in earlier rounds.
  - **`currentEmployeeDetailId` is dead code** — declared, never read
    or written anywhere in the app, confirmed by search before moving
    it. Carried into `hr.js` as-is (not part of this extraction's scope
    to clean up unrelated dead code) with a comment explaining what it
    is, rather than silently dropped or silently kept unexplained.
- **Verified to the same standard as `quote-builder.js`, not less,
  given real money is involved**:
  1. Independent syntax check on all six extracted files.
  2. A genuine 7-way cross-file collision check (`shared.js` →
     `price-book.js` → `clients.js` → `order-index.js` →
     `quote-builder.js` → `hr.js` → inline, real load order, checked
     for the "already declared" error a browser would throw — none).
  3. Every `getElementById`/handler call resolves, except the same 11
     ids inside the three Order-Details functions already confirmed
     dormant-by-design at v63 — nothing new.
  4. **Live commission calculation, a real number checked, not just
     "the page loads"**: created a real employee (pure_sales,
     8/10/12% tiers), a quote with a known line (R29,016.48 turnover,
     R18,858.83 cost → R10,157.65 GP), marked it paid, pulled a real
     statement: `rate_applied_pct: 0.08, commission_due: 812.61` —
     matches the hand-calculated 8%-tier figure exactly.
  5. **Leave approve/reject balance timing, tested explicitly**: set up
     a 21-day balance, submitted a 3-day request — balance confirmed
     **unchanged** (`days_taken: 0, days_remaining: 21`) immediately
     after submission. Approved it — balance **then** updated
     (`days_taken: 3, days_remaining: 18`). Confirmed the deliberate
     "only on approval" behavior still holds through the exact
     endpoints `hr.js` calls.
  6. **Sick note upload + link, tested explicitly since it was just
     added**: uploaded a real file, submitted a sick leave request with
     `sick_note_document_id` set to the returned document's id,
     confirmed the link persisted (`sick_note_document_id: 1` on the
     saved request) and the document itself is correctly typed and
     retrievable. Survived the file split intact.
  All test data (employee, quote, leave balance/requests, document)
  cleaned up afterward; confirmed the employee list and leave balances
  back to exactly the real pre-existing data.
- No backend changes this round.
- **Stage 2 of the foundation refactor is complete.** Six files
  extracted from the original single `index.html`: `shared.js` (212
  lines), `price-book.js` (208), `clients.js` (96), `order-index.js`
  (112), `quote-builder.js` (594), `hr.js` (580) — 1,802 lines of
  feature code now organized by responsibility instead of one
  undifferentiated file. `index.html` itself is down to 796 lines: the
  app shell, landing page, HR/Quote-Builder-adjacent handoff functions
  (`startQuoteForClient`, `startQuoteWithVinylRange`,
  `openQuoteFromIndex`, `onQClientInput`/`selectQClient`), the Print
  Invoice landing tile, Business Settings, Business Overview, and the
  three Order Details functions (`saveOrderDetails`/`logFollowUp`/
  `loadFollowUps`) — kept intact but currently disconnected from any
  UI since their Quote Builder card was removed at v63, awaiting a
  proper home on Order Index as a separate future task.
- Version badge bumped to **v65**.
