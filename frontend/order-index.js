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
  // Quote expiry (confirmed Sept 2026) — same quiet suffix treatment as
  // declined: the row still reads as Quoted, because it is, with the
  // reason it has stopped being chased stated next to it rather than
  // left to be inferred from an absent follow-up prompt.
  const expired = (q.expired && !q.declined_at) ? ' <span class="muted" style="font-size:10.5px;">(expired)</span>' : '';
  // On Hold (Job Workflow Design Proposal Phase 1, confirmed Aug 2026)
  // -- an overlay, same reasoning as declined above: workflow_status
  // itself is untouched while on hold, so the badge still shows
  // Accepted/Scheduled underneath, with this appended for visibility
  // at a glance -- never a 5th status value of its own.
  const onHold = q.on_hold_reason ? ' <span style="font-size:10.5px; font-weight:700; color:var(--coral);">⏸ On Hold</span>' : '';
  return `<span class="status-badge" style="background:${meta.bg}; color:${meta.color};">${meta.label}</span>${declined}${expired}${onHold}`;
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
// Exclude Declined Alternative Quotes (confirmed Sept 2026) — a
// declined quote is never deleted (real, permanent record, same as
// every other quote) but must stay out of the main working list by
// default — "what needs my attention" never includes an alternative
// that was never going to happen. This is a genuinely SEPARATE,
// dedicated view (its own card, its own fetch with
// include_declined=true), not folded into the existing status tabs —
// a declined quote's own workflow_status stays "quoted" forever
// (decline_quote() only ever sets declined_at, main.py), so it can't
// be told apart from a real open quote by status alone; mixing it
// into the Quoted tab's own sort/grouping would have re-introduced
// exactly the clutter this brief exists to remove.
let orderIndexShowDeclined = false;
let orderIndexDeclinedQuotes = [];

const WORKFLOW_TABS = ['all', 'quoted', 'accepted', 'scheduled', 'completed'];

// Search debounce (confirmed Aug 2026, Full Real-Browser Walkthrough &
// Audit — real bug: renderOrderIndex()'s own oninput handler fires on
// EVERY keystroke, and does a full re-fetch + full innerHTML rebuild of
// #landing, including the search <input> itself. There's a deliberate
// focus-restore already in place for exactly this (see renderOrderIndex's
// own comment below) but it doesn't reliably win the race against the
// NEXT keystroke arriving before that re-render/re-fetch/refocus cycle
// finishes — confirmed dropping characters even at a genuine ~1
// keystroke/second human typing pace, not just rapid automated typing.
// Same debounce pattern already used for the Quote Builder's live line
// preview (scheduleGenericLinePreview(), quote-builder.js) — waiting
// until typing actually pauses means the input's own DOM node is never
// touched mid-word, so nothing can ever race it; the one re-render/
// refocus that does happen afterwards only has to happen once.
let orderIndexSearchDebounceTimer = null;
function scheduleOrderIndexSearch(value) {
  clearTimeout(orderIndexSearchDebounceTimer);
  orderIndexSearchDebounceTimer = setTimeout(() => renderOrderIndex(document.getElementById('landing'), value), 300);
}

function setOrderIndexTab(tab) {
  orderIndexActiveTab = tab;
  renderOrderIndexTable();
}

// Exclude Declined Alternative Quotes (confirmed Sept 2026) — its own
// small fetch (include_declined=true, and since every declined quote's
// own workflow_status is still "quoted", filtered client-side down to
// just declined_at != null so this card never accidentally shows a
// genuinely open quote), only made the first time it's actually opened
// — not on every Order Index load, since most visits won't need it.
async function toggleOrderIndexDeclined() {
  orderIndexShowDeclined = !orderIndexShowDeclined;
  if (orderIndexShowDeclined && !orderIndexDeclinedQuotes.length) {
    const res = await fetch(`${API}/quotes?include_declined=true&workflow_status=quoted`);
    const all = await res.json();
    orderIndexDeclinedQuotes = all.filter(q => q.declined_at);
  }
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
  orderIndexShowDeclined = false;
  orderIndexDeclinedQuotes = [];
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
  // input element, every time it runs, so without this restore, typing
  // would drop focus entirely the moment this fires. Debounced now
  // (scheduleOrderIndexSearch(), confirmed Aug 2026, Full Real-Browser
  // Walkthrough & Audit) — this used to fire on every single keystroke,
  // so this restore was racing the NEXT keystroke and confirmed losing
  // that race even at normal human typing speed, dropping characters.
  // Debouncing means the input's own DOM node is never touched mid-word
  // — this restore now only ever has to win a race against a keystroke
  // that's already 300ms in the past, not one still arriving.
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
  // Order Index Priority Ordering (confirmed Sept 2026) — "the list
  // should sort... by real operational priority," applied to the "All"
  // view specifically (brief's own words: "the individual status tabs...
  // can keep their own existing internal ordering" — untouched below,
  // still whatever order the backend returned for a single-status tab).
  // Five buckets, confirmed directly with Burgert where the brief's own
  // 3-bucket description (Scheduled / follow-up-needed Quoted / fresh
  // Quoted) didn't say where Accepted or Completed fit:
  //   0. Scheduled — real, booked work actually happening.
  //   1. Accepted — always carries a real attention_priority
  //      (_job_workflow_info(), main.py: "Book installation"/"Confirm
  //      booking"), genuinely active work not yet booked.
  //   2. Quoted, stale (attention_priority set — gone past
  //      QUOTE_STALE_DAYS without a follow-up) — needs action.
  //   3. Quoted, fresh (no attention_priority) — recently sent, nothing
  //      needed yet.
  //   4. Completed — the physical work is done; even one still needing
  //      an invoice sinks to the very bottom of the list, per Burgert's
  //      own explicit call.
  // A stable sort (Array.prototype.sort, guaranteed stable in every
  // browser this app supports) keeps each bucket's own relative order
  // exactly as the backend returned it — no secondary sort invented
  // beyond what the brief actually asked for.
  // Order Index visual grouping (confirmed Sept 2026, "Manual Quoting
  // Categories, Lead Conversion, Order Index Clarity" brief §3) — real
  // section headers, not just colour, so a long list reads as a few
  // distinct stages instead of one wall of rows.
  //
  // Sections follow the brief's own pipeline order (quote -> accept ->
  // install -> paid). NOTE this changes the TOP-LEVEL order from the
  // earlier Priority Ordering decision, which put Scheduled first and
  // Completed last across the whole list; that ordering is preserved
  // exactly WITHIN each section (orderIndexPriorityBucket below is
  // still the secondary sort), which is what this brief asked for.
  //
  // The fourth section is not in the brief but is required for the
  // third one to be true: a completed job that HAS been paid is not
  // "awaiting final payment", and lumping it there would mislabel
  // finished work.
  function orderIndexStage(q) {
    // Expired quotes are grouped with the finished work, not with live
    // quotes awaiting an answer — checked first for the same reason
    // orderStageOf() does it: they are still workflow_status "quoted".
    if (q.expired) return 3;
    if (q.workflow_status === 'quoted') return 0;
    if (q.workflow_status === 'accepted' || q.workflow_status === 'scheduled') return 1;
    if (q.workflow_status === 'completed') return q.final_payment_date ? 3 : 2;
    return 4;
  }
  function orderIndexPriorityBucket(q) {
    if (q.workflow_status === 'scheduled') return 0;
    if (q.workflow_status === 'accepted') return 1;
    // Expired sinks below every live quote but above nothing else —
    // it is not urgent, but it is still a real quote someone may want
    // to re-quote, so it stays visible rather than being filtered out.
    if (q.expired) return 4;
    if (q.workflow_status === 'quoted') return q.attention_priority ? 2 : 3;
    if (q.workflow_status === 'completed') return 5;
    return 6;   // safety fallback — no real workflow_status value reaches this today
  }
  const shown = orderIndexActiveTab === 'all'
    ? [...quotes].sort((a, b) => (orderIndexStage(a) - orderIndexStage(b))
                                 || (orderIndexPriorityBucket(a) - orderIndexPriorityBucket(b)))
    : quotes.filter(q => q.workflow_status === orderIndexActiveTab);

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

  // Headers only on the "All" view — a single-status tab is already one
  // stage by definition, so a lone header above it would be noise.
  const rows = shown.length
    ? buildOrderIndexRowsHtml(shown, isOwner, money, !!searchTerm, orderIndexActiveTab === 'all' ? orderIndexStage : null)
    : `<tr><td colspan="${isOwner ? 8 : 7}" class="muted">No jobs match.</td></tr>`;

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

    ${renderOrderStageTiles(quotes, money)}

    <div class="card">
      <div class="workflow-tabs">
        ${tab('all', 'All', quotes.length)} ${tab('quoted', 'Quoted', counts.quoted)} ${tab('accepted', 'Accepted', counts.accepted)} ${tab('scheduled', 'Scheduled', counts.scheduled)} ${tab('completed', 'Completed', counts.completed)}
      </div>
      <div class="field"><label>Search (customer, job number, or site)</label><input type="text" id="orderSearchInput" value="${searchTerm || ''}" placeholder="Type to search..." oninput="scheduleOrderIndexSearch(this.value)"></div>
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

    <!-- Exclude Declined Alternative Quotes (confirmed Sept 2026,
    Burgert's own words: "the other two aren't lost opportunities...
    They also clutter the Order Index with quotes that will never move
    forward") — a genuinely separate, out-of-the-way card, not mixed
    into the main table/tabs above. Declined quotes are never deleted
    (decline_quote()'s own docstring, main.py) — this is exactly the
    "remain accessible... via a filter" the brief asks for. -->
    <div class="card">
      <div style="display:flex; justify-content:space-between; align-items:center;">
        <h2 style="margin:0;">Declined Quotes</h2>
        <button onclick="toggleOrderIndexDeclined()">${orderIndexShowDeclined ? 'Hide' : 'Show'}</button>
      </div>
      <p class="muted" style="margin-bottom:${orderIndexShowDeclined ? '12px' : '0'};">Alternative quotes that weren't chosen — kept for reference, out of the working list above. Click one for the full reason.</p>
      ${orderIndexShowDeclined ? `
      <div style="overflow-x:auto;">
        <table class="mobile-card-table">
          <thead><tr><th>Job</th><th>Customer</th><th>Value</th><th>Declined</th></tr></thead>
          <tbody>${orderIndexDeclinedQuotes.length ? orderIndexDeclinedQuotes.map(q => `
            <tr style="cursor:pointer;" onclick="openOrderDetailScreen(${q.id})">
              <td class="job-number card-title" data-label="Job">${q.job_number || `#${q.id}`}</td>
              <td data-label="Customer">${orderIndexClientNameHtml(q)}${q.description ? `<br><span class="muted" style="font-size:11px;">${q.description}</span>` : ''}</td>
              <td data-label="Value">${money(q.total_incl_vat)}</td>
              <td data-label="Declined">${new Date(q.declined_at).toLocaleDateString('en-ZA')}</td>
            </tr>`).join('') : '<tr><td colspan="4" class="muted">No declined quotes.</td></tr>'}</tbody>
        </table>
      </div>` : ''}
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

// Order Index: Client Name Visibility (confirmed Sept 2026) — one
// shared renderer for every row shape this table produces (single row,
// group header, child row inside an expanded group) so the name can
// never drift out of sync between them again. .oi-client-name (styles.css)
// carries the actual consistent size/weight/colour; .linked layers the
// same teal+underline "clickable" language every other link on this
// screen already uses, kept because it's a real, meaningful distinction
// (a genuine linked Client record vs. a walk-in/one-off) — not something
// the brief asked to remove, only to stop rendering inconsistently.
// Stage tiles (confirmed Sept 2026) — the one part of the Order Index
// redesign Burgert kept: "I do like the coloured tiles at the top with
// the amounts and stats, maybe just a little smaller." The rest of that
// redesign (two-column layout, right-hand client list, rep/branch/month
// filter row) was reverted at his request — "the way the order index was
// shown before is the best of all so far".
//
// Display only, deliberately: the status tab strip right below them
// already filters this screen and did so before the redesign. Two
// controls doing the same job on one page is the clutter that redesign
// was trying to remove in the first place, so the tiles answer "how much
// is where" and the tabs answer "show me only these".
//
// Five stages, not the four workflow_status values, because two of them
// split one status: `completed` is either awaiting final payment or
// genuinely finished, and a declined quote is finished too. That split
// is the whole reason a Rand figure per tile is worth reading.
const ORDER_STAGES = [
  { key: 'quoted', label: 'Work Quoted', sub: 'Awaiting acceptance' },
  { key: 'accepted', label: 'Accepted', sub: 'Not yet installed' },
  { key: 'installing', label: 'Being Installed', sub: 'In progress' },
  { key: 'awaiting_payment', label: 'Awaiting Payment', sub: 'Installed, unpaid' },
  { key: 'archive', label: 'Closed', sub: 'Paid & expired' },
];
function orderStageOf(q) {
  // declined_at BEFORE workflow_status: a declined quote's own status
  // stays "quoted" forever (decline_quote(), main.py), so without this
  // check a declined quote would count as Work Quoted.
  if (q.declined_at) return 'declined';
  // Expiry, same reasoning (confirmed Sept 2026): an expired quote is
  // still workflow_status "quoted", so without this it would inflate
  // Work Quoted with month-dead quotes — which is the exact clutter
  // expiry exists to clear. q.expired is derived server-side
  // (_job_workflow_info(), main.py), never recomputed here.
  if (q.expired) return 'archive';
  if (q.workflow_status === 'quoted') return 'quoted';
  if (q.workflow_status === 'accepted') return 'accepted';
  if (q.workflow_status === 'scheduled') return 'installing';
  if (q.workflow_status === 'completed') return q.final_payment_date ? 'archive' : 'awaiting_payment';
  return 'archive';
}
function renderOrderStageTiles(quotes, money) {
  // Declined quotes are deliberately NOT counted in any tile. They are
  // fetched separately and only when the Declined card below is opened
  // (toggleOrderIndexDeclined()), so folding them into a tile would
  // make that tile's number jump the moment someone expanded an
  // unrelated card — a figure that changes based on what you clicked is
  // worse than one that never claimed to include them. orderStageOf()
  // returns 'declined' for them, which matches no tile.
  return `<div class="stage-tiles">${ORDER_STAGES.map(st => {
    const inStage = quotes.filter(q => orderStageOf(q) === st.key);
    const value = inStage.reduce((sum, q) => sum + (q.total_incl_vat || 0), 0);
    return `
      <div class="stage-tile stage-${st.key}">
        <div class="stage-tile-label">${st.label}</div>
        <div class="stage-tile-figures">
          <span class="stage-tile-count">${inStage.length}</span>
          <span class="stage-tile-value">${money(value)}</span>
        </div>
        <div class="stage-tile-sub">${st.sub}</div>
      </div>`;
  }).join('')}</div>`;
}

function orderIndexClientNameHtml(q) {
  return q.client_id
    ? `<span class="oi-client-name linked" onclick="event.stopPropagation(); openClientDetail(${q.client_id})" title="View client details">${q.client_name}</span>`
    : `<span class="oi-client-name" title="No linked client record — walk-in/one-off">${q.client_name}</span>`;
}

function orderIndexRowHtml(q, isOwner, money, isChild) {
  return `
    <tr id="oi-row-${q.id}" style="cursor:pointer;${isChild ? ' background:var(--bg,#f5f6f8);' : ''}" onclick="openOrderDetailScreen(${q.id})">
      ${isOwner ? `<td data-label="" onclick="event.stopPropagation();"><input type="checkbox" class="oi-select" value="${q.id}" onchange="toggleOrderSelected(${q.id}, this.checked)"></td>` : ''}
      <td class="job-number card-title" data-label="Job"${isChild ? ' style="padding-left:28px;"' : ''}>${q.job_number || `#${q.id}`}${q.is_test_data ? `<br><span class="muted" style="font-size:10px; color:var(--coral); font-weight:700;" title="Created by a Trusted Tester account — excluded from Business Overview figures">🧪 ${q.test_data_label}</span>` : ''}</td>
      <td data-label="Customer">${orderIndexClientNameHtml(q)}
        ${q.description ? `<br><span class="muted" style="font-size:11px;">${q.description}</span>` : ''}</td>
      <td data-label="Value">${money(q.total_incl_vat)}${(q.manual_override_total_incl_vat != null || q.has_line_override) ? `<br><span class="muted" style="font-size:10px; color:var(--coral); font-weight:700;" title="A line or the total on this job was manually adjusted — see Job Detail / Quote Builder for the reason">✏️ Adjusted</span>` : ''}</td>
      <td data-label="Status">${workflowStatusBadge(q)}</td>
      <td data-label="Install Date">${dateOrDash(q.installation_date)}</td>
      <td data-label="Next Action">${nextActionButton(q) || '<span class="muted">—</span>'}</td>
      <td class="card-actions-cell" data-label="">
        <!-- Order Index Row Overflow (confirmed Aug 2026, Row Overflow
        & Sticky Header Flicker brief §1) — real usage finding: this
        cell had grown to 5 actions (Edit client, Quick View, Flag,
        Duplicate, Delete) plus the 6 data columns before it, wide
        enough to force a horizontal scrollbar with the rightmost
        action cut off on a normal desktop width. Fixed per the
        approved proposal: the two actions someone reaches for WHILE
        actually scanning the list — Quick View (glance at the document
        without leaving) and the Next Action button (its own column,
        unchanged) — stay directly visible/one-tap; the four genuinely
        occasional actions collapse into one ⋮ menu. Same fix on both
        desktop and mobile (a single unified layout, not two different
        treatments) — this also shortens the mobile card view, which
        used to wrap all 5 links across multiple lines per card. -->
        <!-- Quick View (confirmed Aug 2026, third placement of the
        existing Document Preview) — reuses documentPreviewTileHtml()/
        loadDocumentPreview()/editDocumentPreview() exactly as already
        built (shared.js), no new preview component. Works identically
        for standalone rows and rows nested inside an expanded group —
        toggleQuickView() only needs this row's own quote id. -->
        <a href="#" onclick="event.stopPropagation(); toggleQuickView(${q.id}); return false;" style="font-size:12px; margin-right:8px;" title="Preview this quote/invoice without leaving the Order Index">Quick View</a>
        <span class="oi-actions-menu-wrap" style="position:relative; display:inline-block;" onclick="event.stopPropagation();">
          <button onclick="toggleOiActionsMenu(${q.id})" title="More actions" style="padding:4px 9px; font-size:14px; line-height:1;" aria-label="More actions">⋮</button>
          <div id="oi-actions-menu-${q.id}" class="oi-actions-menu" style="display:none; position:absolute; right:0; top:calc(100% + 4px); z-index:20; background:white; border:1px solid var(--border); border-radius:6px; box-shadow:0 4px 10px rgba(0,0,0,0.15); min-width:140px; padding:4px 0;">
            <!-- Client Link Gap fix (confirmed Aug 2026, Order Index ->
            Client Link Gap brief, Gap 1) — the Client Grouping
            addendum's "Edit client" link only ever existed on a
            GROUPED row's header; a client with just one quote had no
            equivalent way to reach their own page from here. Child
            rows inside an expanded group still don't repeat it —
            their own group header, immediately above, already has it. -->
            ${(!isChild && q.client_id) ? `<a href="#" onclick="closeOiActionsMenu(); openClientDetail(${q.client_id}, true); return false;" class="oi-menu-item" title="Edit this client's details">Edit client</a>` : ''}
            <a href="#" onclick="closeOiActionsMenu(); flagRecord('Quote', ${q.id}, '${(q.job_number || 'Quote #' + q.id).replace(/'/g,"\\'")}'); return false;" class="oi-menu-item">🚩 Flag</a>
            <a href="#" onclick="closeOiActionsMenu(); duplicateQuoteFromIndex(${q.id}, '${(q.client_name||'').replace(/'/g,"\\'")}', ${q.client_id || 'null'}); return false;" class="oi-menu-item">Duplicate</a>
            ${isOwner ? `<a href="#" onclick="closeOiActionsMenu(); deleteQuoteFromIndex(${q.id}); return false;" class="oi-menu-item" style="color:var(--coral);">Delete</a>` : ''}
          </div>
        </span>
      </td>
    </tr>`;
}

// Order Index Row Overflow ⋮ menu (confirmed Aug 2026) — at most one
// open at a time, closed on an outside click or after any item inside
// it is chosen. Module-scope state + a single document-level listener,
// same pattern shared.js's own Enter-key-dismiss listener already
// established for exactly this kind of "one listener, works for every
// row, not registered per-row" mechanism.
let openOiActionsMenuId = null;
function toggleOiActionsMenu(quoteId) {
  if (openOiActionsMenuId !== null && openOiActionsMenuId !== quoteId) closeOiActionsMenu();
  const menu = document.getElementById(`oi-actions-menu-${quoteId}`);
  if (!menu) return;
  const opening = menu.style.display === 'none' || !menu.style.display;
  menu.style.display = opening ? 'block' : 'none';
  openOiActionsMenuId = opening ? quoteId : null;
}
function closeOiActionsMenu() {
  if (openOiActionsMenuId === null) return;
  const menu = document.getElementById(`oi-actions-menu-${openOiActionsMenuId}`);
  if (menu) menu.style.display = 'none';
  openOiActionsMenuId = null;
}
document.addEventListener('click', (e) => {
  if (openOiActionsMenuId !== null && !e.target.closest('.oi-actions-menu-wrap')) closeOiActionsMenu();
});

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

const ORDER_INDEX_STAGE_LABELS = [
  'Quoted — awaiting acceptance',
  'Accepted — not yet installed',
  'Installed — awaiting final payment',
  'Completed, expired & closed',
  'Other',
];

function orderIndexStageHeaderHtml(stage, count, isOwner) {
  return `<tr class="oi-stage-header"><td colspan="${isOwner ? 8 : 7}" style="background:var(--navy,#1a2b3c); color:#fff; font-weight:800; padding:8px 10px; letter-spacing:0.02em;">
    ${ORDER_INDEX_STAGE_LABELS[stage]} <span style="opacity:0.75; font-weight:600;">(${count})</span>
  </td></tr>`;
}

// stageOf (confirmed Sept 2026) — null on a single-status tab, where
// every row is the same stage and a header would say nothing.
function buildOrderIndexRowsHtml(shown, isOwner, money, isSearching, stageOf) {
  // Group Multi-Job Client Fix (confirmed Sept 2026) — real risk found
  // by Burgert, not just tidiness: a client with, say, 2 still-Quoted
  // jobs and 1 that had progressed to Scheduled used to group ALL 3
  // together under one collapsed "(3 jobs)" row, showing a "Mixed"
  // status badge — a scheduled job genuinely happening soon could be
  // sitting invisible inside a row that reads as "still just quoted."
  // Grouping is now scoped to workflow_status === "quoted" ONLY — any
  // job that has progressed beyond Quoted always renders as its own
  // standalone row (via the same fall-through orderIndexRowHtml() call
  // every ungrouped row already used), landing in its own correct
  // position under the new priority order below, never hidden inside a
  // collapsed group. Only the remaining still-Quoted jobs for that same
  // client group together — same collapse/expand mechanism as before,
  // unchanged. Grouped-by-first-appearance position in `shown` is
  // preserved, same as before this fix.
  const byClient = {};
  shown.forEach(q => { if (q.client_id && q.workflow_status === 'quoted') (byClient[q.client_id] = byClient[q.client_id] || []).push(q); });
  const groupClientIds = new Set(Object.keys(byClient).filter(cid => byClient[cid].length > 1).map(Number));

  // Counted up front so each header can state its own size — `shown` is
  // already sorted by stage, so the headers themselves are emitted in
  // one pass below as the stage changes.
  const stageCounts = {};
  if (stageOf) shown.forEach(q => { const st = stageOf(q); stageCounts[st] = (stageCounts[st] || 0) + 1; });
  let lastStage = null;
  const stageHeaderFor = (q) => {
    if (!stageOf) return '';
    const st = stageOf(q);
    if (st === lastStage) return '';
    lastStage = st;
    return orderIndexStageHeaderHtml(st, stageCounts[st], isOwner);
  };

  const seenGroup = new Set();
  return shown.map(q => {
    if (!q.client_id || q.workflow_status !== 'quoted' || !groupClientIds.has(q.client_id)) {
      return stageHeaderFor(q) + orderIndexRowHtml(q, isOwner, money, false);
    }
    if (seenGroup.has(q.client_id)) return '';   // absorbed into the group row already emitted below
    const stageHeader = stageHeaderFor(q);
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
    // Status: every job reaching this point is guaranteed workflow_status
    // === "quoted" (Group Multi-Job Client Fix above scopes grouping to
    // that status only now) — no more "Mixed" case is reachable, so the
    // badge is always the plain Quoted one, same as any single row.
    const groupStatusHtml = workflowStatusBadge(groupQuotes[0]);
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
        <td colspan="2" class="card-title" data-label="Client">${expanded ? '▾' : '▸'} ${orderIndexClientNameHtml(q)} <span class="muted" style="font-weight:400;">(${groupQuotes.length} jobs)</span></td>
        <td data-label="Total Value">${money(groupTotal)}</td>
        <td data-label="Status">${groupStatusHtml}</td>
        <td data-label="Next Install">${dateOrDash(nearestInstallDate)}</td>
        <td data-label="Next Action">${headerAction ? `<span class="muted" style="font-weight:400;">${headerAction}</span>` : ''}</td>
        <td class="card-actions-cell" data-label="" onclick="event.stopPropagation();"><a href="#" onclick="openClientDetail(${q.client_id}, true); return false;" style="font-size:12px;" title="Edit this client's details">Edit client</a></td>
      </tr>`;
    const childRows = expanded ? groupQuotes.map(g => orderIndexRowHtml(g, isOwner, money, true)).join('') : '';
    return stageHeader + headerRow + childRows;
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
  // Robust Owner Delete brief (confirmed Aug 2026) -- an explicit SECOND
  // choice, defaulting to NO/Cancel (preserve, current behavior). Real
  // business quotes should keep their archived copy even after the live
  // record is gone; this is only for deliberate mockup/test cleanup
  // (e.g. the "John Cena" quote). Clicking Cancel here, or backing out
  // of the FIRST confirm entirely, both leave the archive untouched.
  const purge = confirm(`Also permanently delete this quote's archived Dropbox copies (Quote/Invoice/Order Sheet PDFs)?\n\nChoose OK only for deliberate mockup/test cleanup -- real business records should normally keep their archived copy.\n\nChoose Cancel to keep the archive (recommended, default).`);
  const purgeParam = purge ? 'purge_dropbox_archive=true' : '';
  const res = await fetch(`${API}/quotes/${quoteId}${purgeParam ? '?' + purgeParam : ''}`, {method:'DELETE'});
  if (res.ok) {
    renderOrderIndex(document.getElementById('landing'), document.getElementById('orderSearchInput').value);
    return;
  }
  const err = await res.json().catch(() => ({}));
  // Force Delete override (confirmed Aug 2026) -- ONLY offered after the
  // normal delete was actually blocked, showing the real reason(s) from
  // the server (never guessed/generic), and requiring a SEPARATE, more
  // explicit confirmation than the routine delete above -- this bypasses
  // a real safety check meant to protect quotes with genuine payment
  // history, so it must never be as easy to trigger as a normal delete.
  // Reserved for known mockup/test data (e.g. "John Cena" reaching
  // Completed with a real deposit/final payment recorded).
  const detail = err.detail || 'Could not delete this order.';
  if (res.status === 400 && currentRole() === 'owner') {
    const wantsForce = confirm(`${detail}\n\nThis quote has real recorded history blocking a normal delete (shown above).\n\nUse FORCE DELETE anyway? Only do this for known mockup/test data -- this permanently removes the quote and everything attached to it (Order Sheets, follow-ups, photos), bypassing the safety check that protects real payment records.\n\nChoose Cancel to leave this quote alone (recommended unless you're sure this is test data).`);
    if (wantsForce) {
      const params = new URLSearchParams({ force: 'true' });
      if (purge) params.set('purge_dropbox_archive', 'true');
      const forceRes = await fetch(`${API}/quotes/${quoteId}?${params}`, {method:'DELETE'});
      if (!forceRes.ok) {
        const forceErr = await forceRes.json().catch(() => ({}));
        alert(forceErr.detail || 'Could not force-delete this order.');
        return;
      }
      renderOrderIndex(document.getElementById('landing'), document.getElementById('orderSearchInput').value);
      return;
    }
    return;
  }
  alert(detail);
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
// Job Detail: Top Tab Bar (confirmed Sept 2026, replacing the old
// bottom accordion — Customer/Quote/Materials/Installation/Photos/
// Financial/Documents were a stacked <details> list requiring
// scrolling to reach lower sections). null means "not chosen yet this
// job view" — renderOrderDetail() falls back to defaultOpenSection(q)
// (unchanged logic, same per-job-stage default the accordion already
// had), then remembers whichever tab was actually picked across any
// re-render of the SAME job (e.g. after Save Job Details), so an
// in-progress edit never gets silently swapped back to a different
// tab underneath the user. Reset to null only when navigating to a
// DIFFERENT job, right here, same one entry point every job-open
// already funnels through.
let jobDetailActiveTab = null;

function openOrderDetailScreen(quoteId) {
  currentOrderDetailQuoteId = quoteId;
  jobDetailActiveTab = null;
  landingView = 'orderDetail';
  renderLanding();
}

function switchJobDetailTab(tabName) {
  jobDetailActiveTab = tabName;
  document.querySelectorAll('.jd-tab-panel').forEach(panel => {
    panel.style.display = panel.dataset.tab === tabName ? '' : 'none';
  });
  document.querySelectorAll('.jd-tabs button').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.tab === tabName);
  });
}

// Independent Status Tiles (confirmed Aug 2026, approved proposal —
// refines the just-shipped Job Control Panel) — replaces the sequential
// step-strip map (renderStepMapHtml(), retired) with three independent
// tiles: Materials, Booking, Money. Real problem this fixes: the old
// map was a locked sequence, but real jobs don't respect one — a job
// can be paid in full before installation, or built from stock on hand
// and never generate a real Order Sheet at all. Each tile below reads
// its own state independently; none is gated behind another being done
// first. Sign-off was proposed as a 4th tile and explicitly deferred
// (Decision Q1, approved 31 Aug 2026) — only these three ship here.
//
// Every state below is read from a field that already existed before
// this brief, except materials_not_needed and the deposit_pct check
// (Decisions Q2/Q3, both newly real, per-job fields as of this brief —
// see models.py/main.py). Renders nothing for a quote that isn't a job
// yet, or was declined (job_steps is [] in both cases, from the
// server) — same gate the old step map used.
function renderStatusTilesHtml(q, jobSteps) {
  if (!jobSteps || !jobSteps.length) return '';

  // Job Dashboard Tile Treatment (confirmed Aug 2026, approved proposal
  // — visual refinement only, same fields/logic as before) — each tile
  // is now a headline + a plain-language sub-line, rather than one
  // emoji-prefixed line; colour + a small dot carry the status signal
  // instead, matching how the primary status strip already keeps
  // colour separate from wording.

  // Materials — reuses the exact same "does this job have a
  // procurement step at all" signal renderMaterialsSectionHtml()
  // already uses, so the two can never disagree about "not applicable".
  const procurementStep = jobSteps.find(st => st.id === 'procurement');
  let materialsTile;
  if (!procurementStep) {
    materialsTile = { cls: 'na', text: 'Not applicable', sub: 'No flooring/floor-prep on this job' };
  } else if (q.materials_not_needed) {
    materialsTile = { cls: 'done', text: 'Not needed', sub: 'Using stock already on hand' };
  } else if (q.ready_for_installation) {
    materialsTile = { cls: 'done', text: 'Received', sub: 'On site, ready to install' };
  } else if (q.materials_ordered) {
    materialsTile = { cls: 'progress', text: 'Ordered', sub: 'Awaiting delivery' };
  } else {
    materialsTile = { cls: 'progress', text: 'Not yet ordered', sub: 'Place the Order Sheet(s) below' };
  }

  // Booking — the real date joins the sub-line when there is one,
  // same "prominent labelling" the brief asked for, still built purely
  // from installation_date/installation_confirmed_date.
  const installDateShort = q.installation_date ? new Date(q.installation_date + 'T00:00:00').toLocaleDateString('en-ZA', {day:'numeric', month:'short'}) : null;
  let bookingTile;
  if (q.installation_confirmed_date) {
    bookingTile = { cls: 'done', text: 'Confirmed', sub: installDateShort || 'Date confirmed' };
  } else if (q.installation_date) {
    bookingTile = { cls: 'progress', text: 'Date proposed', sub: `${installDateShort} — not yet confirmed` };
  } else {
    bookingTile = { cls: 'progress', text: 'Not yet', sub: 'No date set' };
  }

  // Money — final_payment_date is the same "fully paid" signal the
  // primary status strip's own 🟢 "closed out" case already uses;
  // deposit_pct === 0 is the real, tolerated "no deposit required"
  // shape confirmed against _quote_totals() during investigation, now
  // actually settable per job (Decision Q3) rather than only via a
  // global Business Settings change.
  let moneyTile;
  if (q.final_payment_date) {
    moneyTile = { cls: 'done', text: 'Paid in full', sub: 'Nothing outstanding' };
  } else if (q.deposit_paid_date) {
    moneyTile = { cls: 'progress', text: 'Deposit received', sub: 'Balance due on completion' };
  } else if (q.deposit_pct === 0 && q.actual_deposit_amount == null) {
    moneyTile = { cls: 'progress', text: 'No deposit required', sub: 'Balance due on completion' };
  } else {
    moneyTile = { cls: 'progress', text: 'Awaiting deposit', sub: 'Nothing recorded yet' };
  }

  const tile = (label, t) => `
    <div class="status-tile ${t.cls}">
      <div class="status-tile-head"><span class="status-tile-label">${label}</span><span class="status-tile-dot"></span></div>
      <div class="status-tile-state">${t.text}</div>
      <div class="status-tile-sub">${t.sub}</div>
    </div>`;

  return `<div class="status-tiles">${tile('Materials', materialsTile)}${tile('Booking', bookingTile)}${tile('Money', moneyTile)}</div>`;
}

// Materials section (Job Control Panel §6, revised Sept 2026 — Materials
// Section Should Show the Actual Order Sheets, approved proposal) — the
// real Order Sheet list now lives here directly: preview + the standard
// View/Edit/Print/Save/Mail action bar (documentPreviewTileHtml(),
// shared.js — the exact same component Documents used to render this
// through, moved, not duplicated), plus one-click Mark as Placed/Delete
// (finalizeOrderSheet()/deleteOrderSheet() below — both already tolerant
// of being called from this inline context, unchanged). Quantity-line
// editing and "Add extra item" deliberately stay on the standalone Order
// Sheet Detail screen (via Edit above) — real form state, not a glance-
// and-click action, so folding it in here would clutter this section
// rather than help it. Documents (below) now holds only the Dropbox
// Archive and Job Card — its own, non-overlapping purpose once this
// moved out. Same underlying data (data.job_steps' own procurement step/
// tiles, _job_steps() main.py) as before — nothing re-derived, nothing
// new fetched.
function renderMaterialsSectionHtml(jobSteps, q) {
  const procurementStep = (jobSteps || []).find(st => st.id === 'procurement');
  if (!procurementStep) return `<p class="muted" style="margin:0;">No flooring/floor-prep lines on this job — nothing to order.</p>`;
  // materials_not_needed (Independent Status Tiles, Decision Q2,
  // confirmed Aug 2026) — a job built from stock on hand. Replaces the
  // whole tile list/Generate button with a settled statement plus an
  // Undo, same overlay pattern On Hold already uses elsewhere on this
  // screen — never silently hides the real Order Sheet state if there
  // happens to already be one (a job could genuinely have SOME
  // materials ordered and use stock for the rest; toggling this back
  // off restores the normal view exactly as it was).
  if (q.materials_not_needed) {
    return `
      <p style="margin:0; color:var(--teal); font-weight:700;">✓ Not needed — using materials already on hand</p>
      <a href="#" onclick="setMaterialsNotNeeded(${q.id}, false); return false;" style="font-size:12px; margin-top:6px; display:inline-block;">Undo — this job does need ordering</a>`;
  }
  const tiles = procurementStep.tiles.map(t => {
    const label = t.sheet_type === 'floor_prep' ? 'Flooring + Floor Prep' : 'Flooring';
    const previewId = 'dp_ordersheet_tile_' + t.id;
    const isPlaced = t.status === 'placed';
    // Quantity + colour (confirmed Sept 2026) — read straight off
    // t.quantity_summary/t.colour_summary, computed server-side
    // (_order_sheet_quantity_colour_summary(), main.py) directly from
    // this sheet's own OrderSheetLine rows — the exact data the Order
    // Sheet document itself is built from, never a separate value, and
    // scoped to this one sheet's id so a job with two sheets/colours
    // can never bleed one's values into the other's line.
    const orderedInfo = [t.quantity_summary, t.colour_summary].filter(Boolean).join(' — ');
    return `
      <div style="flex:1 1 260px; min-width:220px; max-width:340px; border:1px solid var(--border); border-radius:8px; padding:9px 12px;">
        <p style="margin:0 0 2px; font-size:12.5px; font-weight:600;">${label} <span class="muted" style="font-weight:400;">(${t.supplier})</span> <span style="float:right; font-weight:700; color:${isPlaced ? 'var(--teal)' : 'var(--coral)'};">${isPlaced ? '✓ Ordered' : 'Needs ordering'}</span></p>
        ${orderedInfo ? `<p class="muted" style="margin:0 0 6px; font-size:11.5px;">${orderedInfo}</p>` : ''}
        ${documentPreviewTileHtml(previewId, t.id, 'ordersheet')}
        <div style="display:flex; gap:6px; flex-wrap:wrap; margin-top:6px;">
          ${!isPlaced ? `<button onclick="event.stopPropagation(); finalizeOrderSheet(${t.id})" style="font-size:12px; padding:4px 10px;">Mark as Placed</button>` : ''}
          ${currentRole() === 'owner' ? `<button class="delete-btn" onclick="event.stopPropagation(); deleteOrderSheet(${t.id})" style="font-size:12px; padding:4px 10px;">Delete</button>` : ''}
        </div>
      </div>`;
  }).join('');
  return `
    <div id="procurementTilesBlock">
      <div id="orderSheetsResultBanner" style="display:none; background:#dcf5e6; color:#1a7a3e; border:2px solid #1a7a3e; border-radius:8px; padding:10px 14px; margin-bottom:10px; font-weight:700; font-size:13.5px;"></div>
      ${tiles ? `<div style="display:flex; gap:8px; flex-wrap:wrap; margin-bottom:10px;">${tiles}</div>` : '<p class="muted" style="font-size:12.5px; margin:0;">No Order Sheet generated yet.</p>'}
      <button onclick="generateOrderSheetsForQuote(${q.id})" style="margin-top:4px; font-size:12.5px; padding:6px 12px;">${tiles ? 'Generate another Order Sheet' : 'Generate Order Sheet(s)'}</button>
      ${!tiles ? `<a href="#" onclick="setMaterialsNotNeeded(${q.id}, true); return false;" style="font-size:12px; margin-top:8px; display:inline-block;">Using materials already on hand — mark as not needed</a>` : ''}
    </div>`;
}

// setMaterialsNotNeeded (Independent Status Tiles, Decision Q2,
// confirmed Aug 2026) — same one-click, immediate-re-render pattern as
// setMaterialsReceived() below, reusing the identical PUT .../materials
// endpoint (main.py), just the new field on it.
async function setMaterialsNotNeeded(quoteId, notNeeded) {
  await fetch(`${API}/quotes/${quoteId}/materials?materials_not_needed=${notNeeded}`, {method: 'PUT'});
  renderOrderDetail(document.getElementById('landing'));
}

// Calendar: Multiple Work Days Per Job (confirmed Sept 2026, approved
// proposal) — a job's EXTRA on-site days (screed before install, etc.),
// listed right under the main Installation date field it's genuinely
// separate from. Same tentative-vs-confirmed shape the Calendar itself
// now understands (calendar.js) — a day is "✓ Confirmed" only once its
// own confirmed_date matches its own work_date, never inferred from the
// main job's installation_confirmed_date.
const JOB_WORK_DAY_LABEL = { screed: 'Screed', installation: 'Installation', other: 'Other' };
function renderJobWorkDaysHtml(workDays, quoteId) {
  const rows = (workDays || []).map(wd => {
    const isConfirmed = !!(wd.confirmed_date && wd.confirmed_date === wd.work_date);
    return `
      <div style="display:flex; align-items:center; gap:8px; padding:6px 0; border-bottom:1px solid var(--border); flex-wrap:wrap;">
        <span style="font-size:12.5px; font-weight:600; min-width:80px;">${JOB_WORK_DAY_LABEL[wd.day_type] || 'Other'}</span>
        <input type="date" id="wd_date_${wd.id}" value="${wd.work_date}" onchange="updateJobWorkDay(${quoteId}, ${wd.id}, this.value)" style="max-width:150px;">
        ${isConfirmed
          ? '<span style="color:var(--teal); font-weight:700; font-size:12px;">✓ Confirmed</span>'
          : `<button onclick="confirmJobWorkDay(${quoteId}, ${wd.id})" style="font-size:12px; padding:4px 10px;">Confirm</button>`}
        <button class="delete-btn" onclick="deleteJobWorkDay(${quoteId}, ${wd.id})" style="font-size:12px; padding:4px 10px; margin-left:auto;">Delete</button>
      </div>`;
  }).join('');
  return `
    <div style="margin-top:16px; padding-top:12px; border-top:1px solid var(--border);">
      <label style="font-weight:600; color:var(--navy); font-size:13px;">Extra work days <span class="adj">(e.g. a screed day separate from the install day — the main Installation date above is unaffected)</span></label>
      <div id="workDaysList_${quoteId}" style="margin-top:6px;">
        ${rows || '<p class="muted" style="font-size:12.5px; margin:4px 0;">No extra work days on this job.</p>'}
      </div>
      <div style="display:flex; gap:8px; align-items:flex-end; margin-top:10px; flex-wrap:wrap;">
        <div class="field" style="margin:0;"><label style="font-size:11.5px;">Type</label>
          <select id="new_work_day_type_${quoteId}">
            <option value="screed">Screed</option>
            <option value="installation">Installation</option>
            <option value="other">Other</option>
          </select>
        </div>
        <div class="field" style="margin:0;"><label style="font-size:11.5px;">Date</label><input type="date" id="new_work_day_date_${quoteId}"></div>
        <button onclick="addJobWorkDay(${quoteId})" style="font-size:12.5px; padding:6px 12px;">Add work day</button>
      </div>
    </div>`;
}

async function addJobWorkDay(quoteId) {
  const dateVal = document.getElementById(`new_work_day_date_${quoteId}`).value;
  if (!dateVal) { alert('Pick a date first.'); return; }
  const dayType = document.getElementById(`new_work_day_type_${quoteId}`).value;
  const res = await fetch(`${API}/quotes/${quoteId}/work-days`, {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({day_type: dayType, work_date: dateVal}),
  });
  if (!res.ok) { const body = await res.json().catch(() => ({})); alert(body.detail || 'Could not add this work day.'); return; }
  renderOrderDetail(document.getElementById('landing'));
}

async function updateJobWorkDay(quoteId, workDayId, dateVal) {
  const res = await fetch(`${API}/quotes/${quoteId}/work-days/${workDayId}?work_date=${dateVal}`, {method: 'PUT'});
  if (!res.ok) { alert('Could not update this work day.'); return; }
  renderOrderDetail(document.getElementById('landing'));
}

async function confirmJobWorkDay(quoteId, workDayId) {
  const dateVal = document.getElementById(`wd_date_${workDayId}`).value;
  const res = await fetch(`${API}/quotes/${quoteId}/work-days/${workDayId}/confirm?work_date=${dateVal}`, {method: 'PUT'});
  if (!res.ok) { const body = await res.json().catch(() => ({})); alert(body.detail || 'Could not confirm this work day.'); return; }
  renderOrderDetail(document.getElementById('landing'));
}

async function deleteJobWorkDay(quoteId, workDayId) {
  if (!confirm('Delete this extra work day? This cannot be undone.')) return;
  const res = await fetch(`${API}/quotes/${quoteId}/work-days/${workDayId}`, {method: 'DELETE'});
  if (!res.ok) { alert('Could not delete this work day.'); return; }
  renderOrderDetail(document.getElementById('landing'));
}

// Primary status strip (Job Control Panel, approved proposal §5) — the
// one 🟢/🟡/🔴 line meant to be the very first thing read on this
// page, ahead of the (now-demoted) step map and every collapsed
// section below it. Reads ONLY fields this page already receives —
// q.attention_priority/attention_label/next_action, the exact same
// _job_workflow_info() engine (main.py) that already drives the Order
// Index's own Needs Attention list — plus q.materials_ordered/
// ready_for_installation/invoice_sent_date/final_payment_date for the
// two genuinely new 🟢 cases. Declined/On Hold are left to
// renderWorkflowActionsHtml()'s own existing panel, which already says
// everything needed for those two and is about to render directly
// below this.
//
// The two 🟢 branches are presentation-only, per Decision Q2 of the
// approved proposal: _job_workflow_info() itself is untouched, and the
// Order Index's Needs Attention list keeps sorting/showing exactly
// what it always has — a job in either of these states was already
// invisible to that list before today, simply with no colour of its
// own here yet. No supplier-promised delivery date exists anywhere in
// Bolton's data (checked directly against models.py — only
// OrderSheet.placed_at, which this endpoint doesn't currently return),
// so per Decision Q1 the "awaiting delivery" message stays honest
// about what Bolton actually knows rather than naming a date — no
// endpoint change needed to add one.
// workflow is data.workflow from GET /quotes/{id} — get_quote() nests
// _job_workflow_info()'s own dict there (main.py, "workflow": ...),
// a genuinely different shape from list_quotes() (Order Index), which
// flattens the identical dict directly onto each row instead. Real gap
// caught during disposable verification, not assumed from the Order
// Index's own q.attention_priority reads: this screen has to read
// data.workflow.attention_priority, not q.attention_priority.
function jobControlPanelStatusHtml(q, workflow) {
  if (q.declined_at || q.on_hold_reason) return '';
  const DOT = { critical: '🔴', warning: '🟡', notice: '🟡' };
  const CLASS = { critical: 'crit', warning: 'warn', notice: 'warn' };
  const strip = (cls, dot, text, sub) => `
    <div class="control-panel-status ${cls}">
      <span class="cp-dot">${dot}</span>
      <div><div class="cp-text">${text}</div><div class="cp-sub">${sub}</div></div>
    </div>`;
  if (workflow && workflow.attention_priority) {
    // Installation date next to "Prepare job" (confirmed Sept 2026) —
    // real gap: this card said "Upcoming" with no actual date. Reads
    // installation_date directly — the exact same field the
    // Installation Calendar reads/writes (calendar.js) — never a
    // separate value, so the two can never disagree about when a job
    // is scheduled. "Upcoming" is the one attention_label unique to
    // this branch (_job_workflow_info(), main.py: ws=="scheduled" and
    // installation_date is tomorrow) — every other label stays exactly
    // as it was.
    let sub = workflow.attention_label;
    if (sub === 'Upcoming' && q.installation_date) {
      sub = `Upcoming — ${calFormatDate(q.installation_date)}`;
    }
    return strip(CLASS[workflow.attention_priority], DOT[workflow.attention_priority], workflow.next_action, sub);
  }
  // No attention_priority set — _job_workflow_info() only omits it for
  // a genuinely healthy wait; decide which honest sentence applies from
  // the same fields already on this page.
  //
  // Real correction made here, caught by testing against the actual
  // running engine rather than trusting the approved proposal's own
  // §5 table: that table listed "scheduled, materials ordered, not yet
  // received" as a second silent/blank case needing a new 🟢 — checked
  // directly against a real job moved through this exact state and
  // _job_workflow_info() already returns attention_priority="warning"
  // ("Confirm receipt") for it, not blank. Arguably the more correct
  // read anyway (confirming receipt is a genuine, if unhurried, click
  // Burgert still owes), so no override is added for it — only the
  // one case actually confirmed blank below.
  // Real gap caught during Independent Status Tiles verification, not
  // present before today's build: "scheduled, materials ordered/not-
  // needed AND received, nothing else blocking" also returns no
  // attention_priority (_job_workflow_info()'s own final `else` branch
  // for "scheduled" never sets one) — previously rare enough to not
  // get noticed, now reached directly the moment a job is marked
  // materials_not_needed, which would otherwise leave this strip
  // completely blank on a job that's actually ready to go.
  if (q.workflow_status === 'scheduled' && !(workflow && workflow.attention_priority)) {
    return strip('ok', '🟢', 'Ready to complete installation', 'Materials sorted — nothing else needed before marking this job complete.');
  }
  if (q.workflow_status === 'completed' && q.invoice_sent_date && !q.final_payment_date) {
    return strip('ok', '🟢', `Invoiced ${new Date(q.invoice_sent_date).toLocaleDateString('en-ZA')} — awaiting payment`, 'Normal — nothing to do until it comes in.');
  }
  if (q.workflow_status === 'completed' && q.final_payment_date) {
    return strip('ok', '🟢', 'Job closed out', 'Invoiced and paid.');
  }
  if (q.workflow_status === 'quoted') {
    return strip('ok', '🟢', 'Waiting on customer decision', 'Sent — no response needed from you yet.');
  }
  return '';
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
    // decline_reason (confirmed Aug 2026, Master Workflow proposal §05)
    // — read back from AuditLog by get_quote(), null for anything
    // declined before this fix shipped (no reason was ever captured for
    // those, correctly shown as such rather than guessed at).
    const reasonHtml = q.decline_reason ? ` — ${(q.decline_reason||'').replace(/</g,'&lt;')}` : ' — no reason recorded';
    return `<p class="muted" style="margin:0;">Declined ${new Date(q.declined_at).toLocaleDateString('en-ZA')}${reasonHtml}</p>`;
  }
  // On Hold (Job Workflow Design Proposal Phase 1, confirmed Aug 2026,
  // §7) -- replaces the whole panel, same pattern as declined_at above:
  // the job's step progress freezes exactly where it was (nothing about
  // workflow_status/installation_date/materials-ordered state changes
  // server-side while on hold), Resume below picks back up right there.
  if (q.on_hold_reason) {
    return `
      <div style="background:var(--coral-bg, #fdece7); border:1px solid var(--coral); border-radius:8px; padding:12px 14px;">
        <p style="margin:0; font-weight:700; color:var(--coral);">⏸ On Hold — ${(q.on_hold_reason||'').replace(/</g,'&lt;')}</p>
        <p class="muted" style="margin:6px 0 0; font-size:12px;">Since ${new Date(q.on_hold_at).toLocaleDateString('en-ZA')}. Nothing about this job's progress has changed — Resume picks up exactly where it was.</p>
      </div>
      <button class="primary" onclick="resumeJobAction(${q.id})" style="margin-top:10px;">Resume Job</button>`;
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
      <p class="muted" style="margin-top:8px;">Confirming a date is what moves this job to Scheduled automatically.</p>
      ${holdButtonHtml(q.id)}`;
  }
  if (q.workflow_status === 'scheduled') {
    // ready_for_installation means, precisely (confirmed directly): the
    // flooring/blinds have been delivered and stock is physically on
    // hand — ready to install from that moment. Always a manual
    // confirmation button, never a passive checkbox — Bolton has no
    // physical stock-on-hand tracking to infer it from, so this has to
    // be a deliberate click, same reasoning every other workflow
    // transition on this screen is a named action, not a raw field.
    // materials_not_needed (Independent Status Tiles, Decision Q2,
    // confirmed Aug 2026) — real inconsistency caught during disposable
    // verification, not shipped: this panel used to keep saying
    // "Materials not yet ordered — place the Order Sheet(s) below" even
    // once the Materials tile correctly said "Not needed," directly
    // contradicting it on the same screen. Both lines below now read
    // the same field the tile does, so the two can never disagree.
    const readyHtml = q.materials_not_needed
      ? `<span class="muted">Nothing to receive — using stock already on hand.</span>`
      : q.ready_for_installation
      ? `<span style="color:var(--teal); font-weight:700;">✓ Materials received</span> <a href="#" onclick="setMaterialsReceived(${q.id}, false); return false;" style="font-size:12px; margin-left:8px;">Undo</a>`
      : `<button onclick="setMaterialsReceived(${q.id}, true)">Mark Materials Received</button>`;
    // Materials ordered (Job Workflow Design Proposal Phase 1, confirmed
    // Aug 2026) -- REAL BUG FIXED: this used to be a manual checkbox
    // with zero connection to whether an Order Sheet was actually
    // placed. Now a read-only, server-derived status line -- true once
    // every Order Sheet this job produced has status "placed"
    // (_materials_ordered_for_quote(), main.py). No control to click
    // here any more; place the real Order Sheet(s) below to change it.
    const materialsHtml = q.materials_not_needed
      ? `<span style="color:var(--teal); font-weight:700;">✓ Not needed — using stock on hand</span>`
      : q.materials_ordered
      ? `<span style="color:var(--teal); font-weight:700;">✓ Materials ordered</span>`
      : `<span class="muted">Materials not yet ordered — place the Order Sheet(s) below</span>`;
    return `
      <div class="field"><label style="font-weight:600; color:var(--navy);">Materials</label><div>${materialsHtml}</div></div>
      <div class="field" style="margin-top:8px;">${readyHtml}</div>
      <div class="field" style="margin-top:10px; max-width:260px;"><label>Installer / team</label><input id="wf_installer" value="${(q.installer_team||'').replace(/"/g,'&quot;')}" onchange="saveInstallerTeam(${q.id})" placeholder="e.g. Ryno + 1"></div>
      <button class="primary" onclick="completeQuoteAction(${q.id})" style="margin-top:10px;">Mark Installation Complete</button>
      ${holdButtonHtml(q.id)}`;
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

// Which of the six Job Control Panel sections opens by default
// (approved proposal mockup — "available, not competing for attention
// by default," but the one section actually relevant to the current
// stage stays a helpful exception, same reasoning the mockup itself
// showed Materials open for a job mid-delivery). Purely a display
// default — every section stays independently openable regardless.
function defaultOpenSection(q) {
  if (q.declined_at || q.on_hold_reason) return null;
  if (q.workflow_status === 'quoted') return 'quote';
  if (q.workflow_status === 'accepted') return 'installation';
  if (q.workflow_status === 'scheduled') return q.ready_for_installation ? 'installation' : 'materials';
  if (q.workflow_status === 'completed') return q.invoice_sent_date ? 'financial' : 'quote';
  return null;
}

function holdButtonHtml(quoteId) {
  return `<a href="#" onclick="holdJobAction(${quoteId}); return false;" style="display:inline-block; margin-top:10px; font-size:12px; color:var(--ink-faint, #8A93A0);">Put job on hold…</a>`;
}

async function holdJobAction(quoteId) {
  const reason = prompt('Why is this job going on hold? (e.g. "Supplier delay", "Customer postponed")');
  if (!reason || !reason.trim()) return;
  const res = await fetch(`${API}/quotes/${quoteId}/hold`, {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ reason: reason.trim() }),
  });
  if (!res.ok) { const body = await res.json().catch(() => ({})); alert(body.detail || 'Could not put this job on hold.'); return; }
  openOrderDetailScreen(quoteId);
}

async function resumeJobAction(quoteId) {
  if (!confirm('Resume this job? It picks back up exactly where it was before the hold.')) return;
  const res = await fetch(`${API}/quotes/${quoteId}/resume`, {method: 'POST'});
  if (!res.ok) { const body = await res.json().catch(() => ({})); alert(body.detail || 'Could not resume this job.'); return; }
  openOrderDetailScreen(quoteId);
}

// Compact payment-status strip (confirmed Aug 2026, JobDetail: Needs
// Attention Bug, Conditional Invoice Preview, Order Sheet Previews
// brief §2) — deposit amount, date paid, method if recorded, balance
// due. Sits right alongside the Quote Preview so payment status is
// visible at this stage without needing a second document preview to
// convey it — reuses the exact same figures the Job Details card
// (main column) already shows (data.deposit_amount/balance_amount from
// _quote_totals(), main.py; q.deposit_paid_date/deposit_payment_method
// straight off the quote), never a second, separately-computed copy.
function paymentStatusStripHtml(q, data) {
  const depositStatus = q.deposit_paid_date
    ? `Paid ${new Date(q.deposit_paid_date).toLocaleDateString('en-ZA')}${q.deposit_payment_method ? ' via ' + q.deposit_payment_method : ''}`
    : 'Not yet paid';
  return `
    <div style="background:var(--bg,#f5f6f8); border-radius:8px; padding:10px 12px; margin-bottom:10px; font-size:12.5px;">
      <div style="display:flex; justify-content:space-between; gap:10px;"><span class="muted">Deposit</span><b>R${data.deposit_amount.toFixed(2)}</b></div>
      <div class="muted" style="font-size:11px; margin:2px 0 6px;">${depositStatus}</div>
      <div style="display:flex; justify-content:space-between; gap:10px; padding-top:6px; border-top:1px solid var(--border);"><span class="muted">Balance due</span><b>R${data.balance_amount.toFixed(2)}</b></div>
    </div>`;
}

async function renderOrderDetail(el) {
  await renderWithRetry(el, 'Job Detail', async () => {
  el.innerHTML = `<span class="back-link" onclick="landingView='orders'; renderLanding();">← Back to Order Index</span><div class="card"><p class="muted">Loading...</p></div>`;
  // Job Workflow Design Proposal Phase 3 (confirmed Aug 2026) -- the
  // separate GET .../order-sheets fetch this screen used to make is
  // gone: Order Sheets are now surfaced entirely via the compact
  // procurement tiles inside data.job_steps (Phase 2), so a second
  // round trip for the raw list is no longer needed here at all.
  const res = await fetch(`${API}/quotes/${currentOrderDetailQuoteId}?role=${currentRole()}`);
  const data = await res.json();
  const q = data.quote;
  // Job Workflow Design Proposal Phase 1 (confirmed Aug 2026) --
  // materials_ordered is now server-derived from real OrderSheet
  // status (main.py), returned as a sibling of `quote`, not a field
  // on it any more -- overwritten here so every existing q.materials_
  // ordered read below picks up the fresh derived value, never the
  // stale DB column the old manual checkbox used to write.
  q.materials_ordered = data.materials_ordered;
  // Decline Quote reason (confirmed Aug 2026, Master Workflow proposal
  // §05) — same sibling-of-`quote` pattern as materials_ordered just
  // above; undefined (falsy) for a quote that was never declined.
  q.decline_reason = data.decline_reason;
  // Page Title in Sticky Header brief -- mirrors this same screen's own
  // <h1> formula below exactly, so the two never say something different.
  setPageTitle(`${q.job_number || 'Quote #' + q.id}${q.description ? ' — ' + q.description : ''}`);

  // Job Detail: Top Tab Bar (confirmed Sept 2026) — same per-job-stage
  // default the old accordion's defaultOpenSection() already computed,
  // now just driving which single tab is shown instead of which
  // <details> starts open. jobDetailActiveTab (module-level) lets a
  // re-render of this SAME job (e.g. right after Save Job Details)
  // keep whatever tab the user was actually on, rather than recomputing
  // the stage-based default every time.
  const activeTab = jobDetailActiveTab || defaultOpenSection(q) || 'customer';
  jobDetailActiveTab = activeTab;
  const jdTab = (name, label) => `<button type="button" class="${activeTab === name ? 'active' : ''}" data-tab="${name}" onclick="switchJobDetailTab('${name}')">${label}</button>`;

  // Document Preview, placement 1b (confirmed Aug 2026, Client Page &
  // Quote Detail: Document Preview + Inline Edit brief) — right-hand
  // panel alongside Workflow + Job Details, roughly spanning their
  // combined height. Same documentPreviewTileHtml() component as
  // placement 1a (client Order History) — one template, two placements,
  // per the brief's own explicit instruction. Edit lives in the
  // standard action bar now (Document Action Bar brief, confirmed Aug
  // 2026), same openQuoteFromIndex() the "Open in Quote Builder (line
  // items)" button below already uses — not a duplicate entry point.
  el.innerHTML = `
    <span class="back-link" onclick="landingView='orders'; renderLanding();">← Back to Order Index</span>
    <div class="landing-welcome">
      <h1>${q.job_number || 'Quote #' + q.id}${q.description ? ' — ' + q.description : ''}</h1>
      <!-- Client Name Prominence (confirmed Sept 2026) — real gap: the
      client is, in practice, the single most important piece of
      context on this page, but was rendered inside the generic
      .landing-welcome p rule (13px, muted grey) every other screen's
      own small subtitle text also uses — changing that shared rule
      would have affected every other screen's subtitle too. A
      dedicated class instead, scoped to just this line. -->
      <p class="job-detail-client-name">${q.client_name} &nbsp; ${workflowStatusBadge(q)}</p>
    </div>

    <!-- Job Control Panel (confirmed Aug 2026, approved proposal) —
    single-column, top-to-bottom: primary status + action first, the
    step map demoted right under it, then every detail field grouped
    into named, collapsed sections (Customer/Quote/Materials/
    Installation/Financial/Documents) rather than one long always-open
    "Job Details" card. Deliberately NOT the two-column
    .job-detail-layout grid this screen used before — that layout gave
    the (now-collapsed) document previews equal, permanent billing
    alongside the primary actions, which is exactly the "everything
    competing for attention" problem the brief named. Every field/ID/
    handler below is unchanged from before this brief; only the
    grouping and ordering moved. -->
    <div class="card">
      ${jobControlPanelStatusHtml(q, data.workflow)}
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

      <!-- Follow-Ups (Decision Q4, approved proposal) — kept visible
      and uncollapsed right alongside the primary status/action, not
      folded into any of the six named sections below: not a clean fit
      for any one of them (a follow-up can be about payment, a delivery
      delay, or just a check-in call), and it's closer in spirit to
      "what's already happened"/"what needs me" than a buried detail
      field. Same fields/handlers as before (fu_date/fu_notes/
      followUpList, logFollowUp()/loadFollowUps()), just relocated. -->
      <h2 style="margin-top:20px; font-size:14px;">Follow-Ups</h2>
      <div id="followUpList" style="margin-bottom:10px;"></div>
      <div class="grid">
        <div class="field"><label>Date</label><input id="fu_date" type="date"></div>
        <div class="field" style="grid-column: span 2;"><label>Notes</label><input id="fu_notes" placeholder="e.g. Called about outstanding balance"></div>
      </div>
      <button onclick="logFollowUp()" style="margin-top:6px;">Log Follow-Up</button>
    </div>

    <!-- Independent Status Tiles (approved proposal, replaces the old
    sequential step-strip map) — a secondary, at-a-glance reference,
    never the primary surface above it. -->
    <div class="card" style="padding:12px 18px;">
      ${renderStatusTilesHtml(q, data.job_steps)}
    </div>

    <!-- Job Detail: Top Tab Bar (confirmed Sept 2026, replacing the old
    bottom accordion of the same seven named sections — "requiring
    scrolling to reach lower sections"). Same tab-button pattern already
    used elsewhere for switching sections, not a new component. Sits
    right here: below the three status cards above, above the section
    content below — matching the brief's own placement requirement.
    Every section's own content/fields/handlers below is UNCHANGED from
    the accordion this replaces; only the <details>/<summary> wrapper
    became a plain <div class="jd-tab-panel">, shown/hidden by
    switchJobDetailTab() instead of the browser's native <details>
    open/close. -->
    <div class="jd-tabs">
      ${jdTab('customer', 'Customer')}${jdTab('quote', 'Quote')}${jdTab('materials', 'Materials')}${jdTab('installation', 'Installation')}${jdTab('photos', 'Photos')}${jdTab('financial', 'Financial')}${jdTab('documents', 'Documents')}
    </div>
    <div class="card">
      <!-- Save confirmation (confirmed Aug 2026, Deposit Amount + Save
      Confirmation + Default Branch brief §2) — one shared banner for
      all three "Save Job Details" buttons below (Customer/Installation/
      Financial all write the same fields via the same saveOrderDetails()
      call), same real, temporary success banner as before this brief,
      just relocated to sit above whichever section it was clicked from. -->
      <div id="jobDetailsSaveBanner" style="display:none; background:#dcf5e6; color:#1a7a3e; border:2px solid #1a7a3e; border-radius:8px; padding:10px 14px; margin-bottom:12px; font-weight:700; font-size:13.5px;"></div>
      <div class="jd-tab-panel" data-tab="customer" style="${activeTab === 'customer' ? '' : 'display:none;'}">
          <!-- Client link (confirmed Aug 2026, Order Index -> Client
          Link Gap brief, Gap 2 fix) — real gap closed: there was
          previously no way to link an existing quote to a real Client
          record after the fact. -->
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
          </div>
          <button class="primary" onclick="saveOrderDetails()" style="margin-top:10px;">Save Job Details</button>
        </div>

      <div class="jd-tab-panel" data-tab="quote" style="${activeTab === 'quote' ? '' : 'display:none;'}">
          <!-- Manual Override total display (confirmed Aug 2026, Manual
          Override brief) — Job Detail doesn't show individual line
          items (that's Quote Builder's job, via "Open in Quote Builder"
          below), so this is the TOTAL only. -->
          <p style="margin:0 0 12px;">
            <b>Total (incl VAT):</b> R${data.total_incl_vat.toFixed(2)}
            ${q.manual_override_total_incl_vat != null ? `<span class="muted" style="font-size:11px; color:var(--coral); font-weight:700;" title="${(q.override_total_reason || '').replace(/"/g,'&quot;')} — by ${q.override_total_by || ''}${q.override_total_at ? ' on ' + new Date(q.override_total_at).toLocaleDateString('en-ZA') : ''}"> ✏️ Manually adjusted</span>` : ''}
            ${currentRole() === 'owner' ? (q.manual_override_total_incl_vat != null
              ? ` <a href="#" onclick="revertJobDetailTotalOverride(); return false;" style="font-size:11px; color:var(--teal); font-weight:600;">Revert to calculated</a>`
              : ` <a href="#" onclick="overrideJobDetailTotal(${data.total_incl_vat}); return false;" style="font-size:11px; color:var(--teal); font-weight:600;">Override total</a>`) : ''}
          </p>
          <!-- Conditional Quote/Invoice Preview (confirmed Aug 2026,
          JobDetail: Needs Attention Bug, Conditional Invoice Preview,
          Order Sheet Previews brief §2) — Quote Preview (with its
          compact payment-status strip) shows for every stage before
          the job is actually invoiced; Invoice Preview only once
          there's something genuine to preview. -->
          ${q.workflow_status !== 'completed' ? `
          <h3 style="font-size:13px; margin:0 0 8px;">Quote Preview</h3>
          ${paymentStatusStripHtml(q, data)}
          ${documentPreviewTileHtml('dp_jobdetail_' + q.id, q.id)}
          ` : ''}
          ${q.workflow_status === 'completed' ? `
          <h3 style="font-size:13px; margin:0 0 8px;">Invoice Preview</h3>
          ${documentPreviewTileHtml('dp_invoice_jobdetail_' + q.id, q.id, 'invoice')}
          ` : ''}
          <button onclick="openQuoteFromIndex(${q.id})" style="margin-top:12px;">Open in Quote Builder (line items)</button>
        </div>

      <div class="jd-tab-panel" data-tab="materials" style="${activeTab === 'materials' ? '' : 'display:none;'}">
          ${renderMaterialsSectionHtml(data.job_steps, q)}
        </div>

      <div class="jd-tab-panel" data-tab="installation" style="${activeTab === 'installation' ? '' : 'display:none;'}">
          <div class="grid">
            <div class="field"><label>Installation date</label><input id="od_installation_date" type="date" value="${q.installation_date || ''}"></div>
          </div>
          <button class="primary" onclick="saveOrderDetails()" style="margin-top:10px;">Save Job Details</button>
          ${renderJobWorkDaysHtml(data.work_days, q.id)}
        </div>

      <!-- Photo Gallery + Job Context (confirmed Sept 2026) — uploaded
      directly from THIS job's own page, so the client/job connection is
      automatic (q.id is already known here) and unambiguous, per the
      brief's own explicit "never a photo floating with no context."
      Genuinely distinct from Documents below (supplier price books,
      business/marketing documents) — same underlying Dropbox-backed
      mechanism (dropbox_archive.py), different concept, per the
      brief's own non-goal. Reuses the exact GET/POST/DELETE
      /quotes/{id}/photos endpoints Quote Builder's own Site Photos
      card already calls — same data, same backend, a second,
      independently-scoped set of render/upload/delete functions here
      (jobPhoto* below) rather than sharing quote-builder.js's
      currentQuoteId-scoped globals, since this screen has its own
      separate currentOrderDetailQuoteId. A photo added from either
      screen shows on both — same rows, same source of truth. -->
      <div class="jd-tab-panel" data-tab="photos" style="${activeTab === 'photos' ? '' : 'display:none;'}">
          <p class="muted" style="margin-top:0;">Site context for this job — no annotation or editing, just a simple gallery.</p>
          <div id="jobPhotoGallery" class="quote-photo-gallery"></div>
          <input type="file" id="jobPhotoInput" accept="image/*" multiple style="margin-top:10px;">
          <button onclick="uploadJobPhotos(${q.id})" style="margin-top:6px;">Upload</button>
          <p class="muted" id="jobPhotoUploadStatus" style="margin-top:6px;"></p>
        </div>

      <div class="jd-tab-panel" data-tab="financial" style="${activeTab === 'financial' ? '' : 'display:none;'}">
          <!-- Deposit Amount (confirmed Aug 2026, brief §1) — same
          precedence/flagging pattern as the Manual Override total,
          without a mandatory reason. balance_amount is already
          computed FROM this figure server-side (_quote_totals()). -->
          <p style="margin:0 0 4px;">
            <b>Deposit:</b> R${data.deposit_amount.toFixed(2)}
            ${q.actual_deposit_amount != null ? `<span class="muted" style="font-size:11px; color:var(--coral); font-weight:700;" title="Entered by ${q.actual_deposit_amount_by || ''}${q.actual_deposit_amount_at ? ' on ' + new Date(q.actual_deposit_amount_at).toLocaleDateString('en-ZA') : ''}"> ✏️ Actual (manually entered)</span>` : ` <span class="muted" style="font-size:11px;">(${(q.deposit_pct*100).toFixed(0)}% calculated)</span>`}
          </p>
          <p style="margin:0 0 14px;"><b>Balance due:</b> R${data.balance_amount.toFixed(2)}</p>
          <div class="grid">
            <!-- Deposit required % (Independent Status Tiles, Decision
            Q3, confirmed Aug 2026) — a genuine per-job override of
            deposit_pct, previously only settable via the global
            Business Settings default at quote creation. 0 is exactly
            how "no deposit required for this job" (Money tile) is
            represented — no separate checkbox needed. -->
            <div class="field"><label>Deposit required (%) <span class="adj">(this job's own required deposit — separate from the workspace-wide default; 0 means no deposit required for this job)</span></label><input id="od_deposit_pct" type="number" step="1" min="0" max="100" value="${(q.deposit_pct*100).toFixed(0)}"></div>
            <div class="field"><label>Invoice sent date</label><input id="od_invoice_sent_date" type="date" value="${q.invoice_sent_date || ''}"></div>
            <div class="field"><label>Deposit paid date</label><input id="od_deposit_paid_date" type="date" value="${q.deposit_paid_date || ''}"></div>
            <div class="field"><label>Deposit amount (R) <span class="adj">(actual amount paid — overrides the ${(q.deposit_pct*100).toFixed(0)}% calculated figure once entered; leave blank to use the percentage)</span></label><input id="od_actual_deposit_amount" type="number" step="0.01" value="${q.actual_deposit_amount != null ? q.actual_deposit_amount : ''}" placeholder="e.g. ${(data.total_incl_vat * q.deposit_pct).toFixed(2)} (calculated)"></div>
            <div class="field"><label>Deposit payment method</label><input id="od_deposit_payment_method" value="${q.deposit_payment_method || ''}" placeholder="EFT / Cash / Card / Yoco..."></div>
            <div class="field"><label>Final payment date</label><input id="od_final_payment_date" type="date" value="${q.final_payment_date || ''}"></div>
            <div class="field"><label>Final payment method</label><input id="od_final_payment_method" value="${q.final_payment_method || ''}" placeholder="EFT / Cash / Card / Yoco..."></div>
          </div>
          <button class="primary" onclick="saveOrderDetails()" style="margin-top:10px;">Save Job Details</button>
        </div>

      <div class="jd-tab-panel" data-tab="documents" style="${activeTab === 'documents' ? '' : 'display:none;'}">
          <!-- Materials Section Should Show the Actual Order Sheets
          (confirmed Sept 2026, approved proposal) — the real Order Sheet
          previews used to render here (renderOrderSheetPreviewsHtml(),
          retired) now live in Materials above, the section actually
          about them; showing them here too would be the exact duplicate
          list the proposal was approved to avoid. Documents' own,
          non-overlapping purpose from here on: the Dropbox Archive and
          Job Card below. -->
          <!-- Dropbox Document Archive brief (confirmed Aug 2026) —
          this card is the Quote's own archive history. Manual trigger
          only. No Dropbox token is configured yet (confirmed with
          Burgert) — every version still renders and stores a real PDF
          and shows honestly as "Pending" until one is set. -->
          <div id="documentArchiveCard">
            <h3 style="font-size:13px; margin:0 0 4px;">Document Archive</h3>
            <p class="muted" style="margin-top:0; font-size:12px;">Backup copy in Dropbox, separate from Bolton's own database — every archived version is kept, never overwritten.</p>
            <div id="documentArchiveContent" class="muted">Loading...</div>
          </div>
          <!-- Job Card (confirmed Aug 2026, Job Card Content Spec) --
          only once a job genuinely exists (job_number assigned at
          Accept). -->
          ${q.job_number ? `<button onclick="openJobCardScreen(${q.id})" style="margin-top:14px;">Job Card</button>` : ''}
        </div>
    </div>
  `;
  loadFollowUps();
  loadJobPhotos(q.id);
  // Only load whichever preview actually rendered above (conditional on
  // workflow_status, §2 of the brief) — loadDocumentPreview() itself
  // no-ops safely if the element isn't there, but calling it for a card
  // that was never rendered means building a real print-doc HTML string
  // just to throw it away, wasted work for every job before/after the
  // stage where it's actually relevant.
  if (q.workflow_status !== 'completed') {
    loadDocumentPreview('dp_jobdetail_' + q.id, q.id);
  } else {
    loadDocumentPreview('dp_invoice_jobdetail_' + q.id, q.id, 'invoice');
    applyInvoiceEditLock(q.id);
  }
  // Order Sheet previews (§3) — one per tile, same pattern
  // renderOrderSheetDetail() already uses for its own single preview.
  (data.job_steps || []).find(st => st.id === 'procurement')?.tiles.forEach(t => {
    loadDocumentPreview('dp_ordersheet_tile_' + t.id, t.id, 'ordersheet');
  });
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

// Invoice Edit-lock (confirmed Aug 2026, Document Action Bar brief,
// resolved with Burgert: "NO editing after an invoice has been sent,
// full stop"). "Sent" = this quote has ever had a real Invoice
// successfully archived — a real, already-existing signal (no new
// field needed), checked async here (after the initial render, same
// pattern as loadDocumentPreview/loadDocumentArchiveStatus above)
// since it needs its own request. Disables the SAME Edit button
// documentActionBarHtml() already rendered (enabled by default) rather
// than re-rendering the whole card — cheap, and avoids a flash of
// "disabled" on every load for the common case (never sent yet).
async function applyInvoiceEditLock(quoteId) {
  try {
    const hist = await (await fetch(`${API}/documents/archive?entity_type=Invoice&entity_id=${quoteId}`)).json();
    if (!Array.isArray(hist) || hist.length === 0) return;   // never sent — stays enabled
    const btn = document.getElementById(`docActionEditBtn_invoice_${quoteId}`);
    if (!btn) return;
    btn.disabled = true;
    btn.style.borderColor = '#c7c7c7';
    btn.style.color = '#c7c7c7';
    btn.style.cursor = 'not-allowed';
    btn.removeAttribute('onclick');
    btn.title = 'This invoice has already been sent — use Duplicate on the original quote to create a supplementary invoice instead.';
  } catch (e) { /* best-effort — Edit just stays enabled if this check fails, never a hard error on the page */ }
}

let orderSheetsResultBannerTimeout = null;
function showOrderSheetsResultBanner(message) {
  const banner = document.getElementById('orderSheetsResultBanner');
  if (!banner) return;
  banner.textContent = message;
  banner.style.display = 'block';
  clearTimeout(orderSheetsResultBannerTimeout);
  orderSheetsResultBannerTimeout = setTimeout(() => { banner.style.display = 'none'; }, 6000);
  // Scroll the procurement tiles into view too (Phase 3, confirmed Aug
  // 2026 -- was the now-retired Order Sheet Preview panel) -- the
  // banner and the real generated tiles both live in this same block
  // already, so this is mostly a no-op scroll on desktop, but keeps
  // the confirmation visible on mobile where it can be below the fold.
  const tilesBlock = document.getElementById('procurementTilesBlock');
  if (tilesBlock) tilesBlock.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
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
      // Dropbox Filenames: Client Name + Job Name (confirmed Sept 2026)
      // — "slow to visually scan or search a folder for the right
      // document" without the client's name in the filename itself.
      // _create_and_upload_archive() (main.py) sanitizes this whole
      // string into the filename verbatim (non-alnum/hyphen -> "_"), so
      // a single hyphen with no surrounding spaces here keeps the
      // result clean — e.g. "J-0001-John_Smith_v2.pdf" — rather than a
      // run of "_-_" from a spaced separator.
      const reference = (qData.quote.job_number || ('Q-' + quoteId)) + '-' + qData.quote.client_name;
      const { html } = await buildPrintDocHtml(quoteId, 'quote');
      const cssRes = await fetch('styles.css');
      const css = cssRes.ok ? await cssRes.text() : '';
      await fetch(`${API}/documents/archive`, {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ entity_type: 'Quote', entity_id: quoteId, reference, html, css, mark_as_accepted: true, branch: qData.quote.branch }),
      });
    }
  } catch (e) { /* best-effort -- the accept itself already succeeded above; this screen re-renders and shows the real archive status regardless */ }
  renderOrderDetail(document.getElementById('landing'));
}
// Decline Quote reason (confirmed Aug 2026, Master Workflow proposal
// §01/§02/§05) — a plain confirm() used to be the entire mechanism, no
// reason captured anywhere. Same prompt()-then-POST shape as
// holdJobAction() just above, reusing the identical one-field pattern
// rather than inventing a second.
async function declineQuoteAction(quoteId) {
  const reason = prompt('Why is this quote being declined? (e.g. "Went with another supplier", "Price too high")');
  if (!reason || !reason.trim()) return;
  const res = await fetch(`${API}/quotes/${quoteId}/decline`, {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ reason: reason.trim() }),
  });
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
// ready_for_installation and installer_team each change independently
// in real life, at different times (confirmed Aug 2026), so each gets
// its own immediate call rather than waiting for a shared "Save" click
// that could silently overwrite one with a stale value from the other.
// materials_ordered's own manual setter was removed in the same spirit
// this comment already established -- it's now a read-only, server-
// derived value (renderWorkflowActionsHtml() above), not a field
// anything on this screen sets directly any more.
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

// ===== Job Card (confirmed Aug 2026, Job Card Content Spec) =====
// A printable, installer-facing document — what a team needs on-site to
// actually do the job, without navigating the quoting system and
// without seeing any pricing. Per the original Master Workflow
// proposal, this is a VIEW ONLY screen — no new data beyond the one
// small installation_notes field (get_job_card(), main.py already pulls
// everything else fresh from Quote/QuoteLineItem/OrderSheet/Client/Lead
// on every load). Deliberately NOT built on the compact click-to-expand
// documentPreviewTileHtml() component the way Quote/Invoice/Order Sheet
// previews are — this is its own full, standalone page (same category
// as Order Sheet Detail itself), so the content is already fully
// visible without needing an expand/collapse tile; "View" (brief's own
// action-bar question) is satisfied by the page simply existing.
//
// Edit deliberately excluded — explicitly view-only per the original
// proposal, generated fresh from existing data every time, never
// something edited in place (the one exception, installation_notes, has
// its own dedicated Save action right in this screen, not a generic
// "Edit" entry point into some other editor).
//
// Save (Dropbox archival) deliberately excluded too, unlike Quote/
// Invoice/Order Sheet — those three are genuine documents OF RECORD
// (proof of what was quoted/invoiced/ordered, worth a permanent
// version history); a Job Card is a regenerate-on-demand operational
// aid with no independent existence of its own — archiving snapshots
// of it wouldn't carry the same evidentiary value, and doing it
// properly would mean adding a new entity type to
// ARCHIVE_CATEGORY_FOLDER (main.py) for comparatively little real
// benefit. Mail IS kept — genuinely cheap (a plain mailto, no new
// backend surface) and directly serves the brief's own named use case
// (a team member having this on their phone before heading out).
let currentJobCardQuoteId = null;
function openJobCardScreen(quoteId) {
  currentJobCardQuoteId = quoteId;
  landingView = 'jobCard';
  renderLanding();
}

function jobCardMaterialsHtml(jc) {
  if (!jc.order_sheets.length) return '<p class="muted" style="margin:0;">No Order Sheet generated for this job yet.</p>';
  return jc.order_sheets.map(s => `
    <div style="margin-bottom:14px;">
      <p style="margin:0 0 6px; font-weight:700; font-size:13px;">${s.sheet_type === 'floor_prep' ? 'Flooring + Floor Prep' : 'Flooring'} <span class="muted" style="font-weight:400;">(${s.supplier})</span></p>
      <table class="mobile-card-table"><tbody>
        ${s.lines.map(l => `<tr><td data-label="Item">${l.product_name}${l.colour ? `<br><b style="color:var(--teal);">${l.colour}</b>` : ''}</td><td class="num" data-label="Qty">${l.quantity} ${l.unit || ''}</td></tr>`).join('')}
      </tbody></table>
    </div>`).join('');
}

const SUBSTRATE_LABELS = {smooth: 'Smooth', over_tiles: 'Over Tiles', removed_tiles: 'Removed Tiles'};

async function renderJobCard(el) {
  await renderWithRetry(el, 'Job Card', async () => {
  el.innerHTML = `<span class="back-link" onclick="openOrderDetailScreen(${currentJobCardQuoteId}); return false;">← Back to Job Detail</span><div class="card"><p class="muted">Loading...</p></div>`;
  const res = await fetch(`${API}/quotes/${currentJobCardQuoteId}/job-card`);
  const jc = await res.json();
  setPageTitle(`Job Card — ${jc.job_number || 'Job'}`);

  const referenceNotes = (jc.client_notes || jc.lead_notes) ? `
    <div class="card">
      <h2>Reference notes <span class="muted" style="font-weight:400; font-size:12px;">(pulled in from elsewhere on file — read-only here)</span></h2>
      ${jc.client_notes ? `<p style="margin:0 0 8px;"><b>Client notes:</b> ${jc.client_notes.replace(/</g,'&lt;')}</p>` : ''}
      ${jc.lead_notes ? `<p style="margin:0;"><b>From original enquiry:</b> ${jc.lead_notes.replace(/</g,'&lt;')}</p>` : ''}
    </div>` : '';

  el.innerHTML = `
    <span class="back-link" onclick="openOrderDetailScreen(${currentJobCardQuoteId}); return false;">← Back to Job Detail</span>
    <div class="landing-welcome">
      <h1>Job Card — ${jc.job_number || 'Job'}</h1>
      <p>${jc.client_name} &nbsp; ${jc.site_address || ''}</p>
    </div>

    <div class="card">
      <h2>Job info</h2>
      <p style="margin:0 0 4px;"><b>Install date:</b> ${dateOrDash(jc.installation_date)}</p>
      <p style="margin:0 0 4px;"><b>Team:</b> ${jc.installer_team || '—'}</p>
      ${jc.substrate ? `<p style="margin:0;"><b>Substrate:</b> ${SUBSTRATE_LABELS[jc.substrate] || jc.substrate}</p>` : ''}
    </div>

    <div class="card">
      <h2>Materials to load</h2>
      ${jobCardMaterialsHtml(jc)}
    </div>

    ${referenceNotes}

    <div class="card">
      <h2>Job Card notes</h2>
      <p class="muted" style="margin-top:-8px;">Access, parking, anything about the floor or install the team needs to know — enter here before printing.</p>
      <textarea id="jc_notes" rows="3" style="width:100%; font-family:inherit; font-size:14px; padding:8px; border:1px solid var(--border); border-radius:6px;" placeholder="e.g. Narrow driveway, use the small van. Gate code on file with client notes above.">${(jc.installation_notes || '').replace(/</g,'&lt;')}</textarea>
      <button class="primary" onclick="saveJobCardNotes()" style="margin-top:8px;">Save notes</button>
      <p class="muted" id="jcNotesSaveStatus" style="margin-top:6px;"></p>
    </div>

    <div class="card">
      <div class="doc-action-bar" style="display:flex; gap:6px; flex-wrap:wrap;">
        <button onclick="printJobCard(${currentJobCardQuoteId})" style="font-size:12px; padding:4px 10px; background:none; border:1.5px solid var(--navy); color:var(--navy); border-radius:5px;">Print</button>
        <button onclick="sendJobCardEmail(${currentJobCardQuoteId})" style="font-size:12px; padding:4px 10px; background:none; border:1.5px solid var(--navy); color:var(--navy); border-radius:5px;">Mail</button>
      </div>
    </div>
  `;
  });
}

async function saveJobCardNotes() {
  const notes = document.getElementById('jc_notes').value;
  const statusEl = document.getElementById('jcNotesSaveStatus');
  const res = await fetch(`${API}/quotes/${currentJobCardQuoteId}?installation_notes=${encodeURIComponent(notes)}`, {method: 'PUT'});
  if (statusEl) statusEl.textContent = res.ok ? '✓ Saved.' : '❌ Could not save — check your connection and try again.';
}

// Hard constraint — no pricing anywhere on this document (Job Card
// Content Spec's own words). Deliberately built from the SAME
// get_job_card() response the on-screen version above renders — never
// a second, separately-fetched copy that could drift or accidentally
// carry a cost field the on-screen version was careful to exclude.
async function buildJobCardPrintHtml(quoteId) {
  const jc = await (await fetch(`${API}/quotes/${quoteId}/job-card`)).json();
  const logoSrc = document.querySelector('header .logo-row img').src;
  const materialsRows = jc.order_sheets.flatMap(s => s.lines.map(l => `
    <tr>
      <td>${l.product_name}${l.colour ? `<br><b style="color:var(--teal);">${l.colour}</b>` : ''}</td>
      <td class="num">${l.quantity} ${l.unit || ''}</td>
    </tr>`)).join('');
  const html = `
    <div class="print-doc">
      <div class="doc-header">
        <img src="${logoSrc}" style="height:36px;">
        <div class="doc-title">JOB CARD — ${jc.job_number || ''}</div>
      </div>
      <div style="margin-bottom:16px; font-size:13px;">
        <div><b>${jc.client_name}</b></div>
        <div>${jc.site_address || ''}</div>
        <div style="margin-top:8px;"><b>Install date:</b> ${jc.installation_date ? new Date(jc.installation_date).toLocaleDateString('en-ZA') : '—'} &nbsp; <b>Team:</b> ${jc.installer_team || '—'}</div>
        ${jc.substrate ? `<div><b>Substrate:</b> ${SUBSTRATE_LABELS[jc.substrate] || jc.substrate}</div>` : ''}
      </div>
      <table style="width:100%; border-collapse:collapse; font-size:13px;">
        <thead><tr><th style="text-align:left;">Materials to load</th><th class="num">Qty</th></tr></thead>
        <tbody>${materialsRows || '<tr><td colspan="2">No Order Sheet generated for this job yet.</td></tr>'}</tbody>
      </table>
      ${(jc.client_notes || jc.lead_notes) ? `
      <div style="margin-top:16px; font-size:12px;">
        ${jc.client_notes ? `<div><b>Client notes:</b> ${jc.client_notes.replace(/</g,'&lt;')}</div>` : ''}
        ${jc.lead_notes ? `<div><b>From original enquiry:</b> ${jc.lead_notes.replace(/</g,'&lt;')}</div>` : ''}
      </div>` : ''}
      ${jc.installation_notes ? `<div style="margin-top:16px; font-size:13px;"><b>Notes:</b> ${jc.installation_notes.replace(/</g,'&lt;')}</div>` : ''}
    </div>`;
  return { html, jc };
}

async function printJobCard(quoteId) {
  const { html } = await buildJobCardPrintHtml(quoteId);
  triggerPrint(html);
}

// Mail (confirmed Aug 2026) — no recipient pre-filled: unlike a
// supplier (Order Sheets) or a client (Quote/Invoice), "the installer
// team" has no stored contact record anywhere in this app
// (Quote.installer_team is plain free text, e.g. "Ryno + 1", not a
// structured Employee reference) — whoever sends this types in
// wherever it's actually going. Subject/body still fully pre-filled,
// same as every other Mail action in this app.
async function sendJobCardEmail(quoteId) {
  const { jc } = await buildJobCardPrintHtml(quoteId);
  const subject = `Job Card — ${jc.job_number || ''} — ${jc.client_name}`;
  const body = `Job Card for ${jc.job_number || ''} (${jc.client_name}, ${jc.site_address || ''}).\n\n(Use Print → "Save as PDF" on the Job Card screen to attach a file — it can't be attached automatically here.)`;
  window.location.href = `mailto:?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`;
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

    <!-- Job Workflow Design Proposal Phase 3 (confirmed Aug 2026) —
    Document Preview + the standard 5-button Action Bar (View/Edit/
    Print/Save/Mail), moved here from the now-retired inline "Order
    Sheet Preview" panel on Job Detail — this screen is now the ONE
    place that panel's full functionality lives, reached via a tile
    click. Print/Mail here call the exact same printOrderSheet()/
    sendOrderSheetEmail() functions the removed panel's own bespoke
    Print/Send buttons already called, so nothing about what those
    DO has changed, only where the buttons live. -->
    <div class="card">
      <h2>Document Preview <span class="muted" style="font-weight:400; font-size:12px;">(supplier-facing procurement document — not the client's quote)</span></h2>
      ${documentPreviewTileHtml('dp_ordersheet_detail_' + sheet.id, sheet.id, 'ordersheet', sheet.status === 'placed' ? {editDisabled: true, editDisabledReason: 'This order sheet is already placed — generate a new one for this job if more materials are needed.'} : null)}
    </div>
  `;
  loadDocumentPreview('dp_ordersheet_detail_' + sheet.id, sheet.id, 'ordersheet');
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

// Dropbox Document Archive brief v2 (confirmed Aug 2026, §2) — the real
// gap the original Dropbox brief pass flagged and deliberately left open
// ("no print-style document template to archive yet" — see
// documentArchiveCard's own comment above): Order Sheets had no clean,
// static rendering of their own — orderSheetLinesEditorHtml() is the
// EDITABLE in-app table (quantity inputs, delete buttons), wrong for an
// archived document. This builds a genuinely separate, non-interactive
// print-doc, then feeds it through the exact same shared pipeline every
// other archived document already uses (render_html_to_pdf() via
// /documents/archive) — per the brief's own "reuse the existing
// Document Preview PDF generation... rather than building new rendering
// logic" (§2): the RENDERING PIPELINE is reused unchanged; only this one
// new HTML-string builder is new, same relationship buildPrintDocHtml()
// (shared.js) already has to that same pipeline for quotes/invoices.
// Real cost (unit_cost) IS shown here, deliberately — this is an
// internal, supplier-facing procurement document, never sent to a
// client, exactly what Order Sheets already are elsewhere in this app.
async function buildOrderSheetPrintHtml(orderSheetId) {
  const sheet = await (await fetch(`${API}/order-sheets/${orderSheetId}`)).json();
  const logoSrc = document.querySelector('header .logo-row img').src;
  const biz = await (await fetch(`${API}/business-settings`)).json();
  const total = sheet.lines.reduce((sum, l) => sum + (l.unit_cost * l.quantity), 0);
  const rows = sheet.lines.map(l => `
    <tr>
      <td>${l.product_name}${l.colour ? `<br><b style="color:var(--teal);">${l.colour}</b>` : ''}${l.is_extra ? '<br><span style="font-size:11px; color:#9aa0a6;">(added manually)</span>' : ''}</td>
      <td class="num">${l.quantity} ${l.unit || ''}</td>
      <td class="num">R${l.unit_cost.toFixed(2)}</td>
      <td class="num">R${(l.unit_cost * l.quantity).toFixed(2)}</td>
    </tr>`).join('');
  const html = `
    <div class="print-doc">
      <div class="doc-header">
        <div>
          <img src="${logoSrc}" style="height:36px;">
          <div style="margin-top:8px; font-size:11px; color:#6b7280; line-height:1.5;">
            ${biz.business_name ? `<b style="color:var(--navy); font-size:12px;">${biz.business_name}</b><br>` : ''}
            ${biz.address ? `${biz.address}<br>` : ''}
            ${biz.phone ? `Tel: ${biz.phone}` : ''}${biz.phone && biz.email ? ' · ' : ''}${biz.email ? biz.email : ''}
          </div>
        </div>
        <div>
          <div class="doc-title">ORDER ${sheet.order_number}</div>
          <div style="text-align:right; font-size:12px; color:#6b7280;">${new Date().toLocaleDateString('en-ZA')}</div>
          <div style="text-align:right; font-size:11px; color:#6b7280;">Ref: ${sheet.job_number || ('Q-' + sheet.quote_id)}${sheet.client_name ? ' — ' + sheet.client_name : ''}</div>
        </div>
      </div>
      <div style="margin-bottom:20px; font-size:13px;">
        <div><b>Supplier:</b> ${sheet.supplier}</div>
        <div><b>Type:</b> ${sheet.sheet_type === 'floor_prep' ? 'Floor Prep' : 'Flooring'}</div>
        <div><b>Status:</b> ${sheet.status === 'placed' ? `Placed${sheet.placed_by ? ' by ' + sheet.placed_by : ''}${sheet.placed_at ? ' on ' + new Date(sheet.placed_at).toLocaleDateString('en-ZA') : ''}` : 'Draft'}</div>
      </div>
      <table style="width:100%; border-collapse:collapse; font-size:13px;">
        <thead><tr><th style="text-align:left;">Product</th><th class="num">Qty</th><th class="num">Cost/unit</th><th class="num">Line total</th></tr></thead>
        <tbody>${rows}</tbody>
        <tfoot><tr><td colspan="3" style="text-align:right;"><b>Total (ex VAT)</b></td><td class="num"><b>R${total.toFixed(2)}</b></td></tr></tfoot>
      </table>
    </div>`;
  return { html };
}

// Print (confirmed Aug 2026, Send button brief) — Order Sheets had no
// real user-facing Print action before this; reuses
// buildOrderSheetPrintHtml() (the exact same HTML the archive already
// stores) and the same triggerPrint() (shared.js) every other print
// action in this app uses.
async function printOrderSheet(orderSheetId) {
  const { html } = await buildOrderSheetPrintHtml(orderSheetId);
  triggerPrint(html);
}

// Send (confirmed Aug 2026, Send button brief) — an Order's recipient is
// its SUPPLIER, not a client, so this can't reuse sendDocumentEmail()
// (shared.js, client-based) — parallel logic instead: look up the
// supplier's email (GET /admin/supplier-emails, deliberately not
// owner-only — any role viewing this Order Sheet can Send it), refuse
// to open a blank/broken mailto when there isn't one, same explicit
// requirement as the client-facing Send button.
async function sendOrderSheetEmail(orderSheetId) {
  const sheet = await (await fetch(`${API}/order-sheets/${orderSheetId}`)).json();
  const emails = await (await fetch(`${API}/admin/supplier-emails`)).json();
  const email = emails[sheet.supplier];
  if (!email) {
    alert(`No email address on file for ${sheet.supplier} — add one via Price Book → Supplier Console first, then try Send again.`);
    return;
  }
  const biz = await (await fetch(`${API}/business-settings`)).json();
  const branchFolder = sheet.branch ? (sheet.branch.charAt(0).toUpperCase() + sheet.branch.slice(1).toLowerCase()) : 'Unassigned';
  const subject = `Order ${sheet.order_number} — ${biz.business_name || ''}`;
  const body = `Hi ${sheet.supplier},\n\nPlease find attached order ${sheet.order_number}, for job ${sheet.job_number || ('Q-' + sheet.quote_id)}${sheet.client_name ? ' (' + sheet.client_name + ')' : ''}.\n\n(The PDF is saved automatically in Dropbox — Bolton/${branchFolder}/ — attach it from there before sending, it can't be attached automatically here.)\n\nKind regards,\n${biz.business_name || ''}`;
  window.location.href = `mailto:${encodeURIComponent(email)}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`;
}

// Order Sheets UX brief §4 (confirmed Aug 2026) — "Executable... mark
// the order as placed." Once placed, generate_order_sheets() no
// longer treats this sheet as blocking a fresh one for the same
// job+supplier+category (see main.py) — a genuine re-order.
async function finalizeOrderSheet(orderSheetId) {
  if (!confirm('Mark this order sheet as placed? This means the order has genuinely been sent to the supplier — quantities can no longer be edited after this.')) return;
  const res = await fetch(`${API}/order-sheets/${orderSheetId}/finalize`, {method: 'POST'});
  if (!res.ok) { const body = await res.json().catch(() => ({})); alert(body.detail || 'Could not mark this order sheet as placed.'); return; }
  // Archive the FINAL, placed state — same "meaningful event, not every
  // edit" trigger philosophy as acceptQuoteAction() (Quote) — a draft
  // being tweaked isn't worth a Dropbox version yet; "genuinely sent to
  // the supplier" is. Best-effort: this finalize already succeeded above
  // and must not be undone by an archive hiccup (brief §7).
  try {
    const sheetForRef = await (await fetch(`${API}/order-sheets/${orderSheetId}`)).json();
    // Dropbox Filenames: Client Name + Job Name (confirmed Sept 2026) —
    // same reasoning as acceptQuoteAction()'s own reference above.
    const reference = sheetForRef.order_number + (sheetForRef.client_name ? '-' + sheetForRef.client_name : '');
    const { html } = await buildOrderSheetPrintHtml(orderSheetId);
    const cssRes = await fetch('styles.css');
    const css = cssRes.ok ? await cssRes.text() : '';
    await fetch(`${API}/documents/archive`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ entity_type: 'OrderSheet', entity_id: orderSheetId, reference, html, css, branch: sheetForRef.branch }),
    });
  } catch (e) { /* best-effort — the finalize above already succeeded regardless */ }
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
  // Deposit required % (Independent Status Tiles, Decision Q3,
  // confirmed Aug 2026) — the field's own displayed value is already a
  // whole percentage (e.g. "70"), converted back to the 0–1 fraction
  // the backend/every other reader of deposit_pct expects.
  const depositPctVal = document.getElementById('od_deposit_pct').value;
  if (depositPctVal !== '') { params.set('deposit_pct', (parseFloat(depositPctVal) / 100).toString()); }
  const res = await fetch(`${API}/quotes/${currentOrderDetailQuoteId}?${params}`, {method:'PUT'});
  if (!res.ok) { const body = await res.json().catch(() => ({})); alert(body.detail || 'Could not save — check your connection and try again.'); return; }
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
  // Logged Follow-Up Doesn't Clear Needs Attention Flag (confirmed
  // Sept 2026) — a bare loadFollowUps() only refreshed this section's
  // own small list; the primary 🟢/🟡/🔴 status strip at the top of
  // this same page (jobControlPanelStatusHtml(), reads data.workflow)
  // stayed showing the stale "Follow up" flag until the whole quote
  // was re-fetched some other way. A full renderOrderDetail() picks up
  // the now-fixed _job_workflow_info() (main.py) result immediately —
  // this already calls loadFollowUps() itself as part of its own
  // render, so nothing is lost, only the strip gains the refresh it
  // was missing.
  renderOrderDetail(document.getElementById('landing'));
}

async function loadFollowUps() {
  const res = await fetch(`${API}/quotes/${currentOrderDetailQuoteId}/follow-ups`);
  const followUps = await res.json();
  const el = document.getElementById('followUpList');
  el.innerHTML = followUps.length
    ? followUps.map(f => `<div class="muted" style="font-size:12px; padding:3px 0;">${new Date(f.follow_up_date).toLocaleDateString('en-ZA')} — ${f.notes || '(no notes)'}</div>`).join('')
    : '<p class="muted" style="font-size:12px;">No follow-ups logged yet.</p>';
}

// Photo Gallery + Job Context (confirmed Sept 2026) — Job Detail's own
// version of quote-builder.js's Site Photos card (loadQuotePhotos()/
// renderQuotePhotoGallery()/uploadQuotePhotos()/deleteQuotePhoto()) —
// same backend (GET/POST/DELETE /quotes/{id}/photos), same DB rows,
// deliberately a second, independently-scoped copy rather than sharing
// that screen's own currentQuoteId-scoped globals, since this screen
// tracks currentOrderDetailQuoteId instead. openPhotoLightbox()/
// closePhotoLightbox() (quote-builder.js) are reused directly — the
// lightbox overlay is one page-level element, not scoped to either
// screen.
let currentJobPhotos = [];
let jobPhotoObjectUrls = [];

async function loadJobPhotos(quoteId) {
  if (!quoteId) return;
  const res = await fetch(`${API}/quotes/${quoteId}/photos`);
  currentJobPhotos = res.ok ? await res.json() : [];
  renderJobPhotoGallery(quoteId);
}

function renderJobPhotoGallery(quoteId) {
  const el = document.getElementById('jobPhotoGallery');
  if (!el) return;
  jobPhotoObjectUrls.forEach(url => URL.revokeObjectURL(url));
  jobPhotoObjectUrls = [];
  if (!currentJobPhotos.length) {
    el.innerHTML = '<p class="muted" style="margin:0;">No photos on this job yet.</p>';
    return;
  }
  el.innerHTML = currentJobPhotos.map(p => `
    <div class="quote-photo-thumb" id="jobPhotoThumb${p.id}">
      <div class="photo-loading">Loading…</div>
      <button class="photo-delete-btn" title="Delete photo" onclick="event.stopPropagation(); deleteJobPhoto(${quoteId}, ${p.id})">✕</button>
      ${p.uploaded_by === 'builder' ? '<span class="photo-badge">Builder</span>' : ''}
    </div>`).join('');
  // Blob object URLs, not a plain <img src="...">, for the same reason
  // quote-builder.js's own gallery does this — the file endpoint needs
  // the Bearer auth header the global fetch() wrapper attaches, which a
  // plain <img> tag has no way to send.
  currentJobPhotos.forEach(async (p) => {
    try {
      const res = await fetch(`${API}/quotes/${quoteId}/photos/${p.id}/file`);
      if (!res.ok) throw new Error('fetch failed');
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      jobPhotoObjectUrls.push(url);
      const thumbEl = document.getElementById(`jobPhotoThumb${p.id}`);
      if (thumbEl) {
        const img = document.createElement('img');
        img.src = url;
        img.onclick = () => openPhotoLightbox(url);
        thumbEl.prepend(img);
        const loadingEl = thumbEl.querySelector('.photo-loading');
        if (loadingEl) loadingEl.remove();
      }
    } catch (e) {
      const thumbEl = document.getElementById(`jobPhotoThumb${p.id}`);
      if (thumbEl) { const l = thumbEl.querySelector('.photo-loading'); if (l) l.textContent = 'Failed'; }
    }
  });
}

async function uploadJobPhotos(quoteId) {
  if (!quoteId) return;
  const input = document.getElementById('jobPhotoInput');
  const statusEl = document.getElementById('jobPhotoUploadStatus');
  const files = Array.from(input.files || []);
  if (!files.length) { statusEl.textContent = 'Choose one or more photos first.'; return; }
  statusEl.textContent = `Uploading ${files.length} photo${files.length !== 1 ? 's' : ''}…`;
  let uploaded = 0, failed = 0;
  // Same real-reason-not-generic-message fix as quote-builder.js's own
  // uploadQuotePhotos() (confirmed Sept 2026, Burgert's direct report
  // "upload failed") — collected across the loop, not overwritten after it.
  const failReasons = [];
  for (const file of files) {
    const body = new FormData();
    body.append('file', file);
    try {
      const res = await fetch(`${API}/quotes/${quoteId}/photos`, { method: 'POST', body });
      if (res.ok) { uploaded++; }
      else {
        failed++;
        const err = await res.json().catch(() => ({}));
        failReasons.push(err.detail || `Couldn't upload ${file.name}.`);
      }
    } catch (e) {
      failed++;
      failReasons.push(`${file.name}: check your connection and try again.`);
    }
  }
  input.value = '';
  statusEl.textContent = failed
    ? `${uploaded} uploaded, ${failed} failed — ${failReasons.join(' ')}`
    : `${uploaded} photo${uploaded !== 1 ? 's' : ''} uploaded ✓`;
  await loadJobPhotos(quoteId);
}

async function deleteJobPhoto(quoteId, photoId) {
  if (!confirm('Delete this photo? This cannot be undone.')) return;
  const res = await fetch(`${API}/quotes/${quoteId}/photos/${photoId}`, { method: 'DELETE' });
  if (!res.ok) { alert('Could not delete photo.'); return; }
  await loadJobPhotos(quoteId);
}

// Cross-job Photo Gallery (Photo Gallery + Job Context brief §2,
// confirmed Sept 2026, built on request — "browse across jobs if
// useful") — deliberately browse-only, no upload/delete controls here:
// those stay on each job's own Photos section (Job Detail), the one
// place that's already grouped by construction. This screen's whole
// job is "see everything across the business, with context, click
// through to the real one." GET /photos already excludes photo_bytes
// (_photo_out(), main.py) and joins in job_number/client_name per row.
let photoGalleryObjectUrls = [];

async function renderPhotoGallery(el) {
  await renderWithRetry(el, 'Photo Gallery', async () => {
    el.innerHTML = `<span class="back-link" onclick="landingView='tiles'; renderLanding();">← Back</span><div class="card"><p class="muted">Loading...</p></div>`;
    const res = await fetch(`${API}/photos`);
    const photos = res.ok ? await res.json() : [];
    el.innerHTML = `
      <span class="back-link" onclick="landingView='tiles'; renderLanding();">← Back</span>
      <div class="landing-welcome">
        <h1>Photo Gallery</h1>
        <p>Every job's site photos, whole business, at a glance — click a photo to open its job.</p>
      </div>
      <div class="card">
        ${photos.length
          ? `<div class="quote-photo-gallery" id="photoGalleryGrid"></div>`
          : '<p class="muted" style="margin:0;">No photos on any job yet.</p>'}
      </div>
    `;
    if (photos.length) renderPhotoGalleryGrid(photos);
  });
}

function renderPhotoGalleryGrid(photos) {
  const el = document.getElementById('photoGalleryGrid');
  if (!el) return;
  photoGalleryObjectUrls.forEach(url => URL.revokeObjectURL(url));
  photoGalleryObjectUrls = [];
  el.innerHTML = photos.map(p => {
    // "Every photo ever loaded" (confirmed Sept 2026) — GET /photos no
    // longer requires a job_number, so two real cases now show up here
    // that never had one before: a photo on a quote not yet accepted
    // into a job (job_number null, but quote_id IS set — still fully
    // clickable), and a builder-submitted photo staff hasn't linked to
    // any quote at all yet (quote_id null too — nothing to click
    // through to). Caption and click-through both branch on that.
    const label = p.job_number || (p.quote_id ? `Q-${p.quote_id}` : 'Builder submission — not yet linked');
    const client = (p.client_name || '').replace(/</g,'&lt;');
    const clickable = !!p.quote_id;
    return `
    <div class="quote-photo-thumb photo-gallery-thumb${clickable ? '' : ' photo-gallery-unlinked'}" id="galleryPhotoThumb${p.id}"${clickable ? ` onclick="openOrderDetailScreen(${p.quote_id})"` : ''}>
      <div class="photo-loading">Loading…</div>
      <div class="photo-gallery-caption">${label}${client ? ' — ' + client : ''}</div>
    </div>`;
  }).join('');
  // Same blob-object-URL loading as every other photo thumbnail in this
  // app (loadDocumentPreview()'s own reasoning applies identically here
  // — the file endpoint needs the Bearer auth header a plain <img> tag
  // has no way to send).
  photos.forEach(async (p) => {
    try {
      const res = await fetch(`${API}/quotes/${p.quote_id}/photos/${p.id}/file`);
      if (!res.ok) throw new Error('fetch failed');
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      photoGalleryObjectUrls.push(url);
      const thumbEl = document.getElementById(`galleryPhotoThumb${p.id}`);
      if (thumbEl) {
        const img = document.createElement('img');
        img.src = url;
        thumbEl.prepend(img);
        const loadingEl = thumbEl.querySelector('.photo-loading');
        if (loadingEl) loadingEl.remove();
      }
    } catch (e) {
      const thumbEl = document.getElementById(`galleryPhotoThumb${p.id}`);
      if (thumbEl) { const l = thumbEl.querySelector('.photo-loading'); if (l) l.textContent = 'Failed'; }
    }
  });
}
