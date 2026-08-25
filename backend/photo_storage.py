"""
Quote Photo Attachments (confirmed Aug 2026) — thin wrapper around the
Supabase Storage REST API. Uses stdlib urllib rather than adding a new
HTTP client dependency (requests/httpx aren't in requirements.txt today)
— same choice ai_import.py already made for its own Claude API calls.

Chosen over this app's other existing file-storage mechanism
(backend/uploads/ local disk, used by HR Documents) after checking
directly rather than assuming: quote photos need to reliably survive
routine deploys over the life of a quote (which can span weeks), and
Supabase Storage — the same project already used for Postgres — gives
that without standing up a new vendor.

One-time setup required in the Supabase + Render dashboards before this
works (nothing here can do it — no dashboard access from the backend):
  1. In the Supabase dashboard: Storage -> New bucket -> name it
     exactly "quote-photos", NOT public (private).
  2. In the Supabase dashboard: Project Settings -> API -> copy the
     service_role key (NOT the anon/public key — this needs write
     access and bypasses RLS, same trust level as this app's own
     DATABASE_URL connection, so treat it with the same care).
  3. On Render, add two env vars to the bolton-backend service:
       SUPABASE_URL           e.g. https://xxxx.supabase.co
       SUPABASE_SERVICE_KEY   the service_role key from step 2

Until those are set, storage_configured() returns False and every
upload/download call raises a clear RuntimeError rather than failing
silently or writing somewhere unexpected.

Private bucket by design: every read in this app is proxied back
through an authenticated Bolton endpoint (see /quotes/{id}/photos/...
in main.py) rather than exposing a public Supabase URL anyone could
hit forever — one fewer way a client's site photos could leak, same
"hand-pick what leaves the server" reasoning the Builder Portal section
already uses for pricing data.
"""
import os
import urllib.request
import urllib.error

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")
BUCKET = "quote-photos"


def storage_configured() -> bool:
    return bool(SUPABASE_URL and SUPABASE_SERVICE_KEY)


def _headers(content_type: str = None) -> dict:
    h = {
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "apikey": SUPABASE_SERVICE_KEY,
    }
    if content_type:
        h["Content-Type"] = content_type
    return h


def upload_photo(path: str, data: bytes, content_type: str) -> None:
    if not storage_configured():
        raise RuntimeError(
            "Photo storage isn't set up yet — SUPABASE_URL/SUPABASE_SERVICE_KEY "
            "are missing on the backend. See photo_storage.py's header comment "
            "for the one-time setup steps."
        )
    url = f"{SUPABASE_URL}/storage/v1/object/{BUCKET}/{path}"
    req = urllib.request.Request(url, data=data, headers=_headers(content_type), method="POST")
    try:
        urllib.request.urlopen(req, timeout=30)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Could not upload photo to storage ({e.code}): {body}")


def download_photo(path: str) -> bytes:
    if not storage_configured():
        raise RuntimeError("Photo storage isn't set up yet.")
    url = f"{SUPABASE_URL}/storage/v1/object/{BUCKET}/{path}"
    req = urllib.request.Request(url, headers=_headers(), method="GET")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read()
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Could not fetch photo from storage ({e.code})")


def delete_photo(path: str) -> None:
    # Best-effort: the QuotePhoto DB row is the source of truth for
    # "does this photo exist" from the app's point of view, not
    # storage — if storage isn't configured, or the delete call itself
    # fails, the DB row is still removed by the caller either way, so
    # there's nothing useful to raise here.
    if not storage_configured():
        return
    url = f"{SUPABASE_URL}/storage/v1/object/{BUCKET}/{path}"
    req = urllib.request.Request(url, headers=_headers(), method="DELETE")
    try:
        urllib.request.urlopen(req, timeout=30)
    except urllib.error.HTTPError:
        pass
