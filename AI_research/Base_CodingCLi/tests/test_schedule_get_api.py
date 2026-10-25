"""Contract for reading a single recurring plan.

`GET /api/scheduler/{schedule_id}` did not exist: the list view merged managed plans
with the legacy cron entries, so a caller that already knew a plan's id had to fetch
the whole list and pick from it. These tests pin what the new route promises, since
an agent capability is now built on it.
"""
import time

import pytest

from core.schedule_store import ScheduleStore
from web.services import managed_scheduler as managed


@pytest.fixture
def admin(client, web_env, monkeypatch):
    root, _ = web_env

    def online():
        managed.store().heartbeat(
            {"checked_at": time.time(), "status": "ok", "runtime": managed.runtime().identity()}
        )
        return {"online": True}

    monkeypatch.setattr(managed, "ensure_online", online)
    client.post("/api/auth/login", json={"username": "admin", "password": "admin-pass-123"})
    headers = {"X-CSRF-Token": client.cookies.get("ai_research_csrf")}

    # managed.save refuses a plan for a job that does not exist, so the job is
    # created for real rather than named in the abstract.
    created = client.post(
        "/api/jobs",
        headers=headers,
        json={
            "name": "daily_ai",
            "category": "research",
            "description": "daily AI research",
            "prompt": "Research AI coding agent changes.",
            "output": "output/daily_ai_{date}.md",
        },
    )
    assert created.status_code == 201, created.text

    return client, root, headers


def create_plan(admin, **payload):
    client, _, headers = admin
    body = {
        "id": "daily-ai",
        "job_name": "research/daily_ai",
        "schedule": "0 8 * * *",
        "timezone": "Asia/Shanghai",
    }
    body.update(payload)
    return client.post("/api/scheduler", headers=headers, json=body)


def test_a_created_plan_can_be_read_back(admin):
    """The round trip the recurring-plan capability depends on."""
    created = create_plan(admin)
    assert created.status_code == 200, created.text
    plan_id = created.json()["id"]

    client, _, _ = admin
    response = client.get(f"/api/scheduler/{plan_id}")

    assert response.status_code == 200, response.text
    body = response.json()

    # The fields a caller needs to confirm a schedule, and no others invented here.
    assert body["id"] == plan_id
    assert body["job_name"] == "research/daily_ai"
    assert body["schedule"] == "0 8 * * *"
    assert body["timezone"] == "Asia/Shanghai"
    assert body["enabled"] is True
    assert body["next_runs"], "an enabled plan must report when it will next fire"
    assert body["last_run"] is None, "a plan that has never run has no last run"
    assert body["backend"] == "managed"


def test_the_read_agrees_with_the_list(admin):
    """One plan read and the same plan inside the list must not disagree."""
    created = create_plan(admin)
    plan_id = created.json()["id"]

    client, _, _ = admin
    single = client.get(f"/api/scheduler/{plan_id}").json()
    listed = next(
        p for p in client.get("/api/scheduler").json()["jobs"] if p.get("id") == plan_id
    )

    for field in ("id", "job_name", "schedule", "timezone", "enabled", "next_runs"):
        assert single[field] == listed[field], field


def test_an_unknown_plan_is_not_found(admin):
    client, _, _ = admin
    response = client.get("/api/scheduler/does-not-exist")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_a_deleted_plan_is_not_found(admin):
    client, _, headers = admin
    plan_id = create_plan(admin).json()["id"]

    assert client.delete(f"/api/scheduler/{plan_id}", headers=headers).status_code == 200
    assert client.get(f"/api/scheduler/{plan_id}").status_code == 404


def test_reading_requires_the_admin_role(client, web_env):
    """The read is guarded exactly as the other scheduler routes are."""
    client.post("/api/auth/login", json={"username": "viewer", "password": "viewer-pass-123"})
    root, _ = web_env
    from web.services import managed_scheduler as service

    store = service.store()
    store.save("research/anything", "0 8 * * *")
    plan_id = store.list()[0]["id"]
    assert ScheduleStore is not None

    response = client.get(f"/api/scheduler/{plan_id}")

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


def test_reading_does_not_touch_cron(admin, monkeypatch):
    """A read must not consult or modify the legacy crontab.

    The guard is installed *after* the plan is created, and that ordering matters:
    `managed.save` deliberately reads the crontab to refuse a plan whose id collides
    with a legacy entry. Only the read is under test here.
    """
    from web.services import scheduler

    plan_id = create_plan(admin).json()["id"]

    monkeypatch.setattr(
        scheduler, "_get_crontab", lambda: pytest.fail("a read consulted cron")
    )
    monkeypatch.setattr(
        scheduler, "_set_crontab", lambda *_: pytest.fail("a read wrote cron")
    )

    client, _, _ = admin
    assert client.get(f"/api/scheduler/{plan_id}").status_code == 200


def test_a_disabled_plan_reports_no_upcoming_runs(admin):
    """A suspended schedule is still readable; it just has nothing scheduled."""
    client, _, headers = admin
    plan_id = create_plan(admin).json()["id"]

    toggled = client.put(
        f"/api/scheduler/{plan_id}/toggle", headers=headers, json={"enabled": False}
    )
    assert toggled.status_code == 200, toggled.text

    body = client.get(f"/api/scheduler/{plan_id}").json()
    assert body["enabled"] is False
    assert body["next_runs"] == []
