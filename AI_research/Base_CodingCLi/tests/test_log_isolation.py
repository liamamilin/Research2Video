"""Code under test never writes the console's real log: test lines there bury and rotate away real failures."""
from __future__ import annotations

import logging
import os
from pathlib import Path

from utils import logger as log_utils

ROOT = Path(__file__).resolve().parents[1]


def _file_handlers() -> list[str]:
    return [os.path.realpath(h.baseFilename) for h in logging.getLogger().handlers if getattr(h, "baseFilename", "")]


def _restore(before: list[logging.Handler]) -> None:
    root = logging.getLogger()
    for handler in list(root.handlers):
        if handler not in before:
            root.removeHandler(handler)
            handler.close()
    for handler in before:
        if handler not in root.handlers:
            root.addHandler(handler)


def test_the_override_wins_over_a_configured_relative_path(tmp_path, monkeypatch):
    target = tmp_path / "isolated.log"
    monkeypatch.setenv(log_utils.LOG_FILE_ENV, str(target))
    before = list(logging.getLogger().handlers)
    try:
        log_utils.attach_file_logging("logs/ai_research.log")
        log_utils.setup_logger(log_file="logs/ai_research.log")
        logging.getLogger("core.test").warning("isolated line")
        assert str(target.resolve()) in _file_handlers()
        assert not any(name.startswith(os.path.realpath(ROOT / "logs")) for name in _file_handlers())
    finally:
        _restore(before)
    assert "isolated line" in target.read_text(encoding="utf-8")


def test_the_suite_points_every_logger_away_from_the_real_logs():
    configured = os.environ[log_utils.LOG_FILE_ENV]
    assert not os.path.realpath(configured).startswith(os.path.realpath(ROOT / "logs"))


def test_the_cli_entry_point_logs_to_the_isolated_file(tmp_path, monkeypatch):
    import run as run_module
    from core import rounds as rounds_mod

    monkeypatch.setattr(rounds_mod, "STATE_FILE", str(tmp_path / "pipeline_rounds.json"))
    monkeypatch.setattr("sys.argv", ["run.py", "--round-start", "2026-09-24"])
    monkeypatch.setattr(run_module, "load_system_config", lambda *a, **k: {})
    before = list(logging.getLogger().handlers)
    try:
        try:
            run_module.main()
        except SystemExit:
            pass
        assert not any(name.startswith(os.path.realpath(ROOT / "logs")) for name in _file_handlers())
    finally:
        _restore(before)
