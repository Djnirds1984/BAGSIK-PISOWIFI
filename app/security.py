"""Password hashing, signed cookies and CSRF tokens.

No external crypto dependency: hashlib for PBKDF2, hmac for signatures.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time

_PBKDF2_ROUNDS = 180_000
_ALGO = "pbkdf2_sha256"


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), _PBKDF2_ROUNDS)
    return f"{_ALGO}${_PBKDF2_ROUNDS}${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, rounds, salt, digest = stored.split("$")
        if algo != _ALGO:
            return False
        candidate = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt.encode("utf-8"), int(rounds)
        ).hex()
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(candidate, digest)


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def sign(payload: dict, secret: str, ttl_seconds: int) -> str:
    body = dict(payload)
    body["exp"] = int(time.time()) + ttl_seconds
    raw = json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8")
    encoded = _b64encode(raw)
    signature = hmac.new(secret.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256).digest()
    return f"{encoded}.{_b64encode(signature)}"


def unsign(token: str | None, secret: str) -> dict | None:
    if not token or "." not in token:
        return None
    encoded, signature = token.split(".", 1)
    expected = hmac.new(secret.encode("utf-8"), encoded.encode("ascii"), hashlib.sha256).digest()
    try:
        provided = _b64decode(signature)
    except Exception:
        return None
    if not hmac.compare_digest(provided, expected):
        return None
    try:
        payload = json.loads(_b64decode(encoded))
    except Exception:
        return None
    if not isinstance(payload, dict) or int(payload.get("exp", 0)) < time.time():
        return None
    return payload


def new_csrf_token() -> str:
    return secrets.token_urlsafe(24)


def constant_time_equal(left: str, right: str) -> bool:
    return hmac.compare_digest(left or "", right or "")
