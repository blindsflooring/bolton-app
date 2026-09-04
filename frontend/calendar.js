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
// Interactive Calendar Design (confirmed Aug 2026, approved proposal)
// — the read-only v1 above was the correct first layer, exactly as
// planned: drag-and-drop rescheduling below reuses the SAME quote ids
// and the SAME two real write paths that already existed (the plain
// PUT for a tentative date, the existing /schedule action for a
// confirmed one) — nothing about the v1 grid needed to be rebuilt to
// add this.

let calendarQuotesCache = [];   // full GET /quotes response, fetched once per screen visit — every month navigation filters this same cache client-side, no refetch, same "fetch once, filter locally" convention Order Index's own orderIndexQuotesCache already uses
let calendarViewYear = null;
let calendarViewMonth = null;   // 0-11, JS Date convention
let calendarExpandedDay = null; // 'YYYY-MM-DD' or null — the day currently expanded into a full list below the grid

// Assigned Leads / To-Dos, Stage 3 (confirmed Sept 2026, "Shared
// calendar integration — leads, to-dos, and measure-ups alongside
// installations") — leads.js/todos.js own caches, fetched alongside
// quotes on every screen visit, same "fetch once per visit, filter
// locally per month" convention calendarQuotesCache already uses.
// Per-type visibility toggles (confirmed in the Stage 1 proposal's own
// recommendation on calendar density — "a per-type toggle... rather
// than forcing everything into view at once"): default all ON, same
// "the business already uses one shared Google Calendar for both
// installations and client visits together" framing the brief itself
// opens with — off is an explicit choice, not the starting state.
let calendarLeadsCache = [];
let calendarTodosCache = [];
let calShowInstallations = true;
let calShowLeads = true;
let calShowTodos = true;

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
    const [quotesRes, leadsRes, todosRes] = await Promise.all([
      fetch(`${API}/quotes`), fetch(`${API}/leads`), fetch(`${API}/todos?done=false`),
    ]);
    calendarQuotesCache = await quotesRes.json();
    // Best-effort (confirmed Sept 2026) — leads/todos are an addition
    // to an already-working calendar; a failure fetching either must
    // never take down the installation view that already worked before
    // this brief.
    calendarLeadsCache = leadsRes.ok ? await leadsRes.json() : [];
    calendarTodosCache = todosRes.ok ? await todosRes.json() : [];
    renderCalendarView(el);
  });
}

function toggleCalendarType(type) {
  if (type === 'installations') calShowInstallations = !calShowInstallations;
  else if (type === 'leads') calShowLeads = !calShowLeads;
  else if (type === 'todos') calShowTodos = !calShowTodos;
  renderCalendarView(document.getElementById('landing'));
}

// Which real, booked jobs land on which day (Decision Q1, approved
// proposal) — job_number is not null (a real job, not just a quote
// that happened to get an early tentative date typed into Job
// Details), never declined, and has installation_date set at all.
//
// Calendar: Multiple Work Days Per Job (confirmed Sept 2026, approved
// proposal) — each day a job actually occupies (its main installation
// day, plus any real extra days from q.work_days — screed before
// install, etc.) becomes its own normalized "chip" object here, never a
// raw Quote reused for two different meanings. installation_date/
// installation_confirmed_date on `q` itself are read exactly once, right
// here, for the main chip — completely unchanged from before this
// brief; everything downstream (rendering, drag-and-drop) works off
// these chip objects, not `q` directly, so it never needs to know which
// kind of day it's looking at except via workDayId (null = the main
// day, the ONLY case that existed before this brief).
function calMainChip(q) {
  return {
    type: 'job', quoteId: q.id, job_number: q.job_number, client_name: q.client_name, description: q.description,
    date: q.installation_date, confirmed: calIsConfirmed(q), workDayId: null, dayType: null,
    // Visual Density & Colour Redesign (confirmed Sept 2026) — the
    // product types already on the quote row (flooring_types, computed
    // server-side in list_quotes() from the job's own lines). The brief
    // asked to "confirm current entry data includes a category/type
    // field... if not, this needs a small data-tagging step first" —
    // it does, so no tagging step was needed.
    types: q.flooring_types || [],
  };
}
function calWorkDayChip(q, wd) {
  return {
    type: 'job', quoteId: q.id, job_number: q.job_number, client_name: q.client_name, description: q.description,
    date: wd.work_date, confirmed: !!(wd.confirmed_date && wd.confirmed_date === wd.work_date),
    workDayId: wd.id, dayType: wd.day_type, types: q.flooring_types || [],
  };
}
// Assigned Leads / To-Dos, Stage 3 (confirmed Sept 2026) — lead visits
// and to-do due dates as their own chip types. Deliberately NOT wired
// into calChipPointerDown/drag-and-drop below — dragging a job chip
// reschedules a Quote's own installation_date/work_day via two
// existing, real write paths; a lead visit or a to-do due date is a
// completely different field on a completely different entity, and
// extending drag semantics to both was never confirmed as part of this
// Stage 3 scope. Both chip types are click-only: open the thing they
// represent, same as every other cross-screen navigation in this app.
function calLeadChip(l) {
  return { type: 'lead', leadId: l.id, name: l.name, contact: l.contact, date: l.visit_date,
           assignedTo: l.assigned_to };
}
function calTodoChip(t) {
  return { type: 'todo', todoId: t.id, title: t.title, date: t.due_date };
}
const CAL_DAY_TYPE_LABEL = { screed: 'Screed', installation: 'Install', other: 'Other' };

// Visual Density & Colour Redesign (confirmed Sept 2026) — colour now
// means WHAT KIND of work this is, not whether it's confirmed.
//
// That swap is the point of the brief: colour used to encode status
// (green = confirmed, cream/dashed = tentative), which spent the single
// strongest visual signal on the one thing a small dot can carry just
// as well. Status moves to that dot (.cal-chip-dot, styles.css), and
// colour is freed for category — "don't collapse status into the color
// scheme", in the brief's own words.
//
// Deliberately ONE function rather than a class picked inline at each
// of the three render sites: a chip's colour and the day-list's own
// label must never disagree about what a job is.
//
// Screed/plak shares the flooring blue per the brief's palette rather
// than getting its own colour — a screed day is already labelled
// "Screed" in its own chip text (CAL_DAY_TYPE_LABEL above), so it is
// distinguishable without spending a second colour on it. chip.dayType
// is still carried, so splitting it out later is a one-line change.
const CAL_CATEGORY_LABEL = {
  flooring: 'Flooring / screed', blinds: 'Blinds', lead: 'Lead visit', todo: 'To-do', other: 'Other',
};
function calChipCategory(chip) {
  if (chip.type === 'lead') return 'lead';
  if (chip.type === 'todo') return 'todo';
  if (chip.dayType === 'screed') return 'flooring';
  const types = chip.types || [];
  // Blinds only counts as a blinds job when there is NO floor on it —
  // a job carrying both is a flooring install that happens to include
  // blinds, and the installer's day is a flooring day.
  if (types.length && types.every(t => t === 'Blinds')) return 'blinds';
  if (types.length) return 'flooring';
  // A booked job with no lines on it yet is still a real booking; it
  // gets the neutral colour rather than being guessed into a category.
  return 'other';
}

function calendarJobsForMonth(year, month) {
  const byDay = {};
  const push = (d, chip) => {
    if (!d) return;
    const [y, m] = d.split('-').map(Number);   // 'YYYY-MM-DD', already the exact shape needed — no Date() parsing/timezone risk
    if (y !== year || m !== month + 1) return;
    (byDay[d] = byDay[d] || []).push(chip);
  };
  if (calShowInstallations) {
    calendarQuotesCache.forEach(q => {
      if (!q.job_number || q.declined_at) return;
      if (q.installation_date) push(q.installation_date, calMainChip(q));
      (q.work_days || []).forEach(wd => push(wd.work_date, calWorkDayChip(q, wd)));
    });
  }
  if (calShowLeads) {
    calendarLeadsCache.forEach(l => { if (l.visit_date) push(l.visit_date, calLeadChip(l)); });
  }
  if (calShowTodos) {
    calendarTodosCache.forEach(t => { if (t.due_date) push(t.due_date, calTodoChip(t)); });
  }
  // Confirmed first within each day, tentative after — the more
  // settled information first, same "done things read as settled"
  // principle the Job Control Panel's own status strip follows. Lead/
  // to-do chips have no such concept (confirmed=undefined sorts as
  // "tentative", i.e. after confirmed jobs but not reordered among
  // themselves) — jobs still read as the most settled thing on a
  // mixed day, which matches their real weight (a booked installation
  // vs. a lead visit or a task).
  Object.values(byDay).forEach(list => list.sort((a, b) => (b.confirmed ? 1 : 0) - (a.confirmed ? 1 : 0)));
  return byDay;
}

// Real bug fix (confirmed Aug 2026, Interactive Calendar Design
// proposal — found while designing the drag feature, not yet reported)
// — installation_confirmed_date means "the date that was confirmed,"
// not "whether confirmation ever happened": schedule_quote() (main.py)
// sets it EQUAL to installation_date at confirmation time. A plain
// truthy check therefore wrongly kept reading a job as confirmed on a
// date nobody actually confirmed, once installation_date changed
// afterward (already possible today via the plain Job Details field,
// with no re-booking). Compares the two dates instead of just checking
// whether one of them is set.
function calIsConfirmed(q) {
  return !!(q.installation_confirmed_date && q.installation_confirmed_date === q.installation_date);
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
    // Every entry is rendered (confirmed Sept 2026) — the old
    // jobs.slice(0, 3) + "+N more" is gone. Hiding entries behind a
    // counter on a moderately busy day is the exact thing this brief
    // set out to remove: the row now grows to fit instead (see
    // .cal-grid / --cal-row-min, styles.css).
    const visible = jobs;
    const isToday = dateStr === todayStr;
    // Drag-and-drop (confirmed Aug 2026, approved Interactive Calendar
    // Design) — onpointerdown starts a possible drag (calChipPointerDown,
    // below); the existing onclick still opens the job normally for a
    // plain tap, guarded by calDragMoved so it doesn't ALSO fire right
    // after a genuine drag-drop just released on this same element.
    //
    // Assigned Leads / To-Dos, Stage 3 (confirmed Sept 2026) — lead/
    // to-do chips are click-only (no onpointerdown at all — see
    // calLeadChip/calTodoChip's own comment above for why drag was
    // deliberately not extended to them), styled distinctly (own CSS
    // classes, styles.css) so a mixed day still reads at a glance as
    // "one booked job, one lead visit, one task," never three
    // identical-looking chips.
    const chipsHtml = visible.map(chip => {
      if (chip.type === 'lead') {
        return `<div class="cal-chip cal-cat-lead ${isToday ? 'is-today' : ''}" title="Lead visit: ${(chip.name || '').replace(/"/g,'&quot;')}${chip.contact ? ' — ' + chip.contact.replace(/"/g,'&quot;') : ''}"
          onclick="event.stopPropagation(); openLeadDetailScreen(${chip.leadId});">📋 ${(chip.name || '').replace(/</g,'&lt;')}</div>`;
      }
      if (chip.type === 'todo') {
        return `<div class="cal-chip cal-cat-todo ${isToday ? 'is-today' : ''}" title="To-do: ${(chip.title || '').replace(/"/g,'&quot;')}"
          onclick="event.stopPropagation(); landingView='todos'; renderLanding();">✓ ${(chip.title || '').replace(/</g,'&lt;')}</div>`;
      }
      // Multiple Work Days Per Job (confirmed Sept 2026) — a work-day
      // chip (chip.workDayId set) shows its type instead of the client
      // name, so it reads as "the same job, a different day," not a
      // second job — tied together visually by the identical job_number
      // text every chip for this job shares. workDayId threads through
      // calChipPointerDown so drag-and-drop moves the right row.
      const label = chip.workDayId
        ? `${chip.job_number} — ${CAL_DAY_TYPE_LABEL[chip.dayType] || 'Extra day'}`
        : `${chip.job_number} ${(chip.client_name || '').replace(/</g,'&lt;')}`;
      // The dot carries CONFIRMED vs TENTATIVE, independent of the
      // chip's colour, which now carries category (calChipCategory()).
      // Kept in the tooltip too, so the distinction survives for anyone
      // who can't read a 6px dot.
      const cat = calChipCategory(chip);
      const status = chip.confirmed ? 'confirmed' : 'tentative';
      return `
      <div class="cal-chip cal-cat-${cat} ${isToday ? 'is-today' : ''}" style="touch-action:none;" title="${(chip.client_name || '').replace(/"/g,'&quot;')}${chip.description ? ' — ' + chip.description.replace(/"/g,'&quot;') : ''} — ${CAL_CATEGORY_LABEL[cat]}, ${status}"
        onpointerdown="calChipPointerDown(event, ${chip.quoteId}, '${dateStr}', ${chip.confirmed}, ${chip.workDayId ?? 'null'})"
        onclick="if (calDragMoved) { event.stopPropagation(); return; } event.stopPropagation(); openOrderDetailScreen(${chip.quoteId});"><span class="cal-chip-dot ${status}"></span>${label}</div>
    `;
    }).join('');

    cellsHtml += `
      <!-- Booking leads onto the calendar (confirmed Sept 2026,
      Burgert: "I need the leads to be able to be booked into th
      ecalender where we can"). Every day is clickable now, not only
      days that already have something on them — an empty day was
      inert, which is exactly the day you want to book INTO. -->
      <div class="cal-day ${otherMonth ? 'other-month' : ''} ${dateStr === todayStr ? 'today' : ''} ${jobs.length ? 'has-jobs' : ''}" data-date="${dateStr}" onclick="toggleCalendarDayList('${dateStr}')">
        <div class="cal-daynum">${d}</div>
        ${chipsHtml}
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
      <!-- Legend rebuilt for the Visual Density & Colour Redesign
      (confirmed Sept 2026). It used to explain only Confirmed vs
      Tentative, because that was all colour meant. Colour now means
      category, so the legend has to say which colour is which kind of
      work — a colour scheme nobody can decode is just decoration —
      and status keeps its own entry, now as the dot. -->
      <div style="display:flex; gap:12px; margin:10px 0 6px; font-size:11.5px; flex-wrap:wrap; align-items:center;">
        <span class="cal-legend-key cal-cat-flooring">Flooring / screed</span>
        <span class="cal-legend-key cal-cat-blinds">Blinds</span>
        <span class="cal-legend-key cal-cat-lead">Lead visit</span>
        <span class="cal-legend-key cal-cat-todo">To-do</span>
      </div>
      <div style="display:flex; gap:14px; margin:0 0 12px; font-size:11.5px; flex-wrap:wrap;" class="muted">
        <span><span class="cal-chip-dot confirmed" style="color:#1c4b8a;"></span> Confirmed</span>
        <span><span class="cal-chip-dot tentative" style="color:#1c4b8a;"></span> Tentative</span>
        <span><span class="cal-legend-today"></span> Today</span>
      </div>
      <!-- Assigned Leads / To-Dos, Stage 3 (confirmed Sept 2026) — per-
      type visibility, the proposal's own confirmed answer to "a
      calendar showing installations, leads, to-dos, and measure-ups
      all at once needs a real plan for staying readable." Default all
      on — off is an explicit choice, not the starting state. -->
      <div style="display:flex; gap:14px; margin:0 0 14px; font-size:12px; flex-wrap:wrap;">
        <label style="cursor:pointer;"><input type="checkbox" ${calShowInstallations?'checked':''} onchange="toggleCalendarType('installations')"> Installations</label>
        <label style="cursor:pointer;"><input type="checkbox" ${calShowLeads?'checked':''} onchange="toggleCalendarType('leads')"> Lead visits 📋</label>
        <label style="cursor:pointer;"><input type="checkbox" ${calShowTodos?'checked':''} onchange="toggleCalendarType('todos')"> To-Dos ✓</label>
      </div>
      <div class="cal-grid">
        ${dowHtml}
        ${cellsHtml}
      </div>
    </div>
    <div id="calendarDayListArea"></div>
  `;
  if (calendarExpandedDay) renderCalendarDayList(calendarExpandedDay);
  calFitCalendarGrid();
}

// Fits one screen, no scrolling (confirmed Sept 2026 — the original
// Interactive Calendar brief's own explicit requirement, confirmed
// directly still unmet on a real browser). A fixed row-height guess
// (the old 76px min-height) can never actually guarantee this — it
// only fits whatever window it happened to be tuned against. This
// measures the grid's own real position after every render and sets
// --cal-row-min (styles.css) from whatever's genuinely left of the real
// viewport below it, so a quiet six-week grid still fills exactly the
// space that's really there. Re-run on resize too (orientation
// change, a window being resized) — deliberately NOT scroll-linked
// (this file/styles.css's own Sticky Header history is explicit that
// whole category of fix was retired for good reason after four failed
// attempts) — this only reacts to real viewport-size changes, never
// to scroll position.
function calFitCalendarGrid() {
  const grid = document.querySelector('.cal-grid');
  if (!grid) return;
  // Measures EVERYTHING else on the page, not just what's above the
  // grid — real gap found testing this directly: a fixed guess based
  // only on the grid's own top offset still overflowed by ~76px, the
  // page's own bottom padding reserved for the floating "back to top"
  // button (index.html, every screen), which sits entirely below the
  // grid and so never showed up in a top-only measurement. Clearing
  // any previous constraint first so this always measures the grid's
  // real natural height, not whatever it was already shrunk to.
  grid.style.removeProperty('--cal-row-min');
  const nonGridHeight = document.body.scrollHeight - grid.getBoundingClientRect().height;
  // 220px floor: below this a real six-week grid stops being legible
  // regardless of how the space is split — a genuinely tiny window
  // scrolls at that point, the same "normal window size" scope the
  // brief's own testing requirement already draws.
  const available = Math.max(window.innerHeight - nonGridHeight, 220);
  // CHANGED Sept 2026 (Visual Density & Colour Redesign) — this used to
  // set a hard total height and let six equal rows divide it up, which
  // is what forced the "+N more" collapsing in the first place: a row
  // that can never grow has to hide whatever doesn't fit.
  //
  // The measurement is kept and repurposed as a per-row MINIMUM instead.
  // That resolves what would otherwise be a straight conflict between
  // two confirmed requirements — "fits one screen, no scrolling" and
  // "no N-more collapsing, every entry visible". A quiet month still
  // fills exactly the space that's really there and needs no scrolling;
  // a genuinely busy week grows its own row and the page scrolls, which
  // is what the brief's own Google Calendar reference does too.
  grid.style.setProperty('--cal-row-min', Math.floor(available / 6) + 'px');
}
if (!window._calFitResizeBound) {
  window._calFitResizeBound = true;
  window.addEventListener('resize', () => { if (document.querySelector('.cal-grid')) calFitCalendarGrid(); });
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
// Book a lead visit straight onto the day you clicked (confirmed Sept
// 2026). Offers only leads that are still active AND not already booked
// — a lead already sitting on another day is moved by opening it, not by
// silently double-booking it from here, and a converted/lost lead has
// nothing left to visit (the backend refuses those outright too).
function renderCalendarBookLeadHtml(dateStr) {
  const bookable = calendarLeadsCache.filter(l =>
    ['new', 'contacted', 'potential'].includes(l.lead_status) && !l.visit_date);
  const alsoHere = calendarLeadsCache.filter(l => l.visit_date === dateStr);
  if (!bookable.length) {
    return `<p class="muted" style="margin:10px 0 0; font-size:12px;">${alsoHere.length
      ? 'Every other active lead already has a visit booked.'
      : 'No unbooked leads to put on this day.'}</p>`;
  }
  return `
    <div style="margin-top:12px; padding:10px; background:var(--bg,#f5f6f8); border-radius:8px;">
      <p style="margin:0 0 6px; font-weight:700;">📋 Book a lead visit on this day</p>
      <div style="display:flex; gap:8px; align-items:center; flex-wrap:wrap;">
        <select id="calBookLeadId">
          ${bookable.map(l => `<option value="${l.id}">${l.name}${l.contact ? ' · ' + l.contact : ''}</option>`).join('')}
        </select>
        <select id="calBookLeadOwner">
          ${Object.keys(LEAD_ASSIGNEE_LABEL).map(k => `<option value="${k}">${LEAD_ASSIGNEE_LABEL[k]}</option>`).join('')}
        </select>
        <button class="primary" onclick="bookLeadOntoCalendarDay('${dateStr}')">Book</button>
      </div>
    </div>`;
}

async function bookLeadOntoCalendarDay(dateStr) {
  const leadId = document.getElementById('calBookLeadId').value;
  const owner = document.getElementById('calBookLeadOwner').value;
  const params = new URLSearchParams({visit_date: dateStr, assigned_to: owner});
  const res = await fetch(`${API}/leads/${leadId}/book-visit?${params}`, {method: 'POST'});
  if (!res.ok) { const b = await res.json().catch(() => ({})); alert(b.detail || 'Could not book that visit.'); return; }
  const saved = await res.json();
  // Patch the cache the calendar draws from rather than re-fetching the
  // whole month — the chip has to appear immediately, and nothing else
  // on screen has changed.
  const cached = calendarLeadsCache.find(l => l.id === saved.id);
  if (cached) { cached.visit_date = saved.visit_date; cached.assigned_to = saved.assigned_to; }
  renderCalendarView(document.getElementById('landing'));
  renderCalendarDayList(dateStr);
}

function renderCalendarDayList(dateStr) {
  const jobs = (calendarJobsForMonth(calendarViewYear, calendarViewMonth)[dateStr] || []);
  const dateObj = new Date(dateStr + 'T00:00:00');
  // Multiple Work Days Per Job (confirmed Sept 2026) — Day column added
  // so a busy day mixing main installs and extra days (screed etc.)
  // still reads clearly; "Install" for every chip before this brief,
  // since workDayId was always null then.
  //
  // Assigned Leads / To-Dos, Stage 3 (confirmed Sept 2026) — lead/to-do
  // rows share the same table (a mixed day should read as one list of
  // "everything happening today," matching the brief's own "one shared
  // calendar" framing) but branch per type since they carry genuinely
  // different fields than a job chip.
  const bookingHtml = renderCalendarBookLeadHtml(dateStr);
  const rows = jobs.length ? jobs.map(chip => {
    if (chip.type === 'lead') {
      return `<tr onclick="openLeadDetailScreen(${chip.leadId})" style="cursor:pointer;">
        <td data-label="Job">📋 Lead visit</td>
        <td data-label="Client">${(chip.name || '').replace(/</g,'&lt;')}</td>
        <td data-label="Day">${chip.assignedTo ? (LEAD_ASSIGNEE_LABEL[chip.assignedTo] || chip.assignedTo) : '—'}</td>
        <td data-label="Description">${(chip.contact || '—').replace(/</g,'&lt;')}</td>
        <td data-label="Status"><span class="muted">Site visit</span></td>
      </tr>`;
    }
    if (chip.type === 'todo') {
      return `<tr onclick="landingView='todos'; renderLanding();" style="cursor:pointer;">
        <td data-label="Job">✓ To-Do</td>
        <td data-label="Client">${(chip.title || '').replace(/</g,'&lt;')}</td>
        <td data-label="Day">—</td>
        <td data-label="Description">—</td>
        <td data-label="Status"><span class="muted">Due today</span></td>
      </tr>`;
    }
    return `<tr onclick="openOrderDetailScreen(${chip.quoteId})" style="cursor:pointer;">
      <td data-label="Job"><b>${chip.job_number}</b></td>
      <td data-label="Client">${(chip.client_name || '').replace(/</g,'&lt;')}</td>
      <td data-label="Day">${chip.workDayId ? (CAL_DAY_TYPE_LABEL[chip.dayType] || 'Extra day') : 'Install'}</td>
      <td data-label="Description">${(chip.description || '—').replace(/</g,'&lt;')}</td>
      <td data-label="Status">${chip.confirmed ? '<span style="color:var(--teal); font-weight:700;">✓ Confirmed</span>' : '<span class="muted">Tentative</span>'}</td>
    </tr>`;
  }).join('') : `<tr><td colspan="5" class="muted">Nothing booked this day.</td></tr>`;
  document.getElementById('calendarDayListArea').innerHTML = `
    <div class="card">
      <h2>${dateObj.toLocaleDateString('en-ZA', { weekday: 'long', day: 'numeric', month: 'long' })}</h2>
      <table class="mobile-card-table"><thead><tr><th>Job</th><th>Client</th><th>Day</th><th>Description</th><th>Status</th></tr></thead>
      <tbody>${rows}</tbody></table>
      ${bookingHtml}
    </div>`;
}

// ===== Drag-and-drop rescheduling (confirmed Aug 2026, approved
// Interactive Calendar Design) =====
// Pointer Events, not the HTML5 Drag and Drop API — confirmed during
// investigation that native HTML5 DnD does not work on touchscreens at
// all, and Bolton has to run in a phone's browser (Consistent Mobile
// Back Navigation brief). Pointer Events unify mouse and touch in one
// model; still zero new dependencies, per the approved v1's own
// non-goal.
//
// Deliberately never moves a chip's real position until the server has
// actually confirmed the save (approved proposal §03's own "never-
// moved-but-looks-moved" requirement) — a successful drop always
// re-fetches and re-renders the whole grid from GET /quotes, nothing
// here is optimistic. setPointerCapture keeps every event routed to the
// same chip element for the life of one gesture, even once the pointer
// has physically moved over other elements — no document-level
// listeners needed.
let calDrag = null;         // {quoteId, originDate, confirmed, workDayId, pointerId, chipEl, cloneEl, startX, startY}
let calDragMoved = false;   // true only once real movement is seen this gesture — checked by the chip's own onclick (index.html-style inline handler, above) to suppress opening the job right after a genuine drag-drop
const CAL_DRAG_THRESHOLD = 6; // px — below this, a pointerdown+up is treated as a plain tap, not a drag

// workDayId (confirmed Sept 2026, Multiple Work Days Per Job) — null for
// the main installation chip, exactly as before this brief; an extra
// day's own id otherwise, threaded through to calChipPointerUp so the
// drop lands on the right row via the right endpoint.
function calChipPointerDown(e, quoteId, dateStr, confirmed, workDayId) {
  if (e.button !== undefined && e.button !== 0) return; // primary mouse button / primary touch only
  const chipEl = e.currentTarget;
  calDrag = { quoteId, originDate: dateStr, confirmed, workDayId: workDayId ?? null, pointerId: e.pointerId, chipEl, cloneEl: null, startX: e.clientX, startY: e.clientY };
  calDragMoved = false;
  chipEl.setPointerCapture(e.pointerId);
  chipEl.addEventListener('pointermove', calChipPointerMove);
  chipEl.addEventListener('pointerup', calChipPointerUp);
  chipEl.addEventListener('pointercancel', calChipPointerCancel);
}

function calChipPointerMove(e) {
  if (!calDrag || e.pointerId !== calDrag.pointerId) return;
  const dx = e.clientX - calDrag.startX, dy = e.clientY - calDrag.startY;
  if (!calDragMoved && Math.hypot(dx, dy) < CAL_DRAG_THRESHOLD) return;
  if (!calDragMoved) {
    calDragMoved = true;
    calDrag.chipEl.style.opacity = '0.35';
    const rect = calDrag.chipEl.getBoundingClientRect();
    const clone = calDrag.chipEl.cloneNode(true);
    clone.removeAttribute('onpointerdown');
    clone.removeAttribute('onclick');
    clone.style.cssText = `position:fixed; left:${rect.left}px; top:${rect.top}px; width:${rect.width}px; z-index:1000; pointer-events:none; box-shadow:0 6px 18px rgba(0,0,0,.25); opacity:0.95;`;
    document.body.appendChild(clone);
    calDrag.cloneEl = clone;
  }
  calDrag.cloneEl.style.left = (e.clientX - calDrag.cloneEl.offsetWidth / 2) + 'px';
  calDrag.cloneEl.style.top = (e.clientY - calDrag.cloneEl.offsetHeight / 2) + 'px';
  document.querySelectorAll('.cal-day.drop-target').forEach(d => d.classList.remove('drop-target'));
  const under = document.elementFromPoint(e.clientX, e.clientY);
  const dayEl = under && under.closest('.cal-day[data-date]');
  if (dayEl) dayEl.classList.add('drop-target');
}

async function calChipPointerUp(e) {
  if (!calDrag || e.pointerId !== calDrag.pointerId) return;
  const { quoteId, originDate, confirmed, workDayId, chipEl, cloneEl } = calDrag;
  chipEl.removeEventListener('pointermove', calChipPointerMove);
  chipEl.removeEventListener('pointerup', calChipPointerUp);
  chipEl.removeEventListener('pointercancel', calChipPointerCancel);
  document.querySelectorAll('.cal-day.drop-target').forEach(d => d.classList.remove('drop-target'));
  chipEl.style.opacity = '';
  if (cloneEl) cloneEl.remove();
  const wasDrag = calDragMoved;
  calDrag = null;
  if (!wasDrag) { calDragMoved = false; return; } // a plain tap — the chip's own onclick opens the job normally, unaffected
  // calDragMoved stays true through the rest of this synchronous turn
  // so the native 'click' event (fires right after pointerup on the
  // same element) is suppressed by the chip's own onclick guard —
  // cleared on the next tick, once that click has already happened.
  setTimeout(() => { calDragMoved = false; }, 0);
  const under = document.elementFromPoint(e.clientX, e.clientY);
  const dayEl = under && under.closest('.cal-day[data-date]');
  const newDate = dayEl ? dayEl.dataset.date : null;
  if (!newDate || newDate === originDate) return; // dropped back on itself, or off the grid entirely — nothing to save
  if (workDayId) { await calendarHandleWorkDayDrop(workDayId, quoteId, originDate, newDate, confirmed); }
  else { await calendarHandleDrop(quoteId, originDate, newDate, confirmed); }
}

function calChipPointerCancel() {
  if (!calDrag) return;
  calDrag.chipEl.removeEventListener('pointermove', calChipPointerMove);
  calDrag.chipEl.removeEventListener('pointerup', calChipPointerUp);
  calDrag.chipEl.removeEventListener('pointercancel', calChipPointerCancel);
  calDrag.chipEl.style.opacity = '';
  if (calDrag.cloneEl) calDrag.cloneEl.remove();
  document.querySelectorAll('.cal-day.drop-target').forEach(d => d.classList.remove('drop-target'));
  calDrag = null;
  calDragMoved = false;
}

function calFormatDate(dateStr) {
  return new Date(dateStr + 'T00:00:00').toLocaleDateString('en-ZA', { day: 'numeric', month: 'long' });
}

// Custom confirm dialog (approved proposal §02) — deliberately NOT the
// browser's native confirm(), per the brief's own "stays entirely in
// Bolton's own established design language" instruction. Reuses the
// exact same overlay/box shape the Client Picker already established
// (.client-picker-overlay/.client-picker-box, styles.css) rather than
// a second, differently-styled modal.
function calShowConfirmDialog(message, confirmLabel) {
  return new Promise(resolve => {
    const overlay = document.createElement('div');
    overlay.className = 'client-picker-overlay';
    overlay.innerHTML = `
      <div class="client-picker-box" style="max-width:380px;">
        <h3 style="margin:0 0 10px; color:var(--coral); font-family:'Poppins',sans-serif; font-size:15px;">Move confirmed installation?</h3>
        <p style="font-size:13px; color:#4b5563; margin:0 0 16px; line-height:1.5;">${message}</p>
        <div style="display:flex; gap:8px; justify-content:flex-end;">
          <button id="calDialogCancel" style="background:none; border:1px solid var(--border); color:var(--navy);">Cancel</button>
          <button id="calDialogConfirm" class="primary">${confirmLabel}</button>
        </div>
      </div>`;
    document.body.appendChild(overlay);
    overlay.querySelector('#calDialogCancel').onclick = () => { overlay.remove(); resolve(false); };
    overlay.querySelector('#calDialogConfirm').onclick = () => { overlay.remove(); resolve(true); };
  });
}

// The two real write paths (approved proposal §01/§02) — a tentative
// job moves via the exact same plain PUT the Installation section's
// own date field already calls (no confirmation step, since nothing
// about it has been confirmed either way yet); a confirmed job moves
// via the exact same /schedule action "Confirm Installation — Book"
// already uses, which is the one and only place
// installation_confirmed_date is ever written — keeping the two dates
// in sync by construction, the same mechanism that already prevents
// calIsConfirmed()'s own bug from recurring through this path.
async function calendarHandleDrop(quoteId, originDate, newDate, confirmed) {
  const q = calendarQuotesCache.find(x => x.id === quoteId);
  const label = q ? `${q.job_number || 'Job #' + q.id} — ${(q.client_name || '').replace(/</g,'&lt;')}` : 'This job';
  const oldDisp = calFormatDate(originDate), newDisp = calFormatDate(newDate);
  if (confirmed) {
    const proceed = await calShowConfirmDialog(
      `${label} is confirmed for ${oldDisp} — moving it to ${newDisp} will re-confirm it there instead.<br><br>The client may already be expecting the original date; Bolton won't notify them — that's still on you to do.`,
      `Move to ${newDisp}`
    );
    if (!proceed) return;
    const res = await fetch(`${API}/quotes/${quoteId}/schedule?installation_date=${newDate}`, { method: 'PUT' });
    if (!res.ok) { const body = await res.json().catch(() => ({})); alert(body.detail || 'Could not move this job — check your connection and try again.'); return; }
    await renderInstallationCalendar(document.getElementById('landing'));
  } else {
    const res = await fetch(`${API}/quotes/${quoteId}?installation_date=${newDate}`, { method: 'PUT' });
    if (!res.ok) { alert('Could not move this job — check your connection and try again.'); return; }
    await renderInstallationCalendar(document.getElementById('landing'));
    showCalendarUndoToast(quoteId, originDate, newDisp, label);
  }
}

// Action-confirmation, not outcome-confirmation (approved proposal
// §02) — "Date updated," never "Installation confirmed": this only
// ever means Bolton recorded a new proposed date, nothing about the
// real world has happened. A short-lived Undo rather than a dialog,
// matching how low the real stakes are for a still-tentative date —
// the same plain PUT this whole move already used, just with the
// dates swapped back.
// workDayId (confirmed Sept 2026, Multiple Work Days Per Job) — null
// undoes via the same main-date PUT as before this brief; an extra
// day's id undoes via its own PUT .../work-days/{id} instead, same
// "swap the dates back" idea, right endpoint for which row moved.
function showCalendarUndoToast(quoteId, originDate, newDisp, label, workDayId) {
  const existing = document.getElementById('calUndoToast');
  if (existing) existing.remove();
  const toast = document.createElement('div');
  toast.id = 'calUndoToast';
  toast.style.cssText = 'position:fixed; bottom:24px; left:50%; transform:translateX(-50%); background:#dcf5e6; color:#1a7a3e; border:1px solid #9fd9b6; border-radius:8px; padding:10px 16px; font-size:13px; font-weight:700; z-index:1000; display:flex; align-items:center; gap:10px; box-shadow:0 4px 14px rgba(0,0,0,.15);';
  toast.innerHTML = `<span>✓ Date updated — ${label} moved to ${newDisp}</span><a href="#" style="color:var(--teal); text-decoration:underline; font-weight:700;">Undo</a>`;
  document.body.appendChild(toast);
  const timeout = setTimeout(() => toast.remove(), 8000);
  toast.querySelector('a').onclick = async (e) => {
    e.preventDefault();
    clearTimeout(timeout);
    toast.remove();
    const url = workDayId ? `${API}/quotes/${quoteId}/work-days/${workDayId}?work_date=${originDate}` : `${API}/quotes/${quoteId}?installation_date=${originDate}`;
    await fetch(url, { method: 'PUT' });
    await renderInstallationCalendar(document.getElementById('landing'));
  };
}

// Extra work day drop (confirmed Sept 2026, Multiple Work Days Per Job)
// — the same two-path discipline calendarHandleDrop() already
// established for the main installation date, applied to one row of
// JobWorkDay instead: a tentative day moves via a plain PUT, a
// confirmed one gets the same "client may already be expecting the
// original date" warning before re-confirming it at the new date.
async function calendarHandleWorkDayDrop(workDayId, quoteId, originDate, newDate, confirmed) {
  const q = calendarQuotesCache.find(x => x.id === quoteId);
  const wd = q && (q.work_days || []).find(x => x.id === workDayId);
  const dayLabel = CAL_DAY_TYPE_LABEL[wd && wd.day_type] || 'Extra day';
  const label = q ? `${q.job_number || 'Job #' + q.id} — ${dayLabel}` : 'This day';
  const oldDisp = calFormatDate(originDate), newDisp = calFormatDate(newDate);
  if (confirmed) {
    const proceed = await calShowConfirmDialog(
      `${label} is confirmed for ${oldDisp} — moving it to ${newDisp} will re-confirm it there instead.<br><br>The client may already be expecting the original date; Bolton won't notify them — that's still on you to do.`,
      `Move to ${newDisp}`
    );
    if (!proceed) return;
    const res = await fetch(`${API}/quotes/${quoteId}/work-days/${workDayId}/confirm?work_date=${newDate}`, { method: 'PUT' });
    if (!res.ok) { const body = await res.json().catch(() => ({})); alert(body.detail || 'Could not move this day — check your connection and try again.'); return; }
    await renderInstallationCalendar(document.getElementById('landing'));
  } else {
    const res = await fetch(`${API}/quotes/${quoteId}/work-days/${workDayId}?work_date=${newDate}`, { method: 'PUT' });
    if (!res.ok) { alert('Could not move this day — check your connection and try again.'); return; }
    await renderInstallationCalendar(document.getElementById('landing'));
    showCalendarUndoToast(quoteId, originDate, newDisp, label, workDayId);
  }
}
