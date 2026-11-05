"""Preview B before an experiment starts (spec §9a step ③): only render, never publish.

A render preview takes a recent finished automatic video, builds B's AutoVideo request through the real request
builder (``video_render.create`` with a ``record`` that returns the request instead of writing a render row), runs the
same visual preparation, submits the job to AutoVideo directly and downloads ``video.mp4`` into
``state/experiments/previews/``. No render row is ever written, so no publisher can pick a preview up. The job does
appear in AutoVideo's own job library, under an ``experiment-preview:`` idempotency key.

Previews start only while no production render is active, one at a time, from the module's own thread.
"""
from __future__ import annotations

import copy
import json
import logging
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Callable, Optional

from core.experiments import hook
from core.experiments.definition import Experiment
from core.experiments.store import ExperimentStore

logger = logging.getLogger(__name__)

KEEP_DAYS = 7  # video.archive.keep_days: an older render's AutoVideo folder may be on the archive disk
POLL_SECONDS = 10.0
TIMEOUT_SECONDS = 3 * 3600


def folder(state_dir: str | Path, preview_id: str) -> Path:
    return Path(state_dir) / "experiments" / "previews" / preview_id


def _video_db(state_dir: str | Path) -> Path:
    return Path(state_dir) / "video.db"


def _ro(path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2)
    db.row_factory = sqlite3.Row
    return db


def sources(state_dir: str | Path, experiment: Experiment, now: float | None = None, limit: int = 10) -> list[dict]:
    """Recent finished automatic renders this experiment could reach, newest first (read-only)."""
    path = _video_db(state_dir)
    if not path.is_file():
        return []
    since = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime((now or time.time()) - KEEP_DAYS * 86400))
    with _ro(path) as db:
        rows = db.execute(
            "SELECT r.id AS render_id, r.script_id, r.title, r.completed_at, s.column_key, s.content_context "
            "FROM renders r JOIN scripts s ON s.id = r.script_id WHERE r.status='COMPLETED' AND r.completed_at >= ? "
            "AND s.created_by='autopilot' AND s.archived=0 ORDER BY r.completed_at DESC LIMIT 200", (since,)).fetchall()
    out = []
    for row in rows:
        context = json.loads(row["content_context"] or "{}")
        column = hook.stratum_column(context, row["column_key"])
        if not experiment.scope.matches(context, column) or (experiment.needs_column() and not context.get("column")):
            continue
        out.append({"render_id": row["render_id"], "script_id": row["script_id"], "title": row["title"],
                    "completed_at": row["completed_at"]})
        if len(out) >= limit:
            break
    return out


def production_idle(state_dir: str | Path) -> bool:
    """No production render is in flight (read-only), so a preview cannot slow one down (§9a)."""
    from core.video.store import ACTIVE_RENDER_STATUSES
    path = _video_db(state_dir)
    if not path.is_file():
        return True
    marks = ",".join("?" * len(ACTIVE_RENDER_STATUSES))
    with _ro(path) as db:
        return db.execute(f"SELECT COUNT(*) FROM renders WHERE status IN ({marks})",
                          tuple(ACTIVE_RENDER_STATUSES)).fetchone()[0] == 0


def request(store: ExperimentStore, state_dir: str | Path, experiment_id: str, by: str,
            source_render_id: str | None = None) -> dict:
    """Queue a preview of B for this draft. The A/B thread runs it when production is idle."""
    found = store.get(experiment_id)
    if not found:
        raise KeyError(experiment_id)
    experiment = Experiment.model_validate(found["definition"])
    kind = experiment.preview_kind()
    if kind == "none":
        raise ValueError("这个实验只改写稿，不需要预览")
    candidates = sources(state_dir, experiment)
    chosen = next((c for c in candidates if c["render_id"] == source_render_id), None) if source_render_id else \
        (candidates[0] if candidates else None)
    if chosen is None:
        raise ValueError(f"最近 {KEEP_DAYS} 天没有这个实验范围内的自动出片，没法预览")
    preview_id = f"{experiment_id}-{uuid.uuid4().hex[:8]}"
    with store.connect(write=True) as db:
        busy = db.execute("SELECT 1 FROM previews WHERE experiment_id=? AND status IN ('requested','running')",
                          (experiment_id,)).fetchone()
        if busy:
            raise ValueError("这个实验已有一个预览在排队或在出片")
        db.execute("INSERT INTO previews (id, experiment_id, kind, status, source_render_id, source_script_id, "
                   "requested_at) VALUES (?,?,?,?,?,?,?)",
                   (preview_id, experiment_id, kind, "requested", chosen["render_id"], chosen["script_id"], time.time()))
        store._event(db, experiment_id, "preview_requested", by, {"preview": preview_id, "source": chosen["render_id"]})
    return latest(store, experiment_id)


def latest(store: ExperimentStore, experiment_id: str) -> Optional[dict]:
    with store.connect() as db:
        row = db.execute("SELECT * FROM previews WHERE experiment_id=? ORDER BY requested_at DESC LIMIT 1",
                         (experiment_id,)).fetchone()
    return {**dict(row), "files": json.loads(row["files"] or "[]")} if row else None


def confirm(store: ExperimentStore, experiment_id: str, by: str) -> None:
    """The user looked at A and B side by side and accepts B (the start check reads this event)."""
    shown = latest(store, experiment_id)
    if not shown or shown["status"] != "done":
        raise ValueError("还没有做好的预览")
    with store.connect(write=True) as db:
        store._event(db, experiment_id, "preview_confirmed", by, {"preview": shown["id"]})


def _update(store: ExperimentStore, preview_id: str, **fields) -> None:
    if "files" in fields:
        fields["files"] = json.dumps(fields["files"])
    with store.connect(write=True) as db:
        db.execute("UPDATE previews SET " + ",".join(f"{k}=?" for k in fields) + " WHERE id=?",
                   (*fields.values(), preview_id))


def b_script(script: dict, experiment: Experiment) -> dict:
    """The source script as B would have frozen it: B's overrides written field by field."""
    out = copy.deepcopy(script)
    context = copy.deepcopy(out.get("content_context") or {})
    for path, value in experiment.arms["B"].overrides.items():
        hook._set(context, path, value)
    out["content_context"] = context
    return out


def build_request(script: dict) -> dict:
    """B's render request from the real builder, without writing a render row."""
    from web.services import video_render
    return video_render.create(script, record=lambda script_id, version, **kw: {"script_id": script_id,
                                                                                 "version": version, **kw})


def render(state_dir: str | Path, preview_id: str, built: dict, client, *,
           sleep: Callable[[float], None] = time.sleep, poll: float = POLL_SECONDS) -> list[str]:
    """Prepare visuals, submit, wait and download ``video.mp4``. Returns the saved file names."""
    from web.services import video_visuals
    shadow = {"id": f"preview-{preview_id}", "script_id": built["script_id"], "request": built["request"],
              "visuals": built.get("visuals") or {}, "source_text": built["source_text"]}
    try:
        payload, _ = video_visuals.prepare(shadow, client)
    except Exception as exc:  # noqa: BLE001 - mirror production: fall back to the request without new visuals
        logger.warning("preview %s: visual preparation failed (%s); using the request as built", preview_id,
                       type(exc).__name__)
        payload = copy.deepcopy(built["request"])
    from web.services import opening_card  # the same post-visuals step production runs in video_visuals.freeze
    shadow.update(version=built.get("version"), title=built.get("title") or "")
    payload, _ = opening_card.apply(payload, shadow, built.get("context"), folder(state_dir, preview_id) / "opening-copy.json")
    if payload.get("input_mode") != "narration":
        payload["source_text"] = built["source_text"]
    payload["idempotency_key"] = f"experiment-preview:{preview_id}"
    job_id = str(client.create_job(payload)["job_id"])
    deadline = time.monotonic() + TIMEOUT_SECONDS
    while True:
        status = str(client.get_job(job_id).get("status") or "")
        if status == "COMPLETED":
            break
        if status in ("FAILED", "CANCELLED"):
            raise RuntimeError(f"AutoVideo 任务 {status}")
        if time.monotonic() > deadline:
            raise TimeoutError("预览出片超过 3 小时")
        sleep(poll)
    dest = folder(state_dir, preview_id)
    dest.mkdir(parents=True, exist_ok=True)
    saved = []
    for item in client.list_artifacts(job_id):
        if item.get("name") == "video.mp4":
            client.download(item, dest / "video.mp4")
            saved.append("video.mp4")
    if not saved:
        raise RuntimeError("AutoVideo 任务完成了，但没有 video.mp4")
    return saved


def covers(state_dir: str | Path, preview_id: str, render: dict, title: str, b_style: str) -> list[str]:
    """Both covers for one finished render: today's style as A, B's style as B (no AutoVideo involved)."""
    from core.social import cover_copy
    from core.social.cover import make_covers
    from web.services import bilibili, video_render
    video = video_render.safe_file(render, "video.mp4")
    if video is None:
        raise RuntimeError("这条视频的成片文件不在本机（可能已归档），换一条")
    dest = folder(state_dir, preview_id)
    dest.mkdir(parents=True, exist_ok=True)
    saved = []
    from core.experiments import registry
    for arm, style in (("a", registry.find("cover_style").default), ("b", b_style)):
        copy = None if style == "title" else cover_copy.for_render(render, title, dest / "copy.json")
        made = make_covers(video, title, {"landscape": dest / f"cover-{arm}.jpg"}, bilibili.ffmpeg_binary(), copy)
        if made.get("landscape"):
            saved.append(f"cover-{arm}.jpg")
    if len(saved) != 2:
        raise RuntimeError("封面没有画出来")
    return saved


def recover(state_dir: str | Path) -> int:
    """At process start: a preview left「running」was cut off by a restart; mark it failed so a new one can be asked for."""
    if not (Path(state_dir) / "experiments.db").is_file():
        return 0
    store = ExperimentStore(state_dir)
    with store.connect(write=True) as db:
        return db.execute("UPDATE previews SET status='failed', error='后台重启，预览中断，请重新生成', finished_at=? "
                          "WHERE status='running'", (time.time(),)).rowcount


def run_next(state_dir: str | Path, client=None) -> Optional[str]:
    """Run the oldest queued preview: covers at once, a render only while production is idle. Returns its id."""
    store = ExperimentStore(state_dir)
    with store.connect() as db:
        row = db.execute("SELECT * FROM previews WHERE status='requested' ORDER BY kind='render', requested_at "
                         "LIMIT 1").fetchone()
    if not row or (row["kind"] == "render" and not production_idle(state_dir)):
        return None
    preview_id = row["id"]
    _update(store, preview_id, status="running")
    try:
        found = store.get(row["experiment_id"])
        experiment = Experiment.model_validate(found["definition"])
        from core.video import store as video_store
        if row["kind"] == "cover":
            render_row = video_store.get_render(row["source_render_id"])
            if render_row is None:
                raise RuntimeError("找不到预览用的出片")
            files = covers(state_dir, preview_id, render_row, render_row.get("title") or "",
                           str(experiment.arms["B"].overrides.get("cover_style", "title")))
            _update(store, preview_id, status="done", files=files, finished_at=time.time())
            return preview_id
        script = video_store.get_script(row["source_script_id"])
        if script is None:
            raise RuntimeError("找不到预览用的稿件")
        shown = b_script(script, experiment)
        built = {**build_request(shown), "context": shown["content_context"]}
        if client is None:
            from web.services.video_render import get_client
            client = get_client()
        with client.session():
            files = render(state_dir, preview_id, built, client)
        _update(store, preview_id, status="done", files=files, finished_at=time.time())
    except Exception as exc:  # noqa: BLE001 - a failed preview is shown in the tab, nothing else is affected
        logger.exception("experiment preview %s failed", preview_id)
        _update(store, preview_id, status="failed", error=f"{type(exc).__name__}: {str(exc)[:300]}",
                finished_at=time.time())
    return preview_id
