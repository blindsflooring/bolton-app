# Restoring Bolton from a Database Backup

This document explains, in plain terms, what Bolton's database backups
are and exactly what to do if one is ever needed. If a backup can't be
explained back in plain language, it isn't a finished backup — this is
that explanation.

## What's actually being protected here

Bolton's real, live data (every quote, client, price book entry, staff
account — everything) lives in one place: a Postgres database hosted by
Supabase. That live database is the only copy that matters day to day.

This backup system is a **safety net**, separate from that live
database, for the situation where something goes badly wrong with it —
accidental deletion, a Supabase outage, corruption, anything that makes
the live data unavailable or wrong. It answers one question: *if the
live database were lost today, could we get the data back?*

It is **not** the same thing as the Dropbox document archive (the
separate system that keeps a permanent copy of every quote/invoice/
order PDF ever generated). That system protects individual documents.
This one protects the whole database.

## Where backups live and how often they're taken

- **Daily** — a fresh backup every night, automatically. The last **7**
  daily backups are kept; older ones are deleted automatically so
  storage doesn't grow forever.
- **Weekly** — every Sunday, that night's backup is also kept in a
  separate weekly set. The last **4** weekly backups are kept.
- Both live in Dropbox, in `Bolton/Database Backups/Daily/` and
  `Bolton/Database Backups/Weekly/`.
- Burgert (Owner) can also trigger a backup manually at any time from
  Bolton itself, right before doing something risky (a big price-book
  import, for example), rather than waiting for the nightly one.

## If you ever actually need one restored

**You don't need to do this yourself.** If Bolton's data is ever lost
or looks wrong and a restore might be needed:

1. Don't panic, and don't try to fix it by hand in the meantime —
   that can make a real restore harder later.
2. Go to Bolton's "Database Backups" screen (or ask whoever manages
   Bolton's technical side — Claude Code, or a developer) to see the
   list of available backups and pick the most recent good one.
3. A developer (or Claude Code) runs the actual restore against the
   live database. This is a deliberate, careful, one-off technical
   action — not a button in the app — because restoring the wrong
   backup, or restoring into a database that still has other people
   using it, can cause more damage than the original problem. It
   should only ever be done by someone who understands what they're
   about to overwrite.
4. Once restored, check a few real, familiar records (a recent quote,
   a client you know) to confirm it looks right before trusting it.

That's genuinely all you need to know. Everything below this line is
the technical detail for whoever actually performs the restore.

---

## Technical detail (for the person doing the restore)

### Which format is a given backup in?

Every backup is one of two formats, and which one is recorded right
alongside it (visible via `GET /admin/database-backup`, or in the
filename's extension):

- **`.sql.gz`** — a real `pg_dump` output, gzip-compressed. This is
  the preferred, standard format: a full, faithful Postgres dump.
- **`.json.gz`** — a plain-Python fallback, gzip-compressed JSON, used
  automatically whenever `pg_dump` isn't available in the running
  environment (a standard Render Python deployment has no guarantee of
  the `pg_dump` command-line tool being present — this fallback exists
  specifically so a backup is never skipped just because that binary
  is missing). Contains every table's rows as plain JSON. One
  deliberate omission: binary blob columns (currently only
  `documentarchive.pdf_bytes`, the stored archived-PDF bytes) are
  recorded as a size placeholder, not the actual bytes — those PDFs
  already have their own permanent copy via the Dropbox document
  archive itself, so duplicating them into every nightly DB backup too
  would bloat it for no real recovery benefit.

### Restoring a `.sql.gz` backup

```bash
gunzip -c backup_2026-08-27.sql.gz > backup.sql
psql "$DATABASE_URL" < backup.sql
```

Run this against a **fresh or intentionally-being-restored** database,
never blindly against the live one while it's still in normal use.

### Restoring a `.json.gz` backup

```python
import gzip, json, psycopg2

with gzip.open("backup_2026-08-27.json.gz") as f:
    data = json.load(f)

conn = psycopg2.connect(DATABASE_URL)
cur = conn.cursor()
for table, rows in data["tables"].items():
    if not rows or isinstance(rows, dict):   # dict means this table failed to export — see failure noted inline
        continue
    for row in rows:
        cols = list(row.keys())
        placeholders = ", ".join(["%s"] * len(cols))
        cur.execute(
            f'INSERT INTO "{table}" ({", ".join(cols)}) VALUES ({placeholders}) ON CONFLICT DO NOTHING',
            [row[c] for c in cols],
        )
conn.commit()
```

This restores DATA (every row, every real value) but not schema/
indexes — it's meant to be run against a database that already has
Bolton's tables created (e.g. a fresh deploy that's run its own
startup migrations), not an empty Postgres instance.

### Where the code lives

- `backend/database_backup.py` — how each backup is actually produced
  (`try_pg_dump()`, `python_logical_backup()`).
- `backend/main.py` — `run_database_backup_job()` (the scheduled job),
  `_prune_old_backups()` (retention), `_record_and_upload_backup()`
  (tracking + Dropbox upload), and the `/admin/database-backup*`
  endpoints (Owner-only: manual trigger, history).
- `backend/models.py` — `DatabaseBackupRecord` (the tracking table;
  does not store the actual backup bytes — those live in Dropbox only).
