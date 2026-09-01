"""
Blinds & Flooring Studio bolt-on backend.
Price Book + Quote Builder, plus everything built on top of that
foundation since: HR/commissions, the Builder Referral Portal, Manual
Override, Order Sheets, the Dropbox document archive, AI-assisted and
deterministic spreadsheet price sheet import. (Dead Code Audit,
confirmed Aug 2026 — the original "No Xero, no PDF import, no AI
assistant yet" note above was stale; all three exist now.)

Run: uvicorn main:app --reload --port 8000
"""
from datetime import datetime, date, timedelta
from typing import List, Optional, Any
import json
import math
import os
import re
import secrets
import shutil
import uuid

from fastapi import FastAPI, HTTPException, UploadFile, File, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel
from sqlmodel import SQLModel, Session, create_engine, select
from sqlalchemy import inspect, text

from models import (
    FlooringProduct, BlindsProduct, TrimProduct, Quote, QuoteLineItem, Client,
    BusinessSettings, Employee, CommissionRate, CommissionPayment,
    HoursWorked, Document, LeaveBalance, LeaveRequest, ColourChangeLog, PaymentFollowUp,
    JobType, UserRole, StairwellType, User, UserSession, DEFAULT_TENANT_ID, AuditLog,
    SupplierDefault, FloorPrepProduct, Builder, BuilderEstimate, QuotePhoto,
    OrderSheet, OrderSheetLine, PasswordResetToken, DocumentArchive, DatabaseBackupRecord,
    FlaggedRecord, Lead,
)
from calculations import calculate_flooring_line, calculate_blinds_line, calculate_trim_line, calculate_stairwell_line, calculate_carpet_line, line_real_cost
from auth import hash_password, verify_password, new_session_token, new_expiry
from ai_import import extract_price_sheet
from spreadsheet_import import parse_master_spreadsheet
from pdf_render import render_html_to_pdf
import dropbox_archive
import database_backup
import photo_storage

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

# Captured once, at process import time — i.e. the moment THIS deploy
# actually started running, not a manually-maintained number anyone has
# to remember to bump. Backs the header version badge (index.html) below.
_APP_STARTED_AT = datetime.utcnow()


@app.get("/version")
def get_version():
    """Version badge fix (confirmed Aug 2026) — the badge had shown a
    hardcoded "v65" since this repo's very first commit (f7dc67f,
    2026-08-19), left over from the pre-repo CHANGELOG.md numbering
    convention ("every version handed to Claude Code gets an entry")
    that stopped once BUILD_LOG.md took over as the record of change —
    nobody ported the bump-it-every-time discipline into the live UI,
    so it silently went stale for 130+ commits / 9 days. Deliberately
    NOT a manually-maintained counter again (that's exactly how it went
    stale the first time) — derived from two things that are always
    real and can't drift: RENDER_GIT_COMMIT, an env var Render sets
    automatically on every deploy (None outside Render, e.g. local dev
    — handled explicitly by the frontend, not guessed at), and
    _APP_STARTED_AT above, captured fresh every time this process
    actually starts (i.e. every real deploy/restart).

    Correction (checked, not assumed): this has no per-endpoint
    Depends(), but that does NOT make it unauthenticated — the closed-
    by-default require_auth middleware below still covers it, same as
    every endpoint not explicitly in PUBLIC_PATHS. Confirmed directly:
    an anonymous request to this path returns 401. That's the right
    behaviour, not a bug — the only caller is showApp() (index.html),
    which only ever runs after a real login, so the badge still loads
    correctly for the one place it's actually shown; deliberately left
    out of PUBLIC_PATHS rather than widened for no real benefit."""
    commit = os.environ.get("RENDER_GIT_COMMIT")
    return {"commit": commit[:7] if commit else None, "started_at": _APP_STARTED_AT.isoformat()}


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
PUBLIC_PATHS = {
    "/auth/login", "/auth/logout", "/auth/me", "/", "/docs", "/openapi.json", "/redoc",
    # Password Reset Link brief (confirmed Aug 2026) — the whole point
    # is a staff member sets their own new password without ever being
    # logged in first, so this pair must be reachable with no session.
    # Neither one trusts anything beyond the token itself (a high-
    # entropy random string, checked against PasswordResetToken —
    # see reset_password() below) — there is no broader auth bypass
    # here, just these two specific, narrowly-scoped actions.
    "/auth/reset-password", "/auth/reset-password/validate",
}


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
        # Extra Rooms / Floor Prep collapsible cards (confirmed Aug 2026)
        # — NULL default: every existing misc line is a genuine ordinary
        # misc line, not a floor-prep entry, so NULL ("not this feature")
        # is correct for all of them, not just a safe placeholder.
        ("quotelineitem", "source_feature", "VARCHAR", "NULL"),
        # Builder Referral Portal, Phase 1 pilot (confirmed Aug 2026) —
        # FALSE default: no existing product is retroactively exposed to
        # the public portal just because this column now exists.
        ("flooringproduct", "available_to_builder_portal", "BOOLEAN", "FALSE"),
        # Quote Description field (confirmed Aug 2026, Duplicate Quote +
        # Quote Description brief) — blank for every existing quote;
        # nothing retroactively guessed or backfilled.
        ("quote", "description", "VARCHAR", "''"),
        # Job Workflow (confirmed Aug 2026, Order Index / Job Workflow
        # Redesign brief + Next Action Addendum) — every existing row
        # gets workflow_status='quoted' from this ALTER TABLE default,
        # then the real backfill (_backfill_job_workflow(), called once
        # below in on_startup(), after this function) derives the
        # correct value for each row from the legacy `status` field and
        # the order-tracking dates that already exist. NULL for every
        # date/optional field — nothing guessed at this stage.
        ("quote", "workflow_status", "VARCHAR", "'quoted'"),
        ("quote", "job_number", "VARCHAR", "NULL"),
        ("quote", "accepted_at", "TIMESTAMP", "NULL"),
        ("quote", "declined_at", "TIMESTAMP", "NULL"),
        ("quote", "installation_confirmed_date", "DATE", "NULL"),
        ("quote", "completion_date", "DATE", "NULL"),
        ("quote", "installer_team", "VARCHAR", "''"),
        ("quote", "materials_ordered", "BOOLEAN", "FALSE"),
        ("quote", "ready_for_installation", "BOOLEAN", "FALSE"),
        # Manual Override, Owner-only (confirmed Aug 2026, Manual Override
        # brief — urgent real use case, see models.py's own comment on
        # these fields for the full reasoning):
        ("quote", "manual_override_total_incl_vat", "FLOAT", "NULL"),
        ("quote", "override_total_reason", "VARCHAR", "NULL"),
        ("quote", "override_total_by", "VARCHAR", "NULL"),
        ("quote", "override_total_at", "TIMESTAMP", "NULL"),
        ("quotelineitem", "pre_override_line_total", "FLOAT", "NULL"),
        ("quotelineitem", "override_reason", "VARCHAR", "NULL"),
        ("quotelineitem", "override_by", "VARCHAR", "NULL"),
        ("quotelineitem", "override_at", "TIMESTAMP", "NULL"),
        # Fixed Display Order + Revert to Original (confirmed Aug 2026,
        # Add-Line Data-Loss brief §4/§5):
        ("quotelineitem", "flooring_pricing_type", "VARCHAR", "NULL"),
        ("quotelineitem", "trim_sub_category", "VARCHAR", "NULL"),
        ("quote", "snapshot_json", "TEXT", "NULL"),
        # Deposit Amount (confirmed Aug 2026, Deposit Amount + Save
        # Confirmation + Default Branch brief):
        ("quote", "actual_deposit_amount", "FLOAT", "NULL"),
        ("quote", "actual_deposit_amount_by", "VARCHAR", "NULL"),
        ("quote", "actual_deposit_amount_at", "TIMESTAMP", "NULL"),
        # Old Password Still Works incident (confirmed Aug 2026):
        ("app_user", "password_changed_at", "TIMESTAMP", "NULL"),
        # Supplier Order Sheets brief (confirmed Aug 2026):
        ("quotelineitem", "boxes_needed", "INTEGER", "NULL"),
        # Single Active Session per User brief (confirmed Aug 2026):
        ("usersession", "ended_reason", "VARCHAR", "NULL"),
        # New Quote Screen: Clarify Buttons + Price Check + Marketing
        # Source brief (confirmed Aug 2026):
        ("quote", "is_price_check", "BOOLEAN", "FALSE"),
        ("client", "marketing_source", "VARCHAR", "''"),
        # Client Info: Company Name, VAT Number, Multiple Phones/Emails
        # brief (confirmed Aug 2026):
        ("client", "company_name", "VARCHAR", "''"),
        ("client", "vat_number", "VARCHAR", "''"),
        ("client", "phone_extra", "TEXT", "''"),
        ("client", "email_extra", "TEXT", "''"),
        # Order Sheets UX: Duplicate Bug + Delete Option + Prominent
        # Placement + Real Preview brief (confirmed Aug 2026):
        ("ordersheet", "status", "VARCHAR", "'draft'"),
        ("ordersheet", "placed_at", "TIMESTAMP", "NULL"),
        ("ordersheet", "placed_by", "VARCHAR", "NULL"),
        # Order Sheet Corrections brief (confirmed Aug 2026):
        ("ordersheetline", "pre_discount_unit_cost", "FLOAT", "NULL"),
        ("ordersheetline", "discount_pct", "FLOAT", "NULL"),
        # Dropbox Document Archive brief, ACCEPTED-version follow-up
        # (confirmed Aug 2026) — real bug found while building the v2
        # pass (Dead Code Audit-adjacent, caught the hard way: a live
        # 500 on production, not a local test): is_accepted_version was
        # added to the DocumentArchive model in that follow-up round but
        # this migration entry was never added alongside it. The table
        # itself was created (SQLModel.metadata.create_all()) BEFORE
        # that column existed on the model, so every DocumentArchive
        # query against the live Supabase Postgres table has been
        # failing with UndefinedColumn ever since — silently, since
        # nothing in this session had exercised any DocumentArchive
        # SELECT against production again until today's Invoice/Order
        # Sheet archiving work did. Confirmed root cause via a real
        # production traceback before writing this fix, not guessed.
        ("documentarchive", "is_accepted_version", "BOOLEAN", "FALSE"),
        # Send button brief (confirmed Aug 2026) — added to the model AND
        # here together this time, having just found out the hard way
        # (directly above) what happens when they drift apart.
        ("supplierdefault", "email", "VARCHAR", "''"),
        # Trusted Tester Accounts brief (confirmed Aug 2026) — added to
        # the model AND here together, same discipline learned from the
        # documentarchive.is_accepted_version incident earlier today.
        ("client", "created_by", "VARCHAR", "''"),
        # Job Workflow Design Proposal, Phase 1 (confirmed Aug 2026).
        ("quote", "on_hold_reason", "VARCHAR", "NULL"),
        ("quote", "on_hold_at", "TIMESTAMP", "NULL"),
        # Lead: Visit Date, Address Fields & Printable Day List (confirmed
        # Aug 2026) — real usage feedback from Burgert actually using the
        # Lead feature the same day it shipped, not guessed at upfront.
        # `lead` itself was a brand-new table earlier today, created by
        # create_all() with no entry needed here — but it's now a live
        # table in production, so these two ADDITIONAL columns need the
        # same migration-list treatment as any other already-existing
        # table (added to the model AND here together, same discipline
        # learned from the documentarchive.is_accepted_version incident).
        ("lead", "visit_date", "DATE", "NULL"),
        ("lead", "site_address", "VARCHAR", "''"),
        # Job Card Content Spec (confirmed Aug 2026) — added to the model
        # AND here together, same discipline learned from the
        # documentarchive.is_accepted_version incident.
        ("quote", "installation_notes", "VARCHAR", "''"),
        # Carpet Calculators (confirmed Aug 2026, Carpet Calculators Final
        # Build Brief) — added to the model AND here together, same
        # discipline as every migration above.
        ("flooringproduct", "roll_width_m", "FLOAT", "NULL"),
        ("quotelineitem", "carpet_category", "VARCHAR", "NULL"),
        ("quotelineitem", "quantity_lm", "FLOAT", "NULL"),
        ("quotelineitem", "gripper_perimeter_m", "FLOAT", "NULL"),
        ("quotelineitem", "gripper_cost_total", "FLOAT", "NULL"),
        ("quotelineitem", "underfelt_area_m2", "FLOAT", "NULL"),
        ("quotelineitem", "underfelt_cost_total", "FLOAT", "NULL"),
        ("quotelineitem", "cutting_fee_total", "FLOAT", "NULL"),
        ("businesssettings", "carpet_cutting_fee", "FLOAT", "350.0"),
        ("businesssettings", "carpet_cushion_vinyl_cutting_fee", "FLOAT", "0.0"),
        ("businesssettings", "carpet_gripper_cost_per_box", "FLOAT", "600.0"),
        ("businesssettings", "carpet_gripper_lm_per_box", "FLOAT", "122.0"),
        ("businesssettings", "carpet_underfelt_cost_per_m2", "FLOAT", "42.0"),
        ("businesssettings", "carpet_underfelt_roll_width_m", "FLOAT", "4.0"),
        ("businesssettings", "carpet_glue_cost_per_20l_drum", "FLOAT", "1232.00"),
        # Margin Becomes Owner-Only; Price Gets a Colour Signal (confirmed
        # Aug 2026):
        ("businesssettings", "margin_band_bright_threshold", "FLOAT", "0.40"),
        ("quotelineitem", "low_margin_reason", "VARCHAR", "NULL"),
        ("quotelineitem", "low_margin_reason_by", "VARCHAR", "NULL"),
        ("quotelineitem", "low_margin_reason_at", "TIMESTAMP", "NULL"),
        ("quote", "low_margin_reason", "VARCHAR", "NULL"),
        ("quote", "low_margin_reason_by", "VARCHAR", "NULL"),
        ("quote", "low_margin_reason_at", "TIMESTAMP", "NULL"),
        # Bulk Import Full Belgotex Carpet Range, PENDING (confirmed Aug 2026):
        ("flooringproduct", "pending_review", "BOOLEAN", "FALSE"),
        # "Colour" Field Showing Products Instead of Real Colours (confirmed Aug 2026):
        ("flooringproduct", "product_variant", "VARCHAR", "NULL"),
        # Independent Status Tiles, Decision Q2 (confirmed Aug 2026):
        ("quote", "materials_not_needed", "BOOLEAN", "FALSE"),
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


def _next_job_number(session: Session, tenant_id: str) -> str:
    """Job Workflow (confirmed Aug 2026) — sequential J-0001 format
    (confirmed directly), tenant-wide, never reused. Shared by the
    startup backfill and accept_quote() so there's exactly one place
    that decides the next number — re-scans existing job numbers each
    call rather than keeping a separate counter row, which is simplest
    and correct at this business's real scale (dozens of jobs, not
    thousands) and can never drift from what's actually in the table."""
    existing = session.exec(select(Quote.job_number).where(Quote.job_number.is_not(None), Quote.tenant_id == tenant_id)).all()
    next_seq = 1
    for jn in existing:
        try:
            next_seq = max(next_seq, int(jn.split("-")[-1]) + 1)
        except (ValueError, AttributeError):
            pass
    return f"J-{next_seq:04d}"


def _enable_row_level_security():
    """URGENT SECURITY FIX (confirmed Aug 2026 — "Supabase Security
    Advisor Flags Publicly Accessible Table" brief, treated as highest
    priority ahead of everything else in progress). Row-Level Security
    was not enabled on ANY table in this database. Supabase auto-
    generates a public REST API (PostgREST) for every table in a
    project, completely independent of anything this FastAPI backend
    itself enforces — without RLS, that auto-generated API lets anyone
    with the project URL read, edit, and delete every row of every
    table with ZERO authentication. This is exactly what the alert
    means by "table publicly accessible" and "sensitive data publicly
    accessible" — app_user (password_hash for every staff login) and
    usersession (live session tokens — arguably the more urgent of the
    two: a valid token read straight from this table lets someone
    impersonate a logged-in user without even needing a password) are
    the two most sensitive tables affected.

    This backend's own connection to Postgres (DATABASE_URL) uses a
    privileged role that bypasses RLS entirely — Postgres's own RLS
    semantics exempt the table owner and any BYPASSRLS role, always,
    unconditionally, regardless of policies — which is the default and
    near-universal setup for Supabase's direct-Postgres connection
    string (as opposed to the PostgREST/anon-key path browsers use).
    So enabling RLS here, even with zero explicit policies, closes the
    public exposure completely without this app losing access to
    anything it already reads/writes.

    STATED PLAINLY, not silently assumed: there is no Supabase
    dashboard or database-role access available to independently
    CONFIRM this connection truly has BYPASSRLS before this deploys —
    that requires the dashboard, which only Burgert can check (brief's
    own Section 1). If that assumption is wrong, the app would start
    failing to read/write visibly, immediately after this goes live.
    Each table is handled independently, wrapped so one failure can't
    block the rest or crash startup — same reasoning as the Clear
    Unlinked Quotes remediation just above. Idempotent: re-enabling RLS
    on a table that already has it is a harmless no-op in Postgres, so
    this is safe to leave running on every future startup permanently.

    IMPORTANT — table list is discovered live, not hardcoded: an
    earlier version of this function hardcoded the 24 tables backing
    this app's own SQLModel classes (confirmed exhaustively against
    models.py). Burgert then confirmed directly from the Supabase
    Security Advisor that 25 tables are flagged, not 24 — meaning one
    real table exists in the live "public" schema that this app's own
    code never created (e.g. a leftover from early Postgres setup —
    see supabase_schema.sql's header). Rather than guess its name
    blindly with no dashboard access to confirm it, this now asks the
    live database itself for every table that actually exists and
    enables RLS on all of them, so it is correct regardless of what
    that 25th table turns out to be, and self-covers any future table
    too. inspect(engine).get_table_names() only returns tables in the
    connection's own default schema ("public" for this project) — it
    does not touch Supabase's own auth/storage-schema tables, which
    Supabase already manages RLS for itself."""
    inspector = inspect(engine)
    existing_tables = sorted(inspector.get_table_names())
    enabled, failed = [], []
    for table in existing_tables:
        try:
            with engine.begin() as conn:
                conn.execute(text(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY;'))
            enabled.append(table)
        except Exception as e:
            failed.append(table)
            print(f"Security: FAILED to enable RLS on \"{table}\" — {e} — needs manual review in the Supabase dashboard")
    print(f"Security: RLS enabled on {len(enabled)}/{len(existing_tables)} live table(s): {', '.join(enabled)}")
    if failed:
        print(f"Security: {len(failed)} table(s) FAILED — needs manual review: {', '.join(failed)}")


def _post_rls_security_precaution():
    """One-time precaution (confirmed Aug 2026, same URGENT RLS brief,
    after Burgert confirmed both the Supabase Advisor and Render show
    the RLS fix clean and live): usersession was publicly readable via
    PostgREST for the entire time RLS was disabled, and it holds live
    session tokens — a token read straight from that table lets someone
    impersonate a logged-in user directly, no password needed, and it
    wouldn't show up as a failed login anywhere since it never goes
    through /auth/login. Two independent actions, each self-limiting so
    this is safe to leave running on every future startup permanently
    (same reasoning/pattern as the Clear Unlinked Quotes remediation
    and RLS fix above — isolated per action, one failure can't block
    the other or crash startup):

    1) End every session that existed at the moment this code was
       written (2026-08-26T05:48:22 UTC, hardcoded — not "all sessions
       ever", so this can never re-log-out someone who logs in after
       this deploy). _resolve_session() already treats ended_at as an
       immediate hard invalidation (same as a real logout), confirmed
       by reading that function before writing this — so this forces
       everyone off immediately regardless of a token's original 24h
       expires_at.
    2) Reset the three staff passwords (password_hash was exposed
       too). Guarded by comparing against the EXACT original seed hash
       for each user — only fires if nobody has changed their password
       since seeding, so it can never silently overwrite a real
       password someone set themselves. New temporary plaintext
       passwords were generated locally (not by this server) using the
       app's own hash_password(), reported directly to Burgert in
       chat, never committed to source control — same handling as the
       original seed passwords."""
    SESSION_INVALIDATION_CUTOVER = datetime(2026, 8, 26, 5, 48, 22)
    try:
        with Session(engine) as session:
            sessions_to_end = session.exec(
                select(UserSession).where(
                    UserSession.ended_at.is_(None),
                    UserSession.created_at <= SESSION_INVALIDATION_CUTOVER,
                )
            ).all()
            if sessions_to_end:
                now = datetime.utcnow()
                for sess in sessions_to_end:
                    sess.ended_at = now
                    session.add(sess)
                session.commit()
                print(f"Security: force-ended {len(sessions_to_end)} pre-existing session(s) — usersession was publicly exposed, precaution per Burgert")
            else:
                print("Security: no pre-existing sessions to force-end (already done or none existed)")
    except Exception as e:
        print(f"Security: session invalidation FAILED ({e}) — needs manual review")

    try:
        # (username, exact original seed hash, new hash to install if the
        # exact original seed hash is still what's stored)
        resets = [
            ("burgert", "pbkdf2_sha256$260000$295728ae9fea1b39e0acbc754f8b57af$bbf141469314ddc450ffdbdf30c2895c321f7accf2c721c2e3ec8d34e68fdf22",
             "pbkdf2_sha256$260000$9a90538136cd35dca6cd6d6aa44c7912$80fc691b8b3d9a4d61deb96b0a8f2ce45b8074494068285a33241abda7532a13"),
            ("ryno", "pbkdf2_sha256$260000$60f81b8c4153c7ec982d6c5415a5f675$b6d263a91c3c1722ff6d6f99705b193c5f43c4ededfa3d307b314fe28fefd915",
             "pbkdf2_sha256$260000$37b312461548e30b9aca77c3ea5c3e75$cdac0195abd962ebd0493ba0116fd9412497a6c0efec929f7e8fccca5b894011"),
            ("madri", "pbkdf2_sha256$260000$eb80a40b8263fc5984c0fc64ecd2ddca$e52495c5a0605b9a208db009fe8bbb6a1053d09e1f4744223d4fce3728dfb962",
             "pbkdf2_sha256$260000$fa93a5a308a782019a50d2b78d658d60$36d94d18e68b78679bf84e333087490244483a54153fb3ad5bc73b504d4d29b3"),
        ]
        with Session(engine) as session:
            reset_count = 0
            for username, old_hash, new_hash in resets:
                user = session.exec(select(User).where(User.username == username)).first()
                if user and user.password_hash == old_hash:
                    user.password_hash = new_hash
                    session.add(user)
                    reset_count += 1
            if reset_count:
                session.commit()
                print(f"Security: reset password for {reset_count} user(s) still on their original seed password — precaution per Burgert, password_hash was exposed")
            else:
                print("Security: no users still on their original seed password (already reset, or already changed by the user)")
    except Exception as e:
        print(f"Security: password reset FAILED ({e}) — needs manual review")


def _old_password_still_works_remediation():
    """Old Password Still Works incident (confirmed Aug 2026, follow-up
    to the RLS remediation above — Madri's new temp password from
    yesterday didn't work; her OLD password did). Root cause found:
    the block just above only reset a user if their CURRENT
    password_hash still exactly matched their ORIGINAL seed hash — a
    guard deliberately added so it could never clobber a real password
    someone had already set themselves. That exact guard is what let
    this happen: at least one account had apparently already changed
    its password (via /auth/change-password — confirmed the only other
    code path that ever writes password_hash, so this isn't a hidden
    auth bypass) at some point BEFORE yesterday's incident. Its hash no
    longer matched the hardcoded seed value, so the reset silently
    skipped it — leaving whatever password was already set (itself
    still exposed via the same RLS gap, regardless of whether it was
    the seed default or self-chosen) completely untouched. Per each
    account it's actually a DIFFERENT explanation (logged below,
    per-account, not assumed) — one true root cause statement per
    account is what the brief itself asks for.

    Fix, unconditional this time, for all three accounts, regardless of
    whatever their current hash is — the exposure was of whatever
    password_hash existed on that row throughout, not specifically the
    seed value. Idempotent by checking against the NEW hash instead of
    an old one (matching against a specific hardcoded "old" value is
    exactly the mechanism that caused this bug) — once applied, the
    hash equals the new value and this becomes a permanent, correct
    no-op regardless of what it was before. New temp passwords
    generated locally (not on this server), reported to Burgert
    directly in chat, never committed to source control."""
    SEED_HASHES = {
        "pbkdf2_sha256$260000$295728ae9fea1b39e0acbc754f8b57af$bbf141469314ddc450ffdbdf30c2895c321f7accf2c721c2e3ec8d34e68fdf22",
        "pbkdf2_sha256$260000$60f81b8c4153c7ec982d6c5415a5f675$b6d263a91c3c1722ff6d6f99705b193c5f43c4ededfa3d307b314fe28fefd915",
        "pbkdf2_sha256$260000$eb80a40b8263fc5984c0fc64ecd2ddca$e52495c5a0605b9a208db009fe8bbb6a1053d09e1f4744223d4fce3728dfb962",
    }
    YESTERDAYS_RESET_HASHES = {
        "pbkdf2_sha256$260000$9a90538136cd35dca6cd6d6aa44c7912$80fc691b8b3d9a4d61deb96b0a8f2ce45b8074494068285a33241abda7532a13",
        "pbkdf2_sha256$260000$37b312461548e30b9aca77c3ea5c3e75$cdac0195abd962ebd0493ba0116fd9412497a6c0efec929f7e8fccca5b894011",
        "pbkdf2_sha256$260000$fa93a5a308a782019a50d2b78d658d60$36d94d18e68b78679bf84e333087490244483a54153fb3ad5bc73b504d4d29b3",
    }
    NEW_HASHES = {
        "burgert": "pbkdf2_sha256$260000$bd71a3a272a66f36a2b1c97dcbd9128f$3916c72b6ad3fd11067fd90aaa1da51fd33f63594bff3e80537ee90cff5886d4",
        "ryno": "pbkdf2_sha256$260000$24a0b84845ba407700436caa2afa8b8c$36d1b1cd056e8885ee8ba5f81762353c4849305c46a8fc2aa723fe16b904c9ab",
        "madri": "pbkdf2_sha256$260000$2455c78678f837b0a730466431d56eca$07395234f94c081223b3a156833421add281b695e5d95a373029d82d0c86629c",
    }
    try:
        with Session(engine) as session:
            reset_count = 0
            for username, new_hash in NEW_HASHES.items():
                user = session.exec(select(User).where(User.username == username)).first()
                if not user:
                    continue
                if user.password_hash == new_hash:
                    print(f"Old Password incident: {username} already on the new hash issued for this incident — already fixed, no-op")
                    continue
                if user.password_hash in SEED_HASHES:
                    state = "still on the ORIGINAL SEED hash — yesterday's reset should have caught this; needs separate investigation into why it didn't"
                elif user.password_hash in YESTERDAYS_RESET_HASHES:
                    state = "was correctly on YESTERDAY'S reset hash — that part worked; resetting again now purely as this incident's own fresh precaution"
                else:
                    state = "on a DIFFERENT hash — had already been changed (via /auth/change-password) BEFORE yesterday's reset ran, so the exact-seed-hash-match guard silently skipped it. This is the confirmed root cause for this account."
                print(f"Old Password incident: {username} was {state}")
                user.password_hash = new_hash
                user.password_changed_at = datetime.utcnow()
                session.add(user)
                reset_count += 1
            if reset_count:
                session.commit()
                print(f"Old Password incident: force-reset {reset_count} account(s) unconditionally")
    except Exception as e:
        print(f"Old Password incident: remediation FAILED ({e}) — needs manual review")


def _burgert_login_recovery_remediation():
    """Burgert locked out (confirmed Aug 2026): the temp password issued
    for burgert's account in the Old Password Still Works incident
    directly above (df01fd1) didn't work for him at login. That plaintext
    was never stored anywhere retrievable (deliberate practice, same as
    every password this app has ever issued) -- by the time this was
    reported, it no longer existed anywhere to hand back, so this is a
    fresh reset rather than a re-report of the old value.

    Scoped to burgert only -- ryno/madri haven't reported an issue, and
    resetting their passwords too would force an unnecessary re-login
    with no upside. Unconditional and idempotent the same way as the
    incident above: compares against the NEW hash (not an old one) so a
    second boot is a clean no-op, and applies regardless of whatever
    burgert's current hash actually is -- no assumption about why the
    previous one failed (mistyped, mis-copied, or something else) is
    needed for this fix to be correct."""
    NEW_HASH = "pbkdf2_sha256$260000$ba426563e9bb603ec419816bde80fba0$1ae2426ba61040dd7d6a7010387cf043c18a5f8f0ec21552a211e83fc00a3943"
    try:
        with Session(engine) as session:
            user = session.exec(select(User).where(User.username == "burgert")).first()
            if not user:
                print("Burgert login recovery: no 'burgert' user row found — nothing to do")
                return
            if user.password_hash == NEW_HASH:
                print("Burgert login recovery: already on the new hash issued for this incident — no-op")
                return
            user.password_hash = NEW_HASH
            user.password_changed_at = datetime.utcnow()
            session.add(user)
            session.commit()
            print("Burgert login recovery: password force-reset")
    except Exception as e:
        print(f"Burgert login recovery: remediation FAILED ({e}) — needs manual review")


def _madri_login_recovery_remediation():
    """Madri needs a fresh password (confirmed Aug 2026) -- same
    pattern as _burgert_login_recovery_remediation() directly above:
    unconditional and idempotent by comparing against the NEW hash, so
    a second boot is a clean no-op regardless of whatever madri's
    current hash actually is. Scoped to madri only -- burgert/ryno
    haven't reported an issue this time."""
    NEW_HASH = "pbkdf2_sha256$260000$6a5800f5faf2e532eac91f94dc90beb8$757c9a6947d105b2c177e8ea63db9ba676edb7ef6db13cfa7ce921ea05b3551a"
    try:
        with Session(engine) as session:
            user = session.exec(select(User).where(User.username == "madri")).first()
            if not user:
                print("Madri login recovery: no 'madri' user row found — nothing to do")
                return
            if user.password_hash == NEW_HASH:
                print("Madri login recovery: already on the new hash issued for this incident — no-op")
                return
            user.password_hash = NEW_HASH
            user.password_changed_at = datetime.utcnow()
            session.add(user)
            session.commit()
            print("Madri login recovery: password force-reset")
    except Exception as e:
        print(f"Madri login recovery: remediation FAILED ({e}) — needs manual review")


def _fix_orphaned_quotes_remediation():
    """Create New Client From Quote incident (confirmed Aug 2026,
    "Root Cause Confirmed" brief) — real, already-stuck data found:
    a quote typed with a genuinely new client's name (Frikkie Klynhans,
    via the Flooring Quotes drill-down, before the Client-Link Audit
    fix went live) had client_id=None with NO way to fix it through the
    UI at all — Job Detail's relink search only searches EXISTING
    clients, and this person was never created as one at all.

    Rather than a one-off fix naming just that one quote, this sweeps
    EVERY quote in this state: any Quote with client_id IS NULL and a
    real (non-blank) client_name, resolved-or-created via the exact
    same _resolve_or_create_client() the Client-Link Audit fix already
    uses for every quote going forward (main.py, create_quote()/
    update_quote_details()) — this closes it for every quote already
    stuck this way, not just the one reported. Deliberately NOT fuzzy-
    matching against a similarly-but-not-identically-spelled existing
    client (e.g. a Builder Portal record with a different spelling of
    the same name) — same "exact match only, never guess" discipline
    the duplicate-client detector already follows; a near-miss creates
    a second client record here rather than silently merging two
    people who might not actually be the same person. Idempotent: a
    quote already linked (client_id set, whether by this remediation on
    a previous boot or normally) is never touched again."""
    try:
        with Session(engine) as session:
            stuck = session.exec(
                select(Quote).where(Quote.client_id.is_(None), Quote.client_name != "")
            ).all()
            fixed = 0
            for q in stuck:
                try:
                    client = _resolve_or_create_client(session, q.tenant_id, None, q.client_name, branch=q.branch)
                    q.client_id = client.id
                    q.client_name = client.name
                    session.add(q)
                    fixed += 1
                    print(f"Migration: linked orphaned Quote #{q.id} ('{client.name}') to client_id={client.id}")
                except Exception as e:
                    print(f"Migration: could not fix orphaned Quote #{q.id} ({e}) — left alone, needs manual review")
            if fixed:
                session.commit()
                print(f"Migration: fixed {fixed} orphaned quote(s) — Create New Client From Quote incident")
            else:
                print("Migration: no orphaned quotes found (already fixed, or none exist)")
    except Exception as e:
        print(f"Migration: orphaned-quotes sweep FAILED ({e}) — needs manual review")


@app.on_event("startup")
def on_startup():
    _verify_cascade_policy_complete()
    _ensure_new_columns()
    SQLModel.metadata.create_all(engine)
    _enable_row_level_security()
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

        # Floor-prep reference data seed (confirmed Aug 2026, Screed
        # Calculator: Extra Rooms brief, Section 2 — "from Azura's own
        # Floor Preparation & Adhesives price list"). One-time, idempotent
        # (only runs if the table is completely empty) — cost_ex_vat_per_pack
        # is deliberately left None for every row: the brief's own
        # reference table gives pack size + coverage rate only, no
        # pricing, so there's nothing real to seed there; Burgert sets it
        # via the Supplier Console the same way any other product's price
        # gets set. Two rows for any product the brief states with two
        # pack sizes (BONDiTe, iTe SLURRY, GRIPiTe V50) — coverage
        # differs meaningfully per pack for iTe SLURRY (85m²/15kg vs
        # 175m²/30kg, not a clean per-kg multiple), so each needs its own
        # row rather than one row with a shared rate.
        if not session.exec(select(FloorPrepProduct)).first():
            floor_prep_seed = [
                ("LEVELiTe F10", 20, "kg", 1.4, "kg_per_m2_per_mm"),
                ("LEVELiTe F30", 20, "kg", 1.4, "kg_per_m2_per_mm"),
                ("BONDiTe (5L)", 5, "L", 4, "m2_per_L"),
                ("BONDiTe (25L)", 25, "L", 4, "m2_per_L"),
                ("iTe SLURRY (15kg)", 15, "kg", 85, "m2_per_pack"),
                ("iTe SLURRY (30kg)", 30, "kg", 175, "m2_per_pack"),
                ("PATCHiTe", 25, "kg", 2, "kg_per_m2_per_mm"),
                ("VAPORiTe", 4.5, "kg", 4, "m2_per_kg"),
                ("GRIPiTe V50 (5L)", 5, "L", 4, "m2_per_L"),
                ("GRIPiTe V50 (20L)", 20, "L", 4, "m2_per_L"),
                ("GRIPiTe H80", 15, "kg", 1, "m2_per_kg"),
            ]
            for name, pack_size, pack_unit, coverage_rate, coverage_basis in floor_prep_seed:
                session.add(FloorPrepProduct(
                    tenant_id=DEFAULT_TENANT_ID, supplier="Azura", product_name=name,
                    pack_size=pack_size, pack_unit=pack_unit, coverage_rate=coverage_rate,
                    coverage_basis=coverage_basis, source="seed",
                ))
            print(f"Migration: seeded {len(floor_prep_seed)} FloorPrepProduct row(s) for Azura")
        session.commit()

        # Fixed Display Order backfill (confirmed Aug 2026, Add-Line
        # Data-Loss brief §4) — best-effort: existing flooring/trim lines
        # were added before flooring_pricing_type/trim_sub_category
        # existed, so they'd otherwise fall back to the sort's default
        # bucket (Floor/Vinyl, Trims) forever. Looks up each line's
        # CURRENT price-book product to backfill the real value — not
        # perfect if that product was since deleted or its type/category
        # changed, but strictly better than leaving every historical line
        # unclassified, and each line is independent so one lookup miss
        # can't affect any other.
        try:
            flooring_to_fix = session.exec(
                select(QuoteLineItem).where(QuoteLineItem.category == "flooring", QuoteLineItem.flooring_pricing_type.is_(None))
            ).all()
            fixed = 0
            for line in flooring_to_fix:
                product = session.get(FlooringProduct, line.product_id)
                if product:
                    line.flooring_pricing_type = product.pricing_type
                    session.add(line)
                    fixed += 1
            if fixed:
                session.commit()
                print(f"Migration: backfilled flooring_pricing_type for {fixed} existing line(s)")
        except Exception as e:
            session.rollback()
            print(f"Migration: flooring_pricing_type backfill failed ({e}) — existing flooring lines keep the default sort bucket")

        try:
            trim_to_fix = session.exec(
                select(QuoteLineItem).where(QuoteLineItem.category == "trim", QuoteLineItem.trim_sub_category.is_(None))
            ).all()
            fixed = 0
            for line in trim_to_fix:
                product = session.get(TrimProduct, line.product_id)
                if product:
                    line.trim_sub_category = product.category
                    session.add(line)
                    fixed += 1
            if fixed:
                session.commit()
                print(f"Migration: backfilled trim_sub_category for {fixed} existing line(s)")
        except Exception as e:
            session.rollback()
            print(f"Migration: trim_sub_category backfill failed ({e}) — existing trim lines keep the default sort bucket")

        # Skirting/Trim category conflation (confirmed Aug 2026, Vinyl
        # Redesign: Real Usage Findings brief §1) — REAL GAP, confirmed
        # not just relabeled: add_trim_line()/edit_trim_line() hardcoded
        # category="trim" for every line regardless of the product's own
        # real sub-type, so a Skirting line has always been stored (and
        # displayed, and rated for commission) as "trim". Checked the
        # downstream impact the brief itself asked about before fixing
        # anything: generate_order_sheets() never looks at trim/skirting
        # lines at all either way (confirmed by reading it — "Burgert
        # orders those separately in bulk direct from Supertrim"), so no
        # Order Sheet impact. Real impact found instead: CommissionRate.
        # category's own model comment already lists "skirting" as a
        # real, intended, separately-configurable builder-rep commission
        # category (models.py) — one that could never actually match,
        # since no line was ever genuinely stored as "skirting". Fixed at
        # the correct layer per the brief's own instruction: the STORED
        # category itself, not a display-only patch — every downstream
        # reader (the Quote Lines category pill, the persistent summary
        # panel, commission lookup) becomes correct automatically once
        # the source value is, with zero changes needed at any of them.
        # Depends on trim_sub_category already being populated — placed
        # right after that backfill on purpose.
        try:
            skirting_to_fix = session.exec(
                select(QuoteLineItem).where(QuoteLineItem.category == "trim", QuoteLineItem.trim_sub_category.in_(("skirting", "quarter_round")))
            ).all()
            fixed = 0
            for line in skirting_to_fix:
                line.category = "skirting"
                session.add(line)
                fixed += 1
            if fixed:
                session.commit()
                print(f"Migration: recategorized {fixed} existing line(s) from 'trim' to 'skirting' — real gap, not cosmetic (see this block's own comment)")
        except Exception as e:
            session.rollback()
            print(f"Migration: skirting recategorization failed ({e}) — existing skirting lines keep reading as 'trim' until this is retried")

        # Job Workflow backfill (confirmed Aug 2026, Order Index / Job
        # Workflow Redesign brief) — one-time-per-row derivation of
        # workflow_status/job_number/accepted_at/declined_at/
        # installation_confirmed_date/completion_date from the legacy
        # `status` field and the order-tracking dates that already
        # existed before this brief. Runs on every startup, only ever
        # ACTS once per row — any Quote that already has job_number,
        # accepted_at, or declined_at set (from an earlier run of this
        # backfill, or genuinely handled since via the new action
        # endpoints below) is skipped. accepted_at and completion_date
        # backfilled here are explicit APPROXIMATIONS (created_at /
        # final_payment_date respectively) since no exact historical
        # timestamp exists for either — logged as such, never silently
        # guessed at as if it were real.
        unmigrated = session.exec(
            select(Quote).where(Quote.job_number.is_(None), Quote.accepted_at.is_(None), Quote.declined_at.is_(None))
        ).all()
        if unmigrated:
            migrated_count = 0
            for q in sorted(unmigrated, key=lambda x: x.created_at):
                legacy = (q.status or "draft").lower()
                if legacy == "declined":
                    q.declined_at = q.created_at   # approximation — no exact decline timestamp exists historically; workflow_status stays 'quoted', a declined quote never became a job
                elif legacy in ("accepted", "invoiced", "paid"):
                    q.accepted_at = q.created_at   # approximation
                    q.job_number = _next_job_number(session, q.tenant_id)
                    q.workflow_status = "accepted"
                    if q.installation_date:
                        q.workflow_status = "scheduled"
                        q.installation_confirmed_date = q.installation_date   # best available proxy — no separate "confirmed" concept existed before this brief
                    if q.final_payment_date:
                        q.workflow_status = "completed"
                        q.completion_date = q.final_payment_date   # best available proxy — no completion_date concept existed before this brief
                # legacy in ('draft', 'sent') -> workflow_status already defaults to 'quoted' from the column's own DEFAULT, nothing else to set
                session.add(q)
                migrated_count += 1
            session.commit()
            if migrated_count:
                print(f"Migration: backfilled job workflow for {migrated_count} existing quote(s) — job numbers assigned, workflow_status/accepted_at/declined_at/installation_confirmed_date/completion_date derived from legacy status + existing dates (accepted_at and completion_date are approximations, see this block's own comment)")

        # One-time data remediation (confirmed Aug 2026, "Clear the
        # Unlinked Quotes List: Actions Requested" brief) — three
        # specific quotes, three DIFFERENT actions, per Burgert's own
        # explicit per-quote sign-off ("they are NOT all the same
        # situation, do not treat them identically"). Deliberately
        # hardcoded to these three quote ids — this is a one-time
        # remediation for a specific, confirmed incident, not a general
        # "clean up unlinked quotes" sweep (that's exactly what the
        # brief warned against — silently merging/deleting is never
        # done automatically anywhere else in this codebase either).
        # Runs via the same idempotent on_startup() mechanism as every
        # other migration here: each action re-checks its own
        # precondition (quote still exists, name still matches what was
        # confirmed, still needs the action) before doing anything, so
        # this is safe to leave in place permanently and re-run on
        # every future deploy without re-doing or mis-doing anything —
        # once each quote is gone/linked, its own block becomes a no-op
        # forever after.
        #
        # Each of the three wrapped independently in its own try/except
        # (confirmed Aug 2026) — this runs unattended on every future
        # deploy with no way to test it against the real production data
        # beforehand, so an unexpected failure on ANY ONE of these
        # (e.g. a network hiccup talking to Supabase Storage while
        # cleaning up a photo) must not (a) take the other two down with
        # it, or (b) crash on_startup() itself, which would leave the
        # whole backend unable to boot at all — a far worse outcome than
        # the original banner. Any real failure is printed, never
        # swallowed silently.
        try:
            q49 = session.get(Quote, 49)
            if q49 and q49.tenant_id == DEFAULT_TENANT_ID and q49.client_name.strip().lower() == "burgert test":
                reasons = _quote_delete_dependencies(session, q49, DEFAULT_TENANT_ID)
                if not reasons:
                    session.add(AuditLog(
                        tenant_id=DEFAULT_TENANT_ID, username="system (Clear Unlinked Quotes brief, confirmed by Burgert)",
                        entity_type="Quote", entity_id=49, field="__deleted__",
                        old_value="Quote #49 — Burgert Test (confirmed test data)", new_value="(deleted)",
                    ))
                    _delete_quote_cascade(session, q49, DEFAULT_TENANT_ID)
                    session.commit()
                    print("Migration: deleted Quote #49 (Burgert Test) — confirmed test data, per Clear Unlinked Quotes brief")
                else:
                    print(f"Migration: Quote #49 still blocked from deletion ({reasons}) — left alone, needs manual review")
        except Exception as e:
            session.rollback()
            print(f"Migration: Quote #49 remediation failed ({e}) — left alone, needs manual review")

        try:
            q40 = session.get(Quote, 40)
            if q40 and q40.tenant_id == DEFAULT_TENANT_ID and q40.client_name.strip().lower() == "john doe":
                # Confirmed test values, not real payment records —
                # cleared first so the delete-dependency check (which
                # correctly treats a real recorded deposit/payment as a
                # hard block, see _quote_delete_dependencies) no longer
                # has anything to object to.
                q40.deposit_paid_date = None
                q40.final_payment_date = None
                session.add(q40)
                session.commit()
                session.refresh(q40)
                reasons = _quote_delete_dependencies(session, q40, DEFAULT_TENANT_ID)
                if not reasons:
                    session.add(AuditLog(
                        tenant_id=DEFAULT_TENANT_ID, username="system (Clear Unlinked Quotes brief, confirmed by Burgert)",
                        entity_type="Quote", entity_id=40, field="__deleted__",
                        old_value="Quote #40 — John Doe (confirmed test data; test deposit/final-payment dates cleared first)", new_value="(deleted)",
                    ))
                    _delete_quote_cascade(session, q40, DEFAULT_TENANT_ID)
                    session.commit()
                    print("Migration: cleared test payment fields and deleted Quote #40 (John Doe), per Clear Unlinked Quotes brief")
                else:
                    print(f"Migration: Quote #40 still blocked from deletion after clearing payment fields ({reasons}) — left alone, needs manual review")
        except Exception as e:
            session.rollback()
            print(f"Migration: Quote #40 remediation failed ({e}) — left alone, needs manual review")

        try:
            q48 = session.get(Quote, 48)
            if q48 and q48.tenant_id == DEFAULT_TENANT_ID and not q48.client_id:
                # REAL client, pricing already verified against
                # Burgert's own Excel calculator — confirmed directly:
                # never delete this one, only link it. Checked for an
                # existing client record first (case-insensitive exact
                # match); only creates a new one if genuinely none
                # exists, and only ever with the name — no phone/email/
                # address fabricated, left blank for Burgert to fill in
                # himself, per the brief's own explicit instruction not
                # to guess contact details.
                existing = session.exec(
                    select(Client).where(Client.tenant_id == DEFAULT_TENANT_ID)
                ).all()
                match = next((c for c in existing if c.name.strip().lower() == "robert aspeling"), None)
                if not match:
                    match = Client(tenant_id=DEFAULT_TENANT_ID, name="Robert Aspeling")
                    session.add(match)
                    session.commit()
                    session.refresh(match)
                    print(f"Migration: created new Client record for Robert Aspeling (id={match.id}) — name only, no contact details fabricated")
                q48.client_id = match.id
                q48.client_name = match.name
                session.add(q48)
                session.commit()
                print(f"Migration: linked Quote #48 to Robert Aspeling (client_id={match.id}), per Clear Unlinked Quotes brief")
        except Exception as e:
            session.rollback()
            print(f"Migration: Quote #48 remediation failed ({e}) — left alone, needs manual review")

    # Run last, deliberately: relies on the User-seeding block earlier in
    # this same function having already run (guards on comparing against
    # each user's real password_hash, which only exists once seeded).
    # Confirmed via a local test this only matters for a from-scratch DB
    # — production already has its users, so ordering here never affected
    # the real deploy — but placed correctly regardless for robustness.
    _post_rls_security_precaution()
    # _old_password_still_works_remediation() / _burgert_login_recovery_
    # remediation() / _madri_login_recovery_remediation() — REMOVED from
    # startup (confirmed Aug 2026, real incident: "Ryno's password
    # doesn't work" after it had been freshly reset and verified working
    # earlier the same day). Root cause: all three functions compare the
    # account's CURRENT password_hash against ONE hardcoded historical
    # hash and force-reset back to that exact hash on EVERY boot if it
    # doesn't match — correct for their original one-time incident, but
    # never guarded against a LEGITIMATE password change happening after
    # that incident closed. Every redeploy since (this session alone:
    # Dead Code Audit, Dropbox v2, the migration fix, the delete-quote
    # fix...) silently reverted Ryno's password back to that old,
    # unrecoverable-plaintext hash the moment a real reset (self-service
    # or Owner-triggered link) set it to anything different. Burgert's
    # and Madri's passwords happened to already equal their own target
    # hash for each of today's redeploys, which is the only reason this
    # wasn't ALSO visibly breaking their logins — the same latent risk
    # applied to all three accounts equally. The three function bodies
    # are left in place (real historical documentation, genuinely
    # harmless once uncalled) but must never run again — a proper
    # password change now has to actually stay changed.
    _fix_orphaned_quotes_remediation()

    # Diagnostic (confirmed Aug 2026, Supplier Order Sheets brief §4 —
    # "verify Azura's existing floor-prep/consumable product records
    # already have their discount % correctly set to 0 — do not rely
    # solely on order-sheet logic to override this; the underlying data
    # should be correct too. Report back what's currently stored and
    # correct if wrong."). Read-only, prints to the Render logs on every
    # boot. Structurally confirmed by reading models.py directly before
    # writing this: FloorPrepProduct has NO discount_pct/trade_discount_pct
    # field at all — cost_ex_vat_per_pack is a flat, already-net figure,
    # and grepping the whole codebase found no code path that applies any
    # discount to it anywhere. So there is nothing that could be
    # incorrectly non-zero here — this prints the real current values so
    # Burgert can see them directly, not because a fix was needed.
    try:
        with Session(engine) as session:
            fp_products = session.exec(select(FloorPrepProduct)).all()
            if fp_products:
                print(f"Supplier Order Sheets brief §4: {len(fp_products)} FloorPrepProduct row(s) — no discount field exists on this table (confirmed via models.py), cost_ex_vat_per_pack is the real, already-net figure for each:")
                for p in fp_products:
                    print(f"  - {p.supplier} / {p.product_name} ({p.pack_size}{p.pack_unit}): cost_ex_vat_per_pack={p.cost_ex_vat_per_pack}")
            else:
                print("Supplier Order Sheets brief §4: no FloorPrepProduct rows found yet.")
    except Exception as e:
        print(f"Supplier Order Sheets brief §4: diagnostic scan failed ({e})")

    # Diagnostic audit (confirmed Aug 2026, Add-Line Data-Loss brief §3
    # — "audit whether any already-saved real quotes have already lost a
    # line due to this bug"). Read-only, prints findings to the Render
    # logs on every boot — cheap enough to just leave running rather than
    # a true one-time migration, and re-checking costs nothing.
    #
    # IMPORTANT LIMITATION, stated plainly: _log_quote_line_audit() only
    # writes to AuditLog for quotes already accepted/scheduled/completed
    # at the time — a brand-new "quoted" draft quote losing a line this
    # way leaves NO trace anywhere, by design of that existing gate (see
    # its own docstring). So this can only find the bug's fingerprint on
    # quotes that had already been accepted when it happened; it CANNOT
    # prove a draft quote was never affected. The fingerprint itself: the
    # bug's frontend mechanism (deleteLineBeingEditedIfAny(), called
    # unconditionally before every add) deletes-then-adds in the same
    # synchronous click — so a "__line_removed__" entry immediately
    # followed by a "__line_added__" entry, same quote, same user, within
    # a couple of seconds, is exactly what that looks like in the log.
    # This does NOT prove data loss on its own (a genuine, intentional
    # "delete this line, then add a replacement" edit looks identical) —
    # it's a candidate list for Burgert to actually look at, not an
    # automatic conclusion.
    try:
        with Session(engine) as session:
            removals = session.exec(
                select(AuditLog).where(AuditLog.field == "__line_removed__").order_by(AuditLog.timestamp)
            ).all()
            additions = session.exec(
                select(AuditLog).where(AuditLog.field == "__line_added__").order_by(AuditLog.timestamp)
            ).all()
            candidates = []
            for rem in removals:
                for add in additions:
                    if (add.entity_id == rem.entity_id and add.username == rem.username
                            and 0 <= (add.timestamp - rem.timestamp).total_seconds() <= 5):
                        candidates.append(f"Quote #{rem.entity_id} by {rem.username} at {rem.timestamp}: removed \"{rem.old_value}\", added \"{add.new_value}\" seconds later")
            if candidates:
                print(f"Audit (Add-Line Data-Loss brief): {len(candidates)} candidate remove-then-add event(s) found on already-accepted+ quotes — review needed, NOT automatic proof of data loss:")
                for c in candidates:
                    print(f"  - {c}")
            else:
                print("Audit (Add-Line Data-Loss brief): no remove-then-add candidates found in AuditLog — but this only covers quotes already accepted/scheduled/completed at the time; draft/quoted-status quotes are not logged and can't be checked this way.")
    except Exception as e:
        print(f"Audit (Add-Line Data-Loss brief): scan failed ({e})")

    # Carpet Calculators (confirmed Aug 2026, Carpet Calculators Final
    # Build Brief) — seeds the four new categories' own price-book rows,
    # same "real business data confirmed directly by Burgert, added via a
    # one-time idempotent startup migration" precedent as Aspen's R15/m²
    # delivery fee. Gated per-product (not "table empty") since real
    # Vinyl products already exist — each check is its own guard so
    # re-running this after Burgert has edited/renamed one of these
    # doesn't silently recreate a duplicate. All four: supplier="Belgotex"
    # (confirmed, Cape Directs price list), sell_markup_multiplier=1.3
    # (30%, confirmed matching Vinyl's own rate), trade_discount_pct=0.0
    # (confirmed — every reference spreadsheet's own "Less 0%" cell).
    try:
        with Session(engine) as session:
            seeds = [
                dict(product_name="Influence", colour="", supplier="Belgotex", pricing_type="material",
                     flooring_category="carpet_tufted_broadloom", base_cost_ex_vat=215.00, roll_width_m=4.00,
                     sell_markup_multiplier=1.3, trade_discount_pct=0.0, tenant_id=DEFAULT_TENANT_ID),
                dict(product_name="Berber Point 920", colour="", supplier="Belgotex", pricing_type="material",
                     flooring_category="carpet_needlepunch_broadloom", base_cost_ex_vat=207.00, roll_width_m=4.20,
                     sell_markup_multiplier=1.3, trade_discount_pct=0.0, tenant_id=DEFAULT_TENANT_ID),
                dict(product_name="Berber Point 920 NEXBAC", colour="", supplier="Belgotex", pricing_type="material",
                     flooring_category="carpet_tile", base_cost_ex_vat=380.00, m2_per_pack=5.0,
                     sell_markup_multiplier=1.3, trade_discount_pct=0.0, tenant_id=DEFAULT_TENANT_ID),
                dict(product_name="Amble", colour="", supplier="Belgotex", pricing_type="material",
                     flooring_category="cushion_vinyl", base_cost_ex_vat=182.00, roll_width_m=3.00,
                     sell_markup_multiplier=1.3, trade_discount_pct=0.0, tenant_id=DEFAULT_TENANT_ID),
            ]
            added = 0
            for s in seeds:
                exists = session.exec(select(FlooringProduct).where(
                    FlooringProduct.tenant_id == s["tenant_id"], FlooringProduct.product_name == s["product_name"],
                    FlooringProduct.supplier == s["supplier"], FlooringProduct.flooring_category == s["flooring_category"],
                )).first()
                if not exists:
                    session.add(FlooringProduct(**s))
                    added += 1
            if added:
                session.commit()
                print(f"Carpet Calculators: seeded {added} new price-book product(s) (Influence/Berber Point 920/Berber Point 920 NEXBAC/Amble)")
            else:
                print("Carpet Calculators: all 4 price-book products already exist — nothing seeded")
    except Exception as e:
        print(f"Carpet Calculators: price-book seed FAILED ({e}) — needs manual review")

    # "Colour" Field Showing Products Instead of Real Colours (confirmed
    # Aug 2026) — one-time move of a genuinely misplaced value, not a
    # relabel: these two ranges are the only ones confirmed (not
    # assumed — checked every multi-row range in the price book for
    # whether its "colours" share an identical cost/pack-size/tile-
    # dimension, the real tell that they're colourways of ONE product
    # rather than several different real products; deZIGN series
    # 200/120/250/XL, every Como/Aspen/Numi range, etc. all passed that
    # check and are correctly left untouched) to have a distinct real
    # product's name sitting in `colour` instead of an actual colour.
    # Idempotent via its own WHERE clause (product_variant IS NULL) —
    # once moved, a row never matches again on a later restart.
    # `colour` is deliberately left blank afterward, never guessed —
    # confirmed via the Belgotex Cape Directs price list itself that no
    # real per-product colour breakdown exists anywhere for either range
    # yet (see quote-builder.js's own "no colours loaded yet" handling
    # for what this blank state actually shows staff).
    try:
        with Session(engine) as session:
            rows_to_fix = session.exec(
                select(FlooringProduct).where(
                    FlooringProduct.product_name.in_(("Luxury Vinyl Planks", "Vinyl Composite Tiles")),
                    FlooringProduct.product_variant.is_(None),
                    FlooringProduct.colour != "",
                )
            ).all()
            fixed = 0
            for product in rows_to_fix:
                product.product_variant = product.colour
                product.colour = ""
                session.add(product)
                fixed += 1
            if fixed:
                session.commit()
                print(f"Migration: moved {fixed} misplaced product name(s) out of 'colour' and into the new 'product_variant' field (Luxury Vinyl Planks / Vinyl Composite Tiles) — see this block's own comment")
    except Exception as e:
        print(f"Migration: product_variant backfill failed ({e}) — Luxury Vinyl Planks/Vinyl Composite Tiles keep showing the old, mislabeled 'colour' values until this is retried")

    # Order Index Nightly Snapshot (Dropbox Document Archive brief v2,
    # confirmed Aug 2026) — in-process APScheduler, not a separate Render
    # Cron Job service: confirmed bolton-backend is on an always-on
    # (paid) Render plan, so this process is genuinely running at 23:00
    # UTC / 01:00 SAST every night, not asleep waiting for a request the
    # way a free-tier service would be — the one condition that makes an
    # in-process scheduler reliable instead of a gamble. hour=23 UTC is
    # deliberately 01:00 SAST (UTC+2, no DST) — a real low-activity
    # window, not an arbitrary number. BackgroundScheduler runs on its
    # own thread inside this same process; runs the job function
    # directly (run_order_index_snapshot_job(), defined further down this
    # file — already available by the time this actually fires, since
    # on_startup() itself only runs once the whole module has finished
    # importing). misfire_grace_time covers a redeploy that happens to
    # land exactly on the scheduled minute — the job still runs shortly
    # after instead of being silently skipped for that day.
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        from apscheduler.triggers.cron import CronTrigger
        scheduler = BackgroundScheduler(timezone="UTC")
        scheduler.add_job(run_order_index_snapshot_job, CronTrigger(hour=23, minute=0), id="order_index_snapshot", misfire_grace_time=3600, replace_existing=True)
        # Database Backups (Dropbox brief §5, confirmed Aug 2026) — same
        # scheduler, a different hour (02:00 UTC / 04:00 SAST) so it
        # never runs at the exact same moment as the snapshot job above.
        scheduler.add_job(run_database_backup_job, CronTrigger(hour=2, minute=0), id="database_backup", misfire_grace_time=3600, replace_existing=True)
        # Live Consistency Monitor (confirmed Aug 2026) — same scheduler,
        # a third distinct hour (03:00 UTC / 05:00 SAST) so it never runs
        # at the exact same moment as either job above. "Reuse the
        # existing in-process scheduler already built... no new
        # scheduling infrastructure needed" — this brief's own explicit
        # instruction, not a new mechanism.
        scheduler.add_job(run_consistency_monitor_job, CronTrigger(hour=3, minute=0), id="consistency_monitor", misfire_grace_time=3600, replace_existing=True)
        scheduler.start()
        print("Scheduler started: Order Index Snapshot next at 23:00 UTC (01:00 SAST); Database Backup next at 02:00 UTC (04:00 SAST); Consistency Monitor next at 03:00 UTC (05:00 SAST)")
    except Exception as e:
        print(f"Scheduler FAILED to start ({e}) — nightly snapshots/backups will not run until this is fixed")


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
        # Single Active Session per User (confirmed Aug 2026) — root
        # cause of multiple simultaneous "Still active" sessions for the
        # same person: staff close the browser/app instead of clicking
        # Log out, so the old session never gets an ended_at and just
        # sits active until its own 24h natural expiry, while a fresh
        # login later that day creates another on top of it. Every OTHER
        # currently-active session for this user is now ended here,
        # immediately, the moment a new login succeeds — "currently
        # active" is the exact same check _resolve_session() itself uses
        # (ended_at IS NULL and not yet naturally expired), so this can
        # never touch a session that was already correctly inert.
        # ended_reason="superseded" (a real stored field, see its own
        # comment on the model) keeps this distinguishable from a real
        # logout or a natural expiry in the session log.
        now = datetime.utcnow()
        other_active_sessions = session.exec(select(UserSession).where(
            UserSession.user_id == user.id, UserSession.ended_at.is_(None), UserSession.expires_at >= now,
        )).all()
        for old_sess in other_active_sessions:
            old_sess.ended_at = now
            old_sess.ended_reason = "superseded"
            session.add(old_sess)
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
                sess.ended_reason = "logout"
                session.add(sess)
                session.commit()
    return {"ok": True}


RESET_LINK_MINUTES = 45   # brief's own suggested range was 30-60; a single fixed value partway through it


def _validate_reset_token(session: Session, token: str) -> PasswordResetToken:
    """Shared by the validate-only GET (frontend's upfront "is this
    link still good" check, before showing the set-password form) and
    the real POST reset below — one place decides what makes a token
    valid, so the two can never quietly disagree."""
    reset = session.exec(select(PasswordResetToken).where(PasswordResetToken.token == token)).first()
    if not reset:
        raise HTTPException(404, "This reset link isn't valid — check you copied the whole link.")
    if reset.used_at is not None:
        raise HTTPException(400, "This reset link has already been used. Ask the Owner for a new one.")
    if reset.expires_at < datetime.utcnow():
        raise HTTPException(400, "This reset link has expired. Ask the Owner for a new one.")
    return reset


@app.get("/auth/reset-password/validate")
def validate_reset_token(token: str):
    """Public (brief §1 — reached before any login exists). Read-only
    upfront check so the frontend can show "this link has expired"
    immediately on page load, rather than only failing once someone's
    already typed a new password and pressed submit."""
    with Session(engine) as session:
        _validate_reset_token(session, token)
        return {"valid": True}


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str


@app.post("/auth/reset-password")
def reset_password(body: ResetPasswordRequest):
    """Password Reset Link brief §1+§3 (confirmed Aug 2026) — public
    (no session required — this IS how someone gets a session again).
    The new password is set directly from this request body, entered
    by the staff member themselves on the "Set your new password"
    screen — never passed through or visible to Burgert at any point,
    the whole point of this brief over the old generate-and-relay
    approach. Marks the token used immediately (can't be reused), and
    — brief's own explicit requirement #4 — force-ends every one of
    this user's currently active sessions, same pattern login() itself
    already uses for Single Active Session, so a stale/leaked old
    session can't coexist with the freshly reset password."""
    if len(body.new_password) < 8:
        raise HTTPException(400, "New password must be at least 8 characters.")
    with Session(engine) as session:
        reset = _validate_reset_token(session, body.token)
        user = session.get(User, reset.user_id)
        if not user:
            raise HTTPException(404, "The account for this reset link no longer exists.")
        user.password_hash = hash_password(body.new_password)
        user.password_changed_at = datetime.utcnow()
        session.add(user)
        reset.used_at = datetime.utcnow()
        session.add(reset)
        now = datetime.utcnow()
        active_sessions = session.exec(select(UserSession).where(
            UserSession.user_id == user.id, UserSession.ended_at.is_(None), UserSession.expires_at >= now,
        )).all()
        for sess in active_sessions:
            sess.ended_at = now
            sess.ended_reason = "password_reset"
            session.add(sess)
        session.commit()
        return {"ok": True, "username": user.username}


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
    """Old-Password-Still-Works Investigation (confirmed Aug 2026) — real
    gap found while checking, not guessed: reset_password() (the Owner-
    triggered one-time link) already force-ends every one of this user's
    active sessions the moment a password changes; this self-service path
    never did. The PASSWORD CHECK ITSELF was never the problem — login()
    always verifies against user.password_hash fresh from the DB, no
    caching layer anywhere, so the OLD password correctly stops working
    immediately either way. This was the separate half of that
    investigation's own question: a SESSION/token issued before the
    change (a stolen/lost device, or one simply left logged in somewhere
    forgotten) stayed valid indefinitely even after the legitimate user
    "fixed" things by changing their own password. Same
    end-other-sessions pattern login()/reset_password() already use —
    the one difference from both: THIS session (the one making the
    change-password request itself) is deliberately excluded, since a
    genuine self-service change while actively logged in shouldn't log
    the person out of the device they're sitting at right now."""
    if len(body.new_password) < 8:
        raise HTTPException(400, "New password must be at least 8 characters.")
    token = _get_bearer_token(request)
    with Session(engine) as session:
        sess = session.exec(select(UserSession).where(UserSession.token == token)).first()
        user = session.get(User, sess.user_id)
        if not verify_password(body.current_password, user.password_hash):
            raise HTTPException(401, "Current password is incorrect.")
        user.password_hash = hash_password(body.new_password)
        user.password_changed_at = datetime.utcnow()
        session.add(user)
        now = datetime.utcnow()
        other_active_sessions = session.exec(select(UserSession).where(
            UserSession.user_id == user.id, UserSession.id != sess.id,
            UserSession.ended_at.is_(None), UserSession.expires_at >= now,
        )).all()
        for old_sess in other_active_sessions:
            old_sess.ended_at = now
            old_sess.ended_reason = "password_changed"
            session.add(old_sess)
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


def _require_active_flooring_product(product: "FlooringProduct") -> "FlooringProduct":
    """Bulk Import Full Belgotex Carpet Range, PENDING (confirmed Aug
    2026) — "NOT selectable in the live quote builder by any account
    (Owner included) until explicitly activated," enforced at the one
    real choke point every pricing path already goes through: right
    after resolving a FlooringProduct for a quote line (add/edit
    Flooring, Carpet, Stairwell vinyl). Deliberately NOT applied to the
    Supplier Console's own CRUD endpoints (update_flooring/
    delete_flooring) — a pending product must stay fully editable/
    deletable there, since reviewing and correcting it is the whole
    point of this state. A frontend dropdown filter alone (the ONLY
    thing that existed for the equivalent Carpet-vs-Vinyl routing gap
    found in the Category Contracts investigation) would satisfy
    "not selectable" for a normal click-through but not a direct API
    call — this is the real, structural version."""
    if product.pending_review:
        raise HTTPException(400, f"'{product.product_name}' is still pending review and can't be used on a quote yet.")
    return product


def margin_band_for(margin_pct, settings) -> Optional[str]:
    """Margin Becomes Owner-Only; Price Gets a Colour Signal (confirmed
    Aug 2026) — a discrete, two-family band Ryno/Madri get INSTEAD of the
    real number: "red" (below the confirmed 30% floor, same
    flooring_margin_warn_threshold every other warning in this app
    already uses — not a new number) or "green"/"green_bright" (at or
    above it). Burgert's own explicit correction mid-build: 30-40% and
    40%+ are BOTH good outcomes, never amber/alarming — only the shade
    differs, distinguishing "acceptable" from "more than what is needed
    or aimed" without ever implying the lower of the two is a problem.
    Discrete bands, not a gradient — confirmed with Burgert directly,
    "safer... a smooth gradient risks trying to reverse-engineer the
    exact percentage from subtle shade differences." Computed here, once,
    from whatever real margin_pct a category's own calc function already
    produced — never a second, independent calculation of margin itself."""
    if margin_pct is None:
        return None
    if margin_pct < settings.flooring_margin_warn_threshold:
        return "red"
    if margin_pct < settings.margin_band_bright_threshold:
        return "green"
    return "green_bright"


def strip_sensitive_fields(line_item: dict, role: str, settings=None) -> dict:
    """Owner-Only Calculation Breakdown Toggle (confirmed Aug 2026) — this
    gate used to be Sales-only ("if role == UserRole.sales"); Admin (Madri)
    saw every cost/breakdown field same as Owner. Tightened to Owner-only
    ("if role != UserRole.owner") per that brief's own explicit framing —
    the full cost/calculation breakdown is now something only the Owner
    can see (behind a toggle, off by default — see the frontend's
    ownerBreakdownVisible), not an Owner+Admin thing. Enforced here,
    server-side, not just hidden in the UI — applies to quote line items
    and live-preview calc dicts wherever either is returned. bags_allowed
    and tile_removal_fee_total stay visible — they're operational/client-
    facing info, not cost data.

    margin_pct: REVISED Aug 2026 (Margin Becomes Owner-Only; Price Gets a
    Colour Signal brief) — explicitly walks back the Owner-Only Calculation
    Breakdown Toggle brief's own earlier "safe to leave visible to
    everyone" call on this one field. Ryno/Madri no longer receive the
    real percentage anywhere, full stop — back in this strip list, gated
    the same as every other cost/breakdown field. margin_band (computed
    above, from margin_pct, BEFORE margin_pct itself gets stripped) is
    the deliberate replacement — sent to every role always, including
    Sales, so the colour signal still works without the real number ever
    leaving the server for a non-Owner account. settings is optional only
    so a caller that hasn't been threaded through yet fails soft (no
    band computed, margin_pct still correctly stripped) rather than
    crashing outright — every real call site below does pass it.

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
    line_item = dict(line_item)
    if settings is not None:
        band = margin_band_for(line_item.get("margin_pct"), settings)
        line_item["margin_band"] = band
        # Low Margin Accountability (confirmed Aug 2026, same brief) — a
        # soft prompt, not a hard block (Burgert's own explicit choice):
        # the line already saved either way, this just tells every role
        # (sales staff are the ones actually entering the reason, most
        # of the time) whether THIS line is currently red with no
        # explanation on file yet, so the frontend knows to show the
        # prompt. False for a live preview line missing the
        # low_margin_reason key entirely (dict.get -> None), same as a
        # real saved line that's never had one recorded — both correctly
        # need prompting.
        line_item["needs_low_margin_reason"] = bool(band == "red" and not line_item.get("low_margin_reason"))
    if role != UserRole.owner:
        for field in (
            "unit_cost", "glue_cost_total",
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
            # Carpet Calculators (confirmed Aug 2026) — same class as
            # glue_cost_total above: real internal cost-composition
            # figures, never broken out as their own visible line to the
            # client (all four — material/gripper/underfelt/cutting-fee/
            # glue — get folded into ONE marked-up line_total/unit_price
            # the client sees), so all three genuinely need stripping,
            # not just documenting a gap for later.
            "gripper_cost_total", "underfelt_cost_total", "cutting_fee_total",
            # Owner-Only Calculation Breakdown Toggle (confirmed Aug 2026)
            # — new fields added to calculate_carpet_line()'s own return
            # dict specifically so a real breakdown (material cost,
            # subtotal before markup, markup multiplier) could be shown
            # to the Owner at all; same "full working" class as everything
            # else in this list, so they need the exact same gate, not a
            # separate one.
            "material_cost_total", "subtotal", "markup_multiplier",
            # Margin Becomes Owner-Only (confirmed Aug 2026) — see this
            # function's own docstring above for why this is back here.
            "margin_pct",
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


@app.get("/price-book/floor-prep", response_model=List[FloorPrepProduct])
def list_floor_prep(tenant_id: str = Depends(get_current_tenant)):
    """Confirmed Aug 2026, Screed Calculator: Extra Rooms brief — same
    plain listing pattern as flooring/blinds/trims above. Mutations for
    this category go through the Supplier Console's existing generic
    commit endpoint (ENTITY_TYPE_MODELS below), same as every other
    product type — no separate POST/PUT/DELETE needed here."""
    with Session(engine) as session:
        return session.exec(select(FloorPrepProduct).where(FloorPrepProduct.tenant_id == tenant_id)).all()


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

def _trusted_tester_usernames(session: Session, tenant_id: str) -> dict:
    """Trusted Tester Accounts brief (confirmed Aug 2026) — the ONE
    structural place this app decides "is this record test data," used
    identically everywhere that decision matters: KPI exclusion
    (analytics_overview below) and display labeling (Order Index,
    client list). Derived from the CURRENT set of trusted_tester-role
    users, never a stored per-record flag — same "derive, don't
    duplicate state that could drift" principle used throughout this
    codebase, and exactly what the brief itself asks for: "a single,
    structural rule applied at the data-access layer... not a filter
    added by hand to each query individually" — the recurring
    delete-cascade bug fixed earlier today is the explicit reason the
    brief gives for insisting on this, and this follows the same
    lesson. Returns {username: display_name} — the display name feeds
    the "TEST — [name]" badge directly; the dict itself doubles as the
    membership set every caller actually needs (`username not in
    result`). Tiny result set (a handful of users at most) — called
    fresh per request, no caching needed at this scale."""
    return {u.username: u.display_name for u in session.exec(
        select(User).where(User.tenant_id == tenant_id, User.role == UserRole.trusted_tester)
    ).all()}


@app.get("/analytics/overview")
def analytics_overview(role: str = Depends(get_current_role), tenant_id: str = Depends(get_current_tenant)):
    """
    Business Overview data. Deliberately built from data that already
    exists — no new pricing logic here, just querying quotes/lines that
    are already being saved. Confirmed Aug 2026: conversion rate excludes
    still-open quotes (draft/sent) from the denominator — a quote that
    hasn't been decided yet isn't a loss, so counting it as one would
    understate the real win rate. "Won" = accepted, invoiced, or paid.
    "Lost" = declined only.

    Trusted Tester Accounts brief (confirmed Aug 2026): explicitly
    blocked for trusted_tester specifically — this endpoint had NO role
    restriction at all before this (Sales could already reach it via a
    direct API call despite the frontend hiding the tile; that
    pre-existing gap is untouched here, out of this brief's scope —
    this only ADDS a new exclusion for trusted_tester, never removes
    existing access from Owner/Admin/Sales). Every figure below also
    excludes quotes created by a Trusted Tester account entirely, for
    every role that CAN see this dashboard — a family member's test
    quote must never inflate real Won Value even from Burgert's own
    view of it.
    """
    if role == UserRole.trusted_tester:
        raise HTTPException(403, "Business Overview Dashboard is not available on a Trusted Tester account.")
    with Session(engine) as session:
        tt_usernames = _trusted_tester_usernames(session, tenant_id)
        # Price Check (confirmed Aug 2026, New Quote Screen brief §3) —
        # "must not affect Order Index counts, Needs Attention, or any
        # dashboard KPI" — excluded at the source here, same as
        # list_quotes(), rather than trying to filter it back out of
        # every downstream figure individually. Trusted Tester quotes
        # excluded the same way, same reasoning, right alongside it.
        quotes = session.exec(select(Quote).where(Quote.tenant_id == tenant_id, Quote.is_price_check == False)).all()  # noqa: E712
        if tt_usernames:
            quotes = [q for q in quotes if q.sales_owner not in tt_usernames]
        lines = session.exec(select(QuoteLineItem).where(QuoteLineItem.tenant_id == tenant_id)).all()
        VAT_PCT = get_settings(session, tenant_id).vat_pct

        # BUG FOUND AND FIXED (confirmed Aug 2026, "Won Value Uses Wrong
        # Field" brief): value_by_quote used to be the raw sum of each
        # quote's line_total values — a pre-VAT, pre-discount subtotal
        # that also never reflects a Manual Override (which lives on
        # Quote.manual_override_total_incl_vat, a QUOTE-level field —
        # completely separate from the individual QuoteLineItem rows,
        # which keep their own original calculated values even when the
        # quote's total is overridden). Robert Aspeling's job (J-0001)
        # showed R19,315.87 here — the ex-VAT subtotal from BEFORE his
        # real, agreed R22,213.08 total was entered via Manual Override
        # — on the Won card, By Branch, By Sales Owner, and folded into
        # Conversion (by value) too, since all four read from this same
        # dict. Now built from each quote's REAL current total_incl_vat,
        # via the exact same _quote_totals() shared helper get_quote()/
        # list_quotes()/get_client_quotes() already use — this can never
        # independently drift from what those screens show again, and
        # correctly respects Manual Override, discount, transport levy,
        # and VAT, not a raw line-item subtotal.
        subtotal_by_quote = {}
        lines_by_quote = {}
        for l in lines:
            subtotal_by_quote[l.quote_id] = subtotal_by_quote.get(l.quote_id, 0.0) + l.line_total
            lines_by_quote.setdefault(l.quote_id, []).append(l)
        value_by_quote = {}
        # profit_by_quote (confirmed Aug 2026, Dashboard: Today/Monthly
        # Sales & Profit + Weekly Graph brief §4 — "must use... real
        # margin/GP data... same standard as Won Value"): real cost via
        # line_real_cost(), the same shared helper the commission engine
        # uses (never reimplemented inline — its own docstring's explicit
        # warning), subtracted from total_ex_vat rather than the raw
        # line-item subtotal — total_ex_vat already correctly backs the
        # ex-VAT figure out of a Manual Override when one is set (see
        # _quote_totals() above), so a job with an overridden sell price
        # still shows its real margin against what was actually charged,
        # not the original calculated price.
        profit_by_quote = {}
        for q in quotes:
            subtotal_ex_vat = subtotal_by_quote.get(q.id, 0.0) + q.transport_levy
            totals = _quote_totals(subtotal_ex_vat, q, VAT_PCT)
            value_by_quote[q.id] = totals["total_incl_vat"]
            real_cost = sum(line_real_cost(l) for l in lines_by_quote.get(q.id, []))
            profit_by_quote[q.id] = totals["total_ex_vat"] - real_cost

        # Real gap found and fixed (confirmed Aug 2026, Order Index / Job
        # Workflow Redesign brief): this used to test the legacy
        # Quote.status string — but the new workflow action endpoints
        # never touch that field, only workflow_status/accepted_at/
        # declined_at. accepted_at/declined_at being set are the real,
        # permanent markers of "won"/"lost" (confirmed directly — the
        # whole reason accepted_at exists at all, per the architecture
        # proposal's §3), so this now tests those instead of a status
        # string nothing new sets.
        def summarize(quote_list):
            won = [q for q in quote_list if q.accepted_at is not None]
            lost = [q for q in quote_list if q.declined_at is not None]
            open_ = [q for q in quote_list if q.accepted_at is None and q.declined_at is None]
            decided = won + lost
            won_value = sum(value_by_quote.get(q.id, 0.0) for q in won)
            lost_value = sum(value_by_quote.get(q.id, 0.0) for q in lost)
            open_value = sum(value_by_quote.get(q.id, 0.0) for q in open_)
            decided_value = won_value + lost_value
            total_value = won_value + lost_value + open_value
            # Total-Quotes-Won primary metric (confirmed Aug 2026,
            # "Make Total-Quotes-Won the Primary Conversion Figure"
            # brief — follow-up to the Sample Size brief): even with a
            # "(N decided)" caveat attached, a bare decided-only rate
            # still reads as misleadingly high on a small sample (e.g.
            # 100% off 1-of-1 decided, while 6 others are still open).
            # These are AGAINST EVERY QUOTE, not just decided ones —
            # the frontend now shows these as the PRIMARY figure and
            # the existing decided-only rates above as the secondary/
            # smaller one.
            #
            # total_value (confirmed Aug 2026, Dashboard: Total Quote
            # Value per Branch brief) — every quote regardless of state
            # (open + won + declined), same real total_incl_vat basis
            # (respecting Manual Override) as every other figure here —
            # pipeline SIZE, distinct from won_value (pipeline CONVERTED).
            return {
                "total_quotes": len(quote_list),
                "open_quotes": len(open_),
                "open_value": round(open_value, 2),
                "won_quotes": len(won),
                "won_value": round(won_value, 2),
                "lost_quotes": len(lost),
                "total_value": round(total_value, 2),
                "conversion_rate_by_count": round(len(won) / len(decided), 4) if decided else None,
                "conversion_rate_by_value": round(won_value / decided_value, 4) if decided_value else None,
                "conversion_rate_by_count_of_total": round(len(won) / len(quote_list), 4) if quote_list else None,
                "conversion_rate_by_value_of_total": round(won_value / total_value, 4) if total_value else None,
            }

        by_branch = {}
        for branch in set(q.branch for q in quotes):
            by_branch[branch] = summarize([q for q in quotes if q.branch == branch])

        by_rep = {}
        for rep in set(q.sales_owner for q in quotes):
            by_rep[rep] = summarize([q for q in quotes if q.sales_owner == rep])

        # ---------- Today/Monthly Sales & Profit + Weekly Graph
        # (confirmed Aug 2026) ----------
        # Date basis confirmed: ACCEPTANCE date (accepted_at, when a
        # quote became Won) — consistent with Won Value elsewhere on
        # this dashboard, not creation or completion date. A quote only
        # ever has ONE accepted_at, set once (models.py) and never
        # reassigned, so this can't double-count.
        #
        # Bucketed in SAST (UTC+2, no DST — South Africa has never
        # observed DST), not raw UTC: every timestamp in this app is
        # stored as naive UTC (datetime.utcnow() throughout), so a job
        # accepted at, say, 00:30 SAST (22:30 UTC the PREVIOUS day)
        # would silently land in "yesterday" on a UTC-boundary "today"
        # card — exactly the kind of few-hours-off error someone
        # checking this dashboard first thing in the morning would
        # actually hit. Fixed offset rather than zoneinfo — simpler,
        # and correct here specifically because SA has no DST to track.
        SAST_OFFSET = timedelta(hours=2)
        today_sast = (datetime.utcnow() + SAST_OFFSET).date()
        month_start_sast = today_sast.replace(day=1)
        monday_sast = today_sast - timedelta(days=today_sast.weekday())  # Monday=0

        def accepted_date_sast(q):
            return (q.accepted_at + SAST_OFFSET).date() if q.accepted_at else None

        won_quotes = [q for q in quotes if q.accepted_at is not None]

        def sales_profit_for(quote_subset):
            return {
                "sales": round(sum(value_by_quote.get(q.id, 0.0) for q in quote_subset), 2),
                "profit": round(sum(profit_by_quote.get(q.id, 0.0) for q in quote_subset), 2),
            }

        today_quotes = [q for q in won_quotes if accepted_date_sast(q) == today_sast]
        month_quotes = [q for q in won_quotes if accepted_date_sast(q) is not None and month_start_sast <= accepted_date_sast(q) <= today_sast]

        weekly_graph = []
        for i in range(7):
            day = monday_sast + timedelta(days=i)
            if day > today_sast:
                break  # brief §3: Monday-to-today, not the rest of the week in advance
            day_quotes = [q for q in won_quotes if accepted_date_sast(q) == day]
            day_figures = sales_profit_for(day_quotes)
            weekly_graph.append({
                "date": day.isoformat(),
                "label": day.strftime("%a"),  # Mon/Tue/... — short, locale-independent enough for a day-of-week axis label
                **day_figures,
            })

        return {
            "overall": summarize(quotes),
            "by_branch": by_branch,
            "by_rep": by_rep,
            "today": sales_profit_for(today_quotes),
            "month": sales_profit_for(month_quotes),
            "weekly_graph": weekly_graph,
        }


# ---------- Supplier Order Sheets (confirmed Aug 2026) ----------
# Internal/supplier-facing procurement documents, separate from the
# client-facing quote/invoice — what to actually send for a job, at
# Burgert's real cost, never the client's sell price. Manual trigger
# only (brief §2) — never generated automatically at any status change.

# Known supplier-name variants that refer to the SAME real supplier,
# entered inconsistently in the price book over time (confirmed with
# Burgert, Job Workflow Design Proposal Phase 1, Aug 2026 — production
# data itself proved this: flooring-material products use both "Azura"
# and "Azura Distributors", screed products use "Azura Distributors
# (iTe)", and FloorPrepProduct uses "Azura" — three different exact
# strings for one real company). Used ONLY to decide whether two
# categories should merge onto one Order Sheet — never changes what's
# actually displayed, printed, or stored; the sheet keeps whichever
# real supplier string that category's own product actually has. A
# narrow data-normalization step, not a hardcoded assumption about
# which supplier matters — extend this list if another supplier's
# price book entries are ever found to have the same inconsistency.
_SUPPLIER_ALIAS_GROUPS = [
    {"Azura", "Azura Distributors", "Azura Distributors (iTe)"},
]


def _merge_supplier_key(supplier: str) -> str:
    for group in _SUPPLIER_ALIAS_GROUPS:
        if supplier in group:
            return sorted(group)[0]   # stable canonical key regardless of which alias matched
    return supplier


def _next_order_number(session: Session, tenant_id: str) -> str:
    """Sequential O-0001 format, tenant-wide, never reused — same
    pattern/reasoning as _next_job_number() above."""
    existing = session.exec(select(OrderSheet.order_number).where(OrderSheet.tenant_id == tenant_id)).all()
    next_seq = 1
    for on in existing:
        try:
            next_seq = max(next_seq, int(on.split("-")[-1]) + 1)
        except (ValueError, AttributeError):
            pass
    return f"O-{next_seq:04d}"


def _floor_prep_supplier(session: Session, tenant_id: str) -> str:
    """Job Workflow Design Proposal, Phase 1 (confirmed Aug 2026) —
    resolves the REAL current floor-prep supplier from price-book data
    (FloorPrepProduct.supplier, a genuine Console-editable field) rather
    than a hardcoded "Azura" literal. Replaces the old fixed constant
    that generate_order_sheets() used to compare every flooring
    supplier against — the confirmed real mechanism behind J-0002
    showing two separate Azura sheets instead of one merged sheet:
    that comparison could silently fail to merge even when the real
    supplier genuinely was Azura, and would never adapt if floor-prep
    sourcing ever changed.

    A screed QuoteLineItem does not currently store which specific
    FloorPrepProduct its bags_allowed figure was calculated from (only
    the resulting bag count/cost, confirmed by reading
    calculate_flooring_line() directly) — so this can't yet resolve a
    TRUE per-line supplier. It resolves the majority supplier across
    the tenant's current FloorPrepProduct rows instead, which matches
    every real row today (all 11 are Azura) and, unlike the literal it
    replaces, updates automatically if that ever changes — a known,
    intentionally scoped limitation flagged in the proposal, not a
    silent gap. Falls back to "Azura" only if the price book has no
    FloorPrepProduct rows at all yet, matching the seed-data default so
    a fresh/empty price book never breaks order sheet generation."""
    products = session.exec(select(FloorPrepProduct).where(FloorPrepProduct.tenant_id == tenant_id)).all()
    if not products:
        return "Azura"
    from collections import Counter
    return Counter(p.supplier for p in products if p.supplier).most_common(1)[0][0]


@app.post("/quotes/{quote_id}/generate-order-sheets")
def generate_order_sheets(quote_id: int, tenant_id: str = Depends(get_current_tenant), username: str = Depends(get_current_username)):
    """Splitting/merging rule (Job Workflow Design Proposal Phase 1,
    confirmed Aug 2026 — REPLACES the old hardcoded "if supplier ==
    'Azura'" special case): every procurable category's line items are
    grouped by their REAL resolved supplier (FlooringProduct.supplier
    for flooring material; _floor_prep_supplier() above for floor-prep)
    — any supplier covering 2+ categories on this job gets ONE combined
    Order Sheet, never a hardcoded company check. This is a general
    rule keyed purely on matching supplier strings, so it keeps working
    correctly if which suppliers cover which categories ever changes,
    with zero code change needed. Blinds is explicitly out of scope for
    this phase — flooring gets built and proven first.

    Trims and Skirting are explicitly out of scope (brief §1, Burgert
    orders those separately in bulk direct from Supertrim) —
    category=="trim"/"skirting" lines are never even looked at here.

    Stairwell vinyl (confirmed Aug 2026, Full Real-Browser Walkthrough &
    Audit) is IN scope, unlike Blinds/Trim/Skirting above — it's real,
    orderable material from the same FlooringProduct/supplier as a normal
    Flooring line, and was only ever missing here because Stairwell
    didn't exist as its own quote category when this function was first
    written. See stairwell_material_line_data() below.

    Floor-prep sheet content — confirmed directly with Burgert:
    generated from each screed line's own already-calculated
    bags_allowed/compound_cost_total, plus any material line's own
    glue_units_needed/glue_cost_total — all four are real, structured,
    already-stored fields (calculate_flooring_line(), calculations.py),
    unlike the separate "Extra Rooms" misc lines, which are free text
    and can't be reliably parsed into product/quantity — those are left
    for Burgert to add by hand via this sheet's own editable-line
    mechanism (brief §5), never guessed at here.

    Pricing rule (brief §4) — Azura flooring gets the real discounted
    cost already baked into unit_cost by calculate_flooring_line()
    (trade_discount_pct applied there); Azura floor-prep/bags NEVER get
    a discount applied anywhere in that same calculation (bag_cost is a
    flat, already-net figure) — both already correctly true of the
    stored values this reads, confirmed by reading calculations.py
    directly before writing this, not re-implemented or overridden
    here.

    Order Sheet Corrections (confirmed Aug 2026, real feedback on
    generated Order O-0001) — three fixes to material_line_data() and
    the floor-prep block below:
    §1 flooring must order in BOXES (wastage included, rounded up),
       never m² — the real bug on O-0001 was an older quote line from
       before boxes_needed was reliably populated, silently falling
       back to the m² display.
    §2 glue removed entirely — drawn from existing stock, never
       ordered per job, on every floor-prep sheet, not just this job.
    §3+§4 discount breakdown (pre-discount price/box, discount rate,
       post-discount cost/box) now stored per line — computed straight
       from the FlooringProduct record (base_cost_ex_vat/
       trade_discount_pct/m2_per_pack), the exact same basis
       calculate_flooring_line() itself uses, so this can never
       disagree with what the quote was actually priced from. A
       floor-prep line gets an explicit discount_pct of 0.0 (not None)
       so the frontend can show "No discount" in visible contrast to a
       flooring line's real rate.

    Duplicate fix (confirmed Aug 2026, Order Sheets UX: Duplicate Bug +
    Delete Option + Prominent Placement + Real Preview brief §1) —
    REVERSES the "deliberately not idempotent" decision above, which
    is exactly what let O-0001/O-0002 (two sheets, same job, same
    supplier, same category) happen: Burgert pressed Generate once,
    got no visible confirmation it worked (root cause was really
    Section 3's missing preview, but the guard here is still required
    regardless — a double-click or re-visiting this page can trigger
    the same thing even once that's fixed), pressed it again assuming
    the first attempt failed, and got a genuine duplicate. Now checks
    for an existing "draft" (not yet placed/finalized) sheet for this
    exact job+supplier+sheet_type combination before creating a new
    one — if found, that existing sheet is returned instead of a fresh
    duplicate. A sheet already marked "placed" (see the new finalize
    endpoint below) is NOT treated as blocking a new one — the
    materials for THAT sheet were genuinely already ordered, so a
    fresh sheet next time is a real re-order, not an accident."""
    with Session(engine) as session:
        quote = get_or_404(session, Quote, quote_id, tenant_id, "Quote")
        lines = session.exec(select(QuoteLineItem).where(QuoteLineItem.quote_id == quote_id, QuoteLineItem.tenant_id == tenant_id)).all()

        # Carpet Calculators (confirmed Aug 2026) — the three continuous-
        # roll carpet types (Tufted/Needlepunch Broadloom, Cushion Vinyl)
        # are deliberately excluded from material_lines below and handled
        # by their own carpet_roll_lines loop further down: they have no
        # product.m2_per_pack at all (roll_width_m instead), so
        # material_line_data()'s box-based fallback would technically run
        # but show m² instead of the LM a supplier actually sells this by
        # — kept out entirely rather than silently relying on a fallback
        # path that wasn't written with this shape in mind. NEXBAC 920
        # tiles (carpet_category=="carpet_tile") are NOT excluded — they
        # genuinely do have m2_per_pack set and belong in the exact same
        # box-based path as any Vinyl tile, unchanged.
        # Category-Aware Contracts, Fix #1 (confirmed Aug 2026, Live
        # Consistency Monitor brief — "sequence directly alongside") —
        # CARPET_ROLL_CATEGORIES is now the one module-level constant
        # (derived from CARPET_ONLY_CATEGORIES, defined further down
        # this file — a real module-level name by the time any request
        # actually reaches this function) — no longer a second,
        # independently hand-typed tuple living only in this function.
        material_lines = [l for l in lines if l.category == "flooring" and l.flooring_pricing_type != "screed" and l.carpet_category not in CARPET_ROLL_CATEGORIES]
        screed_lines = [l for l in lines if l.category == "flooring" and l.flooring_pricing_type == "screed"]
        carpet_roll_lines = [l for l in lines if l.carpet_category in CARPET_ROLL_CATEGORIES]
        # Stairwell vinyl (confirmed Aug 2026, Full Real-Browser Walkthrough
        # & Audit — real gap, not a deliberate exclusion like Blinds/Trim
        # above): a Stairwell line's own vinyl (category=="stairwell",
        # product_id is the vinyl product, same FlooringProduct table/
        # same supplier as a normal Flooring line) was never looked at
        # here at all — simply never added when Stairwell became its own
        # quote category, well after this function was first written.
        # Real, confirmed consequence: neither the generated Order Sheet
        # nor the Job Card (jobCardMaterialsHtml(), order-index.js — reads
        # order_sheets directly, no logic of its own) ever showed a
        # stairwell job's vinyl requirement, so an installer could be sent
        # out without it ever having been ordered or loaded, with nothing
        # anywhere flagging that it was needed.
        stairwell_lines = [l for l in lines if l.category == "stairwell"]

        floor_prep_lines_data = []
        for l in screed_lines:
            if l.bags_allowed:
                unit_cost = (l.compound_cost_total / l.bags_allowed) if l.bags_allowed else 0.0
                floor_prep_lines_data.append({
                    "product_name": f"Screed levelling compound — {l.product_name}",
                    "colour": "", "quantity": float(l.bags_allowed), "unit": "bags", "unit_cost": round(unit_cost, 2),
                    "pre_discount_unit_cost": round(unit_cost, 2), "discount_pct": 0.0,
                })
        # Glue line removed entirely (Order Sheet Corrections brief §2)
        # — glue is drawn from existing stock, not ordered per job, on
        # every floor-prep sheet, not just this one. The block that
        # used to append a "Glue — for {product}" line here is gone,
        # not conditionally skipped.

        def material_line_data(l):
            product = session.get(FlooringProduct, l.product_id)
            if not product or not product.m2_per_pack:
                # No real product record / no box size on file to
                # compute against — genuinely nothing to order by boxes
                # with, so this falls back to the m² basis rather than
                # a fabricated box count.
                return {"product_name": l.product_name, "colour": l.colour, "quantity": l.quantity_m2 or 0.0, "unit": "m²", "unit_cost": round(l.unit_cost or 0.0, 2), "pre_discount_unit_cost": None, "discount_pct": None}
            # §1 — prefer the box count already stored on the quote
            # line (calculate_flooring_line(), calculations.py — frozen
            # at the wastage % actually used when this quote was
            # priced, confirmed with Burgert as the right basis over a
            # flat company-wide 8%); only recompute from the product's
            # CURRENT wastage_pct as a fallback for an older line from
            # before this was reliably populated, so it still orders in
            # boxes rather than silently falling back to m² — the exact
            # bug reported on O-0001.
            if l.boxes_needed:
                boxes = l.boxes_needed
            elif l.quantity_m2:
                boxes = math.ceil((l.quantity_m2 * (1 + product.wastage_pct)) / product.m2_per_pack)
            else:
                boxes = 0
            # §3+§4 — discount breakdown, same basis
            # calculate_flooring_line() itself uses for net_cost_per_box.
            pre_discount_cost_per_box = round(product.base_cost_ex_vat * product.m2_per_pack, 2)
            discount_pct = product.trade_discount_pct
            net_cost_per_box = round(pre_discount_cost_per_box * (1 - discount_pct), 2)
            return {
                "product_name": l.product_name, "colour": l.colour, "quantity": float(boxes), "unit": "boxes",
                "unit_cost": net_cost_per_box, "pre_discount_unit_cost": pre_discount_cost_per_box, "discount_pct": discount_pct,
            }

        def stairwell_material_line_data(l):
            # Mirrors material_line_data() above — same box-rounding and
            # discount-breakdown basis, applied to a stairwell line's own
            # vinyl (product_id) instead of a normal Flooring line's.
            # l.boxes_needed is the stair-tread vinyl only (calculated at
            # add/edit time, calculate_stairwell_line() via
            # _compute_stairwell_calc()); a landing (if any) is priced via
            # a SEPARATE calculate_flooring_line() call folded into this
            # same line's totals (models.py's own comment on
            # landing_area_m2) but never got its own boxes_needed
            # persisted anywhere, so it's recomputed here the same
            # fallback way material_line_data() already does for an older
            # flooring line missing boxes_needed — both draw from the same
            # vinyl product, so one combined box count for the whole line
            # (stairs + landing) is correct, not two separate rows.
            product = session.get(FlooringProduct, l.product_id)
            if not product or not product.m2_per_pack:
                return {"product_name": l.product_name, "colour": l.colour, "quantity": float(l.boxes_needed or 0), "unit": "boxes", "unit_cost": 0.0, "pre_discount_unit_cost": None, "discount_pct": None}
            boxes = l.boxes_needed or 0
            if l.landing_area_m2:
                boxes += math.ceil((l.landing_area_m2 * (1 + product.wastage_pct)) / product.m2_per_pack)
            pre_discount_cost_per_box = round(product.base_cost_ex_vat * product.m2_per_pack, 2)
            discount_pct = product.trade_discount_pct
            net_cost_per_box = round(pre_discount_cost_per_box * (1 - discount_pct), 2)
            return {
                "product_name": f"{product.product_name} (stairwell vinyl)", "colour": l.colour, "quantity": float(boxes), "unit": "boxes",
                "unit_cost": net_cost_per_box, "pre_discount_unit_cost": pre_discount_cost_per_box, "discount_pct": discount_pct,
            }

        def carpet_roll_line_data(l):
            # Carpet Calculators (confirmed Aug 2026) — the roll itself
            # (Tufted/Needlepunch Broadloom, Cushion Vinyl) still needs a
            # real Order Sheet line, per the brief's explicit instruction
            # ("the carpet/broadloom roll itself still needs one") — shown
            # in LM (l.quantity_lm, the exact figure Burgert entered),
            # never m², since that's the unit a supplier actually sells
            # this by. Grippers/underfelt/glue are deliberately absent
            # from this — they're confirmed stock items, never ordered per
            # job, same "drawn from stock" rule Vinyl's own glue has
            # always followed (see calculate_carpet_line()'s own comment).
            product = session.get(FlooringProduct, l.product_id)
            if not product:
                return {"product_name": l.product_name, "colour": l.colour, "quantity": float(l.quantity_lm or 0), "unit": "LM", "unit_cost": 0.0, "pre_discount_unit_cost": None, "discount_pct": None}
            pre_discount_cost_per_lm = round(product.base_cost_ex_vat * (product.roll_width_m or 1.0), 2)
            discount_pct = product.trade_discount_pct
            net_cost_per_lm = round(pre_discount_cost_per_lm * (1 - discount_pct), 2)
            return {
                "product_name": product.product_name, "colour": l.colour, "quantity": float(l.quantity_lm or 0), "unit": "LM",
                "unit_cost": net_cost_per_lm, "pre_discount_unit_cost": pre_discount_cost_per_lm, "discount_pct": discount_pct,
            }

        created_sheets = []
        reused_sheets = []

        def make_sheet(supplier, sheet_type, line_items_data):
            # Matched by merge key, not exact string (confirmed Aug 2026,
            # Phase 1) — otherwise re-generating for a job whose lines
            # happen to iterate in a different order this time (no
            # explicit ORDER BY on `lines` above) could pick a DIFFERENT
            # alias string as display_supplier and fail to find the
            # existing draft sheet, creating a real duplicate — exactly
            # the class of bug this dedup check exists to prevent.
            candidates = session.exec(select(OrderSheet).where(
                OrderSheet.tenant_id == tenant_id, OrderSheet.quote_id == quote_id,
                OrderSheet.sheet_type == sheet_type, OrderSheet.status == "draft",
            )).all()
            existing = next((c for c in candidates if _merge_supplier_key(c.supplier) == _merge_supplier_key(supplier)), None)
            if existing:
                reused_sheets.append(existing)
                return
            sheet = OrderSheet(tenant_id=tenant_id, quote_id=quote_id, order_number=_next_order_number(session, tenant_id),
                                supplier=supplier, sheet_type=sheet_type, created_by=username)
            session.add(sheet)
            session.commit()
            session.refresh(sheet)
            for d in line_items_data:
                session.add(OrderSheetLine(tenant_id=tenant_id, order_sheet_id=sheet.id, **d))
            session.commit()
            created_sheets.append(sheet)

        # General supplier-merge grouping (Job Workflow Design Proposal
        # Phase 1) — every category's line items land in ONE dict, keyed
        # by real resolved supplier (through _merge_supplier_key(), which
        # only normalizes KNOWN naming inconsistencies for the merge
        # decision — see its own docstring). Any supplier that ends up
        # with items from more than one category naturally gets a single
        # combined sheet below, with no per-supplier special case at
        # all — this is what makes the rule general rather than
        # hardcoded, and it's also what keeps the pre-existing
        # multi-supplier-within-flooring case (material_by_supplier's
        # old job) working unchanged: two genuinely different flooring
        # brands on one job still land in two different dict entries.
        # Each entry remembers the REAL supplier string first seen for
        # it — that's what actually gets stored/printed on the sheet,
        # never the normalized key.
        lines_by_merge_key: dict = {}   # merge_key -> {"display_supplier": str, "items": [dict, ...]}
        for l in material_lines:
            product = session.get(FlooringProduct, l.product_id)
            supplier = product.supplier if product else "Unknown supplier"
            entry = lines_by_merge_key.setdefault(_merge_supplier_key(supplier), {"display_supplier": supplier, "items": []})
            entry["items"].append(material_line_data(l))

        # Stairwell vinyl merges into the same supplier grouping as any
        # other flooring material — a stairwell job whose vinyl happens to
        # come from the same supplier as the rest of the job (the common
        # case) lands on the SAME combined sheet, not a separate one.
        for l in stairwell_lines:
            product = session.get(FlooringProduct, l.product_id)
            supplier = product.supplier if product else "Unknown supplier"
            entry = lines_by_merge_key.setdefault(_merge_supplier_key(supplier), {"display_supplier": supplier, "items": []})
            entry["items"].append(stairwell_material_line_data(l))

        # Carpet rolls (Tufted/Needlepunch Broadloom, Cushion Vinyl) —
        # same supplier-merge grouping as everything else. NEXBAC 920
        # tiles need no separate loop at all: they're already inside
        # material_lines above, completely unchanged.
        for l in carpet_roll_lines:
            product = session.get(FlooringProduct, l.product_id)
            supplier = product.supplier if product else "Unknown supplier"
            entry = lines_by_merge_key.setdefault(_merge_supplier_key(supplier), {"display_supplier": supplier, "items": []})
            entry["items"].append(carpet_roll_line_data(l))

        floor_prep_merge_key = None
        if floor_prep_lines_data:
            floor_prep_supplier = _floor_prep_supplier(session, tenant_id)
            floor_prep_merge_key = _merge_supplier_key(floor_prep_supplier)
            entry = lines_by_merge_key.setdefault(floor_prep_merge_key, {"display_supplier": floor_prep_supplier, "items": []})
            entry["items"].extend(floor_prep_lines_data)

        for key, entry in lines_by_merge_key.items():
            # sheet_type convention unchanged, generalized: any sheet
            # carrying floor-prep lines stays the freely-editable
            # "floor_prep" type (brief §5) — whether or not flooring
            # lines are merged into it — a flooring-only sheet stays
            # locked "flooring", reflecting the quote exactly.
            sheet_type = "floor_prep" if key == floor_prep_merge_key else "flooring"
            make_sheet(entry["display_supplier"], sheet_type, entry["items"])

        if not created_sheets and not reused_sheets:
            raise HTTPException(400, "This quote has no flooring, screed, or stairwell line items to generate an order sheet from.")

        # generated/reused split (confirmed Aug 2026, brief §1+§4) — the
        # frontend uses this to show an unambiguous result either way:
        # "Generated N new order sheet(s)" vs "Already generated —
        # showing the existing order sheet(s)" vs a mix of both, rather
        # than a single generic success message that can't distinguish
        # "this just worked for the first time" from "this already
        # existed" — exactly the ambiguity that caused the duplicate.
        return {
            "generated": len(created_sheets), "reused": len(reused_sheets),
            "order_sheet_ids": [s.id for s in created_sheets] + [s.id for s in reused_sheets],
        }


@app.get("/quotes/{quote_id}/order-sheets")
def get_order_sheets_for_quote(quote_id: int, tenant_id: str = Depends(get_current_tenant)):
    with Session(engine) as session:
        sheets = session.exec(select(OrderSheet).where(OrderSheet.quote_id == quote_id, OrderSheet.tenant_id == tenant_id).order_by(OrderSheet.created_at)).all()
        tt_usernames = _trusted_tester_usernames(session, tenant_id)
        result = []
        for s in sheets:
            lines = session.exec(select(OrderSheetLine).where(OrderSheetLine.order_sheet_id == s.id, OrderSheetLine.tenant_id == tenant_id)).all()
            d = s.dict()
            d["lines"] = [l.dict() for l in lines]
            # Trusted Tester Accounts brief (confirmed Aug 2026) — same
            # display-only labeling as list_quotes()/list_clients() above.
            d["is_test_data"] = s.created_by in tt_usernames
            d["test_data_label"] = f"TEST — {tt_usernames[s.created_by]}" if s.created_by in tt_usernames else None
            result.append(d)
        return result


@app.get("/order-sheets/{order_sheet_id}")
def get_order_sheet(order_sheet_id: int, tenant_id: str = Depends(get_current_tenant)):
    with Session(engine) as session:
        sheet = get_or_404(session, OrderSheet, order_sheet_id, tenant_id, "Order sheet")
        quote = session.get(Quote, sheet.quote_id)
        lines = session.exec(select(OrderSheetLine).where(OrderSheetLine.order_sheet_id == order_sheet_id, OrderSheetLine.tenant_id == tenant_id)).all()
        d = sheet.dict()
        d["lines"] = [l.dict() for l in lines]
        d["job_number"] = quote.job_number if quote else None
        d["client_name"] = quote.client_name if quote else None
        d["branch"] = quote.branch if quote else None   # folder-flatten pass (confirmed Aug 2026) — the frontend needs this to archive into the right per-branch Dropbox folder (finalizeOrderSheet(), order-index.js)
        return d


@app.post("/order-sheets/{order_sheet_id}/finalize")
def finalize_order_sheet(order_sheet_id: int, tenant_id: str = Depends(get_current_tenant), username: str = Depends(get_current_username)):
    """Order Sheets UX brief §4 (confirmed Aug 2026) — "Executable — a
    clear action to finalize/send/mark the order as placed, once the
    user is satisfied with it." Also the other half of the §1
    duplicate fix: once placed, this sheet no longer blocks a fresh
    one for the same job+supplier+category (generate_order_sheets()
    only reuses a "draft" sheet) — the materials for this one were
    genuinely already ordered, so a new sheet next time is a real
    re-order, not an accidental duplicate."""
    with Session(engine) as session:
        sheet = get_or_404(session, OrderSheet, order_sheet_id, tenant_id, "Order sheet")
        if sheet.status == "placed":
            raise HTTPException(400, "This order sheet is already marked as placed.")
        sheet.status = "placed"
        sheet.placed_at = datetime.utcnow()
        sheet.placed_by = username
        session.add(sheet)
        session.commit()
        session.refresh(sheet)
        return sheet


class OrderSheetLineUpdate(BaseModel):
    quantity: float


@app.put("/order-sheets/{order_sheet_id}/lines/{line_id}")
def update_order_sheet_line(order_sheet_id: int, line_id: int, body: OrderSheetLineUpdate, tenant_id: str = Depends(get_current_tenant)):
    """Quantities amendable on a floor_prep-type sheet (brief §5) —
    enforced here too, not just left to the frontend to hide the
    control: a flooring-type sheet reflects the quote's own line items
    directly and isn't meant to be freely edited."""
    with Session(engine) as session:
        line = get_or_404(session, OrderSheetLine, line_id, tenant_id, "Order sheet line")
        sheet = get_or_404(session, OrderSheet, order_sheet_id, tenant_id, "Order sheet")
        if line.order_sheet_id != order_sheet_id:
            raise HTTPException(404, "Order sheet line not found")
        if sheet.sheet_type != "floor_prep":
            raise HTTPException(400, "Only floor-prep order sheets can have their quantities amended.")
        # Document Action Bar brief (confirmed Aug 2026, resolved with
        # Burgert) — once "placed," an order sheet is locked, same rule
        # as an Invoice after it's been sent: the materials were
        # genuinely already ordered from the supplier, so changing
        # quantities after the fact could silently disagree with what
        # was actually placed. A genuine correction/re-order still goes
        # through generate_order_sheets() as its own fresh sheet
        # (unaffected by this — that path was never blocked by "placed"
        # anyway, confirmed directly with Burgert). Previously only
        # hidden client-side (the UI didn't offer the input once
        # placed) — never actually enforced here, the exact "hidden in
        # the UI, not enforced server-side" gap the standing rule warns
        # against.
        if sheet.status == "placed":
            raise HTTPException(400, "This order sheet is already placed — quantities can no longer be changed. Generate a new order sheet for this job if more materials are needed.")
        line.quantity = body.quantity
        session.add(line)
        session.commit()
        session.refresh(line)
        return line


class OrderSheetLineCreate(BaseModel):
    product_name: str
    colour: str = ""
    quantity: float
    unit: str = ""
    unit_cost: float = 0.0


@app.post("/order-sheets/{order_sheet_id}/lines")
def add_order_sheet_line(order_sheet_id: int, body: OrderSheetLineCreate, tenant_id: str = Depends(get_current_tenant)):
    """Extra free-text misc line on a floor-prep order (brief §5 —
    "an extra tool, an additional consumable not part of the original
    calculated list"). Same floor_prep-only restriction as edits above."""
    with Session(engine) as session:
        sheet = get_or_404(session, OrderSheet, order_sheet_id, tenant_id, "Order sheet")
        if sheet.sheet_type != "floor_prep":
            raise HTTPException(400, "Extra line items can only be added to floor-prep order sheets.")
        if sheet.status == "placed":
            raise HTTPException(400, "This order sheet is already placed — no more items can be added. Generate a new order sheet for this job if more materials are needed.")
        if not body.product_name.strip():
            raise HTTPException(400, "Enter a product/item description first.")
        line = OrderSheetLine(tenant_id=tenant_id, order_sheet_id=order_sheet_id, product_name=body.product_name.strip(),
                               colour=body.colour, quantity=body.quantity, unit=body.unit, unit_cost=body.unit_cost, is_extra=True)
        session.add(line)
        session.commit()
        session.refresh(line)
        return line


@app.delete("/order-sheets/{order_sheet_id}/lines/{line_id}")
def delete_order_sheet_line(order_sheet_id: int, line_id: int, tenant_id: str = Depends(get_current_tenant)):
    with Session(engine) as session:
        line = get_or_404(session, OrderSheetLine, line_id, tenant_id, "Order sheet line")
        sheet = get_or_404(session, OrderSheet, order_sheet_id, tenant_id, "Order sheet")
        if line.order_sheet_id != order_sheet_id:
            raise HTTPException(404, "Order sheet line not found")
        if sheet.status == "placed":
            raise HTTPException(400, "This order sheet is already placed — items can no longer be removed. Generate a new order sheet for this job if needed.")
        session.delete(line)
        session.commit()
        return {"deleted": line_id}


# ---------- Dropbox Document Archive & Backup Layer (confirmed Aug
# 2026, extended to full scope v2 pass — confirmed Aug 2026) ----------
# Scope: no Dropbox access token exists yet, so real uploads stay in
# "pending" until one is set — but everything else (PDF/CSV rendering,
# archive-version tracking, the never-overwrite-history guarantee,
# retry) is real and working today. Wired up for Quotes, Invoices,
# Order Sheets (finalize), and the nightly Order Index CSV snapshot
# (APScheduler — confirmed always-on Render plan, so an in-process
# scheduler is reliable; no separate Render Cron Job service needed).
ARCHIVE_CATEGORY_FOLDER = {
    "Quote": "Quotes", "Invoice": "Invoices", "OrderSheet": "Orders",
    "OrderIndexSnapshot": "Order Index Snapshots",
}   # NOTE (confirmed Aug 2026, folder-flatten pass): these labels are no
    # longer used as real folder names for Quote/Invoice/OrderSheet — see
    # _branch_folder_name() below — this dict now only (a) validates
    # entity_type in archive_document(), and (b) still names the real
    # folder for OrderIndexSnapshot, which stays whole-tenant/branch-
    # independent (a snapshot spans every branch, so a per-branch folder
    # makes no sense for it).
ARCHIVE_FILE_EXTENSION = {"OrderIndexSnapshot": "csv"}   # everything else defaults to "pdf" — see .get() calls below
ARCHIVE_MEDIA_TYPE = {"pdf": "application/pdf", "csv": "text/csv"}


def _branch_folder_name(branch: Optional[str]) -> str:
    """Folder-flatten pass (confirmed Aug 2026) — every Quote/Invoice/
    Order for a branch lands directly in one flat Dropbox folder named
    for that branch (no year/month, no Quotes/Invoices/Orders split —
    the reference/filename itself, e.g. "Q-1042_Smith_v2.pdf", is what
    keeps files findable). Only two real branches exist today
    (gansbaai/hermanus, stored lowercase — see Quote.branch and the
    q_branch/nc_branch <select> options, index.html), so .title() is
    enough to get "Gansbaai"/"Hermanus"; a blank branch (confirmed
    real, not hypothetical — a quote can be saved with branch never
    explicitly set) falls back to "Unassigned" rather than silently
    dropping the file somewhere unfindable or failing the archive
    outright."""
    if not branch or not branch.strip():
        return "Unassigned"
    return branch.strip().title()


def _create_and_upload_archive(session: Session, tenant_id: str, username: str, entity_type: str, entity_id: int,
                                reference: str, file_bytes: bytes, mark_as_accepted: bool = False, branch: Optional[str] = None) -> DocumentArchive:
    """The actual reusable "pipeline" the brief's §2/§11 asks for (§2:
    "reuse the existing... generation... rather than building new
    rendering logic" — this is the shared HALF that follows rendering:
    version tracking, the Dropbox path/filename, the never-overwrite
    guarantee, the accepted-version flag, and the pending/uploaded/
    failed status mapping). Callers are responsible for producing
    file_bytes however is appropriate for their entity_type (PDF via
    render_html_to_pdf() for Quote/Invoice/OrderSheet, plain CSV bytes
    for OrderIndexSnapshot) — this function itself never renders
    anything, only archives and uploads whatever it's given. Extracted
    Aug 2026 (v2 pass) from what used to be archive_document()'s own
    body, once a second real caller (the snapshot job) needed the exact
    same version/upload/status logic — refactor confirmed safe:
    archive_document()'s own behaviour is unchanged, same commit, same
    return shape, same everything, just callable from more than one
    place now instead of copy-pasted a second time.

    dropbox_path is now ALWAYS computed and persisted on the row up
    front (confirmed Aug 2026, folder-flatten pass), regardless of
    whether the upload itself succeeds — previously it stayed None
    until a successful upload, which meant retry_document_archive() had
    to separately RECONSTRUCT the intended path later using whatever
    the CURRENT date happened to be at retry time (a real latent bug:
    retrying in a later month than the row was created would have
    reconstructed the wrong path, back when the path included year/
    month). Now that the path is deterministic from (branch, reference,
    version) alone — no date component at all — computing it once,
    here, and trusting the stored value forever is both simpler and
    correct."""
    version = _next_archive_version(session, tenant_id, entity_type, entity_id)
    ext = ARCHIVE_FILE_EXTENSION.get(entity_type, "pdf")
    safe_reference = re.sub(r"[^A-Za-z0-9_-]", "_", reference)
    # Brief §3 own example: B-1042_Smith_ACCEPTED.pdf — a distinct,
    # findable-by-name filename for the accepted version, not just
    # another _v{N} in the ordinary sequence.
    filename = f"{safe_reference}_ACCEPTED.{ext}" if mark_as_accepted else f"{safe_reference}_v{version}.{ext}"
    folder = ARCHIVE_CATEGORY_FOLDER[entity_type] if entity_type == "OrderIndexSnapshot" else _branch_folder_name(branch)
    dropbox_path = f"/Bolton/{folder}/{filename}"
    if mark_as_accepted:
        # At most one row per document ever carries this flag — unset
        # it on any earlier version before this new one claims it, so
        # "the accepted version" always means exactly one, findable row.
        prior_accepted = session.exec(select(DocumentArchive).where(
            DocumentArchive.tenant_id == tenant_id, DocumentArchive.entity_type == entity_type,
            DocumentArchive.entity_id == entity_id, DocumentArchive.is_accepted_version == True,  # noqa: E712
        )).all()
        for row in prior_accepted:
            row.is_accepted_version = False
            session.add(row)
    archive = DocumentArchive(
        tenant_id=tenant_id, entity_type=entity_type, entity_id=entity_id, version=version,
        reference=reference, pdf_bytes=file_bytes, created_by=username, is_accepted_version=mark_as_accepted,
        dropbox_path=dropbox_path,
    )
    upload_result = dropbox_archive.upload_document(file_bytes, dropbox_path)
    if upload_result["ok"]:
        archive.status = "uploaded"
        archive.dropbox_path = upload_result["path"]
        archive.dropbox_file_id = upload_result["file_id"]
        archive.uploaded_at = datetime.utcnow()
    else:
        # "not configured yet" reads as an expected, known, temporary
        # state (Pending) — a genuine upload error (bad token, network
        # issue, Dropbox itself down) reads as an actual Failed worth
        # Burgert's attention.
        archive.status = "pending" if upload_result.get("not_configured") else "failed"
        archive.failure_reason = upload_result["reason"]
    session.add(archive)
    session.commit()
    session.refresh(archive)
    return archive


def _next_archive_version(session: Session, tenant_id: str, entity_type: str, entity_id: int) -> int:
    existing = session.exec(select(DocumentArchive).where(
        DocumentArchive.tenant_id == tenant_id, DocumentArchive.entity_type == entity_type, DocumentArchive.entity_id == entity_id,
    )).all()
    return len(existing) + 1


class ArchiveDocumentRequest(BaseModel):
    entity_type: str    # "Quote" | "Invoice" | "OrderSheet"
    entity_id: int
    reference: str       # human label for the filename, e.g. "J-0001" or "O-0002"
    html: str            # exactly what buildPrintDocHtml() (shared.js) already produced for on-screen viewing
    css: str = ""
    mark_as_accepted: bool = False   # brief §3 — "preserve the accepted version distinctly"; frontend sets this on the one archive call it makes right after a quote is actually accepted
    branch: Optional[str] = None     # folder-flatten pass (confirmed Aug 2026) — which per-branch Dropbox folder this lands in (_branch_folder_name()); the caller already has the quote's own branch on hand, so it's passed through rather than re-fetched here


@app.post("/documents/archive")
def archive_document(body: ArchiveDocumentRequest, tenant_id: str = Depends(get_current_tenant), username: str = Depends(get_current_username)):
    """Brief §2+§10 — renders the given html/css into a real PDF
    (pdf_render.py) and attempts a Dropbox upload (dropbox_archive.py),
    creating a new, permanent DocumentArchive row either way — never
    silently skipped, and never overwriting a previous version (brief
    §4): version is always next-in-sequence for this entity, and the
    Dropbox path uses mode=add, so a genuine attempt to reuse a path
    fails loudly rather than replacing history.

    Never raises on a failed Dropbox upload (brief §7 — "Dropbox being
    unavailable must NOT prevent Bolton from creating or saving a
    quote, invoice, or order"): the PDF is rendered and stored in
    Bolton's own database regardless, status becomes "failed" with a
    real reason, and this call still returns 200 — the caller (the
    quote/order save flow) was never blocked by Dropbox either way."""
    if body.entity_type not in ARCHIVE_CATEGORY_FOLDER:
        raise HTTPException(400, f"Unknown entity_type '{body.entity_type}' — must be one of {list(ARCHIVE_CATEGORY_FOLDER.keys())}")
    try:
        pdf_bytes = render_html_to_pdf(body.html, body.css)
    except ValueError as e:
        raise HTTPException(500, f"Could not render this document to PDF: {e}")
    with Session(engine) as session:
        archive = _create_and_upload_archive(
            session, tenant_id, username, body.entity_type, body.entity_id,
            body.reference, pdf_bytes, mark_as_accepted=body.mark_as_accepted, branch=body.branch,
        )
        return {
            "id": archive.id, "version": archive.version, "status": archive.status,
            "dropbox_path": archive.dropbox_path, "failure_reason": archive.failure_reason,
        }


@app.get("/documents/archive")
def list_document_archive(entity_type: str, entity_id: int, tenant_id: str = Depends(get_current_tenant)):
    """Full version history for one document, newest first — pdf_bytes
    deliberately excluded from this response (could be sizeable across
    several versions; see the dedicated download endpoint below for
    the actual file)."""
    with Session(engine) as session:
        rows = session.exec(select(DocumentArchive).where(
            DocumentArchive.tenant_id == tenant_id, DocumentArchive.entity_type == entity_type, DocumentArchive.entity_id == entity_id,
        ).order_by(DocumentArchive.version.desc())).all()
        return [{
            "id": a.id, "version": a.version, "reference": a.reference, "status": a.status,
            "dropbox_path": a.dropbox_path, "failure_reason": a.failure_reason, "is_accepted_version": a.is_accepted_version,
            "created_at": a.created_at, "uploaded_at": a.uploaded_at, "created_by": a.created_by,
        } for a in rows]


@app.get("/documents/archive/{archive_id}/download")
def download_archived_document(archive_id: int, tenant_id: str = Depends(get_current_tenant)):
    """Brief §12 "Recovery" test — confirms an archived PDF can
    actually be opened, not merely marked Uploaded in Bolton. Serves
    Bolton's own stored copy directly (not a Dropbox fetch) — this is
    intentionally also the disaster-recovery path if Dropbox itself is
    ever unreachable, not just a convenience."""
    with Session(engine) as session:
        archive = get_or_404(session, DocumentArchive, archive_id, tenant_id, "Archived document")
        ext = ARCHIVE_FILE_EXTENSION.get(archive.entity_type, "pdf")
        return Response(content=archive.pdf_bytes, media_type=ARCHIVE_MEDIA_TYPE[ext],
                         headers={"Content-Disposition": f'inline; filename="{archive.reference}_v{archive.version}.{ext}"'})


@app.post("/documents/archive/{archive_id}/retry")
def retry_document_archive(archive_id: int, tenant_id: str = Depends(get_current_tenant)):
    """Brief §7's own explicit requirement — "allow the system to
    retry." Re-uploads the SAME already-stored pdf_bytes (never
    regenerates from current live data — brief §10's historical-
    pricing-integrity rule) to the SAME dropbox_path this row was
    already assigned, so a retry genuinely resumes this exact version
    rather than silently creating a parallel one."""
    with Session(engine) as session:
        archive = get_or_404(session, DocumentArchive, archive_id, tenant_id, "Archived document")
        if archive.status == "uploaded":
            raise HTTPException(400, "This version is already uploaded — nothing to retry.")
        folder = ARCHIVE_CATEGORY_FOLDER.get(archive.entity_type, archive.entity_type)
        ext = ARCHIVE_FILE_EXTENSION.get(archive.entity_type, "pdf")
        safe_reference = re.sub(r"[^A-Za-z0-9_-]", "_", archive.reference)
        now = datetime.utcnow()
        dropbox_path = archive.dropbox_path or f"/Bolton/{folder}/{now.year}/{now.month:02d}-{now.strftime('%B')}/{safe_reference}_v{archive.version}.{ext}"
        upload_result = dropbox_archive.upload_document(archive.pdf_bytes, dropbox_path)
        if upload_result["ok"]:
            archive.status = "uploaded"
            archive.dropbox_path = upload_result["path"]
            archive.dropbox_file_id = upload_result["file_id"]
            archive.uploaded_at = datetime.utcnow()
            archive.failure_reason = None
        else:
            archive.status = "pending" if upload_result.get("not_configured") else "failed"
            archive.failure_reason = upload_result["reason"]
        session.add(archive)
        session.commit()
        return {"id": archive.id, "status": archive.status, "failure_reason": archive.failure_reason}


def _order_index_snapshot_csv(session: Session, tenant_id: str) -> bytes:
    """Order Index Snapshot (Dropbox Document Archive brief v2, §2 —
    "Archive it as a dated snapshot export... on a nightly schedule,
    rather than trying to trigger it on every row change"). Deliberately
    a SEPARATE, simpler query from list_quotes() (main.py's own Order
    Index endpoint) — that endpoint's role-stripping/search/filter
    params make no sense for an unconditional full-tenant export, and a
    snapshot job has no role context to strip for anyway (it's an
    internal record, not served to any particular logged-in user). Still
    shares the ONE real source of totals math (_quote_totals()) rather
    than a third parallel copy of that calculation — same discipline
    list_quotes()/get_quote() already follow."""
    import csv, io
    VAT_PCT = get_settings(session, tenant_id).vat_pct
    quotes = session.exec(select(Quote).where(Quote.tenant_id == tenant_id, Quote.is_price_check == False)).all()  # noqa: E712
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["job_number", "client_name", "workflow_status", "sales_owner", "branch",
                      "site_address", "total_incl_vat", "deposit_amount", "balance_amount", "created_at"])
    for q in quotes:
        lines = session.exec(select(QuoteLineItem).where(QuoteLineItem.quote_id == q.id, QuoteLineItem.tenant_id == tenant_id)).all()
        subtotal_ex_vat = sum(l.line_total for l in lines) + q.transport_levy
        totals = _quote_totals(subtotal_ex_vat, q, VAT_PCT)
        writer.writerow([
            q.job_number or f"Q-{q.id}", q.client_name, q.workflow_status, q.sales_owner, q.branch,
            q.site_address or "", f"{totals['total_incl_vat']:.2f}", f"{totals['deposit_amount']:.2f}",
            f"{totals['balance_amount']:.2f}", q.created_at.isoformat(),
        ])
    return buf.getvalue().encode("utf-8")


def _run_consistency_checks(session: Session, tenant_id: str) -> list:
    """Live Consistency Monitor (confirmed Aug 2026) — "reuse the exact
    same category-consistency checks already proposed in the Category-
    Aware Contracts brief... do not invent a second set of rules." The
    two checks below ARE that investigation's own two named findings,
    turned into real queries against live data instead of arguments in
    a proposal document — nothing here corrects, writes, or touches a
    single row; it only reads and reports. Returns a plain list of
    {entity_type, entity_id, note} dicts, the exact shape
    FlaggedRecord/flag_record() already expect — the one existing
    "surface something for the Owner to review" mechanism this reuses
    rather than building a second one (see run_consistency_monitor_job()
    below for where these actually get written)."""
    findings = []
    lines = session.exec(select(QuoteLineItem).where(
        QuoteLineItem.tenant_id == tenant_id, QuoteLineItem.category == "flooring",
    )).all()
    products = {p.id: p for p in session.exec(
        select(FlooringProduct).where(FlooringProduct.tenant_id == tenant_id)
    ).all()}

    for line in lines:
        product = products.get(line.product_id)
        # Check 1 — category tag drift (Category-Aware Contracts
        # investigation, "a line's category tag remains correct and
        # consistent across every stage"): re-derives what
        # carpet_category SHOULD be from the line's own real product
        # record, the same way add_flooring_line()/edit_flooring_line()
        # themselves compute it — a real quote line's stored value
        # should never disagree with that unless something along the
        # way went wrong (a bug, or an old row from before a fix
        # shipped) — the one legitimate way these could genuinely
        # differ later (a product's own category edited after this
        # line was saved) is rare enough that a flagged, human-reviewed
        # false positive is a fair trade for catching a real bug.
        if product is not None:
            expected_carpet_category = product.flooring_category if product.flooring_category in CARPET_ONLY_CATEGORIES else None
            if line.carpet_category != expected_carpet_category:
                findings.append({
                    "entity_type": "QuoteLineItem", "entity_id": line.id,
                    "note": f"Category mismatch on Quote #{line.quote_id}: line has carpet_category={line.carpet_category!r}, but its product ({product.product_name!r}) currently says {expected_carpet_category!r}.",
                })
        # Check 2 — wrong formula used (Category-Aware Contracts
        # investigation, "the correct category-specific pricing
        # calculation was used for each line, never a different
        # category's formula" — Finding 2, the real Conqueror
        # demonstration during the Belgotex import): a genuine
        # roll-shaped carpet category (Tufted/Needlepunch Broadloom,
        # Cushion Vinyl) NEVER has boxes_needed set and ALWAYS has
        # quantity_lm set — calculate_carpet_line()'s own real output
        # shape. Either one being wrong means this line was priced
        # through the box-based Flooring formula instead of Carpet's.
        if line.carpet_category in CARPET_ROLL_CATEGORIES:
            if line.quantity_lm is None or line.boxes_needed is not None:
                findings.append({
                    "entity_type": "QuoteLineItem", "entity_id": line.id,
                    "note": f"Wrong formula suspected on Quote #{line.quote_id}: {line.carpet_category} line has quantity_lm={line.quantity_lm!r}, boxes_needed={line.boxes_needed!r} — looks priced as a box product, not a roll.",
                })
    return findings


# Live Consistency Monitor (confirmed Aug 2026) — the literal string
# every system-raised flag is stamped with, so the frontend's own
# "impossible to miss" banner (index.html) can tell a system finding
# apart from a real person's own Flag for Review note without a new
# column — same generic FlaggedRecord shape, one recognizable value.
CONSISTENCY_MONITOR_FLAGGED_BY = "system (consistency monitor)"


def run_consistency_monitor_job():
    """The scheduled job itself (APScheduler, see on_startup() below).
    "Detect and alert only... never attempt to automatically correct,
    modify, or fix any data itself" — this function's only write of any
    kind is a new FlaggedRecord row; it never touches a Quote,
    QuoteLineItem, or FlooringProduct. Runs per-tenant, same pattern as
    the Order Index snapshot/backup jobs already on this scheduler.
    De-duplicated against the SAME open finding re-appearing every
    single day it stays unfixed: an identical, still-unresolved flag
    (same entity_type/entity_id/note) is never re-raised — Burgert
    would otherwise see the exact same Conqueror-shaped complaint
    accumulate one new row per day for however long it sits open."""
    with Session(engine) as session:
        tenant_ids = session.exec(select(Quote.tenant_id).distinct()).all() or [DEFAULT_TENANT_ID]
        for tenant_id in tenant_ids:
            try:
                findings = _run_consistency_checks(session, tenant_id)
                existing_open = session.exec(select(FlaggedRecord).where(
                    FlaggedRecord.tenant_id == tenant_id,
                    FlaggedRecord.flagged_by == CONSISTENCY_MONITOR_FLAGGED_BY,
                    FlaggedRecord.resolved == False,  # noqa: E712
                )).all()
                already_flagged = {(f.entity_type, f.entity_id, f.note) for f in existing_open}
                new_count = 0
                for finding in findings:
                    key = (finding["entity_type"], finding["entity_id"], finding["note"])
                    if key in already_flagged:
                        continue
                    session.add(FlaggedRecord(
                        tenant_id=tenant_id, entity_type=finding["entity_type"], entity_id=finding["entity_id"],
                        note=finding["note"], flagged_by=CONSISTENCY_MONITOR_FLAGGED_BY,
                    ))
                    new_count += 1
                if new_count:
                    session.commit()
                print(f"Consistency monitor ({tenant_id}): {len(findings)} issue(s) found, {new_count} newly flagged ({len(findings) - new_count} already open)")
            except Exception as e:
                # Same "background job failing must never crash the
                # scheduler thread or take the web service down with
                # it" discipline as every other job on this scheduler.
                print(f"Consistency monitor ({tenant_id}) FAILED ({e}) — needs manual review")


def run_order_index_snapshot_job():
    """The scheduled job itself (APScheduler, see on_startup() below for
    the trigger setup). One snapshot per tenant per run — today that's
    just tenant '1', but scoped correctly for when a second tenant is
    real (multi-tenant groundwork). entity_id=0 (a synthetic, non-FK
    value — there's no real "Order Index" row to point at) with the
    date-stamped reference as the real distinguishing label; a genuine
    second run on the same calendar day (e.g. a manual re-trigger)
    correctly becomes _v2 rather than silently overwriting _v1, same
    never-overwrite guarantee (brief §4) as every other archived
    document, not a special case."""
    today_str = date.today().isoformat()
    with Session(engine) as session:
        tenant_ids = session.exec(select(Quote.tenant_id).distinct()).all() or [DEFAULT_TENANT_ID]
        for tenant_id in tenant_ids:
            try:
                csv_bytes = _order_index_snapshot_csv(session, tenant_id)
                archive = _create_and_upload_archive(
                    session, tenant_id, "scheduled_job", "OrderIndexSnapshot", 0,
                    f"OrderIndex_{today_str}", csv_bytes,
                )
                print(f"Order Index snapshot ({tenant_id}, {today_str}): {archive.status}")
            except Exception as e:
                # A snapshot job failing must never crash the scheduler
                # thread or take the web service down with it — same
                # "background operation, never blocks the real app"
                # principle as every other Dropbox failure path (brief §7).
                print(f"Order Index snapshot ({tenant_id}, {today_str}): FAILED to build/archive ({e})")


# ---------- Database Backups (Dropbox brief §5, confirmed Aug 2026 —
# proceeded without further sign-off per explicit direction) ----------
# Separate scheduled job from the Order Index snapshot above (different
# hour — 02:00 UTC vs 23:00 UTC — so they never compete for DB/CPU at
# the exact same moment), same in-process APScheduler (see on_startup()).
DB_BACKUP_FOLDER = {"daily": "Daily", "weekly": "Weekly"}
DB_BACKUP_KEEP = {"daily": 7, "weekly": 4}   # confirmed retention counts


def _prune_old_backups(session: Session, tenant_id: str, tier: str, keep_n: int):
    """Deletes the OLDEST backups beyond keep_n, both from Dropbox and
    from this table — "older ones can be deleted automatically to
    avoid unbounded storage growth" (confirmed requirement). If the
    Dropbox delete itself fails, the DB record is deliberately LEFT in
    place (not deleted) so this same row gets retried on the next run
    rather than the tracking silently losing count of a file that's
    still actually sitting in Dropbox."""
    rows = session.exec(select(DatabaseBackupRecord).where(
        DatabaseBackupRecord.tenant_id == tenant_id, DatabaseBackupRecord.tier == tier,
    ).order_by(DatabaseBackupRecord.created_at.desc())).all()
    for old in rows[keep_n:]:
        if old.dropbox_path:
            result = dropbox_archive.delete_document(old.dropbox_path)
            if not result["ok"] and not result.get("not_configured"):
                print(f"Database backup prune ({tier}): FAILED to delete {old.dropbox_path} from Dropbox ({result['reason']}) — leaving record, will retry next run")
                continue
        session.delete(old)
    session.commit()


def _record_and_upload_backup(session: Session, tenant_id: str, tier: str, reference: str, file_bytes: bytes, method: str) -> DatabaseBackupRecord:
    ext = "sql.gz" if method == "pg_dump" else "json.gz"
    safe_reference = re.sub(r"[^A-Za-z0-9_-]", "_", reference)
    dropbox_path = f"/Bolton/Database Backups/{DB_BACKUP_FOLDER[tier]}/{safe_reference}.{ext}"
    record = DatabaseBackupRecord(tenant_id=tenant_id, tier=tier, reference=reference, method=method, size_bytes=len(file_bytes))
    upload_result = dropbox_archive.upload_document(file_bytes, dropbox_path)
    if upload_result["ok"]:
        record.status = "uploaded"
        record.dropbox_path = upload_result["path"]
        record.dropbox_file_id = upload_result["file_id"]
        record.uploaded_at = datetime.utcnow()
    else:
        record.status = "pending" if upload_result.get("not_configured") else "failed"
        record.failure_reason = upload_result["reason"]
    session.add(record)
    session.commit()
    session.refresh(record)
    return record


def run_database_backup_job():
    """The scheduled job itself. Generates ONE real backup (pg_dump
    preferred, pure-Python JSON fallback — see database_backup.py for
    why both exist) and reuses those same bytes for both the daily
    upload and, on Sundays, the weekly upload too — one real backup
    generated per run, never two separate (possibly inconsistent)
    snapshots for what's supposed to be "the same day's data." Runs
    per-tenant, same pattern as the Order Index snapshot job."""
    today_str = date.today().isoformat()
    file_bytes = database_backup.try_pg_dump(DATABASE_URL)
    method = "pg_dump"
    if file_bytes is None:
        file_bytes = database_backup.python_logical_backup(engine)
        method = "python_json"
    # Verification-only preview (confirmed Aug 2026 — "confirming it
    # contains real recognizable data, not just a success message"):
    # DatabaseBackupRecord deliberately stores no bytes at all (see its
    # own docstring), so once this function returns there is otherwise
    # no way to inspect what a given run actually captured. Computed
    # here, from the real in-memory bytes, before they're discarded —
    # never persisted anywhere.
    preview = database_backup.summarize_for_preview(file_bytes, method)
    is_sunday = date.today().weekday() == 6
    results = []
    with Session(engine) as session:
        tenant_ids = session.exec(select(Quote.tenant_id).distinct()).all() or [DEFAULT_TENANT_ID]
        for tenant_id in tenant_ids:
            try:
                daily = _record_and_upload_backup(session, tenant_id, "daily", f"backup_{today_str}", file_bytes, method)
                print(f"Database backup daily ({tenant_id}, {today_str}, {method}): {daily.status}")
                _prune_old_backups(session, tenant_id, "daily", DB_BACKUP_KEEP["daily"])
                results.append({"tenant_id": tenant_id, "tier": "daily", "id": daily.id, "status": daily.status, "method": method, "size_bytes": daily.size_bytes})
                if is_sunday:
                    weekly = _record_and_upload_backup(session, tenant_id, "weekly", f"backup_{today_str}", file_bytes, method)
                    print(f"Database backup weekly ({tenant_id}, {today_str}, {method}): {weekly.status}")
                    _prune_old_backups(session, tenant_id, "weekly", DB_BACKUP_KEEP["weekly"])
                    results.append({"tenant_id": tenant_id, "tier": "weekly", "id": weekly.id, "status": weekly.status, "method": method, "size_bytes": weekly.size_bytes})
            except Exception as e:
                # Same "never blocks the real app" principle as every
                # other scheduled job here — a failed backup run must
                # never crash the scheduler thread or the web service.
                print(f"Database backup ({tenant_id}, {today_str}): FAILED ({e})")
                results.append({"tenant_id": tenant_id, "tier": "daily", "status": "failed", "error": str(e)})
    return {"results": results, "preview": preview}


@app.get("/clients/{client_id}/order-sheets")
def get_order_sheets_for_client(client_id: int, tenant_id: str = Depends(get_current_tenant)):
    """New 'Orders' tab on the Client page (brief §6) — every order
    sheet generated for any of this client's jobs, findable by job
    number, order number, or supplier. A standalone, searchable-across-
    all-clients Orders index (also raised in brief §6) was deliberately
    NOT built this round — flagged back to Burgert as its own follow-up
    brief rather than assumed in scope, per his own confirmed answer."""
    with Session(engine) as session:
        get_or_404(session, Client, client_id, tenant_id, "Client")
        quotes = session.exec(select(Quote).where(Quote.client_id == client_id, Quote.tenant_id == tenant_id)).all()
        quote_ids = [q.id for q in quotes]
        quotes_by_id = {q.id: q for q in quotes}
        if not quote_ids:
            return []
        sheets = session.exec(select(OrderSheet).where(OrderSheet.quote_id.in_(quote_ids), OrderSheet.tenant_id == tenant_id).order_by(OrderSheet.created_at.desc())).all()
        result = []
        for s in sheets:
            d = s.dict()
            q = quotes_by_id.get(s.quote_id)
            d["job_number"] = q.job_number if q else None
            result.append(d)
        return result


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


@app.post("/admin/database-backup/run-now")
def trigger_database_backup_now(role: str = Depends(require_owner)):
    """Owner-only manual trigger — a genuinely useful standing
    capability (not just a test hook): lets Burgert take a real,
    immediate backup on demand (e.g. right before a risky price-book
    import) rather than only ever getting one at the nightly scheduled
    time. Runs the exact same run_database_backup_job() the scheduler
    calls, synchronously, so the response reflects the real outcome.
    Defined here, after require_owner, rather than back up alongside
    run_database_backup_job() itself — same reason as list_users()
    below: Depends(require_owner) needs that name to already exist at
    import time."""
    return run_database_backup_job()


@app.get("/admin/database-backup")
def list_database_backups(tier: Optional[str] = None, role: str = Depends(require_owner), tenant_id: str = Depends(get_current_tenant)):
    """Owner-only — full backup history, newest first, so Burgert (or a
    future support conversation) can see at a glance whether last
    night's backup actually succeeded, not just trust that it did."""
    with Session(engine) as session:
        stmt = select(DatabaseBackupRecord).where(DatabaseBackupRecord.tenant_id == tenant_id)
        if tier:
            stmt = stmt.where(DatabaseBackupRecord.tier == tier)
        rows = session.exec(stmt.order_by(DatabaseBackupRecord.created_at.desc())).all()
        return rows


@app.get("/admin/users")
def list_users(role: str = Depends(require_owner), tenant_id: str = Depends(get_current_tenant)):
    """Password Reset Link brief (confirmed Aug 2026) — real gap: there
    was no admin surface for accounts at all before this (every User
    row is env-var-seeded, main.py's on_startup()). Owner-only, same
    as everything else touching login credentials. Defined here, after
    require_owner, rather than back up alongside /auth/ — same reason
    as delete_order_sheet() below: Depends(require_owner) needs that
    name to already exist at import time."""
    with Session(engine) as session:
        users = session.exec(select(User).where(User.tenant_id == tenant_id).order_by(User.username)).all()
        return [{"id": u.id, "username": u.username, "display_name": u.display_name, "role": u.role, "active": u.active} for u in users]


class SetUserActiveRequest(BaseModel):
    active: bool


@app.put("/admin/users/{user_id}/active")
def set_user_active(user_id: int, body: SetUserActiveRequest, role: str = Depends(require_owner),
                     tenant_id: str = Depends(get_current_tenant), username: str = Depends(get_current_username)):
    """Closes a real pre-existing gap, found while cleaning up a QA
    verification account for the Trusted Tester Accounts brief
    (confirmed Aug 2026): the Accounts screen has displayed an Active/
    Inactive badge since the Password Reset Link brief, but nothing
    could ever actually change it — User.active existed and was already
    correctly enforced at login (_resolve_session() checks it), just
    with no way to set it except a direct DB edit. Owner-only, and
    blocks deactivating your own account (the one making this request)
    so an Owner can never accidentally lock themselves out."""
    with Session(engine) as session:
        user = get_or_404(session, User, user_id, tenant_id, "User")
        if user.username == username and not body.active:
            raise HTTPException(400, "You can't deactivate your own account.")
        old_value = user.active
        user.active = body.active
        session.add(user)
        session.add(AuditLog(
            tenant_id=tenant_id, username=username, entity_type="User", entity_id=user.id,
            field="active", old_value=str(old_value), new_value=str(body.active),
        ))
        session.commit()
        session.refresh(user)
        return {"id": user.id, "username": user.username, "active": user.active}


class CreateUserRequest(BaseModel):
    username: str
    display_name: str
    role: str   # UserRole value


@app.post("/admin/users")
def create_user(body: CreateUserRequest, role: str = Depends(require_owner), tenant_id: str = Depends(get_current_tenant)):
    """Trusted Tester Accounts brief (confirmed Aug 2026) — the first
    real account-creation endpoint in this app; every existing account
    (Burgert/Ryno/Madri) was seeded directly, not created through the
    app itself. Owner-only, and deliberately generic (not restricted to
    trusted_tester specifically) since the underlying need — onboard a
    new named person without a manual DB script — isn't specific to
    this one brief.

    No password is set here at all: a throwaway random hash is stored
    (unusable — nobody is ever told it, including Burgert) and a
    one-time reset link is generated immediately, the exact same
    mechanism create_password_reset_link() below already provides for
    an EXISTING account, so the new person sets their own first
    password via that link. This is STRICTER than the "generate +
    report in chat" pattern used earlier this session for an existing
    account's reset (Ryno, under real time pressure) — a brand new
    account has no such urgency, so it gets the fully correct "nobody
    but the account holder ever knows the password" treatment from
    day one."""
    username = body.username.strip().lower()
    if not username or not body.display_name.strip():
        raise HTTPException(400, "Username and display name are required.")
    if body.role not in (UserRole.admin, UserRole.sales, UserRole.trusted_tester):
        raise HTTPException(400, "role must be 'admin', 'sales', or 'trusted_tester' (a second Owner account isn't supported here).")
    with Session(engine) as session:
        existing = session.exec(select(User).where(User.username == username, User.tenant_id == tenant_id)).first()
        if existing:
            raise HTTPException(400, f"A user named '{username}' already exists.")
        throwaway_hash = hash_password(secrets.token_urlsafe(32))
        user = User(tenant_id=tenant_id, username=username, display_name=body.display_name.strip(),
                     password_hash=throwaway_hash, role=body.role)
        session.add(user)
        session.commit()
        session.refresh(user)
        token = secrets.token_urlsafe(32)
        reset = PasswordResetToken(
            tenant_id=tenant_id, user_id=user.id, token=token, created_by="system (new account)",
            expires_at=datetime.utcnow() + timedelta(minutes=RESET_LINK_MINUTES),
        )
        session.add(reset)
        session.commit()
        return {
            "id": user.id, "username": user.username, "display_name": user.display_name, "role": user.role,
            "reset_link": f"https://bolton-frontend.onrender.com/index.html?reset_token={token}",
            "expires_in_minutes": RESET_LINK_MINUTES,
        }


@app.post("/admin/users/{user_id}/reset-password-link")
def create_password_reset_link(user_id: int, role: str = Depends(require_owner),
                                tenant_id: str = Depends(get_current_tenant), username: str = Depends(get_current_username)):
    """Password Reset Link brief §1 (confirmed Aug 2026) — the actual
    reason this brief exists: every reset before this required Burgert
    to generate a plaintext password and relay it, meaning he always
    knew it, even briefly. This generates a one-time LINK instead — a
    high-entropy random token (unguessable is the entire security
    property here), expiring in RESET_LINK_MINUTES, usable exactly
    once (reset_password() above marks it used_at immediately on
    success and checks that first). Burgert shares the returned link
    via whatever channel he likes (brief's own words) — this endpoint
    never sends it anywhere itself, no email/SMS infrastructure
    required or assumed."""
    with Session(engine) as session:
        user = get_or_404(session, User, user_id, tenant_id, "User")
        token = secrets.token_urlsafe(32)
        reset = PasswordResetToken(
            tenant_id=tenant_id, user_id=user.id, token=token, created_by=username,
            expires_at=datetime.utcnow() + timedelta(minutes=RESET_LINK_MINUTES),
        )
        session.add(reset)
        session.commit()
        return {
            "token": token, "expires_in_minutes": RESET_LINK_MINUTES,
            "reset_link": f"https://bolton-frontend.onrender.com/index.html?reset_token={token}",
        }


@app.delete("/order-sheets/{order_sheet_id}")
def delete_order_sheet(order_sheet_id: int, tenant_id: str = Depends(get_current_tenant), username: str = Depends(get_current_username), role: str = Depends(require_owner)):
    """Order Sheets UX brief §2 (confirmed Aug 2026) — real gap: there
    was no way to delete an order sheet at all, confirmed directly by
    Burgert being stuck with a real O-0001/O-0002 duplicate on job
    J-0001 and no way to remove the wrong one himself. Owner-only and
    audit-logged, same seriousness already established elsewhere in
    Bolton for destructive actions on real records (Order Index's own
    bulk quote delete) — the confirmation step itself is a frontend
    concern (a confirm() dialog before this is ever called), this
    endpoint's own job is just to log what happened before it's gone.
    Defined here, after require_owner, rather than back up alongside
    the other /order-sheets/ endpoints — Depends(require_owner) needs
    that name to already exist at import time."""
    with Session(engine) as session:
        sheet = get_or_404(session, OrderSheet, order_sheet_id, tenant_id, "Order sheet")
        quote = session.get(Quote, sheet.quote_id)
        summary = f"{sheet.order_number} — {sheet.supplier} — {sheet.sheet_type} (job {quote.job_number if quote else sheet.quote_id})"
        try:
            session.add(AuditLog(
                tenant_id=tenant_id, username=username, entity_type="OrderSheet", entity_id=order_sheet_id,
                field="__deleted__", old_value=summary, new_value="(deleted)",
            ))
            session.commit()
            # Real bug found deploying this against production Postgres
            # (never surfaced against local SQLite, which doesn't
            # enforce foreign keys by default): staging both the line
            # deletes and the parent sheet delete for ONE final commit
            # hit a genuine ForeignKeyViolation — Postgres executed the
            # OrderSheet DELETE before the OrderSheetLine DELETEs had
            # actually landed. Fixed by making the ordering certain
            # instead of relying on the ORM's own dependency sort:
            # delete every line and commit THAT on its own, only then
            # delete the now-childless sheet.
            lines = session.exec(select(OrderSheetLine).where(OrderSheetLine.order_sheet_id == order_sheet_id, OrderSheetLine.tenant_id == tenant_id)).all()
            for line in lines:
                session.delete(line)
            session.commit()
            session.delete(sheet)
            session.commit()
        except Exception as e:
            # A destructive endpoint failing silently into a bare 500
            # is worse than most — surface the real cause rather than
            # leaving the caller (and whoever reads Render's logs) to
            # guess. Real gap found deploying this: the first
            # production attempt returned a plain, contentless 500 with
            # no way to tell what actually went wrong remotely.
            session.rollback()
            raise HTTPException(500, f"Could not delete this order sheet: {e}")
        return {"deleted": order_sheet_id}


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
    session.

    ended_reason (confirmed Aug 2026, Login Activity Log: Fix Confirmed
    Root Causes brief, Fix B) — "logout" | "expired" | None (still
    active). Deliberately computed here at read time from the EXISTING
    ended_at/expires_at fields, the same way still_active/logout_time
    already are, rather than a new stored column on UserSession: the
    distinction is already fully and unambiguously derivable from data
    that exists today, so a redundant stored field would just be a
    second source of truth that could drift out of sync with it for no
    real benefit. Applies automatically to every row, historical
    included, WITHOUT relabeling any stored data — this only changes
    what the read endpoint reports, never what's written (satisfies the
    brief's own "do not retroactively relabel... historical rows" by
    construction, since nothing is being rewritten).

    Trusted Tester login labeling (confirmed Aug 2026, Trusted Tester
    Accounts: Check Login Activity Visibility & Labelling brief) — real
    gap found and closed: this query already joins on every User
    regardless of role, so a Trusted Tester account's real logins were
    NEVER actually filtered out or missing here — but the returned rows
    carried no role/label information at all, so a Trusted Tester login
    looked completely indistinguishable from Ryno/Madri's own. Fixed the
    same "one structural place decides is this a test account" way
    every other consumer already does (`_trusted_tester_usernames()`,
    its own docstring — "used identically everywhere that decision
    matters") — `is_test_data`/`test_data_label` follow the EXACT same
    shape `list_quotes()`/`list_clients()`/`get_order_sheets_for_quote()`
    already use, so the frontend badge convention (🧪, coral, "TEST —
    [name]") is reused verbatim rather than inventing a second one for
    this one screen."""
    with Session(engine) as session:
        stmt = select(UserSession, User).join(User, UserSession.user_id == User.id).where(User.tenant_id == tenant_id)
        rows = session.exec(stmt).all()
        tt_usernames = _trusted_tester_usernames(session, tenant_id)

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
                # Single Active Session per User (confirmed Aug 2026) —
                # sess.ended_reason is a real stored field now (see the
                # model's own comment), populated "logout" | "superseded"
                # going forward. Falls back to "logout" for any row that
                # predates this brief — it really was a plain logout (or
                # the only value this ever recorded before), never
                # silently relabeled.
                ended_reason = sess.ended_reason or "logout"
            elif sess.expires_at < now:
                logout_time = sess.expires_at   # natural 24h expiry, no explicit logout — approximate end time
                still_active = False
                ended_reason = "expired"
            else:
                logout_time = None
                still_active = True
                ended_reason = None
            duration_minutes = round(((logout_time or now) - sess.created_at).total_seconds() / 60, 1)
            result.append({
                "username": user.username,
                "display_name": user.display_name,
                "login_time": sess.created_at.isoformat(),
                "logout_time": logout_time.isoformat() if logout_time else None,
                "still_active": still_active,
                "ended_reason": ended_reason,
                "duration_minutes": duration_minutes,
                "is_test_data": user.username in tt_usernames,
                "test_data_label": f"TEST — {tt_usernames[user.username]}" if user.username in tt_usernames else None,
            })
        result.sort(key=lambda r: r["login_time"], reverse=True)
        return result


# ---------- Builder Referral Portal, Phase 1 pilot (confirmed Aug 2026) ----------
# The /builder/* endpoints near the bottom of this section are the ONLY
# endpoints in this entire API that don't sit behind a login — no
# Depends(get_current_role)/Depends(get_current_tenant) anywhere in
# them, deliberately isolated here so that's easy to audit at a glance.
# tenant_id comes from the resolved Builder row, never a client-supplied
# value or a session (there is no session here).
#
# THE SINGLE MOST IMPORTANT REQUIREMENT IN THE BRIEF: never expose cost,
# margin, box price, markup %, labour rate, supplier name, or any
# pricing breakdown through these endpoints — only a final sell price.
# Every response below hand-picks its exact fields into a plain dict;
# none of them ever use response_model=FlooringProduct or call
# product.dict() on a real price-book row, specifically so a new
# sensitive field added to FlooringProduct later can't leak through
# here just because nobody remembered to update this file too.
BUILDER_COMMISSION_PCT = 0.06   # confirmed Aug 2026 — flat 6%, on the ex-VAT total, once the linked job is fully paid (see _builder_commission_for_quote below)


def _slugify_builder_name(name: str) -> str:
    """Lowercase, hyphenated, alnum-only — e.g. "Deon Brits" ->
    "deon-brits", matching the brief's own example URL. Not
    cryptographically unguessable by design: access control for this
    pilot is "don't hand the link to someone who shouldn't have it"
    plus the active flag (revocation), not link secrecy — Section 1's
    own explicit no-login/no-password design."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug or "builder"


def _builder_commission_for_quote(session: Session, quote: Optional["Quote"], tenant_id: str) -> tuple:
    """Commission is earned ONLY once the linked job is fully paid
    (confirmed directly: on payment received, not on invoice) — checked
    via Quote.final_payment_date, the same field the Order Index already
    uses to mean "paid in full" (computeOrderStatus, order-index.js).
    Computed against the REAL final ex-VAT total of the linked quote
    (same subtotal/discount math get_quote() uses), never the original
    estimate amount, which may differ from what was actually sold —
    confirmed directly in the brief. Returns (status_label, amount) —
    a small shared helper so this exact logic isn't duplicated (and
    left to drift) across the admin list and the builder's own
    statement below."""
    if quote is None:
        return ("no linked job", 0.0)
    if not quote.final_payment_date:
        return ("became a job — awaiting final payment", 0.0)
    lines = session.exec(select(QuoteLineItem).where(QuoteLineItem.quote_id == quote.id, QuoteLineItem.tenant_id == tenant_id)).all()
    subtotal_ex_vat = sum(l.line_total for l in lines) + quote.transport_levy
    total_ex_vat = subtotal_ex_vat * (1 - quote.discount_pct)
    return ("job completed — commission earned", round(total_ex_vat * BUILDER_COMMISSION_PCT, 2))


# ----- Owner-only management (Burgert/Madri creating and reviewing builders) -----

@app.post("/admin/builders")
def create_builder(name: str, phone: str = "", email: str = "",
                    role: str = Depends(require_owner), tenant_id: str = Depends(get_current_tenant)):
    with Session(engine) as session:
        base_slug = _slugify_builder_name(name)
        slug = base_slug
        n = 2
        while session.exec(select(Builder).where(Builder.slug == slug)).first():
            slug = f"{base_slug}-{n}"
            n += 1
        builder = Builder(tenant_id=tenant_id, name=name, slug=slug, phone=phone, email=email)
        session.add(builder)
        session.commit()
        session.refresh(builder)
        return builder


@app.get("/admin/builders")
def list_builders(role: str = Depends(require_owner), tenant_id: str = Depends(get_current_tenant)):
    with Session(engine) as session:
        return session.exec(select(Builder).where(Builder.tenant_id == tenant_id)).all()


@app.put("/admin/builders/{builder_id}")
def update_builder(builder_id: int, name: str = None, active: bool = None, phone: str = None, email: str = None,
                    role: str = Depends(require_owner), tenant_id: str = Depends(get_current_tenant)):
    """active=False is how a link is revoked (brief's own required
    verification: "revoking a builder's link immediately blocks further
    access") — the public endpoints below refuse to resolve an inactive
    builder's slug at all, 404, same as if it never existed. No
    separate token/session to expire since there never was one."""
    with Session(engine) as session:
        builder = get_or_404(session, Builder, builder_id, tenant_id, "Builder")
        if name is not None: builder.name = name
        if active is not None: builder.active = active
        if phone is not None: builder.phone = phone
        if email is not None: builder.email = email
        session.add(builder)
        session.commit()
        session.refresh(builder)
        return builder


@app.get("/admin/builder-estimates")
def list_builder_estimates(role: str = Depends(require_owner), tenant_id: str = Depends(get_current_tenant)):
    """Every estimate submitted across every builder, for Burgert/Madri
    to review and pick up into a real quote (Section 3 — "this becomes
    the starting point of a real quote, not a separate parallel
    system")."""
    with Session(engine) as session:
        rows = session.exec(
            select(BuilderEstimate, Builder).join(Builder, BuilderEstimate.builder_id == Builder.id)
            .where(BuilderEstimate.tenant_id == tenant_id).order_by(BuilderEstimate.created_at.desc())
        ).all()
        result = []
        for est, builder in rows:
            quote = session.get(Quote, est.linked_quote_id) if est.linked_quote_id else None
            status, commission = _builder_commission_for_quote(session, quote, tenant_id)
            result.append({
                "id": est.id, "builder_name": builder.name, "builder_slug": builder.slug,
                "client_name": est.client_name, "client_contact": est.client_contact,
                "site_address": est.site_address, "area_m2": est.area_m2, "product_name": est.product_name,
                "quoted_price_ex_vat": est.quoted_price_ex_vat, "quoted_price_incl_vat": est.quoted_price_incl_vat,
                "created_at": est.created_at.isoformat(), "linked_quote_id": est.linked_quote_id,
                "commission_status": status, "commission_amount": commission,
            })
        return result


@app.put("/admin/builder-estimates/{estimate_id}/link-quote")
def link_builder_estimate_to_quote(estimate_id: int, quote_id: int,
                                    role: str = Depends(require_owner), tenant_id: str = Depends(get_current_tenant)):
    """Marks a builder-submitted estimate as "picked up" into a real
    Bolton quote — Burgert/Madri still creates that quote through the
    normal Quote Builder (client name/site address pre-fillable from
    this estimate's own fields on the frontend side), this endpoint
    just records the link afterward so commission can be tracked
    against the real, final job."""
    with Session(engine) as session:
        est = get_or_404(session, BuilderEstimate, estimate_id, tenant_id, "Builder estimate")
        get_or_404(session, Quote, quote_id, tenant_id, "Quote")   # confirms it's real and belongs to this tenant
        est.linked_quote_id = quote_id
        session.add(est)
        # Quote Photo Attachments (confirmed Aug 2026) — any photos the
        # builder attached at submission time were stored against
        # builder_estimate_id only (there was no real Quote yet). Now
        # that one exists, backfill quote_id onto those same rows so
        # they show up in the normal quote-level gallery from here on —
        # "these travel with the estimate and land on the resulting
        # quote", per that brief's own Section 2, without duplicating
        # the file in storage.
        photos = session.exec(
            select(QuotePhoto).where(QuotePhoto.builder_estimate_id == estimate_id, QuotePhoto.quote_id == None)
        ).all()
        for p in photos:
            p.quote_id = quote_id
            session.add(p)
        session.commit()
        session.refresh(est)
        return est


# ----- Public, unauthenticated — see this section's own header comment -----

@app.get("/builder/{slug}")
def builder_portal_info(slug: str):
    with Session(engine) as session:
        builder = session.exec(select(Builder).where(Builder.slug == slug, Builder.active == True)).first()
        if not builder:
            raise HTTPException(404, "This link isn't active. Contact Blinds & Flooring Studio for a valid link.")
        products = session.exec(
            select(FlooringProduct).where(
                FlooringProduct.tenant_id == builder.tenant_id,
                FlooringProduct.available_to_builder_portal == True,
            )
        ).all()
        return {
            "builder_name": builder.name,
            "products": [{"id": p.id, "product_name": p.product_name, "colour": p.colour} for p in products],
        }


class BuilderEstimateRequest(BaseModel):
    client_name: str
    client_contact: str = ""
    site_address: str = ""
    area_m2: float
    product_id: int


@app.post("/builder/{slug}/estimate")
def submit_builder_estimate(slug: str, body: BuilderEstimateRequest):
    """Computes price through the EXACT SAME pricing engine
    (calculate_flooring_line + resolve_zone_price) every internal
    flooring quote line uses — deliberately not a second/simplified
    formula, so the builder-tool price and the internal calculator's
    price for the same job structurally cannot drift apart. Satisfies
    the brief's own required verification ("compare the builder-tool
    price against what the same job would cost through the normal
    internal calculator — confirm they match") by construction, not
    just by testing it once. own_staff=True (no subcontracted-labour
    cost passed through) and no quote-level discount — a self-serve
    estimate has neither concept."""
    if body.area_m2 <= 0:
        raise HTTPException(400, "Enter a real area in m².")
    with Session(engine) as session:
        builder = session.exec(select(Builder).where(Builder.slug == slug, Builder.active == True)).first()
        if not builder:
            raise HTTPException(404, "This link isn't active. Contact Blinds & Flooring Studio for a valid link.")
        product = session.exec(
            select(FlooringProduct).where(
                FlooringProduct.id == body.product_id,
                FlooringProduct.tenant_id == builder.tenant_id,
                FlooringProduct.available_to_builder_portal == True,
            )
        ).first()
        if not product:
            raise HTTPException(400, "That product isn't currently available through this portal.")
        settings = get_settings(session, builder.tenant_id)
        resolved = resolve_zone_price(session, builder.tenant_id, product, settings)
        labour_rate = resolved.labour_rate_per_m2 if resolved.labour_rate_per_m2 is not None else settings.default_labour_rate_per_m2
        glue_rate = resolved.glue_rate_per_m2 or 0.0
        calc = calculate_flooring_line(
            resolved, body.area_m2, JobType.smooth, 0.0,
            glue_cost_per_unit=glue_rate * 70, glue_coverage_m2=70,
            labour_rate_per_m2=labour_rate, own_staff=True,
            margin_warn_threshold=settings.flooring_margin_warn_threshold,
            tile_removal_fee_per_m2_incl_vat=settings.tile_removal_fee_per_m2_incl_vat,
            vat_pct=settings.vat_pct,
        )
        price_ex_vat = round(calc["line_total"], 2)
        price_incl_vat = round(price_ex_vat * (1 + settings.vat_pct), 2)
        deposit = round(price_incl_vat * settings.default_deposit_pct, 2)
        product_label = f"{product.product_name}{' — ' + product.colour if product.colour else ''}"

        estimate = BuilderEstimate(
            tenant_id=builder.tenant_id, builder_id=builder.id,
            client_name=body.client_name, client_contact=body.client_contact, site_address=body.site_address,
            area_m2=body.area_m2, product_id=product.id, product_name=product_label,
            quoted_price_ex_vat=price_ex_vat, quoted_price_incl_vat=price_incl_vat, deposit_amount=deposit,
        )
        session.add(estimate)
        session.commit()
        session.refresh(estimate)
        # Hand-picked response fields ONLY — see this section's header comment.
        return {
            "estimate_id": estimate.id,
            "product_name": product_label,
            "area_m2": estimate.area_m2,
            "price_per_m2_ex_vat": round(price_ex_vat / body.area_m2, 2),
            "price_per_m2_incl_vat": round(price_incl_vat / body.area_m2, 2),
            "total_ex_vat": price_ex_vat,
            "total_incl_vat": price_incl_vat,
            "deposit_amount": deposit,
            "deposit_pct": settings.default_deposit_pct,
        }


@app.get("/builder/{slug}/statement")
def builder_statement(slug: str):
    """Read-only history for the builder themselves (Section 2 — "they
    cannot edit or delete a submitted estimate", and no endpoint here
    lets them). Filtered strictly by this resolved builder's own id, a
    server-side join — never a client-supplied builder_id — so a
    builder can never see another builder's data through this endpoint,
    satisfying the brief's own required verification directly."""
    with Session(engine) as session:
        builder = session.exec(select(Builder).where(Builder.slug == slug, Builder.active == True)).first()
        if not builder:
            raise HTTPException(404, "This link isn't active. Contact Blinds & Flooring Studio for a valid link.")
        estimates = session.exec(
            select(BuilderEstimate).where(BuilderEstimate.builder_id == builder.id).order_by(BuilderEstimate.created_at.desc())
        ).all()
        result = []
        total_earned = 0.0
        for est in estimates:
            quote = session.get(Quote, est.linked_quote_id) if est.linked_quote_id else None
            status, commission = _builder_commission_for_quote(session, quote, builder.tenant_id)
            if commission:
                total_earned += commission
            result.append({
                "id": est.id, "client_name": est.client_name, "site_address": est.site_address,
                "product_name": est.product_name, "area_m2": est.area_m2,
                "quoted_price_incl_vat": est.quoted_price_incl_vat, "created_at": est.created_at.isoformat(),
                "status": status if est.linked_quote_id else "estimate only — not yet a job", "commission": commission,
            })
        return {"builder_name": builder.name, "estimates": result, "total_commission_earned": round(total_earned, 2)}


# ---------- Quote Photo Attachments, Phase 1 pilot (confirmed Aug 2026) ----------
# Quote-level, not client-level — every read below filters by quote_id,
# so a client's other quotes never show these (brief Section 1). Two
# entry points for a photo to land here: staff uploading directly onto
# an open quote (authenticated, below), or a builder attaching photos
# while submitting a portal estimate (public, no login — see the
# /builder/{slug}/estimate/{estimate_id}/photos endpoint further down,
# kept in the Builder Portal's public section above rather than here,
# since it shares that section's "no auth dependency, hand-picked
# response" rules, not this section's staff-auth ones).
MAX_PHOTO_SIZE_BYTES = 10 * 1024 * 1024   # 10MB per photo — brief's own suggested default, not confirmed otherwise
MAX_PHOTOS_PER_SUBMISSION = 5             # brief's own suggested default, not confirmed otherwise
ALLOWED_PHOTO_CONTENT_TYPES = {"image/jpeg", "image/jpg", "image/png", "image/heic", "image/heif"}


def _validate_photo_upload(content_type: str, size_bytes: int):
    """Shared by both the staff upload endpoint and the public
    builder-submission one — the public endpoint is unauthenticated, so
    this is the actual abuse guardrail the brief requires, not just a
    UX nicety. Raises a clean 400 with a specific message either way
    (brief's own required verification: "oversized files and non-image
    files are rejected cleanly... not a silent failure")."""
    if content_type not in ALLOWED_PHOTO_CONTENT_TYPES:
        raise HTTPException(400, f"'{content_type or 'unknown'}' isn't a supported photo type — use JPG, PNG, or HEIC/HEIF.")
    if size_bytes > MAX_PHOTO_SIZE_BYTES:
        raise HTTPException(400, f"That photo is too large ({size_bytes // (1024*1024)}MB) — the limit is {MAX_PHOTO_SIZE_BYTES // (1024*1024)}MB per photo.")
    if size_bytes == 0:
        raise HTTPException(400, "That file appears to be empty.")


@app.post("/quotes/{quote_id}/photos")
async def upload_quote_photo(quote_id: int, file: UploadFile = File(...),
                              role: str = Depends(get_current_role), tenant_id: str = Depends(get_current_tenant)):
    with Session(engine) as session:
        get_or_404(session, Quote, quote_id, tenant_id, "Quote")
        data = await file.read()
        content_type = file.content_type or ""
        _validate_photo_upload(content_type, len(data))
        safe_name = os.path.basename(file.filename or "photo.jpg")
        path = f"{tenant_id}/quote_{quote_id}/{uuid.uuid4().hex}_{safe_name}"
        try:
            photo_storage.upload_photo(path, data, content_type)
        except RuntimeError as e:
            raise HTTPException(502, str(e))
        photo = QuotePhoto(
            tenant_id=tenant_id, quote_id=quote_id, storage_path=path,
            original_filename=safe_name, content_type=content_type,
            size_bytes=len(data), uploaded_by="staff",
        )
        session.add(photo)
        session.commit()
        session.refresh(photo)
        return photo


@app.get("/quotes/{quote_id}/photos")
def list_quote_photos(quote_id: int, role: str = Depends(get_current_role), tenant_id: str = Depends(get_current_tenant)):
    with Session(engine) as session:
        get_or_404(session, Quote, quote_id, tenant_id, "Quote")
        return session.exec(
            select(QuotePhoto).where(QuotePhoto.quote_id == quote_id, QuotePhoto.tenant_id == tenant_id)
            .order_by(QuotePhoto.created_at)
        ).all()


@app.get("/quotes/{quote_id}/photos/{photo_id}/file")
def get_quote_photo_file(quote_id: int, photo_id: int, role: str = Depends(get_current_role), tenant_id: str = Depends(get_current_tenant)):
    """Proxies the actual bytes back through this authenticated
    endpoint rather than ever handing out a direct Supabase Storage URL
    — same reasoning as the bucket being private in the first place
    (see photo_storage.py). Used for both the thumbnail gallery and the
    full-size view; this app has no image-resizing step (deliberately
    out of scope per the brief — "no editing, no versioning"), so both
    just request the same original bytes."""
    with Session(engine) as session:
        photo = get_or_404(session, QuotePhoto, photo_id, tenant_id, "Photo")
        if photo.quote_id != quote_id:
            raise HTTPException(404, "Photo not found on this quote")
        try:
            data = photo_storage.download_photo(photo.storage_path)
        except RuntimeError as e:
            raise HTTPException(502, str(e))
        return Response(content=data, media_type=photo.content_type)


@app.delete("/quotes/{quote_id}/photos/{photo_id}")
def delete_quote_photo(quote_id: int, photo_id: int, role: str = Depends(get_current_role), tenant_id: str = Depends(get_current_tenant)):
    """Any logged-in staff member can delete (Burgert, Ryno, or Madri
    per the brief — not Owner-only). Builders have no delete endpoint
    at all — their submission is otherwise read-only, per the Builder
    Portal brief's own rule, carried over here directly."""
    with Session(engine) as session:
        photo = get_or_404(session, QuotePhoto, photo_id, tenant_id, "Photo")
        if photo.quote_id != quote_id:
            raise HTTPException(404, "Photo not found on this quote")
        photo_storage.delete_photo(photo.storage_path)
        session.delete(photo)
        session.commit()
        return {"deleted": photo_id}


@app.post("/builder/{slug}/estimate/{estimate_id}/photos")
async def upload_builder_estimate_photos(slug: str, estimate_id: int, files: List[UploadFile] = File(...)):
    """Public, unauthenticated — same trust boundary as the rest of the
    Builder Portal's public endpoints (see that section's own header
    comment above). A separate call from POST .../estimate itself
    (rather than accepting files on that same request) so the existing,
    already-live JSON estimate contract doesn't have to change to a
    multipart one — builder.html calls this right after it gets an
    estimate_id back, only if the builder actually attached photos."""
    if len(files) > MAX_PHOTOS_PER_SUBMISSION:
        raise HTTPException(400, f"Attach at most {MAX_PHOTOS_PER_SUBMISSION} photos.")
    with Session(engine) as session:
        builder = session.exec(select(Builder).where(Builder.slug == slug, Builder.active == True)).first()
        if not builder:
            raise HTTPException(404, "This link isn't active. Contact Blinds & Flooring Studio for a valid link.")
        estimate = session.exec(
            select(BuilderEstimate).where(BuilderEstimate.id == estimate_id, BuilderEstimate.builder_id == builder.id)
        ).first()
        if not estimate:
            raise HTTPException(404, "Estimate not found.")
        already = len(session.exec(select(QuotePhoto).where(QuotePhoto.builder_estimate_id == estimate_id)).all())
        if already + len(files) > MAX_PHOTOS_PER_SUBMISSION:
            raise HTTPException(400, f"At most {MAX_PHOTOS_PER_SUBMISSION} photos per submission.")
        saved = 0
        for f in files:
            data = await f.read()
            content_type = f.content_type or ""
            _validate_photo_upload(content_type, len(data))
            safe_name = os.path.basename(f.filename or "photo.jpg")
            path = f"{builder.tenant_id}/builder_estimate_{estimate_id}/{uuid.uuid4().hex}_{safe_name}"
            try:
                photo_storage.upload_photo(path, data, content_type)
            except RuntimeError as e:
                raise HTTPException(502, str(e))
            photo = QuotePhoto(
                tenant_id=builder.tenant_id, builder_estimate_id=estimate_id, storage_path=path,
                original_filename=safe_name, content_type=content_type, size_bytes=len(data), uploaded_by="builder",
            )
            session.add(photo)
            saved += 1
        session.commit()
        return {"photos_attached": saved}


# ---------- Supplier & Price Book Management Console (confirmed Aug 2026) ----------

ENTITY_TYPE_MODELS = {
    "FlooringProduct": FlooringProduct,
    "BlindsProduct": BlindsProduct,
    "TrimProduct": TrimProduct,
    "SupplierDefault": SupplierDefault,
    "FloorPrepProduct": FloorPrepProduct,
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
    # Skirting/Trim category fix (confirmed Aug 2026, Vinyl Redesign:
    # Real Usage Findings brief §1) — a real skirting line is now
    # genuinely stored as category "skirting", not "trim" (see
    # _trim_line_category()); this delete-reference-check would
    # otherwise have silently stopped catching skirting products in
    # active use the moment that fix shipped, the exact "Robust Owner
    # Delete" failure mode this whole mechanism exists to prevent.
    "TrimProduct": ["trim", "skirting"],
}

# Human-readable labels for the commit confirmation message and the
# audit log UI — e.g. "Price per m²" instead of "base_cost_ex_vat".
def _log_quote_line_audit(session: Session, quote: "Quote", username: str, action: str, line_label: str):
    """AuditLog entry for a line added/removed on a quote already past
    Draft (confirmed Aug 2026, Client Page & Quote Detail: Document
    Preview + Inline Edit brief §3 — "log... whenever a line is added or
    removed on a document already past Draft status... what changed, by
    whom, when"). Gated on the same condition
    POST_ACCEPT_LOCKED_STATUSES (quote-builder.js) already uses for its
    own "this could affect billing already communicated" warning —
    accepted/scheduled/completed. A brand-new quote still being built up
    for the first time ('quoted') is NOT logged here — every line on it
    is normal, expected quoting, not a post-acceptance change worth a
    permanent audit trail entry. action: "added" | "removed"."""
    if quote.workflow_status not in ("accepted", "scheduled", "completed"):
        return
    field = "__line_added__" if action == "added" else "__line_removed__"
    old_value = "(none)" if action == "added" else line_label
    new_value = line_label if action == "added" else "(removed)"
    session.add(AuditLog(
        tenant_id=quote.tenant_id, username=username, entity_type="Quote", entity_id=quote.id,
        field=field, old_value=old_value, new_value=new_value,
    ))


FIELD_LABELS = {
    "product_name": "Range", "colour": "Colour", "supplier": "Supplier", "email": "Email",
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
    "available_to_builder_portal": "Available to Builder Portal (max 2 products)",
    "default_delivery_fee_per_m2": "Delivery fee default (R/m², for new products)",
    "pack_size": "Pack size", "pack_unit": "Pack unit", "coverage_rate": "Coverage rate",
    "coverage_basis": "Coverage basis", "cost_ex_vat_per_pack": "Cost per pack (ex VAT)",
    "price_zone_a": "Zone A price (calculated)", "price_zone_b": "Zone B price (calculated)", "price_zone_c": "Zone C price (calculated)",
    "book_price": "Book price", "mechanism": "Mechanism", "fabric_tier": "Fabric tier",
    "cost_ex_vat_per_lm": "Cost per lm (ex VAT)", "fixed_sell_price_per_lm": "Fixed sell price per lm",
    "markup_multiplier": "Markup",
    "default_trade_discount_pct": "Trade discount % (default for new products)",
    "pricing_zone": "Pricing zone",
    # Post-Draft line change logging (confirmed Aug 2026, Client Page &
    # Quote Detail: Document Preview + Inline Edit brief, §3).
    "__line_added__": "Line added (post-acceptance)",
    "__line_removed__": "Line removed (post-acceptance)",
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

        # Builder Referral Portal, Phase 1 pilot (confirmed Aug 2026) —
        # "capped at 2 active at a time", a hard constraint the brief
        # explicitly says not to exceed. Checked here, as the LAST step
        # before commit, so it catches the cap regardless of how many
        # products this one batch touched or whether they were staged
        # edits or new entities — session.exec() below sees this
        # transaction's own pending changes via SQLAlchemy's autoflush,
        # so this is accurate even though nothing has been committed
        # yet. Raising here rolls back the WHOLE commit, same "nothing
        # partially saved" guarantee every other validation on this
        # endpoint already has (e.g. the deletion reference-check above).
        builder_portal_count = len(session.exec(
            select(FlooringProduct).where(
                FlooringProduct.tenant_id == tenant_id,
                FlooringProduct.available_to_builder_portal == True,
            )
        ).all())
        if builder_portal_count > 2:
            raise HTTPException(400, f"Only 2 products can be available to the Builder Portal at a time — this commit would leave {builder_portal_count}. Turn one off first.")

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


@app.get("/admin/supplier-emails")
def list_supplier_emails(role: str = Depends(get_current_role), tenant_id: str = Depends(get_current_tenant)):
    """Send button brief (confirmed Aug 2026) — deliberately NOT owner-
    only, unlike list_supplier_defaults() above: an Order Sheet's Send
    button needs to look up its supplier's email regardless of who's
    viewing that Order Sheet, and the pricing-sensitive fields on
    SupplierDefault (trade discount, delivery fee, pricing zone) that
    justify that endpoint being require_owner simply aren't returned
    here at all — only {supplier: email}, for suppliers that actually
    have one on file."""
    with Session(engine) as session:
        rows = session.exec(select(SupplierDefault).where(SupplierDefault.tenant_id == tenant_id, SupplierDefault.email != "")).all()
        return {r.supplier: r.email for r in rows}


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


class FlagRecordRequest(BaseModel):
    entity_type: str
    entity_id: int
    note: str


@app.post("/flags")
def flag_record(body: FlagRecordRequest, tenant_id: str = Depends(get_current_tenant), username: str = Depends(get_current_username)):
    """Trusted Tester Accounts brief §3 (confirmed Aug 2026) — "Flag for
    review," open to every role (Trusted Testers, and useful for Ryno/
    Madri too, per the brief's own words), not just Owner — anyone
    using the app can flag something that looks wrong; only REVIEWING
    the list (below) and deciding what to do about it stays Owner-only.
    No entity_type/entity_id validation against real tables here,
    deliberately — same generic, non-FK pattern as AuditLog/
    DocumentArchive, so flagging something can never itself fail
    because of some OTHER unrelated data problem."""
    if not body.note or not body.note.strip():
        raise HTTPException(400, "A short note about what looked wrong is required.")
    with Session(engine) as session:
        flag = FlaggedRecord(
            tenant_id=tenant_id, entity_type=body.entity_type, entity_id=body.entity_id,
            note=body.note.strip(), flagged_by=username,
        )
        session.add(flag)
        session.commit()
        session.refresh(flag)
        return flag


@app.get("/admin/flags")
def list_flags(resolved: Optional[bool] = None, role: str = Depends(require_owner), tenant_id: str = Depends(get_current_tenant)):
    """Owner-only review list (brief §3 — "one place, rather than
    hunting through the Order Index"). resolved=False (the frontend's
    default view) shows only what still needs a decision; omit the
    param, or pass resolved=True, to see the full/resolved history
    too — same optional-filter pattern as the audit log above."""
    with Session(engine) as session:
        stmt = select(FlaggedRecord).where(FlaggedRecord.tenant_id == tenant_id)
        if resolved is not None:
            stmt = stmt.where(FlaggedRecord.resolved == resolved)
        rows = session.exec(stmt).all()
        return sorted(rows, key=lambda r: r.flagged_at, reverse=True)


class ResolveFlagRequest(BaseModel):
    action: str   # "fixed" | "deleted" | "dismissed"


@app.post("/admin/flags/{flag_id}/resolve")
def resolve_flag(flag_id: int, body: ResolveFlagRequest, role: str = Depends(require_owner),
                  tenant_id: str = Depends(get_current_tenant), username: str = Depends(get_current_username)):
    """Owner-only (brief §3 — "Burgert decides per item whether to fix
    it live or delete it"). This endpoint only records the OUTCOME —
    it never performs the fix or the delete itself; deleting the
    underlying record (e.g. via Force Delete) is a separate action the
    Owner takes on the actual Quote/Client/OrderSheet screen, same as
    today, then comes back here (or the frontend does it in one flow)
    to mark the flag resolved. Keeping this endpoint's only job as
    "record what happened" means it can never itself get out of sync
    with whether the underlying delete/fix actually succeeded."""
    if body.action not in ("fixed", "deleted", "dismissed"):
        raise HTTPException(400, "action must be 'fixed', 'deleted', or 'dismissed'.")
    with Session(engine) as session:
        flag = get_or_404(session, FlaggedRecord, flag_id, tenant_id, "Flag")
        if flag.resolved:
            raise HTTPException(400, "This flag is already resolved.")
        flag.resolved = True
        flag.resolved_action = body.action
        flag.resolved_by = username
        flag.resolved_at = datetime.utcnow()
        session.add(flag)
        session.commit()
        session.refresh(flag)
        return flag


@app.post("/admin/consistency-monitor/run-now")
def run_consistency_monitor_now(role: str = Depends(require_owner), tenant_id: str = Depends(get_current_tenant)):
    """Live Consistency Monitor (confirmed Aug 2026) — on-demand trigger
    for the SAME job the nightly scheduler runs (run_consistency_monitor_job()
    calls _run_consistency_checks() for every tenant; this calls the
    identical check for just the current one and returns the real
    findings directly, not just a "started" acknowledgement) — needed
    to actually verify this without waiting for 03:00 UTC, and useful
    for Burgert afterward if he wants a fresh check on demand rather
    than only once a day. Still detect-only: reuses the exact same
    de-duplication against already-open flags as the scheduled job, so
    running this manually can never create duplicate flags for the
    same still-unresolved issue."""
    with Session(engine) as session:
        findings = _run_consistency_checks(session, tenant_id)
        existing_open = session.exec(select(FlaggedRecord).where(
            FlaggedRecord.tenant_id == tenant_id,
            FlaggedRecord.flagged_by == CONSISTENCY_MONITOR_FLAGGED_BY,
            FlaggedRecord.resolved == False,  # noqa: E712
        )).all()
        already_flagged = {(f.entity_type, f.entity_id, f.note) for f in existing_open}
        new_flags = []
        for finding in findings:
            key = (finding["entity_type"], finding["entity_id"], finding["note"])
            if key in already_flagged:
                continue
            flag = FlaggedRecord(
                tenant_id=tenant_id, entity_type=finding["entity_type"], entity_id=finding["entity_id"],
                note=finding["note"], flagged_by=CONSISTENCY_MONITOR_FLAGGED_BY,
            )
            session.add(flag)
            new_flags.append(finding)
        if new_flags:
            session.commit()
        return {"checked_lines_found_issues": len(findings), "newly_flagged": len(new_flags), "already_open": len(findings) - len(new_flags)}


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
    invoices (final_payment_date set — see the real-gap comment on
    paid_quotes below for why this no longer reads the legacy status
    field), for the given calendar month, per the brief's core rule #1.
    Two calculation paths depending on the
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

        # Real gap found and fixed (confirmed Aug 2026, Order Index / Job
        # Workflow Redesign brief): this used to filter on the legacy
        # Quote.status == "paid" — but the new workflow action endpoints
        # (accept/schedule/complete) never set that field at all, only
        # workflow_status. Left as-is, commission for every job processed
        # through the new workflow would have silently stopped
        # calculating the moment that brief shipped. final_payment_date
        # being set is the real, structural "this job is paid" signal —
        # already existed, already what Order Index's own payment
        # tracking uses — so this now checks that directly instead of a
        # status string nothing sets anymore.
        paid_quotes = session.exec(
            select(Quote).where(
                Quote.sales_owner == sales_owner_key,
                Quote.final_payment_date.is_not(None),
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
        # Trusted Tester Accounts brief (confirmed Aug 2026) — same
        # display-only labeling as list_quotes() above, same shared
        # _trusted_tester_usernames() source, never hidden from the
        # normal client list.
        tt_usernames = _trusted_tester_usernames(session, tenant_id)
        result = []
        for c in clients:
            d = c.dict()
            d["is_test_data"] = c.created_by in tt_usernames
            d["test_data_label"] = f"TEST — {tt_usernames[c.created_by]}" if c.created_by in tt_usernames else None
            result.append(d)
        return result


@app.get("/clients/{client_id}")
def get_client(client_id: int, tenant_id: str = Depends(get_current_tenant)):
    with Session(engine) as session:
        return get_or_404(session, Client, client_id, tenant_id, "Client")


@app.post("/clients")
def create_client(client: Client, tenant_id: str = Depends(get_current_tenant), username: str = Depends(get_current_username)):
    client.tenant_id = tenant_id
    # Trusted Tester Accounts brief (confirmed Aug 2026) -- captured here,
    # not client-suppliable, same trust boundary as tenant_id above: who
    # actually created this record determines whether it's test data
    # (_trusted_tester_usernames(), main.py), so it must come from the
    # authenticated session, never a request body field.
    client.created_by = username
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
def delete_client(client_id: int, role: str = Depends(require_owner),
                   tenant_id: str = Depends(get_current_tenant), username: str = Depends(get_current_username)):
    """Owner-only (Robust Owner Delete brief, confirmed Aug 2026 — found
    while cleaning up test data during unrelated verification work: this
    endpoint had NO role check at all before this, unlike quote delete,
    and NO dependency check either, so it 500'd outright on any client
    with quotes attached — a Postgres FK-constraint violation raised
    unhandled, the same silent-failure shape the rest of this brief
    exists to eliminate. Fixed the minimal, safe way: block with a
    clear, actionable error rather than deciding on this client's
    behalf whether its quotes should be deleted too — that's a real
    business decision the Owner should make explicitly, one quote at a
    time, using the existing (now-fixed) quote delete."""
    with Session(engine) as session:
        client = get_or_404(session, Client, client_id, tenant_id, "Client")
        quotes = session.exec(select(Quote).where(Quote.client_id == client_id, Quote.tenant_id == tenant_id)).all()
        if quotes:
            raise HTTPException(400, f"Can't delete {client.name} — {len(quotes)} quote(s) are linked to this client. Delete those first if you really need to remove this client.")
        session.add(AuditLog(
            tenant_id=tenant_id, username=username, entity_type="Client", entity_id=client.id,
            field="__deleted__", old_value=f"Client — {client.name}", new_value="(deleted)",
        ))
        _cascade_delete_children(session, "client", client.id, tenant_id)
        session.delete(client)
        session.commit()
        return {"deleted": client_id}


def _job_workflow_info(quote: "Quote", today: date, materials_ordered: bool = False) -> dict:
    """Next Action / Needs Attention engine (confirmed Aug 2026, Order
    Index / Job Workflow Redesign brief + Next Action Addendum).
    Computed at read time from workflow_status plus the operational/
    accounting fields — never separately stored, same "derive, don't
    duplicate state that could drift" discipline as computeOrderStatus()
    (retired for this purpose — see order-index.js), _quote_totals(),
    _builder_commission_for_quote(), and ended_reason elsewhere in this
    codebase.

    action_target is a route hint ("job_detail" | "print_invoice") the
    frontend uses to decide where the Next Action button navigates —
    this function only decides WHAT the action is and WHERE it roughly
    belongs, never how the button is wired up.

    7-day staleness threshold for "Follow up" is a fresh constant here,
    deliberately NOT businessSettings.order_overdue_days — that setting
    means something different (days since INVOICE sent), and reusing it
    would conflate two unrelated concepts.

    materials_ordered (Job Workflow Design Proposal Phase 1, confirmed
    Aug 2026) — REAL BUG FIXED: this used to be read directly off
    Quote.materials_ordered, a manually-ticked checkbox with zero
    connection to whether an Order Sheet was actually placed. The
    caller now derives it fresh from real OrderSheet.status rows
    (get_quote()/list_quotes() below) and passes it in here — this
    function no longer touches the stale field at all, same "derive,
    don't duplicate" principle as everything else it already follows.

    On Hold (Phase 1) is checked FIRST, before any workflow_status
    branch — deliberately not a 5th status (quote.on_hold_reason/
    on_hold_at sit alongside workflow_status, never replace one of its
    4 values), so the job's step progress freezes exactly where it was
    and resumes there once taken off hold, with nothing else about its
    state touched."""
    QUOTE_STALE_DAYS = 7
    ws = quote.workflow_status
    invoiced = bool(quote.invoice_sent_date)
    paid = bool(quote.final_payment_date)
    next_action = action_button = action_target = None
    attention_priority = attention_label = None

    if quote.on_hold_reason:
        return {
            "next_action": f"On Hold — {quote.on_hold_reason}", "action_button": "VIEW HOLD", "action_target": "job_detail",
            "attention_priority": "critical", "attention_label": "On Hold",
        }

    if ws == "quoted":
        next_action, action_button, action_target = "Follow up with customer", "FOLLOW UP", "job_detail"
        if (today - quote.created_at.date()).days >= QUOTE_STALE_DAYS:
            attention_priority, attention_label = "notice", "Follow up"
    elif ws == "accepted":
        # Booking visibility fix (confirmed Aug 2026, Master Workflow
        # proposal §01/§05/§07) — REAL GAP FIXED, not a new field.
        # installation_date/installation_confirmed_date were already two
        # separate fields (installation_date settable via Job Details
        # alone, without touching workflow_status — see
        # update_quote_details() below), but this branch never looked at
        # installation_date at all, so a tentative date already sitting
        # on an accepted job was invisible to Next Action and the step
        # strip. installation_confirmed_date is only ever set inside
        # schedule_quote(), together with the flip to "scheduled" — so
        # while ws=="accepted" it's always None; checked explicitly here
        # anyway (not just "installation_date truthy") so this stays
        # correct even if that ever changes. Does NOT flip
        # workflow_status itself — only the explicit "Confirm
        # Installation — Book" action (schedule_quote()) still does
        # that, exactly as before (Test 4's own "must not auto-confirm").
        if quote.installation_date and not quote.installation_confirmed_date:
            next_action = f"Confirm installation — {quote.installation_date.strftime('%d %b')} proposed"
            action_button, action_target = "CONFIRM BOOKING", "job_detail"
            attention_priority, attention_label = "warning", "Confirm booking"
        else:
            next_action, action_button, action_target = "Book installation", "BOOK INSTALLATION", "job_detail"
            attention_priority, attention_label = "critical", "Book installation"
    elif ws == "scheduled":
        # Three real states here, not two (confirmed directly): ordered
        # and physically received/on-hand are genuinely different events
        # — materials_ordered just means the order was placed;
        # ready_for_installation means the flooring/blinds have actually
        # been delivered and are on hand, confirmed manually via "Mark
        # Materials Received" (Bolton doesn't track physical stock-on-
        # hand automatically, so this can't be inferred).
        # materials_not_needed (Independent Status Tiles, Decision Q2,
        # confirmed Aug 2026) — a job built from stock already on hand
        # never produces a real OrderSheet, so materials_ordered would
        # otherwise stay False forever and this would say "Materials
        # required" indefinitely, indistinguishable from a job genuinely
        # forgotten. Short-circuits BOTH the ordered and received checks
        # below — using stock on hand means there's nothing left to wait
        # on for materials at all, same as if it had already arrived.
        if quote.installation_date and quote.installation_date == today + timedelta(days=1):
            next_action, action_button, action_target = "Prepare job", "PREPARE JOB", "job_detail"
            attention_priority, attention_label = "warning", "Upcoming"
        elif not materials_ordered and not quote.materials_not_needed:
            next_action, action_button, action_target = "Prepare / order materials", "PREPARE JOB", "job_detail"
            attention_priority, attention_label = "warning", "Materials required"
        elif not quote.ready_for_installation and not quote.materials_not_needed:
            next_action, action_button, action_target = "Confirm materials received", "PREPARE JOB", "job_detail"
            # Job Detail / Needs Attention disagreement (confirmed Aug
            # 2026, JobDetail: Needs Attention Bug brief) — REAL BUG
            # FIXED. Both screens already read this exact same function
            # (there was never a second, older code path to unify — the
            # brief's own hypothesis, checked and ruled out): the defect
            # was that this branch reused the IDENTICAL "Materials
            # required" label as the branch above (not materials_ordered
            # at all), even though materials genuinely ARE ordered here
            # — that's exactly why Job Detail's derived "Materials
            # ordered" indicator correctly showed green (it only checks
            # materials_ordered, per _job_steps()'s "procurement" step)
            # while the Order Index still showed an orange dot captioned
            # as if nothing had been ordered. Distinct label now, so a
            # job in this genuinely different state can never look
            # identical to one where materials haven't been ordered yet.
            attention_priority, attention_label = "warning", "Confirm receipt"
        else:
            next_action, action_button, action_target = "Complete installation", "OPEN JOB", "job_detail"
    elif ws == "completed":
        if not invoiced:
            next_action, action_button, action_target = "Invoice customer", "CREATE INVOICE", "print_invoice"
            attention_priority, attention_label = "warning", "Invoice"
        elif not paid:
            next_action, action_button, action_target = "Receive payment", "LOG PAYMENT", "job_detail"
        # invoiced and paid -> job fully closed out, nothing left to prompt

    return {
        "next_action": next_action, "action_button": action_button, "action_target": action_target,
        "attention_priority": attention_priority, "attention_label": attention_label,
    }


def _all_sheets_placed(order_sheets: list) -> bool:
    """Job Workflow Design Proposal Phase 1 (confirmed Aug 2026) — the
    real derivation Quote.materials_ordered used to fake with a manual
    checkbox. True once the job has generated at least one Order Sheet
    AND every Order Sheet it produced has status == "placed" — matches
    the proposal's own definition exactly (§03: "Every Order Sheet the
    job actually produced has status == 'placed'"), and naturally
    handles the post-merge case where 1 sheet now covers what used to
    be 2: one shared status for one real delivery, not two independently
    tracked flags that could disagree."""
    return bool(order_sheets) and all(s.status == "placed" for s in order_sheets)


def _materials_ordered_for_quote(session: Session, quote_id: int, tenant_id: str) -> bool:
    """Convenience wrapper over _all_sheets_placed() for callers (e.g.
    list_quotes()) that don't already have the job's Order Sheets
    loaded — get_quote() fetches them once itself (Phase 2, job_steps)
    and calls _all_sheets_placed() directly instead, to avoid querying
    the same table twice in one request."""
    sheets = session.exec(select(OrderSheet).where(OrderSheet.quote_id == quote_id, OrderSheet.tenant_id == tenant_id)).all()
    return _all_sheets_placed(sheets)


def _order_sheet_quantity_colour_summary(session: Session, order_sheet_id: int, tenant_id: str) -> dict:
    """Materials List: Show Quantity Ordered and Colour (confirmed Sept
    2026) — real, already-generated OrderSheetLine data for ONE sheet,
    summarized for the Materials tile. Not a new calculation: quantity is
    a straight sum of each line's own `quantity` (grouped by `unit`, in
    case a sheet genuinely mixes e.g. m² and boxes — rare, but joined
    honestly rather than silently added across units), and colours are
    the distinct non-blank `colour` values, in the order first seen."""
    lines = session.exec(select(OrderSheetLine).where(OrderSheetLine.order_sheet_id == order_sheet_id, OrderSheetLine.tenant_id == tenant_id)).all()
    qty_by_unit: dict = {}
    colours = []
    for l in lines:
        unit = l.unit or ""
        qty_by_unit[unit] = qty_by_unit.get(unit, 0.0) + l.quantity
        if l.colour and l.colour not in colours:
            colours.append(l.colour)
    quantity_summary = ", ".join(f"{qty:g} {unit}".strip() for unit, qty in qty_by_unit.items())
    return {"quantity_summary": quantity_summary, "colour_summary": ", ".join(colours)}


def _job_steps(session: Session, quote: "Quote", tenant_id: str, materials_ordered: bool, order_sheets: list) -> list:
    """Job Workflow Design Proposal Phase 2 (confirmed Aug 2026) — the
    adaptive step list §03 describes: Procurement -> Scheduling ->
    Installation -> Complete & Invoice, computed fresh at read time,
    never stored (same discipline _job_workflow_info() already follows
    for Next Action). Deliberately a VIEW, not a second engine — every
    "done" condition below reads a field _job_workflow_info() and the
    existing Workflow panel already use, nothing new is introduced to
    decide it.

    Only returned for a job that's actually Accepted or later and not
    Declined — a quote that hasn't become a job has no step sequence
    to show, per the brief's own non-goal (nothing upstream of Accepted
    changes). A job with no flooring/floor-prep lines skips the
    Procurement step entirely — never an empty/irrelevant step. Exactly
    one step is marked "active": the first one not yet done — everything
    before it is "done", everything after is upcoming (rendered plain,
    no special flag needed for that)."""
    if quote.workflow_status == "quoted" or quote.declined_at:
        return []

    steps = []

    has_procurable_lines = session.exec(
        select(QuoteLineItem.id).where(
            QuoteLineItem.quote_id == quote.id, QuoteLineItem.tenant_id == tenant_id,
            QuoteLineItem.category == "flooring",
        )
    ).first() is not None

    if has_procurable_lines:
        steps.append({
            # materials_not_needed (Independent Status Tiles, Decision
            # Q2, confirmed Aug 2026) — same short-circuit as
            # _job_workflow_info() above, kept consistent here too even
            # though the step-strip's own visual (dots) is retired by
            # this same brief — job_steps is still a real, documented
            # response field other consumers could read.
            "id": "procurement", "label": "Order Materials", "done": materials_ordered or quote.materials_not_needed,
            # Tiles carry one entry per resulting Order Sheet (post-merge,
            # Phase 1) — never one per category. Empty list means
            # flooring/floor-prep lines exist but Generate Order Sheet(s)
            # hasn't been clicked yet, so the frontend can prompt for
            # that specifically rather than showing a blank step.
            "tiles": [
                {
                    "id": s.id, "supplier": s.supplier, "sheet_type": s.sheet_type, "status": s.status,
                    # Materials List: Show Quantity Ordered and Colour
                    # (confirmed Sept 2026) — surfaces what was actually
                    # ordered, read straight from this sheet's own
                    # OrderSheetLine rows (the same data the Order Sheet
                    # document itself is built from, orderSheetLinesEditorHtml()
                    # frontend), never a separate calculation. Quantity is
                    # summed per unit (almost always one unit per sheet;
                    # joined if genuinely mixed) so a sheet with 3 products
                    # in the same unit reads as one real total, not three
                    # lines the user has to add up themselves. Colours are
                    # the distinct, non-blank values across this sheet's
                    # lines only — never another sheet's, since each is
                    # queried by its own order_sheet_id.
                    **_order_sheet_quantity_colour_summary(session, s.id, tenant_id),
                }
                for s in order_sheets
            ],
        })

    # Booking visibility fix (confirmed Aug 2026, Master Workflow
    # proposal §06) — same tentative-date gap _job_workflow_info() above
    # fixes, surfaced here too so the step strip isn't blind to it
    # either; "note" is purely a display hint, never read back to decide
    # "done" (that stays workflow_status-only, per Test 4).
    scheduling_note = None
    if quote.workflow_status == "accepted" and quote.installation_date and not quote.installation_confirmed_date:
        scheduling_note = f"{quote.installation_date.strftime('%d %b')} proposed — not yet confirmed"
    steps.append({"id": "scheduling", "label": "Schedule Installation", "done": quote.workflow_status in ("scheduled", "completed"), "note": scheduling_note})
    steps.append({"id": "installation", "label": "Installation", "done": quote.workflow_status == "completed"})
    steps.append({"id": "completion", "label": "Complete & Invoice", "done": bool(quote.invoice_sent_date) and bool(quote.final_payment_date)})

    found_active = False
    for step in steps:
        step["active"] = (not step["done"]) and (not found_active)
        found_active = found_active or step["active"]

    return steps


def _quote_line_sort_key(line: dict) -> int:
    """Fixed Display Order (confirmed Aug 2026, Add-Line Data-Loss brief
    §4): Floor/Vinyl -> Screed -> Trims -> Skirtings -> everything else,
    always in this order regardless of the order lines were actually
    added in — applied once here, in get_quote(), so Quote Builder's
    lines table, the printed/PDF document, and the client document
    preview (buildPrintDocHtml(), shared.js — the print doc and the
    preview are literally the same generated html) all agree
    automatically, with zero risk of the three ever drifting apart
    the way three independent copies of the same sort would.

    `category` alone can't place a flooring or trim line correctly — see
    flooring_pricing_type/trim_sub_category's own comment on
    QuoteLineItem (models.py) for why those had to be added as
    denormalized snapshots. Python's sort is stable, so lines within the
    same bucket (and the whole "everything else" bucket — brief's own
    words, "in whatever order makes sense") keep their original
    relative order, nothing more elaborate needed there."""
    category = line.get("category")
    if category == "flooring":
        return 2 if line.get("flooring_pricing_type") == "screed" else 1
    # Skirting/Trim category fix (confirmed Aug 2026, Vinyl Redesign:
    # Real Usage Findings brief §1) — category is now genuinely
    # "skirting" for a real skirting line (add_trim_line()/
    # edit_trim_line(), _trim_line_category()); the trim_sub_category
    # check stays here too as a defensive fallback for any row the
    # on_startup() recategorization migration hasn't reached yet, not
    # because it's still the primary signal going forward.
    if category == "skirting" or (category == "trim" and line.get("trim_sub_category") in ("skirting", "quarter_round")):
        return 4
    if category == "trim":
        return 3
    return 5


def _quote_totals(subtotal_ex_vat: float, quote: "Quote", vat_pct: float) -> dict:
    """Discount -> VAT -> deposit/balance math, shared (confirmed Aug
    2026, Client Order History Columns brief). Previously hand-
    duplicated in get_quote() and list_quotes() — their own comments
    already flagged that exact duplication as a known bug risk ("a
    second, slightly different copy of this math is exactly how earlier
    bugs in this project happened"). Extracted here rather than adding
    a THIRD copy for get_client_quotes()'s new Value column below.
    Takes subtotal_ex_vat as a plain float rather than fetching lines
    itself — each caller already computes it its own way (post-strip
    dicts in get_quote(), raw QuoteLineItem rows elsewhere), so this
    only dedupes the part that was actually identical.

    Manual Override (confirmed Aug 2026, Manual Override brief) — when
    quote.manual_override_total_incl_vat is set, it completely replaces
    the calculated discount->VAT chain; deposit/balance are derived FROM
    the override so a recorded deposit always matches the real agreed
    figure, not a formula the Owner explicitly said doesn't apply here.
    discount_amount is still reported (as the gap between the real
    calculated subtotal and the override, clamped at 0 rather than ever
    going negative) purely so the printed doc's own subtotal -> discount
    -> net -> VAT breakdown still visibly adds up to the final total for
    the client — same clean, professional invoice either way, no
    internal override language anywhere in it (brief's own requirement).

    Deposit Amount (confirmed Aug 2026, Deposit Amount + Save
    Confirmation + Default Branch brief) — deposit_amount was always
    purely deposit_pct of the total, which doesn't reflect reality
    (different clients pay different actual amounts). When
    quote.actual_deposit_amount is set, it replaces the percentage
    figure here — same "the real recorded figure wins" precedent as the
    Manual Override total above — and balance_amount is computed from
    THAT, not the percentage-derived one, so what shows as owing always
    matches what was actually paid."""
    if quote.manual_override_total_incl_vat is not None:
        total_incl_vat = quote.manual_override_total_incl_vat
        total_ex_vat = total_incl_vat / (1 + vat_pct)
        discount_amount = max(0.0, subtotal_ex_vat - total_ex_vat)
    else:
        discount_amount = subtotal_ex_vat * quote.discount_pct
        total_ex_vat = subtotal_ex_vat - discount_amount
        total_incl_vat = total_ex_vat * (1 + vat_pct)
    deposit_amount = quote.actual_deposit_amount if quote.actual_deposit_amount is not None else total_incl_vat * quote.deposit_pct
    balance_amount = total_incl_vat - deposit_amount
    return {
        "discount_amount": round(discount_amount, 2), "total_ex_vat": round(total_ex_vat, 2),
        "total_incl_vat": round(total_incl_vat, 2), "deposit_amount": round(deposit_amount, 2),
        "balance_amount": round(balance_amount, 2),
    }


@app.get("/clients/{client_id}/quotes")
def get_client_quotes(client_id: int, tenant_id: str = Depends(get_current_tenant)):
    """Order history for a client — every quote ever linked to this
    record, most recent first.

    site_address and total_incl_vat (confirmed Aug 2026, Client Order
    History Columns brief) — added so two quotes for the same client
    (e.g. two drafts, same branch, same day) are actually distinguishable
    in the list without opening each one. site_address is already a
    plain Quote field (per-job site, not the client's own contact
    address shown separately above this table); total_incl_vat is
    computed the same way the Order Index's own totals are (confirmed
    directly: incl VAT, "to match what a client would see")."""
    with Session(engine) as session:
        client = get_or_404(session, Client, client_id, tenant_id, "Client")
        VAT_PCT = get_settings(session, tenant_id).vat_pct
        quotes = session.exec(
            select(Quote).where(Quote.client_id == client_id, Quote.tenant_id == tenant_id).order_by(Quote.created_at.desc())
        ).all()
        result = []
        for q in quotes:
            lines = session.exec(select(QuoteLineItem).where(QuoteLineItem.quote_id == q.id, QuoteLineItem.tenant_id == tenant_id)).all()
            subtotal_ex_vat = sum(l.line_total for l in lines) + q.transport_levy
            totals = _quote_totals(subtotal_ex_vat, q, VAT_PCT)
            d = q.dict()
            d["total_incl_vat"] = totals["total_incl_vat"]
            result.append(d)
        return {"client": client, "quotes": result}


@app.get("/admin/duplicate-clients")
def find_duplicate_clients(role: str = Depends(require_owner), tenant_id: str = Depends(get_current_tenant)):
    """Order Index -> Client Link Gap brief (confirmed Aug 2026) —
    "check for and report any other duplicate client records that may
    already exist." Exact match only, after trimming whitespace and
    normalizing case — deliberately NOT a fuzzy/typo-tolerant matcher,
    which is a much bigger, riskier feature that could just as easily
    flag two genuinely different people (a father and son with the same
    name, e.g.) as "duplicates." This catches the specific, confirmed
    real pattern instead: the same person typed in more than once,
    verbatim or with only casing/whitespace differences, across separate
    walk-in quote entries — the exact mechanism behind this brief's own
    Gap 2 finding (see update_quote_details()'s client_id param).
    quote_count per client included so Burgert can tell at a glance
    which of a duplicate pair is the "real" one worth keeping active."""
    with Session(engine) as session:
        clients = session.exec(select(Client).where(Client.tenant_id == tenant_id)).all()
        groups: dict = {}
        for c in clients:
            key = c.name.strip().lower()
            groups.setdefault(key, []).append(c)
        result = []
        for key, members in groups.items():
            if len(members) < 2:
                continue
            member_details = []
            for c in members:
                quote_count = len(session.exec(select(Quote).where(Quote.client_id == c.id, Quote.tenant_id == tenant_id)).all())
                member_details.append({
                    "id": c.id, "name": c.name, "phone": c.phone, "email": c.email,
                    "created_at": c.created_at.isoformat(), "quote_count": quote_count,
                })
            result.append({"name": key, "clients": member_details})
        return result


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

def _resolve_or_create_client(session: Session, tenant_id: str, client_id: Optional[int], client_name: Optional[str], branch: Optional[str] = None) -> Client:
    """Client-Link Audit (confirmed Aug 2026, "Third Occurrence of
    Orphaned/Unlinked Quotes" brief). The brief's own framing: two
    earlier fixes (Duplicate Quote's free-text client_name, and Start
    Quote's un-clicked autocomplete suggestion) each patched ONE
    frontend entry point, and a THIRD, different entry point (the
    Flooring Quotes drill-down) produced the identical symptom —
    "patching entry points one at a time is not working." A full audit
    found exactly two backend code paths where a quote's client can
    ever be set: create_quote() below, and update_quote_details()'s
    client_name branch — every current and future frontend entry point
    (main "+ New Quote", Flooring Quotes drill, Builder Portal "Start
    Quote from Estimate") already funnels through one or the other, so
    fixing the invariant HERE, once, covers all of them regardless of
    which frontend flow gets built next — the actual fix for "patching
    one at a time isn't working."

    - client_id given: validated and returned directly (existing, safe
      behaviour — an explicit, real link).
    - No client_id, but a name: an EXACT (case/whitespace-insensitive)
      match against this tenant's existing clients is auto-linked; no
      match creates a brand-new Client record with just that name — no
      phone/email/address ever fabricated, same precedent as the Clear
      Unlinked Quotes brief's Robert Aspeling remediation. A PARTIAL
      match is a live frontend suggestion (already built, q_client's
      own oninput handler, index.html) for the user to actively pick —
      not something this backend safety net silently guesses at.
    - Neither given: rejected outright (400) — the brief's own words,
      "there must be NO remaining path where a quote can be saved with
      just a text name and no real client link" extends to no name and
      no link at all.

    branch (confirmed Aug 2026, Full Real-Browser Walkthrough & Audit) —
    real gap fixed: a brand-new Client created here used to always fall
    back to the Client model's own hardcoded field default ("gansbaai",
    models.py), regardless of which branch the quote actually being
    created/edited is for. Every current call site now has a real branch
    value in scope to pass (create_quote()'s own `branch` param;
    convert_price_check_to_quote()/update_quote_details()/the orphaned-
    quote migration's own `quote.branch`) — optional and defaulting to
    None (leaving the model default untouched) only so this never breaks
    for some future caller that genuinely has no branch to give."""
    if client_id:
        return get_or_404(session, Client, client_id, tenant_id, "Client")
    name = (client_name or "").strip()
    if not name:
        raise HTTPException(400, "A client name is required.")
    existing = session.exec(select(Client).where(Client.tenant_id == tenant_id)).all()
    match = next((c for c in existing if c.name.strip().lower() == name.lower()), None)
    if match:
        return match
    new_client = Client(tenant_id=tenant_id, name=name, **({"preferred_branch": branch} if branch else {}))
    session.add(new_client)
    session.commit()
    session.refresh(new_client)
    return new_client


@app.post("/quotes")
def create_quote(client_name: str, sales_owner: str, branch: str = "gansbaai",
                  blinds_measurements_visible: bool = True,
                  discount_pct: float = 0.0, deposit_pct: float = 0.70,
                  client_id: int = None, is_price_check: bool = False,
                  tenant_id: str = Depends(get_current_tenant)):
    """Every quote created here is now linked to a real Client record
    from the moment it exists — via _resolve_or_create_client() above,
    confirmed Aug 2026 (Client-Link Audit brief). Previously, without a
    client_id, this silently created a "walk-in/one-off" quote using
    just the typed name and no CRM link at all — exactly the recurring
    orphaned-quote bug this brief was written to close for good, not
    patch again at one more entry point. site_address is still taken
    from the resolved Client record when it has one on file.

    is_price_check (confirmed Aug 2026, New Quote Screen brief §3) — the
    ONE deliberate, sanctioned exception to the paragraph above: contact
    details are explicitly OPTIONAL for a Price Check, since it isn't a
    real tracked job until someone converts it (POST
    /quotes/{id}/convert-to-quote below). If a name/client_id genuinely
    was given (the brief's own "optionally capturing the walk-in's name/
    contact details"), it's still resolved-or-created and linked
    normally — this bypass only applies when NEITHER was provided."""
    with Session(engine) as session:
        if is_price_check and not client_id and not client_name.strip():
            final_client_name, final_client_id, site_address = "Walk-in (Price Check)", None, ""
        else:
            client = _resolve_or_create_client(session, tenant_id, client_id, client_name, branch=branch)
            final_client_name, final_client_id, site_address = client.name, client.id, (client.address or "")
        quote = Quote(
            client_name=final_client_name,
            client_id=final_client_id,
            is_price_check=is_price_check,
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


@app.post("/quotes/{quote_id}/convert-to-quote")
def convert_price_check_to_quote(quote_id: int, client_id: int = None, client_name: str = None,
                                  tenant_id: str = Depends(get_current_tenant)):
    """'Save as real quote' / 'Convert to quote' (brief §3) — turns a
    Price Check into a genuine tracked quote, appearing on the Order
    Index/Needs Attention/dashboards like any other from this point on.
    Carries over whatever product/pricing/contact details were already
    entered — line items are already real QuoteLineItem rows (same
    calculator, same add-line endpoints, brief's own "reuse, don't
    rebuild"), untouched by this. If contact details were captured
    during the Price Check (client_id/client_name passed here, or
    already set on the quote), those are reused/confirmed; if genuinely
    none exist yet, one is required now — a converted quote is a real
    job and falls back under the same 'no orphaned quotes' rule as
    everything else (Client-Link Audit brief)."""
    with Session(engine) as session:
        quote = get_or_404(session, Quote, quote_id, tenant_id, "Quote")
        if not quote.is_price_check:
            raise HTTPException(400, "This quote is not a Price Check.")
        if client_id or (client_name and client_name.strip()):
            client = _resolve_or_create_client(session, tenant_id, client_id, client_name, branch=quote.branch)
            quote.client_id = client.id
            quote.client_name = client.name
            if client.address:
                quote.site_address = client.address
        elif not quote.client_id:
            raise HTTPException(400, "A client name is required to convert this Price Check into a real quote.")
        quote.is_price_check = False
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
def update_quote_details(quote_id: int, client_name: str = None, client_id: int = None, sales_owner: str = None,
                          branch: str = None, status: str = None, description: str = None,
                          site_address: str = None, installation_date: str = None,
                          invoice_sent_date: str = None, deposit_paid_date: str = None,
                          deposit_payment_method: str = None, final_payment_date: str = None,
                          final_payment_method: str = None, installer_team: str = None,
                          workflow_status: str = None, actual_deposit_amount: float = None,
                          clear_actual_deposit_amount: bool = False, installation_notes: str = None,
                          deposit_pct: float = None,
                          tenant_id: str = Depends(get_current_tenant),
                          username: str = Depends(get_current_username)):
    """Update a quote's own details — client name, sales owner, branch,
    legacy status, plus order-tracking fields (site address, installation
    date, invoice/payment dates and methods) confirmed Aug 2026 for the
    Order Index. Used by the "Save Quote" button. Line items are already
    saved individually as they're added — this covers quote-level fields
    only.

    installation_notes (confirmed Aug 2026, Job Card Content Spec) — the
    one small new field that spec called for: free-text notes about the
    floor/installation, plus anything else the installer needs (access,
    parking) that isn't already captured elsewhere. Deliberately reused
    from this SAME existing endpoint (the Job Card screen's own "Save
    notes" action calls this, not a new parallel one) rather than a
    separate mechanism — and deliberately NOT the internal AuditLog
    outcome-note pattern used for Leads/On Hold/Force Delete: this is a
    plain, freely-editable field on the job itself, never a permanent,
    append-only audit trail — it can be corrected or rewritten before
    printing, same as every other Job Details field on this endpoint.

    client_id (confirmed Aug 2026, Order Index -> Client Link Gap brief)
    — real gap closed: there was previously NO way to link an existing
    quote to a real Client record after creation. A quote typed as a
    plain name in Quote Builder without clicking the matching autocomplete
    suggestion (the confirmed root cause of that brief's Gap 2 — see
    createQuote()'s own comment, quote-builder.js) becomes a permanently
    disconnected walk-in with client_id=None, even if a real client of
    that same name already exists — it would show correctly on the
    Order Index (which lists every quote regardless of client_id) but
    never appear in that real client's own Order History (which filters
    strictly by client_id). Passing client_id here re-links it, and — same
    as create_quote()'s own client_id branch — refreshes client_name/
    site_address from that real record, so the two can't quietly
    disagree afterward.

    workflow_status here is the MANUAL OVERRIDE / correction path
    (confirmed Aug 2026, Order Index / Job Workflow Redesign — Q5): the
    automatic transitions (accept_quote/decline_quote/schedule_quote/
    complete_quote below) are the PRIMARY way this field changes — this
    is only for the exception case where one of those fired on wrong
    information and needs a direct fix, so it's deliberately not wired
    to a prominent button anywhere in the UI. Does NOT touch job_number
    or accepted_at — those stay whatever they already were, since a
    correction to what STAGE a job is in shouldn't erase the permanent
    record of WHEN it was first accepted.

    Date params are accepted as strings and explicitly coerced — same
    fix as v38's coerce_date_fields(), applied here from the start this
    time rather than being rediscovered as a bug later.

    deposit_pct (Independent Status Tiles, Decision Q3, confirmed Aug
    2026) — real gap closed: this field already existed on Quote but
    was only ever written once, at create_quote() time, from Business
    Settings' own global default_deposit_pct — there was no way to
    change what ONE job requires without changing (and reverting) the
    workspace-wide setting. Genuine per-job override now, same
    precedent as actual_deposit_amount just below (a real field already
    lets one job's ACTUAL deposit differ from the calculated one; this
    lets one job's REQUIRED deposit differ from the global default) —
    0.0 is exactly how "no deposit required for this job" (Money tile)
    is represented, already tolerated cleanly by _quote_totals() with
    no special-casing needed there."""
    if deposit_pct is not None and not (0 <= deposit_pct <= 1):
        raise HTTPException(400, "Deposit % must be between 0 and 100.")
    with Session(engine) as session:
        quote = get_or_404(session, Quote, quote_id, tenant_id, "Quote")
        if client_id is not None:
            client = get_or_404(session, Client, client_id, tenant_id, "Client")
            quote.client_id = client.id
            quote.client_name = client.name
            if client.address:
                quote.site_address = client.address
        elif client_name is not None and quote.client_id is None:
            # Client-Link Audit (confirmed Aug 2026) — only resolve-or-
            # create when this quote genuinely has no client link yet.
            # saveQuote() (quote-builder.js) resends client_name on
            # EVERY save regardless of whether the quote is already
            # linked; blindly re-resolving every time would risk
            # silently relinking an already-correctly-linked quote to a
            # DIFFERENT client on a simple display-text edit. Once
            # client_id is set, only an explicit client_id here (the
            # real "Change client" relink action, Job Detail) ever
            # changes it again — this closes the gap for a quote that's
            # still unlinked and being saved with a typed name, without
            # ever touching one that's already correctly linked.
            client = _resolve_or_create_client(session, tenant_id, None, client_name, branch=quote.branch)
            quote.client_id = client.id
            quote.client_name = client.name
            if client.address:
                quote.site_address = client.address
        elif client_name is not None:
            quote.client_name = client_name
        if sales_owner is not None:
            quote.sales_owner = sales_owner
        if branch is not None:
            quote.branch = branch
        if status is not None:
            quote.status = status
        if workflow_status is not None:
            if workflow_status not in ("quoted", "accepted", "scheduled", "completed"):
                raise HTTPException(400, "workflow_status must be one of: quoted, accepted, scheduled, completed")
            quote.workflow_status = workflow_status
        if description is not None:
            quote.description = description
        if site_address is not None:
            quote.site_address = site_address
        if installer_team is not None:
            quote.installer_team = installer_team
        if installation_notes is not None:
            quote.installation_notes = installation_notes
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
        # Deposit Amount (confirmed Aug 2026, Deposit Amount + Save
        # Confirmation + Default Branch brief) — actual_deposit_amount
        # (set) and clear_actual_deposit_amount (revert to the
        # percentage-calculated figure) are mutually exclusive on any
        # single save; a set always wins if somehow both were sent.
        # Logged to AuditLog either way, same "every manual entry is
        # logged" discipline as the Manual Override fields.
        if actual_deposit_amount is not None:
            old = quote.actual_deposit_amount
            quote.actual_deposit_amount = actual_deposit_amount
            quote.actual_deposit_amount_by = username
            quote.actual_deposit_amount_at = datetime.utcnow()
            session.add(AuditLog(
                tenant_id=tenant_id, username=username, entity_type="Quote", entity_id=quote_id,
                field="actual_deposit_amount",
                old_value=(f"R{old:.2f}" if old is not None else "(calculated from %)"),
                new_value=f"R{actual_deposit_amount:.2f}",
            ))
        elif clear_actual_deposit_amount and quote.actual_deposit_amount is not None:
            old = quote.actual_deposit_amount
            quote.actual_deposit_amount = None
            quote.actual_deposit_amount_by = None
            quote.actual_deposit_amount_at = None
            session.add(AuditLog(
                tenant_id=tenant_id, username=username, entity_type="Quote", entity_id=quote_id,
                field="actual_deposit_amount_cleared", old_value=f"R{old:.2f}",
                new_value="(reverted to % calculated)",
            ))
        # Per-job deposit override (Independent Status Tiles, Decision
        # Q3, confirmed Aug 2026) — logged the same way actual_deposit_
        # amount is above; this changes what's REQUIRED, not just what
        # was actually paid, so it's worth the same permanent trail.
        if deposit_pct is not None and deposit_pct != quote.deposit_pct:
            old_pct = quote.deposit_pct
            quote.deposit_pct = deposit_pct
            session.add(AuditLog(
                tenant_id=tenant_id, username=username, entity_type="Quote", entity_id=quote_id,
                field="deposit_pct", old_value=f"{old_pct*100:.0f}%", new_value=f"{deposit_pct*100:.0f}%",
            ))
        session.add(quote)
        session.commit()
        session.refresh(quote)
        return quote


# ----- Job Workflow automatic transitions (confirmed Aug 2026, Order
# Index / Job Workflow Redesign brief + Next Action Addendum) — each
# endpoint below is a specific, named EVENT, not a raw status field
# anyone can set to anything. This is the primary way workflow_status
# changes; update_quote_details()'s workflow_status param just above is
# the secondary, manual-correction escape hatch (Q5). -----

@app.post("/quotes/{quote_id}/accept")
def accept_quote(quote_id: int, tenant_id: str = Depends(get_current_tenant)):
    """QUOTED -> ACCEPTED. Assigns job_number and sets accepted_at —
    together, the real "this is now a job" marker (confirmed Aug 2026,
    §3 of the architecture proposal — "reflected in the data
    architecture, not just the UI"), permanent even if workflow_status
    is later hand-corrected via the manual override above."""
    with Session(engine) as session:
        quote = get_or_404(session, Quote, quote_id, tenant_id, "Quote")
        if quote.workflow_status != "quoted":
            raise HTTPException(400, f"This quote is already {quote.workflow_status} — nothing to accept.")
        quote.workflow_status = "accepted"
        quote.accepted_at = datetime.utcnow()
        if not quote.job_number:
            quote.job_number = _next_job_number(session, tenant_id)
        session.add(quote)
        session.commit()
        session.refresh(quote)
        return quote


class DeclineQuoteRequest(BaseModel):
    reason: str


@app.post("/quotes/{quote_id}/decline")
def decline_quote(quote_id: int, body: DeclineQuoteRequest, tenant_id: str = Depends(get_current_tenant), username: str = Depends(get_current_username)):
    """Deliberately NOT one of the 4 workflow values (confirmed Aug
    2026, Q2) — a declined quote never became a job, so it doesn't
    belong inside a job-workflow enum. Its own timestamp instead, same
    reasoning that already kept invoicing/payment out of the status
    field too. Also what makes conversion-rate reporting possible.

    Decline Quote reason (confirmed Aug 2026, Master Workflow proposal
    §01/§02/§05) — REAL GAP FIXED: this used to take no reason at all
    and write no AuditLog entry, marking a quote permanently lost with
    zero record of why — the exact "marked done, zero evidence" shape
    §02's Proof-of-Work principle was written to catch, found by
    sweeping the existing system for that shape rather than only
    checking Leads. Same one-field-plus-AuditLog treatment as On Hold
    (hold_quote() above) — no new field on Quote itself, the reason
    lives in AuditLog only (read back by get_quote() below)."""
    if not body.reason or not body.reason.strip():
        raise HTTPException(400, "A reason is required to decline a quote.")
    with Session(engine) as session:
        quote = get_or_404(session, Quote, quote_id, tenant_id, "Quote")
        if quote.workflow_status != "quoted":
            raise HTTPException(400, "Only a quote that hasn't been accepted yet can be declined.")
        quote.declined_at = datetime.utcnow()
        session.add(quote)
        session.add(AuditLog(
            tenant_id=tenant_id, username=username, entity_type="Quote", entity_id=quote.id,
            field="declined", old_value=quote.workflow_status, new_value=body.reason.strip(),
        ))
        session.commit()
        session.refresh(quote)
        return quote


@app.put("/quotes/{quote_id}/schedule")
def schedule_quote(quote_id: int, installation_date: str, tenant_id: str = Depends(get_current_tenant)):
    """ACCEPTED -> SCHEDULED. installation_confirmed_date is set to the
    SAME value as installation_date here deliberately — confirming a
    date is exactly what "booking" an installation means (confirmed Aug
    2026); the two fields only diverge if installation_date was set
    earlier as a tentative/target date without ever being confirmed."""
    with Session(engine) as session:
        quote = get_or_404(session, Quote, quote_id, tenant_id, "Quote")
        if quote.workflow_status not in ("accepted", "scheduled"):
            raise HTTPException(400, "Only an accepted job can be scheduled.")
        quote.installation_date = date.fromisoformat(installation_date)
        quote.installation_confirmed_date = quote.installation_date
        quote.workflow_status = "scheduled"
        session.add(quote)
        session.commit()
        session.refresh(quote)
        return quote


@app.put("/quotes/{quote_id}/materials")
def update_quote_materials(quote_id: int, materials_ordered: bool = None, ready_for_installation: bool = None,
                            installer_team: str = None, materials_not_needed: bool = None,
                            tenant_id: str = Depends(get_current_tenant)):
    """Operational fields, never statuses (confirmed Aug 2026) —
    materials_ordered and ready_for_installation are deliberately two
    INDEPENDENT booleans (Q6): ordering materials and them actually
    being physically delivered/on hand are genuinely different real-
    world events days apart, and one auto-following the other would
    produce false "ready" signals. ready_for_installation is always a
    manual confirmation ("Mark Materials Received" in the UI) — never
    inferred, since Bolton has no physical stock-on-hand tracking to
    infer it from. Available whenever a job is Accepted or further
    along — not gated to Scheduled only, since materials are often
    ordered before an installation date is even confirmed. Each field
    is set independently by the caller (one param per click in the UI),
    but all three can be sent together too.

    materials_not_needed (Independent Status Tiles, Decision Q2,
    confirmed Aug 2026) — same "one manual click, never inferred"
    pattern as ready_for_installation, added here rather than a new
    endpoint since it's the same real-world object (this job's
    Materials state) as the other two fields on this route."""
    with Session(engine) as session:
        quote = get_or_404(session, Quote, quote_id, tenant_id, "Quote")
        if materials_ordered is not None:
            quote.materials_ordered = materials_ordered
        if ready_for_installation is not None:
            quote.ready_for_installation = ready_for_installation
        if installer_team is not None:
            quote.installer_team = installer_team
        if materials_not_needed is not None:
            quote.materials_not_needed = materials_not_needed
        session.add(quote)
        session.commit()
        session.refresh(quote)
        return quote


@app.post("/quotes/{quote_id}/complete")
def complete_quote(quote_id: int, completion_date: str = None, tenant_id: str = Depends(get_current_tenant)):
    """SCHEDULED -> COMPLETED. completion_date defaults to today if not
    given — the common case is marking a job complete on the day it
    actually finished."""
    with Session(engine) as session:
        quote = get_or_404(session, Quote, quote_id, tenant_id, "Quote")
        if quote.workflow_status != "scheduled":
            raise HTTPException(400, "Only a scheduled job can be marked complete.")
        quote.completion_date = date.fromisoformat(completion_date) if completion_date else date.today()
        quote.workflow_status = "completed"
        session.add(quote)
        session.commit()
        session.refresh(quote)
        return quote


class HoldQuoteRequest(BaseModel):
    reason: str


@app.post("/quotes/{quote_id}/hold")
def hold_quote(quote_id: int, body: HoldQuoteRequest, tenant_id: str = Depends(get_current_tenant), username: str = Depends(get_current_username)):
    """Job Workflow Design Proposal Phase 1, §7 (confirmed Aug 2026) —
    "a way to handle [real exceptions] without corrupting its state."
    Deliberately NOT a 5th workflow_status value (on_hold_reason/
    on_hold_at sit alongside it, models.py) — the job's step progress
    freezes exactly where it was; nothing about workflow_status,
    installation_date, or materials-ordered state is touched by this
    endpoint. _job_workflow_info() checks this pair FIRST, before its
    normal per-status branches, so Next Action reads "On Hold — [reason]"
    regardless of which status the job was in when held. Restricted to
    accepted/scheduled — a quote that hasn't become a job yet, or a job
    already completed, has no in-progress step to freeze."""
    if not body.reason or not body.reason.strip():
        raise HTTPException(400, "A reason is required to put a job on hold.")
    with Session(engine) as session:
        quote = get_or_404(session, Quote, quote_id, tenant_id, "Quote")
        if quote.workflow_status not in ("accepted", "scheduled"):
            raise HTTPException(400, "Only an accepted or scheduled job can be put on hold.")
        if quote.on_hold_reason:
            raise HTTPException(400, "This job is already on hold.")
        quote.on_hold_reason = body.reason.strip()
        quote.on_hold_at = datetime.utcnow()
        session.add(quote)
        session.add(AuditLog(
            tenant_id=tenant_id, username=username, entity_type="Quote", entity_id=quote.id,
            field="on_hold", old_value="not on hold", new_value=quote.on_hold_reason,
        ))
        session.commit()
        session.refresh(quote)
        return quote


@app.post("/quotes/{quote_id}/resume")
def resume_quote(quote_id: int, tenant_id: str = Depends(get_current_tenant), username: str = Depends(get_current_username)):
    """Taking a job off hold (Phase 1, §7) — clears the pair set by
    hold_quote() above and nothing else. The job resumes exactly where
    it was: workflow_status, installation_date, and materials-ordered
    state were never touched while on hold, so Next Action immediately
    goes back to reading whatever it would have said if the hold had
    never happened."""
    with Session(engine) as session:
        quote = get_or_404(session, Quote, quote_id, tenant_id, "Quote")
        if not quote.on_hold_reason:
            raise HTTPException(400, "This job isn't on hold.")
        old_reason = quote.on_hold_reason
        quote.on_hold_reason = None
        quote.on_hold_at = None
        session.add(quote)
        session.add(AuditLog(
            tenant_id=tenant_id, username=username, entity_type="Quote", entity_id=quote.id,
            field="on_hold", old_value=old_reason, new_value="resumed",
        ))
        session.commit()
        session.refresh(quote)
        return quote


# ---------------------------------------------------------------------------
# Leads (confirmed Aug 2026, Master Workflow proposal §02/§05/§06/§07 — the
# "LEAD" stage of the master flow, which had nowhere to live at all before
# this: the earliest tracked stage was previously a Quote). Deliberately
# isolated from the Job workflow (proposal §08's own risk assessment): a
# Lead only ever feeds INTO a Quote via converted_quote_id; nothing here
# reads back from Quote to Lead, so this can't destabilise anything already
# shipped.
# ---------------------------------------------------------------------------

LEAD_TRANSITIONABLE_STATUSES = ("contacted", "potential", "lost")  # "converted" is reachable ONLY via /convert, never a manual status flip
LEAD_STALE_DAYS = 3   # shorter than _job_workflow_info()'s 7-day stale-quote threshold — an uncontacted lead goes cold quicker than a sent quote (proposal §02)


class LeadStatusRequest(BaseModel):
    new_status: str
    note: str


def _log_lead_status_audit(session: Session, lead: "Lead", username: str, old_status: str, new_status: str, note: str):
    """Proof-of-Work principle (Master Workflow proposal §02) — one
    AuditLog row IS the entire outcome-note mechanism, not a parallel
    notes-with-timestamp field: "every outcome note is one more AuditLog
    row, nothing else needed." The note is folded into new_value
    alongside the status itself (AuditLog.old_value/new_value are plain
    strings regardless of the real field's shape, per that model's own
    docstring) so one row tells the whole story — what changed AND why —
    exactly as read back in the Lead's own activity trail (get_lead())."""
    session.add(AuditLog(
        tenant_id=lead.tenant_id, username=username, entity_type="Lead", entity_id=lead.id,
        field="lead_status", old_value=old_status, new_value=f"{new_status} — {note.strip()}",
    ))


def _lead_last_outcome_at(session: Session, lead: "Lead", tenant_id: str) -> datetime:
    """The most recent logged-outcome timestamp, for staleness detection
    (_lead_next_action() below) — read fresh from the real AuditLog
    trail every time, never a separately-stored timestamp field on Lead
    that could drift from what the audit trail actually says (same
    "derive, don't duplicate state" discipline as _job_workflow_info()).
    Falls back to created_at for a lead that's never had a status change
    logged yet (still sitting at "new")."""
    last = session.exec(
        select(AuditLog)
        .where(AuditLog.tenant_id == tenant_id, AuditLog.entity_type == "Lead", AuditLog.entity_id == lead.id)
        .order_by(AuditLog.timestamp.desc())
    ).first()
    return last.timestamp if last else lead.created_at


def _lead_next_action(lead: "Lead", last_outcome_at: datetime, today: date) -> dict:
    """Next Action for a Lead (Master Workflow proposal §07) — a
    deliberately separate, smaller engine from _job_workflow_info(),
    not a branch bolted onto it: a Lead is isolated from the Job
    workflow and has its own, much shorter lifecycle, so merging the
    two would mix two genuinely different engines for two genuinely
    different stages. "new" -> "Contact customer"; "contacted"/
    "potential" gone quiet for LEAD_STALE_DAYS -> "Follow up lead",
    system-detected rather than manually chased; "converted"/"lost" are
    terminal, nothing left to prompt."""
    if lead.lead_status == "new":
        return {"next_action": "Contact customer", "attention_priority": "critical", "attention_label": "New lead"}
    if lead.lead_status in ("contacted", "potential") and (today - last_outcome_at.date()).days >= LEAD_STALE_DAYS:
        return {"next_action": "Follow up lead", "attention_priority": "notice", "attention_label": "Follow up"}
    return {"next_action": None, "attention_priority": None, "attention_label": None}


@app.post("/leads")
def create_lead(lead: Lead, tenant_id: str = Depends(get_current_tenant), username: str = Depends(get_current_username)):
    """New enquiry (Master Workflow proposal §02/§05). lead_status/
    converted_quote_id/id are reset here regardless of what's sent —
    same trust-boundary discipline as tenant_id/created_by
    (create_client()) — a lead only ever starts at "new", and can only
    ever reach "converted" through POST /leads/{id}/convert actually
    creating a real Quote, never a client-supplied field at creation
    time. No AuditLog entry on creation itself — the trail exists to
    record what happened TO an existing lead, not the fact of its own
    creation, same as Client."""
    if not lead.name or not lead.name.strip():
        raise HTTPException(400, "A name is required.")
    coerce_date_fields(lead, "visit_date")
    lead.id = None
    lead.tenant_id = tenant_id
    lead.created_by = username
    lead.lead_status = "new"
    lead.converted_quote_id = None
    with Session(engine) as session:
        session.add(lead)
        session.commit()
        session.refresh(lead)
        return lead


@app.get("/leads")
def list_leads(lead_status: Optional[str] = None, search: Optional[str] = None, tenant_id: str = Depends(get_current_tenant)):
    """Leads screen (Master Workflow proposal §06) — same table-with-
    Next-Action pattern as the Order Index (list_quotes() above), each
    row carrying its own computed next_action/attention_priority rather
    than a second, separate lookup the frontend would have to make."""
    with Session(engine) as session:
        stmt = select(Lead).where(Lead.tenant_id == tenant_id)
        if lead_status:
            stmt = stmt.where(Lead.lead_status == lead_status)
        leads = session.exec(stmt).all()
        if search:
            search_lower = search.lower()
            leads = [l for l in leads if search_lower in l.name.lower() or search_lower in l.contact.lower()]
        today = date.today()
        result = []
        for lead in leads:
            last_outcome_at = _lead_last_outcome_at(session, lead, tenant_id)
            d = lead.dict()
            d.update(_lead_next_action(lead, last_outcome_at, today))
            d["last_outcome_at"] = last_outcome_at
            result.append(d)
        # Urgency before recency, same instinct as the Order Index's own
        # Needs Attention list: new leads needing first contact and
        # stale follow-ups surface above quiet in-progress/terminal ones.
        priority_order = {"critical": 0, "notice": 1, None: 2}
        result.sort(key=lambda d: (priority_order.get(d["attention_priority"], 2), -d["id"]))
        return result


def _leads_for_day(session: Session, tenant_id: str, day: date) -> list:
    """Leads with a visit_date matching a given day (confirmed Aug 2026,
    Lead: Visit Date, Address Fields & Printable Day List brief §3) —
    a real usage-feedback gap: Burgert wants a simple, scannable,
    printable list of what needs to happen on a given day. Written as a
    standalone, reusable query rather than embedded ad hoc inside the
    day-list endpoint below, per the brief's own explicit instruction —
    it's an intentional stepping stone toward a future calendar feature,
    where "leads with a visit date on day X" will very likely become one
    view inside a proper calendar; this is the piece that must survive
    unchanged even if today's printable screen itself gets fully
    replaced. Deliberately does NOT filter by lead_status — a future
    calendar view would want to show every visit on a day regardless of
    outcome (including one that already converted), so status-based
    filtering/styling stays a display-layer concern, not baked into the
    data access itself."""
    return session.exec(
        select(Lead).where(Lead.tenant_id == tenant_id, Lead.visit_date == day)
    ).all()


@app.get("/leads/day-list")
def leads_day_list(day: Optional[str] = None, tenant_id: str = Depends(get_current_tenant)):
    """Printable 'Today's Leads' day list (confirmed Aug 2026, same
    brief) — thin wrapper around _leads_for_day() above; defaults to
    today when no day is given. Each lead carries the same computed
    next_action as list_leads()/get_lead() (not a second, differently-
    derived summary), so the printed list shows what's needed at a
    glance without disagreeing with the Leads screen itself. Registered
    BEFORE /leads/{lead_id} below — Starlette matches routes in
    registration order, and "/leads/day-list" would otherwise match
    {lead_id}'s path template first and 422 trying to parse "day-list"
    as an int."""
    target_day = date.fromisoformat(day) if day else date.today()
    with Session(engine) as session:
        leads = _leads_for_day(session, tenant_id, target_day)
        today = date.today()
        result = []
        for lead in leads:
            last_outcome_at = _lead_last_outcome_at(session, lead, tenant_id)
            d = lead.dict()
            d.update(_lead_next_action(lead, last_outcome_at, today))
            # "any outcome notes already logged" (brief §3) — same
            # AuditLog trail get_lead() already returns, so the printed
            # list can never show something that disagrees with the
            # lead's own Activity history. Day lists are short by
            # nature (one day's worth of visits), so the full trail per
            # lead stays cheap and genuinely scannable, not capped.
            history = session.exec(
                select(AuditLog)
                .where(AuditLog.tenant_id == tenant_id, AuditLog.entity_type == "Lead", AuditLog.entity_id == lead.id)
                .order_by(AuditLog.timestamp.desc())
            ).all()
            d["history"] = history
            result.append(d)
        result.sort(key=lambda d: d["name"].lower())
        return {"day": target_day.isoformat(), "leads": result}


@app.get("/leads/{lead_id}")
def get_lead(lead_id: int, tenant_id: str = Depends(get_current_tenant)):
    with Session(engine) as session:
        lead = get_or_404(session, Lead, lead_id, tenant_id, "Lead")
        last_outcome_at = _lead_last_outcome_at(session, lead, tenant_id)
        history = session.exec(
            select(AuditLog)
            .where(AuditLog.tenant_id == tenant_id, AuditLog.entity_type == "Lead", AuditLog.entity_id == lead.id)
            .order_by(AuditLog.timestamp.desc())
        ).all()
        d = lead.dict()
        d.update(_lead_next_action(lead, last_outcome_at, date.today()))
        d["last_outcome_at"] = last_outcome_at
        d["history"] = history
        return d


@app.put("/leads/{lead_id}")
def update_lead(lead_id: int, updates: Lead, tenant_id: str = Depends(get_current_tenant)):
    """Editing the lead's own captured details (name/contact/source/
    notes) only — deliberately excludes lead_status/converted_quote_id
    from this generic update: status only ever changes via
    POST /leads/{id}/status (mandatory outcome note) or
    POST /leads/{id}/convert, never a silent field edit here, same
    "no bypassing the audited path" discipline as everywhere else in
    this codebase that gates a status change behind its own endpoint."""
    coerce_date_fields(updates, "visit_date")
    with Session(engine) as session:
        lead = get_or_404(session, Lead, lead_id, tenant_id, "Lead")
        data = updates.dict(exclude_unset=True, exclude={"id", "tenant_id", "lead_status", "converted_quote_id", "created_by", "created_at"})
        for k, v in data.items():
            setattr(lead, k, v)
        session.add(lead)
        session.commit()
        session.refresh(lead)
        return lead


@app.post("/leads/{lead_id}/status")
def change_lead_status(lead_id: int, body: LeadStatusRequest, tenant_id: str = Depends(get_current_tenant), username: str = Depends(get_current_username)):
    """Proof-of-Work principle (Master Workflow proposal §02) — every
    status change requires a real, attributable outcome note, "less
    effort than a WhatsApp, not more": a single field, never a form.
    "converted" is deliberately not a valid target here — a Lead can
    only become Converted by actually linking a real Quote, via
    POST /leads/{id}/convert, never a manual label (§02's "Quoted must
    be backed by a real Quote" rule, applied identically to Lead)."""
    new_status = body.new_status.strip().lower()
    if new_status not in LEAD_TRANSITIONABLE_STATUSES:
        raise HTTPException(400, "Status must be one of: contacted, potential, lost. Use Convert to Quote to mark a lead converted.")
    if not body.note or not body.note.strip():
        raise HTTPException(400, "A short outcome note is required — what happened, in one line.")
    with Session(engine) as session:
        lead = get_or_404(session, Lead, lead_id, tenant_id, "Lead")
        if lead.lead_status in ("converted", "lost"):
            raise HTTPException(400, f"This lead is already {lead.lead_status} — its status can't be changed further.")
        old_status = lead.lead_status
        lead.lead_status = new_status
        session.add(lead)
        _log_lead_status_audit(session, lead, username, old_status, new_status, body.note)
        session.commit()
        session.refresh(lead)
        return lead


@app.post("/leads/{lead_id}/convert")
def convert_lead(lead_id: int, sales_owner: str, branch: str = "gansbaai", client_id: int = None,
                  tenant_id: str = Depends(get_current_tenant), username: str = Depends(get_current_username)):
    """'Convert to Quote' (Master Workflow proposal §02/§06) — the ONLY
    way lead_status can ever become "converted". Creates a genuine new
    Quote (same shape as create_quote() above) and links it back via
    converted_quote_id, rather than a manual label claiming a quote
    exists.

    client_id lets an already-existing Client be linked directly (the
    lead turned out to be an existing client enquiring again); otherwise
    an exact name match is reused (same convention as
    _resolve_or_create_client()) or a brand-new Client is created from
    the lead's own name/contact/source — real data already captured on
    the Lead, carried forward rather than dropped and re-typed from
    scratch. Never overwrites an EXISTING client's real data — phone/
    marketing_source are only ever filled in when a brand-new Client
    record is created here, same "additive, never clobber a real
    record" discipline as SupplierDefault's non-retroactive pre-fill."""
    with Session(engine) as session:
        lead = get_or_404(session, Lead, lead_id, tenant_id, "Lead")
        if lead.lead_status in ("converted", "lost"):
            raise HTTPException(400, f"This lead is already {lead.lead_status} and can't be converted.")
        if client_id:
            client = get_or_404(session, Client, client_id, tenant_id, "Client")
        else:
            existing = session.exec(select(Client).where(Client.tenant_id == tenant_id)).all()
            match = next((c for c in existing if c.name.strip().lower() == lead.name.strip().lower()), None)
            if match:
                client = match
            else:
                # preferred_branch=branch (confirmed Aug 2026, Full Real-
                # Browser Walkthrough & Audit — real gap): this used to be
                # left unset, silently falling back to the Client model's
                # own hardcoded field default ("gansbaai", models.py) no
                # matter which branch this lead/quote actually belongs
                # to. `branch` here is the same real branch the resulting
                # Quote itself gets (just below) — a Hermanus lead now
                # correctly produces a client record that says Hermanus
                # too, not silently mislabeled Gansbaai every time.
                client = Client(tenant_id=tenant_id, name=lead.name, phone=lead.contact, marketing_source=lead.source, created_by=username, preferred_branch=branch)
                session.add(client)
                session.commit()
                session.refresh(client)
        quote = Quote(
            client_name=client.name, client_id=client.id, sales_owner=sales_owner,
            branch=branch, site_address=client.address or "", tenant_id=tenant_id,
        )
        session.add(quote)
        session.commit()
        session.refresh(quote)

        old_status = lead.lead_status
        lead.lead_status = "converted"
        lead.converted_quote_id = quote.id
        session.add(lead)
        _log_lead_status_audit(session, lead, username, old_status, "converted", f"Converted to Quote #{quote.id}")
        session.commit()
        # This second commit expires every object still attached to this
        # session (SQLAlchemy's default expire_on_commit) — including the
        # already-refreshed `quote` above. Real bug caught before shipping
        # (not a live incident): without re-refreshing it here too, `quote`
        # serializes as an empty {} in the response below, since its
        # attributes are now expired and the session is about to close.
        session.refresh(lead)
        session.refresh(quote)
        return {"lead": lead, "quote": quote}


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


def _quote_delete_dependencies(session: Session, quote: "Quote", tenant_id: str) -> List[str]:
    """Order Index Bulk Delete brief (confirmed Aug 2026) — "check
    whether an order is referenced elsewhere in the system (linked
    invoice, payment/deposit record, client job history)... same
    principle already established for Supplier Console product
    deletion" (see the deletions-validated-first pass in
    commit_supplier_console_changes). This app has no separate
    Invoice/Payment table — those are fields on Quote itself — so a
    real recorded deposit/final payment counts as "real dependency"
    directly. Returns a list of human-readable reasons this quote
    should NOT be deleted; empty means safe. Checked read-only, before
    anything is touched, same "abort the whole batch rather than
    partially delete" guarantee that Supplier Console pass already
    gives."""
    reasons = []
    if session.exec(select(HoursWorked).where(HoursWorked.quote_id == quote.id, HoursWorked.tenant_id == tenant_id)).first():
        reasons.append("has hours logged against it")
    linked_estimate = session.exec(
        select(BuilderEstimate).where(BuilderEstimate.linked_quote_id == quote.id, BuilderEstimate.tenant_id == tenant_id)
    ).first()
    if linked_estimate:
        reasons.append(f"is linked to a builder estimate (#{linked_estimate.id})")
    if quote.deposit_paid_date or quote.final_payment_date:
        reasons.append("has a recorded deposit or final payment")
    return reasons


# Robust Owner Delete brief (confirmed Aug 2026) — root-cause fix for a
# bug pattern that had already bitten this project THREE separate times
# under the old hand-listed-per-table approach: PaymentFollowUp/
# ColourChangeLog missed first, then QuotePhoto, then OrderSheet/
# OrderSheetLine (real production incident, "Delete Not Working on
# Quote #62"). Each time, a new feature added a table with a foreign
# key pointing at quote.id, and the delete function silently forgot
# about it — surfacing as an unhandled Postgres FK-constraint 500 in
# production rather than a clean delete or a clear error.
#
# The fix is two parts, both required:
#   1. _CASCADE_POLICY is an explicit, reviewed decision for every table
#      that currently references quote.id/client.id/quotelineitem.id/
#      ordersheet.id — "cascade" (delete the child too, recursing into
#      ITS OWN dependents first), "nullify" (keep the row, clear the
#      reference — for a soft backward-link that shouldn't vanish just
#      because the quote it points at did), or "assert_empty" (this FK
#      is guaranteed to have zero matching rows by a pre-delete
#      dependency check elsewhere — e.g. _quote_delete_dependencies
#      already blocks deleting a quote with logged hours or a linked
#      builder estimate outright, so cascade code should never actually
#      encounter a row here; if it ever does, that means the pre-check
#      has a bug, and this raises loudly rather than silently deleting
#      or orphaning real business data).
#   2. _verify_cascade_policy_complete(), run at startup, scans the
#      REAL SQLAlchemy schema for every foreign key pointing at one of
#      the tables above and fails app startup immediately if any of
#      them has no entry in _CASCADE_POLICY. This is what actually
#      closes the failure class for good: a future table with
#      foreign_key="quote.id" now can't reach production without a
#      developer consciously deciding its policy — no more "found six
#      months later via a real customer's failed delete click."
#
# Side effects that a DB-level cascade could never handle anyway
# (deleting the QuotePhoto's real file out of Supabase Storage) stay as
# an explicit per-entry side_effect callable, same reasoning that ruled
# out a pure DB-level ON DELETE CASCADE for this brief.
_CASCADE_POLICY = {
    "quote": [
        (QuoteLineItem, "quote_id", "cascade", None),
        (OrderSheet, "quote_id", "cascade", None),
        (PaymentFollowUp, "quote_id", "cascade", None),
        (QuotePhoto, "quote_id", "cascade", lambda p: photo_storage.delete_photo(p.storage_path)),
        (HoursWorked, "quote_id", "assert_empty", None),          # blocked upstream by _quote_delete_dependencies
        (BuilderEstimate, "linked_quote_id", "assert_empty", None),  # blocked upstream by _quote_delete_dependencies
        (Lead, "converted_quote_id", "nullify", None),  # Leads brief (confirmed Aug 2026): the enquiry genuinely happened and is real history — deleting the quote it converted into must not delete the Lead, only clear the backward-link (lead_status stays "converted", same as a builder estimate surviving a Force Delete with only its link cleared)
    ],
    "quotelineitem": [
        (ColourChangeLog, "quote_line_item_id", "cascade", None),
    ],
    "ordersheet": [
        (OrderSheetLine, "order_sheet_id", "cascade", None),
    ],
    "client": [
        (Quote, "client_id", "assert_empty", None),  # blocked upstream by delete_client()'s own pre-check
    ],
}


def _cascade_delete_children(session: Session, table_key: str, parent_id: int, tenant_id: str, force: bool = False):
    """Generic executor for _CASCADE_POLICY, rooted at table_key
    (e.g. "quote"). Recurses into each cascaded child's OWN dependents
    first and commits before deleting that child, generalizing the
    ordering fix an earlier incident already established for OrderSheet/
    OrderSheetLine specifically: Postgres enforces FK constraints
    (SQLite never catches this locally), so batching a parent delete
    into the same flush as its not-yet-committed child deletes fails —
    deepest rows must be committed-deleted before anything that points
    at them is deleted.

    force (Force Delete override, confirmed Aug 2026 — real case: the
    "John Cena" mockup had progressed all the way to a Completed Job
    with a real install date, deposit, and final payment recorded,
    which _quote_delete_dependencies correctly blocks a NORMAL delete
    for) changes what "assert_empty" means: normally it's a defense-in-
    depth assertion that _quote_delete_dependencies already guaranteed
    zero matching rows before cascade code ever runs, so finding one
    means the PRE-CHECK has a bug and this raises loudly. Under an
    explicit Owner force-override, that pre-check is deliberately
    bypassed, so real rows (logged hours, a linked builder estimate)
    can legitimately be present — those are real historical/payroll
    records that must survive even a forced delete, so force mode
    treats "assert_empty" as "nullify" instead of raising: the record
    is kept, only its link to the deleted quote is cleared."""
    for child_model, fk_col, policy, side_effect in _CASCADE_POLICY.get(table_key, []):
        rows = session.exec(
            select(child_model).where(getattr(child_model, fk_col) == parent_id, child_model.tenant_id == tenant_id)
        ).all()
        if not rows:
            continue
        if policy == "cascade":
            child_key = child_model.__tablename__.lower()
            for row in rows:
                if child_key in _CASCADE_POLICY:
                    _cascade_delete_children(session, child_key, row.id, tenant_id, force=force)
                if side_effect:
                    side_effect(row)  # best-effort storage/external cleanup — never blocks the DB delete
                session.delete(row)
            session.commit()
        elif policy == "nullify":
            for row in rows:
                setattr(row, fk_col, None)
                session.add(row)
            session.commit()
        elif policy == "assert_empty":
            if force:
                for row in rows:
                    setattr(row, fk_col, None)
                    session.add(row)
                session.commit()
            else:
                raise RuntimeError(
                    f"Cascade-delete safety net triggered: {child_model.__tablename__}.{fk_col} has "
                    f"{len(rows)} row(s) pointing at {table_key}.id={parent_id}, but this dependency is "
                    f"supposed to be blocked upstream before delete is ever attempted. The upstream "
                    f"pre-check has a bug — fix that, don't loosen this policy."
                )


def _verify_cascade_policy_complete():
    """Startup-time safety net — the actual root-fix. Scans every real
    ForeignKeyConstraint in the live schema pointing at a table this app
    ever cascade-deletes from, and raises immediately if any of them has
    no entry in _CASCADE_POLICY above. Fails app startup, not a future
    customer's delete click — see the big comment on _CASCADE_POLICY."""
    tracked_tables = set(_CASCADE_POLICY.keys())
    known = {(child_model.__tablename__.lower(), fk_col)
             for entries in _CASCADE_POLICY.values()
             for child_model, fk_col, _policy, _side_effect in entries}
    for table in SQLModel.metadata.tables.values():
        for fk in table.foreign_keys:
            target_table = fk.column.table.name
            if target_table in tracked_tables:
                key = (table.name, fk.parent.name)
                if key not in known:
                    raise RuntimeError(
                        f"Cascade-delete policy gap: {table.name}.{fk.parent.name} references "
                        f"{target_table}.{fk.column.name} but has no entry in _CASCADE_POLICY (main.py). "
                        f"Add one (cascade / nullify / assert_empty, plus any needed side-effect) before "
                        f"this can ship — this check exists specifically to stop the recurring "
                        f"silent-cascade-miss bug (hit three times before: ColourChangeLog/PaymentFollowUp, "
                        f"QuotePhoto, OrderSheet/OrderSheetLine)."
                    )


def _delete_quote_cascade(session: Session, quote: "Quote", tenant_id: str, force: bool = False):
    """Deletes a quote — which IS the Job (job_number/workflow_status/
    installation_date/completion_date all live directly on this same
    row; there is no separate Job table) — and everything hanging off
    it, per _CASCADE_POLICY above. This is also what makes any KPI it
    contributed to self-correct on next load with zero extra work:
    analytics_overview() (Business Overview Dashboard) queries `select
    (Quote)` fresh on every call, same "derive, don't duplicate state
    that could drift" principle as everywhere else in this codebase —
    there is no separate cached/materialized KPI table that could go
    stale, confirmed directly (Force Delete brief, Aug 2026) by reading
    real Won Value/conversion figures before and after a real deletion.
    Shared by the single-quote and bulk-delete endpoints so this
    cascade exists in exactly one place, not two that could quietly
    drift apart (same reasoning as _builder_commission_for_quote).
    Caller is responsible for the dependency check
    (_quote_delete_dependencies, unless force=True), the AuditLog
    entry, and session.commit() for the quote row itself."""
    _cascade_delete_children(session, "quote", quote.id, tenant_id, force=force)
    session.delete(quote)


@app.delete("/quotes/{quote_id}")
def delete_quote(quote_id: int, purge_dropbox_archive: bool = False, force: bool = False,
                  role: str = Depends(require_owner),
                  tenant_id: str = Depends(get_current_tenant), username: str = Depends(get_current_username)):
    """Owner-only (confirmed Aug 2026, Order Index Bulk Delete brief) —
    the brief's own hard requirement applies here too, not just the new
    bulk action below: this single-quote delete is the exact same
    destructive action, just one at a time, so leaving it open to Sales/
    Admin would trivially defeat the whole point. Blocked outright (not
    just warned) if the quote has a real dependency elsewhere — see
    _quote_delete_dependencies — and every deletion now writes to the
    AuditLog, neither of which this endpoint did before this brief.

    purge_dropbox_archive (Robust Owner Delete brief, confirmed Aug
    2026): an explicit, OFF-by-default choice — preserving archived
    Dropbox copies stays the default behavior for every routine delete,
    exactly as already verified in the Dropbox Archive brief, since
    that's what protects real business data from being lost twice over
    (once live, once in the backup). Only when the Owner deliberately
    opts in (mockup/test cleanup, e.g. the "John Cena" quote) are the
    quote's own archived Quote/Invoice PDFs, plus every archived Order
    Sheet PDF for this job, genuinely removed from Dropbox — not just
    hidden. DocumentArchive.entity_id is a plain indexed int, not a real
    foreign key (confirmed during the "Delete Not Working on Quote #62"
    incident), so these rows are gathered explicitly here rather than
    through _CASCADE_POLICY, which only governs real FK-backed tables.

    force (Force Delete override, confirmed Aug 2026 — real case: the
    "John Cena" mockup quote had progressed all the way to a Completed
    Job with a real installation date, deposit, and final payment
    recorded, which _quote_delete_dependencies correctly blocks a
    NORMAL delete for). OFF by default — this bypasses the exact
    safety check that protects real business quotes with real payment
    history from accidental deletion, so it must stay a deliberate,
    separate choice, never the default. When on, the reasons that would
    normally have blocked this delete are recorded to AuditLog verbatim
    (what was overridden, not just that something was), and the
    dynamic cascade (_delete_quote_cascade/_cascade_delete_children,
    same mechanism as every other delete — no separate, narrower path)
    still runs in full: every Order Sheet/line, colour log, follow-up,
    and photo hanging off this quote is genuinely removed, and real
    historical records that must never be destroyed even by a forced
    delete (logged hours, a linked builder estimate) are preserved and
    only unlinked, never deleted outright."""
    with Session(engine) as session:
        quote = get_or_404(session, Quote, quote_id, tenant_id, "Quote")
        reasons = _quote_delete_dependencies(session, quote, tenant_id)
        if reasons and not force:
            raise HTTPException(400, f"Can't delete Quote #{quote.id} ({quote.client_name}) — it {' and '.join(reasons)}. Resolve this first if you really need to delete it, or use Force Delete if this is deliberate mockup/test cleanup.")
        label = f"Quote #{quote.id} — {quote.client_name}" + (f" ({quote.description})" if quote.description else "")
        session.add(AuditLog(
            tenant_id=tenant_id, username=username, entity_type="Quote", entity_id=quote.id,
            field="__deleted__", old_value=label, new_value="(deleted)",
        ))
        if force and reasons:
            session.add(AuditLog(
                tenant_id=tenant_id, username=username, entity_type="Quote", entity_id=quote.id,
                field="force_delete_override", old_value=' and '.join(reasons),
                new_value="deleted anyway (Owner Force Delete)",
            ))

        archive_note = "archive preserved (default)"
        if purge_dropbox_archive:
            sheet_ids = [s.id for s in session.exec(
                select(OrderSheet).where(OrderSheet.quote_id == quote.id, OrderSheet.tenant_id == tenant_id)
            ).all()]
            archives = session.exec(
                select(DocumentArchive).where(
                    DocumentArchive.tenant_id == tenant_id,
                    DocumentArchive.entity_type.in_(["Quote", "Invoice"]),
                    DocumentArchive.entity_id == quote.id,
                )
            ).all()
            if sheet_ids:
                archives += session.exec(
                    select(DocumentArchive).where(
                        DocumentArchive.tenant_id == tenant_id,
                        DocumentArchive.entity_type == "OrderSheet",
                        DocumentArchive.entity_id.in_(sheet_ids),
                    )
                ).all()
            purged_paths = []
            for a in archives:
                if a.dropbox_path:
                    result = dropbox_archive.delete_document(a.dropbox_path)  # never raises — best-effort
                    if result.get("ok"):
                        purged_paths.append(a.dropbox_path)
                session.delete(a)
            archive_note = (
                f"archive purge requested — {len(purged_paths)}/{len(archives)} Dropbox file(s) removed"
                if archives else "archive purge requested (no archived files existed)"
            )
        session.add(AuditLog(
            tenant_id=tenant_id, username=username, entity_type="Quote", entity_id=quote.id,
            field="dropbox_archive", old_value="n/a", new_value=archive_note,
        ))

        _delete_quote_cascade(session, quote, tenant_id, force=force)
        session.commit()
        return {"deleted": quote_id}


class BulkDeleteQuotesRequest(BaseModel):
    quote_ids: List[int]


@app.post("/quotes/bulk-delete")
def bulk_delete_quotes(body: BulkDeleteQuotesRequest, role: str = Depends(require_owner),
                        tenant_id: str = Depends(get_current_tenant), username: str = Depends(get_current_username)):
    """Order Index Bulk Delete, Owner-only (confirmed Aug 2026).
    require_owner reads through get_current_role, so an Owner previewing
    as Sales/Admin is correctly blocked here too, same enforcement path
    as everywhere else in Owner Preview Mode — satisfies the brief's
    "same server-side role-stripping pattern already established
    elsewhere" instruction directly.

    Every selected quote is dependency-checked FIRST, as a pure read —
    if ANY of them is blocked, the whole batch is rejected and nothing
    is deleted, same "abort the whole batch, nothing partially saved"
    guarantee the Supplier Console's own delete validation already
    gives. The frontend confirmation dialog is what shows client names/
    order refs before this is ever called (brief Section 3) — this
    endpoint's own error message repeats that detail too, in case it's
    ever called directly."""
    if not body.quote_ids:
        raise HTTPException(400, "No orders selected.")
    with Session(engine) as session:
        quotes = [get_or_404(session, Quote, qid, tenant_id, "Quote") for qid in body.quote_ids]
        blocked = []
        for q in quotes:
            reasons = _quote_delete_dependencies(session, q, tenant_id)
            if reasons:
                blocked.append(f"Quote #{q.id} ({q.client_name}) — {' and '.join(reasons)}")
        if blocked:
            raise HTTPException(400, "Can't delete — " + "; ".join(blocked) + ". Resolve these first, or deselect them and try again.")

        deleted_labels = []
        for q in quotes:
            label = f"Quote #{q.id} — {q.client_name}" + (f" ({q.description})" if q.description else "")
            deleted_labels.append(label)
            session.add(AuditLog(
                tenant_id=tenant_id, username=username, entity_type="Quote", entity_id=q.id,
                field="__deleted__", old_value=label, new_value="(deleted)",
            ))
            _delete_quote_cascade(session, q, tenant_id)
        session.commit()
        return {"deleted_count": len(quotes), "deleted": deleted_labels}


class DuplicateQuoteRequest(BaseModel):
    # client_name (free text) deliberately REMOVED (confirmed Aug 2026,
    # Save Redirect + Client Link Missing brief) — this was the actual,
    # confirmed root cause of duplicated quotes losing their real client
    # link: the frontend's old prompt() pre-filled the source's
    # client_name as editable TEXT, and any edit at all — including a
    # well-intentioned note typed into what looked like a free-text box
    # — got sent back as a "different client" by name, with no real
    # client_id behind it, silently orphaning the duplicate. The ONLY
    # way to change the client on a duplicate now is a validated
    # client_id — never free text — so this whole failure mode can't
    # recur, from this endpoint or any future caller of it.
    client_id: Optional[int] = None


@app.post("/quotes/{quote_id}/duplicate")
def duplicate_quote(quote_id: int, body: DuplicateQuoteRequest, tenant_id: str = Depends(get_current_tenant)):
    """Duplicate Quote (confirmed Aug 2026) — a TRUE independent copy:
    every field on every new row is copied by VALUE into brand new
    database rows (fresh ids), never a shared/linked record, so editing
    one can never touch the other, per the brief's own explicit
    requirement.

    What's copied: every line item exactly as calculated (flooring,
    trim, stairwell, blinds, misc/floor-prep — copied field-for-field
    rather than recalculated, so the duplicate's numbers are guaranteed
    identical to the source's, not just close — same "don't build a
    second copy of pricing logic that could drift" discipline this
    whole app follows), plus quote-level pricing/structure (discount_pct,
    transport_levy, deposit_pct, sales_owner, branch,
    blinds_measurements_visible).

    What's deliberately NOT copied, and why:
      - status: always resets to "draft" (brief's own explicit rule),
        even if the source was accepted/invoiced/paid.
      - Order Details tracking fields (site_address, installation_date,
        invoice_sent_date, deposit_paid_date/method, final_payment_date/
        method) and xero_quote_id: these describe a SPECIFIC execution
        of a SPECIFIC job — carrying final_payment_date forward, for
        instance, would make a brand new draft look already fully paid.
        Not called out either way in the brief; left blank as the
        clearly safer default rather than assumed.
      - Site photos: confirmed directly with Burgert — always excluded
        on duplicate, no carry-over option, for this pilot.
      - created_at / id: fresh, real ("Fresh quote reference/ID, fresh
        date" per the brief).

    Client: defaults to the source quote's own client (by real
    client_id, not a name comparison), but the caller can override with
    a different client_id — a real, validated Client record, never free
    text (confirmed Aug 2026 — see DuplicateQuoteRequest's own comment
    for the bug this closes) — confirmed directly: "allow Burgert to
    pick a different client instead... reusing a quote's structure as a
    starting template for a similar job for a DIFFERENT client"."""
    with Session(engine) as session:
        source = get_or_404(session, Quote, quote_id, tenant_id, "Quote")
        new_client_id = source.client_id
        new_client_name = source.client_name
        if body.client_id is not None:
            client = get_or_404(session, Client, body.client_id, tenant_id, "Client")
            new_client_id = client.id
            new_client_name = client.name

        new_quote = Quote(
            tenant_id=tenant_id,
            client_name=new_client_name,
            client_id=new_client_id,
            sales_owner=source.sales_owner,
            branch=source.branch,
            blinds_measurements_visible=source.blinds_measurements_visible,
            status="draft",
            workflow_status="quoted",   # explicit, not just relying on the model default — a duplicate is always a fresh quote, never inherits the source's job_number/accepted_at either (both correctly left unset here)
            discount_pct=source.discount_pct,
            transport_levy=source.transport_levy,
            deposit_pct=source.deposit_pct,
            description=f"Copy of {source.description}" if source.description else f"Copy of Quote #{source.id}",
        )
        session.add(new_quote)
        session.commit()
        session.refresh(new_quote)

        lines = session.exec(
            select(QuoteLineItem).where(QuoteLineItem.quote_id == quote_id, QuoteLineItem.tenant_id == tenant_id)
        ).all()
        for line in lines:
            data = line.dict(exclude={"id", "quote_id", "tenant_id"})
            session.add(QuoteLineItem(tenant_id=tenant_id, quote_id=new_quote.id, **data))
        session.commit()
        session.refresh(new_quote)
        return {"quote": new_quote, "lines_copied": len(lines)}


# Persistent Summary Panel / Carpet Tab integration gap (confirmed Aug
# 2026, real bug found live testing the just-shipped Carpet Tab, Type
# Split, and Product Filtering fix, not assumed) — NEXBAC 920 Tile
# deliberately reuses this same, unmodified add_flooring_line()/
# edit_flooring_line() pair (it's genuinely box/m²-shaped, same as
# Vinyl — see the Carpet Tab card's own comment, index.html), but
# neither endpoint ever set carpet_category on the line it created —
# that param only ever existed on add_carpet_line()/edit_carpet_line()
# (the other 3 types' own endpoint). Confirmed live: adding a NEXBAC
# 920 Tile line from the Carpet tab landed the line correctly (right
# total, right product) but the persistent summary panel bucketed it
# under "Flooring" instead of "Carpet" (lineSummaryBucket(), quote-
# builder.js, keys off carpet_category being truthy) — "Carpet" stayed
# stuck showing only its other lines while a real Tile line sat
# invisibly inside the Flooring bucket instead. Fixed at the single
# source of truth — product.flooring_category — rather than requiring
# every caller of these two endpoints to remember to pass it: any
# flooring line built from one of the four Carpet Calculators product
# categories is tagged here automatically, tile included, regardless of
# which screen/entry point created it.
CARPET_ONLY_CATEGORIES = ("carpet_tufted_broadloom", "carpet_needlepunch_broadloom", "carpet_tile", "cushion_vinyl")
# Category-Aware Contracts, Fix #1 (confirmed Aug 2026, Live Consistency
# Monitor brief — "sequence directly alongside") — ONE authoritative
# registry, not the three independently hand-typed tuples the
# investigation found (this one, a second copy of this one, and a
# same-but-different "roll types only" one inside generate_order_sheets()
# further up this file). Derived here, once, rather than hand-typed a
# second time — carpet_tile is the one category that genuinely belongs
# in the box-based path, never the roll-based one.
CARPET_ROLL_CATEGORIES = tuple(c for c in CARPET_ONLY_CATEGORIES if c != "carpet_tile")


@app.post("/quotes/{quote_id}/lines/flooring")
def add_flooring_line(quote_id: int, product_id: int, quantity_m2: float,
                       job_type: JobType, discount_pct: float = 0.0,
                       glue_cost_per_unit: float = 0.0, glue_coverage_m2: float = 0.0,
                       labour_rate_per_m2: float = 45.0,
                       bag_cost: float = 235.0, bag_coverage_m2: float = None,
                       own_staff: bool = True, markup_override: float = None,
                       include_tile_removal_fee: bool = False,
                       apply_delivery_fee: bool = True,
                       role: str = Depends(get_current_role), tenant_id: str = Depends(get_current_tenant),
                       username: str = Depends(get_current_username)):
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
    apply_delivery_fee (confirmed Aug 2026, Transport/Courier Toggle
    Relocation brief): defaults True (unchanged behaviour for any caller
    that doesn't pass it) — per-JOB override of the product's own
    delivery_fee_per_m2, so Burgert can turn courier off for one specific
    room without touching the supplier-wide default.
    """
    with Session(engine) as session:
        quote = get_or_404(session, Quote, quote_id, tenant_id, "Quote")
        product = _require_active_flooring_product(get_or_404(session, FlooringProduct, product_id, tenant_id, "Flooring product"))
        settings = get_settings(session, tenant_id)

        calc = calculate_flooring_line(
            resolve_zone_price(session, tenant_id, product, settings), quantity_m2, job_type, discount_pct,
            glue_cost_per_unit, glue_coverage_m2, labour_rate_per_m2,
            bag_cost, bag_coverage_m2, own_staff, markup_override,
            include_tile_removal_fee,
            apply_delivery_fee=apply_delivery_fee,
            margin_warn_threshold=settings.flooring_margin_warn_threshold,
            tile_removal_fee_per_m2_incl_vat=settings.tile_removal_fee_per_m2_incl_vat,
            vat_pct=settings.vat_pct,
        )
        line = QuoteLineItem(
            quote_id=quote_id, category="flooring", product_id=product_id, tenant_id=tenant_id,
            product_name=product.product_name, colour=product.colour, original_colour=product.colour,
            job_type=job_type, flooring_pricing_type=product.pricing_type,
            carpet_category=product.flooring_category if product.flooring_category in CARPET_ONLY_CATEGORIES else None,
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
            boxes_needed=calc.get("packs_needed"),
            compound_cost_total=calc["compound_cost_total"],
            tile_removal_fee_total=calc["tile_removal_fee_total"],
            delivery_fee_total=calc["delivery_fee_total"],
            total_job_cost=calc["total_job_cost"],
        )
        session.add(line)
        _log_quote_line_audit(session, quote, username, "added", f"Flooring — {product.product_name}{', ' + product.colour if product.colour else ''}, {quantity_m2}m²")
        session.commit()
        session.refresh(line)

        result = strip_sensitive_fields(line.dict(), role, settings)
        # Warning text itself contains the margin % — only show it to
        # roles that are allowed to see margin at all (Owner), or it defeats
        # the point of stripping margin_pct as a field above (Margin
        # Becomes Owner-Only; Price Gets a Colour Signal, confirmed Aug 2026).
        if calc["warning"] and role == UserRole.owner:
            result["warning"] = calc["warning"]
        if "packs_needed" in calc:
            result["packs_needed"] = calc["packs_needed"]
        if "glue_units_needed" in calc:
            result["glue_units_needed"] = calc["glue_units_needed"]
            result["glue_sell_total"] = calc["glue_sell_total"]
        return result


@app.post("/quotes/{quote_id}/lines/blinds")
def add_blinds_line(quote_id: int, product_id: int, width_mm: float, drop_mm: float,
                     discount_pct: float = 0.0, role: str = Depends(get_current_role), tenant_id: str = Depends(get_current_tenant),
                     username: str = Depends(get_current_username)):
    with Session(engine) as session:
        quote = get_or_404(session, Quote, quote_id, tenant_id, "Quote")
        product = get_or_404(session, BlindsProduct, product_id, tenant_id, "Blinds product")
        settings = get_settings(session, tenant_id)

        calc = calculate_blinds_line(product, width_mm, drop_mm, discount_pct)
        line = QuoteLineItem(
            quote_id=quote_id, category="blinds", product_id=product_id, tenant_id=tenant_id,
            product_name=product.product_name, width_mm=width_mm, drop_mm=drop_mm,
            discount_pct=discount_pct,
            unit_cost=calc["unit_cost"], unit_price=calc["unit_price"],
            line_total=calc["line_total"], margin_pct=calc["margin_pct"],
        )
        session.add(line)
        _log_quote_line_audit(session, quote, username, "added", f"Blinds — {product.product_name}, {width_mm}×{drop_mm}mm")
        session.commit()
        session.refresh(line)

        return strip_sensitive_fields(line.dict(), role, settings)


def _trim_line_category(product: "TrimProduct") -> str:
    """Skirting/Trim category fix (confirmed Aug 2026, Vinyl Redesign:
    Real Usage Findings brief §1) — the ONE place that decides which
    top-level QuoteLineItem.category a trim-book product actually gets,
    shared by add_trim_line()/edit_trim_line() so this can never drift
    between the two the way the hardcoded "trim" literal used to be
    duplicated in both. Same skirting/quarter_round set already used
    everywhere else this distinction is made (refreshLineProductOptions(),
    quote-builder.js; the on_startup() backfill just above)."""
    return "skirting" if product.category in ("skirting", "quarter_round") else "trim"


@app.post("/quotes/{quote_id}/lines/trims")
def add_trim_line(quote_id: int, product_id: int, length_m: float,
                   discount_pct: float = 0.0, colour: str = "", role: str = Depends(get_current_role), tenant_id: str = Depends(get_current_tenant),
                   username: str = Depends(get_current_username)):
    """colour (Trim Colours brief, confirmed Aug 2026) — Trim gets exactly
    two real choices, Champagne and Silver, per Burgert's own explicit
    instruction; reuses QuoteLineItem.colour, the SAME field Flooring/
    Vinyl lines already use, rather than a new one-off column —
    "reuse the existing colour-selection mechanism," per the brief's own
    words. Skirting lines never send this (no colour choice for
    skirting was asked for), so it stays blank there, same as before."""
    with Session(engine) as session:
        quote = get_or_404(session, Quote, quote_id, tenant_id, "Quote")
        product = get_or_404(session, TrimProduct, product_id, tenant_id, "Trim product")
        settings = get_settings(session, tenant_id)

        calc = calculate_trim_line(product, length_m, discount_pct, margin_warn_threshold=settings.flooring_margin_warn_threshold)
        line = QuoteLineItem(
            quote_id=quote_id, category=_trim_line_category(product), product_id=product_id, tenant_id=tenant_id,
            product_name=product.product_name, length_m=length_m,
            trim_sub_category=product.category, colour=colour,
            discount_pct=discount_pct,
            unit_cost=calc["unit_cost"], unit_price=calc["unit_price"],
            line_total=calc["line_total"], margin_pct=calc["margin_pct"],
        )
        session.add(line)
        _log_quote_line_audit(session, quote, username, "added", f"Trim — {product.product_name}, {length_m}lm")
        session.commit()
        session.refresh(line)

        result = strip_sensitive_fields(line.dict(), role, settings)
        if calc["warning"] and role == UserRole.owner:
            result["warning"] = calc["warning"]
        return result


def _compute_stairwell_calc(session: Session, tenant_id: str, vinyl_product_id: int, nosing_product_id: int,
                             num_stairs: int, stairwell_type: StairwellType,
                             stair_area_m2: float = 0.45, own_staff: bool = True,
                             landing_area_m2: float = 0.0) -> dict:
    """Extracted (confirmed Aug 2026, Vinyl Quoting UX Redesign proposal
    §10, Phase 4, approved) from add_stairwell_line() — this was about
    to become a THIRD copy of the exact same product-lookup +
    calculate_stairwell_line() + landing-combining logic (add, edit,
    now preview), so it's pulled out once instead. Returns everything a
    caller could need: the two products (for building/updating a real
    line), the raw per-part calcs, and the combined totals a stairwell
    line always displays as one figure. Deliberately does NOT touch
    the database or Quote/QuoteLineItem at all — callers that need to
    persist something still do that themselves, same division of
    responsibility calculate_stairwell_line() itself already has.

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
    line's fields instead of becoming its own QuoteLineItem row."""
    vinyl_product = _require_active_flooring_product(get_or_404(session, FlooringProduct, vinyl_product_id, tenant_id, "Vinyl product"))
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

    return {
        "vinyl_product": vinyl_product, "nosing_product": nosing_product,
        "calc": calc, "landing_calc": landing_calc,
        "combined_line_total": combined_line_total, "combined_total_job_cost": combined_total_job_cost,
        "combined_margin_pct": combined_margin_pct, "combined_labour_charged": combined_labour_charged,
        "combined_labour_cost": combined_labour_cost, "combined_warning": combined_warning,
    }


def _compute_carpet_calc(session: Session, tenant_id: str, product_id: int, quantity_lm: float,
                          discount_pct: float = 0.0, own_staff: bool = True, labour_rate_per_m2: float = None,
                          apply_cutting_fee: bool = False, apply_grippers: bool = False, gripper_perimeter_m: float = 0.0,
                          apply_underfelt: bool = False, apply_glue: bool = False, markup_override: float = None) -> dict:
    """Carpet Calculators (confirmed Aug 2026, Carpet Calculators Final
    Build Brief) — shared by add_carpet_line()/edit_carpet_line() below
    and the live preview branch (preview_line()), same "exactly one
    formula per category, whether previewing or actually saving" reasoning
    _compute_stairwell_calc() above already established. Resolves every
    confirmed real rate from BusinessSettings/the product record before
    calling calculate_carpet_line() (calculations.py) — never touches
    calculate_flooring_line() or its Vinyl callers at all.

    labour_rate_per_m2 resolution (confirmed Aug 2026): per-line override
    if given, else the product's own FlooringProduct.labour_rate_per_m2
    (Tufted Broadloom's own still-open R35-vs-R45 question is resolved
    THIS way — set it on the specific product if it genuinely needs to
    differ), else BusinessSettings.default_labour_rate_per_m2 (45.0, the
    same confirmed rate Vinyl itself uses) — deliberately no separate
    "carpet labour rate" setting, per the brief's own explicit instruction
    not to hardcode a second rate per category.

    Cutting fee rate is category-specific: Cushion Vinyl uses its own
    (still-unconfirmed, defaults to 0/off) BusinessSettings.
    carpet_cushion_vinyl_cutting_fee — never assumed equal to Broadloom's
    confirmed R350, per the brief's explicit "don't assume R543 or R350
    either way" instruction. Every other carpet category uses
    carpet_cutting_fee (R350, confirmed current, found identically in two
    independent reference spreadsheets)."""
    product = _require_active_flooring_product(get_or_404(session, FlooringProduct, product_id, tenant_id, "Carpet product"))
    settings = get_settings(session, tenant_id)
    resolved_labour_rate = (
        labour_rate_per_m2 if labour_rate_per_m2 is not None
        else (product.labour_rate_per_m2 if product.labour_rate_per_m2 is not None else settings.default_labour_rate_per_m2)
    )
    cutting_fee_rate = settings.carpet_cushion_vinyl_cutting_fee if product.flooring_category == "cushion_vinyl" else settings.carpet_cutting_fee
    gripper_cost_per_lm = (settings.carpet_gripper_cost_per_box / settings.carpet_gripper_lm_per_box) if settings.carpet_gripper_lm_per_box else 0.0
    calc = calculate_carpet_line(
        product, quantity_lm, discount_pct=discount_pct, markup_override=markup_override,
        labour_rate_per_m2=resolved_labour_rate, own_staff=own_staff,
        apply_cutting_fee=apply_cutting_fee, cutting_fee=cutting_fee_rate,
        apply_grippers=apply_grippers, gripper_perimeter_m=gripper_perimeter_m, gripper_cost_per_lm=gripper_cost_per_lm,
        apply_underfelt=apply_underfelt, underfelt_roll_width_m=settings.carpet_underfelt_roll_width_m,
        underfelt_cost_per_m2=settings.carpet_underfelt_cost_per_m2,
        # Glue coverage (confirmed Aug 2026): reuses the SAME confirmed
        # 70m²/20L-drum coverage Vinyl's own glue calculation already
        # uses everywhere (stairwell, Builder Portal) — the brief's own
        # explicit instruction was "the coverage-per-litre figure must
        # come from Vinyl's existing logic, not [the Teckem price list]",
        # which states price only, no coverage rate.
        apply_glue=apply_glue, glue_cost_per_unit=settings.carpet_glue_cost_per_20l_drum,
        glue_coverage_m2=settings.stairwell_default_glue_coverage_m2,
        margin_warn_threshold=settings.flooring_margin_warn_threshold,
    )
    return {"product": product, "calc": calc}


@app.post("/quotes/{quote_id}/lines/carpet")
def add_carpet_line(quote_id: int, product_id: int, quantity_lm: float, carpet_category: str,
                     discount_pct: float = 0.0, own_staff: bool = True, labour_rate_per_m2: float = None,
                     apply_cutting_fee: bool = False, apply_grippers: bool = False, gripper_perimeter_m: float = 0.0,
                     apply_underfelt: bool = False, apply_glue: bool = False,
                     role: str = Depends(get_current_role), tenant_id: str = Depends(get_current_tenant),
                     username: str = Depends(get_current_username)):
    """See _compute_carpet_calc() just above for the actual formula/rate-
    resolution logic, shared with edit_carpet_line() and the preview
    endpoint — this function's own job is turning that computation into a
    real, persisted line. category="flooring" deliberately (not a new
    QuoteLineItem category) — every existing category=="flooring"
    consumer (generate_order_sheets(), the Job Card, the printed quote,
    the Quote Lines detail column) already needs to handle this line
    correctly with zero new category-based sweep; carpet_category is the
    denormalized snapshot telling those consumers which of the four real
    shapes this specific line is."""
    with Session(engine) as session:
        quote = get_or_404(session, Quote, quote_id, tenant_id, "Quote")
        settings = get_settings(session, tenant_id)
        r = _compute_carpet_calc(session, tenant_id, product_id, quantity_lm, discount_pct, own_staff, labour_rate_per_m2,
                                  apply_cutting_fee, apply_grippers, gripper_perimeter_m, apply_underfelt, apply_glue)
        product, calc = r["product"], r["calc"]

        line = QuoteLineItem(
            quote_id=quote_id, category="flooring", tenant_id=tenant_id,
            product_id=product_id, product_name=product.product_name, colour=product.colour, original_colour=product.colour,
            flooring_pricing_type=product.pricing_type, carpet_category=carpet_category,
            quantity_lm=quantity_lm, quantity_m2=calc["quantity_m2"],
            discount_pct=discount_pct, unit_cost=calc["unit_cost"], unit_price=calc["unit_price"],
            line_total=calc["line_total"], margin_pct=calc["margin_pct"], total_job_cost=calc["total_job_cost"],
            gripper_perimeter_m=gripper_perimeter_m if apply_grippers else None, gripper_cost_total=calc["gripper_cost_total"],
            underfelt_area_m2=calc["underfelt_area_m2"] or None, underfelt_cost_total=calc["underfelt_cost_total"],
            cutting_fee_total=calc["cutting_fee_total"],
            glue_cost_total=calc["glue_cost_total"], glue_sell_total=calc["glue_sell_total"], glue_units_needed=calc["glue_units_needed"],
            labour_cost_total=calc["labour_cost_total"], labour_charged_total=calc["labour_charged_total"], own_staff=own_staff,
        )
        session.add(line)
        _log_quote_line_audit(session, quote, username, "added", f"Carpet ({carpet_category}) — {product.product_name}, {quantity_lm}LM")
        session.commit()
        session.refresh(line)

        result = strip_sensitive_fields(line.dict(), role, settings)
        if calc["warning"] and role == UserRole.owner:
            result["warning"] = calc["warning"]
        return result


@app.put("/quotes/{quote_id}/lines/{line_id}/carpet")
def edit_carpet_line(quote_id: int, line_id: int, product_id: int, quantity_lm: float, carpet_category: str,
                      discount_pct: float = 0.0, own_staff: bool = True, labour_rate_per_m2: float = None,
                      apply_cutting_fee: bool = False, apply_grippers: bool = False, gripper_perimeter_m: float = 0.0,
                      apply_underfelt: bool = False, apply_glue: bool = False,
                      role: str = Depends(get_current_role), tenant_id: str = Depends(get_current_tenant),
                      username: str = Depends(get_current_username)):
    """Edit-in-place (confirmed Aug 2026) — same PUT-to-the-same-line-id
    pattern every other category's edit endpoint already uses, not the
    old delete-then-re-add shape."""
    with Session(engine) as session:
        quote = get_or_404(session, Quote, quote_id, tenant_id, "Quote")
        line = get_or_404(session, QuoteLineItem, line_id, tenant_id, "Quote line")
        if line.category != "flooring" or not line.carpet_category:
            raise HTTPException(400, "This line isn't a carpet line.")
        settings = get_settings(session, tenant_id)
        r = _compute_carpet_calc(session, tenant_id, product_id, quantity_lm, discount_pct, own_staff, labour_rate_per_m2,
                                  apply_cutting_fee, apply_grippers, gripper_perimeter_m, apply_underfelt, apply_glue)
        product, calc = r["product"], r["calc"]

        line.product_id = product_id
        line.product_name = product.product_name
        line.colour = product.colour
        line.flooring_pricing_type = product.pricing_type
        line.carpet_category = carpet_category
        line.quantity_lm = quantity_lm
        line.quantity_m2 = calc["quantity_m2"]
        line.discount_pct = discount_pct
        line.unit_cost = calc["unit_cost"]
        line.unit_price = calc["unit_price"]
        line.line_total = calc["line_total"]
        line.margin_pct = calc["margin_pct"]
        # Low Margin Accountability (confirmed Aug 2026, Margin
        # Becomes Owner-Only; Price Gets a Colour Signal brief) — a
        # fresh recalculation is a fresh pricing decision, so any
        # reason recorded against the PREVIOUS number no longer
        # applies; cleared here so a line that lands below threshold
        # again (even back at the same number) asks for accountability
        # again rather than silently reusing a stale explanation.
        line.low_margin_reason = None
        line.low_margin_reason_by = None
        line.low_margin_reason_at = None
        line.total_job_cost = calc["total_job_cost"]
        line.gripper_perimeter_m = gripper_perimeter_m if apply_grippers else None
        line.gripper_cost_total = calc["gripper_cost_total"]
        line.underfelt_area_m2 = calc["underfelt_area_m2"] or None
        line.underfelt_cost_total = calc["underfelt_cost_total"]
        line.cutting_fee_total = calc["cutting_fee_total"]
        line.glue_cost_total = calc["glue_cost_total"]
        line.glue_sell_total = calc["glue_sell_total"]
        line.glue_units_needed = calc["glue_units_needed"]
        line.labour_cost_total = calc["labour_cost_total"]
        line.labour_charged_total = calc["labour_charged_total"]
        line.own_staff = own_staff
        session.add(line)
        _log_quote_line_audit(session, quote, username, "edited", f"Carpet ({carpet_category}) — {product.product_name}, {quantity_lm}LM")
        session.commit()
        session.refresh(line)

        result = strip_sensitive_fields(line.dict(), role, settings)
        if calc["warning"] and role == UserRole.owner:
            result["warning"] = calc["warning"]
        return result


@app.post("/quotes/{quote_id}/lines/stairwell")
def add_stairwell_line(quote_id: int, vinyl_product_id: int, nosing_product_id: int,
                        num_stairs: int, stairwell_type: StairwellType,
                        stair_area_m2: float = 0.45, own_staff: bool = True,
                        landing_area_m2: float = 0.0,
                        role: str = Depends(get_current_role), tenant_id: str = Depends(get_current_tenant),
                        username: str = Depends(get_current_username)):
    """See _compute_stairwell_calc() just above for the actual formula/
    product-lookup logic, shared with edit_stairwell_line() and the
    preview endpoint (preview_line() below) — this function's own job is
    now just turning that computation into a real, persisted line."""
    with Session(engine) as session:
        quote = get_or_404(session, Quote, quote_id, tenant_id, "Quote")
        settings = get_settings(session, tenant_id)
        r = _compute_stairwell_calc(session, tenant_id, vinyl_product_id, nosing_product_id, num_stairs, stairwell_type,
                                     stair_area_m2, own_staff, landing_area_m2)
        vinyl_product, nosing_product, calc, landing_calc = r["vinyl_product"], r["nosing_product"], r["calc"], r["landing_calc"]
        combined_line_total, combined_total_job_cost = r["combined_line_total"], r["combined_total_job_cost"]
        combined_margin_pct, combined_labour_charged = r["combined_margin_pct"], r["combined_labour_charged"]
        combined_labour_cost, combined_warning = r["combined_labour_cost"], r["combined_warning"]

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
        _log_quote_line_audit(session, quote, username, "added", f"Stairwell — {vinyl_product.product_name} + {nosing_product.product_name}, {num_stairs} stairs")
        session.commit()
        session.refresh(line)

        result = strip_sensitive_fields(line.dict(), role, settings)
        if combined_warning and role == UserRole.owner:
            result["warning"] = combined_warning
        return result


@app.post("/quotes/{quote_id}/lines/misc")
def add_misc_line(quote_id: int, description: str, amount_ex_vat: float, cost_ex_vat: float = 0.0,
                   source_feature: str = None,
                   role: str = Depends(get_current_role), tenant_id: str = Depends(get_current_tenant),
                   username: str = Depends(get_current_username)):
    """Confirmed Aug 2026 — freeform line for anything that doesn't fit
    an existing category: extra Saturday/Sunday labour, a one-off
    special request, anything not covered by a real product record.
    cost_ex_vat is optional (defaults to 0, i.e. pure margin) — useful
    for things like weekend labour where there's genuinely no
    additional cost beyond what's already being paid in salary.

    source_feature (confirmed Aug 2026, Extra Rooms / Floor Prep
    Collapsible brief): None for an ordinary misc line (unchanged
    default — every existing caller of this endpoint keeps working
    exactly as before); "floor_prep" when quote-builder.js's
    addFloorPrepLine() is the caller, so the frontend can pick those
    lines back out for their own collapsible-card rendering."""
    with Session(engine) as session:
        quote = get_or_404(session, Quote, quote_id, tenant_id, "Quote")
        settings = get_settings(session, tenant_id)
        margin_pct = (amount_ex_vat - cost_ex_vat) / amount_ex_vat if amount_ex_vat else 0.0
        line = QuoteLineItem(
            quote_id=quote_id, category="misc", product_id=0, tenant_id=tenant_id,
            product_name=description, source_feature=source_feature,
            unit_cost=cost_ex_vat, unit_price=amount_ex_vat,
            line_total=amount_ex_vat, margin_pct=margin_pct,
        )
        session.add(line)
        # "Additional: Extra screed — Lounge — R2,400" (confirmed Aug
        # 2026, brief's own example) — this is exactly the misc-line
        # path Extra Rooms/Floor Prep and any other freeform post-
        # acceptance addition already goes through.
        _log_quote_line_audit(session, quote, username, "added", f"{description} — R{amount_ex_vat:.2f}")
        session.commit()
        session.refresh(line)
        return strip_sensitive_fields(line.dict(), role, settings)


# ---------- Edit Quote Line In Place (confirmed Aug 2026, Edit Quote Line
# In Place brief) — the highest direct-value fix for the vinyl workflow per
# the brief's own words. Before this, changing a line meant delete + re-add
# (editQuoteLine()/deleteLineBeingEditedIfAny(), quote-builder.js) — the
# endpoints below UPDATE the existing QuoteLineItem row instead, so the
# line keeps its own id/identity throughout (display order was already
# driven by category via _quote_line_sort_key(), not insertion order, but
# an in-place update also means the row's own position in the table never
# moves either). One PUT endpoint per category, deliberately mirroring its
# POST add_*_line() sibling's exact params and calc call — the SAME trusted
# formula, never a second shadow calc, per the standing rule that already
# governed how quote-builder.js's pre-brief editQuoteLine() prefill worked.
# Stairwell is excluded — same as the pre-existing edit UI (no Edit button
# offered for it there either); Vaporite/Bondite/stairwell calculator are
# explicit non-goals per the brief.
def _reapply_line_calc_respecting_override(line: "QuoteLineItem", calc_line_total: float, product_changed: bool,
                                            session: Session, tenant_id: str, username: str) -> dict:
    """Manual Override survival rule (confirmed Aug 2026, Edit Quote Line In
    Place brief §2) — applied identically by every edit-in-place endpoint
    below and by change_line_colour():
      - No override on this line: nothing to do, the caller's fresh calc
        just applies normally.
      - Override present AND the underlying product/colour changed: the
        override no longer means anything against different pricing, so
        it's CLEARED outright — line_total becomes the fresh calculated
        value, override fields reset to None — and flagged back to the
        caller (override_cleared=True) so the frontend can show a plain
        heads-up that the Owner should reconfirm/re-apply if still wanted.
        This is the brief's own proposed safest default, not a guess.
      - Override present AND only quantity/dimensions changed (product/
        colour unchanged): confirmed with Burgert (Aug 2026) — the override
        amount STAYS FIXED, full stop. The caller is responsible for
        re-asserting line.line_total = the pre-edit override amount AFTER
        calling this (this function only updates pre_override_line_total,
        the recorded "true calculated value" baseline, so a future "Revert
        to calculated value" restores the CURRENT correct calculation
        rather than a stale pre-edit one — everything else about the line,
        e.g. margin/cost bookkeeping, is left to reflect the fresh calc).
    Requires the caller to have already set line.line_total = calc_line_total
    (or the equivalent) before calling this, when product_changed is True —
    this function does not itself write calc_line_total anywhere except
    into pre_override_line_total."""
    if line.pre_override_line_total is None:
        return {"override_cleared": False}

    if product_changed:
        old_total = line.line_total
        line.line_total = calc_line_total
        line.pre_override_line_total = None
        line.override_reason = None
        line.override_by = None
        line.override_at = None
        session.add(AuditLog(
            tenant_id=tenant_id, username=username, entity_type="QuoteLineItem", entity_id=line.id,
            field="manual_override_cleared_on_edit",
            old_value=f"R{old_total:.2f} (manual override)",
            new_value=f"R{calc_line_total:.2f} (recalculated — override cleared because the product/colour changed; reconfirm if an override is still needed)",
        ))
        return {"override_cleared": True}

    line.pre_override_line_total = calc_line_total
    return {"override_cleared": False}


def _log_quote_line_edit_audit(session: Session, quote: "Quote", username: str, old_label: str, new_label: str):
    """Same gating as _log_quote_line_audit() above (only logged once a
    quote is past Draft — see that function's own docstring for why).
    '__line_edited__' sits alongside the existing __line_added__/
    __line_removed__ pair rather than being shoehorned into either — an
    in-place edit is neither of those."""
    if quote.workflow_status not in ("accepted", "scheduled", "completed"):
        return
    session.add(AuditLog(
        tenant_id=quote.tenant_id, username=username, entity_type="Quote", entity_id=quote.id,
        field="__line_edited__", old_value=old_label, new_value=new_label,
    ))


@app.put("/quotes/{quote_id}/lines/{line_id}/flooring")
def edit_flooring_line(quote_id: int, line_id: int, product_id: int, quantity_m2: float,
                        job_type: JobType, discount_pct: float = 0.0,
                        glue_cost_per_unit: float = 0.0, glue_coverage_m2: float = 0.0,
                        labour_rate_per_m2: float = 45.0,
                        bag_cost: float = 235.0, bag_coverage_m2: float = None,
                        own_staff: bool = True, markup_override: float = None,
                        include_tile_removal_fee: bool = False,
                        apply_delivery_fee: bool = True,
                        role: str = Depends(get_current_role), tenant_id: str = Depends(get_current_tenant),
                        username: str = Depends(get_current_username)):
    """Covers both vinyl and screed (job_type/pricing_type distinguishes
    them, same as add_flooring_line() — see that endpoint's own docstring
    for the param meanings, unchanged here)."""
    with Session(engine) as session:
        quote = get_or_404(session, Quote, quote_id, tenant_id, "Quote")
        line = session.get(QuoteLineItem, line_id)
        if not line or line.quote_id != quote_id or line.tenant_id != tenant_id:
            raise HTTPException(404, "Quote line not found")
        if line.category != "flooring":
            raise HTTPException(400, "This line is not a flooring/screed line.")
        product = _require_active_flooring_product(get_or_404(session, FlooringProduct, product_id, tenant_id, "Flooring product"))
        settings = get_settings(session, tenant_id)

        product_changed = (line.product_id != product_id)
        existing_override_total = line.line_total if line.pre_override_line_total is not None else None
        old_desc = f"{line.product_name}{', ' + line.colour if line.colour else ''}, {line.quantity_m2}m²"

        calc = calculate_flooring_line(
            resolve_zone_price(session, tenant_id, product, settings), quantity_m2, job_type, discount_pct,
            glue_cost_per_unit, glue_coverage_m2, labour_rate_per_m2,
            bag_cost, bag_coverage_m2, own_staff, markup_override,
            include_tile_removal_fee,
            apply_delivery_fee=apply_delivery_fee,
            margin_warn_threshold=settings.flooring_margin_warn_threshold,
            tile_removal_fee_per_m2_incl_vat=settings.tile_removal_fee_per_m2_incl_vat,
            vat_pct=settings.vat_pct,
        )
        line.product_id = product_id
        line.product_name = product.product_name
        line.colour = product.colour   # original_colour is set once at creation, permanently — never touched by an edit, same rule change_line_colour() already follows
        line.job_type = job_type
        line.flooring_pricing_type = product.pricing_type
        line.carpet_category = product.flooring_category if product.flooring_category in CARPET_ONLY_CATEGORIES else None
        line.quantity_m2 = quantity_m2
        line.discount_pct = discount_pct
        line.unit_cost = calc["unit_cost"]
        line.unit_price = calc["unit_price"]
        line.line_total = calc["line_total"]
        line.margin_pct = calc["margin_pct"]
        # Low Margin Accountability (confirmed Aug 2026, Margin
        # Becomes Owner-Only; Price Gets a Colour Signal brief) — a
        # fresh recalculation is a fresh pricing decision, so any
        # reason recorded against the PREVIOUS number no longer
        # applies; cleared here so a line that lands below threshold
        # again (even back at the same number) asks for accountability
        # again rather than silently reusing a stale explanation.
        line.low_margin_reason = None
        line.low_margin_reason_by = None
        line.low_margin_reason_at = None
        line.glue_cost_total = calc["glue_cost_total"]
        line.glue_sell_total = calc["glue_sell_total"]
        line.glue_units_needed = calc["glue_units_needed"]
        line.labour_cost_total = calc["labour_cost_total"]
        line.labour_charged_total = calc["labour_charged_total"]
        line.own_staff = calc["own_staff"]
        line.bags_allowed = calc["bags_allowed"]
        line.boxes_needed = calc.get("packs_needed")
        line.compound_cost_total = calc["compound_cost_total"]
        line.tile_removal_fee_total = calc["tile_removal_fee_total"]
        line.delivery_fee_total = calc["delivery_fee_total"]
        line.total_job_cost = calc["total_job_cost"]

        override_result = _reapply_line_calc_respecting_override(line, calc["line_total"], product_changed, session, tenant_id, username)
        if not override_result["override_cleared"] and existing_override_total is not None:
            line.line_total = existing_override_total   # stays fixed, confirmed Aug 2026

        new_desc = f"{line.product_name}{', ' + line.colour if line.colour else ''}, {line.quantity_m2}m²"
        _log_quote_line_edit_audit(session, quote, username, old_desc, new_desc)
        session.add(line)
        session.commit()
        session.refresh(line)

        result = strip_sensitive_fields(line.dict(), role, settings)
        if calc["warning"] and role == UserRole.owner:
            result["warning"] = calc["warning"]
        if "packs_needed" in calc:
            result["packs_needed"] = calc["packs_needed"]
        if "glue_units_needed" in calc:
            result["glue_units_needed"] = calc["glue_units_needed"]
            result["glue_sell_total"] = calc["glue_sell_total"]
        result["override_cleared"] = override_result["override_cleared"]
        return result


@app.put("/quotes/{quote_id}/lines/{line_id}/blinds")
def edit_blinds_line(quote_id: int, line_id: int, product_id: int, width_mm: float, drop_mm: float,
                      discount_pct: float = 0.0, role: str = Depends(get_current_role),
                      tenant_id: str = Depends(get_current_tenant), username: str = Depends(get_current_username)):
    with Session(engine) as session:
        quote = get_or_404(session, Quote, quote_id, tenant_id, "Quote")
        line = session.get(QuoteLineItem, line_id)
        if not line or line.quote_id != quote_id or line.tenant_id != tenant_id:
            raise HTTPException(404, "Quote line not found")
        if line.category != "blinds":
            raise HTTPException(400, "This line is not a blinds line.")
        product = get_or_404(session, BlindsProduct, product_id, tenant_id, "Blinds product")
        settings = get_settings(session, tenant_id)

        product_changed = (line.product_id != product_id)
        existing_override_total = line.line_total if line.pre_override_line_total is not None else None
        old_desc = f"{line.product_name}, {line.width_mm}×{line.drop_mm}mm"

        calc = calculate_blinds_line(product, width_mm, drop_mm, discount_pct)
        line.product_id = product_id
        line.product_name = product.product_name
        line.width_mm = width_mm
        line.drop_mm = drop_mm
        line.discount_pct = discount_pct
        line.unit_cost = calc["unit_cost"]
        line.unit_price = calc["unit_price"]
        line.line_total = calc["line_total"]
        line.margin_pct = calc["margin_pct"]
        # Low Margin Accountability (confirmed Aug 2026, Margin
        # Becomes Owner-Only; Price Gets a Colour Signal brief) — a
        # fresh recalculation is a fresh pricing decision, so any
        # reason recorded against the PREVIOUS number no longer
        # applies; cleared here so a line that lands below threshold
        # again (even back at the same number) asks for accountability
        # again rather than silently reusing a stale explanation.
        line.low_margin_reason = None
        line.low_margin_reason_by = None
        line.low_margin_reason_at = None

        override_result = _reapply_line_calc_respecting_override(line, calc["line_total"], product_changed, session, tenant_id, username)
        if not override_result["override_cleared"] and existing_override_total is not None:
            line.line_total = existing_override_total

        new_desc = f"{line.product_name}, {line.width_mm}×{line.drop_mm}mm"
        _log_quote_line_edit_audit(session, quote, username, old_desc, new_desc)
        session.add(line)
        session.commit()
        session.refresh(line)
        result = strip_sensitive_fields(line.dict(), role, settings)
        result["override_cleared"] = override_result["override_cleared"]
        return result


@app.put("/quotes/{quote_id}/lines/{line_id}/trims")
def edit_trim_line(quote_id: int, line_id: int, product_id: int, length_m: float,
                    discount_pct: float = 0.0, colour: str = "", role: str = Depends(get_current_role),
                    tenant_id: str = Depends(get_current_tenant), username: str = Depends(get_current_username)):
    with Session(engine) as session:
        quote = get_or_404(session, Quote, quote_id, tenant_id, "Quote")
        line = session.get(QuoteLineItem, line_id)
        if not line or line.quote_id != quote_id or line.tenant_id != tenant_id:
            raise HTTPException(404, "Quote line not found")
        # Skirting/Trim category fix (confirmed Aug 2026, Vinyl Redesign:
        # Real Usage Findings brief §1) — a real, existing skirting line
        # is now genuinely stored as category "skirting", not "trim"
        # (see _trim_line_category(), add_trim_line() above) — this
        # check has to accept both, same PUT endpoint genuinely serves
        # either.
        if line.category not in ("trim", "skirting"):
            raise HTTPException(400, "This line is not a trim/skirting line.")
        product = get_or_404(session, TrimProduct, product_id, tenant_id, "Trim product")
        settings = get_settings(session, tenant_id)

        product_changed = (line.product_id != product_id)
        existing_override_total = line.line_total if line.pre_override_line_total is not None else None
        old_desc = f"{line.product_name}, {line.length_m}lm"

        calc = calculate_trim_line(product, length_m, discount_pct, margin_warn_threshold=settings.flooring_margin_warn_threshold)
        line.product_id = product_id
        line.product_name = product.product_name
        line.length_m = length_m
        line.category = _trim_line_category(product)   # a skirting line edited into a genuine trim product (or vice versa) must be able to move between the two, same as any other product-change edit
        line.trim_sub_category = product.category
        line.colour = colour   # Trim Colours brief (confirmed Aug 2026) — blank for skirting, same as add_trim_line() above
        line.discount_pct = discount_pct
        line.unit_cost = calc["unit_cost"]
        line.unit_price = calc["unit_price"]
        line.line_total = calc["line_total"]
        line.margin_pct = calc["margin_pct"]
        # Low Margin Accountability (confirmed Aug 2026, Margin
        # Becomes Owner-Only; Price Gets a Colour Signal brief) — a
        # fresh recalculation is a fresh pricing decision, so any
        # reason recorded against the PREVIOUS number no longer
        # applies; cleared here so a line that lands below threshold
        # again (even back at the same number) asks for accountability
        # again rather than silently reusing a stale explanation.
        line.low_margin_reason = None
        line.low_margin_reason_by = None
        line.low_margin_reason_at = None

        override_result = _reapply_line_calc_respecting_override(line, calc["line_total"], product_changed, session, tenant_id, username)
        if not override_result["override_cleared"] and existing_override_total is not None:
            line.line_total = existing_override_total

        new_desc = f"{line.product_name}, {line.length_m}lm"
        _log_quote_line_edit_audit(session, quote, username, old_desc, new_desc)
        session.add(line)
        session.commit()
        session.refresh(line)
        result = strip_sensitive_fields(line.dict(), role, settings)
        if calc["warning"] and role == UserRole.owner:
            result["warning"] = calc["warning"]
        result["override_cleared"] = override_result["override_cleared"]
        return result


@app.put("/quotes/{quote_id}/lines/{line_id}/stairwell")
def edit_stairwell_line(quote_id: int, line_id: int, vinyl_product_id: int, nosing_product_id: int,
                         num_stairs: int, stairwell_type: StairwellType,
                         stair_area_m2: float = 0.45, own_staff: bool = True,
                         landing_area_m2: float = 0.0,
                         role: str = Depends(get_current_role), tenant_id: str = Depends(get_current_tenant),
                         username: str = Depends(get_current_username)):
    """Editability — every category, in place (confirmed Aug 2026,
    Vinyl Quoting UX Redesign proposal §09, approved) — REAL GAP CLOSED.
    Stairwell was the one category that couldn't be edited without
    delete-then-re-add: no PUT endpoint existed at all, and the
    frontend's own Quote Lines row template explicitly hid the Edit
    button for stairwell rows. Same shape as every other edit_*_line()
    above (params identical to add_stairwell_line(), which this mirrors
    exactly) — calls the SAME calculate_stairwell_line()/
    calculate_flooring_line() (landing) that add_stairwell_line() calls,
    zero new calculation logic, per the brief's own explicit non-goal.
    product_changed tracks the vinyl product only, same signal
    edit_flooring_line() uses — the nosing product isn't stored as its
    own id on QuoteLineItem (only baked into product_name/cost totals),
    so there's nothing to compare it against on an edit."""
    with Session(engine) as session:
        quote = get_or_404(session, Quote, quote_id, tenant_id, "Quote")
        line = session.get(QuoteLineItem, line_id)
        if not line or line.quote_id != quote_id or line.tenant_id != tenant_id:
            raise HTTPException(404, "Quote line not found")
        if line.category != "stairwell":
            raise HTTPException(400, "This line is not a stairwell line.")
        settings = get_settings(session, tenant_id)

        product_changed = (line.product_id != vinyl_product_id)
        existing_override_total = line.line_total if line.pre_override_line_total is not None else None
        old_desc = f"Stairwell — {line.product_name}, {line.num_stairs} stairs"

        # Shared with add_stairwell_line() and the preview endpoint
        # (confirmed Aug 2026, Vinyl Quoting UX Redesign proposal §10,
        # Phase 4, approved) — see _compute_stairwell_calc()'s own
        # docstring; this used to duplicate that whole computation here.
        r = _compute_stairwell_calc(session, tenant_id, vinyl_product_id, nosing_product_id, num_stairs, stairwell_type,
                                     stair_area_m2, own_staff, landing_area_m2)
        vinyl_product, nosing_product, calc, landing_calc = r["vinyl_product"], r["nosing_product"], r["calc"], r["landing_calc"]
        combined_line_total, combined_total_job_cost = r["combined_line_total"], r["combined_total_job_cost"]
        combined_margin_pct, combined_labour_charged = r["combined_margin_pct"], r["combined_labour_charged"]
        combined_labour_cost, combined_warning = r["combined_labour_cost"], r["combined_warning"]

        line.product_id = vinyl_product_id
        line.product_name = f"{vinyl_product.product_name} + {nosing_product.product_name} (stairwell)"
        line.num_stairs = num_stairs
        line.stairwell_type = stairwell_type
        line.nosing_length_m = calc["nosing_length_m"]
        line.boxes_needed = calc["boxes_needed"]
        line.billed_vinyl_area_m2 = calc["billed_vinyl_area_m2"]
        line.glue_area_m2 = calc["glue_area_m2"]
        line.vinyl_sell_total = calc["vinyl_sell_total"]
        line.vinyl_cost_total = calc["vinyl_cost_total"]
        line.nosing_cost_total = calc["nosing_cost_total"]
        line.nosing_sell_total = calc["nosing_sell_total"]
        line.glue_cost_total = calc["glue_cost_total"]
        line.glue_sell_total = calc["glue_sell_total"]
        line.glue_units_needed = calc["glue_units_needed"]
        line.labour_cost_total = combined_labour_cost
        line.labour_charged_total = combined_labour_charged
        line.own_staff = calc["own_staff"]
        line.landing_area_m2 = landing_area_m2 if landing_calc else None
        line.landing_sell_total = landing_calc["line_total"] if landing_calc else None
        line.margin_pct = combined_margin_pct
        # Low Margin Accountability (confirmed Aug 2026, Margin
        # Becomes Owner-Only; Price Gets a Colour Signal brief) — a
        # fresh recalculation is a fresh pricing decision, so any
        # reason recorded against the PREVIOUS number no longer
        # applies; cleared here so a line that lands below threshold
        # again (even back at the same number) asks for accountability
        # again rather than silently reusing a stale explanation.
        line.low_margin_reason = None
        line.low_margin_reason_by = None
        line.low_margin_reason_at = None
        line.total_job_cost = combined_total_job_cost
        line.line_total = combined_line_total

        override_result = _reapply_line_calc_respecting_override(line, combined_line_total, product_changed, session, tenant_id, username)
        if not override_result["override_cleared"] and existing_override_total is not None:
            line.line_total = existing_override_total   # stays fixed, same rule as every other edit_*_line() above

        new_desc = f"Stairwell — {line.product_name}, {line.num_stairs} stairs"
        _log_quote_line_edit_audit(session, quote, username, old_desc, new_desc)
        session.add(line)
        session.commit()
        session.refresh(line)

        result = strip_sensitive_fields(line.dict(), role, settings)
        if combined_warning and role == UserRole.owner:
            result["warning"] = combined_warning
        result["override_cleared"] = override_result["override_cleared"]
        return result


@app.put("/quotes/{quote_id}/lines/{line_id}/misc")
def edit_misc_line(quote_id: int, line_id: int, description: str, amount_ex_vat: float, cost_ex_vat: float = 0.0,
                    role: str = Depends(get_current_role), tenant_id: str = Depends(get_current_tenant),
                    username: str = Depends(get_current_username)):
    """Misc lines have no product record — 'product changed' has no direct
    equivalent, so per the brief's own 'propose the safest default'
    guidance (§2), ANY edit to an already-overridden misc line clears the
    override (same conservative treatment as a product/colour change on a
    real product line), rather than guessing whether a freeform
    description/amount edit counts as 'quantity-only'."""
    with Session(engine) as session:
        quote = get_or_404(session, Quote, quote_id, tenant_id, "Quote")
        line = session.get(QuoteLineItem, line_id)
        if not line or line.quote_id != quote_id or line.tenant_id != tenant_id:
            raise HTTPException(404, "Quote line not found")
        if line.category != "misc":
            raise HTTPException(400, "This line is not a freeform/extra line.")
        settings = get_settings(session, tenant_id)

        old_desc = f"{line.product_name} — R{line.unit_price:.2f}"
        margin_pct = (amount_ex_vat - cost_ex_vat) / amount_ex_vat if amount_ex_vat else 0.0

        line.product_name = description
        line.unit_cost = cost_ex_vat
        line.unit_price = amount_ex_vat
        line.line_total = amount_ex_vat
        line.margin_pct = margin_pct
        # Low Margin Accountability (confirmed Aug 2026, Margin
        # Becomes Owner-Only; Price Gets a Colour Signal brief) — a
        # fresh recalculation is a fresh pricing decision, so any
        # reason recorded against the PREVIOUS number no longer
        # applies; cleared here so a line that lands below threshold
        # again (even back at the same number) asks for accountability
        # again rather than silently reusing a stale explanation.
        line.low_margin_reason = None
        line.low_margin_reason_by = None
        line.low_margin_reason_at = None

        override_result = _reapply_line_calc_respecting_override(line, amount_ex_vat, True, session, tenant_id, username)

        new_desc = f"{line.product_name} — R{line.unit_price:.2f}"
        _log_quote_line_edit_audit(session, quote, username, old_desc, new_desc)
        session.add(line)
        session.commit()
        session.refresh(line)
        result = strip_sensitive_fields(line.dict(), role, settings)
        result["override_cleared"] = override_result["override_cleared"]
        return result


@app.put("/quotes/{quote_id}/lines/{line_id}/low-margin-reason")
def set_line_low_margin_reason(quote_id: int, line_id: int, reason: str,
                                tenant_id: str = Depends(get_current_tenant),
                                username: str = Depends(get_current_username)):
    """Low Margin Accountability, per-line (confirmed Aug 2026, Margin
    Becomes Owner-Only; Price Gets a Colour Signal brief §3, Burgert's
    own clarification) — "the system won't allow going under [30%]
    anyway except with a discount, but there should be accountability
    for those types of decisions." A soft prompt, not a hard block: the
    line already saved (add/edit endpoints above never wait on this) —
    this is a separate, generic, always-available endpoint the frontend
    calls right after, once it sees needs_low_margin_reason: true on a
    line. One endpoint for every category rather than threading a
    reason param through 10 different add/edit signatures, since
    QuoteLineItem is the one shared model underneath all of them
    regardless of category. Always logged to AuditLog, unconditionally
    (unlike _log_quote_line_edit_audit's post-accept-only gating above)
    — an accountability record is exactly the kind of thing worth
    keeping even on a quote that's still in Draft."""
    with Session(engine) as session:
        quote = get_or_404(session, Quote, quote_id, tenant_id, "Quote")
        line = session.get(QuoteLineItem, line_id)
        if not line or line.quote_id != quote_id or line.tenant_id != tenant_id:
            raise HTTPException(404, "Quote line not found")
        reason = reason.strip()
        if not reason:
            raise HTTPException(400, "A reason is required.")
        line.low_margin_reason = reason
        line.low_margin_reason_by = username
        line.low_margin_reason_at = datetime.utcnow()
        session.add(line)
        session.add(AuditLog(
            tenant_id=tenant_id, username=username, entity_type="QuoteLineItem", entity_id=line.id,
            field="low_margin_reason", old_value="(none)", new_value=reason,
        ))
        session.commit()
        return {"low_margin_reason": reason, "low_margin_reason_by": username}


@app.put("/quotes/{quote_id}/low-margin-reason")
def set_quote_low_margin_reason(quote_id: int, reason: str,
                                 tenant_id: str = Depends(get_current_tenant),
                                 username: str = Depends(get_current_username)):
    """Low Margin Accountability, whole-quote (confirmed Aug 2026, same
    brief) — same soft-prompt shape as set_line_low_margin_reason()
    above, checked and recorded separately since a real quote can have
    every individual line clear the floor while the combined total
    (transport levy, quote-level discount, etc. all folded in) still
    doesn't."""
    with Session(engine) as session:
        quote = get_or_404(session, Quote, quote_id, tenant_id, "Quote")
        reason = reason.strip()
        if not reason:
            raise HTTPException(400, "A reason is required.")
        quote.low_margin_reason = reason
        quote.low_margin_reason_by = username
        quote.low_margin_reason_at = datetime.utcnow()
        session.add(quote)
        session.add(AuditLog(
            tenant_id=tenant_id, username=username, entity_type="Quote", entity_id=quote.id,
            field="low_margin_reason", old_value="(none)", new_value=reason,
        ))
        session.commit()
        return {"low_margin_reason": reason, "low_margin_reason_by": username}


@app.get("/quotes/lines/preview")
def preview_line(category: str,
                  product_id: int = None, width_mm: float = None, drop_mm: float = None,
                  length_m: float = None, discount_pct: float = 0.0,
                  vinyl_product_id: int = None, nosing_product_id: int = None,
                  num_stairs: int = None, stairwell_type: StairwellType = None,
                  stair_area_m2: float = 0.45, own_staff: bool = True, landing_area_m2: float = 0.0,
                  quantity_lm: float = None, labour_rate_per_m2: float = None,
                  apply_cutting_fee: bool = False, apply_grippers: bool = False, gripper_perimeter_m: float = 0.0,
                  apply_underfelt: bool = False, apply_glue: bool = False,
                  role: str = Depends(get_current_role), tenant_id: str = Depends(get_current_tenant)):
    """Live price preview (confirmed Aug 2026, Vinyl Quoting UX Redesign
    proposal §01/§10, Phase 4, approved) — "the core of what immediate
    confirmation actually means" (Burgert's own words), extended to
    Blinds/Skirting/Trim/Stairwell, the categories that had none before
    this (Misc has no real formula to preview — amount minus cost is
    plain arithmetic the frontend already does directly, no backend
    round-trip needed for it).

    Deliberately NOT the same approach Flooring's own live preview
    (fjCalc(), quote-builder.js) uses — that one is a genuine client-
    side JS reimplementation of the pricing formula, an accepted-but-
    real "shadow calculation" for that one category, already bitten
    once (a hardcoded VAT rate that silently drifted from Business
    Settings, fixed separately). Rather than repeat that pattern three
    more times, this calls the SAME calculate_blinds_line()/
    calculate_trim_line()/_compute_stairwell_calc() the real add/edit
    endpoints already use — there is exactly one formula per category
    in this whole codebase, whether you're previewing or actually
    saving. No quote_id in this URL on purpose: a preview doesn't touch
    or need any specific quote, only the price book and Business
    Settings, both tenant-scoped.

    Never writes anything — no QuoteLineItem, no AuditLog, no
    session.commit() at all. Same strip_sensitive_fields()/warning-
    gating discipline as every real add/edit endpoint, so Sales sees
    exactly as much (and as little) in the live preview as they would
    after actually saving the line — never a preview that leaks more
    than the real save would."""
    with Session(engine) as session:
        settings = get_settings(session, tenant_id)
        if category == "blinds":
            if product_id is None or width_mm is None or drop_mm is None:
                raise HTTPException(400, "product_id, width_mm, and drop_mm are required to preview a blinds line.")
            product = get_or_404(session, BlindsProduct, product_id, tenant_id, "Blinds product")
            calc = calculate_blinds_line(product, width_mm, drop_mm, discount_pct)
        elif category in ("trim", "skirting"):
            if product_id is None or length_m is None:
                raise HTTPException(400, "product_id and length_m are required to preview a trim/skirting line.")
            product = get_or_404(session, TrimProduct, product_id, tenant_id, "Trim product")
            calc = calculate_trim_line(product, length_m, discount_pct, margin_warn_threshold=settings.flooring_margin_warn_threshold)
        elif category == "stairwell":
            if vinyl_product_id is None or nosing_product_id is None or num_stairs is None or stairwell_type is None:
                raise HTTPException(400, "vinyl_product_id, nosing_product_id, num_stairs, and stairwell_type are required to preview a stairwell line.")
            r = _compute_stairwell_calc(session, tenant_id, vinyl_product_id, nosing_product_id, num_stairs, stairwell_type,
                                         stair_area_m2, own_staff, landing_area_m2)
            # Reshaped into the same flat {unit_cost, unit_price, line_total,
            # margin_pct, warning} shape calculate_blinds_line()/
            # calculate_trim_line() already return, so the frontend has
            # exactly one response shape to read regardless of category —
            # never a category-specific parsing branch on the client side.
            calc = {
                "unit_cost": 0, "unit_price": 0,   # matches add_stairwell_line()'s own QuoteLineItem() call exactly — a stairwell line has no single meaningful per-unit figure, real saves store 0 too, not omitted
                "line_total": round(r["combined_line_total"], 2),
                "margin_pct": round(r["combined_margin_pct"], 4),
                "warning": r["combined_warning"],
            }
        elif category == "carpet":
            if product_id is None or quantity_lm is None:
                raise HTTPException(400, "product_id and quantity_lm are required to preview a carpet line.")
            r = _compute_carpet_calc(session, tenant_id, product_id, quantity_lm, discount_pct, own_staff, labour_rate_per_m2,
                                      apply_cutting_fee, apply_grippers, gripper_perimeter_m, apply_underfelt, apply_glue)
            calc = r["calc"]
        else:
            raise HTTPException(400, f"No live preview available for category '{category}'.")

        result = strip_sensitive_fields(calc, role, settings)
        # Margin Becomes Owner-Only; Price Gets a Colour Signal (confirmed
        # Aug 2026, revises the comment this replaces) — margin_pct is
        # Owner-only again, so the warning text (which embeds the real
        # percentage in prose, e.g. "...is 32.1%, below...") is the exact
        # same leak class strip_sensitive_fields() guards every other
        # field against. Non-owners still get the colour signal via
        # result["margin_band"] (set above); the prose warning itself is
        # Owner-only.
        if role != UserRole.owner:
            result.pop("warning", None)
        return result


@app.get("/quotes/{quote_id}")
def get_quote(quote_id: int, role: str = Depends(get_current_role), tenant_id: str = Depends(get_current_tenant)):
    with Session(engine) as session:
        quote = get_or_404(session, Quote, quote_id, tenant_id, "Quote")
        settings = get_settings(session, tenant_id)
        lines = session.exec(
            select(QuoteLineItem).where(QuoteLineItem.quote_id == quote_id, QuoteLineItem.tenant_id == tenant_id)
        ).all()
        lines_out = [strip_sensitive_fields(l.dict(), role, settings) for l in lines]
        # Fixed Display Order (confirmed Aug 2026, Add-Line Data-Loss
        # brief §4) — sorted here, once, so every consumer of this
        # response (Quote Builder's table, the printed/PDF document, the
        # client document preview) shows the same fixed hierarchy with
        # zero risk of drifting apart. list.sort() is stable — original
        # relative order is preserved within each bucket.
        lines_out.sort(key=_quote_line_sort_key)
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
        # disagree on totals. Both now read from the same settings row,
        # and now share the actual discount/VAT/deposit math too, via
        # _quote_totals() (confirmed Aug 2026, Client Order History
        # Columns brief — see that function's own comment).
        VAT_PCT = settings.vat_pct
        totals = _quote_totals(subtotal_ex_vat, quote, VAT_PCT)
        # Fetched once here (Phase 2, confirmed Aug 2026) and reused for
        # both materials_ordered and job_steps below, rather than
        # querying OrderSheet twice in the same request.
        order_sheets_for_workflow = session.exec(select(OrderSheet).where(OrderSheet.quote_id == quote_id, OrderSheet.tenant_id == tenant_id)).all()
        materials_ordered_flag = _all_sheets_placed(order_sheets_for_workflow)

        response = {
            "quote": quote.dict(),
            "lines": lines_out,
            "subtotal_ex_vat": round(subtotal_ex_vat, 2),
            **totals,
            # Next Action / Needs Attention (confirmed Aug 2026, Order
            # Index / Job Workflow Redesign brief + Next Action
            # Addendum) — same engine list_quotes() uses, so the Job
            # Detail screen's own action button always agrees with
            # whatever the Order Index row showed to get here.
            "workflow": _job_workflow_info(quote, date.today(), materials_ordered_flag),
            # Job Workflow Design Proposal Phase 1 (confirmed Aug 2026) —
            # exposed directly, not just folded into next_action's prose,
            # so the frontend can show a clean derived status line
            # instead of parsing a sentence.
            "materials_ordered": materials_ordered_flag,
            # Job Workflow Design Proposal Phase 2 (confirmed Aug 2026) —
            # the adaptive step list, computed fresh, never stored. Empty
            # list for a quote that isn't a job yet (or was declined) —
            # the frontend renders nothing extra in that case, same as
            # today.
            "job_steps": _job_steps(session, quote, tenant_id, materials_ordered_flag, order_sheets_for_workflow),
        }

        # Decline Quote reason (confirmed Aug 2026, Master Workflow
        # proposal §05) — read back from AuditLog, never a field on
        # Quote itself (decline_quote() above). None for a quote that
        # was never declined, or one declined before this fix shipped
        # (no matching row exists for those, and that's correct — there
        # genuinely is no reason on record for them).
        if quote.declined_at:
            decline_entry = session.exec(
                select(AuditLog).where(
                    AuditLog.tenant_id == tenant_id, AuditLog.entity_type == "Quote",
                    AuditLog.entity_id == quote.id, AuditLog.field == "declined",
                ).order_by(AuditLog.timestamp.desc())
            ).first()
            response["decline_reason"] = decline_entry.new_value if decline_entry else None

        # "At a glance" job margin check — total sell vs. total real cost
        # (material + glue + labour for flooring, cost for blinds) across
        # the whole quote, so a mistake anywhere in the job shows up
        # immediately, not line by line. Margin uses the POST-discount
        # total, since that's the real revenue.
        # Margin Becomes Owner-Only; Price Gets a Colour Signal (confirmed
        # Aug 2026) — REVERSES the Owner-Only Calculation Breakdown
        # Toggle brief's earlier call to send overall_margin_pct to every
        # role. Ryno/Madri never receive the real percentage anywhere,
        # full stop — that now includes this whole-quote figure, not just
        # per-line margin_pct. overall_margin_band (same red/green/
        # green_bright bands as margin_band_for(), computed from the SAME
        # real number before it's withheld) is the replacement every role
        # gets instead, so the Job Detail screen's "overall margin"
        # indicator keeps working as a colour signal without the number
        # ever leaving the server for a non-Owner account.
        total_cost = sum(line_real_cost(l) for l in lines)
        total_ex_vat = totals["total_ex_vat"]
        overall_margin = (total_ex_vat - total_cost) / total_ex_vat if total_ex_vat else 0.0
        overall_margin_band = margin_band_for(overall_margin, settings)
        response["overall_margin_band"] = overall_margin_band
        # Low Margin Accountability, whole-quote (confirmed Aug 2026,
        # same brief) — separate flag/reason from any one line's own
        # low_margin_reason (see QuoteLineItem's own trio) since a real
        # quote can have every individual line clear the floor while the
        # combined total (transport levy, quote-level discount, etc. all
        # folded in) still doesn't.
        response["quote_needs_low_margin_reason"] = bool(overall_margin_band == "red" and not quote.low_margin_reason)
        if role == UserRole.owner:
            response["overall_margin_pct"] = round(overall_margin, 4)
            response["overall_cost_ex_vat"] = round(total_cost, 2)

        return response


@app.get("/quotes/{quote_id}/job-card")
def get_job_card(quote_id: int, tenant_id: str = Depends(get_current_tenant)):
    """Job Card Content Spec (confirmed Aug 2026) — a printable,
    installer-facing document: what a team needs on-site to actually do
    the job, without navigating the quoting system and without seeing
    any pricing. Per the original Master Workflow proposal, this pulls
    from records that already exist — no new tables, and (per this
    spec) exactly one small new field (Quote.installation_notes).

    "What's being installed" + "quantities to load" (spec items 2+3)
    are deliberately sourced ENTIRELY from this job's real Order
    Sheet(s) — never re-derived from QuoteLineItem separately — per the
    spec's own explicit "reuse the exact same data Order Sheets already
    use, so the two can never disagree." unit_cost is never selected
    onto the returned line dicts at all (not stripped after the fact —
    never fetched in the first place), the same hard "no pricing
    anywhere on this document" constraint the spec requires, matching
    the same principle already applied to the Builder Portal's public
    responses (hand-picked dicts, cost/margin fields structurally
    unable to leak even if the model grows more of them later).

    Known, deliberate scope limit: Blinds lines never produce an Order
    Sheet at all (Job Workflow Design Proposal, confirmed Aug 2026 —
    Blinds explicitly excluded from that whole redesign), so a job with
    only Blinds lines gets an empty order_sheets list here today. Not a
    bug — the same scope boundary already applied everywhere else in
    this codebase's current Job Workflow build; Blinds Job Cards are a
    future phase's problem, same as Blinds procurement tracking itself.

    Substrate (spec item 4) reuses QuoteLineItem.job_type directly (the
    same field screed pricing already uses) — the first screed line
    found, since a job has at most one substrate condition in practice.

    Notes pulled in from elsewhere on the system, read-only reference
    material (per direct instruction — "notes may be pulled in from
    notes anywhere on the system"): the client's own on-file notes
    (Client.notes — exactly where a gate code or access quirk already
    likely lives) and, if this job originated from a Lead, that Lead's
    own notes. PaymentFollowUp entries deliberately NOT pulled in here
    despite also being real per-job notes — they're payment-chasing
    records ("Called about outstanding balance"), not installation
    information, and surfacing them on an installer-facing document
    would work against the same "operational, not commercial" principle
    the no-pricing constraint exists for."""
    with Session(engine) as session:
        quote = get_or_404(session, Quote, quote_id, tenant_id, "Quote")
        sheets = session.exec(select(OrderSheet).where(OrderSheet.quote_id == quote_id, OrderSheet.tenant_id == tenant_id)).all()
        order_sheets_out = []
        for s in sheets:
            lines = session.exec(select(OrderSheetLine).where(OrderSheetLine.order_sheet_id == s.id, OrderSheetLine.tenant_id == tenant_id)).all()
            order_sheets_out.append({
                "supplier": s.supplier, "sheet_type": s.sheet_type,
                "lines": [{"product_name": l.product_name, "colour": l.colour, "quantity": l.quantity, "unit": l.unit} for l in lines],
            })

        substrate_line = session.exec(
            select(QuoteLineItem).where(
                QuoteLineItem.quote_id == quote_id, QuoteLineItem.tenant_id == tenant_id, QuoteLineItem.job_type.is_not(None),
            )
        ).first()

        client_notes = None
        if quote.client_id:
            client = session.get(Client, quote.client_id)
            if client and client.notes:
                client_notes = client.notes

        lead = session.exec(select(Lead).where(Lead.converted_quote_id == quote_id, Lead.tenant_id == tenant_id)).first()
        lead_notes = lead.notes if lead and lead.notes else None

        return {
            "job_number": quote.job_number, "client_name": quote.client_name, "site_address": quote.site_address,
            "installation_date": quote.installation_date, "installer_team": quote.installer_team,
            "substrate": substrate_line.job_type if substrate_line else None,
            "order_sheets": order_sheets_out,
            "installation_notes": quote.installation_notes,
            "client_notes": client_notes, "lead_notes": lead_notes,
        }


@app.delete("/quotes/{quote_id}/lines/{line_id}")
def delete_quote_line(quote_id: int, line_id: int, tenant_id: str = Depends(get_current_tenant),
                       username: str = Depends(get_current_username)):
    with Session(engine) as session:
        line = session.get(QuoteLineItem, line_id)
        if not line or line.quote_id != quote_id or line.tenant_id != tenant_id:
            raise HTTPException(404, "Quote line not found")
        quote = get_or_404(session, Quote, quote_id, tenant_id, "Quote")
        detail = f"{line.quantity_m2}m²" if line.quantity_m2 else f"{line.length_m}lm" if line.length_m else f"{line.width_mm}×{line.drop_mm}mm" if line.width_mm else f"{line.num_stairs} stairs" if line.num_stairs else ""
        _log_quote_line_audit(session, quote, username, "removed", f"{line.category.capitalize()} — {line.product_name}{', ' + detail if detail else ''}")
        session.delete(line)
        session.commit()
        return {"deleted": line_id}


# ---------- Manual Override, Owner-only (confirmed Aug 2026, Manual
# Override brief — urgent real use case: a job already quoted/accepted/
# deposit-paid in Burgert's OLD pre-Bolton system needs to be entered
# here matching those already-agreed figures exactly, not recalculated
# by Bolton's formula engine). require_owner on all four endpoints —
# this deliberately correctly respects Owner Preview Mode too (same
# get_current_role() chain everything else uses): previewing as Sales/
# Admin genuinely loses override access for that request, matching what
# the brief asks to be verified ("confirm Sales/Admin logins cannot").
# A reason is mandatory on every apply (never a silent override) and
# every action — apply AND revert — writes a permanent AuditLog entry.
class LineOverrideRequest(BaseModel):
    new_value: float
    reason: str


class TotalOverrideRequest(BaseModel):
    new_value: float
    reason: str


@app.put("/quotes/{quote_id}/lines/{line_id}/override")
def override_quote_line(quote_id: int, line_id: int, body: LineOverrideRequest,
                         role: str = Depends(require_owner), tenant_id: str = Depends(get_current_tenant),
                         username: str = Depends(get_current_username)):
    if not body.reason or not body.reason.strip():
        raise HTTPException(400, "A reason is required to manually override a value.")
    with Session(engine) as session:
        line = session.get(QuoteLineItem, line_id)
        if not line or line.quote_id != quote_id or line.tenant_id != tenant_id:
            raise HTTPException(404, "Quote line not found")
        quote = get_or_404(session, Quote, quote_id, tenant_id, "Quote")
        old_value = line.line_total
        # Only ever captured on the FIRST override — a second override
        # applied later must not overwrite the true original with what
        # was just the previous override's value, or "Revert to
        # calculated value" would stop being true to its name.
        if line.pre_override_line_total is None:
            line.pre_override_line_total = line.line_total
        line.line_total = body.new_value
        line.override_reason = body.reason.strip()
        line.override_by = username
        line.override_at = datetime.utcnow()
        session.add(AuditLog(
            tenant_id=tenant_id, username=username, entity_type="QuoteLineItem", entity_id=line_id,
            field="manual_override", old_value=f"R{old_value:.2f}",
            new_value=f"R{body.new_value:.2f} — {body.reason.strip()}",
        ))
        session.add(line)
        session.commit()
        session.refresh(line)
        return line


@app.post("/quotes/{quote_id}/lines/{line_id}/revert-override")
def revert_quote_line_override(quote_id: int, line_id: int,
                                role: str = Depends(require_owner), tenant_id: str = Depends(get_current_tenant),
                                username: str = Depends(get_current_username)):
    with Session(engine) as session:
        line = session.get(QuoteLineItem, line_id)
        if not line or line.quote_id != quote_id or line.tenant_id != tenant_id:
            raise HTTPException(404, "Quote line not found")
        if line.pre_override_line_total is None:
            raise HTTPException(400, "This line has no override to revert.")
        old_value = line.line_total
        restored = line.pre_override_line_total
        line.line_total = restored
        line.pre_override_line_total = None
        line.override_reason = None
        line.override_by = None
        line.override_at = None
        session.add(AuditLog(
            tenant_id=tenant_id, username=username, entity_type="QuoteLineItem", entity_id=line_id,
            field="manual_override_reverted", old_value=f"R{old_value:.2f}",
            new_value=f"R{restored:.2f} (reverted to calculated value)",
        ))
        session.add(line)
        session.commit()
        session.refresh(line)
        return line


@app.put("/quotes/{quote_id}/override-total")
def override_quote_total(quote_id: int, body: TotalOverrideRequest,
                          role: str = Depends(require_owner), tenant_id: str = Depends(get_current_tenant),
                          username: str = Depends(get_current_username)):
    if not body.reason or not body.reason.strip():
        raise HTTPException(400, "A reason is required to manually override the total.")
    with Session(engine) as session:
        quote = get_or_404(session, Quote, quote_id, tenant_id, "Quote")
        old_value = quote.manual_override_total_incl_vat
        quote.manual_override_total_incl_vat = body.new_value
        quote.override_total_reason = body.reason.strip()
        quote.override_total_by = username
        quote.override_total_at = datetime.utcnow()
        session.add(AuditLog(
            tenant_id=tenant_id, username=username, entity_type="Quote", entity_id=quote_id,
            field="manual_override_total",
            old_value=(f"R{old_value:.2f}" if old_value is not None else "(calculated)"),
            new_value=f"R{body.new_value:.2f} — {body.reason.strip()}",
        ))
        session.add(quote)
        session.commit()
        session.refresh(quote)
        return quote


@app.post("/quotes/{quote_id}/revert-total-override")
def revert_quote_total_override(quote_id: int, role: str = Depends(require_owner),
                                 tenant_id: str = Depends(get_current_tenant),
                                 username: str = Depends(get_current_username)):
    with Session(engine) as session:
        quote = get_or_404(session, Quote, quote_id, tenant_id, "Quote")
        if quote.manual_override_total_incl_vat is None:
            raise HTTPException(400, "This quote's total has no override to revert.")
        old_value = quote.manual_override_total_incl_vat
        quote.manual_override_total_incl_vat = None
        quote.override_total_reason = None
        quote.override_total_by = None
        quote.override_total_at = None
        session.add(AuditLog(
            tenant_id=tenant_id, username=username, entity_type="Quote", entity_id=quote_id,
            field="manual_override_total_reverted", old_value=f"R{old_value:.2f}",
            new_value="(reverted to calculated total)",
        ))
        session.add(quote)
        session.commit()
        session.refresh(quote)
        return quote


# ---------- Revert to Original (confirmed Aug 2026, Add-Line Data-Loss
# brief §5 — "one level of undo back to what was last saved," NOT a
# full multi-version history, deliberately kept simple). Not Owner-
# only, unlike Manual Override above — undoing an editing mistake
# should be available to whoever can edit a quote's lines in the first
# place (same as add/delete line), not a separate restricted feature. ----------
QUOTE_SNAPSHOT_FIELDS = [
    "discount_pct", "transport_levy", "description",
    "manual_override_total_incl_vat", "override_total_reason", "override_total_by", "override_total_at",
]


@app.post("/quotes/{quote_id}/snapshot")
def snapshot_quote(quote_id: int, tenant_id: str = Depends(get_current_tenant)):
    """Captures the current state as the "last saved" point a later
    revert_quote() call restores. Called once, right when Quote Builder
    is opened for this quote (openQuoteFromIndex(), index.html) — never
    on every subsequent add/edit/delete, or a revert would just restore
    whatever was already there instead of genuinely undoing this
    editing session's changes."""
    with Session(engine) as session:
        quote = get_or_404(session, Quote, quote_id, tenant_id, "Quote")
        lines = session.exec(select(QuoteLineItem).where(QuoteLineItem.quote_id == quote_id, QuoteLineItem.tenant_id == tenant_id)).all()
        quote_fields = {f: getattr(quote, f) for f in QUOTE_SNAPSHOT_FIELDS}
        # Explicit isoformat, not left to json.dumps' default=str fallback
        # below — guarantees this round-trips cleanly through
        # datetime.fromisoformat() in revert_quote(), rather than relying
        # on str(datetime)'s implicit (also-usually-fine, but not
        # guaranteed-by-us) formatting.
        if quote_fields.get("override_total_at") is not None:
            quote_fields["override_total_at"] = quote_fields["override_total_at"].isoformat()
        snapshot = {
            "quote_fields": quote_fields,
            # .json() (Pydantic's own encoder) rather than .dict() —
            # correctly ISO-formats every datetime field on the line
            # (created_at, override_at) up front, so revert_quote() can
            # just re-construct QuoteLineItem(**line_data) directly and
            # let Pydantic's own validation parse them back.
            "lines": [json.loads(l.json()) for l in lines],
        }
        quote.snapshot_json = json.dumps(snapshot, default=str)
        session.add(quote)
        session.commit()
        return {"snapshotted": True, "line_count": len(lines)}


@app.post("/quotes/{quote_id}/revert")
def revert_quote(quote_id: int, tenant_id: str = Depends(get_current_tenant), username: str = Depends(get_current_username)):
    """Restores line items and the editable quote-level fields above to
    exactly the state captured by the most recent snapshot_quote() call.
    Existing lines are deleted and recreated from the snapshot (fresh
    ids, not the originals — any colour-change history tied to a
    replaced line becomes orphaned; an accepted, rare trade-off for a
    genuinely simple one-level undo, per the brief's own instruction)."""
    with Session(engine) as session:
        quote = get_or_404(session, Quote, quote_id, tenant_id, "Quote")
        if not quote.snapshot_json:
            raise HTTPException(400, "No saved snapshot to revert to yet — open this quote in Quote Builder first, then try again.")
        snapshot = json.loads(quote.snapshot_json)

        current_lines = session.exec(select(QuoteLineItem).where(QuoteLineItem.quote_id == quote_id, QuoteLineItem.tenant_id == tenant_id)).all()
        for line in current_lines:
            session.delete(line)

        for line_data in snapshot["lines"]:
            line_data.pop("id", None)
            session.add(QuoteLineItem(**line_data))

        for field, value in snapshot["quote_fields"].items():
            if field == "override_total_at" and value is not None:
                value = datetime.fromisoformat(value)
            setattr(quote, field, value)

        session.add(AuditLog(
            tenant_id=tenant_id, username=username, entity_type="Quote", entity_id=quote_id,
            field="__reverted__", old_value="(edited state)", new_value="(reverted to last-opened snapshot)",
        ))
        session.add(quote)
        session.commit()
        return {"reverted": True, "line_count": len(snapshot["lines"])}


@app.put("/quotes/{quote_id}/lines/{line_id}/colour")
def change_line_colour(quote_id: int, line_id: int, new_colour: str, reason: str = "", changed_by: str = "",
                        tenant_id: str = Depends(get_current_tenant), username: str = Depends(get_current_username)):
    """Confirmed Aug 2026 — a colour quoted might go out of stock and
    need substituting. This changes the ACTIVE colour on the line (what
    shows on the quote, what gets ordered), while logging the change so
    the full history is never lost. original_colour on the line itself
    is never touched here — it's set once, at creation, permanently.

    Manual Override (confirmed Aug 2026, Edit Quote Line In Place brief §2/
    §3 — "confirm the edit path doesn't reopen any... risk", checked here
    too, not just the new PUT .../flooring|blinds|trims|misc endpoints):
    this is exactly the kind of change the brief calls out — a colour swap
    is meaningful enough that an active override should not silently keep
    applying. Uses the same clear-and-flag rule as those endpoints (via
    _reapply_line_calc_respecting_override()), with calc_line_total passed
    as the line's own PRE-override value, since a pure colour swap (no
    product/quantity change here) never changes the real calculated price
    — there is nothing new to compute."""
    with Session(engine) as session:
        line = session.get(QuoteLineItem, line_id)
        if not line or line.quote_id != quote_id or line.tenant_id != tenant_id:
            raise HTTPException(404, "Quote line not found")

        colour_changed = (line.colour or "") != new_colour

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

        override_cleared = False
        if colour_changed and line.pre_override_line_total is not None:
            calc_line_total = line.pre_override_line_total   # nothing new to compute here — restore the real pre-override figure
            override_result = _reapply_line_calc_respecting_override(line, calc_line_total, True, session, tenant_id, username)
            override_cleared = override_result["override_cleared"]

        session.add(line)
        session.commit()
        session.refresh(line)
        result = line.dict()
        result["override_cleared"] = override_cleared
        return result


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
                 status: Optional[str] = None, workflow_status: Optional[str] = None,
                 search: Optional[str] = None, include_price_checks: bool = False,
                 tenant_id: str = Depends(get_current_tenant)):
    """Confirmed Aug 2026 — Order Index needs totals (deposit amount,
    balance amount) visible without clicking into each quote, so this
    now computes them per quote, same VAT_PCT/discount logic as the
    single-quote endpoint (kept identical deliberately — a second,
    slightly different copy of this math is exactly how earlier bugs in
    this project happened). VAT_PCT now comes from Business Settings —
    real bug found and fixed while merging v54: this was hardcoded
    identically here AND in get_quote above, so a VAT change (or a typo
    landing in only one spot) could have made the two quietly disagree.

    workflow_status filter added alongside the legacy status filter
    (confirmed Aug 2026, Order Index / Job Workflow Redesign brief) —
    status kept working, not removed, for anything that still passes it;
    every new call site uses workflow_status instead. Search now also
    matches job_number and site_address (brief §5 — "search by
    Customer, Job number, and Site"), alongside the pre-existing client
    name / quote # / description match."""
    with Session(engine) as session:
        VAT_PCT = get_settings(session, tenant_id).vat_pct
        tt_usernames = _trusted_tester_usernames(session, tenant_id)
        stmt = select(Quote).where(Quote.tenant_id == tenant_id)
        # Price Check (confirmed Aug 2026, New Quote Screen brief §3) —
        # excluded from the Order Index by default: it isn't a real
        # tracked job until explicitly converted. include_price_checks
        # exists for future callers that genuinely need to see them
        # (e.g. a dedicated Price Checks view, not built this round) —
        # not wired to anything in the frontend yet.
        if not include_price_checks:
            stmt = stmt.where(Quote.is_price_check == False)  # noqa: E712 — SQLAlchemy needs == for a WHERE clause, `is False` doesn't build a comparison expression
        if sales_owner:
            stmt = stmt.where(Quote.sales_owner == sales_owner)
        if branch:
            stmt = stmt.where(Quote.branch == branch)
        if status:
            stmt = stmt.where(Quote.status == status)
        if workflow_status:
            stmt = stmt.where(Quote.workflow_status == workflow_status)
        quotes = session.exec(stmt).all()
        if search:
            search_lower = search.lower()
            quotes = [q for q in quotes if search_lower in q.client_name.lower() or search_lower in str(q.id)
                      or search_lower in (q.description or "").lower() or search_lower in (q.job_number or "").lower()
                      or search_lower in (q.site_address or "").lower()]

        today = date.today()
        result = []
        for q in quotes:
            lines = session.exec(select(QuoteLineItem).where(QuoteLineItem.quote_id == q.id, QuoteLineItem.tenant_id == tenant_id)).all()
            # Transport Levy included here too (confirmed Aug 2026) — same
            # reasoning as get_quote() above; keeps the Order Index's own
            # totals consistent with the quote detail screen's, same "one
            # settings source, never two places that could quietly disagree"
            # discipline VAT_PCT already follows in this function. Now
            # shares the actual math with get_quote() too, via
            # _quote_totals() (confirmed Aug 2026, Client Order History
            # Columns brief).
            subtotal_ex_vat = sum(l.line_total for l in lines) + q.transport_levy
            totals = _quote_totals(subtotal_ex_vat, q, VAT_PCT)
            d = q.dict()
            d["total_incl_vat"] = totals["total_incl_vat"]
            d["deposit_amount"] = totals["deposit_amount"]
            d["balance_amount"] = totals["balance_amount"]
            # Manual Override badge (confirmed Aug 2026, Manual Override
            # brief) — q.dict() already carries manual_override_total_incl_vat
            # itself for the quote-level case; has_line_override covers the
            # per-line case too, cheaply, from the `lines` already fetched
            # above (no extra query) — Order Index shows a row's "Adjusted"
            # flag if EITHER is true.
            d["has_line_override"] = any(l.pre_override_line_total is not None for l in lines)
            # Trusted Tester Accounts brief (confirmed Aug 2026) — NOT
            # hidden from the Order Index, per the brief's own explicit
            # requirement ("NOT hidden from Burgert, Ryno, or Madri's
            # normal views — just clearly marked"); this is a display
            # label only, computed fresh here via the same
            # _trusted_tester_usernames() the KPI exclusion above uses,
            # never a stored flag on the row itself.
            d["is_test_data"] = q.sales_owner in tt_usernames
            d["test_data_label"] = f"TEST — {tt_usernames[q.sales_owner]}" if q.sales_owner in tt_usernames else None
            # Next Action / Needs Attention (confirmed Aug 2026, Order
            # Index / Job Workflow Redesign brief + Next Action
            # Addendum) — computed per row so the Order Index table's
            # Next Action column and the Needs Attention list can both
            # be built client-side from this one response, no second
            # request.
            row_materials_ordered = _materials_ordered_for_quote(session, q.id, tenant_id)
            d.update(_job_workflow_info(q, today, row_materials_ordered))
            # Job Workflow Design Proposal Phase 1 (confirmed Aug 2026) —
            # same direct field as get_quote() above.
            d["materials_ordered"] = row_materials_ordered
            result.append(d)
        return result
