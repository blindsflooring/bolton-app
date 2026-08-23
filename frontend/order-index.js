// ===== ORDER INDEX =====
// The Order Index list page: status colour logic, the list itself,
// per-row delete, and the "New Client → Start Quote" panel that lives
// on this page. Confirmed Aug 2026, Stage 2 of the foundation
// refactor, fourth extraction. Depends on shared.js (API, businessSettings,
// R/dateOrDash) and calls into index.html for openQuoteFromIndex(),
// openClientDetail(), and startQuoteForClient() — all handoffs into
// Quote Builder or Clients, staying where they are, same pattern as
// every prior extraction round.
//
// Deliberately NOT moved here yet: saveOrderDetails(), logFollowUp(),
// loadFollowUps() — these currently serve the Order Details card that
// still lives on the Quote Builder page, not Order Index. Moving them
// here now, before that card relocates, would put them in the wrong
// file relative to the UI they actually serve. That relocation (Quote
// Builder → Order Index) is a separate, still-open task — flagged
// directly as a real UX concern (order/payment info doesn't make sense
// to ask for at quoting time, when none of it exists yet), not acted
// on yet.

function computeOrderStatus(q) {
  // Confirmed Aug 2026: colour-coded at-a-glance status. Overdue
  // threshold now genuinely comes from Business Settings (real gap
  // found while merging v54 — the setting existed and was editable
  // there, but this function still had its own hardcoded fallback
  // constant wired to nothing). 7 is the default until changed.
  const OVERDUE_DAYS = businessSettings?.order_overdue_days ?? 7;
  if (q.final_payment_date) return {label: 'Paid in Full', color: '#1a7a3e', bg: '#dcf5e6'};
  if (q.deposit_paid_date) return {label: 'Deposit Paid — Balance Due', color: '#8a6d00', bg: 'var(--cream)'};
  if (q.invoice_sent_date) {
    const daysSince = (new Date() - new Date(q.invoice_sent_date)) / (1000*60*60*24);
    if (daysSince > OVERDUE_DAYS) return {label: `Overdue (${Math.floor(daysSince)}d)`, color: 'white', bg: 'var(--coral)'};
    return {label: 'Awaiting Deposit', color: 'white', bg: 'var(--teal)'};
  }
  return {label: 'Not Invoiced', color: '#6b7280', bg: '#f0f0f0'};
}

async function renderOrderIndex(el, searchTerm) {
  await renderWithRetry(el, 'Order Index', async () => {
  el.innerHTML = `<span class="back-link" onclick="landingView='tiles'; renderLanding();">← Back</span><div class="card"><h2>Order Index</h2><p class="muted">Loading...</p></div>`;
  const params = searchTerm ? `?search=${encodeURIComponent(searchTerm)}` : '';
  const res = await fetch(`${API}/quotes${params}`);
  const quotes = await res.json();
  const money = R; // alias — consolidated to the one definition in shared.js

  const rows = quotes.length ? quotes.map(q => {
    const status = computeOrderStatus(q);
    return `
    <tr>
      <td style="cursor:pointer;" onclick="openQuoteFromIndex(${q.id})">#${q.id}</td>
      <td style="cursor:pointer;" onclick="openQuoteFromIndex(${q.id})">${q.client_id
          ? `<span style="cursor:pointer; color:var(--teal); text-decoration:underline;" onclick="event.stopPropagation(); openClientDetail(${q.client_id})" title="View client details">${q.client_name}</span>`
          : `<span title="No linked client record — walk-in/one-off">${q.client_name}</span>`}</td>
      <td style="cursor:pointer; max-width:160px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" onclick="openQuoteFromIndex(${q.id})">${q.site_address || '—'}</td>
      <td style="cursor:pointer;" onclick="openQuoteFromIndex(${q.id})"><span class="status-badge" style="background:${status.bg}; color:${status.color};">${status.label}</span></td>
      <td style="cursor:pointer;" onclick="openQuoteFromIndex(${q.id})">${dateOrDash(q.installation_date)}</td>
      <td style="cursor:pointer;" onclick="openQuoteFromIndex(${q.id})">${money(q.deposit_amount)}<br><span class="muted" style="font-size:11px;">${dateOrDash(q.deposit_paid_date)}${q.deposit_payment_method ? ' · '+q.deposit_payment_method : ''}</span></td>
      <td style="cursor:pointer;" onclick="openQuoteFromIndex(${q.id})">${money(q.balance_amount)}<br><span class="muted" style="font-size:11px;">${dateOrDash(q.final_payment_date)}${q.final_payment_method ? ' · '+q.final_payment_method : ''}</span></td>
      <td style="cursor:pointer;" onclick="openQuoteFromIndex(${q.id})">${dateOrDash(q.invoice_sent_date)}</td>
      <td><button class="delete-btn" onclick="event.stopPropagation(); deleteQuoteFromIndex(${q.id})">Delete</button></td>
    </tr>`;
  }).join('') : '<tr><td colspan="9" class="muted">No quotes match.</td></tr>';

  el.innerHTML = `
    <span class="back-link" onclick="landingView='tiles'; renderLanding();">← Back</span>
    <div class="card">
      <h2>Order Index</h2>
      <p class="muted">All quotes, open and closed. Click one to open and edit its order details. Colour-coded status for an at-a-glance read.</p>
      <div class="field"><label>Search (client name or quote #)</label><input type="text" id="orderSearchInput" value="${searchTerm || ''}" placeholder="Type to search..." oninput="renderOrderIndex(document.getElementById('landing'), this.value)"></div>
      <div style="overflow-x:auto;">
      <table><thead><tr><th>#</th><th>Client</th><th>Address</th><th>Status</th><th>Install Date</th><th>Deposit</th><th>Final Payment</th><th>Invoice Sent</th><th></th></tr></thead>
      <tbody>${rows}</tbody></table>
      </div>
    </div>
    <div class="card">
      <h2>New Client → Start Quote</h2>
      <p class="muted">Fill in a new client's details, then jump straight into a quote for them.</p>
      <div class="grid">
        <div class="field"><label>Name</label><input id="oi_cl_name" placeholder="Client name"></div>
        <div class="field"><label>Phone</label><input id="oi_cl_phone" placeholder="082 555 1234"></div>
        <div class="field"><label>Email</label><input id="oi_cl_email" placeholder="client@example.com"></div>
        <div class="field"><label>Preferred branch</label>
          <select id="oi_cl_branch"><option value="gansbaai">Gansbaai</option><option value="hermanus">Hermanus</option></select>
        </div>
        <div class="field" style="grid-column: span 2;"><label>Address</label><input id="oi_cl_address" placeholder="Site/delivery address"></div>
      </div>
      <br><button class="primary" onclick="addClientAndStartQuote()">Add Client &amp; Start Quote</button>
    </div>
  `;
  const input = document.getElementById('orderSearchInput');
  input.focus();
  input.setSelectionRange(input.value.length, input.value.length);
  });
}

async function deleteQuoteFromIndex(quoteId) {
  if (!confirm(`Delete quote #${quoteId} and all its line items? This can't be undone.`)) return;
  await fetch(`${API}/quotes/${quoteId}`, {method:'DELETE'});
  renderOrderIndex(document.getElementById('landing'), document.getElementById('orderSearchInput').value);
}

async function addClientAndStartQuote() {
  const body = {
    name: document.getElementById('oi_cl_name').value,
    phone: document.getElementById('oi_cl_phone').value,
    email: document.getElementById('oi_cl_email').value,
    address: document.getElementById('oi_cl_address').value,
    preferred_branch: document.getElementById('oi_cl_branch').value,
  };
  if (!body.name) { alert('Client name is required.'); return; }
  const res = await fetch(`${API}/clients`, {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)});
  const client = await res.json();
  startQuoteForClient(client.id, client.name, client.preferred_branch);
}
