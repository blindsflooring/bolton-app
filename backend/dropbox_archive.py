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


def credentials_configured() -> bool:
    """Whether a Dropbox credential exists at all - the same check
    _get_client() makes, without building a client or touching the
    network.

    Exists because the consistency monitor was ASSERTING this rather
    than asking. It printed "no Dropbox credential configured" on the
    strength of a pending count alone, which sent four days of hunting
    through Render environment variables while the credentials were
    fine and uploads were landing every night. A pending row means "not
    uploaded yet"; it has never meant "no credential".

    Note what this does NOT claim: a credential that exists can still be
    expired or revoked. That only shows up when an upload is actually
    attempted, and it surfaces as a failed row with its own reason.
    """
    if os.environ.get("DROPBOX_REFRESH_TOKEN") and os.environ.get("DROPBOX_APP_KEY") \
            and os.environ.get("DROPBOX_APP_SECRET"):
        return True
    return bool(os.environ.get("DROPBOX_ACCESS_TOKEN"))


def describe_failure(exc: Exception, action: str, dropbox_path: str = "",
                     size_bytes: int = None, seconds: float = None) -> str:
    """A failure reason that is never empty and names something you can act on.

    WHY THIS EXISTS, stated plainly because it replaces something that
    actively misled. Both calls below used to record `str(e)`, and for
    Dropbox's own API errors that is fine — ApiError.__str__ returns
    repr(self), which always carries the request id and the error union.
    But the exceptions a flaky upload actually raises are network ones,
    and every single one of those stringifies to the EMPTY STRING:

        requests.exceptions.ConnectionError   str(e) == ''
        requests.exceptions.ReadTimeout       str(e) == ''
        ConnectionResetError                  str(e) == ''
        OSError                               str(e) == ''

    So the reason was saved as "", the screen printed
    "the Dropbox upload failed: unknown error" (shared.js's own `||`
    fallback), and the document-history panel showed no reason at all
    because it only renders one when truthy. The information needed to
    tell a network blip from an expired token was discarded at the point
    it was caught — one more system reporting a status about something
    it never actually said.

    The exception TYPE is the diagnosis here, not the message: an empty
    ConnectionError IS the answer ("could not reach Dropbox"), it was
    just never written down. So the type name always leads, the message
    follows when there is one, and the context (which path, how big, how
    long before it gave up) is what separates "network dropped" from
    "this particular file is the problem".
    """
    name = "%s.%s" % (type(exc).__module__, type(exc).__name__)
    if name.startswith("builtins."):
        name = name[len("builtins."):]
    message = str(exc).strip()
    if not message:
        # repr() is the last resort before admitting there is nothing:
        # some exceptions carry their detail in args rather than __str__.
        detail = repr(exc)
        message = ("no message — the exception carried none, which for a "
                   "network error is normal and is itself the finding"
                   if detail in ("%s()" % type(exc).__name__, "") else detail)
    parts = ["%s while %s: %s" % (name, action, message)]
    if dropbox_path:
        parts.append("path %s" % dropbox_path)
    if size_bytes is not None:
        parts.append("%.1f KB" % (size_bytes / 1024.0))
    if seconds is not None:
        parts.append("gave up after %.1fs" % seconds)
    return " | ".join(parts)


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
    import time
    started = time.monotonic()
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
        return {"ok": False, "not_configured": False,
                "reason": describe_failure(e, "uploading to Dropbox", dropbox_path,
                                           size_bytes=len(file_bytes or b""),
                                           seconds=time.monotonic() - started)}


def delete_document(dropbox_path: str) -> dict:
    """Two deliberate exceptions to this module's usual "never
    overwrite, never delete" document-archive philosophy call this:
    (1) Database Backups brief (§5) retention pruning — keep last 7
    daily / last 4 weekly backups, main.py — since old backups aren't
    permanent history the way an archived Quote/Invoice/Order Sheet is;
    (2) Robust Owner Delete brief (confirmed Aug 2026) — an EXPLICIT,
    off-by-default Owner choice at quote-delete time to also purge that
    quote's archived Dropbox copies, for deliberate mockup/test cleanup.
    Every other archive call site still never deletes. Same never-raise
    contract as upload_document() — a failed delete must never crash
    the caller's own larger operation (a backup-prune cycle, or a
    quote delete that should still succeed even if Dropbox is
    unreachable); the caller checks result["ok"] and decides what to
    tell the user."""
    try:
        dbx = _get_client()
        if dbx is None:
            return {"ok": False, "not_configured": True, "reason": "Dropbox not connected yet (no credential configured)."}
        dbx.files_delete_v2(dropbox_path)
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "not_configured": False,
                "reason": describe_failure(e, "deleting from Dropbox", dropbox_path)}
