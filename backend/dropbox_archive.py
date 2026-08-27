"""Dropbox Document Archive & Backup Layer brief (confirmed Aug 2026).

Server-side only (brief §6) — this module is the ONLY place that ever
touches a Dropbox access token; it is never returned in an API
response, never sent to the frontend, never logged. Read from the
environment (DROPBOX_ACCESS_TOKEN), same pattern as every other secret
this app uses (AUTH_SECRET_KEY, SEED_PASSWORD_*) — never stored in the
database, never committed to source (this repo is public).

No token configured yet (confirmed with Burgert — Dropbox itself is
on hold until a real token exists) is deliberately treated as EXACTLY
the same case as Dropbox being temporarily unreachable (brief §7:
"Dropbox being unavailable must NOT prevent Bolton from creating or
saving a quote, invoice, or order") — no special-casing, one code
path, one failure mode, retriable the same way either way. This means
the archive-status/retry machinery this brief also asks for is fully
exercised and genuinely working right now, today, with zero Dropbox
access — it just stays in "Pending" until a real token is set, at
which point the exact same retry action starts succeeding with no
code change needed."""
import os


def upload_document(file_bytes: bytes, dropbox_path: str) -> dict:
    """Returns {"ok": True, "path": ..., "file_id": ...} on a genuine,
    confirmed upload, or {"ok": False, "reason": ...} on absolutely any
    failure — including no token configured — never raises. The
    caller (main.py) is responsible for turning this into the correct
    DocumentArchive status; this function's only job is "did a real
    file land in Dropbox, and if not, why not."

    file_bytes (renamed from pdf_bytes, v2 pass, confirmed Aug 2026):
    genuinely generic now — every archived PDF (Quote/Invoice/Order
    Sheet) AND the nightly Order Index CSV snapshot both flow through
    this exact same function; it has never actually cared about the
    byte content's format, only that it's bytes headed to a path.

    mode=WriteMode("add") (not "overwrite") — brief §4's own hard
    requirement: a version already archived must never be silently
    replaced. If dropbox_path somehow already exists, Dropbox itself
    rejects the add, which surfaces here as a normal failure — the
    caller is expected to pass an already-uniquely-versioned path
    (see _next_archive_version(), main.py), so this should only ever
    trigger on a genuine, worth-investigating conflict."""
    token = os.environ.get("DROPBOX_ACCESS_TOKEN")
    if not token:
        # not_configured=True (distinct from a genuine upload error) —
        # main.py maps this to status="pending" rather than "failed":
        # this is an expected, known, temporary state Burgert is
        # already aware of, not an alarming error to surface as one.
        return {"ok": False, "not_configured": True, "reason": "Dropbox not connected yet (no access token configured) — will retry automatically once it is."}
    try:
        import dropbox
        dbx = dropbox.Dropbox(token)
        result = dbx.files_upload(file_bytes, dropbox_path, mode=dropbox.files.WriteMode("add"))
        return {"ok": True, "path": result.path_display, "file_id": result.id}
    except Exception as e:
        return {"ok": False, "not_configured": False, "reason": str(e)}


def delete_document(dropbox_path: str) -> dict:
    """Database Backups brief (Dropbox Document Archive & Backup Layer
    §5) — the ONE deliberate exception to this whole module's "never
    overwrite, never delete" document-archive philosophy: retention
    pruning (keep last 7 daily / last 4 weekly backups, main.py) needs
    to actually remove old backup files, unlike every archived Quote/
    Invoice/Order Sheet PDF, which is permanent history by design and
    has no delete path anywhere. Same never-raise contract as
    upload_document() — a failed prune must never crash the backup job
    that's still trying to do its real work (create today's backup);
    the caller logs the failure and tries again next run rather than
    blocking on it."""
    token = os.environ.get("DROPBOX_ACCESS_TOKEN")
    if not token:
        return {"ok": False, "not_configured": True, "reason": "Dropbox not connected yet (no access token configured)."}
    try:
        import dropbox
        dbx = dropbox.Dropbox(token)
        dbx.files_delete_v2(dropbox_path)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "not_configured": False, "reason": str(e)}
