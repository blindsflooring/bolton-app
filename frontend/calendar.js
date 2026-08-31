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
  Object.values(byDay).forEach(list => list.sort((a, b) => (calIsConfirmed(b) ? 1 : 0) - (calIsConfirmed(a) ? 1 : 0)));
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
    const visible = jobs.slice(0, 3);
    const overflow = jobs.length - visible.length;
    // Drag-and-drop (confirmed Aug 2026, approved Interactive Calendar
    // Design) — onpointerdown starts a possible drag (calChipPointerDown,
    // below); the existing onclick still opens the job normally for a
    // plain tap, guarded by calDragMoved so it doesn't ALSO fire right
    // after a genuine drag-drop just released on this same element.
    const chipsHtml = visible.map(q => {
      const confirmed = calIsConfirmed(q);
      return `
      <div class="cal-chip ${confirmed ? 'confirmed' : 'tentative'}" style="touch-action:none;" title="${(q.client_name || '').replace(/"/g,'&quot;')}${q.description ? ' — ' + q.description.replace(/"/g,'&quot;') : ''}"
        onpointerdown="calChipPointerDown(event, ${q.id}, '${dateStr}', ${confirmed})"
        onclick="if (calDragMoved) { event.stopPropagation(); return; } event.stopPropagation(); openOrderDetailScreen(${q.id});">${q.job_number} ${(q.client_name || '').replace(/</g,'&lt;')}</div>
    `;
    }).join('');
    const moreHtml = overflow > 0
      ? `<div class="cal-chip more" onclick="event.stopPropagation(); toggleCalendarDayList('${dateStr}');">+${overflow} more</div>`
      : '';
    cellsHtml += `
      <div class="cal-day ${otherMonth ? 'other-month' : ''} ${dateStr === todayStr ? 'today' : ''} ${jobs.length ? 'has-jobs' : ''}" data-date="${dateStr}" ${jobs.length ? `onclick="toggleCalendarDayList('${dateStr}')"` : ''}>
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
      <td data-label="Status">${calIsConfirmed(q) ? '<span style="color:var(--teal); font-weight:700;">✓ Confirmed</span>' : '<span class="muted">Tentative</span>'}</td>
    </tr>`).join('') : `<tr><td colspan="4" class="muted">Nothing booked this day.</td></tr>`;
  document.getElementById('calendarDayListArea').innerHTML = `
    <div class="card">
      <h2>${dateObj.toLocaleDateString('en-ZA', { weekday: 'long', day: 'numeric', month: 'long' })}</h2>
      <table class="mobile-card-table"><thead><tr><th>Job</th><th>Client</th><th>Description</th><th>Status</th></tr></thead>
      <tbody>${rows}</tbody></table>
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
let calDrag = null;         // {quoteId, originDate, confirmed, pointerId, chipEl, cloneEl, startX, startY}
let calDragMoved = false;   // true only once real movement is seen this gesture — checked by the chip's own onclick (index.html-style inline handler, above) to suppress opening the job right after a genuine drag-drop
const CAL_DRAG_THRESHOLD = 6; // px — below this, a pointerdown+up is treated as a plain tap, not a drag

function calChipPointerDown(e, quoteId, dateStr, confirmed) {
  if (e.button !== undefined && e.button !== 0) return; // primary mouse button / primary touch only
  const chipEl = e.currentTarget;
  calDrag = { quoteId, originDate: dateStr, confirmed, pointerId: e.pointerId, chipEl, cloneEl: null, startX: e.clientX, startY: e.clientY };
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
  const { quoteId, originDate, confirmed, chipEl, cloneEl } = calDrag;
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
  await calendarHandleDrop(quoteId, originDate, newDate, confirmed);
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
function showCalendarUndoToast(quoteId, originDate, newDisp, label) {
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
    await fetch(`${API}/quotes/${quoteId}?installation_date=${originDate}`, { method: 'PUT' });
    await renderInstallationCalendar(document.getElementById('landing'));
  };
}
