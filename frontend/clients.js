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
      </div>
      <br><button class="primary" onclick="addClient()">Add Client</button>
    </div>
  `;
  const input = document.getElementById('clientSearchInput');
  if (input) { input.focus(); input.setSelectionRange(input.value.length, input.value.length); }
  });
}

async function addClient() {
  const body = {
    name: document.getElementById('cl_name').value,
    phone: document.getElementById('cl_phone').value,
    email: document.getElementById('cl_email').value,
    address: document.getElementById('cl_address').value,
    preferred_branch: document.getElementById('cl_branch').value,
  };
  if (!body.name) { alert('Client name is required.'); return; }
  await fetch(`${API}/clients`, {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)});
  renderClients(document.getElementById('landing'));
}

let currentClientDetailId = null;
function openClientDetail(clientId) {
  currentClientDetailId = clientId;
  landingView = 'client-detail';
  renderLanding();
}

async function renderClientDetail(el) {
  await renderWithRetry(el, 'Client Detail', async () => {
  el.innerHTML = `<span class="back-link" onclick="landingView='clients'; renderLanding();">← Back to Clients</span><div class="card"><p class="muted">Loading...</p></div>`;
  const res = await fetch(`${API}/clients/${currentClientDetailId}/quotes`);
  const data = await res.json();
  const c = data.client;
  const rows = data.quotes.length ? data.quotes.map(q => `
    <tr style="cursor:pointer;" onclick="openQuoteFromIndex(${q.id})">
      <td>#${q.id}</td><td><span class="badge flooring">${q.status}</span></td><td>${q.branch}</td>
      <td>${new Date(q.created_at).toLocaleDateString('en-ZA')}</td>
    </tr>`).join('') : '<tr><td colspan="4" class="muted">No quotes yet for this client.</td></tr>';
  el.innerHTML = `
    <span class="back-link" onclick="landingView='clients'; renderLanding();">← Back to Clients</span>
    <div class="card">
      <h2>${c.name}</h2>
      <div class="product-info">
        <div>Phone: <b>${c.phone || '—'}</b></div>
        <div>Email: <b>${c.email || '—'}</b></div>
        <div>Branch: <b>${c.preferred_branch}</b></div>
        <div>Address: <b>${c.address || '—'}</b></div>
      </div>
      <button class="primary" style="margin-top:14px;" onclick="startQuoteForClient(${c.id}, '${c.name.replace(/'/g,"\\'")}', '${c.preferred_branch || ''}')">+ New Quote for ${c.name}</button>
    </div>
    <div class="card">
      <h2>Order History</h2>
      <table><thead><tr><th>#</th><th>Status</th><th>Branch</th><th>Date</th></tr></thead>
      <tbody>${rows}</tbody></table>
    </div>
  `;
  });
}
