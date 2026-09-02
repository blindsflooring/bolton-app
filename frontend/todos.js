// ===== TO-DOS =====
// Stage 2 of the Assigned Leads / To-Dos / Calendar brief (confirmed
// Sept 2026) — "a broader to-do list the Owner can assign to anyone."
// Deliberately its own small module, same "one file per feature"
// convention as leads.js/order-index.js/calendar.js — genuinely
// separate from Leads (own table, own screen, no lead_status/proof-of-
// work note), reusing only the visual language (tabs, By Person
// grouping, inline assignee dropdown) leads.js already established for
// exactly this kind of "assignable to a real person" list, per the
// brief's own confirmed scope: minimal, not a rebuild of Leads.

const TODO_ASSIGNEE_LABEL = { burgert: 'Burgert', ryno: 'Ryno', madri: 'Madri' };
let todosCache = [];
let todosActiveTab = 'open';   // 'open' | 'done' | 'all'
let todosGroupByPerson = false;

function todoAssigneeSelectHtml(t) {
  return `<select onclick="event.stopPropagation();" onchange="event.stopPropagation(); reassignTodo(${t.id}, this.value)" style="font-size:12px; padding:2px 4px;">
    ${Object.keys(TODO_ASSIGNEE_LABEL).map(k => `<option value="${k}" ${t.assigned_to===k?'selected':''}>${TODO_ASSIGNEE_LABEL[k]}</option>`).join('')}
  </select>`;
}

function todoDueLabel(t) {
  if (!t.due_date) return '<span class="muted">—</span>';
  const today = new Date().toISOString().slice(0, 10);
  const overdue = !t.done && t.due_date < today;
  return `<span style="${overdue ? 'color:var(--coral); font-weight:700;' : ''}">${new Date(t.due_date + 'T00:00:00').toLocaleDateString('en-ZA')}${overdue ? ' (overdue)' : ''}</span>`;
}

function todoRowHtml(t) {
  return `
    <tr style="${t.done ? 'opacity:0.6;' : ''}">
      <td data-label="" onclick="event.stopPropagation();"><input type="checkbox" ${t.done ? 'checked' : ''} onchange="toggleTodoDone(${t.id}, this.checked)" title="Mark done"></td>
      <td class="card-title" data-label="Title" style="${t.done ? 'text-decoration:line-through;' : ''}">${t.title}</td>
      <td data-label="Assigned">${todoAssigneeSelectHtml(t)}</td>
      <td data-label="Due">${todoDueLabel(t)}</td>
      <td data-label="" onclick="event.stopPropagation();"><a href="#" onclick="deleteTodoAction(${t.id}); return false;" style="font-size:12px; color:var(--coral);">Delete</a></td>
    </tr>`;
}

async function renderTodos(el) {
  await renderWithRetry(el, 'To-Dos', async () => {
  el.innerHTML = `<span class="back-link" onclick="landingView='tiles'; renderLanding();">← Back</span><div class="card"><h2>To-Dos</h2><p class="muted">Loading...</p></div>`;
  const res = await fetch(`${API}/todos`);
  todosCache = await res.json();
  renderTodosTable();
  });
}

function setTodosTab(tab) {
  todosActiveTab = tab;
  renderTodosTable();
}

function toggleTodosGroupByPerson() {
  todosGroupByPerson = !todosGroupByPerson;
  renderTodosTable();
}

function renderTodosTable() {
  const el = document.getElementById('landing');
  const todos = todosCache;
  const openCount = todos.filter(t => !t.done).length;
  const doneCount = todos.filter(t => t.done).length;
  const shown = todosActiveTab === 'all' ? todos : todos.filter(t => t.done === (todosActiveTab === 'done'));

  let bodyHtml;
  if (todosGroupByPerson) {
    const byAssignee = {};
    shown.forEach(t => { const key = t.assigned_to || '(unassigned)'; (byAssignee[key] = byAssignee[key] || []).push(t); });
    const names = Object.keys(byAssignee).sort();
    bodyHtml = names.length ? names.map(key => `
      <tr><td colspan="5" style="background:var(--bg,#f5f6f8); font-weight:700; padding:8px 10px;">${TODO_ASSIGNEE_LABEL[key] || key} (${byAssignee[key].length})</td></tr>
      ${byAssignee[key].map(todoRowHtml).join('')}
    `).join('') : '<tr><td colspan="5" class="muted">No to-dos match.</td></tr>';
  } else {
    bodyHtml = shown.length ? shown.map(todoRowHtml).join('') : '<tr><td colspan="5" class="muted">No to-dos match.</td></tr>';
  }

  const tab = (key, label, count) => `<button onclick="setTodosTab('${key}')" style="${todosActiveTab===key ? 'background:var(--teal); color:white; border-color:var(--teal);' : ''}">${label}${count !== undefined ? ` (${count})` : ''}</button>`;

  el.innerHTML = `
    <span class="back-link" onclick="landingView='tiles'; renderLanding();">← Back</span>
    <div class="landing-welcome">
      <h1>To-Dos</h1>
      <p>General tasks, assignable to anyone — separate from Leads, no sales process attached.</p>
    </div>
    <div class="card">
      <div style="display:flex; justify-content:space-between; align-items:center; gap:6px; flex-wrap:wrap; margin-bottom:14px;">
        <div style="display:flex; gap:6px; flex-wrap:wrap;">
          ${tab('open', 'Open', openCount)}${tab('done', 'Done', doneCount)}${tab('all', 'All', todos.length)}
        </div>
        <button onclick="toggleTodosGroupByPerson()" style="${todosGroupByPerson ? 'background:var(--teal); color:white; border-color:var(--teal);' : ''}">By Person</button>
      </div>
      <table class="mobile-card-table"><thead><tr><th></th><th>Title</th><th>Assigned</th><th>Due</th><th></th></tr></thead>
      <tbody>${bodyHtml}</tbody></table>
    </div>
    <div class="card">
      <h2>New To-Do</h2>
      <div class="grid">
        <div class="field" style="grid-column: span 2;"><label>Title</label><input id="td_title" placeholder="e.g. Call supplier about backordered stock"></div>
        <div class="field"><label>Assigned to</label>
          <select id="td_assigned_to">
            <option value="burgert" ${effectiveUsernameForQuoting()==='burgert'?'selected':''}>Burgert</option>
            <option value="ryno" ${effectiveUsernameForQuoting()==='ryno'?'selected':''}>Ryno</option>
            <option value="madri" ${effectiveUsernameForQuoting()==='madri'?'selected':''}>Madri</option>
          </select>
        </div>
        <div class="field"><label>Due date <span class="adj">(optional)</span></label><input id="td_due_date" type="date"></div>
      </div>
      <br><button class="primary" id="addTodoBtn" onclick="addTodo()">Add To-Do</button>
      <p class="muted" id="addTodoStatus" style="margin-top:8px;"></p>
    </div>
  `;
}

async function addTodo() {
  const btn = document.getElementById('addTodoBtn');
  const statusEl = document.getElementById('addTodoStatus');
  const title = document.getElementById('td_title').value.trim();
  if (!title) { alert('Enter a title.'); return; }
  const body = {
    title,
    assigned_to: document.getElementById('td_assigned_to').value,
    due_date: document.getElementById('td_due_date').value || null,
  };
  if (btn) { btn.disabled = true; btn.textContent = 'Adding...'; }
  try {
    const res = await fetch(`${API}/todos`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body)});
    if (!res.ok) throw new Error('save failed');
    if (statusEl) statusEl.textContent = `✓ "${title}" added.`;
    await renderTodos(document.getElementById('landing'));
  } catch (e) {
    if (statusEl) statusEl.textContent = '❌ Could not save — check your connection and try again.';
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Add To-Do'; }
  }
}

async function toggleTodoDone(todoId, done) {
  const res = await fetch(`${API}/todos/${todoId}`, {
    method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ done }),
  });
  if (!res.ok) { alert('Could not update this to-do.'); return; }
  const todo = todosCache.find(t => t.id === todoId);
  if (todo) { todo.done = done; }
  renderTodosTable();
}

async function reassignTodo(todoId, newAssignee) {
  const res = await fetch(`${API}/todos/${todoId}`, {
    method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ assigned_to: newAssignee }),
  });
  if (!res.ok) { alert('Could not reassign this to-do.'); return; }
  const todo = todosCache.find(t => t.id === todoId);
  if (todo) todo.assigned_to = newAssignee;
  renderTodosTable();
}

async function deleteTodoAction(todoId) {
  if (!confirm('Delete this to-do? This can\'t be undone.')) return;
  const res = await fetch(`${API}/todos/${todoId}`, {method: 'DELETE'});
  if (!res.ok) { alert('Could not delete this to-do.'); return; }
  todosCache = todosCache.filter(t => t.id !== todoId);
  renderTodosTable();
}
