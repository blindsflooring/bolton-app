// ===== LEADS =====
// New enquiry tracking, from first contact through to becoming a real
// Quote or being lost (confirmed Aug 2026, Master Workflow proposal
// §02/§05/§06/§07 — the "LEAD" stage of the master flow, which had
// nowhere to live at all before this build). Deliberately its own
// module, not folded into order-index.js: a Lead is isolated from the
// Job workflow by design (proposal §08's own risk assessment) — it only
// ever feeds INTO a Quote via converted_quote_id, nothing reads back —
// so this stays a small, self-contained screen with its own table-with-
// Next-Action pattern, same visual language as the Order Index but a
// genuinely separate engine underneath (_lead_next_action(), main.py,
// not a branch on _job_workflow_info()).

const LEAD_STATUS_META = {
  new:       {label: 'New',       bg: '#fdecea',      color: '#c0392b'},
  contacted: {label: 'Contacted', bg: '#f0f0f0',       color: '#6b7280'},
  potential: {label: 'Potential', bg: 'var(--cream)',  color: '#8a6d00'},
  converted: {label: 'Converted', bg: '#dcf5e6',       color: '#1a7a3e'},
  lost:      {label: 'Lost',      bg: '#f0f0f0',       color: '#9ca3af'},
};
function leadStatusBadge(l) {
  const meta = LEAD_STATUS_META[l.lead_status] || LEAD_STATUS_META.new;
  return `<span class="status-badge" style="background:${meta.bg}; color:${meta.color};">${meta.label}</span>`;
}
function leadNextActionButton(l) {
  if (!l.next_action) return '';
  return `<button class="next-action-btn" onclick="event.stopPropagation(); openLeadDetailScreen(${l.id})" title="${l.next_action}">${l.next_action === 'Contact customer' ? 'CONTACT' : 'FOLLOW UP'}</button>`;
}

// Past Leads archive (confirmed Sept 2026, "Link Leads to Quotes +
// Past Leads archive" brief — Burgert: "Past leads or dead leads needs
// to be collapsable. I dont want a lead section that is over
// populated"). Converted and Lost dropped out of the main tab strip:
// they are terminal, they have no next action, and every one of them
// stays on this screen forever, so leaving them mixed into "All" is
// exactly what over-populates it. They live in their own collapsed
// section below the active table instead — nothing is deleted, nothing
// is hidden, it just isn't in the way.
const LEAD_ACTIVE_STATUSES = ['new', 'contacted', 'potential'];
const LEAD_TABS = ['all', 'new', 'contacted', 'potential'];
let leadsActiveTab = 'all';
let pastLeadsOpen = false;
let pastLeadsFilter = 'all';   // 'all' | 'converted' | 'lost'
let leadsCache = [];
// Assigned Leads, Stage 1 (confirmed Sept 2026) — "By Person" is a pure
// client-side regrouping of the SAME leadsCache the flat table already
// has (no second fetch) — Madri's own confirmed need to "track leads
// across the team." Off by default; the flat table stays the normal
// view for everyone else.
let leadsGroupByPerson = false;
const LEAD_ASSIGNEE_LABEL = { burgert: 'Burgert', ryno: 'Ryno', madri: 'Madri' };

function togglePastLeads() {
  pastLeadsOpen = !pastLeadsOpen;
  renderLeadsTable();
}

function setPastLeadsFilter(f) {
  pastLeadsFilter = f;
  pastLeadsOpen = true;
  renderLeadsTable();
}

function setLeadsTab(tab) {
  leadsActiveTab = tab;
  renderLeadsTable();
}

function toggleLeadsGroupByPerson() {
  leadsGroupByPerson = !leadsGroupByPerson;
  renderLeadsTable();
}

// Assigned Leads, Stage 1 (confirmed Sept 2026) — Madri's own confirmed
// "view + reassign" access: reassigning goes through the exact same
// generic PUT /leads/{id} every other lead-detail edit already uses
// (update_lead(), main.py already allows assigned_to through its own
// exclude-list) — not a new, narrower endpoint.
async function reassignLead(leadId, newAssignee, selectEl) {
  const res = await fetch(`${API}/leads/${leadId}`, {
    method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ assigned_to: newAssignee }),
  });
  if (!res.ok) { alert('Could not reassign this lead.'); return; }
  const lead = leadsCache.find(l => l.id === leadId);
  if (lead) lead.assigned_to = newAssignee;
  renderLeadsTable();
}

// Booking a lead onto the calendar (confirmed Sept 2026, Burgert:
// "Theres no way to book a date for anyone"). Inline on the row, saved
// on change — deliberately the same shape as the assignee dropdown
// right beside it, because these two fields together ARE the booking:
// a date, and whose day it lands in. Both were technically settable
// before this, but only by opening the lead and going through a
// whole-record edit form, which is why in practice nothing got booked.
function leadVisitDateHtml(l) {
  return `<input type="date" value="${l.visit_date || ''}"
    onclick="event.stopPropagation();"
    onchange="event.stopPropagation(); bookLeadVisit(${l.id}, this.value, this)"
    style="font-size:12px; padding:2px 4px;">`;
}

async function bookLeadVisit(leadId, visitDate, inputEl) {
  const params = new URLSearchParams({ visit_date: visitDate || '' });
  const res = await fetch(`${API}/leads/${leadId}/book-visit?${params}`, {method: 'POST'});
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    alert(body.detail || 'Could not book that date.');
    // Put the field back to what the server still holds, rather than
    // leaving a date on screen that was never saved.
    const lead = leadsCache.find(l => l.id === leadId);
    if (inputEl && lead) inputEl.value = lead.visit_date || '';
    return;
  }
  const saved = await res.json();
  const lead = leadsCache.find(l => l.id === leadId);
  if (lead) { lead.visit_date = saved.visit_date; lead.assigned_to = saved.assigned_to; }
  // Deliberately no full re-render: re-rendering the table on every
  // date change would close the Past Leads section and throw away the
  // search box, for a change already reflected in the input itself.
}

function leadAssigneeSelectHtml(l) {
  return `<select onclick="event.stopPropagation();" onchange="event.stopPropagation(); reassignLead(${l.id}, this.value, this)" style="font-size:12px; padding:2px 4px;">
    ${Object.keys(LEAD_ASSIGNEE_LABEL).map(k => `<option value="${k}" ${l.assigned_to===k?'selected':''}>${LEAD_ASSIGNEE_LABEL[k]}</option>`).join('')}
  </select>`;
}

async function renderLeads(el, searchTerm) {
  await renderWithRetry(el, 'Leads', async () => {
  el.innerHTML = `<span class="back-link" onclick="landingView='tiles'; renderLanding();">← Back</span><div class="card"><h2>Leads</h2><p class="muted">Loading...</p></div>`;
  const params = searchTerm ? `?search=${encodeURIComponent(searchTerm)}` : '';
  const res = await fetch(`${API}/leads${params}`);
  leadsCache = await res.json();
  renderLeadsTable(searchTerm);
  // Same "only steal focus on a genuine typed search, never on first
  // arrival" discipline as every other landing screen (Remove Unwanted
  // Auto-Focus brief) — this whole innerHTML is replaced on every
  // keystroke, so the restore is still needed while typing.
  const input = document.getElementById('leadSearchInput');
  if (input && searchTerm !== undefined) { input.focus(); input.setSelectionRange(input.value.length, input.value.length); }
  });
}

function leadRowHtml(l) {
  return `
    <tr style="cursor:pointer;" onclick="openLeadDetailScreen(${l.id})">
      <td class="card-title" data-label="Name">${l.name}</td>
      <td data-label="Contact">${l.contact || '—'}</td>
      <td data-label="Source">${l.source || '—'}</td>
      <td data-label="Assigned">${leadAssigneeSelectHtml(l)}</td>
      <td data-label="Visit">${leadVisitDateHtml(l)}</td>
      <td data-label="Status">${leadStatusBadge(l)}</td>
      <td data-label="Next Action">${leadNextActionButton(l)}
        <!-- "Mark as Lost/No Result" on any lead (confirmed Sept 2026)
        — one click from the list, so closing a dead enquiry costs less
        effort than leaving it to clutter the feed. Deliberately still
        goes through the same outcome-note prompt as every other status
        change (Proof-of-Work principle, proposal §02): a lead can be
        closed quickly, but never silently. -->
        <button onclick="event.stopPropagation(); markLeadLostFromList(${l.id}, '${l.name.replace(/'/g,"\'")}')" title="Mark as lost / no result" style="font-size:11px; padding:2px 6px; color:var(--coral); border-color:var(--coral);">Lost</button>
      </td>
    </tr>`;
}

// Past Leads (confirmed Sept 2026). Collapsed by default and rendered
// only when opened — an archive that grows forever must not cost
// anything to have on the page when nobody's looking at it. Filterable
// by outcome, per the brief, so "what did we win" and "what did we
// lose" are each one click.
function renderPastLeadsCard(pastLeads) {
  const converted = pastLeads.filter(l => l.lead_status === 'converted');
  const lost = pastLeads.filter(l => l.lead_status === 'lost');
  const shown = pastLeadsFilter === 'converted' ? converted
              : pastLeadsFilter === 'lost' ? lost
              : pastLeads;
  // Newest outcome first — an archive is read from the most recent end,
  // unlike the active feed which is sorted by urgency.
  const ordered = shown.slice().sort((a, b) => String(b.last_outcome_at || '').localeCompare(String(a.last_outcome_at || '')));
  const fmt = (v) => v ? new Date(v).toLocaleDateString('en-ZA', {dateStyle: 'medium'}) : '—';
  const pill = (key, label, count) => `<button onclick="setPastLeadsFilter('${key}')" style="${pastLeadsFilter===key ? 'background:var(--teal); color:white; border-color:var(--teal);' : ''}">${label} (${count})</button>`;
  return `
    <div class="card">
      <div onclick="togglePastLeads()" style="cursor:pointer; display:flex; align-items:center; gap:8px;">
        <span style="font-size:13px;">${pastLeadsOpen ? '▾' : '▸'}</span>
        <h2 style="margin:0;">Past Leads (${pastLeads.length})</h2>
      </div>
      <p class="muted" style="margin:4px 0 0;">Closed enquiries — converted to a real quote, or lost. Kept permanently; nothing is ever deleted.</p>
      ${!pastLeadsOpen ? '' : `
        <div style="display:flex; gap:6px; flex-wrap:wrap; margin:12px 0;">
          ${pill('all', 'All', pastLeads.length)}${pill('converted', 'Converted', converted.length)}${pill('lost', 'Lost / No result', lost.length)}
        </div>
        <table class="mobile-card-table">
          <thead><tr><th>Name</th><th>Contact</th><th>Source</th><th>Outcome</th><th>Became</th><th>Closed</th></tr></thead>
          <tbody>
            ${ordered.length ? ordered.map(l => `
              <tr onclick="openLeadDetailScreen(${l.id})" style="cursor:pointer;">
                <td data-label="Name"><b>${l.name}</b></td>
                <td data-label="Contact">${l.contact || '—'}</td>
                <td data-label="Source">${l.source || '—'}</td>
                <td data-label="Outcome">${leadStatusBadge(l)}</td>
                <td data-label="Became">${l.converted_quote
                  ? `<a href="#" onclick="event.stopPropagation(); openQuoteFromIndex(${l.converted_quote.quote_id}); return false;" style="color:var(--teal); font-weight:600;">${l.converted_quote.job_number || '#' + l.converted_quote.quote_id}</a>`
                  : (l.lead_status === 'converted' ? '<span class="muted">quote since deleted</span>' : '<span class="muted">—</span>')}</td>
                <td data-label="Closed">${fmt(l.last_outcome_at)}</td>
              </tr>`).join('') : '<tr><td colspan="6" class="muted">Nothing here yet.</td></tr>'}
          </tbody>
        </table>`}
    </div>`;
}

function renderLeadsTable(searchTerm) {
  if (searchTerm === undefined) {
    const existingInput = document.getElementById('leadSearchInput');
    searchTerm = existingInput ? existingInput.value : '';
  }
  const el = document.getElementById('landing');
  // The active feed is the working list; anything terminal has moved to
  // the archive below. A lead linked to a quote therefore disappears
  // from here the moment it's linked, which is the brief's own "the
  // lead is automatically cleared from the active leads feed".
  const leads = leadsCache.filter(l => LEAD_ACTIVE_STATUSES.includes(l.lead_status));
  const pastLeads = leadsCache.filter(l => !LEAD_ACTIVE_STATUSES.includes(l.lead_status));
  const counts = {new: 0, contacted: 0, potential: 0};
  leads.forEach(l => { if (counts[l.lead_status] !== undefined) counts[l.lead_status]++; });
  const shown = leadsActiveTab === 'all' ? leads : leads.filter(l => l.lead_status === leadsActiveTab);

  // Assigned Leads, Stage 1 (confirmed Sept 2026) — "By Person" groups
  // the exact same `shown` rows (still respects whatever status tab is
  // active) under a header per assignee, urgent-first within each
  // group — same priority_order the backend already sorted the flat
  // list by (list_leads(), main.py), just partitioned visually.
  let bodyHtml;
  if (leadsGroupByPerson) {
    const byAssignee = {};
    shown.forEach(l => { const key = l.assigned_to || '(unassigned)'; (byAssignee[key] = byAssignee[key] || []).push(l); });
    const names = Object.keys(byAssignee).sort();
    bodyHtml = names.length ? names.map(key => `
      <tr><td colspan="7" style="background:var(--bg,#f5f6f8); font-weight:700; padding:8px 10px;">${LEAD_ASSIGNEE_LABEL[key] || key} (${byAssignee[key].length})</td></tr>
      ${byAssignee[key].map(leadRowHtml).join('')}
    `).join('') : '<tr><td colspan="7" class="muted">No leads match.</td></tr>';
  } else {
    bodyHtml = shown.length ? shown.map(leadRowHtml).join('') : '<tr><td colspan="7" class="muted">No leads match.</td></tr>';
  }

  const tab = (key, label, count) => `<button onclick="setLeadsTab('${key}')" style="${leadsActiveTab===key ? 'background:var(--teal); color:white; border-color:var(--teal);' : ''}">${label}${count !== undefined ? ` (${count})` : ''}</button>`;

  el.innerHTML = `
    <span class="back-link" onclick="landingView='tiles'; renderLanding();">← Back</span>
    <div class="landing-welcome">
      <h1>Leads</h1>
      <p>New enquiries, from first contact through to a real quote — before a job exists at all.</p>
    </div>
    <div class="card">
      <div style="display:flex; justify-content:space-between; align-items:flex-start; gap:10px; flex-wrap:wrap;">
        <div class="field" style="flex:1; min-width:200px;"><label>Search</label><input type="text" id="leadSearchInput" value="${searchTerm || ''}" placeholder="Name or contact..." oninput="renderLeads(document.getElementById('landing'), this.value)"></div>
        <a href="#" onclick="openLeadsDayList(); return false;" style="margin-top:22px; font-size:12px; color:var(--teal); font-weight:600; white-space:nowrap;">📋 Today's Leads (printable)</a>
      </div>
      <div style="display:flex; justify-content:space-between; align-items:center; gap:6px; flex-wrap:wrap; margin-bottom:14px;">
        <div style="display:flex; gap:6px; flex-wrap:wrap;">
          ${tab('all', 'All', leads.length)}${tab('new', 'New', counts.new)}${tab('contacted', 'Contacted', counts.contacted)}${tab('potential', 'Potential', counts.potential)}
        </div>
        <!-- Assigned Leads, Stage 1 (confirmed Sept 2026) — Madri's own
        confirmed "track leads across the team" need. -->
        <button onclick="toggleLeadsGroupByPerson()" style="${leadsGroupByPerson ? 'background:var(--teal); color:white; border-color:var(--teal);' : ''}">By Person</button>
      </div>
      <table class="mobile-card-table"><thead><tr><th>Name</th><th>Contact</th><th>Source</th><th>Assigned</th><th>Visit date</th><th>Status</th><th>Next Action</th></tr></thead>
      <tbody>${bodyHtml}</tbody></table>
    </div>
    ${renderPastLeadsCard(pastLeads)}
    <div class="card">
      <h2>New Lead</h2>
      <p class="muted" style="margin-top:-8px;">Often just a name and a phone number — that's enough to start.</p>
      <div class="grid">
        <div class="field"><label>Name</label><input id="ld_name" placeholder="Enquiry's name"></div>
        <div class="field"><label>Contact <span class="adj">(phone or email)</span></label><input id="ld_contact" placeholder="082 555 1234"></div>
        <div class="field"><label>Source <span class="adj">(how did they hear about us?)</span></label>
          <select id="ld_source">
            <option value="">—</option>
            <option value="Referral">Referral</option>
            <option value="Walk-in">Walk-in</option>
            <option value="Google/Online search">Google/Online search</option>
            <option value="Social media">Social media</option>
            <option value="Signage">Signage</option>
            <option value="Builder referral">Builder referral</option>
            <option value="Repeat client">Repeat client</option>
            <option value="Other">Other</option>
          </select>
        </div>
        <!-- Assigned Leads, Stage 1 (confirmed Sept 2026) -- who owns
        following this up, defaults to whoever's creating it (same
        STAFF_DEFAULT_OWNER-style three real staff options the Sales
        Owner picker already uses, shared.js) -- explicit hand-off at
        creation is allowed (e.g. Burgert logging a lead straight onto
        Ryno), just never silent. -->
        <div class="field"><label>Assigned to</label>
          <select id="ld_assigned_to">
            <option value="burgert" ${effectiveUsernameForQuoting()==='burgert'?'selected':''}>Burgert</option>
            <option value="ryno" ${effectiveUsernameForQuoting()==='ryno'?'selected':''}>Ryno</option>
            <option value="madri" ${effectiveUsernameForQuoting()==='madri'?'selected':''}>Madri</option>
          </select>
        </div>
        <!-- Lead: Visit Date, Address Fields & Printable Day List (confirmed
        Aug 2026) -- a scheduling detail, independent of lead_status; a lead
        can have a visit proposed while sitting in any status. -->
        <div class="field"><label>Site visit date <span class="adj">(optional)</span></label><input id="ld_visit_date" type="date"></div>
        <div class="field"><label>Site address <span class="adj">(optional — where the visit would happen)</span></label><input id="ld_site_address" placeholder="Street address"></div>
        <div class="field" style="grid-column: span 2;"><label>Notes <span class="adj">(optional)</span></label><textarea id="ld_notes" rows="2" style="width:100%; font-family:inherit; font-size:14px; padding:8px; border:1px solid var(--border); border-radius:6px;" placeholder="What are they after?"></textarea></div>
      </div>
      <br><button class="primary" id="addLeadBtn" onclick="addLead()">Add Lead</button>
      <p class="muted" id="addLeadStatus" style="margin-top:8px;"></p>
    </div>
  `;
}

async function addLead() {
  const btn = document.getElementById('addLeadBtn');
  const statusEl = document.getElementById('addLeadStatus');
  const body = {
    name: document.getElementById('ld_name').value,
    contact: document.getElementById('ld_contact').value,
    source: document.getElementById('ld_source').value,
    assigned_to: document.getElementById('ld_assigned_to').value,
    visit_date: document.getElementById('ld_visit_date').value || null,
    site_address: document.getElementById('ld_site_address').value,
    notes: document.getElementById('ld_notes').value,
  };
  if (!body.name.trim()) { alert('A name is required.'); return; }
  // Double-submit guard — same reasoning as addClient() (Client-Side
  // Commercial Workflow brief, Sprint A).
  if (btn) { btn.disabled = true; btn.textContent = 'Adding...'; }
  try {
    const res = await fetch(`${API}/leads`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
    if (!res.ok) throw new Error('save failed');
    const lead = await res.json();
    if (statusEl) statusEl.textContent = `✓ ${body.name} added.`;
    openLeadDetailScreen(lead.id);
  } catch (e) {
    if (statusEl) statusEl.textContent = '❌ Could not save — check your connection and try again.';
    if (btn) { btn.disabled = false; btn.textContent = 'Add Lead'; }
  }
}

let currentLeadDetailId = null;
let currentLeadDetail = null;   // the actually-loaded record for THIS detail screen — real bug avoided here: leadsCache (the list screen's cache) is empty/stale whenever this screen is reached directly (e.g. straight after addLead()), so showEditLeadForm() below reads this instead, never leadsCache
function openLeadDetailScreen(leadId) {
  currentLeadDetailId = leadId;
  landingView = 'leadDetail';
  renderLanding();
}

// The booking control on the lead itself (confirmed Sept 2026) — a
// real, labelled "Site visit" block rather than a read-only line that
// only appeared once a date somehow existed. Date and person together,
// because booking a visit without saying whose day it lands in is what
// "no way to book a date for anyone" was actually describing.
function leadVisitBookingHtml(l, terminal) {
  if (terminal) {
    return l.visit_date ? `<p class="muted" style="margin:4px 0;">📅 Site visit was booked for ${dateOrDash(l.visit_date)}</p>` : '';
  }
  return `
    <div style="margin:10px 0; padding:10px; background:var(--bg,#f5f6f8); border-radius:8px;">
      <p style="margin:0 0 6px; font-weight:700;">📅 Site visit</p>
      <div style="display:flex; gap:8px; align-items:center; flex-wrap:wrap;">
        <input type="date" id="leadVisitDate" value="${l.visit_date || ''}">
        <select id="leadVisitOwner">
          ${Object.keys(LEAD_ASSIGNEE_LABEL).map(k => `<option value="${k}" ${l.assigned_to===k?'selected':''}>${LEAD_ASSIGNEE_LABEL[k]}</option>`).join('')}
        </select>
        <button class="primary" onclick="bookLeadVisitFromDetail(${l.id})">${l.visit_date ? 'Update booking' : 'Book visit'}</button>
        ${l.visit_date ? `<button class="secondary" onclick="clearLeadVisit(${l.id})">Clear</button>` : ''}
      </div>
      <p class="muted" style="margin:6px 0 0; font-size:12px;">${l.visit_date
        ? 'Booked — this shows on the Installation Calendar for whoever it is assigned to.'
        : 'Pick a date and who is going; it appears on the Installation Calendar straight away.'}</p>
    </div>`;
}

async function bookLeadVisitFromDetail(leadId) {
  const visitDate = document.getElementById('leadVisitDate').value;
  const owner = document.getElementById('leadVisitOwner').value;
  if (!visitDate) { alert('Pick a date first.'); return; }
  const params = new URLSearchParams({visit_date: visitDate, assigned_to: owner});
  const res = await fetch(`${API}/leads/${leadId}/book-visit?${params}`, {method: 'POST'});
  if (!res.ok) { const b = await res.json().catch(() => ({})); alert(b.detail || 'Could not book that visit.'); return; }
  renderLeadDetail(document.getElementById('landing'));
}

async function clearLeadVisit(leadId) {
  if (!confirm('Clear this booked visit? It comes off the calendar.')) return;
  const res = await fetch(`${API}/leads/${leadId}/book-visit?visit_date=`, {method: 'POST'});
  if (!res.ok) { const b = await res.json().catch(() => ({})); alert(b.detail || 'Could not clear that visit.'); return; }
  renderLeadDetail(document.getElementById('landing'));
}

async function renderLeadDetail(el) {
  await renderWithRetry(el, 'Lead', async () => {
  el.innerHTML = `<span class="back-link" onclick="landingView='leads'; renderLanding();">← Back to Leads</span><div class="card"><p class="muted">Loading...</p></div>`;
  const res = await fetch(`${API}/leads/${currentLeadDetailId}`);
  if (!res.ok) { el.innerHTML = `<span class="back-link" onclick="landingView='leads'; renderLanding();">← Back to Leads</span><div class="card"><p class="muted">This lead couldn't be found.</p></div>`; return; }
  const l = await res.json();
  currentLeadDetail = l;
  setPageTitle(`Lead: ${l.name}`);

  const terminal = l.lead_status === 'converted' || l.lead_status === 'lost';
  const nextActionHtml = l.next_action ? `<p style="margin:8px 0 0; font-weight:700; color:var(--coral);">→ ${l.next_action}</p>` : '';

  // Outcome-note actions (Proof-of-Work principle, proposal §02) — a
  // real prompt() for the note, same minimal-effort pattern as the
  // existing "Put job on hold…" action (order-index.js's holdJobAction())
  // — "less effort than a WhatsApp, not more."
  const actionsHtml = terminal ? '' : `
    <div style="display:flex; gap:8px; flex-wrap:wrap; margin-top:14px;">
      ${l.lead_status === 'new' ? `<button class="primary" onclick="changeLeadStatusAction(${l.id}, 'contacted')">Mark Contacted</button>` : ''}
      ${l.lead_status !== 'potential' ? `<button onclick="changeLeadStatusAction(${l.id}, 'potential')">Mark Potential</button>` : ''}
      <button onclick="convertLeadAction(${l.id})">Convert to New Quote</button>
      <!-- Link to Quote (confirmed Sept 2026, "Link Leads to Quotes +
      Past Leads archive" brief). Sits beside Convert deliberately: they
      close a lead the same way but do genuinely different things —
      Convert CREATES a quote, this ATTACHES the one already raised in
      the Order Index. Without it, closing a lead whose quote already
      existed meant converting anyway and ending up with a duplicate. -->
      <button onclick="openLinkQuotePicker(${l.id})">Link to Existing Quote</button>
      <button style="color:var(--coral); border-color:var(--coral);" onclick="changeLeadStatusAction(${l.id}, 'lost')">Mark Lost / No Result</button>
    </div>
    <div id="linkQuotePicker"></div>`;

  // Past Leads archive (confirmed Sept 2026) — a closed lead has to say
  // what became of it, on the record itself and not only in the archive
  // table, so opening one from anywhere tells the whole story.
  const convertedHtml = l.converted_quote_id
    ? `<p style="margin:8px 0 0;"><a href="#" onclick="openQuoteFromIndex(${l.converted_quote_id}); return false;">→ Open Quote #${l.converted_quote_id}</a></p>`
    : (l.lead_status === 'lost'
        ? '<p class="muted" style="margin:8px 0 0;">Closed as lost / no result. Kept here permanently — see the history below for why.</p>'
        : '');

  // Activity history (confirmed Aug 2026, Proof-of-Work principle §02)
  // — the real AuditLog trail IS the outcome-note record, "no new
  // mechanism for who/when": read straight back here, not a separate
  // notes table.
  const historyHtml = (l.history || []).length ? l.history.map(h => `
    <div style="padding:8px 0; border-bottom:1px solid var(--border);">
      <p style="margin:0; font-size:13px;">${(h.new_value || '').replace(/</g,'&lt;')}</p>
      <p class="muted" style="margin:2px 0 0; font-size:11.5px;">${h.username} · ${new Date(h.timestamp + 'Z').toLocaleString()}</p>
    </div>`).join('') : '<p class="muted" style="margin:0;">No status changes logged yet.</p>';

  el.innerHTML = `
    <span class="back-link" onclick="landingView='leads'; renderLanding();">← Back to Leads</span>
    <div class="card">
      <h2>${l.name} ${leadStatusBadge(l)}</h2>
      <p style="margin:4px 0;">${l.contact || '<span class="muted">No contact on file</span>'}${l.source ? ' · ' + l.source : ''}</p>
      ${leadVisitBookingHtml(l, terminal)}
      ${l.site_address ? `<p style="margin:4px 0;">📍 ${l.site_address.replace(/</g,'&lt;')}</p>` : ''}
      ${l.notes ? `<p class="muted" style="margin:4px 0;">${l.notes.replace(/</g,'&lt;')}</p>` : ''}
      ${nextActionHtml}
      ${convertedHtml}
      ${actionsHtml}
      ${!terminal ? `<p style="margin-top:14px;"><a href="#" onclick="showEditLeadForm(${l.id}); return false;" style="font-size:12px; color:var(--teal); font-weight:600;">Edit details</a></p>` : ''}
      <!-- Delete Lead (confirmed Sep 2026, Burgert's own words: "I also
      need to be able to delete leads") — Owner-only, matching
      delete_lead()'s own require_owner gate (main.py); a converted
      lead can still be deleted by role, but that endpoint blocks it
      with a clear reason, same as clicking through and finding out
      rather than hiding the option and looking broken. -->
      ${realRole() === 'owner' ? `<p style="margin-top:6px;"><a href="#" onclick="deleteLeadAction(${l.id}, '${l.name.replace(/'/g,"\\'")}'); return false;" style="font-size:12px; color:var(--coral); font-weight:600;">Delete lead</a></p>` : ''}
    </div>
    <div class="card">
      <h2>Activity</h2>
      ${historyHtml}
    </div>
    <div id="editLeadCard"></div>
  `;
  });
}

// Link to Quote picker (confirmed Sept 2026). Renders inline under the
// action row rather than as a prompt(): unlike an outcome note, this is
// a CHOICE between real records, and typing an id from memory is exactly
// the free-text guessing the brief rules out.
async function openLinkQuotePicker(leadId) {
  const box = document.getElementById('linkQuotePicker');
  if (!box) return;
  if (box.dataset.open === String(leadId)) { box.dataset.open = ''; box.innerHTML = ''; return; }
  box.dataset.open = String(leadId);
  box.innerHTML = '<p class="muted">Loading quotes...</p>';
  const res = await fetch(`${API}/leads/${leadId}/linkable-quotes`);
  if (!res.ok) { box.innerHTML = '<p class="error">Could not load quotes — try again.</p>'; return; }
  const d = await res.json();
  const fmt = (iso) => iso ? new Date(iso).toLocaleDateString('en-ZA', {dateStyle: 'medium'}) : '';
  const row = (q) => `
    <div style="display:flex; justify-content:space-between; align-items:center; gap:10px; padding:8px 0; border-bottom:1px solid var(--border);">
      <div style="min-width:0;">
        <b>${q.job_number || '#' + q.id}</b> — ${q.client_name}
        ${q.description ? `<br><span class="muted" style="font-size:12px;">${q.description}</span>` : ''}
        <br><span class="muted" style="font-size:11.5px;">${q.workflow_status || ''} · ${fmt(q.created_at)}</span>
        ${q.already_linked_to ? `<br><span style="color:var(--coral); font-size:11.5px;">Already linked to the lead "${q.already_linked_to}"</span>` : ''}
      </div>
      <button onclick="linkLeadToQuoteAction(${leadId}, ${q.id}, '${q.client_name.replace(/'/g,"\\'")}')">Link</button>
    </div>`;
  box.innerHTML = `
    <div class="card" style="margin-top:12px;">
      <h3 style="margin-top:0;">Link "${d.lead_name}" to an existing quote</h3>
      ${d.matched.length ? `
        <p class="muted" style="margin-top:0;">Quotes for this same client:</p>
        ${d.matched.map(row).join('')}` : `
        <p class="muted" style="margin-top:0;">No quote is on file under this exact client name yet. If the quote was raised under a slightly different name, pick it from the recent list below — otherwise use "Convert to New Quote" instead.</p>`}
      <p class="muted" style="margin:14px 0 0;">Recent quotes${d.matched.length ? ' (other clients)' : ''}:</p>
      ${d.other_recent.length ? d.other_recent.map(row).join('') : '<p class="muted">No other quotes on file.</p>'}
      <button class="secondary" style="margin-top:10px;" onclick="openLinkQuotePicker(${leadId})">Cancel</button>
    </div>`;
}

async function linkLeadToQuoteAction(leadId, quoteId, clientName) {
  if (!confirm(`Link this lead to the quote for ${clientName}?\n\nThe lead moves to Past Leads as Converted, with this quote on its record.`)) return;
  let res = await fetch(`${API}/leads/${leadId}/link-quote?quote_id=${quoteId}`, {method: 'POST'});
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    // The backend refuses a quote another lead already claims — a real
    // mis-click guard, overridable when two enquiries genuinely were
    // for the same job.
    if (!confirm(`${body.detail || 'Could not link this quote.'}\n\nLink it anyway?`)) return;
    res = await fetch(`${API}/leads/${leadId}/link-quote?quote_id=${quoteId}&force=true`, {method: 'POST'});
    if (!res.ok) { alert('Could not link this quote.'); return; }
  }
  renderLanding();
}

async function changeLeadStatusAction(leadId, newStatus) {
  // "Lost / No result" (confirmed Sept 2026) — same wording the button
  // and the Past Leads filter use, so one outcome isn't called three
  // different things across three screens.
  const label = {contacted: 'Contacted', potential: 'Potential', lost: 'Lost / No result'}[newStatus];
  const note = prompt(`What happened? One line — e.g. "Called 14:20, wants a site visit Thursday".\n\nMarking as: ${label}`);
  if (!note || !note.trim()) return;
  const res = await fetch(`${API}/leads/${leadId}/status`, {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({new_status: newStatus, note: note.trim()}),
  });
  if (!res.ok) { const body = await res.json().catch(() => ({})); alert(body.detail || 'Could not update this lead.'); return; }
  renderLeadDetail(document.getElementById('landing'));
}

// "Lost / No result" straight off a list row (confirmed Sept 2026, the
// brief's "Add a 'Mark as Lost/No Result' action on any lead"). Shares
// changeLeadStatusAction()'s outcome-note prompt and its endpoint, then
// re-renders whichever list the click came from rather than dragging
// the user onto the detail screen for a lead they've just closed.
async function markLeadLostFromList(leadId, name) {
  const note = prompt(`What happened? One line — e.g. "Went with another supplier".

Marking "${name}" as: Lost / No result`);
  if (!note || !note.trim()) return;
  const res = await fetch(`${API}/leads/${leadId}/status`, {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({new_status: 'lost', note: note.trim()}),
  });
  if (!res.ok) { const body = await res.json().catch(() => ({})); alert(body.detail || 'Could not update this lead.'); return; }
  renderLeads(document.getElementById('landing'));
}

async function convertLeadAction(leadId) {
  if (!confirm('Convert this lead to a real Quote? This creates a new Quote and marks the lead Converted — it can\'t be undone from here.')) return;
  const salesOwner = currentUser?.username && ['burgert', 'ryno', 'madri'].includes(currentUser.username) ? currentUser.username : 'burgert';
  const branch = defaultBranchForCurrentUser();
  const res = await fetch(`${API}/leads/${leadId}/convert?sales_owner=${encodeURIComponent(salesOwner)}&branch=${encodeURIComponent(branch)}`, {method: 'POST'});
  if (!res.ok) { const body = await res.json().catch(() => ({})); alert(body.detail || 'Could not convert this lead.'); return; }
  const data = await res.json();
  await openQuoteFromIndex(data.quote.id);
}

async function deleteLeadAction(leadId, name) {
  if (!confirm(`Delete the lead "${name}"? This can't be undone.`)) return;
  const res = await fetch(`${API}/leads/${leadId}`, { method: 'DELETE' });
  if (!res.ok) { const body = await res.json().catch(() => ({})); alert(body.detail || 'Could not delete this lead.'); return; }
  landingView = 'leads';
  renderLanding();
}

function showEditLeadForm(leadId) {
  const l = currentLeadDetail && currentLeadDetail.id === leadId ? currentLeadDetail : null;
  const card = document.getElementById('editLeadCard');
  if (!card) return;
  card.innerHTML = `
    <div class="card">
      <h2>Edit Lead</h2>
      <div class="grid">
        <div class="field"><label>Name</label><input id="ld_edit_name" value="${(l?.name || '').replace(/"/g,'&quot;')}"></div>
        <div class="field"><label>Contact</label><input id="ld_edit_contact" value="${(l?.contact || '').replace(/"/g,'&quot;')}"></div>
        <!-- Editable Source (confirmed Sept 2026, Burgert's own real
        gap: "change the where did they hear about us after we saved
        the lead" — the New Lead form already asked this, but the Edit
        Lead form here never carried it, so it was locked in forever at
        creation, often before it was even known for sure. Same select
        options as the New Lead form (renderLeadsTable() above), not a
        second, differently-worded list that could drift. -->
        <div class="field"><label>Source <span class="adj">(how did they hear about us?)</span></label>
          <select id="ld_edit_source">
            <option value="" ${!l?.source?'selected':''}>—</option>
            <option value="Referral" ${l?.source==='Referral'?'selected':''}>Referral</option>
            <option value="Walk-in" ${l?.source==='Walk-in'?'selected':''}>Walk-in</option>
            <option value="Google/Online search" ${l?.source==='Google/Online search'?'selected':''}>Google/Online search</option>
            <option value="Social media" ${l?.source==='Social media'?'selected':''}>Social media</option>
            <option value="Signage" ${l?.source==='Signage'?'selected':''}>Signage</option>
            <option value="Builder referral" ${l?.source==='Builder referral'?'selected':''}>Builder referral</option>
            <option value="Repeat client" ${l?.source==='Repeat client'?'selected':''}>Repeat client</option>
            <option value="Other" ${l?.source==='Other'?'selected':''}>Other</option>
          </select>
        </div>
        <div class="field"><label>Site visit date</label><input id="ld_edit_visit_date" type="date" value="${l?.visit_date || ''}"></div>
        <div class="field"><label>Site address</label><input id="ld_edit_site_address" value="${(l?.site_address || '').replace(/"/g,'&quot;')}"></div>
        <div class="field" style="grid-column: span 2;"><label>Notes</label><textarea id="ld_edit_notes" rows="2" style="width:100%; font-family:inherit; font-size:14px; padding:8px; border:1px solid var(--border); border-radius:6px;">${(l?.notes || '').replace(/</g,'&lt;')}</textarea></div>
      </div>
      <br><button class="primary" onclick="saveLeadEdit(${leadId})">Save</button>
      <button onclick="document.getElementById('editLeadCard').innerHTML='';">Cancel</button>
    </div>`;
}

// Printable "Today's Leads" day list (confirmed Aug 2026, Lead: Visit
// Date, Address Fields & Printable Day List brief §3) — an explicit
// stepping stone toward a future calendar visualizer per the brief's
// own words: this screen is deliberately simple and fine to replace
// later, but reuses the shared triggerPrint() mechanism (Document
// Action Bar's own Print behaviour) rather than a new print mechanism,
// and reads from the backend's own reusable _leads_for_day() query
// (main.py) rather than filtering leadsCache client-side — the real
// query needs to survive unchanged even when this screen doesn't.
let leadsDayListDate = null;
let leadsDayListCache = [];   // last-fetched day's leads — the Print button reads from here rather than re-serializing data into an onclick attribute (a name/note containing a quote or apostrophe would break that), same "cache the fetch, don't re-embed it in markup" convention as leadsCache/orderIndexQuotesCache elsewhere in this codebase
function openLeadsDayList() {
  leadsDayListDate = new Date().toISOString().slice(0, 10);   // today, local ISO date — matches the <input type="date"> value shape
  landingView = 'leadsDayList';
  renderLanding();
}

async function renderLeadsDayList(el) {
  await renderWithRetry(el, "Today's Leads", async () => {
  el.innerHTML = `<span class="back-link" onclick="landingView='leads'; renderLanding();">← Back to Leads</span><div class="card"><p class="muted">Loading...</p></div>`;
  const res = await fetch(`${API}/leads/day-list?day=${leadsDayListDate}`);
  const data = await res.json();
  const leads = data.leads;
  leadsDayListCache = leads;

  const rows = leads.length ? leads.map(l => `
    <tr>
      <td data-label="Name"><b>${l.name}</b>${l.contact ? `<br><span class="muted" style="font-size:11px;">${l.contact}</span>` : ''}</td>
      <td data-label="Address">${l.site_address || '—'}</td>
      <td data-label="What's needed">${(l.notes || '—').replace(/</g,'&lt;')}${l.next_action ? `<br><span style="font-weight:600; color:var(--coral);">→ ${l.next_action}</span>` : ''}</td>
      <td data-label="Outcome notes logged">${(l.history || []).length ? l.history.map(h => `<div style="margin-bottom:4px;">${(h.new_value||'').replace(/</g,'&lt;')}</div>`).join('') : '<span class="muted">—</span>'}</td>
    </tr>`).join('') : `<tr><td colspan="4" class="muted">No leads have a site visit on this day.</td></tr>`;

  el.innerHTML = `
    <span class="back-link" onclick="landingView='leads'; renderLanding();">← Back to Leads</span>
    <div class="card">
      <h2>Today's Leads</h2>
      <p class="muted" style="margin-top:-8px;">Leads with a site visit on the chosen day — a scannable to-do list, not the full Leads table.</p>
      <div style="display:flex; gap:10px; align-items:flex-end; flex-wrap:wrap; margin-bottom:14px;">
        <div class="field" style="margin:0;"><label>Day</label><input type="date" id="leadsDayListInput" value="${leadsDayListDate}" onchange="leadsDayListDate=this.value; renderLeadsDayList(document.getElementById('landing'));"></div>
        <button onclick="printLeadsDayList('${data.day}')">🖨 Print</button>
      </div>
      <table class="mobile-card-table"><thead><tr><th>Name</th><th>Address</th><th>What's needed</th><th>Outcome notes logged</th></tr></thead>
      <tbody>${rows}</tbody></table>
    </div>
  `;
  });
}

function printLeadsDayList(day) {
  const leads = leadsDayListCache;
  const rows = leads.length ? leads.map(l => `
    <tr>
      <td><b>${l.name}</b>${l.contact ? `<br><span style="font-size:10px; color:#6b7280;">${l.contact}</span>` : ''}</td>
      <td>${l.site_address || '—'}</td>
      <td>${(l.notes || '—').replace(/</g,'&lt;')}${l.next_action ? `<br><b>→ ${l.next_action}</b>` : ''}</td>
      <td>${(l.history || []).length ? l.history.map(h => `<div>${(h.new_value||'').replace(/</g,'&lt;')}</div>`).join('') : '—'}</td>
    </tr>`).join('') : `<tr><td colspan="4">No leads have a site visit on this day.</td></tr>`;
  triggerPrint(`
    <div class="print-doc">
      <div class="doc-header">
        <img src="${document.querySelector('header .logo-row img').src}" style="height:36px;">
        <div class="doc-title">TODAY'S LEADS — ${day}</div>
      </div>
      <table>
        <thead><tr><th>Name</th><th>Address</th><th>What's needed</th><th>Outcome notes logged</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
  `);
}

async function saveLeadEdit(leadId) {
  const body = {
    name: document.getElementById('ld_edit_name').value,
    contact: document.getElementById('ld_edit_contact').value,
    source: document.getElementById('ld_edit_source').value,
    visit_date: document.getElementById('ld_edit_visit_date').value || null,
    site_address: document.getElementById('ld_edit_site_address').value,
    notes: document.getElementById('ld_edit_notes').value,
  };
  if (!body.name.trim()) { alert('A name is required.'); return; }
  const res = await fetch(`${API}/leads/${leadId}`, {method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
  if (!res.ok) { alert('Could not save changes.'); return; }
  renderLeadDetail(document.getElementById('landing'));
}
