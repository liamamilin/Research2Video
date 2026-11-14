import json
from datetime import datetime

import pytest
import yaml

from core import production_policy as policy
from core.schedule_store import ScheduleStore
from core.search import SearchResult
from core.research_window import instruction, select


@pytest.fixture
def setup(tmp_path):
    config = tmp_path / "config"
    (config / "matrices").mkdir(parents=True)
    (config / "matrices/demo.yaml").write_text(
        "name: Demo\ndomain: Demo\njobs: [research/demo]\n"
    )
    value = {
        "schema_version": 1,
        "matrices": ["demo"],
        "window": {"mode": "previous_slot"},
        "collection": {"enabled": False},
        "automatic": {"daily_limit": 4},
    }
    path = config / policy.FILE
    path.write_text(yaml.safe_dump(value))
    store = ScheduleStore(str(tmp_path / "state"))
    return config, path, value, store, {"_file": "research/demo", "prompt": "Original"}


def test_queued_weekly_cutoff_does_not_move_with_actual_start(setup):
    config, path, value, store, job = setup
    due = datetime.fromisoformat("2026-11-02T02:40:00+08:00").timestamp()
    context = {
        "schedule_id": "x",
        "version": 1,
        "expression": "40 2 * * 1",
        "due_at": due,
        "timezone": "Asia/Shanghai",
    }
    actual = policy.issue(
        str(config), store.state_dir, job, datetime(2026, 11, 2, 8), context
    )
    assert actual["research_window"]["start"] == "2026-10-26T02:40:00+08:00"
    assert actual["research_window"]["end"] == "2026-11-02T02:40:00+08:00"
    assert (
        policy.issue(str(config), store.state_dir, job, datetime(2026, 11, 9), actual)
        == actual
    )


@pytest.mark.parametrize(
    "expression,cutoff,start",
    [
        ("40 2 * * *", "2026-11-02T02:40:00+08:00", "2026-11-01T02:40:00+08:00"),
        ("40 2 1 * *", "2026-03-01T02:40:00+08:00", "2026-02-01T02:40:00+08:00"),
        ("40 2 * * 1,4", "2026-11-05T02:40:00+08:00", "2026-11-02T02:40:00+08:00"),
    ],
)
def test_window_follows_any_existing_calendar_rule(setup, expression, cutoff, start):
    config, _, _, store, job = setup
    x = policy.issue(
        str(config),
        store.state_dir,
        job,
        datetime.now(),
        {
            "expression": expression,
            "due_at": datetime.fromisoformat(cutoff).timestamp(),
            "timezone": "Asia/Shanghai",
        },
    )
    assert (
        x["research_window"]["start"] == start and x["research_window"]["end"] == cutoff
    )


def test_retry_retains_job_window_and_policy_revision_after_edits(setup):
    config, path, value, store, job = setup
    base = datetime.fromisoformat("2026-11-02T02:39:00+08:00").timestamp()
    p = store.save(job["_file"], "40 2 * * 1", timezone="Asia/Shanghai", now=base)
    store.enqueue_due(base + 60)
    run = store.runs(p["id"])[0]
    original = store.freeze_context(
        run["id"],
        lambda c: policy.issue(str(config), store.state_dir, job, datetime.now(), c),
    )
    value["window"] = {"mode": "rolling", "days": 14}
    path.write_text(yaml.safe_dump(value))
    job["prompt"] = "Changed"
    retained = store.freeze_context(
        run["id"], lambda c: pytest.fail("Retry reinterpreted its policy")
    )
    assert retained == original and retained["job"]["prompt"] == "Original"
    fresh = policy.issue(str(config), store.state_dir, job, datetime(2026, 11, 9))
    assert (
        fresh["research_window"]["days"] == 14
        and fresh["policy_revision"] != original["policy_revision"]
    )


def test_manual_run_uses_same_calendar_cutoff(setup):
    config, _, _, store, job = setup
    base = datetime.fromisoformat("2026-11-02T02:39:00+08:00").timestamp()
    store.save(job["_file"], "40 2 * * 1", timezone="Asia/Shanghai", now=base)
    manual = policy.issue(str(config), store.state_dir, job, datetime(2026, 11, 2, 16))
    assert manual["research_window"]["end"] == "2026-11-02T02:40:00+08:00"


def test_flash_burst_uses_last_successful_cutoff_and_freezes_trigger_time(setup, monkeypatch):
    from core import state

    config, _, _, store, _ = setup
    job = {"_file": policy.FLASH_JOB, "prompt": "Flash"}
    (config / "matrices/demo.yaml").write_text(
        f"name: Demo\ndomain: Demo\njobs: [{policy.FLASH_JOB}]\n"
    )
    monkeypatch.setattr(state, "_STATE_DIR", store.state_dir)
    monkeypatch.setattr(state, "_HISTORY_DIR", store.state_dir + "/history")
    morning = datetime.fromisoformat("2026-11-14T07:30:00+08:00")
    triggered = datetime.fromisoformat("2026-11-14T16:36:37+08:00")
    plan = store.save(policy.FLASH_JOB, "30 7,18 * * *", timezone="Asia/Shanghai",
                      now=morning.timestamp())
    context = {"flash_triggered_at": triggered.timestamp()}
    fallback = policy.issue(str(config), store.state_dir, job, triggered, context)["research_window"]
    assert fallback["start"] == "2026-11-13T18:30:00+08:00"
    assert fallback["end"] == triggered.isoformat()
    state.StateManager.append_history(policy.FLASH_JOB, "success", usage={
        "collection_window": {"end": morning.isoformat()}
    })
    state.StateManager.append_history(policy.FLASH_JOB, "failed", usage={
        "collection_window": {"end": "2026-11-14T13:00:00+08:00"}
    })
    # Even if the worker does not start until after the evening slot, select as of the trigger.
    issued = policy.issue(str(config), store.state_dir, job, datetime(2026, 11, 14, 19), context)
    window = issued["research_window"]
    assert (window["start"], window["end"]) == (morning.isoformat(), triggered.isoformat())
    assert f"{morning.isoformat()} 至 {triggered.isoformat()}" in instruction(window)
    assert "近 1 天" not in instruction(window)
    reports = [
        SearchResult(title, f"https://example.com/{title}", when)
        for title, when in [
            ("morning", morning.isoformat()),
            ("afternoon", "2026-11-14T14:00:00+08:00"),
            ("later", "2026-11-14T17:00:00+08:00"),
        ]
    ]
    assert [report.title for report in select(reports, window)] == ["afternoon"]
    state.StateManager.append_history(policy.FLASH_JOB, "success", usage={"collection_window": window})
    later = datetime.fromisoformat("2026-11-14T17:15:00+08:00")
    second = policy.issue(str(config), store.state_dir, job, later, {"flash_triggered_at": later.timestamp()})
    assert second["research_window"]["start"] == triggered.isoformat()
    assert second["issue_id"] != issued["issue_id"]
    evening = datetime.fromisoformat("2026-11-14T18:30:00+08:00")
    scheduled = policy.issue(str(config), store.state_dir, job, later, {
        "due_at": evening.timestamp(), "expression": plan["schedule"], "timezone": plan["timezone"],
    })
    assert scheduled["research_window"]["start"] == morning.isoformat()
    assert scheduled["research_window"]["end"] == evening.isoformat()
    assert store.get(plan["id"])["next_run_at"] == evening.timestamp()


@pytest.mark.parametrize("failed_attempts", [50, 125])
def test_flash_cutoff_survives_a_long_run_of_failures(setup, failed_attempts):
    config, _, _, store, _ = setup
    (config / "matrices/demo.yaml").write_text(
        f"name: Demo\ndomain: Demo\njobs: [{policy.FLASH_JOB}]\n"
    )
    now = datetime.fromisoformat("2026-11-14T16:36:37+08:00")
    store.save(policy.FLASH_JOB, "30 7,18 * * *", timezone="Asia/Shanghai", now=now.timestamp())
    last_success = "2026-11-01T07:30:00+08:00"
    rows = [{"status": "success", "usage": {"collection_window": {"end": last_success}}}]
    rows += [{"status": "failed", "usage": None}] * failed_attempts
    history = config.parent / "state/history"
    history.mkdir(parents=True, exist_ok=True)
    (history / "monitoring_ai_flash_discovery.jsonl").write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n"
    )
    issued = policy.issue(str(config), store.state_dir, {"_file": policy.FLASH_JOB}, now,
                          {"flash_triggered_at": now.timestamp()})
    assert issued["research_window"]["start"] == last_success
    assert issued["research_window"]["end"] == now.isoformat()


def test_legacy_run_is_not_rewritten(setup):
    _, _, _, store, job = setup
    base = datetime.fromisoformat("2026-11-02T02:39:00+08:00").timestamp()
    p = store.save(job["_file"], "40 2 * * 1", timezone="Asia/Shanghai", now=base)
    store.enqueue_due(base + 60)
    run = store.runs(p["id"])[0]
    with store.connect(True) as db:
        db.execute(
            "UPDATE schedule_runs SET context='{}',created_at=0 WHERE id=?",
            (run["id"],),
        )
    assert store.freeze_context(run["id"], lambda c: pytest.fail("Legacy changed")) == {
        "legacy": True
    }


def test_new_worker_recovers_identity_from_older_dispatcher(setup):
    config, _, _, store, job = setup
    base = datetime.fromisoformat("2026-11-02T02:39:00+08:00").timestamp()
    p = store.save(job["_file"], "40 2 * * 1", timezone="Asia/Shanghai", now=base)
    store.enqueue_due(base + 60)
    run = store.runs(p["id"])[0]
    with store.connect(True) as db:
        db.execute("UPDATE schedule_runs SET context='{}' WHERE id=?", (run["id"],))
        # The schema migration is stamped with the real clock; place it before this fixed-date run, or the run
        # reads as legacy once the real date passes 2026-11-02 (the test failed from then on).
        db.execute("UPDATE scheduler_meta SET value=? WHERE key='issue_context_enabled_at'", (str(base - 3600),))
    result = store.freeze_context(
        run["id"],
        lambda c: policy.issue(str(config), store.state_dir, job, datetime.now(), c),
    )
    assert result["research_window"]["end"] == "2026-11-02T02:40:00+08:00"


@pytest.mark.parametrize(
    "change",
    [
        {"automatic": {"daily_limit": 0}},
        {"automatic": {"daily_limit": True}},
        {"window": {"mode": "rolling", "days": "7"}},
        {"collection": {"enabled": True, "schedule": "bad"}},
        {"unexpected": True},
    ],
)
def test_invalid_policy_is_rejected(setup, change):
    _, _, value, _, _ = setup
    with pytest.raises((ValueError, KeyError)):
        policy.validate({**value, **change})


def test_enqueue_freezes_job_and_edit_only_changes_future_issues(setup):
    config, path, value, store, job = setup
    jobs = config.parent / "jobs/research"
    jobs.mkdir(parents=True)
    source = jobs / "demo.yaml"
    source.write_text("prompt: Original\nenabled: true\n")
    base = datetime.fromisoformat("2026-11-02T02:39:00+08:00").timestamp()
    plan = store.save(job["_file"], "40 2 * * 1", timezone="Asia/Shanghai", now=base)
    store.enqueue_due(base + 60)
    run = store.runs(plan["id"])[0]
    original = json.loads(run["context"])
    assert original["job"]["prompt"] == "Original"
    source.write_text("prompt: Changed\n")
    value["window"] = {"mode": "rolling", "days": 14}
    path.write_text(yaml.safe_dump(value))
    store.save(
        job["_file"],
        "40 2 * * *",
        schedule_id=plan["id"],
        editing=True,
        timezone="Asia/Shanghai",
        now=base + 120,
    )
    assert store.run(run["id"])["status"] == "queued"
    claimed = store.claim(base + 120)
    assert claimed["id"] == run["id"] and store.start(run["id"], 1, base + 120)
    store.finish(run["id"], "failed", "retry", now=base + 121)
    assert store.run(run["id"])["status"] == "retry"
    assert json.loads(store.run(run["id"])["context"]) == original
    store.toggle(plan["id"], False, base + 122)
    assert (
        store.run(run["id"])["status"] == "skipped"
    )  # Explicit user pause still fences accepted work.
