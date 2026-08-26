// ===== HR & COMMISSION =====
// Employees, Hours Worked, Leave (balances, requests, approve/reject,
// sick note upload), Documents, and Commission (read-only statements).
// Confirmed Aug 2026, Stage 2 of the foundation refactor, sixth and
// final extraction. Depends on shared.js (API, currentRole,
// triggerPrint, hrView) for the one piece of state that's genuinely
// cross-file — see below.
//
// hrView moved to shared.js, not declared here — a real cross-file
// dependency found during the pre-extraction scoping check: it's SET
// from index.html's onTileClick() (landing tile dispatcher, stays
// there) and READ here in renderHR()/hrSubnav(). Same category as
// CATEGORY_LABELS and sortByPriority in earlier rounds.
//
// currentEmployeeDetailId below is carried over as-is but is dead code
// — confirmed by search before moving it: declared, never read or
// written anywhere in the app. Left in place since removing unrelated
// dead code wasn't part of this extraction; flagged here rather than
// silently dropped or silently kept without comment.
//
// Checked and confirmed nothing outside this file reads employee or
// commission data directly — Quote Builder, Order Index, Clients, and
// Price Book have no dependency on anything here.

let currentEmployeeDetailId = null;

function hrSubnav(active) {
  const sections = [
    {id:'employees', label:'Employees'}, {id:'hours', label:'Hours'},
    {id:'leave', label:'Leave'}, {id:'documents', label:'Documents'}, {id:'commission', label:'Commission'},
  ];
  return `<div class="hr-subnav">${sections.map(s =>
    `<button class="${s.id === active ? 'active' : ''}" onclick="hrView='${s.id}'; renderLanding();">${s.label}</button>`
  ).join('')}</div>`;
}

async function renderHR(el) {
  await renderWithRetry(el, 'HR & Commission', async () => {
  el.innerHTML = `<span class="back-link" onclick="landingView='tiles'; renderLanding();">← Back</span>
    <div class="landing-welcome"><h1>HR &amp; Commission</h1><p>Employees, hours, leave, documents, and commission — Owner/Admin tools.</p></div>
    ${hrSubnav(hrView)}
    <div id="hrContent"><p class="muted">Loading...</p></div>`;
  const content = document.getElementById('hrContent');
  // Confirmed Aug 2026: these weren't awaited before — a rejection
  // inside any of the 5 sub-views (e.g. the fetch timeout below firing)
  // would have become a silent unhandled promise rejection, invisible
  // to this function's own try/catch (added as part of the retry-and-
  // fail-visibly fix) since an un-awaited call's rejection never
  // propagates to its caller. Now it does.
  if (hrView === 'employees') await renderHREmployees(content);
  else if (hrView === 'hours') await renderHRHours(content);
  else if (hrView === 'leave') await renderHRLeave(content);
  else if (hrView === 'documents') await renderHRDocuments(content);
  else if (hrView === 'commission') await renderHRCommission(content);
  });
}

// ---- Employees ----

let hrEmployeesCache = [];

async function renderHREmployees(el, editingId) {
  const res = await fetch(`${API}/employees?role=${currentRole()}`);
  hrEmployeesCache = await res.json();
  const editing = editingId ? hrEmployeesCache.find(e => e.id === editingId) : null;

  // Mobile Rendering Audit brief (confirmed Aug 2026) -- same
  // .mobile-card-table treatment, found needing it during that
  // brief's own required systematic sweep (HR Documents/Employees was
  // explicitly named in the brief's screen list).
  const rows = hrEmployeesCache.length ? hrEmployeesCache.map(e => `
    <tr>
      <td class="card-title" data-label="Name">${e.full_name}</td><td data-label="Role">${e.role_title || '—'}</td>
      <td data-label="Status"><span class="status-badge ${e.employment_status === 'active' ? 'active-status' : 'inactive-status'}">${e.employment_status}</span></td>
      <td data-label="Start date">${e.start_date || '—'}</td><td data-label="Birthday">${e.birthday || '—'}</td>
      <td data-label=""><button class="delete-btn" onclick="renderHREmployees(document.getElementById('hrContent'), ${e.id})" style="border-color:var(--teal); color:var(--teal);">Edit</button></td>
    </tr>`).join('') : '<tr><td colspan="6" class="muted">No employees yet.</td></tr>';

  el.innerHTML = `
    <div class="card">
      <h2>Employees</h2>
      <table class="mobile-card-table"><thead><tr><th>Name</th><th>Role</th><th>Status</th><th>Start date</th><th>Birthday</th><th></th></tr></thead>
      <tbody>${rows}</tbody></table>
    </div>
    <div class="card">
      <h2>${editing ? 'Edit ' + editing.full_name : 'Add Employee'}</h2>
      <div class="grid">
        <div class="field"><label>Full name</label><input id="emp_name" value="${editing ? editing.full_name : ''}"></div>
        <div class="field"><label>Role / title</label>
          <select id="emp_role_title">
            ${['Owner','Admin','Sales','Builder-Rep','Installer','Other'].map(r => `<option ${editing && editing.role_title===r ? 'selected':''}>${r}</option>`).join('')}
          </select>
        </div>
        <div class="field"><label>Start date</label><input id="emp_start_date" type="date" value="${editing && editing.start_date ? editing.start_date : ''}"></div>
        <div class="field"><label>Birthday</label><input id="emp_birthday" type="date" value="${editing && editing.birthday ? editing.birthday : ''}"></div>
        <div class="field"><label>Phone</label><input id="emp_phone" value="${editing ? editing.phone||'' : ''}"></div>
        <div class="field"><label>Email</label><input id="emp_email" value="${editing ? editing.email||'' : ''}"></div>
        <div class="field"><label>Employment status</label>
          <select id="emp_status">
            <option value="active" ${editing && editing.employment_status==='active' ? 'selected':''}>Active</option>
            <option value="inactive" ${editing && editing.employment_status==='inactive' ? 'selected':''}>Inactive</option>
          </select>
        </div>
        <div class="field"><label>Commission eligible</label>
          <select id="emp_comm_eligible">
            <option value="true" ${editing && editing.commission_eligible ? 'selected':''}>Yes</option>
            <option value="false" ${editing && !editing.commission_eligible ? 'selected':''}>No</option>
          </select>
        </div>
        <div class="field"><label>Commission type</label>
          <select id="emp_comm_type">
            <option value="pure_sales" ${editing && editing.commission_role_type==='pure_sales' ? 'selected':''}>Pure Sales (% of GP)</option>
            <option value="builder_rep" ${editing && editing.commission_role_type==='builder_rep' ? 'selected':''}>Builder-Rep (% of ex-VAT/job)</option>
            <option value="other" ${editing && editing.commission_role_type==='other' ? 'selected':''}>Other</option>
          </select>
        </div>
        <div class="field"><label>Sales owner key <span class="adj">(links to quotes — e.g. "ryno")</span></label><input id="emp_sales_key" value="${editing ? editing.sales_owner_key||'' : ''}" placeholder="ryno"></div>
        <div class="field"><label>Receives 13th cheque</label>
          <select id="emp_13th">
            <option value="true" ${editing && editing.thirteenth_cheque_eligible ? 'selected':''}>Yes</option>
            <option value="false" ${editing && !editing.thirteenth_cheque_eligible ? 'selected':''}>No</option>
          </select>
        </div>
        <div class="field" style="grid-column: span 2;"><label>Notes <span class="adj">(Owner + Admin only)</span></label><input id="emp_notes" value="${editing ? editing.notes||'' : ''}"></div>
      </div>
      <br>
      <button class="primary" onclick="saveEmployee(${editing ? editing.id : 'null'})">${editing ? 'Save Changes' : 'Add Employee'}</button>
      ${editing ? `<button onclick="renderHREmployees(document.getElementById('hrContent'))" style="margin-left:10px; background:none; border:2px solid var(--border); border-radius:6px; padding:9px 16px; cursor:pointer;">Cancel</button>` : ''}
      <p class="muted" id="empSaveStatus" style="margin-top:8px;"></p>
    </div>
  `;
}

async function saveEmployee(editingId) {
  const body = {
    full_name: document.getElementById('emp_name').value,
    role_title: document.getElementById('emp_role_title').value,
    start_date: document.getElementById('emp_start_date').value || null,
    birthday: document.getElementById('emp_birthday').value || null,
    phone: document.getElementById('emp_phone').value,
    email: document.getElementById('emp_email').value,
    employment_status: document.getElementById('emp_status').value,
    commission_eligible: document.getElementById('emp_comm_eligible').value === 'true',
    commission_role_type: document.getElementById('emp_comm_type').value,
    sales_owner_key: document.getElementById('emp_sales_key').value,
    thirteenth_cheque_eligible: document.getElementById('emp_13th').value === 'true',
    notes: document.getElementById('emp_notes').value,
  };
  if (!body.full_name) { alert('Full name is required.'); return; }
  const url = editingId ? `${API}/employees/${editingId}` : `${API}/employees`;
  const method = editingId ? 'PUT' : 'POST';
  await fetch(url, {method, headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)});
  renderHREmployees(document.getElementById('hrContent'));
}

// ---- Hours Worked ----

function employeeOptionsHtml(selectedId) {
  return hrEmployeesCache.map(e => `<option value="${e.id}" ${selectedId==e.id?'selected':''}>${e.full_name}</option>`).join('');
}

async function renderHRHours(el, summaryEmployeeId, summaryYear, summaryMonth) {
  if (!hrEmployeesCache.length) {
    const res = await fetch(`${API}/employees?role=${currentRole()}`);
    hrEmployeesCache = await res.json();
  }
  const now = new Date();
  const year = summaryYear || now.getFullYear();
  const month = summaryMonth || (now.getMonth() + 1);

  el.innerHTML = `
    <div class="card">
      <h2>Capture Hours</h2>
      <div class="grid">
        <div class="field"><label>Employee</label><select id="hr_employee">${employeeOptionsHtml()}</select></div>
        <div class="field"><label>Date</label><input id="hr_date" type="date" value="${now.toISOString().slice(0,10)}"></div>
        <div class="field"><label>Hours</label><input id="hr_hours" type="number" step="0.25" placeholder="8"></div>
        <div class="field"><label>Type</label>
          <select id="hr_type">
            <option value="normal">Normal</option><option value="overtime">Overtime</option>
            <option value="sunday">Sunday</option><option value="public_holiday">Public Holiday</option>
          </select>
        </div>
        <div class="field"><label>Quote reference (optional)</label><input id="hr_quote_id" type="number" placeholder="Quote #"></div>
        <div class="field"><label>Notes</label><input id="hr_notes"></div>
      </div>
      <br><button class="primary" onclick="logHours()">Log Hours</button>
      <p class="muted" id="hoursSaveStatus" style="margin-top:8px;"></p>
    </div>

    <div class="card">
      <h2>Monthly Summary</h2>
      <div class="grid">
        <div class="field"><label>Employee (blank = all)</label>
          <select id="hr_summary_employee" onchange="reloadHoursSummary()">
            <option value="">All employees</option>
            ${employeeOptionsHtml(summaryEmployeeId)}
          </select>
        </div>
        <div class="field"><label>Year</label><input id="hr_summary_year" type="number" value="${year}" onchange="reloadHoursSummary()"></div>
        <div class="field"><label>Month</label><input id="hr_summary_month" type="number" min="1" max="12" value="${month}" onchange="reloadHoursSummary()"></div>
      </div>
      <div id="hoursSummaryResult"><p class="muted">Loading...</p></div>
      <button onclick="printHoursSummary()" style="margin-top:12px; background:none; border:2px solid var(--navy); color:var(--navy); font-family:'Poppins',sans-serif; font-weight:600; border-radius:6px; padding:8px 16px; cursor:pointer;">Print Summary</button>
    </div>
  `;
  loadHoursSummary(summaryEmployeeId, year, month);
}

async function reloadHoursSummary() {
  const empId = document.getElementById('hr_summary_employee').value || null;
  const year = document.getElementById('hr_summary_year').value;
  const month = document.getElementById('hr_summary_month').value;
  loadHoursSummary(empId, year, month);
}

async function loadHoursSummary(employeeId, year, month) {
  const params = new URLSearchParams({year, month});
  if (employeeId) params.set('employee_id', employeeId);
  const res = await fetch(`${API}/hours-worked/summary?${params}`);
  const data = await res.json();
  const el = document.getElementById('hoursSummaryResult');
  if (!data.employees.length) { el.innerHTML = '<p class="muted">No hours logged for this period.</p>'; return; }
  const rows = data.employees.map(e => `
    <tr>
      <td>${e.employee_name}</td><td><b>${e.total_hours}</b></td>
      <td>${(e.by_type.normal||0)}</td><td>${(e.by_type.overtime||0)}</td>
      <td>${(e.by_type.sunday||0)}</td><td>${(e.by_type.public_holiday||0)}</td>
    </tr>`).join('');
  el.innerHTML = `<table><thead><tr><th>Employee</th><th>Total</th><th>Normal</th><th>Overtime</th><th>Sunday</th><th>Public Holiday</th></tr></thead><tbody>${rows}</tbody></table>`;
}

async function logHours() {
  const body = {
    employee_id: parseInt(document.getElementById('hr_employee').value),
    work_date: document.getElementById('hr_date').value,
    hours: parseFloat(document.getElementById('hr_hours').value),
    hour_type: document.getElementById('hr_type').value,
    quote_id: document.getElementById('hr_quote_id').value ? parseInt(document.getElementById('hr_quote_id').value) : null,
    notes: document.getElementById('hr_notes').value,
  };
  if (!body.employee_id || !body.work_date || !body.hours) { alert('Employee, date, and hours are required.'); return; }
  await fetch(`${API}/hours-worked`, {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)});
  document.getElementById('hoursSaveStatus').textContent = `Logged ✓ ${new Date().toLocaleTimeString('en-ZA')}`;
  document.getElementById('hr_hours').value = '';
  document.getElementById('hr_notes').value = '';
  reloadHoursSummary();
}

function printHoursSummary() {
  const empSelect = document.getElementById('hr_summary_employee');
  const empLabel = empSelect.value ? empSelect.options[empSelect.selectedIndex].text : 'All Employees';
  const year = document.getElementById('hr_summary_year').value;
  const month = document.getElementById('hr_summary_month').value;
  const tableHtml = document.getElementById('hoursSummaryResult').innerHTML;
  triggerPrint(`
    <div class="print-doc">
      <div class="doc-header">
        <img src="${document.querySelector('header .logo-row img').src}" style="height:36px;">
        <div class="doc-title">HOURS SUMMARY</div>
      </div>
      <p><b>${empLabel}</b> — ${year}-${String(month).padStart(2,'0')}</p>
      ${tableHtml}
    </div>
  `);
}

// ---- Leave ----

async function renderHRLeave(el) {
  if (!hrEmployeesCache.length) {
    const res = await fetch(`${API}/employees?role=${currentRole()}`);
    hrEmployeesCache = await res.json();
  }
  el.innerHTML = `
    <div class="card">
      <h2>Leave Balances</h2>
      <div id="leaveBalancesResult"><p class="muted">Loading...</p></div>
      <hr style="margin:16px 0; border:none; border-top:1px solid var(--border);">
      <p style="font-size:12px; font-weight:600; margin-bottom:8px;">Set up a new balance (start of a leave cycle):</p>
      <div class="grid">
        <div class="field"><label>Employee</label><select id="lb_employee">${employeeOptionsHtml()}</select></div>
        <div class="field"><label>Leave type</label>
          <select id="lb_type"><option value="annual">Annual</option><option value="sick">Sick</option><option value="unpaid">Unpaid</option><option value="other">Other</option></select>
        </div>
        <div class="field"><label>Cycle start date</label><input id="lb_cycle_start" type="date"></div>
        <div class="field"><label>Days entitled</label><input id="lb_entitled" type="number" step="0.5" placeholder="21"></div>
      </div>
      <button class="primary" onclick="createLeaveBalance()">Set Up Balance</button>
    </div>

    <div class="card">
      <h2>Submit Leave Request</h2>
      <div class="grid">
        <div class="field"><label>Employee</label><select id="lr_employee">${employeeOptionsHtml()}</select></div>
        <div class="field"><label>Leave type</label>
          <select id="lr_type" onchange="toggleSickNoteField()"><option value="annual">Annual</option><option value="sick">Sick</option><option value="unpaid">Unpaid</option><option value="other">Other</option></select>
        </div>
        <div class="field"><label>Start date</label><input id="lr_start" type="date"></div>
        <div class="field"><label>End date</label><input id="lr_end" type="date"></div>
        <div class="field" style="grid-column: span 2;"><label>Reason (optional)</label><input id="lr_reason"></div>
        <div class="field" id="lr_sick_note_field" style="display:none; grid-column: span 2;">
          <label>Sick note <span class="adj">(optional — can also be attached later via Documents)</span></label>
          <input id="lr_sick_note_file" type="file">
        </div>
      </div>
      <button class="primary" onclick="submitLeaveRequest()">Submit Request</button>
      <p class="muted" id="leaveSaveStatus" style="margin-top:8px;"></p>
    </div>

    <div class="card">
      <h2>Pending Requests</h2>
      <div id="pendingLeaveResult"><p class="muted">Loading...</p></div>
    </div>
  `;
  loadLeaveBalances();
  loadPendingLeave();
}

async function loadLeaveBalances() {
  const res = await fetch(`${API}/leave-balances`);
  const balances = await res.json();
  const el = document.getElementById('leaveBalancesResult');
  if (!balances.length) { el.innerHTML = '<p class="muted">No leave balances set up yet.</p>'; return; }
  const rows = balances.map(b => {
    const emp = hrEmployeesCache.find(e => e.id === b.employee_id);
    return `<tr>
      <td>${emp ? emp.full_name : 'Employee #'+b.employee_id}</td>
      <td style="text-transform:capitalize;">${b.leave_type}</td>
      <td>${b.days_entitled}</td><td>${b.days_taken}</td>
      <td><b>${b.days_remaining}</b></td>
    </tr>`;
  }).join('');
  el.innerHTML = `<table><thead><tr><th>Employee</th><th>Type</th><th>Entitled</th><th>Taken</th><th>Remaining</th></tr></thead><tbody>${rows}</tbody></table>`;
}

async function createLeaveBalance() {
  const body = {
    employee_id: parseInt(document.getElementById('lb_employee').value),
    leave_type: document.getElementById('lb_type').value,
    cycle_start_date: document.getElementById('lb_cycle_start').value,
    days_entitled: parseFloat(document.getElementById('lb_entitled').value),
  };
  if (!body.cycle_start_date || !body.days_entitled) { alert('Cycle start date and days entitled are required.'); return; }
  await fetch(`${API}/leave-balances`, {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)});
  loadLeaveBalances();
}

function businessDaysBetween(startStr, endStr) {
  if (!startStr || !endStr) return 0;
  const start = new Date(startStr), end = new Date(endStr);
  let count = 0;
  for (let d = new Date(start); d <= end; d.setDate(d.getDate() + 1)) {
    const day = d.getDay();
    if (day !== 0 && day !== 6) count++;   // exclude weekends — simple approximation, doesn't account for public holidays
  }
  return count;
}

function toggleSickNoteField() {
  const isSick = document.getElementById('lr_type').value === 'sick';
  document.getElementById('lr_sick_note_field').style.display = isSick ? '' : 'none';
}

async function submitLeaveRequest() {
  const body = {
    employee_id: parseInt(document.getElementById('lr_employee').value),
    leave_type: document.getElementById('lr_type').value,
    start_date: document.getElementById('lr_start').value,
    end_date: document.getElementById('lr_end').value,
    days_requested: businessDaysBetween(document.getElementById('lr_start').value, document.getElementById('lr_end').value),
    reason: document.getElementById('lr_reason').value,
  };
  if (!body.start_date || !body.end_date) { alert('Start and end date are required.'); return; }
  // Real gap found and restored while extracting hr.js: the backend has
  // always supported linking a sick note document to a leave request
  // (LeaveRequest.sick_note_document_id, Document.document_type ==
  // "sick_note") but the frontend never wired it up — confirmed by
  // searching the whole file before restoring, not assumed missing.
  // Uploads to Documents first, then links the resulting document ID to
  // the leave request in the same submit action, matching the model.
  const sickNoteFile = document.getElementById('lr_sick_note_file');
  if (sickNoteFile && sickNoteFile.files.length) {
    const formData = new FormData();
    formData.append('file', sickNoteFile.files[0]);
    const params = new URLSearchParams({employee_id: body.employee_id, document_type: 'sick_note'});
    const docRes = await fetch(`${API}/documents/upload?${params}`, {method:'POST', body: formData});
    if (docRes.ok) {
      const doc = await docRes.json();
      body.sick_note_document_id = doc.id;
    } else {
      document.getElementById('leaveSaveStatus').textContent = 'Sick note upload failed — submitting request without it.';
    }
  }
  const res = await fetch(`${API}/leave-requests`, {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(body)});
  if (!res.ok) { const err = await res.json(); document.getElementById('leaveSaveStatus').textContent = 'Error: ' + (err.detail || 'could not submit'); return; }
  document.getElementById('leaveSaveStatus').textContent = `Submitted ✓ (${body.days_requested} day${body.days_requested!==1?'s':''} requested)${body.sick_note_document_id ? ' with sick note attached' : ''} — awaiting approval.`;
  sickNoteFile.value = '';
  loadPendingLeave();
}

async function loadPendingLeave() {
  const res = await fetch(`${API}/leave-requests?status=pending`);
  const requests = await res.json();
  const el = document.getElementById('pendingLeaveResult');
  if (!requests.length) { el.innerHTML = '<p class="muted">No pending requests.</p>'; return; }
  const rows = requests.map(r => {
    const emp = hrEmployeesCache.find(e => e.id === r.employee_id);
    return `<tr>
      <td>${emp ? emp.full_name : 'Employee #'+r.employee_id}</td>
      <td style="text-transform:capitalize;">${r.leave_type}</td>
      <td>${r.start_date} → ${r.end_date}</td><td>${r.days_requested}</td>
      <td>
        <button class="primary" style="padding:6px 12px; font-size:12px;" onclick="reviewLeaveRequest(${r.id}, 'approve')">Approve</button>
        <button class="delete-btn" onclick="reviewLeaveRequest(${r.id}, 'reject')">Reject</button>
      </td>
    </tr>`;
  }).join('');
  el.innerHTML = `<table><thead><tr><th>Employee</th><th>Type</th><th>Dates</th><th>Days</th><th></th></tr></thead><tbody>${rows}</tbody></table>`;
}

async function reviewLeaveRequest(requestId, action) {
  const reviewer = currentRole();
  const res = await fetch(`${API}/leave-requests/${requestId}/${action}?reviewed_by=${reviewer}`, {method:'PUT'});
  if (!res.ok) { const err = await res.json(); alert('Error: ' + (err.detail || 'could not process')); return; }
  loadPendingLeave();
  loadLeaveBalances();
}

// ---- Documents ----

async function renderHRDocuments(el) {
  if (!hrEmployeesCache.length) {
    const res = await fetch(`${API}/employees?role=${currentRole()}`);
    hrEmployeesCache = await res.json();
  }
  el.innerHTML = `
    <div class="card">
      <h2>Upload Document</h2>
      <div class="grid">
        <div class="field"><label>Employee</label><select id="doc_employee" onchange="loadDocuments()">${employeeOptionsHtml()}</select></div>
        <div class="field"><label>Document type</label>
          <select id="doc_type"><option value="contract">Contract</option><option value="sick_note">Sick Note</option><option value="warning">Warning</option><option value="other">Other</option></select>
        </div>
        <div class="field"><label>File</label><input id="doc_file" type="file"></div>
        <div class="field"><label>Owner-only? <span class="adj">(hidden from Sales)</span></label>
          <select id="doc_owner_only"><option value="false">No</option><option value="true">Yes</option></select>
        </div>
        <div class="field" style="grid-column: span 2;"><label>Notes</label><input id="doc_notes"></div>
      </div>
      <button class="primary" onclick="uploadDocument()">Upload</button>
      <p class="muted" id="docUploadStatus" style="margin-top:8px;"></p>
    </div>

    <div class="card">
      <h2>Documents for <span id="docListEmployeeName"></span></h2>
      <div id="documentsResult"><p class="muted">Loading...</p></div>
    </div>
  `;
  loadDocuments();
}

async function loadDocuments() {
  const empId = document.getElementById('doc_employee').value;
  const emp = hrEmployeesCache.find(e => e.id == empId);
  document.getElementById('docListEmployeeName').textContent = emp ? emp.full_name : '';
  const res = await fetch(`${API}/documents?employee_id=${empId}&role=${currentRole()}`);
  const docs = await res.json();
  const el = document.getElementById('documentsResult');
  if (!docs.length) { el.innerHTML = '<p class="muted">No documents for this employee (or none visible to your role).</p>'; return; }
  const rows = docs.map(d => `
    <tr>
      <td style="text-transform:capitalize;">${d.document_type.replace('_',' ')}</td>
      <td>${d.filename}${d.owner_only ? ' <span class="status-badge pending-status">Owner only</span>' : ''}</td>
      <td>${new Date(d.uploaded_at).toLocaleDateString('en-ZA')}</td>
      <td><a href="#" onclick="downloadDocumentFile(${d.id}, '${d.filename.replace(/'/g,"\\'")}'); return false;" style="color:var(--teal); font-weight:600; font-size:13px;">Download</a></td>
      <td><button class="delete-btn" onclick="deleteDocument(${d.id})">Delete</button></td>
    </tr>`).join('');
  el.innerHTML = `<table><thead><tr><th>Type</th><th>Filename</th><th>Uploaded</th><th></th><th></th></tr></thead><tbody>${rows}</tbody></table>`;
}

// Real bug found while building Quote Photo Attachments (confirmed Aug
// 2026): this download link used to be a plain <a href="...?role=...">
// — but get_current_role() was hardened months ago to read the role
// ONLY from the validated Bearer session, never a client-supplied
// query param (anyone could otherwise just claim to be Owner by
// editing the URL). A plain link navigation doesn't go through the
// app's fetch() wrapper, so it never sent the Authorization header
// either way — meaning every click here has been 401ing since that
// hardening went in, silently (a new tab opening to an error page
// looks enough like "downloading" not to be noticed at a glance). Same
// fetch-then-blob pattern Quote Photo Attachments' thumbnails use, for
// the same reason: this is the only way to get an authenticated
// request's response savable as a file.
async function downloadDocumentFile(docId, filename) {
  const res = await fetch(`${API}/documents/${docId}/download`);
  if (!res.ok) { alert('Could not download this file.'); return; }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

async function uploadDocument() {
  const fileInput = document.getElementById('doc_file');
  if (!fileInput.files.length) { alert('Choose a file first.'); return; }
  const formData = new FormData();
  formData.append('file', fileInput.files[0]);
  const params = new URLSearchParams({
    employee_id: document.getElementById('doc_employee').value,
    document_type: document.getElementById('doc_type').value,
    owner_only: document.getElementById('doc_owner_only').value,
    notes: document.getElementById('doc_notes').value,
  });
  await fetch(`${API}/documents/upload?${params}`, {method:'POST', body: formData});
  document.getElementById('docUploadStatus').textContent = `Uploaded ✓ ${new Date().toLocaleTimeString('en-ZA')}`;
  fileInput.value = '';
  loadDocuments();
}

async function deleteDocument(docId) {
  if (!confirm('Delete this document?')) return;
  await fetch(`${API}/documents/${docId}`, {method:'DELETE'});
  loadDocuments();
}

// ---- Commission (read-only view) ----

async function renderHRCommission(el) {
  if (!hrEmployeesCache.length) {
    const res = await fetch(`${API}/employees?role=${currentRole()}`);
    hrEmployeesCache = await res.json();
  }
  const commissionEligible = hrEmployeesCache.filter(e => e.commission_eligible && e.sales_owner_key);
  const now = new Date();
  el.innerHTML = `
    <div class="card">
      <h2>Commission Statement</h2>
      <div class="grid">
        <div class="field"><label>Employee</label>
          <select id="comm_employee">
            ${commissionEligible.length
              ? commissionEligible.map(e => `<option value="${e.sales_owner_key}">${e.full_name} (${e.commission_role_type === 'pure_sales' ? '% of GP' : '% ex-VAT/job'})</option>`).join('')
              : '<option value="">No commission-eligible employees with a sales_owner_key set yet</option>'}
          </select>
        </div>
        <div class="field"><label>Year</label><input id="comm_year" type="number" value="${now.getFullYear()}"></div>
        <div class="field"><label>Month</label><input id="comm_month" type="number" min="1" max="12" value="${now.getMonth()+1}"></div>
      </div>
      <button class="primary" onclick="loadCommissionStatement()">View Statement</button>
      <div id="commissionResult" style="margin-top:16px;"></div>
    </div>
  `;
}

async function loadCommissionStatement() {
  const key = document.getElementById('comm_employee').value;
  if (!key) return;
  const year = document.getElementById('comm_year').value;
  const month = document.getElementById('comm_month').value;
  const res = await fetch(`${API}/commission/statement/${key}?year=${year}&month=${month}`);
  const data = await res.json();
  const el = document.getElementById('commissionResult');

  if (!data.commission_eligible && data.commission_eligible !== undefined) {
    el.innerHTML = `<p class="muted">${data.employee} is not commission eligible.</p>`;
    return;
  }

  if (data.commission_role_type === 'pure_sales') {
    el.innerHTML = `
      <div class="per-m2-check" style="margin-top:0;">
        <div class="per-m2-label">${data.employee} — ${data.period} — % of Gross Profit</div>
        <div class="per-m2-value ex" style="font-size:28px;">R${data.commission_due.toFixed(2)}</div>
      </div>
      <div class="fj-line step" style="margin-top:12px;"><span>Paid jobs this period</span><span>${data.jobs_count}</span></div>
      <div class="fj-line step"><span>Turnover</span><span>R${data.turnover.toFixed(2)}</span></div>
      <div class="fj-line step"><span>Gross Profit</span><span>R${data.gp.toFixed(2)}</span></div>
      <div class="fj-line result"><span>Rate applied</span><span>${(data.rate_applied_pct*100).toFixed(1)}%</span></div>
      <button onclick="printCommissionStatement()" style="margin-top:14px; background:none; border:2px solid var(--navy); color:var(--navy); font-family:'Poppins',sans-serif; font-weight:600; border-radius:6px; padding:8px 16px; cursor:pointer;">Print Statement</button>
    `;
  } else if (data.commission_role_type === 'builder_rep') {
    const breakdownRows = Object.keys(data.breakdown_by_category).map(cat =>
      `<tr><td style="text-transform:capitalize;">${cat}</td><td>R${data.breakdown_by_category[cat].toFixed(2)}</td></tr>`
    ).join('') || '<tr><td colspan="2" class="muted">No commission-earning categories this period.</td></tr>';
    const missingNote = data.categories_with_no_rate_configured.length
      ? `<p class="muted" style="color:var(--coral);">⚠️ No rate configured for: ${data.categories_with_no_rate_configured.join(', ')} — these earned R0 commission. Add rates via POST /commission-rates.</p>`
      : '';
    el.innerHTML = `
      <div class="per-m2-check" style="margin-top:0;">
        <div class="per-m2-label">${data.employee} — ${data.period} — % ex-VAT price per job</div>
        <div class="per-m2-value ex" style="font-size:28px;">R${data.commission_due.toFixed(2)}</div>
      </div>
      <div class="fj-line step" style="margin-top:12px;"><span>Paid jobs this period</span><span>${data.jobs_count}</span></div>
      <div class="fj-line step"><span>Turnover</span><span>R${data.turnover.toFixed(2)}</span></div>
      <table style="margin-top:12px;"><thead><tr><th>Category</th><th>Commission</th></tr></thead><tbody>${breakdownRows}</tbody></table>
      ${missingNote}
      <button onclick="printCommissionStatement()" style="margin-top:14px; background:none; border:2px solid var(--navy); color:var(--navy); font-family:'Poppins',sans-serif; font-weight:600; border-radius:6px; padding:8px 16px; cursor:pointer;">Print Statement</button>
    `;
  }
}

function printCommissionStatement() {
  const content = document.getElementById('commissionResult').innerHTML;
  triggerPrint(`
    <div class="print-doc">
      <div class="doc-header">
        <img src="${document.querySelector('header .logo-row img').src}" style="height:36px;">
        <div class="doc-title">COMMISSION STATEMENT</div>
      </div>
      ${content}
    </div>
  `);
}
