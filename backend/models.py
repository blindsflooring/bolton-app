"""
Data models for Blinds & Flooring Studio bolt-on — Phase 1 (Price Book + Quote Builder)

NOTE ON XERO SCOPES (per research pass, Aug 2026):
Xero has no separate "accounting.banktransactions" scope. Invoices, quotes,
purchase orders AND bank transactions all live under accounting.transactions.
So banking exclusion is enforced here as a CODE-LEVEL RULE, not a scope toggle:
this backend must never call Xero's bank transaction / bank feed endpoints,
and must never request the `bankfeeds` scope when that integration is added
in Phase 2. See xero_client.py (Phase 2) for the enforced allow-list of
endpoints this app is permitted to call.
"""
from datetime import datetime, date
from enum import Enum
from typing import Optional
from sqlmodel import SQLModel, Field

# Multi-tenant groundwork (confirmed Aug 2026 — cheap now, painful to
# retrofit onto live data later). Single tenant today ("1" = Blinds &
# Flooring Studio); every business-data table below carries a tenant_id
# so every query CAN be tenant-scoped from day one, even though there's
# only one tenant to scope against right now. No tenant-switcher, no way
# to create a new tenant through the app yet — this is invisible
# groundwork, not a feature. See main.py's _ensure_tenant_id_columns()
# for how this lands on an already-live database without re-entering
# any existing data, and get_current_tenant() for where the scoping
# value comes from at request time (the logged-in user's own tenant_id).
DEFAULT_TENANT_ID = "1"


class StairwellType(str, Enum):
    closed = "closed"                    # both sides closed — 900mm nosing, R250/stair
    one_side_open = "one_side_open"      # 900+500mm nosing, R300/stair
    both_sides_open = "both_sides_open"  # 900+500+500mm nosing, R350/stair


STAIRWELL_NOSING_MM = {
    StairwellType.closed: 900,
    StairwellType.one_side_open: 1400,
    StairwellType.both_sides_open: 1900,
}
STAIRWELL_LABOUR_PER_STAIR = {
    StairwellType.closed: 250.0,
    StairwellType.one_side_open: 300.0,
    StairwellType.both_sides_open: 350.0,
}
TILES_PER_STAIR = 3   # confirmed Aug 2026: tread coverage = 3 planks x standard plank width per stair (was 2 — corrected per 3 independent flooring reps)
STAIR_AREA_M2 = 0.45  # confirmed Aug 2026: 900mm wide tread x (300mm going + 200mm riser) = 0.9 x 0.5 = 0.45m² per stair


class JobType(str, Enum):
    """Flooring job type — multiplier confirmed by Burgert, ex VAT."""
    smooth = "smooth"              # base rate x1
    over_tiles = "over_tiles"      # base rate x1.5
    removed_tiles = "removed_tiles"  # base rate x2


JOB_TYPE_MULTIPLIERS = {
    JobType.smooth: 1.0,
    JobType.over_tiles: 1.5,
    JobType.removed_tiles: 2.0,
}


class UserRole(str, Enum):
    owner = "owner"       # Burgert — full access
    admin = "admin"       # Madri — price book edit, quotes, imports, send POs, CRM/HR/Admin areas
    sales = "sales"       # Ryno — build/view quotes, selling price only, no margin, Sales/Flooring/Blinds areas


class User(SQLModel, table=True):
    """Real per-person login account (confirmed Aug 2026 — replaces the old
    self-reported 'Viewing as' role dropdown, flagged in the go-live
    handover as a blocking dependency before a Builder-Rep Portal can be
    built safely: a client-supplied role query param meant anyone could
    just claim to be Owner). Table name deliberately NOT "user" — that's a
    reserved word in Postgres and this project has already hit one real
    table-naming bug on Supabase from an unquoted reserved-ish name."""
    __tablename__ = "app_user"
    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: str = Field(default=DEFAULT_TENANT_ID, index=True)
    username: str = Field(unique=True, index=True)   # lowercase, e.g. "burgert"
    display_name: str
    password_hash: str          # pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex> — see auth.py
    role: str                   # UserRole value
    active: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)
    # Old Password Still Works incident (confirmed Aug 2026) — real gap
    # found investigating it: nothing recorded WHEN password_hash last
    # changed, so "confirm the timestamp it was last changed" (the
    # brief's own explicit ask) had no answer at all. Set by both
    # /auth/change-password and any server-side reset going forward
    # (change_password(), _post_rls_security_precaution(), main.py).
    # None only for an account that has never had its password changed
    # since this field was added.
    password_changed_at: Optional[datetime] = None


class PasswordResetToken(SQLModel, table=True):
    """Self-Service Password Reset (Owner-Triggered Link) brief
    (confirmed Aug 2026) — the actual reason this brief exists: every
    reset before this required generating a plaintext password and
    relaying it to the staff member, meaning Burgert always knew their
    password, even briefly. This is the opposite design: Burgert
    triggers a one-time LINK (never a password), and only the staff
    member ever sees/sets the real new password, via
    POST /auth/reset-password below.

    One row per reset attempt, permanent (not deleted once used or
    expired) — same "a token record is only meaningful if it can't be
    quietly changed/removed" reasoning as UserSession/AuditLog, and
    lets "was this link ever used" stay answerable later. token is a
    high-entropy random string (secrets.token_urlsafe(32), main.py) —
    unguessable is the entire security property this whole feature
    rests on, no separate secret/signature needed."""
    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: str = Field(default=DEFAULT_TENANT_ID, index=True)
    user_id: int = Field(foreign_key="app_user.id")
    token: str = Field(unique=True, index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    created_by: str   # the Owner username who triggered this reset
    expires_at: datetime
    used_at: Optional[datetime] = None


class UserSession(SQLModel, table=True):
    """Server-side session record backing the login cookie. Stored in the
    DB (not in-memory) so sessions survive a Render backend restart/redeploy
    — an in-memory dict would silently log everyone out on every deploy."""
    id: Optional[int] = Field(default=None, primary_key=True)
    token: str = Field(unique=True, index=True)
    user_id: int = Field(foreign_key="app_user.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: datetime   # confirmed Aug 2026: fixed 24h session length from login — not permanent, not aggressive re-login mid-shift
    ended_at: Optional[datetime] = None   # Login & Session Activity Log Phase 1 (confirmed Aug 2026): real logout time. NULL means either still active, OR ended by natural 24h expiry (no explicit logout call) — the session-log endpoint tells the two apart by comparing expires_at to now at read time, no background job needed. A real logout now sets this instead of deleting the row, so the log has real history — see /auth/logout and _resolve_session()'s corresponding check that an ended_at session is no longer valid even if expires_at hasn't passed yet.
    # Single Active Session per User (confirmed Aug 2026) — a REAL
    # stored field this time, unlike ended_reason's own read-time-only
    # computation elsewhere (session_log(), main.py): that approach
    # deliberately relied on "logout vs expiry" being fully derivable
    # from ended_at/expires_at timing alone, with no third option. A
    # session superseded by a new login also just has ended_at set at
    # an arbitrary moment before natural expiry — genuinely
    # indistinguishable from a real logout by timing alone, so a real
    # explicit "logout" | "superseded" | None value is unavoidable
    # here. None on every row that predates this brief (they really
    # were plain logouts, or logic that only ever recorded "logout" —
    # session_log() falls back to "logout" for these, never silently
    # relabeling historical data).
    ended_reason: Optional[str] = None


class HourType(str, Enum):
    normal = "normal"
    overtime = "overtime"
    sunday = "sunday"
    public_holiday = "public_holiday"


class LeaveType(str, Enum):
    annual = "annual"
    sick = "sick"
    unpaid = "unpaid"
    other = "other"


class LeaveRequestStatus(str, Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"


class TrimProduct(SQLModel, table=True):
    """Trim/skirting price book entry — pine skirting and aluminium trims.
    Priced per linear metre (lm), not per m².

    Two pricing modes, confirmed Aug 2026:
    - "fixed": sell price is a flat R/lm figure set directly (used for pine
      skirting — Burgert's "final installed price", not a cost-derived
      formula: 69mm=R80, 96mm=R145, 140mm=R195, quarter round=R45)
    - "markup": sell price = (cost_ex_vat_per_lm x (1 + vat_pct)) x
      markup_multiplier (CORRECTED Aug 2026 — used for aluminium trims:
      add VAT to the trade cost first, THEN apply the 50% markup on top,
      confirmed "trim book price plus vat then add 50%". Cost used for
      margin stays the raw ex-VAT figure.)
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: str = Field(default=DEFAULT_TENANT_ID, index=True)
    product_name: str
    profile_code: str = ""       # e.g. "S299", or blank for skirting
    category: str = "skirting"   # "skirting" | "stair_nose" | "reducer" | "carpet_strip" | "quarter_round"
    supplier: str
    cost_ex_vat_per_lm: float
    vat_pct: float = 0.15        # confirmed Aug 2026 — used in markup mode: cost x (1+vat) x markup
    wastage_pct: float = 0.08    # confirmed Aug 2026 — buffer for offcuts/mitres when ordering, affects cost only (client is charged for actual length, not the extra bought)
    pricing_mode: str = "fixed"  # "fixed" | "markup"
    fixed_sell_price_per_lm: Optional[float] = None   # required if pricing_mode == "fixed"
    markup_multiplier: float = 1.5                    # used if pricing_mode == "markup"
    unit: str = "lm"
    last_updated: datetime = Field(default_factory=datetime.utcnow)
    source: str = "manual"


class FloorPrepProduct(SQLModel, table=True):
    """Floor-prep/screed materials — levelling & patching compounds,
    bonding agents, moisture barriers, adhesives (confirmed Aug 2026,
    Screed Calculator: Extra Rooms brief). Lives in the Supplier Console
    like every other product category, per the brief's explicit Section
    5 instruction — coverage-rate reference data must be Console-
    editable, not hardcoded inline in the calculator, so a rate change
    is handled the same audited way as any other supplier pricing update.

    coverage_basis distinguishes the THREE genuinely different formula
    shapes the real products use (confirmed from Azura's own Floor
    Preparation & Adhesives price list, brief Section 2) — a single
    "coverage_rate" number means something different depending on this:
    - "kg_per_m2_per_mm": levelling/patching compounds (LEVELiTe F10/F30,
      PATCHiTe) — kg needed = area x thickness x coverage_rate. The
      brief's Section 3 calculated-mode formula is written specifically
      for this shape.
    - "m2_per_L": bonding agents/liquid adhesives (BONDiTe, GRIPiTe V50)
      — L needed = area / coverage_rate, thickness-independent.
    - "m2_per_pack" / "m2_per_kg": simpler single-ratio products (iTe
      SLURRY's whole-pack coverage, VAPORiTe, GRIPiTe H80) — kept as
      Console reference data per Section 5 even though the brief's own
      calculated-mode formula (Section 3) only exercises the first two
      shapes above; not wired into a calculator formula of its own yet.

    cost_ex_vat_per_pack: genuinely NOT supplied by the brief's own
    Section 2 reference table (pack size + coverage rate only, no
    pricing) — added anyway, defaulting None/blank, since real cost-
    tracking (the banked item this brief explicitly supersedes) needs a
    real price to ever produce a Rand figure. Burgert sets this the same
    way any other product's price gets set — via the Console."""
    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: str = Field(default=DEFAULT_TENANT_ID, index=True)
    supplier: str
    product_name: str
    pack_size: float           # e.g. 20 (kg) or 25 (L) — magnitude only, see pack_unit
    pack_unit: str = "kg"      # "kg" | "L"
    coverage_rate: float
    coverage_basis: str = "kg_per_m2_per_mm"   # "kg_per_m2_per_mm" | "m2_per_L" | "m2_per_pack" | "m2_per_kg"
    cost_ex_vat_per_pack: Optional[float] = None
    last_updated: datetime = Field(default_factory=datetime.utcnow)
    source: str = "manual"


class FlooringProduct(SQLModel, table=True):
    """Flooring price book entry. Job-type multiplier applied at quote time,
    so one product entry covers all three job types.

    pricing_type distinguishes SCREED (job-type multiplier applies — Smooth
    x1 always, Over Tiles and Removed Tiles editable per product via
    over_tiles_multiplier/removed_tiles_multiplier, since your real 8-year
    rates are NOT a clean 1.5x/2x — deZIGN S200 screed is 130/160/250, i.e.
    ~1.23x/1.92x, confirmed from your actual spreadsheet Aug 2026) from
    MATERIAL (vinyl, laminate, oak flooring etc. — flat price per m²,
    unaffected by job type; the substrate prep cost is a separate screed
    line item on the same quote, not baked into the material price).
    Confirmed with Burgert Aug 2026: vinyl price stays flat regardless of
    job type."""
    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: str = Field(default=DEFAULT_TENANT_ID, index=True)
    product_name: str          # the range/product name, e.g. "Aspen Premium Range 2.5mm" — NOT the colour, see colour field below
    colour: str = ""            # confirmed Aug 2026: real structured field, not baked into product_name — so the exact colour ordered from the supplier stays locked and clearly visible on the actual quote. Same range at different colours = separate price book entries.
    supplier: str
    pricing_type: str = "material"   # "screed" | "material"
    flooring_category: str = "vinyl"  # "vinyl" | "laminate" | "spc" | "novilon" | "carpet" | "engineered_wood" | "screed" — for dashboard grouping
    display_order: int = 100         # confirmed Aug 2026: lower number = shown first in dropdowns/price book — manual priority, not auto usage tracking (not enough real quote history to learn from yet). Set your best-sellers low, e.g. series 200 at 10.
    # Supplier Console Field Sequence Redesign (confirmed Aug 2026 —
    # root cause of the Como Flooring pricing bug, finally identified:
    # the Console never had a dedicated "price per box" field, so when a
    # supplier's sheet states box price, it had nowhere correct to go
    # and got written into base_cost_ex_vat instead). base_cost_ex_vat
    # (and price_zone_a/b/c below) are now SYSTEM-CALCULATED —
    # price_per_box_ex_vat / m2_per_pack — recomputed and overwritten by
    # the Supplier Console commit endpoint (see recompute_calculated_
    # prices() in main.py) every time price_per_box_ex_vat or
    # m2_per_pack changes, and read-only in the Console UI. Never set
    # these directly from an edit or an AI-import mapping again.
    base_cost_ex_vat: float          # supplier cost per m² (screed: base rate; material: RRP/cost basis — see sell_markup_multiplier for actual sell price). CALCULATED, see price_per_box_ex_vat below.
    sell_markup_multiplier: float = 1.3  # MATERIAL only (confirmed Aug 2026): sell = base_cost_ex_vat x this. Default changed to your confirmed real markup (×1.3, 30%) so it's automatic without setup — still fully adjustable per product or per quote.
    wastage_pct: float = 0.08        # 8% default wastage
    trade_discount_pct: float = 0.0  # e.g. 0.30 for Azura vinyl
    settlement_discount_pct: float = 0.0  # a further discount some suppliers offer on top of trade discount — kept entirely as margin, never passed through to a lower client price (matches how BlindsProduct already handles it)
    tile_width_mm: Optional[float] = None   # confirmed Aug 2026 (Supplier Console brief) — real format from Azura's own price sheet: width x length x thickness mm, e.g. "184.15 x 1219.2 x 2.0". Now genuinely used — see tiles_per_pack below.
    tile_length_mm: Optional[float] = None
    tile_thickness_mm: Optional[float] = None
    sku: Optional[str] = None            # confirmed Aug 2026, Standard Import Format brief — a supplier's own product code, where they provide one. Purely informational, nothing else keys off it.
    wear_layer_mm: Optional[float] = None  # confirmed Aug 2026, Standard Import Format brief — vinyl/LVT wear layer thickness, where applicable (not every supplier states one). Purely informational, same as SKU — no pricing/quote logic reads this.
    # Master Spreadsheet System of Record (confirmed Aug 2026): a
    # product that existed in Bolton but is absent from a supplier's
    # newly re-imported master sheet has been discontinued BY THE
    # SUPPLIER — flagged here, never auto-deleted and never silently
    # left looking current (Section 3 of the brief). Deliberately does
    # NOT hide the product from anything — still selectable in quotes,
    # still appears in the flooring calculator, exactly as before —
    # this is a visible warning flag for Burgert to act on manually,
    # not an automatic removal. See import_master_spreadsheet() (main.py)
    # for where this gets staged during a re-import.
    discontinued: bool = False
    # Builder Referral Portal, Phase 1 pilot (confirmed Aug 2026) — "a
    # simple 'available to builder portal' flag on a product, capped at
    # 2 active at a time" per the brief's own hard constraint. The cap
    # is enforced server-side in the Supplier Console commit endpoint
    # (main.py) — NOT here, since a plain model field can't validate
    # against every OTHER row's value at assignment time.
    available_to_builder_portal: bool = False
    # Courier/Delivery Cost Toggle (confirmed Aug 2026, Courier Toggle
    # brief) — reuses THIS existing field, deliberately no separate
    # courier_enabled/courier_rate_per_m2 pair: courier IS this cost,
    # confirmed by Burgert directly (the brief's proposed R15/m² for
    # Aspen is this exact same figure this field already exists for).
    # A nonzero value here IS the "on" state — no separate boolean, so
    # there's no way for a toggle and a rate to disagree with each
    # other. CORRECTED Aug 2026 (the line below used to say "never
    # marked up" — that was simply wrong, not a deliberate-then-changed
    # design: the real formula, calculate_flooring_line() in
    # calculations.py, bundles this into the pre-markup subtotal
    # alongside boxes+glue, so it IS marked up — confirmed deliberate by
    # Burgert directly, Aug 2026, not a bug). A Bolton-NATIVE business
    # setting, same family as trade_discount_pct/wastage_pct/glue_rate_
    # per_m2/labour_rate_per_m2 — reflects Burgert's own cost structure
    # with this specific supplier, never anything the supplier's own
    # price list states, so it's deliberately NOT in the spreadsheet
    # re-import's GOVERNED_FLOORING_FIELDS whitelist (index.html) —
    # never touched by a re-import. 0.0 (off) by default for every
    # supplier — hardwired to R15.00/m² for Aspen specifically via a
    # one-time startup migration + SupplierDefault backfill, see
    # on_startup() (main.py).
    delivery_fee_per_m2: float = 0.0
    over_tiles_multiplier: float = 1.5    # SCREED only, editable per product (confirmed Aug 2026: your real 8-year rates are NOT a clean 1.5x/2x — e.g. deZIGN S200 screed is 130/160/250, ratios ~1.23x/1.92x, not 1.5x/2x. Default 1.5 is a generic placeholder — set your real number per product.
    removed_tiles_multiplier: float = 2.0  # SCREED only, editable per product — same as above
    m2_per_pack: Optional[float] = None  # for purchase-order pack-quantity calc (Phase 3, §13)
    tiles_per_pack: Optional[float] = None  # for stairwell calc (3 tiles/stair, confirmed Aug 2026 — 3 planks x standard plank width = tread width per stair). CHANGED Aug 2026 (Supplier Console brief): now auto-derived from tile_length_mm x tile_width_mm x m2_per_pack whenever the console commits an edit touching any of those three — see recompute_tiles_per_pack() in main.py. Still a plain editable field for products without full dimension data.
    unit: str = "m2"
    last_updated: datetime = Field(default_factory=datetime.utcnow)
    source: str = "manual"           # "manual" | "pdf_import" | "legacy_import"
    # Supplier & Price Book Management Console (confirmed Aug 2026):
    glue_rate_per_m2: Optional[float] = None       # per-product default (R/m², same figure as the Floor Job builder's manual glue-rate field) — pre-fills that field when this product is selected; still fully overridable per quote, same pattern as BusinessSettings.default_labour_rate_per_m2 already does for labour
    labour_rate_per_m2: Optional[float] = None     # per-product default labour rate — same pre-fill-not-mandate pattern
    default_own_staff: bool = True                 # per-product default labour source — same pre-fill pattern
    # Azura zone pricing (confirmed Aug 2026, real rule from Azura's own
    # "Suggested Retail Price List" — every Azura/deZIGN product has
    # three real prices side by side, one per zone). All three are
    # stored on every Azura product (not just the zone this business
    # currently uses) so a future tenant in a different zone already has
    # their own correct column, no new fields needed later. Resolved to
    # the effective base_cost_ex_vat at quote-calc time via
    # resolve_zone_price() in main.py, based on BusinessSettings.
    # pricing_zone — never a per-quote manual choice. None on every
    # non-Azura product; base_cost_ex_vat is used directly for those,
    # completely unchanged. CALCULATED as of the Field Sequence Redesign
    # (Aug 2026) — see price_per_box_zone_a/b/c below, same rule as
    # base_cost_ex_vat: never set these three directly again.
    price_zone_a: Optional[float] = None
    price_zone_b: Optional[float] = None
    price_zone_c: Optional[float] = None
    # Price per box (confirmed Aug 2026, Supplier Console Field Sequence
    # Redesign brief — the actual root cause of the Como Flooring
    # pricing bug). These are now the RAW INPUT fields, always populated
    # for every product: either read directly off the source sheet
    # (suppliers like Como that state box price), or back-calculated
    # ONCE at entry/import time (price_per_m2 x m2_per_pack) for
    # suppliers whose sheet only states per-m2 (e.g. Azura) — see
    # Section 2 of the brief. base_cost_ex_vat / price_zone_a/b/c above
    # are always DERIVED from these + m2_per_pack, never the other way
    # around, so a box price can never again land directly in a per-m2
    # field by mistake. price_per_box_ex_vat pairs with base_cost_ex_vat
    # (non-zone-priced products); price_per_box_zone_a/b/c pair with
    # price_zone_a/b/c (zone-priced products, e.g. Azura, Como Flooring)
    # — a product uses one pair or the other, matching whichever of
    # base_cost_ex_vat/price_zone_* it already used.
    price_per_box_ex_vat: Optional[float] = None
    price_per_box_zone_a: Optional[float] = None
    price_per_box_zone_b: Optional[float] = None
    price_per_box_zone_c: Optional[float] = None


class SupplierDefault(SQLModel, table=True):
    """Per-supplier default values (confirmed Aug 2026, Supplier Console
    brief) — modeled as its own small table (one row per supplier name)
    rather than fields bolted onto BusinessSettings, since these are
    naturally one-row-per-supplier and more are plausible later.

    default_trade_discount_pct: a DEFAULT, not an enforced rule, and NOT
    retroactive — read once, at the moment a new product is staged, to
    pre-fill that product's own trade_discount_pct field; the product's
    own stored value is what's authoritative from then on. Changing this
    default later never touches already-existing (or already-staged-
    but-not-yet-committed) products, only ones created after the change.

    pricing_zone (confirmed Aug 2026): which of a zone-priced product's
    price_zone_a/b/c columns is that SUPPLIER's effective base price —
    used by resolve_zone_price() (main.py). Previously a single
    business-wide BusinessSettings.pricing_zone applied to every zone-
    priced supplier; now each such supplier (Azura, Como Flooring,
    potentially more later) sets its own independently, e.g. Azura on
    Zone A while Como Flooring is on Zone B. A one-time startup backfill
    (see on_startup() in main.py) seeds this from whatever the global
    setting was, for every supplier that already had zone pricing at
    the time this shipped — so no existing supplier's effective price
    changed the moment this went live. BusinessSettings.pricing_zone
    itself is untouched/still exists, now used only as the fallback for
    a brand-new zone-priced supplier that hasn't had its own zone set
    yet — not the deciding value for any supplier that already has one.

    No table-level uniqueness constraint on (tenant_id, supplier) — the
    commit endpoint's own logic (reusing the existing CommitChange path
    once a row exists, NewEntityImport only for the first one) is what
    keeps this to one row per supplier per tenant, same trust boundary
    as everything else the Console writes through that one endpoint.

    default_delivery_fee_per_m2 (confirmed Aug 2026, Courier Toggle
    brief — reuses FlooringProduct.delivery_fee_per_m2, no separate
    courier field): same pre-fill-new-products-only, non-retroactive
    pattern as default_trade_discount_pct — None means "no supplier-
    level default set," which lets a brand-new product fall through to
    FlooringProduct.delivery_fee_per_m2's own default (0.0/off) with no
    special-casing needed. Hardwired to 15.00 for Aspen specifically via
    a one-time startup backfill (on_startup(), main.py) — every other
    supplier gets no row/no override, i.e. off, exactly as the brief
    specifies."""
    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: str = Field(default=DEFAULT_TENANT_ID, index=True)
    supplier: str = Field(index=True)
    default_trade_discount_pct: Optional[float] = None
    pricing_zone: Optional[str] = None
    default_delivery_fee_per_m2: Optional[float] = None


class BlindsProduct(SQLModel, table=True):
    """Blinds price book entry.
    Margin formula (confirmed): net cost = book price less 45% trade discount,
    less further 7.5% settlement discount. Selling price = book price + VAT (15%).
    This yields ~49% margin at full price."""
    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: str = Field(default=DEFAULT_TENANT_ID, index=True)
    product_name: str
    supplier: str
    mechanism: str            # e.g. roller, venetian, vertical
    fabric_tier: str = ""
    width_band: str = ""
    drop_band: str = ""
    book_price: float
    trade_discount_pct: float = 0.45
    settlement_discount_pct: float = 0.075
    vat_pct: float = 0.15
    last_updated: datetime = Field(default_factory=datetime.utcnow)
    source: str = "manual"


class Employee(SQLModel, table=True):
    """Confirmed Aug 2026 — HR & Commission module, Phase A. notes field is
    Owner+Admin only, stripped for self-service views the same way cost/
    margin is stripped for Sales elsewhere in this app."""
    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: str = Field(default=DEFAULT_TENANT_ID, index=True)
    full_name: str
    role_title: str = ""          # Sales, Admin, Installer, Builder-Rep, etc.
    start_date: Optional[date] = None
    birthday: Optional[date] = None
    id_number: str = ""
    phone: str = ""
    email: str = ""
    employment_status: str = "active"   # "active" | "inactive"
    thirteenth_cheque_eligible: bool = False
    notes: str = ""                # Owner + Admin only
    commission_eligible: bool = False
    commission_role_type: str = "pure_sales"   # "pure_sales" | "builder_rep" | "other" — confirmed Aug 2026: pure_sales is % of GP (tiered), builder_rep is % of ex-VAT price per job (per category, since builder-reps already earn from installation labour)
    sales_owner_key: str = ""      # links this employee to Quote.sales_owner (e.g. "ryno") for commission calc — kept as a separate free-text key rather than assuming they always match, since sales_owner predates this table
    created_at: datetime = Field(default_factory=datetime.utcnow)


class CommissionRate(SQLModel, table=True):
    """Confirmed Aug 2026 — the configurable rate card the brief explicitly
    calls for ("so rates can be adjusted later without code changes").
    One table represents both commission models:

    - pure_sales: basis="gp", category=None, tier_min/tier_max define GP
      bands (e.g. R0-50k=8%, R50k-100k=10%, R100k+=12%). tier_max=None
      means "and above".
    - builder_rep: basis="ex_vat_price", category set per product category
      (e.g. "blinds"=10%), tier_min/tier_max unused (flat rate per job,
      not tiered) — confirmed Aug 2026: builder-reps earn a flat % of the
      ex-VAT price per job, by category, since they already earn from
      installation labour and a GP-based model would double-reward that.
    """
    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: str = Field(default=DEFAULT_TENANT_ID, index=True)
    role_type: str            # "pure_sales" | "builder_rep"
    basis: str                # "gp" | "ex_vat_price"
    category: Optional[str] = None   # flooring / blinds / trim / skirting / stairwell — builder_rep only
    tier_min: float = 0.0
    tier_max: Optional[float] = None   # None = "and above"
    rate_pct: float           # e.g. 0.08 for 8%
    active: bool = True


class CommissionPayment(SQLModel, table=True):
    """Historical record of what was actually paid — confirmed Aug 2026,
    per the brief's requirement for an auditable payment history separate
    from the calculated-but-not-yet-paid statement."""
    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: str = Field(default=DEFAULT_TENANT_ID, index=True)
    employee_id: int = Field(foreign_key="employee.id")
    period_year: int
    period_month: int
    calculated_amount: float
    paid_amount: float
    paid_date: Optional[date] = None
    notes: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Builder(SQLModel, table=True):
    """Builder Referral Portal, Phase 1 pilot (confirmed Aug 2026) — a
    local builder/handyman, NOT an Employee (no login, no staff record,
    no relation to CommissionRate's own "builder_rep" role type, which
    is an unrelated internal-staff commission model that happens to
    share a similar name — do not confuse the two).

    slug is the entire access control mechanism for this pilot, per the
    brief's explicit "no login/account system" requirement — whoever
    has the link (bolton.app/q/{slug}) can submit estimates as this
    builder. active=False immediately blocks the public endpoints below
    from resolving this slug at all (404, same as a slug that never
    existed) — this IS "revoking a link" per the brief's own
    verification requirement, no token/session to separately expire."""
    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: str = Field(default=DEFAULT_TENANT_ID, index=True)
    name: str
    slug: str = Field(unique=True, index=True)
    active: bool = True
    phone: str = ""
    email: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)


class BuilderEstimate(SQLModel, table=True):
    """One self-serve estimate a builder submitted through their portal
    link (confirmed Aug 2026, Builder Referral Portal Phase 1). Deliberately
    stores its own price snapshot (quoted_price_ex_vat/incl_vat/deposit_amount)
    rather than only a product_id + area — prices can change in the price
    book later, and this must always show what the builder was ACTUALLY
    quoted at the time, same "denormalized snapshot" principle
    QuoteLineItem.product_name already follows.

    linked_quote_id: None until Burgert/Madri picks this up into a real
    Bolton quote (Section 3 — "this becomes the starting point of a real
    quote, not a separate parallel system"). Commission is deliberately
    NOT a stored field here — computed at read time from the LINKED
    QUOTE's own final_payment_date (commission earned on payment
    received, confirmed directly) x 6%, same "derive from the existing
    source of truth, don't duplicate state that could drift" principle
    already used for ended_reason (Login Activity brief) — see
    /builder/{slug}/statement and /admin/builder-estimates in main.py."""
    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: str = Field(default=DEFAULT_TENANT_ID, index=True)
    builder_id: int = Field(foreign_key="builder.id")
    client_name: str
    client_contact: str = ""
    site_address: str = ""
    area_m2: float
    product_id: int = Field(foreign_key="flooringproduct.id")
    product_name: str          # denormalized snapshot — same reasoning as QuoteLineItem
    quoted_price_ex_vat: float
    quoted_price_incl_vat: float
    deposit_amount: float
    linked_quote_id: Optional[int] = Field(default=None, foreign_key="quote.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)


class QuotePhoto(SQLModel, table=True):
    """Quote Photo Attachments, Phase 1 pilot (confirmed Aug 2026).
    Quote-level, not client-level, by design — every read filters by
    quote_id, so a client's other quotes never show these.

    builder_estimate_id (not quote_id) is set for photos a builder
    attached while submitting an estimate — there's no real Quote yet
    at that point. The moment staff links that estimate to a real quote
    (link_builder_estimate_to_quote, main.py), quote_id gets backfilled
    onto those same rows so "these travel with the estimate and land on
    the resulting quote" holds without ever duplicating the file itself.

    Only storage_path is kept here — the actual bytes live in Supabase
    Storage (see photo_storage.py), chosen over this app's other
    existing file mechanism (backend/uploads/, used by HR Documents)
    specifically because these need to reliably survive routine
    deploys over the life of a quote, confirmed directly rather than
    assumed."""
    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: str = Field(default=DEFAULT_TENANT_ID, index=True)
    quote_id: Optional[int] = Field(default=None, foreign_key="quote.id", index=True)
    builder_estimate_id: Optional[int] = Field(default=None, foreign_key="builderestimate.id", index=True)
    storage_path: str
    original_filename: str
    content_type: str
    size_bytes: int
    uploaded_by: str = "staff"   # "staff" | "builder"
    created_at: datetime = Field(default_factory=datetime.utcnow)


class HoursWorked(SQLModel, table=True):
    """Confirmed Aug 2026 — HR Phase A. Traceable hours record with a
    clean monthly summary for the accountant, per the brief."""
    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: str = Field(default=DEFAULT_TENANT_ID, index=True)
    employee_id: int = Field(foreign_key="employee.id")
    work_date: date
    hours: float
    hour_type: str = "normal"   # normal | overtime | sunday | public_holiday
    quote_id: Optional[int] = Field(default=None, foreign_key="quote.id")   # optional link to the job this time was spent on
    notes: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Document(SQLModel, table=True):
    """Confirmed Aug 2026 — HR Phase A. Actual files are stored on disk
    under /uploads (see main.py), this record is the metadata + access
    control (owner_only)."""
    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: str = Field(default=DEFAULT_TENANT_ID, index=True)
    employee_id: int = Field(foreign_key="employee.id")
    document_type: str = "other"   # contract | sick_note | warning | other
    filename: str
    file_path: str
    owner_only: bool = False       # confirmed Aug 2026: some documents (e.g. disciplinary) are Owner-only, not even Admin
    notes: str = ""
    uploaded_at: datetime = Field(default_factory=datetime.utcnow)


class LeaveBalance(SQLModel, table=True):
    """Confirmed Aug 2026 — one row per employee per leave type per
    cycle. Sick leave uses SA's standard 36-month cycle; annual leave
    uses a normal leave year. days_carried_over is capped by
    max_carry_over at cycle rollover (enforced in the rollover endpoint,
    not automatically — carry-over is a deliberate admin action)."""
    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: str = Field(default=DEFAULT_TENANT_ID, index=True)
    employee_id: int = Field(foreign_key="employee.id")
    leave_type: str            # annual | sick | unpaid | other
    cycle_start_date: date
    days_entitled: float
    days_taken: float = 0.0
    days_carried_over: float = 0.0
    max_carry_over: float = 5.0   # confirmed Aug 2026: brief's suggested default (5-8 days), configurable per employee


class LeaveRequest(SQLModel, table=True):
    """Confirmed Aug 2026 — the actual request/approve workflow. Balance
    on the matching LeaveBalance only updates on approval, not on
    submission — per the brief's explicit requirement."""
    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: str = Field(default=DEFAULT_TENANT_ID, index=True)
    employee_id: int = Field(foreign_key="employee.id")
    leave_type: str
    start_date: date
    end_date: date
    days_requested: float
    status: str = "pending"    # pending | approved | rejected
    reason: str = ""
    requested_at: datetime = Field(default_factory=datetime.utcnow)
    reviewed_by: str = ""
    reviewed_at: Optional[datetime] = None
    sick_note_document_id: Optional[int] = None   # links to a Document if a sick note was attached — not a hard FK constraint, since a request can exist before a document is uploaded


class Client(SQLModel, table=True):
    """Real client records, confirmed Aug 2026 — replaces the plain-text
    client_name field on Quote as the source of truth. Quotes keep their
    own client_name too (denormalized snapshot), so walk-in/one-off quotes
    still work without requiring a CRM entry first, and historical quotes
    aren't affected if a client's name is later corrected."""
    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: str = Field(default=DEFAULT_TENANT_ID, index=True)
    name: str
    phone: str = ""
    email: str = ""
    address: str = ""
    preferred_branch: str = "gansbaai"
    notes: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)
    # Marketing source (confirmed Aug 2026, New Quote Screen: Clarify
    # Buttons + Price Check + Marketing Source brief §4) — "how did you
    # hear about us." Plain free-form string, not an enum on this side —
    # the frontend's own dropdown offers a simple starting list
    # (Referral, Walk-in, Google/Online search, Social media, Signage,
    # Builder referral, Repeat client, Other), deliberately kept simple
    # per the brief's own words ("can be expanded later... not part of
    # this brief's scope") — a backend enum would make expanding that
    # list a migration instead of a one-line frontend edit.
    marketing_source: str = ""
    # Client Info: Company Name, VAT Number, Multiple Phones/Emails
    # (confirmed Aug 2026). company_name/vat_number are for
    # business/company clients -- blank for individuals, same
    # optional-string pattern as marketing_source above. phone/email
    # above stay exactly as they are (the PRIMARY entry, unchanged --
    # every existing client record already has its real data there, no
    # migration/backfill needed); phone_extra/email_extra hold any
    # ADDITIONAL entries as a JSON array of strings (e.g.
    # '["082 111 2222", "083 333 4444"]'), blank ("") when there are
    # none. A JSON-in-a-text-column list rather than a new table --
    # this is a small, per-client, order-doesn't-matter set of strings,
    # not a relation with its own fields/lifecycle, so a new table
    # would be more machinery than the data shape actually needs.
    company_name: str = ""
    vat_number: str = ""
    phone_extra: str = ""
    email_extra: str = ""


class BusinessSettings(SQLModel, table=True):
    """Singleton — always id=1, one row, created with sensible defaults
    on first call and edited via PUT from then on, rather than a full
    CRUD resource (there's only ever one). Originally just letterhead
    details for the printed quote header; expanded Aug 2026 (v54) into
    the single source of truth for business-wide values that were
    previously hardcoded or duplicated — found: VAT_PCT hardcoded
    identically in two separate places in main.py; the R350 screed bag
    overage rate hardcoded as literal text in two frontend locations;
    the default deposit % never actually wired from the frontend at
    all, so every quote silently got the model's own hardcoded 70%.
    business_name/address/phone/email/vat_number/bank_details keep
    their original names and shapes rather than the v54 zip lineage's
    rename+restructure (company_name/contact_phone/contact_email, four
    separate structured bank fields) — real production data already
    lived in these columns (address, phone, email, bank_details) by the
    time that alternate design was seen; bank_details in particular is
    free text with a "Send Proof of Payment to" line that wouldn't fit
    four fixed fields without either losing it or guessing at a split.
    yoco_payment_link is new and safe to add outright — nothing existed
    there before."""
    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: str = Field(default=DEFAULT_TENANT_ID, unique=True, index=True)   # one settings row per tenant (was a hardcoded id=1 singleton — see main.py's get_settings())
    business_name: str = ""
    address: str = ""
    phone: str = ""
    email: str = ""
    vat_number: str = ""
    bank_details: str = ""   # free text — bank/branch/account, shown on the printed quote for deposit payment
    yoco_payment_link: str = ""
    # Operational values — confirmed Aug 2026, closing the hardcoded/
    # duplicated-value bugs found above:
    vat_pct: float = 0.15
    default_deposit_pct: float = 0.70
    bag_overage_rate: float = 350.0            # R/bag incl. VAT, screed site-variance charge — see calculations.py's BAG_OVERAGE_RATE comment
    default_labour_rate_per_m2: float = 45.0
    order_overdue_days: int = 7                # Order Index "Overdue" status threshold
    # Multi-tenant groundwork Part 2 (confirmed Aug 2026): the remaining
    # genuinely-hardcoded numeric business rules found auditing
    # calculations.py/models.py — moved here so a future tenant gets
    # their own editable defaults with zero code change. Existing values
    # match the prior hardcoded constants exactly — no behaviour change
    # for Blinds & Flooring Studio. VAT/deposit %/commission tiers were
    # already settings- or table-backed before this pass and needed no
    # further work; Azura's 30% trade discount is real per-product data,
    # not a code constant, so it's untouched too.
    flooring_margin_warn_threshold: float = 0.30   # was FLOORING_MARGIN_WARN_THRESHOLD in calculations.py
    stairwell_labour_closed: float = 250.0         # was STAIRWELL_LABOUR_PER_STAIR[closed] in models.py
    stairwell_labour_one_side_open: float = 300.0  # was STAIRWELL_LABOUR_PER_STAIR[one_side_open]
    stairwell_labour_both_sides_open: float = 350.0  # was STAIRWELL_LABOUR_PER_STAIR[both_sides_open]
    stairwell_default_glue_cost_per_unit: float = 1193.50   # was calculate_stairwell_line's glue_cost_per_unit default (Techem Tek 70/70)
    stairwell_default_glue_coverage_m2: float = 70.0        # was calculate_stairwell_line's glue_coverage_m2 default
    default_bag_cost: float = 235.0                # was DEFAULT_BAG_COST in calculations.py (iTe LEVELiTe F10, 20kg)
    default_bag_coverage_smooth_m2: float = 4.0     # was DEFAULT_BAG_COVERAGE_M2[smooth]
    default_bag_coverage_over_tiles_m2: float = 3.0     # was DEFAULT_BAG_COVERAGE_M2[over_tiles]
    default_bag_coverage_removed_tiles_m2: float = 2.0  # was DEFAULT_BAG_COVERAGE_M2[removed_tiles]
    tile_removal_fee_per_m2_incl_vat: float = 45.0  # was TILE_REMOVAL_FEE_PER_M2_INCL_VAT in calculations.py
    # Part 3 finding (confirmed Aug 2026): the printed quote's logo was a
    # base64 image hardcoded in frontend/index.html, not pulled from
    # here like every other letterhead detail. Empty by default so the
    # frontend falls back to the existing hardcoded image — no visible
    # change for Blinds & Flooring Studio unless/until this is filled in.
    logo_base64: str = ""
    # Supplier & Price Book Management Console (confirmed Aug 2026): the
    # one place "which zone am I" is decided — Azura product pricing is
    # tiered by zone (see FlooringProduct.price_zone_a/b/c), and every
    # quote calculation for an Azura product automatically uses whichever
    # zone matches this setting. Never a per-quote manual choice.
    pricing_zone: str = "A"   # "A" | "B" | "C" — confirmed Aug 2026, Blinds & Flooring Studio is Zone A


class Quote(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: str = Field(default=DEFAULT_TENANT_ID, index=True)
    client_name: str
    client_id: Optional[int] = Field(default=None, foreign_key="client.id")  # confirmed Aug 2026: links to a real Client record when one exists; nullable so walk-in/one-off quotes without a CRM entry still work
    # Price Check (confirmed Aug 2026, New Quote Screen: Clarify Buttons
    # + Price Check + Marketing Source brief §3) — the ONE deliberate,
    # sanctioned exception to the Client-Link Audit brief's own "every
    # quote must have a real client_id" rule: a Price Check is
    # explicitly allowed to exist with no client link at all, since it
    # isn't a real tracked job until someone chooses to convert it (see
    # POST /quotes/{id}/convert-to-quote, main.py). While True, this
    # quote must never appear on the Order Index, Needs Attention, or
    # any dashboard KPI (list_quotes()/analytics_overview() both filter
    # it out) — reuses the exact same Quote row/calculator/line-item
    # flow as a real quote otherwise (brief's own "reuse, don't
    # rebuild"), just this one flag apart.
    is_price_check: bool = False
    sales_owner: str          # "ryno" | "madri" | "burgert" — drives commission, decoupled
                               # from who performs admin actions on the quote afterward
    branch: str = "gansbaai"  # "gansbaai" | "hermanus"
    blinds_measurements_visible: bool = True   # client-facing toggle, internal data always kept
    status: str = "draft"     # draft -> sent -> accepted -> invoiced -> paid, OR draft/sent -> declined. "declined" added Aug 2026 specifically so conversion rate is computable (previously only "reached accepted" vs "still open" existed, which conflates "not yet decided" with "actually lost")
    discount_pct: float = 0.0   # confirmed Aug 2026: applied to the whole quote's ex-VAT subtotal, before VAT — not per line
    # Transport Levy (confirmed Aug 2026, Courier Toggle brief Section 6
    # — explicitly a DIFFERENT feature from the per-product delivery
    # fee, do not merge the two): a manual, one-off, job-level amount
    # for jobs that need it (out-of-town site, awkward delivery, etc) —
    # opt-in, blank/zero on every quote by default. Shows as its own
    # line ("Transport levy: R...") and is added into subtotal_ex_vat
    # alongside the real line items (get_quote(), main.py) — same
    # discount/VAT treatment as every other line on the quote. Fully
    # independent of FlooringProduct.delivery_fee_per_m2 — both can
    # apply to the same quote at once (e.g. an out-of-town Aspen job).
    transport_levy: float = 0.0
    deposit_pct: float = 0.70   # confirmed Aug 2026: 70% deposit, balance on completion
    created_at: datetime = Field(default_factory=datetime.utcnow)
    xero_quote_id: Optional[str] = None   # populated once pushed to Xero (Phase 2)
    # Order tracking fields, confirmed Aug 2026 — "know everything at a
    # glance" from Order Index without opening each quote individually.
    site_address: str = ""             # install/delivery site — may differ from the client's registered address
    installation_date: Optional[date] = None
    invoice_sent_date: Optional[date] = None
    deposit_paid_date: Optional[date] = None
    deposit_payment_method: str = ""    # EFT / Cash / Card / Yoco / etc — free text, not an enum, since new methods shouldn't need a code change
    final_payment_date: Optional[date] = None
    final_payment_method: str = ""
    # Quote Description field (confirmed Aug 2026, Duplicate Quote +
    # Quote Description brief) — free text, e.g. "Full house — vinyl +
    # screed", "Kitchen only — Aspen options". Purpose is purely to tell
    # quotes apart at a glance in the Order Index, especially once a
    # client has several or a quote's been duplicated into variants —
    # not shown on the client-facing printed quote, this is an internal
    # label only.
    description: str = ""

    # ---------- Job Workflow (confirmed Aug 2026, Order Index / Job
    # Workflow Redesign brief + Next Action Addendum) ----------
    # The old `status` field above is kept AS-IS, untouched, for
    # historical data — it is NOT deleted or renamed (real, working data
    # in Postgres; this session never drops what already works). It is
    # simply no longer read for workflow purposes anywhere new — every
    # new code path uses workflow_status instead. See main.py's
    # migration/backfill in on_startup() for how existing rows get a
    # workflow_status derived from this legacy field.
    #
    # Exactly 4 values, per the brief's own hard requirement — never add
    # a 5th. Everything else (invoicing, payment, materials, installer)
    # is a separate field below, not a status:
    workflow_status: str = "quoted"   # quoted | accepted | scheduled | completed
    # Quote -> Job distinction, reflected in the data, not just a status
    # string (confirmed directly): job_number is None for every quote
    # that has never been accepted; assigned exactly once, at
    # acceptance, and never reassigned or reused. This — not
    # workflow_status alone — is the real, structural answer to "is this
    # still just a quote, or has it become a job."
    job_number: Optional[str] = Field(default=None, index=True)
    accepted_at: Optional[datetime] = None   # set once, exactly when QUOTED -> ACCEPTED happens (auto, via POST /quotes/{id}/accept) — permanent, even if workflow_status is later hand-corrected
    # Declining is deliberately NOT one of the 4 workflow values (a
    # declined quote never became a job at all, so it doesn't belong
    # inside a job-workflow enum) — its own field instead, same
    # reasoning that kept invoicing/payment out too. Also what makes
    # conversion-rate reporting possible (confirmed: this exact need is
    # why a "declined" status existed on the old field in the first
    # place).
    declined_at: Optional[datetime] = None
    # Promotes ACCEPTED -> SCHEDULED (confirmed Aug 2026) — deliberately
    # a SEPARATE field from installation_date below: a date being
    # present isn't the same as it being confirmed/booked. installation_date
    # can still be set earlier as a tentative/target date without
    # promoting the job to Scheduled.
    installation_confirmed_date: Optional[date] = None
    completion_date: Optional[date] = None   # promotes SCHEDULED -> COMPLETED
    # Operational fields (confirmed Aug 2026) — never statuses, per the
    # brief's own explicit instruction ("do not create statuses like
    # 'Materials Ordered'... keep these as separate fields instead").
    # materials_ordered and ready_for_installation are deliberately two
    # INDEPENDENT manual booleans, not one auto-following the other —
    # ordering materials and them actually being on-site/ready are
    # genuinely different real-world events days apart; collapsing them
    # would produce false "ready" signals. ready_for_installation means,
    # precisely (confirmed directly): the flooring/blinds have been
    # delivered and stock is physically on hand — ready to install from
    # that moment. Always a MANUAL confirmation ("Mark Materials
    # Received" button, order-index.js), never inferred — Bolton doesn't
    # track physical stock-on-hand per job, so there's no signal to
    # automate this from even if it wanted to.
    installer_team: str = ""             # free text, same not-an-enum reasoning as sales_owner
    materials_ordered: bool = False
    ready_for_installation: bool = False

    # ---------- Manual Override, Owner-only (confirmed Aug 2026, Manual
    # Override brief — urgent real use case: a job already quoted/
    # accepted/deposit-paid in Burgert's OLD pre-Bolton system needs to
    # be entered here matching those already-agreed figures exactly,
    # not recalculated by Bolton's formula engine). Deliberately
    # separate from discount_pct above — a discount is a normal,
    # calculated business rule; this is a manual escape hatch for "the
    # real agreed number doesn't match what the calculator produces,"
    # used rarely and only by the Owner. None = never overridden, the
    # normal case for every quote. See _quote_totals() (main.py) for
    # where this takes over the calculated total, and the matching
    # QuoteLineItem fields below for the per-line equivalent.
    manual_override_total_incl_vat: Optional[float] = None
    override_total_reason: Optional[str] = None
    override_total_by: Optional[str] = None
    override_total_at: Optional[datetime] = None

    # ---------- Deposit Amount (confirmed Aug 2026, Deposit Amount +
    # Save Confirmation + Default Branch brief) ----------
    # deposit_amount was purely calculated (total_incl_vat *
    # deposit_pct) — doesn't reflect reality, since different clients
    # pay different actual amounts, not a fixed percentage every time.
    # None = still purely percentage-calculated, the normal case. Once
    # set, takes precedence over the percentage figure everywhere
    # deposit_amount/balance_amount are computed (_quote_totals(),
    # main.py) — same "the manually entered real figure wins, but is
    # never a silent, invisible substitution" precedent as the Manual
    # Override fields above, just without a mandatory reason (this
    # isn't a price CORRECTION needing justification, it's simply
    # recording what was actually paid).
    actual_deposit_amount: Optional[float] = None
    actual_deposit_amount_by: Optional[str] = None
    actual_deposit_amount_at: Optional[datetime] = None

    # ---------- Revert to Original (confirmed Aug 2026, Add-Line
    # Data-Loss brief §5 — "one level of undo back to what was last
    # saved," explicitly not full multi-version history) ----------
    # A JSON snapshot of every line + the quote's own editable fields,
    # captured once each time Quote Builder is opened for this quote
    # (openQuoteFromIndex(), index.html — never on every subsequent
    # add/edit/delete, or "revert" would just restore whatever was
    # already there). None until a quote has been opened for editing at
    # least once since this brief shipped. See snapshot_quote()/
    # revert_quote() in main.py.
    snapshot_json: Optional[str] = None


class PaymentFollowUp(SQLModel, table=True):
    """Confirmed Aug 2026 — a quote can need MULTIPLE follow-ups over
    time (first reminder, second reminder...), so this is its own
    append-only log, not a single field that would overwrite the
    previous follow-up date every time a new one goes out."""
    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: str = Field(default=DEFAULT_TENANT_ID, index=True)
    quote_id: int = Field(foreign_key="quote.id")
    follow_up_date: date
    notes: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)


class QuoteLineItem(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: str = Field(default=DEFAULT_TENANT_ID, index=True)
    quote_id: int = Field(foreign_key="quote.id")
    category: str              # "flooring" | "blinds"
    product_id: int
    product_name: str          # denormalized snapshot at time of quoting
    # Fixed Display Order (confirmed Aug 2026, Add-Line Data-Loss brief
    # §4) — the required hierarchy (Floor/Vinyl -> Screed -> Trims ->
    # Skirtings -> everything else) can't be derived from `category`
    # alone: a flooring line is EITHER a Floor/Vinyl or a Screed line
    # depending on the underlying FlooringProduct.pricing_type, and a
    # trim line is EITHER a Trim or a Skirting depending on the
    # underlying TrimProduct.category — neither distinction previously
    # existed on the line item itself. Denormalized snapshots, same
    # reasoning as product_name/colour above: the price book entry could
    # be edited or deleted later, and a historical quote's own display
    # order must never silently drift because of that. None on every
    # line added before this brief (and any set() where the sort falls
    # back to a sensible default — see the shared sort logic, main.py).
    flooring_pricing_type: Optional[str] = None   # "material" | "screed" (flooring lines only)
    trim_sub_category: Optional[str] = None        # "skirting" | "stair_nose" | "reducer" | "carpet_strip" | "quarter_round" (trim lines only)
    # Supplier Order Sheets brief (confirmed Aug 2026) — same denormalized-
    # snapshot reasoning as the two fields above: boxes_needed is already
    # calculated at add-time (calculate_flooring_line()'s own boxes_needed/
    # packs_needed, calculations.py) but was never persisted anywhere
    # before this — only returned transiently in the add-line response.
    # Needed here so an order sheet generated weeks after the quote was
    # made reflects what was ACTUALLY calculated then, not a recalculation
    # against a price book that may have since changed (m2_per_pack could
    # be edited). Material flooring lines only — screed's own real
    # order-sheet quantity is bags_allowed below, which was already a
    # stored field.
    boxes_needed: Optional[int] = None
    # source_feature (confirmed Aug 2026, Extra Rooms / Floor Prep
    # Collapsible brief) — a misc line's category alone can't tell "an
    # Extra Room/Floor Prep entry" apart from any other freeform misc
    # line (e.g. "extra Saturday labour") to render it as its own
    # collapsible card. None for every ordinary misc line (and every
    # other category — flooring/blinds/trim/stairwell already have
    # their own dedicated category value, they don't need this).
    source_feature: Optional[str] = None   # None | "floor_prep"
    colour: str = ""            # confirmed Aug 2026: denormalized snapshot too — locks the exact colour that was actually quoted, even if the price book entry is later edited or the colour discontinued. This is what gets ordered.
    original_colour: str = ""   # confirmed Aug 2026: set once when the line is first created, never touched again — the true original, even if `colour` above gets changed later (e.g. out of stock, substituted). Full change history lives in ColourChangeLog.
    job_type: Optional[str] = None       # flooring only
    width_mm: Optional[float] = None     # blinds only
    drop_mm: Optional[float] = None      # blinds only
    quantity_m2: Optional[float] = None  # flooring only
    length_m: Optional[float] = None     # trims only
    discount_pct: float = 0.0
    unit_cost: float = 0.0     # material cost per unit — never shown to Sales role
    unit_price: float = 0.0    # what the client sees
    line_total: float = 0.0
    margin_pct: float = 0.0    # overall margin incl. glue+labour for material lines — never shown to Sales role
    # Job cost buildup (material flooring lines only — glue/labour don't apply
    # to screed or blinds, since screed's job-type multiplier already covers
    # its own labour/complexity, and blinds are priced as finished units):
    glue_cost_total: float = 0.0
    glue_sell_total: float = 0.0
    glue_units_needed: int = 0
    labour_cost_total: float = 0.0
    labour_charged_total: float = 0.0
    own_staff: bool = True
    bags_allowed: int = 0
    compound_cost_total: float = 0.0
    tile_removal_fee_total: float = 0.0
    delivery_fee_total: float = 0.0    # confirmed Aug 2026: real cost, no markup on the fee itself in isolation, but bundled into the pre-markup subtotal same as glue — defaults to 0 for every supplier except where explicitly set
    total_job_cost: float = 0.0   # material_cost_total + glue_cost_total + labour_cost_total + compound_cost_total + tile_removal_fee_total
    # Stairwell-specific (category == "stairwell"):
    num_stairs: Optional[int] = None
    stairwell_type: Optional[str] = None
    nosing_length_m: Optional[float] = None
    boxes_needed: Optional[int] = None
    billed_vinyl_area_m2: Optional[float] = None
    glue_area_m2: Optional[float] = None
    vinyl_sell_total: Optional[float] = None
    vinyl_cost_total: Optional[float] = None
    nosing_cost_total: Optional[float] = None
    nosing_sell_total: Optional[float] = None
    # Stairwell landing (confirmed Aug 2026): folded into the SAME
    # stairwell line's totals rather than appearing as its own separate
    # "flooring" quote line — same standard per-m² flat-rate calculation
    # as before (calculate_flooring_line, unchanged), just combined at
    # persistence time instead of posted as a second line item. These
    # two fields exist purely so the description can clearly state the
    # landing is included (and how much), and so Owner/Admin/Sales alike
    # can see the price breakdown — landing_sell_total is a SELL price,
    # not a cost figure, so it's not stripped for Sales.
    landing_area_m2: Optional[float] = None
    landing_sell_total: Optional[float] = None

    # ---------- Manual Override, Owner-only (confirmed Aug 2026, Manual
    # Override brief) ----------
    # line_total itself becomes the overridden value once applied (see
    # override_quote_line() in main.py) — deliberately NOT a separate
    # "override_value" field read instead of line_total at display/total
    # time, since subtotal_ex_vat is already summed from line_total in
    # four places across this file (get_quote, list_quotes,
    # get_client_quotes, and the older single-quote helper) — mutating
    # line_total directly means all four keep working unchanged, zero
    # risk of one of them forgetting to check for an override. The TRUE
    # calculated value is preserved here instead, only on the FIRST
    # override (never overwritten by a second override applied later),
    # so "Revert to calculated value" always restores the real original,
    # not whatever the previous override happened to be.
    pre_override_line_total: Optional[float] = None   # None = never overridden
    override_reason: Optional[str] = None
    override_by: Optional[str] = None
    override_at: Optional[datetime] = None


class ColourChangeLog(SQLModel, table=True):
    """Confirmed Aug 2026 — real business need: a colour quoted might go
    out of stock and need substituting. This is the internal record of
    what changed and why, kept separate from the quote line itself so
    every change is preserved (not just the most recent one) — an
    internal/operational record, not shown on the client-facing printed
    quote, which only ever shows the current colour."""
    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: str = Field(default=DEFAULT_TENANT_ID, index=True)
    quote_line_item_id: int = Field(foreign_key="quotelineitem.id")
    old_colour: str
    new_colour: str
    reason: str = ""
    changed_by: str = ""
    changed_at: datetime = Field(default_factory=datetime.utcnow)


class AuditLog(SQLModel, table=True):
    """General-purpose audit log (confirmed Aug 2026, Supplier & Price
    Book Management Console brief — built for this real need, not
    speculatively, but deliberately generic so it's reusable anywhere
    else "what changed and when" matters later, without a rebuild).
    old_value/new_value are stored as plain strings regardless of the
    field's real type (float, bool, str...) — this is a change-history
    record, not a typed data store; str() on either side is enough to
    answer "what changed" and keeps this table usable for literally any
    entity/field combination without per-type columns.
    Permanent — no update/delete endpoint exists for this table, ever,
    for the same reason the session log can't be edited: a record that
    can be quietly changed isn't a record."""
    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: str = Field(default=DEFAULT_TENANT_ID, index=True)
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    username: str              # who made the change — the real, authenticated username, never client-supplied
    entity_type: str           # e.g. "FlooringProduct", "BlindsProduct", "TrimProduct" — Python class name, so it's unambiguous and greppable against models.py
    entity_id: int
    field: str
    old_value: str
    new_value: str


class OrderSheet(SQLModel, table=True):
    """Supplier Order Sheet (confirmed Aug 2026, Supplier Order Sheets
    brief) — an internal/supplier-facing procurement document, separate
    from the client-facing quote/invoice: tells a supplier what to
    actually send for a job, at Burgert's real cost, never the client's
    sell price.

    One job (Quote) can produce TWO of these — a job where the flooring
    supplier isn't Azura splits into one sheet to that supplier
    (flooring only) and one to Azura (screed/floor-prep only), per the
    brief's own splitting rule (see generate_order_sheets(), main.py,
    for the full logic). Both stay traceable back to the same
    job_number via quote_id; each still gets its own distinct
    order_number.

    Trims are explicitly OUT of scope (brief §1) — Burgert orders those
    separately, in bulk, direct from Supertrim — never appear on any
    OrderSheet generated here."""
    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: str = Field(default=DEFAULT_TENANT_ID, index=True)
    quote_id: int = Field(foreign_key="quote.id")
    order_number: str = Field(index=True)   # "O-0001", sequential, never reused — see _next_order_number(), main.py
    supplier: str
    sheet_type: str   # "flooring" | "floor_prep" — floor_prep sheets are the editable ones (brief §5); a flooring-only sheet reflects the quote's own line items directly and isn't meant to be freely edited
    created_at: datetime = Field(default_factory=datetime.utcnow)
    created_by: str
    # Order Sheets UX: Duplicate Bug + Delete Option + Prominent
    # Placement + Real Preview brief (confirmed Aug 2026). status is
    # what generate_order_sheets() now checks BEFORE creating a new
    # sheet, per the brief's own root-cause fix (§1): a "draft" sheet
    # already existing for the same job+supplier+category means
    # pressing Generate again re-opens that one instead of silently
    # creating a duplicate (which is exactly how O-0001/O-0002 on
    # J-0001 happened). Once "placed" (the new finalize/execute action,
    # §4), a genuinely fresh re-order for that same job+supplier is no
    # longer treated as a duplicate -- the materials were actually
    # ordered, so a new sheet next time is real, not accidental.
    status: str = "draft"   # "draft" | "placed"
    placed_at: Optional[datetime] = None
    placed_by: Optional[str] = None


class OrderSheetLine(SQLModel, table=True):
    """One line on an OrderSheet — product, spec/colour, quantity, and
    Burgert's real cost price (confirmed Aug 2026, brief §3). quantity
    is editable on a floor_prep-type sheet (brief §5 — "quantities...
    must be amendable"); is_extra distinguishes a line Burgert typed in
    himself (an extra tool, an additional consumable not part of the
    original calculated list) from one generated from the quote's own
    line items."""
    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: str = Field(default=DEFAULT_TENANT_ID, index=True)
    order_sheet_id: int = Field(foreign_key="ordersheet.id")
    product_name: str
    colour: str = ""
    quantity: float
    unit: str = ""            # "boxes" | "bags" | "drums" | "m²" | "" — whatever's meaningful for that line
    unit_cost: float = 0.0    # Burgert's real cost per unit, ex VAT, AFTER any discount — NEVER the client's sell price
    is_extra: bool = False
    # Order Sheet Corrections brief (confirmed Aug 2026, §3+§4) —
    # "show three values instead of a single cost figure": pre-discount
    # book/Zone A price, the discount rate applied, and the resulting
    # unit_cost above. Both None for a manually-added extra line (no
    # book price/discount concept applies to something Burgert typed in
    # himself) or for a hand-computed fallback with no product record
    # to price against. discount_pct explicitly 0.0 (not None) for a
    # floor-prep/consumable line — Azura's real no-discount rule (§4) —
    # so the frontend can show "No discount" in visible contrast to a
    # flooring line's real rate, rather than the two looking the same
    # (None and 0.0 would both render as blank otherwise).
    pre_discount_unit_cost: Optional[float] = None
    discount_pct: Optional[float] = None


class DocumentArchive(SQLModel, table=True):
    """Dropbox Document Archive & Backup Layer brief (confirmed Aug
    2026) — one row per ARCHIVED VERSION of a document (quote/invoice/
    order sheet), never overwritten (brief §4 — "commercial history
    must be preserved"): a v2 gets its own new row with version=2;
    the v1 row is untouched forever.

    pdf_bytes is the actual, already-rendered PDF, stored here (not
    regenerated later) — brief §10's own hard requirement: "an
    archived PDF must represent the document exactly as it existed at
    the time it was generated... a later supplier price-list update
    must never alter an already-generated quote/invoice/order PDF."
    Regenerating on a later retry would silently violate that the
    moment pricing changed in between — retry_archive_upload() (main.py)
    re-uploads THIS stored copy, never a freshly-rendered one. Render's
    own filesystem is ephemeral across restarts/redeploys, so a DB
    column is the only place this can live and still be retriable
    reliably days later.

    status: "pending" (not yet attempted, or DROPBOX_ACCESS_TOKEN not
    configured — treated as the exact same retriable case as Dropbox
    being genuinely unreachable, brief §7) | "uploaded" (dropbox_path/
    dropbox_file_id are then real, confirmed values, never guessed) |
    "failed" (failure_reason set, retriable via the same action)."""
    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: str = Field(default=DEFAULT_TENANT_ID, index=True)
    entity_type: str = Field(index=True)   # "Quote" | "Invoice" | "OrderSheet" | "OrderIndexSnapshot"
    entity_id: int = Field(index=True)     # the quote/order id this version belongs to (0 for a dated Order Index snapshot, not tied to one entity)
    version: int                           # 1, 2, 3... per entity_type+entity_id, never reused
    reference: str                         # human label for the Dropbox filename, e.g. "J-0001" or "O-0002"
    status: str = "pending"
    dropbox_path: Optional[str] = None
    dropbox_file_id: Optional[str] = None
    failure_reason: Optional[str] = None
    pdf_bytes: bytes
    created_at: datetime = Field(default_factory=datetime.utcnow)
    uploaded_at: Optional[datetime] = None
    created_by: str
    # Dropbox brief §3 — "once the customer accepts the quote,
    # preserve the accepted version distinctly." At most one row per
    # entity_type+entity_id ever carries this flag (archive_document()
    # unsets it on any earlier row for the same document before
    # setting it on the new one) — always findable as THE version that
    # was actually agreed to, regardless of how many later versions
    # (a post-acceptance Manual Override, say) get archived on top.
    is_accepted_version: bool = False
