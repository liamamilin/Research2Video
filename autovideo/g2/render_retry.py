"""One bounded recovery for a Remotion frame timeout, keeping every attempt."""
from __future__ import annotations

import re
from pathlib import Path


def frame_timeout(log: Path) -> bool:
    try:
        with log.open("rb") as stream:
            stream.seek(0, 2)
            stream.seek(max(0, stream.tell() - 65536))
            tail = stream.read().decode("utf-8", errors="replace")
    except OSError:
        return False
    return bool(re.search(r"delayRender\(\).*?not cleared after\s+\d+\s*ms", tail, re.DOTALL))


def run(pipeline, label: str, argv: list, cwd: Path, *, timeout: int, resources: dict) -> None:
    target = Path(argv[5])
    if target.exists():
        raise pipeline.G2Fault("RENDER_OUTPUT_EXISTS", f"render output already exists: {target}")
    folder = pipeline.ARTIFACTS / "render-attempts" / label
    folder.mkdir(parents=True, exist_ok=True)
    report = {"max_attempts": 2, "attempts": [], "status": "running"}
    report_path = folder / "recovery.json"
    for index in range(2):
        attempt_label = label if index == 0 else f"{label}-retry-1"
        output = folder / f"attempt-{index + 1}.mp4"
        concurrency = resources["concurrency"] if index == 0 else 1
        frame_timeout_ms = 30000 if index == 0 else 60000
        args = [str(arg) for arg in argv if not str(arg).startswith(("--concurrency=", "--timeout="))]
        args[5] = str(output)
        args.extend([f"--concurrency={concurrency}", f"--timeout={frame_timeout_ms}"])
        record = {"attempt": index + 1, "concurrency": concurrency, "frame_timeout_ms": frame_timeout_ms,
                  "log": str(pipeline.LOGS / f"{attempt_label}.log"), "output": str(output), "status": "running"}
        report["attempts"].append(record)
        pipeline.write_json(report_path, report)
        try:
            pipeline._run_fixed_command(attempt_label, args, cwd, timeout=timeout)
            if not output.is_file():
                raise pipeline.G2Fault("RENDER_OUTPUT_MISSING", "renderer exited without producing the video")
        except pipeline.G2Fault as exc:
            retryable = exc.code == "COMMAND_FAILED" and frame_timeout(Path(record["log"]))
            record.update(status="failed", error_code=exc.code, retryable_frame_timeout=retryable)
            report["status"] = "retrying" if index == 0 and retryable else "failed"
            pipeline.write_json(report_path, report)
            if index == 0 and retryable:
                print("RENDER RECOVERY: frame timeout; retrying once with one browser and a 60s frame deadline", flush=True)
                continue
            raise
        record["status"] = "completed"
        target.parent.mkdir(parents=True, exist_ok=True)
        output.replace(target)
        report.update(status="completed", output=str(target))
        pipeline.write_json(report_path, report)
        return
