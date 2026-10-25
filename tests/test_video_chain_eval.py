"""The real-generation harness must preserve isolation and parallel receipts."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path

import pytest

from core import engine, events
from scripts import eval_video_chain as evaluation


def test_report_attempts_do_not_share_production_workspace_or_event_memory(tmp_path, monkeypatch):
    production = tmp_path / "production"
    production.mkdir()
    event_file = production / "state/events.db"
    event_file.parent.mkdir()
    event_file.write_bytes(b"Existing production event memory")
    original = hashlib.sha256(event_file.read_bytes()).hexdigest()
    monkeypatch.setattr(events, "DB_FILE", str(event_file))
    monkeypatch.setattr(evaluation, "ROOT", production)
    monkeypatch.setattr(evaluation, "load_job", lambda *a: {"name": "sample", "prompt": "task"})
    monkeypatch.setattr(evaluation.matrices, "for_run", lambda job, *a: job)
    monkeypatch.setattr(engine, "load_system_config", lambda *a: {})
    monkeypatch.setattr(evaluation.SampleEngine, "_build_prompt", lambda *a: "task")

    def run(self, key):
        report = Path(self.workspace_dir) / "output/report.md"
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text("# A real retrieval result\n\nhttps://sample.example/article")
        self._register_events(key, str(report), report.read_text())
        return self._output_reference(report)

    monkeypatch.setattr(engine.ResearchEngine, "run_job", run)
    first = evaluation.report_case(tmp_path / "samples", "science/daily")
    second = evaluation.report_case(tmp_path / "samples", "history/daily")
    for row in (first, second):
        assert row["status"] == "COMPLETED"
        assert Path(row["report"]).is_absolute()
        assert Path(row["report"]).is_relative_to(Path(row["work"]))
        assert (Path(row["work"]) / "state/events.db").is_file()
    assert first["report"] != second["report"]
    assert hashlib.sha256(event_file.read_bytes()).hexdigest() == original


def test_independent_receipt_writers_keep_each_others_results(tmp_path):
    index = tmp_path / "index.json"
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda i: evaluation.record(index, "reports", str(i), {"status": "COMPLETED"}), range(20)))
    assert set(json.loads(index.read_text())["reports"]) == {str(i) for i in range(20)}


def test_validation_output_cannot_enter_production_report_index(tmp_path, monkeypatch):
    monkeypatch.setattr(evaluation, "ROOT", tmp_path)
    with pytest.raises(ValueError, match="outside production reports"):
        evaluation.validation_output(tmp_path / "output/review")
    expected = tmp_path / "state/validation/review"
    assert evaluation.validation_output(expected) == expected


def test_validation_output_compatibility_link_resolves_to_isolated_root(tmp_path, monkeypatch):
    monkeypatch.setattr(evaluation, "ROOT", tmp_path)
    target = tmp_path / "state/validation/review"
    target.mkdir(parents=True)
    link = tmp_path / "output/review"
    link.parent.mkdir()
    link.symlink_to(target, target_is_directory=True)
    assert evaluation.validation_output(link) == target


def test_active_scope_includes_ungrouped_jobs_and_excludes_archived_and_generated(tmp_path, monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(evaluation, "ROOT", tmp_path)
    live = SimpleNamespace(key="live", archived=False, jobs=["live/daily", "live/disabled"])
    archived = SimpleNamespace(key="old", archived=True, jobs=["old/daily"])
    monkeypatch.setattr(evaluation.matrices, "load_all", lambda *a: {"live": live, "old": archived})
    monkeypatch.setattr(evaluation, "load_job", lambda root, key: {"enabled": key != "live/disabled"})
    monkeypatch.setattr(evaluation, "list_jobs", lambda *a, **kw: ["live/daily", "old/daily", "research/indie", "video/temporary"])
    assert evaluation.active_jobs() == {"live/daily": live, "research/indie": None}
