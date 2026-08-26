// ===== SHARED FOUNDATION =====
// cache-bust: 2026-08-23T14:55Z — forcing a fresh Render deploy to clear
// a Cloudflare edge cache that got poisoned with a transient 502 error
// page during the previous deploy (confirmed: real origin content was
// always correct — cf-cache-status varied HIT/MISS by edge PoP, which
// is why mobile consistently failed while one desktop request worked).
// No logic change in this commit.
// Loaded first — everything else depends on this. Confirmed Aug 2026,
// Stage 2 of the foundation refactor: only genuinely cross-feature code
// lives here (used by more than one feature area). Feature-specific
// state (e.g. hrEmployeesCache, currentClientDetailId, hrView,
// qClientSearchTimeout, CATEGORY_LABELS) stays with its own file when
// that split happens in a later step — putting it here too early would
// just recreate the "everything in one place" problem this refactor
// exists to fix.

const API = "https://bolton-backend.onrender.com"; // confirmed live Aug 2026 — real quote calc verified against this exact URL (R29,016.48, series 200) before this change was made

// Auth, changed Aug 2026 — real mobile bug, confirmed via Render logs:
// login was succeeding (200, cookie set) but every request after it
// came back 401, consistently, on mobile only. Root cause: the session
// token used to travel as an HttpOnly cookie (SameSite=None; Secure —
// the correct setting for a cross-site cookie, since bolton-frontend
// and bolton-backend are different *sites* per the browser, onrender.com
// being on the Public Suffix List) — but that's necessary, not
// sufficient. Mobile browsers (mobile Safari's ITP in particular) apply
// a SEPARATE third-party-cookie-blocking policy on top of SameSite, and
// silently refuse to persist a cookie set via a cross-site fetch()
// response regardless of how correctly it's configured. `credentials:
// 'include'` and the CORS config were both already correct — this
// wasn't a code bug in the old sense, it was a cookie ever being usable
// cross-site on mobile at all.
// Now: the token comes back in the LOGIN RESPONSE BODY (not a cookie),
// stored here and in localStorage (so it survives a page refresh), and
// re-attached as a plain `Authorization: Bearer <token>` header on
// every request by the same fetch() wrapper below — a normal header
// isn't subject to any cookie policy, so this sidesteps the whole
// problem rather than fighting it.
let sessionToken = localStorage.getItem('bolton_token') || null;
function setSessionToken(token) {
  sessionToken = token;
  if (token) localStorage.setItem('bolton_token', token);
  else localStorage.removeItem('bolton_token');
}

// Owner Preview Mode (confirmed Aug 2026): in-memory only, deliberately
// never persisted (localStorage/cookie) — a fresh login or even a plain
// page refresh always starts back at the Owner's own real view, never
// stuck mid-preview, per the brief's own requirement. Same fetch-wrapper
// trick as the auth header above — it rides along on every request
// automatically, no need to touch any of the dozens of existing
// fetch(`${API}/...`) call sites.
let previewRole = null;   // null | 'sales' | 'admin'

// Timeout, confirmed Aug 2026 — real bug, mobile-specific: native
// fetch() has no default timeout, so a stalled connection just hangs
// forever, with the returned promise never resolving OR rejecting.
// Confirmed via code audit: every "Loading..." landing section
// (Flooring Quotes, Order Index, Clients, Business Overview, Supplier
// Console, etc.) awaits a plain fetch() with no try/catch anywhere —
// if that promise never settles, nothing ever replaces "Loading...",
// with no error and no way out. Most likely trigger on mobile
// specifically: a cellular carrier silently drops an idle TCP
// connection that's been waiting 30+ seconds for a response (e.g.
// during a Render free-tier cold start) WITHOUT the browser ever
// getting a clean network error to reject the fetch promise with — on
// desktop/broadband the same slow cold start just means a longer wait
// that eventually resolves successfully.
// Bound to the same wrapper credentials/preview-role already use
// (rather than a separate fetchWithTimeout() helper requiring every
// call site to opt in) so this protection is automatic on all ~65
// existing and future fetch(`${API}/...`) calls with zero changes
// needed at any of them — same reasoning as the credentials trick just
// above. 20s: comfortably longer than a normal request, short enough
// that "hangs forever" becomes "fails within 20s" instead.
const _nativeFetch = window.fetch.bind(window);
window.fetch = function(url, options = {}) {
  if (typeof url === 'string' && url.startsWith(API)) {
    options = Object.assign({}, options);
    if (sessionToken) {
      options.headers = Object.assign({}, options.headers || {}, {'Authorization': `Bearer ${sessionToken}`});
    }
    if (previewRole && realRole() === 'owner') {
      options.headers = Object.assign({}, options.headers || {}, {'X-Preview-Role': previewRole});
    }
    if (!options.signal) {   // don't clobber a caller-supplied signal — none exist today, but stay safe
      const controller = new AbortController();
      setTimeout(() => controller.abort(), 20000);
      options.signal = controller.signal;
    }
  }
  return _nativeFetch(url, options);
};

// Shared retry-and-fail-visibly wrapper for every landing "Loading..."
// section — the other half of the timeout fix above. A bounded timeout
// alone would only turn "hangs forever silently" into "throws an
// uncaught rejection silently" (still stuck on "Loading..." — the
// exception has nowhere to go) unless something actually catches it and
// updates the DOM. renderFn is the real work (set "Loading...", fetch,
// build the real content, set the final innerHTML) — on any failure
// (timeout, network error, anything thrown while building the view)
// it's retried automatically once after a short delay, since a cold
// start is an expected, self-resolving occurrence, not a bug — the
// first failure shouldn't alarm anyone. Only shows a real error state,
// with a manual retry button, if the second attempt also fails.
// Page Title in Sticky Header (confirmed Aug 2026, follow-up to the
// Sticky Header brief) — a single global setter so "where am I" always
// updates the same way regardless of which screen changed it.
// renderWithRetry() below calls this with its own `label` for every
// screen that already goes through it (13 of them, for free); a few
// screens (Home tiles, Business Settings, Price Book, New Quote) don't
// use renderWithRetry and call this directly; a handful more
// (Client Detail, Job Detail, Order Sheet, Quote Builder once a real
// quote/client is known) call it a second time once their own fetch
// resolves, upgrading the generic label to something specific — e.g.
// "Client Detail" -> "Client: Robert Aspeling" -- same pattern as a
// browser tab title, just also mirrored into the sticky header itself
// so it's visible without needing to look at the tab.
function setPageTitle(title) {
  const el = document.getElementById('pageTitleDisplay');
  if (el) el.textContent = title || '';
  document.title = title ? `${title} — Bolt-on` : 'Bolt-on';
}

async function renderWithRetry(el, label, renderFn) {
  setPageTitle(label);
  try {
    await renderFn();
  } catch (e) {
    await new Promise(r => setTimeout(r, 3000));
    try {
      await renderFn();
    } catch (e2) {
      el.innerHTML = `
        <span class="back-link" onclick="landingView='tiles'; renderLanding();">← Back</span>
        <div class="card">
          <p class="muted">Couldn't load ${label} — the server may still be waking up from idle, or your connection dropped. Safe to retry.</p>
          <button class="primary" onclick="renderLanding()">Tap to retry</button>
        </div>`;
    }
  }
}

let currentUser = null;   // {username, display_name, role} — set once /auth/me or /auth/login succeeds; see index.html's doLogin()/checkAuthOnLoad(). This is always the REAL identity, never swapped by preview.

// Client Info: Company Name, VAT Number, Multiple Phones/Emails
// (confirmed Aug 2026) -- shared between the Add Client form, the Edit
// Details form, and the printed quote/invoice, so all three read a
// client's contact list the exact same way. A client's phone/email are
// stored as one PRIMARY value (Client.phone/email, unchanged) plus an
// optional JSON array of extras (Client.phone_extra/email_extra) --
// these two helpers are the one place that combines them back into a
// flat list; nothing else should parse phone_extra/email_extra
// directly.
function clientPhoneList(client) {
  if (!client) return [];
  const list = [];
  if (client.phone) list.push(client.phone);
  if (client.phone_extra) {
    try { JSON.parse(client.phone_extra).forEach(p => { if (p) list.push(p); }); } catch (e) { /* malformed/legacy value -- ignore rather than break the page */ }
  }
  return list;
}
function clientEmailList(client) {
  if (!client) return [];
  const list = [];
  if (client.email) list.push(client.email);
  if (client.email_extra) {
    try { JSON.parse(client.email_extra).forEach(e => { if (e) list.push(e); }); } catch (e) { /* malformed/legacy value -- ignore rather than break the page */ }
  }
  return list;
}

// Generic addable-row control (confirmed Aug 2026, same brief) -- one
// pair of functions for both the phone list and the email list, on
// both the Add Client and Edit Details forms (4 uses total from 2
// functions, rather than 4 near-identical copies). addContactField()
// appends a new blank, removable row; the very first row in each list
// is rendered without a remove button by the caller (matches the
// brief's own "starting with one entry each... no forced minimum
// beyond that" -- you can always leave the one field blank, but the
// UI never lets the list disappear to literally zero rows).
function addContactField(listId, entryClass, placeholder, value) {
  const list = document.getElementById(listId);
  if (!list) return;
  const row = document.createElement('div');
  row.className = 'addable-row';
  row.style.cssText = 'display:flex; gap:6px; margin-bottom:6px;';
  row.innerHTML = `<input class="${entryClass}" placeholder="${placeholder}" value="${(value || '').replace(/"/g, '&quot;')}" style="flex:1;">` +
    `<button type="button" onclick="this.parentElement.remove();" title="Remove" style="padding:6px 10px;">✕</button>`;
  list.appendChild(row);
}
function collectContactValues(entryClass) {
  return Array.from(document.querySelectorAll('.' + entryClass)).map(el => el.value.trim()).filter(v => v);
}
// Splits a collected list back into {primary, extraJson} for the
// backend's own storage shape (Client.phone/email + phone_extra/
// email_extra) -- kept here alongside the two collectors above so the
// split logic only exists once.
function contactListToFields(values) {
  return { primary: values[0] || '', extraJson: values.length > 1 ? JSON.stringify(values.slice(1)) : '' };
}

// Default Branch per Staff (confirmed Aug 2026, Deposit Amount + Save
// Confirmation + Default Branch brief) — a default only, pre-selected
// wherever a branch dropdown appears for a NEW record (a fresh quote,
// a fresh client); never forced on an existing, already-saved record's
// own stored preference (e.g. the Client Detail edit page shows that
// client's real preferred_branch, untouched by this). Always fully
// changeable per quote/client afterward, per the brief's own words.
const STAFF_DEFAULT_BRANCH = { burgert: 'hermanus', ryno: 'gansbaai', madri: 'gansbaai' };
function defaultBranchForCurrentUser() {
  return STAFF_DEFAULT_BRANCH[currentUser?.username] || 'gansbaai';
}

// Cross-feature state — read/written by more than one feature area:
let currentQuoteId = null;
let flooringProducts = [];   // price book cache — read by Price Book AND Quote Builder
let blindsProducts = [];
let trimProducts = [];
let landingView = 'tiles';   // which landing sub-view is showing — the app shell's own state
// Moved here during the hr.js extraction — a real cross-file
// dependency found during the pre-extraction scoping check: set from
// index.html's onTileClick() (landing tile dispatcher, stays there),
// read from hr.js's renderHR()/hrSubnav(). Same category as
// CATEGORY_LABELS and sortByPriority in earlier rounds.
let hrView = 'employees';
let pendingVinylRange = null;    // handoff: Flooring Quotes tile -> Quote Builder
let pendingCategory = null;
let pendingClientId = null;      // handoff: Client detail "+New Quote" -> Quote Builder
let pendingClientName = null;
let pendingBuilderEstimateId = null;   // handoff: Builder Portal "Start Quote" -> Quote Builder (confirmed Aug 2026) — createQuote() links this estimate to the new quote once it exists, then clears it
let businessSettings = null;   // cached on page load, same pattern as flooringProducts etc — avoids refetching on every quote/print

// Confirmed Aug 2026 — single source of truth for business-wide values
// that were previously hardcoded or duplicated: VAT_PCT was hardcoded
// identically in two separate places in main.py (single-quote endpoint
// and the Order Index list endpoint); the R350 screed bag overage rate
// was hardcoded as literal text in two frontend locations; the default
// deposit % was never actually wired from the frontend at all — every
// quote silently got the backend model's own hardcoded 70%. Call sites
// use `businessSettings?.field ?? fallback` defensively since this
// loads async at startup and may not have resolved yet on first paint.
async function loadBusinessSettings() {
  const res = await fetch(`${API}/business-settings`);
  businessSettings = await res.json();
  // Part 3 finding (confirmed Aug 2026): the printed quote's logo used to
  // be hardcoded in this file's <img> tag with no way to change it per
  // tenant. Empty by default (Blinds & Flooring Studio keeps the
  // existing hardcoded image, unchanged) — only swaps the src if a
  // tenant has actually set one.
  if (businessSettings.logo_base64) {
    const logoEl = document.getElementById('headerLogo');
    if (logoEl) logoEl.src = businessSettings.logo_base64;
  }
}

// Confirmed Aug 2026: role now comes exclusively from the real logged-in
// session (currentUser, set in index.html after /auth/login or
// /auth/me) — replaces the old self-reported "Viewing as" dropdown,
// which let anyone claim to be Owner just by picking it from a <select>.
function realRole() { return currentUser ? currentUser.role : null; }

// Owner Preview Mode (confirmed Aug 2026): currentRole() now returns the
// EFFECTIVE role — the preview, when one is active, otherwise the real
// role. Every existing role-based UI check already calls currentRole()
// (applyRoleVisibility, visibleLandingTiles, the price-locked fields,
// etc.) — deliberately the same "swap the one function" trick used
// server-side in get_current_role(), so none of that logic needed to
// change or be duplicated to respect an active preview. Use realRole()
// instead wherever the actual logged-in identity matters regardless of
// preview (e.g. deciding whether to show the preview control at all).
function currentRole() { return previewRole || realRole(); }

function applyRoleVisibility() {
  const isSales = currentRole() === 'sales';
  document.querySelectorAll('.cost-col').forEach(el => el.style.display = isSales ? 'none' : '');
  // Confirmed: Sales can see the resulting PRICE, but must not be able to
  // change the underlying pricing inputs (list price, discount, m²/box,
  // markup) — these directly control what a client gets charged. Locking
  // here, centrally, rather than scattered per-field, so this is the one
  // place to look when role rules need to change later.
  const priceLockedFields = ['fj_box_price', 'fj_trade_discount', 'fj_m2_per_box', 'fj_markup', 'fj_screed_rate'];
  priceLockedFields.forEach(id => {
    const el = document.getElementById(id);
    if (el) { el.disabled = isSales; el.title = isSales ? 'Locked for Sales role' : ''; }
  });
}

// Money formatting — confirmed Aug 2026: this exact formula previously
// existed THREE times (once here, and separately redefined inside both
// renderOrderIndex and renderBusinessOverview) — real duplication,
// consolidated to this single definition per the refactor brief's
// explicit call to reduce duplication where safe. Every caller now uses
// R() instead of a locally-scoped "money" copy. The `|| 0` safety net
// was present in the local copies but not the original R() — kept here
// so consolidating doesn't change behaviour for a null/undefined value.
function R(n) { return 'R' + (n || 0).toLocaleString('en-ZA', {minimumFractionDigits: 2, maximumFractionDigits: 2}); }

// Also previously redefined locally inside renderOrderIndex only (not a
// triple-duplication like R(), but the same genuinely cross-feature
// shape — worth having in one place before Order Index becomes its own
// file in a later Stage 2 step).
function dateOrDash(d) { return d ? new Date(d).toLocaleDateString('en-ZA') : '—'; }

// Print scaffolding — confirmed Aug 2026: the "set printArea content,
// then trigger the browser print dialog" pattern was repeated
// identically three times (renderPrintDoc — shared by the Quote
// Builder's Print button and the Print Invoice tile, printHoursSummary,
// printCommissionStatement) with only the actual HTML content
// differing. Consolidated the shared mechanics here; each caller still
// builds its own content, since that part is genuinely different per
// document type.
// Simple send path (confirmed Aug 2026, Client-Side Commercial Workflow
// brief, Sprint C) — a small floating panel, created once and reused,
// appended straight to <body> so it works identically regardless of
// which screen called renderPrintDoc() (Quote Builder's "Print / PDF"
// button and the separate Print Invoice landing tile both reach this).
// Genuinely on-screen (unlike anything placed inside #printArea, which
// is invisible outside of an actual print render — see renderPrintDoc()'s
// own comment) and has nothing to do with the print stylesheet at all,
// so no @media print exclusion is needed — it simply isn't part of
// #printArea's contents.
function showSendActionsPanel(docLabel, mailtoLink, waLink, clientEmail) {
  let panel = document.getElementById('sendActionsPanel');
  if (!panel) {
    panel = document.createElement('div');
    panel.id = 'sendActionsPanel';
    panel.style.cssText = 'position:fixed; bottom:16px; right:16px; z-index:1000; background:white; border:2px solid var(--teal); border-radius:10px; padding:14px 16px; box-shadow:0 6px 20px rgba(0,0,0,0.15); font-family:"Figtree",sans-serif; max-width:280px;';
    document.body.appendChild(panel);
  }
  panel.innerHTML = `
    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:8px;">
      <b style="font-size:13px; color:var(--navy);">Send this ${docLabel.toLowerCase()}</b>
      <span onclick="document.getElementById('sendActionsPanel').remove()" style="cursor:pointer; font-size:16px; color:#9aa0a6; line-height:1;">&times;</span>
    </div>
    <p class="muted" style="font-size:11px; margin:0 0 10px;">Use Print → "Save as PDF" to get a file, then attach it below.</p>
    <div style="display:flex; gap:8px; flex-wrap:wrap;">
      <a href="${mailtoLink}" style="flex:1; text-align:center; background:var(--navy); color:white; text-decoration:none; padding:8px 10px; border-radius:6px; font-size:12px; font-weight:600;">📧 Email${clientEmail ? '' : ' (no address)'}</a>
      <a href="${waLink}" target="_blank" rel="noopener" style="flex:1; text-align:center; background:#25D366; color:white; text-decoration:none; padding:8px 10px; border-radius:6px; font-size:12px; font-weight:600;">💬 WhatsApp</a>
    </div>
  `;
}

function triggerPrint(html) {
  document.getElementById('printArea').innerHTML = html;
  window.print();
}

// Moved here during the quote-builder.js extraction — a real cross-file
// coupling found and flagged during the pre-extraction audit before it
// caused a problem: this was defined in index.html's inline script but
// called by price-book.js's renderFlooringTree() (already extracted).
// It worked at runtime only because of script-load timing (price-book.js
// never called it until after every script tag had finished loading),
// not because it was actually available where it was needed. Now that
// quote-builder.js needs it too, it belongs here properly.
function sortByPriority(products) {
  return [...products].sort((a, b) => (a.display_order ?? 100) - (b.display_order ?? 100) || a.product_name.localeCompare(b.product_name));
}

// Shared by the Quote Builder's "Print / PDF" button (in quote-builder.js)
// and the Print Invoice landing tile (renderPrintInvoicePicker/
// printInvoiceForQuote, staying in index.html — landing-tile concern, not
// Quote Builder) — same real quote data either way, just a different
// heading (Quotation vs. Tax Invoice) and salutation. Nothing is re-typed
// or re-entered for the invoice; it's the same line items, totals,
// deposit/balance split as the quote, since there's no separate
// invoicing system yet (Xero is Phase 2 — see README). Belongs here, not
// in quote-builder.js, precisely because both callers need it.
// Split into buildPrintDocHtml() (returns the html + send-link info, no
// side effects) and renderPrintDoc() (the thin wrapper that actually
// prints) — confirmed Aug 2026, Client Page & Quote Detail: Document
// Preview + Inline Edit brief. That brief needs the EXACT same document
// template rendered as an on-screen preview tile (client Order History
// and the Quote/Job Detail page), explicitly "do not build a second,
// separate preview component" — this split is what makes that possible
// without duplicating the HTML-building logic: the preview component
// (order-index.js) calls buildPrintDocHtml() directly, never touches
// #printArea or window.print() at all.

// Dropbox Document Archive & Backup Layer (confirmed Aug 2026) --
// archives whatever buildPrintDocHtml() (below) ACTUALLY produces for
// on-screen viewing, unchanged -- one source for what a document
// looks like, never a second copy that could drift. Manual trigger
// only, same "explicit action" philosophy already established for
// Order Sheets generation. No Dropbox token is configured yet
// (confirmed with Burgert) -- every archive attempt still renders and
// stores a REAL PDF server-side and shows honestly as "Pending" until
// one is set; nothing here is faked.
function documentArchiveStatusBadge(status) {
  if (status === 'uploaded') return `<span class="status-badge active-status">Uploaded</span>`;
  if (status === 'failed') return `<span class="status-badge rejected-status">Failed</span>`;
  return `<span class="status-badge pending-status">Pending</span>`;
}

async function loadDocumentArchiveStatus(entityType, entityId, reference, printSourceId, printDocType) {
  const el = document.getElementById('documentArchiveContent');
  if (!el) return;
  const res = await fetch(`${API}/documents/archive?entity_type=${entityType}&entity_id=${entityId}`);
  const history = res.ok ? await res.json() : [];
  const safeRef = reference.replace(/'/g, "\\'");
  el.innerHTML = `
    ${history.length ? history.map(h => `
      <div style="display:flex; align-items:center; gap:8px; padding:6px 0; border-bottom:1px solid var(--border); flex-wrap:wrap;">
        <b>v${h.version}</b>
        ${documentArchiveStatusBadge(h.status)}
        ${h.is_accepted_version ? `<span class="status-badge active-status" title="Preserved distinctly at the moment this quote was accepted">Accepted</span>` : ''}
        <span class="muted" style="font-size:11px;">${new Date(h.created_at).toLocaleString('en-ZA', {dateStyle:'medium', timeStyle:'short'})}</span>
        <a href="${API}/documents/archive/${h.id}/download" target="_blank" style="font-size:12px; margin-left:auto;">Download</a>
        ${h.status !== 'uploaded' ? `<button onclick="retryArchiveVersion(${h.id}, '${entityType}', ${entityId}, '${safeRef}', ${printSourceId}, '${printDocType}')" style="font-size:12px;">Retry</button>` : ''}
      </div>
      ${h.status !== 'uploaded' && h.failure_reason ? `<div class="muted" style="font-size:11px; margin:2px 0 4px;">${h.failure_reason}</div>` : ''}
    `).join('') : '<p class="muted" style="margin:0 0 10px;">Not archived yet.</p>'}
    <button class="primary" id="archiveNowBtn" onclick="triggerArchiveDocument('${entityType}', ${entityId}, '${safeRef}', ${printSourceId}, '${printDocType}')" style="margin-top:10px;">Archive now</button>
  `;
}

async function triggerArchiveDocument(entityType, entityId, reference, printSourceId, printDocType) {
  const btn = document.getElementById('archiveNowBtn');
  if (btn) { btn.disabled = true; btn.textContent = 'Archiving...'; }
  try {
    const { html } = await buildPrintDocHtml(printSourceId, printDocType);
    const cssRes = await fetch('styles.css');
    const css = cssRes.ok ? await cssRes.text() : '';
    const res = await fetch(`${API}/documents/archive`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ entity_type: entityType, entity_id: entityId, reference, html, css }),
    });
    if (!res.ok) { alert('Could not archive this document.'); return; }
    await loadDocumentArchiveStatus(entityType, entityId, reference, printSourceId, printDocType);
  } catch (e) {
    alert('Could not archive this document — check your connection.');
  } finally {
    const btnAfter = document.getElementById('archiveNowBtn');
    if (btnAfter) { btnAfter.disabled = false; btnAfter.textContent = 'Archive now'; }
  }
}

async function retryArchiveVersion(archiveId, entityType, entityId, reference, printSourceId, printDocType) {
  const res = await fetch(`${API}/documents/archive/${archiveId}/retry`, {method: 'POST'});
  if (!res.ok) { const body = await res.json().catch(() => ({})); alert(body.detail || 'Could not retry this archive.'); return; }
  await loadDocumentArchiveStatus(entityType, entityId, reference, printSourceId, printDocType);
}

async function renderPrintDoc(quoteId, docType) {
  const { html, docLabel, mailtoLink, waLink, clientEmail } = await buildPrintDocHtml(quoteId, docType);
  showSendActionsPanel(docLabel, mailtoLink, waLink, clientEmail);
  triggerPrint(html);
}

async function buildPrintDocHtml(quoteId, docType) {
  const res = await fetch(`${API}/quotes/${quoteId}?role=${currentRole()}`); // print view only ever shows sell prices, never cost/margin, so this is safe regardless of role
  const data = await res.json();
  const logoSrc = document.querySelector('header .logo-row img').src;
  const isInvoice = docType === 'invoice';

  // Full client details (phone/email/address) only exist on a real Client
  // record — a walk-in/one-off quote has no client_id, so falls back to
  // just the typed name with no contact details to show.
  let client = null;
  if (data.quote.client_id) {
    const cRes = await fetch(`${API}/clients/${data.quote.client_id}`);
    if (cRes.ok) client = await cRes.json();
  }

  const biz = await (await fetch(`${API}/business-settings`)).json();

  // Simple send path (confirmed Aug 2026, Client-Side Commercial
  // Workflow brief, Sprint C — "download / share-ready file, email or
  // WhatsApp-ready. Do not over-build CRM automation this sprint").
  // Deliberately NOT a real attachment-send integration (that's the
  // over-build this brief explicitly warns against) — window.print()'s
  // own "Save as PDF" is still the actual file-download step, same as
  // before; these are pre-filled email/WhatsApp starters so getting the
  // saved PDF to the client is one click of composing, not a blank
  // message typed from scratch every time.
  const docLabel = isInvoice ? 'Invoice' : 'Quote';
  const emailSubject = encodeURIComponent(`${docLabel} #${quoteId} from ${biz.business_name || 'us'}`);
  const emailBody = encodeURIComponent(
    `Hi ${data.quote.client_name},\n\nPlease find attached your ${docLabel.toLowerCase()} #${quoteId}, total R${data.total_incl_vat.toFixed(2)} incl VAT.\n\n(Save this page as a PDF first — Print / Save as PDF below — then attach it here before sending.)\n\nKind regards,\n${biz.business_name || ''}`
  );
  const clientEmail = (client && client.email) ? client.email : '';
  const mailtoLink = `mailto:${clientEmail}?subject=${emailSubject}&body=${emailBody}`;
  const waText = encodeURIComponent(
    `Hi ${data.quote.client_name}, here's your ${docLabel.toLowerCase()} #${quoteId} from ${biz.business_name || 'us'} — total R${data.total_incl_vat.toFixed(2)} incl VAT. I'll send the PDF separately.`
  );
  // South African numbers are stored as free text, usually local format
  // (e.g. "082 555 1234") — wa.me needs international format with no
  // leading 0. Only converts when the local-SA-mobile shape is
  // unambiguous (10 digits starting with 0); anything else falls back
  // to NO number (wa.me/?text=... still opens WhatsApp's own contact
  // picker with the message pre-filled) rather than risk guessing wrong
  // and pointing at a stranger's chat.
  const waDigitsRaw = (client && client.phone) ? client.phone.replace(/[^0-9]/g, '') : '';
  const waPhone = (client && client.phone && client.phone.trim().startsWith('+')) ? waDigitsRaw
    : (waDigitsRaw.startsWith('27') && waDigitsRaw.length >= 11) ? waDigitsRaw
    : (waDigitsRaw.startsWith('0') && waDigitsRaw.length === 10) ? ('27' + waDigitsRaw.slice(1))
    : '';
  const waLink = `https://wa.me/${waPhone}?text=${waText}`;

  const rows = data.lines.map(l => {
    let detail = l.category === 'flooring' ? `${l.quantity_m2 || ''} m² — ${l.job_type || ''}`
      : l.category === 'trim' ? `${l.length_m} lm`
      : l.category === 'stairwell' ? `${l.num_stairs} stairs${l.landing_area_m2 ? ` (incl. ${l.landing_area_m2}m² landing)` : ''}`
      : (l.width_mm ? `${l.width_mm}×${l.drop_mm}mm` : '');
    // Screed lines carry a bag allowance — bags_allowed is screed-specific
    // (material lines never set it), so its presence identifies this as
    // a screed line. Confirmed Aug 2026: show the client exactly how many
    // bags are included, and the flat overage rate for anything extra
    // actually used on site.
    const bagNote = l.bags_allowed > 0
      ? `<br><span style="font-size:11px; color:#9aa0a6;">Includes ${l.bags_allowed} bags of smoothing compound. Extra bags used on site charged at R${(businessSettings?.bag_overage_rate ?? 350).toFixed(2)}/bag incl. VAT.</span>`
      : '';
    // Colour is locked to what was actually quoted (a snapshot on the
    // line item itself, confirmed Aug 2026) — shown prominently, bold,
    // right under the product name, so it's unambiguous what to order.
    const colourLine = l.colour ? `<br><b style="color:var(--teal);">Colour: ${l.colour}</b>` : '';
    return `<tr><td>${l.product_name}${colourLine}${bagNote}</td><td class="num">${detail}</td><td class="num">R${l.line_total.toFixed(2)}</td></tr>`;
  }).join('');

  // Real gap found building this (confirmed Aug 2026): #printArea is
  // display:none on screen ALWAYS, except during the browser's own
  // print rendering pass (styles.css's @media print rule) — anything
  // placed inside it is genuinely invisible/unclickable in the normal
  // page, only "visible" in the print output itself, which is exactly
  // backwards from what a clickable Email/WhatsApp action needs. Shown
  // in a real, always-on-screen panel instead by renderPrintDoc() above
  // (showSendActionsPanel()), separate from triggerPrint()'s own
  // printArea/window.print() — deliberately NOT touching triggerPrint()
  // itself, since hr.js also calls it for unrelated printable documents
  // that have nothing to do with this brief. This function itself has
  // no side effects at all now (confirmed Aug 2026) — the document
  // preview tile (order-index.js) calls it directly and just wants the
  // html back, never a send panel or a print dialog.
  const html = `
    <div class="print-doc">
      <div class="doc-header">
        <div>
          <img src="${logoSrc}" style="height:36px;">
          <div style="margin-top:8px; font-size:11px; color:#6b7280; line-height:1.5;">
            ${biz.business_name ? `<b style="color:var(--navy); font-size:12px;">${biz.business_name}</b><br>` : ''}
            ${biz.address ? `${biz.address}<br>` : ''}
            ${biz.phone ? `Tel: ${biz.phone}` : ''}${biz.phone && biz.email ? ' · ' : ''}${biz.email ? biz.email : ''}
            ${biz.vat_number ? `<br>VAT: ${biz.vat_number}` : ''}
          </div>
        </div>
        <div>
          <div class="doc-title">${isInvoice ? `TAX INVOICE #INV-${String(quoteId).padStart(4,'0')}` : `QUOTE #${String(quoteId).padStart(4,'0')}`}</div>
          <div style="text-align:right; font-size:12px; color:#6b7280;">${new Date().toLocaleDateString('en-ZA')}</div>
          ${isInvoice ? `<div style="text-align:right; font-size:11px; color:#6b7280;">Ref: Quote #${quoteId}</div>` : ''}
        </div>
      </div>
      <!-- Client Info: Company Name, VAT Number, Multiple Phones/Emails
           brief §4 (confirmed with Burgert before building) — when a
           company_name is on file, it REPLACES the "Quoted to" name
           line (standard B2B invoice convention), with the individual
           contact kept as a smaller "Attn:" line right underneath so
           nothing about who to actually contact is lost. VAT number
           gets its own line. Phone/Email now show every entry on file
           (clientPhoneList()/clientEmailList() above), not just the
           single primary value. -->
      <div style="margin-bottom:20px; font-size:13px;">
        <div><b>${isInvoice ? 'Invoice to' : 'Quoted to'}:</b> ${client && client.company_name ? client.company_name : data.quote.client_name}</div>
        ${client && client.company_name ? `<div style="font-size:11px; color:#6b7280;">Attn: ${data.quote.client_name}</div>` : ''}
        ${client && client.vat_number ? `<div><b>VAT no:</b> ${client.vat_number}</div>` : ''}
        ${clientPhoneList(client).length ? `<div><b>Phone:</b> ${clientPhoneList(client).join(', ')}</div>` : ''}
        ${clientEmailList(client).length ? `<div><b>Email:</b> ${clientEmailList(client).join(', ')}</div>` : ''}
        ${client && client.address ? `<div><b>Address:</b> ${client.address}</div>` : ''}
        <div><b>Branch:</b> ${data.quote.branch}</div>
      </div>
      <table>
        <thead><tr><th>Description</th><th class="num">Detail</th><th class="num">Total (ex VAT)</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
      <div class="totals">
        ${data.quote.transport_levy ? `<div class="row"><span>Transport levy</span><span>R${data.quote.transport_levy.toFixed(2)}</span></div>` : ''}
        <div class="row"><span>Subtotal (ex VAT)</span><span>R${data.subtotal_ex_vat.toFixed(2)}</span></div>
        ${data.discount_amount ? `<div class="row" style="color:var(--coral);"><span>Discount</span><span>-R${data.discount_amount.toFixed(2)}</span></div>
        <div class="row"><span>Net (ex VAT)</span><span>R${data.total_ex_vat.toFixed(2)}</span></div>` : ''}
        <div class="row"><span>VAT (${((businessSettings?.vat_pct ?? 0.15)*100).toFixed(0)}%)</span><span>R${(data.total_incl_vat - data.total_ex_vat).toFixed(2)}</span></div>
        <div class="row grand"><span>Total (incl VAT)</span><span>R${data.total_incl_vat.toFixed(2)}</span></div>
        ${data.lines.some(l => l.has_delivery_fee) ? `<div class="row" style="color:#6b7280; font-style:italic; font-size:11px;"><span>Delivery included</span><span></span></div>` : ''}
        <div class="row" style="margin-top:10px; padding-top:10px; border-top:1px dashed var(--border);"><span>Deposit (${(data.quote.deposit_pct*100).toFixed(0)}%, due to start)</span><span>R${data.deposit_amount.toFixed(2)}</span></div>
        <div class="row"><span>Balance (on completion)</span><span>R${data.balance_amount.toFixed(2)}</span></div>
      </div>
      ${biz.bank_details ? `<div style="margin-top:20px; padding-top:14px; border-top:1px solid var(--border); font-size:11px; color:#6b7280;"><b>Banking details for deposit payment:</b><br>${biz.bank_details.replace(/\n/g,'<br>')}</div>` : ''}
    </div>
  `;
  return { html, docLabel, mailtoLink, waLink, clientEmail };
}

// ===== Document Preview + Inline Edit (confirmed Aug 2026) =====
// ONE reusable component, two placements — client page's Order History
// list, and the individual Quote/Job Detail page (order-index.js's
// renderOrderDetail) — sharing this exact code, per the brief's own
// "do not build a second, separate preview component... one template,
// two placements." Lives in shared.js (not clients.js or order-index.js)
// specifically because both need it, same reasoning renderPrintDoc
// itself already lives here for.
//
// The "mini visual replica" is the REAL print-doc HTML from
// buildPrintDocHtml() above, shrunk with a CSS transform (styles.css)
// — never a second, separately-rendered thumbnail that could drift
// from what's actually sent. documentPreviewTileHtml() is synchronous
// (embeds straight into a template-literal .map() the same way every
// other row in this app renders) — the real content loads separately,
// via loadDocumentPreview(), which callers fire right after the tile's
// placeholder actually exists in the DOM. Split this way, not
// lazy-loaded on first click, because the brief's own point is that the
// mini version already shows real content at a glance — both
// placements only ever render a handful of tiles at once (one client's
// own job history, or a single quote's own preview), so eagerly
// fetching all of them is cheap in practice.
function documentPreviewTileHtml(previewId, quoteId) {
  return `
    <div class="doc-preview-tile" id="${previewId}">
      <div class="doc-preview-frame" onclick="toggleDocumentPreview('${previewId}')">
        <div class="doc-preview-scale-wrap"><p class="muted" style="padding:16px;">Loading preview…</p></div>
      </div>
      <div class="doc-preview-actions">
        <span class="doc-preview-hint" onclick="toggleDocumentPreview('${previewId}')">Expand / collapse</span>
        <button onclick="event.stopPropagation(); editDocumentPreview(${quoteId})">Edit</button>
      </div>
    </div>`;
}

function toggleDocumentPreview(previewId) {
  const el = document.getElementById(previewId);
  if (el) el.classList.toggle('expanded');
}

async function loadDocumentPreview(previewId, quoteId) {
  const el = document.getElementById(previewId);
  if (!el) return;
  try {
    const { html } = await buildPrintDocHtml(quoteId, 'quote');
    const wrap = el.querySelector('.doc-preview-scale-wrap');
    if (wrap) wrap.innerHTML = html;
  } catch (e) {
    const wrap = el.querySelector('.doc-preview-scale-wrap');
    if (wrap) wrap.innerHTML = '<p class="muted" style="padding:16px;">Could not load preview.</p>';
  }
}

// Edit button (confirmed Aug 2026, brief §2) — opens the real Quote
// Builder for this record via Sprint B's existing line-editing
// capability. Deliberately the SAME function every other "edit this
// quote's lines" entry point already calls — on the Quote Detail page
// this is the identical action as that screen's own "Open in Quote
// Builder (line items)" button, never a second/duplicate entry point.
function editDocumentPreview(quoteId) {
  openQuoteFromIndex(quoteId);   // switches to the Quote Builder tab itself, no separate goToTab needed here
}

// Moved here at v56 (not in the shared.js pass at v53) — genuinely used
// by both price-book.js's renderFlooringTree AND renderFlooringDrill()
// (a landing-page browsing view that stays in index.html) — a real
// cross-file dependency, checked directly, not assumed.
const CATEGORY_LABELS = {
  vinyl: 'Vinyl', laminate: 'Laminate', spc: 'SPC', novilon: 'Novilon',
  carpet: 'Carpet', engineered_wood: 'Engineered Wood', screed: 'Screed',
};

const LANDING_TILES = [
  { id: 'business', title: 'Business Overview', desc: 'Live KPIs, dynamic', ready: true },
  { id: 'orders', title: 'Order Index', desc: 'All jobs, searchable', ready: true },
  { id: 'flooring', title: 'Flooring Quotes', desc: 'Vinyl, SPC, laminate...', ready: true },
  { id: 'blinds', title: 'Blinds', desc: 'Blinds quoting', ready: true },
  { id: 'clients', title: 'Clients', desc: 'Client records', ready: true },
  // 'supplierPrices' (old Price Book screen) and 'supplierUploads' (a
  // "Coming soon" placeholder for PDF import) removed Aug 2026, per
  // explicit confirmation — both were fully superseded by Supplier
  // Console: 'supplierUploads' described a feature that now exists
  // (AI-Assisted Price Sheet Import, inside the Console), and
  // 'supplierPrices' was a second, unaudited way to add/delete
  // products (writes straight to the DB, no staging, no Change Log
  // entry) that bypassed the entire commit-and-audit workflow the
  // Console exists to enforce — confirmed nothing there isn't covered
  // by the Console. The underlying screen/functions weren't deleted
  // (loadFlooring/loadBlinds/loadTrims are still used elsewhere, e.g.
  // populating Quote Builder's product dropdowns) — just made
  // unreachable, since removing this tile was its only entry point.
  { id: 'printInvoice', title: 'Print Invoice', desc: 'Generate & print', ready: true },
  { id: 'hr', title: 'HR & Commission', desc: 'Employees, hours, leave, docs', ready: true },
  { id: 'settings', title: 'Business Settings', desc: 'VAT, deposit %, banking, rates', ready: true },
  { id: 'sessionLog', title: 'Login Activity', desc: 'Who logged in, when, for how long', ready: true },
  { id: 'supplierConsole', title: 'Supplier Console', desc: 'Every supplier\'s real data, one place', ready: true },
  { id: 'changeLog', title: 'Change Log', desc: 'Every price book edit, audited', ready: true },
  { id: 'accounts', title: 'Accounts', desc: 'Staff logins, password reset links', ready: true },
  { id: 'builderPortal', title: 'Builder Portal', desc: 'Referral links, estimates, commission', ready: true },
];

// Default role/tile split (confirmed Aug 2026, proposed per the go-live
// handover and implemented for Burgert to review/adjust after seeing it
// in practice): Sales/Rep (Ryno) is scoped to Sales/Flooring/Blinds
// areas — quoting, clients, orders, printing — and doesn't need (or see
// cost/margin data in) Business Overview, Business Settings, HR &
// Commission, or the supplier price books, which stay Owner/Admin areas.
// This is a frontend visibility layer on top of the real server-side
// enforcement that already existed (strip_sensitive_fields, the
// owner-only business-settings write, etc.) — it doesn't change what the
// backend allows, just what's surfaced in the UI.
const SALES_HIDDEN_TILES = ['business', 'settings', 'hr'];
// Login & Session Activity Log (confirmed Aug 2026): Owner-only, under
// all circumstances — stricter than SALES_HIDDEN_TILES above, which
// only hides from Sales (Admin still sees Business Overview/Settings/
// HR). Uses currentRole() — the EFFECTIVE role — so an Owner previewing
// as Sales or Admin correctly loses this tile too, same as the backend
// blocking the endpoint itself for a previewed non-owner role.
const OWNER_ONLY_TILES = ['sessionLog', 'supplierConsole', 'changeLog', 'builderPortal', 'accounts'];
function visibleLandingTiles() {
  const role = currentRole();
  return LANDING_TILES.filter(t => {
    if (OWNER_ONLY_TILES.includes(t.id) && role !== 'owner') return false;
    if (role === 'sales' && SALES_HIDDEN_TILES.includes(t.id)) return false;
    return true;
  });
}

// ===== Consistent Mobile Back Navigation (confirmed Aug 2026) =====
// Bolton is a browser web app running in the phone's browser, not a
// native installed app — it cannot reposition or take over the phone's
// actual OS-level back button/gesture, that's controlled by Android/
// the browser (confirmed directly, brief's own §0). What CAN be built,
// and is: making that real gesture navigate through Bolton's own screen
// history sensibly, via the browser's actual History API (pushState/
// popstate) — plus a second, consistent, thumb-reachable on-screen Back
// button (index.html's #mobileBackBtn) that does the exact same thing,
// via the exact same history.back() call, never a different mechanism.
//
// No real URL routing — every screen already lived at one single URL,
// with state in plain JS globals (landingView, currentOrderDetailQuoteId,
// currentClientDetailId, hrView), long before this brief. pushState is
// used purely to create real history ENTRIES carrying a snapshot of
// that existing state, without ever changing the address bar — a
// well-established pattern for a single-URL SPA with no server-side
// routes to match anyway.
let restoringNavState = false;   // guards popstate's own render calls from re-pushing a new history entry, which would turn "back" into a no-op
let nextNavIsBaseline = false;   // set once, by showApp() right after login — that first render establishes the history baseline (replaceState) instead of pushing a new entry on top of nothing

function navSnapshot() {
  const quoteBuilderEl = document.getElementById('quoteBuilder');
  if (quoteBuilderEl && quoteBuilderEl.style.display !== 'none') return { tab: 'quoteBuilder' };
  const priceBookEl = document.getElementById('priceBook');
  if (priceBookEl && priceBookEl.style.display !== 'none') return { tab: 'priceBook' };
  return {
    tab: 'landing',
    landingView: typeof landingView !== 'undefined' ? landingView : 'tiles',
    orderDetailQuoteId: typeof currentOrderDetailQuoteId !== 'undefined' ? currentOrderDetailQuoteId : null,
    clientDetailId: typeof currentClientDetailId !== 'undefined' ? currentClientDetailId : null,
    hrView: typeof hrView !== 'undefined' ? hrView : null,
  };
}

function pushNavState() {
  if (restoringNavState) return;   // this render was triggered BY a popstate restoration — don't push another entry on top of the one the browser just navigated to
  if (!currentUser) return;        // never push history entries for the login screen itself
  if (nextNavIsBaseline) {
    nextNavIsBaseline = false;
    history.replaceState({ nav: navSnapshot() }, '', location.href);
  } else {
    history.pushState({ nav: navSnapshot() }, '', location.href);
  }
}

// Same section-visibility toggle as showSection() (index.html),
// WITHOUT its forced landingView='tiles' reset — that reset is correct
// for a deliberate "Home" tap, wrong for restoring an arbitrary prior
// screen from history (which is what applyNavState() below uses this
// for).
function showRawSection(tabName) {
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  const navBtn = document.querySelector(`.tab-btn[data-tab="${tabName}"]`);
  if (navBtn) navBtn.classList.add('active');
  document.getElementById('landing').style.display = tabName === 'landing' ? 'block' : 'none';
  document.getElementById('quoteBuilder').style.display = tabName === 'quoteBuilder' ? 'block' : 'none';
  const priceBookEl = document.getElementById('priceBook');
  if (priceBookEl) priceBookEl.style.display = tabName === 'priceBook' ? 'block' : 'none';
}

function applyNavState(navState) {
  restoringNavState = true;
  if (!navState) {
    landingView = 'tiles';
    showRawSection('landing');
    renderLanding();
  } else if (navState.tab === 'quoteBuilder') {
    showRawSection('quoteBuilder');
  } else if (navState.tab === 'priceBook') {
    showRawSection('priceBook');
  } else {
    landingView = navState.landingView || 'tiles';
    if (navState.orderDetailQuoteId) currentOrderDetailQuoteId = navState.orderDetailQuoteId;
    if (navState.clientDetailId) currentClientDetailId = navState.clientDetailId;
    if (navState.hrView) hrView = navState.hrView;
    showRawSection('landing');
    renderLanding();
  }
  restoringNavState = false;
}

// Confirmed Aug 2026, brief §1 — "ensure the browser's native back
// gesture/button navigates through Bolton's own screen history
// sensibly... rather than behaving unpredictably or exiting the app
// unexpectedly." This is that: every popstate event (fired by the
// phone's real back gesture, the browser's own back button, AND the
// new #mobileBackBtn below, which all go through the identical
// history.back() call) re-renders whatever screen that history entry
// actually describes.
window.addEventListener('popstate', (e) => {
  if (!currentUser) return;   // ignore stray popstate events firing before login (e.g. from a page refresh mid-navigation)
  applyNavState(e.state && e.state.nav);
});

// ===== Keyboard Dismiss on Enter (confirmed Aug 2026) =====
// One global, delegated listener rather than an onkeydown handler on
// every individual field — this app has well over a hundred <input>
// elements across every screen, built up over an entire session's
// worth of briefs, and a per-field approach would both miss existing
// ones and need remembering on every future field too. Delegating on
// `document` catches Enter on any <input> anywhere, including ones
// added by screens built after this brief.
//
// Scoped to <input> ONLY, via an explicit ALLOWLIST of genuinely
// single-line text-like types — never <textarea> (this app has 5:
// client notes x2, business bank details, two AI-import instructions
// boxes — every one of them needs Enter to insert a line break, not
// submit/dismiss, so excluding the whole tag structurally is the
// brief's own required "confirm single-line vs multi-line" check,
// built into the scoping itself rather than a per-field guess), and
// never checkbox/radio/date/file/color, where Enter already means
// something else (toggle a checkbox, open a native date picker) that
// blurring could interfere with unpredictably.
//
// blur() only — never preventDefault() or stopping the event — so any
// field's OWN existing onkeydown="if(event.key==='Enter') doSomething()"
// handler (login username/password, quote client-search) still fires
// exactly as before; this only adds "and now also close the keyboard
// afterward," never replaces what Enter already did.
const KEYBOARD_DISMISS_INPUT_TYPES = ['text', 'search', 'email', 'tel', 'password', 'number', 'url', '', undefined];
document.addEventListener('keydown', (e) => {
  if (e.key !== 'Enter') return;
  if (e.target.tagName !== 'INPUT') return;
  if (!KEYBOARD_DISMISS_INPUT_TYPES.includes(e.target.type)) return;
  e.target.blur();
});

// ===== Client Picker (confirmed Aug 2026, Save Redirect + Client Link
// Missing brief) — the reusable fix for the confirmed root cause of
// duplicated (and possibly other) quotes losing their real client
// link: a free-text prompt() asking for a "client name" that silently
// created a disconnected quote the moment anyone typed anything other
// than an exact re-match of the original text (even a well-intentioned
// note). This replaces that with a real search-and-SELECT picker —
// the only way out is clicking an actual Client record, so the result
// is always a validated client_id, never a name string that might or
// might not match one. Promise-based so a caller just does
// `const picked = await openClientPicker(...); if (!picked) return;`
// — same shape as a native prompt()/confirm(), but returns {id, name}
// or null instead of a string. One shared component (not rebuilt per
// caller) — currently used by Duplicate Quote (order-index.js); the
// Job Detail "link this quote to a client" box (also order-index.js)
// predates this and already has its own inline version of the same
// idea, left as-is rather than churned for its own sake. =====
let clientPickerResolve = null;
let clientPickerSearchTimeout = null;

function openClientPicker(label) {
  return new Promise((resolve) => {
    clientPickerResolve = resolve;
    let panel = document.getElementById('clientPickerPanel');
    if (!panel) {
      panel = document.createElement('div');
      panel.id = 'clientPickerPanel';
      document.body.appendChild(panel);
    }
    panel.innerHTML = `
      <div class="client-picker-overlay" onclick="closeClientPicker(null)">
        <div class="client-picker-box" onclick="event.stopPropagation();">
          <h3 style="margin-top:0;">${label}</h3>
          <input type="text" id="clientPickerSearch" placeholder="Search clients by name..." oninput="clientPickerSearchInput(this.value)" autocomplete="off">
          <div id="clientPickerResults" class="client-picker-results"></div>
          <button onclick="closeClientPicker(null)" style="margin-top:10px;">Cancel</button>
        </div>
      </div>`;
    document.getElementById('clientPickerSearch').focus();
  });
}

async function clientPickerSearchInput(value) {
  clearTimeout(clientPickerSearchTimeout);
  const box = document.getElementById('clientPickerResults');
  if (!box) return;
  if (!value || value.length < 2) { box.innerHTML = ''; return; }
  clientPickerSearchTimeout = setTimeout(async () => {
    const res = await fetch(`${API}/clients?search=${encodeURIComponent(value)}`);
    const matches = await res.json();
    box.innerHTML = matches.length
      ? matches.map(c => `<div class="client-picker-result" onclick="closeClientPicker({id:${c.id}, name:'${c.name.replace(/'/g,"\\'")}'})"><b>${c.name}</b>${c.phone ? ' — '+c.phone : ''}</div>`).join('')
      : '<p class="muted" style="padding:8px;">No matches.</p>';
  }, 250);
}

function closeClientPicker(result) {
  const panel = document.getElementById('clientPickerPanel');
  if (panel) panel.innerHTML = '';
  if (clientPickerResolve) { clientPickerResolve(result); clientPickerResolve = null; }
}
