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
    username: str = Field(unique=True, index=True)   # lowercase, e.g. "burgert"
    display_name: str
    password_hash: str          # pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex> — see auth.py
    role: str                   # UserRole value
    active: bool = True
    created_at: datetime = Field(default_factory=datetime.utcnow)


class UserSession(SQLModel, table=True):
    """Server-side session record backing the login cookie. Stored in the
    DB (not in-memory) so sessions survive a Render backend restart/redeploy
    — an in-memory dict would silently log everyone out on every deploy."""
    id: Optional[int] = Field(default=None, primary_key=True)
    token: str = Field(unique=True, index=True)
    user_id: int = Field(foreign_key="app_user.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    expires_at: datetime   # confirmed Aug 2026: fixed 24h session length from login — not permanent, not aggressive re-login mid-shift


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
    product_name: str          # the range/product name, e.g. "Aspen Premium Range 2.5mm" — NOT the colour, see colour field below
    colour: str = ""            # confirmed Aug 2026: real structured field, not baked into product_name — so the exact colour ordered from the supplier stays locked and clearly visible on the actual quote. Same range at different colours = separate price book entries.
    supplier: str
    pricing_type: str = "material"   # "screed" | "material"
    flooring_category: str = "vinyl"  # "vinyl" | "laminate" | "spc" | "novilon" | "carpet" | "engineered_wood" | "screed" — for dashboard grouping
    display_order: int = 100         # confirmed Aug 2026: lower number = shown first in dropdowns/price book — manual priority, not auto usage tracking (not enough real quote history to learn from yet). Set your best-sellers low, e.g. series 200 at 10.
    base_cost_ex_vat: float          # supplier cost per m² (screed: base rate; material: RRP/cost basis — see sell_markup_multiplier for actual sell price)
    sell_markup_multiplier: float = 1.3  # MATERIAL only (confirmed Aug 2026): sell = base_cost_ex_vat x this. Default changed to your confirmed real markup (×1.3, 30%) so it's automatic without setup — still fully adjustable per product or per quote.
    wastage_pct: float = 0.08        # 8% default wastage
    trade_discount_pct: float = 0.0  # e.g. 0.30 for Azura vinyl
    settlement_discount_pct: float = 0.0  # a further discount some suppliers offer on top of trade discount — kept entirely as margin, never passed through to a lower client price (matches how BlindsProduct already handles it)
    tile_width_mm: Optional[float] = None   # reference data from the supplier price list — not used in any calculation
    tile_length_mm: Optional[float] = None
    tile_thickness_mm: Optional[float] = None
    delivery_fee_per_m2: float = 0.0  # confirmed Aug 2026: some suppliers (e.g. Aspen) charge delivery on top, no trade discount to offset it. Pass-through, same treatment as glue — real cost AND charged in full, never marked up. Defaults to 0 so it never affects any other supplier.
    over_tiles_multiplier: float = 1.5    # SCREED only, editable per product (confirmed Aug 2026: your real 8-year rates are NOT a clean 1.5x/2x — e.g. deZIGN S200 screed is 130/160/250, ratios ~1.23x/1.92x, not 1.5x/2x. Default 1.5 is a generic placeholder — set your real number per product.
    removed_tiles_multiplier: float = 2.0  # SCREED only, editable per product — same as above
    m2_per_pack: Optional[float] = None  # for purchase-order pack-quantity calc (Phase 3, §13)
    tiles_per_pack: Optional[float] = None  # for stairwell calc (3 tiles/stair, confirmed Aug 2026 — 3 planks x standard plank width = tread width per stair) — derived from plank dimensions
    unit: str = "m2"
    last_updated: datetime = Field(default_factory=datetime.utcnow)
    source: str = "manual"           # "manual" | "pdf_import" | "legacy_import"


class BlindsProduct(SQLModel, table=True):
    """Blinds price book entry.
    Margin formula (confirmed): net cost = book price less 45% trade discount,
    less further 7.5% settlement discount. Selling price = book price + VAT (15%).
    This yields ~49% margin at full price."""
    id: Optional[int] = Field(default=None, primary_key=True)
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
    employee_id: int = Field(foreign_key="employee.id")
    period_year: int
    period_month: int
    calculated_amount: float
    paid_amount: float
    paid_date: Optional[date] = None
    notes: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)


class HoursWorked(SQLModel, table=True):
    """Confirmed Aug 2026 — HR Phase A. Traceable hours record with a
    clean monthly summary for the accountant, per the brief."""
    id: Optional[int] = Field(default=None, primary_key=True)
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
    name: str
    phone: str = ""
    email: str = ""
    address: str = ""
    preferred_branch: str = "gansbaai"
    notes: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)


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


class Quote(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    client_name: str
    client_id: Optional[int] = Field(default=None, foreign_key="client.id")  # confirmed Aug 2026: links to a real Client record when one exists; nullable so walk-in/one-off quotes without a CRM entry still work
    sales_owner: str          # "ryno" | "madri" | "burgert" — drives commission, decoupled
                               # from who performs admin actions on the quote afterward
    branch: str = "gansbaai"  # "gansbaai" | "hermanus"
    blinds_measurements_visible: bool = True   # client-facing toggle, internal data always kept
    status: str = "draft"     # draft -> sent -> accepted -> invoiced -> paid, OR draft/sent -> declined. "declined" added Aug 2026 specifically so conversion rate is computable (previously only "reached accepted" vs "still open" existed, which conflates "not yet decided" with "actually lost")
    discount_pct: float = 0.0   # confirmed Aug 2026: applied to the whole quote's ex-VAT subtotal, before VAT — not per line
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


class PaymentFollowUp(SQLModel, table=True):
    """Confirmed Aug 2026 — a quote can need MULTIPLE follow-ups over
    time (first reminder, second reminder...), so this is its own
    append-only log, not a single field that would overwrite the
    previous follow-up date every time a new one goes out."""
    id: Optional[int] = Field(default=None, primary_key=True)
    quote_id: int = Field(foreign_key="quote.id")
    follow_up_date: date
    notes: str = ""
    created_at: datetime = Field(default_factory=datetime.utcnow)


class QuoteLineItem(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    quote_id: int = Field(foreign_key="quote.id")
    category: str              # "flooring" | "blinds"
    product_id: int
    product_name: str          # denormalized snapshot at time of quoting
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


class ColourChangeLog(SQLModel, table=True):
    """Confirmed Aug 2026 — real business need: a colour quoted might go
    out of stock and need substituting. This is the internal record of
    what changed and why, kept separate from the quote line itself so
    every change is preserved (not just the most recent one) — an
    internal/operational record, not shown on the client-facing printed
    quote, which only ever shows the current colour."""
    id: Optional[int] = Field(default=None, primary_key=True)
    quote_line_item_id: int = Field(foreign_key="quotelineitem.id")
    old_colour: str
    new_colour: str
    reason: str = ""
    changed_by: str = ""
    changed_at: datetime = Field(default_factory=datetime.utcnow)
