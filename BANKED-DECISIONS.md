# Banked Architecture Decisions

Decisions that are **confirmed direction, not built** — documented so
nothing built in the meantime accidentally forecloses on them, and so
whoever picks up multi-company/auth work later (including future
Claude Code sessions) doesn't have to reconstruct the reasoning from
scratch or guess at a shape that's already been thought through.

Nothing in this file should be treated as a task. If a change under
development looks like it would conflict with an entry here, that's
worth flagging before proceeding, not silently working around.

---

## Master Supplier Price Catalog
**Status: BANKED, not built. Documented Aug 2026 for when multi-company
use is real.**

### The problem this solves

Right now, one person (Claude, in chat with Burgert) manually curates
and loads supplier price data for one business. That doesn't scale past
one company, and it means every price update is a manual, one-off task.

The goal: when a supplier (Azura, Aspen, or a future blinds supplier)
updates their price list — typically once a year — every subscribed
company picks up the new numbers automatically, with zero manual
per-company data entry, while each company still fully controls their
own commercial terms on top of the supplier's raw prices.

### Core distinction: what's shared vs. what's per-company

**Shared (one copy, maintained centrally, "the master catalog"):**
- Supplier name
- Product/range name (e.g. "deZIGN series 200")
- Colour
- The supplier's actual list price (Zone A rate, or per-box price)
- Physical specs — m²/box, dimensions, wear layer, etc.
- An effective date / version number per price point (see versioning
  below)

**Per-company (each business controls this on their own side):**
- Which suppliers they use at all
- Which specific ranges/products within a supplier's full catalog they
  subscribe to — not automatically the whole catalog. Example: Burgert
  only wants deZIGN LVT out of Azura's much larger range (which also
  includes laminate, oak flooring, rigid board, accessories)
- Trade discount % — varies company to company based on their own deal
  with the supplier
- Sell markup — each company's own margin strategy
- Delivery fee, wastage convention — company-specific operational
  choices

### The price-update workflow (the part that needs real care)

1. Supplier sends an updated price list (once a year, typically)
2. The master catalog entry for that product/colour gets a **new
   version**, not an overwrite — the old price stays retrievable, not
   deleted
3. Every company subscribed to that range sees "a new price is
   available" — this is a **notification, not an automatic change**
4. Each company independently chooses: adopt the new price for new
   quotes going forward, or keep quoting on the old price until they're
   ready to switch
5. **Critical, non-negotiable rule**: this never retroactively touches
   a quote that's already been created. A quote locks in the price it
   was built with at creation time — exactly the same principle already
   built and tested for colour (a quote's colour is a locked snapshot,
   immune to later price book edits, confirmed in v41–v42, see
   `CHANGELOG.md`). Price should work the same way. The "commit to old
   or new" choice only ever affects what *new* quotes use going forward.

### Why this is banked, not built now

- Depends entirely on real multi-tenancy existing first (there's one
  company right now — "subscribe" and "per-company control" are
  meaningless without a second real company to test the design against)
- Depends on real authentication existing first (same reason)
- Building this speculatively, before a second company's real
  requirements are known, risks guessing wrong on the exact shape of
  "which ranges a company subscribes to" — better to build it once,
  correctly, when there's a real second customer to validate against

**Sequencing**: this comes after real auth, after multi-tenancy, likely
around the same time as the "Bolton Flooring / Blinds / Complete
editions" module-toggle work — see **Bolton Editions — Product
Packaging Strategy** below. Natural to build both together, since
they're both "per-company configuration on top of a shared foundation."

### What happens in the meantime (current, manual process)

When a supplier sends an updated price list, it goes to Claude the same
way Azura's and Aspen's real data did originally — real numbers
verified, formula-checked, loaded via the existing bulk-import feature.
Manual, but reliable, and correct for a single-company reality. This
stays the process until the automated version above is actually built.

### Things already built that this must stay compatible with

- **Per-quote-line colour snapshot** (`colour`/`original_colour` on
  `QuoteLineItem`, confirmed v41–v42, `ColourChangeLog` for the audit
  trail) — the price-locking behaviour above is explicitly modelled on
  this. Don't let future price-versioning work quietly weaken the
  colour snapshot's own guarantee while making price work the same way.
- **`BusinessSettings` singleton** (`id` always `1`, confirmed v54) —
  explicitly documented as "deliberately NOT multi-tenant yet... small,
  mechanical [to extend] to one row per company later, not a
  restructure." Real multi-tenancy work should confirm that claim
  still holds before relying on it.
- **Role-based visibility** (Owner/Admin/Sales, enforced server-side) —
  whatever auth gets built should extend this model, not replace it;
  the per-role cost/margin hiding is load-bearing throughout `main.py`.

---

## Bolton Editions — Product Packaging Strategy
**Status: BANKED, not built. Decision recorded, no code should exist
for this yet.**

Originally decided as part of a broader product-strategy conversation
(product strategy brief, Aug 2026). Written down here properly since it
was previously only in chat history, not the repo — a real gap flagged
while reviewing the master-catalog architecture doc above (its own
"Sequencing" section referenced this exact work as "banked from an
earlier strategy conversation" with nowhere to point to — now fixed).

### The decision

**Concentrate on Bolton.** It's the primary product — own business use
plus a clearer commercial path — and must not compete for build
attention with Vestige (secondary, longer-term).

**One codebase, not three.** Even though Bolton eventually needs to
serve businesses that only do flooring, only do blinds, or both, the
decision is explicitly **NOT** to split into separate flooring/blinds/
complete codebases. One application, sold as different **editions** by
enabling or disabling modules per company.

| Edition | Flooring quoting | Blinds quoting | Shared (clients, orders, commission, HR) |
|---|---|---|---|
| Bolton Flooring | On | Off | On |
| Bolton Blinds | Off | On | On |
| Bolton Complete | On | On | On |

**Future implementation shape** (not built, just the intended design):
a per-company `enabled_modules` setting, with both the UI and the API
hiding or blocking whichever modules are switched off for that company.

### Why this packaging, not separate codebases

- One codebase to maintain, not three drifting in parallel
- Shared functionality (clients, order tracking, commission, HR) stays
  consistent across every customer regardless of which quoting modules
  they've bought — no duplicated logic to keep in sync
- Can sell flooring-only, blinds-only, or both without forking anything
- A real split into separate codebases is only worth revisiting if
  later real-world traction proves the domains genuinely conflict in a
  way module-toggling can't handle — not a decision to make
  speculatively now

### Explicitly not for now

Do **not** build `enabled_modules`, per-company module flags, or any
UI/API gating for this. This is a recorded decision, not a task. The
current priority is making the existing hybrid (flooring + blinds in
one app, one business) stable first — this only becomes real work once
multi-tenancy exists and there's a second real customer whose module
needs are actually known, not guessed at.

### Things already built that this must stay compatible with

(Same spirit as the master catalog doc's own compatibility section —
noted so a future session building this doesn't fight the existing
architecture.)

- **Server-side role visibility** (Sales role cost/margin stripping,
  price-field locking) — the module on/off gating this design implies
  should follow the same pattern: enforced server-side, not just hidden
  in the UI.
- **`BusinessSettings` singleton** — already documented above as easy
  to extend to one row per company later; `enabled_modules` would
  naturally live on that same per-company settings record once it
  exists, not a separate table.
- **The colour-snapshot / locked-at-creation pattern** (colour, and the
  master-catalog price-versioning design above) — the same principle of
  "changes to shared/master data don't retroactively affect something
  already committed" likely applies here too: if a company's edition
  changes (e.g. adds Blinds later), that shouldn't retroactively alter
  historical quotes/orders created before the change.
