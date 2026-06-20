from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import time
from http.cookies import SimpleCookie
from typing import Any


SESSION_COOKIE = "aw_session"
SESSION_TTL_SECONDS = 8 * 60 * 60
ROLE_ORDER = {
    "viewer": 0,
    "assistant": 1,
    "planner": 2,
    "company_admin": 3,
    "system_admin": 4,
}


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 240_000)
    return "pbkdf2_sha256$240000$%s$%s" % (
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(digest).decode("ascii"),
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt_b64, digest_b64 = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(digest_b64)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(iterations))
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def new_session_id() -> str:
    return secrets.token_urlsafe(32)


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def session_expires_at() -> int:
    return int(time.time()) + SESSION_TTL_SECONDS


def parse_cookies(header: str | None) -> dict[str, str]:
    cookie = SimpleCookie()
    if header:
        cookie.load(header)
    return {key: morsel.value for key, morsel in cookie.items()}


def can(role: str | None, required: str) -> bool:
    if not role:
        return False
    return ROLE_ORDER.get(role, -1) >= ROLE_ORDER[required]


def is_system_admin(role: str | None) -> bool:
    return role == "system_admin"


def is_company_admin(role: str | None) -> bool:
    return role in {"company_admin", "system_admin"}


def mask_ssn_last4(value: Any, *, reveal: bool = False) -> str:
    text = str(value or "").strip()
    if not text:
        return "***-"
    if reveal:
        return f"***-{text[-4:]}"
    return "***-****"


def default_admin_credentials() -> tuple[str, str, str]:
    return (
        os.environ.get("AW_DEFAULT_ADMIN_EMAIL", "system.admin@awportal.local"),
        os.environ.get("AW_DEFAULT_ADMIN_NAME", "System Admin"),
        os.environ.get("AW_DEFAULT_ADMIN_PASSWORD", "ChangeMe123!"),
    )
