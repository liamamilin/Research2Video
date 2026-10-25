"""Unattended source pictures share the normal render path and never weaken its submit ledger."""
import copy
import hashlib
from io import BytesIO
from uuid import uuid4
from datetime import datetime, timezone

import httpx
import pytest
from PIL import Image

from core.video import screenshots, store, visual_materials as visuals
from tests.test_video_scripts import video_env, FakeAutoVideo  # noqa: F401
from web.services import video_render, video_visuals

URL = "https://example.com/docs/screenshots"


def png():
    buf = BytesIO()
    Image.new("RGB", (800, 500), "navy").save(buf, "PNG")
    return buf.getvalue()


def script():
    row = store.create_script("deep_dive", {"title": "Acme 截图功能", "fields": {}})
    sections = [{"title": "了解功能", "paragraphs": [
        {"text": "先了解适用范围。", "citations": [], "evidence": "UNCITED"},
        {"text": "Acme 支持网页截图功能。", "citations": [URL], "evidence": "CITED"},
        {"text": "最后归纳本期重点。", "citations": [], "evidence": "UNCITED"}]}]
    store.add_version(row["id"], title="Acme 截图功能", sections=sections, length_check={}, edit_type="EDITED")
    store.update_script(row["id"], status="APPROVED", approved_version=1)
    return store.get_script(row["id"])


class Engine(FakeAutoVideo):
    def __init__(self):
        super().__init__()
        self.uploads = 0

    def handle(self, request):
        if request.url.path == "/api/creative-images":
            self.uploads += 1
            return httpx.Response(201, json={"image_id": str(uuid4()), "sha256": hashlib.sha256(png()).hexdigest()})
        return super().handle(request)


@pytest.fixture
def enabled(video_env, monkeypatch):
    engine = Engine()
    monkeypatch.setattr(video_render, "get_client", engine.client)
    monkeypatch.setattr(video_render, "_video_config", lambda: {"visual_materials": {"enabled": True, "version": 1}})
    acquired = []

    def capture(need, url, policy, **kwargs):
        acquired.append(url)
        return png(), {"url": url, "final_url": url, "title": "Acme 功能说明", "captured_at": datetime.now(timezone.utc).isoformat(),
                       "mode": "viewport", "selector": "", "clip": None}

    monkeypatch.setattr(video_visuals, "acquire", capture)
    return engine, acquired


def test_normal_render_automatically_captures_registers_maps_and_freezes_before_submit(enabled):
    engine, acquired = enabled
    row = script()
    render = video_render.create(row)
    assert acquired == [] and render["visuals"]["state"] == "pending"
    original = copy.deepcopy(render["request"])
    assert original["visual_storytelling"] is True
    assert video_render.process(render["id"]) == "COMPLETED"
    assert acquired == [URL] and engine.uploads == 1
    submitted = engine.posts[0]
    assert submitted["video_form"] == "object_showcase"
    assert submitted["visual_storytelling"] is True
    assert submitted["image_materials"][0]["placements"] == [{"chapter_index": 0, "paragraph_index": 1}]
    saved = store.get_render(render["id"])
    assert saved["request"] == original
    assert saved["submission_request"] == submitted
    assert saved["visuals"]["state"] == "completed"


def test_capture_failure_downgrades_without_holding_or_retrying_the_video(enabled, monkeypatch):
    engine, acquired = enabled
    monkeypatch.setattr(video_visuals, "acquire", lambda *a, **k: (_ for _ in ()).throw(screenshots.ScreenshotError("验证码页面")))
    render = video_render.create(script())
    assert video_render.process(render["id"]) == "COMPLETED"
    assert not engine.uploads and "image_materials" not in engine.posts[0]
    saved = store.get_render(render["id"])
    assert saved["visuals"]["state"] == "degraded"
    assert "验证码" in saved["visuals"]["decisions"][0]["reason"]


def test_lost_submit_reconciles_frozen_images_without_recapture(enabled):
    engine, acquired = enabled
    engine.drop_create_response = True
    render = video_render.create(script())
    assert video_render.process(render["id"]) == "COMPLETED"
    assert len(engine.posts) == engine.uploads == len(acquired) == 1
    assert video_render.process(render["id"]) == "COMPLETED"
    assert len(acquired) == 1


def test_explicit_empty_manual_choice_and_fast_cards_do_not_trigger_capture(enabled):
    engine, acquired = enabled
    row = script()
    version = store.get_version(row["id"], 1)
    screenshots.select(row["id"], version, "object_showcase", [], engine.client().base_url)
    render = video_render.create(row)
    assert render["visuals"]["state"] == "manual"
    assert video_render.process(render["id"]) == "COMPLETED"
    other = script()
    engine.statuses = ["COMPLETED"]
    fast = video_render.create(other, production_mode="fast")
    assert fast["visuals"]["state"] == "skipped"
    assert video_render.process(fast["id"]) == "COMPLETED"
    assert acquired == []


def test_snapshot_tampering_is_refused_before_network_submit(enabled):
    engine, _ = enabled
    render = video_render.create(script())
    frozen = video_visuals.freeze(render, engine.client())
    payload = copy.deepcopy(frozen["submission_request"])
    payload["chapters"][0]["paragraphs"][0] = "被改变的讲稿"
    store.update_render(render["id"], submission_request=payload)
    assert video_render.process(render["id"]) == "FAILED"
    assert not engine.posts


def test_fact_provenance_controls_sources_and_skips_weak_or_derived_claims(video_env):
    row = script()
    version = store.get_version(row["id"], 1)
    store.save_fact_sheet("sheet", report_path="", sheet={"facts": [
        {"id": "F1", "claim": "Acme 支持截图功能", "sources": [URL], "confidence": "厂商自述"},
        {"id": "F2", "claim": "推导成本", "sources": [URL], "confidence": "推导", "derived_from": ["F1"]}]})
    version["meta"]["fact_sheet_id"] = "sheet"
    paragraph = version["sections"][0]["paragraphs"][1]
    paragraph.update(facts=["F1"], citations=["https://unrelated.example.com"])
    assert visuals.needs_for(version)[0]["urls"] == [URL]
    paragraph.update(facts=["F2"])
    assert visuals.needs_for(version) == []
    paragraph.update(facts=["F1"], evidence="NEEDS_REVIEW")
    assert visuals.needs_for(version) == []


def test_completed_preparation_survives_restart_before_freezing_submission(enabled):
    engine, acquired = enabled
    render = video_render.create(script())
    prepared, audit = video_visuals.prepare(render, engine.client())
    assert prepared["image_materials"] and audit["state"] == "completed"
    frozen = video_visuals.freeze(store.get_render(render["id"]), engine.client())
    assert frozen["submission_request"] == prepared
    assert len(acquired) == engine.uploads == 1


def test_partial_capture_resumes_registration_even_when_page_budget_was_consumed(enabled, monkeypatch):
    engine, acquired = enabled
    render = video_render.create(script())
    audit = render["visuals"]
    audit["policy"]["max_pages"] = 1
    store.update_render(render["id"], visuals=audit)
    original = screenshots.register
    monkeypatch.setattr(screenshots, "register", lambda *a, **k: (_ for _ in ()).throw(SystemExit("simulated process exit")))
    with pytest.raises(SystemExit):
        video_visuals.prepare(store.get_render(render["id"]), engine.client())
    monkeypatch.setattr(screenshots, "register", original)
    resumed = video_visuals.freeze(store.get_render(render["id"]), engine.client())
    assert resumed["submission_request"]["image_materials"]
    assert len(acquired) == engine.uploads == 1


def test_cache_reuses_verified_bytes_for_another_render_and_reregisters_for_current_engine(enabled):
    engine, acquired = enabled
    first = video_render.create(script())
    assert video_render.process(first["id"]) == "COMPLETED"
    engine.statuses = ["COMPLETED"]
    second = video_render.create(script())
    assert video_render.process(second["id"]) == "COMPLETED"
    assert len(acquired) == 1 and engine.uploads == 2
    assert store.get_render(second["id"])["visuals"]["decisions"][0]["cached"]


def test_autopilot_uses_the_same_automatic_preparation_without_user_action(enabled, monkeypatch):
    from web.services import video_executor
    engine, acquired = enabled
    monkeypatch.setattr(video_render, "start", lambda render_id: video_render.process(render_id))
    outcome = video_executor.advance_auto(script(), {}, can_start_script=True, can_start_render=True)
    assert outcome["took"]["render"] and not outcome["hold"]
    assert acquired == [URL] and engine.posts[0]["image_materials"]


def test_deduplicated_picture_can_cover_multiple_paragraphs_without_duplicate_uuid(enabled):
    engine, acquired = enabled
    row = script()
    version = store.get_version(row["id"], 1)
    version["sections"][0]["paragraphs"][2] = {"text": "Acme 支持页面截图功能。", "citations": [URL], "evidence": "CITED"}
    store.update_version_audit(row["id"], 1, sections=version["sections"], meta=version["meta"])
    render = video_render.create(store.get_script(row["id"]))
    assert video_render.process(render["id"]) == "COMPLETED"
    images = engine.posts[0]["image_materials"]
    assert len(images) == 1 and engine.uploads == len(acquired) == 1
    assert images[0]["placements"] == [{"chapter_index": 0, "paragraph_index": 1}, {"chapter_index": 0, "paragraph_index": 2}]


def test_new_version_recomputes_placements_while_reusing_valid_source_bytes(enabled):
    engine, acquired = enabled
    row = script()
    first = video_render.create(row)
    assert video_render.process(first["id"]) == "COMPLETED"
    version = store.get_version(row["id"], 1)
    version["sections"][0]["paragraphs"].reverse()
    # Move the sourced paragraph to a different position, rather than inherit its old position.
    p = version["sections"][0]["paragraphs"].pop(1)
    version["sections"][0]["paragraphs"].append(p)
    store.add_version(row["id"], title=version["title"], sections=version["sections"], length_check={}, edit_type="EDITED")
    store.update_script(row["id"], status="APPROVED", approved_version=2)
    engine.statuses = ["COMPLETED"]
    second = video_render.create(store.get_script(row["id"]))
    assert video_render.process(second["id"]) == "COMPLETED"
    assert len(acquired) == 1
    assert engine.posts[0]["image_materials"][0]["placements"][0]["paragraph_index"] == 1
    assert engine.posts[1]["image_materials"][0]["placements"][0]["paragraph_index"] == 2


def test_page_budget_and_failed_first_source_do_not_block_render(enabled, monkeypatch):
    engine, acquired = enabled
    row = script()
    render = video_render.create(row)
    audit = render["visuals"]
    audit["policy"]["max_pages"] = 1
    audit["needs"][0]["urls"] = ["https://example.com/fail", URL]
    store.update_render(render["id"], visuals=audit)
    calls = []
    def reject(*args, **kwargs):
        calls.append(args[1]); raise screenshots.ScreenshotError("页面不可用")
    monkeypatch.setattr(video_visuals, "acquire", reject)
    assert video_render.process(render["id"]) == "COMPLETED"
    assert len(calls) == 1 and not engine.uploads
    assert store.get_render(render["id"])["visuals"]["state"] == "degraded"


def test_api_exposes_automatic_selection_and_explicit_manual_opt_out(enabled, client):
    from tests.test_video_scripts import _login, _csrf
    engine, _ = enabled
    row = script()
    render = video_render.create(row)
    assert video_render.process(render["id"]) == "COMPLETED"
    _login(client, "editor", "editor-pass-123")
    data = client.get(f"/api/video/scripts/{row['id']}/screenshots?version=1").json()
    assert data["automatic"]["state"] == "completed" and data["selection"]["origin"] == "automatic"
    assert data["selection"]["materials"][0]["placements"] == [{"chapter_index": 0, "paragraph_index": 1}]
    saved = client.put(f"/api/video/scripts/{row['id']}/image-materials", json={"version": 1, "materials": []}, headers=_csrf(client))
    assert saved.status_code == 200
    next_render = video_render.create(row)
    assert next_render["visuals"]["state"] == "manual"
