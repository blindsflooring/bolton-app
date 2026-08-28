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

const LEAD_TABS = ['all', 'new', 'contacted', 'potential', 'converted', 'lost'];
let leadsActiveTab = 'all';
let leadsCache = [];

function setLeadsTab(tab) {
  leadsActiveTab = tab;
  renderLeadsTable();
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

function renderLeadsTable(searchTerm) {
  if (searchTerm === undefined) {
    const existingInput = document.getElementById('leadSearchInput');
    searchTerm = existingInput ? existingInput.value : '';
  }
  const el = document.getElementById('landing');
  const leads = leadsCache;
  const counts = {new: 0, contacted: 0, potential: 0, converted: 0, lost: 0};
  leads.forEach(l => { if (counts[l.lead_status] !== undefined) counts[l.lead_status]++; });
  const shown = leadsActiveTab === 'all' ? leads : leads.filter(l => l.lead_status === leadsActiveTab);

  const rows = shown.length ? shown.map(l => `
    <tr style="cursor:pointer;" onclick="openLeadDetailScreen(${l.id})">
      <td class="card-title" data-label="Name">${l.name}</td>
      <td data-label="Contact">${l.contact || '—'}</td>
      <td data-label="Source">${l.source || '—'}</td>
      <td data-label="Status">${leadStatusBadge(l)}</td>
      <td data-label="Next Action">${leadNextActionButton(l)}</td>
    </tr>`).join('') : '<tr><td colspan="5" class="muted">No leads match.</td></tr>';

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
      <div style="display:flex; gap:6px; flex-wrap:wrap; margin-bottom:14px;">
        ${tab('all', 'All', leads.length)}${tab('new', 'New', counts.new)}${tab('contacted', 'Contacted', counts.contacted)}${tab('potential', 'Potential', counts.potential)}${tab('converted', 'Converted', counts.converted)}${tab('lost', 'Lost', counts.lost)}
      </div>
      <table class="mobile-card-table"><thead><tr><th>Name</th><th>Contact</th><th>Source</th><th>Status</th><th>Next Action</th></tr></thead>
      <tbody>${rows}</tbody></table>
    </div>
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
      <button onclick="convertLeadAction(${l.id})">Convert to Quote</button>
      <button style="color:var(--coral); border-color:var(--coral);" onclick="changeLeadStatusAction(${l.id}, 'lost')">Mark Lost</button>
    </div>`;

  const convertedHtml = l.converted_quote_id ? `<p style="margin:8px 0 0;"><a href="#" onclick="openQuoteFromIndex(${l.converted_quote_id}); return false;">→ Open Quote #${l.converted_quote_id}</a></p>` : '';

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
      ${l.visit_date ? `<p style="margin:4px 0;">📅 Site visit: <b>${dateOrDash(l.visit_date)}</b></p>` : ''}
      ${l.site_address ? `<p style="margin:4px 0;">📍 ${l.site_address.replace(/</g,'&lt;')}</p>` : ''}
      ${l.notes ? `<p class="muted" style="margin:4px 0;">${l.notes.replace(/</g,'&lt;')}</p>` : ''}
      ${nextActionHtml}
      ${convertedHtml}
      ${actionsHtml}
      ${!terminal ? `<p style="margin-top:14px;"><a href="#" onclick="showEditLeadForm(${l.id}); return false;" style="font-size:12px; color:var(--teal); font-weight:600;">Edit details</a></p>` : ''}
    </div>
    <div class="card">
      <h2>Activity</h2>
      ${historyHtml}
    </div>
    <div id="editLeadCard"></div>
  `;
  });
}

async function changeLeadStatusAction(leadId, newStatus) {
  const label = {contacted: 'Contacted', potential: 'Potential', lost: 'Lost'}[newStatus];
  const note = prompt(`What happened? One line — e.g. "Called 14:20, wants a site visit Thursday".\n\nMarking as: ${label}`);
  if (!note || !note.trim()) return;
  const res = await fetch(`${API}/leads/${leadId}/status`, {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({new_status: newStatus, note: note.trim()}),
  });
  if (!res.ok) { const body = await res.json().catch(() => ({})); alert(body.detail || 'Could not update this lead.'); return; }
  renderLeadDetail(document.getElementById('landing'));
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
    visit_date: document.getElementById('ld_edit_visit_date').value || null,
    site_address: document.getElementById('ld_edit_site_address').value,
    notes: document.getElementById('ld_edit_notes').value,
  };
  if (!body.name.trim()) { alert('A name is required.'); return; }
  const res = await fetch(`${API}/leads/${leadId}`, {method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
  if (!res.ok) { alert('Could not save changes.'); return; }
  renderLeadDetail(document.getElementById('landing'));
}
