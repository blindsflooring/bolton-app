// ===== Ask Bolton (confirmed Sept 2026) =====
//
// A question box on Home, because Home is the one screen all three
// roles land on and the questions are about the business rather than
// about whichever screen you happen to be looking at. Not a floating
// chat bubble: this answers a fixed set of questions, and the shape of
// the control should tell the truth about that rather than inviting an
// open-ended conversation it cannot have.
//
// MOBILE FIRST, and that is a real constraint here rather than a note:
// the people asking "who still owes a deposit" are usually standing in
// a client's lounge holding a phone. One full-width field, a button big
// enough for a thumb, suggestions that wrap, and an answer that reads
// top-down with no horizontal scrolling except inside the table itself.
//
// THE SENTENCE IS NEVER THE ONLY THING SHOWN. Every answer renders the
// figures and the row detail underneath it, plus the sources it came
// from. That is what makes a phrasing slip visible instead of silent —
// the sentence is written by a model, the table is not.

// The wrapped window.fetch (shared.js) aborts any API call at 20s and
// deliberately leaves a caller-supplied signal alone. Ask Bolton is the
// first caller that needs its own: the backend allows 30s to classify
// plus 45s to phrase, so the shared 20s would abort a request that was
// still legitimately working and report it as a network failure. Longer
// than the backend's own worst case on purpose, with real margin, so
// the specific server-side message wins the race rather than a generic
// front-end abort — the same reasoning ai_import.py's own timeout
// comment sets out.
const ASK_BOLTON_TIMEOUT_MS = 90000;

let askBoltonBusy = false;
let askBoltonSuggestions = [];

function askBoltonHtml() {
  return `
    <div class="ask-bolton" id="askBolton">
      <form class="ask-row" onsubmit="askBoltonSubmit(event)">
        <input id="askBoltonInput" class="ask-input" type="text" autocomplete="off"
               maxlength="500" placeholder="Ask about the business…"
               aria-label="Ask a question about the business">
        <button class="ask-btn" type="submit" id="askBoltonBtn">Ask</button>
      </form>
      <div class="ask-suggestions" id="askBoltonSuggestions"></div>
      <div class="ask-result" id="askBoltonResult" aria-live="polite"></div>
    </div>`;
}

// Loaded from the server rather than hardcoded here, so the chips can
// never offer a role something it would then be refused for asking —
// the endpoint filters by the same catalogue the classifier is given.
async function loadAskBoltonSuggestions() {
  const host = document.getElementById('askBoltonSuggestions');
  if (!host) return;
  try {
    const res = await fetch(`${API}/ask-bolton/can-answer`);
    if (!res.ok) { host.innerHTML = ''; return; }
    const data = await res.json();
    askBoltonSuggestions = data.can_answer || [];
  } catch (e) {
    host.innerHTML = '';
    return;
  }
  const chips = ASK_BOLTON_CHIPS.filter(c => askBoltonSuggestions.some(a => a.name === c.name));
  host.innerHTML = chips.map(c =>
    `<button type="button" class="ask-chip" onclick="askBoltonAsk(${JSON.stringify(c.q).replace(/"/g, '&quot;')})">${c.label}</button>`
  ).join('');
}

// Short, real questions rather than the catalogue's own descriptions —
// a chip has to fit on a phone and read like something a person would
// actually type. Keyed by answerer name so the filter above can drop
// any the current role may not ask.
const ASK_BOLTON_CHIPS = [
  { name: 'deposits_outstanding', label: 'Deposits owing', q: "Who still owes their deposit?" },
  { name: 'final_payments_outstanding', label: 'Money owing', q: "Who still owes us money on finished jobs?" },
  { name: 'installations_outstanding', label: 'Installs pending', q: "Which jobs still need to be installed?" },
  { name: 'colours_awaiting_installation', label: 'Floors to go down', q: "What colour floors are still to go down?" },
  { name: 'screed_bags_needed', label: 'Screed bags', q: "How many bags of screed do we need?" },
  { name: 'trims_needed', label: 'Trims', q: "How many trims do we need across all jobs?" },
  { name: 'sales_comparison', label: 'Sales vs last year', q: "How do this year's sales compare to last year?" },
  { name: 'monthly_gp_target_progress', label: 'On target?', q: "Are we on target for this month?" },
];

function askBoltonAsk(question) {
  const input = document.getElementById('askBoltonInput');
  if (input) input.value = question;
  askBoltonRun(question);
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
  host.innerHTML = `<div class="ask-thinking">Looking that up…</div>`;

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
    if (!res.ok) {
      // The backend's own message, not a generic one — a missing API
      // key and a Claude outage need different things done about them.
      host.innerHTML = askBoltonNoticeHtml(data.detail || `Something went wrong (${res.status}).`);
    } else {
      host.innerHTML = askBoltonRenderHtml(data);
    }
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
  if (data.ok === false) return askBoltonNoticeHtml(data.error || 'That one is not available.');

  if (data.kind === 'clarify') {
    // A clarifying question, never a guessed assumption — the standing
    // rule for this whole feature. Rendered as a question the person
    // answers by typing again, not as a failure.
    return `<div class="ask-clarify">
        <strong>One thing first:</strong> ${escapeHtmlAsk(data.clarify)}
      </div>`;
  }

  if (data.kind === 'unsupported') {
    const list = (data.can_answer || []).map(x => `<li>${escapeHtmlAsk(x)}</li>`).join('');
    return `<div class="ask-notice">
        ${escapeHtmlAsk(data.message || "I can't answer that one yet.")}
        ${list ? `<div class="ask-can">Things I can answer:<ul>${list}</ul></div>` : ''}
      </div>`;
  }

  const parts = [];
  if (data.answer) parts.push(`<p class="ask-answer">${escapeHtmlAsk(data.answer)}</p>`);
  if (data.headline) parts.push(`<p class="ask-headline">${escapeHtmlAsk(data.headline)}</p>`);
  (data.gaps || []).forEach(g => parts.push(`<p class="ask-gap">${escapeHtmlAsk(g)}</p>`));
  parts.push(askBoltonFiguresHtml(data.figures));
  parts.push(askBoltonTableHtml(data.rows));
  if ((data.sources || []).length) {
    parts.push(`<p class="ask-sources">From: ${data.sources.map(escapeHtmlAsk).join(' · ')}</p>`);
  }
  return parts.join('');
}

// Which keys are money, which are dates, and what to call them on
// screen. Anything not listed still renders — with its raw key
// prettified — so a new answerer is never silently missing a column.
const ASK_MONEY_KEYS = new Set([
  'deposit_due', 'amount_outstanding', 'amount_paid', 'total_incl_vat',
  'gross_profit', 'sales_incl_vat', 'value_incl_vat', 'target', 'shortfall',
  'current_sales_incl_vat', 'current_gross_profit', 'total_deposit_due',
  'total_outstanding',
]);
const ASK_DATE_KEYS = new Set([
  'installation_date', 'invoice_sent_date', 'accepted_at', 'won_on',
]);
const ASK_LABELS = {
  quote_id: 'Quote', job_number: 'Job', client_name: 'Client', branch: 'Branch',
  workflow_status: 'Status', on_hold_reason: 'On hold', installation_date: 'Install',
  confirmed: 'Booked', installer_team: 'Team', days_away: 'Days away',
  days_waiting: 'Days waiting', deposit_due: 'Deposit due',
  amount_outstanding: 'Outstanding', amount_paid: 'Paid', total_incl_vat: 'Total',
  product_name: 'Product', colour: 'Colour', quantity_m2: 'm²',
  boxes_needed: 'Boxes', bags: 'Bags', length_m: 'Linear m', jobs: 'Jobs',
  category: 'Type', colours: 'Colours', line_count: 'Lines', label: 'Period',
  sales_incl_vat: 'Sales', gross_profit: 'Gross profit', fiscal_year: 'Year',
  source: 'Source', won_on: 'Won', value_incl_vat: 'Value',
  accepted_at: 'Accepted', invoice_sent_date: 'Invoiced',
};

function askBoltonLabel(key) {
  return ASK_LABELS[key] || key.replace(/_/g, ' ').replace(/^./, c => c.toUpperCase());
}

function askBoltonCell(key, value) {
  if (value === null || value === undefined || value === '') return '—';
  if (Array.isArray(value)) return value.length ? escapeHtmlAsk(value.join(', ')) : '—';
  if (typeof value === 'boolean') return value ? 'Yes' : 'No';
  if (ASK_MONEY_KEYS.has(key)) return escapeHtmlAsk(R(value));
  if (ASK_DATE_KEYS.has(key)) return escapeHtmlAsk(dateOrDash(value));
  return escapeHtmlAsk(value);
}

function askBoltonFiguresHtml(figures) {
  const entries = Object.entries(figures || {}).filter(([, v]) => v !== null && v !== undefined);
  if (!entries.length) return '';
  return `<div class="ask-figures">${entries.map(([k, v]) =>
    `<div class="ask-figure"><span class="ask-figure-label">${escapeHtmlAsk(askBoltonLabel(k))}</span>
       <span class="ask-figure-value">${askBoltonCell(k, v)}</span></div>`).join('')}</div>`;
}

function askBoltonTableHtml(rows) {
  if (!rows || !rows.length) return '';
  // The union of keys that actually carry a value on at least one row —
  // the answerers deliberately return null rather than 0 for a quantity
  // a job genuinely doesn't have, and a column of dashes helps nobody.
  const keys = [];
  rows.forEach(r => Object.keys(r).forEach(k => {
    if (keys.includes(k)) return;
    if (rows.some(x => x[k] !== null && x[k] !== undefined && x[k] !== ''
                       && !(Array.isArray(x[k]) && !x[k].length))) keys.push(k);
  }));
  const head = keys.map(k => `<th>${escapeHtmlAsk(askBoltonLabel(k))}</th>`).join('');
  const body = rows.map(r =>
    `<tr>${keys.map(k => `<td>${askBoltonCell(k, r[k])}</td>`).join('')}</tr>`).join('');
  return `<div class="ask-table-wrap"><table class="ask-table">
      <thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
}
