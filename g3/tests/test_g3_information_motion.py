import json

from fastapi.testclient import TestClient

from g3.app import create_app
from g3.config import Settings

SOURCE = "交接任务时先说明目标，再提供背景，最后约定怎样算完成。背景包括使用场景和已知条件，标准应说明交付物及必要限制。" * 45
READY = lambda _: {"ready": True, "checks": {"diagram_layout": False}, "blockers": []}


def test_information_content_does_not_require_layout_and_survives_restart_and_retry(tmp_path):
    settings = Settings(data_dir=tmp_path / "data")
    with TestClient(create_app(settings, start_worker=False, preflight=READY)) as client:
        response = client.post("/api/jobs", json={"source_text": SOURCE, "video_form": "information_motion", "duration_mode": "content"})
        assert response.status_code == 202
        jid = response.json()["job_id"]
        claim = client.app.state.store.claim_next()
        assert (claim["video_form"], claim["duration_mode"]) == ("information_motion", "content")
        client.app.state.store.mark_failed(jid, 1, "STORYBOARD", "TEST_FAILURE", "test", True)
        assert client.post(f"/api/jobs/{jid}/retry").status_code == 202
        assert client.post("/api/jobs", json={"source_text": SOURCE[:100], "video_form": "information_motion", "use_draw_things": True}).status_code == 422
    with TestClient(create_app(settings, start_worker=False, preflight=READY)) as client:
        job = client.get(f"/api/jobs/{jid}").json()
        assert job["video_form"] == "information_motion"
        payload = json.loads((settings.data_dir / "jobs" / jid / "input/input.json").read_text())
        assert payload["duration_mode"] == "content"
