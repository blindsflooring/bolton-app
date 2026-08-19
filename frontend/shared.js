// ===== SHARED FOUNDATION =====
// Loaded first — everything else depends on this. Confirmed Aug 2026,
// Stage 2 of the foundation refactor: only genuinely cross-feature code
// lives here (used by more than one feature area). Feature-specific
// state (e.g. hrEmployeesCache, currentClientDetailId, hrView,
// qClientSearchTimeout, CATEGORY_LABELS) stays with its own file when
// that split happens in a later step — putting it here too early would
// just recreate the "everything in one place" problem this refactor
// exists to fix.

const API = "http://127.0.0.1:8020"; // swap for deployed backend URL

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
}

function currentRole() { return document.getElementById('roleSelect').value; }

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
async function renderPrintDoc(quoteId, docType) {
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

  const rows = data.lines.map(l => {
    let detail = l.category === 'flooring' ? `${l.quantity_m2 || ''} m² — ${l.job_type || ''}`
      : l.category === 'trim' ? `${l.length_m} lm`
      : l.category === 'stairwell' ? `${l.num_stairs} stairs`
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

  triggerPrint(`
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
      <div style="margin-bottom:20px; font-size:13px;">
        <div><b>${isInvoice ? 'Invoice to' : 'Quoted to'}:</b> ${data.quote.client_name}</div>
        ${client && client.phone ? `<div><b>Phone:</b> ${client.phone}</div>` : ''}
        ${client && client.email ? `<div><b>Email:</b> ${client.email}</div>` : ''}
        ${client && client.address ? `<div><b>Address:</b> ${client.address}</div>` : ''}
        <div><b>Branch:</b> ${data.quote.branch}</div>
      </div>
      <table>
        <thead><tr><th>Description</th><th class="num">Detail</th><th class="num">Total (ex VAT)</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
      <div class="totals">
        <div class="row"><span>Subtotal (ex VAT)</span><span>R${data.subtotal_ex_vat.toFixed(2)}</span></div>
        ${data.discount_amount ? `<div class="row" style="color:var(--coral);"><span>Discount</span><span>-R${data.discount_amount.toFixed(2)}</span></div>
        <div class="row"><span>Net (ex VAT)</span><span>R${data.total_ex_vat.toFixed(2)}</span></div>` : ''}
        <div class="row"><span>VAT (${((businessSettings?.vat_pct ?? 0.15)*100).toFixed(0)}%)</span><span>R${(data.total_incl_vat - data.total_ex_vat).toFixed(2)}</span></div>
        <div class="row grand"><span>Total (incl VAT)</span><span>R${data.total_incl_vat.toFixed(2)}</span></div>
        <div class="row" style="margin-top:10px; padding-top:10px; border-top:1px dashed var(--border);"><span>Deposit (${(data.quote.deposit_pct*100).toFixed(0)}%, due to start)</span><span>R${data.deposit_amount.toFixed(2)}</span></div>
        <div class="row"><span>Balance (on completion)</span><span>R${data.balance_amount.toFixed(2)}</span></div>
      </div>
      ${biz.bank_details ? `<div style="margin-top:20px; padding-top:14px; border-top:1px solid var(--border); font-size:11px; color:#6b7280;"><b>Banking details for deposit payment:</b><br>${biz.bank_details.replace(/\n/g,'<br>')}</div>` : ''}
    </div>
  `);
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
  { id: 'supplierPrices', title: 'Supplier Prices', desc: 'Price books', ready: true },
  { id: 'supplierUploads', title: 'Supplier Uploads', desc: 'PDF import per supplier', ready: false },
  { id: 'printInvoice', title: 'Print Invoice', desc: 'Generate & print', ready: true },
  { id: 'hr', title: 'HR & Commission', desc: 'Employees, hours, leave, docs', ready: true },
  { id: 'settings', title: 'Business Settings', desc: 'VAT, deposit %, banking, rates', ready: true },
];
