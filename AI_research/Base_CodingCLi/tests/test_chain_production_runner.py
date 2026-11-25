"""The chain observer must share, rather than duplicate, native video work."""
# ruff: noqa: F811 -- imported pytest fixtures are bound by argument name.

from core.video import autopilot_store as ap
from tests.test_video_scripts import video_env  # noqa: F401
from web.services import chain_runner, video_autopilot


def test_same_report_handoff_reuses_native_batch(video_env, monkeypatch):
    monkeypatch.setattr(
        video_autopilot,
        "_start_run",
        lambda spec, **kw: ap.create_run(spec, [], started_by=kw["started_by"]),
    )
    automatic = {
        "job": "monitoring/ai_flash_discovery",
        "report": "output/flash/a.md",
        "report_identity": "frozen-report-revision",
        "picks": [{"production": "flash_each"}],
    }
    first = video_autopilot.start_run(automatic, started_by="autopilot")
    manual = video_autopilot.start_run(
        {**automatic, "chain_run_id": "chain-run"}, started_by="admin"
    )
    assert manual["id"] == first["id"]
    assert len(ap.list_runs(limit=None)) == 1


def test_observer_never_advances_the_video_worker(monkeypatch):
    batch = {"id": "existing-batch", "status": "RUNNING", "items": []}
    monkeypatch.setattr(ap, "get_run", lambda _id: batch)
    monkeypatch.setattr(
        video_autopilot,
        "_tick_run",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("second worker")),
    )
    monkeypatch.setattr(chain_runner, "put", lambda row: None)
    row = {
        "id": "chain-run",
        "status": "RUNNING",
        "stage": "VIDEO",
        "batch_id": batch["id"],
        "outputs": ["first"],
    }
    chain_runner.advance(row)
    assert row["status"] == "RUNNING"
    batch.update(status="DONE", items=[{"state": "DONE", "production": "first"}])
    chain_runner.advance(row)
    assert row["status"] == "DONE"


def test_restarted_research_does_not_consume_another_runs_last_report(
    web_env, monkeypatch
):
    from core import report_meta, state
    from web.runner.registry import TaskRegistry

    monkeypatch.setattr(TaskRegistry, "get", lambda _self, _job: None)
    monkeypatch.setattr(
        report_meta,
        "load_all",
        lambda: {"other.md": {"chain_run_id": "other-run", "path": "output/other.md"}},
    )
    monkeypatch.setattr(
        state.StateManager,
        "get",
        lambda _job: (_ for _ in ()).throw(AssertionError("unsafe last_output")),
    )
    monkeypatch.setattr(chain_runner, "put", lambda row: None)
    row = {
        "id": "our-run",
        "job": "same-job",
        "task_id": "original-task",
        "status": "RUNNING",
        "stage": "RESEARCH",
    }
    chain_runner.advance(row)
    assert row["status"] == "INTERRUPTED"
    assert "report" not in row


def test_native_database_busy_does_not_spend_batch_retries(video_env, monkeypatch):
    import sqlite3
    from web.services import theme_workbench

    for name in ("fire_due_plans", "fire_auto", "reopen_approved"):
        monkeypatch.setattr(video_autopilot, name, lambda: 0)
    monkeypatch.setattr(video_autopilot.story_pool, "reconcile", lambda: None)
    monkeypatch.setattr(theme_workbench, "enqueue_recommendations", lambda: 0)
    batch = ap.create_run({"picks": []}, [], started_by="admin")
    monkeypatch.setattr(
        video_autopilot,
        "_tick_run",
        lambda *_a: (_ for _ in ()).throw(
            sqlite3.OperationalError("database is locked")
        ),
    )
    for _ in range(4):
        video_autopilot.tick()
    current = ap.get_run(batch["id"])
    assert current["status"] == "RUNNING"
    assert current.get("dispatch_failures", 0) == 0


def test_stopping_one_chain_keeps_other_outputs_running(video_env):
    batch = ap.create_run(
        {"picks": [{"production": "first"}, {"production": "other"}]},
        [
            {"column_key": "flash", "topic": {"id": "one"}, "production": "first"},
            {"column_key": "flash", "topic": {"id": "two"}, "production": "other"},
        ],
        started_by="admin",
    )
    row = {
        "id": "partial-stop",
        "chain_id": "first-chain",
        "job": "test-job",
        "status": "RUNNING",
        "stage": "VIDEO",
        "outputs": ["first"],
        "batch_id": batch["id"],
    }
    chain_runner.put(row)
    chain_runner.cancel(row["id"])
    updated = ap.get_run(batch["id"])
    assert updated["status"] == "RUNNING"
    assert [(i["production"], i["state"]) for i in updated["items"]] == [
        ("first", "CANCELLED"),
        ("other", "PENDING"),
    ]
    chain_runner.advance(chain_runner.get(row["id"]))
    assert chain_runner.get(row["id"])["status"] == "CANCELLED"


def test_legacy_schedule_retry_does_not_invent_an_original_window(monkeypatch):
    import pytest
    from fastapi import HTTPException
    from web.services import chains

    row = {
        "id": "schedule-old",
        "chain_id": "legacy",
        "job": "legacy/job",
        "status": "FAILED",
        "stage": "RESEARCH",
        "native_schedule": True,
        "snapshot": {"legacy": True},
    }
    monkeypatch.setattr(chain_runner, "get", lambda _: row)
    monkeypatch.setattr(chains, "active_for_job", lambda _: [])
    monkeypatch.setattr(
        chain_runner, "start_job", lambda *_a, **_k: pytest.fail("must not reinterpret")
    )
    with pytest.raises(HTTPException, match="原周期缺少冻结"):
        chain_runner.retry(row["id"], {"username": "admin"})


def test_historical_completed_batch_with_skipped_item_is_not_a_stopped_run(
    web_env, monkeypatch
):
    from web.services import chains

    row = {
        "id": "historical-outcome",
        "chain_id": "history-chain",
        "job": "demo",
        "outputs": ["first"],
        "status": "RUNNING",
        "stage": "VIDEO",
        "batch_id": "native",
        "historical": True,
        "started_at": 1,
    }
    chains.save_run(row)
    batch = {
        "id": "native",
        "status": "DONE",
        "items": [
            {"state": "DONE", "production": "first"},
            {"state": "CANCELLED", "production": "first"},
        ],
    }
    monkeypatch.setattr(ap, "get_run", lambda _: batch)
    assert chain_runner.get(row["id"])["status"] == "DONE"
    batch["status"] = "CANCELLED"
    assert chain_runner.get(row["id"])["status"] == "CANCELLED"
