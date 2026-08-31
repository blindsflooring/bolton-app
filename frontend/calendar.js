// ===== INSTALLATION CALENDAR =====
// Whole-business booking visibility (confirmed Aug 2026, approved
// proposal) — a real, standalone month view of every job with a booked
// installation date, replacing "piece scheduling together job by job."
// Deliberately small, per the brief's own explicit instruction: read-
// only, no installer/team assignment, built entirely from fields that
// already existed before this brief (installation_date,
// installation_confirmed_date) — no schema or endpoint change, a pure
// frontend addition over the exact same GET /quotes the Order Index
// already fetches.
//
// The open design question (does the calendar become the actual way a
// date gets set, or stay a read-only view layered on the existing
// Booking tile?) was answered explicitly in the approved proposal:
// read-only for v1. This is deliberate, not a smaller idea than what
// Burgert has described wanting eventually (drag-and-drop rescheduling,
// fluid like Google Calendar) — it's the correct first layer under it.
// Every chip below already carries its own quote id, and the write
// path a future drag interaction would call (PUT /quotes/{id}?
// installation_date=...) already exists, unchanged, today. Nothing
// here needs to be rebuilt to add that later.

let calendarQuotesCache = [];   // full GET /quotes response, fetched once per screen visit — every month navigation filters this same cache client-side, no refetch, same "fetch once, filter locally" convention Order Index's own orderIndexQuotesCache already uses
let calendarViewYear = null;
let calendarViewMonth = null;   // 0-11, JS Date convention
let calendarExpandedDay = null; // 'YYYY-MM-DD' or null — the day currently expanded into a full list below the grid

function openInstallationCalendar() {
  const today = new Date();
  calendarViewYear = today.getFullYear();
  calendarViewMonth = today.getMonth();
  calendarExpandedDay = null;
  landingView = 'installCalendar';
  renderLanding();
}

async function renderInstallationCalendar(el) {
  await renderWithRetry(el, 'Installation Calendar', async () => {
    el.innerHTML = `<span class="back-link" onclick="landingView='tiles'; renderLanding();">← Back</span><div class="card"><p class="muted">Loading...</p></div>`;
    const res = await fetch(`${API}/quotes`);
    calendarQuotesCache = await res.json();
    renderCalendarView(el);
  });
}

// Which real, booked jobs land on which day (Decision Q1, approved
// proposal) — job_number is not null (a real job, not just a quote
// that happened to get an early tentative date typed into Job
// Details), never declined, and has installation_date set at all.
function calendarJobsForMonth(year, month) {
  const byDay = {};
  calendarQuotesCache.forEach(q => {
    if (!q.job_number || q.declined_at || !q.installation_date) return;
    const d = q.installation_date; // 'YYYY-MM-DD', already the exact shape needed — no Date() parsing/timezone risk
    const [y, m] = d.split('-').map(Number);
    if (y !== year || m !== month + 1) return;
    (byDay[d] = byDay[d] || []).push(q);
  });
  // Confirmed first within each day, tentative after — the more
  // settled information first, same "done things read as settled"
  // principle the Job Control Panel's own status strip follows.
  Object.values(byDay).forEach(list => list.sort((a, b) => (b.installation_confirmed_date ? 1 : 0) - (a.installation_confirmed_date ? 1 : 0)));
  return byDay;
}

function changeCalendarMonth(delta) {
  calendarViewMonth += delta;
  if (calendarViewMonth < 0) { calendarViewMonth = 11; calendarViewYear--; }
  if (calendarViewMonth > 11) { calendarViewMonth = 0; calendarViewYear++; }
  calendarExpandedDay = null;
  renderCalendarView(document.getElementById('landing'));
}

const CAL_MONTH_NAMES = ['January','February','March','April','May','June','July','August','September','October','November','December'];
const CAL_DOW = ['Sun','Mon','Tue','Wed','Thu','Fri','Sat'];

function renderCalendarView(el) {
  const year = calendarViewYear, month = calendarViewMonth;
  const byDay = calendarJobsForMonth(year, month);
  const todayStr = new Date().toISOString().slice(0, 10);

  // Standard month-grid build: start on the Sunday on/before the 1st,
  // run 6 full weeks (42 cells) so every month lays out consistently
  // regardless of how many rows it actually needs — a plain grid, no
  // library, per the proposal's own confirmed non-goal.
  const firstOfMonth = new Date(year, month, 1);
  const startOffset = firstOfMonth.getDay(); // 0=Sun
  const gridStart = new Date(year, month, 1 - startOffset);

  let cellsHtml = '';
  for (let i = 0; i < 42; i++) {
    const cellDate = new Date(gridStart);
    cellDate.setDate(gridStart.getDate() + i);
    const y = cellDate.getFullYear(), m = cellDate.getMonth(), d = cellDate.getDate();
    const dateStr = `${y}-${String(m + 1).padStart(2, '0')}-${String(d).padStart(2, '0')}`;
    const otherMonth = m !== month;
    const jobs = byDay[dateStr] || [];
    const visible = jobs.slice(0, 3);
    const overflow = jobs.length - visible.length;
    const chipsHtml = visible.map(q => `
      <div class="cal-chip ${q.installation_confirmed_date ? 'confirmed' : 'tentative'}" title="${(q.client_name || '').replace(/"/g,'&quot;')}${q.description ? ' — ' + q.description.replace(/"/g,'&quot;') : ''}" onclick="event.stopPropagation(); openOrderDetailScreen(${q.id});">${q.job_number} ${(q.client_name || '').replace(/</g,'&lt;')}</div>
    `).join('');
    const moreHtml = overflow > 0
      ? `<div class="cal-chip more" onclick="event.stopPropagation(); toggleCalendarDayList('${dateStr}');">+${overflow} more</div>`
      : '';
    cellsHtml += `
      <div class="cal-day ${otherMonth ? 'other-month' : ''} ${dateStr === todayStr ? 'today' : ''} ${jobs.length ? 'has-jobs' : ''}" ${jobs.length ? `onclick="toggleCalendarDayList('${dateStr}')"` : ''}>
        <div class="cal-daynum">${d}</div>
        ${chipsHtml}${moreHtml}
      </div>`;
  }

  const dowHtml = CAL_DOW.map(d => `<div class="cal-dow">${d}</div>`).join('');

  el.innerHTML = `
    <span class="back-link" onclick="landingView='tiles'; renderLanding();">← Back</span>
    <div class="landing-welcome">
      <h1>Installation Calendar</h1>
      <p>Every booked job, whole business, at a glance — click a job to open it, click a busy day to see everything on it.</p>
    </div>
    <div class="card">
      <div class="cal-head">
        <button onclick="changeCalendarMonth(-1)">‹ Prev</button>
        <h2 style="margin:0;">${CAL_MONTH_NAMES[month]} ${year}</h2>
        <button onclick="changeCalendarMonth(1)">Next ›</button>
      </div>
      <div style="display:flex; gap:14px; margin:10px 0 14px; font-size:12px;" class="muted">
        <span><span class="cal-legend-dot confirmed"></span> Confirmed</span>
        <span><span class="cal-legend-dot tentative"></span> Tentative</span>
      </div>
      <div class="cal-grid">
        ${dowHtml}
        ${cellsHtml}
      </div>
    </div>
    <div id="calendarDayListArea"></div>
  `;
  if (calendarExpandedDay) renderCalendarDayList(calendarExpandedDay);
}

function toggleCalendarDayList(dateStr) {
  calendarExpandedDay = calendarExpandedDay === dateStr ? null : dateStr;
  if (calendarExpandedDay) renderCalendarDayList(calendarExpandedDay);
  else document.getElementById('calendarDayListArea').innerHTML = '';
}

// Day list — same clear, scannable table shape as the existing Leads
// day-list screen, rendered inline right below the grid rather than a
// separate screen-and-back-link round trip: the jobs are already in
// calendarQuotesCache, so there's nothing worth a real navigation for
// here, and staying on one screen keeps this fluid rather than adding
// a click-through for something this small.
function renderCalendarDayList(dateStr) {
  const jobs = (calendarJobsForMonth(calendarViewYear, calendarViewMonth)[dateStr] || []);
  const dateObj = new Date(dateStr + 'T00:00:00');
  const rows = jobs.length ? jobs.map(q => `
    <tr onclick="openOrderDetailScreen(${q.id})" style="cursor:pointer;">
      <td data-label="Job"><b>${q.job_number}</b></td>
      <td data-label="Client">${(q.client_name || '').replace(/</g,'&lt;')}</td>
      <td data-label="Description">${(q.description || '—').replace(/</g,'&lt;')}</td>
      <td data-label="Status">${q.installation_confirmed_date ? '<span style="color:var(--teal); font-weight:700;">✓ Confirmed</span>' : '<span class="muted">Tentative</span>'}</td>
    </tr>`).join('') : `<tr><td colspan="4" class="muted">Nothing booked this day.</td></tr>`;
  document.getElementById('calendarDayListArea').innerHTML = `
    <div class="card">
      <h2>${dateObj.toLocaleDateString('en-ZA', { weekday: 'long', day: 'numeric', month: 'long' })}</h2>
      <table class="mobile-card-table"><thead><tr><th>Job</th><th>Client</th><th>Description</th><th>Status</th></tr></thead>
      <tbody>${rows}</tbody></table>
    </div>`;
}
