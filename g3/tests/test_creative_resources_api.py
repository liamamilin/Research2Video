import copy
import hashlib
import json
import threading

import pytest
from fastapi.testclient import TestClient

from g2 import creative_library
from g3.app import create_app
from g3.config import Settings
from g3.worker import G2ProcessRunner
from g3.tests.test_g3_package import READY, CHAPTERS, PACKAGE, saved_input


@pytest.mark.parametrize("recipe,form", [("ai_news", "kinetic_typography"), ("how_to", "process_diagram"), ("science_explainer", "auto")])
def test_recipe_end_to_end_material_and_authored_jobs(tmp_path, recipe, form):
    with TestClient(create_app(Settings(data_dir=tmp_path), start_worker=False, preflight=READY)) as client:
        data = client.get("/api/creative-resources").json()
        assert {r["id"] for r in data["recipes"]} == {"ai_news", "how_to", "science_explainer"}
        for body in [{"source_text": "用于讲解的材料内容。" * 8}, {"input_mode": "narration", "chapters": CHAPTERS}]:
            response = client.post("/api/jobs", json={**body, "production_recipe": recipe})
            assert response.status_code == 202, response.text
            jid = response.json()["job_id"]
            saved = saved_input(client, jid)
            assert saved["video_form"] == form
            assert saved["duration_mode"] == "content"
            assert saved["production_recipe_resolved"]["recipe"]["id"] == recipe
            assert client.get(f"/api/jobs/{jid}").json()["production_recipe_resolved"] == saved["production_recipe_resolved"]


def test_reject_unknown_recipe_injected_snapshot_and_unimplemented_form(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path), start_worker=False, preflight=READY)) as client:
        for extra in [{"production_recipe": "unknown"}, {"production_recipe": []},
                      {"production_recipe_resolved": {}}, {"production_recipe": "how_to", "video_form": "F15"}]:
            response = client.post("/api/jobs", json={"source_text": "完整的素材内容。" * 10, **extra})
            assert response.status_code == 422
        assert client.get("/api/jobs").json()["total"] == 0


def test_explicit_form_legacy_packaging_and_capability_errors(tmp_path):
    ready = lambda _: {"ready": True, "checks": {"diagram_layout": False}, "blockers": []}
    with TestClient(create_app(Settings(data_dir=tmp_path), start_worker=False, preflight=ready)) as client:
        body = {"source_text": "教程的完整内容。" * 10, "production_recipe": "how_to"}
        assert client.post("/api/jobs", json=body).status_code == 503
        response = client.post("/api/jobs", json={**body, "video_form": "information_motion", "package": PACKAGE})
        assert response.status_code == 202, response.text
        saved = saved_input(client, response.json()["job_id"])
        assert saved["package"]["intro"]["subtitle"] == PACKAGE["intro"]["subtitle"]
        assert saved["packaging_resolved"]["legacy_request"]


def test_retries_and_all_content_revision_modes_keep_saved_definition(tmp_path, monkeypatch):
    with TestClient(create_app(Settings(data_dir=tmp_path), start_worker=False, preflight=READY)) as client:
        response = client.post("/api/jobs", json={"production_recipe": "ai_news", "source_text": "原始材料内容。" * 10})
        jid = response.json()["job_id"]
        store = client.app.state.store
        snapshot = saved_input(client, jid)["production_recipe_resolved"]
        data = creative_library.catalog()
        changed = copy.deepcopy(data)
        changed["recipes"][0]["defaults"]["video_form"] = "process_diagram"
        changed["recipes"][0]["version"] = 2
        monkeypatch.setattr(creative_library, "catalog", lambda: changed)
        store.claim_next()
        store.mark_failed(jid, 1, "PLAN", "TEMPORARY", "test failure", True)
        assert client.post(f"/api/jobs/{jid}/retry").status_code == 202
        assert saved_input(client, jid)["production_recipe_resolved"] == snapshot
        claim = store.claim_next()
        attempt = store.data_dir / claim["workspace_rel_path"]
        attempt.mkdir(parents=True)
        raw = json.dumps({"segments": [{"text": "先说明变化。"}]}).encode()
        (attempt / "narration.json").write_bytes(raw)
        store.mark_completed(jid, 2, [{"name": "narration.json", "relative_path": "narration.json", "content_type": "application/json",
            "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}])
        for mode, text in [("source", "修订后的完整素材。" * 10), ("narration", "先说明变化。")]:
            response = client.post(f"/api/jobs/{jid}/revisions", json={"mode": mode, "text": text})
            assert response.status_code == 202, response.text
            child = saved_input(client, response.json()["job_id"])
            assert child["production_recipe_resolved"]["recipe"] == snapshot["recipe"]
            assert child["video_form"] == "kinetic_typography"


def test_library_works_without_external_disk_and_does_not_expose_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOVIDEO_CREATIVE_ASSET_ROOT", str(tmp_path / "disconnected"))
    with TestClient(create_app(Settings(data_dir=tmp_path), start_worker=False, preflight=READY)) as client:
        data = client.get("/api/creative-resources").json()
        assert len(data["forms"]) == 31
        assert all(not r["example"]["available"] for r in data["forms"] if r.get("example"))
        assert "relative_path" not in json.dumps(data)
        assert client.get("/api/creative-resources/F08/preview").status_code == 404


@pytest.mark.parametrize("corruption", ["changed", "missing", "settings"])
def test_worker_rejects_corrupt_saved_recipe_before_generation(tmp_path, monkeypatch, corruption):
    settings = Settings(data_dir=tmp_path)
    with TestClient(create_app(settings, start_worker=False, preflight=READY)) as client:
        response = client.post("/api/jobs", json={"production_recipe": "ai_news", "source_text": "完整的素材内容。" * 10})
        jid = response.json()["job_id"]
        store = client.app.state.store
        claim = store.claim_next()
        saved = saved_input(client, jid)
        if corruption == "missing":
            saved.pop("production_recipe_resolved")
        elif corruption == "settings":
            saved["video_form"] = "process_diagram"
        else:
            saved["production_recipe_resolved"]["recipe"]["version"] = 99
        (store.source_path(jid).parent / "input.json").write_text(json.dumps(saved))
        def unexpected(*args, **kwargs):
            pytest.fail("generation must not start with a corrupt recipe")
        monkeypatch.setattr("g3.worker.subprocess.Popen", unexpected)
        G2ProcessRunner(settings, store).execute(claim, threading.Event())
        result = store.get_job(jid)
        assert result["status"] == "FAILED"
        assert result["error_code"] == "PRODUCTION_RECIPE_INVALID"
        assert result["retryable"] is False


@pytest.mark.parametrize("recipe,form", [("ai_news", "kinetic_typography"), ("how_to", "process_diagram"), ("science_explainer", "auto")])
def test_valid_recipe_reaches_existing_runner_and_exports_snapshot(tmp_path, monkeypatch, recipe, form):
    settings = Settings(data_dir=tmp_path)
    with TestClient(create_app(settings, start_worker=False, preflight=READY)) as client:
        response = client.post("/api/jobs", json={"production_recipe": recipe, "source_text": "完整的素材内容。" * 10})
        jid = response.json()["job_id"]
        store = client.app.state.store
        claim = store.claim_next()
        called = []
        def isolated_launch(args, **kwargs):
            called.append(kwargs["env"]["AUTOVIDEO_G2_VIDEO_FORM"])
            raise OSError("isolated test stops before model execution")
        monkeypatch.setattr("g3.worker.subprocess.Popen", isolated_launch)
        G2ProcessRunner(settings, store).execute(claim, threading.Event())
        assert called == [form]
        exported = store.data_dir / claim["workspace_rel_path"] / "production-recipe.json"
        assert json.loads(exported.read_text()) == saved_input(client, jid)["production_recipe_resolved"]
        assert store.get_job(jid)["error_code"] == "WORKER_LAUNCH_FAILED"
