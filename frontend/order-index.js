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
// Order Details screen (confirmed Aug 2026, Quote Builder Layout
// Corrections brief) — the previously-flagged relocation is done:
// saveOrderDetails()/logFollowUp()/loadFollowUps() moved here from
// index.html, along with the card itself (now renderOrderDetail()
// below), because order/payment info doesn't make sense to ask for at
// quoting time, when none of it exists yet — it belongs once a quote's
// been accepted and converted. Reached via an "Order Details" link per
// row on this screen, not a tile — its own landingView sub-view
// ('orderDetail'), same dedicated-sub-page pattern clients.js already
// uses for openClientDetail()/renderClientDetail().

// Job Workflow (confirmed Aug 2026, Order Index / Job Workflow Redesign
// brief + Next Action Addendum) — retires computeOrderStatus() as the
// PRIMARY status: that function derived its label purely from the
// accounting date fields and never actually read Quote.status at all —
// the real finding that kicked off this whole redesign (see the
// architecture proposal shared with Burgert). The badge shown
// everywhere now is workflow_status, backend-authoritative and exactly
// 4 values. Accounting/payment state is still visible (Deposit/Final
// Payment columns, unchanged) — just no longer standing in as "the"
// status.
const WORKFLOW_STATUS_META = {
  quoted:    {label: 'Quoted',    bg: '#f0f0f0',      color: '#6b7280'},
  accepted:  {label: 'Accepted',  bg: 'var(--cream)', color: '#8a6d00'},
  scheduled: {label: 'Scheduled', bg: 'var(--teal)',  color: 'white'},
  completed: {label: 'Completed', bg: '#dcf5e6',      color: '#1a7a3e'},
};
function workflowStatusBadge(q) {
  const meta = WORKFLOW_STATUS_META[q.workflow_status] || WORKFLOW_STATUS_META.quoted;
  const declined = q.declined_at ? ' <span class="muted" style="font-size:10.5px;">(declined)</span>' : '';
  return `<span class="status-badge" style="background:${meta.bg}; color:${meta.color};">${meta.label}</span>${declined}`;
}
// Next Action button — action_target from _job_workflow_info() (main.py)
// decides where it goes: 'print_invoice' opens the existing Print
// Invoice flow directly (Create Invoice), everything else opens Job
// Detail, where the actual state-changing controls live (Accept/
// Decline/Schedule/Materials/Complete) — the button on THIS list only
// ever navigates, it never silently mutates a job's data on a single
// click.
function nextActionButton(q) {
  if (!q.next_action) return '';
  const go = q.action_target === 'print_invoice' ? `printInvoiceForQuote(${q.id})` : `openOrderDetailScreen(${q.id})`;
  return `<button class="next-action-btn" onclick="event.stopPropagation(); ${go}" title="${q.next_action}">${q.action_button}</button>`;
}

// Order Index Bulk Delete, Owner-only (confirmed Aug 2026) — selection
// state for the checkboxes. Deliberately reset at the top of every
// renderOrderIndex() call (a fresh fetch/search), not preserved across
// searches — keeping a selection from a previous, different filtered
// list would be more confusing than useful.
let orderIndexSelectedIds = new Set();
let orderIndexQuotesCache = [];   // last-fetched (search-filtered, not status-tab-filtered) rows — source for the summary counts, the Needs Attention list, and the bulk-delete confirmation's client names/descriptions, all without a second round trip
let orderIndexActiveTab = 'all';   // 'all' | 'quoted' | 'accepted' | 'scheduled' | 'completed' — filtered client-side against orderIndexQuotesCache so the summary counts (computed from the same cache) never disagree with what a tab click shows

const WORKFLOW_TABS = ['all', 'quoted', 'accepted', 'scheduled', 'completed'];

function setOrderIndexTab(tab) {
  orderIndexActiveTab = tab;
  renderOrderIndexTable();
}

async function renderOrderIndex(el, searchTerm) {
  await renderWithRetry(el, 'Order Index', async () => {
  el.innerHTML = `<span class="back-link" onclick="landingView='tiles'; renderLanding();">← Back</span><div class="card"><h2>Order Index</h2><p class="muted">Loading...</p></div>`;
  // No workflow_status filter sent here deliberately — the full
  // (search-filtered) set is fetched once so the summary counts and
  // every status tab can be computed/rendered client-side from the
  // SAME data, guaranteeing they can never disagree with each other.
  const params = searchTerm ? `?search=${encodeURIComponent(searchTerm)}` : '';
  const res = await fetch(`${API}/quotes${params}`);
  orderIndexQuotesCache = await res.json();
  orderIndexSelectedIds = new Set();
  renderOrderIndexTable(searchTerm);
  });
}

function renderOrderIndexTable(searchTerm) {
  const el = document.getElementById('landing');
  const quotes = orderIndexQuotesCache;
  const money = R; // alias — consolidated to the one definition in shared.js
  // Owner-only checkboxes/delete (confirmed Aug 2026, Order Index Bulk
  // Delete brief — "hard requirement... not just disabled, not present
  // in the UI"). currentRole() already accounts for Owner Preview Mode
  // (previewRole || realRole()), same as OWNER_ONLY_TILES elsewhere —
  // an Owner previewing as Sales/Admin sees exactly what they'd see.
  // This is only the first layer either way: every actual delete call
  // is independently require_owner-gated server-side (main.py).
  const isOwner = currentRole() === 'owner';

  // Quick summary counts (confirmed Aug 2026, brief §6 — "12 Quoted | 8
  // Accepted | 5 Scheduled | 23 Completed"), and the tab filter itself,
  // both computed from the one fetched set above.
  const counts = {quoted: 0, accepted: 0, scheduled: 0, completed: 0};
  quotes.forEach(q => { if (counts[q.workflow_status] !== undefined) counts[q.workflow_status]++; });
  const shown = orderIndexActiveTab === 'all' ? quotes : quotes.filter(q => q.workflow_status === orderIndexActiveTab);

  // Needs Attention (confirmed Aug 2026, brief §7 + addendum's priority
  // tiers) — every quote with an attention_priority set, sorted most-
  // urgent first, each row clickable straight to the job. Built from
  // the SAME cache as the table below, not a separate fetch, so it can
  // never show something the table itself disagrees with.
  const PRIORITY_ORDER = {critical: 0, warning: 1, notice: 2};
  const attentionItems = quotes.filter(q => q.attention_priority)
    .sort((a, b) => PRIORITY_ORDER[a.attention_priority] - PRIORITY_ORDER[b.attention_priority]);
  const PRIORITY_FLAG = {critical: '🔴', warning: '🟠', notice: '🟡'};
  const attentionHtml = attentionItems.length ? attentionItems.map(q => `
    <div class="attention-item priority-${q.attention_priority}" onclick="openOrderDetailScreen(${q.id})">
      <span class="attn-flag">${PRIORITY_FLAG[q.attention_priority]} ${q.attention_label}</span>
      <span class="attn-detail">${q.job_number || '#'+q.id} — ${q.client_name}${q.description ? ' · '+q.description : ''}</span>
      ${nextActionButton(q)}
    </div>`).join('') : '<p class="muted" style="margin:0;">Nothing needs attention right now.</p>';

  const rows = shown.length ? shown.map(q => `
    <tr style="cursor:pointer;" onclick="openOrderDetailScreen(${q.id})">
      ${isOwner ? `<td onclick="event.stopPropagation();"><input type="checkbox" class="oi-select" value="${q.id}" onchange="toggleOrderSelected(${q.id}, this.checked)"></td>` : ''}
      <td class="job-number">${q.job_number || `#${q.id}`}</td>
      <td>${q.client_id
          ? `<span style="cursor:pointer; color:var(--teal); text-decoration:underline;" onclick="event.stopPropagation(); openClientDetail(${q.client_id})" title="View client details">${q.client_name}</span>`
          : `<span title="No linked client record — walk-in/one-off">${q.client_name}</span>`}
        ${q.description ? `<br><span class="muted" style="font-size:11px;">${q.description}</span>` : ''}</td>
      <td>${money(q.total_incl_vat)}</td>
      <td>${workflowStatusBadge(q)}</td>
      <td>${dateOrDash(q.installation_date)}</td>
      <td>${nextActionButton(q) || '<span class="muted">—</span>'}</td>
      <td style="white-space:nowrap;">
        <button onclick="event.stopPropagation(); duplicateQuoteFromIndex(${q.id}, '${(q.client_name||'').replace(/'/g,"\\'")}')">Duplicate</button>
        ${isOwner ? `<button class="delete-btn" onclick="event.stopPropagation(); deleteQuoteFromIndex(${q.id})">Delete</button>` : ''}
      </td>
    </tr>`).join('') : `<tr><td colspan="${isOwner ? 8 : 7}" class="muted">No jobs match.</td></tr>`;

  const tab = (key, label, count) => `<button onclick="setOrderIndexTab('${key}')" style="${orderIndexActiveTab===key ? 'background:var(--teal); color:white; border-color:var(--teal);' : ''}">${label}${count !== undefined ? ` (${count})` : ''}</button>`;

  el.innerHTML = `
    <span class="back-link" onclick="landingView='tiles'; renderLanding();">← Back</span>
    <div class="landing-welcome">
      <h1>Order Index</h1>
      <p>What jobs do I have? Where's each one in the process? What needs to happen next?</p>
    </div>

    <div class="card">
      <h2>Needs Attention</h2>
      <div class="attention-list">${attentionHtml}</div>
    </div>

    <div class="card">
      <div class="summary-counts">
        <span><b>${counts.quoted}</b> Quoted</span>
        <span><b>${counts.accepted}</b> Accepted</span>
        <span><b>${counts.scheduled}</b> Scheduled</span>
        <span><b>${counts.completed}</b> Completed</span>
      </div>
      <div class="workflow-tabs" style="margin-top:12px;">
        ${tab('all', 'All', quotes.length)} ${tab('quoted', 'Quoted', counts.quoted)} ${tab('accepted', 'Accepted', counts.accepted)} ${tab('scheduled', 'Scheduled', counts.scheduled)} ${tab('completed', 'Completed', counts.completed)}
      </div>
      <div class="field"><label>Search (customer, job number, or site)</label><input type="text" id="orderSearchInput" value="${searchTerm || ''}" placeholder="Type to search..." oninput="renderOrderIndex(document.getElementById('landing'), this.value)"></div>
      ${isOwner ? `<div style="margin-bottom:10px;"><button id="oiDeleteSelectedBtn" class="delete-btn" disabled onclick="bulkDeleteSelectedOrders()">Delete Selected (0)</button></div>` : ''}
      <div style="overflow-x:auto;">
      <table><thead><tr>
        ${isOwner ? `<th><input type="checkbox" id="oiSelectAll" title="Select all shown" onchange="toggleSelectAllOrders(this.checked)"></th>` : ''}
        <th>Job</th><th>Customer</th><th>Value</th><th>Status</th><th>Install Date</th><th>Next Action</th><th></th>
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
  if (input) { input.focus(); input.setSelectionRange(input.value.length, input.value.length); }
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

// ===== Job Detail screen (confirmed Aug 2026, Order Index / Job
// Workflow Redesign brief §11 — "the overview [Order Index] is not a
// place to display every piece of job information... clicking a job
// opens the full Job Detail page"). Grew out of the Order Details card
// relocated here by the earlier Quote Builder Layout Corrections brief
// — that content (site/payment tracking, Follow-Ups) is now this
// screen's Workflow/Operational/Accounting section, per this brief's
// own §0 ("becomes part of the Job Detail screen"), rather than a
// separate destination. Built incrementally (confirmed directly, Q3):
// this first cut is workflow + what already existed; the brief's fuller
// Job Detail scope (documents, activity history, etc.) grows later as
// its own briefs, not attempted in one pass here. =====

let currentOrderDetailQuoteId = null;

function openOrderDetailScreen(quoteId) {
  currentOrderDetailQuoteId = quoteId;
  landingView = 'orderDetail';
  renderLanding();
}

// Workflow action section — one clear primary action at a time, driven
// entirely by workflow_status, per the addendum's "current step -> next
// step -> next step" principle. Deliberately NOT the same as the Next
// Action button on the Order Index row: that button only navigates
// here; the actual state-changing controls (Accept, Schedule, Mark
// Complete...) live on this screen, one click away, never silently
// fired from the list.
function renderWorkflowActionsHtml(q) {
  if (q.declined_at) {
    return `<p class="muted" style="margin:0;">Declined ${new Date(q.declined_at).toLocaleDateString('en-ZA')} — no further workflow action.</p>`;
  }
  if (q.workflow_status === 'quoted') {
    return `
      <button class="primary" onclick="acceptQuoteAction(${q.id})">Accept Quote</button>
      <button onclick="declineQuoteAction(${q.id})" style="margin-left:8px;">Decline</button>
      <p class="muted" style="margin-top:8px;">Accepting assigns a Job Number and moves this to Accepted — done automatically, not a status you set by hand.</p>`;
  }
  if (q.workflow_status === 'accepted') {
    return `
      <div class="field" style="max-width:220px;"><label>Installation date</label><input type="date" id="wf_install_date" value="${q.installation_date || ''}"></div>
      <button class="primary" onclick="scheduleQuoteAction(${q.id})" style="margin-top:6px;">Confirm Installation — Book</button>
      <p class="muted" style="margin-top:8px;">Confirming a date is what moves this job to Scheduled automatically.</p>`;
  }
  if (q.workflow_status === 'scheduled') {
    // ready_for_installation means, precisely (confirmed directly): the
    // flooring/blinds have been delivered and stock is physically on
    // hand — ready to install from that moment. Always a manual
    // confirmation button, never a passive checkbox — Bolton has no
    // physical stock-on-hand tracking to infer it from, so this has to
    // be a deliberate click, same reasoning every other workflow
    // transition on this screen is a named action, not a raw field.
    const readyHtml = q.ready_for_installation
      ? `<span style="color:var(--teal); font-weight:700;">✓ Materials received</span> <a href="#" onclick="setMaterialsReceived(${q.id}, false); return false;" style="font-size:12px; margin-left:8px;">Undo</a>`
      : `<button onclick="setMaterialsReceived(${q.id}, true)">Mark Materials Received</button>`;
    return `
      <div class="field"><label style="font-weight:600; color:var(--navy);"><input type="checkbox" id="wf_materials_ordered" ${q.materials_ordered ? 'checked' : ''} onchange="setMaterialsOrdered(${q.id}, this.checked)" style="width:auto; margin-right:6px;"> Materials ordered</label></div>
      <div class="field" style="margin-top:8px;">${readyHtml}</div>
      <div class="field" style="margin-top:10px; max-width:260px;"><label>Installer / team</label><input id="wf_installer" value="${(q.installer_team||'').replace(/"/g,'&quot;')}" onchange="saveInstallerTeam(${q.id})" placeholder="e.g. Ryno + 1"></div>
      <button class="primary" onclick="completeQuoteAction(${q.id})" style="margin-top:10px;">Mark Installation Complete</button>`;
  }
  if (q.workflow_status === 'completed') {
    if (!q.invoice_sent_date) {
      return `<button class="primary" onclick="printInvoiceForQuote(${q.id})">Create Invoice</button>`;
    }
    if (!q.final_payment_date) {
      return `<p class="muted" style="margin:0 0 8px;">Invoiced ${new Date(q.invoice_sent_date).toLocaleDateString('en-ZA')} — log payment below when it comes in.</p>`;
    }
    return `<p style="margin:0; color:var(--teal); font-weight:700;">✓ Job fully closed out — invoiced and paid.</p>`;
  }
  return '';
}

async function renderOrderDetail(el) {
  await renderWithRetry(el, 'Job Detail', async () => {
  el.innerHTML = `<span class="back-link" onclick="landingView='orders'; renderLanding();">← Back to Order Index</span><div class="card"><p class="muted">Loading...</p></div>`;
  const res = await fetch(`${API}/quotes/${currentOrderDetailQuoteId}?role=${currentRole()}`);
  const data = await res.json();
  const q = data.quote;

  el.innerHTML = `
    <span class="back-link" onclick="landingView='orders'; renderLanding();">← Back to Order Index</span>
    <div class="landing-welcome">
      <h1>${q.job_number || 'Quote #' + q.id}${q.description ? ' — ' + q.description : ''}</h1>
      <p>${q.client_name} &nbsp; ${workflowStatusBadge(q)}</p>
    </div>

    <div class="card">
      <h2>Workflow</h2>
      ${renderWorkflowActionsHtml(q)}
      <details style="margin-top:16px;">
        <summary class="muted" style="cursor:pointer; font-size:12.5px;">Correct workflow status manually (exception path — use Accept/Schedule/Complete above normally)</summary>
        <div style="margin-top:10px; display:flex; gap:8px; align-items:center;">
          <select id="wf_override_status">
            ${['quoted','accepted','scheduled','completed'].map(s => `<option value="${s}" ${q.workflow_status===s?'selected':''}>${s.charAt(0).toUpperCase()+s.slice(1)}</option>`).join('')}
          </select>
          <button onclick="overrideWorkflowStatus(${q.id})">Override</button>
        </div>
      </details>
    </div>

    <div class="card">
      <h2>Job Details</h2>
      <p class="muted" style="margin-top:-8px;">Site/installation and payment tracking for this job — shown at a glance on the Order Index once saved.</p>
      <div class="grid">
        <div class="field" style="grid-column: span 2;"><label>Site address</label><input id="od_site_address" value="${q.site_address || ''}" placeholder="Install/delivery site, if different from the client's own address"></div>
        <div class="field"><label>Installation date</label><input id="od_installation_date" type="date" value="${q.installation_date || ''}"></div>
        <div class="field"><label>Invoice sent date</label><input id="od_invoice_sent_date" type="date" value="${q.invoice_sent_date || ''}"></div>
        <div class="field"><label>Deposit paid date</label><input id="od_deposit_paid_date" type="date" value="${q.deposit_paid_date || ''}"></div>
        <div class="field"><label>Deposit payment method</label><input id="od_deposit_payment_method" value="${q.deposit_payment_method || ''}" placeholder="EFT / Cash / Card / Yoco..."></div>
        <div class="field"><label>Final payment date</label><input id="od_final_payment_date" type="date" value="${q.final_payment_date || ''}"></div>
        <div class="field"><label>Final payment method</label><input id="od_final_payment_method" value="${q.final_payment_method || ''}" placeholder="EFT / Cash / Card / Yoco..."></div>
      </div>
      <button class="primary" onclick="saveOrderDetails()" style="margin-top:10px;">Save Job Details</button>
      <button onclick="openQuoteFromIndex(${q.id})" style="margin-top:10px;">Open in Quote Builder (line items)</button>
      <p class="muted" id="orderDetailsSaveStatus" style="margin-top:8px;"></p>

      <h2 style="margin-top:20px;">Follow-Ups</h2>
      <div id="followUpList" style="margin-bottom:10px;"></div>
      <div class="grid">
        <div class="field"><label>Date</label><input id="fu_date" type="date"></div>
        <div class="field" style="grid-column: span 2;"><label>Notes</label><input id="fu_notes" placeholder="e.g. Called about outstanding balance"></div>
      </div>
      <button onclick="logFollowUp()" style="margin-top:6px;">Log Follow-Up</button>
    </div>
  `;
  loadFollowUps();
  });
}

// Job Workflow actions (confirmed Aug 2026) — each a specific named
// event against the backend's own action endpoints, never a raw status
// field set directly (that's the override path above, deliberately
// separate and de-emphasized). Every one just re-renders this same
// screen on success so the workflow section immediately reflects the
// new state — no separate "did it work" check needed beyond that.
async function acceptQuoteAction(quoteId) {
  const res = await fetch(`${API}/quotes/${quoteId}/accept`, {method: 'POST'});
  if (!res.ok) { const e = await res.json().catch(()=>({})); alert(e.detail || 'Could not accept this quote.'); return; }
  renderOrderDetail(document.getElementById('landing'));
}
async function declineQuoteAction(quoteId) {
  if (!confirm('Mark this quote as declined? This stops it counting as an open job.')) return;
  const res = await fetch(`${API}/quotes/${quoteId}/decline`, {method: 'POST'});
  if (!res.ok) { const e = await res.json().catch(()=>({})); alert(e.detail || 'Could not decline this quote.'); return; }
  renderOrderDetail(document.getElementById('landing'));
}
async function scheduleQuoteAction(quoteId) {
  const dateVal = document.getElementById('wf_install_date').value;
  if (!dateVal) { alert('Pick an installation date first.'); return; }
  const res = await fetch(`${API}/quotes/${quoteId}/schedule?installation_date=${dateVal}`, {method: 'PUT'});
  if (!res.ok) { const e = await res.json().catch(()=>({})); alert(e.detail || 'Could not schedule this job.'); return; }
  renderOrderDetail(document.getElementById('landing'));
}
// Three independent actions (confirmed Aug 2026), not one combined
// save — materials_ordered, ready_for_installation, and installer_team
// each change independently in real life, at different times, so each
// gets its own immediate call rather than waiting for a shared "Save"
// click that could silently overwrite one with a stale value from the
// other.
async function setMaterialsOrdered(quoteId, checked) {
  await fetch(`${API}/quotes/${quoteId}/materials?materials_ordered=${checked}`, {method: 'PUT'});
  renderOrderDetail(document.getElementById('landing'));
}
async function setMaterialsReceived(quoteId, received) {
  // "Mark Materials Received" (confirmed directly): the flooring/blinds
  // have been delivered and are physically on hand, ready to install —
  // always a deliberate click, never inferred, since Bolton has no
  // physical stock-on-hand tracking to infer it from.
  await fetch(`${API}/quotes/${quoteId}/materials?ready_for_installation=${received}`, {method: 'PUT'});
  renderOrderDetail(document.getElementById('landing'));
}
async function saveInstallerTeam(quoteId) {
  const val = document.getElementById('wf_installer').value;
  await fetch(`${API}/quotes/${quoteId}/materials?installer_team=${encodeURIComponent(val)}`, {method: 'PUT'});
}
async function completeQuoteAction(quoteId) {
  if (!confirm('Mark installation complete for this job?')) return;
  const res = await fetch(`${API}/quotes/${quoteId}/complete`, {method: 'POST'});
  if (!res.ok) { const e = await res.json().catch(()=>({})); alert(e.detail || 'Could not mark this job complete.'); return; }
  renderOrderDetail(document.getElementById('landing'));
}
async function overrideWorkflowStatus(quoteId) {
  const newStatus = document.getElementById('wf_override_status').value;
  if (!confirm(`Manually set status to "${newStatus}"? This is the exception path — prefer Accept/Schedule/Complete above when they apply.`)) return;
  await fetch(`${API}/quotes/${quoteId}?workflow_status=${newStatus}`, {method: 'PUT'});
  renderOrderDetail(document.getElementById('landing'));
}

async function saveOrderDetails() {
  if (!currentOrderDetailQuoteId) return;
  const params = new URLSearchParams({
    site_address: document.getElementById('od_site_address').value,
    installation_date: document.getElementById('od_installation_date').value,
    invoice_sent_date: document.getElementById('od_invoice_sent_date').value,
    deposit_paid_date: document.getElementById('od_deposit_paid_date').value,
    deposit_payment_method: document.getElementById('od_deposit_payment_method').value,
    final_payment_date: document.getElementById('od_final_payment_date').value,
    final_payment_method: document.getElementById('od_final_payment_method').value,
  });
  await fetch(`${API}/quotes/${currentOrderDetailQuoteId}?${params}`, {method:'PUT'});
  document.getElementById('orderDetailsSaveStatus').textContent = `Saved ✓ ${new Date().toLocaleTimeString('en-ZA')}`;
}

async function logFollowUp() {
  if (!currentOrderDetailQuoteId) return;
  const followUpDate = document.getElementById('fu_date').value;
  if (!followUpDate) { alert('Pick a date first.'); return; }
  const notes = document.getElementById('fu_notes').value;
  const params = new URLSearchParams({follow_up_date: followUpDate, notes});
  await fetch(`${API}/quotes/${currentOrderDetailQuoteId}/follow-ups?${params}`, {method:'POST'});
  document.getElementById('fu_date').value = '';
  document.getElementById('fu_notes').value = '';
  loadFollowUps();
}

async function loadFollowUps() {
  const res = await fetch(`${API}/quotes/${currentOrderDetailQuoteId}/follow-ups`);
  const followUps = await res.json();
  const el = document.getElementById('followUpList');
  el.innerHTML = followUps.length
    ? followUps.map(f => `<div class="muted" style="font-size:12px; padding:3px 0;">${new Date(f.follow_up_date).toLocaleDateString('en-ZA')} — ${f.notes || '(no notes)'}</div>`).join('')
    : '<p class="muted" style="font-size:12px;">No follow-ups logged yet.</p>';
}
