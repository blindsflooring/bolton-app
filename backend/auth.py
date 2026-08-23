"""
Authentication helpers (confirmed Aug 2026): real per-person login,
replacing the old self-reported "Viewing as" role dropdown.

Password hashing: PBKDF2-HMAC-SHA256 via Python's stdlib `hashlib` —
deliberately NOT bcrypt/passlib, which aren't in requirements.txt and
would add a compiled-dependency risk to the Render build for a 3-4 user
internal tool. PBKDF2 with a high iteration count is a NIST-approved,
industry-standard choice and ships with zero new dependencies.

Sessions: a random opaque token stored server-side in the UserSession
table (survives a Render restart, unlike an in-memory dict). Fixed 24h
length from login — long enough to cover a full work day/shift without
re-login, short enough that a forgotten logged-in browser doesn't stay
valid indefinitely.

Transport, changed Aug 2026 — real mobile bug found via Render logs:
this token used to travel as an HttpOnly cookie (SameSite=None;
Secure). That's the CORRECT setting for a cross-site cookie, but it
turned out to be necessary-not-sufficient: bolton-frontend.onrender.com
and bolton-backend.onrender.com are different subdomains of a shared
hosting domain that's on the Public Suffix List, so browsers treat them
as different *sites* — making this a genuine third-party cookie, which
mobile browsers (mobile Safari's ITP in particular, on by default for
years) block or refuse to persist regardless of SameSite/Secure being
set correctly. Confirmed via logs: POST /auth/login succeeded and set
the cookie, but every request after it came back 401 on mobile,
consistently, not just after cold starts — the browser was silently
declining to store/send it. Desktop worked because desktop browsers
have generally been more permissive about third-party cookies by
default. Now travels as a plain `Authorization: Bearer <token>` header
instead, set by the frontend from the login response body and kept in
localStorage — sidesteps cookie policy entirely, since it was never
actually about credentials:'include' or CORS being misconfigured (both
were already correct)."""
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta

PBKDF2_ITERATIONS = 260_000
SESSION_LENGTH = timedelta(hours=24)


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt}${dk.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        algo, iterations_str, salt, hash_hex = stored_hash.split("$")
        if algo != "pbkdf2_sha256":
            return False
        iterations = int(iterations_str)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), bytes.fromhex(salt), iterations)
        return hmac.compare_digest(dk.hex(), hash_hex)
    except (ValueError, AttributeError):
        return False


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def new_expiry() -> datetime:
    return datetime.utcnow() + SESSION_LENGTH
