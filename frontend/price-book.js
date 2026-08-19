// ===== PRICE BOOK =====
// Flooring/blinds/trim price book management: load, render as a
// collapsible tree, add, delete, bulk import. Confirmed Aug 2026, Stage
// 2 of the foundation refactor, second extraction (after shared.js).
// CATEGORY_LABELS deliberately stayed in shared.js, not here — it's
// used by this file's renderFlooringTree AND by renderFlooringDrill()
// (a landing-page browsing view, staying in index.html) — a real
// cross-file dependency, not something that could cleanly live in only
// one feature file. TRIM_CATEGORY_LABELS has no such second caller, so
// it stays local to this file.

async function loadFlooring() {
  const res = await fetch(`${API}/price-book/flooring`);
  flooringProducts = await res.json();
  document.getElementById('flooringTree').innerHTML = renderFlooringTree(flooringProducts);
  refreshLineProductOptions();
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
          <td><button class="delete-btn" onclick="deleteFlooring(${p.id})">Delete</button></td>
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

const TRIM_CATEGORY_LABELS = {
  skirting: 'Skirting', quarter_round: 'Quarter Round', stair_nose: 'Stair Nose',
  reducer: 'Reducer', carpet_strip: 'Carpet Strip', corner_protector: 'Corner Protector/Angle',
};

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
          <td><button class="delete-btn" onclick="deleteTrim(${p.id})">Delete</button></td>
        </tr>`;
      }).join('');
      return `<details class="tree-node supplier">
        <summary>${supplier} <span class="tree-count">(${bySupplier[supplier].length})</span></summary>
        <div class="tree-body">
          <table><thead><tr><th>Product</th><th class="cost-col">Cost/lm</th><th>Sell/lm</th><th class="cost-col">Margin</th><th></th></tr></thead>
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
