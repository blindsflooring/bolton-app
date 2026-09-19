# Restoring Bolton from a Database Backup

This document explains, in plain terms, what Bolton's database backups
are and exactly what to do if one is ever needed.

**This procedure has been performed end to end and verified.** On
19 September 2026 the previous night's backup was restored into a
throwaway Supabase project and checked table by table: 41 of 41 tables,
8 031 of 8 031 rows, no mismatches. Everything below describes what
actually happened, not what should happen in theory. The things that
went wrong are written down too, because they will go wrong again.

## What's actually being protected here

Bolton's real, live data (every quote, client, price book entry, staff
account — everything) lives in one place: a Postgres database hosted by
Supabase. That live database is the only copy that matters day to day.

This backup system is a **safety net** for the situation where something
goes badly wrong with it — accidental deletion, a Supabase outage,
corruption. It answers one question: *if the live database were lost
today, could we get the data back?* The answer is now yes, demonstrably.

It is **not** the same thing as the Dropbox document archive (which keeps
a permanent copy of every quote/invoice/order PDF). That protects
individual documents. This protects the whole database.

## Where backups actually live

**In the app's own Dropbox folder, not the personal one:**

```
/Apps/Bolton Archive 2/Bolton/Database Backups/Daily/
/Apps/Bolton Archive 2/Bolton/Database Backups/Weekly/
```

This document used to give the path as `/Bolton/Database Backups/`, and
that is wrong — it is a different Dropbox namespace. A folder of that
name does exist there, holding one stale backup from 27 August left over
from an earlier app configuration. Looking there during a real incident
would show one three-week-old file and none of the good ones.

- **Daily** — every night at 02:00 UTC. The last **7 restorable** backups
  are kept.
- **Weekly** — every Sunday, kept as a separate set of **4**. Note: no
  weekly backup has ever succeeded as of 19 Sept 2026 (three attempts,
  all during the Dropbox token outage). Do not assume one exists.
- Burgert can trigger one manually at any time from Bolton, before doing
  anything risky.

Retention counts backups that actually uploaded, not rows in the
tracking table — a failed run no longer displaces a good backup. That
was a real bug, fixed 19 Sept 2026.

## If you ever actually need one restored

**You don't need to do this yourself.**

1. Don't panic, and don't try to fix the data by hand in the meantime —
   that makes a real restore harder later.
2. Go to Bolton's "Database Backups" screen (or ask whoever manages the
   technical side) and pick the most recent good one.
3. A developer performs the restore. **Never straight into the live
   database** — see below.
4. Once restored, check a few real, familiar records before trusting it.

---

# Technical detail (for whoever performs the restore)

## Step 1 — Restore into a THROWAWAY database first, always

Create a new, empty Supabase project. Never restore into the live
database, and never into anything with data you care about: the dump
begins by creating tables and will fight anything already there.

Verify the target is empty before starting:

```sql
SELECT count(*) FROM information_schema.tables
WHERE table_schema = 'public' AND table_type = 'BASE TABLE';
```

**It must return 0.** And check the project ref in the connection string
is *not* the production one. The restore used for this verification
refused to start until both were confirmed.

Use the **Session pooler** connection string (port `5432` on the
`...pooler.supabase.com` host), copied from that project's own Connect
dialog. Do not compose the host by hand — the cluster number is not
derivable from the region, and getting it wrong produces
`Tenant or user not found`, which reads like a credentials problem and
is not.

## Step 2 — Which format is this backup?

Recorded alongside every backup (`GET /admin/database-backup`) and
visible in the filename:

- **`.sql.gz`** — a real `pg_dump`, gzip-compressed. Every backup to date
  has been this. Full schema and data.
- **`.json.gz`** — the pure-Python fallback, used only when `pg_dump` is
  unavailable. Data but no schema, and **binary columns are omitted**
  (`documentarchive.pdf_bytes`, `quotephoto.photo_bytes` — those have
  their own Dropbox copies).

## Step 3 — Restore it

With `psql` available, this is the whole job:

```bash
gunzip -c backup_2026-09-19.sql.gz > backup.sql
psql "$TARGET_DATABASE_URL" < backup.sql
```

**If `psql` is not installed** — it was not on the machine this was
verified from, and Supabase's SQL editor cannot ingest a 13 MB dump by
paste — use the committed driver, which needs only `psycopg2`:

```bash
python backend/tools/restore_driver.py backup.sql          # dry run
python backend/tools/restore_driver.py backup.sql --go     # execute
```

It reads the target connection string from `target-url.txt` beside it,
never prints it, and handles the one thing a naive executor gets wrong:
`pg_dump` 18 emits psql **meta-commands** (`\restrict`, `\unrestrict`)
that are not SQL and that the server rejects.

## Step 4 — EXPECT HUNDREDS OF ERRORS, AND IGNORE THEM

This is the most surprising part and the reason this section exists.

A Supabase `pg_dump` contains Supabase's own managed schemas — `auth`,
`storage`, `realtime`, `graphql`, `graphql_public`, `vault`, `pgbouncer`,
`extensions`. On a fresh project these **already exist**, and the
`postgres` role **is not permitted to modify them**. The verified restore
produced **583 errors**, every one of them of this kind:

```
CREATE SCHEMA auth;              -> schema "auth" already exists
CREATE TYPE auth.aal_level ...   -> permission denied for schema auth
CREATE FUNCTION auth.email() ... -> permission denied for schema auth
```

None of that matters. Supabase provides those schemas itself. Bolton's
data is entirely in `public`.

**So do not judge the restore by whether it reported errors.** Judge it
by Step 5.

## Step 5 — Verify against the dump, table by table

This is the actual test of success:

```sql
-- every table Bolton owns
SELECT table_name FROM information_schema.tables
WHERE table_schema = 'public' AND table_type = 'BASE TABLE' ORDER BY 1;

-- and a row count for each
SELECT count(*) FROM public.quote;
```

Compare against the dump itself — each `COPY public.<table> ... FROM
stdin;` block is followed by exactly one line per row, terminated by a
lone `\.`. The verified restore matched on all 41 tables and all 8 031
rows.

Then check records you recognise. The verification used J-0023
(Marlize Louw, Franskraal): 55 bags of screed, 5,4 m + 3,7 m of trim,
R74 081,88 deposit paid 15 September, final payment outstanding — all
present and correct, along with 86 indexes and 66 constraints.

## Step 6 — Two things that will catch you out

**A backup is a point in time, and that cuts deeper than it sounds.**
The 19 September backup was taken at 02:00. The five annual financial
statements were loaded into production later that same morning, so they
are **not in it** — the restored copy has `financialstatement` empty,
correctly. Before restoring over live data, ask what has happened since
the backup ran. A restore is not a rollback; it is a reset to that
moment.

**An older backup carries an older schema.** Restoring a dump from a
month ago gives you that month's columns. Bolton's own startup
(`SQLModel.metadata.create_all()` then `_ensure_new_columns()`, main.py)
adds missing tables and columns when it boots, so pointing the app at a
restored database should reconcile it — but that path has not been
tested, and it is the obvious next thing to verify. For reference, the
27 August dump was missing 12 whole tables and 78 columns relative to
the models three weeks later.

## Step 7 — Afterwards

**Delete the throwaway project.** It now contains real client data, real
addresses and real password hashes. Delete the local dump and the
connection-string file too.

## Where the code lives

- `backend/database_backup.py` — `try_pg_dump()`, `python_logical_backup()`
- `backend/tools/restore_driver.py` — the psycopg2 restore driver
- `backend/main.py` — `run_database_backup_job()` (nightly),
  `_prune_old_backups()` (retention), `_record_and_upload_backup()`
- `backend/models.py` — `DatabaseBackupRecord` (tracking only; the bytes
  live in Dropbox)
