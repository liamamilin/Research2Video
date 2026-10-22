"""The video model (AutoVideo's storyboard provider) is chosen on AREC's settings page: spec/llm-providers.md."""

from __future__ import annotations

import json

import httpx
import pytest

from core.video.autovideo import AutoVideoClient
from tests.test_video_scripts import _csrf, _login, video_env  # noqa: F401
from web.services import video_render

CONFIG = {"label": "DeepSeek（官方）", "base_url": "https://api.deepseek.com", "model": "deepseek-flash",
          "key_env": "DEEPSEEK_API_KEY", "env_file": "", "source": "默认", "needs_key": True, "key_ready": True, "error": ""}


class FakeSettingsAutoVideo:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict | None]] = []
        self.down = False

    def client(self) -> AutoVideoClient:
        return AutoVideoClient("http://autovideo.test", transport=httpx.MockTransport(self.handle))

    def handle(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("refused", request=request)
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, request.url.path, body))
        if request.url.path == "/api/config/llm/test":
            return httpx.Response(200, json={"ok": True, "status": 200, "message": "可用"})
        if request.method == "PUT" and not str(body["base_url"]).startswith("http"):
            return httpx.Response(422, json={"detail": {"code": "LLM_SETTINGS_INVALID", "message": "接口地址要以 http:// 开头"}})
        if request.method == "PUT":
            return httpx.Response(200, json={**CONFIG, **body, "source": "设置页"})
        return httpx.Response(200, json=CONFIG)


@pytest.fixture
def fake(monkeypatch):
    fake = FakeSettingsAutoVideo()
    monkeypatch.setattr(video_render, "get_client", fake.client)
    return fake


def test_only_an_admin_can_see_or_change_the_video_model(video_env, client, fake):
    _login(client, "editor", "editor-pass-123")
    assert client.get("/api/video/model").status_code == 403
    assert client.put("/api/video/model", json={"base_url": "https://x/v1", "model": "m"}, headers=_csrf(client)).status_code == 403
    assert fake.calls == []


def test_the_page_reads_what_autovideo_uses_now(video_env, client, fake):
    _login(client, "admin", "admin-pass-123")
    body = client.get("/api/video/model").json()
    assert body["reachable"] is True and body["config"]["model"] == "deepseek-flash"


def test_an_unreachable_autovideo_is_shown_not_raised(video_env, client, fake):
    _login(client, "admin", "admin-pass-123")
    fake.down = True
    body = client.get("/api/video/model")
    assert body.status_code == 200 and body.json()["reachable"] is False and "AutoVideo 无响应" in body.json()["error"]


def test_saving_sends_the_key_name_and_this_apps_env_file_never_a_key(video_env, client, fake):
    _login(client, "admin", "admin-pass-123")
    reply = client.put("/api/video/model", headers=_csrf(client), json={
        "base_url": "https://amr-link.open-design.ai/v1", "model": "deepseek-v4-flash", "key_env": "OPENDESIGN_API_KEY"})
    assert reply.status_code == 200 and reply.json()["source"] == "设置页"
    method, path, sent = fake.calls[-1]
    assert (method, path) == ("PUT", "/api/config/llm")
    assert sent["key_env"] == "OPENDESIGN_API_KEY" and sent["env_file"].endswith(".env")


def test_a_keyless_local_service_sends_no_env_file(video_env, client, fake):
    _login(client, "admin", "admin-pass-123")
    client.put("/api/video/model", headers=_csrf(client), json={"base_url": "http://127.0.0.1:11434/v1", "model": "qwen3:8b"})
    assert fake.calls[-1][2]["env_file"] == ""


def test_autovideos_refusal_reaches_the_page_as_a_sentence(video_env, client, fake):
    _login(client, "admin", "admin-pass-123")
    reply = client.put("/api/video/model", headers=_csrf(client), json={"base_url": "ftp://x", "model": "m"})
    assert reply.status_code == 422 and "http://" in json.dumps(reply.json(), ensure_ascii=False)


def test_test_and_reset_go_through(video_env, client, fake):
    _login(client, "admin", "admin-pass-123")
    ok = client.post("/api/video/model/test", headers=_csrf(client), json={"base_url": "https://x/v1", "model": "m"})
    assert ok.json()["ok"] is True and fake.calls[-1][1] == "/api/config/llm/test"
    assert client.delete("/api/video/model", headers=_csrf(client)).status_code == 200
    assert fake.calls[-1][:2] == ("DELETE", "/api/config/llm")
