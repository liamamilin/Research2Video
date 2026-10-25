import json
from fastapi.testclient import TestClient

from g3.app import create_app
from g3.config import Settings

SOURCE = "读者提交预约申请。系统核对库存，有库存时保留图书，没有库存时进入候补。图书归还后，系统按候补顺序通知读者取书。" * 45
READY = lambda _: {"ready": True, "checks": {"diagram_layout": True}, "blockers": []}


def test_content_mode_accepts_complete_material_and_survives_restart_and_retry(tmp_path):
    settings = Settings(data_dir=tmp_path / "data")
    with TestClient(create_app(settings, start_worker=False, preflight=READY)) as client:
        response = client.post("/api/jobs", json={"source_text": SOURCE, "duration_mode": "content", "video_form": "process_diagram"})
        assert response.status_code == 202
        jid = response.json()["job_id"]
        store = client.app.state.store
        claim = store.claim_next()
        assert claim["duration_mode"] == "content"
        assert claim["source_char_count"] > 2000
        store.mark_failed(jid, 1, "STORYBOARD", "TEST_FAILURE", "test", True)
        assert client.post(f"/api/jobs/{jid}/retry").status_code == 202
    with TestClient(create_app(settings, start_worker=False, preflight=READY)) as reopened:
        assert reopened.get(f"/api/jobs/{jid}").json()["duration_mode"] == "content"
        payload = json.loads((settings.data_dir / "jobs" / jid / "input/input.json").read_text())
        assert payload["duration_mode"] == "content"


def test_fixed_mode_keeps_legacy_limits_and_content_requires_diagram_form(tmp_path):
    settings = Settings(data_dir=tmp_path / "data")
    with TestClient(create_app(settings, start_worker=False, preflight=READY)) as client:
        assert client.post("/api/jobs", json={"source_text": SOURCE}).status_code == 202
        assert client.post("/api/jobs", json={"source_text": SOURCE, "duration_mode": "content"}).status_code == 422
        response = client.post("/api/jobs", json={"source_text": SOURCE[:100]})
        assert response.status_code == 202
        assert client.get(f"/api/jobs/{response.json()['job_id']}").json()["duration_mode"] == "fixed"
