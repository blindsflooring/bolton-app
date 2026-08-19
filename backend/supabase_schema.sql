-- Phase 2: run this in Supabase's SQL editor when migrating off local SQLite.
-- Mirrors models.py exactly. Add RLS policies per role before going live
-- (row-level security matching the Owner/Admin/Sales split in brief §7).

create table if not exists flooring_product (
    id serial primary key,
    product_name text not null,   -- the range/product name, NOT the colour
    colour text default '',       -- confirmed Aug 2026: real structured field, not baked into product_name
    supplier text not null,
    pricing_type text not null default 'material',  -- 'screed' | 'material'
    flooring_category text not null default 'vinyl', -- 'vinyl'|'laminate'|'spc'|'novilon'|'carpet'|'engineered_wood'|'screed' — for dashboard grouping
    base_cost_ex_vat numeric not null,
    sell_markup_multiplier numeric not null default 1.0, -- material only, confirmed Aug 2026 — applied to (boxes+glue) subtotal, e.g. 1.30 for a 30% markup (box-by-box formula, not flat base x markup)
    wastage_pct numeric not null default 0.08,
    trade_discount_pct numeric not null default 0,
    settlement_discount_pct numeric not null default 0,  -- a further discount some suppliers offer on top of trade discount — kept entirely as margin, never passed through to a lower client price (matches blinds_product)
    tile_width_mm numeric,      -- reference data from the supplier price list — not used in any calculation
    tile_length_mm numeric,
    tile_thickness_mm numeric,
    over_tiles_multiplier numeric not null default 1.5,   -- screed only, editable — confirmed Aug 2026 real rates aren't a clean 1.5x
    removed_tiles_multiplier numeric not null default 2.0, -- screed only, editable
    m2_per_pack numeric,                -- for purchase-order pack-quantity calc
    tiles_per_pack numeric,             -- for stairwell calc (confirmed exact whole numbers per product)
    display_order int not null default 100,  -- confirmed Aug 2026: lower number = shown first in dropdowns/price book
    delivery_fee_per_m2 numeric not null default 0,  -- confirmed Aug 2026: some suppliers (e.g. Aspen) charge delivery on top, no trade discount to offset it — real pass-through cost
    unit text not null default 'm2',
    last_updated timestamptz not null default now(),
    source text not null default 'manual'
);

create table if not exists blinds_product (
    id serial primary key,
    product_name text not null,
    supplier text not null,
    mechanism text not null,
    fabric_tier text default '',
    width_band text default '',
    drop_band text default '',
    book_price numeric not null,
    trade_discount_pct numeric not null default 0.45,
    settlement_discount_pct numeric not null default 0.075,
    vat_pct numeric not null default 0.15,
    last_updated timestamptz not null default now(),
    source text not null default 'manual'
);

create table if not exists trim_product (
    id serial primary key,
    product_name text not null,
    profile_code text default '',
    category text not null default 'skirting',
    supplier text not null,
    cost_ex_vat_per_lm numeric not null,
    vat_pct numeric not null default 0.15,   -- confirmed Aug 2026: markup mode = cost x (1+vat) x markup
    wastage_pct numeric not null default 0.08,   -- confirmed Aug 2026, buffer for offcuts/mitres
    pricing_mode text not null default 'fixed',   -- 'fixed' | 'markup'
    fixed_sell_price_per_lm numeric,
    markup_multiplier numeric not null default 1.5,
    unit text not null default 'lm',
    last_updated timestamptz not null default now(),
    source text not null default 'manual'
);

create table if not exists employee (
    id serial primary key,
    full_name text not null,
    role_title text default '',
    start_date date,
    birthday date,
    id_number text default '',
    phone text default '',
    email text default '',
    employment_status text not null default 'active',
    thirteenth_cheque_eligible boolean not null default false,
    notes text default '',              -- Owner + Admin only, stripped for Sales/self-service views
    commission_eligible boolean not null default false,
    commission_role_type text not null default 'pure_sales',  -- 'pure_sales' | 'builder_rep' | 'other'
    sales_owner_key text default '',    -- links to quote.sales_owner for commission calc
    created_at timestamptz not null default now()
);

create table if not exists commission_rate (
    id serial primary key,
    role_type text not null,            -- 'pure_sales' | 'builder_rep'
    basis text not null,                -- 'gp' | 'ex_vat_price'
    category text,                      -- builder_rep only: flooring/blinds/trim/skirting/stairwell
    tier_min numeric not null default 0,
    tier_max numeric,                   -- null = "and above"
    rate_pct numeric not null,
    active boolean not null default true
);

create table if not exists commission_payment (
    id serial primary key,
    employee_id int not null references employee(id),
    period_year int not null,
    period_month int not null,
    calculated_amount numeric not null,
    paid_amount numeric not null,
    paid_date date,
    notes text default '',
    created_at timestamptz not null default now()
);

create table if not exists hours_worked (
    id serial primary key,
    employee_id int not null references employee(id),
    work_date date not null,
    hours numeric not null,
    hour_type text not null default 'normal',   -- normal | overtime | sunday | public_holiday
    quote_id int references quote(id),
    notes text default '',
    created_at timestamptz not null default now()
);

create table if not exists document (
    id serial primary key,
    employee_id int not null references employee(id),
    document_type text not null default 'other',  -- contract | sick_note | warning | other
    filename text not null,
    file_path text not null,
    owner_only boolean not null default false,
    notes text default '',
    uploaded_at timestamptz not null default now()
);

create table if not exists leave_balance (
    id serial primary key,
    employee_id int not null references employee(id),
    leave_type text not null,           -- annual | sick | unpaid | other
    cycle_start_date date not null,
    days_entitled numeric not null,
    days_taken numeric not null default 0,
    days_carried_over numeric not null default 0,
    max_carry_over numeric not null default 5
);

create table if not exists leave_request (
    id serial primary key,
    employee_id int not null references employee(id),
    leave_type text not null,
    start_date date not null,
    end_date date not null,
    days_requested numeric not null,
    status text not null default 'pending',   -- pending | approved | rejected
    reason text default '',
    requested_at timestamptz not null default now(),
    reviewed_by text default '',
    reviewed_at timestamptz,
    sick_note_document_id int
);

create table if not exists colour_change_log (
    id serial primary key,
    quote_line_item_id int not null references quote_line_item(id),
    old_colour text not null,
    new_colour text not null,
    reason text default '',
    changed_by text default '',
    changed_at timestamptz not null default now()
);

create table if not exists client (
    id serial primary key,
    name text not null,
    phone text default '',
    email text default '',
    address text default '',
    preferred_branch text not null default 'gansbaai',
    notes text default '',
    created_at timestamptz not null default now()
);

-- Singleton — always id=1, one row. Originally just letterhead details
-- for the printed quote; expanded Aug 2026 (v54) into the single source
-- of truth for business-wide values (VAT %, deposit %, bag overage
-- rate, default labour rate, Order Index overdue threshold) that were
-- previously hardcoded/duplicated in application code.
create table if not exists business_settings (
    id int primary key default 1,
    business_name text default '',
    address text default '',
    phone text default '',
    email text default '',
    vat_number text default '',
    bank_details text default '',
    yoco_payment_link text default '',
    vat_pct numeric not null default 0.15,
    default_deposit_pct numeric not null default 0.70,
    bag_overage_rate numeric not null default 350.0,
    default_labour_rate_per_m2 numeric not null default 45.0,
    order_overdue_days int not null default 7
);

create table if not exists quote (
    id serial primary key,
    client_name text not null,
    client_id int references client(id),  -- confirmed Aug 2026: nullable, links to a real Client record when one exists; walk-in/one-off quotes still work without a CRM entry
    sales_owner text not null,          -- 'ryno' | 'madri' | 'burgert' — drives commission
    branch text not null default 'gansbaai',
    blinds_measurements_visible boolean not null default true,
    status text not null default 'draft',
    discount_pct numeric not null default 0,   -- confirmed Aug 2026: applied to whole quote ex-VAT subtotal, before VAT
    deposit_pct numeric not null default 0.70, -- confirmed Aug 2026: 70% deposit
    created_at timestamptz not null default now(),
    xero_quote_id text,                 -- populated once pushed to Xero (Phase 2)
    site_address text default '',
    installation_date date,
    invoice_sent_date date,
    deposit_paid_date date,
    deposit_payment_method text default '',
    final_payment_date date,
    final_payment_method text default ''
);

create table if not exists payment_follow_up (
    id serial primary key,
    quote_id int not null references quote(id),
    follow_up_date date not null,
    notes text default '',
    created_at timestamptz not null default now()
);

create table if not exists quote_line_item (
    id serial primary key,
    quote_id int not null references quote(id) on delete cascade,
    category text not null,             -- 'flooring' | 'blinds'
    product_id int not null,
    product_name text not null,
    colour text default '',             -- confirmed Aug 2026: denormalized snapshot, locks the exact colour quoted even if the price book entry changes later
    original_colour text default '',    -- confirmed Aug 2026: set once at creation, never touched again — the true original even if colour is later changed
    job_type text,
    width_mm numeric,
    drop_mm numeric,
    quantity_m2 numeric,
    length_m numeric,                   -- trims only
    discount_pct numeric not null default 0,
    unit_cost numeric not null default 0,   -- RLS: hide from sales role
    unit_price numeric not null default 0,
    line_total numeric not null default 0,
    margin_pct numeric not null default 0,  -- RLS: hide from sales role
    glue_cost_total numeric not null default 0,       -- RLS: hide from sales role
    glue_sell_total numeric not null default 0,        -- visible to all roles (charged amount)
    glue_units_needed int not null default 0,           -- visible to all roles (reference only, not used in cost calc)
    labour_cost_total numeric not null default 0,     -- RLS: hide from sales role
    labour_charged_total numeric not null default 0,   -- visible to all roles (always what's charged to client)
    own_staff boolean not null default true,            -- visible to all roles
    bags_allowed int not null default 0,               -- visible to all roles (operational, not cost)
    compound_cost_total numeric not null default 0,    -- RLS: hide from sales role
    tile_removal_fee_total numeric not null default 0, -- visible to all roles (client-facing fee)
    delivery_fee_total numeric not null default 0,     -- RLS: hide from sales role (real cost, bundled pre-markup same as glue)
    total_job_cost numeric not null default 0,          -- RLS: hide from sales role
    -- Stairwell-specific:
    num_stairs int,
    stairwell_type text,          -- 'closed' | 'one_side_open' | 'both_sides_open'
    nosing_length_m numeric,
    boxes_needed int,
    billed_vinyl_area_m2 numeric,
    glue_area_m2 numeric,
    vinyl_sell_total numeric,
    vinyl_cost_total numeric,     -- RLS: hide from sales role
    nosing_cost_total numeric,    -- RLS: hide from sales role
    nosing_sell_total numeric
);

-- Example RLS sketch (finish once Supabase auth/roles are wired up in Phase 2):
-- alter table quote_line_item enable row level security;
-- create policy "sales cannot see cost/margin" on quote_line_item
--   for select using (
--     current_setting('request.jwt.claims', true)::json->>'role' != 'sales'
--     or true -- sales row visible, but cost/margin columns must be filtered
--             -- at the API layer since Postgres RLS is row-level, not column-level;
--             -- keep the strip_sensitive_fields() logic in main.py as the real gate.
--   );
