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
// Single Active Session Per Login (confirmed Aug 2026) — reset on every
// fresh token so a LATER supersession (e.g. this same account logging in
// on another device again) can trigger the redirect-to-login handling
// below again. See that handler's own comment for the full picture.
let sessionInvalidatedHandled = false;
function setSessionToken(token) {
  sessionToken = token;
  if (token) { localStorage.setItem('bolton_token', token); sessionInvalidatedHandled = false; }
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

// Owner-Only Calculation Breakdown Toggle (confirmed Aug 2026) — "a
// toggle, not a permanent second mode... must never be on by default for
// a new session without the Owner having chosen it," but also "should not
// need to be re-enabled every session necessarily." sessionStorage is the
// deliberate middle ground: survives a page reload/tab refresh within the
// same browser session (so flipping it once covers the rest of a working
// session), but is always gone on a genuinely new session (new tab/window,
// browser restart) — unlike localStorage, which would persist it forever,
// including into a DIFFERENT person's session on a shared machine.
// Gating on WHETHER this is even shown/honoured is currentRole() ===
// 'owner' everywhere it's read (same as every other role check in this
// codebase) — so Owner Preview Mode (previewRole) correctly hides the
// breakdown the instant Burgert previews as Ryno/Madri, without this
// needing any preview-specific code of its own.
let ownerBreakdownVisible = sessionStorage.getItem('bolton_owner_breakdown') === 'true';
function toggleOwnerBreakdown() {
  const cb = document.getElementById('ownerBreakdownToggle');
  ownerBreakdownVisible = !!(cb && cb.checked);
  sessionStorage.setItem('bolton_owner_breakdown', ownerBreakdownVisible ? 'true' : 'false');
  applyRoleVisibility();
  // Re-render whatever calculated line(s) are currently on screen so the
  // breakdown appears/disappears immediately, not just on the next
  // keystroke/reload — same "flip it and see it happen" expectation as
  // every other live-preview control in the Quote Builder.
  if (typeof fjCalc === 'function' && document.getElementById('fj_floor_m2')) fjCalc();
  if (typeof previewCarpetLine === 'function') previewCarpetLine();
  if (typeof previewGenericLine === 'function') previewGenericLine();
  if (typeof currentQuoteId !== 'undefined' && currentQuoteId && typeof loadQuote === 'function') loadQuote();
}

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
  const isApiCall = typeof url === 'string' && url.startsWith(API);
  if (isApiCall) {
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
  const promise = _nativeFetch(url, options);
  // Single Active Session Per Login (confirmed Aug 2026) — the backend
  // already force-ends any OTHER active session for an account the
  // moment a new login happens (login(), main.py — ended_reason=
  // "superseded"), and _resolve_session() there already rejects that
  // session's next request with a clean 401. The gap was entirely here:
  // nothing on the frontend ever looked at a 401, so the old session's
  // next action just fell into whatever generic error handling that one
  // screen happened to have — renderWithRetry() (below) shows a
  // MISLEADING "server may still be waking up... Tap to retry" message
  // that would then fail forever, never actually telling the user
  // they'd been logged out elsewhere. Checked here, once, for every one
  // of the ~65 existing fetch(`${API}/...`) call sites with zero
  // changes needed at any of them — same "one wrapper, all call sites"
  // pattern the token/timeout handling just above already uses.
  // Excludes /auth/login itself: a wrong-password 401 there is normal
  // and must never force a "logged out" redirect.
  if (isApiCall && !url.includes('/auth/login')) {
    promise.then(res => {
      if (res.status === 401 && sessionToken && !sessionInvalidatedHandled) {
        sessionInvalidatedHandled = true;
        setSessionToken(null);
        currentUser = null;
        previewRole = null;
        if (typeof showLogin === 'function') {
          showLogin();
          const errEl = document.getElementById('login_error');
          if (errEl) errEl.textContent = 'You were logged out — most likely because this account logged in elsewhere. Please log in again.';
        }
      }
    }).catch(() => {});
  }
  return promise;
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
// New Quote Starting Screen: Sales Owner / Branch reconciliation
// (confirmed Aug 2026) — real gap found, not assumed: this used to read
// currentUser?.username directly, ignoring Owner Preview Mode entirely.
// The whole point of routing the rare "Burgert logs a job as Ryno/Madri"
// case through Viewing As (New Quote Flow Clarity brief §3, reconciled
// with Staff Account Defaults) depends on the resulting quote actually
// being attributed to the previewed rep — this is the one function that
// needed to change for that to be true. effectiveUsernameForQuoting()
// is the same "which real person is this effectively acting as" logic
// currentRole() already applies to role, just resolved down to a
// username these two staff maps can key off.
function effectiveUsernameForQuoting() {
  if (previewRole === 'sales') return 'ryno';
  if (previewRole === 'admin') return 'madri';
  return currentUser?.username;
}
function defaultBranchForCurrentUser() {
  return STAFF_DEFAULT_BRANCH[effectiveUsernameForQuoting()] || 'gansbaai';
}

// Sales Owner default (confirmed Aug 2026, Vinyl Quoting UX Redesign
// proposal §07 — approved) — REAL GAP CLOSED: q_owner had no default at
// all, unlike q_branch above. sales_owner values already match staff
// usernames one-to-one (confirmed against #q_owner's own <option>
// values, index.html), so this is a map rather than currentUser.
// username directly on purpose — a Trusted Tester account (or any
// future username that isn't one of the three real Sales Owner
// options) falls through to the explicit 'burgert' fallback instead of
// silently setting an unmatched <select> value. Same "default, but
// fully overridable, never forced on an already-saved record" rules as
// defaultBranchForCurrentUser() above.
const STAFF_DEFAULT_OWNER = { burgert: 'burgert', ryno: 'ryno', madri: 'madri' };
function defaultSalesOwnerForCurrentUser() {
  return STAFF_DEFAULT_OWNER[effectiveUsernameForQuoting()] || 'burgert';
}

// New Quote Starting Screen: Clarity Pass / Staff Account Defaults,
// reconciled (confirmed Aug 2026) — Sales Owner is never shown as a
// picker for anyone now (nothing to choose — see #q_owner's own
// comment, index.html); Branch collapses to one small toggle for
// whichever branch is the LESS common one for the currently-effective
// account. Sets both the toggle's own label and its checked state from
// the real underlying #q_branch value — called wherever that value
// might have just changed for a reason OTHER than the toggle itself
// (login, Viewing As switch, resetQuoteBuilderUI()'s own defaulting) so
// the toggle never silently disagrees with what's actually about to be
// submitted.
function syncQuoteOwnerBranchControls() {
  const branchEl = document.getElementById('q_branch');
  const toggleEl = document.getElementById('q_branch_toggle');
  const labelEl = document.getElementById('qBranchToggleLabel');
  if (!branchEl || !toggleEl || !labelEl) return;
  const defaultBranch = STAFF_DEFAULT_BRANCH[effectiveUsernameForQuoting()] || 'gansbaai';
  const otherBranch = defaultBranch === 'hermanus' ? 'gansbaai' : 'hermanus';
  labelEl.textContent = `Quoting for ${otherBranch.charAt(0).toUpperCase()}${otherBranch.slice(1)}?`;
  toggleEl.checked = (branchEl.value === otherBranch);
}
function onQBranchToggleChange() {
  const defaultBranch = STAFF_DEFAULT_BRANCH[effectiveUsernameForQuoting()] || 'gansbaai';
  const otherBranch = defaultBranch === 'hermanus' ? 'gansbaai' : 'hermanus';
  document.getElementById('q_branch').value = document.getElementById('q_branch_toggle').checked ? otherBranch : defaultBranch;
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
let pendingCarpetType = null;    // handoff: Flooring Quotes tile -> Carpet tab (confirmed Aug 2026, Carpet Tab, Type Split, and Product Filtering brief) — same shape as pendingVinylRange above, one level more specific since Carpet needs a type AND a range
let pendingCarpetRange = null;
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
  const isOwner = currentRole() === 'owner';
  // Owner-Only Calculation Breakdown Toggle (confirmed Aug 2026) —
  // .cost-col used to mean "hide from Sales" (isSales); it now means
  // "only ever shown to the Owner, and only with the breakdown toggle
  // on" — Admin (Madri) loses this too, per that brief's own explicit
  // "Owner-only" framing.
  // Margin Becomes Owner-Only; Price Gets a Colour Signal (confirmed
  // Aug 2026) — REVERSES this comment's own next sentence from earlier
  // today: margin_pct is back in this same Owner-only/toggle-gated
  // class (strip_sensitive_fields' own comment, main.py) — the Quote
  // Lines table's Margin column has the cost-col class back too
  // (index.html), so it hides/shows together with Cost again. The
  // colour signal every role always sees regardless of this toggle
  // lives on the Price column instead (l.margin_band).
  const showBreakdown = isOwner && ownerBreakdownVisible;
  document.querySelectorAll('.cost-col').forEach(el => el.style.display = showBreakdown ? '' : 'none');
  document.querySelectorAll('.owner-breakdown-toggle-wrap').forEach(el => el.style.display = isOwner ? '' : 'none');
  const breakdownToggleEl = document.getElementById('ownerBreakdownToggle');
  if (breakdownToggleEl) breakdownToggleEl.checked = ownerBreakdownVisible;
  const fjBreakdown = document.getElementById('fjBreakdownSection');
  if (fjBreakdown) fjBreakdown.style.display = showBreakdown ? '' : 'none';
  const fjGpCard = document.getElementById('fjBreakdownGpCard');
  if (fjGpCard) fjGpCard.style.display = showBreakdown ? '' : 'none';
  // Move Product Constants Off the Quoting Screen (confirmed Aug 2026) —
  // Wastage %/m² per box/Price per m²/Trade discount %/Glue rate, same
  // toggle-gated treatment as the rest of the breakdown — visible only
  // to the Owner, only with the breakdown on, purely informational
  // (readonly, index.html) since they're no longer quote-level inputs.
  const vinylConstants = document.getElementById('vinylConstantsBreakdown');
  if (vinylConstants) vinylConstants.style.display = showBreakdown ? '' : 'none';
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

// Owner-Only Calculation Breakdown Toggle (confirmed Aug 2026) — ONE
// generic renderer, per the brief's own explicit "build this once...
// not a separate implementation per category" instruction, used by
// every live-preview box that isn't Vinyl's own fjCalc() (Vinyl keeps
// its existing, already-detailed static Result panel — see
// #fjBreakdownSection, index.html — since it's entirely client-side
// already and has no equivalent calc-dict shape to iterate over).
// Reads whichever of these keys are actually PRESENT on the calc/line
// object passed in — a category that returns none of them (e.g. Misc)
// simply renders nothing extra, and a future category's calculation
// function only needs to return a field with one of these names (or a
// new one added to this same lookup) for its own breakdown to appear
// here for free, no new per-category branch required.
const OWNER_BREAKDOWN_FIELD_LABELS = {
  material_cost_total: 'Material cost',
  unit_cost: 'Unit cost',
  gripper_cost_total: 'Gripper cost',
  underfelt_cost_total: 'Underfelt cost',
  cutting_fee_total: 'Cutting fee',
  glue_cost_total: 'Adhesive/glue cost',
  compound_cost_total: 'Compound cost',
  delivery_fee_total: 'Delivery/courier fee',
  vinyl_cost_total: 'Vinyl cost (stairwell)',
  nosing_cost_total: 'Nosing cost (stairwell)',
  subtotal: 'Subtotal (before markup)',
  markup_multiplier: 'Markup applied',
  labour_cost_total: 'Labour cost (real)',
  labour_charged_total: 'Labour charged to client',
};
const OWNER_BREAKDOWN_FIELD_ORDER = Object.keys(OWNER_BREAKDOWN_FIELD_LABELS);

// Margin Becomes Owner-Only; Price Gets a Colour Signal (confirmed Aug
// 2026) — one shared helper so every price/margin display in the app
// (Quote Builder's lines table, every category's live preview, the
// overall-margin line) reads the same discrete, two-family colour
// language the same way, never a one-off computed locally. Red is the
// ONLY alarming shade (below the confirmed 30% floor) — reuses
// var(--coral), the same colour this app already uses everywhere else
// for a warning/alarming state, per Burgert's own "angry colour"
// framing. Both greens are "acceptable or better" (his own words:
// "green should always be acceptable, different shades of green
// though"), distinguished from each other only. margin_band is computed
// server-side (margin_band_for(), main.py) from the real margin_pct
// BEFORE that number is stripped for non-Owner roles — this function
// never sees or needs the real percentage, only the band name.
function marginBandColor(band) {
  if (band === 'red') return 'var(--coral)';
  if (band === 'green_bright') return 'var(--margin-green-bright)';
  if (band === 'green') return 'var(--margin-green)';
  return '';   // no band computed (e.g. a category the server hasn't threaded settings through for) — no colour, never a guess
}

// Returns an HTML fragment (or '' when nothing should show) — the caller
// appends it to whatever preview box it's already building. Never called
// from anywhere that isn't already gated on currentRole()==='owner'
// showing the box at all would be pointless for Sales/Admin, since the
// server already stripped every one of these fields from `calc` for
// them (strip_sensitive_fields, main.py) — this is a display nicety on
// top of that real, server-side gate, never a substitute for it.
function ownerBreakdownHtml(calc) {
  if (!(currentRole() === 'owner' && ownerBreakdownVisible)) return '';
  const rows = OWNER_BREAKDOWN_FIELD_ORDER
    .filter(k => calc[k] !== undefined && calc[k] !== null)
    .map(k => {
      const label = OWNER_BREAKDOWN_FIELD_LABELS[k];
      const display = (k === 'markup_multiplier') ? `×${calc[k].toFixed(2)}` : R(calc[k]);
      return `<div style="display:flex; justify-content:space-between; font-size:12px; color:var(--muted-text,#666);"><span>${label}</span><span>${display}</span></div>`;
    }).join('');
  const vatPct = businessSettings?.vat_pct ?? 0.15;
  const inclVatRow = calc.line_total !== undefined
    ? `<div style="display:flex; justify-content:space-between; font-size:12px; color:var(--muted-text,#666); margin-top:2px;"><span>Line, incl VAT</span><span>${R(calc.line_total * (1 + vatPct))}</span></div>`
    : '';
  if (!rows && !inclVatRow) return '';
  return `<div style="margin-top:8px; padding-top:8px; border-top:1px dashed var(--border,#ddd);">
    <div style="font-size:11px; font-weight:700; color:var(--coral); margin-bottom:4px;">BREAKDOWN (Owner only)</div>
    ${rows}${inclVatRow}
  </div>`;
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
// showSendActionsPanel() REMOVED (confirmed Aug 2026, Full Real-Browser
// Walkthrough & Audit brief — Document Preview Overlap bug). It was a
// fixed-position panel (bottom:16px; right:16px) appended straight to
// <body>, with no awareness of whatever content was scrolling underneath
// it — on mobile this covered real Document Preview content (a line item
// was fully hidden behind it in the reported recording), and its Email
// button stayed clickable even with "(no address)" shown, i.e. a control
// presented as available that couldn't actually succeed. It existed only
// because #printArea is invisible outside an actual print render (see
// buildPrintDocHtml()'s own comment below), so a clickable Email/WhatsApp
// action needed a separate always-on-screen home — true when this shipped,
// but the standardized Mail button (documentActionBarHtml() below,
// sendDocumentEmail()) has since become that home instead: a normal
// inline button, not an overlay, that already refuses cleanly when there's
// no email on file rather than showing a dead-looking one. Confirmed this
// panel is what renderPrintDoc() (the Print button/flow) still popped up
// on top of the document — retired here rather than re-approved, since
// the brief's own point is that the original agreed Send behaviour IS the
// mailto-only one the Mail button already does. Print now just prints.

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
  const { html } = await buildPrintDocHtml(quoteId, docType);
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
  // Deliberately NOT a real attachment-send integration — not possible
  // from a browser regardless of implementation (confirmed again, Send
  // button brief) — these are pre-filled email/WhatsApp starters so
  // composing the message is one click, not typed from scratch. The
  // instructional line in the body used to say "save this page as a
  // PDF first" (the only way to get one, before the Dropbox archive
  // existed) — now that every Print/Send action already auto-archives
  // this exact document (acceptQuoteAction()/printInvoiceForQuote(),
  // order-index.js/index.html), it points at the real Dropbox folder
  // instead: flat, per-branch, filed by client name (folder-flatten
  // pass, confirmed Aug 2026) — same _branch_folder_name() logic as
  // the backend (main.py), mirrored here only for this message's own
  // wording, not for anything that decides where the file actually goes.
  const docLabel = isInvoice ? 'Invoice' : 'Quote';
  const branchFolder = data.quote.branch ? (data.quote.branch.charAt(0).toUpperCase() + data.quote.branch.slice(1).toLowerCase()) : 'Unassigned';
  const emailSubject = encodeURIComponent(`${docLabel} #${quoteId} from ${biz.business_name || 'us'}`);
  const emailBody = encodeURIComponent(
    `Hi ${data.quote.client_name},\n\nPlease find attached your ${docLabel.toLowerCase()} #${quoteId}, total R${data.total_incl_vat.toFixed(2)} incl VAT.\n\n(The PDF is saved automatically in Dropbox — Bolton/${branchFolder}/ — attach it from there before sending, it can't be attached automatically here.)\n\nKind regards,\n${biz.business_name || ''}`
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
    // Skirting/Trim category fix (confirmed Aug 2026, Vinyl Redesign:
    // Real Usage Findings brief §1) — real, stored 'skirting' category
    // now exists (main.py); without this, a skirting line's length
    // would silently vanish from the actual client-facing printed/PDF
    // document, not just an internal screen.
    // NEXBAC 920 Tile is m²-only, never LM (Persistent Summary Panel /
    // Carpet Tab integration gap, confirmed Aug 2026 — carpet_category
    // is now set on a Tile line too, so it needs its own branch here or
    // it prints a blank "LM (10 m²)" on the real client-facing document).
    let detail = (l.category === 'flooring' && l.carpet_category === 'carpet_tile') ? `${l.quantity_m2 || ''} m²`
      : (l.category === 'flooring' && l.carpet_category) ? `${l.quantity_lm || ''} LM (${l.quantity_m2 || ''} m²)`
      : l.category === 'flooring' ? `${l.quantity_m2 || ''} m² — ${l.job_type || ''}`
      : (l.category === 'trim' || l.category === 'skirting') ? `${l.length_m} lm`
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
  // page, only "visible" in the print output itself. That's exactly why
  // a clickable Email action needed a home outside of it — originally a
  // floating always-on-screen panel (showSendActionsPanel(), removed —
  // see renderPrintDoc()'s own comment above), now the standardized Mail
  // button (documentActionBarHtml()/sendDocumentEmail()) instead, a
  // normal inline button in the regular page flow, not inside
  // #printArea. This function itself has no side effects at all
  // (confirmed Aug 2026) — the document preview tile (order-index.js)
  // calls it directly and just wants the html back, never a print
  // dialog.
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

// Send button (confirmed Aug 2026) — a genuinely SEPARATE action from
// Print, not bundled together the way the existing Print flow's own
// send-actions-panel is (that stays exactly as it was — "Print stays
// exactly as it is today, no changes there" is the brief's own explicit
// instruction). This opens the user's default mail client directly, no
// intermediate panel, and — the real, explicit requirement this adds —
// refuses to open a blank/broken mailto at all when there's no email on
// file, saying so clearly instead. Reuses buildPrintDocHtml() entirely
// for the actual email/subject/body computation (zero duplication) —
// this function's only job is the "is there actually an email" check
// and firing the mailto.
async function sendDocumentEmail(quoteId, docType) {
  const { docLabel, mailtoLink, clientEmail } = await buildPrintDocHtml(quoteId, docType);
  if (!clientEmail) {
    alert(`No email address on file for this client — add one on their Client record first, then try Send again.`);
    return;
  }
  window.location.href = mailtoLink;
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
// docType (confirmed Aug 2026, Document Action Bar brief) — optional,
// defaults to 'quote' so all three pre-existing call sites (client
// Order History, Job Detail, Order Index Quick View) keep working
// completely unchanged. The single old bespoke "Edit" button inside
// this tile is gone — superseded by the standardized 5-button bar
// (documentActionBarHtml() below), rendered right under the tile now,
// same "one consistent, predictable place" the brief asks for instead
// of Edit living in a different spot per document type.
function documentPreviewTileHtml(previewId, id, docType, opts) {
  docType = docType || 'quote';
  return `
    <div class="doc-preview-tile" id="${previewId}">
      <div class="doc-preview-frame" onclick="toggleDocumentPreview('${previewId}')">
        <div class="doc-preview-scale-wrap"><p class="muted" style="padding:16px;">Loading preview…</p></div>
      </div>
      <div class="doc-preview-actions">
        <span class="doc-preview-hint" onclick="toggleDocumentPreview('${previewId}')">Expand / collapse</span>
      </div>
    </div>
    ${documentActionBarHtml(docType, id, previewId, opts)}`;
}

function toggleDocumentPreview(previewId) {
  const el = document.getElementById(previewId);
  if (el) el.classList.toggle('expanded');
}

async function loadDocumentPreview(previewId, id, docType) {
  docType = docType || 'quote';
  const el = document.getElementById(previewId);
  if (!el) return;
  try {
    const { html } = docType === 'ordersheet' ? await buildOrderSheetPrintHtml(id) : await buildPrintDocHtml(id, docType);
    const wrap = el.querySelector('.doc-preview-scale-wrap');
    if (wrap) wrap.innerHTML = html;
  } catch (e) {
    const wrap = el.querySelector('.doc-preview-scale-wrap');
    if (wrap) wrap.innerHTML = '<p class="muted" style="padding:16px;">Could not load preview.</p>';
  }
}

// Standard Document Action Bar (confirmed Aug 2026) — same five
// actions, same order, same visual style across every Quote/Invoice/
// Order Sheet preview placement, per the brief's own words: "Right now
// these actions exist in different places with different names
// depending on which screen you're on." Print and Mail are never
// reimplemented here — this just calls the exact same functions
// already shipped (renderPrintDoc/printInvoiceForQuote/printOrderSheet,
// sendDocumentEmail/sendOrderSheetEmail), so what those two actions DO
// is completely unchanged, only WHERE they're offered is standardized.
//
// Edit, resolved per document type (confirmed with Burgert):
// - Quote: always enabled — opens Quote Builder, freely editable at
//   any stage, no restriction, matching existing behaviour exactly.
// - Invoice: disabled once this quote has ever had a real Invoice
//   archived (checked by the caller, passed in as opts.editDisabled —
//   see applyInvoiceEditLock(), order-index.js) — "no editing after
//   an invoice has been sent, full stop... the correct process is a
//   supplementary invoice" via the existing Duplicate action. This is
//   a UI-level steer at the Invoice entry point only — it does NOT add
//   any new restriction to the underlying Quote/Quote Builder itself,
//   which stays exactly as freely editable as the Quote rule above
//   requires; the same record just can't be edited FROM the Invoice
//   context anymore once sent.
// - Order Sheet: disabled once "placed" — enforced server-side too
//   (update_order_sheet_line()/add_order_sheet_line()/
//   delete_order_sheet_line(), main.py), not just hidden here.
//
// Save, resolved per document type (the brief's own explicit "these
// are different actions, don't assume" — genuinely different per
// type, not one shared meaning):
// - Quote: saveDocumentArchive() below detects whether this id is the
//   quote currently open in Quote Builder and, if so, defers to the
//   EXISTING saveQuote() (header fields — client/branch/description —
//   the one thing not already saved immediately by each line's own
//   add/edit/delete). Not currently open in the builder (i.e.
//   triggered from a read-only preview card)? Same meaning as Invoice/
//   Order Sheet below — there's no in-progress form to save from a
//   read-only context either.
// - Invoice / Order Sheet: neither has a separate in-progress edit
//   STATE to persist (every field already saves immediately on its
//   own) — Save here means "capture a fresh archive snapshot to
//   Dropbox right now," on demand, decoupled from Print/Mark-as-Placed
//   auto-archiving it.
function documentActionBarHtml(docType, id, previewId, opts) {
  opts = opts || {};
  const editDisabled = !!opts.editDisabled;
  const editReason = (opts.editDisabledReason || '').replace(/"/g, '&quot;');
  const editFn = docType === 'ordersheet' ? `openOrderSheetDetail(${id})` : `openQuoteFromIndex(${id})`;
  const printFn = docType === 'quote' ? `renderPrintDoc(${id}, 'quote')`
    : docType === 'invoice' ? `printInvoiceForQuote(${id})`
    : `printOrderSheet(${id})`;
  const mailFn = docType === 'ordersheet' ? `sendOrderSheetEmail(${id})` : `sendDocumentEmail(${id}, '${docType}')`;
  const style = (disabled) => `font-size:12px; padding:4px 10px; background:none; border:1.5px solid ${disabled ? '#c7c7c7' : 'var(--navy)'}; color:${disabled ? '#c7c7c7' : 'var(--navy)'}; border-radius:5px; cursor:${disabled ? 'not-allowed' : 'pointer'};`;
  return `
    <div class="doc-action-bar" style="display:flex; gap:6px; flex-wrap:wrap; margin-top:8px;">
      <button onclick="event.stopPropagation(); toggleDocumentPreview('${previewId}')" style="${style(false)}">View</button>
      <button id="docActionEditBtn_${docType}_${id}" ${editDisabled ? `disabled title="${editReason}"` : `onclick="event.stopPropagation(); ${editFn}"`} style="${style(editDisabled)}">Edit</button>
      <button onclick="event.stopPropagation(); ${printFn}" style="${style(false)}">Print</button>
      <button onclick="event.stopPropagation(); saveDocumentArchive('${docType}', ${id})" style="${style(false)}">Save</button>
      <button onclick="event.stopPropagation(); ${mailFn}" style="${style(false)}">Mail</button>
    </div>`;
}

// Save (confirmed Aug 2026) — see documentActionBarHtml()'s own
// docstring above for the full reasoning on what Save means per type.
async function saveDocumentArchive(docType, id) {
  if (docType === 'quote' && typeof currentQuoteId !== 'undefined' && currentQuoteId === id) {
    return saveQuote();
  }
  try {
    let reference, html, entityType, branch;
    if (docType === 'ordersheet') {
      const sheet = await (await fetch(`${API}/order-sheets/${id}`)).json();
      reference = sheet.order_number;
      entityType = 'OrderSheet';
      branch = sheet.branch;
      ({ html } = await buildOrderSheetPrintHtml(id));
    } else {
      const qData = await (await fetch(`${API}/quotes/${id}?role=${currentRole()}`)).json();
      entityType = docType === 'invoice' ? 'Invoice' : 'Quote';
      reference = docType === 'invoice' ? ('INV-' + (qData.quote.job_number || id)) : (qData.quote.job_number || ('Q-' + id));
      branch = qData.quote.branch;
      ({ html } = await buildPrintDocHtml(id, docType));
    }
    const cssRes = await fetch('styles.css');
    const css = cssRes.ok ? await cssRes.text() : '';
    const res = await fetch(`${API}/documents/archive`, {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ entity_type: entityType, entity_id: id, reference, html, css, branch }),
    });
    const result = await res.json();
    if (result.status === 'uploaded') alert('Saved — a new version has been uploaded to Dropbox.');
    else if (result.status === 'pending') alert('Saved — will upload to Dropbox automatically once connected (currently pending).');
    else alert(`Saved locally, but the Dropbox upload failed: ${result.failure_reason || 'unknown error'}`);
  } catch (e) {
    alert('Could not save this document right now — check your connection and try again.');
  }
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
  // Master Workflow proposal §02/§05/§06 (confirmed Aug 2026) — the
  // "LEAD" stage of the master flow, which had nowhere to live at all
  // before this: the earliest tracked stage was previously a Quote.
  // Placed right after Order Index, before quoting itself — a lead is
  // the step BEFORE a quote exists.
  { id: 'leads', title: 'Leads', desc: 'New enquiries, before they’re a quote', ready: true },
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
  // Trusted Tester Accounts brief §3 (confirmed Aug 2026) — "one place
  // Burgert can review [flags], rather than hunting through the Order
  // Index." Owner-only (added to OWNER_ONLY_TILES below), same as
  // Session Log/Change Log.
  { id: 'flaggedItems', title: 'Flagged for Review', desc: 'Issues testers spotted, one list', ready: true },
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
const OWNER_ONLY_TILES = ['sessionLog', 'supplierConsole', 'changeLog', 'builderPortal', 'accounts', 'flaggedItems'];
// Trusted Tester Accounts brief (confirmed Aug 2026) — same scope as
// Sales (they're here for client/quote/job work, not business
// operations), even though they get Sales's pricing-restriction
// counterpart LIFTED (full cost/margin visibility, confirmed with
// Burgert) — tile visibility and pricing visibility are two
// independent axes in this codebase already, so combining "Sales-like
// tile scope" with "Admin-like pricing visibility" needs no new
// mechanism, just this second role check alongside the Sales one
// below. 'business' (Business Overview Dashboard) is ALSO enforced
// server-side now (analytics_overview(), main.py) — this hides the
// tile too, but the real boundary is the 403, not this.
const TRUSTED_TESTER_HIDDEN_TILES = ['business', 'settings', 'hr'];
function visibleLandingTiles() {
  const role = currentRole();
  return LANDING_TILES.filter(t => {
    if (OWNER_ONLY_TILES.includes(t.id) && role !== 'owner') return false;
    if (role === 'sales' && SALES_HIDDEN_TILES.includes(t.id)) return false;
    if (role === 'trusted_tester' && TRUSTED_TESTER_HIDDEN_TILES.includes(t.id)) return false;
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
  // Widened Quote Builder (confirmed Aug 2026, Vinyl Quoting UX Redesign
  // proposal §03, approved) — same body-class toggle as showSection()
  // (index.html); kept in sync here for the same reason this whole
  // function mirrors that one's display logic — see this function's own
  // comment above.
  document.body.classList.toggle('quote-builder-active', tabName === 'quoteBuilder');
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
