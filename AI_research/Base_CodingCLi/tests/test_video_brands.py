"""Brand management remains admin-only and upstream settings retain overrides."""
import httpx
import pytest

from core.video.autovideo import AutoVideoClient
from core.video.production_settings import validate, inherit
from tests.test_video_scripts import _csrf, _login


def test_proxy_permissions_and_preserved_profile_body(web_env, monkeypatch):
    from fastapi.testclient import TestClient
    from web.server import create_app
    from web.routes import video_brands
    calls = []
    def engine(request):
        calls.append(request)
        if request.url.path.endswith("/assets/logo"):
            return httpx.Response(200, content=b"png", headers={"Content-Type": "image/png"})
        return httpx.Response(200, json={"active": {"enabled": False}, "profiles": []})
    client = AutoVideoClient("http://engine", transport=httpx.MockTransport(engine))
    monkeypatch.setattr(video_brands, "get_client", lambda: client)
    with TestClient(create_app()) as c:
        assert c.get("/api/video-brands").status_code == 401
        _login(c, "viewer", "viewer-pass-123")
        assert c.get("/api/video-brands").status_code == 403
        _login(c, "admin", "admin-pass-123")
        assert c.get("/api/video-brands").status_code == 200
        assert c.put("/api/video-brands/demo", json={"name": "Demo Studio"}, headers=_csrf(c)).status_code == 200
        assert calls[-1].url.path == "/api/brands/demo"
        assert c.post("/api/video-brands/demo/install", headers=_csrf(c)).status_code == 200
        assert calls[-1].url.path == "/api/brands/demo/install"
        preview = c.get("/api/video-brands/demo/assets/logo")
        assert preview.content == b"png"
        assert preview.headers["cache-control"] == "no-store"


def test_brand_settings_can_be_inherited_and_disabled():
    defaults = validate({"brand": {"profile": "demo"}})
    effective = validate(inherit(defaults, {"brand": {"enabled": False}}))
    assert effective["brand"]["enabled"] is False
    for bad in ({"path": "/tmp/logo.png"}, {"profile": "../private"}, {"enabled": "false"}):
        with pytest.raises(ValueError):
            validate({"brand": bad})
