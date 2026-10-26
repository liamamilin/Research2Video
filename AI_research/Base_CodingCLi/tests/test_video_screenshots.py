"""Screenshot acquisition boundaries and versioned paragraph-to-picture delivery."""
import copy
import hashlib
import socket
from io import BytesIO
from uuid import uuid4

import httpx
import pytest
from PIL import Image

from core.video import screenshots, store
from core.video.autovideo import AutoVideoClient, AutoVideoError, AutoVideoUnreachable
from tests.test_video_scripts import video_env, _approved_script, _login, _csrf  # noqa: F401
from web.services import video_render


def png():
    buf = BytesIO()
    Image.new("RGB", (640, 360), "navy").save(buf, "PNG")
    return buf.getvalue()


def metadata():
    return {"url": "https://example.com/pricing", "final_url": "https://example.com/pricing/",
            "title": "官方价格页", "captured_at": "2026-10-25T10:00:00+00:00", "mode": "crop",
            "selector": "", "clip": {"x": 0, "y": 0, "width": 640, "height": 360}}


class Engine:
    base_url = "http://engine.test"
    calls = 0

    def upload_creative_image(self, raw, filename):
        self.calls += 1
        return {"image_id": str(uuid4()), "sha256": hashlib.sha256(raw).hexdigest()}


def material(asset, **changes):
    return {"screenshot_id": asset["id"], "caption": "官方价格表，以来源当时说明为准", "role": "comparison",
            "placements": [{"chapter_index": 1, "paragraph_index": 0}], **changes}


@pytest.mark.parametrize("url", ["file:///etc/passwd", "http://127.0.0.1", "http://[::1]", "http://169.254.169.254",
                                "https://user:password@example.com", "http://example.com:8011", "https://example.com\n.bad"])
def test_non_public_or_credential_urls_are_refused(url):
    with pytest.raises(screenshots.ScreenshotError):
        screenshots.public_url(url)


def test_dns_with_any_private_answer_is_refused(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(None, None, None, None, ("8.8.8.8", 443)),
                                                              (None, None, None, None, ("10.0.0.3", 443))])
    with pytest.raises(screenshots.ScreenshotError):
        screenshots.public_url("https://example.com")


def test_fake_ip_proxy_requires_independent_public_dns_verification(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(None, None, None, None, ("198.18.1.1", 443))])
    verified = []
    monkeypatch.setattr(screenshots, "_proxy_public_addresses", lambda host, minute: verified.append(host) or ("8.8.8.8",))
    assert screenshots.public_url("https://example.com") == "https://example.com"
    assert verified == ["example.com"]
    for url in ("http://198.18.1.1", "http://device.local"):
        with pytest.raises(screenshots.ScreenshotError):
            screenshots.public_url(url)


def test_registration_snapshot_binding_and_changed_script_are_checked(video_env, monkeypatch):
    script = _approved_script()
    version = store.get_version(script["id"], 1)
    engine = Engine()
    monkeypatch.setattr(video_render, "get_client", lambda: engine)
    asset = screenshots.save(script["id"], 1, png(), metadata(), created_by="editor")
    with pytest.raises(screenshots.ScreenshotError, match="登记"):
        screenshots.select(script["id"], version, "object_showcase", [material(asset)], engine.base_url)
    registered = screenshots.register(script["id"], asset["id"], engine)
    assert screenshots.register(script["id"], asset["id"], engine) == registered
    assert engine.calls == 1
    selected = screenshots.select(script["id"], version, "dynamic_collage", [material(asset)], engine.base_url)
    render = video_render.create(script)
    request = render["request"]
    assert request["video_form"] == "dynamic_collage"
    assert request["production_mode"] == "standard" and request["use_draw_things"] is False
    assert request["image_materials"][0]["placements"] == [{"chapter_index": 1, "paragraph_index": 0}]
    assert request["image_materials"][0]["source"]["url"] == metadata()["url"]
    assert "screenshot_id" not in request["image_materials"][0]
    # Editorial changes after submission do not mutate the already frozen render request.
    screenshots.select(script["id"], version, "object_showcase", [], engine.base_url)
    assert store.get_render(render["id"])["request"] == request
    screenshots.select(script["id"], version, "dynamic_collage", [material(asset)], engine.base_url)
    store.update_render(render['id'], status='FAILED')
    mixed = video_render.create(script, production_mode="fast")['request']
    assert mixed['production_mode'] == 'fast' and mixed['video_form'] == 'information_cards'
    assert mixed['image_materials'] == request['image_materials']
    changed = copy.deepcopy(version)
    changed["sections"][0]["paragraphs"][0]["text"] = "另一篇讲稿。"
    with pytest.raises(screenshots.ScreenshotError, match="不一致"):
        screenshots.selection(script["id"], changed)
    store.add_version(script["id"], title=version["title"], sections=version["sections"], length_check={}, edit_type="EDITED")
    assert screenshots.selection(script["id"], store.get_version(script["id"], 2))["materials"] == []
    assert screenshots.selection(script["id"], version) == selected
    with pytest.raises(screenshots.ScreenshotError, match="引擎地址"):
        screenshots.render_materials(script["id"], version, "http://other.test")


def test_bad_mapping_and_tampered_picture_are_rejected(video_env):
    script = _approved_script()
    version = store.get_version(script["id"], 1)
    engine = Engine()
    asset = screenshots.save(script["id"], 1, png(), metadata(), created_by="editor")
    screenshots.register(script["id"], asset["id"], engine)
    for placements in ([], [{"chapter_index": 9, "paragraph_index": 0}],
                       [{"chapter_index": 0, "paragraph_index": 0}] * 2):
        with pytest.raises(screenshots.ScreenshotError):
            screenshots.select(script["id"], version, "object_showcase", [material(asset, placements=placements)], engine.base_url)
    _, path = screenshots.load(script["id"], asset["id"])
    path.write_bytes(b"changed")
    with pytest.raises(screenshots.ScreenshotError):
        screenshots.load(script["id"], asset["id"])


def test_api_preserves_capture_after_registration_failure_and_respects_roles(client, video_env, monkeypatch):
    script = _approved_script()
    sid = script["id"]
    engine = Engine()
    monkeypatch.setattr(screenshots, "capture", lambda *a, **k: (png(), metadata()))
    monkeypatch.setattr(video_render, "get_client", lambda: engine)
    monkeypatch.setattr(engine, "upload_creative_image", lambda *a: (_ for _ in ()).throw(AutoVideoUnreachable("offline")))
    _login(client, "editor", "editor-pass-123")
    r = client.post(f"/api/video/scripts/{sid}/screenshots", json={"version": 1, "url": metadata()["url"]}, headers=_csrf(client))
    assert r.status_code == 201, r.text
    asset = r.json()["asset"]
    assert r.json()["problem"] and asset["image_id"] is None
    assert client.get(asset["preview_url"]).content == png()
    monkeypatch.setattr(engine, "upload_creative_image", Engine().upload_creative_image)
    r = client.post(f"/api/video/scripts/{sid}/screenshots/{asset['id']}/register?version=1", headers=_csrf(client))
    assert r.status_code == 200, r.text
    selected = client.put(f"/api/video/scripts/{sid}/image-materials", json={"version": 1, "materials": [material(asset)]}, headers=_csrf(client))
    assert selected.status_code == 200, selected.text
    data = client.get(f"/api/video/scripts/{sid}/screenshots?version=1").json()
    assert len(data["selection"]["materials"]) == 1 and data["sources"]
    store.add_version(sid, title="新文稿", sections=store.get_version(sid, 1)["sections"], length_check={}, edit_type="EDITED")
    assert client.put(f"/api/video/scripts/{sid}/image-materials", json={"version": 1, "materials": []}, headers=_csrf(client)).status_code == 409
    _login(client, "viewer", "viewer-pass-123")
    assert client.get(f"/api/video/scripts/{sid}/screenshots?version=1").status_code == 200
    assert client.post(f"/api/video/scripts/{sid}/screenshots", json={"version": 2, "url": metadata()["url"]}, headers=_csrf(client)).status_code == 403


def test_image_upload_client_uses_multipart_and_validates_uuid():
    def handler(request):
        assert request.url.path == "/api/creative-images"
        assert request.headers["content-type"].startswith("multipart/form-data")
        assert b"image/png" in request.content and png() in request.content
        return httpx.Response(201, json={"image_id": "not-a-uuid"})
    with pytest.raises(AutoVideoError, match="BAD_IMAGE_RESPONSE"):
        AutoVideoClient("http://engine.test", transport=httpx.MockTransport(handler)).upload_creative_image(png(), "test.png")
