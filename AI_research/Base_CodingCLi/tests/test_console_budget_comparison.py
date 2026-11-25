"""The regression checker must detect real cost increases and tolerate noisy clocks."""
from copy import deepcopy
import json
import sys
from types import SimpleNamespace

import pytest

from scripts.check_console_performance import compare


def report():
    return {"fixture_sha256": "fixed", "cases": {"scripts": {
        "tab": "文稿", "path": "/api/video/scripts?compact=true", "samples_ms": [99, 100, 101],
        "median_ms": 100, "mad_ms": 1, "decoded_bytes": [10000], "wire_bytes": [1000],
        "sql_selects": [8], "connections": [5], "schema_statements": [0],
    }}}


def test_flags_payload_query_connection_and_schema_regressions():
    baseline = report()
    for metric, value in [("decoded_bytes", 30000), ("wire_bytes", 3000), ("sql_selects", 600),
                          ("connections", 600), ("schema_statements", 1)]:
        current = deepcopy(baseline)
        current["cases"]["scripts"][metric] = [value]
        failures, _ = compare(baseline, current)
        assert any(metric in item and "文稿" in item for item in failures)


def test_sustained_slowdown_warns_but_noisy_samples_get_a_wider_envelope():
    baseline, current = report(), report()
    current["cases"]["scripts"].update(median_ms=300, mad_ms=1, samples_ms=[299, 300, 301])
    failures, warnings = compare(baseline, current)
    assert not failures and len(warnings) == 1
    current["cases"]["scripts"]["mad_ms"] = 100
    assert compare(baseline, current) == ([], [])


def test_removed_cases_or_changed_dataset_cannot_pass_silently():
    current = report()
    current["cases"].clear()
    assert compare(report(), current)[0]
    current = report()
    current["fixture_sha256"] = "different-data"
    assert compare(report(), current)[0]


@pytest.mark.parametrize("mode,strict,expected", [("size", False, 1), ("time", False, 0), ("time", True, 1)])
def test_command_exit_status_and_reviewed_baseline_are_preserved(tmp_path, monkeypatch, mode, strict, expected):
    from scripts import check_console_performance as check
    baseline, current = report(), report()
    if mode == "size":
        current["cases"]["scripts"]["decoded_bytes"] = [30000]
    else:
        current["cases"]["scripts"].update(median_ms=300, mad_ms=1, samples_ms=[299, 300, 301])
    target = tmp_path / "baseline.json"
    target.write_text(json.dumps(baseline))
    (tmp_path / "ui/node_modules").mkdir(parents=True)
    monkeypatch.setattr(check, "ROOT", tmp_path)
    monkeypatch.setattr(check.platform, "platform", lambda: "fixture-platform")
    monkeypatch.setitem(sys.modules, "trial_storage", SimpleNamespace(trial_directory=lambda *args: tmp_path))

    def run(command, **kwargs):
        if "AREC_PERF_REPORT" in kwargs.get("env", {}):
            from pathlib import Path
            Path(kwargs["env"]["AREC_PERF_REPORT"]).write_text(json.dumps(current))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(check.subprocess, "run", run)
    monkeypatch.setattr(sys, "argv", ["check_console_performance.py", "--baseline", str(target),
                                     *(["--strict-timing"] if strict else [])])
    assert check.main() == expected
    assert json.loads(target.read_text()) == baseline
