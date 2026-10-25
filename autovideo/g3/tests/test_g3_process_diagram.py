from dataclasses import replace
import json
from fastapi.testclient import TestClient

from g3.app import create_app
from g3.config import Settings

SOURCE = "请求到达后先查询缓存。缓存命中时直接返回缓存的数据；未命中时读取数据库，再把结果写入缓存并返回。缓存保存之前查询的结果，后续请求命中时可以减少重复读取。"


def test_diagram_option_survives_restart_and_retry(tmp_path):
    settings = Settings(data_dir=tmp_path / "data")
    ready = lambda _: {"ready": True, "checks": {"diagram_layout": True}, "blockers": []}
    with TestClient(create_app(settings, start_worker=False, preflight=ready)) as client:
        response = client.post("/api/jobs", json={"source_text": SOURCE, "video_form": "process_diagram"})
        assert response.status_code == 202
        jid = response.json()["job_id"]
        store = client.app.state.store
        claim = store.claim_next()
        assert claim["video_form"] == "process_diagram"
        store.mark_failed(jid, 1, "STORYBOARD", "TEST_FAILURE", "test", True)
        assert client.post(f"/api/jobs/{jid}/retry").status_code == 202
    with TestClient(create_app(settings, start_worker=False, preflight=ready)) as reopened:
        assert reopened.get(f"/api/jobs/{jid}").json()["video_form"] == "process_diagram"
        payload = json.loads((settings.data_dir / "jobs" / jid / "input/input.json").read_text())
        assert payload["video_form"] == "process_diagram"


def test_optional_diagram_dependency_does_not_block_legacy_jobs(tmp_path):
    settings = Settings(data_dir=tmp_path / "data")
    ready = lambda _: {"ready": True, "checks": {"diagram_layout": False}, "blockers": []}
    with TestClient(create_app(settings, start_worker=False, preflight=ready)) as client:
        assert client.post("/api/jobs", json={"source_text": SOURCE}).status_code == 202
        assert client.post("/api/jobs", json={"source_text": SOURCE, "video_form": "process_diagram"}).status_code == 503
        assert client.post("/api/jobs", json={"source_text": SOURCE, "video_form": "unknown"}).status_code == 422


def test_diagram_rejects_unimplemented_image_combination(tmp_path):
    settings = Settings(data_dir=tmp_path / "data")
    ready = lambda _: {"ready": True, "checks": {"diagram_layout": True, "draw_things": True}, "blockers": []}
    with TestClient(create_app(settings, start_worker=False, preflight=ready)) as client:
        response = client.post("/api/jobs", json={"source_text": SOURCE, "video_form": "process_diagram", "use_draw_things": True})
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "DIAGRAM_BACKGROUND_UNSUPPORTED"
