from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from g3.app import create_app
from g3.config import Settings, probe_config
from g3.store import JobStore, StoreError
from g3.worker import G2ProcessRunner, SingleWorker


SOURCE = "这是一段用于验证本机任务队列、持久化与视频任务输入校验的中文素材。" * 4


def request_payload(**overrides):
    return {
        "title": "并发保护验收样例",
        "source_text": SOURCE,
        "target_duration_sec": 18,
        "theme": "minimal_dark",
        "voice_id": "zf_xiaoxiao",
        **overrides,
    }


def make_settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / "data", key_file=tmp_path / "missing.env",
                    project_root=Path(__file__).resolve().parents[2])


def make_store(tmp_path: Path) -> JobStore:
    store = JobStore(tmp_path / "data")
    store.initialize()
    return store


def new_job(store: JobStore, **overrides) -> str:
    job_id = str(uuid4())
    store.create_job(job_id, request_payload(**overrides))
    return job_id


def test_atomic_claim_only_assigns_each_job_once_under_contention(tmp_path):
    store = make_store(tmp_path)
    job_ids = {new_job(store, title=f"并发任务 {index}") for index in range(8)}

    with ThreadPoolExecutor(max_workers=12) as pool:
        claims = list(pool.map(lambda _: JobStore(store.data_dir).claim_next(), range(16)))

    claimed = [claim for claim in claims if claim]
    claimed_ids = [claim["job_id"] for claim in claimed]
    assert len(claimed_ids) == 8
    assert set(claimed_ids) == job_ids
    assert len(set(claimed_ids)) == len(claimed_ids)
    assert all(store.get_job(job_id)["attempt_count"] == 1 for job_id in job_ids)
    assert store.claim_next() is None


def test_startup_recovery_marks_running_attempt_failed_and_retryable(tmp_path):
    settings = make_settings(tmp_path)
    store = JobStore(settings.data_dir)
    store.initialize()
    job_id = new_job(store)
    claim = store.claim_next()
    store.set_runner_pid(job_id, claim["attempt_no"], 987654)
    store.update_stage(job_id, 1, "STORYBOARD", "RUNNING")

    with TestClient(create_app(settings, start_worker=False)) as restarted_app:
        job = restarted_app.get(f"/api/jobs/{job_id}").json()
        assert job["status"] == "FAILED"
        assert job["error_code"] == "WORKER_INTERRUPTED"
        assert job["retryable"] is True
        assert job["attempts"][0]["stages"][2]["status"] == "INTERRUPTED"
        assert restarted_app.app.state.store.claim_next() is None


def test_retry_creates_a_separate_attempt_and_preserves_first_workspace(tmp_path):
    store = make_store(tmp_path)
    job_id = new_job(store)
    first = store.claim_next()
    first_root = store.data_dir / first["workspace_rel_path"]
    first_root.mkdir(parents=True)
    (first_root / "worker.log").write_text("first attempt evidence", encoding="utf-8")
    store.mark_failed(job_id, 1, "STORYBOARD", "MODEL_TIMEOUT", "request timed out", True)

    queued = store.retry(job_id)
    second = store.claim_next()
    second_root = store.data_dir / second["workspace_rel_path"]

    assert queued["status"] == "QUEUED"
    assert second["attempt_no"] == 2
    assert first_root != second_root
    assert (first_root / "worker.log").read_text(encoding="utf-8") == "first attempt evidence"
    assert store.get_job(job_id)["attempts"][1]["error_code"] == "MODEL_TIMEOUT"


def test_queued_cancel_is_terminal_and_running_cancel_is_cooperative(tmp_path):
    store = make_store(tmp_path)
    queued_id = new_job(store, title="排队取消样例")
    assert store.request_cancel(queued_id)["status"] == "CANCELLED"
    assert store.claim_next() is None

    running_id = new_job(store, title="运行取消样例")
    store.claim_next()
    assert store.request_cancel(running_id)["cancel_requested"] is True
    assert store.cancel_requested(running_id) is True
    store.mark_cancelled(running_id, 1)
    assert store.get_job(running_id)["status"] == "CANCELLED"


def test_artifact_download_checks_workspace_and_sha256(tmp_path):
    store = make_store(tmp_path)
    job_id = new_job(store)
    claim = store.claim_next()
    attempt_root = store.data_dir / claim["workspace_rel_path"]
    attempt_root.mkdir(parents=True)
    video = attempt_root / "video.mp4"
    payload = b"verified local media placeholder"
    video.write_bytes(payload)
    artifact = {"name": "video.mp4", "relative_path": "video.mp4", "content_type": "video/mp4",
                "sha256": hashlib.sha256(payload).hexdigest(), "bytes": len(payload)}
    assert store.mark_completed(job_id, 1, [artifact]) is True
    resolved, metadata = store.artifact_path(job_id, "video.mp4")
    assert resolved == video
    assert metadata["sha256"] == artifact["sha256"]

    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"outside")
    escaped = {**artifact, "relative_path": "../../outside.mp4",
               "sha256": hashlib.sha256(b"outside").hexdigest(), "bytes": len(b"outside")}
    store.add_artifact(job_id, 1, escaped)
    with pytest.raises(StoreError, match="outside"):
        store.artifact_path(job_id, "video.mp4")


def test_artifact_integrity_detects_modified_file(tmp_path):
    store = make_store(tmp_path)
    job_id = new_job(store)
    claim = store.claim_next()
    attempt_root = store.data_dir / claim["workspace_rel_path"]
    attempt_root.mkdir(parents=True)
    video = attempt_root / "video.mp4"
    video.write_bytes(b"original")
    store.mark_completed(job_id, 1, [{"name": "video.mp4", "relative_path": "video.mp4",
        "content_type": "video/mp4", "sha256": hashlib.sha256(b"original").hexdigest(), "bytes": 8}])
    video.write_bytes(b"tampered")
    with pytest.raises(StoreError, match="integrity"):
        store.artifact_path(job_id, "video.mp4")


def test_single_worker_file_lock_prevents_a_second_worker(tmp_path):
    settings = make_settings(tmp_path)
    store = make_store(tmp_path)
    first = SingleWorker(settings, store, G2ProcessRunner(settings, store))
    second = SingleWorker(settings, store, G2ProcessRunner(settings, store))
    assert first.start() is True
    try:
        assert second.start() is False
    finally:
        first.stop()
        second.stop()


def test_api_create_and_job_state_survive_app_restart(tmp_path):
    settings = make_settings(tmp_path)
    ready = lambda _settings: {"ready": True, "provider": "DeepSeek API", "model": "deepseek-flash",
                              "checks": {"deepseek": True}, "blockers": [], "api_key_value_exposed": False}
    with TestClient(create_app(settings, start_worker=False, preflight=ready)) as client:
        assert client.get("/api/config/status").json()["ready"] is True
        created = client.post("/api/jobs", json=request_payload())
        assert created.status_code == 202
        job_id = created.json()["job_id"]
        assert client.get("/api/jobs").json()["total"] == 1
        assert client.post(f"/api/jobs/{job_id}/cancel").json()["status"] == "CANCELLED"

    with TestClient(create_app(settings, start_worker=False, preflight=ready)) as reopened:
        job = reopened.get(f"/api/jobs/{job_id}").json()
        assert job["status"] == "CANCELLED"
        assert job["source_char_count"] == len(SOURCE.strip())
        assert reopened.get("/api/jobs").json()["items"][0]["job_id"] == job_id


def test_api_rejects_out_of_scope_or_malformed_inputs(tmp_path):
    settings = make_settings(tmp_path)
    ready = lambda _settings: {"ready": True, "provider": "DeepSeek API", "model": "deepseek-flash",
                              "checks": {}, "blockers": [], "api_key_value_exposed": False}
    with TestClient(create_app(settings, start_worker=False, preflight=ready)) as client:
        short_source = client.post("/api/jobs", json=request_payload(source_text="太短"))
        assert short_source.status_code == 422
        long_duration = client.post("/api/jobs", json=request_payload(target_duration_sec=21))
        assert long_duration.status_code == 422
        unknown_key = client.post("/api/jobs", json={**request_payload(), "script_path": "/tmp/code.js"})
        assert unknown_key.status_code == 422
        missing_config = TestClient(create_app(settings, start_worker=False, preflight=probe_config))
        with missing_config as blocked:
            response = blocked.post("/api/jobs", json=request_payload())
            assert response.status_code == 503
            body = json.dumps(blocked.get("/api/config/status").json())
            assert "DEEPSEEK_API_KEY" not in body
