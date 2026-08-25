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

// Order Index Bulk Delete, Owner-only (confirmed Aug 2026) — selection
// state for the checkboxes. Deliberately reset at the top of every
// renderOrderIndex() call (a fresh fetch/search), not preserved across
// searches — keeping a selection from a previous, different filtered
// list would be more confusing than useful.
let orderIndexSelectedIds = new Set();
let orderIndexQuotesCache = [];   // last-fetched rows, so the bulk-delete confirmation can show client names/descriptions without a second round trip

async function renderOrderIndex(el, searchTerm) {
  await renderWithRetry(el, 'Order Index', async () => {
  el.innerHTML = `<span class="back-link" onclick="landingView='tiles'; renderLanding();">← Back</span><div class="card"><h2>Order Index</h2><p class="muted">Loading...</p></div>`;
  const params = searchTerm ? `?search=${encodeURIComponent(searchTerm)}` : '';
  const res = await fetch(`${API}/quotes${params}`);
  const quotes = await res.json();
  orderIndexQuotesCache = quotes;
  orderIndexSelectedIds = new Set();
  const money = R; // alias — consolidated to the one definition in shared.js
  // Owner-only checkboxes/delete (confirmed Aug 2026, Order Index Bulk
  // Delete brief — "hard requirement... not just disabled, not present
  // in the UI"). currentRole() already accounts for Owner Preview Mode
  // (previewRole || realRole()), same as OWNER_ONLY_TILES elsewhere —
  // an Owner previewing as Sales/Admin sees exactly what they'd see.
  // This is only the first layer either way: every actual delete call
  // is independently require_owner-gated server-side (main.py).
  const isOwner = currentRole() === 'owner';

  const rows = quotes.length ? quotes.map(q => {
    const status = computeOrderStatus(q);
    return `
    <tr>
      ${isOwner ? `<td onclick="event.stopPropagation();"><input type="checkbox" class="oi-select" value="${q.id}" onchange="toggleOrderSelected(${q.id}, this.checked)"></td>` : ''}
      <td style="cursor:pointer;" onclick="openQuoteFromIndex(${q.id})">#${q.id}</td>
      <td style="cursor:pointer;" onclick="openQuoteFromIndex(${q.id})">${q.client_id
          ? `<span style="cursor:pointer; color:var(--teal); text-decoration:underline;" onclick="event.stopPropagation(); openClientDetail(${q.client_id})" title="View client details">${q.client_name}</span>`
          : `<span title="No linked client record — walk-in/one-off">${q.client_name}</span>`}</td>
      <td style="cursor:pointer; max-width:140px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" onclick="openQuoteFromIndex(${q.id})" title="${(q.description||'').replace(/"/g,'&quot;')}">${q.description || '—'}</td>
      <td style="cursor:pointer; max-width:160px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;" onclick="openQuoteFromIndex(${q.id})">${q.site_address || '—'}</td>
      <td style="cursor:pointer;" onclick="openQuoteFromIndex(${q.id})"><span class="status-badge" style="background:${status.bg}; color:${status.color};">${status.label}</span></td>
      <td style="cursor:pointer;" onclick="openQuoteFromIndex(${q.id})">${dateOrDash(q.installation_date)}</td>
      <td style="cursor:pointer;" onclick="openQuoteFromIndex(${q.id})">${money(q.deposit_amount)}<br><span class="muted" style="font-size:11px;">${dateOrDash(q.deposit_paid_date)}${q.deposit_payment_method ? ' · '+q.deposit_payment_method : ''}</span></td>
      <td style="cursor:pointer;" onclick="openQuoteFromIndex(${q.id})">${money(q.balance_amount)}<br><span class="muted" style="font-size:11px;">${dateOrDash(q.final_payment_date)}${q.final_payment_method ? ' · '+q.final_payment_method : ''}</span></td>
      <td style="cursor:pointer;" onclick="openQuoteFromIndex(${q.id})">${dateOrDash(q.invoice_sent_date)}</td>
      <td style="white-space:nowrap;">
        <button onclick="event.stopPropagation(); duplicateQuoteFromIndex(${q.id}, '${(q.client_name||'').replace(/'/g,"\\'")}')">Duplicate</button>
        ${isOwner ? `<button class="delete-btn" onclick="event.stopPropagation(); deleteQuoteFromIndex(${q.id})">Delete</button>` : ''}
      </td>
    </tr>`;
  }).join('') : `<tr><td colspan="${isOwner ? 11 : 10}" class="muted">No quotes match.</td></tr>`;

  el.innerHTML = `
    <span class="back-link" onclick="landingView='tiles'; renderLanding();">← Back</span>
    <div class="card">
      <h2>Order Index</h2>
      <p class="muted">All quotes, open and closed. Click one to open and edit its order details. Colour-coded status for an at-a-glance read.</p>
      <div class="field"><label>Search (client name, quote #, or description)</label><input type="text" id="orderSearchInput" value="${searchTerm || ''}" placeholder="Type to search..." oninput="renderOrderIndex(document.getElementById('landing'), this.value)"></div>
      ${isOwner ? `<div style="margin-bottom:10px;"><button id="oiDeleteSelectedBtn" class="delete-btn" disabled onclick="bulkDeleteSelectedOrders()">Delete Selected (0)</button></div>` : ''}
      <div style="overflow-x:auto;">
      <table><thead><tr>
        ${isOwner ? `<th><input type="checkbox" id="oiSelectAll" title="Select all shown" onchange="toggleSelectAllOrders(this.checked)"></th>` : ''}
        <th>#</th><th>Client</th><th>Description</th><th>Address</th><th>Status</th><th>Install Date</th><th>Deposit</th><th>Final Payment</th><th>Invoice Sent</th><th></th>
      </tr></thead>
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

function toggleOrderSelected(quoteId, checked) {
  if (checked) orderIndexSelectedIds.add(quoteId); else orderIndexSelectedIds.delete(quoteId);
  updateBulkDeleteButtonState();
}

function toggleSelectAllOrders(checked) {
  document.querySelectorAll('.oi-select').forEach(cb => {
    cb.checked = checked;
    const id = parseInt(cb.value, 10);
    if (checked) orderIndexSelectedIds.add(id); else orderIndexSelectedIds.delete(id);
  });
  updateBulkDeleteButtonState();
}

function updateBulkDeleteButtonState() {
  const btn = document.getElementById('oiDeleteSelectedBtn');
  if (!btn) return;
  btn.disabled = orderIndexSelectedIds.size === 0;
  btn.textContent = `Delete Selected (${orderIndexSelectedIds.size})`;
}

async function deleteQuoteFromIndex(quoteId) {
  if (!confirm(`Delete quote #${quoteId} and all its line items? This can't be undone.`)) return;
  const res = await fetch(`${API}/quotes/${quoteId}`, {method:'DELETE'});
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    alert(err.detail || 'Could not delete this order.');
    return;
  }
  renderOrderIndex(document.getElementById('landing'), document.getElementById('orderSearchInput').value);
}

// Order Index Bulk Delete, Owner-only (confirmed Aug 2026). The
// confirmation dialog itself is the "required safeguard" — client
// names/order refs shown in full before anything is sent (brief
// Section 3: "show what's about to be deleted... enough detail that
// Burgert can sanity-check the selection"). The real dependency check
// and AuditLog write both happen server-side (main.py's
// _quote_delete_dependencies / bulk_delete_quotes) — this is UX, not
// the actual guard.
async function bulkDeleteSelectedOrders() {
  if (orderIndexSelectedIds.size === 0) return;
  const selected = orderIndexQuotesCache.filter(q => orderIndexSelectedIds.has(q.id));
  const detail = selected.map(q => `#${q.id} — ${q.client_name}${q.description ? ' ('+q.description+')' : ''}`).join('\n');
  if (!confirm(`Delete ${selected.length} order${selected.length !== 1 ? 's' : ''}? This cannot be undone.\n\n${detail}`)) return;
  const res = await fetch(`${API}/quotes/bulk-delete`, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({quote_ids: Array.from(orderIndexSelectedIds)}),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    alert(err.detail || 'Could not delete the selected orders.');
    return;
  }
  const data = await res.json();
  orderIndexSelectedIds = new Set();
  alert(`${data.deleted_count} order${data.deleted_count !== 1 ? 's' : ''} deleted.`);
  renderOrderIndex(document.getElementById('landing'), document.getElementById('orderSearchInput').value);
}

// Duplicate Quote (confirmed Aug 2026) — called both from an Order
// Index row and from the "Duplicate Quote" button inside an open quote
// (index.html). Client name defaults to the source's own (leave the
// prompt as-is), or can be edited to duplicate as a starting template
// for a different client's job (brief Section 2). A plain typed name,
// same "walk-in/one-off, no CRM record required" rule create_quote()
// already uses — not a full client-search picker, which felt like more
// UI than this pilot's stated scope called for.
async function duplicateQuoteFromIndex(quoteId, clientName) {
  const newClientName = prompt(
    `Duplicate quote #${quoteId}\n\nClient for the new quote — leave as-is to duplicate for the same client, or edit for a different one:`,
    clientName || ''
  );
  if (newClientName === null) return;   // cancelled
  const trimmed = newClientName.trim();
  const body = (trimmed && trimmed !== (clientName || '').trim()) ? {client_name: trimmed} : {};
  const res = await fetch(`${API}/quotes/${quoteId}/duplicate`, {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body),
  });
  if (!res.ok) { alert('Could not duplicate this quote.'); return; }
  const data = await res.json();
  alert(`Duplicated as quote #${data.quote.id} (${data.lines_copied} line${data.lines_copied !== 1 ? 's' : ''} copied) — opening it now.\nStatus reset to Draft. Description pre-filled as "${data.quote.description}" — edit it if you'd like.`);
  openQuoteFromIndex(data.quote.id);
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
