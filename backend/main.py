"""
Blinds & Flooring Studio bolt-on — Phase 1 backend.
Price Book + Quote Builder. No Xero, no PDF import, no AI assistant yet —
those are Phase 2+ per the build brief.

Run: uvicorn main:app --reload --port 8000
"""
from datetime import datetime, date
from typing import List, Optional, Any
import os
import shutil
import uuid

from fastapi import FastAPI, HTTPException, Query, UploadFile, File, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel
from sqlmodel import SQLModel, Session, create_engine, select

from models import (
    FlooringProduct, BlindsProduct, TrimProduct, Quote, QuoteLineItem, Client,
    BusinessSettings, Employee, CommissionRate, CommissionPayment,
    HoursWorked, Document, LeaveBalance, LeaveRequest, ColourChangeLog, PaymentFollowUp,
    JobType, UserRole, StairwellType, User, UserSession, DEFAULT_TENANT_ID, AuditLog,
    SupplierDefault,
)
from calculations import calculate_flooring_line, calculate_blinds_line, calculate_trim_line, calculate_stairwell_line, line_real_cost
from auth import hash_password, verify_password, new_session_token, new_expiry
from ai_import import extract_price_sheet
from spreadsheet_import import parse_master_spreadsheet

# Confirmed Aug 2026, deployment kickoff: reads DATABASE_URL from the
# environment (set in Render's dashboard, never committed to the repo)
# so the real Supabase Postgres credential never lives in source
# control. Falls back to the local SQLite file when DATABASE_URL isn't
# set, so `uvicorn main:app --reload` still works unchanged for local
# dev without anyone needing to export anything.
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./bolton.db")
# connect_args is SQLite-only (disables its single-thread check, needed
# because FastAPI can hand requests to different threads) — applying it
# to a Postgres URL would raise, so it's conditional on which DB is
# actually in use, not applied unconditionally.
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, echo=False, connect_args=connect_args)

app = FastAPI(title="Blinds & Flooring Studio — Bolt-on API")


def coerce_date_fields(obj, *field_names):
    """BUG WORKAROUND (found Aug 2026, first time this codebase used a
    plain `date` field): constructing an SQLModel table=True object
    directly from a JSON request body does NOT convert ISO date strings
    ("2026-01-01") into actual Python date objects — they stay strings,
    and SQLite then rejects the insert with a cryptic type error. This
    never surfaced before because every existing model used `datetime`
    (which doesn't have this issue), never plain `date`. Call this right
    after receiving any request body with `date` fields, before using it."""
    for field in field_names:
        value = getattr(obj, field, None)
        if isinstance(value, str):
            setattr(obj, field, date.fromisoformat(value))
    return obj
# Tightened Aug 2026 (real login shipped): this can no longer be "*" now
# that auth relies on a real session token instead of a client-supplied
# role param — an explicit origin list, not a wildcard.
# bolton-frontend.onrender.com and bolton-backend.onrender.com are
# different subdomains of a shared hosting domain (onrender.com is on the
# public suffix list, same reasoning as github.io/herokuapp.com), so the
# browser treats them as cross-site. The session token itself no longer
# rides as a cookie (moved to an Authorization header, Aug 2026 — see
# auth.py's docstring for the real mobile bug that caused this: the
# cookie was correctly configured, SameSite=None + Secure, but that
# didn't stop mobile browsers' separate third-party-cookie-blocking
# policies from silently refusing to persist it), so allow_credentials
# below is no longer load-bearing for auth itself — left on since it's
# harmless and this list of allowed origins still matters for CORS
# regardless.
# Closed-by-default enforcement (confirmed Aug 2026, ported from review of
# a parallel patch): every request must carry a valid session UNLESS its
# path is explicitly allowlisted below. This sits on top of the
# per-endpoint `role: str = Depends(get_current_role)` already used
# throughout — that per-endpoint check only protects an endpoint that
# remembers to declare it (the commission-rate CRUD endpoints were a real
# example of one that didn't, until this same review caught it). This
# middleware means a future endpoint that forgets isn't silently left
# wide open. Registered BEFORE CORSMiddleware below — Starlette makes the
# LAST-registered middleware the OUTERMOST one, so CORS must be added
# after this to wrap it; otherwise a 401 short-circuit here never reaches
# CORSMiddleware and the browser can't even read the error response
# (confirmed by testing: without this ordering, Access-Control-Allow-
# Origin was missing from blocked responses entirely).
PUBLIC_PATHS = {"/auth/login", "/auth/logout", "/auth/me", "/", "/docs", "/openapi.json", "/redoc"}


@app.middleware("http")
async def require_auth(request: Request, call_next):
    if request.method == "OPTIONS" or request.url.path in PUBLIC_PATHS:
        return await call_next(request)
    if _resolve_session(request) is None:
        return JSONResponse(status_code=401, content={"detail": "Not logged in — please log in again"})
    return await call_next(request)


FRONTEND_ORIGINS = [
    "https://bolton-frontend.onrender.com",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=FRONTEND_ORIGINS,
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",  # local dev only
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _ensure_new_columns():
    """Multi-tenant groundwork (confirmed Aug 2026): adds every new
    column introduced by this brief to whichever already-existing tables
    need them, WITHOUT touching or requiring re-entry of any existing
    data. SQLModel.metadata.create_all() (called right after this) only
    creates tables that don't exist yet — it never alters an existing
    table — so on a live database where these tables already exist, the
    new fields in models.py would otherwise silently do nothing at all
    (a real bug caught testing this against a real pre-existing SQLite
    copy: the tenant_id backfill alone wasn't enough — the new
    BusinessSettings Part 2 fields, e.g. flooring_margin_warn_threshold,
    needed the exact same treatment on that same already-existing table).
    Engine-agnostic (identical SQL works against local SQLite and live
    Supabase Postgres), idempotent — only ever adds a column if it isn't
    already there — and safe to run on every startup.

    Existing columns for every table are captured up front, once, then
    updated in memory as columns get added — deliberately not
    re-querying the DB mid-loop, so there's no dependency on how
    aggressively the DB driver caches schema reflection within one
    transaction."""
    from sqlalchemy import inspect, text
    new_columns = [
        # (table, column, SQL type, default SQL literal)
        # Part 1 — tenant_id on every business-data table:
        ("app_user", "tenant_id", "VARCHAR", f"'{DEFAULT_TENANT_ID}'"),
        ("flooringproduct", "tenant_id", "VARCHAR", f"'{DEFAULT_TENANT_ID}'"),
        ("trimproduct", "tenant_id", "VARCHAR", f"'{DEFAULT_TENANT_ID}'"),
        ("blindsproduct", "tenant_id", "VARCHAR", f"'{DEFAULT_TENANT_ID}'"),
        ("client", "tenant_id", "VARCHAR", f"'{DEFAULT_TENANT_ID}'"),
        ("quote", "tenant_id", "VARCHAR", f"'{DEFAULT_TENANT_ID}'"),
        ("quotelineitem", "tenant_id", "VARCHAR", f"'{DEFAULT_TENANT_ID}'"),
        ("employee", "tenant_id", "VARCHAR", f"'{DEFAULT_TENANT_ID}'"),
        ("commissionrate", "tenant_id", "VARCHAR", f"'{DEFAULT_TENANT_ID}'"),
        ("commissionpayment", "tenant_id", "VARCHAR", f"'{DEFAULT_TENANT_ID}'"),
        ("hoursworked", "tenant_id", "VARCHAR", f"'{DEFAULT_TENANT_ID}'"),
        ("document", "tenant_id", "VARCHAR", f"'{DEFAULT_TENANT_ID}'"),
        ("leavebalance", "tenant_id", "VARCHAR", f"'{DEFAULT_TENANT_ID}'"),
        ("leaverequest", "tenant_id", "VARCHAR", f"'{DEFAULT_TENANT_ID}'"),
        ("colourchangelog", "tenant_id", "VARCHAR", f"'{DEFAULT_TENANT_ID}'"),
        ("paymentfollowup", "tenant_id", "VARCHAR", f"'{DEFAULT_TENANT_ID}'"),
        ("businesssettings", "tenant_id", "VARCHAR", f"'{DEFAULT_TENANT_ID}'"),
        # Part 2 — hardcoded business-rule constants moved onto BusinessSettings:
        ("businesssettings", "flooring_margin_warn_threshold", "FLOAT", "0.30"),
        ("businesssettings", "stairwell_labour_closed", "FLOAT", "250.0"),
        ("businesssettings", "stairwell_labour_one_side_open", "FLOAT", "300.0"),
        ("businesssettings", "stairwell_labour_both_sides_open", "FLOAT", "350.0"),
        ("businesssettings", "stairwell_default_glue_cost_per_unit", "FLOAT", "1193.50"),
        ("businesssettings", "stairwell_default_glue_coverage_m2", "FLOAT", "70.0"),
        ("businesssettings", "default_bag_cost", "FLOAT", "235.0"),
        ("businesssettings", "default_bag_coverage_smooth_m2", "FLOAT", "4.0"),
        ("businesssettings", "default_bag_coverage_over_tiles_m2", "FLOAT", "3.0"),
        ("businesssettings", "default_bag_coverage_removed_tiles_m2", "FLOAT", "2.0"),
        ("businesssettings", "tile_removal_fee_per_m2_incl_vat", "FLOAT", "45.0"),
        # Part 3 — logo pulled from settings instead of hardcoded in the frontend:
        ("businesssettings", "logo_base64", "TEXT", "''"),
        # Login & Session Activity Log Phase 1 (confirmed Aug 2026):
        ("usersession", "ended_at", "TIMESTAMP", "NULL"),   # FIXED (confirmed via real Render/Postgres deploy failure, Aug 2026): DATETIME is not a valid Postgres type name (psycopg2.errors.UndefinedObject) — SQLite silently accepted it since it has no real type enforcement, which is exactly why this wasn't caught by local SQLite testing. TIMESTAMP is valid in both.
        # Stairwell landing folded into the stairwell line (confirmed Aug 2026):
        ("quotelineitem", "landing_area_m2", "FLOAT", "NULL"),
        ("quotelineitem", "landing_sell_total", "FLOAT", "NULL"),
        # Supplier & Price Book Management Console (confirmed Aug 2026):
        ("flooringproduct", "glue_rate_per_m2", "FLOAT", "NULL"),
        ("flooringproduct", "labour_rate_per_m2", "FLOAT", "NULL"),
        ("flooringproduct", "default_own_staff", "BOOLEAN", "TRUE"),   # TRUE, not 1 — confirmed Postgres boolean literal syntax (learned from the earlier DATETIME mistake: verify type/literal syntax explicitly, don't assume SQLite's permissiveness proves Postgres compatibility)
        ("flooringproduct", "price_zone_a", "FLOAT", "NULL"),
        ("flooringproduct", "price_zone_b", "FLOAT", "NULL"),
        ("flooringproduct", "price_zone_c", "FLOAT", "NULL"),
        ("businesssettings", "pricing_zone", "VARCHAR", "'A'"),
        # Per-supplier zone pricing (confirmed Aug 2026) — NULL default,
        # not 'A': unlike businesssettings.pricing_zone (exactly one row,
        # always meaningful), most supplierdefault rows are for suppliers
        # with NO zone pricing at all (e.g. a trade-discount-only
        # supplier) — defaulting every one of those to 'A' would be
        # wrong, implying a zone setting that doesn't apply. The
        # dedicated startup backfill below (on_startup()) sets the real
        # value only for suppliers that actually have zone pricing.
        ("supplierdefault", "pricing_zone", "VARCHAR", "NULL"),
        # Price per box (confirmed Aug 2026, Supplier Console Field
        # Sequence Redesign brief — root cause fix for the Como Flooring
        # pricing bug: base_cost_ex_vat/price_zone_a/b/c had no
        # dedicated "price per box" field to hold a supplier's stated
        # box price, so it landed directly in the per-m2 field instead.
        # NULL default, not 0 — the one-time startup migration below
        # (on_startup()) populates every existing product's real value;
        # a fresh 0 would be indistinguishable from "genuinely zero".
        ("flooringproduct", "price_per_box_ex_vat", "FLOAT", "NULL"),
        ("flooringproduct", "price_per_box_zone_a", "FLOAT", "NULL"),
        ("flooringproduct", "price_per_box_zone_b", "FLOAT", "NULL"),
        ("flooringproduct", "price_per_box_zone_c", "FLOAT", "NULL"),
        # Standard Import Format (confirmed Aug 2026 — deterministic
        # spreadsheet import replacing direct AI-PDF extraction going
        # forward): two fields the master spreadsheet format asks for
        # that had nowhere to go before — same "add the field rather
        # than silently drop the data" lesson as price_per_box_ex_vat
        # above.
        ("flooringproduct", "sku", "VARCHAR", "NULL"),
        ("flooringproduct", "wear_layer_mm", "FLOAT", "NULL"),
        # Master Spreadsheet System of Record (confirmed Aug 2026):
        ("flooringproduct", "discontinued", "BOOLEAN", "FALSE"),
        # Courier/Delivery Cost Toggle (confirmed Aug 2026) — reuses the
        # EXISTING flooringproduct.delivery_fee_per_m2 column (already
        # in the schema, no migration needed for it) rather than a new
        # courier field — confirmed by Burgert directly that this is the
        # same real cost, deliberately still marked up (see models.py's
        # FlooringProduct.delivery_fee_per_m2 docstring). Only the
        # supplier-level default is new. NULL default, not 0 — mirrors
        # default_trade_discount_pct's own reasoning (None means "no
        # default set", not "default is zero").
        ("supplierdefault", "default_delivery_fee_per_m2", "FLOAT", "NULL"),
        # Transport Levy (confirmed Aug 2026) — manual, per-job, opt-in;
        # 0.0 default so every existing quote is completely unaffected.
        ("quote", "transport_levy", "FLOAT", "0.0"),
    ]
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    table_columns = {t: {c["name"] for c in inspector.get_columns(t)} for t in existing_tables}
    with engine.begin() as conn:
        for table, column, sql_type, default_literal in new_columns:
            if table not in existing_tables:
                continue  # brand new table — create_all() right after this will create it already carrying every current field, nothing to backfill
            if column in table_columns[table]:
                continue
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type} DEFAULT {default_literal}"))
            table_columns[table].add(column)
            print(f"Migration: added {column} to {table}, defaulted to {default_literal}")


@app.on_event("startup")
def on_startup():
    _ensure_new_columns()
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        if not session.exec(select(CommissionRate)).first():
            # Confirmed Aug 2026: seeding the brief's own recommended GP
            # tiers for pure_sales, plus the confirmed blinds=10% rate for
            # builder_rep. All editable afterward via the rate card —
            # this is a starting point, not a hardcoded permanent value.
            default_rates = [
                CommissionRate(role_type="pure_sales", basis="gp", tier_min=0, tier_max=50000, rate_pct=0.08),
                CommissionRate(role_type="pure_sales", basis="gp", tier_min=50000, tier_max=100000, rate_pct=0.10),
                CommissionRate(role_type="pure_sales", basis="gp", tier_min=100000, tier_max=None, rate_pct=0.12),
                CommissionRate(role_type="builder_rep", basis="ex_vat_price", category="blinds", rate_pct=0.10),
                # flooring/trim/skirting builder_rep rates intentionally NOT
                # seeded — Burgert confirmed these need their own specific
                # rates, not guessed at. Add via POST /commission-rates
                # once confirmed. Until then, those categories show R0
                # commission with a clear "no rate configured" flag rather
                # than silently applying the wrong number.
            ]
            for r in default_rates:
                session.add(r)
            session.commit()

        if not session.exec(select(User)).first():
            # One-time seed (confirmed Aug 2026): only runs while the
            # app_user table is empty, so it never overwrites passwords
            # anyone has since changed via /auth/change-password. These
            # are temporary initial passwords, hashed here — the plaintext
            # was reported to Burgert directly in chat, never committed to
            # source control, and each user should change theirs on
            # first login.
            seed_users = [
                User(username="burgert", display_name="Burgert", role=UserRole.owner,
                     password_hash="pbkdf2_sha256$260000$295728ae9fea1b39e0acbc754f8b57af$bbf141469314ddc450ffdbdf30c2895c321f7accf2c721c2e3ec8d34e68fdf22"),
                User(username="ryno", display_name="Ryno", role=UserRole.sales,
                     password_hash="pbkdf2_sha256$260000$60f81b8c4153c7ec982d6c5415a5f675$b6d263a91c3c1722ff6d6f99705b193c5f43c4ededfa3d307b314fe28fefd915"),
                User(username="madri", display_name="Madri", role=UserRole.admin,
                     password_hash="pbkdf2_sha256$260000$eb80a40b8263fc5984c0fc64ecd2ddca$e52495c5a0605b9a208db009fe8bbb6a1053d09e1f4744223d4fce3728dfb962"),
            ]
            for u in seed_users:
                session.add(u)
            session.commit()

        # Per-supplier zone pricing backfill (confirmed Aug 2026): zone
        # pricing used to always resolve via the one global
        # BusinessSettings.pricing_zone for every zone-priced supplier —
        # now each such supplier gets its own SupplierDefault.pricing_zone
        # instead (see resolve_zone_price). Runs on every startup but
        # only ever ACTS the first time for a given supplier: for every
        # distinct (tenant, supplier) with at least one zone-priced
        # FlooringProduct, ensures a SupplierDefault row exists with
        # pricing_zone carried forward from whatever that tenant's
        # global setting currently is — so no supplier's effective price
        # silently changed the moment this shipped (confirmed
        # requirement: Azura keeps computing at Zone A, its existing
        # default, unless deliberately changed afterward). Never
        # overwrites a pricing_zone that's already set on an existing
        # SupplierDefault row — from an earlier run of this exact
        # backfill, or a real per-supplier change made since — this is a
        # one-time-per-supplier seed, not an ongoing sync back to the
        # global setting.
        zone_priced_products = session.exec(
            select(FlooringProduct).where(
                (FlooringProduct.price_zone_a.is_not(None))
                | (FlooringProduct.price_zone_b.is_not(None))
                | (FlooringProduct.price_zone_c.is_not(None))
            )
        ).all()
        zone_priced_suppliers = {(p.tenant_id, p.supplier) for p in zone_priced_products}
        for zp_tenant_id, zp_supplier in zone_priced_suppliers:
            zp_settings = get_settings(session, zp_tenant_id)
            existing = session.exec(
                select(SupplierDefault).where(
                    SupplierDefault.tenant_id == zp_tenant_id,
                    SupplierDefault.supplier == zp_supplier,
                )
            ).first()
            if existing:
                if not existing.pricing_zone:
                    existing.pricing_zone = zp_settings.pricing_zone
                    session.add(existing)
            else:
                session.add(SupplierDefault(tenant_id=zp_tenant_id, supplier=zp_supplier, pricing_zone=zp_settings.pricing_zone))
        session.commit()

        # Price-per-box migration (confirmed Aug 2026, Supplier Console
        # Field Sequence Redesign brief — the actual root cause of the
        # Como Flooring pricing bug: base_cost_ex_vat/price_zone_a/b/c
        # never had a dedicated "price per box" field, so a supplier's
        # stated box price landed directly in the per-m2 field instead).
        # Runs on every startup, only ever ACTS once per product — any
        # FlooringProduct that already has price_per_box_ex_vat OR any
        # price_per_box_zone_* set is treated as already migrated.
        #
        # Two different starting assumptions per product's CURRENT
        # per-m2 value, per the brief's own Section 5 — the one
        # deliberate supplier-specific branch in this whole feature (a
        # one-time historical-data decision about which suppliers'
        # existing data is already correct, not part of the ongoing
        # calculation logic below, which has no supplier branches at
        # all):
        #   - Como Flooring (confirmed wrong): the value currently in
        #     base_cost_ex_vat / price_zone_a/b/c IS a box price that
        #     was never divided — move it AS-IS into the new box-price
        #     field, then calculate the true per-m2 price from it for
        #     the first time.
        #   - every other supplier, e.g. Azura (confirmed correct): the
        #     value currently in base_cost_ex_vat / price_zone_a/b/c is
        #     already right — back-calculate the box price from it
        #     (per-m2 x m2_per_pack) and leave the per-m2 fields
        #     completely untouched.
        # Products with no m2_per_pack are skipped entirely — neither
        # direction of this calculation is possible without it, and
        # this migration never guesses one; left for manual review.
        #
        # This is a mechanical, uniform pass — it corrects the box-
        # price-in-the-wrong-field bug for every Como product whose
        # OTHER stored data (m2_per_pack, matched to the right range)
        # was already accurate. It does NOT independently catch the two
        # separately-confirmed name-contamination bugs ("Como Bonsai
        # 2.0 / Como Bellagio", "deZIGN series 200" aliasing) — those
        # need the PDF-hand-verified stageComoVerifiedCorrections tool
        # (frontend/index.html) as a second pass, same as before.
        price_box_migration_pairs = (
            ("base_cost_ex_vat", "price_per_box_ex_vat"),
            ("price_zone_a", "price_per_box_zone_a"),
            ("price_zone_b", "price_per_box_zone_b"),
            ("price_zone_c", "price_per_box_zone_c"),
        )
        unmigrated = session.exec(
            select(FlooringProduct).where(
                FlooringProduct.price_per_box_ex_vat.is_(None),
                FlooringProduct.price_per_box_zone_a.is_(None),
                FlooringProduct.price_per_box_zone_b.is_(None),
                FlooringProduct.price_per_box_zone_c.is_(None),
            )
        ).all()
        migrated_products = 0
        for p in unmigrated:
            if not p.m2_per_pack or p.m2_per_pack <= 0:
                continue
            is_como = "como" in (p.supplier or "").lower()
            touched = False
            for per_m2_field, box_field in price_box_migration_pairs:
                per_m2_value = getattr(p, per_m2_field)
                if per_m2_value is None:
                    continue
                if is_como:
                    new_box = per_m2_value            # confirmed-wrong value IS the box price
                    new_per_m2 = round(new_box / p.m2_per_pack, 4)
                else:
                    new_box = round(per_m2_value * p.m2_per_pack, 4)   # back-calculated from confirmed-correct per-m2
                    new_per_m2 = per_m2_value          # untouched
                setattr(p, box_field, new_box)
                session.add(AuditLog(
                    tenant_id=p.tenant_id, username="system-migration", entity_type="FlooringProduct", entity_id=p.id,
                    field=box_field, old_value="(new)", new_value=str(new_box),
                ))
                if new_per_m2 != per_m2_value:
                    setattr(p, per_m2_field, new_per_m2)
                    session.add(AuditLog(
                        tenant_id=p.tenant_id, username="system-migration", entity_type="FlooringProduct", entity_id=p.id,
                        field=per_m2_field, old_value=str(per_m2_value), new_value=str(new_per_m2),
                    ))
                touched = True
            if touched:
                session.add(p)
                migrated_products += 1
        if migrated_products:
            print(f"Migration: price-per-box field sequence redesign — migrated {migrated_products} FlooringProduct row(s)")
        session.commit()

        # Courier/Delivery Cost Toggle (confirmed Aug 2026, Courier
        # Toggle brief) — hardwired for Aspen, reusing the EXISTING
        # delivery_fee_per_m2 field (confirmed by Burgert directly: this
        # IS the courier cost, not a separate thing — no new product
        # field). Two parts, both idempotent and safe to run on every
        # startup:
        #  1. SupplierDefault backfill, same "only set if not already
        #     set" pattern as the zone-pricing backfill above — so any
        #     NEW Aspen product created from now on pre-fills correctly.
        #  2. One-time bulk update (Section 4 of the brief) on Aspen
        #     products ALREADY in Bolton. delivery_fee_per_m2 defaults to
        #     0.0, not NULL, so — unlike the price-per-box migration
        #     above — "still at 0.0" can't be told apart from "Burgert
        #     deliberately set this Aspen product's delivery fee to R0"
        #     using this field alone. Deliberately conservative: only
        #     touches rows CURRENTLY AT EXACTLY 0.0 (the untouched
        #     default), never overwrites any other already-set value, so
        #     this can never clobber a real per-product customization —
        #     matches this project's standing rule of never silently
        #     overwriting data it can't be certain about.
        aspen_pairs = {
            (p.tenant_id, p.supplier)
            for p in session.exec(select(FlooringProduct).where(FlooringProduct.supplier.ilike("%aspen%"))).all()
        }
        for aspen_tenant_id, aspen_supplier in aspen_pairs:
            existing_default = session.exec(
                select(SupplierDefault).where(
                    SupplierDefault.tenant_id == aspen_tenant_id,
                    SupplierDefault.supplier == aspen_supplier,
                )
            ).first()
            if existing_default:
                if existing_default.default_delivery_fee_per_m2 is None:
                    existing_default.default_delivery_fee_per_m2 = 15.00
                    session.add(existing_default)
            else:
                session.add(SupplierDefault(
                    tenant_id=aspen_tenant_id, supplier=aspen_supplier,
                    default_delivery_fee_per_m2=15.00,
                ))

        aspen_unmigrated = session.exec(
            select(FlooringProduct).where(
                FlooringProduct.delivery_fee_per_m2 == 0.0,
                FlooringProduct.supplier.ilike("%aspen%"),
            )
        ).all()
        for p in aspen_unmigrated:
            p.delivery_fee_per_m2 = 15.00
            session.add(p)
            session.add(AuditLog(
                tenant_id=p.tenant_id, username="system-migration", entity_type="FlooringProduct", entity_id=p.id,
                field="delivery_fee_per_m2", old_value="0.0", new_value="15.0",
            ))
        if aspen_unmigrated:
            print(f"Migration: courier/delivery fee bulk update — set R15.00/m² for {len(aspen_unmigrated)} Aspen FlooringProduct row(s)")
        session.commit()


def get_session():
    with Session(engine) as session:
        yield session


def _get_bearer_token(request: Request) -> Optional[str]:
    """Session token transport, changed Aug 2026 (see auth.py's docstring
    for the full root-cause writeup): moved off an HttpOnly cookie onto a
    plain `Authorization: Bearer <token>` header, set by the frontend
    from the login response body and re-attached to every request by its
    global fetch() wrapper — a real mobile bug, confirmed via Render
    logs (login succeeded, every request after it 401'd, consistently,
    on mobile only), traced to bolton-frontend/bolton-backend being
    different *sites* per the browser (onrender.com is on the Public
    Suffix List), making the session cookie a genuine third-party
    cookie that mobile browsers silently refused to persist — no amount
    of correct SameSite/Secure/credentials-include configuration fixes
    that, since it's a separate browser policy layered on top."""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[len("Bearer "):].strip() or None
    return None


def _resolve_session(request: Request) -> Optional[dict]:
    """Shared by get_current_role/get_current_tenant (per-endpoint checks)
    and the require_auth middleware (closed-by-default check) — one place
    that actually validates the session token, so none of them can
    drift. Cached on request.state (confirmed Aug 2026, added alongside
    get_current_tenant): without this, a single request using both
    get_current_role and get_current_tenant as separate Depends() would
    hit the DB twice for the exact same token lookup — cheap at today's
    scale, but no reason to pay it twice when one lookup already answers
    both questions."""
    if hasattr(request.state, "bolton_session"):
        return request.state.bolton_session
    token = _get_bearer_token(request)
    result = None
    if token:
        with Session(engine) as session:
            sess = session.exec(select(UserSession).where(UserSession.token == token)).first()
            # ended_at check (confirmed Aug 2026, Login & Session Activity
            # Log): a real logout now soft-ends a session (sets ended_at)
            # instead of deleting the row, so the log has real history —
            # but that means expires_at alone is no longer enough to prove
            # a session is still valid. An ended session must stop working
            # immediately, not linger until its original 24h expiry.
            if sess and sess.ended_at is None and sess.expires_at >= datetime.utcnow():
                user = session.get(User, sess.user_id)
                if user and user.active:
                    result = {"role": user.role, "tenant_id": user.tenant_id, "user_id": user.id, "username": user.username}
    request.state.bolton_session = result
    return result


def get_real_role(request: Request) -> str:
    """The actual authenticated role — NEVER affected by Owner Preview
    Mode (confirmed Aug 2026). Used only where the real identity matters
    regardless of any active preview: deciding whether to honor a
    preview header at all, and /auth/me's own response."""
    session_data = _resolve_session(request)
    if session_data is None:
        raise HTTPException(401, "Not logged in — please log in again")
    return session_data["role"]


def get_current_role(request: Request) -> str:
    """Replaces the old client-supplied `role` query param (confirmed Aug
    2026 — anyone could just claim to be Owner by editing the URL). Role
    now comes exclusively from a validated server-side session looked up
    by the bearer token (see _get_bearer_token); there is no way for the
    frontend to override it.

    Owner Preview Mode (confirmed Aug 2026): when the REAL role is
    owner, an X-Preview-Role header ('sales' or 'admin') swaps the role
    every endpoint downstream sees for the rest of this request — same
    field-stripping, same permission checks (e.g. require_owner, which
    depends on this same function), just fed a different role value.
    This is deliberately the ONLY function that needs to know about
    preview mode — every endpoint already reads role through here (or
    through require_owner, which reads it through here too), so nothing
    else needed to change. Any non-owner sending this header is
    silently ignored — no second path, no exceptions, per the brief."""
    session_data = _resolve_session(request)
    if session_data is None:
        raise HTTPException(401, "Not logged in — please log in again")
    real_role = session_data["role"]
    if real_role == UserRole.owner:
        preview = request.headers.get("X-Preview-Role")
        if preview in (UserRole.sales, UserRole.admin):
            return preview
    return real_role


def get_current_tenant(request: Request) -> str:
    """Multi-tenant groundwork (confirmed Aug 2026): the tenant_id every
    endpoint scopes its queries by. Comes from the logged-in user's own
    tenant_id — never client-supplied, same trust boundary as role
    above. Every real user today belongs to tenant '1' (Blinds &
    Flooring Studio), so this always resolves to the same value right
    now — the point is that every query already goes through this, so
    nothing needs auditing/retrofitting later when a second tenant is
    real."""
    session_data = _resolve_session(request)
    if session_data is None:
        raise HTTPException(401, "Not logged in — please log in again")
    return session_data["tenant_id"]


def get_current_username(request: Request) -> str:
    """The real authenticated username — for AuditLog entries (confirmed
    Aug 2026, Supplier Console brief: "who made the change" must be the
    real person, never affected by Owner Preview Mode — not that it can
    diverge in practice, since anything gated by require_owner already
    can't be reached while previewing as a lesser role)."""
    session_data = _resolve_session(request)
    if session_data is None:
        raise HTTPException(401, "Not logged in — please log in again")
    return session_data["username"]


class LoginRequest(BaseModel):
    username: str
    password: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


@app.post("/auth/login")
def login(body: LoginRequest):
    # Body, not query params (unlike the rest of this API's endpoints) —
    # deliberate: credentials must never land in a URL, where they'd be
    # captured by Render/proxy access logs and browser history.
    with Session(engine) as session:
        user = session.exec(select(User).where(User.username == body.username.strip().lower())).first()
        # Deliberately identical error for "no such user" and "wrong
        # password" — doesn't leak which usernames exist.
        if not user or not user.active or not verify_password(body.password, user.password_hash):
            raise HTTPException(401, "Incorrect username or password")
        token = new_session_token()
        sess = UserSession(token=token, user_id=user.id, expires_at=new_expiry())
        session.add(sess)
        session.commit()
        # Returned in the body, not set as a cookie (changed Aug 2026 —
        # see auth.py's docstring) — the frontend stores this itself
        # (localStorage) and re-attaches it as an Authorization header on
        # every subsequent request.
        return {"username": user.username, "display_name": user.display_name, "role": user.role, "token": token}


@app.post("/auth/logout")
def logout(request: Request):
    token = _get_bearer_token(request)
    if token:
        with Session(engine) as session:
            sess = session.exec(select(UserSession).where(UserSession.token == token)).first()
            # CHANGED Aug 2026 (Login & Session Activity Log): used to
            # delete the row outright — now soft-ends it (sets ended_at)
            # so a real login/logout history survives for the session
            # log. _resolve_session() treats an ended_at session as
            # invalid immediately, same practical effect as deleting it
            # for auth purposes, just without losing the record.
            if sess and sess.ended_at is None:
                sess.ended_at = datetime.utcnow()
                session.add(sess)
                session.commit()
    return {"ok": True}


@app.get("/auth/me")
def get_me(request: Request):
    token = _get_bearer_token(request)
    if not token:
        raise HTTPException(401, "Not logged in")
    with Session(engine) as session:
        sess = session.exec(select(UserSession).where(UserSession.token == token)).first()
        if not sess or sess.ended_at is not None or sess.expires_at < datetime.utcnow():
            raise HTTPException(401, "Session expired — please log in again")
        user = session.get(User, sess.user_id)
        if not user or not user.active:
            raise HTTPException(401, "Account not available")
        return {"username": user.username, "display_name": user.display_name, "role": user.role}


@app.post("/auth/change-password")
def change_password(body: ChangePasswordRequest, request: Request, role: str = Depends(get_current_role)):
    if len(body.new_password) < 8:
        raise HTTPException(400, "New password must be at least 8 characters.")
    token = _get_bearer_token(request)
    with Session(engine) as session:
        sess = session.exec(select(UserSession).where(UserSession.token == token)).first()
        user = session.get(User, sess.user_id)
        if not verify_password(body.current_password, user.password_hash):
            raise HTTPException(401, "Current password is incorrect.")
        user.password_hash = hash_password(body.new_password)
        session.add(user)
        session.commit()
        return {"ok": True}


def get_or_404(session: Session, model, obj_id: int, tenant_id: str, name: str = "Record"):
    """Multi-tenant groundwork (confirmed Aug 2026): replaces the
    session.get(Model, id) + `if not X: raise 404` pattern used
    throughout this file with one that also enforces the tenant
    boundary. A row that exists but belongs to a different tenant comes
    back as the SAME 404 as a row that doesn't exist at all — never a
    distinct "belongs to someone else" error, which would let one
    tenant probe for another tenant's real IDs."""
    obj = session.get(model, obj_id)
    if not obj or obj.tenant_id != tenant_id:
        raise HTTPException(404, f"{name} not found")
    return obj


def strip_sensitive_fields(line_item: dict, role: str) -> dict:
    """Sales role never sees cost or margin — enforced server-side, not just
    hidden in the UI. Applies to quote line items wherever they're returned.
    bags_allowed and tile_removal_fee_total stay visible — they're
    operational/client-facing info, not cost data. Everything else that
    reveals what a job actually costs (material, glue, labour, compound,
    the total real cost) is stripped.

    BUG FOUND AND FIXED Aug 2026 (caught during regression testing while
    building the Supplier Console, not something that brief asked for):
    vinyl_cost_total/nosing_cost_total (stairwell-specific cost fields)
    were only ever stripped in add_stairwell_line's own immediate
    response — get_quote/list_quotes call this shared function but never
    knew about those two fields, so re-opening a saved stairwell quote
    leaked real cost data to Sales even though adding the line in the
    first place correctly hid it. Centralizing the strip here closes
    that gap everywhere this function is called, not just at creation
    time — the ad-hoc pop() calls that used to live in add_stairwell_line
    are removed as redundant now that this covers it."""
    if role == UserRole.sales:
        line_item = dict(line_item)
        for field in (
            "unit_cost", "margin_pct", "glue_cost_total",
            "labour_cost_total", "compound_cost_total", "total_job_cost",
            "vinyl_cost_total", "nosing_cost_total",
            # BUG FOUND AND FIXED (Aug 2026, Courier Toggle brief — caught
            # while adding delivery/courier fee visibility to the
            # internal quote-builder view, not something the brief itself
            # asked for): delivery_fee_total has ALWAYS been genuinely
            # cost data (real pass-through spend, same class as glue/
            # labour) and supabase_schema.sql's own column comment
            # already claimed it was "RLS: hide from sales role" — but it
            # was never actually added to this strip list, so it's been
            # silently visible to Sales this whole time. Closing that
            # gap now, before making it more visible elsewhere (this
            # brief's new internal quote-line display) would have made
            # the existing leak worse, not better.
            "delivery_fee_total",
        ):
            line_item.pop(field, None)
    return line_item


# ---------- Price Book: Flooring ----------

@app.get("/price-book/flooring", response_model=List[FlooringProduct])
def list_flooring(tenant_id: str = Depends(get_current_tenant)):
    with Session(engine) as session:
        return session.exec(select(FlooringProduct).where(FlooringProduct.tenant_id == tenant_id)).all()


@app.post("/price-book/flooring", response_model=FlooringProduct)
def create_flooring(product: FlooringProduct, tenant_id: str = Depends(get_current_tenant)):
    product.tenant_id = tenant_id   # never client-supplied — same trust boundary as role
    with Session(engine) as session:
        session.add(product)
        session.commit()
        session.refresh(product)
        return product


@app.put("/price-book/flooring/{product_id}", response_model=FlooringProduct)
def update_flooring(product_id: int, updates: FlooringProduct, tenant_id: str = Depends(get_current_tenant)):
    with Session(engine) as session:
        product = get_or_404(session, FlooringProduct, product_id, tenant_id, "Flooring product")
        data = updates.dict(exclude_unset=True, exclude={"id", "tenant_id"})
        for k, v in data.items():
            setattr(product, k, v)
        product.last_updated = datetime.utcnow()
        session.add(product)
        session.commit()
        session.refresh(product)
        return product


@app.delete("/price-book/flooring/{product_id}")
def delete_flooring(product_id: int, tenant_id: str = Depends(get_current_tenant)):
    with Session(engine) as session:
        product = get_or_404(session, FlooringProduct, product_id, tenant_id, "Flooring product")
        session.delete(product)
        session.commit()
        return {"deleted": product_id}


@app.post("/price-book/flooring/bulk-import")
def bulk_import_flooring(products: List[FlooringProduct], tenant_id: str = Depends(get_current_tenant)):
    """Confirmed Aug 2026 — loading a full supplier range one product at
    a time through the form doesn't scale (e.g. Aspen's 35+ colours
    across 5 ranges). Takes a list of the same shape the single-create
    endpoint accepts. All-or-nothing — if any product fails validation,
    nothing is committed, so a bad import can't leave the price book
    half-populated."""
    with Session(engine) as session:
        for product in products:
            product.tenant_id = tenant_id
            session.add(product)
        session.commit()
        return {"imported": len(products)}


# ---------- Price Book: Blinds ----------

@app.get("/price-book/blinds", response_model=List[BlindsProduct])
def list_blinds(tenant_id: str = Depends(get_current_tenant)):
    with Session(engine) as session:
        return session.exec(select(BlindsProduct).where(BlindsProduct.tenant_id == tenant_id)).all()


@app.post("/price-book/blinds", response_model=BlindsProduct)
def create_blinds(product: BlindsProduct, tenant_id: str = Depends(get_current_tenant)):
    product.tenant_id = tenant_id
    with Session(engine) as session:
        session.add(product)
        session.commit()
        session.refresh(product)
        return product


@app.put("/price-book/blinds/{product_id}", response_model=BlindsProduct)
def update_blinds(product_id: int, updates: BlindsProduct, tenant_id: str = Depends(get_current_tenant)):
    with Session(engine) as session:
        product = get_or_404(session, BlindsProduct, product_id, tenant_id, "Blinds product")
        data = updates.dict(exclude_unset=True, exclude={"id", "tenant_id"})
        for k, v in data.items():
            setattr(product, k, v)
        product.last_updated = datetime.utcnow()
        session.add(product)
        session.commit()
        session.refresh(product)
        return product


@app.delete("/price-book/blinds/{product_id}")
def delete_blinds(product_id: int, tenant_id: str = Depends(get_current_tenant)):
    with Session(engine) as session:
        product = get_or_404(session, BlindsProduct, product_id, tenant_id, "Blinds product")
        session.delete(product)
        session.commit()
        return {"deleted": product_id}


# ---------- Price Book: Trims ----------

@app.get("/price-book/trims", response_model=List[TrimProduct])
def list_trims(tenant_id: str = Depends(get_current_tenant)):
    with Session(engine) as session:
        return session.exec(select(TrimProduct).where(TrimProduct.tenant_id == tenant_id)).all()


@app.post("/price-book/trims", response_model=TrimProduct)
def create_trim(product: TrimProduct, tenant_id: str = Depends(get_current_tenant)):
    product.tenant_id = tenant_id
    with Session(engine) as session:
        session.add(product)
        session.commit()
        session.refresh(product)
        return product


@app.put("/price-book/trims/bulk-update-markup")
def bulk_update_trim_markup(new_markup: float, category: str = None, tenant_id: str = Depends(get_current_tenant)):
    """Confirmed Aug 2026 — bulk-adjust the markup multiplier across every
    markup-mode trim product in one call (optionally filtered to one
    category), instead of editing each product individually. Only
    touches products with pricing_mode == "markup" — fixed-price
    skirting doesn't use this field at all, so it's left untouched
    rather than silently rewritten. Used to raise aluminium trim margin
    from the old 1.5x default to the confirmed 1.725x (equivalent to
    cost x VAT x 1.5, but kept as a single ex-VAT multiplier, not by
    baking VAT into the calculation again — that's the exact double-VAT
    bug found and fixed back in v17).

    Registered ABOVE the /{product_id} route below on purpose: FastAPI/
    Starlette matches routes in registration order, and {product_id} is
    an untyped-at-the-routing-layer path segment (int conversion only
    happens at parameter-validation time, after a route already matched)
    — so if that route were registered first, a request to this path
    would match it first and fail trying to parse "bulk-update-markup"
    as an int, never reaching this function at all."""
    with Session(engine) as session:
        stmt = select(TrimProduct).where(TrimProduct.pricing_mode == "markup", TrimProduct.tenant_id == tenant_id)
        if category:
            stmt = stmt.where(TrimProduct.category == category)
        products = session.exec(stmt).all()
        for p in products:
            p.markup_multiplier = new_markup
            p.last_updated = datetime.utcnow()
            session.add(p)
        session.commit()
        return {"updated": len(products), "new_markup": new_markup}


@app.put("/price-book/trims/{product_id}", response_model=TrimProduct)
def update_trim(product_id: int, updates: TrimProduct, tenant_id: str = Depends(get_current_tenant)):
    with Session(engine) as session:
        product = get_or_404(session, TrimProduct, product_id, tenant_id, "Trim product")
        data = updates.dict(exclude_unset=True, exclude={"id", "tenant_id"})
        for k, v in data.items():
            setattr(product, k, v)
        product.last_updated = datetime.utcnow()
        session.add(product)
        session.commit()
        session.refresh(product)
        return product


@app.delete("/price-book/trims/{product_id}")
def delete_trim(product_id: int, tenant_id: str = Depends(get_current_tenant)):
    with Session(engine) as session:
        product = get_or_404(session, TrimProduct, product_id, tenant_id, "Trim product")
        session.delete(product)
        session.commit()
        return {"deleted": product_id}


# ---------- Analytics ----------

@app.get("/analytics/overview")
def analytics_overview(tenant_id: str = Depends(get_current_tenant)):
    """
    Business Overview data. Deliberately built from data that already
    exists — no new pricing logic here, just querying quotes/lines that
    are already being saved. Confirmed Aug 2026: conversion rate excludes
    still-open quotes (draft/sent) from the denominator — a quote that
    hasn't been decided yet isn't a loss, so counting it as one would
    understate the real win rate. "Won" = accepted, invoiced, or paid.
    "Lost" = declined only.
    """
    with Session(engine) as session:
        quotes = session.exec(select(Quote).where(Quote.tenant_id == tenant_id)).all()
        lines = session.exec(select(QuoteLineItem).where(QuoteLineItem.tenant_id == tenant_id)).all()

        # value per quote = sum of its line totals (client-facing sell price, ex VAT)
        value_by_quote = {}
        for l in lines:
            value_by_quote[l.quote_id] = value_by_quote.get(l.quote_id, 0.0) + l.line_total

        WON_STATUSES = {"accepted", "invoiced", "paid"}
        LOST_STATUSES = {"declined"}
        OPEN_STATUSES = {"draft", "sent"}

        def summarize(quote_list):
            won = [q for q in quote_list if q.status in WON_STATUSES]
            lost = [q for q in quote_list if q.status in LOST_STATUSES]
            open_ = [q for q in quote_list if q.status in OPEN_STATUSES]
            decided = won + lost
            won_value = sum(value_by_quote.get(q.id, 0.0) for q in won)
            open_value = sum(value_by_quote.get(q.id, 0.0) for q in open_)
            return {
                "total_quotes": len(quote_list),
                "open_quotes": len(open_),
                "open_value": round(open_value, 2),
                "won_quotes": len(won),
                "won_value": round(won_value, 2),
                "lost_quotes": len(lost),
                "conversion_rate_by_count": round(len(won) / len(decided), 4) if decided else None,
                "conversion_rate_by_value": round(won_value / sum(value_by_quote.get(q.id, 0.0) for q in decided), 4) if decided and sum(value_by_quote.get(q.id, 0.0) for q in decided) else None,
            }

        by_branch = {}
        for branch in set(q.branch for q in quotes):
            by_branch[branch] = summarize([q for q in quotes if q.branch == branch])

        by_rep = {}
        for rep in set(q.sales_owner for q in quotes):
            by_rep[rep] = summarize([q for q in quotes if q.sales_owner == rep])

        return {
            "overall": summarize(quotes),
            "by_branch": by_branch,
            "by_rep": by_rep,
        }


# ---------- HR: Employees ----------

def strip_employee_notes(emp_dict: dict, role: str) -> dict:
    """notes is Owner+Admin only, per the brief — same stripping pattern
    used for quote cost/margin elsewhere in this app."""
    if role == UserRole.sales:
        emp_dict.pop("notes", None)
        emp_dict.pop("id_number", None)
    return emp_dict


@app.get("/employees")
def list_employees(role: str = Depends(get_current_role), tenant_id: str = Depends(get_current_tenant)):
    with Session(engine) as session:
        employees = session.exec(select(Employee).where(Employee.tenant_id == tenant_id)).all()
        return [strip_employee_notes(e.dict(), role) for e in employees]


@app.post("/employees")
def create_employee(employee: Employee, tenant_id: str = Depends(get_current_tenant)):
    coerce_date_fields(employee, "start_date", "birthday")
    employee.tenant_id = tenant_id
    with Session(engine) as session:
        session.add(employee)
        session.commit()
        session.refresh(employee)
        return employee


@app.put("/employees/{employee_id}")
def update_employee(employee_id: int, updates: Employee, tenant_id: str = Depends(get_current_tenant)):
    coerce_date_fields(updates, "start_date", "birthday")
    with Session(engine) as session:
        emp = get_or_404(session, Employee, employee_id, tenant_id, "Employee")
        data = updates.dict(exclude_unset=True, exclude={"id", "tenant_id"})
        for k, v in data.items():
            setattr(emp, k, v)
        session.add(emp)
        session.commit()
        session.refresh(emp)
        return emp


@app.delete("/employees/{employee_id}")
def delete_employee(employee_id: int, tenant_id: str = Depends(get_current_tenant)):
    with Session(engine) as session:
        emp = get_or_404(session, Employee, employee_id, tenant_id, "Employee")
        session.delete(emp)
        session.commit()
        return {"deleted": employee_id}


# ---------- HR: Commission rate card ----------
# Owner-only to edit, per the brief ("Admin... Cannot change commission
# rates/structures"). CORRECTED Aug 2026 (caught during review of a
# parallel patch): this was flagged as frontend-gated only, with a
# comment saying server-side enforcement would follow once real auth
# existed. Real auth now exists (see get_current_role above) — this was
# a genuine gap until now, closed here with the same require_owner
# dependency used elsewhere.

def require_owner(role: str = Depends(get_current_role)) -> str:
    if role != UserRole.owner:
        raise HTTPException(403, "Only the Owner role can do this.")
    return role


# ---------- Login & Session Activity Log, Phase 1 (confirmed Aug 2026) ----------

@app.get("/admin/session-log")
def session_log(start_date: Optional[str] = None, end_date: Optional[str] = None,
                 role: str = Depends(require_owner), tenant_id: str = Depends(get_current_tenant)):
    """Read-only, Owner-only (require_owner reads through get_current_role,
    so an Owner previewing as Sales/Admin correctly gets blocked here too
    — same enforcement path as everything else in Owner Preview Mode).

    Filterable by date range (start_date/end_date, ISO date strings,
    inclusive) — the frontend's "This week"/"This month" buttons compute
    the actual range and pass it through; filtering by user happens
    client-side over this same result set, since the whole dataset is
    tiny for a 3-4 person team. No username param needed here to satisfy
    that.

    still_active / logout_time distinguishes a real logout (ended_at
    set) from a session nobody explicitly logged out of but whose 24h
    window has since passed (expires_at used as the approximate end
    time in that case) — computed here at read time, no background job
    needed. duration_minutes covers both: elapsed time up to the real
    or approximate end, or up to now for a genuinely still-active
    session."""
    with Session(engine) as session:
        stmt = select(UserSession, User).join(User, UserSession.user_id == User.id).where(User.tenant_id == tenant_id)
        rows = session.exec(stmt).all()

        now = datetime.utcnow()
        result = []
        for sess, user in rows:
            if start_date and sess.created_at.date() < date.fromisoformat(start_date):
                continue
            if end_date and sess.created_at.date() > date.fromisoformat(end_date):
                continue
            if sess.ended_at is not None:
                logout_time = sess.ended_at
                still_active = False
            elif sess.expires_at < now:
                logout_time = sess.expires_at   # natural 24h expiry, no explicit logout — approximate end time
                still_active = False
            else:
                logout_time = None
                still_active = True
            duration_minutes = round(((logout_time or now) - sess.created_at).total_seconds() / 60, 1)
            result.append({
                "username": user.username,
                "display_name": user.display_name,
                "login_time": sess.created_at.isoformat(),
                "logout_time": logout_time.isoformat() if logout_time else None,
                "still_active": still_active,
                "duration_minutes": duration_minutes,
            })
        result.sort(key=lambda r: r["login_time"], reverse=True)
        return result


# ---------- Supplier & Price Book Management Console (confirmed Aug 2026) ----------

ENTITY_TYPE_MODELS = {
    "FlooringProduct": FlooringProduct,
    "BlindsProduct": BlindsProduct,
    "TrimProduct": TrimProduct,
    "SupplierDefault": SupplierDefault,
}

# Confirmed Aug 2026, Console delete feature: which QuoteLineItem.category
# values reference which entity type via product_id, used to check
# "is this product actually used on a real quote" before a delete is
# allowed to proceed. FlooringProduct covers both regular flooring lines
# AND stairwell lines (a stairwell line's product_id is the vinyl
# product — see add_stairwell_line). Known gap, not fixed here: a
# TrimProduct used only as a stairwell's NOSING product isn't tracked by
# id on QuoteLineItem (only nosing_length_m, a computed value survives),
# so this check can't catch that specific usage — deleting a nosing
# product still in active use on a stairwell quote would not be blocked.
ENTITY_TYPE_LINE_CATEGORIES = {
    "FlooringProduct": ["flooring", "stairwell"],
    "BlindsProduct": ["blinds"],
    "TrimProduct": ["trim"],
}

# Human-readable labels for the commit confirmation message and the
# audit log UI — e.g. "Price per m²" instead of "base_cost_ex_vat".
FIELD_LABELS = {
    "product_name": "Range", "colour": "Colour", "supplier": "Supplier",
    "base_cost_ex_vat": "Price per m² (ex VAT, calculated)", "m2_per_pack": "m² per box",
    "price_per_box_ex_vat": "Price per box (ex VAT, before discount)",
    "price_per_box_zone_a": "Zone A price per box", "price_per_box_zone_b": "Zone B price per box", "price_per_box_zone_c": "Zone C price per box",
    "trade_discount_pct": "Trade discount %", "wastage_pct": "Wastage %",
    "settlement_discount_pct": "Settlement discount %",
    "sell_markup_multiplier": "Markup", "delivery_fee_per_m2": "Delivery fee (R/m²)",
    "glue_rate_per_m2": "Glue rate (R/m²)", "labour_rate_per_m2": "Labour rate (R/m²)",
    "default_own_staff": "Labour source",
    "tile_length_mm": "Plank length (mm)", "tile_width_mm": "Plank width (mm)",
    "tile_thickness_mm": "Plank thickness (mm)", "tiles_per_pack": "Planks per box",
    "sku": "Product code", "wear_layer_mm": "Wear layer (mm)", "discontinued": "Discontinued",
    "default_delivery_fee_per_m2": "Delivery fee default (R/m², for new products)",
    "price_zone_a": "Zone A price (calculated)", "price_zone_b": "Zone B price (calculated)", "price_zone_c": "Zone C price (calculated)",
    "book_price": "Book price", "mechanism": "Mechanism", "fabric_tier": "Fabric tier",
    "cost_ex_vat_per_lm": "Cost per lm (ex VAT)", "fixed_sell_price_per_lm": "Fixed sell price per lm",
    "markup_multiplier": "Markup",
    "default_trade_discount_pct": "Trade discount % (default for new products)",
    "pricing_zone": "Pricing zone",
}


def format_field_value(field: str, value) -> str:
    """Confirmed Aug 2026: currency/percentage formatting for the commit
    confirmation message and audit log display — e.g. "R42.61" not
    "42.61", "8.0%" not "0.08". Best-effort by field-name pattern, not a
    strict per-field type registry — this is display formatting only,
    the actual stored value (str(value), see the commit endpoint below)
    is never affected by this."""
    if value is None:
        return "(not set)"
    if isinstance(value, bool):
        # BUG FOUND AND FIXED (Aug 2026, Master Sheet System of Record —
        # caught before shipping the `discontinued` field, not a live
        # incident): this used to hardcode default_own_staff's own two
        # labels for EVERY bool field, which would have produced a
        # nonsensical "changed from Outside/subcontracted to Own staff"
        # message for a Discontinued flag change. Field-name-specific
        # now, generic True/False fallback for any future bool field
        # that isn't explicitly one of these two.
        if field == "discontinued":
            return "Discontinued" if value else "Active"
        if field == "default_own_staff":
            return "Own staff (salaried)" if value else "Outside/subcontracted"
        return "True" if value else "False"
    fl = field.lower()
    if isinstance(value, (int, float)):
        if "pct" in fl or "discount" in fl:
            return f"{value * 100:.1f}%"
        # CORRECTED Aug 2026 (real display inconsistency found, not a
        # data bug — confirmed against the live database that the
        # stored value itself was always correct): sell_markup_multiplier/
        # markup_multiplier are stored as a raw multiplier (1.3 = x1.3)
        # but shown as "% above cost" everywhere else in this app (the
        # Floor Job builder's own Markup % field) — matched here so the
        # commit confirmation message and audit log read the same way a
        # value was actually typed on the console. Deliberately an exact
        # field-name match, NOT a loose "multiplier" substring match —
        # over_tiles_multiplier/removed_tiles_multiplier are a genuinely
        # different kind of multiplier (a screed job-type rate factor,
        # e.g. "Over Tiles is x1.5 the Smooth rate") where "x1.5" is the
        # correct, already-established display, not a %-above-cost figure.
        if field in ("sell_markup_multiplier", "markup_multiplier"):
            return f"{(value - 1) * 100:.1f}%"
        if any(k in fl for k in ("price", "cost", "rate", "fee")):
            return f"R{value:.2f}"
        if "multiplier" in fl:
            return f"{value}x"
    return str(value)


def recompute_tiles_per_pack(product: FlooringProduct) -> Optional[float]:
    """Plank dimensions genuinely used (confirmed Aug 2026, Supplier
    Console brief — real mm format confirmed directly from Azura's own
    price sheet, e.g. "184.15 x 1219.2 x 2.0"): planks/box is now
    auto-derived from length x width x m2_per_pack whenever all three
    are present on the product, instead of being a separately-entered
    number that can silently drift out of sync with the real plank
    dimensions. Returns None (leave tiles_per_pack untouched) for
    products missing full dimension data — not every supplier's price
    list gives plank dimensions, so this degrades gracefully rather
    than blocking edits on products that don't have them."""
    if not (product.tile_length_mm and product.tile_width_mm and product.m2_per_pack):
        return None
    plank_area_m2 = (product.tile_length_mm / 1000) * (product.tile_width_mm / 1000)
    if plank_area_m2 <= 0:
        return None
    return round(product.m2_per_pack / plank_area_m2)


PRICE_PER_BOX_FIELD_MAP = {
    # (raw box-price field) -> (calculated per-m² field it drives)
    "price_per_box_ex_vat": "base_cost_ex_vat",
    "price_per_box_zone_a": "price_zone_a",
    "price_per_box_zone_b": "price_zone_b",
    "price_per_box_zone_c": "price_zone_c",
}
# Every field that can trigger a per-m² recalculation when it changes
# (the four box-price fields themselves, or m2_per_pack, the shared
# divisor for all four).
PRICE_RECALC_TRIGGER_FIELDS = set(PRICE_PER_BOX_FIELD_MAP.keys()) | {"m2_per_pack"}


def recompute_calculated_prices(entity: FlooringProduct) -> dict:
    """Supplier Console Field Sequence Redesign (confirmed Aug 2026 —
    root-cause fix for the Como Flooring pricing bug). base_cost_ex_vat
    and price_zone_a/b/c are no longer editable inputs — this is the
    ONE place they're ever derived, always as price_per_box_* /
    m2_per_pack, straight off whatever's currently set on `entity`
    (which may include changes just applied earlier in this same commit
    but not yet flushed). Returns {field: new_value} only for fields
    that actually have a computable box price; a product with no
    m2_per_pack yet, or no box price for a given zone, keeps whatever
    its per-m² field already held (never zeroed/nulled by this)."""
    result = {}
    if not entity.m2_per_pack or entity.m2_per_pack <= 0:
        return result
    for box_field, per_m2_field in PRICE_PER_BOX_FIELD_MAP.items():
        box_value = getattr(entity, box_field)
        if box_value is not None:
            result[per_m2_field] = round(box_value / entity.m2_per_pack, 4)
    return result


def compute_calculated_prices_from_fields(fields: dict) -> dict:
    """Same calculation as recompute_calculated_prices(), for a brand
    new product's plain `fields` dict (staged via new_entities) before
    it's ever constructed as a real FlooringProduct — needed because
    base_cost_ex_vat is NOT NULL, so this has to run and populate it
    BEFORE model(**fields) is called, not after."""
    result = {}
    m2_per_pack = fields.get("m2_per_pack")
    if not m2_per_pack or m2_per_pack <= 0:
        return result
    for box_field, per_m2_field in PRICE_PER_BOX_FIELD_MAP.items():
        box_value = fields.get(box_field)
        if box_value is not None:
            result[per_m2_field] = round(box_value / m2_per_pack, 4)
    return result


class CommitChange(BaseModel):
    entity_type: str   # "FlooringProduct" | "BlindsProduct" | "TrimProduct"
    entity_id: int
    field: str
    new_value: Any


class NewEntityImport(BaseModel):
    """A brand-new product row (confirmed Aug 2026, AI Price Sheet Import
    brief — onboarding a new supplier's range means genuinely NEW rows,
    not just corrections to existing ones). Same staging discipline as
    CommitChange: nothing is created until the whole batch commits."""
    entity_type: str
    supplier: str
    fields: dict


class StagedDeletion(BaseModel):
    """Confirmed Aug 2026 — closes a real gap: the Console had edit and
    create, but no delete, so a genuinely old/duplicate/test product had
    no audited way to be removed (the only prior delete path was the OLD
    Price Book screen's raw, unaudited delete, which is exactly why that
    screen was removed as a tile). Same staging discipline as everything
    else here: nothing is deleted until Commit Changes, and the whole
    commit is validated (see the reference check in the handler below)
    before ANY of it — changes, new_entities, deletions — is applied."""
    entity_type: str
    entity_id: int


class CommitRequest(BaseModel):
    changes: List[CommitChange] = []
    new_entities: List[NewEntityImport] = []
    deletions: List[StagedDeletion] = []


@app.post("/admin/supplier-console/commit")
def commit_supplier_console_changes(
    body: CommitRequest, role: str = Depends(require_owner),
    tenant_id: str = Depends(get_current_tenant), username: str = Depends(get_current_username),
):
    """Commit-and-acknowledge workflow (confirmed Aug 2026): applies every
    staged edit in ONE action — nothing hits the database until this is
    called (the console stages edits locally in the browser, see
    index.html). Writes one AuditLog row per genuinely-changed field
    (skips any staged "change" that's actually a no-op — same value
    re-submitted), and returns a plain-English confirmation line per
    change, e.g. "Aspen — GD Aspen Oak: Price per m² (ex VAT) changed
    from R42.61 to R190.00" — not just a generic "Saved."

    Owner-only via require_owner, which reads through get_current_role —
    so an Owner previewing as Sales/Admin is correctly blocked here too,
    same enforcement path as everywhere else in Owner Preview Mode.
    """
    with Session(engine) as session:
        # Deletions validated FIRST, before anything else in this commit
        # is touched (changes, new_entities, or other deletions) — a
        # deletion that turns out to be unsafe (a real quote still
        # references it) aborts the WHOLE commit, same "nothing partially
        # saved" guarantee everything else here already has. Checked as
        # a pure read here — nothing is deleted until the second pass
        # further down, after every deletion in this batch has passed.
        for d in body.deletions:
            if d.entity_type not in ENTITY_TYPE_MODELS:
                raise HTTPException(400, f"Unknown entity_type '{d.entity_type}'")
            model = ENTITY_TYPE_MODELS[d.entity_type]
            entity = get_or_404(session, model, d.entity_id, tenant_id, d.entity_type)
            categories = ENTITY_TYPE_LINE_CATEGORIES.get(d.entity_type, [])
            ref = session.exec(
                select(QuoteLineItem).where(
                    QuoteLineItem.product_id == d.entity_id,
                    QuoteLineItem.category.in_(categories),
                    QuoteLineItem.tenant_id == tenant_id,
                )
            ).first()
            if ref:
                label = f"{entity.supplier} — {entity.product_name}" if hasattr(entity, "product_name") else f"{d.entity_type} #{d.entity_id}"
                raise HTTPException(400, f"Can't delete {label} — it's used on quote #{ref.quote_id}. Remove it from that quote first if you really need to delete this product.")

        by_entity: dict = {}
        for c in body.changes:
            if c.entity_type not in ENTITY_TYPE_MODELS:
                raise HTTPException(400, f"Unknown entity_type '{c.entity_type}'")
            by_entity.setdefault((c.entity_type, c.entity_id), []).append(c)

        summary_lines = []
        for (entity_type, entity_id), changes in by_entity.items():
            model = ENTITY_TYPE_MODELS[entity_type]
            entity = get_or_404(session, model, entity_id, tenant_id, entity_type)
            if entity_type == "SupplierDefault":
                entity_label = f"{entity.supplier} (supplier default)"
            else:
                entity_label = f"{entity.supplier} — {entity.product_name}" if hasattr(entity, "product_name") else f"{entity.supplier} product #{entity_id}"
                if hasattr(entity, "colour") and entity.colour:
                    entity_label += f" ({entity.colour})"

            old_tiles_per_pack = getattr(entity, "tiles_per_pack", None) if entity_type == "FlooringProduct" else None
            touched_dimension_field = False
            touched_price_recalc_field = False

            for c in changes:
                if not hasattr(entity, c.field):
                    raise HTTPException(400, f"{entity_type} has no field '{c.field}'")
                # Field Sequence Redesign (confirmed Aug 2026): these four
                # are calculated, not editable — never accept a direct
                # edit to them, even from a stale/cached frontend or a
                # direct API call, precisely the failure mode that let a
                # box price silently land in base_cost_ex_vat in the
                # first place. Set price_per_box_ex_vat / m2_per_pack (or
                # the zone equivalents) instead; these recompute
                # automatically below.
                if entity_type == "FlooringProduct" and c.field in ("base_cost_ex_vat", "price_zone_a", "price_zone_b", "price_zone_c"):
                    raise HTTPException(400, f"'{FIELD_LABELS.get(c.field, c.field)}' is calculated from Price per box ÷ m² per box — it can't be edited directly. Change the price per box instead.")
                old_value = getattr(entity, c.field)
                if old_value == c.new_value:
                    continue   # staged but genuinely unchanged — no audit entry, no confirmation line
                setattr(entity, c.field, c.new_value)
                if c.field in ("tile_length_mm", "tile_width_mm", "m2_per_pack"):
                    touched_dimension_field = True
                if c.field in PRICE_RECALC_TRIGGER_FIELDS:
                    touched_price_recalc_field = True
                label = FIELD_LABELS.get(c.field, c.field)
                session.add(AuditLog(
                    tenant_id=tenant_id, username=username, entity_type=entity_type, entity_id=entity_id,
                    field=c.field, old_value=str(old_value), new_value=str(c.new_value),
                ))
                summary_lines.append(f"{entity_label}: {label} changed from {format_field_value(c.field, old_value)} to {format_field_value(c.field, c.new_value)}")

            # Recalculate base_cost_ex_vat / price_zone_a/b/c whenever a
            # box price or m2_per_pack just changed (confirmed Aug 2026,
            # Field Sequence Redesign) — the ONE place these ever get
            # set for an existing product from now on. Logged as its own
            # AuditLog row per field, same "auto-derived, still audited"
            # pattern already established for tiles_per_pack just below.
            if entity_type == "FlooringProduct" and touched_price_recalc_field:
                derived_prices = recompute_calculated_prices(entity)
                for per_m2_field, new_value in derived_prices.items():
                    old_value = getattr(entity, per_m2_field)
                    if old_value == new_value:
                        continue
                    setattr(entity, per_m2_field, new_value)
                    label = FIELD_LABELS.get(per_m2_field, per_m2_field)
                    session.add(AuditLog(
                        tenant_id=tenant_id, username=username, entity_type=entity_type, entity_id=entity_id,
                        field=per_m2_field, old_value=str(old_value), new_value=str(new_value),
                    ))
                    summary_lines.append(f"{entity_label}: {label} auto-calculated (price per box ÷ m² per box) from {format_field_value(per_m2_field, old_value)} to {format_field_value(per_m2_field, new_value)}")

            if entity_type == "FlooringProduct" and touched_dimension_field:
                new_tiles_per_pack = recompute_tiles_per_pack(entity)
                if new_tiles_per_pack is not None and new_tiles_per_pack != old_tiles_per_pack:
                    session.add(AuditLog(
                        tenant_id=tenant_id, username=username, entity_type=entity_type, entity_id=entity_id,
                        field="tiles_per_pack", old_value=str(old_tiles_per_pack), new_value=str(new_tiles_per_pack),
                    ))
                    summary_lines.append(f"{entity_label}: Planks per box (auto-derived from dimensions) changed from {format_field_value('tiles_per_pack', old_tiles_per_pack)} to {format_field_value('tiles_per_pack', new_tiles_per_pack)}")
                    entity.tiles_per_pack = new_tiles_per_pack

            if hasattr(entity, "last_updated"):
                entity.last_updated = datetime.utcnow()
            session.add(entity)

        # New products (confirmed Aug 2026, AI Price Sheet Import brief —
        # onboarding a new supplier's range, not just correcting existing
        # rows). Modeled as "every given field went from (new) to its
        # value" so it reuses the exact same audit-log/confirmation-
        # message shape as an edit — no separate code path to keep in
        # sync. session.flush() (not commit) makes the new row's id
        # available for the audit log entries below while keeping the
        # whole batch atomic with everything else in this request — if
        # anything later in this same commit fails, this insert rolls
        # back too, same "nothing saved until Commit succeeds" guarantee.
        for ne in body.new_entities:
            if ne.entity_type not in ENTITY_TYPE_MODELS:
                raise HTTPException(400, f"Unknown entity_type '{ne.entity_type}'")
            model = ENTITY_TYPE_MODELS[ne.entity_type]
            fields = dict(ne.fields)
            fields["tenant_id"] = tenant_id
            fields["supplier"] = ne.supplier
            # Field Sequence Redesign (confirmed Aug 2026 — root-cause
            # fix for the Como Flooring pricing bug): base_cost_ex_vat /
            # price_zone_a/b/c are always CALCULATED from
            # price_per_box_ex_vat / price_per_box_zone_a/b/c ÷
            # m2_per_pack, computed here and overwritten into `fields`
            # BEFORE construction — never trusted from whatever a
            # (possibly stale/cached) frontend staged directly for those
            # four fields. base_cost_ex_vat is NOT NULL, so this has to
            # run before model(**fields) below, not after.
            derived_prices = {}
            if ne.entity_type == "FlooringProduct":
                derived_prices = compute_calculated_prices_from_fields(fields)
                fields.update(derived_prices)
            # BUG FOUND AND FIXED (Aug 2026, caught in testing, not
            # something the brief asked for): SQLModel table models don't
            # enforce a missing required field (e.g. base_cost_ex_vat)
            # at construction time the way a plain Pydantic model would
            # — the real failure only surfaces as a raw NOT NULL
            # constraint violation at flush()/INSERT time, which the
            # original try/except here didn't cover (it only wrapped
            # model(**fields)). Left unguarded, this crashed with a bare
            # 500 instead of a clean 400 explaining what's missing —
            # exactly the kind of confusing error a reviewer correcting
            # an AI-extracted row with a gap (e.g. price never read)
            # would hit. Now wraps the flush too, and rolls back so the
            # session isn't left in an aborted state.
            try:
                entity = model(**fields)
                session.add(entity)
                session.flush()   # assigns entity.id without committing yet
            except Exception as e:
                session.rollback()
                raise HTTPException(400, f"Could not create new {ne.entity_type} for {ne.supplier} ('{fields.get('product_name', '?')}'): {e}")

            is_supplier_default = ne.entity_type == "SupplierDefault"
            entity_label = f"{ne.supplier} (supplier default)" if is_supplier_default else f"{ne.supplier} — {fields.get('product_name', '(new)')}"
            if fields.get("colour"):
                entity_label += f" ({fields['colour']})"
            new_item_kind = "new supplier default" if is_supplier_default else "new product"
            calculated_price_fields = ("base_cost_ex_vat", "price_zone_a", "price_zone_b", "price_zone_c")
            for field, value in ne.fields.items():
                if field in ("supplier",):
                    continue   # already reflected in entity_label, not a separately useful audit line
                if ne.entity_type == "FlooringProduct" and field in calculated_price_fields:
                    continue   # logged separately below as calculated, not as directly staged — see derived_prices
                label = FIELD_LABELS.get(field, field)
                session.add(AuditLog(
                    tenant_id=tenant_id, username=username, entity_type=ne.entity_type, entity_id=entity.id,
                    field=field, old_value="(new)", new_value=str(value),
                ))
                summary_lines.append(f"{entity_label}: {label} set to {format_field_value(field, value)} ({new_item_kind})")

            if ne.entity_type == "FlooringProduct":
                for per_m2_field, value in derived_prices.items():
                    label = FIELD_LABELS.get(per_m2_field, per_m2_field)
                    session.add(AuditLog(
                        tenant_id=tenant_id, username=username, entity_type=ne.entity_type, entity_id=entity.id,
                        field=per_m2_field, old_value="(new)", new_value=str(value),
                    ))
                    summary_lines.append(f"{entity_label}: {label} calculated (price per box ÷ m² per box) to {format_field_value(per_m2_field, value)} (new product)")
                derived = recompute_tiles_per_pack(entity)
                if derived is not None and derived != entity.tiles_per_pack:
                    session.add(AuditLog(
                        tenant_id=tenant_id, username=username, entity_type=ne.entity_type, entity_id=entity.id,
                        field="tiles_per_pack", old_value="(new)", new_value=str(derived),
                    ))
                    summary_lines.append(f"{entity_label}: Planks per box (auto-derived from dimensions) set to {format_field_value('tiles_per_pack', derived)} (new product)")
                    entity.tiles_per_pack = derived
            session.add(entity)

        # Deletions — actually performed here, second pass, only after
        # every one of them already passed the reference check above.
        # Logged as a single AuditLog row per deletion (field="__deleted__",
        # capturing the full label so the permanent record still says what
        # was removed even though the row itself is gone) — same
        # append-only, no-edit-no-delete Change Log every other action
        # here already writes to.
        for d in body.deletions:
            model = ENTITY_TYPE_MODELS[d.entity_type]
            entity = session.get(model, d.entity_id)
            entity_label = f"{entity.supplier} — {entity.product_name}" if hasattr(entity, "product_name") else f"{d.entity_type} #{d.entity_id}"
            if hasattr(entity, "colour") and entity.colour:
                entity_label += f" ({entity.colour})"
            session.add(AuditLog(
                tenant_id=tenant_id, username=username, entity_type=d.entity_type, entity_id=d.entity_id,
                field="__deleted__", old_value=entity_label, new_value="(deleted)",
            ))
            summary_lines.append(f"{entity_label}: deleted from the price book")
            session.delete(entity)

        session.commit()
        return {"changed_count": len(summary_lines), "summary": summary_lines}


@app.post("/admin/supplier-console/import")
async def import_price_sheet(
    supplier: str, file: UploadFile = File(...), instructions: str = "",
    role: str = Depends(require_owner), tenant_id: str = Depends(get_current_tenant),
):
    """AI-Assisted Price Sheet Import (confirmed Aug 2026 — banked brief,
    built ahead of its own stated precondition per explicit instruction).
    Sends the uploaded file to the Claude API (ai_import.py) and returns
    PROPOSED staging rows only — writes NOTHING to the price book. The
    frontend loads these into the console's existing staging area
    exactly as if typed in by hand; nothing is saved until the owner
    reviews and clicks the existing Commit Changes button, same
    commit-and-log path as any manual edit. Owner-only via require_owner
    (preview-aware, same enforcement path as everything else).

    instructions: optional free text, appended to the extraction prompt
    for this import only (e.g. "skip the clearance section") — blank
    behaves exactly as before this was added."""
    file_bytes = await file.read()
    media_type = file.content_type or "application/octet-stream"
    try:
        return extract_price_sheet(file_bytes, media_type, supplier, instructions)
    except RuntimeError as e:
        raise HTTPException(502, str(e))


@app.post("/admin/supplier-console/import-spreadsheet")
async def import_master_spreadsheet(
    supplier: str, file: UploadFile = File(...),
    role: str = Depends(require_owner), tenant_id: str = Depends(get_current_tenant),
):
    """Standard Import Format — deterministic spreadsheet import (confirmed
    Aug 2026, Standard Import Format brief). The new PRIMARY supplier
    import path, replacing direct AI-PDF extraction: no AI call, no
    inference, straight column-name-to-field mapping (spreadsheet_import.py)
    against a fixed, exact header format — either the whole file parses
    cleanly or the whole request is rejected with a clear, specific
    error, same "returns PROPOSED staging rows only, writes NOTHING"
    contract as the AI import above (nothing is saved until the owner
    reviews and clicks Commit Changes). 400, not 502, on a rejected file
    — this is a validation failure (bad input), not an upstream service
    failure like a Claude API error would be.

    The AI-PDF import endpoint above is NOT retired by this existing —
    kept in place per the brief's explicit instruction, just no longer
    the recommended path for ongoing supplier imports.

    Master Spreadsheet System of Record (confirmed Aug 2026): this
    endpoint always returns every parsed row as if new — it doesn't
    query the database at all. The actual re-import UPDATE CYCLE
    (matching rows against this supplier's current products, staging
    edits to only the GOVERNED fields for a match, flagging an
    unmatched existing product Discontinued) happens entirely in the
    frontend (stageSpreadsheetUpdateCycle(), index.html), which already
    has this supplier's current product list loaded for review anyway —
    no reason to duplicate that matching logic or add a second DB round
    trip here."""
    file_bytes = await file.read()
    try:
        rows = parse_master_spreadsheet(file_bytes)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"rows": rows}


@app.get("/admin/supplier-defaults", response_model=List[SupplierDefault])
def list_supplier_defaults(role: str = Depends(require_owner), tenant_id: str = Depends(get_current_tenant)):
    """Per-supplier defaults (confirmed Aug 2026 — see SupplierDefault's
    own docstring in models.py). One row per supplier that has ever had
    a default set; a supplier with no row yet just has no default (the
    Console shows an empty, stageable field for it either way, and
    staging a value for the first time creates the row via the existing
    new_entities path in commit_supplier_console_changes — no separate
    create endpoint needed)."""
    with Session(engine) as session:
        return session.exec(select(SupplierDefault).where(SupplierDefault.tenant_id == tenant_id)).all()


@app.get("/admin/audit-log")
def get_audit_log(
    entity_type: Optional[str] = None, entity_id: Optional[int] = None,
    start_date: Optional[str] = None, end_date: Optional[str] = None,
    role: str = Depends(require_owner), tenant_id: str = Depends(get_current_tenant),
):
    """Change Log / Audit Log (confirmed Aug 2026) — read-only, Owner-only
    (require_owner, same preview-aware enforcement as session-log above).
    Deliberately general-purpose (see AuditLog's own docstring in
    models.py) — entity_type/entity_id here happen to always be price-
    book products today, but nothing about this endpoint assumes that.
    No edit/delete endpoint exists for this table, ever — permanent
    record, same principle as the session log."""
    with Session(engine) as session:
        stmt = select(AuditLog).where(AuditLog.tenant_id == tenant_id)
        if entity_type:
            stmt = stmt.where(AuditLog.entity_type == entity_type)
        if entity_id:
            stmt = stmt.where(AuditLog.entity_id == entity_id)
        rows = session.exec(stmt).all()
        if start_date:
            rows = [r for r in rows if r.timestamp.date() >= date.fromisoformat(start_date)]
        if end_date:
            rows = [r for r in rows if r.timestamp.date() <= date.fromisoformat(end_date)]
        rows = sorted(rows, key=lambda r: r.timestamp, reverse=True)
        return [
            {
                "id": r.id, "timestamp": r.timestamp.isoformat(), "username": r.username,
                "entity_type": r.entity_type, "entity_id": r.entity_id,
                "field": r.field, "field_label": FIELD_LABELS.get(r.field, r.field),
                "old_value": r.old_value, "new_value": r.new_value,
                "old_value_formatted": format_field_value(r.field, _parse_audit_value(r.old_value)),
                "new_value_formatted": format_field_value(r.field, _parse_audit_value(r.new_value)),
            }
            for r in rows
        ]


def _parse_audit_value(raw: str):
    """AuditLog stores every value as a plain string (see AuditLog's own
    docstring) — this is a best-effort re-parse purely for display
    formatting (format_field_value needs a real number/bool to format
    currency/percentages correctly), never used for anything that
    affects a stored value or a calculation."""
    if raw in ("None", ""):
        return None
    if raw in ("True", "False"):
        return raw == "True"
    try:
        return float(raw)
    except ValueError:
        return raw


@app.get("/commission-rates")
def list_commission_rates(tenant_id: str = Depends(get_current_tenant)):
    with Session(engine) as session:
        return session.exec(select(CommissionRate).where(CommissionRate.active == True, CommissionRate.tenant_id == tenant_id)).all()


@app.post("/commission-rates")
def create_commission_rate(rate: CommissionRate, role: str = Depends(require_owner), tenant_id: str = Depends(get_current_tenant)):
    rate.tenant_id = tenant_id
    with Session(engine) as session:
        session.add(rate)
        session.commit()
        session.refresh(rate)
        return rate


@app.put("/commission-rates/{rate_id}")
def update_commission_rate(rate_id: int, updates: CommissionRate, role: str = Depends(require_owner), tenant_id: str = Depends(get_current_tenant)):
    with Session(engine) as session:
        rate = get_or_404(session, CommissionRate, rate_id, tenant_id, "Rate")
        data = updates.dict(exclude_unset=True, exclude={"id", "tenant_id"})
        for k, v in data.items():
            setattr(rate, k, v)
        session.add(rate)
        session.commit()
        session.refresh(rate)
        return rate


@app.delete("/commission-rates/{rate_id}")
def delete_commission_rate(rate_id: int, role: str = Depends(require_owner), tenant_id: str = Depends(get_current_tenant)):
    with Session(engine) as session:
        rate = get_or_404(session, CommissionRate, rate_id, tenant_id, "Rate")
        rate.active = False   # soft delete — keeps history for past statements intact
        session.add(rate)
        session.commit()
        return {"deactivated": rate_id}


# ---------- HR: Commission calculation ----------

@app.get("/commission/statement/{sales_owner_key}")
def commission_statement(sales_owner_key: str, year: int, month: int, tenant_id: str = Depends(get_current_tenant)):
    """
    Confirmed Aug 2026: commission is calculated ONLY on fully paid
    invoices (status == "paid"), for the given calendar month, per the
    brief's core rule #1. Two calculation paths depending on the
    employee's commission_role_type:

    - pure_sales: % of total GP generated that month, tiered (rate found
      by which GP band the MONTHLY TOTAL falls into — not per job).
    - builder_rep: % of ex-VAT price PER LINE ITEM, rate looked up by
      that line's category, summed across the month. Flat per-category
      rate, not tiered, since builder-reps already earn from installation
      labour and shouldn't also get a GP-scaling bonus on top of that.
    """
    with Session(engine) as session:
        employee = session.exec(
            select(Employee).where(Employee.sales_owner_key == sales_owner_key, Employee.tenant_id == tenant_id)
        ).first()
        if not employee:
            raise HTTPException(404, f"No employee found with sales_owner_key '{sales_owner_key}'")
        if not employee.commission_eligible:
            return {"employee": employee.full_name, "commission_eligible": False, "commission_due": 0.0}

        paid_quotes = session.exec(
            select(Quote).where(
                Quote.sales_owner == sales_owner_key,
                Quote.status == "paid",
                Quote.tenant_id == tenant_id,
            )
        ).all()
        paid_quotes = [q for q in paid_quotes if q.created_at.year == year and q.created_at.month == month]

        rates = session.exec(select(CommissionRate).where(CommissionRate.active == True, CommissionRate.tenant_id == tenant_id)).all()

        if employee.commission_role_type == "pure_sales":
            total_turnover = 0.0
            total_gp = 0.0
            for q in paid_quotes:
                lines = session.exec(select(QuoteLineItem).where(QuoteLineItem.quote_id == q.id, QuoteLineItem.tenant_id == tenant_id)).all()
                quote_turnover = sum(l.line_total for l in lines)
                quote_cost = sum(line_real_cost(l) for l in lines)
                total_turnover += quote_turnover
                total_gp += (quote_turnover - quote_cost)

            gp_tiers = sorted(
                [r for r in rates if r.role_type == "pure_sales" and r.basis == "gp"],
                key=lambda r: r.tier_min,
            )
            applicable_rate = None
            for tier in gp_tiers:
                if total_gp >= tier.tier_min and (tier.tier_max is None or total_gp <= tier.tier_max):
                    applicable_rate = tier
                    break
            rate_pct = applicable_rate.rate_pct if applicable_rate else 0.0
            commission_due = total_gp * rate_pct

            return {
                "employee": employee.full_name,
                "commission_role_type": "pure_sales",
                "period": f"{year}-{month:02d}",
                "jobs_count": len(paid_quotes),
                "turnover": round(total_turnover, 2),
                "gp": round(total_gp, 2),
                "rate_applied_pct": round(rate_pct, 4),
                "commission_due": round(commission_due, 2),
            }

        elif employee.commission_role_type == "builder_rep":
            category_rates = {r.category: r.rate_pct for r in rates if r.role_type == "builder_rep" and r.basis == "ex_vat_price"}
            total_turnover = 0.0
            total_commission = 0.0
            breakdown = {}
            for q in paid_quotes:
                lines = session.exec(select(QuoteLineItem).where(QuoteLineItem.quote_id == q.id, QuoteLineItem.tenant_id == tenant_id)).all()
                for l in lines:
                    total_turnover += l.line_total
                    rate = category_rates.get(l.category)
                    if rate is not None:
                        commission = l.line_total * rate
                        total_commission += commission
                        breakdown[l.category] = breakdown.get(l.category, 0.0) + commission
                    # categories with no configured rate contribute R0 commission — flagged, not silently guessed at

            missing_categories = sorted(set(
                l.category for q in paid_quotes
                for l in session.exec(select(QuoteLineItem).where(QuoteLineItem.quote_id == q.id, QuoteLineItem.tenant_id == tenant_id)).all()
                if l.category not in category_rates
            ))

            return {
                "employee": employee.full_name,
                "commission_role_type": "builder_rep",
                "period": f"{year}-{month:02d}",
                "jobs_count": len(paid_quotes),
                "turnover": round(total_turnover, 2),
                "commission_due": round(total_commission, 2),
                "breakdown_by_category": {k: round(v, 2) for k, v in breakdown.items()},
                "categories_with_no_rate_configured": missing_categories,
            }

        else:
            return {"employee": employee.full_name, "commission_eligible": False, "commission_due": 0.0, "note": "commission_role_type is 'other' — no calculation defined"}


# ---------- HR: Hours Worked ----------

@app.post("/hours-worked")
def log_hours(entry: HoursWorked, tenant_id: str = Depends(get_current_tenant)):
    coerce_date_fields(entry, "work_date")
    entry.tenant_id = tenant_id
    with Session(engine) as session:
        session.add(entry)
        session.commit()
        session.refresh(entry)
        return entry


@app.get("/hours-worked")
def list_hours(employee_id: Optional[int] = None, year: Optional[int] = None, month: Optional[int] = None, tenant_id: str = Depends(get_current_tenant)):
    with Session(engine) as session:
        stmt = select(HoursWorked).where(HoursWorked.tenant_id == tenant_id)
        if employee_id:
            stmt = stmt.where(HoursWorked.employee_id == employee_id)
        entries = session.exec(stmt).all()
        if year:
            entries = [e for e in entries if e.work_date.year == year]
        if month:
            entries = [e for e in entries if e.work_date.month == month]
        return entries


@app.delete("/hours-worked/{entry_id}")
def delete_hours(entry_id: int, tenant_id: str = Depends(get_current_tenant)):
    with Session(engine) as session:
        entry = get_or_404(session, HoursWorked, entry_id, tenant_id, "Entry")
        session.delete(entry)
        session.commit()
        return {"deleted": entry_id}


@app.get("/hours-worked/summary")
def hours_summary(year: int, month: int, employee_id: Optional[int] = None, tenant_id: str = Depends(get_current_tenant)):
    """Monthly summary, per the brief's "accountant-ready" requirement —
    totals by hour type, per employee (or one employee if filtered)."""
    with Session(engine) as session:
        employees = session.exec(select(Employee).where(Employee.tenant_id == tenant_id)).all()
        if employee_id:
            employees = [e for e in employees if e.id == employee_id]

        result = []
        for emp in employees:
            entries = session.exec(
                select(HoursWorked).where(HoursWorked.employee_id == emp.id, HoursWorked.tenant_id == tenant_id)
            ).all()
            entries = [e for e in entries if e.work_date.year == year and e.work_date.month == month]
            if not entries and employee_id is None:
                continue  # skip employees with zero hours this month in the consolidated view
            by_type = {}
            for e in entries:
                by_type[e.hour_type] = by_type.get(e.hour_type, 0.0) + e.hours
            result.append({
                "employee_id": emp.id,
                "employee_name": emp.full_name,
                "total_hours": round(sum(by_type.values()), 2),
                "by_type": {k: round(v, 2) for k, v in by_type.items()},
                "entries_count": len(entries),
            })
        return {"period": f"{year}-{month:02d}", "employees": result}


# ---------- HR: Documents ----------

UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)


def _tenant_upload_dir(tenant_id: str) -> str:
    """Part 3 finding (confirmed Aug 2026): every document used to land
    in one shared uploads/ folder regardless of tenant — fine with one
    tenant, a real filename-collision/cross-listing risk once a second
    one exists. Existing files for tenant '1' are untouched in place
    (they're already correctly namespaced by their random uuid4 prefix,
    so nothing needs moving) — this only changes where NEW uploads land."""
    d = os.path.join(UPLOAD_DIR, tenant_id)
    os.makedirs(d, exist_ok=True)
    return d


@app.post("/documents/upload")
def upload_document(employee_id: int, document_type: str = "other", owner_only: bool = False,
                     notes: str = "", file: UploadFile = File(...), tenant_id: str = Depends(get_current_tenant)):
    # SECURITY FIX: file.filename is client-supplied and untrusted — a
    # crafted value like "../../../etc/passwd" would otherwise let a
    # direct API call write outside UPLOAD_DIR. basename() strips any
    # directory components before it's used in the storage path.
    original_name = os.path.basename(file.filename or "upload")
    safe_name = f"{uuid.uuid4().hex}_{original_name}"
    dest_path = os.path.join(_tenant_upload_dir(tenant_id), safe_name)
    with open(dest_path, "wb") as f:
        shutil.copyfileobj(file.file, f)
    with Session(engine) as session:
        doc = Document(
            employee_id=employee_id, document_type=document_type, filename=original_name,
            file_path=safe_name, owner_only=owner_only, notes=notes, tenant_id=tenant_id,
        )
        session.add(doc)
        session.commit()
        session.refresh(doc)
        return doc


@app.get("/documents")
def list_documents(employee_id: Optional[int] = None, role: str = Depends(get_current_role), tenant_id: str = Depends(get_current_tenant)):
    with Session(engine) as session:
        stmt = select(Document).where(Document.tenant_id == tenant_id)
        if employee_id:
            stmt = stmt.where(Document.employee_id == employee_id)
        docs = session.exec(stmt).all()
        if role == UserRole.sales:
            docs = [d for d in docs if not d.owner_only]
        return docs


@app.get("/documents/{doc_id}/download")
def download_document(doc_id: int, role: str = Depends(get_current_role), tenant_id: str = Depends(get_current_tenant)):
    with Session(engine) as session:
        doc = get_or_404(session, Document, doc_id, tenant_id, "Document")
        # SECURITY FIX: list_documents already hides owner_only docs from
        # Sales, but this endpoint had no role check at all — anyone who
        # knew/guessed a doc_id could download an owner_only document
        # directly, bypassing the list-view restriction entirely.
        # Default changed from UserRole.owner to UserRole.sales (confirmed
        # Aug 2026) — the original default failed open: any future caller
        # that forgot to pass role got the MOST privileged access,
        # silently. This is exactly how the frontend's own download link
        # went unnoticed without ?role= for as long as it did (regressed
        # sometime after v44, confirmed via index.html.pre-v44-merge,
        # which did pass it correctly). Failing toward the LEAST
        # privileged role means a future caller that forgets the param
        # breaks loudly (a wrongly-denied Owner will report it) instead
        # of leaking quietly (a wrongly-allowed Sales user won't).
        if doc.owner_only and role == UserRole.sales:
            raise HTTPException(403, "This document is restricted to Owner/Admin")
        # Existing (pre-tenant-groundwork) files live directly in
        # UPLOAD_DIR; new ones land under UPLOAD_DIR/{tenant_id}/ — check
        # both so nothing already on disk breaks.
        full_path = os.path.join(_tenant_upload_dir(tenant_id), doc.file_path)
        if not os.path.exists(full_path):
            full_path = os.path.join(UPLOAD_DIR, doc.file_path)
        if not os.path.exists(full_path):
            raise HTTPException(404, "File missing on disk")
        return FileResponse(full_path, filename=doc.filename)


@app.delete("/documents/{doc_id}")
def delete_document(doc_id: int, tenant_id: str = Depends(get_current_tenant)):
    with Session(engine) as session:
        doc = get_or_404(session, Document, doc_id, tenant_id, "Document")
        full_path = os.path.join(_tenant_upload_dir(tenant_id), doc.file_path)
        if not os.path.exists(full_path):
            full_path = os.path.join(UPLOAD_DIR, doc.file_path)
        if os.path.exists(full_path):
            os.remove(full_path)
        session.delete(doc)
        session.commit()
        return {"deleted": doc_id}


# ---------- HR: Leave ----------

@app.post("/leave-balances")
def create_leave_balance(balance: LeaveBalance, tenant_id: str = Depends(get_current_tenant)):
    coerce_date_fields(balance, "cycle_start_date")
    balance.tenant_id = tenant_id
    with Session(engine) as session:
        session.add(balance)
        session.commit()
        session.refresh(balance)
        return balance


@app.get("/leave-balances")
def list_leave_balances(employee_id: Optional[int] = None, tenant_id: str = Depends(get_current_tenant)):
    with Session(engine) as session:
        stmt = select(LeaveBalance).where(LeaveBalance.tenant_id == tenant_id)
        if employee_id:
            stmt = stmt.where(LeaveBalance.employee_id == employee_id)
        balances = session.exec(stmt).all()
        result = []
        for b in balances:
            d = b.dict()
            d["days_remaining"] = round(b.days_entitled + b.days_carried_over - b.days_taken, 2)
            result.append(d)
        return result


@app.post("/leave-requests")
def submit_leave_request(request: LeaveRequest, tenant_id: str = Depends(get_current_tenant)):
    """Confirmed Aug 2026: submitting a request does NOT touch the
    balance — only approval does. This matches the brief's explicit
    workflow (request -> approve -> balance updates)."""
    coerce_date_fields(request, "start_date", "end_date")
    with Session(engine) as session:
        request.status = "pending"
        request.tenant_id = tenant_id
        session.add(request)
        session.commit()
        session.refresh(request)
        return request


@app.get("/leave-requests")
def list_leave_requests(employee_id: Optional[int] = None, status: Optional[str] = None, tenant_id: str = Depends(get_current_tenant)):
    with Session(engine) as session:
        stmt = select(LeaveRequest).where(LeaveRequest.tenant_id == tenant_id)
        if employee_id:
            stmt = stmt.where(LeaveRequest.employee_id == employee_id)
        if status:
            stmt = stmt.where(LeaveRequest.status == status)
        return session.exec(stmt).all()


@app.put("/leave-requests/{request_id}/approve")
def approve_leave_request(request_id: int, reviewed_by: str, tenant_id: str = Depends(get_current_tenant)):
    with Session(engine) as session:
        req = get_or_404(session, LeaveRequest, request_id, tenant_id, "Request")
        if req.status != "pending":
            raise HTTPException(400, f"Request is already {req.status}, cannot approve again")

        balance = session.exec(
            select(LeaveBalance).where(
                LeaveBalance.employee_id == req.employee_id,
                LeaveBalance.leave_type == req.leave_type,
                LeaveBalance.tenant_id == tenant_id,
            )
        ).first()
        if not balance:
            raise HTTPException(400, f"No {req.leave_type} leave balance found for this employee — create one first")

        remaining = balance.days_entitled + balance.days_carried_over - balance.days_taken
        if req.days_requested > remaining:
            raise HTTPException(400, f"Only {remaining} days remaining, request is for {req.days_requested} days")

        balance.days_taken += req.days_requested
        req.status = "approved"
        req.reviewed_by = reviewed_by
        req.reviewed_at = datetime.utcnow()
        session.add(balance)
        session.add(req)
        session.commit()
        session.refresh(req)
        return req


@app.put("/leave-requests/{request_id}/reject")
def reject_leave_request(request_id: int, reviewed_by: str, tenant_id: str = Depends(get_current_tenant)):
    with Session(engine) as session:
        req = get_or_404(session, LeaveRequest, request_id, tenant_id, "Request")
        if req.status != "pending":
            raise HTTPException(400, f"Request is already {req.status}")
        req.status = "rejected"
        req.reviewed_by = reviewed_by
        req.reviewed_at = datetime.utcnow()
        session.add(req)
        session.commit()
        session.refresh(req)
        return req


# ---------- Clients ----------

@app.get("/clients")
def list_clients(search: str = None, tenant_id: str = Depends(get_current_tenant)):
    """search filters by name (case-insensitive, contains) — used by the
    Order Index / New Quote client picker."""
    with Session(engine) as session:
        stmt = select(Client).where(Client.tenant_id == tenant_id)
        clients = session.exec(stmt).all()
        if search:
            search_lower = search.lower()
            clients = [c for c in clients if search_lower in c.name.lower()]
        return clients


@app.get("/clients/{client_id}")
def get_client(client_id: int, tenant_id: str = Depends(get_current_tenant)):
    with Session(engine) as session:
        return get_or_404(session, Client, client_id, tenant_id, "Client")


@app.post("/clients")
def create_client(client: Client, tenant_id: str = Depends(get_current_tenant)):
    client.tenant_id = tenant_id
    with Session(engine) as session:
        session.add(client)
        session.commit()
        session.refresh(client)
        return client


@app.put("/clients/{client_id}")
def update_client(client_id: int, updates: Client, tenant_id: str = Depends(get_current_tenant)):
    with Session(engine) as session:
        client = get_or_404(session, Client, client_id, tenant_id, "Client")
        data = updates.dict(exclude_unset=True, exclude={"id", "tenant_id"})
        for k, v in data.items():
            setattr(client, k, v)
        session.add(client)
        session.commit()
        session.refresh(client)
        return client


@app.delete("/clients/{client_id}")
def delete_client(client_id: int, tenant_id: str = Depends(get_current_tenant)):
    with Session(engine) as session:
        client = get_or_404(session, Client, client_id, tenant_id, "Client")
        session.delete(client)
        session.commit()
        return {"deleted": client_id}


@app.get("/clients/{client_id}/quotes")
def get_client_quotes(client_id: int, tenant_id: str = Depends(get_current_tenant)):
    """Order history for a client — every quote ever linked to this
    record, most recent first."""
    with Session(engine) as session:
        client = get_or_404(session, Client, client_id, tenant_id, "Client")
        quotes = session.exec(
            select(Quote).where(Quote.client_id == client_id, Quote.tenant_id == tenant_id).order_by(Quote.created_at.desc())
        ).all()
        return {"client": client, "quotes": quotes}


# ---------- Business Settings ----------

def get_settings(session: Session, tenant_id: str = DEFAULT_TENANT_ID) -> BusinessSettings:
    """Confirmed Aug 2026 — the single source of truth for business-wide
    values (VAT %, default deposit %, screed bag overage rate, default
    labour rate, Order Index overdue threshold, plus the original
    letterhead details). Auto-creates the default row for this tenant on
    first call if none exists, same as before, so this never errors on a
    fresh database. Call this instead of hardcoding a business-wide value
    directly — that exact duplication (VAT_PCT hardcoded identically in
    two endpoints, fixed at v54) is the bug this table was expanded to
    close.

    Multi-tenant groundwork (confirmed Aug 2026): was a hardcoded
    session.get(BusinessSettings, 1) singleton lookup — now looks up by
    tenant_id instead of assuming id=1, so a second tenant automatically
    gets their own settings row on first call, no code change needed.
    tenant_id defaults to DEFAULT_TENANT_ID only so any code that hasn't
    been updated to pass it explicitly still behaves exactly as before
    for the one real tenant that exists today."""
    settings = session.exec(select(BusinessSettings).where(BusinessSettings.tenant_id == tenant_id)).first()
    if not settings:
        settings = BusinessSettings(tenant_id=tenant_id)
        session.add(settings)
        session.commit()
        session.refresh(settings)
    return settings


def _effective_pricing_zone(session: Session, tenant_id: str, supplier: str, settings: BusinessSettings) -> str:
    """Per-supplier zone override (confirmed Aug 2026 — Zone pricing is
    now settable independently per zone-priced supplier, e.g. Azura on
    Zone A while Como Flooring is on Zone B, replacing the one single
    business-wide default that used to apply to every zone-priced
    supplier alike). Looks up SupplierDefault.pricing_zone for this
    supplier; falls back to BusinessSettings.pricing_zone only if this
    supplier has never had its own zone set — in practice this only
    matters for a brand-new zone-priced supplier before its first
    explicit zone choice, since the startup backfill (on_startup())
    already seeded a per-supplier value for every supplier that had zone
    pricing at the time this shipped."""
    row = session.exec(
        select(SupplierDefault).where(SupplierDefault.tenant_id == tenant_id, SupplierDefault.supplier == supplier)
    ).first()
    if row and row.pricing_zone:
        return row.pricing_zone
    return settings.pricing_zone


def resolve_zone_price(session: Session, tenant_id: str, product: FlooringProduct, settings: BusinessSettings) -> FlooringProduct:
    """Zone pricing (confirmed Aug 2026, Supplier Console brief — real
    rule from Azura's own "Suggested Retail Price List", not guessed:
    every Azura/deZIGN product has three real prices side by side, one
    per zone; Como Flooring confirmed to use the same structure). A
    product with zone prices stored (price_zone_a/b/c) uses whichever
    zone this specific SUPPLIER is currently set to (see
    _effective_pricing_zone — per-supplier, not business-wide, as of
    Aug 2026) as its effective base_cost_ex_vat, instead of its own
    plain base_cost_ex_vat — no manual per-quote zone selection, ever,
    per the brief.

    Returns a DETACHED copy (model_copy — not session-tracked), never
    the real ORM object with a field mutated in place: mutating a
    tracked SQLAlchemy attribute risks it being silently flushed back to
    the database on some later autoflush, which would corrupt the real
    stored per-zone prices. This is deliberately calculations.py-free —
    calculate_flooring_line()/calculate_stairwell_line() only ever read
    base_cost_ex_vat off whatever product object they're handed, so
    resolving zone pricing here, before calling them, means their
    formulas never needed to change at all.

    Products without zone pricing (price_zone_a/b/c all None — every
    non-zone-priced supplier) pass through completely unchanged."""
    zone = _effective_pricing_zone(session, tenant_id, product.supplier, settings)
    zone_price = getattr(product, f"price_zone_{zone.lower()}", None)
    if zone_price is None:
        return product
    return product.model_copy(update={"base_cost_ex_vat": zone_price})


@app.get("/business-settings")
def get_business_settings(tenant_id: str = Depends(get_current_tenant)):
    with Session(engine) as session:
        return get_settings(session, tenant_id)


@app.put("/business-settings")
def update_business_settings(updates: BusinessSettings, role: str = Depends(get_current_role), tenant_id: str = Depends(get_current_tenant)):
    # Confirmed Aug 2026: business settings (including the VAT %, deposit
    # %, and rate defaults added in v54, same as the original letterhead
    # details) are Owner-only to change — Admin/Sales can still view them
    # (GET is unrestricted, they're used across the whole app regardless
    # of who's logged in), but only Burgert can edit. Same enforcement
    # style as strip_sensitive_fields: checked server-side, not just
    # hidden in the UI. (The zip lineage's own v54 dropped this check
    # entirely — not adopted; real security fix, same pattern as every
    # other role check in this file.)
    if role != UserRole.owner:
        raise HTTPException(403, "Only the Owner role can change business settings")
    with Session(engine) as session:
        settings = get_settings(session, tenant_id)
        data = updates.dict(exclude_unset=True, exclude={"id", "tenant_id"})
        for k, v in data.items():
            setattr(settings, k, v)
        session.add(settings)
        session.commit()
        session.refresh(settings)
        return settings


# ---------- Quotes ----------

@app.post("/quotes")
def create_quote(client_name: str, sales_owner: str, branch: str = "gansbaai",
                  blinds_measurements_visible: bool = True,
                  discount_pct: float = 0.0, deposit_pct: float = 0.70,
                  client_id: int = None, tenant_id: str = Depends(get_current_tenant)):
    """If client_id is given, client_name AND site_address are taken
    from that real Client record (overriding whatever was passed) —
    confirmed Aug 2026, previously only client_name did this, leaving a
    real gap where a known client's saved address never carried through
    to their new quote. Without client_id, this is a walk-in/one-off
    quote using just the typed name, no CRM entry required."""
    with Session(engine) as session:
        site_address = ""
        if client_id:
            client = get_or_404(session, Client, client_id, tenant_id, "Client")
            client_name = client.name
            site_address = client.address
        quote = Quote(
            client_name=client_name,
            client_id=client_id,
            sales_owner=sales_owner,
            branch=branch,
            blinds_measurements_visible=blinds_measurements_visible,
            discount_pct=discount_pct,
            deposit_pct=deposit_pct,
            site_address=site_address,
            tenant_id=tenant_id,
        )
        session.add(quote)
        session.commit()
        session.refresh(quote)
        return quote


@app.put("/quotes/{quote_id}/discount")
def update_quote_discount(quote_id: int, discount_pct: float, tenant_id: str = Depends(get_current_tenant)):
    """Set/change the quote-level discount after the fact — you often won't
    know the discount until the quote's already built up with line items."""
    with Session(engine) as session:
        quote = get_or_404(session, Quote, quote_id, tenant_id, "Quote")
        quote.discount_pct = discount_pct
        session.add(quote)
        session.commit()
        session.refresh(quote)
        return quote


@app.put("/quotes/{quote_id}/transport-levy")
def update_quote_transport_levy(quote_id: int, transport_levy: float, tenant_id: str = Depends(get_current_tenant)):
    """Transport Levy (confirmed Aug 2026, Courier Toggle brief Section 6)
    — manual, per-job, opt-in amount, same "set/change after the fact"
    pattern as update_quote_discount above. Explicitly a DIFFERENT thing
    from the per-product delivery fee (FlooringProduct.delivery_fee_per_m2)
    — this is a single ad-hoc amount typed in for a specific job, not
    tied to any product or supplier."""
    with Session(engine) as session:
        quote = get_or_404(session, Quote, quote_id, tenant_id, "Quote")
        quote.transport_levy = transport_levy
        session.add(quote)
        session.commit()
        session.refresh(quote)
        return quote


@app.put("/quotes/{quote_id}")
def update_quote_details(quote_id: int, client_name: str = None, sales_owner: str = None,
                          branch: str = None, status: str = None,
                          site_address: str = None, installation_date: str = None,
                          invoice_sent_date: str = None, deposit_paid_date: str = None,
                          deposit_payment_method: str = None, final_payment_date: str = None,
                          final_payment_method: str = None, tenant_id: str = Depends(get_current_tenant)):
    """Update a quote's own details — client name, sales owner, branch,
    status, plus order-tracking fields (site address, installation date,
    invoice/payment dates and methods) confirmed Aug 2026 for the Order
    Index. Used by the "Save Quote" button. Line items are already saved
    individually as they're added — this covers quote-level fields only.

    Date params are accepted as strings and explicitly coerced — same
    fix as v38's coerce_date_fields(), applied here from the start this
    time rather than being rediscovered as a bug later."""
    with Session(engine) as session:
        quote = get_or_404(session, Quote, quote_id, tenant_id, "Quote")
        if client_name is not None:
            quote.client_name = client_name
        if sales_owner is not None:
            quote.sales_owner = sales_owner
        if branch is not None:
            quote.branch = branch
        if status is not None:
            quote.status = status
        if site_address is not None:
            quote.site_address = site_address
        if installation_date is not None:
            quote.installation_date = date.fromisoformat(installation_date) if installation_date else None
        if invoice_sent_date is not None:
            quote.invoice_sent_date = date.fromisoformat(invoice_sent_date) if invoice_sent_date else None
        if deposit_paid_date is not None:
            quote.deposit_paid_date = date.fromisoformat(deposit_paid_date) if deposit_paid_date else None
        if deposit_payment_method is not None:
            quote.deposit_payment_method = deposit_payment_method
        if final_payment_date is not None:
            quote.final_payment_date = date.fromisoformat(final_payment_date) if final_payment_date else None
        if final_payment_method is not None:
            quote.final_payment_method = final_payment_method
        session.add(quote)
        session.commit()
        session.refresh(quote)
        return quote


@app.post("/quotes/{quote_id}/follow-ups")
def log_follow_up(quote_id: int, follow_up_date: str, notes: str = "", tenant_id: str = Depends(get_current_tenant)):
    with Session(engine) as session:
        get_or_404(session, Quote, quote_id, tenant_id, "Quote")
        entry = PaymentFollowUp(quote_id=quote_id, follow_up_date=date.fromisoformat(follow_up_date), notes=notes, tenant_id=tenant_id)
        session.add(entry)
        session.commit()
        session.refresh(entry)
        return entry


@app.get("/quotes/{quote_id}/follow-ups")
def list_follow_ups(quote_id: int, tenant_id: str = Depends(get_current_tenant)):
    with Session(engine) as session:
        return session.exec(
            select(PaymentFollowUp).where(PaymentFollowUp.quote_id == quote_id, PaymentFollowUp.tenant_id == tenant_id).order_by(PaymentFollowUp.follow_up_date)
        ).all()


@app.delete("/quotes/{quote_id}")
def delete_quote(quote_id: int, tenant_id: str = Depends(get_current_tenant)):
    """Deletes a quote and everything hanging off it. SQLite doesn't
    enforce the cascade from supabase_schema.sql, so it's all removed
    explicitly here.

    BUG FOUND AND FIXED Aug 2026 (while testing the v49 follow-up log
    merge): PaymentFollowUp and ColourChangeLog rows were never included
    in this cascade — deleting a quote orphaned them, and since SQLite
    reuses a deleted row's rowid for the next insert, a brand new
    unrelated quote could resurface a prior quote's "deleted" follow-up
    history under its own id. Caught by a real test cycle, not
    inspection."""
    with Session(engine) as session:
        quote = get_or_404(session, Quote, quote_id, tenant_id, "Quote")
        lines = session.exec(
            select(QuoteLineItem).where(QuoteLineItem.quote_id == quote_id, QuoteLineItem.tenant_id == tenant_id)
        ).all()
        for line in lines:
            colour_logs = session.exec(
                select(ColourChangeLog).where(ColourChangeLog.quote_line_item_id == line.id, ColourChangeLog.tenant_id == tenant_id)
            ).all()
            for log in colour_logs:
                session.delete(log)
            session.delete(line)
        follow_ups = session.exec(
            select(PaymentFollowUp).where(PaymentFollowUp.quote_id == quote_id, PaymentFollowUp.tenant_id == tenant_id)
        ).all()
        for f in follow_ups:
            session.delete(f)
        session.delete(quote)
        session.commit()
        return {"deleted": quote_id}


@app.post("/quotes/{quote_id}/lines/flooring")
def add_flooring_line(quote_id: int, product_id: int, quantity_m2: float,
                       job_type: JobType, discount_pct: float = 0.0,
                       glue_cost_per_unit: float = 0.0, glue_coverage_m2: float = 0.0,
                       labour_rate_per_m2: float = 45.0,
                       bag_cost: float = 235.0, bag_coverage_m2: float = None,
                       own_staff: bool = True, markup_override: float = None,
                       include_tile_removal_fee: bool = False,
                       role: str = Depends(get_current_role), tenant_id: str = Depends(get_current_tenant)):
    """
    Material lines: glue_cost_per_unit / glue_coverage_m2 (e.g. Techem Tek
    70/70 = 1193.50 / 70), labour_rate_per_m2 — your fixed per-m² labour cost.
    own_staff: True (default) = your own salaried guys, labour cost treated
    as R0 (charge = pure margin). False = outside/subcontracted, labour
    cost treated as pass-through (roughly what you actually pay out).
    Screed lines: bag_cost (default R235, iTe LEVELiTe F10 20kg), bag_coverage_m2
    (default varies by job_type — Smooth 4, Over Tiles 3, Removed Tiles 2 —
    pass to override). include_tile_removal_fee: explicit toggle for the
    confirmed R45/m² incl VAT tile removal fee — not auto-tied to job_type.
    """
    with Session(engine) as session:
        quote = get_or_404(session, Quote, quote_id, tenant_id, "Quote")
        product = get_or_404(session, FlooringProduct, product_id, tenant_id, "Flooring product")
        settings = get_settings(session, tenant_id)

        calc = calculate_flooring_line(
            resolve_zone_price(session, tenant_id, product, settings), quantity_m2, job_type, discount_pct,
            glue_cost_per_unit, glue_coverage_m2, labour_rate_per_m2,
            bag_cost, bag_coverage_m2, own_staff, markup_override,
            include_tile_removal_fee,
            margin_warn_threshold=settings.flooring_margin_warn_threshold,
            tile_removal_fee_per_m2_incl_vat=settings.tile_removal_fee_per_m2_incl_vat,
            vat_pct=settings.vat_pct,
        )
        line = QuoteLineItem(
            quote_id=quote_id, category="flooring", product_id=product_id, tenant_id=tenant_id,
            product_name=product.product_name, colour=product.colour, original_colour=product.colour,
            job_type=job_type,
            quantity_m2=quantity_m2, discount_pct=discount_pct,
            unit_cost=calc["unit_cost"], unit_price=calc["unit_price"],
            line_total=calc["line_total"], margin_pct=calc["margin_pct"],
            glue_cost_total=calc["glue_cost_total"],
            glue_sell_total=calc["glue_sell_total"],
            glue_units_needed=calc["glue_units_needed"],
            labour_cost_total=calc["labour_cost_total"],
            labour_charged_total=calc["labour_charged_total"],
            own_staff=calc["own_staff"],
            bags_allowed=calc["bags_allowed"],
            compound_cost_total=calc["compound_cost_total"],
            tile_removal_fee_total=calc["tile_removal_fee_total"],
            delivery_fee_total=calc["delivery_fee_total"],
            total_job_cost=calc["total_job_cost"],
        )
        session.add(line)
        session.commit()
        session.refresh(line)

        result = strip_sensitive_fields(line.dict(), role)
        # Warning text itself contains the margin % — only show it to
        # roles that are allowed to see margin at all, or it defeats the
        # point of stripping margin_pct as a field above.
        if calc["warning"] and role != UserRole.sales:
            result["warning"] = calc["warning"]
        if "packs_needed" in calc:
            result["packs_needed"] = calc["packs_needed"]
        if "glue_units_needed" in calc:
            result["glue_units_needed"] = calc["glue_units_needed"]
            result["glue_sell_total"] = calc["glue_sell_total"]
        return result


@app.post("/quotes/{quote_id}/lines/blinds")
def add_blinds_line(quote_id: int, product_id: int, width_mm: float, drop_mm: float,
                     discount_pct: float = 0.0, role: str = Depends(get_current_role), tenant_id: str = Depends(get_current_tenant)):
    with Session(engine) as session:
        get_or_404(session, Quote, quote_id, tenant_id, "Quote")
        product = get_or_404(session, BlindsProduct, product_id, tenant_id, "Blinds product")

        calc = calculate_blinds_line(product, width_mm, drop_mm, discount_pct)
        line = QuoteLineItem(
            quote_id=quote_id, category="blinds", product_id=product_id, tenant_id=tenant_id,
            product_name=product.product_name, width_mm=width_mm, drop_mm=drop_mm,
            discount_pct=discount_pct,
            unit_cost=calc["unit_cost"], unit_price=calc["unit_price"],
            line_total=calc["line_total"], margin_pct=calc["margin_pct"],
        )
        session.add(line)
        session.commit()
        session.refresh(line)

        return strip_sensitive_fields(line.dict(), role)


@app.post("/quotes/{quote_id}/lines/trims")
def add_trim_line(quote_id: int, product_id: int, length_m: float,
                   discount_pct: float = 0.0, role: str = Depends(get_current_role), tenant_id: str = Depends(get_current_tenant)):
    with Session(engine) as session:
        get_or_404(session, Quote, quote_id, tenant_id, "Quote")
        product = get_or_404(session, TrimProduct, product_id, tenant_id, "Trim product")
        settings = get_settings(session, tenant_id)

        calc = calculate_trim_line(product, length_m, discount_pct, margin_warn_threshold=settings.flooring_margin_warn_threshold)
        line = QuoteLineItem(
            quote_id=quote_id, category="trim", product_id=product_id, tenant_id=tenant_id,
            product_name=product.product_name, length_m=length_m,
            discount_pct=discount_pct,
            unit_cost=calc["unit_cost"], unit_price=calc["unit_price"],
            line_total=calc["line_total"], margin_pct=calc["margin_pct"],
        )
        session.add(line)
        session.commit()
        session.refresh(line)

        result = strip_sensitive_fields(line.dict(), role)
        if calc["warning"] and role != UserRole.sales:
            result["warning"] = calc["warning"]
        return result


@app.post("/quotes/{quote_id}/lines/stairwell")
def add_stairwell_line(quote_id: int, vinyl_product_id: int, nosing_product_id: int,
                        num_stairs: int, stairwell_type: StairwellType,
                        stair_area_m2: float = 0.45, own_staff: bool = True,
                        landing_area_m2: float = 0.0,
                        role: str = Depends(get_current_role), tenant_id: str = Depends(get_current_tenant)):
    """
    stair_area_m2 defaults to 0.45 (confirmed: 900mm wide tread x (300mm
    going + 200mm riser) = 0.9 x 0.5). Override if your stairs differ.
    own_staff: True (default) = your own salaried guys, labour cost treated
    as R0 (charge = pure margin). False = outside/subcontracted, labour
    cost treated as pass-through (roughly what you actually pay out).

    landing_area_m2 (confirmed Aug 2026 — CHANGED from a separate line):
    staircases with a turn/half-landing can have a landing platform,
    summed from however many landing rows the frontend collected. This
    used to be posted as its own separate "flooring" quote line at the
    standard per-m² rate; it's now folded into THIS SAME stairwell line's
    totals instead, so the client/quote shows one combined stair price,
    not two. The rate/formula is completely unchanged — still priced via
    calculate_flooring_line(), the exact same function a normal flooring
    line uses, same vinyl product, no markup override, no stair-tread
    tile/glue logic applied to it. Only how the result is combined and
    displayed changed, not the calculation itself — landing_calc below
    is computed identically to before, just added into the stairwell
    line's fields instead of becoming its own QuoteLineItem row.
    """
    with Session(engine) as session:
        get_or_404(session, Quote, quote_id, tenant_id, "Quote")
        vinyl_product = get_or_404(session, FlooringProduct, vinyl_product_id, tenant_id, "Vinyl product")
        nosing_product = get_or_404(session, TrimProduct, nosing_product_id, tenant_id, "Nosing product")
        if not vinyl_product.tiles_per_pack:
            raise HTTPException(400, "Selected vinyl product has no tiles_per_pack set — required for stairwell vinyl billing")
        settings = get_settings(session, tenant_id)

        stairwell_labour_by_type = {
            StairwellType.closed: settings.stairwell_labour_closed,
            StairwellType.one_side_open: settings.stairwell_labour_one_side_open,
            StairwellType.both_sides_open: settings.stairwell_labour_both_sides_open,
        }
        effective_vinyl = resolve_zone_price(session, tenant_id, vinyl_product, settings)   # per-supplier zone pricing — see resolve_zone_price(); no-op for non-zone-priced suppliers
        calc = calculate_stairwell_line(
            effective_vinyl, nosing_product, num_stairs, stairwell_type, stair_area_m2=stair_area_m2, own_staff=own_staff,
            glue_cost_per_unit=settings.stairwell_default_glue_cost_per_unit,
            glue_coverage_m2=settings.stairwell_default_glue_coverage_m2,
            labour_per_stair=stairwell_labour_by_type[stairwell_type],
            margin_warn_threshold=settings.flooring_margin_warn_threshold,
        )

        landing_calc = None
        if landing_area_m2 > 0:
            landing_calc = calculate_flooring_line(
                effective_vinyl, landing_area_m2, JobType.smooth, own_staff=own_staff,
                margin_warn_threshold=settings.flooring_margin_warn_threshold,
                tile_removal_fee_per_m2_incl_vat=settings.tile_removal_fee_per_m2_incl_vat,
                vat_pct=settings.vat_pct,
            )

        combined_line_total = calc["line_total"] + (landing_calc["line_total"] if landing_calc else 0.0)
        combined_total_job_cost = calc["total_job_cost"] + (landing_calc["total_job_cost"] if landing_calc else 0.0)
        combined_margin_pct = (combined_line_total - combined_total_job_cost) / combined_line_total if combined_line_total else 0.0
        combined_labour_charged = calc["labour_charged_total"] + (landing_calc["labour_charged_total"] if landing_calc else 0.0)
        combined_labour_cost = calc["labour_cost_total"] + (landing_calc["labour_cost_total"] if landing_calc else 0.0)
        combined_warning = None
        if combined_margin_pct < settings.flooring_margin_warn_threshold:
            combined_warning = f"Overall margin on this stairwell line (incl. landing) is {combined_margin_pct:.1%}, below the {settings.flooring_margin_warn_threshold:.0%} warning threshold."

        line = QuoteLineItem(
            quote_id=quote_id, category="stairwell", tenant_id=tenant_id,
            product_id=vinyl_product_id,
            product_name=f"{vinyl_product.product_name} + {nosing_product.product_name} (stairwell)",
            unit_cost=0, unit_price=0,
            line_total=combined_line_total, margin_pct=combined_margin_pct,
            total_job_cost=combined_total_job_cost,
            num_stairs=num_stairs, stairwell_type=stairwell_type,
            nosing_length_m=calc["nosing_length_m"], boxes_needed=calc["boxes_needed"],
            billed_vinyl_area_m2=calc["billed_vinyl_area_m2"],
            glue_area_m2=calc["glue_area_m2"],
            vinyl_sell_total=calc["vinyl_sell_total"],
            vinyl_cost_total=calc["vinyl_cost_total"],
            nosing_cost_total=calc["nosing_cost_total"],
            nosing_sell_total=calc["nosing_sell_total"],
            glue_cost_total=calc["glue_cost_total"],
            glue_sell_total=calc["glue_sell_total"],
            glue_units_needed=calc["glue_units_needed"],
            labour_cost_total=combined_labour_cost,
            labour_charged_total=combined_labour_charged,
            own_staff=calc["own_staff"],
            landing_area_m2=landing_area_m2 if landing_calc else None,
            landing_sell_total=landing_calc["line_total"] if landing_calc else None,
        )
        session.add(line)
        session.commit()
        session.refresh(line)

        result = strip_sensitive_fields(line.dict(), role)
        if combined_warning and role != UserRole.sales:
            result["warning"] = combined_warning
        return result


@app.post("/quotes/{quote_id}/lines/misc")
def add_misc_line(quote_id: int, description: str, amount_ex_vat: float, cost_ex_vat: float = 0.0,
                   role: str = Depends(get_current_role), tenant_id: str = Depends(get_current_tenant)):
    """Confirmed Aug 2026 — freeform line for anything that doesn't fit
    an existing category: extra Saturday/Sunday labour, a one-off
    special request, anything not covered by a real product record.
    cost_ex_vat is optional (defaults to 0, i.e. pure margin) — useful
    for things like weekend labour where there's genuinely no
    additional cost beyond what's already being paid in salary."""
    with Session(engine) as session:
        get_or_404(session, Quote, quote_id, tenant_id, "Quote")
        margin_pct = (amount_ex_vat - cost_ex_vat) / amount_ex_vat if amount_ex_vat else 0.0
        line = QuoteLineItem(
            quote_id=quote_id, category="misc", product_id=0, tenant_id=tenant_id,
            product_name=description,
            unit_cost=cost_ex_vat, unit_price=amount_ex_vat,
            line_total=amount_ex_vat, margin_pct=margin_pct,
        )
        session.add(line)
        session.commit()
        session.refresh(line)
        return strip_sensitive_fields(line.dict(), role)


@app.get("/quotes/{quote_id}")
def get_quote(quote_id: int, role: str = Depends(get_current_role), tenant_id: str = Depends(get_current_tenant)):
    with Session(engine) as session:
        quote = get_or_404(session, Quote, quote_id, tenant_id, "Quote")
        lines = session.exec(
            select(QuoteLineItem).where(QuoteLineItem.quote_id == quote_id, QuoteLineItem.tenant_id == tenant_id)
        ).all()
        lines_out = [strip_sensitive_fields(l.dict(), role) for l in lines]
        # Courier/delivery fee — client-facing confirmation, NOT the
        # amount (confirmed Aug 2026, Courier Toggle brief: "delivery
        # stays folded into the total... no separate cost shown to the
        # client, but add a plain note... 'Delivery included' — no
        # amount, just the confirmation"). has_delivery_fee is a plain
        # boolean, computed from the ORIGINAL (pre-strip) line objects —
        # deliberately added AFTER strip_sensitive_fields, on every
        # role including sales, since a yes/no fact reveals nothing
        # about the actual cost/margin the real delivery_fee_total
        # figure would. This is what the print view (shared.js) reads
        # to decide whether to show the note, instead of needing the
        # real (correctly role-stripped) figure.
        for l, l_out in zip(lines, lines_out):
            l_out["has_delivery_fee"] = bool(l.delivery_fee_total)
        # Transport Levy (confirmed Aug 2026, Courier Toggle brief
        # Section 6) — a manual, opt-in, job-level amount, added into
        # subtotal_ex_vat alongside the real line items so it gets
        # exactly the same discount/VAT treatment as every other line on
        # the quote ("added to the total the same way other line items
        # are"). 0.0 on every quote unless Burgert explicitly sets one —
        # completely inert until then.
        subtotal_ex_vat = sum(l["line_total"] for l in lines_out) + quote.transport_levy

        # Client-facing measurement toggle: strip width/drop from blinds
        # lines if the quote's toggle is off. Internal DB record is untouched.
        if not quote.blinds_measurements_visible:
            for l in lines_out:
                if l["category"] == "blinds":
                    l.pop("width_mm", None)
                    l.pop("drop_mm", None)

        # Quote-level discount (confirmed Aug 2026): applied to the whole
        # ex-VAT subtotal, before VAT — not per line. VAT_PCT now comes
        # from Business Settings (v54) — real bug found and fixed while
        # merging: this was hardcoded identically here AND in list_quotes
        # below, so a VAT change (or a typo landing in only one spot)
        # could have made this endpoint and the Order Index quietly
        # disagree on totals. Both now read from the same settings row.
        VAT_PCT = get_settings(session, tenant_id).vat_pct
        discount_amount = subtotal_ex_vat * quote.discount_pct
        total_ex_vat = subtotal_ex_vat - discount_amount
        total_incl_vat = total_ex_vat * (1 + VAT_PCT)
        deposit_amount = total_incl_vat * quote.deposit_pct
        balance_amount = total_incl_vat - deposit_amount

        response = {
            "quote": quote.dict(),
            "lines": lines_out,
            "subtotal_ex_vat": round(subtotal_ex_vat, 2),
            "discount_amount": round(discount_amount, 2),
            "total_ex_vat": round(total_ex_vat, 2),
            "total_incl_vat": round(total_incl_vat, 2),
            "deposit_amount": round(deposit_amount, 2),
            "balance_amount": round(balance_amount, 2),
        }

        # "At a glance" job margin check (owner/admin only — never shown to
        # Sales) — total sell vs. total real cost (material + glue + labour
        # for flooring, cost for blinds) across the whole quote, so a
        # mistake anywhere in the job shows up immediately, not line by line.
        # Margin uses the POST-discount total, since that's the real revenue.
        if role != UserRole.sales:
            total_cost = sum(line_real_cost(l) for l in lines)
            overall_margin = (total_ex_vat - total_cost) / total_ex_vat if total_ex_vat else 0.0
            response["overall_cost_ex_vat"] = round(total_cost, 2)
            response["overall_margin_pct"] = round(overall_margin, 4)

        return response


@app.delete("/quotes/{quote_id}/lines/{line_id}")
def delete_quote_line(quote_id: int, line_id: int, tenant_id: str = Depends(get_current_tenant)):
    with Session(engine) as session:
        line = session.get(QuoteLineItem, line_id)
        if not line or line.quote_id != quote_id or line.tenant_id != tenant_id:
            raise HTTPException(404, "Quote line not found")
        session.delete(line)
        session.commit()
        return {"deleted": line_id}


@app.put("/quotes/{quote_id}/lines/{line_id}/colour")
def change_line_colour(quote_id: int, line_id: int, new_colour: str, reason: str = "", changed_by: str = "", tenant_id: str = Depends(get_current_tenant)):
    """Confirmed Aug 2026 — a colour quoted might go out of stock and
    need substituting. This changes the ACTIVE colour on the line (what
    shows on the quote, what gets ordered), while logging the change so
    the full history is never lost. original_colour on the line itself
    is never touched here — it's set once, at creation, permanently."""
    with Session(engine) as session:
        line = session.get(QuoteLineItem, line_id)
        if not line or line.quote_id != quote_id or line.tenant_id != tenant_id:
            raise HTTPException(404, "Quote line not found")

        # BUG FIXED Aug 2026: this used to reject setting a colour when the
        # line had none yet — but "forgot to set a colour, add it now" is
        # exactly as legitimate a use case as "swap an existing colour for
        # another." Logged as old_colour="(none)" for a readable history,
        # rather than blocking the action entirely.
        log_entry = ColourChangeLog(
            quote_line_item_id=line_id, old_colour=line.colour or "(none)", new_colour=new_colour,
            reason=reason, changed_by=changed_by, tenant_id=tenant_id,
        )
        session.add(log_entry)

        line.colour = new_colour
        session.add(line)
        session.commit()
        session.refresh(line)
        return line


@app.get("/quotes/{quote_id}/lines/{line_id}/colour-history")
def get_colour_history(quote_id: int, line_id: int, tenant_id: str = Depends(get_current_tenant)):
    """Internal/operational view — never shown on the client-facing
    printed quote, which only ever shows the current colour."""
    with Session(engine) as session:
        line = session.get(QuoteLineItem, line_id)
        if not line or line.quote_id != quote_id or line.tenant_id != tenant_id:
            raise HTTPException(404, "Quote line not found")
        history = session.exec(
            select(ColourChangeLog)
            .where(ColourChangeLog.quote_line_item_id == line_id, ColourChangeLog.tenant_id == tenant_id)
            .order_by(ColourChangeLog.changed_at)
        ).all()
        return {
            "original_colour": line.original_colour,
            "current_colour": line.colour,
            "changes": history,
        }


@app.get("/quotes")
def list_quotes(sales_owner: Optional[str] = None, branch: Optional[str] = None,
                 status: Optional[str] = None, search: Optional[str] = None,
                 tenant_id: str = Depends(get_current_tenant)):
    """Confirmed Aug 2026 — Order Index needs totals (deposit amount,
    balance amount) visible without clicking into each quote, so this
    now computes them per quote, same VAT_PCT/discount logic as the
    single-quote endpoint (kept identical deliberately — a second,
    slightly different copy of this math is exactly how earlier bugs in
    this project happened). VAT_PCT now comes from Business Settings —
    real bug found and fixed while merging v54: this was hardcoded
    identically here AND in get_quote above, so a VAT change (or a typo
    landing in only one spot) could have made the two quietly disagree."""
    with Session(engine) as session:
        VAT_PCT = get_settings(session, tenant_id).vat_pct
        stmt = select(Quote).where(Quote.tenant_id == tenant_id)
        if sales_owner:
            stmt = stmt.where(Quote.sales_owner == sales_owner)
        if branch:
            stmt = stmt.where(Quote.branch == branch)
        if status:
            stmt = stmt.where(Quote.status == status)
        quotes = session.exec(stmt).all()
        if search:
            search_lower = search.lower()
            quotes = [q for q in quotes if search_lower in q.client_name.lower() or search_lower in str(q.id)]

        result = []
        for q in quotes:
            lines = session.exec(select(QuoteLineItem).where(QuoteLineItem.quote_id == q.id, QuoteLineItem.tenant_id == tenant_id)).all()
            # Transport Levy included here too (confirmed Aug 2026) — same
            # reasoning as get_quote() above; keeps the Order Index's own
            # totals consistent with the quote detail screen's, same "one
            # settings source, never two places that could quietly disagree"
            # discipline VAT_PCT already follows in this function.
            subtotal_ex_vat = sum(l.line_total for l in lines) + q.transport_levy
            discount_amount = subtotal_ex_vat * q.discount_pct
            total_ex_vat = subtotal_ex_vat - discount_amount
            total_incl_vat = total_ex_vat * (1 + VAT_PCT)
            deposit_amount = total_incl_vat * q.deposit_pct
            balance_amount = total_incl_vat - deposit_amount
            d = q.dict()
            d["total_incl_vat"] = round(total_incl_vat, 2)
            d["deposit_amount"] = round(deposit_amount, 2)
            d["balance_amount"] = round(balance_amount, 2)
            result.append(d)
        return result
