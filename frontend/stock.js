// ===== Material stock on hand (confirmed Sept 2026) =====
//
// Screed, glue, slurry and bondite, counted daily and deducted as jobs
// consume them. Built after a real near-miss: the business ran short of
// bonding liquid and slurry with no warning at all, and had no idea how
// much glue or screed was on hand.
//
// THIS FILE RENDERS; IT DOES NOT CALCULATE. Every figure below comes off
// GET /stock/overview, which is the one place on-hand, needed, on-order
// and short-by are worked out (stock_overview(), main.py). A second
// opinion computed in the browser is how a tile ends up disagreeing with
// the screen you opened to check it.
//
// THE ONE RULE THAT MATTERS FOR READING THESE TILES: null is not zero.
// A material nobody has counted yet shows "Not counted", never "0 bags".
// A tile reading zero when nobody has looked is the exact false
// confidence this feature exists to remove, and it would be worse than
// the gap it replaces because somebody would book work against it.

function stockQty(value, unitLabel) {
  if (value === null || value === undefined) return '—';
  // Quarter drums read as quarters, whole bags read as whole numbers.
  const rounded = Math.round(value * 100) / 100;
  return (Number.isInteger(rounded) ? rounded : rounded.toFixed(2).replace(/0$/, ''))
    + (unitLabel ? ' ' + unitLabel : '');
}

// The unmissable one. Deliberately NOT a tile among tiles — the brief
// asks for a warning that "surfaces on its own", and something that
// looks like the other tiles is something you stop seeing by Thursday.
function stockAlertHtml(data) {
  const short = data.short || [];
  const never = data.never_counted || [];
  const drifted = (data.materials || []).filter(m => m.last_variance_flagged);
  if (!short.length && !never.length && !drifted.length) return '';

  const blocks = [];
  if (short.length) {
    blocks.push(`
      <div class="stock-alert stock-alert-short">
        <div class="stock-alert-title">⚠️ Not enough ${short.map(m => m.label).join(', ')} for the work on hand</div>
        ${short.map(m => `
          <div class="stock-alert-line">
            <b>${m.label}</b> — ${stockQty(m.on_hand, m.unit_label)} on hand${m.on_order ? ` + ${stockQty(m.on_order, m.unit_label)} on order` : ', none on order'},
            but jobs on hand need ${stockQty(m.needed, m.unit_label)}.
            <b>Short by ${stockQty(m.short_by, m.unit_label)}.</b>
          </div>`).join('')}
      </div>`);
  }
  if (never.length) {
    blocks.push(`
      <div class="stock-alert stock-alert-unknown">
        <div class="stock-alert-title">Never counted: ${never.map(m => m.label).join(', ')}</div>
        <div class="stock-alert-line">
          Bolton cannot tell you what is on the shelf until somebody counts it once.
          It will not guess, and it will not show a zero it does not know.
          <span class="stock-alert-action" onclick="openStockCount()">Do the first count</span>
        </div>
      </div>`);
  }
  if (drifted.length) {
    blocks.push(`
      <div class="stock-alert stock-alert-drift">
        <div class="stock-alert-title">A count did not match what Bolton expected</div>
        ${drifted.map(m => `
          <div class="stock-alert-line">
            <b>${m.label}</b> — counted ${stockQty(m.last_counted_qty, m.unit_label)} on
            ${m.last_counted_on}, which is ${stockQty(Math.abs(m.last_variance), m.unit_label)}
            ${m.last_variance > 0 ? 'more' : 'less'} than the jobs can account for.
          </div>`).join('')}
      </div>`);
  }
  return blocks.join('');
}

// Bold and large, per the brief — this is meant to be readable from
// across the room without opening anything.
function stockTilesHtml(data) {
  const materials = data.materials || [];
  if (!materials.length) return '';
  return `
    <div class="stock-section">
      <div class="stock-section-head">
        <h2>Stock on hand</h2>
        <button class="primary" onclick="openStockCount()">Count stock</button>
      </div>
      <div class="stock-tile-grid">
        ${materials.map(m => `
          <div class="stock-tile ${m.is_short ? 'stock-tile-short' : ''} ${m.never_counted ? 'stock-tile-unknown' : ''}"
               onclick="openStockCount('${m.key}')">
            <div class="stock-tile-label">${m.label}</div>
            <div class="stock-tile-figure">${m.never_counted
              ? '<span class="stock-tile-unknown-text">Not counted</span>'
              : stockQty(m.on_hand, '')}</div>
            <div class="stock-tile-unit">${m.never_counted ? '' : m.unit_label}${m.pack_note ? ` · ${m.pack_note}` : ''}</div>
            <div class="stock-tile-detail">
              <span>Needed <b>${stockQty(m.needed, '')}</b></span>
              <span>On order <b>${stockQty(m.on_order, '')}</b></span>
            </div>
            ${m.is_short ? `<div class="stock-tile-flag">Short by ${stockQty(m.short_by, m.unit_label)}</div>` : ''}
            ${m.never_counted ? '' : `<div class="stock-tile-foot">Counted ${m.last_counted_on}${m.last_counted_by ? ' by ' + m.last_counted_by : ''}</div>`}
          </div>`).join('')}
      </div>
    </div>`;
}

// Home must never wait on a fetch — same rule the Ask Bolton scope and
// the money sections both follow. The placeholders are already on screen
// when this runs.
async function loadHomeStock() {
  const tiles = document.getElementById('homeStockTiles');
  const alert = document.getElementById('homeStockAlert');
  if (!tiles && !alert) return;
  try {
    const res = await fetch(`${API}/stock/overview`);
    if (!res.ok) return;
    const data = await res.json();
    if (tiles) tiles.innerHTML = stockTilesHtml(data);
    if (alert) alert.innerHTML = stockAlertHtml(data);
  } catch (e) { /* best-effort — a stock read must never block Home */ }
}

function openStockCount(materialKey) {
  stockFocusKey = materialKey || null;
  landingView = 'stockCount';
  renderLanding();
}

let stockFocusKey = null;

// The daily count screen.
//
// One row per material, each showing what Bolton EXPECTS before anything
// is typed. That is deliberate and it is the whole design: the brief's
// worry is somebody re-entering yesterday's number without going to
// look, and showing the expected figure makes the disagreement visible
// at the moment of entry rather than in a report afterwards.
//
// The quarter buttons exist because a part-used drum is eyeballed, not
// measured — and because a free-text box invites "3.3 drums", which the
// server rejects and which wastes a trip to the shelf.
async function renderStockCount(el) {
  setPageTitle('Stock Count');
  el.innerHTML = '<div class="card"><p class="muted">Loading stock…</p></div>';
  let data;
  try {
    const res = await fetch(`${API}/stock/overview`);
    if (!res.ok) throw new Error('overview');
    data = await res.json();
  } catch (e) {
    el.innerHTML = `
      <span class="back-link" onclick="landingView='tiles'; renderLanding();">← Back</span>
      <div class="card"><p>Could not load stock right now. Check your connection and try again.</p></div>`;
    return;
  }

  el.innerHTML = `
    <span class="back-link" onclick="landingView='tiles'; renderLanding();">← Back to Home</span>
    <div class="landing-welcome">
      <h1>Stock Count</h1>
      <p>What is actually on the shelf, today. Count it, don't remember it —
         the figure beside each one is what Bolton thinks is there, and the
         point of counting is to catch it being wrong.</p>
    </div>
    ${(data.materials || []).map(m => stockCountRowHtml(m)).join('')}
  `;
  if (stockFocusKey) {
    const focus = document.getElementById('stockInput_' + stockFocusKey);
    if (focus) { focus.focus(); focus.scrollIntoView({ block: 'center' }); }
    stockFocusKey = null;
  }
}

function stockCountRowHtml(m) {
  const steps = [];
  // Whole units plus the quarters between them, up to something a person
  // would plausibly have on a shelf. Typed entry stays available for more.
  for (let whole = 0; whole <= 6; whole++) {
    for (let q = 0; q < 4; q++) {
      const value = whole + q * (m.increment || 1);
      if (m.increment >= 1 && q > 0) continue;
      if (value > 6) break;
      steps.push(value);
    }
  }
  return `
    <div class="card stock-count-row" id="stockRow_${m.key}">
      <div class="stock-count-head">
        <div>
          <h3>${m.label}</h3>
          <p class="muted">${m.pack_note} · counted in ${m.unit_label}${m.increment < 1 ? ` (¼ ${m.unit_label} steps)` : ''}</p>
        </div>
        <div class="stock-count-expected">
          <div class="muted">Bolton expects</div>
          <div class="stock-count-expected-figure">${m.never_counted
            ? '<span class="muted">nothing yet</span>'
            : stockQty(m.on_hand, m.unit_label)}</div>
          ${m.never_counted ? '' : `<div class="muted">${stockQty(m.consumed_since_count, m.unit_label)} used since ${m.last_counted_on}</div>`}
        </div>
      </div>
      <div class="stock-count-steps">
        ${steps.map(v => `<button type="button" class="stock-step" onclick="setStockInput('${m.key}', ${v})">${stockQty(v, '')}</button>`).join('')}
      </div>
      <div class="stock-count-entry">
        <label for="stockInput_${m.key}">Counted</label>
        <input type="number" id="stockInput_${m.key}" step="${m.increment || 1}" min="0"
               placeholder="${m.unit_label}">
        <input type="text" id="stockNote_${m.key}" placeholder="Note (optional)">
        <button class="primary" onclick="submitStockCount('${m.key}')">Save count</button>
      </div>
      <div class="stock-count-result" id="stockResult_${m.key}"></div>
    </div>`;
}

function setStockInput(key, value) {
  const input = document.getElementById('stockInput_' + key);
  if (input) { input.value = value; input.focus(); }
}

async function submitStockCount(key) {
  const input = document.getElementById('stockInput_' + key);
  const note = document.getElementById('stockNote_' + key);
  const out = document.getElementById('stockResult_' + key);
  if (!input || input.value === '') { if (out) out.innerHTML = '<div class="stock-result-bad">Enter what you counted first.</div>'; return; }
  const body = { material_key: key, counted_qty: parseFloat(input.value), note: note ? note.value : '' };
  try {
    const res = await fetch(`${API}/stock/count`, {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body),
    });
    const result = await res.json();
    if (!res.ok) {
      out.innerHTML = `<div class="stock-result-bad">${result.detail || 'Could not save that count.'}</div>`;
      return;
    }
    // The server's own sentence, not one rebuilt here — the message and
    // the arithmetic behind it must never be able to disagree.
    out.innerHTML = `<div class="${result.variance_flagged ? 'stock-result-flag' : 'stock-result-ok'}">${result.message}</div>`;
    const row = document.getElementById('stockRow_' + key);
    if (row) row.classList.toggle('stock-row-flagged', !!result.variance_flagged);
    const expected = row ? row.querySelector('.stock-count-expected-figure') : null;
    if (expected) expected.textContent = stockQty(result.material.on_hand, result.material.unit_label);
  } catch (e) {
    out.innerHTML = '<div class="stock-result-bad">Could not save that count — check your connection.</div>';
  }
}
