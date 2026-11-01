import json
from datetime import datetime

import pytest
import yaml

from core import production_policy as policy
from core.schedule_store import ScheduleStore


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
