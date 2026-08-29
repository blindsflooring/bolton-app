// ===== QUOTE BUILDER =====
// The pricing calculator (vinyl/screed live preview + save) and the
// quote lifecycle (create, save, print, add/delete lines of every
// category, colour changes). Confirmed Aug 2026, Stage 2 of the
// foundation refactor, fifth extraction — the biggest and highest-risk
// file, done only after a full pre-extraction audit (function inventory,
// direct-build questions, commission rate values) confirmed nothing else
// was hiding here undiscovered.
//
// Deliberately NOT included here, per explicit scoping confirmed before
// extraction:
// - renderPrintDoc() lives in shared.js, not here — genuinely shared
//   between this file's printQuote() and index.html's Print Invoice tile
//   (renderPrintInvoicePicker/printInvoiceForQuote), so it belongs where
//   both callers can reach it.
// - renderPrintInvoicePicker()/printInvoiceForQuote() stay in
//   index.html — Print-Invoice-tile-specific, not a Quote Builder
//   concern, same pattern as other landing-tile functions that stayed
//   put in earlier extraction rounds.
// - saveOrderDetails()/logFollowUp()/loadFollowUps() never came here —
//   the previously-flagged relocation (Quote Builder → Order Index) is
//   now done (Quote Builder Layout Corrections brief, confirmed Aug
//   2026): the Order Details card and its functions moved straight to
//   order-index.js, keyed off currentOrderDetailQuoteId. This file no
//   longer references any of it.
// - sortByPriority() moved to shared.js, not here — a real cross-file
//   coupling found during the pre-extraction audit: price-book.js
//   (already extracted) was calling it while it was still only defined
//   in index.html, working purely by script-load timing luck, not by
//   design.

// Carpet Tab, Type Split, and Product Filtering (confirmed Aug 2026) —
// fixes a real, confirmed pricing bug: the four Carpet Calculators
// product types (their own real FlooringProduct rows, flooring_category
// tagged, added same day) were reachable and selectable inside the
// Vinyl-only panel (#fjMain's populateVinylRangeDropdown(), below),
// which filtered only on pricing_type — never on flooring_category at
// all — and ran them through Vinyl's box-based calculate_flooring_line()
// path instead of their own already-confirmed LM-based
// calculate_carpet_line(). A carpet product has no m2_per_pack (it uses
// roll_width_m instead), so that box path silently produced wrong
// numbers rather than erroring — confirmed on screen (screenshot
// evidence in the brief): "Berber Point 920" priced with a stale/wrong
// glue rate and box-based fields that don't apply to it at all. Used
// everywhere a product list needs to genuinely exclude these four —
// the Vinyl range/colour dropdowns below, AND the Carpet tab's own
// per-type filtering (populateCarpetTypeProducts()) uses the SAME list
// the other direction (INCLUDE only the one matching type).
const CARPET_ONLY_CATEGORIES = ['carpet_tufted_broadloom', 'carpet_needlepunch_broadloom', 'carpet_tile', 'cushion_vinyl'];

function refreshLineProductOptions() {
  const cat = document.getElementById('line_category').value;
  const sel = document.getElementById('line_product');
  let list;
  if (cat === 'blinds') list = blindsProducts;
  else if (cat === 'skirting') list = trimProducts.filter(p => p.category === 'skirting' || p.category === 'quarter_round');
  else if (cat === 'trim') list = trimProducts.filter(p => p.category !== 'skirting' && p.category !== 'quarter_round');
  else list = trimProducts;
  sel.innerHTML = sortByPriority ? sortByPriority(list).map(p => `<option value="${p.id}">${p.product_name}</option>`).join('')
    : list.map(p => `<option value="${p.id}">${p.product_name}</option>`).join('');
}

// Category tabs (confirmed Aug 2026, Vinyl Quoting UX Redesign proposal
// §06, approved) — the visible tab row (index.html) is a front end onto
// the SAME #line_category <select> every existing call site already
// reads/writes; nothing downstream had to change. Screed isn't a real
// line_category value at all (still "flooring" underneath, per the
// proposal's own explicit non-goal — no calculation-engine change) — it
// maps to flooring with the vinyl checkbox off, screed checkbox on, so
// the tab lands screed-only without a checkbox detour.
//
// Bring Vinyl/Screed to Carpet's Add Line Flow (confirmed Aug 2026) —
// the Flooring tab used to set BOTH fj_include_vinyl AND
// fj_include_screed true, the actual mechanism behind the "combined
// Floor Job" submission this brief exists to retire: one click could add
// a vinyl line AND a screed line together, unlike every other category
// (Carpet included), which only ever adds the one thing currently
// selected. Now mutually exclusive, same shape Screed's own branch
// already had — Flooring tab means "I'm adding a vinyl line right now,"
// Screed tab means "I'm adding a screed line right now," never both at
// once. fj_include_vinyl/fj_include_screed are no longer user-facing
// checkboxes (index.html) — hidden inputs now, purely internal state —
// but every downstream reader of their .checked property
// (fjOnIncludeChange(), fjCalc(), addFloorJob(), prefillFlooringEdit())
// needed zero changes, since the values they read are still exactly
// "which one is active," just set by tab selection instead of a
// checkbox click.
function selectLineCategoryTab(tab) {
  // Same guard the old <select>'s onchange handler carried (see its own
  // long-form comment, index.html) — a stale editingLineId surviving a
  // category switch would try to save the NEW category's fields against
  // the OLD line's id and get rejected server-side. New trigger, same
  // protection.
  if (editingLineId) cancelLineEdit();
  document.getElementById('line_category').value = (tab === 'screed') ? 'flooring' : tab;
  if (tab === 'flooring') {
    document.getElementById('fj_include_vinyl').checked = true;
    document.getElementById('fj_include_screed').checked = false;
  } else if (tab === 'screed') {
    document.getElementById('fj_include_vinyl').checked = false;
    document.getElementById('fj_include_screed').checked = true;
  }
  toggleLineFields();
}

// Screed has no line_category value of its own (see above) — the active
// tab is derived from the real state (category + include-checkboxes)
// rather than tracked as separate parallel state that could drift out
// of sync with it.
function activeLineCategoryTab() {
  const cat = document.getElementById('line_category').value;
  if (cat !== 'flooring') return cat;
  const includeVinyl = document.getElementById('fj_include_vinyl').checked;
  const includeScreed = document.getElementById('fj_include_screed').checked;
  return (includeScreed && !includeVinyl) ? 'screed' : 'flooring';
}
function syncActiveCategoryTab() {
  const active = activeLineCategoryTab();
  document.querySelectorAll('.line-category-tab').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.tab === active);
  });
}

// Persistent summary panel (confirmed Aug 2026, Vinyl Quoting UX
// Redesign proposal §03/§10, approved, Phase 3) — "what's been added,
// what's outstanding, and the total, visible at all times without
// scrolling." Same seven buckets as the category tabs above (a screed
// line is still category "flooring" server-side — flooring_pricing_type
// is what tells the two apart, exactly like the tabs already do).
const QUOTE_SUMMARY_CATEGORIES = [
  ['flooring', 'Flooring'], ['screed', 'Screed'], ['carpet', 'Carpet'], ['blinds', 'Blinds'],
  ['skirting', 'Skirting'], ['trim', 'Trim'], ['stairwell', 'Stairwell'], ['misc', 'Misc'],
];
function lineSummaryBucket(line) {
  // Carpet (confirmed Aug 2026, Carpet Calculators / Carpet Tab briefs)
  // — a carpet line is category=="flooring" underneath (deliberately, so
  // every existing category=="flooring" consumer needs no changes — see
  // generate_order_sheets()'s own comment, main.py), but it needs its
  // OWN summary bucket, not lumped into plain "Flooring", the same way
  // Screed already gets pulled out below it. carpet_category is the
  // real, stored tell — never re-derived from the product record.
  if (line.category === 'flooring') {
    if (line.carpet_category) return 'carpet';
    return line.flooring_pricing_type === 'screed' ? 'screed' : 'flooring';
  }
  return line.category;
}

// Three states, formalized in the approved proposal §10 — Not started
// (○, muted, "none yet" — informational, never a warning: not every
// job needs every category), Calculated (→, the tab currently being
// worked on, no line saved yet), Included in Quote (✓, a real saved
// line, with its subtotal). "Calculated" here is approximated by "this
// is the active tab" rather than tracking every field's dirty state —
// a deliberately simpler, lower-risk signal that's already available
// (activeLineCategoryTab(), same file) rather than a new mechanism
// built just for this.
// Cached purely so a tab switch (selectLineCategoryTab()) can refresh
// just the "active/in-progress" row without re-fetching the whole
// quote — always overwritten with the real, backend-computed figure
// every time loadQuote() runs, never independently calculated.
let lastKnownQuoteTotalInclVat = 0;
function renderQuoteSummaryPanel(totalInclVat) {
  const panel = document.getElementById('quoteSummaryPanel');
  if (!panel) return;
  if (!currentQuoteId) { panel.style.display = 'none'; return; }
  if (totalInclVat === undefined) { totalInclVat = lastKnownQuoteTotalInclVat; }
  else { lastKnownQuoteTotalInclVat = totalInclVat; }
  panel.style.display = '';

  const buckets = {};
  (currentQuoteLinesCache || []).forEach(l => {
    const b = lineSummaryBucket(l);
    if (!buckets[b]) buckets[b] = { count: 0, subtotal: 0 };
    buckets[b].count += 1;
    buckets[b].subtotal += l.line_total;
  });
  const addLineCardVisible = document.getElementById('addLineCard').style.display !== 'none';
  const active = addLineCardVisible ? activeLineCategoryTab() : null;

  const rows = QUOTE_SUMMARY_CATEGORIES.map(([id, label]) => {
    const b = buckets[id];
    if (b) {
      return `<div class="qsp-row qsp-row-done"><span>✓ ${label}${b.count > 1 ? ` (${b.count})` : ''}</span><span>R${b.subtotal.toFixed(2)}</span></div>`;
    }
    if (id === active) {
      return `<div class="qsp-row qsp-row-active"><span>→ ${label} — in progress</span><span></span></div>`;
    }
    return `<div class="qsp-row qsp-row-empty"><span>○ ${label} — none yet</span><span></span></div>`;
  }).join('');

  const outstanding = QUOTE_SUMMARY_CATEGORIES.filter(([id]) => !buckets[id]).map(([, label]) => label);
  const lineCount = (currentQuoteLinesCache || []).length;
  const outstandingText = outstanding.length
    ? `${outstanding.slice(0, 2).join(', ')}${outstanding.length > 2 ? '…' : ''} outstanding`
    : 'nothing outstanding';
  const compactText = lineCount
    ? `${lineCount} added · ${outstandingText} · R${totalInclVat.toFixed(2)}`
    : `No lines yet · R0.00`;

  // Sticky Save (confirmed Aug 2026, Vinyl Redesign: Real Usage
  // Findings brief §3) — "part of the summary panel, not a third
  // separate sticky element," per the brief's own suggested resolution
  // — calls the EXISTING saveQuote() directly, never a second save
  // path. Only offered once there's at least one real line — nothing
  // to usefully save before that. Real value named in the brief: a
  // simple, single-product quote no longer needs a scroll to the very
  // bottom just to finish.
  const saveButtonHtml = lineCount
    ? `<button class="primary" onclick="saveQuote()" style="width:100%; margin-top:10px;">Save Quote</button>`
    : '';
  panel.innerHTML = `
    <div class="qsp-compact" onclick="toggleQuoteSummaryExpanded()">
      <span class="qsp-compact-text">${compactText}</span>
      <span class="qsp-chevron">▲</span>
    </div>
    <div class="qsp-full">
      <div class="qsp-title">This Quote</div>
      ${rows}
      <div class="qsp-row qsp-total"><span>Total incl. VAT</span><span>R${totalInclVat.toFixed(2)}</span></div>
      ${saveButtonHtml}
    </div>`;
  positionQuoteSummaryPanel();
}

// Mobile only — desktop's .qsp-full is always visible (CSS), this
// toggle has no effect there. Click-driven, not scroll-driven — the
// header's own five-pass saga earlier today is exactly why that
// distinction is being made explicit in a comment here.
function toggleQuoteSummaryExpanded() {
  const panel = document.getElementById('quoteSummaryPanel');
  if (panel) panel.classList.toggle('expanded');
}

// Positions the desktop sticky sidebar just below the real, CURRENT
// header height — measured via JS rather than a hardcoded CSS value,
// since #appHeaderWrap's height genuinely varies (the Owner Preview
// banner adds to it when active) and the header is otherwise static in
// height now (Header Flicker: Static By Design, same day) — no
// scroll-linked repositioning here, only ever called from a render
// (loadQuote()) or a resize, never from a scroll listener.
function positionQuoteSummaryPanel() {
  const panel = document.getElementById('quoteSummaryPanel');
  const header = document.getElementById('appHeaderWrap');
  if (!panel || !header) return;
  // Desktop only (matches styles.css's own min-width:900px breakpoint
  // for the sticky sidebar) — the mobile layout uses position:fixed
  // with bottom:0 and no set height; leaving an inline `top` from a
  // previous desktop measurement would combine with that bottom:0 and
  // stretch the bar to fill the whole screen height instead of sitting
  // as a slim bar at the bottom. Cleared explicitly on mobile rather
  // than just never set, since a resize (e.g. rotating a tablet) can
  // cross the breakpoint in either direction after it was already set.
  panel.style.top = (window.innerWidth >= 900) ? (header.offsetHeight + 12) + 'px' : '';
}
window.addEventListener('resize', positionQuoteSummaryPanel);

// Live price preview for Blinds/Skirting/Trim/Stairwell (confirmed Aug
// 2026, Vinyl Quoting UX Redesign proposal §01/§10, Phase 4, approved)
// — "the core of what immediate confirmation actually means" (Burgert's
// own words). Deliberately NOT the same approach Flooring's own
// fjCalc() uses (a client-side JS reimplementation of the pricing
// formula, an accepted-but-real "shadow calculation" for that one
// category, already bitten once by a hardcoded VAT rate drifting from
// Business Settings) — this calls the new backend preview endpoint
// (main.py), the SAME calculate_blinds_line()/calculate_trim_line()/
// _compute_stairwell_calc() the real add/edit endpoints use. No
// preview for Misc — amount minus cost is plain arithmetic with no
// real formula behind it, already shown wherever those two fields are
// visible, no backend round-trip needed for it.
let genericPreviewDebounceTimer = null;
function scheduleGenericLinePreview() {
  clearTimeout(genericPreviewDebounceTimer);
  genericPreviewDebounceTimer = setTimeout(previewGenericLine, 300);
}
async function previewGenericLine() {
  const box = document.getElementById('genericLinePreview');
  if (!box) return;
  const cat = document.getElementById('line_category').value;
  const params = new URLSearchParams({ category: cat, role: currentRole() });
  let ready = false;
  if (cat === 'blinds') {
    const productId = document.getElementById('line_product').value;
    const width = document.getElementById('line_width').value;
    const drop = document.getElementById('line_drop').value;
    if (productId && width && drop) {
      params.set('product_id', productId);
      params.set('width_mm', width);
      params.set('drop_mm', drop);
      params.set('discount_pct', (parseFloat(document.getElementById('line_discount').value) || 0) / 100);
      ready = true;
    }
  } else if (cat === 'trim' || cat === 'skirting') {
    const productId = document.getElementById('line_product').value;
    const length = document.getElementById('line_length').value;
    if (productId && length) {
      params.set('product_id', productId);
      params.set('length_m', length);
      params.set('discount_pct', (parseFloat(document.getElementById('line_discount').value) || 0) / 100);
      ready = true;
    }
  } else if (cat === 'stairwell') {
    const vinylId = document.getElementById('line_stair_vinyl').value;
    const nosingId = document.getElementById('line_nosing_product').value;
    const numStairs = document.getElementById('line_num_stairs').value;
    if (vinylId && nosingId && numStairs) {
      params.set('vinyl_product_id', vinylId);
      params.set('nosing_product_id', nosingId);
      params.set('num_stairs', numStairs);
      params.set('stairwell_type', document.getElementById('line_stairwell_type').value);
      params.set('stair_area_m2', document.getElementById('line_stair_area').value || 0.45);
      params.set('own_staff', document.getElementById('line_stair_own_staff').value);
      params.set('landing_area_m2', recomputeLandingTotal());
      ready = true;
    }
  } else {
    box.style.display = 'none';   // Misc, or nothing selected yet
    return;
  }
  box.style.display = '';
  if (!ready) { box.innerHTML = '<span class="muted">Fill in the fields above to see a live price.</span>'; return; }
  box.innerHTML = '<span class="muted">Calculating…</span>';
  try {
    const res = await fetch(`${API}/quotes/lines/preview?${params}`);
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      box.innerHTML = `<span class="muted">${(body.detail || 'Could not calculate a preview.').replace(/</g, '&lt;')}</span>`;
      return;
    }
    const data = await res.json();
    let html = `<div style="display:flex; justify-content:space-between; font-weight:700;"><span>Price (ex VAT)</span><span>R${data.line_total.toFixed(2)}</span></div>`;
    if (data.margin_pct !== undefined) {
      html += `<div class="muted" style="font-size:12px; margin-top:2px;">Margin: ${(data.margin_pct * 100).toFixed(1)}%</div>`;
    }
    if (data.warning) {
      html += `<div style="color:var(--coral); font-size:12px; margin-top:4px; font-weight:600;">${data.warning.replace(/</g, '&lt;')}</div>`;
    }
    html += ownerBreakdownHtml(data);
    box.innerHTML = html;
  } catch (e) {
    box.innerHTML = '<span class="muted">Could not calculate a preview.</span>';
  }
}
// Delegated listener (same technique already established in this
// codebase — Keyboard Dismiss on Enter, 2026-08-25) rather than an
// oninput/onchange attribute on every individual field: genericLineCard
// is a static, permanent element, never re-created, so one listener
// attached once here covers every current AND future field inside it.
document.getElementById('genericLineCard').addEventListener('input', scheduleGenericLinePreview);
document.getElementById('genericLineCard').addEventListener('change', scheduleGenericLinePreview);

async function toggleLineFields() {
  const cat = document.getElementById('line_category').value;
  const isFlooring = cat === 'flooring';
  const isCarpet = cat === 'carpet';
  document.getElementById('fjMain').style.display = isFlooring ? '' : 'none';
  document.getElementById('carpetLineCard').style.display = isCarpet ? '' : 'none';
  document.getElementById('genericLineCard').style.display = (isFlooring || isCarpet) ? 'none' : '';
  if (isCarpet) {
    await loadFlooring();
    // Default to the first type (Stretch) on a fresh arrival at this tab
    // — selectCarpetType() itself is idempotent (safe to call again with
    // the same type), so re-entering this tab never loses whatever was
    // already chosen (activeCarpetType, module-level below).
    selectCarpetType(activeCarpetType || 'carpet_tufted_broadloom');
    syncActiveCategoryTab();
    renderQuoteSummaryPanel();
    return;
  }
  // Hardened Aug 2026, extended after a live bug found via staff-testing
  // the deployed app: the original fix here only re-fetched
  // flooringProducts (isFlooring branch below), assuming
  // blindsProducts/trimProducts were safe to read directly further
  // down — same unguarded-cache-read shape, just not yet caught. Blinds/
  // trim/skirting/stairwell all read straight from the cache via
  // refreshLineProductOptions() or the stairwell dropdowns below, with
  // no await — invisible against instant local SQLite, trivially easy
  // to hit against the real deployed backend on a Render free-tier cold
  // start (30-60+s to wake). Now awaits all three caches up front,
  // regardless of which category is selected, before anything below
  // reads any of them.
  await Promise.all([loadFlooring(), loadBlinds(), loadTrims(), loadFloorPrep()]);
  if (isFlooring) {
    populateFloorProductDropdowns();
    // Labour rate default from Business Settings — set once here, NOT
    // inside onVinylProductChange(), since labour rate is a genuinely
    // per-quote adjustable value (weekend labour, own-staff overrides).
    // Resetting it every time a colour changes would silently wipe out
    // a manual override already made for this specific job.
    if (businessSettings) {
      document.getElementById('fj_labour_rate').value = businessSettings.default_labour_rate_per_m2;
    }
    fjOnIncludeChange();
    return;
  }
  document.querySelectorAll('.blinds-field').forEach(el => el.style.display = cat === 'blinds' ? '' : 'none');
  document.querySelectorAll('.trim-field').forEach(el => el.style.display = (cat === 'trim' || cat === 'skirting') ? '' : 'none');
  document.querySelectorAll('.stairwell-field').forEach(el => el.style.display = cat === 'stairwell' ? '' : 'none');
  document.querySelectorAll('.misc-field').forEach(el => el.style.display = cat === 'misc' ? '' : 'none');
  document.getElementById('product_field').style.display = (cat === 'stairwell' || cat === 'misc') ? 'none' : '';
  if (cat === 'stairwell') {
    const stairVinyl = flooringProducts.filter(p => p.pricing_type === 'material' && p.tiles_per_pack);
    document.getElementById('line_stair_vinyl').innerHTML = stairVinyl.map(p => `<option value="${p.id}">${p.product_name}${p.colour ? ' — ' + p.colour : ''}</option>`).join('');
    document.getElementById('line_nosing_product').innerHTML = trimProducts.map(p => `<option value="${p.id}">${p.product_name}</option>`).join('');
  } else if (cat !== 'misc') {
    refreshLineProductOptions();
  }
  // Category tabs (confirmed Aug 2026, Vinyl Quoting UX Redesign
  // proposal §06, approved) — the flooring branch above syncs via
  // fjOnIncludeChange() instead (needs the vinyl/screed checkboxes,
  // not just the category), same reasoning as that call site's own
  // comment.
  syncActiveCategoryTab();
  renderQuoteSummaryPanel();   // refreshes the "→ in progress" row for whichever tab is now active — §03/§10, Phase 3
  previewGenericLine();   // Phase 4 — resets/refreshes the live preview for whichever category is now selected
}

// ===== CARPET (confirmed Aug 2026, Carpet Tab, Type Split, and Product
// Filtering brief) — Tufted/Needlepunch Broadloom, Cushion Vinyl (all
// three: LM input, calculate_carpet_line() on the backend) and NEXBAC
// 920 Tiles (m² input, the EXISTING unmodified Vinyl box endpoint —
// genuinely reuses that engine's shape, per the Carpet Calculators
// proposal's own instruction, not a fourth copy of anything). Each
// type's product dropdown is filtered to ONLY that type's real
// flooring_category — the fix for the confirmed pricing bug this brief
// exists for: a carpet product must never be reachable from any OTHER
// type's dropdown, and never falls back to Vinyl's box-based path.
let activeCarpetType = null;

const CARPET_TYPE_LABELS = {
  carpet_tufted_broadloom: 'Stretch (Tufted Broadloom)',
  carpet_needlepunch_broadloom: 'Glued Down (Needlepunch Broadloom)',
  cushion_vinyl: 'Cushion Vinyl',
  carpet_tile: 'NEXBAC 920 Tile',
};

function selectCarpetType(type, preselectRange) {
  activeCarpetType = type;
  document.querySelectorAll('.carpet-type-tab').forEach(btn => btn.classList.toggle('active', btn.dataset.carpetType === type));
  const isTile = type === 'carpet_tile';
  document.getElementById('carpet_lm_field').style.display = isTile ? 'none' : '';
  document.getElementById('carpet_m2_field').style.display = isTile ? '' : 'none';

  // Per-type extra fields — deliberately NOT a generic checkbox for
  // every possible toggle: cutting fee/grippers/underfelt/glue are
  // mandatory PARTS of what a type physically IS (Tufted always needs
  // grippers+underfelt; Needlepunch/Cushion Vinyl are glued down BY
  // DEFINITION, always needs adhesive) wherever the brief itself states
  // them as a requirement, not an option — a checkbox only appears
  // where the brief's own Final Build Brief left the rate genuinely
  // unconfirmed (Cushion Vinyl's own cutting fee; whether NEXBAC tiles
  // need adhesive at all), both defaulting OFF per that brief's explicit
  // instruction not to assume either way.
  const fieldsEl = document.getElementById('carpet_type_fields');
  if (type === 'carpet_tufted_broadloom') {
    fieldsEl.innerHTML = `<div class="field"><label>Gripper perimeter (m) <span class="adj">(room perimeter, NOT the same measurement as carpet LM above)</span></label><input id="carpet_gripper_perimeter" type="number" step="0.1" value="0" oninput="scheduleCarpetPreview()"></div>`;
  } else if (type === 'carpet_needlepunch_broadloom') {
    fieldsEl.innerHTML = `<p class="muted" style="margin:0;">Cutting fee and adhesive are applied automatically — both confirmed, always part of a glued-down Needlepunch job.</p>`;
  } else if (type === 'cushion_vinyl') {
    fieldsEl.innerHTML = `<div class="field"><label style="font-weight:600;"><input type="checkbox" id="carpet_apply_cutting_fee" style="width:auto; margin-right:6px;" onchange="scheduleCarpetPreview()"> Apply cutting fee <span class="adj">(unconfirmed for Cushion Vinyl — off by default; adhesive is applied automatically)</span></label></div>`;
  } else if (type === 'carpet_tile') {
    fieldsEl.innerHTML = `<div class="field"><label style="font-weight:600;"><input type="checkbox" id="carpet_apply_glue" style="width:auto; margin-right:6px;" onchange="scheduleCarpetPreview()"> Apply adhesive <span class="adj">(unconfirmed whether NEXBAC 920 needs it — off by default)</span></label></div>`;
  }

  populateCarpetTypeProducts(type, preselectRange);
}

function populateCarpetTypeProducts(type, preselectRange) {
  const products = sortByPriority(flooringProducts.filter(p => p.flooring_category === type));
  const sel = document.getElementById('carpet_product');
  if (!products.length) {
    sel.innerHTML = `<option value="">No ${CARPET_TYPE_LABELS[type]} products in price book</option>`;
    return;
  }
  sel.innerHTML = products.map(p => `<option value="${p.id}" ${p.product_name === preselectRange ? 'selected' : ''}>${p.product_name}${p.colour ? ' — ' + p.colour : ''}</option>`).join('');
  scheduleCarpetPreview();
}

let carpetPreviewDebounceTimer = null;
function scheduleCarpetPreview() {
  clearTimeout(carpetPreviewDebounceTimer);
  carpetPreviewDebounceTimer = setTimeout(previewCarpetLine, 300);
}

function carpetApplyFlags(type) {
  // Single source of truth for which extras apply to this type — read
  // by both the live preview and the real Add Line call, so the two can
  // never disagree about what a given type actually includes.
  if (type === 'carpet_tufted_broadloom') {
    return { cutting_fee: true, grippers: true, underfelt: true, glue: false };
  } else if (type === 'carpet_needlepunch_broadloom') {
    return { cutting_fee: true, grippers: false, underfelt: false, glue: true };
  } else if (type === 'cushion_vinyl') {
    const cb = document.getElementById('carpet_apply_cutting_fee');
    return { cutting_fee: !!(cb && cb.checked), grippers: false, underfelt: false, glue: true };
  }
  return { cutting_fee: false, grippers: false, underfelt: false, glue: false };   // carpet_tile handled separately (existing Vinyl endpoint, its own glue toggle)
}

async function previewCarpetLine() {
  const box = document.getElementById('carpetLinePreview');
  const type = activeCarpetType;
  const productId = document.getElementById('carpet_product').value;
  if (!type || !productId) { box.style.display = 'none'; return; }
  const discount = (parseFloat(document.getElementById('carpet_discount').value) || 0) / 100;
  const role = currentRole();

  if (type === 'carpet_tile') {
    // No backend preview branch for plain Vinyl-shaped material lines —
    // Flooring itself has never needed one (its own client-side fjCalc()
    // covers that ground already) — kept consistent rather than adding
    // a bespoke preview path for one product type. Add Line remains the
    // way to see the real, saved number for a tile line.
    box.style.display = 'none';
    return;
  }

  const lm = parseFloat(document.getElementById('carpet_lm').value);
  if (!lm || lm <= 0) { box.style.display = 'none'; return; }
  const flags = carpetApplyFlags(type);
  const params = new URLSearchParams({
    category: 'carpet', product_id: productId, quantity_lm: lm, discount_pct: discount, role,
    apply_cutting_fee: flags.cutting_fee, apply_glue: flags.glue,
  });
  if (flags.grippers) {
    params.set('apply_grippers', true);
    params.set('gripper_perimeter_m', parseFloat(document.getElementById('carpet_gripper_perimeter')?.value) || 0);
  }
  if (flags.underfelt) { params.set('apply_underfelt', true); }
  try {
    const res = await fetch(`${API}/quotes/lines/preview?${params}`);
    if (!res.ok) { box.style.display = 'none'; return; }
    const calc = await res.json();
    box.style.display = '';
    box.innerHTML = `
      <div class="fj-line result"><span>Price (ex VAT)</span><span>R${calc.line_total.toFixed(2)}</span></div>
      ${calc.margin_pct !== undefined ? `<div class="muted" style="font-size:12px;">Margin: ${(calc.margin_pct*100).toFixed(1)}%</div>` : ''}
      ${calc.warning ? `<div class="muted" style="color:var(--coral); font-size:12px; margin-top:4px;">${calc.warning}</div>` : ''}
      ${ownerBreakdownHtml(calc)}
    `;
  } catch (e) { box.style.display = 'none'; }
}

async function addCarpetLine() {
  const type = activeCarpetType;
  const productId = document.getElementById('carpet_product').value;
  if (!productId) { alert('Pick a product first.'); return; }
  const discount = (parseFloat(document.getElementById('carpet_discount').value) || 0) / 100;
  const role = currentRole();
  // Edit Quote Line In Place (confirmed Aug 2026) — same "PUT to the
  // same line id" pattern every other category's own add function
  // already uses (addLine(), addFloorJob() above) — Carpet gets its own
  // check here rather than routing through the shared addLine(), same
  // reasoning addFloorJob() has its own dedicated card/flow instead of
  // reusing #genericLineCard.
  const editing = !!editingLineId;

  if (type === 'carpet_tile') {
    const m2 = parseFloat(document.getElementById('carpet_m2').value);
    if (!m2 || m2 <= 0) { alert('Enter a real area in m² first.'); return; }
    const applyGlueEl = document.getElementById('carpet_apply_glue');
    const params = new URLSearchParams({ product_id: productId, quantity_m2: m2, job_type: 'smooth', discount_pct: discount, role });
    if (applyGlueEl && applyGlueEl.checked) {
      params.set('glue_cost_per_unit', businessSettings.carpet_glue_cost_per_20l_drum);
      params.set('glue_coverage_m2', businessSettings.stairwell_default_glue_coverage_m2);
    }
    const url = editing
      ? `${API}/quotes/${currentQuoteId}/lines/${editingLineId}/flooring?${params}`
      : `${API}/quotes/${currentQuoteId}/lines/flooring?${params}`;
    const res = await fetch(url, { method: editing ? 'PUT' : 'POST' });
    if (!res.ok) { alert('Could not save this line — check your connection and try again.'); return; }
    document.getElementById('carpet_m2').value = '';
    if (editing) cancelLineEdit();
    loadQuote();
    return;
  }

  const lm = parseFloat(document.getElementById('carpet_lm').value);
  if (!lm || lm <= 0) { alert('Enter a real length in LM first.'); return; }
  const flags = carpetApplyFlags(type);
  const params = new URLSearchParams({
    product_id: productId, quantity_lm: lm, carpet_category: type, discount_pct: discount, role,
    apply_cutting_fee: flags.cutting_fee, apply_glue: flags.glue,
  });
  if (flags.grippers) {
    params.set('apply_grippers', true);
    params.set('gripper_perimeter_m', parseFloat(document.getElementById('carpet_gripper_perimeter')?.value) || 0);
  }
  if (flags.underfelt) { params.set('apply_underfelt', true); }
  const url = editing
    ? `${API}/quotes/${currentQuoteId}/lines/${editingLineId}/carpet?${params}`
    : `${API}/quotes/${currentQuoteId}/lines/carpet?${params}`;
  const res = await fetch(url, { method: editing ? 'PUT' : 'POST' });
  if (!res.ok) { alert('Could not save this line — check your connection and try again.'); return; }
  document.getElementById('carpet_lm').value = '';
  if (editing) cancelLineEdit();
  loadQuote();
}

function prefillCarpetEdit(line) {
  selectCarpetType(line.carpet_category);
  document.getElementById('carpet_product').value = line.product_id;
  if (line.carpet_category === 'carpet_tile') {
    document.getElementById('carpet_m2').value = line.quantity_m2 || '';
    const glueEl = document.getElementById('carpet_apply_glue');
    if (glueEl) glueEl.checked = (line.glue_cost_total || 0) > 0;
  } else {
    document.getElementById('carpet_lm').value = line.quantity_lm || '';
    if (line.carpet_category === 'carpet_tufted_broadloom') {
      document.getElementById('carpet_gripper_perimeter').value = line.gripper_perimeter_m || 0;
    } else if (line.carpet_category === 'cushion_vinyl') {
      const cbEl = document.getElementById('carpet_apply_cutting_fee');
      // cutting_fee_total is stripped for Sales — can't recover the
      // toggle's real state for that role, same "not recoverable" class
      // as Stairwell's own stair_area_m2 (see editQuoteLine()'s comment
      // on that). Owner/Admin see the real figure and can tell.
      if (cbEl && line.cutting_fee_total !== undefined) cbEl.checked = line.cutting_fee_total > 0;
    }
  }
  document.getElementById('carpet_discount').value = ((line.discount_pct || 0) * 100);
  scheduleCarpetPreview();
}

function populateFloorProductDropdowns() {
  const screedProducts = sortByPriority(flooringProducts.filter(p => p.pricing_type === 'screed'));
  const screedSelect = document.getElementById('fj_screed_product');
  const screedCheckbox = document.getElementById('fj_include_screed');
  populateVinylRangeDropdown();
  screedSelect.innerHTML = screedProducts.length
    ? screedProducts.map(p => `<option value="${p.id}">${p.product_name}</option>`).join('')
    : '';
  document.getElementById('screedEmptyNote').style.display = screedProducts.length ? 'none' : '';
  // If there's genuinely no screed product to select, don't leave the
  // checkbox showing "included" with nothing behind it — that looked like
  // a broken toggle. Auto-uncheck and disable until a product exists.
  screedCheckbox.disabled = !screedProducts.length;
  if (!screedProducts.length) { screedCheckbox.checked = false; }
  onScreedProductChange();
  fjOnIncludeChange();
}

// ===== Extra Rooms / Floor Prep (confirmed Aug 2026, Screed Calculator:
// Extra Rooms brief) — separate from the main Vinyl/Screed floor job
// above: any number of these can be added to one quote, each becoming
// its own "misc" quote line (reuses the existing, already-correct
// /quotes/{id}/lines/misc endpoint — no new line-creation endpoint
// needed, same "don't build a parallel calc/save path" discipline this
// project has followed all session). Calculated mode implements the
// brief's own Section 3 formula, verified against its Section 6
// reference case (16m², 10mm, LEVELiTe F10 -> 224.00kg, 12 bags, 4.00L
// BONDiTe, 1×25L drum OR 1×5L bottle) — see fpCalc() below.
let floorPrepProducts = [];

async function loadFloorPrep() {
  const res = await fetch(`${API}/price-book/floor-prep`);
  floorPrepProducts = await res.json();
  populateFloorPrepDropdowns();
}

function populateFloorPrepDropdowns() {
  // Only the two coverage_basis shapes the brief's Section 3 formula
  // actually covers get a dropdown here (kg/m²/mm compounds, m²/L
  // bonding agents) — the other reference products (iTe SLURRY,
  // VAPORiTe, GRIPiTe H80) live in the Supplier Console as reference
  // data per Section 5 but aren't wired into a calculator formula of
  // their own by this brief.
  const compounds = floorPrepProducts.filter(p => p.coverage_basis === 'kg_per_m2_per_mm');
  const bondingAgents = floorPrepProducts.filter(p => p.coverage_basis === 'm2_per_L');
  const compoundSelect = document.getElementById('fp_compound_product');
  const bondingSelect = document.getElementById('fp_bonding_product');
  if (!compoundSelect || !bondingSelect) return;   // card not in the DOM yet on first script load
  compoundSelect.innerHTML = compounds.length
    ? compounds.map(p => `<option value="${p.id}">${p.product_name} (${p.pack_size}${p.pack_unit})</option>`).join('')
    : '<option value="">No levelling/patching compound in the price book yet</option>';
  bondingSelect.innerHTML = bondingAgents.length
    ? bondingAgents.map(p => `<option value="${p.id}">${p.product_name} (${p.pack_size}${p.pack_unit})</option>`).join('')
    : '<option value="">No bonding agent in the price book yet</option>';
  fpCalc();
}

function fpOnModeChange() {
  const mode = document.querySelector('input[name="fp_mode"]:checked').value;
  document.getElementById('fpCalculatedFields').style.display = mode === 'calculated' ? '' : 'none';
  document.getElementById('fpManualFields').style.display = mode === 'manual' ? '' : 'none';
}

// Live calculation, Section 3's formula exactly:
//   compound kg = area x thickness x coverage_rate (kg_per_m2_per_mm)
//   bags = ROUND UP(kg / pack_size) — always up, never fractional
//   bonding L = area / coverage_rate (m2_per_L), conservative (lower) rate
//   containers = ROUND UP(L / pack_size), shown per available pack size
//     as alternatives (e.g. one row per size Bolton has for that product)
// Extra Screed auto-pricing (confirmed Aug 2026, Vinyl Redesign: Real
// Usage Findings brief §2) — REAL GAP CLOSED: "Amount charged"/"Real
// cost" sat at 0 requiring manual entry even once Bags needed was
// already known. Real cost is deliberately left alone — the brief only
// gives a confirmed sell rate (R350 incl VAT/bag), not a real-cost
// figure, so nothing is guessed at there. fpAmountManuallyEdited is
// reset in addFloorPrepLine()'s own per-room reset block — a fresh
// room always gets the real default again, same "default, not forced,
// per room" pattern the Courier toggle already uses elsewhere in this
// file.
let fpAmountManuallyEdited = false;
function fpCalc() {
  const area = parseFloat(document.getElementById('fp_area').value) || 0;
  const thickness = parseFloat(document.getElementById('fp_thickness').value) || 0;
  const compound = floorPrepProducts.find(p => p.id == document.getElementById('fp_compound_product').value);
  const bonding = floorPrepProducts.find(p => p.id == document.getElementById('fp_bonding_product').value);

  const compoundKg = (compound && area && thickness) ? area * thickness * compound.coverage_rate : 0;
  const bags = (compound && compoundKg > 0) ? Math.ceil(compoundKg / compound.pack_size) : 0;
  if (bags > 0 && !fpAmountManuallyEdited) {
    const sellRateInclVat = 350;   // confirmed Aug 2026 — Burgert's own real sell rate for loose/extra screed bags
    const vatPct = businessSettings?.vat_pct ?? 0.15;
    document.getElementById('fp_amount').value = ((bags * sellRateInclVat) / (1 + vatPct)).toFixed(2);
  }
  const bondingL = (bonding && area) ? area / bonding.coverage_rate : 0;
  // Every pack size Bolton has on file for this exact bonding agent NAME
  // (e.g. both "BONDiTe (5L)" and "BONDiTe (25L)" rows) is shown as its
  // own alternative — matches the reference calculator's dual-option
  // format (Section 3: "shown for each available container size as an
  // alternative").
  // Largest pack first, matching the brief's own reference example
  // wording ("1×25L drum OR 1×5L bottle") — purely cosmetic ordering,
  // both options are always shown regardless.
  const bondingSiblings = bonding
    ? floorPrepProducts.filter(p => p.product_name.replace(/\s*\([^)]*\)\s*$/, '') === bonding.product_name.replace(/\s*\([^)]*\)\s*$/, '') && p.coverage_basis === 'm2_per_L').sort((a, b) => b.pack_size - a.pack_size)
    : [];
  const containerOptions = (bondingL > 0 ? bondingSiblings : []).map(p => `${Math.ceil(bondingL / p.pack_size)} × ${p.pack_size}${p.pack_unit}`);

  document.getElementById('fp_out_compound_kg').textContent = compound ? `${compoundKg.toFixed(2)} kg` : '—';
  document.getElementById('fp_out_bags').textContent = compound ? `${bags} bag${bags !== 1 ? 's' : ''} of ${compound.product_name}` : '—';
  document.getElementById('fp_out_bonding_l').textContent = bonding ? `${bondingL.toFixed(2)} L` : '—';
  document.getElementById('fp_out_containers').textContent = containerOptions.length ? containerOptions.join(' OR ') : '—';

  // Confirmed Aug 2026, Extra Rooms / Floor Prep Collapsible brief,
  // Section 1/3 — the room name is the HEADLINE (what a client sees,
  // what's scannable collapsed), the quantities are the detail that
  // follows. fp_technical_detail holds just the quantities part on its
  // own (no room name) — used by addFloorPrepLine() to require it's
  // non-empty before allowing a save, separately from the room name
  // requirement.
  const technicalDetail = (area && thickness && compound)
    ? `${area}m² × ${thickness}mm ${compound.product_name} — ${compoundKg.toFixed(2)}kg (${bags} bag${bags !== 1 ? 's' : ''})${bonding && bondingL > 0 ? `, ${bondingL.toFixed(2)}L ${bonding.product_name.replace(/\s*\([^)]*\)\s*$/, '')} (${containerOptions.join(' OR ')})` : ''}`
    : '';
  const descEl = document.getElementById('fp_calc_description');
  if (descEl) descEl.textContent = technicalDetail;
  return technicalDetail;
}

async function addFloorPrepLine() {
  if (!currentQuoteId) { alert('Start a quote first.'); return; }
  const roomName = document.getElementById('fp_room_name').value.trim();
  if (!roomName) { alert('Enter a room name / description first — this is what shows on the printed quote for this line.'); return; }
  const mode = document.querySelector('input[name="fp_mode"]:checked').value;
  // Confirmed Aug 2026 (brief Section 1) — room name is always the
  // headline; the technical detail (calculated mode) or free-text
  // extra detail (manual mode) follows after an em-dash, matching the
  // brief's own reference example format exactly: "Guest Bathroom —
  // 224.00kg LEVELiTe F10 (12 bags)...".
  let description;
  if (mode === 'calculated') {
    const technicalDetail = fpCalc();
    if (!technicalDetail) { alert('Fill in area, thickness, and pick a compound product first.'); return; }
    description = `${roomName} — ${technicalDetail}`;
  } else {
    const extra = document.getElementById('fp_manual_desc').value.trim();
    description = extra ? `${roomName} — ${extra}` : roomName;
  }
  const amount = parseFloat(document.getElementById('fp_amount').value) || 0;
  const cost = parseFloat(document.getElementById('fp_cost').value) || 0;
  if (!confirmPostAcceptChange('adding this line')) return;
  const params = new URLSearchParams({ description, amount_ex_vat: amount, cost_ex_vat: cost, source_feature: 'floor_prep', role: currentRole() });
  const res = await fetch(`${API}/quotes/${currentQuoteId}/lines/misc?${params}`, { method: 'POST' });
  const line = await res.json();
  if (line.warning) alert(line.warning);
  // Reset for the next room, per the brief's own "a quote can contain
  // multiple extra rooms" requirement — deliberately does NOT reset the
  // compound/bonding product choice (often the same product across
  // several rooms on one job), only the per-room name/quantities/amounts.
  document.getElementById('fp_room_name').value = '';
  document.getElementById('fp_area').value = '';
  document.getElementById('fp_thickness').value = '';
  document.getElementById('fp_manual_desc').value = '';
  document.getElementById('fp_amount').value = 0;
  document.getElementById('fp_cost').value = 0;
  fpAmountManuallyEdited = false;   // a fresh room gets the real R350/bag default again
  fpCalc();
  loadQuote();
}

// Collapsible room cards (confirmed Aug 2026, Extra Rooms / Floor Prep
// Collapsible brief) — each already-added floor-prep line gets its own
// card, collapsed by default, nested inside floorPrepCard (not a
// separate section). Sourced from currentQuoteLinesCache (already
// loaded by loadQuote() below) filtered by source_feature — the ONLY
// reliable way to tell an Extra Room line apart from any other
// freeform misc line (e.g. "extra Saturday labour"), category alone
// can't distinguish them. The collapsed header IS the full description
// text (room name + quantities, per Section 1/3 — both already baked
// into product_name by addFloorPrepLine() above), so "visible
// quantities without expanding" needs no separate summary field to
// keep in sync — expanding only reveals the Edit/Delete actions,
// reusing Sprint B's existing editQuoteLine()/deleteQuoteLine() rather
// than a second edit path.
function renderFloorPrepRoomCards() {
  const container = document.getElementById('floorPrepRoomCards');
  if (!container) return;
  const rooms = currentQuoteLinesCache.filter(l => l.category === 'misc' && l.source_feature === 'floor_prep');
  container.innerHTML = rooms.length ? rooms.map(l => `
    <div style="border:1px solid var(--border); border-radius:6px; margin-bottom:6px; overflow:hidden;">
      <div onclick="toggleFloorPrepRoomCard(this)" style="display:flex; justify-content:space-between; align-items:center; gap:10px; padding:10px 12px; cursor:pointer; background:var(--card);">
        <span style="font-size:13px;"><span class="fp-caret" style="display:inline-block; width:14px;">▶</span>${l.product_name}</span>
        <b style="font-size:13px; white-space:nowrap;">R${l.line_total.toFixed(2)}</b>
      </div>
      <div style="display:none; padding:8px 12px 12px 26px; border-top:1px solid var(--border);">
        <button onclick="editQuoteLine(${l.id})" style="font-size:11px; margin-right:6px;">Edit</button>
        <button class="delete-btn" onclick="deleteQuoteLine(${l.id})" style="font-size:11px;">Delete</button>
      </div>
    </div>`).join('')
    : '<p class="muted" style="font-size:12px;">No extra rooms added yet.</p>';
}

function toggleFloorPrepRoomCard(headerEl) {
  const body = headerEl.nextElementSibling;
  const collapsed = body.style.display === 'none';
  body.style.display = collapsed ? '' : 'none';
  headerEl.querySelector('.fp-caret').textContent = collapsed ? '▼' : '▶';
}

// Two-step selection (confirmed Aug 2026): pick a Range first, then a
// Colour within it — the colour list depends on which range is chosen,
// since each range has its own set of colour-specific price book entries.
function populateVinylRangeDropdown(preselectRange) {
  const vinylProducts = flooringProducts.filter(p => p.pricing_type === 'material' && !CARPET_ONLY_CATEGORIES.includes(p.flooring_category));
  const rangesByPriority = {};
  vinylProducts.forEach(p => { if (!(p.product_name in rangesByPriority)) rangesByPriority[p.product_name] = p.display_order ?? 100; });
  const ranges = Object.keys(rangesByPriority).sort((a, b) => rangesByPriority[a] - rangesByPriority[b] || a.localeCompare(b));
  const rangeSelect = document.getElementById('fj_vinyl_range');
  if (!ranges.length) {
    // Safety net, not just a silent empty dropdown — if this ever shows,
    // it's a real data problem (no vinyl products loaded), not a hidden bug.
    rangeSelect.innerHTML = '<option value="">No vinyl products in price book</option>';
    document.getElementById('fj_vinyl_colour').innerHTML = '';
    return;
  }
  // Discontinued (confirmed Aug 2026, Master Sheet System of Record
  // brief): still fully selectable — the brief is explicit that this
  // must never be hidden or blocked here — just visibly labelled so
  // whoever's building the quote can see it and decide, same "warning
  // flag, not a soft-delete" spirit as the Supplier Console's own badge.
  // A whole RANGE is only marked here if every colour under it is
  // discontinued (a range with a live colour left isn't discontinued
  // itself).
  const rangeAllDiscontinued = r => vinylProducts.filter(p => p.product_name === r).every(p => p.discontinued);
  rangeSelect.innerHTML = ranges.map(r => `<option value="${r}" ${r===preselectRange?'selected':''}>${r}${rangeAllDiscontinued(r) ? ' (Discontinued)' : ''}</option>`).join('');
  onVinylRangeChange();
}

function onVinylRangeChange() {
  const range = document.getElementById('fj_vinyl_range').value;
  const colours = sortByPriority(flooringProducts.filter(p => p.pricing_type === 'material' && !CARPET_ONLY_CATEGORIES.includes(p.flooring_category) && p.product_name === range));
  const colourSelect = document.getElementById('fj_vinyl_colour');
  colourSelect.innerHTML = colours.map(p => `<option value="${p.id}">${p.colour || '(no colour set)'}${p.discontinued ? ' (Discontinued)' : ''}</option>`).join('');
  onVinylColourChange();
}

function onVinylColourChange() {
  const productId = document.getElementById('fj_vinyl_colour').value;
  document.getElementById('fj_vinyl_product').value = productId;
  onVinylProductChange();
}

function fjOnIncludeChange() {
  const includeVinyl = document.getElementById('fj_include_vinyl').checked;
  const includeScreed = document.getElementById('fj_include_screed').checked;
  document.getElementById('vinylCard').style.display = includeVinyl ? '' : 'none';
  document.getElementById('screedCard').style.display = includeScreed ? '' : 'none';
  // Bring Vinyl/Screed to Carpet's Add Line Flow (confirmed Aug 2026) —
  // same "the heading names the one thing this step adds" clarity
  // Carpet's own per-type card already has (CARPET_TYPE_LABELS) — since
  // includeVinyl/includeScreed are mutually exclusive now (see
  // selectLineCategoryTab()'s own comment), this always names exactly
  // one real thing, never "Floor Job" (a combined submission that no
  // longer exists). The single shared "Add Line" button this used to
  // also relabel is gone (Auto-Add Screed for Vinyl / commit-boundary
  // fix, confirmed Aug 2026) — vinylCard and screedCard each have their
  // own dedicated, statically-labelled button right after their own
  // fields now, so there's nothing left to relabel here.
  const floorJobTitleEl = document.getElementById('floorJobCardTitle');
  if (floorJobTitleEl) floorJobTitleEl.textContent = includeScreed ? 'Screed' : 'Vinyl';
  // Category tabs (confirmed Aug 2026, Vinyl Quoting UX Redesign
  // proposal §06, approved) — the Flooring/Screed tab highlight tracks
  // these same two checkboxes (the real state), so a direct checkbox
  // toggle (not just a tab click) keeps the visible tab honest too.
  syncActiveCategoryTab();
  renderQuoteSummaryPanel();   // same refresh as toggleLineFields()'s non-flooring path — §03/§10, Phase 3
  fjCalc();
}

// Selecting a product auto-fills its real price book numbers into the
// (still editable) fields — no more retyping box price/discount by hand.
function onVinylProductChange() {
  const id = document.getElementById('fj_vinyl_product').value;
  const p = flooringProducts.find(x => x.id == id);
  if (!p) return;
  document.getElementById('fj_wastage').value = (p.wastage_pct * 100).toFixed(1);
  document.getElementById('fj_m2_per_box').value = p.m2_per_pack || '';
  // Azura zone pricing (confirmed Aug 2026): a product with zone prices
  // set uses whichever zone matches BusinessSettings.pricing_zone as
  // its EFFECTIVE price — same resolution the backend applies at
  // quote-calc time (resolve_zone_price in main.py). Pre-filling the
  // raw base_cost_ex_vat here would show the wrong number in this
  // live preview for Azura products and disagree with what the real
  // saved quote line actually charges.
  const zoneField = `price_zone_${(businessSettings?.pricing_zone || 'A').toLowerCase()}`;
  const zonePrice = p[zoneField];
  document.getElementById('fj_box_price').value = (zonePrice !== null && zonePrice !== undefined) ? zonePrice : p.base_cost_ex_vat;
  document.getElementById('fj_trade_discount').value = (p.trade_discount_pct * 100).toFixed(1);
  document.getElementById('fj_markup').value = (((p.sell_markup_multiplier || 1) - 1) * 100).toFixed(1);
  // Supplier Console per-product defaults (confirmed Aug 2026) —
  // pre-fill glue rate/labour rate/labour source from this product's
  // own stored defaults when set, same pre-fill-not-mandate pattern as
  // everything else here: still fully overridable per quote, and falls
  // back to whatever was already in the field (e.g. Business Settings'
  // default labour rate) if this product has no override of its own.
  if (p.glue_rate_per_m2 !== null && p.glue_rate_per_m2 !== undefined) {
    document.getElementById('fj_glue_rate').value = p.glue_rate_per_m2;
  }
  if (p.labour_rate_per_m2 !== null && p.labour_rate_per_m2 !== undefined) {
    document.getElementById('fj_labour_rate').value = p.labour_rate_per_m2;
  }
  document.getElementById('fj_own_staff').value = (p.default_own_staff === false) ? 'false' : 'true';
  // Courier toggle default (confirmed Aug 2026, Transport/Courier Toggle
  // Relocation brief, Section 4) — "Default state follows the product's/
  // supplier's existing setting" — re-defaults every time a different
  // colour/product is picked (not sticky across a product change), since
  // a different product can have a completely different courier rate/
  // applicability. Still freely overridable per room afterward.
  const courierToggle = document.getElementById('fj_courier_toggle');
  if (courierToggle) courierToggle.checked = !!(p.delivery_fee_per_m2 && p.delivery_fee_per_m2 > 0);
  fjCalc();
}

function onScreedProductChange() {
  const id = document.getElementById('fj_screed_product').value;
  const p = flooringProducts.find(x => x.id == id);
  if (!p) {
    document.getElementById('fj_screed_rate').value = '';
    fjCalc();
    return;
  }
  applyScreedRateForJobType();
}

function applyScreedRateForJobType() {
  const id = document.getElementById('fj_screed_product').value;
  const p = flooringProducts.find(x => x.id == id);
  if (!p) return;
  const jt = document.getElementById('fj_screed_jobtype').value;
  const mult = jt === 'smooth' ? 1 : (jt === 'over_tiles' ? (p.over_tiles_multiplier || 1.5) : (p.removed_tiles_multiplier || 2.0));
  document.getElementById('fj_screed_rate').value = (p.base_cost_ex_vat * mult).toFixed(2);
  // Sensible default, not a hard rule — Removed Tiles jobs usually want
  // the tile removal fee, but it's still a manual checkbox you can
  // override either way, since the two aren't strictly tied together.
  document.getElementById('fj_tile_removal').checked = (jt === 'removed_tiles');
  fjCalc();
}
document.getElementById('fj_screed_jobtype').addEventListener('change', applyScreedRateForJobType);

// "Material only" toggle (confirmed: a client can just buy the vinyl —
// box cost x markup only, no glue or labour). Disables and zeroes the
// glue/labour inputs rather than hiding them, so the numbers actually used
// are visible; remembers the prior values in dataset so toggling back off
// restores them instead of losing what was typed.
function fjOnMaterialOnlyChange() {
  const materialOnly = document.getElementById('fj_material_only').checked;
  const glueInput = document.getElementById('fj_glue_rate');
  const labourInput = document.getElementById('fj_labour_rate');
  const labourSourceSelect = document.getElementById('fj_own_staff');
  if (materialOnly) {
    glueInput.dataset.prevValue = glueInput.value;
    labourInput.dataset.prevValue = labourInput.value;
    glueInput.value = 0;
    labourInput.value = 0;
  } else {
    glueInput.value = glueInput.dataset.prevValue || '17.05';
    labourInput.value = labourInput.dataset.prevValue || '45';
  }
  glueInput.disabled = materialOnly;
  labourInput.disabled = materialOnly;
  labourSourceSelect.disabled = materialOnly;   // no labour charged at all, so "who does it" is moot
  document.getElementById('materialOnlyNote').style.display = materialOnly ? '' : 'none';
  fjCalc();
}

// Live local calculation — identical formula to the confirmed calculator
// and to the real backend (verified match, Aug 2026). This gives instant
// feedback as you type; "Add Floor Job to Quote" is what actually saves
// it via the real API.
function fjCalc() {
  const floor_m2 = parseFloat(document.getElementById('fj_floor_m2').value) || 0;
  const wastage = parseFloat(document.getElementById('fj_wastage').value) / 100 || 0;
  const m2_per_box = parseFloat(document.getElementById('fj_m2_per_box').value) || 1;
  const box_price = parseFloat(document.getElementById('fj_box_price').value) || 0;
  const trade_discount = parseFloat(document.getElementById('fj_trade_discount').value) / 100 || 0;
  const glue_rate = parseFloat(document.getElementById('fj_glue_rate').value) || 0;
  const markup = parseFloat(document.getElementById('fj_markup').value) / 100 || 0;
  const labour_rate = parseFloat(document.getElementById('fj_labour_rate').value) || 0;
  const screed_rate = parseFloat(document.getElementById('fj_screed_rate').value) || 0;
  const bag_cost = parseFloat(document.getElementById('fj_bag_cost').value) || 0;
  const bag_coverage = parseFloat(document.getElementById('fj_bag_coverage').value) || 1;
  const discount_pct = parseFloat(document.getElementById('fj_discount_pct').value) / 100 || 0;
  // Real bug found in v55: this live preview calculator was hardcoded at
  // 15%, entirely separate from Business Settings — a VAT change would
  // have made this preview disagree with the actual saved/printed quote,
  // same class of bug as the on-screen total fixed in v54 (that one
  // used the backend's real total_incl_vat; this one has no backend
  // round-trip yet since it's a live-as-you-type preview, so it reads
  // the cached setting directly instead).
  const vat = businessSettings?.vat_pct ?? 0.15;
  const includeVinyl = document.getElementById('fj_include_vinyl').checked;
  const includeScreed = document.getElementById('fj_include_screed').checked;
  const includeTileRemoval = document.getElementById('fj_tile_removal').checked;
  const materialOnly = document.getElementById('fj_material_only').checked;   // client buys the vinyl alone — box cost x markup only, no glue or labour
  // Confirmed R45/m² INCL VAT — stored as its ex-VAT equivalent here too,
  // matching the backend, so the normal "VAT applied once at the end" flow
  // still lands on exactly R45 incl VAT to the client.
  const tile_removal_rate_ex_vat = 45.0 / 1.15;

  // Courier/delivery fee preview (confirmed Aug 2026, Transport/Courier
  // Toggle Relocation brief) — same treatment as the real backend
  // (calculate_flooring_line, calculations.py): bundled into the
  // pre-markup subtotal alongside boxes+glue, so it IS marked up here
  // too, matching exactly what "Add Floor Job to Quote" will actually
  // save. Reads the rate from the currently-selected product, gated by
  // the Courier toggle — the toggle controls THIS job only, the
  // product's own stored rate is never touched by this preview.
  const courierOn = document.getElementById('fj_courier_toggle')?.checked ?? false;
  const selectedVinylProduct = (typeof flooringProducts !== 'undefined') ? flooringProducts.find(x => x.id == document.getElementById('fj_vinyl_product').value) : null;
  const delivery_fee_rate = (courierOn && selectedVinylProduct && selectedVinylProduct.delivery_fee_per_m2) ? selectedVinylProduct.delivery_fee_per_m2 : 0;

  const m2_needed = floor_m2 * (1 + wastage);
  const boxes = includeVinyl ? Math.ceil(m2_needed / m2_per_box) : 0;
  const list_box_price = box_price * m2_per_box; // confirmed Aug 2026: the real box price, shown explicitly so "222" is never mistaken for a box price — it's the per-m2 rate the box price is derived from
  const net_box_price = box_price * (1 - trade_discount) * m2_per_box; // per-box cost, converted from the per-m2 price book rate
  const box_total = includeVinyl ? boxes * net_box_price : 0;
  const glue_total = (includeVinyl && !materialOnly) ? floor_m2 * glue_rate : 0;
  const delivery_fee_total = includeVinyl ? floor_m2 * delivery_fee_rate : 0;
  const subtotal = box_total + glue_total + delivery_fee_total;
  const marked_up = subtotal * (1 + markup);
  const labour_total = (includeVinyl && !materialOnly) ? floor_m2 * labour_rate : 0;
  const vinyl_ex = includeVinyl ? marked_up + labour_total : 0;
  const vinyl_incl = vinyl_ex * (1 + vat);

  const bags = includeScreed ? Math.ceil(floor_m2 / bag_coverage) : 0;
  const screed_cost_total = includeScreed ? bags * bag_cost : 0;
  const tile_removal_total = (includeScreed && includeTileRemoval) ? floor_m2 * tile_removal_rate_ex_vat : 0;
  const screed_ex = includeScreed ? (floor_m2 * screed_rate) + tile_removal_total : 0;
  const screed_incl = screed_ex * (1 + vat);

  const pre_discount_ex = vinyl_ex + screed_ex;
  const discount_amount = pre_discount_ex * discount_pct;
  const total_ex = pre_discount_ex - discount_amount;
  const total_incl = total_ex * (1 + vat);

  // Tile removal fee is a pure pass-through — billed and costed at the
  // same figure, same as the confirmed pattern for stairwell labour — so
  // it dilutes overall margin % without ever being marked up itself.
  // delivery_fee_total is genuinely marked up (see above) so, unlike
  // tile removal, it's real cost AND contributes its markup to revenue —
  // included here so GP Rand/% reflect it the same way the real backend
  // (material_cost_total, calculations.py) does.
  const total_real_cost = box_total + glue_total + delivery_fee_total + screed_cost_total + tile_removal_total;
  const total_revenue = total_ex;
  const gp_rand = total_revenue - total_real_cost;
  const gp_pct = total_revenue ? (gp_rand / total_revenue) * 100 : 0;

  // Owner-Only Calculation Breakdown Toggle (confirmed Aug 2026, §0a) —
  // the only two figures shown by default now, for every role, always;
  // everything from here down in this function still runs and populates
  // #fjBreakdownSection's own elements exactly as before, that section
  // (and the separate #fjBreakdownGpCard) is just hidden via CSS
  // (applyRoleVisibility(), shared.js) unless the Owner has the
  // breakdown toggle on.
  document.getElementById('fj_out_summary_price').textContent = R(total_ex);
  document.getElementById('fj_out_summary_margin').textContent = gp_pct.toFixed(1) + '%';

  document.getElementById('fj_out_floor').textContent = floor_m2.toFixed(2) + ' m²';
  document.getElementById('fj_out_m2_needed').textContent = m2_needed.toFixed(2) + ' m²';
  document.getElementById('fj_out_boxes').textContent = boxes + ' boxes';
  document.getElementById('fj_out_bags').textContent = bags + ' bags';
  document.getElementById('fj_row_boxes').style.display = includeVinyl ? '' : 'none';
  document.getElementById('fj_row_bags').style.display = includeScreed ? '' : 'none';
  document.getElementById('fj_out_list_box').textContent = R(list_box_price) + '/box';
  document.getElementById('fj_out_net_box').textContent = R(net_box_price) + '/box';
  document.getElementById('fj_out_box_total').textContent = R(box_total);
  document.getElementById('fj_out_glue_total').textContent = R(glue_total);
  document.getElementById('fj_row_delivery_fee').style.display = delivery_fee_total > 0 ? '' : 'none';
  document.getElementById('fj_out_delivery_fee').textContent = R(delivery_fee_total);
  document.getElementById('fj_out_subtotal').textContent = R(subtotal);
  document.getElementById('fj_out_marked_up').textContent = R(marked_up) + ` (×${(1+markup).toFixed(2)})`;
  document.getElementById('fj_out_labour').textContent = R(labour_total);
  document.getElementById('fj_out_vinyl_ex').textContent = R(vinyl_ex);
  document.getElementById('fj_out_vinyl_incl').textContent = R(vinyl_incl);
  document.getElementById('fj_row_tile_removal').style.display = includeTileRemoval ? '' : 'none';
  document.getElementById('fj_out_tile_removal').textContent = R(tile_removal_total) + ' (R45/m² incl. VAT)';
  document.getElementById('fj_out_screed_ex').textContent = R(screed_ex);
  document.getElementById('fj_out_screed_incl').textContent = R(screed_incl);
  document.getElementById('fj_out_pre_discount').textContent = R(pre_discount_ex);
  document.getElementById('fj_out_discount_pct_label').textContent = (discount_pct*100).toFixed(1);
  document.getElementById('fj_out_discount_amount').textContent = '-' + R(discount_amount);
  document.getElementById('fj_out_total_ex').textContent = R(total_ex);
  document.getElementById('fj_out_total_incl').textContent = R(total_incl);
  document.getElementById('fj_out_per_m2_ex').textContent = floor_m2 ? ((vinyl_ex + screed_ex) / floor_m2).toFixed(2) : '0.00';
  document.getElementById('fj_out_per_m2_incl').textContent = floor_m2 ? ((vinyl_incl + screed_incl) / floor_m2).toFixed(2) : '0.00';

  let splitParts = [], labelText = '';
  if (includeVinyl && includeScreed) { splitParts.push(`Vinyl: ${R(vinyl_ex/(floor_m2||1))}/m²`, `Screed: ${R(screed_ex/(floor_m2||1))}/m²`); labelText = 'Total price per m² — Vinyl + Screed'; }
  else if (includeVinyl) { splitParts.push(`Vinyl only: ${R(vinyl_ex/(floor_m2||1))}/m²`); labelText = 'Total price per m² — Vinyl only'; }
  else if (includeScreed) { splitParts.push(`Screed only: ${R(screed_ex/(floor_m2||1))}/m²`); labelText = 'Total price per m² — Screed only'; }
  else { labelText = 'No floor items selected'; }
  document.getElementById('fj_out_per_m2_label').textContent = labelText;
  document.getElementById('fj_out_per_m2_split').innerHTML = splitParts.join(' &nbsp;+&nbsp; ') +
    '<span class="muted" style="display:block; margin-top:4px;">(floor only)</span>';

  document.getElementById('fj_section_vinyl').style.display = includeVinyl ? '' : 'none';
  document.getElementById('fj_block_vinyl').style.display = includeVinyl ? '' : 'none';
  document.getElementById('fj_section_screed').style.display = includeScreed ? '' : 'none';
  document.getElementById('fj_block_screed').style.display = includeScreed ? '' : 'none';

  document.getElementById('fj_out_gp_box_cost').textContent = R(box_total);
  document.getElementById('fj_out_gp_glue_cost').textContent = R(glue_total);
  document.getElementById('fj_out_gp_screed_cost').textContent = R(screed_cost_total) + ` (${bags} bags × R${bag_cost})`;
  document.getElementById('fj_row_gp_tile_removal').style.display = includeTileRemoval ? '' : 'none';
  document.getElementById('fj_out_gp_tile_removal').textContent = R(tile_removal_total);
  document.getElementById('fj_out_gp_total_cost').textContent = R(total_real_cost);
  document.getElementById('fj_out_gp_revenue').textContent = R(total_revenue);
  document.getElementById('fj_out_gp_rand').textContent = R(gp_rand);
  document.getElementById('fj_out_gp_pct').textContent = gp_pct.toFixed(1) + '%';
  document.getElementById('fj_out_gp_labour_amount').textContent = labour_total.toFixed(2);
}

// Actually saves the floor job via the real API — the local fjCalc()
// above is preview-only until this is clicked.
async function addFloorJob() {
  const floorM2 = parseFloat(document.getElementById('fj_floor_m2').value);
  if (!floorM2 || floorM2 <= 0) { alert('Enter a floor size first.'); return; }
  if (!confirmPostAcceptChange(editingLineId ? 'saving this change' : 'adding this line')) return;
  const includeVinyl = document.getElementById('fj_include_vinyl').checked;
  const includeScreed = document.getElementById('fj_include_screed').checked;
  const role = currentRole();
  // Quote-level discount PUT REMOVED from here (confirmed Aug 2026, Full
  // Real-Browser Walkthrough & Audit) — this was the actual bug: applying
  // the discount only as a side effect of adding a floor job meant Save
  // Quote silently dropped it, and a quote with no Flooring line had no
  // way to apply one at all. #fj_discount_pct now fires its own PUT
  // independently the moment it's changed (updateQuoteDiscount(), same
  // pattern as Transport Levy's updateTransportLevy()) — nothing left to
  // do here.

  // Edit Quote Line In Place (confirmed Aug 2026) — saving an in-progress
  // edit on a flooring/screed line now PUTs to that SAME line id instead
  // of the old delete-then-re-add (deleteLineBeingEditedIfAny(), removed).
  // prefillFlooringEdit() always leaves exactly one of includeVinyl/
  // includeScreed checked, matching whichever type this line actually is.
  if (editingLineId) {
    const editingScreed = includeScreed && !includeVinyl;
    let params;
    if (editingScreed) {
      const productId = document.getElementById('fj_screed_product').value;
      if (!productId) { alert('Pick a screed product first.'); return; }
      params = new URLSearchParams({
        product_id: productId, quantity_m2: floorM2, job_type: document.getElementById('fj_screed_jobtype').value, discount_pct: 0,
        labour_rate_per_m2: 0, own_staff: document.getElementById('fj_own_staff').value,
        bag_cost: document.getElementById('fj_bag_cost').value || 235,
        include_tile_removal_fee: document.getElementById('fj_tile_removal').checked,
        role,
      });
    } else {
      const productId = document.getElementById('fj_vinyl_product').value;
      const materialOnly = document.getElementById('fj_material_only').checked;
      const glueRate = materialOnly ? 0 : (parseFloat(document.getElementById('fj_glue_rate').value) || 0);
      params = new URLSearchParams({
        product_id: productId, quantity_m2: floorM2, job_type: document.getElementById('fj_jobtype').value, discount_pct: 0,
        glue_cost_per_unit: glueRate * 70, glue_coverage_m2: 70,
        labour_rate_per_m2: materialOnly ? 0 : (document.getElementById('fj_labour_rate').value || 0),
        own_staff: document.getElementById('fj_own_staff').value,
        markup_override: 1 + (parseFloat(document.getElementById('fj_markup').value) / 100 || 0),
        apply_delivery_fee: document.getElementById('fj_courier_toggle')?.checked ?? false,
        role,
      });
    }
    const res = await fetch(`${API}/quotes/${currentQuoteId}/lines/${editingLineId}/flooring?${params}`, {method:'PUT'});
    const result = await res.json();
    if (result.warning) alert(result.warning);
    if (result.override_cleared) alert('This line had a Manual Override applied — because the product/colour changed, the override was cleared and the price recalculated from the new figures. Reconfirm the override if one is still needed.');
    cancelLineEdit();
    loadQuote();
    return;
  }

  if (includeVinyl) {
    const productId = document.getElementById('fj_vinyl_product').value;
    const jobType = document.getElementById('fj_jobtype').value;
    const materialOnly = document.getElementById('fj_material_only').checked;
    const glueRate = materialOnly ? 0 : (parseFloat(document.getElementById('fj_glue_rate').value) || 0);
    const params = new URLSearchParams({
      product_id: productId, quantity_m2: floorM2, job_type: jobType, discount_pct: 0,
      glue_cost_per_unit: glueRate * 70,   // reverse-derived from rate (R/m²) x standard 70m² drum coverage
      glue_coverage_m2: 70,
      labour_rate_per_m2: materialOnly ? 0 : (document.getElementById('fj_labour_rate').value || 0),
      own_staff: document.getElementById('fj_own_staff').value,
      markup_override: 1 + (parseFloat(document.getElementById('fj_markup').value) / 100 || 0),
      // Courier toggle (confirmed Aug 2026, Transport/Courier Toggle
      // Relocation brief) — per-JOB override submitted with this specific
      // line; the product's own delivery_fee_per_m2 is never modified by
      // this, only whether THIS line applies it.
      apply_delivery_fee: document.getElementById('fj_courier_toggle')?.checked ?? false,
      role,
    });
    const res = await fetch(`${API}/quotes/${currentQuoteId}/lines/flooring?${params}`, {method:'POST'});
    const result = await res.json();
    if (result.warning) alert(result.warning);
    // Auto-Add Screed for Vinyl (confirmed Aug 2026) — "very few flooring
    // jobs go in without needing screed": adding a fresh Vinyl line now
    // automatically adds its own companion Screed line too, using
    // whatever the Screed section's own fields are already defaulted to
    // (Smooth job type is the dropdown's own first/default option; area
    // = this same floorM2 — "the flooring's own floor size," per the
    // brief's own wording) — genuinely the SAME code path/defaults a
    // manual Screed add on the Screed tab would use, just fired
    // automatically rather than requiring a second, separate step. Only
    // on a fresh ADD, never on an edit (editingLineId already returned
    // above before this point) — editing an existing Vinyl line's floor
    // size shouldn't silently spawn an unrelated new Screed line. Scoped
    // to Vinyl/Flooring only, per the brief's own explicit non-goal —
    // Carpet's own screed decision (optional, manual) is completely
    // untouched, this function has no Carpet code in it at all.
    await autoAddScreedLine(floorM2, role);
  }
  if (includeScreed) {
    const productId = document.getElementById('fj_screed_product').value;
    if (!productId) {
      alert('Screed is ticked but there\'s no screed product in your price book yet — add one under Price Book, or untick "Include screed" for this job.');
    } else {
      const jobType = document.getElementById('fj_screed_jobtype').value;
      const params = new URLSearchParams({
        product_id: productId, quantity_m2: floorM2, job_type: jobType, discount_pct: 0,
        labour_rate_per_m2: 0,   // screed rate is confirmed all-in — no separate labour add-on
        own_staff: document.getElementById('fj_own_staff').value,
        bag_cost: document.getElementById('fj_bag_cost').value || 235,
        include_tile_removal_fee: document.getElementById('fj_tile_removal').checked,
        role,
      });
      const res = await fetch(`${API}/quotes/${currentQuoteId}/lines/flooring?${params}`, {method:'POST'});
      const result = await res.json();
      if (result.warning) alert(result.warning);
    }
  }
  loadQuote();
}

// Auto-Add Screed for Vinyl (confirmed Aug 2026) — factored out of
// addFloorJob()'s own manual includeScreed branch above rather than
// duplicated, since the two need to behave identically except for one
// thing: a manual Screed add (user is deliberately on the Screed tab)
// alerts if there's no screed product configured yet — an auto-add
// (user only asked to add a VINYL line) stays silent instead and just
// skips it. Surfacing an alert about a missing SCREED product the
// moment someone adds a VINYL line would be a confusing non-sequitur,
// not a helpful prompt — the manual path already covers that case for
// anyone who deliberately wants a screed line and hits it.
async function autoAddScreedLine(floorM2, role) {
  const productId = document.getElementById('fj_screed_product').value;
  if (!productId) return;
  const jobType = document.getElementById('fj_screed_jobtype').value;
  const params = new URLSearchParams({
    product_id: productId, quantity_m2: floorM2, job_type: jobType, discount_pct: 0,
    labour_rate_per_m2: 0,
    own_staff: document.getElementById('fj_own_staff').value,
    bag_cost: document.getElementById('fj_bag_cost').value || 235,
    include_tile_removal_fee: document.getElementById('fj_tile_removal').checked,
    role,
  });
  const res = await fetch(`${API}/quotes/${currentQuoteId}/lines/flooring?${params}`, {method:'POST'});
  const result = await res.json();
  if (result.warning) alert(result.warning);
}

async function createQuote() {
  const typedClientName = document.getElementById('q_client').value;
  // Clarify Buttons + Price Check + Marketing Source (confirmed Aug
  // 2026) — Start Quote must ONLY ever link to a real, selected
  // existing client, never free text (brief's own explicit words,
  // matching the Client-Link Audit's own safe search-and-select
  // pattern). The ONE exception: a Builder Estimate's referred contact
  // (pendingBuilderEstimateId set, index.html) isn't staff free-typing
  // a name — it's a real contact already captured through the public
  // Builder Portal, so that flow keeps the exact-match-or-auto-create
  // fallback below, unchanged from before this brief.
  if (!pendingClientId && !pendingBuilderEstimateId) {
    alert('Search for an existing client and select them from the list first.\n\nTo add someone new, use the "New Client" button instead — or "Price Check" if you just need a quick price with no client on file yet.');
    return;
  }
  // Confirmed root cause of the Order Index -> Client Link Gap brief's
  // Gap 2 (confirmed Aug 2026): typing a client's name here WITHOUT
  // clicking the matching autocomplete suggestion (onQClientInput
  // resets pendingClientId to null on every keystroke, index.html) — a
  // real, easy-to-miss slip, e.g. fast typing before the debounced
  // suggestion box has appeared — creates a permanently disconnected
  // walk-in quote (client_id=None). It still shows correctly on the
  // Order Index (which lists every quote regardless of client_id), but
  // never appears in that client's own Order History, which filters
  // strictly by client_id. This is the same bug's PREVENTION: if the
  // typed text exactly matches (case/whitespace-insensitive) a real
  // existing client that the user just didn't happen to click, ask
  // before silently creating a second, disconnected record. Only
  // reachable via the Builder Estimate path now (the guard above
  // requires pendingClientId otherwise) — a quote that's meant to be a
  // genuine new walk-in from that flow (no matching client) never
  // triggers this — one extra request, only when there's something to
  // actually warn about.
  if (!pendingClientId && typedClientName.trim()) {
    try {
      const searchRes = await fetch(`${API}/clients?search=${encodeURIComponent(typedClientName.trim())}`);
      const matches = await searchRes.json();
      const exactMatch = matches.find(c => c.name.trim().toLowerCase() === typedClientName.trim().toLowerCase());
      if (exactMatch) {
        const linkInstead = confirm(`A client named "${exactMatch.name}" already exists. Link this quote to them instead of creating a separate, unlinked quote?\n\nOK = link to ${exactMatch.name}\nCancel = continue as a new, unlinked walk-in`);
        if (linkInstead) { pendingClientId = exactMatch.id; }
      }
    } catch (e) { /* best-effort check — a failed lookup shouldn't block starting the quote at all */ }
  }
  const params = new URLSearchParams({
    client_name: document.getElementById('q_client').value,
    sales_owner: document.getElementById('q_owner').value,
    branch: document.getElementById('q_branch').value,
    blinds_measurements_visible: document.getElementById('q_measurements').checked,
    // Real gap found while merging v54: this never sent deposit_pct at
    // all, so every quote silently got the backend model's own
    // hardcoded 70% default regardless of what Business Settings said.
    deposit_pct: businessSettings?.default_deposit_pct ?? 0.70,
  });
  if (pendingClientId) { params.set('client_id', pendingClientId); }
  const res = await fetch(`${API}/quotes?${params}`, {method:'POST'});
  const quote = await res.json();
  currentQuoteId = quote.id;
  // Revert to Original (confirmed Aug 2026, Add-Line Data-Loss brief
  // §5) — snapshots the (empty) starting state so "Revert to original"
  // has something to restore to even for a brand-new quote. Fire-and-
  // forget, same reasoning as the Builder Portal linkage below: a
  // failure here shouldn't block quote creation, and there's nothing
  // to lose yet at this exact moment for a revert to meaningfully undo.
  fetch(`${API}/quotes/${quote.id}/snapshot`, {method:'POST'});
  // Defensive backstop against stale-quote residue (confirmed Aug 2026,
  // Client-Side Commercial Workflow brief) — a brand-new quote is
  // genuinely empty server-side, but the DOM (#linesTable, #quoteTotal,
  // etc.) could still be showing whatever a PREVIOUS quote left behind
  // if this entry point is ever reached without going through
  // resetQuoteBuilderUI() first. Cheap and always correct to clear here
  // too, regardless of how this was reached.
  clearStaleQuoteResidue();
  currentQuoteClientId = quote.client_id || null;   // set AFTER clearStaleQuoteResidue(), which resets it to null — this is the new quote's own real client, straight from the just-created record
  document.getElementById('quoteStatus').textContent = `Quote #${quote.id} started for ${quote.client_name}.`;
  document.getElementById('addLineCard').style.display = 'block';
  document.getElementById('linesCard').style.display = 'block';
  document.getElementById('quoteDiscountCard').style.display = 'block';
  document.getElementById('transportCourierCard').style.display = 'block';
  document.getElementById('floorPrepCard').style.display = 'block';
  // Real gap found while building Duplicate Quote (confirmed Aug 2026):
  // quotePhotosCard was only ever shown inside loadQuote() (which this
  // function doesn't call — only addLine() does, after the first line
  // is added), so a brand-new quote with zero lines couldn't accept a
  // site photo yet, even though "site context before committing time or
  // stock" is exactly the moment that'd be most useful. Shown directly
  // here too now, same as the other cards on this line.
  document.getElementById('quotePhotosCard').style.display = 'block';
  // Persistent summary panel (confirmed Aug 2026, Vinyl Quoting UX
  // Redesign proposal §03, approved, Phase 3) — this function never
  // calls loadQuote() (only addLine() does, after the first real
  // line — see this function's own earlier comment), so without this
  // the panel would stay hidden/stale until then. 0 is correct here,
  // not a placeholder — a brand-new quote genuinely has no lines yet.
  renderQuoteSummaryPanel(0);
  const dupBtn = document.getElementById('duplicateQuoteBtn');
  if (dupBtn) dupBtn.style.display = '';
  const revertBtn = document.getElementById('revertQuoteBtn');
  if (revertBtn) revertBtn.style.display = '';
  const startBtn = document.getElementById('startQuoteBtn');
  startBtn.disabled = true;
  startBtn.textContent = 'Already open (see below)';
  pendingClientId = null;
  pendingClientName = null;
  // Builder Referral Portal pilot (confirmed Aug 2026) — if this quote
  // was started via "Start Quote" on a builder's estimate, link the two
  // records together now that the quote actually has an id. Fire-and-
  // forget deliberately: a failure here shouldn't block quote creation
  // itself, since the quote is already real either way — worst case the
  // link can be retried by hand later (the estimate just stays
  // "unlinked" on the Builder Portal screen, it isn't lost).
  if (pendingBuilderEstimateId) {
    const linkId = pendingBuilderEstimateId;
    pendingBuilderEstimateId = null;
    fetch(`${API}/admin/builder-estimates/${linkId}/link-quote?quote_id=${quote.id}`, {method: 'PUT'})
      .catch(() => {});
  }
  // If arrived here via a landing page drill-down (e.g. clicked series 200
  // directly), pick up where that left off — category and product already chosen.
  if (pendingCategory) {
    document.getElementById('line_category').value = pendingCategory;
  }
  await toggleLineFields();
  if (pendingVinylRange) {
    populateVinylRangeDropdown(pendingVinylRange);
    pendingVinylRange = null;
  }
  if (pendingCarpetType) {
    selectCarpetType(pendingCarpetType, pendingCarpetRange);
    pendingCarpetType = null;
    pendingCarpetRange = null;
  }
  pendingCategory = null;
}

// Price Check (confirmed Aug 2026, New Quote Screen brief §3) — reuses
// the exact same calculator/line-item flow as a real quote (brief's own
// "reuse, don't rebuild"), just flagged is_price_check=true so it's
// excluded from the Order Index/Needs Attention/dashboards until
// explicitly converted (convertPriceCheckToQuote(), below). Contact
// details are OPTIONAL here — deliberately NOT behind createQuote()'s
// own hard "must select a real existing client" gate, since the whole
// point is a walk-in who may not want to give any details yet. If a
// real client WAS searched and selected first, or a name typed, that's
// carried over and linked normally server-side (create_quote(),
// main.py) — this isn't a separate, parallel client-handling path.
async function startPriceCheck() {
  const params = new URLSearchParams({
    client_name: document.getElementById('q_client').value,
    sales_owner: document.getElementById('q_owner').value,
    branch: document.getElementById('q_branch').value,
    blinds_measurements_visible: document.getElementById('q_measurements').checked,
    deposit_pct: businessSettings?.default_deposit_pct ?? 0.70,
    is_price_check: true,
  });
  if (pendingClientId) { params.set('client_id', pendingClientId); }
  const res = await fetch(`${API}/quotes?${params}`, {method:'POST'});
  const quote = await res.json();
  currentQuoteId = quote.id;
  fetch(`${API}/quotes/${quote.id}/snapshot`, {method:'POST'});
  clearStaleQuoteResidue();
  currentQuoteClientId = quote.client_id || null;
  document.getElementById('quoteStatus').innerHTML =
    `<b style="color:var(--coral);">PRICE CHECK</b> #${quote.id} started${quote.client_id ? ' for ' + quote.client_name : ''} — not a saved job until you convert it below.`;
  document.getElementById('addLineCard').style.display = 'block';
  document.getElementById('linesCard').style.display = 'block';
  document.getElementById('quoteDiscountCard').style.display = 'block';
  document.getElementById('transportCourierCard').style.display = 'block';
  document.getElementById('floorPrepCard').style.display = 'block';
  document.getElementById('quotePhotosCard').style.display = 'block';
  renderQuoteSummaryPanel(0);   // same reasoning as createQuote()'s own comment just above it — this entry point never calls loadQuote() either
  const startBtn = document.getElementById('startQuoteBtn');
  startBtn.disabled = true;
  startBtn.textContent = 'Already open (see below)';
  pendingClientId = null;
  pendingClientName = null;
  if (pendingCategory) { document.getElementById('line_category').value = pendingCategory; }
  await toggleLineFields();
  if (pendingVinylRange) { populateVinylRangeDropdown(pendingVinylRange); pendingVinylRange = null; }
  if (pendingCarpetType) { selectCarpetType(pendingCarpetType, pendingCarpetRange); pendingCarpetType = null; pendingCarpetRange = null; }
  pendingCategory = null;
  loadQuote();
}

async function convertPriceCheckToQuote() {
  if (!currentQuoteId) return;
  let clientId = currentQuoteClientId;
  if (!clientId) {
    const name = prompt('This Price Check has no client on file yet. Enter the client\'s name to convert it into a real, tracked quote:');
    if (!name || !name.trim()) return;
    const res = await fetch(`${API}/quotes/${currentQuoteId}/convert-to-quote?client_name=${encodeURIComponent(name.trim())}`, {method: 'POST'});
    if (!res.ok) { const body = await res.json().catch(() => ({})); alert(body.detail || 'Could not convert this Price Check.'); return; }
  } else {
    const res = await fetch(`${API}/quotes/${currentQuoteId}/convert-to-quote?client_id=${clientId}`, {method: 'POST'});
    if (!res.ok) { const body = await res.json().catch(() => ({})); alert(body.detail || 'Could not convert this Price Check.'); return; }
  }
  alert('Converted — this is now a real, tracked quote and will appear on the Order Index.');
  loadQuote();
}

// Zero data leakage between quotes (confirmed Aug 2026, Client-Side
// Commercial Workflow brief, Sprint A item #2) — real bug found tracing
// this exact concern: startFreshQuote() previously only HID
// linesCard/addLineCard, it never actually cleared #linesTable's rows
// or #quoteTotal's text. Since those two elements are only ever
// repopulated by loadQuote() (which only runs again once the FIRST new
// line is added), the previous quote's line items and total sat there,
// still in the DOM, just invisible — the instant linesCard was shown
// again (createQuote(), right after "Start Quote"), the OLD quote's
// lines would flash back into view until a new line was added. Same gap
// existed via startQuoteForClient() (clients.js/index.html — "+ New
// Quote" from a client's own page), which never called any reset at
// all, and is actually the MORE likely real path into this bug (browse
// to a different client, click "+ New Quote" directly, no deliberate
// "New Quote (different client)" click in between). Centralized here so
// every entry point into "start a quote" shares one true reset,
// including createQuote() itself as a defensive backstop.
// Split in two deliberately: clearStaleQuoteResidue() is safe to call
// at ANY point, including right before reading q_client/q_owner/q_branch
// to create a new quote (createQuote() does exactly that) — it never
// touches those three fields. resetQuoteBuilderUI() is the FULL reset
// (adds clearing q_client itself + the Start Quote button state), for
// entry points where no quote is being submitted in the same breath.
function clearStaleQuoteResidue() {
  currentQuoteStatus = 'quoted';   // a brand-new quote's workflow_status is always 'quoted' — must not inherit the PREVIOUS quote's status and wrongly trigger (or skip) the post-accept confirmation below
  currentQuoteClientId = null;   // must not leak the PREVIOUS quote's real client into a new one — createQuote() sets this fresh via loadQuote() once the new quote is actually saved
  // Real, pre-existing gap this function's own stated purpose already
  // covers, closed while building the persistent summary panel
  // (confirmed Aug 2026, Vinyl Quoting UX Redesign proposal §03,
  // approved, Phase 3): this cache was never reset here, only ever
  // reassigned inside loadQuote() — meaning renderFloorPrepRoomCards()
  // and the new summary panel could both briefly read the PREVIOUS
  // quote's lines on a fresh quote, before the first addLine()/
  // loadQuote() call overwrote it. Same "cheap and always correct to
  // clear here too" reasoning as everything else in this function.
  currentQuoteLinesCache = [];
  const printInvoiceBtn = document.getElementById('printInvoiceBtn');
  if (printInvoiceBtn) printInvoiceBtn.style.display = 'none';
  const sendInvoiceBtn = document.getElementById('sendInvoiceBtn');
  if (sendInvoiceBtn) sendInvoiceBtn.style.display = 'none';
  const tbody = document.querySelector('#linesTable tbody');
  if (tbody) tbody.innerHTML = '';
  const totalEl = document.getElementById('quoteTotal');
  if (totalEl) totalEl.innerHTML = '';
  const levyAmountEl = document.getElementById('fj_transport_amount');
  if (levyAmountEl) levyAmountEl.value = 0;
  const levyToggleEl = document.getElementById('fj_transport_toggle');
  if (levyToggleEl) levyToggleEl.checked = false;
  const levyFieldEl = document.getElementById('fj_transport_amount_field');
  if (levyFieldEl) levyFieldEl.style.display = 'none';
  const courierToggleEl = document.getElementById('fj_courier_toggle');
  if (courierToggleEl) courierToggleEl.checked = false;
  // Quote-level Discount (confirmed Aug 2026, Full Real-Browser
  // Walkthrough & Audit) — same stale-residue reasoning as the Transport
  // levy fields just above: a brand-new quote must not silently inherit
  // the PREVIOUS quote's discount %.
  const discountPctEl = document.getElementById('fj_discount_pct');
  if (discountPctEl) discountPctEl.value = 0;
  // New Quote Starting Screen: Price Check Fix (confirmed Aug 2026) —
  // real bug found, not just made-more-visible: this function already
  // reset plenty of state (transport levy, discount %, floor prep
  // scratch fields) but never touched the actual "Add Line" calculator
  // inputs themselves — Price Check specifically only ever reset the
  // OUTPUT (result panel), never the fields that produced it, since it
  // calls this function directly without the fuller resetQuoteBuilderUI()
  // (which would also wrongly clear q_client/disable Start Quote mid-
  // Price-Check-flow). Confirmed via code reading, not guessed: floor
  // size had NO reset path anywhere at all (not even indirectly, unlike
  // Range/Colour which get refilled as a side effect of
  // toggleLineFields()'s own product-dropdown-repopulation cascade
  // right after this runs); glue rate had a real conditional gap too —
  // onVinylProductChange() only overwrites it when the newly-selected
  // product has its OWN glue_rate_per_m2 override set, so a product
  // with none would silently inherit whatever rate the PREVIOUS
  // calculation left behind. Blanking every raw "in progress" input
  // here, once, closes the whole class of bug rather than chasing each
  // field individually — the same onXChange cascades already fired by
  // toggleLineFields() right after this function returns (both call
  // sites: startPriceCheck() and resetQuoteBuilderUI()) then correctly
  // refill whatever has a real per-product/business-setting default.
  ['fj_floor_m2', 'fj_wastage', 'fj_m2_per_box', 'fj_box_price', 'fj_trade_discount',
   'fj_markup', 'fj_labour_rate', 'fj_screed_rate',
   'carpet_lm', 'carpet_m2', 'line_width', 'line_drop', 'line_length', 'line_num_stairs',
   'line_misc_desc', 'line_misc_amount'].forEach(id => { const el = document.getElementById(id); if (el) el.value = ''; });
  ['line_discount', 'carpet_discount'].forEach(id => { const el = document.getElementById(id); if (el) el.value = 0; });
  const miscCostEl = document.getElementById('line_misc_cost');
  if (miscCostEl) miscCostEl.value = 0;
  // Glue rate deliberately reset to its own real fallback (17.05, the
  // same static default the input's own HTML attribute always carried),
  // not blanked to '' like the rest of this block — real regression
  // caught in the same pass, not shipped: onVinylProductChange() only
  // overwrites this field when the newly-selected product has its OWN
  // glue_rate_per_m2 override set, so blanking it here for a product
  // with none would leave a genuinely empty field, which fjCalc() reads
  // as a real 0 glue rate — silently pricing that vinyl line as if it
  // needed no adhesive at all, not "reset to the confirmed universal
  // fallback" like every other product without its own override
  // already correctly gets.
  const glueRateEl = document.getElementById('fj_glue_rate');
  if (glueRateEl) glueRateEl.value = 17.05;
  const bagCostEl = document.getElementById('fj_bag_cost');
  if (bagCostEl) bagCostEl.value = 235;
  const bagCoverageEl = document.getElementById('fj_bag_coverage');
  if (bagCoverageEl) bagCoverageEl.value = 4;
  const stairAreaEl = document.getElementById('line_stair_area');
  if (stairAreaEl) stairAreaEl.value = 0.45;
  const tileRemovalEl = document.getElementById('fj_tile_removal');
  if (tileRemovalEl) tileRemovalEl.checked = false;
  const materialOnlyEl = document.getElementById('fj_material_only');
  if (materialOnlyEl) materialOnlyEl.checked = false;
  const ownStaffEl = document.getElementById('fj_own_staff');
  if (ownStaffEl) ownStaffEl.value = 'true';
  const stairOwnStaffEl = document.getElementById('line_stair_own_staff');
  if (stairOwnStaffEl) stairOwnStaffEl.value = 'true';
  const stairwellTypeEl = document.getElementById('line_stairwell_type');
  if (stairwellTypeEl) stairwellTypeEl.value = 'closed';
  activeCarpetType = null;   // so re-entering the Carpet tab defaults fresh to Stretch, same as a genuinely first-ever visit
  const statusDisplayEl = document.getElementById('q_status_display');
  if (statusDisplayEl) statusDisplayEl.innerHTML = '<span class="muted">—</span>';
  const saveStatusEl = document.getElementById('saveStatus');
  if (saveStatusEl) saveStatusEl.textContent = '';
  const descriptionEl = document.getElementById('q_description');
  if (descriptionEl) descriptionEl.value = '';
  clearLandingRows();   // stairwell landing rows are per-quote scratch state too
  // Order Details / Follow-Ups field-clearing REMOVED from here
  // (confirmed Aug 2026, Quote Builder Layout Corrections brief) —
  // that whole card moved off Quote Builder onto the Order Index's own
  // Order Details screen (order-index.js), which manages its own
  // residue independently since it's no longer part of this screen's
  // state at all.
  // Extra Rooms / Floor Prep (confirmed Aug 2026) — per-room scratch
  // state, same reasoning as the stairwell landing rows above.
  ['fp_room_name', 'fp_area', 'fp_thickness', 'fp_manual_desc'].forEach(id => { const el = document.getElementById(id); if (el) el.value = ''; });
  const fpRoomCardsEl = document.getElementById('floorPrepRoomCards');
  if (fpRoomCardsEl) fpRoomCardsEl.innerHTML = '';
  ['fp_amount', 'fp_cost'].forEach(id => { const el = document.getElementById(id); if (el) el.value = 0; });
  const fpDescEl = document.getElementById('fp_calc_description');
  if (fpDescEl) fpDescEl.textContent = '';
  // Real bug found while building Sprint B's line editing: without this,
  // editingLineId could survive into a DIFFERENT quote (started right
  // after cancelling out of an edit mid-flow) — the next "Add" click in
  // that new quote would then wrongly delete a line ID belonging to the
  // PREVIOUS quote.
  cancelLineEdit();
  // Quote Photo Attachments (confirmed Aug 2026) — same stale-residue
  // risk as everything else here: without this, the PREVIOUS quote's
  // thumbnails would still be sitting in the gallery when this card is
  // shown again, before loadQuotePhotos() gets a chance to repopulate
  // it. Revoking the object URLs too, not just clearing the array —
  // otherwise every quote opened in one session leaks its blob memory.
  quotePhotoObjectUrls.forEach(url => URL.revokeObjectURL(url));
  quotePhotoObjectUrls = [];
  currentQuotePhotos = [];
  const galleryEl = document.getElementById('quotePhotoGallery');
  if (galleryEl) galleryEl.innerHTML = '';
  const photoInputEl = document.getElementById('quotePhotoInput');
  if (photoInputEl) photoInputEl.value = '';
  const photoStatusEl = document.getElementById('quotePhotoUploadStatus');
  if (photoStatusEl) photoStatusEl.textContent = '';
}

function resetQuoteBuilderUI() {
  currentQuoteId = null;
  setPageTitle('New Quote');   // Page Title in Sticky Header brief
  document.getElementById('q_client').value = '';
  // Default Branch per Staff (confirmed Aug 2026) — also closes a real,
  // separate stale-residue gap found while investigating the reported
  // "Client feed -> add new client -> Create new quote" occurrence:
  // resetQuoteBuilderUI() never reset q_branch at all, so it silently
  // kept whatever branch the PREVIOUS quote happened to be on, which
  // could easily read as "stale previous quote data" even though the
  // lines/total themselves were correctly cleared below. Explicit
  // client-preference overrides (startQuoteForClient()'s preferredBranch
  // param) are applied by the caller right after this returns, so they
  // still correctly win over this staff default.
  document.getElementById('q_branch').value = defaultBranchForCurrentUser();
  // Sales Owner default (confirmed Aug 2026, Vinyl Quoting UX Redesign
  // proposal §07, approved) — same real gap/fix shape as q_branch just
  // above: resetQuoteBuilderUI() never reset q_owner either, so a new
  // quote silently kept whatever Sales Owner the PREVIOUS quote had.
  document.getElementById('q_owner').value = defaultSalesOwnerForCurrentUser();
  syncQuoteOwnerBranchControls();
  clearStaleQuoteResidue();
  document.getElementById('addLineCard').style.display = 'none';
  document.getElementById('linesCard').style.display = 'none';
  document.getElementById('quoteDiscountCard').style.display = 'none';
  document.getElementById('transportCourierCard').style.display = 'none';
  document.getElementById('floorPrepCard').style.display = 'none';
  // Persistent summary panel (confirmed Aug 2026, Vinyl Quoting UX
  // Redesign proposal §03, approved, Phase 3) — currentQuoteId is
  // already null above, so this just hides the panel; without this
  // call it would keep showing the PREVIOUS quote's stale summary
  // until the next loadQuote(), same stale-residue class of bug
  // q_branch/q_owner above were fixed for.
  renderQuoteSummaryPanel();
  document.getElementById('quotePhotosCard').style.display = 'none';
  const dupBtnHide = document.getElementById('duplicateQuoteBtn');
  if (dupBtnHide) dupBtnHide.style.display = 'none';
  const revertBtnHide = document.getElementById('revertQuoteBtn');
  if (revertBtnHide) revertBtnHide.style.display = 'none';
  // New Quote Starting Screen: Clarity Pass (confirmed Aug 2026) —
  // Start Quote is genuinely disabled again on a fresh reset, same as
  // a real first-ever visit — q_client was just cleared above, and
  // pendingClientId (a real, previously-selected client id) must not
  // silently survive into this new, blank state either, or the button
  // would wrongly stay enabled for a client name that's no longer even
  // showing in the field.
  pendingClientId = null;
  document.getElementById('startQuoteBtn').textContent = 'Start Quote';
  updateStartQuoteButtonState();
  document.getElementById('quoteStatus').textContent = '';
}

function startFreshQuote() {
  resetQuoteBuilderUI();
}

async function saveQuote() {
  if (!currentQuoteId) return;
  // Workflow status deliberately NOT sent from here (confirmed Aug
  // 2026, Order Index / Job Workflow Redesign brief) — it only ever
  // changes via a specific action (Accept/Decline/Schedule/Complete) on
  // the Job Detail screen, never a raw field saved from Quote Builder.
  const params = new URLSearchParams({
    client_name: document.getElementById('q_client').value,
    sales_owner: document.getElementById('q_owner').value,
    branch: document.getElementById('q_branch').value,
    // Quote Description field (confirmed Aug 2026, Duplicate Quote +
    // Quote Description brief) — free text so quotes are identifiable
    // at a glance in the Order Index, especially once duplicated into
    // variants.
    description: document.getElementById('q_description').value,
  });
  const res = await fetch(`${API}/quotes/${currentQuoteId}?${params}`, {method:'PUT'});
  if (!res.ok) {
    document.getElementById('saveStatus').textContent = '❌ Could not save — check your connection and try again.';
    return;
  }
  // Confirmed Aug 2026, Save Redirect + Client Link Missing brief —
  // "taken back to the Order Index automatically... so he can
  // immediately see the saved quote sitting in the list — confirmation
  // that it actually landed, rather than trusting an on-screen 'Saved✓'
  // message alone." Navigates straight there instead of showing the
  // status text (which would never be seen anyway, since the screen
  // changes immediately) — the saved quote's own presence in that list
  // IS the confirmation now. showRawSection(), not goToTab()/
  // showSection() — that pair forces landingView back to 'tiles' first
  // (correct for the Home button, wrong here), which would flash the
  // tile menu for a frame before Order Index actually renders.
  showRawSection('landing');
  landingView = 'orders';
  renderLanding();
}

async function printQuote() {
  if (!currentQuoteId) return;
  await renderPrintDoc(currentQuoteId, 'quote');
}

// Landing support (staircases with a turn/half-landing can have more than
// one landing platform — confirmed Aug 2026). Each row is measured
// individually and summed; the total is billed as a normal flooring
// material line, NOT part of the stairwell tile/glue formula (landing was
// already documented as deliberately outside the stairwell calc).
function addLandingRow() {
  const list = document.getElementById('stairwell_landings_list');
  const row = document.createElement('div');
  row.className = 'landing-row';
  row.style.cssText = 'display:flex; gap:6px; align-items:center; margin-bottom:4px;';
  row.innerHTML = `<input type="number" class="landing-area-input" step="0.01" min="0" placeholder="Landing area m²" style="width:140px;" oninput="recomputeLandingTotal()"><button type="button" onclick="removeLandingRow(this)">Remove</button>`;
  list.appendChild(row);
  recomputeLandingTotal();
}
function removeLandingRow(btn) {
  btn.closest('.landing-row').remove();
  recomputeLandingTotal();
}
function recomputeLandingTotal() {
  const inputs = document.querySelectorAll('.landing-area-input');
  let total = 0;
  inputs.forEach(i => { total += parseFloat(i.value) || 0; });
  document.getElementById('landing_total_display').textContent = total.toFixed(2);
  return total;
}
function clearLandingRows() {
  document.getElementById('stairwell_landings_list').innerHTML = '';
  recomputeLandingTotal();
}

async function addLine() {
  const cat = document.getElementById('line_category').value;
  const productId = document.getElementById('line_product').value;
  const discount = parseFloat(document.getElementById('line_discount').value) / 100;
  const role = currentRole();
  if (!confirmPostAcceptChange(editingLineId ? 'saving this change' : 'adding this line')) return;

  // Stairwell params built once here — shared by both the edit (PUT) and
  // create (POST) paths below, same "one param set, two possible verbs"
  // shape every other category already uses.
  let stairwellParams, stairwellLandingTotal;
  if (cat === 'stairwell') {
    // Landing(s), summed — CHANGED Aug 2026: folded into this SAME
    // stairwell request/line instead of a separate POST to
    // /lines/flooring, so the quote shows one combined stair price, not
    // two lines. Still priced at the standard per-m² flat-flooring rate
    // (same vinyl product, no markup override) — only how it's posted
    // and displayed changed, not the rate or calculation.
    stairwellLandingTotal = recomputeLandingTotal();
    stairwellParams = new URLSearchParams({
      vinyl_product_id: document.getElementById('line_stair_vinyl').value,
      nosing_product_id: document.getElementById('line_nosing_product').value,
      num_stairs: document.getElementById('line_num_stairs').value,
      stair_area_m2: document.getElementById('line_stair_area').value || 0.45,
      stairwell_type: document.getElementById('line_stairwell_type').value,
      own_staff: document.getElementById('line_stair_own_staff').value,
      landing_area_m2: stairwellLandingTotal,
      role,
    });
  }

  // Edit Quote Line In Place (confirmed Aug 2026, extended to Stairwell
  // Aug 2026, Vinyl Quoting UX Redesign proposal §09, approved) — saving
  // an in-progress edit on any category now PUTs to that SAME line id
  // instead of the old delete-then-re-add.
  if (editingLineId) {
    let editUrl;
    if (cat === 'stairwell') {
      editUrl = `${API}/quotes/${currentQuoteId}/lines/${editingLineId}/stairwell?${stairwellParams}`;
    } else if (cat === 'blinds') {
      const params = new URLSearchParams({
        product_id: productId, width_mm: document.getElementById('line_width').value,
        drop_mm: document.getElementById('line_drop').value, discount_pct: discount, role,
      });
      editUrl = `${API}/quotes/${currentQuoteId}/lines/${editingLineId}/blinds?${params}`;
    } else if (cat === 'trim' || cat === 'skirting') {
      const params = new URLSearchParams({
        product_id: productId, length_m: document.getElementById('line_length').value,
        discount_pct: discount, role,
      });
      editUrl = `${API}/quotes/${currentQuoteId}/lines/${editingLineId}/trims?${params}`;
    } else if (cat === 'misc') {
      if (!document.getElementById('line_misc_desc').value) { alert('Enter a description first.'); return; }
      const params = new URLSearchParams({
        description: document.getElementById('line_misc_desc').value,
        amount_ex_vat: document.getElementById('line_misc_amount').value || 0,
        cost_ex_vat: document.getElementById('line_misc_cost').value || 0,
        role,
      });
      editUrl = `${API}/quotes/${currentQuoteId}/lines/${editingLineId}/misc?${params}`;
    }
    const res = await fetch(editUrl, {method:'PUT'});
    const line = await res.json();
    if (line.warning) alert(line.warning);
    if (line.override_cleared) alert('This line had a Manual Override applied — because the product changed, the override was cleared and the price recalculated from the new figures. Reconfirm the override if one is still needed.');
    if (cat === 'stairwell' && stairwellLandingTotal > 0) clearLandingRows();
    cancelLineEdit();
    loadQuote();
    return;
  }

  if (cat === 'stairwell') {
    const res = await fetch(`${API}/quotes/${currentQuoteId}/lines/stairwell?${stairwellParams}`, {method:'POST'});
    const line = await res.json();
    if (line.warning) alert(line.warning);
    if (stairwellLandingTotal > 0) clearLandingRows();
    loadQuote();
    return;
  }

  let url;
  if (cat === 'blinds') {
    const params = new URLSearchParams({
      product_id: productId, width_mm: document.getElementById('line_width').value,
      drop_mm: document.getElementById('line_drop').value, discount_pct: discount, role,
    });
    url = `${API}/quotes/${currentQuoteId}/lines/blinds?${params}`;
  } else if (cat === 'trim' || cat === 'skirting') {
    const params = new URLSearchParams({
      product_id: productId, length_m: document.getElementById('line_length').value,
      discount_pct: discount, role,
    });
    url = `${API}/quotes/${currentQuoteId}/lines/trims?${params}`;
  } else if (cat === 'misc') {
    const params = new URLSearchParams({
      description: document.getElementById('line_misc_desc').value,
      amount_ex_vat: document.getElementById('line_misc_amount').value || 0,
      cost_ex_vat: document.getElementById('line_misc_cost').value || 0,
      role,
    });
    if (!document.getElementById('line_misc_desc').value) { alert('Enter a description first.'); return; }
    url = `${API}/quotes/${currentQuoteId}/lines/misc?${params}`;
  }
  const res = await fetch(url, {method:'POST'});
  const line = await res.json();
  if (line.warning) alert(line.warning);
  loadQuote();
}

// Controlled post-accept adjustments (confirmed Aug 2026, Client-Side
// Commercial Workflow brief, Sprint D) — "controlled" means a
// deliberate heads-up, not a block: extra screed/trims/site extras
// genuinely do need adding after a quote's been accepted, that's the
// whole point of this sprint item. Nothing prevented this before (no
// status check existed anywhere on the add/edit/delete path), which
// also meant nothing FLAGGED it either — a line could be silently
// added to an already-accepted/invoiced/paid quote with zero
// indication anything unusual was happening. Genuinely draft/sent
// quotes are completely unaffected — no new dialog, same one-click
// editing Sprint B already built.
// Confirmed Aug 2026, Order Index / Job Workflow Redesign brief — moved
// from the legacy 6-value status set to the new 4-value workflow_status
// (accepted/scheduled/completed = "post-accept", same meaning as
// before, plus Scheduled now correctly included — a small pre-existing
// gap: a scheduled job wasn't covered by the old ['accepted','invoiced',
// 'paid'] set at all).
const POST_ACCEPT_LOCKED_STATUSES = ['accepted', 'scheduled', 'completed'];
function confirmPostAcceptChange(actionLabel) {
  if (!POST_ACCEPT_LOCKED_STATUSES.includes(currentQuoteStatus)) return true;
  return confirm(`This quote is already marked "${currentQuoteStatus}" — ${actionLabel} now could affect billing already communicated to the client.\n\nThis is allowed (e.g. extra screed, trims, or site extras found after acceptance) — just make sure the client knows about the change. Continue?`);
}

async function deleteQuoteLine(lineId) {
  const msg = POST_ACCEPT_LOCKED_STATUSES.includes(currentQuoteStatus)
    ? `This quote is already marked "${currentQuoteStatus}" — delete this line anyway? This could affect billing already communicated to the client; make sure the client knows.`
    : 'Delete this line from the quote?';
  if (!confirm(msg)) return;
  await fetch(`${API}/quotes/${currentQuoteId}/lines/${lineId}`, {method:'DELETE'});
  loadQuote();
}

// Edit an existing line (confirmed Aug 2026, Client-Side Commercial
// Workflow brief, Sprint B, then rebuilt Aug 2026 by the Edit Quote Line
// In Place brief — "same line, updated product/colour/quantity — without
// delete-and-re-add", the highest direct-value fix for the vinyl workflow
// per that brief's own words). Real constraint that still applies: a
// QuoteLineItem only ever stores the CALCULATED outputs (unit_cost,
// line_total, labour_charged_total...), never the raw inputs that produced
// them (wastage %, trade discount %, markup %, glue/labour rate) — those
// were never persisted, so a flooring line's exact original inputs can't
// be recovered once saved. Editing still works by pre-filling the Add Line
// form with whatever CAN be recovered (product, quantity/length/width/
// drop, discount) plus the CURRENT price book defaults for anything else —
// but Save now PUTs to the SAME line id through edit_flooring_line()/
// edit_blinds_line()/edit_trim_line()/edit_misc_line() (main.py), the same
// trusted calc functions add_*_line() itself uses, just updating the
// existing row in place instead of delete-then-recreate. The line's id
// (and therefore its position — see _quote_line_sort_key(), main.py) never
// changes. Manual Override survival on an edited line is handled entirely
// server-side (see _reapply_line_calc_respecting_override(), main.py) —
// this file never has to reason about it beyond showing the
// override_cleared flag the backend hands back.
//
// Stairwell (confirmed Aug 2026, Vinyl Quoting UX Redesign proposal §09,
// approved) — the exclusion noted above is CLOSED, not still open.
// edit_stairwell_line() (main.py) mirrors the others exactly. Two things
// genuinely can't be recovered, same "prefill what's recoverable, default
// the rest" honesty as flooring's own limitation just above — nosing
// product isn't stored as its own id on QuoteLineItem at all (only baked
// into product_name/cost totals), and stair_area_m2 isn't persisted
// either (only its downstream billed_vinyl_area_m2 is) — both left at the
// form's own current defaults rather than guessed at from the display
// string. landing_area_m2 IS stored (as one aggregate figure, not
// itemized rows) — prefilled as a single landing row carrying that total,
// so saving without touching landings doesn't silently zero it out.
let editingLineId = null;

function editQuoteLine(lineId) {
  const line = currentQuoteLinesCache.find(l => l.id === lineId);
  if (!line) return;
  // Carpet (confirmed Aug 2026, Carpet Tab, Type Split, and Product
  // Filtering brief) — checked BEFORE the generic category==='flooring'
  // branch below: a carpet line IS category==='flooring' underneath
  // (deliberately, see lineSummaryBucket()'s own comment above), but it
  // must open the Carpet tab/card, never Vinyl's #fjMain — the exact
  // routing mistake this whole brief exists to fix, now closed for the
  // Edit path too, not just Add.
  document.getElementById('line_category').value = line.carpet_category ? 'carpet' : line.category;
  toggleLineFields().then(() => {
    if (line.carpet_category) {
      prefillCarpetEdit(line);
    } else if (line.category === 'flooring') {
      prefillFlooringEdit(line);
    } else if (line.category === 'blinds') {
      document.getElementById('line_product').value = line.product_id;
      document.getElementById('line_width').value = line.width_mm || '';
      document.getElementById('line_drop').value = line.drop_mm || '';
      document.getElementById('line_discount').value = ((line.discount_pct || 0) * 100);
    } else if (line.category === 'trim' || line.category === 'skirting') {
      document.getElementById('line_product').value = line.product_id;
      document.getElementById('line_length').value = line.length_m || '';
      document.getElementById('line_discount').value = ((line.discount_pct || 0) * 100);
    } else if (line.category === 'misc') {
      document.getElementById('line_misc_desc').value = line.product_name || '';
      document.getElementById('line_misc_amount').value = line.unit_price || 0;
      document.getElementById('line_misc_cost').value = line.unit_cost || 0;
    } else if (line.category === 'stairwell') {
      // Editability — every category, in place (confirmed Aug 2026,
      // Vinyl Quoting UX Redesign proposal §09, approved) — see this
      // function's own doc comment above for exactly what can/can't be
      // recovered and why.
      document.getElementById('line_stair_vinyl').value = line.product_id;
      document.getElementById('line_num_stairs').value = line.num_stairs || '';
      document.getElementById('line_stairwell_type').value = line.stairwell_type || 'closed';
      document.getElementById('line_stair_area').value = 0.45;   // not recoverable — see comment above
      document.getElementById('line_stair_own_staff').value = line.own_staff === false ? 'false' : 'true';
      // Nosing product not recoverable either — left at whatever the
      // dropdown's own default/first option is; re-selecting it is a
      // deliberate, visible part of confirming this edit, not silently
      // guessed at from the formatted product_name string.
      clearLandingRows();
      if (line.landing_area_m2) {
        addLandingRow();
        document.querySelector('.landing-area-input').value = line.landing_area_m2;
        recomputeLandingTotal();
      }
    }
    // Live preview (confirmed Aug 2026, Vinyl Quoting UX Redesign
    // proposal §01/§10, Phase 4, approved) — toggleLineFields() above
    // already called this once, but before any of the prefill values
    // just above were set, so it only showed the empty-fields state.
    // Re-run now that the real line's values are in place — no-op for
    // flooring (its own live preview already runs via
    // prefillFlooringEdit()'s fjOnIncludeChange()/fjCalc()) and misc
    // (no preview box exists for it).
    previewGenericLine();
    editingLineId = lineId;
    const banner = document.getElementById('editLineBanner');
    if (banner) {
      banner.style.display = '';
      banner.querySelector('span').textContent = line.category === 'flooring'
        ? 'Editing this line — adjust the fields, then click "Add Floor Job to Quote" to save your changes, or Cancel.'
        : 'Editing this line — adjust the fields, then click "Add Line" to save your changes, or Cancel.';
    }
    document.getElementById('addLineCard').scrollIntoView({ behavior: 'smooth' });
  });
}

function prefillFlooringEdit(line) {
  document.getElementById('fj_floor_m2').value = line.quantity_m2 || '';
  // Edit Quote Line In Place (confirmed Aug 2026, brief §1 — "should be
  // checked against Screed... since they share the quote builder"): this
  // used to ALWAYS prefill as a vinyl line, even when editing a real
  // screed line — a genuine gap the brief asked to be checked for before
  // building. flooring_pricing_type ("material" | "screed", set on the
  // line at creation — see add_flooring_line()/edit_flooring_line(),
  // main.py) is what distinguishes them; branch on it instead of assuming.
  const isScreed = line.flooring_pricing_type === 'screed';
  document.getElementById('fj_include_vinyl').checked = !isScreed;
  document.getElementById('fj_include_screed').checked = isScreed;   // editing ONLY this one line — a companion vinyl/screed line on the same quote, if any, is a separate line item and untouched
  const product = flooringProducts.find(p => p.id === line.product_id);
  if (isScreed) {
    if (product) {
      document.getElementById('fj_screed_product').value = product.id;
      document.getElementById('fj_screed_jobtype').value = line.job_type || 'smooth';
      applyScreedRateForJobType();   // pre-fills fj_screed_rate from this product's CURRENT price book rate (see this function's own doc comment for why the ORIGINAL rate can't be recovered instead) and defaults fj_tile_removal by job type
      // Override applyScreedRateForJobType()'s job-type-based DEFAULT for
      // the tile removal fee with what this line was ACTUALLY saved with.
      document.getElementById('fj_tile_removal').checked = !!line.tile_removal_fee_total;
    } else {
      alert('The original screed product for this line no longer exists in the price book — pick the replacement product manually before saving.');
    }
  } else if (product) {
    populateVinylRangeDropdown(product.product_name);
    document.getElementById('fj_vinyl_colour').value = product.id;
    onVinylColourChange();   // sets fj_vinyl_product + pre-fills wastage/box price/trade discount/markup/labour/courier from this product's CURRENT price book entry (see this function's own doc comment for why the ORIGINAL inputs can't be recovered instead)
  } else {
    // Real edge case worth guarding, not silently mismatching: the
    // product this line was originally quoted against no longer exists
    // in the price book at all (hard-deleted, not just discontinued).
    // Whatever range/colour happened to be selected by default is NOT
    // the original product — say so rather than let it look correct.
    alert('The original product for this line no longer exists in the price book — pick the replacement product manually before saving.');
  }
  fjOnIncludeChange();
  fjCalc();
}

function cancelLineEdit() {
  editingLineId = null;
  const banner = document.getElementById('editLineBanner');
  if (banner) banner.style.display = 'none';
}

// Revert to Original (confirmed Aug 2026, Add-Line Data-Loss brief §5)
// — restores the state captured when this quote was opened
// (snapshot_quote(), called from openQuoteFromIndex()/createQuote()).
// One level of undo back to "what was last saved," not a full multi-
// version history, per the brief's own explicit scope.
async function revertQuoteToOriginal() {
  if (!currentQuoteId) return;
  if (!confirm('Discard every change made in this editing session and return to how this quote looked when you opened it?\n\nThis cannot be undone.')) return;
  cancelLineEdit();   // an in-progress edit is exactly the kind of unsaved change this is meant to discard too
  const res = await fetch(`${API}/quotes/${currentQuoteId}/revert`, { method: 'POST' });
  if (!res.ok) { const body = await res.json().catch(() => ({})); alert(body.detail || 'Could not revert this quote.'); return; }
  loadQuote();
}

async function changeLineColour(lineId) {
  const newColour = prompt('New colour:');
  if (!newColour) return;
  const reason = prompt('Reason for the change (e.g. "out of stock at supplier"):') || '';
  const changedBy = currentRole();
  const params = new URLSearchParams({new_colour: newColour, reason, changed_by: changedBy});
  const res = await fetch(`${API}/quotes/${currentQuoteId}/lines/${lineId}/colour?${params}`, {method:'PUT'});
  if (!res.ok) { const err = await res.json(); alert('Error: ' + (err.detail || 'could not change colour')); return; }
  const result = await res.json();
  // Edit Quote Line In Place brief (confirmed Aug 2026) — a colour swap
  // clears an active Manual Override on this line too, same rule as a
  // product change (see change_line_colour(), main.py).
  if (result.override_cleared) alert('This line had a Manual Override applied — because the colour changed, the override was cleared. Reconfirm the override if one is still needed.');
  loadQuote();
}

// Manual Override, Owner-only (confirmed Aug 2026, Manual Override
// brief — urgent real use case: matching a job already quoted/
// accepted/deposit-paid in Burgert's OLD pre-Bolton system exactly).
// The backend's own require_owner is the real enforcement (Sales/Admin
// get a 403 even if they somehow triggered this); these buttons are
// hidden from them client-side too, per the badge-visible/action-Owner-
// only split in the line-row and total-display templates above. A
// reason is required for every apply — mirrors the backend's own 400
// if it's blank, so a Sales/Admin bypass attempt (or a stray call)
// fails the same way either side checks it.
async function overrideLinePrice(lineId, currentValue) {
  const newValueStr = prompt(`Enter the manual override price for this line (currently R${currentValue.toFixed(2)}):`, currentValue.toFixed(2));
  if (newValueStr === null) return;
  const newValue = parseFloat(newValueStr);
  if (isNaN(newValue) || newValue < 0) { alert('Enter a valid, non-negative number.'); return; }
  const reason = prompt('Reason for this override (required — e.g. "Matching accepted price from legacy system"):');
  if (!reason || !reason.trim()) { alert('A reason is required to apply a manual override.'); return; }
  const res = await fetch(`${API}/quotes/${currentQuoteId}/lines/${lineId}/override`, {
    method: 'PUT', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({new_value: newValue, reason: reason.trim()}),
  });
  if (!res.ok) { const body = await res.json().catch(() => ({})); alert(body.detail || 'Could not apply override.'); return; }
  loadQuote();
}

async function revertLineOverride(lineId) {
  if (!confirm('Revert this line back to its calculated value?')) return;
  const res = await fetch(`${API}/quotes/${currentQuoteId}/lines/${lineId}/revert-override`, {method: 'POST'});
  if (!res.ok) { const body = await res.json().catch(() => ({})); alert(body.detail || 'Could not revert override.'); return; }
  loadQuote();
}

async function overrideQuoteTotal(currentValue) {
  const newValueStr = prompt(`Enter the manual override total, incl. VAT (currently R${currentValue.toFixed(2)}):`, currentValue.toFixed(2));
  if (newValueStr === null) return;
  const newValue = parseFloat(newValueStr);
  if (isNaN(newValue) || newValue < 0) { alert('Enter a valid, non-negative number.'); return; }
  const reason = prompt('Reason for this override (required — e.g. "Matching accepted price from legacy system"):');
  if (!reason || !reason.trim()) { alert('A reason is required to apply a manual override.'); return; }
  const res = await fetch(`${API}/quotes/${currentQuoteId}/override-total`, {
    method: 'PUT', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({new_value: newValue, reason: reason.trim()}),
  });
  if (!res.ok) { const body = await res.json().catch(() => ({})); alert(body.detail || 'Could not apply override.'); return; }
  loadQuote();
}

async function revertQuoteTotalOverride() {
  if (!confirm("Revert this quote's total back to the calculated value?")) return;
  const res = await fetch(`${API}/quotes/${currentQuoteId}/revert-total-override`, {method: 'POST'});
  if (!res.ok) { const body = await res.json().catch(() => ({})); alert(body.detail || 'Could not revert override.'); return; }
  loadQuote();
}

async function viewColourHistory(lineId) {
  const res = await fetch(`${API}/quotes/${currentQuoteId}/lines/${lineId}/colour-history`);
  const data = await res.json();
  let msg = `Original colour: ${data.original_colour}\nCurrent colour: ${data.current_colour}\n\nChange history:\n`;
  data.changes.forEach(c => {
    msg += `\n${new Date(c.changed_at).toLocaleString('en-ZA')} — ${c.old_colour} → ${c.new_colour}\nBy: ${c.changed_by || 'unknown'}\nReason: ${c.reason || '(no reason given)'}\n`;
  });
  alert(msg);
}

// Transport Levy (confirmed Aug 2026, Courier Toggle brief Section 6) —
// manual, job-level, one-off amount, its own field/endpoint, set
// independently, any time, not tied to adding a line. Discount % (below)
// now follows this exact same "set/change after the fact" PUT pattern —
// it used to be the one exception, only ever applied via addFloorJob()
// as a side effect of building a floor job line (real bug, fixed Aug
// 2026, Full Real-Browser Walkthrough & Audit: silently dropped by Save
// Quote, unreachable on a quote with no Flooring line at all).
async function updateTransportLevy() {
  if (!currentQuoteId) return;   // toggle can be flicked before a quote even exists yet
  const value = parseFloat(document.getElementById('fj_transport_amount').value) || 0;
  await fetch(`${API}/quotes/${currentQuoteId}/transport-levy?transport_levy=${value}`, { method: 'PUT' });
  loadQuote();   // refresh the totals so the new levy is reflected immediately
}

// Quote-level Discount (confirmed Aug 2026, Full Real-Browser Walkthrough
// & Audit) — same pattern as updateTransportLevy() just above, and the
// exact same backend endpoint addFloorJob() used to call as a side
// effect. Fires the moment the field is changed (onchange, index.html),
// regardless of which category tab is active or whether this quote has
// any Flooring line at all — no longer tied to "Add Floor Job to Quote".
async function updateQuoteDiscount() {
  if (!currentQuoteId) return;   // field can be typed into before a quote even exists yet
  const value = parseFloat(document.getElementById('fj_discount_pct').value) / 100 || 0;
  await fetch(`${API}/quotes/${currentQuoteId}/discount?discount_pct=${value}`, { method: 'PUT' });
  loadQuote();   // refresh the totals so the new discount is reflected immediately
}

// Transport toggle (confirmed Aug 2026, Transport/Courier Toggle
// Relocation brief) — OFF genuinely means "applies nothing", not just
// "hidden": zeroes the amount and pushes that to the backend immediately,
// same as if Burgert had typed 0 in by hand. ON reveals the field but
// deliberately does NOT push anything until a real amount is typed —
// switching it on with no value yet must not silently write a stray 0.
function onTransportToggleChange() {
  const on = document.getElementById('fj_transport_toggle').checked;
  const field = document.getElementById('fj_transport_amount_field');
  field.style.display = on ? '' : 'none';
  if (!on) {
    document.getElementById('fj_transport_amount').value = 0;
    updateTransportLevy();
  }
}

// Cached by every loadQuote() (confirmed Aug 2026, Client-Side
// Commercial Workflow brief, Sprint B — "Edit quantity, product, job
// type, extras") — editQuoteLine() below needs the FULL line record
// (product_id, job_type, discount_pct, etc.), not just what's rendered
// in the table's own cells, to pre-fill an edit form.
let currentQuoteLinesCache = [];
// Cached alongside the lines (confirmed Aug 2026, Client-Side Commercial
// Workflow brief, Sprint D — "Allow CONTROLLED post-accept adjustments")
// — used by confirmPostAcceptChange() below to decide whether adding/
// deleting a line needs an explicit heads-up first.
let currentQuoteStatus = 'draft';
// Real client_id of the currently-open quote (confirmed Aug 2026, Save
// Redirect + Client Link Missing brief) — separate from pendingClientId
// (which is only ever for a NEW, not-yet-created quote): this is what
// the Quote Builder's own "Duplicate Quote" button passes to
// duplicateQuoteFromIndex() so "duplicate for the same client" is
// always a real, validated client_id, never re-derived from whatever
// text happens to be sitting in the q_client field.
let currentQuoteClientId = null;
// Quote Photo Attachments, Phase 1 pilot (confirmed Aug 2026) —
// object URLs created per thumbnail (see loadQuotePhotos()) so they
// can be revoked on the next load instead of quietly leaking memory
// every time a different quote is opened in the same session.
let currentQuotePhotos = [];
let quotePhotoObjectUrls = [];

async function loadQuote() {
  if (!currentQuoteId) return;
  const res = await fetch(`${API}/quotes/${currentQuoteId}?role=${currentRole()}`);
  const data = await res.json();
  currentQuoteLinesCache = data.lines;
  renderFloorPrepRoomCards();
  document.getElementById('quotePhotosCard').style.display = 'block';
  loadQuotePhotos();
  if (data.quote && data.quote.workflow_status) { currentQuoteStatus = data.quote.workflow_status; }
  currentQuoteClientId = data.quote ? (data.quote.client_id || null) : null;
  // Page Title in Sticky Header brief (confirmed Aug 2026) -- upgrades
  // the generic "New Quote" title once a real quote/Price Check is
  // actually loaded.
  if (data.quote) {
    setPageTitle(data.quote.is_price_check
      ? `Price Check #${currentQuoteId}`
      : `Quote #${currentQuoteId}${data.quote.client_name ? ' — ' + data.quote.client_name : ''}`);
  }
  const descEl = document.getElementById('q_description');
  if (descEl && data.quote) descEl.value = data.quote.description || '';
  const printInvoiceBtn = document.getElementById('printInvoiceBtn');
  if (printInvoiceBtn) { printInvoiceBtn.style.display = POST_ACCEPT_LOCKED_STATUSES.includes(currentQuoteStatus) ? '' : 'none'; }
  const sendInvoiceBtn = document.getElementById('sendInvoiceBtn');
  if (sendInvoiceBtn) { sendInvoiceBtn.style.display = POST_ACCEPT_LOCKED_STATUSES.includes(currentQuoteStatus) ? '' : 'none'; }
  // Read-only status badge (confirmed Aug 2026, Order Index / Job
  // Workflow Redesign brief) — replaces the old q_status <select>;
  // workflow_status only changes via an action on the Job Detail screen
  // (order-index.js), so this screen just reflects it, with a link
  // straight there for anyone who wants to change it.
  const statusDisplayEl = document.getElementById('q_status_display');
  if (statusDisplayEl && data.quote) {
    // Price Check (confirmed Aug 2026, New Quote Screen brief §3) — a
    // persistent, hard-to-miss indicator for as long as this quote
    // stays flagged, right alongside the normal status badge, with the
    // one action that matters most (convert it into a real, tracked
    // quote) always one click away. "Manage in Job Detail" is
    // deliberately NOT shown for a Price Check — that screen (Accept/
    // Decline, payment tracking) doesn't apply to something that isn't
    // a real job yet.
    statusDisplayEl.innerHTML = data.quote.is_price_check
      ? `<b style="color:var(--coral); background:#fbe0db; padding:2px 10px; border-radius:10px; font-size:11px;">PRICE CHECK — not a saved job</b> <button onclick="convertPriceCheckToQuote()" style="margin-left:6px;">Convert to real quote</button>`
      : `${workflowStatusBadge(data.quote)} <a href="#" onclick="goToTab('landing'); openOrderDetailScreen(${currentQuoteId}); return false;" style="font-size:12px; margin-left:6px;">Manage in Job Detail →</a>`;
  }
  // Order Details field population REMOVED from here (confirmed Aug
  // 2026, Quote Builder Layout Corrections brief) — that card no longer
  // lives on this screen at all; see renderOrderDetail() (order-index.js)
  // for the equivalent "reflect whatever's actually stored" population,
  // now scoped to the Order Index's own Order Details screen instead.
  // Transport Levy (confirmed Aug 2026, relocated into the floor job
  // calculator — Transport/Courier Toggle Relocation brief) — reflect
  // whatever's actually stored, not just whatever's sitting in the
  // input from a previous quote load. Quote-level, not per-room: this
  // is the ONE toggle/field in the whole page, so re-populating it here
  // on every loadQuote() (which runs after every add/edit/delete,
  // regardless of which room triggered it) is what keeps it correctly
  // showing the single shared value no matter how many rooms are on
  // this quote.
  const levyAmountEl = document.getElementById('fj_transport_amount');
  const levyToggleEl = document.getElementById('fj_transport_toggle');
  const levyFieldEl = document.getElementById('fj_transport_amount_field');
  if (levyAmountEl && data.quote) {
    const levy = data.quote.transport_levy || 0;
    levyAmountEl.value = levy;
    if (levyToggleEl) levyToggleEl.checked = levy > 0;
    if (levyFieldEl) levyFieldEl.style.display = levy > 0 ? '' : 'none';
  }
  // Quote-level Discount (confirmed Aug 2026, Full Real-Browser
  // Walkthrough & Audit — this used to be a write-only field, never
  // populated from the real saved value at all, since it used to only
  // ever get READ at the moment "Add Floor Job to Quote" was clicked,
  // never displayed back). Same "reflect whatever's actually stored, on
  // every loadQuote()" reasoning as Transport Levy just above — without
  // this, reopening a quote with a real discount already applied would
  // misleadingly show 0.
  const discountPctEl = document.getElementById('fj_discount_pct');
  if (discountPctEl && data.quote) { discountPctEl.value = ((data.quote.discount_pct || 0) * 100); }
  const tbody = document.querySelector('#linesTable tbody');
  // Skirting/Trim category fix (confirmed Aug 2026, Vinyl Redesign:
  // Real Usage Findings brief §1) — l.category === 'skirting' now
  // exists as a real, distinct stored value (see _trim_line_category(),
  // main.py) — every category check below that used to only match
  // 'trim' needs 'skirting' too, or a real skirting line's own length
  // silently goes missing from its own detail column.
  // Owner-Only Calculation Breakdown Toggle (confirmed Aug 2026) — the
  // muted cost-reveal info lines below (glue/gripper/underfelt/cutting-
  // fee/labour/delivery) used to render unconditionally whenever the
  // underlying field was present — which, before today, meant "whenever
  // role !== sales" (strip_sensitive_fields, main.py), since Admin got
  // the real fields too. Admin now gets the same server-side strip Sales
  // always had (that function's own comment), so these already stop
  // appearing for Admin from data absence alone, no frontend change
  // needed there — but Owner still receives the real fields always, so
  // the frontend needs its own gate for Owner specifically: only when
  // the breakdown toggle is actually on.
  const showBreakdown = currentRole() === 'owner' && ownerBreakdownVisible;
  tbody.innerHTML = data.lines.map(l => {
    // Persistent Summary Panel / Carpet Tab integration gap (confirmed
    // Aug 2026) — carpet_category is now set on a NEXBAC 920 Tile line
    // too (add_flooring_line()/edit_flooring_line(), main.py — the fix
    // for that bug), so it takes this branch like the other 3 carpet
    // types now, but a Tile line is genuinely m²-only (the EXISTING
    // Vinyl box endpoint's own shape, never had quantity_lm at all) —
    // without this check it printed "null LM (10 m²)" the moment that
    // fix shipped, a real regression caught live, not assumed away.
    let detail = (l.category === 'flooring' && l.carpet_category === 'carpet_tile')
      ? `${l.quantity_m2} m² — ${CARPET_TYPE_LABELS[l.carpet_category] || l.carpet_category}`
      : (l.category === 'flooring' && l.carpet_category)
      ? `${l.quantity_lm} LM (${l.quantity_m2} m²) — ${CARPET_TYPE_LABELS[l.carpet_category] || l.carpet_category}`
      : l.category === 'flooring'
      ? `${l.quantity_m2} m² — ${l.job_type}`
      : (l.category === 'trim' || l.category === 'skirting')
      ? `${l.length_m} lm`
      : l.category === 'stairwell'
      ? `${l.num_stairs} stairs — ${l.stairwell_type}, ${l.nosing_length_m}m nosing, ${l.boxes_needed} boxes (${l.billed_vinyl_area_m2}m² vinyl billed, ${l.glue_area_m2}m² glue coverage)${l.landing_area_m2 ? ` — incl. ${l.landing_area_m2}m² landing (R${l.landing_sell_total.toFixed(2)})` : ''}`
      : l.category === 'misc'
      ? '—'
      : (l.width_mm ? `${l.width_mm}×${l.drop_mm}mm` : `<span class="hidden-note">measurements hidden</span>`);
    if (showBreakdown && l.category === 'flooring' && l.glue_cost_total > 0) {
      detail += `<br><span class="muted">glue: R${l.glue_cost_total.toFixed(2)} (drawn from stock, ~${l.glue_units_needed} drum${l.glue_units_needed !== 1 ? 's' : ''} worth)${l.labour_cost_total > 0 ? ' — +labour R'+l.labour_cost_total.toFixed(2) : ''}</span>`;
    }
    // Carpet Calculators (confirmed Aug 2026) — gripper/underfelt/cutting-
    // fee visibility, same "muted info line" pattern glue/labour above
    // already use. All three are confirmed stock items (never on an
    // Order Sheet, see generate_order_sheets()'s own comment, main.py).
    // Owner-Only Calculation Breakdown Toggle (confirmed Aug 2026) — now
    // gated behind showBreakdown too: these are real cost-composition
    // figures, the same class as everything else this toggle governs.
    if (showBreakdown && l.carpet_category && l.gripper_cost_total > 0) {
      detail += `<br><span class="muted">grippers: R${l.gripper_cost_total.toFixed(2)} (drawn from stock, ${l.gripper_perimeter_m}m perimeter)</span>`;
    }
    if (showBreakdown && l.carpet_category && l.underfelt_cost_total > 0) {
      detail += `<br><span class="muted">underfelt: R${l.underfelt_cost_total.toFixed(2)} (drawn from stock, ${l.underfelt_area_m2}m²)</span>`;
    }
    if (showBreakdown && l.carpet_category && l.cutting_fee_total > 0) {
      detail += `<br><span class="muted">cutting fee: R${l.cutting_fee_total.toFixed(2)}</span>`;
    }
    if (showBreakdown && l.category === 'stairwell' && l.glue_cost_total > 0) {
      detail += `<br><span class="muted">glue: R${l.glue_cost_total.toFixed(2)} (drawn from stock, ~${l.glue_units_needed} drum${l.glue_units_needed !== 1 ? 's' : ''} worth)</span>`;
    }
    if (l.category === 'flooring' && l.bags_allowed > 0) {
      detail += `<br><span class="muted">${l.bags_allowed} bags included (R${(businessSettings?.bag_overage_rate ?? 350).toFixed(2)}/bag if more used on site)</span>`;
      if (l.tile_removal_fee_total > 0) {
        detail += `<br><span class="muted">+tile removal R${l.tile_removal_fee_total.toFixed(2)}</span>`;
      }
    }
    if (showBreakdown && (l.category === 'flooring' || l.category === 'stairwell') && l.labour_charged_total > 0) {
      detail += `<br><span class="muted">labour: R${l.labour_charged_total.toFixed(2)} charged (${l.own_staff ? 'own staff — pure margin' : 'outside — real cost'})</span>`;
    }
    // Courier/delivery fee visibility (confirmed Aug 2026, Courier
    // Toggle brief — "not a pricing change, add visibility"): this was
    // computed and stored all along (delivery_fee_total) but never shown
    // anywhere on screen, internal or otherwise. Real bug found and
    // fixed while adding this (main.py's strip_sensitive_fields):
    // delivery_fee_total was DOCUMENTED as sales-hidden but never
    // actually added to the strip list — fixed there first, so this
    // field genuinely won't be present here for the sales role now, no
    // extra role check needed on this side, same pattern glue/labour
    // above already rely on. Owner-Only Calculation Breakdown Toggle
    // (confirmed Aug 2026) — same real-cost class, gated the same way.
    if (showBreakdown && l.category === 'flooring' && l.delivery_fee_total > 0) {
      detail += `<br><span class="muted">delivery/courier: R${l.delivery_fee_total.toFixed(2)} (marked up with the rest of the line, not a separate charge)</span>`;
    }
    const qty = l.category === 'flooring' ? (l.quantity_m2 || 1) : ((l.category === 'trim' || l.category === 'skirting') ? (l.length_m || 1) : 1);
    const totalCost = (l.category === 'flooring' || l.category === 'stairwell') && l.total_job_cost !== undefined
      ? l.total_job_cost
      : (l.unit_cost !== undefined ? l.unit_cost * qty : undefined);
    const cost = totalCost !== undefined ? `R${totalCost.toFixed(2)}` : '—';
    const margin = l.margin_pct !== undefined ? `${(l.margin_pct*100).toFixed(1)}%` : '—';
    const wasChanged = l.colour && l.original_colour && l.colour !== l.original_colour;
    const colourHtml = l.colour
      ? `<br><b style="color:var(--teal); font-size:12px;">${l.colour}</b>${wasChanged ? `<br><span class="muted" style="font-size:11px; color:var(--coral);" title="Click to see full change history">⚠️ changed from: ${l.original_colour}</span>` : ''}
         <br><a onclick="changeLineColour(${l.id})" style="font-size:11px; color:var(--teal); cursor:pointer; font-weight:600;">Change colour</a>
         ${wasChanged ? ` · <a onclick="viewColourHistory(${l.id})" style="font-size:11px; color:var(--teal); cursor:pointer; font-weight:600;">History</a>` : ''}`
      : (l.category === 'flooring' ? `<br><span class="muted" style="font-size:11px; color:var(--coral);">No colour set</span><br><a onclick="changeLineColour(${l.id})" style="font-size:11px; color:var(--teal); cursor:pointer; font-weight:600;">Add colour</a>` : '');
    // Manual Override, Owner-only (confirmed Aug 2026, Manual Override
    // brief) — badge shown to every internal role (never mistaken for a
    // normal calculated value, per the brief's own requirement), the
    // Override/Revert action links themselves Owner-only. Never touches
    // the client-facing printed doc: buildPrintDocHtml() (shared.js)
    // only ever reads l.line_total directly, no badge/reason rendered
    // there at all.
    const overrideBadge = l.pre_override_line_total != null
      ? `<br><span class="muted" style="font-size:10.5px; color:var(--coral); font-weight:700;" title="${(l.override_reason || '').replace(/"/g,'&quot;')} — by ${l.override_by || ''}${l.override_at ? ' on ' + new Date(l.override_at).toLocaleDateString('en-ZA') : ''}">✏️ Manually adjusted</span>`
      : '';
    const overrideAction = currentRole() === 'owner'
      ? (l.pre_override_line_total != null
          ? `<br><a onclick="revertLineOverride(${l.id})" style="font-size:10.5px; color:var(--teal); cursor:pointer; font-weight:600;">Revert to calculated (R${l.pre_override_line_total.toFixed(2)})</a>`
          : `<br><a onclick="overrideLinePrice(${l.id}, ${l.line_total})" style="font-size:10.5px; color:var(--teal); cursor:pointer; font-weight:600;">Override price</a>`)
      : '';
    return `<tr>
      <td data-label="Category"><span class="badge ${l.category}">${l.category}</span></td>
      <td class="card-title" data-label="Product">${l.product_name}${colourHtml}</td><td data-label="Detail">${detail}</td>
      <td data-label="Price">R${l.line_total.toFixed(2)}${overrideBadge}${overrideAction}</td>
      <td class="cost-col" data-label="Cost">${cost}</td><td class="cost-col" data-label="Margin">${margin}</td>
      <!-- Editability — every category, in place (confirmed Aug 2026,
      Vinyl Quoting UX Redesign proposal §09, approved) — REAL GAP
      CLOSED: Edit used to be explicitly hidden for stairwell rows here
      (the only category with no in-place edit path — deleting and
      re-adding from scratch was the only option). Now offered for
      every category, same as the other five. -->
      <td class="card-actions-cell" data-label=""><button onclick="editQuoteLine(${l.id})" style="margin-right:6px;">Edit</button><button class="delete-btn" onclick="deleteQuoteLine(${l.id})">Delete</button></td>
    </tr>`;
  }).join('');

  // Real bug found while merging v54: this recomputed incl-VAT locally,
  // hardcoded at *1.15 regardless of Business Settings' configured VAT
  // % — so this on-screen total could silently disagree with the
  // printed quote (which uses total_incl_vat straight from the
  // backend). Fixed by using the same backend-computed figure instead
  // of a second, drift-prone calculation of the same number.
  const vatPct = businessSettings?.vat_pct ?? 0.15;
  const inclVat = data.total_incl_vat;
  // Transport Levy shown as its own clearly labelled line (confirmed
  // Aug 2026, brief Section 6) — the backend already folds it into
  // subtotal_ex_vat/total_ex_vat/total_incl_vat (same discount/VAT
  // treatment as any other line item), so this is purely a display
  // addition, not a second calculation of anything. Blank/0 by default
  // — only shown at all when Burgert has actually set one.
  const transportLevy = data.quote && data.quote.transport_levy;
  const transportLevyLine = transportLevy ? `<br>Transport levy: R${transportLevy.toFixed(2)}` : '';
  let totalText = `Total ex VAT: R${data.total_ex_vat.toFixed(2)}${transportLevyLine}<br><span style="color:var(--teal);">Total incl. VAT (${(vatPct*100).toFixed(0)}%): R${inclVat.toFixed(2)}</span>`;
  // Manual Override on the quote's overall total, Owner-only (confirmed
  // Aug 2026, Manual Override brief) — same badge-visible-to-all,
  // action-Owner-only pattern as the per-line override above. Never
  // shown to the client: buildPrintDocHtml() only ever reads
  // data.total_incl_vat directly, already the (possibly overridden)
  // real figure, no badge/reason baked into that number.
  const totalOverride = data.quote && data.quote.manual_override_total_incl_vat != null;
  if (totalOverride) {
    totalText += `<br><span class="muted" style="font-size:11px; color:var(--coral); font-weight:700;" title="${(data.quote.override_total_reason || '').replace(/"/g,'&quot;')} — by ${data.quote.override_total_by || ''}${data.quote.override_total_at ? ' on ' + new Date(data.quote.override_total_at).toLocaleDateString('en-ZA') : ''}">✏️ Total manually adjusted</span>`;
  }
  if (currentRole() === 'owner') {
    totalText += totalOverride
      ? `<br><a onclick="revertQuoteTotalOverride()" style="font-size:11px; color:var(--teal); cursor:pointer; font-weight:600;">Revert total to calculated value</a>`
      : `<br><a onclick="overrideQuoteTotal(${inclVat})" style="font-size:11px; color:var(--teal); cursor:pointer; font-weight:600;">Override total</a>`;
  }
  // Owner-Only Calculation Breakdown Toggle (confirmed Aug 2026) — this
  // used to be one combined line, gated `!== 'sales'` (Owner+Admin both
  // got cost AND margin). Split per the brief's own §0 exception:
  // overall_margin_pct is now sent to every role (get_quote(), main.py)
  // and shown to every role here, unconditionally; overall_cost_ex_vat
  // is only ever sent to Owner at all (same endpoint), so it's only
  // ever shown here too, and only with the breakdown toggle on.
  if (data.overall_margin_pct !== undefined) {
    const pct = (data.overall_margin_pct * 100).toFixed(1);
    const flag = data.overall_margin_pct < 0.30 ? ' ⚠️' : ' ✓';
    totalText += `<br><span style="font-size:14px; font-weight:600;">Overall margin: ${pct}%${flag}</span>`;
    if (data.overall_cost_ex_vat !== undefined && showBreakdown) {
      totalText += ` <span class="muted" style="font-size:12px; font-weight:400;">(Overall cost: R${data.overall_cost_ex_vat.toFixed(2)})</span>`;
    }
  }
  document.getElementById('quoteTotal').innerHTML = totalText;
  // Persistent summary panel (confirmed Aug 2026, Vinyl Quoting UX
  // Redesign proposal §03, approved, Phase 3) — reuses inclVat, the
  // SAME backend-computed total_incl_vat this function's own total
  // line above already uses, never a second, independently-summed
  // total that could disagree with it (discount/VAT/transport
  // levy/Manual Override all already folded into this one figure
  // server-side).
  renderQuoteSummaryPanel(inclVat);
  applyRoleVisibility();
}

// ===== Quote Photo Attachments, Phase 1 pilot (confirmed Aug 2026) =====
// Site context Burgert can review before committing time/stock —
// upload, thumbnail gallery, click to view full size, staff can
// delete. No annotation/editing/versioning, deliberately, per the
// brief. Quote-level only: every call below is scoped to
// currentQuoteId, and the backend itself also filters by quote_id, so
// a client's other quotes never show these.

async function loadQuotePhotos() {
  if (!currentQuoteId) return;
  const res = await fetch(`${API}/quotes/${currentQuoteId}/photos`);
  currentQuotePhotos = res.ok ? await res.json() : [];
  renderQuotePhotoGallery();
}

function renderQuotePhotoGallery() {
  const el = document.getElementById('quotePhotoGallery');
  if (!el) return;
  // Revoke last round's object URLs before building new ones — see
  // clearStaleQuoteResidue()'s comment for why this matters.
  quotePhotoObjectUrls.forEach(url => URL.revokeObjectURL(url));
  quotePhotoObjectUrls = [];
  if (!currentQuotePhotos.length) {
    el.innerHTML = '<p class="muted" style="margin:0;">No photos on this quote yet.</p>';
    return;
  }
  el.innerHTML = currentQuotePhotos.map(p => `
    <div class="quote-photo-thumb" id="photoThumb${p.id}">
      <div class="photo-loading">Loading…</div>
      <button class="photo-delete-btn" title="Delete photo" onclick="event.stopPropagation(); deleteQuotePhoto(${p.id})">✕</button>
      ${p.uploaded_by === 'builder' ? '<span class="photo-badge">Builder</span>' : ''}
    </div>`).join('');
  // Thumbnails load as blob object URLs, not a plain <img src="...">,
  // because this endpoint requires the Bearer auth header the global
  // fetch() wrapper attaches — a plain <img> tag has no way to send
  // that header (same reason a plain <a href> download link never
  // could either — see the HR Documents download-link fix alongside
  // this brief for the other place that same gap was found live).
  currentQuotePhotos.forEach(async (p) => {
    try {
      const res = await fetch(`${API}/quotes/${currentQuoteId}/photos/${p.id}/file`);
      if (!res.ok) throw new Error('fetch failed');
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      quotePhotoObjectUrls.push(url);
      const thumbEl = document.getElementById(`photoThumb${p.id}`);
      if (thumbEl) {
        const img = document.createElement('img');
        img.src = url;
        img.onclick = () => openPhotoLightbox(url);
        thumbEl.prepend(img);
        const loadingEl = thumbEl.querySelector('.photo-loading');
        if (loadingEl) loadingEl.remove();
      }
    } catch (e) {
      const thumbEl = document.getElementById(`photoThumb${p.id}`);
      if (thumbEl) { const l = thumbEl.querySelector('.photo-loading'); if (l) l.textContent = 'Failed'; }
    }
  });
}

function openPhotoLightbox(url) {
  document.getElementById('photoLightboxImg').src = url;
  document.getElementById('photoLightbox').style.display = 'flex';
}

function closePhotoLightbox() {
  document.getElementById('photoLightbox').style.display = 'none';
}

async function uploadQuotePhotos() {
  if (!currentQuoteId) return;
  const input = document.getElementById('quotePhotoInput');
  const statusEl = document.getElementById('quotePhotoUploadStatus');
  const files = Array.from(input.files || []);
  if (!files.length) { statusEl.textContent = 'Choose one or more photos first.'; return; }
  statusEl.textContent = `Uploading ${files.length} photo${files.length !== 1 ? 's' : ''}…`;
  let uploaded = 0, failed = 0;
  for (const file of files) {
    const body = new FormData();
    body.append('file', file);
    try {
      const res = await fetch(`${API}/quotes/${currentQuoteId}/photos`, { method: 'POST', body });
      if (res.ok) { uploaded++; }
      else {
        failed++;
        const err = await res.json().catch(() => ({}));
        statusEl.textContent = err.detail || `Couldn't upload ${file.name}.`;
      }
    } catch (e) {
      failed++;
    }
  }
  input.value = '';
  statusEl.textContent = failed
    ? `${uploaded} uploaded, ${failed} failed — see above.`
    : `${uploaded} photo${uploaded !== 1 ? 's' : ''} uploaded ✓`;
  await loadQuotePhotos();
}

async function deleteQuotePhoto(photoId) {
  if (!confirm('Delete this photo? This cannot be undone.')) return;
  const res = await fetch(`${API}/quotes/${currentQuoteId}/photos/${photoId}`, { method: 'DELETE' });
  if (!res.ok) { alert('Could not delete photo.'); return; }
  await loadQuotePhotos();
}
