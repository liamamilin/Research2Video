import copy
import json
import threading

import pytest
from fastapi.testclient import TestClient

from g3.app import create_app
from g3.config import Settings
from g3.tests.test_g3_package import READY, CHAPTERS, saved_input
from g3.worker import G2ProcessRunner
from g2 import creative_library


def test_fast_mixed_materials_use_registered_images_and_keep_replay_snapshot(tmp_path):
    from g3.tests.test_creative_forms_api import upload
    from g2.production_plan import verify
    with TestClient(create_app(Settings(data_dir=tmp_path), start_worker=False, preflight=READY)) as client:
        image = upload(client)['image_id']
        body = {'input_mode': 'narration', 'chapters': CHAPTERS, 'duration_mode': 'content',
                'production_mode': 'fast', 'visual_storytelling': True,
                'image_materials': [{'image_id': image, 'caption': '功能页面',
                                    'placements': [{'chapter_index': 0, 'paragraph_index': 0}]}]}
        response = client.post('/api/jobs', json=body)
        assert response.status_code == 202, response.text
        jid = response.json()['job_id']
        saved = saved_input(client, jid)
        assert saved['video_form'] == 'information_cards'
        assert verify(saved)['executor']['id'] == 'hybrid_cards'
        assert saved['slideshow_images_resolved'][0]['image_id'] == image
        assert client.post('/api/jobs', json={**body, 'visual_storytelling': False}).status_code == 422
        assert client.post('/api/jobs', json={**body, 'image_materials': [{'image_id': image}]}).status_code == 422
        preview = client.post('/api/production/resolve', json=body)
        assert preview.status_code == 200, preview.text
        assert preview.json()['plan']['executor']['id'] == 'hybrid_cards'


def test_fast_authored_job_replay_plan_and_snapshot_are_exposed(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path), start_worker=False, preflight=READY)) as client:
        body = {"input_mode": "narration", "chapters": CHAPTERS, "duration_mode": "content",
                "production_mode": "fast", "production_recipe": "ai_news", "idempotency_key": "fast-example",
                "packaging": {"sound": {"enabled": False}},
                "content_notices": [{"chapter_index": 0, "message": "本章节有内容待核实"}]}
        first = client.post("/api/jobs", json=body)
        assert first.status_code == 202, first.text
        jid = first.json()["job_id"]
        assert client.post("/api/jobs", json=body).json()["job_id"] == jid
        saved = saved_input(client, jid)
        assert saved["video_form"] == "information_cards"
        assert saved["production_plan"]["executor"]["id"] == "ffmpeg_cards"
        assert saved["production_plan"]["origins"]["packaging.sound.enabled"] == "request"
        assert client.get(f"/api/jobs/{jid}").json()["production_plan"] == saved["production_plan"]
        assert client.get("/api/production/capabilities").json()["fast"]["forms"] == ["information_cards"]
        assert client.get("/api/jobs").json()["total"] == 1


def test_resolve_preview_uses_same_layers_without_creating_a_job(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path), start_worker=False, preflight=READY)) as client:
        response = client.post("/api/production/resolve", json={
            "source_text": "缓存有失效条件，应当检查更新。" * 5,
            "production_mode": "fast",
            "production_context": {"matrix": {"id": "technology", "settings": {
                "packaging": {"sound": {"enabled": False}}}}},
        })
        assert response.status_code == 200, response.text
        assert response.json()["preview"] is True
        plan = response.json()["plan"]
        assert plan["executor"]["id"] == "ffmpeg_cards"
        assert plan["origins"]["packaging.sound.enabled"] == "matrix"
        assert plan["packaging"]["components"]["sound"]["enabled"] is False
        assert client.get("/api/jobs").json()["total"] == 0


@pytest.mark.parametrize("extra", [
    {"production_plan": {}}, {"production_mode": "fast", "video_form": "process_diagram"},
    {"production_mode": "fast", "visual_style": "legacy"},
    {"content_notices": [{"chapter_index": 99, "message": "不存在"}]},
])
def test_unavailable_combinations_and_injected_snapshot_create_no_jobs(tmp_path, extra):
    with TestClient(create_app(Settings(data_dir=tmp_path), start_worker=False, preflight=READY)) as client:
        response = client.post("/api/jobs", json={"source_text": "完整的原始素材。" * 10, **extra})
        assert response.status_code == 422, response.text
        assert client.get("/api/jobs").json()["total"] == 0


@pytest.mark.parametrize("missing", [False, True])
def test_worker_rejects_modified_or_missing_plan_before_launch(tmp_path, monkeypatch, missing):
    settings = Settings(data_dir=tmp_path)
    with TestClient(create_app(settings, start_worker=False, preflight=READY)) as client:
        jid = client.post("/api/jobs", json={"source_text": "完整的原始素材。" * 10}).json()["job_id"]
        store = client.app.state.store
        claim = store.claim_next()
        value = saved_input(client, jid)
        if missing:
            value.pop("production_plan")
        else:
            value["voice_id"] = "zm_yunxi"
        (store.source_path(jid).parent / "input.json").write_text(json.dumps(value))
        monkeypatch.setattr("g3.worker.subprocess.Popen", lambda *a, **kw: pytest.fail("must not execute"))
        G2ProcessRunner(settings, store).execute(claim, threading.Event())
        assert store.get_job(jid)["error_code"] == "PRODUCTION_PLAN_INVALID"


def test_source_revision_keeps_fast_executor_and_preserves_review_notice(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path), start_worker=False, preflight=READY)) as client:
        jid = client.post("/api/jobs", json={"input_mode": "narration", "chapters": CHAPTERS,
            "duration_mode": "content", "production_mode": "fast",
            "content_notices": [{"chapter_index": 0, "message": "有内容待核实"}]}).json()["job_id"]
        client.post(f"/api/jobs/{jid}/cancel")
        response = client.post(f"/api/jobs/{jid}/revisions", json={"mode": "source", "text": "新的材料仍然保留来源限制。" * 8})
        assert response.status_code == 202, response.text
        child = saved_input(client, response.json()["job_id"])
        assert child["production_plan"]["executor"]["id"] == "ffmpeg_cards"
        assert child["production_plan"]["parent_sha256"] == saved_input(client, jid)["production_plan"]["sha256"]
        assert child["content_notices"][0]["chapter_index"] is None
        assert "待核实" in child["content_notices"][0]["message"]


def test_repeated_submission_reuses_frozen_job_after_recipe_defaults_change(tmp_path, monkeypatch):
    with TestClient(create_app(Settings(data_dir=tmp_path), start_worker=False, preflight=READY)) as client:
        body = {"source_text": "完整的原始素材。" * 10, "production_recipe": "ai_news", "idempotency_key": "stable-recipe"}
        first = client.post("/api/jobs", json=body)
        assert first.status_code == 202
        jid = first.json()["job_id"]
        before = saved_input(client, jid)
        changed = copy.deepcopy(creative_library.catalog())
        changed["recipes"][0]["defaults"]["video_form"] = "information_motion"
        changed["recipes"][0]["version"] += 1
        monkeypatch.setattr(creative_library, "catalog", lambda: changed)
        again = client.post("/api/jobs", json=body)
        assert again.status_code == 202, again.text
        assert again.json()["job_id"] == jid
        assert saved_input(client, jid)["production_plan"] == before["production_plan"]
        assert client.get("/api/jobs").json()["total"] == 1
