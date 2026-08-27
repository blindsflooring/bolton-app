"""Dropbox Document Archive & Backup Layer brief (confirmed Aug 2026).

Server-side only (brief §6) — this module is the ONLY place that ever
touches a Dropbox credential; none of it is ever returned in an API
response, sent to the frontend, or logged. Read from the environment,
same pattern as every other secret this app uses (AUTH_SECRET_KEY,
SEED_PASSWORD_*) — never stored in the database, never committed to
source (this repo is public).

Two credential modes, tried in this order (confirmed Aug 2026 — long-
lived setup, after first proving the mechanism with a short-lived
token):

1. Refresh token (preferred, durable) — DROPBOX_REFRESH_TOKEN +
   DROPBOX_APP_KEY + DROPBOX_APP_SECRET. A refresh token itself never
   expires (unless revoked in the Dropbox App Console); the `dropbox`
   SDK uses it to silently mint a fresh short-lived access token on
   every request as needed, with zero manual renewal ever again. This
   is what "long-lived" actually means for Dropbox's API — there is no
   such thing as a permanent ACCESS token, only a refresh token that
   can keep producing fresh ones indefinitely.
2. Static access token (fallback, short-lived) — DROPBOX_ACCESS_TOKEN
   alone, exactly as this app's first Dropbox connection worked. A
   token generated via the App Console's "Generate access token"
   button expires in ~4 hours; kept working here for anyone who's only
   set that one variable, or as an emergency override.

No credential configured at all is deliberately treated as EXACTLY the
same case as Dropbox being temporarily unreachable (brief §7: "Dropbox
being unavailable must NOT prevent Bolton from creating or saving a
quote, invoice, or order") — no special-casing, one code path, one
failure mode, retriable the same way either way."""
import os


def _get_client():
    """Returns a real dropbox.Dropbox client, or None if no credential
    is configured at all. Raises nothing — an actually-invalid/expired
    credential still surfaces as a normal exception from the caller's
    files_upload()/files_delete_v2() call, handled there exactly like
    any other Dropbox API failure."""
    import dropbox
    refresh_token = os.environ.get("DROPBOX_REFRESH_TOKEN")
    app_key = os.environ.get("DROPBOX_APP_KEY")
    app_secret = os.environ.get("DROPBOX_APP_SECRET")
    if refresh_token and app_key and app_secret:
        return dropbox.Dropbox(oauth2_refresh_token=refresh_token, app_key=app_key, app_secret=app_secret)
    access_token = os.environ.get("DROPBOX_ACCESS_TOKEN")
    if access_token:
        return dropbox.Dropbox(access_token)
    return None


def upload_document(file_bytes: bytes, dropbox_path: str) -> dict:
    """Returns {"ok": True, "path": ..., "file_id": ...} on a genuine,
    confirmed upload, or {"ok": False, "reason": ...} on absolutely any
    failure — including no credential configured — never raises. The
    caller (main.py) is responsible for turning this into the correct
    DocumentArchive status; this function's only job is "did a real
    file land in Dropbox, and if not, why not."

    file_bytes: genuinely generic — every archived PDF (Quote/Invoice/
    Order Sheet), the nightly Order Index CSV snapshot, and the
    database backups all flow through this exact same function; it has
    never actually cared about the byte content's format, only that
    it's bytes headed to a path.

    mode=WriteMode("add") (not "overwrite") — brief §4's own hard
    requirement: a version already archived must never be silently
    replaced. If dropbox_path somehow already exists, Dropbox itself
    rejects the add, which surfaces here as a normal failure — the
    caller is expected to pass an already-uniquely-versioned path
    (see _next_archive_version(), main.py), so this should only ever
    trigger on a genuine, worth-investigating conflict."""
    try:
        import dropbox
        dbx = _get_client()
        if dbx is None:
            # not_configured=True (distinct from a genuine upload error) —
            # main.py maps this to status="pending" rather than "failed":
            # this is an expected, known, temporary state, not an
            # alarming error to surface as one.
            return {"ok": False, "not_configured": True, "reason": "Dropbox not connected yet (no credential configured) — will retry automatically once it is."}
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
    try:
        dbx = _get_client()
        if dbx is None:
            return {"ok": False, "not_configured": True, "reason": "Dropbox not connected yet (no credential configured)."}
        dbx.files_delete_v2(dropbox_path)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "not_configured": False, "reason": str(e)}
