from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
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
