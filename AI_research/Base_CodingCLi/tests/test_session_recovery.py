"""Shared browser sessions must survive late responses from another tab."""

import json
from pathlib import Path

import pytest

from web import audit
from web.auth import jwt as tokens


def login(client, username="admin"):
    response = client.post("/api/auth/login", json={
        "username": username, "password": f"{username}-pass-123", "remember": True,
    })
    assert response.status_code == 200
    return client.cookies.get("ai_research_csrf")


def test_refresh_does_not_rewrite_shared_login_cookies(client):
    csrf = login(client)
    original_refresh = client.cookies.get("ai_research_refresh")
    response = client.post("/api/auth/refresh", headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200
    assert response.json()["csrf_token"] == csrf
    cookies = response.headers.get_list("set-cookie")
    assert len(cookies) == 1
    assert cookies[0].startswith("ai_research_access=")
    assert client.cookies.get("ai_research_refresh") == original_refresh
    assert client.cookies.get("ai_research_csrf") == csrf


def test_rejected_old_logout_keeps_the_new_login(client):
    old_csrf = login(client)
    new_csrf = login(client)
    response = client.post("/api/auth/logout", headers={"X-CSRF-Token": old_csrf})
    assert response.status_code == 403
    assert not response.headers.get_list("set-cookie")
    assert client.get("/api/auth/me").status_code == 200
    assert client.post("/api/auth/refresh", headers={"X-CSRF-Token": new_csrf}).status_code == 200


def test_late_access_cookie_cannot_restore_previous_account(client):
    csrf = login(client)
    late_response = client.post("/api/auth/refresh", headers={"X-CSRF-Token": csrf})
    stale_access = client.cookies.get("ai_research_access")
    assert late_response.status_code == 200
    new_csrf = login(client, "editor")
    # A response from the earlier tab arrives after the editor signed in.
    client.cookies.set("ai_research_access", stale_access, domain="testserver.local", path="/")
    rejected = client.get("/api/auth/me")
    assert rejected.status_code == 401
    assert rejected.json()["error"]["code"] == "session_changed"
    assert client.post("/api/auth/refresh", headers={"X-CSRF-Token": new_csrf}).status_code == 200
    assert client.get("/api/auth/me").json()["username"] == "editor"


@pytest.mark.parametrize("reason", ["expired", "signature_mismatch", "invalid_token", "no_refresh_token"])
def test_refresh_failure_records_reason_without_credentials(client, reason):
    csrf = login(client)
    if reason == "expired":
        token, _, _ = tokens.create_token(1, "admin", "admin", "refresh")
        import jwt
        from web.settings import get_settings
        claims = tokens.decode_token(token)
        claims["exp"] = 1
        token = jwt.encode(claims, get_settings().auth.secret_key, algorithm="HS256")
    elif reason == "signature_mismatch":
        import jwt
        token = jwt.encode({"sub": "1", "kind": "refresh"}, "another-test-signing-key-0123456789", algorithm="HS256")
    else:
        token = "malformed-private-token" if reason == "invalid_token" else None
    client.cookies.delete("ai_research_refresh")
    if token:
        client.cookies.set("ai_research_refresh", token, domain="testserver.local", path="/api/auth")
    response = client.post("/api/auth/refresh", headers={"X-CSRF-Token": csrf})
    assert response.status_code == 401
    event = next(e for e in audit.tail(10) if e["action"] == "session_refresh")
    assert event["result"] == "failed"
    assert event["details"]["reason"] == reason
    assert "pid" in event["details"]
    serialized = json.dumps(event)
    assert not token or token not in serialized
    assert csrf not in serialized
    assert "set-cookie" not in serialized.lower()


def test_cookie_lifetime_uses_remaining_seconds_not_a_rounded_extra_day(client, monkeypatch):
    from starlette.responses import Response
    from web.auth import routes
    monkeypatch.setattr(routes.time, "time", lambda: 1000)
    response = Response()
    routes._set_auth_cookies(response, "access", "refresh", "csrf", refresh_expires_at=1060)
    for value in response.headers.getlist("set-cookie"):
        if not value.startswith("ai_research_access="):
            assert "Max-Age=60;" in value


def test_original_remembered_session_recovers_after_settings_and_app_restart(client, web_env, tmp_path, monkeypatch):
    import os
    import yaml
    from fastapi.testclient import TestClient
    from web import settings
    from web.server import create_app

    key_file = tmp_path / "private.env"
    key_file.write_text('AI_RESEARCH_SECRET_KEY="restart-test-signing-key-0123456789-AbCdEfGh"\n')
    monkeypatch.setattr(settings, "_secret_file", lambda: key_file)
    config_file = Path(os.environ["AI_RESEARCH_WEB_CONFIG"])
    config = yaml.safe_load(config_file.read_text())
    config["auth"]["secret_key"] = "CHANGE_ME"
    config_file.write_text(yaml.safe_dump(config))
    monkeypatch.delenv("AI_RESEARCH_SECRET_KEY", raising=False)
    monkeypatch.delenv("WEB_SECRET_KEY", raising=False)
    settings.reset_settings()
    csrf = login(client)
    original_refresh = client.cookies.get("ai_research_refresh")
    original_file = key_file.read_bytes()
    monkeypatch.delenv("AI_RESEARCH_SECRET_KEY", raising=False)
    settings.reset_settings()
    restarted = TestClient(create_app())
    restarted.cookies.update(client.cookies)
    restarted.cookies.delete("ai_research_access")
    assert restarted.get("/api/auth/me").status_code == 401
    response = restarted.post("/api/auth/refresh", headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200
    assert response.headers["X-Session-Days"] == "30"
    assert restarted.cookies.get("ai_research_refresh") == original_refresh
    assert key_file.read_bytes() == original_file
    assert restarted.get("/api/auth/me").json()["username"] == "admin"
