import yaml

from core.schedule_store import ScheduleStore
from tests.test_config_api import _csrf, _login


def test_policy_api_requires_admin_and_csrf(client, web_env):
    assert client.get("/api/config/production-policy").status_code == 401
    _login(client, "viewer", "viewer-pass-123")
    assert client.get("/api/config/production-policy").status_code == 403
    _login(client)
    assert client.put("/api/config/production-policy", json={}).status_code == 403


def test_policy_edits_use_revisions_sync_only_collector_and_preserve_existing_runs(
    client, web_env
):
    root = web_env[0]
    matrices = root / "config/matrices"
    matrices.mkdir(exist_ok=True)
    (matrices / "demo.yaml").write_text("name: Demo\njobs: [test_job]\n")
    (root / "jobs/system").mkdir(exist_ok=True)
    (root / "jobs/system/material_collection.yaml").write_text(
        "prompt: Collect\nsource_collection: true\n"
    )
    storage = ScheduleStore(str(root / "state"))
    old = storage.save("test_job", "0 2 * * *", timezone="Asia/Shanghai", now=1000000)
    storage.enqueue_due(old["next_run_at"])
    old = storage.get(old["id"])
    before = storage.runs(old["id"])
    _login(client)
    value = {
        "schema_version": 1,
        "matrices": ["demo"],
        "window": {"mode": "previous_slot"},
        "collection": {"enabled": True, "schedule": "0 0 * * *"},
        "automatic": {"daily_limit": 4},
    }
    url = "/api/config/production-policy"
    response = client.put(
        url, json={"expected_revision": "", "policy": value}, headers=_csrf(client)
    )
    assert response.status_code == 200, response.text
    result = response.json()
    revision = result["policy"]["revision"]
    collector = result["collector"]
    assert (
        collector["job_name"] == "system/material_collection"
        and collector["schedule"] == "0 0 * * *"
    )
    assert storage.runs(old["id"]) == before
    assert storage.get(old["id"]) == old
    assert (
        client.put(
            url, json={"expected_revision": "", "policy": value}, headers=_csrf(client)
        ).status_code
        == 409
    )
    value["window"] = {"mode": "rolling", "days": 14}
    value["automatic"]["daily_limit"] = 3
    response = client.put(
        url,
        json={"expected_revision": revision, "policy": value},
        headers=_csrf(client),
    )
    assert response.status_code == 200 and response.json()["collector"] == collector
    assert (
        yaml.safe_load((root / "config/production_policy.yaml").read_text())["window"][
            "days"
        ]
        == 14
    )
    assert list((root / "state/policy-history").glob("*.yaml"))


def test_disabled_collection_does_not_cancel_running_or_queued_work(client, web_env):
    root = web_env[0]
    (root / "config/matrices").mkdir(exist_ok=True)
    (root / "config/matrices/demo.yaml").write_text("name: Demo\njobs: []\n")
    _login(client)
    value = {
        "schema_version": 1,
        "matrices": ["demo"],
        "window": {"mode": "rolling", "days": 14},
        "collection": {"enabled": False},
    }
    response = client.put(
        "/api/config/production-policy",
        json={"policy": value, "expected_revision": ""},
        headers=_csrf(client),
    )
    assert response.status_code == 200 and response.json()["collector"] is None
