"""
Blinds & Flooring Studio bolt-on — Phase 1 backend.
Price Book + Quote Builder. No Xero, no PDF import, no AI assistant yet —
those are Phase 2+ per the build brief.

Run: uvicorn main:app --reload --port 8000
"""
from datetime import datetime, date
from typing import List, Optional
import os
import shutil
import uuid

from fastapi import FastAPI, HTTPException, Query, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from sqlmodel import SQLModel, Session, create_engine, select

from models import (
    FlooringProduct, BlindsProduct, TrimProduct, Quote, QuoteLineItem, Client,
    BusinessSettings, Employee, CommissionRate, CommissionPayment,
    HoursWorked, Document, LeaveBalance, LeaveRequest, ColourChangeLog, PaymentFollowUp,
    JobType, UserRole, StairwellType,
)
from calculations import calculate_flooring_line, calculate_blinds_line, calculate_trim_line, calculate_stairwell_line, line_real_cost

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
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten before real deployment
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup():
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


def get_session():
    with Session(engine) as session:
        yield session


def strip_sensitive_fields(line_item: dict, role: str) -> dict:
    """Sales role never sees cost or margin — enforced server-side, not just
    hidden in the UI. Applies to quote line items wherever they're returned.
    bags_allowed and tile_removal_fee_total stay visible — they're
    operational/client-facing info, not cost data. Everything else that
    reveals what a job actually costs (material, glue, labour, compound,
    the total real cost) is stripped."""
    if role == UserRole.sales:
        line_item = dict(line_item)
        for field in (
            "unit_cost", "margin_pct", "glue_cost_total",
            "labour_cost_total", "compound_cost_total", "total_job_cost",
        ):
            line_item.pop(field, None)
    return line_item


# ---------- Price Book: Flooring ----------

@app.get("/price-book/flooring", response_model=List[FlooringProduct])
def list_flooring():
    with Session(engine) as session:
        return session.exec(select(FlooringProduct)).all()


@app.post("/price-book/flooring", response_model=FlooringProduct)
def create_flooring(product: FlooringProduct):
    with Session(engine) as session:
        session.add(product)
        session.commit()
        session.refresh(product)
        return product


@app.put("/price-book/flooring/{product_id}", response_model=FlooringProduct)
def update_flooring(product_id: int, updates: FlooringProduct):
    with Session(engine) as session:
        product = session.get(FlooringProduct, product_id)
        if not product:
            raise HTTPException(404, "Flooring product not found")
        data = updates.dict(exclude_unset=True, exclude={"id"})
        for k, v in data.items():
            setattr(product, k, v)
        product.last_updated = datetime.utcnow()
        session.add(product)
        session.commit()
        session.refresh(product)
        return product


@app.delete("/price-book/flooring/{product_id}")
def delete_flooring(product_id: int):
    with Session(engine) as session:
        product = session.get(FlooringProduct, product_id)
        if not product:
            raise HTTPException(404, "Flooring product not found")
        session.delete(product)
        session.commit()
        return {"deleted": product_id}


@app.post("/price-book/flooring/bulk-import")
def bulk_import_flooring(products: List[FlooringProduct]):
    """Confirmed Aug 2026 — loading a full supplier range one product at
    a time through the form doesn't scale (e.g. Aspen's 35+ colours
    across 5 ranges). Takes a list of the same shape the single-create
    endpoint accepts. All-or-nothing — if any product fails validation,
    nothing is committed, so a bad import can't leave the price book
    half-populated."""
    with Session(engine) as session:
        for product in products:
            session.add(product)
        session.commit()
        return {"imported": len(products)}


# ---------- Price Book: Blinds ----------

@app.get("/price-book/blinds", response_model=List[BlindsProduct])
def list_blinds():
    with Session(engine) as session:
        return session.exec(select(BlindsProduct)).all()


@app.post("/price-book/blinds", response_model=BlindsProduct)
def create_blinds(product: BlindsProduct):
    with Session(engine) as session:
        session.add(product)
        session.commit()
        session.refresh(product)
        return product


@app.put("/price-book/blinds/{product_id}", response_model=BlindsProduct)
def update_blinds(product_id: int, updates: BlindsProduct):
    with Session(engine) as session:
        product = session.get(BlindsProduct, product_id)
        if not product:
            raise HTTPException(404, "Blinds product not found")
        data = updates.dict(exclude_unset=True, exclude={"id"})
        for k, v in data.items():
            setattr(product, k, v)
        product.last_updated = datetime.utcnow()
        session.add(product)
        session.commit()
        session.refresh(product)
        return product


@app.delete("/price-book/blinds/{product_id}")
def delete_blinds(product_id: int):
    with Session(engine) as session:
        product = session.get(BlindsProduct, product_id)
        if not product:
            raise HTTPException(404, "Blinds product not found")
        session.delete(product)
        session.commit()
        return {"deleted": product_id}


# ---------- Price Book: Trims ----------

@app.get("/price-book/trims", response_model=List[TrimProduct])
def list_trims():
    with Session(engine) as session:
        return session.exec(select(TrimProduct)).all()


@app.post("/price-book/trims", response_model=TrimProduct)
def create_trim(product: TrimProduct):
    with Session(engine) as session:
        session.add(product)
        session.commit()
        session.refresh(product)
        return product


@app.put("/price-book/trims/bulk-update-markup")
def bulk_update_trim_markup(new_markup: float, category: str = None):
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
        stmt = select(TrimProduct).where(TrimProduct.pricing_mode == "markup")
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
def update_trim(product_id: int, updates: TrimProduct):
    with Session(engine) as session:
        product = session.get(TrimProduct, product_id)
        if not product:
            raise HTTPException(404, "Trim product not found")
        data = updates.dict(exclude_unset=True, exclude={"id"})
        for k, v in data.items():
            setattr(product, k, v)
        product.last_updated = datetime.utcnow()
        session.add(product)
        session.commit()
        session.refresh(product)
        return product


@app.delete("/price-book/trims/{product_id}")
def delete_trim(product_id: int):
    with Session(engine) as session:
        product = session.get(TrimProduct, product_id)
        if not product:
            raise HTTPException(404, "Trim product not found")
        session.delete(product)
        session.commit()
        return {"deleted": product_id}


# ---------- Analytics ----------

@app.get("/analytics/overview")
def analytics_overview():
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
        quotes = session.exec(select(Quote)).all()
        lines = session.exec(select(QuoteLineItem)).all()

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
def list_employees(role: str = Query(default=UserRole.owner)):
    with Session(engine) as session:
        employees = session.exec(select(Employee)).all()
        return [strip_employee_notes(e.dict(), role) for e in employees]


@app.post("/employees")
def create_employee(employee: Employee):
    coerce_date_fields(employee, "start_date", "birthday")
    with Session(engine) as session:
        session.add(employee)
        session.commit()
        session.refresh(employee)
        return employee


@app.put("/employees/{employee_id}")
def update_employee(employee_id: int, updates: Employee):
    coerce_date_fields(updates, "start_date", "birthday")
    with Session(engine) as session:
        emp = session.get(Employee, employee_id)
        if not emp:
            raise HTTPException(404, "Employee not found")
        data = updates.dict(exclude_unset=True, exclude={"id"})
        for k, v in data.items():
            setattr(emp, k, v)
        session.add(emp)
        session.commit()
        session.refresh(emp)
        return emp


@app.delete("/employees/{employee_id}")
def delete_employee(employee_id: int):
    with Session(engine) as session:
        emp = session.get(Employee, employee_id)
        if not emp:
            raise HTTPException(404, "Employee not found")
        session.delete(emp)
        session.commit()
        return {"deleted": employee_id}


# ---------- HR: Commission rate card ----------
# Owner-only to edit, per the brief ("Admin... Cannot change commission
# rates/structures"). This endpoint doesn't enforce that server-side yet
# (no real auth exists in this Phase 1 build — role is a client-supplied
# param throughout), but the frontend gates the edit UI to Owner only,
# consistent with how the rest of this app's role system currently works.

@app.get("/commission-rates")
def list_commission_rates():
    with Session(engine) as session:
        return session.exec(select(CommissionRate).where(CommissionRate.active == True)).all()


@app.post("/commission-rates")
def create_commission_rate(rate: CommissionRate):
    with Session(engine) as session:
        session.add(rate)
        session.commit()
        session.refresh(rate)
        return rate


@app.put("/commission-rates/{rate_id}")
def update_commission_rate(rate_id: int, updates: CommissionRate):
    with Session(engine) as session:
        rate = session.get(CommissionRate, rate_id)
        if not rate:
            raise HTTPException(404, "Rate not found")
        data = updates.dict(exclude_unset=True, exclude={"id"})
        for k, v in data.items():
            setattr(rate, k, v)
        session.add(rate)
        session.commit()
        session.refresh(rate)
        return rate


@app.delete("/commission-rates/{rate_id}")
def delete_commission_rate(rate_id: int):
    with Session(engine) as session:
        rate = session.get(CommissionRate, rate_id)
        if not rate:
            raise HTTPException(404, "Rate not found")
        rate.active = False   # soft delete — keeps history for past statements intact
        session.add(rate)
        session.commit()
        return {"deactivated": rate_id}


# ---------- HR: Commission calculation ----------

@app.get("/commission/statement/{sales_owner_key}")
def commission_statement(sales_owner_key: str, year: int, month: int):
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
            select(Employee).where(Employee.sales_owner_key == sales_owner_key)
        ).first()
        if not employee:
            raise HTTPException(404, f"No employee found with sales_owner_key '{sales_owner_key}'")
        if not employee.commission_eligible:
            return {"employee": employee.full_name, "commission_eligible": False, "commission_due": 0.0}

        paid_quotes = session.exec(
            select(Quote).where(
                Quote.sales_owner == sales_owner_key,
                Quote.status == "paid",
            )
        ).all()
        paid_quotes = [q for q in paid_quotes if q.created_at.year == year and q.created_at.month == month]

        rates = session.exec(select(CommissionRate).where(CommissionRate.active == True)).all()

        if employee.commission_role_type == "pure_sales":
            total_turnover = 0.0
            total_gp = 0.0
            for q in paid_quotes:
                lines = session.exec(select(QuoteLineItem).where(QuoteLineItem.quote_id == q.id)).all()
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
                lines = session.exec(select(QuoteLineItem).where(QuoteLineItem.quote_id == q.id)).all()
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
                for l in session.exec(select(QuoteLineItem).where(QuoteLineItem.quote_id == q.id)).all()
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
def log_hours(entry: HoursWorked):
    coerce_date_fields(entry, "work_date")
    with Session(engine) as session:
        session.add(entry)
        session.commit()
        session.refresh(entry)
        return entry


@app.get("/hours-worked")
def list_hours(employee_id: Optional[int] = None, year: Optional[int] = None, month: Optional[int] = None):
    with Session(engine) as session:
        stmt = select(HoursWorked)
        if employee_id:
            stmt = stmt.where(HoursWorked.employee_id == employee_id)
        entries = session.exec(stmt).all()
        if year:
            entries = [e for e in entries if e.work_date.year == year]
        if month:
            entries = [e for e in entries if e.work_date.month == month]
        return entries


@app.delete("/hours-worked/{entry_id}")
def delete_hours(entry_id: int):
    with Session(engine) as session:
        entry = session.get(HoursWorked, entry_id)
        if not entry:
            raise HTTPException(404, "Entry not found")
        session.delete(entry)
        session.commit()
        return {"deleted": entry_id}


@app.get("/hours-worked/summary")
def hours_summary(year: int, month: int, employee_id: Optional[int] = None):
    """Monthly summary, per the brief's "accountant-ready" requirement —
    totals by hour type, per employee (or one employee if filtered)."""
    with Session(engine) as session:
        employees = session.exec(select(Employee)).all()
        if employee_id:
            employees = [e for e in employees if e.id == employee_id]

        result = []
        for emp in employees:
            entries = session.exec(
                select(HoursWorked).where(HoursWorked.employee_id == emp.id)
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


@app.post("/documents/upload")
def upload_document(employee_id: int, document_type: str = "other", owner_only: bool = False,
                     notes: str = "", file: UploadFile = File(...)):
    # SECURITY FIX: file.filename is client-supplied and untrusted — a
    # crafted value like "../../../etc/passwd" would otherwise let a
    # direct API call write outside UPLOAD_DIR. basename() strips any
    # directory components before it's used in the storage path.
    original_name = os.path.basename(file.filename or "upload")
    safe_name = f"{uuid.uuid4().hex}_{original_name}"
    dest_path = os.path.join(UPLOAD_DIR, safe_name)
    with open(dest_path, "wb") as f:
        shutil.copyfileobj(file.file, f)
    with Session(engine) as session:
        doc = Document(
            employee_id=employee_id, document_type=document_type, filename=original_name,
            file_path=safe_name, owner_only=owner_only, notes=notes,
        )
        session.add(doc)
        session.commit()
        session.refresh(doc)
        return doc


@app.get("/documents")
def list_documents(employee_id: Optional[int] = None, role: str = Query(default=UserRole.owner)):
    with Session(engine) as session:
        stmt = select(Document)
        if employee_id:
            stmt = stmt.where(Document.employee_id == employee_id)
        docs = session.exec(stmt).all()
        if role == UserRole.sales:
            docs = [d for d in docs if not d.owner_only]
        return docs


@app.get("/documents/{doc_id}/download")
def download_document(doc_id: int, role: str = Query(default=UserRole.sales)):
    with Session(engine) as session:
        doc = session.get(Document, doc_id)
        if not doc:
            raise HTTPException(404, "Document not found")
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
        full_path = os.path.join(UPLOAD_DIR, doc.file_path)
        if not os.path.exists(full_path):
            raise HTTPException(404, "File missing on disk")
        return FileResponse(full_path, filename=doc.filename)


@app.delete("/documents/{doc_id}")
def delete_document(doc_id: int):
    with Session(engine) as session:
        doc = session.get(Document, doc_id)
        if not doc:
            raise HTTPException(404, "Document not found")
        full_path = os.path.join(UPLOAD_DIR, doc.file_path)
        if os.path.exists(full_path):
            os.remove(full_path)
        session.delete(doc)
        session.commit()
        return {"deleted": doc_id}


# ---------- HR: Leave ----------

@app.post("/leave-balances")
def create_leave_balance(balance: LeaveBalance):
    coerce_date_fields(balance, "cycle_start_date")
    with Session(engine) as session:
        session.add(balance)
        session.commit()
        session.refresh(balance)
        return balance


@app.get("/leave-balances")
def list_leave_balances(employee_id: Optional[int] = None):
    with Session(engine) as session:
        stmt = select(LeaveBalance)
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
def submit_leave_request(request: LeaveRequest):
    """Confirmed Aug 2026: submitting a request does NOT touch the
    balance — only approval does. This matches the brief's explicit
    workflow (request -> approve -> balance updates)."""
    coerce_date_fields(request, "start_date", "end_date")
    with Session(engine) as session:
        request.status = "pending"
        session.add(request)
        session.commit()
        session.refresh(request)
        return request


@app.get("/leave-requests")
def list_leave_requests(employee_id: Optional[int] = None, status: Optional[str] = None):
    with Session(engine) as session:
        stmt = select(LeaveRequest)
        if employee_id:
            stmt = stmt.where(LeaveRequest.employee_id == employee_id)
        if status:
            stmt = stmt.where(LeaveRequest.status == status)
        return session.exec(stmt).all()


@app.put("/leave-requests/{request_id}/approve")
def approve_leave_request(request_id: int, reviewed_by: str):
    with Session(engine) as session:
        req = session.get(LeaveRequest, request_id)
        if not req:
            raise HTTPException(404, "Request not found")
        if req.status != "pending":
            raise HTTPException(400, f"Request is already {req.status}, cannot approve again")

        balance = session.exec(
            select(LeaveBalance).where(
                LeaveBalance.employee_id == req.employee_id,
                LeaveBalance.leave_type == req.leave_type,
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
def reject_leave_request(request_id: int, reviewed_by: str):
    with Session(engine) as session:
        req = session.get(LeaveRequest, request_id)
        if not req:
            raise HTTPException(404, "Request not found")
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
def list_clients(search: str = None):
    """search filters by name (case-insensitive, contains) — used by the
    Order Index / New Quote client picker."""
    with Session(engine) as session:
        stmt = select(Client)
        clients = session.exec(stmt).all()
        if search:
            search_lower = search.lower()
            clients = [c for c in clients if search_lower in c.name.lower()]
        return clients


@app.get("/clients/{client_id}")
def get_client(client_id: int):
    with Session(engine) as session:
        client = session.get(Client, client_id)
        if not client:
            raise HTTPException(404, "Client not found")
        return client


@app.post("/clients")
def create_client(client: Client):
    with Session(engine) as session:
        session.add(client)
        session.commit()
        session.refresh(client)
        return client


@app.put("/clients/{client_id}")
def update_client(client_id: int, updates: Client):
    with Session(engine) as session:
        client = session.get(Client, client_id)
        if not client:
            raise HTTPException(404, "Client not found")
        data = updates.dict(exclude_unset=True, exclude={"id"})
        for k, v in data.items():
            setattr(client, k, v)
        session.add(client)
        session.commit()
        session.refresh(client)
        return client


@app.delete("/clients/{client_id}")
def delete_client(client_id: int):
    with Session(engine) as session:
        client = session.get(Client, client_id)
        if not client:
            raise HTTPException(404, "Client not found")
        session.delete(client)
        session.commit()
        return {"deleted": client_id}


@app.get("/clients/{client_id}/quotes")
def get_client_quotes(client_id: int):
    """Order history for a client — every quote ever linked to this
    record, most recent first."""
    with Session(engine) as session:
        client = session.get(Client, client_id)
        if not client:
            raise HTTPException(404, "Client not found")
        quotes = session.exec(
            select(Quote).where(Quote.client_id == client_id).order_by(Quote.created_at.desc())
        ).all()
        return {"client": client, "quotes": quotes}


# ---------- Business Settings ----------

def get_settings(session: Session) -> BusinessSettings:
    """Confirmed Aug 2026 — the single source of truth for business-wide
    values (VAT %, default deposit %, screed bag overage rate, default
    labour rate, Order Index overdue threshold, plus the original
    letterhead details). Auto-creates the default row on first call if
    none exists, same as before, so this never errors on a fresh
    database. Call this instead of hardcoding a business-wide value
    directly — that exact duplication (VAT_PCT hardcoded identically in
    two endpoints below) is the bug this table was expanded to close."""
    settings = session.get(BusinessSettings, 1)
    if not settings:
        settings = BusinessSettings(id=1)
        session.add(settings)
        session.commit()
        session.refresh(settings)
    return settings


@app.get("/business-settings")
def get_business_settings():
    with Session(engine) as session:
        return get_settings(session)


@app.put("/business-settings")
def update_business_settings(updates: BusinessSettings, role: str = Query(default=UserRole.owner)):
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
        settings = get_settings(session)
        data = updates.dict(exclude_unset=True, exclude={"id"})
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
                  client_id: int = None):
    """If client_id is given, client_name AND site_address are taken
    from that real Client record (overriding whatever was passed) —
    confirmed Aug 2026, previously only client_name did this, leaving a
    real gap where a known client's saved address never carried through
    to their new quote. Without client_id, this is a walk-in/one-off
    quote using just the typed name, no CRM entry required."""
    with Session(engine) as session:
        site_address = ""
        if client_id:
            client = session.get(Client, client_id)
            if not client:
                raise HTTPException(404, "Client not found")
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
        )
        session.add(quote)
        session.commit()
        session.refresh(quote)
        return quote


@app.put("/quotes/{quote_id}/discount")
def update_quote_discount(quote_id: int, discount_pct: float):
    """Set/change the quote-level discount after the fact — you often won't
    know the discount until the quote's already built up with line items."""
    with Session(engine) as session:
        quote = session.get(Quote, quote_id)
        if not quote:
            raise HTTPException(404, "Quote not found")
        quote.discount_pct = discount_pct
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
                          final_payment_method: str = None):
    """Update a quote's own details — client name, sales owner, branch,
    status, plus order-tracking fields (site address, installation date,
    invoice/payment dates and methods) confirmed Aug 2026 for the Order
    Index. Used by the "Save Quote" button. Line items are already saved
    individually as they're added — this covers quote-level fields only.

    Date params are accepted as strings and explicitly coerced — same
    fix as v38's coerce_date_fields(), applied here from the start this
    time rather than being rediscovered as a bug later."""
    with Session(engine) as session:
        quote = session.get(Quote, quote_id)
        if not quote:
            raise HTTPException(404, "Quote not found")
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
def log_follow_up(quote_id: int, follow_up_date: str, notes: str = ""):
    with Session(engine) as session:
        quote = session.get(Quote, quote_id)
        if not quote:
            raise HTTPException(404, "Quote not found")
        entry = PaymentFollowUp(quote_id=quote_id, follow_up_date=date.fromisoformat(follow_up_date), notes=notes)
        session.add(entry)
        session.commit()
        session.refresh(entry)
        return entry


@app.get("/quotes/{quote_id}/follow-ups")
def list_follow_ups(quote_id: int):
    with Session(engine) as session:
        return session.exec(
            select(PaymentFollowUp).where(PaymentFollowUp.quote_id == quote_id).order_by(PaymentFollowUp.follow_up_date)
        ).all()


@app.delete("/quotes/{quote_id}")
def delete_quote(quote_id: int):
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
        quote = session.get(Quote, quote_id)
        if not quote:
            raise HTTPException(404, "Quote not found")
        lines = session.exec(
            select(QuoteLineItem).where(QuoteLineItem.quote_id == quote_id)
        ).all()
        for line in lines:
            colour_logs = session.exec(
                select(ColourChangeLog).where(ColourChangeLog.quote_line_item_id == line.id)
            ).all()
            for log in colour_logs:
                session.delete(log)
            session.delete(line)
        follow_ups = session.exec(
            select(PaymentFollowUp).where(PaymentFollowUp.quote_id == quote_id)
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
                       role: str = Query(default=UserRole.owner)):
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
        quote = session.get(Quote, quote_id)
        if not quote:
            raise HTTPException(404, "Quote not found")
        product = session.get(FlooringProduct, product_id)
        if not product:
            raise HTTPException(404, "Flooring product not found")

        calc = calculate_flooring_line(
            product, quantity_m2, job_type, discount_pct,
            glue_cost_per_unit, glue_coverage_m2, labour_rate_per_m2,
            bag_cost, bag_coverage_m2, own_staff, markup_override,
            include_tile_removal_fee,
        )
        line = QuoteLineItem(
            quote_id=quote_id, category="flooring", product_id=product_id,
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
                     discount_pct: float = 0.0, role: str = Query(default=UserRole.owner)):
    with Session(engine) as session:
        quote = session.get(Quote, quote_id)
        if not quote:
            raise HTTPException(404, "Quote not found")
        product = session.get(BlindsProduct, product_id)
        if not product:
            raise HTTPException(404, "Blinds product not found")

        calc = calculate_blinds_line(product, width_mm, drop_mm, discount_pct)
        line = QuoteLineItem(
            quote_id=quote_id, category="blinds", product_id=product_id,
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
                   discount_pct: float = 0.0, role: str = Query(default=UserRole.owner)):
    with Session(engine) as session:
        quote = session.get(Quote, quote_id)
        if not quote:
            raise HTTPException(404, "Quote not found")
        product = session.get(TrimProduct, product_id)
        if not product:
            raise HTTPException(404, "Trim product not found")

        calc = calculate_trim_line(product, length_m, discount_pct)
        line = QuoteLineItem(
            quote_id=quote_id, category="trim", product_id=product_id,
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
                        role: str = Query(default=UserRole.owner)):
    """
    stair_area_m2 defaults to 0.45 (confirmed: 900mm wide tread x (300mm
    going + 200mm riser) = 0.9 x 0.5). Override if your stairs differ.
    own_staff: True (default) = your own salaried guys, labour cost treated
    as R0 (charge = pure margin). False = outside/subcontracted, labour
    cost treated as pass-through (roughly what you actually pay out).
    """
    with Session(engine) as session:
        quote = session.get(Quote, quote_id)
        if not quote:
            raise HTTPException(404, "Quote not found")
        vinyl_product = session.get(FlooringProduct, vinyl_product_id)
        if not vinyl_product:
            raise HTTPException(404, "Vinyl product not found")
        nosing_product = session.get(TrimProduct, nosing_product_id)
        if not nosing_product:
            raise HTTPException(404, "Nosing product not found")
        if not vinyl_product.tiles_per_pack:
            raise HTTPException(400, "Selected vinyl product has no tiles_per_pack set — required for stairwell vinyl billing")

        calc = calculate_stairwell_line(vinyl_product, nosing_product, num_stairs, stairwell_type, stair_area_m2=stair_area_m2, own_staff=own_staff)
        line = QuoteLineItem(
            quote_id=quote_id, category="stairwell",
            product_id=vinyl_product_id,
            product_name=f"{vinyl_product.product_name} + {nosing_product.product_name} (stairwell)",
            unit_cost=0, unit_price=0,
            line_total=calc["line_total"], margin_pct=calc["margin_pct"],
            total_job_cost=calc["total_job_cost"],
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
            labour_cost_total=calc["labour_cost_total"],
            labour_charged_total=calc["labour_charged_total"],
            own_staff=calc["own_staff"],
        )
        session.add(line)
        session.commit()
        session.refresh(line)

        result = strip_sensitive_fields(line.dict(), role)
        if calc["warning"] and role != UserRole.sales:
            result["warning"] = calc["warning"]
        # vinyl_cost_total / nosing_cost_total are stairwell-specific cost
        # fields not covered by the standard strip list — remove for Sales
        if role == UserRole.sales:
            result.pop("vinyl_cost_total", None)
            result.pop("nosing_cost_total", None)
        return result


@app.post("/quotes/{quote_id}/lines/misc")
def add_misc_line(quote_id: int, description: str, amount_ex_vat: float, cost_ex_vat: float = 0.0,
                   role: str = Query(default=UserRole.owner)):
    """Confirmed Aug 2026 — freeform line for anything that doesn't fit
    an existing category: extra Saturday/Sunday labour, a one-off
    special request, anything not covered by a real product record.
    cost_ex_vat is optional (defaults to 0, i.e. pure margin) — useful
    for things like weekend labour where there's genuinely no
    additional cost beyond what's already being paid in salary."""
    with Session(engine) as session:
        quote = session.get(Quote, quote_id)
        if not quote:
            raise HTTPException(404, "Quote not found")
        margin_pct = (amount_ex_vat - cost_ex_vat) / amount_ex_vat if amount_ex_vat else 0.0
        line = QuoteLineItem(
            quote_id=quote_id, category="misc", product_id=0,
            product_name=description,
            unit_cost=cost_ex_vat, unit_price=amount_ex_vat,
            line_total=amount_ex_vat, margin_pct=margin_pct,
        )
        session.add(line)
        session.commit()
        session.refresh(line)
        return strip_sensitive_fields(line.dict(), role)


@app.get("/quotes/{quote_id}")
def get_quote(quote_id: int, role: str = Query(default=UserRole.owner)):
    with Session(engine) as session:
        quote = session.get(Quote, quote_id)
        if not quote:
            raise HTTPException(404, "Quote not found")
        lines = session.exec(
            select(QuoteLineItem).where(QuoteLineItem.quote_id == quote_id)
        ).all()
        lines_out = [strip_sensitive_fields(l.dict(), role) for l in lines]
        subtotal_ex_vat = sum(l["line_total"] for l in lines_out)

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
        VAT_PCT = get_settings(session).vat_pct
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
def delete_quote_line(quote_id: int, line_id: int):
    with Session(engine) as session:
        line = session.get(QuoteLineItem, line_id)
        if not line or line.quote_id != quote_id:
            raise HTTPException(404, "Quote line not found")
        session.delete(line)
        session.commit()
        return {"deleted": line_id}


@app.put("/quotes/{quote_id}/lines/{line_id}/colour")
def change_line_colour(quote_id: int, line_id: int, new_colour: str, reason: str = "", changed_by: str = ""):
    """Confirmed Aug 2026 — a colour quoted might go out of stock and
    need substituting. This changes the ACTIVE colour on the line (what
    shows on the quote, what gets ordered), while logging the change so
    the full history is never lost. original_colour on the line itself
    is never touched here — it's set once, at creation, permanently."""
    with Session(engine) as session:
        line = session.get(QuoteLineItem, line_id)
        if not line or line.quote_id != quote_id:
            raise HTTPException(404, "Quote line not found")

        # BUG FIXED Aug 2026: this used to reject setting a colour when the
        # line had none yet — but "forgot to set a colour, add it now" is
        # exactly as legitimate a use case as "swap an existing colour for
        # another." Logged as old_colour="(none)" for a readable history,
        # rather than blocking the action entirely.
        log_entry = ColourChangeLog(
            quote_line_item_id=line_id, old_colour=line.colour or "(none)", new_colour=new_colour,
            reason=reason, changed_by=changed_by,
        )
        session.add(log_entry)

        line.colour = new_colour
        session.add(line)
        session.commit()
        session.refresh(line)
        return line


@app.get("/quotes/{quote_id}/lines/{line_id}/colour-history")
def get_colour_history(quote_id: int, line_id: int):
    """Internal/operational view — never shown on the client-facing
    printed quote, which only ever shows the current colour."""
    with Session(engine) as session:
        line = session.get(QuoteLineItem, line_id)
        if not line or line.quote_id != quote_id:
            raise HTTPException(404, "Quote line not found")
        history = session.exec(
            select(ColourChangeLog)
            .where(ColourChangeLog.quote_line_item_id == line_id)
            .order_by(ColourChangeLog.changed_at)
        ).all()
        return {
            "original_colour": line.original_colour,
            "current_colour": line.colour,
            "changes": history,
        }


@app.get("/quotes")
def list_quotes(sales_owner: Optional[str] = None, branch: Optional[str] = None,
                 status: Optional[str] = None, search: Optional[str] = None):
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
        VAT_PCT = get_settings(session).vat_pct
        stmt = select(Quote)
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
            lines = session.exec(select(QuoteLineItem).where(QuoteLineItem.quote_id == q.id)).all()
            subtotal_ex_vat = sum(l.line_total for l in lines)
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
