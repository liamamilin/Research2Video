"""The module's own background thread (spec §2 rule 1): checks that run on a timer, kept out of existing loops.

Phase 1 runs only the reconciliation: assignment tokens are matched to the scripts that carry them. Existing data is
opened read-only (``mode=ro``), so a bug here can never write ``video.db``.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import subprocess
import threading
from pathlib import Path

from core.experiments.store import ExperimentStore

logger = logging.getLogger(__name__)

INTERVAL = 600.0
PREVIEW_INTERVAL = 30.0
_started = threading.Lock()
_thread: threading.Thread | None = None
_code_version: str | None = None


def code_version(repo: str | Path | None = None) -> str:
    """The commit the running process started from, read once (§7, §11). ``未知`` when git cannot say."""
    global _code_version
    if _code_version is None:
        try:
            out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=repo or Path(__file__).resolve().parent,
                                 capture_output=True, text=True, timeout=5)
            _code_version = out.stdout.strip() if out.returncode == 0 and out.stdout.strip() else "未知"
        except (OSError, subprocess.SubprocessError):
            _code_version = "未知"
    return _code_version


_diffs: dict[tuple[str, str], list[str]] = {}


def changed_files(old: str, new: str, files: list[str]) -> list[str]:
    """Which of ``files`` differ between two commits (``git diff --name-only``); empty when git cannot say."""
    key = (old, new)
    if key not in _diffs:
        try:
            out = subprocess.run(["git", "diff", "--name-only", "--relative", old, new, "--", *files],
                                 cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, timeout=10)
            _diffs[key] = [line for line in out.stdout.splitlines() if line.strip()] if out.returncode == 0 else []
        except (OSError, subprocess.SubprocessError):
            _diffs[key] = []
    return [f for f in _diffs[key] if f in files]


def tokens_on_scripts(video_db: str | Path, since: float) -> dict[str, str]:
    """token → script id, from scripts created at or after ``since`` (read-only)."""
    path = Path(video_db)
    if not path.is_file():
        return {}
    import time
    # the same local format scripts are stamped with (core/video/store.py _now), so text comparison orders them
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(since))
    out: dict[str, str] = {}
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2) as db:
        rows = db.execute("SELECT id, content_context FROM scripts WHERE created_at >= ? "
                          "AND content_context LIKE '%\"experiments\"%'", (stamp,)).fetchall()
    for script_id, raw in rows:
        try:
            items = json.loads(raw or "{}").get("experiments") or []
        except ValueError:
            continue
        for item in items if isinstance(items, list) else []:
            if isinstance(item, dict) and item.get("token"):
                out[str(item["token"])] = script_id
    return out


def reconcile(store: ExperimentStore, video_db: str | Path) -> int:
    """Bind tokens to scripts for every running experiment. Returns how many assignments were bound."""
    bound = 0
    for experiment in store.running():
        pending = set(store.unbound_tokens(experiment["id"]))
        if not pending:
            continue
        found = tokens_on_scripts(video_db, experiment.get("started_at") or experiment["created_at"])
        bound += store.bind_scripts({t: s for t, s in found.items() if t in pending})
    return bound


def start_queued(store: ExperimentStore, config_dir: str | None = None, jobs_dir: str | None = None) -> list[str]:
    """Start queued experiments, oldest first, once nothing blocks them (§9a「实验排队」).

    One still waiting only for its preview is reminded about once and left queued.
    """
    from core.experiments import checks
    from core.experiments.definition import Experiment
    if config_dir is None or jobs_dir is None:
        from web.settings import get_settings
        paths = get_settings().paths
        config_dir, jobs_dir = paths.config_dir, paths.jobs_dir
    started = []
    for row in sorted((r for r in store.list() if r["status"] == "queued"), key=lambda r: r["updated_at"]):
        problems = checks.start_problems(Experiment.model_validate(row["definition"]), store, config_dir, jobs_dir)
        if not problems:
            store.move(row["id"], "start", "system", {"reason": "queue"})
            started.append(row["id"])
        elif problems == [checks.NO_PREVIEW] and "queue_waiting" not in {e["kind"] for e in store.events(row["id"])}:
            with store.connect(write=True) as db:
                store._event(db, row["id"], "queue_waiting", "system", {"reason": checks.NO_PREVIEW})
    return started


def tick(state_dir: str | Path) -> None:
    store = ExperimentStore(state_dir)
    if not store.list():
        return
    reconcile(store, Path(state_dir) / "video.db")
    start_queued(store)
    from core.experiments import results
    results.evaluate_all(state_dir)  # alarms, end point and final snapshot run even while assignment is switched off


def start_background(state_dir: str | Path, stop: threading.Event) -> None:
    """Start the timer thread once per process. Every failure is logged and the loop goes on."""
    global _thread
    with _started:
        if _thread is not None and _thread.is_alive():
            return
        code_version()

        def loop() -> None:
            while not stop.wait(INTERVAL):
                try:
                    tick(state_dir)
                except Exception:  # noqa: BLE001 - the A/B module must never take anything else down
                    logger.exception("experiments background check failed; will try again")

        def previews() -> None:
            # its own thread: a render preview can take an hour and must not hold up the checks above
            from core.experiments import preview
            while not stop.wait(PREVIEW_INTERVAL):
                try:
                    preview.run_next(state_dir)
                except Exception:  # noqa: BLE001
                    logger.exception("experiments preview pass failed; will try again")

        _thread = threading.Thread(target=loop, name="experiments", daemon=True)
        _thread.start()
        threading.Thread(target=previews, name="experiments-preview", daemon=True).start()
