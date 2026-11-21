from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import sqlite3
from types import SimpleNamespace

import yaml

from core.video.production_admission import Ledger, enter

NOW = datetime.fromisoformat("2026-10-31T02:00:00+08:00").timestamp()
RULE = {"daily_limit": 4, "timezone": "Asia/Shanghai"}


def test_report_retry_reuses_reservation_but_new_report_revisions_count(tmp_path):
    config = tmp_path / "config"
    (config / "matrices").mkdir(parents=True)
    (config / "matrices/demo.yaml").write_text("name: Demo\njobs: [demo]\n")
    (config / "production_policy.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "matrices": ["demo"],
                "window": {"mode": "previous_slot"},
                "automatic": {"daily_limit": 1},
            }
        )
    )
    paths = SimpleNamespace(
        config_dir=str(config),
        jobs_dir=str(tmp_path / "jobs"),
        state_dir=str(tmp_path / "state"),
    )
    topic = {
        "_production": "demo",
        "episode": {"job": "demo", "report_path": "report.md", "report_identity": "v1"},
    }
    assert enter(paths, topic, key="item")
    assert enter(paths, topic, key="render-retry")
    assert not enter(
        paths,
        {**topic, "episode": {**topic["episode"], "report_identity": "v2"}},
        key="new",
    )


def test_concurrent_producers_share_daily_allowance_and_retries_reuse_it(tmp_path):
    ledger = Ledger(str(tmp_path), now=NOW - 100)
    with ThreadPoolExecutor(8) as pool:
        values = list(
            pool.map(lambda n: ledger.enter(str(n), RULE, "v1", now=NOW), range(12))
        )
    assert sum(values) == 4
    accepted = str(next(i for i, v in enumerate(values) if v))
    assert Ledger(str(tmp_path)).enter(accepted, RULE, "changed", now=NOW + 86400)
    assert (
        sum(
            ledger.enter("tomorrow" + str(n), RULE, "v1", now=NOW + 86400)
            for n in range(6)
        )
        == 4
    )


def test_old_accepted_tasks_and_explicit_manual_batches_keep_running(tmp_path):
    ledger = Ledger(str(tmp_path), now=NOW)
    assert all(
        ledger.enter("old" + str(n), RULE, "v1", accepted_before=NOW - 1, now=NOW)
        for n in range(10)
    )
    assert all(
        ledger.enter("manual" + str(n), RULE, "v1", manual=True, now=NOW)
        for n in range(10)
    )
    assert (
        sum(ledger.enter("auto" + str(n), RULE, "v1", now=NOW) for n in range(6)) == 4
    )


def test_policy_changes_adjust_future_allowance_without_recounting_existing(tmp_path):
    ledger = Ledger(str(tmp_path), now=NOW - 1)
    assert ledger.enter("a", RULE, "v1", now=NOW)
    assert not ledger.enter("b", {**RULE, "daily_limit": 1}, "v2", now=NOW)
    assert ledger.enter("b", {**RULE, "daily_limit": 2}, "v3", now=NOW)


def test_no_daily_limit_admits_every_issue_once(tmp_path):
    ledger = Ledger(str(tmp_path), now=NOW - 10)
    unlimited = {**RULE, "daily_limit": None}
    assert all(ledger.enter(f"issue-{i}", unlimited, "v1", now=NOW + i) for i in range(12))
    assert ledger.enter("issue-0", unlimited, "v1", now=NOW + 20)  # a retry is the same issue
    with ledger.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM admissions").fetchone()[0] == 12


def test_job_daily_limit_counts_videos_across_reports_without_using_other_jobs_slots(tmp_path, monkeypatch):
    config = tmp_path / "config"
    jobs = tmp_path / "jobs"
    state = tmp_path / "state"
    (config / "matrices").mkdir(parents=True)
    (config / "matrices/demo.yaml").write_text("name: Demo\njobs: [tutorial, news]\n")
    (config / "production_policy.yaml").write_text(
        "schema_version: 1\nmatrices: [demo]\nwindow: {mode: previous_slot}\nautomatic: {daily_limit: null}\n"
    )
    jobs.mkdir()
    (jobs / "tutorial.yaml").write_text(
        "name: 教程\noutput: output/{date}_tutorial.md\ntopics: {format: items, prefix: ATU, daily_limit: 3}\n"
    )
    state.mkdir()
    with sqlite3.connect(state / "production_admission.db") as db:
        db.execute("CREATE TABLE admissions (issue TEXT PRIMARY KEY, day TEXT, accepted_at REAL, policy_revision TEXT, mode TEXT)")
        db.execute("INSERT INTO admissions VALUES ('old','2026-10-31',?,'v1','automatic')", (NOW,))
    paths = SimpleNamespace(config_dir=str(config), jobs_dir=str(jobs), state_dir=str(state))
    clock = [NOW]
    monkeypatch.setattr("core.video.production_admission.time.time", lambda: clock[0])

    def item(number, report):
        return {
            "id": f"topic-{number}",
            "from_item": f"topic-{number}",
            "from_report": report,
            "matrix_dedup": {"job": "tutorial"},
        }

    assert enter(paths, {"id": "news-1", "matrix_dedup": {"job": "news"}}, key="other-job")
    assert all(enter(paths, item(n, "first.md"), key=f"write-{n}") for n in range(1, 4))
    assert enter(paths, item(1, "first.md"), key="render-1")
    assert not enter(paths, item(4, "rerun.md"), key="rerun")
    assert enter(paths, item(5, "rerun.md"), key="manual", manual=True)
    clock[0] += 86400
    assert enter(paths, item(4, "rerun.md"), key="rerun")
    with Ledger(str(state)).connect() as db:
        assert db.execute("SELECT mode FROM admissions WHERE issue='old'").fetchone() == ("automatic",)
        assert db.execute("SELECT COUNT(*) FROM admissions WHERE job='tutorial' AND mode='automatic'").fetchone()[0] == 4
