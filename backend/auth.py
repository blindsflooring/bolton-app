"""
Authentication helpers (confirmed Aug 2026): real per-person login,
replacing the old self-reported "Viewing as" role dropdown.

Password hashing: PBKDF2-HMAC-SHA256 via Python's stdlib `hashlib` —
deliberately NOT bcrypt/passlib, which aren't in requirements.txt and
would add a compiled-dependency risk to the Render build for a 3-4 user
internal tool. PBKDF2 with a high iteration count is a NIST-approved,
industry-standard choice and ships with zero new dependencies.

Sessions: a random opaque token stored server-side in the UserSession
table (survives a Render restart, unlike an in-memory dict), set as an
HttpOnly cookie. Fixed 24h length from login — long enough to cover a
full work day/shift without re-login, short enough that a forgotten
logged-in browser doesn't stay valid indefinitely.
"""
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta

PBKDF2_ITERATIONS = 260_000
SESSION_COOKIE_NAME = "bolton_session"
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
