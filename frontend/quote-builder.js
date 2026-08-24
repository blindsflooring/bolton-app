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
// - saveOrderDetails()/logFollowUp()/loadFollowUps() stay in index.html
//   — they serve the Order Details card, which still lives on the Quote
//   Builder page today but is flagged as a separate, still-open
//   relocation task (Quote Builder → Order Index). Moving them here now
//   would tie them to this file right before that relocation might move
//   them again; loadQuote() below still calls the external
//   loadFollowUps() directly, same kind of cross-file call as every
//   other extraction round (e.g. order-index.js calling into
//   index.html's startQuoteForClient()).
// - sortByPriority() moved to shared.js, not here — a real cross-file
//   coupling found during the pre-extraction audit: price-book.js
//   (already extracted) was calling it while it was still only defined
//   in index.html, working purely by script-load timing luck, not by
//   design.

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

async function toggleLineFields() {
  const cat = document.getElementById('line_category').value;
  const isFlooring = cat === 'flooring';
  document.getElementById('fjMain').style.display = isFlooring ? '' : 'none';
  document.getElementById('genericLineCard').style.display = isFlooring ? 'none' : '';
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
  await Promise.all([loadFlooring(), loadBlinds(), loadTrims()]);
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

// Two-step selection (confirmed Aug 2026): pick a Range first, then a
// Colour within it — the colour list depends on which range is chosen,
// since each range has its own set of colour-specific price book entries.
function populateVinylRangeDropdown(preselectRange) {
  const vinylProducts = flooringProducts.filter(p => p.pricing_type === 'material');
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
  const colours = sortByPriority(flooringProducts.filter(p => p.pricing_type === 'material' && p.product_name === range));
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
  await deleteLineBeingEditedIfAny();   // if this is a Save on an in-progress edit, remove the old version of this line first
  const includeVinyl = document.getElementById('fj_include_vinyl').checked;
  const includeScreed = document.getElementById('fj_include_screed').checked;
  const role = currentRole();
  const discountPct = parseFloat(document.getElementById('fj_discount_pct').value) / 100 || 0;

  // Quote-level discount (confirmed Aug 2026: applied to the whole quote, not per line)
  if (discountPct) {
    await fetch(`${API}/quotes/${currentQuoteId}/discount?discount_pct=${discountPct}`, {method:'PUT'});
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

function toggleScreedSubfields() {
  // no longer used — screed prep is now its own dedicated card, not a subfield toggle
}

async function createQuote() {
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
  // Defensive backstop against stale-quote residue (confirmed Aug 2026,
  // Client-Side Commercial Workflow brief) — a brand-new quote is
  // genuinely empty server-side, but the DOM (#linesTable, #quoteTotal,
  // etc.) could still be showing whatever a PREVIOUS quote left behind
  // if this entry point is ever reached without going through
  // resetQuoteBuilderUI() first. Cheap and always correct to clear here
  // too, regardless of how this was reached.
  clearStaleQuoteResidue();
  document.getElementById('quoteStatus').textContent = `Quote #${quote.id} started for ${quote.client_name}.`;
  document.getElementById('addLineCard').style.display = 'block';
  document.getElementById('linesCard').style.display = 'block';
  const startBtn = document.getElementById('startQuoteBtn');
  startBtn.disabled = true;
  startBtn.textContent = 'Already open (see below)';
  pendingClientId = null;
  pendingClientName = null;
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
  pendingCategory = null;
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
  const statusSelect = document.getElementById('q_status');
  if (statusSelect) statusSelect.value = 'draft';
  const saveStatusEl = document.getElementById('saveStatus');
  if (saveStatusEl) saveStatusEl.textContent = '';
  clearLandingRows();   // stairwell landing rows are per-quote scratch state too
  // Real bug found while building Sprint B's line editing: without this,
  // editingLineId could survive into a DIFFERENT quote (started right
  // after cancelling out of an edit mid-flow) — the next "Add" click in
  // that new quote would then wrongly delete a line ID belonging to the
  // PREVIOUS quote.
  cancelLineEdit();
}

function resetQuoteBuilderUI() {
  currentQuoteId = null;
  document.getElementById('q_client').value = '';
  clearStaleQuoteResidue();
  document.getElementById('addLineCard').style.display = 'none';
  document.getElementById('linesCard').style.display = 'none';
  const startBtn = document.getElementById('startQuoteBtn');
  startBtn.disabled = false;
  startBtn.textContent = 'Start Quote';
  document.getElementById('quoteStatus').textContent = '';
}

function startFreshQuote() {
  resetQuoteBuilderUI();
}

async function saveQuote() {
  if (!currentQuoteId) return;
  const params = new URLSearchParams({
    client_name: document.getElementById('q_client').value,
    sales_owner: document.getElementById('q_owner').value,
    branch: document.getElementById('q_branch').value,
    status: document.getElementById('q_status').value,
  });
  await fetch(`${API}/quotes/${currentQuoteId}?${params}`, {method:'PUT'});
  document.getElementById('saveStatus').textContent = `Saved ✓ ${new Date().toLocaleTimeString('en-ZA')} — line items save automatically as you add them; this saves the client/owner/branch/status.`;
  loadQuote();
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
  if (cat !== 'stairwell') { await deleteLineBeingEditedIfAny(); }   // if this is a Save on an in-progress edit, remove the old version of this line first (stairwell lines are never in edit mode — no Edit button offered for them)

  if (cat === 'stairwell') {
    const vinylProductId = document.getElementById('line_stair_vinyl').value;
    const ownStaff = document.getElementById('line_stair_own_staff').value;
    // Landing(s), summed — CHANGED Aug 2026: folded into this SAME
    // stairwell request/line instead of a separate POST to
    // /lines/flooring, so the quote shows one combined stair price, not
    // two lines. Still priced at the standard per-m² flat-flooring rate
    // (same vinyl product, no markup override) — only how it's posted
    // and displayed changed, not the rate or calculation.
    const landingTotal = recomputeLandingTotal();
    const params = new URLSearchParams({
      vinyl_product_id: vinylProductId,
      nosing_product_id: document.getElementById('line_nosing_product').value,
      num_stairs: document.getElementById('line_num_stairs').value,
      stair_area_m2: document.getElementById('line_stair_area').value || 0.45,
      stairwell_type: document.getElementById('line_stairwell_type').value,
      own_staff: ownStaff,
      landing_area_m2: landingTotal,
      role,
    });
    const res = await fetch(`${API}/quotes/${currentQuoteId}/lines/stairwell?${params}`, {method:'POST'});
    const line = await res.json();
    if (line.warning) alert(line.warning);
    if (landingTotal > 0) clearLandingRows();
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

async function deleteQuoteLine(lineId) {
  if (!confirm('Delete this line from the quote?')) return;
  await fetch(`${API}/quotes/${currentQuoteId}/lines/${lineId}`, {method:'DELETE'});
  loadQuote();
}

// Edit an existing line (confirmed Aug 2026, Client-Side Commercial
// Workflow brief, Sprint B — "Edit quantity, product, job type, extras
// without total corruption"). Real constraint found while building
// this: QuoteLineItem only ever stores the CALCULATED outputs
// (unit_cost, line_total, labour_charged_total...), never the raw
// inputs that produced them (wastage %, trade discount %, markup %,
// glue/labour rate) — those were never persisted, so a flooring line's
// exact original inputs can't be recovered once saved. Rather than
// build a second, approximate recalculation engine here (the exact
// class of bug this project has repeatedly found and fixed — a
// frontend shadow-calc silently drifting from the real backend
// formula), editing works by pre-filling the Add Line form with
// whatever CAN be recovered (product, quantity/length/width/drop,
// discount) plus the CURRENT price book defaults for anything else,
// then deleting the old line and creating a fresh one through the SAME
// trusted backend endpoint used for a brand new line (see
// deleteLineBeingEditedIfAny(), called from addFloorJob()/addLine()) —
// so "total corruption" is structurally impossible: the total is
// always freshly computed server-side from whatever lines currently
// exist, never patched in place. Stairwell lines are excluded for now
// (landings/nosing make a faithful pre-fill materially more complex) —
// flagged honestly rather than shipped half-working.
let editingLineId = null;

function editQuoteLine(lineId) {
  const line = currentQuoteLinesCache.find(l => l.id === lineId);
  if (!line) return;
  document.getElementById('line_category').value = line.category;
  toggleLineFields().then(() => {
    if (line.category === 'flooring') {
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
    }
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
  document.getElementById('fj_include_vinyl').checked = true;
  document.getElementById('fj_include_screed').checked = false;   // editing ONLY this vinyl line — a screed line on the same quote, if any, is a separate line item and untouched
  document.getElementById('fj_floor_m2').value = line.quantity_m2 || '';
  const product = flooringProducts.find(p => p.id === line.product_id);
  if (product) {
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

// Called at the top of both addFloorJob() and addLine() — if a line is
// currently being edited, its old version is deleted first so the
// "Add" click that follows creates its replacement rather than an
// extra duplicate line.
async function deleteLineBeingEditedIfAny() {
  if (!editingLineId) return;
  await fetch(`${API}/quotes/${currentQuoteId}/lines/${editingLineId}`, { method: 'DELETE' });
  cancelLineEdit();
}

async function changeLineColour(lineId) {
  const newColour = prompt('New colour:');
  if (!newColour) return;
  const reason = prompt('Reason for the change (e.g. "out of stock at supplier"):') || '';
  const changedBy = currentRole();
  const params = new URLSearchParams({new_colour: newColour, reason, changed_by: changedBy});
  const res = await fetch(`${API}/quotes/${currentQuoteId}/lines/${lineId}/colour?${params}`, {method:'PUT'});
  if (!res.ok) { const err = await res.json(); alert('Error: ' + (err.detail || 'could not change colour')); return; }
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
// manual, job-level, one-off amount, deliberately its own field/endpoint
// separate from the quote-level Discount % above (which is set via
// addFloorJob() as part of building a floor job line) — this is set
// independently, any time, not tied to adding a line. Same "set/change
// after the fact" PUT pattern as the discount endpoint.
async function updateTransportLevy() {
  if (!currentQuoteId) return;   // toggle can be flicked before a quote even exists yet
  const value = parseFloat(document.getElementById('fj_transport_amount').value) || 0;
  await fetch(`${API}/quotes/${currentQuoteId}/transport-levy?transport_levy=${value}`, { method: 'PUT' });
  loadQuote();   // refresh the totals so the new levy is reflected immediately
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

async function loadQuote() {
  if (!currentQuoteId) return;
  const res = await fetch(`${API}/quotes/${currentQuoteId}?role=${currentRole()}`);
  const data = await res.json();
  currentQuoteLinesCache = data.lines;
  const statusEl = document.getElementById('q_status');
  if (statusEl && data.quote && data.quote.status) { statusEl.value = data.quote.status; }
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
  const tbody = document.querySelector('#linesTable tbody');
  tbody.innerHTML = data.lines.map(l => {
    let detail = l.category === 'flooring'
      ? `${l.quantity_m2} m² — ${l.job_type}`
      : l.category === 'trim'
      ? `${l.length_m} lm`
      : l.category === 'stairwell'
      ? `${l.num_stairs} stairs — ${l.stairwell_type}, ${l.nosing_length_m}m nosing, ${l.boxes_needed} boxes (${l.billed_vinyl_area_m2}m² vinyl billed, ${l.glue_area_m2}m² glue coverage)${l.landing_area_m2 ? ` — incl. ${l.landing_area_m2}m² landing (R${l.landing_sell_total.toFixed(2)})` : ''}`
      : l.category === 'misc'
      ? '—'
      : (l.width_mm ? `${l.width_mm}×${l.drop_mm}mm` : `<span class="hidden-note">measurements hidden</span>`);
    if (l.category === 'flooring' && l.glue_cost_total > 0) {
      detail += `<br><span class="muted">glue: R${l.glue_cost_total.toFixed(2)} (drawn from stock, ~${l.glue_units_needed} drum${l.glue_units_needed !== 1 ? 's' : ''} worth)${l.labour_cost_total > 0 ? ' — +labour R'+l.labour_cost_total.toFixed(2) : ''}</span>`;
    }
    if (l.category === 'stairwell' && l.glue_cost_total > 0) {
      detail += `<br><span class="muted">glue: R${l.glue_cost_total.toFixed(2)} (drawn from stock, ~${l.glue_units_needed} drum${l.glue_units_needed !== 1 ? 's' : ''} worth)</span>`;
    }
    if (l.category === 'flooring' && l.bags_allowed > 0) {
      detail += `<br><span class="muted">${l.bags_allowed} bags included (R${(businessSettings?.bag_overage_rate ?? 350).toFixed(2)}/bag if more used on site)</span>`;
      if (l.tile_removal_fee_total > 0) {
        detail += `<br><span class="muted">+tile removal R${l.tile_removal_fee_total.toFixed(2)}</span>`;
      }
    }
    if ((l.category === 'flooring' || l.category === 'stairwell') && l.labour_charged_total > 0) {
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
    // above already rely on.
    if (l.category === 'flooring' && l.delivery_fee_total > 0) {
      detail += `<br><span class="muted">delivery/courier: R${l.delivery_fee_total.toFixed(2)} (marked up with the rest of the line, not a separate charge)</span>`;
    }
    const qty = l.category === 'flooring' ? (l.quantity_m2 || 1) : (l.category === 'trim' ? (l.length_m || 1) : 1);
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
    return `<tr>
      <td><span class="badge ${l.category}">${l.category}</span></td>
      <td>${l.product_name}${colourHtml}</td><td>${detail}</td>
      <td>R${l.line_total.toFixed(2)}</td>
      <td class="cost-col">${cost}</td><td class="cost-col">${margin}</td>
      <td>${l.category === 'stairwell' ? '' : `<button onclick="editQuoteLine(${l.id})" style="margin-right:6px;">Edit</button>`}<button class="delete-btn" onclick="deleteQuoteLine(${l.id})">Delete</button></td>
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
  if (data.overall_margin_pct !== undefined && currentRole() !== 'sales') {
    const pct = (data.overall_margin_pct * 100).toFixed(1);
    const flag = data.overall_margin_pct < 0.30 ? ' ⚠️' : ' ✓';
    totalText += `<br><span style="font-size:14px; font-weight:600;">Overall cost: R${data.overall_cost_ex_vat.toFixed(2)} — Overall margin: ${pct}%${flag}</span>`;
  }
  document.getElementById('quoteTotal').innerHTML = totalText;
  applyRoleVisibility();
}
