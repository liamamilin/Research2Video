"""JWT encode/decode helpers for access & refresh tokens."""

from __future__ import annotations

import hashlib
import secrets
import time
from typing import Literal, Optional

import jwt

from web.settings import get_settings

_ALG = "HS256"


def _now() -> int:
    return int(time.time())


def create_token(
    user_id: int,
    username: str,
    role: str,
    kind: Literal["access", "refresh"],
    ttl_seconds: int | None = None,
    csrf_token: str | None = None,
) -> tuple[str, str, int]:
    """Return (token, jti, expires_at).

    ``ttl_seconds`` overrides the configured lifetime, which "remember me"
    needs: a cookie that outlives its own token only produces a 401 at the next
    refresh, so the two have to be chosen together.
    """
    settings = get_settings()
    if ttl_seconds is not None:
        ttl = max(1, int(ttl_seconds))
    elif kind == "access":
        ttl = settings.auth.access_token_minutes * 60
    else:
        ttl = settings.auth.refresh_token_days * 86400

    jti = secrets.token_urlsafe(16)
    exp = _now() + ttl
    payload = {
        "sub": str(user_id),
        "username": username,
        "role": role,
        "kind": kind,
        "jti": jti,
        "iat": _now(),
        "exp": exp,
    }
    if csrf_token is not None and kind == "access":
        # A late refresh response must not switch a newly signed-in browser
        # back to the previous account. Only this session's CSRF cookie matches.
        payload["csrf"] = hashlib.sha256(csrf_token.encode()).hexdigest()
    token = jwt.encode(payload, settings.auth.secret_key, algorithm=_ALG)
    return token, jti, exp


def decode_token(token: str) -> Optional[dict]:
    """Decode and validate a JWT. Returns None on any failure."""
    return decode_token_diagnostic(token)[0]


def decode_token_diagnostic(token: str) -> tuple[Optional[dict], str]:
    """Return a safe failure category, never token contents or exception text."""
    settings = get_settings()
    try:
        return jwt.decode(token, settings.auth.secret_key, algorithms=[_ALG]), ""
    except jwt.ExpiredSignatureError:
        return None, "expired"
    except jwt.InvalidSignatureError:
        return None, "signature_mismatch"
    except jwt.PyJWTError:
        return None, "invalid_token"


def matches_browser_session(payload: dict, csrf_token: str | None) -> bool:
    binding = payload.get("csrf")
    # Existing sessions remain usable; their next refresh adds the binding.
    if binding is None:
        return True
    actual = hashlib.sha256((csrf_token or "").encode()).hexdigest()
    return isinstance(binding, str) and secrets.compare_digest(binding, actual)


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)
