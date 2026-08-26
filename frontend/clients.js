// ===== CLIENTS =====
// Client CRM: list/search, add, detail view + order history. Confirmed
// Aug 2026, Stage 2 of the foundation refactor, third extraction.
// Deliberately NOT moved here, consistent with the price-book.js
// decisions: startQuoteForClient() and addClientAndStartQuote() stay in
// index.html — same "handoff into Quote Builder" category as
// startQuoteWithVinylRange, which also stayed put when price-book.js
// was extracted, even though they're triggered from client-related
// pages. onQClientInput() and selectQClient() also stay — they power
// the New Quote form's own client-search-while-typing feature, a Quote
// Builder concern, not client CRM management.

async function renderClients(el, searchTerm) {
  await renderWithRetry(el, 'Clients', async () => {
  el.innerHTML = `<span class="back-link" onclick="landingView='tiles'; renderLanding();">← Back</span><div class="card"><h2>Clients</h2><p class="muted">Loading...</p></div>`;
  const params = searchTerm ? `?search=${encodeURIComponent(searchTerm)}` : '';
  const res = await fetch(`${API}/clients${params}`);
  const clients = await res.json();
  // Mobile Rendering Audit brief (confirmed Aug 2026) -- found needing
  // .mobile-card-table during that brief's own required systematic
  // sweep (this table had no mobile handling at all before -- not even
  // overflow-x:auto -- and email addresses are exactly the kind of
  // unbreakable string that forces a table wider than the screen).
  const rows = clients.length ? clients.map(c => `
    <tr style="cursor:pointer;" onclick="openClientDetail(${c.id})">
      <td class="card-title" data-label="Name">${c.name}</td><td data-label="Phone">${c.phone || '—'}</td><td data-label="Email">${c.email || '—'}</td><td data-label="Branch">${c.preferred_branch}</td>
    </tr>`).join('') : '<tr><td colspan="4" class="muted">No clients match.</td></tr>';
  // Possible Duplicate Clients (confirmed Aug 2026, Order Index ->
  // Client Link Gap brief — "check for and report any other duplicate
  // client records"). Owner-only endpoint (require_owner server-side),
  // so only fetched when actually an owner — a Sales/Admin login would
  // just get a 403 here for no benefit. Exact-name match only (see the
  // endpoint's own comment for why not fuzzy) — only rendered at all
  // when there's genuinely something to show, so this never clutters
  // the screen for the common case of zero duplicates.
  let duplicatesHtml = '';
  if (currentRole() === 'owner') {
    try {
      const dupRes = await fetch(`${API}/admin/duplicate-clients`);
      const dupGroups = dupRes.ok ? await dupRes.json() : [];
      if (dupGroups.length) {
        duplicatesHtml = `
          <div class="card" style="border-color:var(--coral);">
            <h2 style="color:var(--coral);">⚠ Possible Duplicate Clients</h2>
            <p class="muted" style="margin-top:-8px;">Same name (ignoring case/spacing) on more than one client record — check these aren't the same person entered twice before quotes end up split across both.</p>
            ${dupGroups.map(g => `
              <div style="padding:8px 0; border-bottom:1px solid var(--border);">
                ${g.clients.map(c => `<span style="cursor:pointer; color:var(--teal); text-decoration:underline; margin-right:14px;" onclick="openClientDetail(${c.id})">${c.name} <span class="muted">(#${c.id}, ${c.quote_count} quote${c.quote_count!==1?'s':''})</span></span>`).join('')}
              </div>`).join('')}
          </div>`;
      }
    } catch (e) { /* best-effort — never block the whole Clients screen over this */ }
  }
  el.innerHTML = `
    <span class="back-link" onclick="landingView='tiles'; renderLanding();">← Back</span>
    ${duplicatesHtml}
    <div class="card">
      <h2>Clients</h2>
      <div class="field"><label>Search</label><input type="text" id="clientSearchInput" value="${searchTerm || ''}" placeholder="Type to search..." oninput="renderClients(document.getElementById('landing'), this.value)"></div>
      <table class="mobile-card-table"><thead><tr><th>Name</th><th>Phone</th><th>Email</th><th>Branch</th></tr></thead>
      <tbody>${rows}</tbody></table>
    </div>
    <div class="card">
      <h2>Add Client</h2>
      <div class="grid">
        <div class="field"><label>Name</label><input id="cl_name" placeholder="Client name"></div>
        <!-- Client Info: Company Name, VAT Number, Multiple Phones/
             Emails brief (confirmed Aug 2026) — Company name/VAT
             number optional, blank for individuals. -->
        <div class="field"><label>Company name <span class="adj">(optional — leave blank for an individual)</span></label><input id="cl_company_name" placeholder="e.g. Aspeling Builders CC"></div>
        <div class="field"><label>VAT number <span class="adj">(optional)</span></label><input id="cl_vat_number" placeholder="e.g. 4123456789"></div>
        <div class="field" style="grid-column: span 2;">
          <label>Phone</label>
          <div id="cl_phone_list"><div class="addable-row" style="display:flex; gap:6px; margin-bottom:6px;"><input class="cl_phone_entry" placeholder="082 555 1234" style="flex:1;"></div></div>
          <a href="#" onclick="addContactField('cl_phone_list', 'cl_phone_entry', '082 555 1234'); return false;" style="font-size:12px; color:var(--teal); font-weight:600;">+ Add phone</a>
        </div>
        <div class="field" style="grid-column: span 2;">
          <label>Email</label>
          <div id="cl_email_list"><div class="addable-row" style="display:flex; gap:6px; margin-bottom:6px;"><input class="cl_email_entry" placeholder="client@example.com" style="flex:1;"></div></div>
          <a href="#" onclick="addContactField('cl_email_list', 'cl_email_entry', 'client@example.com'); return false;" style="font-size:12px; color:var(--teal); font-weight:600;">+ Add email</a>
        </div>
        <div class="field"><label>Preferred branch</label>
          <!-- Default Branch per Staff (confirmed Aug 2026) — pre-
          selected from whoever's logged in, per-render (this whole card
          is rebuilt fresh each time renderClients() runs, so this
          correctly re-applies every time, never a stale prior choice
          left over from three renders ago). Fully changeable, same as
          always — this is only ever the starting value. -->
          <select id="cl_branch">
            <option value="gansbaai" ${defaultBranchForCurrentUser()==='gansbaai'?'selected':''}>Gansbaai</option>
            <option value="hermanus" ${defaultBranchForCurrentUser()==='hermanus'?'selected':''}>Hermanus</option>
          </select>
        </div>
        <div class="field" style="grid-column: span 2;"><label>Address</label><input id="cl_address" placeholder="Site/delivery address"></div>
        <div class="field" style="grid-column: span 2;"><label>Notes <span class="adj">(anything worth remembering about this client — access instructions, preferences, etc.)</span></label><textarea id="cl_notes" rows="2" style="width:100%; font-family:inherit; font-size:14px; padding:8px; border:1px solid var(--border); border-radius:6px;" placeholder="Optional"></textarea></div>
      </div>
      <br><button class="primary" id="addClientBtn" onclick="addClient()">Add Client</button>
      <p class="muted" id="addClientStatus" style="margin-top:8px;"></p>
    </div>
  `;
  // Autofocus fix (confirmed Aug 2026, Remove Unwanted Auto-Focus
  // brief) — real bug: this used to fire unconditionally, including on
  // the very FIRST render (arriving at this screen from the tiles menu
  // or a "Back to Clients" link), which popped the on-screen keyboard
  // on mobile before the user had tapped anything. Only restore focus
  // when this render was actually TRIGGERED by the user typing in the
  // search box (searchTerm passed as a real string via the oninput
  // handler below) — without that restore, typing a second character
  // would otherwise drop focus entirely (this function replaces the
  // whole innerHTML, including the input element itself, on every
  // keystroke), closing the keyboard mid-type — a worse bug than the
  // one being fixed. searchTerm is only ever undefined on the initial,
  // one-argument dispatch call (renderLanding() -> renderClients(el)).
  const input = document.getElementById('clientSearchInput');
  if (input && searchTerm !== undefined) { input.focus(); input.setSelectionRange(input.value.length, input.value.length); }
  });
}

async function addClient() {
  const btn = document.getElementById('addClientBtn');
  const statusEl = document.getElementById('addClientStatus');
  // Client Info brief (confirmed Aug 2026) -- phone/email collected as
  // addable lists, split back into the backend's primary+extra shape.
  const phones = contactListToFields(collectContactValues('cl_phone_entry'));
  const emails = contactListToFields(collectContactValues('cl_email_entry'));
  const body = {
    name: document.getElementById('cl_name').value,
    company_name: document.getElementById('cl_company_name').value,
    vat_number: document.getElementById('cl_vat_number').value,
    phone: phones.primary, phone_extra: phones.extraJson,
    email: emails.primary, email_extra: emails.extraJson,
    address: document.getElementById('cl_address').value,
    preferred_branch: document.getElementById('cl_branch').value,
    notes: document.getElementById('cl_notes').value,
  };
  if (!body.name.trim()) { alert('Client name is required.'); return; }

  // Duplicate-by-accident guard (confirmed Aug 2026, Client-Side
  // Commercial Workflow brief, Sprint A — "no duplicates created by
  // accident"): a soft warning, not a hard block — two genuinely
  // different real clients CAN share a common name (e.g. two "John
  // Smith"s), so refusing outright would be wrong. Checks for an
  // EXACT (case-insensitive) name match among already-loaded search
  // results first; if this add wasn't reached via a search that would
  // have already surfaced it, falls back to a real search call so a
  // duplicate typed fresh into a blank form still gets caught.
  const existingMatches = await fetch(`${API}/clients?search=${encodeURIComponent(body.name.trim())}`).then(r => r.json()).catch(() => []);
  const exactDuplicate = existingMatches.find(c => c.name.trim().toLowerCase() === body.name.trim().toLowerCase());
  if (exactDuplicate) {
    const proceed = confirm(`A client named "${exactDuplicate.name}" already exists (${exactDuplicate.phone || 'no phone on file'}). Add another client with the same name anyway?`);
    if (!proceed) return;
  }

  // Double-submit guard (confirmed Aug 2026, same brief) — a real risk
  // on a slow connection with no feedback between click and completion;
  // this is the other concrete way "duplicates created by accident"
  // happens, separate from the name-collision case above.
  if (btn) { btn.disabled = true; btn.textContent = 'Adding...'; }
  try {
    const res = await fetch(`${API}/clients`, {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)});
    if (!res.ok) throw new Error('save failed');
    if (statusEl) statusEl.textContent = `✓ ${body.name} added.`;
    renderClients(document.getElementById('landing'));
  } catch (e) {
    if (statusEl) statusEl.textContent = '❌ Could not save — check your connection and try again.';
    if (btn) { btn.disabled = false; btn.textContent = 'Add Client'; }
  }
}

let currentClientDetailId = null;
// openEdit (confirmed Aug 2026, Order Index Group Multi-Quote Clients
// addendum — "Edit client" link on a collapsed group header) — jumps
// straight to the Edit Details form instead of the read-only card,
// satisfying the brief's own "one click out" requirement. Consumed
// once, in renderClientDetail() below, right after the real client
// record is fetched (showEditClientForm() needs that record already
// cached — can't open the edit form before it exists).
let pendingClientDetailOpenEdit = false;
function openClientDetail(clientId, openEdit) {
  currentClientDetailId = clientId;
  pendingClientDetailOpenEdit = !!openEdit;
  landingView = 'client-detail';
  renderLanding();
}

// Orders tab (confirmed Aug 2026, Supplier Order Sheets brief §6) —
// alongside the existing Order History, per the brief's own words.
// Same subview-toggle pattern as the Builder Portal's own
// builderPortalSubview. A standalone, searchable-across-all-clients
// Orders index (also raised in brief §6) was deliberately NOT built —
// flagged back to Burgert as its own follow-up brief rather than
// assumed in scope, per his confirmed answer.
let clientDetailSubview = 'history';   // 'history' | 'orders'

async function renderClientDetail(el) {
  await renderWithRetry(el, 'Client Detail', async () => {
  el.innerHTML = `<span class="back-link" onclick="landingView='clients'; renderLanding();">← Back to Clients</span><div class="card"><p class="muted">Loading...</p></div>`;
  const [res, orderSheetsRes] = await Promise.all([
    fetch(`${API}/clients/${currentClientDetailId}/quotes`),
    fetch(`${API}/clients/${currentClientDetailId}/order-sheets`),
  ]);
  const data = await res.json();
  const orderSheets = orderSheetsRes.ok ? await orderSheetsRes.json() : [];
  const c = data.client;
  setPageTitle('Client: ' + c.name);   // Page Title in Sticky Header brief -- upgrades the generic "Client Detail" label once the real name is known
  // Address + Value columns (confirmed Aug 2026, Client Order History
  // Columns brief) — real gap: two draft quotes for the same client,
  // same branch, same day were previously indistinguishable in this
  // list without opening each one. Address is the per-JOB site address
  // (Quote.site_address, set on the Order Details screen — order-index.js),
  // deliberately NOT the client's own general contact address shown
  // separately above this table (a client can have multiple properties/
  // jobs). Value is total_incl_vat (confirmed directly — "to match what
  // a client would see"), computed server-side by _quote_totals() (main.py).
  // Status badge now workflow_status (confirmed Aug 2026, Order Index /
  // Job Workflow Redesign brief — "keep both views consistent" with the
  // new Order Index table) via the same workflowStatusBadge() helper
  // (order-index.js) that table uses. Row click now opens Job Detail
  // too, same reasoning — this list and the Order Index should behave
  // the same way for the same underlying job.
  //
  // Document Preview (confirmed Aug 2026, Client Page & Quote Detail:
  // Document Preview + Inline Edit brief, placement 1a) — restructured
  // from a plain <table> to a card per job, since a mini document
  // preview genuinely doesn't fit inside a <td>. Info line kept
  // exactly as before, just no longer table markup.
  const rows = data.quotes.length ? data.quotes.map(q => `
    <div class="card" style="margin-top:10px; padding:14px;">
      <div style="display:flex; flex-wrap:wrap; gap:4px 16px; align-items:center; cursor:pointer;" onclick="goToTab('landing'); openOrderDetailScreen(${q.id})">
        <span class="job-number">${q.job_number || '#'+q.id}</span>
        ${workflowStatusBadge(q)}
        <span class="muted">${q.branch}</span>
        <span class="muted">${new Date(q.created_at).toLocaleDateString('en-ZA')}</span>
        <span class="muted" style="max-width:200px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" title="${(q.site_address||'').replace(/"/g,'&quot;')}">${q.site_address || '—'}</span>
        <b style="margin-left:auto;">${R(q.total_incl_vat)}</b>
      </div>
      ${documentPreviewTileHtml('dp_client_' + q.id, q.id)}
    </div>`).join('') : '<p class="muted">No quotes yet for this client.</p>';
  el.innerHTML = `
    <span class="back-link" onclick="landingView='clients'; renderLanding();">← Back to Clients</span>
    <div class="card" id="clientDetailCard">
      <h2>${c.name}${c.company_name ? ` <span class="muted" style="font-weight:400; font-size:14px;">— ${c.company_name}</span>` : ''}</h2>
      <div class="product-info">
        ${c.vat_number ? `<div>VAT no: <b>${c.vat_number}</b></div>` : ''}
        <div>Phone: <b>${clientPhoneList(c).join(', ') || '—'}</b></div>
        <div>Email: <b>${clientEmailList(c).join(', ') || '—'}</b></div>
        <div>Branch: <b>${c.preferred_branch}</b></div>
        <div>Address: <b>${c.address || '—'}</b></div>
        <div>Notes: <b>${c.notes || '—'}</b></div>
      </div>
      <div style="margin-top:14px; display:flex; gap:10px; flex-wrap:wrap;">
        <button class="primary" onclick="startQuoteForClient(${c.id}, '${c.name.replace(/'/g,"\\'")}', '${c.preferred_branch || ''}')">+ New Quote for ${c.name}</button>
        <button onclick="showEditClientForm(${c.id})">Edit details</button>
      </div>
    </div>
    <div class="card">
      <div style="display:flex; gap:8px; margin-bottom:14px;">
        <button onclick="clientDetailSubview='history'; renderClientDetail(document.getElementById('landing'));" style="${clientDetailSubview==='history' ? 'background:var(--teal); color:white;' : ''}">Order History</button>
        <button onclick="clientDetailSubview='orders'; renderClientDetail(document.getElementById('landing'));" style="${clientDetailSubview==='orders' ? 'background:var(--teal); color:white;' : ''}">Orders${orderSheets.length ? ` (${orderSheets.length})` : ''}</button>
      </div>
      ${clientDetailSubview === 'history' ? rows : clientOrderSheetsHtml(orderSheets)}
    </div>
  `;
  // Document Preview content loads after the tiles actually exist in
  // the DOM (confirmed Aug 2026) — documentPreviewTileHtml() above only
  // renders the placeholder synchronously; this fires the real fetch
  // per job, in parallel, right after el.innerHTML is set. Order
  // History subview only — no preview tiles on the Orders subview.
  if (clientDetailSubview === 'history') { data.quotes.forEach(q => loadDocumentPreview('dp_client_' + q.id, q.id)); }
  // Stashed for showEditClientForm() below — avoids a second fetch just
  // to populate the edit form with what's already on screen.
  window._currentClientRecord = c;
  if (pendingClientDetailOpenEdit) {
    pendingClientDetailOpenEdit = false;
    showEditClientForm(c.id);
  }
  });
}

// Orders tab content (confirmed Aug 2026, Supplier Order Sheets brief
// §6) — findable by job number, order number, or supplier (brief's own
// words), all shown right in the list rather than requiring a search.
// job_number falls back to '#'+quote_id, same convention used
// everywhere else in this app (Order Index, Order History above) —
// a quote that's never been Accepted legitimately has no job_number
// yet, an order sheet can still exist for it.
function clientOrderSheetsHtml(orderSheets) {
  if (!orderSheets.length) return '<p class="muted">No order sheets generated yet for this client\'s jobs.</p>';
  return orderSheets.map(s => `
    <div class="card" style="margin-top:10px; padding:14px; cursor:pointer;" onclick="openOrderSheetDetail(${s.id})">
      <div style="display:flex; flex-wrap:wrap; gap:4px 16px; align-items:center;">
        <span class="job-number">${s.job_number || '#'+s.quote_id}</span>
        <b>${s.order_number}</b>
        <span class="muted">${s.supplier}</span>
        <span class="badge ${s.sheet_type === 'floor_prep' ? 'flooring' : 'trim'}">${s.sheet_type === 'floor_prep' ? 'Floor Prep' : 'Flooring'}</span>
        <span class="muted" style="margin-left:auto;">${new Date(s.created_at).toLocaleDateString('en-ZA')}</span>
      </div>
    </div>`).join('');
}

// Editing an existing client (confirmed Aug 2026, Client-Side Commercial
// Workflow brief) — real gap closed: PUT /clients/{id} has existed on
// the backend all along, but nothing in the frontend ever called it.
// Client details genuinely change (new phone number, corrected address,
// notes added after a site visit) — there was no way to reflect that
// without going around the app. Inline replacement of the detail card's
// own content (not a separate screen) — same in-place-edit pattern the
// rest of this app already uses for its settings-style forms.
function showEditClientForm(clientId) {
  const c = window._currentClientRecord;
  if (!c || c.id !== clientId) return;
  const card = document.getElementById('clientDetailCard');
  if (!card) return;
  // Client Info brief (confirmed Aug 2026) -- pre-fill the addable
  // phone/email lists from the client's real stored list
  // (clientPhoneList()/clientEmailList(), shared.js), not just the
  // single primary value -- first row has no remove button (matches
  // the Add Client form's own "always at least one row" rule), every
  // row after it does.
  const existingPhones = clientPhoneList(c);
  const existingEmails = clientEmailList(c);
  const phoneRowsHtml = (existingPhones.length ? existingPhones : ['']).map((p, i) => `
    <div class="addable-row" style="display:flex; gap:6px; margin-bottom:6px;">
      <input class="ec_phone_entry" placeholder="082 555 1234" value="${p.replace(/"/g,'&quot;')}" style="flex:1;">
      ${i > 0 ? `<button type="button" onclick="this.parentElement.remove();" title="Remove" style="padding:6px 10px;">✕</button>` : ''}
    </div>`).join('');
  const emailRowsHtml = (existingEmails.length ? existingEmails : ['']).map((em, i) => `
    <div class="addable-row" style="display:flex; gap:6px; margin-bottom:6px;">
      <input class="ec_email_entry" placeholder="client@example.com" value="${em.replace(/"/g,'&quot;')}" style="flex:1;">
      ${i > 0 ? `<button type="button" onclick="this.parentElement.remove();" title="Remove" style="padding:6px 10px;">✕</button>` : ''}
    </div>`).join('');
  card.innerHTML = `
    <h2>Edit ${c.name}</h2>
    <div class="grid">
      <div class="field"><label>Name</label><input id="ec_name" value="${c.name.replace(/"/g,'&quot;')}"></div>
      <div class="field"><label>Company name <span class="adj">(optional — leave blank for an individual)</span></label><input id="ec_company_name" value="${(c.company_name||'').replace(/"/g,'&quot;')}" placeholder="e.g. Aspeling Builders CC"></div>
      <div class="field"><label>VAT number <span class="adj">(optional)</span></label><input id="ec_vat_number" value="${(c.vat_number||'').replace(/"/g,'&quot;')}" placeholder="e.g. 4123456789"></div>
      <div class="field" style="grid-column: span 2;">
        <label>Phone</label>
        <div id="ec_phone_list">${phoneRowsHtml}</div>
        <a href="#" onclick="addContactField('ec_phone_list', 'ec_phone_entry', '082 555 1234'); return false;" style="font-size:12px; color:var(--teal); font-weight:600;">+ Add phone</a>
      </div>
      <div class="field" style="grid-column: span 2;">
        <label>Email</label>
        <div id="ec_email_list">${emailRowsHtml}</div>
        <a href="#" onclick="addContactField('ec_email_list', 'ec_email_entry', 'client@example.com'); return false;" style="font-size:12px; color:var(--teal); font-weight:600;">+ Add email</a>
      </div>
      <div class="field"><label>Preferred branch</label>
        <select id="ec_branch">
          <option value="gansbaai" ${c.preferred_branch==='gansbaai'?'selected':''}>Gansbaai</option>
          <option value="hermanus" ${c.preferred_branch==='hermanus'?'selected':''}>Hermanus</option>
        </select>
      </div>
      <div class="field" style="grid-column: span 2;"><label>Address</label><input id="ec_address" value="${(c.address||'').replace(/"/g,'&quot;')}"></div>
      <div class="field" style="grid-column: span 2;"><label>Notes</label><textarea id="ec_notes" rows="2" style="width:100%; font-family:inherit; font-size:14px; padding:8px; border:1px solid var(--border); border-radius:6px;">${c.notes||''}</textarea></div>
    </div>
    <div style="margin-top:14px; display:flex; gap:10px;">
      <button class="primary" id="saveClientEditBtn" onclick="saveClientEdit(${c.id})">Save</button>
      <button onclick="renderClientDetail(document.getElementById('landing'))">Cancel</button>
    </div>
    <p class="muted" id="editClientStatus" style="margin-top:8px;"></p>
  `;
}

async function saveClientEdit(clientId) {
  const btn = document.getElementById('saveClientEditBtn');
  const statusEl = document.getElementById('editClientStatus');
  const name = document.getElementById('ec_name').value.trim();
  if (!name) { alert('Client name is required.'); return; }
  // Client Info brief (confirmed Aug 2026) -- same list-collection
  // pattern as addClient().
  const phones = contactListToFields(collectContactValues('ec_phone_entry'));
  const emails = contactListToFields(collectContactValues('ec_email_entry'));
  const body = {
    name,
    company_name: document.getElementById('ec_company_name').value,
    vat_number: document.getElementById('ec_vat_number').value,
    phone: phones.primary, phone_extra: phones.extraJson,
    email: emails.primary, email_extra: emails.extraJson,
    address: document.getElementById('ec_address').value,
    preferred_branch: document.getElementById('ec_branch').value,
    notes: document.getElementById('ec_notes').value,
  };
  if (btn) { btn.disabled = true; btn.textContent = 'Saving...'; }
  try {
    const res = await fetch(`${API}/clients/${clientId}`, {method:'PUT', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)});
    if (!res.ok) throw new Error('save failed');
    renderClientDetail(document.getElementById('landing'));   // re-render from the freshly-saved record, not the stale local one
  } catch (e) {
    if (statusEl) statusEl.textContent = '❌ Could not save — check your connection and try again.';
    if (btn) { btn.disabled = false; btn.textContent = 'Save'; }
  }
}
