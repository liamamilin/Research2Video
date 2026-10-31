import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from g2 import run_g2
from g2.render_retry import run


def setup(tmp_path, failures):
    commands = []
    logs = tmp_path / "logs"
    logs.mkdir()

    def command(label, argv, cwd, timeout):
        commands.append((label, argv, timeout))
        output = Path(argv[5])
        output.write_bytes(b"partial" if len(commands) <= len(failures) else b"completed")
        message = failures[len(commands)-1] if len(commands) <= len(failures) else "completed"
        (logs / f"{label}.log").write_text(message)
        if message != "completed":
            raise run_g2.G2Fault("COMMAND_FAILED", "render failed")

    def write_json(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))

    pipeline = SimpleNamespace(ARTIFACTS=tmp_path / "artifacts", LOGS=logs,
        G2Fault=run_g2.G2Fault, _run_fixed_command=command, write_json=write_json)
    target = tmp_path / "video.mp4"
    argv = ["node", "cli", "render", "src/index.ts", "Composition", str(target),
            "--concurrency=2", "--codec=h264", "--crf=18", "--frames=1348-1847"]
    return pipeline, argv, target, commands


FRAME_TIMEOUT = 'A delayRender() "Setting the current frame to 1688" was called but not cleared after 28000ms.'


def test_frame_timeout_recovers_once_without_overwriting_failed_output(tmp_path):
    pipeline, argv, target, commands = setup(tmp_path, [FRAME_TIMEOUT])
    run(pipeline, "remotion-render", argv, tmp_path, timeout=1200, resources={"concurrency": 2})
    assert len(commands) == 2 and target.read_bytes() == b"completed"
    assert "--concurrency=1" in commands[1][1] and "--timeout=60000" in commands[1][1]
    for _, args, timeout in commands:
        assert {"--codec=h264", "--crf=18", "--frames=1348-1847"} <= set(args)
        assert timeout == 1200
    folder = pipeline.ARTIFACTS / "render-attempts/remotion-render"
    assert (folder / "attempt-1.mp4").read_bytes() == b"partial"
    report = json.loads((folder / "recovery.json").read_text())
    assert report["status"] == "completed"
    assert [r["status"] for r in report["attempts"]] == ["failed", "completed"]


@pytest.mark.parametrize("error, count", [(FRAME_TIMEOUT, 2), ("Invalid props: unknown component", 1)])
def test_retry_is_bounded_and_does_not_repeat_content_errors(tmp_path, error, count):
    pipeline, argv, target, commands = setup(tmp_path, [error, error])
    with pytest.raises(run_g2.G2Fault):
        run(pipeline, "remotion-render", argv, tmp_path, timeout=1200, resources={"concurrency": 2})
    assert len(commands) == count and not target.exists()
    report = json.loads((pipeline.ARTIFACTS / "render-attempts/remotion-render/recovery.json").read_text())
    assert report["status"] == "failed"


def test_success_uses_one_attempt_and_rejects_an_existing_final_output(tmp_path):
    pipeline, argv, target, commands = setup(tmp_path, [])
    run(pipeline, "remotion-render", argv, tmp_path, timeout=1200, resources={"concurrency": 1})
    assert len(commands) == 1
    with pytest.raises(run_g2.G2Fault, match="already exists"):
        run(pipeline, "remotion-render", argv, tmp_path, timeout=1200, resources={"concurrency": 1})
    assert target.read_bytes() == b"completed"
