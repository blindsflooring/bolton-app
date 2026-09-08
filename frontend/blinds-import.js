// Blinds Quote Import (confirmed Sept 2026, "Blinds Quote Import
// (Excel -> Order Index)" brief).
//
// Blinds are quoted in an Excel template and go on being quoted there.
// This screen brings a FINISHED blinds quote into Bolton so it counts
// toward KPIs and rep commission — explicitly "without moving blinds
// pricing/calculation logic into Bolton itself". Nothing here prices a
// blind; it shows what the sheet says and asks for a yes.
//
// Two steps on purpose, matching the supplier price-sheet import: the
// upload PREVIEWS and writes nothing, and a separate Import button
// commits. This creates real money in the Order Index, so it does not
// happen on an upload alone.
//
// The file is held in memory between the two steps and re-sent on
// commit, because the backend re-parses it rather than trusting posted
// figures — see commit_blinds_import()'s own docstring for why.

let blindsImportFile = null;
let blindsImportParsed = null;
let blindsImportReplaceId = null;

function blindsImportCardHtml() {
  return `
    <div class="card" id="blindsImportCard">
      <h2>Import a Blinds Quote</h2>
      <p class="muted">
        Upload the finished blinds quote from the Excel template. Nothing is saved until you
        review what was read and click Import — and the spreadsheet stays the source of truth
        for blinds pricing, so a revision means re-importing the updated file, not editing it here.
      </p>
      <div class="field">
        <label>Blinds quote spreadsheet (.xlsx)</label>
        <input type="file" id="blindsImportInput" accept=".xlsx,.xlsm" onchange="onBlindsImportFileChosen()">
      </div>
      <div id="blindsImportResult"></div>
    </div>`;
}

async function onBlindsImportFileChosen() {
  const input = document.getElementById('blindsImportInput');
  const out = document.getElementById('blindsImportResult');
  blindsImportParsed = null;
  blindsImportReplaceId = null;
  blindsImportFile = input.files && input.files[0];
  if (!blindsImportFile) { out.innerHTML = ''; return; }

  out.innerHTML = `<p class="muted"><span class="mini-spinner"></span>Reading ${blindsImportFile.name}...</p>`;
  const body = new FormData();
  body.append('file', blindsImportFile);
  let res, data;
  try {
    res = await fetch(`${API}/admin/blinds-import/preview`, { method: 'POST', body });
    data = await res.json();
  } catch (e) {
    out.innerHTML = `<p class="error-text">Couldn't reach the server (${e.message}). Nothing was imported.</p>`;
    return;
  }
  if (!res.ok) {
    // The backend's rejection message names the exact cell or row —
    // shown verbatim, because "invalid file" would send someone hunting
    // through 40 rows for something the server already located.
    out.innerHTML = `<div class="import-reject"><b>This sheet wasn't imported.</b><br>${data.detail || 'Unknown error.'}</div>`;
    return;
  }
  blindsImportParsed = data;
  renderBlindsImportPreview();
}

function renderBlindsImportPreview() {
  const d = blindsImportParsed;
  const out = document.getElementById('blindsImportResult');
  const money = R;
  const rows = d.lines.map(l => `
    <tr>
      <td>${l.item_no || ''}</td>
      <td>${l.room || '<span class="muted">—</span>'}</td>
      <td>${l.blind_type}</td>
      <td>${l.colour || '<span class="muted">—</span>'}</td>
      <td>${l.width_mm}×${l.drop_mm}${l.side ? ' ' + l.side : ''}</td>
      <td>${l.qty}</td>
      <td>${money(l.book_price_ex_vat)}</td>
      <td class="muted">${money(l.cost_ex_vat)}</td>
    </tr>`).join('');

  // The rep. The template's Rep cell is a formula pulling the client
  // reference (the brief's own open item), so it cannot attribute
  // commission — this asks, rather than guessing or leaving it blank.
  const repOpts = d.rep_options.map(u =>
    `<option value="${u.username}">${u.display_name || u.username}</option>`).join('');

  const replaceHtml = d.replaces.length ? `
    <div class="import-replace">
      <b>This looks like a revision.</b> ${d.replaces.length} quote${d.replaces.length === 1 ? '' : 's'}
      already imported with the reference <b>${d.client.reference}</b>:
      <div style="margin-top:6px;">
        ${d.replaces.map(r => `
          <label style="display:block; margin:3px 0; font-weight:600; cursor:pointer;">
            <input type="radio" name="blindsReplace" value="${r.id}" style="width:auto; margin-right:6px;"
                   onchange="blindsImportReplaceId=${r.id}; renderBlindsImportAction();">
            ${r.job_number || '#' + r.id} — ${r.client_name} (${r.workflow_status}${r.sales_owner ? ', ' + r.sales_owner : ''})
            ${r.already_started ? '<span class="import-warn-inline">this job has already moved past Quoted — replacing its lines will change what it is worth</span>' : ''}
          </label>`).join('')}
        <label style="display:block; margin:3px 0; font-weight:600; cursor:pointer;">
          <input type="radio" name="blindsReplace" value="" style="width:auto; margin-right:6px;" checked
                 onchange="blindsImportReplaceId=null; renderBlindsImportAction();">
          None of these — import as a brand-new job
        </label>
      </div>
    </div>` : '';

  const warnHtml = d.warnings.length
    ? `<div class="import-warn">${d.warnings.map(w => `<div>${w}</div>`).join('')}</div>` : '';

  out.innerHTML = `
    ${warnHtml}
    <div class="grid" style="margin-top:10px;">
      <div class="field"><label>Client</label><div><b>${d.client.name}</b></div></div>
      <div class="field"><label>Reference (D15)</label><div>${d.client.reference || '<span class="muted">—</span>'}</div></div>
      <div class="field"><label>Branch (D48)</label><div><b>${d.branch_code}</b> → ${d.branch}</div></div>
      <div class="field"><label>Phone (D16)</label><div>${d.client.phone || '<span class="muted">—</span>'}</div></div>
    </div>
    <div class="field"><label>Address (D13)</label><div>${d.client.address || '<span class="muted">—</span>'}</div></div>

    <div style="overflow-x:auto;">
      <table class="import-lines">
        <thead><tr>
          <th>#</th><th>Room</th><th>Blind</th><th>Colour</th><th>W×D</th><th>Qty</th>
          <th>Client price ex VAT</th><th>Our cost ex VAT</th>
        </tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>

    <div class="import-totals">
      <div><span class="muted">Subtotal ex VAT</span><b>${money(d.totals.subtotal_ex_vat)}</b></div>
      <div><span class="muted">VAT</span><b>${money(d.totals.vat)}</b></div>
      <div><span class="muted">Total incl VAT</span><b>${money(d.totals.total_incl_vat)}</b></div>
      <div><span class="muted">Deposit on sheet</span><b>${d.totals.deposit == null ? '—' : money(d.totals.deposit)}</b></div>
    </div>
    <!-- The cost side, shown because it is the half the sheet does NOT
         state and therefore the half nobody can check by eye. -->
    <div class="import-totals import-totals-cost">
      <div><span class="muted">Our cost ex VAT</span><b>${money(d.cost.cost_ex_vat)}</b></div>
      <div><span class="muted">Our cost incl VAT</span><b>${money(d.cost.cost_incl_vat)}</b></div>
      <div><span class="muted">Gross profit ex VAT</span><b>${money(d.cost.gross_profit_ex_vat)}</b></div>
      <div><span class="muted">Margin</span><b>${d.cost.margin_pct.toFixed(1)}%</b></div>
    </div>
    <p class="muted" style="font-size:11px;">
      Cost = book price less ${(d.cost.trade_discount_pct * 100).toFixed(0)}% trade discount,
      less ${(d.cost.settlement_discount_pct * 100).toFixed(1)}% settlement discount.
      Shown ex VAT because that is what every other margin figure in Bolton compares against;
      the incl-VAT figure beside it is what actually leaves the bank.
    </p>

    ${replaceHtml}

    <div class="field" style="max-width:320px;">
      <label>Rep this quote belongs to <span class="adj">(required — for commission)</span></label>
      <select id="blindsImportRep" onchange="renderBlindsImportAction()">
        <option value="">— Choose the rep —</option>
        ${repOpts}
      </select>
      <div class="muted" style="font-size:11px; margin-top:4px;">
        ${d.rep.usable
          ? `The sheet's Rep cell reads "${d.rep.raw}" — confirm it here anyway.`
          : `Not taken from the sheet: ${d.rep.reason} Until the template has a real Rep field, this has to be chosen by hand.`}
      </div>
    </div>
    <div id="blindsImportAction"></div>`;
  renderBlindsImportAction();
}

// The Import button is only ever enabled with a rep chosen — commission
// attribution is the point of the import, and a quote landing with no
// rep is a silent gap nobody would notice until payday.
function renderBlindsImportAction() {
  const rep = document.getElementById('blindsImportRep');
  const el = document.getElementById('blindsImportAction');
  if (!rep || !el) return;
  const ready = !!rep.value;
  el.innerHTML = `
    <button onclick="commitBlindsImport()" ${ready ? '' : 'disabled'}
            title="${ready ? '' : 'Choose the rep first'}">
      ${blindsImportReplaceId ? 'Replace that job with this sheet' : 'Import as a new job'}
    </button>
    ${ready ? '' : '<span class="muted" style="margin-left:8px; font-size:12px;">Choose the rep first.</span>'}`;
}

async function commitBlindsImport() {
  const rep = document.getElementById('blindsImportRep').value;
  if (!rep || !blindsImportFile) return;
  const d = blindsImportParsed;
  const what = blindsImportReplaceId
    ? `Replace the lines on that existing job with this sheet?\n\nThe job keeps its job number, status and payment record — only its lines and totals are replaced.`
    : `Import ${d.lines.length} blind(s) for ${d.client.name} as a new job?\n\nTotal ${R(d.totals.total_incl_vat)} incl VAT.`;
  if (!confirm(what)) return;

  const el = document.getElementById('blindsImportAction');
  el.innerHTML = '<p class="muted"><span class="mini-spinner"></span>Importing...</p>';
  const body = new FormData();
  body.append('file', blindsImportFile);
  const params = new URLSearchParams({ sales_owner: rep });
  if (blindsImportReplaceId) params.set('replace_quote_id', blindsImportReplaceId);
  const res = await fetch(`${API}/admin/blinds-import/commit?${params}`, { method: 'POST', body });
  const data = await res.json();
  if (!res.ok) {
    el.innerHTML = `<div class="import-reject"><b>Not imported.</b><br>${data.detail || 'Unknown error.'}</div>`;
    return;
  }
  document.getElementById('blindsImportResult').innerHTML = `
    <div class="import-done">
      <b>${data.replaced ? 'Replaced' : 'Imported'}: ${data.job_number || '#' + data.quote_id}</b> —
      ${data.client_name}, ${data.lines_imported} line(s), ${R(data.totals.total_incl_vat)} incl VAT,
      ${data.branch}, rep ${data.sales_owner}.
      <div style="margin-top:6px;">
        <a href="#" onclick="openOrderDetailScreen(${data.quote_id}); return false;">Open the job</a>
      </div>
    </div>`;
  blindsImportFile = null;
  blindsImportParsed = null;
  blindsImportReplaceId = null;
  document.getElementById('blindsImportInput').value = '';
  // The Order Index's own cache is now stale — the new job isn't in it.
  // Re-rendered rather than just cleared, so the row (and its Blinds
  // badge) is on screen immediately, which is the confirmation that
  // actually matters.
  if (typeof renderLanding === 'function' && landingView === 'orders') {
    const el = document.getElementById('landing');
    const done = document.getElementById('blindsImportResult').innerHTML;
    await renderOrderIndex(el, '');
    const slot = document.getElementById('blindsImportResult');
    if (slot) slot.innerHTML = done;
  }
}
