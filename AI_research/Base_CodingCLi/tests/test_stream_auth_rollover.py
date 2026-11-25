"""Verify live assistant SSE streams survive natural access-token exp rollover without relaxing security."""
import time
import jwt
import pytest
from fastapi import HTTPException
from starlette.requests import Request

from web import deps
from web.auth import db as user_db
from web.auth.routes import ACCESS_COOKIE, CSRF_COOKIE
from web.settings import get_settings


def _make_request(*, token: str, csrf: str = "csrf-1", stream_user_id: int | None = None) -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/jobs",
        "headers": [(b"cookie", f"{ACCESS_COOKIE}={token}; {CSRF_COOKIE}={csrf}".encode())],
    }
    if stream_user_id is not None:
        deps.delegate_stream(scope, stream_user_id)
    return Request(scope)


def _expired_access_token(user: dict, *, csrf: str = "csrf-1", jti: str = "jti-exp-1", secret: str | None = None) -> str:
    import hashlib
    settings = get_settings()
    payload = {
        "sub": str(user["id"]),
        "username": user["username"],
        "role": user["role"],
        "kind": "access",
        "jti": jti,
        "iat": int(time.time()) - 3600,
        "exp": int(time.time()) - 60,
        "csrf": hashlib.sha256(csrf.encode()).hexdigest(),
    }
    return jwt.encode(payload, secret or settings.auth.secret_key, algorithm="HS256")


def test_stream_delegation_allows_natural_exp_rollover_only(client):
    user = user_db.get_user_by_username("admin")
    assert user is not None
    expired = _expired_access_token(user, jti="jti-exp-ok")

    # Normal HTTP request with expired token MUST still be rejected with 401.
    with pytest.raises(HTTPException) as exc:
        deps.current_user(_make_request(token=expired))
    assert exc.value.status_code == 401

    # Active delegated stream for the same user survives natural exp rollover.
    resolved = deps.current_user(_make_request(token=expired, stream_user_id=int(user["id"])))
    assert resolved["id"] == user["id"]

    # Revoked session MUST still be rejected even inside a delegated stream.
    user_db.store_session("jti-exp-revoked", int(user["id"]), "access", int(time.time()) + 60)
    user_db.revoke_session("jti-exp-revoked")
    revoked = _expired_access_token(user, jti="jti-exp-revoked")
    with pytest.raises(HTTPException) as exc:
        deps.current_user(_make_request(token=revoked, stream_user_id=int(user["id"])))
    assert exc.value.status_code == 401

    # Tampered signature MUST still be rejected even inside a delegated stream.
    tampered = _expired_access_token(user, jti="jti-exp-bad", secret="wrong-secret-key-0000000000000000")
    with pytest.raises(HTTPException) as exc:
        deps.current_user(_make_request(token=tampered, stream_user_id=int(user["id"])))
    assert exc.value.status_code == 401
