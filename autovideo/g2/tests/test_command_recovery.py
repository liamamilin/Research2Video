import json
from pathlib import Path
import sys

import pytest

from g2 import run_g2 as pipeline


@pytest.fixture
def command_env(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "PROJECT", tmp_path)
    monkeypatch.setattr(pipeline, "PYTHON", Path(sys.executable))
    monkeypatch.setattr(pipeline, "LOGS", tmp_path / "logs")
    monkeypatch.setattr(pipeline, "COMMANDS_PATH", tmp_path / "logs/commands.jsonl")
    return tmp_path


def test_command_timeout_keeps_partial_output_and_structured_failure(command_env):
    with pytest.raises(pipeline.G2Fault) as error:
        pipeline._run_fixed_command("slow", [sys.executable, "-u", "-c",
            "import time; print('last completed frame', flush=True); time.sleep(30)"], command_env, timeout=.2)
    assert error.value.code == "COMMAND_TIMEOUT"
    assert "last completed frame" in (command_env / "logs/slow.log").read_text()
    record = json.loads((command_env / "logs/commands.jsonl").read_text())
    assert record["error_code"] == "COMMAND_TIMEOUT" and record["timeout_seconds"] == .2
    assert record["elapsed_seconds"] < 5


def test_command_output_and_nonzero_failure_are_both_logged(command_env):
    assert pipeline._run_fixed_command("ok", [sys.executable, "-c", "print('probe data')"], command_env) == "probe data\n"
    with pytest.raises(pipeline.G2Fault) as error:
        pipeline._run_fixed_command("bad", [sys.executable, "-c", "print('decode error'); raise SystemExit(3)"], command_env)
    assert error.value.code == "COMMAND_FAILED"
    assert (command_env / "logs/bad.log").read_text().strip() == "decode error"
    records = [json.loads(line) for line in (command_env / "logs/commands.jsonl").read_text().splitlines()]
    assert [r["exit_code"] for r in records] == [0, 3]
