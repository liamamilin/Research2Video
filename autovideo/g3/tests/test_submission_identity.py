from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from g3.app import create_app
from g3.store import JobStore, StoreError
from g3.tests.test_g3 import make_settings, make_store, request_payload


def test_parallel_replays_create_one_workspace_and_one_job(tmp_path):
    store = make_store(tmp_path)
    payload = request_payload(idempotency_key="research:one-render")
    with ThreadPoolExecutor(max_workers=8) as pool:
        jobs = list(pool.map(lambda _: store.create_job(str(uuid4()), payload), range(12)))
    assert len({job["job_id"] for job in jobs}) == 1
    assert len(list(store.job_root.iterdir())) == 1
    assert store.list_jobs()["total"] == 1
    assert store.claim_next() is not None
    assert store.claim_next() is None


def test_key_cannot_adopt_a_job_with_different_production_settings(tmp_path):
    store = make_store(tmp_path)
    payload = request_payload(idempotency_key="research:one-render")
    first = store.create_job(str(uuid4()), payload)
    restarted = JobStore(store.data_dir)
    restarted.initialize()
    for changed in ({"voice_id": "zf_xiaoyi"}, {"packaging": {"enabled": False}},
                    {"source_text": payload["source_text"] + "新段落。"}):
        with pytest.raises(StoreError) as error:
            restarted.create_job(str(uuid4()), {**payload, **changed})
        assert error.value.code == "IDEMPOTENCY_CONFLICT"
    assert restarted.get_job(first["job_id"])["status"] == "QUEUED"
    assert len(list(store.job_root.iterdir())) == 1


def test_api_replays_survive_restart_and_lost_readiness(tmp_path):
    settings = make_settings(tmp_path)
    payload = request_payload(idempotency_key="research:one-render")
    ready = lambda _: {"ready": True, "checks": {}}
    with TestClient(create_app(settings, start_worker=False, preflight=ready)) as client:
        first = client.post("/api/jobs", json=payload)
        assert first.status_code == 202, first.text
        job_id = first.json()["job_id"]
    with TestClient(create_app(settings, start_worker=False, preflight=lambda _: {"ready": False})) as client:
        replay = client.post("/api/jobs", json=payload)
        assert replay.status_code == 202, replay.text
        assert replay.json()["job_id"] == job_id
        assert replay.json()["status_url"] == f"/api/jobs/{job_id}"
        listed = client.get("/api/jobs", params={"idempotency_key": payload["idempotency_key"]}).json()
        assert [job["job_id"] for job in listed["items"]] == [job_id]
        conflict = client.post("/api/jobs", json={**payload, "target_duration_sec": 20})
        assert conflict.status_code == 409
        assert conflict.json()["detail"]["code"] == "IDEMPOTENCY_CONFLICT"


def test_no_key_still_allows_intentional_reruns(tmp_path):
    store = make_store(tmp_path)
    jobs = [store.create_job(str(uuid4()), request_payload()) for _ in range(2)]
    assert jobs[0]["job_id"] != jobs[1]["job_id"]
