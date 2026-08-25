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
  const rows = clients.length ? clients.map(c => `
    <tr style="cursor:pointer;" onclick="openClientDetail(${c.id})">
      <td>${c.name}</td><td>${c.phone || '—'}</td><td>${c.email || '—'}</td><td>${c.preferred_branch}</td>
    </tr>`).join('') : '<tr><td colspan="4" class="muted">No clients match.</td></tr>';
  el.innerHTML = `
    <span class="back-link" onclick="landingView='tiles'; renderLanding();">← Back</span>
    <div class="card">
      <h2>Clients</h2>
      <div class="field"><label>Search</label><input type="text" id="clientSearchInput" value="${searchTerm || ''}" placeholder="Type to search..." oninput="renderClients(document.getElementById('landing'), this.value)"></div>
      <table><thead><tr><th>Name</th><th>Phone</th><th>Email</th><th>Branch</th></tr></thead>
      <tbody>${rows}</tbody></table>
    </div>
    <div class="card">
      <h2>Add Client</h2>
      <div class="grid">
        <div class="field"><label>Name</label><input id="cl_name" placeholder="Client name"></div>
        <div class="field"><label>Phone</label><input id="cl_phone" placeholder="082 555 1234"></div>
        <div class="field"><label>Email</label><input id="cl_email" placeholder="client@example.com"></div>
        <div class="field"><label>Preferred branch</label>
          <select id="cl_branch"><option value="gansbaai">Gansbaai</option><option value="hermanus">Hermanus</option></select>
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
  const body = {
    name: document.getElementById('cl_name').value,
    phone: document.getElementById('cl_phone').value,
    email: document.getElementById('cl_email').value,
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

async function renderClientDetail(el) {
  await renderWithRetry(el, 'Client Detail', async () => {
  el.innerHTML = `<span class="back-link" onclick="landingView='clients'; renderLanding();">← Back to Clients</span><div class="card"><p class="muted">Loading...</p></div>`;
  const res = await fetch(`${API}/clients/${currentClientDetailId}/quotes`);
  const data = await res.json();
  const c = data.client;
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
      <h2>${c.name}</h2>
      <div class="product-info">
        <div>Phone: <b>${c.phone || '—'}</b></div>
        <div>Email: <b>${c.email || '—'}</b></div>
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
      <h2>Order History</h2>
      ${rows}
    </div>
  `;
  // Document Preview content loads after the tiles actually exist in
  // the DOM (confirmed Aug 2026) — documentPreviewTileHtml() above only
  // renders the placeholder synchronously; this fires the real fetch
  // per job, in parallel, right after el.innerHTML is set.
  data.quotes.forEach(q => loadDocumentPreview('dp_client_' + q.id, q.id));
  // Stashed for showEditClientForm() below — avoids a second fetch just
  // to populate the edit form with what's already on screen.
  window._currentClientRecord = c;
  if (pendingClientDetailOpenEdit) {
    pendingClientDetailOpenEdit = false;
    showEditClientForm(c.id);
  }
  });
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
  card.innerHTML = `
    <h2>Edit ${c.name}</h2>
    <div class="grid">
      <div class="field"><label>Name</label><input id="ec_name" value="${c.name.replace(/"/g,'&quot;')}"></div>
      <div class="field"><label>Phone</label><input id="ec_phone" value="${(c.phone||'').replace(/"/g,'&quot;')}"></div>
      <div class="field"><label>Email</label><input id="ec_email" value="${(c.email||'').replace(/"/g,'&quot;')}"></div>
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
  const body = {
    name,
    phone: document.getElementById('ec_phone').value,
    email: document.getElementById('ec_email').value,
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
