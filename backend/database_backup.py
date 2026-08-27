"""Database Backups brief (Dropbox Document Archive & Backup Layer §5,
confirmed Aug 2026 — user directed to proceed without further sign-off).

Separate, disaster-recovery layer from the document archive
(pdf_render.py/dropbox_archive.py's job is "preserve this one quote/
invoice/order exactly as it looked" — this module's job is "restore
Bolton's actual data if the live Supabase Postgres database itself is
ever lost or corrupted"). Two independent backup strategies, tried in
order, so this never hard-depends on either one being available:

1. pg_dump (preferred) — a real, standard, fully-restorable Postgres
   dump via `psql`/`pg_restore` later, produced by shelling out to the
   pg_dump CLI against DATABASE_URL. NOT guaranteed to exist on a
   standard Render Python buildpack (confirmed earlier this session:
   WeasyPrint needed native system libraries this exact kind of
   buildpack doesn't have — pg_dump is the same category of risk, a
   system binary, not a Python package). Tried first; any failure
   (binary missing, connection issue) is caught here and reported back
   as None, never raised — the caller falls back automatically.
2. Pure-Python logical backup (fallback, always works) — connects via
   the app's own SQLAlchemy engine, discovers every live table the
   exact same way _enable_row_level_security() already does
   (inspect(engine).get_table_names() — not a hardcoded model list, so
   this covers every real table including the one un-mapped table
   already confirmed to exist from that brief), and serializes every
   row of every table to JSON. Not a byte-for-byte pg_dump equivalent
   (no schema DDL, no indexes) but fully sufficient to restore actual
   DATA, which is the brief's own explicit requirement ("capable of
   restoring Bolton's actual data — not merely the documents").

Whichever method actually produced a given backup is recorded
alongside it (DatabaseBackupRecord.method, main.py) — never silently
ambiguous which kind of file is sitting in Dropbox."""
import gzip
import json
import subprocess
from datetime import datetime


def try_pg_dump(database_url: str) -> bytes:
    """Returns gzipped SQL dump bytes on success, or None on ANY
    failure (missing pg_dump binary, connection error, timeout) —
    never raises, so run_database_backup_job() (main.py) can fall back
    to the pure-Python method cleanly. 120s timeout: a hung pg_dump
    must not hang the nightly scheduler thread forever."""
    if not database_url or database_url.startswith("sqlite"):
        return None   # local dev / no real Postgres — nothing to pg_dump
    try:
        result = subprocess.run(
            ["pg_dump", database_url, "--no-owner", "--no-privileges"],
            capture_output=True, timeout=120,
        )
        if result.returncode != 0 or not result.stdout:
            return None
        return gzip.compress(result.stdout)
    except Exception:
        # FileNotFoundError (no pg_dump binary), TimeoutExpired, or
        # anything else — all treated identically: this method isn't
        # available right now, fall back.
        return None


def _json_default(value):
    """bytes gets a placeholder, not its full content — the one large
    blob column in this schema (DocumentArchive.pdf_bytes) is already
    separately preserved by the document archive feature itself
    (Dropbox, one file per version); duplicating every archived PDF's
    raw bytes into every nightly DB backup too would make this backup
    balloon in size and time for no real recovery benefit. Everything
    else non-JSON-native (datetime/date/Decimal/UUID) gets str() —
    this is a data export for restoring VALUES, not a typed schema, so
    a readable string form is the right target."""
    if isinstance(value, (bytes, bytearray, memoryview)):
        return f"<binary, {len(value)} bytes, omitted — see the Document Archive/Dropbox instead>"
    return str(value)


def python_logical_backup(engine) -> bytes:
    """Always works — pure Python/SQLAlchemy, zero native dependencies,
    same reasoning that made xhtml2pdf the right call over WeasyPrint
    earlier this session. One JSON object: {"generated_at": ...,
    "method": "python_json", "tables": {table_name: [row dicts]}}."""
    from sqlalchemy import inspect, text
    inspector = inspect(engine)
    tables = sorted(inspector.get_table_names())
    data = {"generated_at": datetime.utcnow().isoformat(), "method": "python_json", "tables": {}}
    with engine.connect() as conn:
        for table in tables:
            try:
                rows = conn.execute(text(f'SELECT * FROM "{table}"')).mappings().all()
                data["tables"][table] = [dict(r) for r in rows]
            except Exception as e:
                data["tables"][table] = {"__error__": str(e)}
    raw = json.dumps(data, default=_json_default).encode("utf-8")
    return gzip.compress(raw)


def summarize_for_preview(file_bytes: bytes, method: str) -> dict:
    """Verification-only, confirmed Aug 2026 — "confirming it contains
    real recognizable data, not just a success message." Called once,
    right after a backup is generated, from the real in-memory bytes —
    DatabaseBackupRecord deliberately never stores the bytes themselves
    (see its own docstring), so this is the one place a real look at
    what a given run actually captured is possible at all. Never
    persisted — this dict only ever flows back in an API response."""
    try:
        if method == "python_json":
            data = json.loads(gzip.decompress(file_bytes))
            table_row_counts = {
                t: (len(rows) if isinstance(rows, list) else f"export error: {rows.get('__error__')}")
                for t, rows in data["tables"].items()
            }
            # One genuinely recognizable, real value — not just a count —
            # so this can't be mistaken for a plausible-looking fake.
            sample = None
            quotes = data["tables"].get("quote")
            if isinstance(quotes, list) and quotes:
                sample = {"table": "quote", "client_name": quotes[0].get("client_name"), "id": quotes[0].get("id")}
            return {"method": "python_json", "table_row_counts": table_row_counts, "sample_real_row": sample}
        else:
            text = gzip.decompress(file_bytes).decode("utf-8", errors="replace")
            lines = [l for l in text.splitlines() if l.strip() and not l.startswith("--")]
            return {"method": "pg_dump", "line_count": len(text.splitlines()), "sample_lines": lines[:15]}
    except Exception as e:
        return {"method": method, "preview_error": str(e)}
