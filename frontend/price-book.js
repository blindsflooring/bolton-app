// ===== PRICE BOOK =====
// Flooring/blinds/trim price book management: load, render as a
// collapsible tree, add, delete, bulk import. Confirmed Aug 2026, Stage
// 2 of the foundation refactor, second extraction (after shared.js).
// CATEGORY_LABELS deliberately stayed in shared.js, not here — it's
// used by this file's renderFlooringTree AND by renderFlooringDrill()
// (a landing-page browsing view, staying in index.html) — a real
// cross-file dependency, not something that could cleanly live in only
// one feature file. TRIM_CATEGORY_LABELS used to stay local here for
// exactly that reason -- it had no second caller. It does now (the quote
// builder groups its trim dropdowns by category, Sept 2026), so by this
// same rule it moved to shared.js rather than being duplicated.

async function loadFlooring() {
  const res = await fetch(`${API}/price-book/flooring`);
  flooringProducts = await res.json();
  document.getElementById('flooringTree').innerHTML = renderFlooringTree(flooringProducts);
  refreshLineProductOptions();
  refreshFlooringJobsPicker();   // same list, so the picker can never offer a product the book no longer has
}

function renderFlooringTree(products) {
  if (!products.length) return '<p class="muted">No flooring products yet.</p>';
  const byCategory = {};
  products.forEach(p => {
    const cat = p.flooring_category || 'vinyl';
    (byCategory[cat] = byCategory[cat] || []).push(p);
  });
  return Object.keys(byCategory).sort().map(cat => {
    const items = byCategory[cat];
    const bySupplier = {};
    items.forEach(p => (bySupplier[p.supplier] = bySupplier[p.supplier] || []).push(p));
    const supplierHtml = Object.keys(bySupplier).sort().map(supplier => {
      const rows = sortByPriority(bySupplier[supplier]).map(p => `
        <tr>
          <td>${p.product_name}</td>
          <td>${p.colour || '—'}</td>
          <td><span class="badge ${p.pricing_type === 'screed' ? 'flooring' : 'blinds'}">${p.pricing_type}</span></td>
          <td>R${p.base_cost_ex_vat.toFixed(2)}</td>
          <td>R${(p.pricing_type === 'screed' ? p.base_cost_ex_vat*1 : p.base_cost_ex_vat*(p.sell_markup_multiplier||1)).toFixed(2)}</td>
          <td>R${(p.pricing_type === 'screed' ? p.base_cost_ex_vat*(p.over_tiles_multiplier||1.5) : p.base_cost_ex_vat*(p.sell_markup_multiplier||1)).toFixed(2)}</td>
          <td>R${(p.pricing_type === 'screed' ? p.base_cost_ex_vat*(p.removed_tiles_multiplier||2.0) : p.base_cost_ex_vat*(p.sell_markup_multiplier||1)).toFixed(2)}</td>
          <td><button onclick="showFlooringProductJobs(${p.id})" title="Which accepted/scheduled jobs are waiting on this product?" style="font-size:12px;">Jobs</button>
              <button class="delete-btn" onclick="deleteFlooring(${p.id})">Delete</button></td>
        </tr>`).join('');
      return `<details class="tree-node supplier">
        <summary>${supplier} <span class="tree-count">(${bySupplier[supplier].length})</span></summary>
        <div class="tree-body">
          <table><thead><tr><th>Product</th><th>Colour</th><th>Type</th><th>Base rate</th><th>Smooth</th><th>Over Tiles</th><th>Removed Tiles</th><th></th></tr></thead>
          <tbody>${rows}</tbody></table>
        </div>
      </details>`;
    }).join('');
    return `<details class="tree-node category" open>
      <summary>${CATEGORY_LABELS[cat] || cat} <span class="tree-count">(${items.length})</span></summary>
      <div class="tree-body">${supplierHtml}</div>
    </details>`;
  }).join('');
}

async function deleteFlooring(id) {
  if (!confirm('Delete this flooring product from the price book?')) return;
  await fetch(`${API}/price-book/flooring/${id}`, {method:'DELETE'});
  loadFlooring();
}

async function loadBlinds() {
  const res = await fetch(`${API}/price-book/blinds`);
  blindsProducts = await res.json();
  const tbody = document.querySelector('#blindsTable tbody');
  tbody.innerHTML = blindsProducts.map(p => {
    const netCost = p.book_price * (1-p.trade_discount_pct) * (1-p.settlement_discount_pct);
    const margin = ((p.book_price - netCost) / p.book_price * 100).toFixed(1);
    return `<tr>
      <td>${p.product_name}</td><td>${p.supplier}</td><td>R${p.book_price.toFixed(2)}</td>
      <td>R${p.book_price.toFixed(2)}</td><td class="cost-col">${margin}%</td>
      <td><button class="delete-btn" onclick="deleteBlinds(${p.id})">Delete</button></td>
    </tr>`;
  }).join('');
  refreshLineProductOptions();
  applyRoleVisibility();
}

async function deleteBlinds(id) {
  if (!confirm('Delete this blinds product from the price book?')) return;
  await fetch(`${API}/price-book/blinds/${id}`, {method:'DELETE'});
  loadBlinds();
}

async function addFlooring() {
  const body = {
    product_name: document.getElementById('fl_name').value,
    colour: document.getElementById('fl_colour').value,
    supplier: document.getElementById('fl_supplier').value,
    pricing_type: document.getElementById('fl_pricing_type').value,
    flooring_category: document.getElementById('fl_category').value,
    base_cost_ex_vat: parseFloat(document.getElementById('fl_cost').value),
    wastage_pct: parseFloat(document.getElementById('fl_wastage').value) / 100,
    trade_discount_pct: parseFloat(document.getElementById('fl_discount').value) / 100,
    m2_per_pack: document.getElementById('fl_m2perpack').value ? parseFloat(document.getElementById('fl_m2perpack').value) : null,
    sell_markup_multiplier: parseFloat(document.getElementById('fl_markup').value) || 1.3,
    display_order: parseInt(document.getElementById('fl_display_order').value) || 100,
    delivery_fee_per_m2: parseFloat(document.getElementById('fl_delivery_fee').value) || 0,
    over_tiles_multiplier: parseFloat(document.getElementById('fl_over_tiles_mult').value) || 1.5,
    removed_tiles_multiplier: parseFloat(document.getElementById('fl_removed_tiles_mult').value) || 2.0,
  };
  await fetch(`${API}/price-book/flooring`, {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)});
  loadFlooring();
}

async function bulkImportFlooring() {
  const fileInput = document.getElementById('bulkImportFile');
  if (!fileInput.files.length) { alert('Choose a JSON file first.'); return; }
  const text = await fileInput.files[0].text();
  let products;
  try { products = JSON.parse(text); } catch (e) { alert('That file isn\'t valid JSON.'); return; }
  const res = await fetch(`${API}/price-book/flooring/bulk-import`, {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(products),
  });
  const result = await res.json();
  document.getElementById('bulkImportStatus').textContent = res.ok
    ? `Imported ${result.imported} products ✓`
    : `Error: ${result.detail || 'import failed'}`;
  loadFlooring();
}

async function addBlinds() {
  const body = {
    product_name: document.getElementById('bl_name').value,
    supplier: document.getElementById('bl_supplier').value,
    mechanism: document.getElementById('bl_mechanism').value,
    book_price: parseFloat(document.getElementById('bl_price').value),
  };
  await fetch(`${API}/price-book/blinds`, {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)});
  loadBlinds();
}

function toggleTrimPricingFields() {
  const mode = document.getElementById('tr_pricing_mode').value;
  document.getElementById('tr_fixed_field').style.display = mode === 'fixed' ? '' : 'none';
  document.getElementById('tr_markup_field').style.display = mode === 'markup' ? '' : 'none';
  document.getElementById('tr_vat_field').style.display = mode === 'markup' ? '' : 'none';
}


async function loadTrims() {
  const res = await fetch(`${API}/price-book/trims`);
  trimProducts = await res.json();
  document.getElementById('trimTree').innerHTML = renderTrimTree(trimProducts);
  refreshLineProductOptions();
  applyRoleVisibility();
}

function renderTrimTree(products) {
  if (!products.length) return '<p class="muted">No trim products yet.</p>';
  const byCategory = {};
  products.forEach(p => (byCategory[p.category] = byCategory[p.category] || []).push(p));
  return Object.keys(byCategory).sort().map(cat => {
    const items = byCategory[cat];
    const bySupplier = {};
    items.forEach(p => (bySupplier[p.supplier] = bySupplier[p.supplier] || []).push(p));
    const supplierHtml = Object.keys(bySupplier).sort().map(supplier => {
      const rows = bySupplier[supplier].map(p => {
        const sell = p.pricing_mode === 'fixed' ? p.fixed_sell_price_per_lm : (p.cost_ex_vat_per_lm * p.markup_multiplier);
        const margin = ((sell - p.cost_ex_vat_per_lm) / sell * 100).toFixed(1);
        return `<tr>
          <td>${p.product_name}${p.profile_code ? ' ('+p.profile_code+')' : ''}</td>
          <td class="cost-col">R${p.cost_ex_vat_per_lm.toFixed(2)}</td>
          <td>R${sell.toFixed(2)}</td><td class="cost-col">${margin}%</td>
          <!-- Builder Portal: Trims (confirmed Sept 2026, Burgert's own
          words: "Only one trim, Reducing profile per door width
          opening. Leave skirtings"). Only offered on reducers, mirroring
          _builder_portal_trim()'s own filter (main.py) — showing this on
          a skirting row would let Burgert tick a box that the portal
          then silently ignores. -->
          <td>${p.category === 'reducer'
            ? `<label style="font-size:12px; white-space:nowrap;"><input type="checkbox" ${p.available_to_builder_portal ? 'checked' : ''} onchange="setTrimBuilderPortal(${p.id}, this.checked)"> Builder portal</label>`
            : ''}</td>
          <td><button class="delete-btn" onclick="deleteTrim(${p.id})">Delete</button></td>
        </tr>`;
      }).join('');
      return `<details class="tree-node supplier">
        <summary>${supplier} <span class="tree-count">(${bySupplier[supplier].length})</span></summary>
        <div class="tree-body">
          <table><thead><tr><th>Product</th><th class="cost-col">Cost/lm</th><th>Sell/lm</th><th class="cost-col">Margin</th><th></th><th></th></tr></thead>
          <tbody>${rows}</tbody></table>
        </div>
      </details>`;
    }).join('');
    return `<details class="tree-node category" open>
      <summary>${TRIM_CATEGORY_LABELS[cat] || cat} <span class="tree-count">(${items.length})</span></summary>
      <div class="tree-body">${supplierHtml}</div>
    </details>`;
  }).join('');
}

// Builder Portal: Trims (confirmed Sept 2026). The portal resolves ONE
// reducer (_builder_portal_trim(), main.py — first by name), so ticking
// a second one doesn't add it, it just makes which one wins depend on
// alphabetical order. Untick the current one first, which is what this
// does automatically rather than leaving Burgert to discover it.
async function setTrimBuilderPortal(id, checked) {
  const product = trimProducts.find(p => p.id === id);
  if (!product) return;
  if (checked) {
    const already = trimProducts.find(p => p.id !== id && p.category === 'reducer' && p.available_to_builder_portal);
    if (already && !confirm(`${already.product_name} is currently the Builder Portal trim. Replace it with ${product.product_name}?`)) {
      loadTrims();
      return;
    }
    if (already) {
      await fetch(`${API}/price-book/trims/${already.id}`, {
        method: 'PUT', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({...already, available_to_builder_portal: false}),
      });
    }
  }
  const res = await fetch(`${API}/price-book/trims/${id}`, {
    method: 'PUT', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({...product, available_to_builder_portal: checked}),
  });
  if (!res.ok) alert('Could not update this trim.');
  loadTrims();
}

async function deleteTrim(id) {
  if (!confirm('Delete this trim product from the price book?')) return;
  await fetch(`${API}/price-book/trims/${id}`, {method:'DELETE'});
  loadTrims();
}

async function addTrim() {
  const body = {
    product_name: document.getElementById('tr_name').value,
    profile_code: document.getElementById('tr_code').value,
    category: document.getElementById('tr_category').value,
    supplier: document.getElementById('tr_supplier').value,
    cost_ex_vat_per_lm: parseFloat(document.getElementById('tr_cost').value),
    pricing_mode: document.getElementById('tr_pricing_mode').value,
    fixed_sell_price_per_lm: document.getElementById('tr_fixed_price').value ? parseFloat(document.getElementById('tr_fixed_price').value) : null,
    markup_multiplier: parseFloat(document.getElementById('tr_markup').value) || 1.9636,
    vat_pct: (parseFloat(document.getElementById('tr_vat').value) || 15) / 100,
  };
  await fetch(`${API}/price-book/trims`, {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)});
  loadTrims();
}

// ===== Jobs waiting on a flooring product (confirmed Sept 2026) =====
// The real question this answers, in Burgert's own framing: a product
// has run short — who else is waiting on it? Before this, the only way
// was opening every flooring quote one at a time.
//
// Lives on the Price Book page deliberately (the brief's own "likely the
// flooring price book"): the moment you learn a product is short is the
// moment you are looking at that product, so the answer belongs one
// click from it rather than behind a separate screen to navigate to.
// Two entry points, one code path — the picker below for "I know the
// product", and a Jobs button on every price book row for "I am already
// looking at it".
//
// Picking from the price book rather than typing a name is the whole
// design, not a shortcut — see flooring_product_pending_jobs() (main.py)
// for why a free-text name search gives a genuinely wrong answer here
// (one product_name, e.g. "deZIGN series 200", spans five separate
// price book entries that differ only by colour).

function flooringProductLabel(p) {
  const parts = [p.product_name];
  if (p.product_variant) parts.push(p.product_variant);
  if (p.colour) parts.push(p.colour);
  return `${parts.join(' — ')} (${p.supplier})`;
}

// Rebuilt from flooringProducts (already loaded by loadFlooring()) — no
// second fetch for a list the page is holding anyway.
function refreshFlooringJobsPicker() {
  const sel = document.getElementById('jobsProductPicker');
  if (!sel) return;
  const filter = (document.getElementById('jobsProductFilter')?.value || '').toLowerCase().trim();
  const matches = sortByPriority(flooringProducts || []).filter(p =>
    !filter || flooringProductLabel(p).toLowerCase().includes(filter));
  const previous = sel.value;
  sel.innerHTML = matches.length
    ? matches.map(p => `<option value="${p.id}">${flooringProductLabel(p)}</option>`).join('')
    : '<option value="">No product matches that</option>';
  // Keep the current selection when it survived the filter, so typing to
  // narrow the list doesn't silently re-point the button at a different
  // product than the one already chosen.
  if (previous && matches.some(p => String(p.id) === previous)) sel.value = previous;
  const count = document.getElementById('jobsProductCount');
  if (count) count.textContent = filter ? `${matches.length} of ${(flooringProducts || []).length} products` : '';
}

function showJobsForPickedProduct() {
  const sel = document.getElementById('jobsProductPicker');
  if (!sel || !sel.value) { alert('Pick a flooring product first.'); return; }
  showFlooringProductJobs(parseInt(sel.value, 10));
}

// Called from the price book row button too — jumps the picker to that
// product so the two entry points can never disagree about what is being
// shown.
async function showFlooringProductJobs(productId) {
  const el = document.getElementById('flooringProductJobs');
  if (!el) return;
  // Keep the picker honest about what is actually shown below it. The
  // row button can name a product the text filter currently excludes —
  // leaving the picker on its old selection would put a product name in
  // the control and a DIFFERENT one in the results directly beneath it,
  // so the filter is cleared first and the list rebuilt, rather than
  // silently letting the two disagree.
  const sel = document.getElementById('jobsProductPicker');
  if (sel) {
    if (![...sel.options].some(o => o.value === String(productId))) {
      const filter = document.getElementById('jobsProductFilter');
      if (filter) filter.value = '';
      refreshFlooringJobsPicker();
    }
    if ([...sel.options].some(o => o.value === String(productId))) sel.value = String(productId);
  }
  el.innerHTML = '<p class="muted">Loading...</p>';
  el.scrollIntoView({behavior: 'smooth', block: 'nearest'});
  let data;
  try {
    const res = await fetch(`${API}/price-book/flooring/${productId}/pending-jobs`);
    if (!res.ok) { el.innerHTML = '<p class="muted">Could not load jobs for this product.</p>'; return; }
    data = await res.json();
  } catch (e) {
    el.innerHTML = '<p class="muted">Could not load jobs for this product — check your connection.</p>';
    return;
  }
  el.innerHTML = flooringProductJobsHtml(data);
}

function flooringProductJobsHtml(data) {
  const p = data.product;
  const heading = `<h3 style="margin:0 0 2px;">${flooringProductLabel(p)}</h3>
    <p class="muted" style="margin:0 0 12px; font-size:12px;">Jobs accepted or scheduled and not yet installed. Completed installs and quotes that haven't been accepted are excluded.</p>`;
  // Manual lines carry no price book product (product_id 0 by design) so
  // they cannot be matched here — stated plainly rather than leaving a
  // silently short list to be trusted as complete.
  const caveat = `<p class="muted" style="margin:12px 0 0; font-size:11px;">Hand-typed Engineered Wood / Laminate lines aren't included — those aren't linked to a price book product, so there's no product to match them on.</p>`;
  if (!data.jobs.length) {
    return `${heading}<p class="muted" style="margin:0;">No jobs are waiting on this product — nothing accepted or scheduled has it on.</p>${caveat}`;
  }
  const rows = data.jobs.map(j => {
    // Only the quantities this job actually has, in the unit it's
    // ordered in — a screed line's real number is bags, a material
    // line's is boxes. A missing one is absent, never shown as 0.
    const qty = [
      j.quantity_m2 ? `${(+j.quantity_m2).toFixed(2).replace(/\.00$/, '')} m²` : null,
      j.boxes_needed ? `${j.boxes_needed} boxes` : null,
      j.bags_allowed ? `${j.bags_allowed} bags` : null,
      j.length_m ? `${j.length_m} lm` : null,
    ].filter(Boolean).join(' · ') || '—';
    const colours = j.colours.length ? `<br><span style="font-size:11px; color:var(--teal); font-weight:700;">${j.colours.join(', ')}</span>` : '';
    const multi = j.line_count > 1 ? `<br><span class="muted" style="font-size:11px;">across ${j.line_count} lines on this job</span>` : '';
    const when = j.installation_date
      ? new Date(j.installation_date).toLocaleDateString('en-ZA')
      : '<span class="muted">not scheduled</span>';
    return `<tr>
      <td data-label="Client"><b>${j.client_name || '(no name)'}</b>${colours}</td>
      <td data-label="Job"><a href="#" onclick="goToTab('landing'); openOrderDetailScreen(${j.quote_id}); return false;">${j.job_number || 'Q-' + j.quote_id}</a></td>
      <td data-label="Qty needed">${qty}${multi}</td>
      <td data-label="Status">${workflowStatusBadge(j)}</td>
      <td data-label="Install date">${when}</td>
    </tr>`;
  }).join('');
  return `${heading}
    <p style="margin:0 0 8px; font-size:13px;"><b>${data.jobs.length}</b> job${data.jobs.length === 1 ? '' : 's'} waiting on this product.</p>
    <table class="mobile-card-table">
      <thead><tr><th>Client</th><th>Job</th><th>Qty needed</th><th>Status</th><th>Install date</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>${caveat}`;
}
