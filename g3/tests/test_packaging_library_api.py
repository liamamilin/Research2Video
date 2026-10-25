from fastapi.testclient import TestClient
import hashlib
import json

from g3.app import create_app
from g3.config import Settings
from g3.tests.test_g3_package import READY, CHAPTERS, saved_input


def test_material_and_authored_entry_points_default_to_common_library(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path), start_worker=False, preflight=READY)) as client:
        catalog = client.get("/api/packaging").json()
        assert len(catalog["components"]) == 5
        for request in [{"source_text": "用于讲解的材料内容。" * 8},
                        {"input_mode": "narration", "chapters": CHAPTERS, "duration_mode": "content", "video_form": "information_motion"}]:
            response = client.post("/api/jobs", json=request)
            assert response.status_code == 202, response.text
            saved = saved_input(client, response.json()["job_id"])
            assert saved["packaging_resolved"]["selection"] == {"preset": "common"}
            assert {"intro", "outro", "badge"} <= set(saved["package"])


def test_flow_diagrams_get_a_separate_source_card_and_can_disable_packaging(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path), start_worker=False, preflight=READY)) as client:
        body = {"input_mode": "narration", "chapters": CHAPTERS, "duration_mode": "content", "video_form": "process_diagram",
                "sources": [{"title": "来源", "url": "https://example.com/a", "date": "2026-10-23"}]}
        response = client.post("/api/jobs", json=body)
        assert response.status_code == 202, response.text
        saved = saved_input(client, response.json()["job_id"])
        assert saved["packaging_resolved"]["source_card_state"] == "enabled"
        off = client.post("/api/jobs", json={**body, "packaging": {"enabled": False}})
        assert off.status_code == 202
        assert "package" not in saved_input(client, off.json()["job_id"])


def test_api_rejects_unknown_components_and_resolved_snapshot_injection(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path), start_worker=False, preflight=READY)) as client:
        for extra in [{"packaging": {"intro": {"component": "unknown"}}}, {"packaging_resolved": {}}]:
            response = client.post("/api/jobs", json={"source_text": "用于讲解的材料内容。" * 8, **extra})
            assert response.status_code == 422
        assert client.get("/api/jobs").json()["total"] == 0


def test_library_content_revisions_preserve_the_snapshot_and_sources(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path), start_worker=False, preflight=READY)) as client:
        references = [{"title": "官方资料", "url": "https://example.com/a", "date": "2026-10-23"}]
        response = client.post("/api/jobs", json={"input_mode": "narration", "chapters": CHAPTERS,
                                               "duration_mode": "content", "video_form": "information_motion",
                                               "packaging": {"label": "快讯"}, "sources": references})
        assert response.status_code == 202
        jid = response.json()["job_id"]
        store = client.app.state.store
        claim = store.claim_next()
        attempt = store.data_dir / claim["workspace_rel_path"]
        attempt.mkdir(parents=True)
        raw = json.dumps({"segments": [{"text": "先说明价格变化。"}]}).encode()
        (attempt / "narration.json").write_bytes(raw)
        store.mark_completed(jid, 1, [{"name": "narration.json", "relative_path": "narration.json", "content_type": "application/json",
                                      "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}])
        parent = saved_input(client, jid)
        for mode, text in [("source", "这一段新的制作素材只用于验证包装继承。" * 5), ("narration", "先说明价格变化。")]:
            child = client.post(f"/api/jobs/{jid}/revisions", json={"mode": mode, "text": text})
            assert child.status_code == 202, child.text
            saved = saved_input(client, child.json()["job_id"])
            assert saved["packaging_resolved"] == parent["packaging_resolved"]
            assert saved["sources"] == references
