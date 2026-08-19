"""
One-time data migration: local SQLite (bolton.db) -> Supabase Postgres.

Confirmed Aug 2026, deployment kickoff. Scope confirmed directly with
Burgert, not guessed — see CHANGELOG.md's deployment entry:

  MIGRATE:     flooringproduct (81 rows — the 80 originally-confirmed
               real products (Aspen 35 + Azura 40 + 4 + 1 older generic
               Azura entries) plus the 1 real Kalahari Quartz entry
               (id 81), excluding id 82, a confirmed exact duplicate of
               id 81 submitted 6 seconds apart. Live count was 82
               before this exclusion, not 80 — the two Kalahari Quartz
               rows were added after the original 80 were confirmed
               real, so "80 real products" and "82 total rows" are both
               correct statements about different points in time, not
               a contradiction),
               trimproduct (all 15), businesssettings (the 1 real row),
               employee (the 1 real row, Danile Qotsini).
  DO NOT MIGRATE: client, quote, quotelineitem, colourchangelog,
               paymentfollowup, hoursworked, leavebalance,
               leaverequest, document, commissionrate,
               commissionpayment — all confirmed test/empty data as of
               this migration.

Table names match SQLModel's own auto-generated convention (lowercase,
concatenated — flooringproduct, not flooring_product), confirmed
directly against Model.__tablename__ for every model, not guessed —
see supabase_schema.sql's own header for the real story of how this
was found the hard way, running against Postgres for the first time.

Usage:
    `SQLModel.metadata.create_all(engine)` in main.py already creates
    every table automatically the first time the app connects to a
    fresh database, correctly named — so in practice this script can
    just be run directly; `supabase_schema.sql` is not a prerequisite
    for table creation, only for explicit documentation/future RLS
    policies. This script only inserts into tables that must already
    exist, so run the app (or the schema file) against the target
    database at least once first, either way.

    Then, with DATABASE_URL set to the real Supabase connection string
    (never hardcode it — same rule as main.py):

        DATABASE_URL="postgresql://..." python migrate_to_supabase.py

    Safe to re-run: each table is checked for existing rows first and
    skipped with a warning rather than silently duplicating, since this
    is meant to run exactly once against a fresh database.
"""
import os
import sqlite3
import sys

from sqlalchemy import create_engine, text

SQLITE_PATH = os.path.join(os.path.dirname(__file__), "bolton.db")

# (sqlite table, postgres table, [excluded ids], columns in SQLModel/schema
# order, {bool columns}) — SQLite has no real boolean type (stores 0/1 as
# plain integers); Postgres does and won't implicitly cast an integer
# parameter to boolean, so these need explicit conversion before insert.
# Real bug found running this the first time: employee failed on exactly
# this (thirteenth_cheque_eligible, commission_eligible) after
# flooring_product/trim_product/business_settings had already succeeded,
# since none of those three have any boolean columns.
TABLES = [
    ("flooringproduct", "flooringproduct", [82], [
        "id", "product_name", "supplier", "pricing_type", "base_cost_ex_vat",
        "wastage_pct", "trade_discount_pct", "m2_per_pack", "unit",
        "last_updated", "source", "tiles_per_pack", "flooring_category",
        "sell_markup_multiplier", "over_tiles_multiplier",
        "removed_tiles_multiplier", "display_order", "colour",
        "delivery_fee_per_m2", "settlement_discount_pct", "tile_width_mm",
        "tile_length_mm", "tile_thickness_mm",
    ], set()),
    ("trimproduct", "trimproduct", [], [
        "id", "product_name", "profile_code", "category", "supplier",
        "cost_ex_vat_per_lm", "wastage_pct", "pricing_mode",
        "fixed_sell_price_per_lm", "markup_multiplier", "unit",
        "last_updated", "source", "vat_pct",
    ], set()),
    ("businesssettings", "businesssettings", [], [
        "id", "business_name", "address", "phone", "email", "vat_number",
        "bank_details", "yoco_payment_link", "vat_pct", "default_deposit_pct",
        "bag_overage_rate", "default_labour_rate_per_m2", "order_overdue_days",
    ], set()),
    ("employee", "employee", [], [
        "id", "full_name", "role_title", "start_date", "birthday",
        "id_number", "phone", "email", "employment_status",
        "thirteenth_cheque_eligible", "notes", "commission_eligible",
        "commission_role_type", "sales_owner_key", "created_at",
    ], {"thirteenth_cheque_eligible", "commission_eligible"}),
]


def main():
    database_url = os.environ.get("DATABASE_URL")
    if not database_url or database_url.startswith("sqlite"):
        print("ERROR: set DATABASE_URL to the real Supabase Postgres connection "
              "string first (not committed anywhere, passed as an env var only).")
        sys.exit(1)

    sqlite_conn = sqlite3.connect(SQLITE_PATH)
    sqlite_conn.row_factory = sqlite3.Row
    pg_engine = create_engine(database_url)

    with pg_engine.connect() as pg_conn:
        for sqlite_table, pg_table, excluded_ids, columns, bool_columns in TABLES:
            # Safety check: skip tables that already have data, rather
            # than silently duplicating — this script is meant to run
            # exactly once, against a fresh database.
            existing = pg_conn.execute(text(f"SELECT COUNT(*) FROM {pg_table}")).scalar()
            if existing:
                print(f"SKIP {pg_table}: already has {existing} row(s) — not re-migrating.")
                continue

            cur = sqlite_conn.cursor()
            placeholders = ",".join("?" * len(excluded_ids)) if excluded_ids else None
            where_clause = f" WHERE id NOT IN ({placeholders})" if excluded_ids else ""
            cur.execute(f"SELECT {','.join(columns)} FROM {sqlite_table}{where_clause}", excluded_ids)
            rows = cur.fetchall()

            if not rows:
                print(f"SKIP {pg_table}: no rows in local SQLite to migrate.")
                continue

            col_list = ",".join(columns)
            param_list = ",".join(f":{c}" for c in columns)
            insert_sql = text(f"INSERT INTO {pg_table} ({col_list}) VALUES ({param_list})")

            for row in rows:
                values = dict(row)
                for bc in bool_columns:
                    values[bc] = bool(values[bc])
                pg_conn.execute(insert_sql, values)

            # Bump the serial sequence past the highest id we just
            # inserted explicitly — Postgres SERIAL doesn't know about
            # rows inserted with an explicit id, so the next auto-assigned
            # id would otherwise collide with one we just wrote.
            pg_conn.execute(text(
                f"SELECT setval(pg_catalog.pg_get_serial_sequence('{pg_table}', 'id'), "
                f"(SELECT MAX(id) FROM {pg_table}))"
            ))
            pg_conn.commit()
            excluded_note = f" (excluded id(s) {excluded_ids})" if excluded_ids else ""
            print(f"OK {pg_table}: migrated {len(rows)} row(s){excluded_note}.")

    sqlite_conn.close()
    print("\nMigration complete.")


if __name__ == "__main__":
    main()
