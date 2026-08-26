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
// Group Multi-Quote Clients (confirmed Aug 2026, Order Index addendum
// #2) — which client groups are manually expanded, by client_id.
// Deliberately NOT reset on every re-render (only on a fresh fetch, in
// renderOrderIndex()) — toggling a tab or checking a box shouldn't
// collapse a group you just opened to look at.
let orderIndexExpandedClientIds = new Set();

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
  orderIndexExpandedClientIds = new Set();
  renderOrderIndexTable(searchTerm);
  // Focus/cursor restore only belongs on a genuine fetch (fresh load or
  // a search keystroke) — moved out of renderOrderIndexTable() itself
  // (confirmed Aug 2026, Group Multi-Quote Clients addendum) since that
  // function now also re-renders on a tab click or a group
  // expand/collapse, neither of which should steal focus back into the
  // search box every time.
  //
  // Real bug found and fixed (confirmed Aug 2026, Remove Unwanted
  // Auto-Focus brief) — the "fresh load" half of the comment above was
  // itself the bug: this fired unconditionally, including on the very
  // FIRST render (arriving here from the tiles menu), popping the
  // on-screen keyboard on mobile before anyone had tapped anything.
  // Only restore focus when this render was actually triggered by the
  // user typing (searchTerm passed as a real string via the oninput
  // handler) — never on the initial one-argument dispatch call
  // (renderLanding() -> renderOrderIndex(el)). Still needed for typing
  // itself: this function replaces the whole innerHTML, including the
  // input element, on every keystroke, so without this restore, typing
  // a second character would drop focus entirely.
  const input = document.getElementById('orderSearchInput');
  if (input && searchTerm !== undefined) { input.focus(); input.setSelectionRange(input.value.length, input.value.length); }
  });
}

function renderOrderIndexTable(searchTerm) {
  // Real gap found and fixed while building Group Multi-Quote Clients
  // (confirmed Aug 2026): setOrderIndexTab()/toggleClientGroup() call
  // this with no searchTerm arg, since they're not themselves a new
  // search — without this fallback, switching tabs (or expanding a
  // group) would visibly blank the search box even though the
  // underlying orderIndexQuotesCache still reflects whatever was
  // searched for. Read it back from the input itself when not given.
  if (searchTerm === undefined) {
    const existingInput = document.getElementById('orderSearchInput');
    searchTerm = existingInput ? existingInput.value : '';
  }
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
  // PRIORITY_ORDER/PRIORITY_FLAG defined once at module scope below
  // (shared with the group-header "most urgent" logic in
  // buildOrderIndexRowsHtml() — same ranking used in both places).
  const attentionItems = quotes.filter(q => q.attention_priority)
    .sort((a, b) => PRIORITY_ORDER[a.attention_priority] - PRIORITY_ORDER[b.attention_priority]);
  const attentionHtml = attentionItems.length ? attentionItems.map(q => `
    <div class="attention-item priority-${q.attention_priority}" onclick="openOrderDetailScreen(${q.id})">
      <span class="attn-flag">${PRIORITY_FLAG[q.attention_priority]} ${q.attention_label}</span>
      <span class="attn-detail">${q.job_number || '#'+q.id} — ${q.client_name}${q.description ? ' · '+q.description : ''}</span>
      ${nextActionButton(q)}
    </div>`).join('') : '<p class="muted" style="margin:0;">Nothing needs attention right now.</p>';

  const rows = shown.length ? buildOrderIndexRowsHtml(shown, isOwner, money, !!searchTerm) : `<tr><td colspan="${isOwner ? 8 : 7}" class="muted">No jobs match.</td></tr>`;

  const tab = (key, label, count) => `<button onclick="setOrderIndexTab('${key}')" style="${orderIndexActiveTab===key ? 'background:var(--teal); color:white; border-color:var(--teal);' : ''}">${label}${count !== undefined ? ` (${count})` : ''}</button>`;

  // Unlinked Quotes notice (confirmed Aug 2026, Save Redirect + Client
  // Link Missing brief §3 — "if quotes with no real client link already
  // exist, report how many and propose how to reconcile them... rather
  // than silently merging or deleting anything"). No DB access from
  // here to fix these directly or decide which client each one
  // actually belongs to — this surfaces exactly which rows are
  // affected, from data already fetched, and points at the Job Detail
  // "Client" section (order-index.js's own renderOrderDetail()) built
  // for precisely this — Burgert confirms and links each one himself,
  // nothing is guessed or auto-merged. Only shown at all when there's
  // genuinely something to report, and only to Owner (same data every
  // row already carries, just isn't worth a second fetch for).
  const unlinked = isOwner ? quotes.filter(q => !q.client_id) : [];
  const unlinkedHtml = unlinked.length ? `
    <div class="card" style="border-color:var(--coral);">
      <h2 style="color:var(--coral);">⚠ ${unlinked.length} Quote${unlinked.length!==1?'s':''} With No Linked Client</h2>
      <p class="muted" style="margin-top:-8px;">These show up here because the Order Index lists every quote regardless — but they won't appear in any client's own Order History until linked. Click one to open its Job Detail page and link it under "Client".</p>
      <div class="attention-list">
        ${unlinked.map(q => `<div class="attention-item priority-notice" onclick="openOrderDetailScreen(${q.id})"><span class="attn-flag">${q.job_number || '#'+q.id}</span><span class="attn-detail">${q.client_name}${q.description ? ' · '+q.description : ''}</span></div>`).join('')}
      </div>
    </div>` : '';

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

    ${unlinkedHtml}

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
      <!-- Mobile Rendering Audit brief (confirmed Aug 2026) -- same
           .mobile-card-table treatment as Business Overview's By
           Branch/By Sales Owner tables (found needing it during that
           brief's own required systematic sweep). overflow-x:auto above
           is kept as a harmless desktop-only safety net -- inert once
           the card layout takes over below the breakpoint. -->
      <table class="mobile-card-table"><thead><tr>
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
          <!-- Default Branch per Staff (confirmed Aug 2026) — same
          per-render defaulting as clients.js's own New Client form. -->
          <select id="oi_cl_branch">
            <option value="gansbaai" ${defaultBranchForCurrentUser()==='gansbaai'?'selected':''}>Gansbaai</option>
            <option value="hermanus" ${defaultBranchForCurrentUser()==='hermanus'?'selected':''}>Hermanus</option>
          </select>
        </div>
        <div class="field" style="grid-column: span 2;"><label>Address</label><input id="oi_cl_address" placeholder="Site/delivery address"></div>
      </div>
      <br><button class="primary" onclick="addClientAndStartQuote()">Add Client &amp; Start Quote</button>
    </div>
  `;
}

// Group Multi-Quote Clients (confirmed Aug 2026, second Order Index
// addendum) — "any client with more than one open quote/job... grouped
// under a single collapsed row." Grouped strictly by client_id, never
// by client_name string matching — two walk-in/one-off quotes (no
// client_id) that happen to share a typed name are NOT the same client
// record and must never be grouped together. A client with only one
// job in the CURRENTLY SHOWN set (i.e. within the active tab/search)
// renders as a normal single row, same as today (brief §5) — grouping
// is evaluated against what's actually visible, not the client's
// all-time job count, so switching tabs can correctly ungroup someone
// down to a single row.
const PRIORITY_ORDER = {critical: 0, warning: 1, notice: 2};
const PRIORITY_FLAG = {critical: '🔴', warning: '🟠', notice: '🟡'};

function toggleClientGroup(clientId) {
  if (orderIndexExpandedClientIds.has(clientId)) orderIndexExpandedClientIds.delete(clientId);
  else orderIndexExpandedClientIds.add(clientId);
  renderOrderIndexTable();
}

function orderIndexRowHtml(q, isOwner, money, isChild) {
  return `
    <tr id="oi-row-${q.id}" style="cursor:pointer;${isChild ? ' background:var(--bg,#f5f6f8);' : ''}" onclick="openOrderDetailScreen(${q.id})">
      ${isOwner ? `<td data-label="" onclick="event.stopPropagation();"><input type="checkbox" class="oi-select" value="${q.id}" onchange="toggleOrderSelected(${q.id}, this.checked)"></td>` : ''}
      <td class="job-number card-title" data-label="Job"${isChild ? ' style="padding-left:28px;"' : ''}>${q.job_number || `#${q.id}`}</td>
      <td data-label="Customer">${isChild ? '' : (q.client_id
          ? `<span style="cursor:pointer; color:var(--teal); text-decoration:underline;" onclick="event.stopPropagation(); openClientDetail(${q.client_id})" title="View client details">${q.client_name}</span>`
          : `<span title="No linked client record — walk-in/one-off">${q.client_name}</span>`)}
        ${q.description ? `<br><span class="muted" style="font-size:11px;">${q.description}</span>` : ''}</td>
      <td data-label="Value">${money(q.total_incl_vat)}${(q.manual_override_total_incl_vat != null || q.has_line_override) ? `<br><span class="muted" style="font-size:10px; color:var(--coral); font-weight:700;" title="A line or the total on this job was manually adjusted — see Job Detail / Quote Builder for the reason">✏️ Adjusted</span>` : ''}</td>
      <td data-label="Status">${workflowStatusBadge(q)}</td>
      <td data-label="Install Date">${dateOrDash(q.installation_date)}</td>
      <td data-label="Next Action">${nextActionButton(q) || '<span class="muted">—</span>'}</td>
      <td class="card-actions-cell" data-label="">
        <!-- Client Link Gap fix (confirmed Aug 2026, Order Index ->
        Client Link Gap brief, Gap 1) — the Client Grouping addendum's
        "Edit client" link only ever existed on a GROUPED row's header;
        a client with just one quote had no equivalent, consistent way
        to reach their own page from here (brief's own words: "a gap in
        that spec, not something missed during implementation"). Same
        link, same target (openClientDetail(id, true) — straight to the
        edit form), on every standalone row now too. Child rows inside
        an expanded group still don't repeat it — their own group
        header, immediately above, already has it for that same client. -->
        ${(!isChild && q.client_id) ? `<a href="#" onclick="event.stopPropagation(); openClientDetail(${q.client_id}, true); return false;" style="font-size:12px; margin-right:8px;" title="Edit this client's details">Edit client</a>` : ''}
        <!-- Quick View (confirmed Aug 2026, third placement of the
        existing Document Preview) — reuses documentPreviewTileHtml()/
        loadDocumentPreview()/editDocumentPreview() exactly as already
        built (shared.js), no new preview component. Works identically
        for standalone rows and rows nested inside an expanded group —
        toggleQuickView() only needs this row's own quote id. -->
        <a href="#" onclick="event.stopPropagation(); toggleQuickView(${q.id}); return false;" style="font-size:12px; margin-right:8px;" title="Preview this quote/invoice without leaving the Order Index">Quick View</a>
        <button onclick="event.stopPropagation(); duplicateQuoteFromIndex(${q.id}, '${(q.client_name||'').replace(/'/g,"\\'")}', ${q.client_id || 'null'})">Duplicate</button>
        ${isOwner ? `<button class="delete-btn" onclick="event.stopPropagation(); deleteQuoteFromIndex(${q.id})">Delete</button>` : ''}
      </td>
    </tr>`;
}

// Quick View (confirmed Aug 2026, third placement of the existing
// Document Preview feature — client page Order History and the Job
// Detail page are the other two, same shared documentPreviewTileHtml()/
// loadDocumentPreview() component, never a separate design). Inserts a
// new row directly below the clicked one holding the preview tile —
// real DOM insertion, not a template re-render, so opening/closing it
// never disturbs any other row's own state (another expanded group,
// another open Quick View, checkbox selections, etc.). Lazy: the
// preview is only fetched the first time a row's Quick View is opened,
// not for every visible row up front.
function toggleQuickView(quoteId) {
  const existingRow = document.getElementById(`oi-quickview-${quoteId}`);
  if (existingRow) { existingRow.remove(); return; }
  const anchorRow = document.getElementById(`oi-row-${quoteId}`);
  if (!anchorRow) return;
  const previewId = `dp_oi_quickview_${quoteId}`;
  const newRow = document.createElement('tr');
  newRow.id = `oi-quickview-${quoteId}`;
  // colspan="100%" rather than a hardcoded column count — correctly
  // spans the real number of columns regardless of Owner's extra
  // checkbox column, with no need to thread isOwner through here too.
  newRow.innerHTML = `<td colspan="100%" style="background:var(--bg,#f5f6f8); padding:12px;" onclick="event.stopPropagation();">${documentPreviewTileHtml(previewId, quoteId)}</td>`;
  anchorRow.after(newRow);
  loadDocumentPreview(previewId, quoteId);
}

function buildOrderIndexRowsHtml(shown, isOwner, money, isSearching) {
  // Group by client_id, preserving each group's first-appearance
  // position in `shown` so the table's overall order doesn't jump
  // around as groups collapse/expand.
  const byClient = {};
  shown.forEach(q => { if (q.client_id) (byClient[q.client_id] = byClient[q.client_id] || []).push(q); });
  const groupClientIds = new Set(Object.keys(byClient).filter(cid => byClient[cid].length > 1).map(Number));

  const seenGroup = new Set();
  return shown.map(q => {
    if (!q.client_id || !groupClientIds.has(q.client_id)) {
      return orderIndexRowHtml(q, isOwner, money, false);
    }
    if (seenGroup.has(q.client_id)) return '';   // absorbed into the group row already emitted below
    seenGroup.add(q.client_id);
    const groupQuotes = byClient[q.client_id];
    // Search auto-expands every group in the (already search-filtered)
    // result set (confirmed Aug 2026, brief §4) — "should auto-expand
    // the relevant group so the matching job is visible, not hidden
    // inside a still-collapsed row." Otherwise, respect whatever the
    // user has manually toggled.
    const expanded = isSearching || orderIndexExpandedClientIds.has(q.client_id);
    const withAttention = groupQuotes.filter(g => g.attention_priority)
      .sort((a, b) => PRIORITY_ORDER[a.attention_priority] - PRIORITY_ORDER[b.attention_priority]);
    // Critical requirement (confirmed Aug 2026, brief §2): collapsing
    // must never hide urgency — the group header itself always shows
    // the single most urgent Next Action across every job in it, same
    // priority flag as the Needs Attention list. Falls back to the
    // first job's own Next Action (no flag) if nothing in the group is
    // actually flagged urgent.
    const urgent = withAttention[0];
    const headerAction = urgent
      ? `${PRIORITY_FLAG[urgent.attention_priority]} ${urgent.attention_label}`
      : (groupQuotes[0].next_action || '');
    // Group Header Row Doesn't Match Column Layout (confirmed Aug 2026):
    // Value/Status/Install Date used to be one blank colspan=3 cell,
    // making the collapsed header visually read as a different kind of
    // element from the job rows below it — easy to skip over while
    // scanning. Now populated with real per-column summaries so the
    // header reads as one row of the same table, not a separate banner.
    // Value = total combined value across the group (collapsed);
    // expanding still shows each job's own individual price on its own
    // row via orderIndexRowHtml — both confirmed directly with Burgert,
    // not guessed.
    const groupTotal = groupQuotes.reduce((sum, g) => sum + (g.total_incl_vat || 0), 0);
    // Status = the shared badge if every job in the group is on the same
    // workflow_status, otherwise "Mixed" — brief §2's own wording.
    const statusSet = new Set(groupQuotes.map(g => g.workflow_status));
    const groupStatusHtml = statusSet.size === 1
      ? workflowStatusBadge(groupQuotes[0])
      : `<span class="status-badge" style="background:#f0f0f0; color:#6b7280;">Mixed</span>`;
    // Install Date = nearest/soonest date among jobs that have one set;
    // blank (—, via dateOrDash) if none do, same as an individual row.
    const installDates = groupQuotes.map(g => g.installation_date).filter(Boolean);
    const nearestInstallDate = installDates.length
      ? installDates.reduce((min, d) => new Date(d) < new Date(min) ? d : min)
      : null;
    // Column math must match the real header row exactly (Job |
    // Customer | Value | Status | Install Date | Next Action | Actions
    // = 7 cells, +1 checkbox for Owner) — a mismatch here silently
    // misaligns every column below whenever a group is present.
    // Group header checkbox (confirmed Aug 2026, Group Header Alignment
    // brief follow-up) — the one remaining visual gap once Value/
    // Status/Install Date were filled in: an empty checkbox column on
    // the header row while every individual row has one. Selects every
    // job in the group at once for bulk delete, not just a visual
    // filler — orderIndexSelectedIds (the actual bulk-delete selection
    // state) doesn't require a row to be visually expanded/rendered to
    // hold its id, so this works correctly whether the group is
    // collapsed or expanded.
    const groupQuoteIds = groupQuotes.map(g => g.id);
    const allGroupSelected = groupQuoteIds.every(id => orderIndexSelectedIds.has(id));
    const headerRow = `
      <tr style="cursor:pointer; font-weight:600;" onclick="toggleClientGroup(${q.client_id})">
        ${isOwner ? `<td data-label="" onclick="event.stopPropagation();"><input type="checkbox" ${allGroupSelected ? 'checked' : ''} onchange="toggleGroupSelected([${groupQuoteIds.join(',')}], this.checked)"></td>` : ''}
        <td colspan="2" class="card-title" data-label="Client">${expanded ? '▾' : '▸'} ${q.client_name} <span class="muted" style="font-weight:400;">(${groupQuotes.length} jobs)</span></td>
        <td data-label="Total Value">${money(groupTotal)}</td>
        <td data-label="Status">${groupStatusHtml}</td>
        <td data-label="Next Install">${dateOrDash(nearestInstallDate)}</td>
        <td data-label="Next Action">${headerAction ? `<span class="muted" style="font-weight:400;">${headerAction}</span>` : ''}</td>
        <td class="card-actions-cell" data-label="" onclick="event.stopPropagation();"><a href="#" onclick="openClientDetail(${q.client_id}, true); return false;" style="font-size:12px;" title="Edit this client's details">Edit client</a></td>
      </tr>`;
    const childRows = expanded ? groupQuotes.map(g => orderIndexRowHtml(g, isOwner, money, true)).join('') : '';
    return headerRow + childRows;
  }).join('');
}

function toggleOrderSelected(quoteId, checked) {
  if (checked) orderIndexSelectedIds.add(quoteId); else orderIndexSelectedIds.delete(quoteId);
  updateBulkDeleteButtonState();
}

// Group header checkbox (confirmed Aug 2026) — selects/deselects every
// job in the group at once. Also syncs each visible CHILD row's own
// checkbox when the group is currently expanded, purely for visual
// consistency — orderIndexSelectedIds itself is already correct either
// way, a collapsed row's checkbox never needing to exist in the DOM for
// its id to count toward the bulk-delete selection.
function toggleGroupSelected(quoteIds, checked) {
  quoteIds.forEach(id => {
    if (checked) orderIndexSelectedIds.add(id); else orderIndexSelectedIds.delete(id);
    const cb = document.querySelector(`.oi-select[value="${id}"]`);
    if (cb) cb.checked = checked;
  });
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
// Confirmed root cause (Aug 2026, Save Redirect + Client Link Missing
// brief) of duplicated quotes losing their real client link: this used
// to prompt() for "client name" as free text, pre-filled with the
// source's name — ANY edit at all (including a well-intentioned note
// typed into what looked like a free-text box, e.g. describing what's
// different about this copy) sent that text back as a "different
// client," with no real client_id behind it, silently creating an
// orphaned duplicate. Redesigned so the client can ONLY ever be "the
// same as the source" (explicit client_id, or none if the source
// itself was a walk-in — never re-derived from a name comparison) or
// "a different, actually-selected real client" via openClientPicker()
// (shared.js) — never free text, so this exact failure mode can't
// recur. If you want to add a note about what's different in this
// copy, use the Description field on the duplicate itself (already
// pre-filled as "Copy of ..." and editable) — that's what it's for.
async function duplicateQuoteFromIndex(quoteId, clientName, clientId) {
  const sameClient = confirm(`Duplicate quote #${quoteId}${clientName ? ' for ' + clientName : ''}?\n\nOK = same client\nCancel = link the duplicate to a different client instead`);
  let body = {};
  if (sameClient) {
    if (clientId) body = {client_id: clientId};
    // else: the source itself has no real client record (a walk-in) — body stays empty, the backend correctly keeps the source's own client_id (still null) and client_name text as-is
  } else {
    const picked = await openClientPicker('Link the duplicate to which client?');
    if (!picked) return;   // fully cancelled — no duplicate made at all, same as cancelling the confirm() above
    body = {client_id: picked.id};
  }
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
  const [res, orderSheetsRes] = await Promise.all([
    fetch(`${API}/quotes/${currentOrderDetailQuoteId}?role=${currentRole()}`),
    fetch(`${API}/quotes/${currentOrderDetailQuoteId}/order-sheets`),
  ]);
  const data = await res.json();
  const q = data.quote;
  const orderSheets = orderSheetsRes.ok ? await orderSheetsRes.json() : [];
  // Page Title in Sticky Header brief -- mirrors this same screen's own
  // <h1> formula below exactly, so the two never say something different.
  setPageTitle(`${q.job_number || 'Quote #' + q.id}${q.description ? ' — ' + q.description : ''}`);

  // Document Preview, placement 1b (confirmed Aug 2026, Client Page &
  // Quote Detail: Document Preview + Inline Edit brief) — right-hand
  // panel alongside Workflow + Job Details, roughly spanning their
  // combined height. Same documentPreviewTileHtml() component as
  // placement 1a (client Order History) — one template, two placements,
  // per the brief's own explicit instruction. Edit button inside it
  // calls editDocumentPreview(), which is the exact same
  // openQuoteFromIndex() the "Open in Quote Builder (line items)"
  // button below already uses — not a duplicate entry point.
  el.innerHTML = `
    <span class="back-link" onclick="landingView='orders'; renderLanding();">← Back to Order Index</span>
    <div class="landing-welcome">
      <h1>${q.job_number || 'Quote #' + q.id}${q.description ? ' — ' + q.description : ''}</h1>
      <p>${q.client_name} &nbsp; ${workflowStatusBadge(q)}</p>
    </div>

    <div class="job-detail-layout">
      <div class="job-detail-main">
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

        <!-- Order Sheets UX brief §3 (confirmed Aug 2026) — "Generate
        Order Sheet(s) is buried... move it to a prominent position...
        alongside or directly below the Workflow section." Moved out of
        the Job Details card (was near the bottom of a long page) to
        its own card right here, immediately below Workflow -- visible
        without scrolling on exactly the screen this matters most on
        (arriving via "Prepare Job" from Needs Attention). §1's real
        result banner (the earlier duplicate happened because pressing
        Generate gave no visible confirmation) shows here too. -->
        <div class="card">
          <h2>Order Sheets</h2>
          <div id="orderSheetsResultBanner" style="display:none; background:#dcf5e6; color:#1a7a3e; border:2px solid #1a7a3e; border-radius:8px; padding:10px 14px; margin-bottom:12px; font-weight:700; font-size:13.5px;"></div>
          ${orderSheets.length ? orderSheets.map(s => `
            <div style="display:flex; align-items:center; gap:10px; padding:8px 0; border-bottom:1px solid var(--border); cursor:pointer;" onclick="openOrderSheetDetail(${s.id})">
              <b>${s.order_number}</b>
              <span class="muted">${s.supplier}</span>
              <span class="badge ${s.sheet_type === 'floor_prep' ? 'flooring' : 'trim'}">${s.sheet_type === 'floor_prep' ? 'Floor Prep' : 'Flooring'}</span>
              ${s.status === 'placed' ? `<span class="status-badge active-status">Placed</span>` : `<span class="status-badge pending-status">Draft</span>`}
              <span class="muted" style="margin-left:auto; font-size:12px;">View →</span>
            </div>`).join('') : '<p class="muted" style="margin-top:-6px;">No order sheets generated yet for this job.</p>'}
          <button class="primary" onclick="generateOrderSheetsForQuote(${q.id})" style="margin-top:10px;">Generate Order Sheet(s)</button>
        </div>

        <div class="card">
          <h2>Job Details</h2>
          <!-- Save confirmation (confirmed Aug 2026, Deposit Amount +
          Save Confirmation + Default Branch brief §2) — replaces the
          old small grey "Saved ✓ 13:05:17" note (easy to miss) with a
          real, temporary success banner right at the top of this card.
          Deliberately NOT a navigate-away-on-save, unlike Quote
          Builder's own Save (Save Redirect brief) — this page hosts
          several related actions in one visit (Job Details, Document
          Preview, Follow-Ups), so auto-navigating away would interrupt
          someone doing more than one of those. showJobDetailsSaveBanner()
          (below) shows this and fades it after a few seconds; leaving
          the page is always the user's own choice. -->
          <div id="jobDetailsSaveBanner" style="display:none; background:#dcf5e6; color:#1a7a3e; border:2px solid #1a7a3e; border-radius:8px; padding:10px 14px; margin-bottom:12px; font-weight:700; font-size:13.5px;"></div>
          <p class="muted" style="margin-top:-8px;">Site/installation and payment tracking for this job — shown at a glance on the Order Index once saved.</p>

          <!-- Manual Override total display (confirmed Aug 2026, Manual
          Override brief) — Job Detail doesn't show individual line
          items (that's Quote Builder's job, via "Open in Quote Builder"
          below), so this is the TOTAL only, right alongside the
          deposit/payment fields it actually affects (deposit_amount/
          balance_amount are derived FROM the override server-side —
          _quote_totals(), main.py — so what's recorded here always
          matches the real agreed figure). Badge visible to every
          internal role; Override/Revert action Owner-only, same split
          as Quote Builder's own line/total controls. -->
          <p style="margin:0 0 4px;">
            <b>Total (incl VAT):</b> R${data.total_incl_vat.toFixed(2)}
            ${q.manual_override_total_incl_vat != null ? `<span class="muted" style="font-size:11px; color:var(--coral); font-weight:700;" title="${(q.override_total_reason || '').replace(/"/g,'&quot;')} — by ${q.override_total_by || ''}${q.override_total_at ? ' on ' + new Date(q.override_total_at).toLocaleDateString('en-ZA') : ''}"> ✏️ Manually adjusted</span>` : ''}
            ${currentRole() === 'owner' ? (q.manual_override_total_incl_vat != null
              ? ` <a href="#" onclick="revertJobDetailTotalOverride(); return false;" style="font-size:11px; color:var(--teal); font-weight:600;">Revert to calculated</a>`
              : ` <a href="#" onclick="overrideJobDetailTotal(${data.total_incl_vat}); return false;" style="font-size:11px; color:var(--teal); font-weight:600;">Override total</a>`) : ''}
          </p>
          <!-- Deposit Amount (confirmed Aug 2026, brief §1) — same
          precedence/flagging pattern as the Manual Override total just
          above, without a mandatory reason: this isn't a price
          correction needing justification, just what was actually
          paid. balance_amount below is already computed FROM this
          figure server-side (_quote_totals()), never a second,
          separate calculation here. -->
          <p style="margin:0 0 4px;">
            <b>Deposit:</b> R${data.deposit_amount.toFixed(2)}
            ${q.actual_deposit_amount != null ? `<span class="muted" style="font-size:11px; color:var(--coral); font-weight:700;" title="Entered by ${q.actual_deposit_amount_by || ''}${q.actual_deposit_amount_at ? ' on ' + new Date(q.actual_deposit_amount_at).toLocaleDateString('en-ZA') : ''}"> ✏️ Actual (manually entered)</span>` : ` <span class="muted" style="font-size:11px;">(${(q.deposit_pct*100).toFixed(0)}% calculated)</span>`}
          </p>
          <p style="margin:0 0 12px;"><b>Balance due:</b> R${data.balance_amount.toFixed(2)}</p>

          <!-- Client link (confirmed Aug 2026, Order Index -> Client
          Link Gap brief, Gap 2 fix) — real gap closed: there was
          previously no way to link an existing quote to a real Client
          record after the fact. A quote typed as a plain name in Quote
          Builder without clicking the matching autocomplete suggestion
          becomes a permanently disconnected walk-in (client_id=None) —
          it shows correctly on the Order Index (which lists every
          quote regardless), but never appears in that real client's
          own Order History (which filters strictly by client_id).
          This is exactly that self-service fix, right where the
          problem is actually noticed. -->
          <div class="field" style="grid-column: span 2; position:relative; margin-bottom:14px;">
            <label>Client</label>
            ${q.client_id
              ? `<p style="margin:0;"><a href="#" onclick="openClientDetail(${q.client_id}); return false;" style="color:var(--teal); font-weight:600;">${q.client_name}</a> <a href="#" onclick="document.getElementById('jd_relink_box').style.display='block'; this.style.display='none'; return false;" style="font-size:12px; margin-left:8px;">Change</a></p>`
              : `<p style="margin:0; color:var(--coral);">Not linked to a client record — a walk-in quote. Search below to link it to a real client so it shows up in their Order History.</p>`}
            <div id="jd_relink_box" style="${q.client_id ? 'display:none;' : ''} margin-top:8px;">
              <input type="text" id="jd_client_search" placeholder="Search existing clients by name..." oninput="onJobDetailClientSearch(${q.id}, this.value)" autocomplete="off">
              <div id="jdClientSuggestions" style="display:none; position:absolute; z-index:10; background:white; border:1px solid var(--border); border-radius:6px; width:100%; max-height:160px; overflow-y:auto; box-shadow:0 4px 10px rgba(0,0,0,0.1);"></div>
            </div>
          </div>

          <div class="grid">
            <div class="field" style="grid-column: span 2;"><label>Site address</label><input id="od_site_address" value="${q.site_address || ''}" placeholder="Install/delivery site, if different from the client's own address"></div>
            <div class="field"><label>Installation date</label><input id="od_installation_date" type="date" value="${q.installation_date || ''}"></div>
            <div class="field"><label>Invoice sent date</label><input id="od_invoice_sent_date" type="date" value="${q.invoice_sent_date || ''}"></div>
            <div class="field"><label>Deposit paid date</label><input id="od_deposit_paid_date" type="date" value="${q.deposit_paid_date || ''}"></div>
            <div class="field"><label>Deposit amount (R) <span class="adj">(actual amount paid — overrides the ${(q.deposit_pct*100).toFixed(0)}% calculated figure once entered; leave blank to use the percentage)</span></label><input id="od_actual_deposit_amount" type="number" step="0.01" value="${q.actual_deposit_amount != null ? q.actual_deposit_amount : ''}" placeholder="e.g. ${(data.total_incl_vat * q.deposit_pct).toFixed(2)} (calculated)"></div>
            <div class="field"><label>Deposit payment method</label><input id="od_deposit_payment_method" value="${q.deposit_payment_method || ''}" placeholder="EFT / Cash / Card / Yoco..."></div>
            <div class="field"><label>Final payment date</label><input id="od_final_payment_date" type="date" value="${q.final_payment_date || ''}"></div>
            <div class="field"><label>Final payment method</label><input id="od_final_payment_method" value="${q.final_payment_method || ''}" placeholder="EFT / Cash / Card / Yoco..."></div>
          </div>
          <button class="primary" onclick="saveOrderDetails()" style="margin-top:10px;">Save Job Details</button>
          <button onclick="openQuoteFromIndex(${q.id})" style="margin-top:10px;">Open in Quote Builder (line items)</button>

          <h2 style="margin-top:20px;">Follow-Ups</h2>
          <div id="followUpList" style="margin-bottom:10px;"></div>
          <div class="grid">
            <div class="field"><label>Date</label><input id="fu_date" type="date"></div>
            <div class="field" style="grid-column: span 2;"><label>Notes</label><input id="fu_notes" placeholder="e.g. Called about outstanding balance"></div>
          </div>
          <button onclick="logFollowUp()" style="margin-top:6px;">Log Follow-Up</button>
        </div>
      </div>

      <div class="job-detail-preview">
        <div class="card">
          <h2>Document Preview</h2>
          ${documentPreviewTileHtml('dp_jobdetail_' + q.id, q.id)}
        </div>

        <!-- Dropbox Document Archive brief (confirmed Aug 2026) —
        scoped to Quotes for this pass (Order Sheets have no existing
        print-style document template to archive yet — a real gap,
        flagged as its own follow-up rather than rushed here). Manual
        trigger only, same "explicit action, not silent autosave"
        philosophy already established for Order Sheets generation —
        archives whatever the Document Preview above is ACTUALLY
        showing right now (buildPrintDocHtml(), shared.js — the exact
        same function, unchanged), so there is exactly one source for
        what this quote's document looks like. No Dropbox token is
        configured yet (confirmed with Burgert) — every version still
        renders and stores a real PDF and shows honestly as "Pending"
        until one is set; nothing here is faked or skipped. -->
        <div class="card" id="documentArchiveCard">
          <h2>Document Archive</h2>
          <p class="muted" style="margin-top:-8px;">Backup copy in Dropbox, separate from Bolton's own database — every archived version is kept, never overwritten.</p>
          <div id="documentArchiveContent" class="muted">Loading...</div>
        </div>

        <!-- Order Sheet Preview (confirmed Aug 2026, Order Sheets UX
        brief §4) — a SECOND, equally prominent preview panel, clearly
        labelled so there's never ambiguity about which panel is the
        client-facing quote (above) and which is the supplier-facing
        procurement document (this one). Only shown once at least one
        order sheet exists for this job -- an empty card here before
        anything's been generated would just be dead space alongside a
        real Document Preview. Editable/savable/executable per the
        brief's own words: orderSheetLinesEditorHtml() (shared with the
        standalone Order Sheet Detail screen) is the same live-save-on-
        change table used there, "Mark as Placed" is the executable
        action, Delete is the owner-only cleanup action (§2) -- this is
        genuinely the real order sheet, not a read-only summary of it. -->
        ${orderSheets.length ? `
        <div class="card" id="orderSheetPreviewCard">
          <h2>Order Sheet Preview <span class="muted" style="font-weight:400; font-size:12px;">(supplier-facing procurement document — not the client's quote)</span></h2>
          ${orderSheets.map(s => `
            <div style="border:1px solid var(--border); border-radius:8px; padding:12px; margin-bottom:12px;">
              <div style="display:flex; align-items:center; gap:8px; flex-wrap:wrap; margin-bottom:8px;">
                <b>${s.order_number}</b>
                <span class="muted">${s.supplier}</span>
                <span class="badge ${s.sheet_type === 'floor_prep' ? 'flooring' : 'trim'}">${s.sheet_type === 'floor_prep' ? 'Floor Prep' : 'Flooring'}</span>
                ${s.status === 'placed' ? `<span class="status-badge active-status">Placed</span>` : `<span class="status-badge pending-status">Draft</span>`}
                <a href="#" onclick="openOrderSheetDetail(${s.id}); return false;" style="font-size:12px; margin-left:auto;">Full page →</a>
              </div>
              ${orderSheetLinesEditorHtml(s, s.sheet_type === 'floor_prep' && s.status !== 'placed')}
              <div style="margin-top:12px; display:flex; gap:8px; flex-wrap:wrap;">
                ${s.status !== 'placed' ? `<button class="primary" onclick="finalizeOrderSheet(${s.id})">Mark as Placed</button>` : `<span class="muted" style="font-size:11px; align-self:center;">Placed by ${s.placed_by || ''}${s.placed_at ? ' on ' + new Date(s.placed_at).toLocaleDateString('en-ZA') : ''}</span>`}
                ${currentRole() === 'owner' ? `<button class="delete-btn" onclick="deleteOrderSheet(${s.id})">Delete</button>` : ''}
              </div>
            </div>`).join('')}
        </div>` : ''}
      </div>
    </div>
  `;
  loadFollowUps();
  loadDocumentPreview('dp_jobdetail_' + q.id, q.id);
  loadDocumentArchiveStatus('Quote', q.id, q.job_number || ('Q-' + q.id), q.id, 'quote');
  // Immediate, visible confirmation after Generate (confirmed Aug
  // 2026, brief §1+§4 -- "immediate, visible confirmation/preview...
  // directly prevents the confusion that caused Section 1's
  // duplicate"). generateOrderSheetsForQuote() sets this right before
  // calling this same render function again.
  if (window._orderSheetsResultMessage) {
    showOrderSheetsResultBanner(window._orderSheetsResultMessage);
    window._orderSheetsResultMessage = null;
  }
  });
}

let orderSheetsResultBannerTimeout = null;
function showOrderSheetsResultBanner(message) {
  const banner = document.getElementById('orderSheetsResultBanner');
  if (!banner) return;
  banner.textContent = message;
  banner.style.display = 'block';
  clearTimeout(orderSheetsResultBannerTimeout);
  orderSheetsResultBannerTimeout = setTimeout(() => { banner.style.display = 'none'; }, 6000);
  // Scroll the Order Sheet Preview panel into view too -- the banner
  // sits in the left column's Order Sheets card, but the actual
  // generated content (what genuinely proves it worked) is in the
  // right column, which can be below the fold on mobile.
  const previewCard = document.getElementById('orderSheetPreviewCard');
  if (previewCard) previewCard.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
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
  // Dropbox Document Archive brief §3 (confirmed Aug 2026) — "once the
  // customer accepts the quote, preserve the accepted version
  // distinctly." Archives right here, at the actual acceptance event
  // — not a separate manual step someone could forget — using
  // whatever the Document Preview would show for this quote right
  // now (buildPrintDocHtml(), same as every other archive call),
  // marked so exactly one version per quote is ever findable as "the
  // one that was actually agreed to." Best-effort: a failure here
  // (Dropbox down, or PDF rendering hiccup) must never block the
  // accept action that already succeeded above — same "Dropbox being
  // unavailable must not prevent Bolton from saving" principle (§7)
  // applied to this trigger point too.
  try {
    const qRes = await fetch(`${API}/quotes/${quoteId}?role=${currentRole()}`);
    if (qRes.ok) {
      const qData = await qRes.json();
      const reference = qData.quote.job_number || ('Q-' + quoteId);
      const { html } = await buildPrintDocHtml(quoteId, 'quote');
      const cssRes = await fetch('styles.css');
      const css = cssRes.ok ? await cssRes.text() : '';
      await fetch(`${API}/documents/archive`, {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ entity_type: 'Quote', entity_id: quoteId, reference, html, css, mark_as_accepted: true }),
      });
    }
  } catch (e) { /* best-effort -- the accept itself already succeeded above; this screen re-renders and shows the real archive status regardless */ }
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

// Manual Override on the quote total, Job Detail's own copy (confirmed
// Aug 2026, Manual Override brief) — same backend endpoints as Quote
// Builder's overrideQuoteTotal()/revertQuoteTotalOverride()
// (quote-builder.js), deliberately NOT shared with those: this screen
// tracks the open quote as currentOrderDetailQuoteId, not
// currentQuoteId (Quote Builder's own global) — reusing those functions
// as-is would silently act on the wrong quote if the two ever disagree.
async function overrideJobDetailTotal(currentValue) {
  const newValueStr = prompt(`Enter the manual override total, incl. VAT (currently R${currentValue.toFixed(2)}):`, currentValue.toFixed(2));
  if (newValueStr === null) return;
  const newValue = parseFloat(newValueStr);
  if (isNaN(newValue) || newValue < 0) { alert('Enter a valid, non-negative number.'); return; }
  const reason = prompt('Reason for this override (required — e.g. "Matching accepted price from legacy system"):');
  if (!reason || !reason.trim()) { alert('A reason is required to apply a manual override.'); return; }
  const res = await fetch(`${API}/quotes/${currentOrderDetailQuoteId}/override-total`, {
    method: 'PUT', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({new_value: newValue, reason: reason.trim()}),
  });
  if (!res.ok) { const body = await res.json().catch(() => ({})); alert(body.detail || 'Could not apply override.'); return; }
  renderOrderDetail(document.getElementById('landing'));
}

async function revertJobDetailTotalOverride() {
  if (!confirm("Revert this quote's total back to the calculated value?")) return;
  const res = await fetch(`${API}/quotes/${currentOrderDetailQuoteId}/revert-total-override`, {method: 'POST'});
  if (!res.ok) { const body = await res.json().catch(() => ({})); alert(body.detail || 'Could not revert override.'); return; }
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

// Link/relink to a real client (confirmed Aug 2026, Order Index ->
// Client Link Gap brief, Gap 2 fix) — same search-while-typing pattern
// as Quote Builder's own client field (onQClientInput/selectQClient,
// index.html), scoped to this one quote instead of a new-quote form.
let jdClientSearchTimeout = null;
function onJobDetailClientSearch(quoteId, value) {
  clearTimeout(jdClientSearchTimeout);
  const box = document.getElementById('jdClientSuggestions');
  if (!value || value.length < 2) { box.style.display = 'none'; return; }
  jdClientSearchTimeout = setTimeout(async () => {
    const res = await fetch(`${API}/clients?search=${encodeURIComponent(value)}`);
    const matches = await res.json();
    // Create New Client From Quote (confirmed Aug 2026) — real gap
    // found and confirmed reproducible: this search only ever searched
    // EXISTING clients, so a genuinely new person typed here (e.g.
    // Frikkie Klynhans's stuck quote) had no way to be created — the
    // quote was permanently stuck, unfixable through the UI at all.
    // Always offered, whether or not there are partial matches (a
    // partial match isn't necessarily the right person), same "explicit
    // one-click option, never a silent guess" approach as the Client-
    // Link Audit's own backend safety net.
    const createOption = `
      <div style="padding:8px 10px; cursor:pointer; color:var(--teal); font-weight:600;" onclick="createClientAndLinkQuote(${quoteId}, '${value.replace(/'/g,"\\'")}')">
        + Create new client: "${value.replace(/</g,'&lt;')}"
      </div>`;
    box.innerHTML = matches.map(c => `
      <div style="padding:8px 10px; cursor:pointer; border-bottom:1px solid var(--border);" onclick="linkQuoteToClient(${quoteId}, ${c.id}, '${c.name.replace(/'/g,"\\'")}')">
        <b>${c.name}</b>${c.phone ? ' — '+c.phone : ''}
      </div>`).join('') + createOption;
    box.style.display = 'block';
  }, 250);
}
async function linkQuoteToClient(quoteId, clientId, clientName) {
  if (!confirm(`Link this quote to ${clientName}? It will then show correctly in their own Order History.`)) return;
  await fetch(`${API}/quotes/${quoteId}?client_id=${clientId}`, {method: 'PUT'});
  renderOrderDetail(document.getElementById('landing'));
}
async function createClientAndLinkQuote(quoteId, name) {
  if (!confirm(`Create a new client named "${name}" and link this quote to them? Other details (phone, email, address) can be filled in afterward on their client page.`)) return;
  const res = await fetch(`${API}/clients`, {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({name})});
  if (!res.ok) { alert('Could not create the new client.'); return; }
  const client = await res.json();
  await fetch(`${API}/quotes/${quoteId}?client_id=${client.id}`, {method: 'PUT'});
  renderOrderDetail(document.getElementById('landing'));
}

// Supplier Order Sheets (confirmed Aug 2026) — manual trigger only
// (brief §2), so this is a real button click, never automatic. Reused
// verbatim from Client Detail's Orders tab (clients.js) for the
// individual sheet view itself — one screen, two entry points.
let currentOrderSheetId = null;

function openOrderSheetDetail(orderSheetId) {
  currentOrderSheetId = orderSheetId;
  landingView = 'orderSheetDetail';
  renderLanding();
}

async function generateOrderSheetsForQuote(quoteId) {
  if (!confirm('Generate order sheet(s) for this job now? This is a real procurement action — make sure the line items are final first.')) return;
  const res = await fetch(`${API}/quotes/${quoteId}/generate-order-sheets`, {method: 'POST'});
  if (!res.ok) { const body = await res.json().catch(() => ({})); alert(body.detail || 'Could not generate order sheet(s).'); return; }
  const result = await res.json();
  // Order Sheets UX brief §1 (confirmed Aug 2026) — unambiguous result
  // message using the backend's own generated/reused split, exactly
  // the visible confirmation that was missing before (the actual root
  // cause of the O-0001/O-0002 duplicate: pressing Generate gave no
  // visible result, so Burgert pressed it again assuming it failed).
  window._orderSheetsResultMessage = result.generated && result.reused
    ? `✓ Generated ${result.generated} new order sheet(s), reused ${result.reused} already-existing one(s) below.`
    : result.generated
    ? `✓ Generated ${result.generated} order sheet(s) — see below.`
    : `Already generated for this job — showing the existing order sheet(s) below, not a new duplicate.`;
  renderOrderDetail(document.getElementById('landing'));
}

// Order Sheets UX brief (confirmed Aug 2026) — orderSheetLinesEditorHtml()
// is the ONE table+total+add-extra-item template, used by BOTH the
// standalone Order Sheet Detail screen below AND the new inline
// "Order Sheet Preview" panel on Job Detail (§4) — same reasoning as
// documentPreviewTileHtml() being one component in two placements
// (Client Order History + Job Detail): a second, slightly different
// copy of this markup is exactly the kind of thing that drifts out of
// sync later. Every action (edit/delete/add-extra) now takes the
// order sheet's id explicitly rather than reading an implicit global —
// the inline panel can show up to two sheets on screen at once, which
// a single "currentOrderSheetId" can't distinguish between.
function orderSheetLinesEditorHtml(sheet, editable) {
  const total = sheet.lines.reduce((sum, l) => sum + (l.quantity * l.unit_cost), 0);
  // Order Sheet Corrections brief §3+§4 (confirmed Aug 2026) — "show
  // three values instead of a single cost figure" for a flooring
  // line: pre-discount price, the discount rate, and the resulting
  // (already-shown) cost. discount_pct === 0 (a floor-prep/consumable
  // line, e.g. Azura's screed compound) reads as an explicit "No
  // discount" note, in visible contrast to a flooring line's real
  // rate — deliberately distinguished from discount_pct === null (a
  // manually-added extra item, or the m²-fallback path with no
  // product to price a discount against), which shows nothing at all
  // rather than a misleading "No discount" on something that was
  // never a book-price line to begin with.
  const discountNote = (l) => {
    if (l.discount_pct === null || l.discount_pct === undefined) return '';
    if (l.discount_pct === 0) return `<br><span class="muted" style="font-size:10.5px;">Book price, R${l.pre_discount_unit_cost.toFixed(2)} — <b style="color:var(--navy);">No discount</b></span>`;
    return `<br><span class="muted" style="font-size:10.5px;">R${l.pre_discount_unit_cost.toFixed(2)} less ${(l.discount_pct*100).toFixed(0)}% trade discount</span>`;
  };
  const rows = sheet.lines.length ? sheet.lines.map(l => `
    <tr>
      <td>${l.product_name}${l.colour ? `<br><span class="muted" style="font-size:11px;">${l.colour}</span>` : ''}${l.is_extra ? '<br><span class="muted" style="font-size:10px;">(added manually)</span>' : ''}</td>
      <td>${editable ? `<input type="number" step="0.01" value="${l.quantity}" style="width:70px;" onchange="updateOrderSheetLineQty(${sheet.id}, ${l.id}, this.value)">` : l.quantity} ${l.unit}</td>
      <td>R${l.unit_cost.toFixed(2)}${discountNote(l)}</td>
      <td>R${(l.quantity * l.unit_cost).toFixed(2)}</td>
      <td>${editable ? `<button class="delete-btn" onclick="deleteOrderSheetLine(${sheet.id}, ${l.id})">Delete</button>` : ''}</td>
    </tr>`).join('') : '<tr><td colspan="5" class="muted">No line items on this order sheet.</td></tr>';
  return `
      <table>
        <thead><tr><th>Product</th><th>Quantity</th><th>Cost (ex VAT)</th><th>Line total</th><th></th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
      <div class="total-row">Total: R${total.toFixed(2)}</div>
      ${editable ? `
      <h3 style="margin-top:20px;">Add extra item</h3>
      <div class="grid">
        <div class="field"><label>Product/item</label><input id="os_extra_name_${sheet.id}" placeholder="e.g. Extra trowel"></div>
        <div class="field"><label>Quantity</label><input id="os_extra_qty_${sheet.id}" type="number" step="0.01" value="1"></div>
        <div class="field"><label>Unit</label><input id="os_extra_unit_${sheet.id}" placeholder="e.g. units"></div>
        <div class="field"><label>Cost per unit (R, ex VAT)</label><input id="os_extra_cost_${sheet.id}" type="number" step="0.01" value="0"></div>
      </div>
      <button class="primary" onclick="addOrderSheetExtraLine(${sheet.id})">Add item</button>` : ''}`;
}

// Refreshes whichever screen is actually showing this order sheet
// right now (confirmed Aug 2026, brief §4) — the standalone Order
// Sheet Detail screen, or the inline preview panel on Job Detail.
// Re-renders the whole relevant screen (not just the one panel) —
// same "just call the real render function again" pattern already
// used throughout this app (renderOrderDetail() itself after every
// workflow action), simpler and less error-prone than hand-patching
// one row of a table in place.
function refreshOrderSheetContext(orderSheetId) {
  if (landingView === 'orderSheetDetail' && currentOrderSheetId === orderSheetId) {
    renderOrderSheetDetail(document.getElementById('landing'));
  } else if (landingView === 'orderDetail') {
    renderOrderDetail(document.getElementById('landing'));
  }
}

async function renderOrderSheetDetail(el) {
  await renderWithRetry(el, 'Order Sheet', async () => {
  el.innerHTML = `<span class="back-link" onclick="landingView='orders'; renderLanding();">← Back to Order Index</span><div class="card"><p class="muted">Loading...</p></div>`;
  const res = await fetch(`${API}/order-sheets/${currentOrderSheetId}`);
  const sheet = await res.json();
  setPageTitle('Order Sheet ' + sheet.order_number);   // Page Title in Sticky Header brief
  // Editable quantities + extra lines only on a floor_prep-type sheet
  // that hasn't been placed yet (brief §5, plus §4's "placed" status —
  // enforced server-side too (update_order_sheet_line()/
  // add_order_sheet_line(), main.py currently only checks sheet_type;
  // this hides the controls that would otherwise imply an edit still
  // works after the real order has already gone out).
  const editable = sheet.sheet_type === 'floor_prep' && sheet.status !== 'placed';
  el.innerHTML = `
    <span class="back-link" onclick="landingView='orders'; renderLanding();">← Back to Order Index</span>
    <div class="landing-welcome">
      <h1>Order ${sheet.order_number} ${sheet.status === 'placed' ? '<span class="status-badge active-status">Placed</span>' : '<span class="status-badge pending-status">Draft</span>'}</h1>
      <p>To: ${sheet.supplier} — Job ${sheet.job_number || '#'+sheet.quote_id}${sheet.client_name ? ', ' + sheet.client_name : ''}</p>
    </div>
    <div class="card">
      <p class="muted">${sheet.sheet_type === 'floor_prep'
        ? (sheet.status === 'placed' ? 'Floor-prep order — already marked as placed, read-only.' : 'Floor-prep order — quantities can be adjusted, and extra items added below, before this is finalized and sent.')
        : 'Flooring order — reflects this job\'s own line items directly.'}
        Cost prices only, ex VAT — never the client\'s sell price.</p>
      ${orderSheetLinesEditorHtml(sheet, editable)}
      <div style="margin-top:16px; display:flex; gap:10px; flex-wrap:wrap;">
        ${sheet.status !== 'placed' ? `<button class="primary" onclick="finalizeOrderSheet(${sheet.id})">Mark as Placed</button>` : `<span class="muted" style="font-size:12px; align-self:center;">Placed by ${sheet.placed_by || ''}${sheet.placed_at ? ' on ' + new Date(sheet.placed_at).toLocaleDateString('en-ZA') : ''}</span>`}
        ${currentRole() === 'owner' ? `<button class="delete-btn" onclick="deleteOrderSheet(${sheet.id})">Delete order sheet</button>` : ''}
      </div>
    </div>
  `;
  });
}

async function updateOrderSheetLineQty(orderSheetId, lineId, value) {
  const qty = parseFloat(value) || 0;
  await fetch(`${API}/order-sheets/${orderSheetId}/lines/${lineId}`, {method:'PUT', headers:{'Content-Type':'application/json'}, body: JSON.stringify({quantity: qty})});
  refreshOrderSheetContext(orderSheetId);
}

async function deleteOrderSheetLine(orderSheetId, lineId) {
  if (!confirm('Remove this item from the order sheet?')) return;
  await fetch(`${API}/order-sheets/${orderSheetId}/lines/${lineId}`, {method:'DELETE'});
  refreshOrderSheetContext(orderSheetId);
}

async function addOrderSheetExtraLine(orderSheetId) {
  const productName = document.getElementById('os_extra_name_' + orderSheetId).value.trim();
  if (!productName) { alert('Enter a product/item description first.'); return; }
  const body = {
    product_name: productName,
    quantity: parseFloat(document.getElementById('os_extra_qty_' + orderSheetId).value) || 0,
    unit: document.getElementById('os_extra_unit_' + orderSheetId).value,
    unit_cost: parseFloat(document.getElementById('os_extra_cost_' + orderSheetId).value) || 0,
  };
  const res = await fetch(`${API}/order-sheets/${orderSheetId}/lines`, {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)});
  if (!res.ok) { alert('Could not add this item.'); return; }
  refreshOrderSheetContext(orderSheetId);
}

// Order Sheets UX brief §4 (confirmed Aug 2026) — "Executable... mark
// the order as placed." Once placed, generate_order_sheets() no
// longer treats this sheet as blocking a fresh one for the same
// job+supplier+category (see main.py) — a genuine re-order.
async function finalizeOrderSheet(orderSheetId) {
  if (!confirm('Mark this order sheet as placed? This means the order has genuinely been sent to the supplier — quantities can no longer be edited after this.')) return;
  const res = await fetch(`${API}/order-sheets/${orderSheetId}/finalize`, {method: 'POST'});
  if (!res.ok) { const body = await res.json().catch(() => ({})); alert(body.detail || 'Could not mark this order sheet as placed.'); return; }
  refreshOrderSheetContext(orderSheetId);
}

// Order Sheets UX brief §2 (confirmed Aug 2026) — the real gap this
// whole brief exists to close: Burgert had no way to remove the
// O-0001/O-0002 duplicate himself. Same "real procurement action"
// seriousness as generate itself — explicit confirm, Owner-only
// (enforced server-side too, delete_order_sheet() main.py), and
// logged to the AuditLog there.
async function deleteOrderSheet(orderSheetId) {
  if (!confirm('Delete this order sheet? This cannot be undone. Only do this for a genuine mistake or duplicate — not a real order that was already sent.')) return;
  const res = await fetch(`${API}/order-sheets/${orderSheetId}`, {method: 'DELETE'});
  if (!res.ok) { const body = await res.json().catch(() => ({})); alert(body.detail || 'Could not delete this order sheet.'); return; }
  // After a delete, the standalone Order Sheet Detail screen (if that's
  // where this was called from) has nothing left to show -- back to
  // Order Index rather than re-rendering a now-404ing screen. The
  // inline Job Detail panel context re-renders normally (that screen
  // still exists regardless).
  if (landingView === 'orderSheetDetail' && currentOrderSheetId === orderSheetId) {
    landingView = 'orders';
    renderLanding();
  } else {
    renderOrderDetail(document.getElementById('landing'));
  }
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
  // Deposit Amount (confirmed Aug 2026) — blank means "use the
  // percentage-calculated figure", sent as an explicit clear rather
  // than just omitting the param, so the backend can tell "leave it
  // alone" apart from "the user emptied this field on purpose."
  const actualDepositVal = document.getElementById('od_actual_deposit_amount').value;
  if (actualDepositVal !== '') { params.set('actual_deposit_amount', actualDepositVal); }
  else { params.set('clear_actual_deposit_amount', 'true'); }
  const res = await fetch(`${API}/quotes/${currentOrderDetailQuoteId}?${params}`, {method:'PUT'});
  if (!res.ok) { alert('Could not save — check your connection and try again.'); return; }
  // Save confirmation (confirmed Aug 2026, brief §2) — re-renders this
  // SAME screen in place (never navigates away, per the brief's own
  // explicit instruction) so the Deposit/Balance figures and the
  // "Actual" badge reflect what was just saved immediately, then shows
  // the success banner — awaited first so #jobDetailsSaveBanner exists
  // in the freshly-rebuilt DOM before this tries to show it.
  await renderOrderDetail(document.getElementById('landing'));
  showJobDetailsSaveBanner();
}

let jobDetailsSaveBannerTimeout = null;
function showJobDetailsSaveBanner() {
  const banner = document.getElementById('jobDetailsSaveBanner');
  if (!banner) return;
  banner.textContent = `✓ Saved — ${new Date().toLocaleTimeString('en-ZA')}`;
  banner.style.display = 'block';
  clearTimeout(jobDetailsSaveBannerTimeout);
  jobDetailsSaveBannerTimeout = setTimeout(() => { banner.style.display = 'none'; }, 4000);
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
