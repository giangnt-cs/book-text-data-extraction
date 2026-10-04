"""Token short link, mật khẩu tự sinh, cookie phiên share."""

from __future__ import annotations

import secrets

from django.conf import settings
from django.core.signing import BadSignature, SignatureExpired, TimestampSigner
from django.utils import timezone

COOKIE_NAME = "share_auth"
COOKIE_PATH = "/s/"
SIGN_SALT = "book-share-session"
MAX_SESSION_AGE = 12 * 3600
TOKEN_BYTES = 8
_PASSWORD_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"


def new_share_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def new_share_password(length: int = 12) -> str:
    length = max(8, min(int(length or 12), 32))
    return "".join(secrets.choice(_PASSWORD_ALPHABET) for _ in range(length))


def share_public_url(token: str) -> str:
    path = f"/s/{token}/"
    base = (getattr(settings, "PUBLIC_BASE_URL", None) or "").strip().rstrip("/")
    return f"{base}{path}" if base else path


def _signer() -> TimestampSigner:
    return TimestampSigner(salt=SIGN_SALT)


def session_max_age(expires_at) -> int:
    if expires_at is None:
        return MAX_SESSION_AGE
    remaining = int((expires_at - timezone.now()).total_seconds())
    return max(0, min(MAX_SESSION_AGE, remaining))


def sign_share_session(*, share_id: str, token: str, visitor_id: str, expires_at) -> tuple[str, int]:
    max_age = session_max_age(expires_at)
    payload = f"{share_id}:{token}:{visitor_id}"
    return _signer().sign(payload), max_age


def unsign_share_session(value: str) -> tuple[str, str, str] | None:
    if not value:
        return None
    try:
        payload = _signer().unsign(value, max_age=MAX_SESSION_AGE)
    except (BadSignature, SignatureExpired, TypeError, ValueError):
        return None
    parts = str(payload).split(":")
    if len(parts) != 3:
        return None
    return parts[0], parts[1], parts[2]


def cookie_secure() -> bool:
    return bool(getattr(settings, "SESSION_COOKIE_SECURE", False))
