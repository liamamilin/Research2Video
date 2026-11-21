"""Repeatable console regression check. No production data, workers or network calls.

Default: check reviewed byte/query budgets; noisy timing changes are warnings.
--strict-timing also fails sustained timing regressions on comparable hardware.
--record-baseline explicitly records a new baseline after reviewing the changes.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import platform
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "tests/fixtures/console_performance_baseline.json"


def timing_limit(baseline: dict, current: dict) -> float:
    """A generous measured envelope: p95 headroom and robust within-run noise."""
    samples = sorted(baseline["samples_ms"])
    p95 = samples[min(len(samples) - 1, math.ceil(len(samples) * .95) - 1)]
    return max(p95 * 1.5, baseline["median_ms"] + 6 * max(baseline["mad_ms"], current["mad_ms"]))


def compare(baseline: dict, current: dict) -> tuple[list[str], list[str]]:
    """Deterministic costs fail; hardware-sensitive wall time is reported separately."""
    failures, warnings = [], []
    if baseline.get("fixture_sha256") != current.get("fixture_sha256"):
        return ["固定数据定义已变化：请核对规模并显式重测基线。"], []
    if set(baseline["cases"]) != set(current["cases"]):
        return ["接口集合与基线不一致：不允许漏测页面后通过。"], []
    for key, row in current["cases"].items():
        old = baseline["cases"][key]
        label = f"{row['tab']} [{key}] {row['path']}"
        for metric in ("decoded_bytes", "wire_bytes", "sql_selects", "connections", "schema_statements"):
            before, after = max(old[metric]), max(row[metric])
            # Allow modest JSON additions and gzip entropy, but no extra queries
            # or repeated schema work on this fixed workload without review.
            limit = math.ceil(before * 1.10) if metric.endswith("bytes") else before
            if after > limit:
                failures.append(f"{label}: {metric} {before} → {after}，预算 {limit}")
        limit_ms = timing_limit(old, row)
        if row["median_ms"] > limit_ms:
            warnings.append(f"{label}: 中位耗时 {old['median_ms']:.1f} → {row['median_ms']:.1f} ms，"
                            f"实测容忍区间上沿 {limit_ms:.1f} ms；同机空闲时复测。")
    return failures, warnings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record-baseline", action="store_true")
    parser.add_argument("--strict-timing", action="store_true")
    parser.add_argument("--baseline", type=Path, default=BASELINE)
    parser.add_argument("--samples", type=int, choices=range(3, 16), default=5)
    parser.add_argument("--rounds", type=int, choices=range(1, 6))
    args = parser.parse_args()
    from trial_storage import trial_directory

    try:
        folder = trial_directory("research", "console-performance")
    except (ValueError, OSError) as exc:
        print(f"性能检查未执行：{exc}", file=sys.stderr)
        return 2
    run = folder / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8])
    run.mkdir()
    report = run / "measurements.json"
    env = {**os.environ, "AREC_PERF_REPORT": str(report), "AREC_PERF_SAMPLES": str(args.samples),
           "AREC_PERF_ROUNDS": str(args.rounds or (3 if args.record_baseline else 1))}
    command = [sys.executable, "-m", "pytest", "tests/test_console_performance_budget.py", "-q", "-s",
               "--basetemp", str(run / "data")]
    print(f"固定数据接口检查；证据：{run}", flush=True)
    with (run / "backend.log").open("w") as log:
        status = subprocess.run(command, cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT).returncode
    if status:
        print((run / "backend.log").read_text()[-8000:])
        return status
    if not (ROOT / "ui/node_modules").is_dir():
        print("前端依赖未安装，无法核对页面切换和请求次数。请先按 README 安装依赖。", file=sys.stderr)
        return 2
    print("检查前端首次进入、切回、重复请求和数据更新…", flush=True)
    with (run / "frontend.log").open("w") as log:
        status = subprocess.run(["npm", "run", "test:performance"], cwd=ROOT / "ui",
                                stdout=log, stderr=subprocess.STDOUT).returncode
    if status:
        print((run / "frontend.log").read_text()[-8000:])
        return status
    current = json.loads(report.read_text())
    current["environment"] = {"python": platform.python_version(), "platform": platform.platform(),
                              "recorded_at": datetime.now(timezone.utc).isoformat()}
    report.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n")
    if args.record_baseline:
        args.baseline.parent.mkdir(parents=True, exist_ok=True)
        if args.baseline.exists():
            previous = json.loads(args.baseline.read_text())
            if previous.get("fixture_sha256") == current["fixture_sha256"]:
                failures, warnings = compare(previous, current)
                for message in failures + warnings:
                    print(f"基线变化：{message}")
        args.baseline.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n")
        print(f"已显式记录基线：{args.baseline}；样本数 {args.samples * int(env['AREC_PERF_ROUNDS'])}/接口。")
        return 0
    if not args.baseline.is_file():
        print("缺少性能基线。核对本次结果后运行 --record-baseline；检查不会自动接受当前结果。", file=sys.stderr)
        return 2
    baseline = json.loads(args.baseline.read_text())
    failures, warnings = compare(baseline, current)
    for key, row in current["cases"].items():
        old = baseline["cases"].get(key)
        if old:
            print(f"{row['tab']} [{key}]: {old['median_ms']:.1f} → {row['median_ms']:.1f} ms；"
                  f"JSON {max(old['decoded_bytes'])} → {max(row['decoded_bytes'])} B；"
                  f"SELECT {max(old['sql_selects'])} → {max(row['sql_selects'])}")
    for message in failures:
        print(f"FAIL {message}")
    for message in warnings:
        print(f"WARN {message}")
    summary = {"failures": failures, "timing_warnings": warnings, "strict_timing": args.strict_timing}
    (run / "comparison.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    if failures or (args.strict_timing and warnings):
        return 1
    print(f"性能回归检查通过；{len(warnings)} 项耗时提示。完整证据：{run}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
