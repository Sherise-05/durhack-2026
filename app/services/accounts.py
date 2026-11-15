"""Password authentication and revocable, server-side account sessions."""

from __future__ import annotations

import base64
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from functools import lru_cache
import hashlib
import hmac
import re
import secrets
from threading import Lock
import time
import os
from urllib.parse import unquote, urlsplit, urlunsplit

from app.services import db

SESSION_KEY = "account_session"
PASSWORD_MIN = 12
PASSWORD_MAX = 128
SESSION_DAYS = 14
SCRYPT_N, SCRYPT_R, SCRYPT_P = 32768, 8, 3
_attempts = defaultdict(list)
_attempt_lock = Lock()


def normalise_email(email: str) -> str:
    value = email.strip().casefold()
    if len(value) > 254 or not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
        raise ValueError("Enter a valid email address.")
    return value


def hash_password(password: str) -> str:
    if not PASSWORD_MIN <= len(password) <= PASSWORD_MAX:
        raise ValueError(f"Use a password between {PASSWORD_MIN} and {PASSWORD_MAX} characters.")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P,
                            maxmem=64 * 1024 * 1024, dklen=32)
    return "$".join(("scrypt", str(SCRYPT_N), str(SCRYPT_R), str(SCRYPT_P),
                     base64.b64encode(salt).decode("ascii"), base64.b64encode(digest).decode("ascii")))


def verify_password(password: str, encoded: str) -> bool:
    if not isinstance(password, str) or len(password) > PASSWORD_MAX:
        return False
    try:
        kind, n, r, p, salt, expected = encoded.split("$")
        if kind != "scrypt" or (int(n), int(r), int(p)) != (SCRYPT_N, SCRYPT_R, SCRYPT_P):
            return False
        salt_bytes, expected_bytes = base64.b64decode(salt, validate=True), base64.b64decode(expected, validate=True)
        if len(salt_bytes) != 16 or len(expected_bytes) != 32:
            return False
        digest = hashlib.scrypt(password.encode("utf-8"), salt=salt_bytes, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P,
                                maxmem=64 * 1024 * 1024, dklen=32)
        return hmac.compare_digest(digest, expected_bytes)
    except (ValueError, TypeError):
        return False


@lru_cache(maxsize=1)
def _dummy_password() -> str:
    return hash_password("Not a real account password 123!")


def register(email: str, password: str, name: str) -> dict:
    email = normalise_email(email)
    name = name.strip()
    if not 1 <= len(name) <= 80:
        raise ValueError("Enter your name using 1–80 characters.")
    return db.create_account(email, hash_password(password), name)


def authenticate(email: str, password: str) -> dict | None:
    try:
        email = normalise_email(email)
    except ValueError:
        verify_password(password, _dummy_password())
        return None
    account = db.find_account_by_email(email)
    valid = verify_password(password, account["password_hash"] if account else _dummy_password())
    return db.get_account(account["id"]) if account and valid else None


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def resolve_session(token) -> dict | None:
    if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", token):
        return None
    return db.resolve_account_session(token_hash(token))


def start_session(request, account: dict):
    previous = request.session.get(SESSION_KEY)
    if isinstance(previous, str):
        db.delete_account_session(token_hash(previous))
    pending = request.session.get("oauth_pending_ref")
    if pending and getattr(request.state, "account", None):
        db.pop_oauth_pending(pending)
    request.session.clear()
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)
    db.create_account_session(account["id"], token_hash(token), expires.isoformat())
    request.session[SESSION_KEY] = token


def end_session(request):
    token = request.session.get(SESSION_KEY)
    if isinstance(token, str):
        db.delete_account_session(token_hash(token))
    pending = request.session.get("oauth_pending_ref")
    if pending and getattr(request.state, "account", None):
        db.pop_oauth_pending(pending)
    request.session.clear()


def destination(account: dict) -> str:
    return "/dashboard" if account["onboarding_complete"] else "/onboarding"


def safe_next(value: str | None) -> str:
    if not value or not value.startswith("/") or value.startswith("//") or "\\" in value or any(ord(c) < 32 for c in value):
        return "/dashboard"
    decoded = unquote(unquote(value))
    if decoded.startswith("//") or "\\" in decoded or any(ord(c) < 32 for c in decoded):
        return "/dashboard"
    # Authentication/setup pages must not become a post-login redirect loop.
    if value.split("?", 1)[0] in ("/", "/login", "/signup", "/logout"):
        return "/dashboard"
    return value


def canonical_entry_url(request) -> str | None:
    """Choose a loopback callback host before creating host-bound login cookies."""
    aliases = {"localhost", "127.0.0.1", "::1"}
    try:
        callback = urlsplit(os.getenv("GOOGLE_REDIRECT_URI", ""))
        current = urlsplit(str(request.url))
        if (callback.hostname not in aliases or current.hostname not in aliases
            or callback.hostname == current.hostname or callback.scheme != current.scheme
            or callback.path != "/auth/callback" or callback.username or callback.password
            or callback.query or callback.fragment):
            return None
        default = 443 if current.scheme == "https" else 80
        if (callback.port if callback.port is not None else default) != (current.port if current.port is not None else default):
            return None
        return urlunsplit((callback.scheme, callback.netloc, current.path, current.query, ""))
    except ValueError:
        return None


def login_allowed(ip: str, email: str) -> bool:
    key = token_hash(ip + ":" + email.strip().casefold())
    now = time.monotonic()
    with _attempt_lock:
        _attempts[key] = [stamp for stamp in _attempts[key] if now - stamp < 900]
        return len(_attempts[key]) < 10


def record_login(ip: str, email: str, *, successful: bool):
    key = token_hash(ip + ":" + email.strip().casefold())
    with _attempt_lock:
        if successful:
            _attempts.pop(key, None)
        else:
            _attempts[key].append(time.monotonic())
