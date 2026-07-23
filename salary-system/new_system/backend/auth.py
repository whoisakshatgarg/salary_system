"""Password hashing with the standard library only (no bcrypt dependency).

The old system stored and compared admin passwords in plaintext. Here we use
PBKDF2-HMAC-SHA256, which ships with Python, so there is still nothing to
install. Hash format: ``pbkdf2_sha256$<iterations>$<salt_hex>$<hash_hex>``.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time

from . import paths

_ALGO = "pbkdf2_sha256"
_ITERATIONS = 240_000

_SECRET_PATH = paths.secret_path()


def hash_password(password: str, *, iterations: int = _ITERATIONS) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"{_ALGO}${iterations}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, iter_s, salt_hex, hash_hex = stored.split("$")
        if algo != _ALGO:
            return False
        dk = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iter_s)
        )
        return hmac.compare_digest(dk.hex(), hash_hex)
    except (ValueError, AttributeError):
        return False


# --------------------------------------------------------------------------- #
# Stateless signed session tokens (HMAC, no external session store)
# --------------------------------------------------------------------------- #
def _secret() -> bytes:
    """A per-install random secret, persisted so tokens survive a restart."""
    _SECRET_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not _SECRET_PATH.exists():
        _SECRET_PATH.write_text(os.urandom(32).hex(), encoding="utf-8")
    return bytes.fromhex(_SECRET_PATH.read_text(encoding="utf-8").strip())


def _sign(payload: str) -> str:
    return hmac.new(_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()


def create_token(username: str, role: str, *, ttl_seconds: int = 7 * 86400) -> str:
    expiry = int(time.time()) + ttl_seconds
    payload = f"{username}|{role}|{expiry}"
    return f"{payload}|{_sign(payload)}"


def read_token(token: str | None) -> dict | None:
    """Return {'username','role'} if the token is valid and unexpired, else None."""
    if not token:
        return None
    try:
        username, role, expiry_s, sig = token.split("|")
    except ValueError:
        return None
    payload = f"{username}|{role}|{expiry_s}"
    if not hmac.compare_digest(sig, _sign(payload)):
        return None
    if int(expiry_s) < int(time.time()):
        return None
    return {"username": username, "role": role}
