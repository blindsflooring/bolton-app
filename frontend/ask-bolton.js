// ===== Ask Bolton — natural-language query agent (confirmed Sept 2026) =====
//
// One box on Home, any question, for all three roles within what each
// may see. Claude writes a real query against the data it is allowed to
// describe; the server validates and runs it read-only. Nothing here
// decides access — this file renders whatever the server permitted, and
// the server is the boundary.
//
// EVERY ANSWER SHOWS ITS WORK. The sentence is written by a model and
// the rows are not, so the rows are always rendered, and the query that
// produced them is always one tap away. A confident sentence nobody can
// check is the failure mode this whole feature has to avoid, and hiding
// the query would be choosing it.
//
// MOBILE FIRST, and that is a real constraint: the people asking are
// usually holding a phone in a client's lounge. One full-width field, a
// thumb-sized button, an answer that reads top-down, and the only thing
// allowed to scroll sideways is the results table inside its own box.

// The wrapped window.fetch (shared.js) aborts any API call at 20s and
// deliberately leaves a caller-supplied signal alone. This is the first
// caller that needs its own: writing SQL with adaptive thinking, then
// running it, then phrasing the result, can legitimately take longer
// than that. Comfortably beyond the backend's own worst case, so the
// specific server-side message wins the race rather than a generic
// front-end abort — the same reasoning ai_import.py's timeout comment
// sets out.
const ASK_BOLTON_TIMEOUT_MS = 120000;

let askBoltonBusy = false;
let askBoltonScope = null;

function askBoltonHtml() {
  return `
    <div class="ask-bolton" id="askBolton">
      <form class="ask-row" onsubmit="askBoltonSubmit(event)">
        <input id="askBoltonInput" class="ask-input" type="text" autocomplete="off"
               maxlength="500" placeholder="Ask about the business…"
               aria-label="Ask a question about the business">
        <button class="ask-btn" type="submit" id="askBoltonBtn">Ask</button>
      </form>
      <div class="ask-scope" id="askBoltonScope"></div>
      <div class="ask-result" id="askBoltonResult" aria-live="polite"></div>
    </div>`;
}

// What the agent can currently reach, stated up front rather than
// discovered by asking something it has to refuse. The phase is real
// information for the person typing: at phase 1 a question about live
// jobs is not a failure of the tool, it is outside what it has been
// pointed at yet.
async function loadAskBoltonScope() {
  const host = document.getElementById('askBoltonScope');
  if (!host) return;
  try {
    const res = await fetch(`${API}/ask-bolton/scope`);
    if (!res.ok) { host.innerHTML = ''; return; }
    askBoltonScope = await res.json();
  } catch (e) {
    host.innerHTML = '';
    return;
  }
  if (!askBoltonScope.available) {
    askBoltonSetEnabled(false, 'Not available to your role yet.');
    return;
  }
  // Told up front, not discovered by asking. A server with no read-only
  // database user, or no API key, cannot answer ANY question — so the
  // honest thing is to say that before somebody types one and waits,
  // and to say which piece is missing rather than "something went
  // wrong". setup_problem is the server's own sentence, which already
  // names the environment variable to set.
  if (askBoltonScope.database_ready === false) {
    askBoltonSetEnabled(false,
      escapeHtmlAsk(askBoltonScope.setup_problem || 'Not configured yet.'));
    return;
  }
  if (askBoltonScope.ai_configured === false) {
    askBoltonSetEnabled(false,
      'No Anthropic API key is set on the server, so questions can’t be answered yet.');
    return;
  }
  askBoltonSetEnabled(true,
    `Ask anything about ${escapeHtmlAsk(askBoltonScope.data_available)}.`);
}

// A disabled box with a reason beats an enabled one that fails 20
// seconds later. The reason is rendered as the scope line rather than
// as an error, because nothing has gone wrong yet — it is a setting
// nobody has made.
function askBoltonSetEnabled(enabled, message) {
  const host = document.getElementById('askBoltonScope');
  const input = document.getElementById('askBoltonInput');
  const btn = document.getElementById('askBoltonBtn');
  if (host) {
    host.innerHTML = `<span class="ask-scope-text${enabled ? '' : ' ask-scope-blocked'}">${message}</span>`;
  }
  if (input) {
    input.disabled = !enabled;
    if (!enabled) input.placeholder = 'Not available yet';
  }
  if (btn) btn.disabled = !enabled;
}

function askBoltonSubmit(event) {
  event.preventDefault();
  const input = document.getElementById('askBoltonInput');
  askBoltonRun(input ? input.value : '');
}

async function askBoltonRun(question) {
  const host = document.getElementById('askBoltonResult');
  const btn = document.getElementById('askBoltonBtn');
  if (!host) return;
  question = (question || '').trim();
  if (!question) { host.innerHTML = ''; return; }
  if (askBoltonBusy) return;

  askBoltonBusy = true;
  if (btn) { btn.disabled = true; btn.textContent = '…'; }
  host.innerHTML = `<div class="ask-thinking">Working that out…</div>`;

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), ASK_BOLTON_TIMEOUT_MS);
  try {
    const res = await fetch(`${API}/ask-bolton`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question }),
      signal: controller.signal,
    });
    const data = await res.json().catch(() => ({}));
    host.innerHTML = res.ok
      ? askBoltonRenderHtml(data)
      // The backend's own message, never a generic one: a missing API
      // key, a Claude outage and a server with no read-only database
      // user each need something different done about them.
      : askBoltonNoticeHtml(data.detail || `Something went wrong (${res.status}).`);
  } catch (e) {
    host.innerHTML = askBoltonNoticeHtml(
      e.name === 'AbortError'
        ? "That took too long to come back. Try again, or ask something narrower."
        : "Couldn't reach the server. Check your connection and try again.");
  } finally {
    clearTimeout(timer);
    askBoltonBusy = false;
    if (btn) { btn.disabled = false; btn.textContent = 'Ask'; }
  }
}

function askBoltonNoticeHtml(text) {
  return `<div class="ask-notice">${escapeHtmlAsk(text)}</div>`;
}

function escapeHtmlAsk(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function askBoltonRenderHtml(data) {
  if (data.ok === false) {
    // A refusal says what was refused. The rejected query is shown too —
    // if the agent ever tries to reach somewhere it shouldn't, that
    // should be visible to the person who asked, not only in a log.
    const attempts = (data.attempts || []).map(a =>
      `<pre class="ask-sql ask-sql-bad">${escapeHtmlAsk(a.sql || '')}</pre>`).join('');
    return `<div class="ask-notice">${escapeHtmlAsk(data.error || 'That one is not available.')}
      ${attempts ? `<details class="ask-details"><summary>What it tried to run</summary>${attempts}</details>` : ''}
      ${data.sql ? `<details class="ask-details"><summary>The query</summary>
         <pre class="ask-sql">${escapeHtmlAsk(data.sql)}</pre></details>` : ''}
    </div>`;
  }

  if (data.kind === 'clarify') {
    // Asked back, never assumed. Rendered as a question to answer by
    // typing again, not as a failure.
    return `<div class="ask-clarify"><strong>One thing first:</strong>
      ${escapeHtmlAsk(data.clarify)}</div>`;
  }

  if (data.kind === 'cannot_answer') {
    // "I don't have that", stated plainly, with what it DOES have — so
    // the gap is a fact rather than a dead end.
    return `<div class="ask-notice">${escapeHtmlAsk(data.message)}
      ${data.data_available
        ? `<div class="ask-can">Right now I can only see ${escapeHtmlAsk(data.data_available)}.</div>`
        : ''}</div>`;
  }

  const parts = [];
  if (data.answer) parts.push(`<p class="ask-answer">${escapeHtmlAsk(data.answer)}</p>`);
  if (data.gap) parts.push(`<p class="ask-gap">${escapeHtmlAsk(data.gap)}</p>`);
  if (data.truncated) {
    parts.push(`<p class="ask-gap">Only the first ${data.row_count} rows are shown — ask something narrower for the full picture.</p>`);
  }
  parts.push(askBoltonTableHtml(data.columns, data.rows));
  parts.push(askBoltonProvenanceHtml(data));
  return parts.join('');
}

// Which tables the figures came from, and the exact query — always
// present, never behind a setting. Collapsed by default because most
// people want the answer, expanded in one tap because the point is that
// anybody CAN check it.
function askBoltonProvenanceHtml(data) {
  const tables = (data.tables || []).join(', ');
  return `
    <div class="ask-provenance">
      ${tables ? `<span class="ask-sources">From: ${escapeHtmlAsk(tables)}</span>` : ''}
      ${data.sql ? `<details class="ask-details"><summary>Show the query</summary>
        <pre class="ask-sql">${escapeHtmlAsk(data.sql)}</pre></details>` : ''}
    </div>`;
}

// Columns come back named by the query itself, so they are formatted by
// what the VALUE is rather than by a fixed label map — the agent can
// return a column nobody has seen before, and it still has to render
// sensibly.
const ASK_MONEY_HINT = /(sales|cost|profit|revenue|total|amount|value|turnover|deposit|outstanding|paid|target|expenses|assets|liabilities|equity)/i;
const ASK_PCT_HINT = /(pct|percent|margin|coverage|share)/i;

function askBoltonLabel(key) {
  return String(key).replace(/_/g, ' ').replace(/^./, c => c.toUpperCase());
}

// Opening a job from an answer goes through the SAME screen and the
// SAME permission check as opening it from the Order Index - Ask Bolton
// is not a second door. GET /quotes/{id} enforces scoped_username()
// server-side and returns 404, not 403, on someone else's job, so a link
// cannot become a way round that. This decides where to navigate; it
// decides nothing about who may arrive.
function askBoltonOpenJob(quoteId) {
  if (!quoteId || typeof openOrderDetailScreen !== 'function') return;
  landingView = 'orders';
  openOrderDetailScreen(Number(quoteId));
}

function askBoltonCell(key, value, row) {
  if (value === null || value === undefined || value === '') return '—';
  if (typeof value === 'boolean') return value ? 'Yes' : 'No';
  // A job reference is a place to go, not just a fact. Routed from the
  // row's own quote_id - never from the job number, which is a label and
  // not a route.
  const jobId = row && (row.quote_id || row.id);
  if (jobId && (key === 'job_number' || key === 'quote_id' || key === 'client_name')) {
    return `<a class="ask-joblink" role="button" tabindex="0"
               onclick="askBoltonOpenJob(${Number(jobId)})"
               onkeydown="if(event.key===&quot;Enter&quot;)askBoltonOpenJob(${Number(jobId)})"
               >${escapeHtmlAsk(value)}</a>`;
  }
  if (typeof value === 'number') {
    if (ASK_PCT_HINT.test(key)) {
      // Stored as a fraction throughout Bolton (0.36 = 36%), so a bare
      // 0.36 on screen would read as a third of a percent.
      return value <= 1.5 ? (value * 100).toFixed(1) + '%' : value.toFixed(1) + '%';
    }
    if (ASK_MONEY_HINT.test(key)) return escapeHtmlAsk(R(value));
    return escapeHtmlAsk(Number.isInteger(value) ? value : value.toFixed(2));
  }
  return escapeHtmlAsk(value);
}

function askBoltonTableHtml(columns, rows) {
  if (!rows || !rows.length) return '';
  const keys = columns && columns.length ? columns : Object.keys(rows[0]);
  // A single number is the answer, not a table of one cell.
  if (rows.length === 1 && keys.length === 1) {
    const k = keys[0];
    return `<div class="ask-figures"><div class="ask-figure">
        <span class="ask-figure-label">${escapeHtmlAsk(askBoltonLabel(k))}</span>
        <span class="ask-figure-value">${askBoltonCell(k, rows[0][k], rows[0])}</span>
      </div></div>`;
  }
  const head = keys.map(k => `<th>${escapeHtmlAsk(askBoltonLabel(k))}</th>`).join('');
  const body = rows.map(r =>
    `<tr>${keys.map(k => `<td>${askBoltonCell(k, r[k], r)}</td>`).join('')}</tr>`).join('');
  return `<div class="ask-table-wrap"><table class="ask-table">
      <thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
}
