"""Hand an approved script to AutoVideo and keep the result.

One background thread per render: submit, poll AutoVideo until its job ends,
then download and verify the files into ``<state_dir>/video_renders/<id>/``
(not under output/, which the report index scans).

A render resumes from whatever its row records, so a restart loses nothing:

  SUBMITTING, never sent      → submit now
  SUBMITTING, sent, no answer → reconcile against AutoVideo's job list by
                                submission key (legacy: material hash); never
                                guess among multiple matching jobs
  QUEUED / RUNNING            → keep polling the saved job id
  DOWNLOADING                 → verify saved files and fetch only missing/changed files
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Optional

from core.config import load_system_config
from core.fileio import file_lock
from core.video import archive as video_archive
from core.video import columns as video_columns
from core.video import sources as video_sources
from core.video import store
from core.video.audience_copy import submission_problem
from core.video.autovideo import ARTIFACT_NAME, AutoVideoClient, AutoVideoError, AutoVideoUnreachable
from web.services import video_workflow
from web.settings import get_settings

logger = logging.getLogger("ai_research.web.video")

DEFAULT_BASE_URL = "http://127.0.0.1:8011"
DEFAULT_POLL_SECONDS = 5.0
MAX_BACKOFF_SECONDS = 60.0
DOWNLOAD_ATTEMPTS = 3
RECONCILE_ATTEMPTS = 5
RECONCILE_SLACK = timedelta(minutes=2)

_running: set[str] = set()
_running_lock = threading.Lock()


class RenderError(RuntimeError):
    pass


def _video_config() -> dict:
    try:
        return load_system_config(get_settings().paths.config_dir).get("video") or {}
    except Exception:  # noqa: BLE001 - defaults are fine
        return {}


def offline_mounts() -> list[str]:
    """The mount points ``video.require_mounts`` names that are not mounted now.

    The big video folders live on an external disk (a link at the old place). Unplugged, nothing is mounted at the
    disk's path, and a program that creates folders along the way would quietly write the videos to the laptop
    under /Volumes. So a render is not started while a disk it needs is missing.
    """
    return [str(m) for m in _video_config().get("require_mounts") or [] if not os.path.ismount(str(m))]


def get_client() -> AutoVideoClient:
    return AutoVideoClient(str(_video_config().get("autovideo_base_url") or DEFAULT_BASE_URL))


def poll_seconds() -> float:
    return float(_video_config().get("poll_seconds", DEFAULT_POLL_SECONDS))


def files_dir(render_id: str) -> Path:
    return Path(get_settings().paths.state_dir) / "video_renders" / render_id


#: AutoVideo's limit on a chapter title (shown on screen, never voiced).
CHAPTER_TITLE_MAX = 32


def chapters(version: dict) -> list[dict]:
    """The approved script as AutoVideo narration chapters.

    Only spoken text goes over: citations and review notes are production
    metadata. Titles are shown as chapter labels, not read aloud.
    """
    out = []
    for section in version["sections"]:
        paragraphs = [p["text"].strip() for p in section["paragraphs"] if p["text"].strip()]
        if paragraphs:
            title = " ".join(section["title"].split())[:CHAPTER_TITLE_MAX] or "未命名"
            out.append({"title": title, "paragraphs": paragraphs})
    return out


def narration_source(chapter_list: list[dict]) -> tuple[str, str]:
    """The source text AutoVideo stores for a narration job, and its SHA-256.

    Same rule as AutoVideo (UPSTREAM_INTEGRATION.md, narration mode): a
    chapter's paragraphs joined by newlines, chapters by a blank line. The
    hash is how an unanswered submit is found again in its job list.
    """
    text = "\n\n".join("\n".join(p.strip() for p in c["paragraphs"]) for c in chapter_list).strip()
    return text, hashlib.sha256(text.encode("utf-8")).hexdigest()


_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")


def topic_date(topic: dict) -> str:
    """The date a topic is about, as 「10月19日」: its item's release date, else its report's date; empty when unknown."""
    fields = topic.get("fields") or {}
    for raw in (fields.get("发布日期"), topic.get("round")):
        match = _DATE.search(str(raw or ""))
        if match:
            return f"{int(match.group(2))}月{int(match.group(3))}日"
    return ""


def package_request(column: video_columns.Column, topic: dict) -> dict:
    """The column's package block for the render request, with ``{date}`` filled from the topic.

    A part whose text needs a date the topic does not have loses that text rather than say a wrong day: the render
    date would be wrong for a topic from last week."""
    date = topic_date(topic)
    package: dict = {}
    for name, value in column.package.items():
        if not isinstance(value, dict):
            package[name] = value
            continue
        part = dict(value)
        subtitle = part.get("subtitle")
        if subtitle and "{date}" in subtitle:
            if date:
                part["subtitle"] = subtitle.replace("{date}", date)
            else:
                del part["subtitle"]
        package[name] = part
    return package


def packaging_request(column: video_columns.Column, topic: dict) -> dict:
    """Recipe overrides on the engine's common defaults, with a topic date when available."""
    from core.video.packaging import merge
    value = merge({"preset": "common", "label": column.name[:12]}, column.packaging or {})
    value["recipe"] = column.key
    intro = value.get("intro", {})
    if "{date}" in intro.get("subtitle", ""):
        date = topic_date(topic)
        if date:
            intro["subtitle"] = intro["subtitle"].replace("{date}", date)
        else:
            intro.pop("subtitle")
    return value


def source_card_request(script: dict, version: dict) -> list[dict]:
    """The script's ``sources`` for the closing source card; empty when the report cannot be read or cites none."""
    try:
        report = video_workflow.report_of(script)
    except (OSError, KeyError, TypeError):
        logger.warning("script %s: no readable report, so no source card", script.get("id"))
        report = ""
    from core import report_material

    meta = version.get("meta") or {}
    original = report_material.read(meta.get("material") or {}, meta.get("report_path") or "")
    return video_sources.source_cards(version, report, material=original)


def publish_pack(script: dict, version: dict, column, *, render_id: str | None = None) -> dict:
    """The publish pack of ``version``; chapter times come from its newest completed render, if there is one."""
    from core.video import publish

    report = ""
    try:
        report = video_workflow.report_of(script)
    except (OSError, KeyError, TypeError):
        pass
    timeline = None
    for render in store.list_renders(script["id"]):
        if render_id is not None and render["id"] != render_id:
            continue
        if render["version"] != version["version"] or render["status"] != "COMPLETED":
            continue
        timeline = {}  # there is a video: with no chapters file it has no chapters, which is not "not rendered yet"
        path = safe_file(render, "chapters.json")
        if path is not None:
            try:
                timeline = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                timeline = {}
        break
    checks = video_workflow.rule_checks(version, column)
    from web.services import video_recheck

    fresh = video_recheck.freshness(script, version, column, store.list_rechecks(script["id"]))
    return publish.build(script, {**version, "checks": checks}, column, report, timeline, fresh)


def create(script: dict, *, voice_id: Optional[str] = None, recover_download: bool = False, production_mode: Optional[str] = None) -> dict:
    """Record a render of the script's approved version. Does not contact AutoVideo."""
    version_no = script.get("approved_version")
    if not version_no:
        raise RenderError("文稿还没有定稿，不能出片")
    version = store.get_version(script["id"], version_no)
    if version is None:
        raise RenderError("找不到定稿版本，请重新选择文稿版本")
    column = video_columns.get(get_settings().paths.config_dir, script["column_key"])
    chapter_list = chapters(version)
    text, sha = narration_source(chapter_list)
    title = version["title"][:80]
    # Narration mode: the approved words are voiced verbatim; AutoVideo only
    # plans the visuals and fails the job rather than change a word.
    form = script["topic"].get("video_form") or column.video_form
    if form not in (column.allowed_forms or (column.video_form,)):
        raise RenderError(f"这个栏目不能用视频形式 {form}")
    request: dict[str, Any] = {
        "input_mode": "narration",
        "chapters": chapter_list,
        "video_form": form,
        "duration_mode": "content",
        "creative_intent": {**(column.creative_intent or {"purpose": "知识讲解"}),
                            "focus": (script["topic"].get("fields") or {}).get("核心角度", "")[:300]},
    }
    if (version.get("meta", {}).get("checks") or {}).get("mode") == "on_request":
        direction = version.get("meta", {}).get("creative_direction", "")
        request["creative_intent"] = {
            "purpose": "根据素材，产出目标观众愿意看、听得懂、想继续看的视频。" + ("\n" + direction if direction else ""),
            "audience": (script["topic"].get("fields") or {}).get("目标受众") or column.audience or "普通观众",
            "focus": version["title"],
        }
    if len(title) >= 2:
        request["title"] = title
    modern = column.packaging is not None or not column.package
    if modern:
        request["packaging"] = packaging_request(column, script["topic"])
        # Use the episode's own short heading when no recipe title was chosen.
        # A longer heading keeps the configured label instead of cutting a name.
        heading = re.split(r"[，。！？：,!:?]", title, maxsplit=1)[0].strip()
        if 1 <= len(heading) <= 12 and "title" not in request["packaging"].get("intro", {}):
            request["packaging"].setdefault("intro", {})["title"] = heading
    else:
        request["package"] = package_request(column, script["topic"])
    card_enabled = ((request["packaging"].get("enabled", True) and request["packaging"].get("source_card", {}).get("enabled", True))
                    if modern else column.source_card and form in ("auto", "information_motion"))
    if card_enabled:
        cards = source_card_request(script, version)
        if cards:
            request["sources"] = cards
    # The column's voice is the one its length estimate assumes, so it is also what the render asks for.
    voice_override = voice_id
    voice_id = voice_id or column.voice_id
    if voice_id:
        request["voice_id"] = voice_id
    spoken_sections = [section for section in version.get("sections", [])
                       if any(p.get("text", "").strip() for p in section.get("paragraphs", []))]
    notices = [{"chapter_index": i, "message": "本章节有内容待核实"}
               for i, section in enumerate(spoken_sections)
               if any(p.get("evidence") == "NEEDS_REVIEW" for p in section.get("paragraphs", []))]
    if notices:
        request["content_notices"] = notices
    from core.video import production
    paths = get_settings().paths
    config = production.get(paths.config_dir, paths.jobs_dir, script["production"]) if script.get("production") else None
    from core import matrices
    job = getattr(config, "job", "") if config else ""
    if job:
        matrix = matrices.of_job(job, paths.config_dir)
    else:
        # Older scripts have no production key. Resolve their source Job using
        # the same attribution as the board, never the recipe's subject tags.
        from core.video import outputs
        related = [script]
        seen = {script["id"]}
        while len(related) <= 5:
            parent_id = (related[-1].get("topic") or {}).get("parent_script")
            if not parent_id or parent_id in seen:
                break
            parent = store.get_script(parent_id)
            if not parent:
                break
            related.append(parent)
            seen.add(parent_id)
        owners, _ = outputs.script_owners(paths.config_dir, paths.jobs_dir, related)
        owner = owners.get(script["id"], {})
        matrix = matrices.load_all(paths.config_dir).get(owner.get("matrix", ""))
    from core.video.production_settings import inherit
    matrix_settings = inherit(matrix.render if matrix else {}, config.render if config and config.usable else {})
    if column.production_settings or matrix_settings or production_mode is not None:
        if production_mode not in (None, "standard", "fast"):
            raise RenderError("未知制作档位")
        content_settings = {k: v for k, v in column.production_settings.items() if k != "production_recipe"}
        # Old recipe values are explicit only when no new production reference was selected.
        if not column.production_recipe:
            content_settings.update({k: request[k] for k in ("video_form", "creative_intent", "packaging", "voice_id") if k in request})
        else:
            for key in ("creative_intent", "packaging"):
                if key in content_settings and key in request:
                    content_settings[key] = request[key]
        recipe_ref = matrix_settings.get("production_recipe") or column.production_recipe
        if recipe_ref:
            request["production_recipe"] = recipe_ref
        for key in ("video_form", "creative_intent", "packaging", "voice_id"):
            request.pop(key, None)
        request["production_context"] = {"content_recipe": {"id": "research:" + column.key, "settings": content_settings}}
        if matrix_settings:
            settings_id = "research-production:" + config.key if config else "research-matrix:" + matrix.key
            request["production_context"]["matrix"] = {"id": settings_id,
                "settings": {k: v for k, v in matrix_settings.items() if k != "production_recipe"}}
        if script["topic"].get("video_form"):
            request["video_form"] = script["topic"]["video_form"]
        if voice_override:
            request["voice_id"] = voice_override
        if production_mode is not None:
            request["production_mode"] = production_mode
            if production_mode == "fast":
                request.update(video_form="information_cards", visual_style="editorial_light")
    try:
        if recover_download:
            previous = store.list_renders(script["id"])
            latest = previous[0] if previous else None
            if (latest and latest["version"] == version_no and latest["status"] == "FAILED"
                    and latest["error_code"] == "DOWNLOAD_FAILED"
                    and {k: v for k, v in latest["request"].items() if k != "idempotency_key"} == request):
                resumed = store.resume_render_download(latest["id"])
                if resumed:
                    return resumed
        problem = submission_problem(version)
        if problem:
            raise RenderError(problem)
        return store.create_render(script["id"], version_no, title=title, source_text=text,
                                   source_sha256=sha, request=request)
    except store.ActiveRenderError as exc:
        raise RenderError(str(exc)) from exc


def start(render_id: str, *, client: Optional[AutoVideoClient] = None) -> None:
    """Run (or resume) a render in a background thread, once per process."""
    with _running_lock:
        if render_id in _running:
            return
        _running.add(render_id)

    def _target() -> None:
        try:
            process(render_id, client=client)
        finally:
            with _running_lock:
                _running.discard(render_id)

    threading.Thread(target=_target, name=f"video-render-{render_id[:8]}", daemon=True).start()


def resume_active() -> int:
    """Probe active work; the process lock leaves a live follower untouched."""
    # The process lock is authoritative. Probe even recent heartbeats after a
    # restart rather than waiting 120 seconds for an already-dead follower.
    with _running_lock:
        local = set(_running)
    ids = [identity for identity in store.orphaned_render_ids(stale_after=0) if identity not in local]
    for render_id in ids:
        start(render_id)
    return len(ids)


def process(render_id: str, *, client: Optional[AutoVideoClient] = None) -> str:
    """Drive one render to a terminal status and return that status."""
    client = client or get_client()
    # Heartbeats decide which work to recover, but cannot prevent two app
    # processes from seeing the same stale row. Hold a crash-released lock for
    # the entire submit/poll/download lifecycle, outside the media mount.
    lock_key = Path(get_settings().paths.state_dir) / "video_render_locks" / hashlib.sha256(render_id.encode()).hexdigest()
    try:
        with file_lock(str(lock_key), timeout=0):
            try:
                with store.beating(store.touch_render, render_id):
                    started = time.monotonic()
                    previous = (store.get_render(render_id) or {}).get("timings") or {}
                    _timing(render_id, process_runs=previous.get("process_runs", 0) + 1,
                            last_process_started_at=datetime.now(timezone.utc).isoformat())
                    try:
                        with client.session():
                            return _advance(render_id, client)
                    finally:
                        _timing(render_id, process_seconds=round(previous.get("process_seconds", 0)
                                                                 + time.monotonic() - started, 3))
            except Exception as exc:  # noqa: BLE001 - a crashed render must not look in-flight
                logger.exception("video render %s crashed", render_id)
                return _fail(render_id, "INTERNAL_ERROR", f"{type(exc).__name__}: {exc}")
    except TimeoutError:
        current = store.get_render(render_id)
        return current["status"] if current else "MISSING"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def _timing(render_id: str, **values) -> None:
    current = (store.get_render(render_id) or {}).get("timings") or {}
    store.update_render(render_id, timings={**current, **values})


def _fail(render_id: str, code: str, message: str) -> str:
    store.update_render(render_id, status="FAILED", error_code=code, error_message=message, completed_at=_now())
    return "FAILED"


def _advance(render_id: str, client: AutoVideoClient) -> str:
    render = store.get_render(render_id)
    if render is None:
        return "MISSING"
    if render["status"] not in store.ACTIVE_RENDER_STATUSES:
        return render["status"]
    job_id = render["autovideo_job_id"]
    if job_id is None:
        job_id = _reconcile(render, client) if render["submit_started_at"] else _submit(render, client)
        if job_id is None:
            return "FAILED"
    if store.get_render(render_id)["status"] != "DOWNLOADING":
        outcome = _poll(render_id, job_id, client)
        if outcome != "COMPLETED":
            return outcome
    return _download(render_id, job_id, client)


def _submit(render: dict, client: AutoVideoClient) -> Optional[str]:
    timer = time.monotonic()
    offline = offline_mounts()
    if offline:
        _fail(render["id"], "DISK_OFFLINE", f"外接盘没有挂载：{'、'.join(offline)}。接上后再重试，这次没有出片")
        return None
    started = datetime.now(timezone.utc)
    store.update_render(render["id"], submit_started_at=started.isoformat())
    payload = dict(render["request"])
    if payload.get("input_mode") != "narration":
        payload["source_text"] = render["source_text"]
    try:
        created = client.create_job(payload)
    except AutoVideoUnreachable as exc:
        logger.warning("render %s: create got no answer (%s); reconciling", render["id"], exc)
        return _reconcile({**render, "submit_started_at": started.isoformat()}, client)
    except AutoVideoError as exc:
        _fail(render["id"], exc.code, exc.message or str(exc))
        return None
    job_id = str(created["job_id"])
    _timing(render["id"], submit_seconds=round(time.monotonic() - timer, 3))
    store.update_render(render["id"], autovideo_job_id=job_id, status=_active(created.get("status")),
                        submitted_at=_now())
    return job_id


def _reconcile(render: dict, client: AutoVideoClient) -> Optional[str]:
    """Find a job an unanswered create may have made, without creating another."""
    started = _parse(render["submit_started_at"]) or datetime.now(timezone.utc)
    owned = store.owned_autovideo_job_ids()
    delay = poll_seconds()
    key = render["request"].get("idempotency_key")
    for attempt in range(RECONCILE_ATTEMPTS):
        try:
            items = client.find_jobs(render["title"] or "", idempotency_key=key)
        except (AutoVideoUnreachable, AutoVideoError) as exc:
            logger.warning("render %s: reconcile lookup failed (%s)", render["id"], exc)
            items = []
        matches = [
            item for item in items
            if item.get("source_sha256") == render["source_sha256"]
            and (not key or item.get("idempotency_key") == key)
            and item.get("job_id") not in owned
            and (key or (_parse(item.get("created_at")) or started) >= started - RECONCILE_SLACK)
        ]
        if len(matches) == 1:
            job_id = str(matches[0]["job_id"])
            store.update_render(render["id"], autovideo_job_id=job_id, status=_active(matches[0].get("status")),
                                submitted_at=_now())
            return job_id
        if len(matches) > 1:
            break
        if attempt + 1 < RECONCILE_ATTEMPTS:
            delay = _backoff(delay)
            time.sleep(delay)
    _fail(render["id"], "SUBMIT_UNCONFIRMED",
          "无法唯一确认这次提交对应的 AutoVideo 任务，请先在作品库核对后再重试。")
    return None


def _poll(render_id: str, job_id: str, client: AutoVideoClient) -> str:
    timer = time.monotonic()
    polls = 0
    failures = 0
    interval = poll_seconds()
    delay = interval
    last: Optional[dict] = None
    while True:
        polls += 1
        try:
            job = client.get_job(job_id)
        except AutoVideoError as exc:
            if exc.status_code == 404:
                return _fail(render_id, "JOB_NOT_FOUND", f"AutoVideo 里找不到任务 {job_id}")
            job, note = None, f"暂时无法读取 AutoVideo 任务状态：{exc}"
        except AutoVideoUnreachable as exc:
            job, note = None, f"暂时连不上 AutoVideo，稍后自动重试：{exc}"
        if job is None:
            failures += 1
            store.update_render(render_id, error_message=note)
            last = None
            delay = _backoff(delay)
            time.sleep(delay)
            continue
        delay = interval
        status = str(job.get("status") or "")
        mirror = {
            "active_stage": job.get("active_stage"),
            "attempt_count": int(job.get("attempt_count") or 0),
            "resolved_video_form": job.get("resolved_video_form"),
            "error_code": job.get("error_code"),
            "error_message": job.get("error_message"),
        }
        if status == "COMPLETED":
            _timing(render_id, poll_seconds=round(time.monotonic() - timer, 3), poll_count=polls,
                    poll_failures=failures, remote_completed_at=job.get("updated_at"),
                    completion_observed_at=datetime.now(timezone.utc).isoformat())
            store.update_render(render_id, status="DOWNLOADING", **mirror)
            return "COMPLETED"
        if status in ("FAILED", "CANCELLED"):
            store.update_render(render_id, status=status, completed_at=_now(), **mirror)
            return status
        current = {"status": _active(status), **mirror}
        if current != last:
            store.update_render(render_id, **current)
            last = current
        time.sleep(interval)


def _download(render_id: str, job_id: str, client: AutoVideoClient) -> str:
    timer = time.monotonic()
    dest = files_dir(render_id)
    delay = poll_seconds()
    last_error: Optional[Exception] = None
    for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
        offline = offline_mounts()
        if offline:
            return _fail(render_id, "DOWNLOAD_FAILED", f"成片已生成，但外接盘没有挂载：{'、'.join(offline)}。接上后重跑可继续复制。")
        try:
            listed = time.monotonic()
            items = [item for item in client.list_artifacts(job_id)
                     if ARTIFACT_NAME.fullmatch(str(item.get("name") or ""))]
            if len({item["name"] for item in items}) != len(items):
                raise AutoVideoError(502, "BAD_ARTIFACT_METADATA", "制品清单有重复文件名，不能并发回传")
            listing_seconds = time.monotonic() - listed
            def copy_one(item):
                started = time.monotonic()
                result = client.download(item, dest / item["name"]) or {}
                return ({"name": item["name"], "content_type": item.get("content_type") or "application/octet-stream",
                         "bytes": item.get("bytes"), "sha256": item.get("sha256")},
                        {"name": item["name"], "seconds": round(time.monotonic() - started, 3), **result})
            # Every submitted future finishes before leaving this scope, even
            # on failure; no writer can race a retry's .part file.
            with ThreadPoolExecutor(max_workers=4, thread_name_prefix="video-copy") as pool:
                results = list(pool.map(copy_one, items))
            saved = [item for item, _ in results]
        except (AutoVideoUnreachable, AutoVideoError, OSError) as exc:
            last_error = exc
            delay = _backoff(delay)
            if attempt < DOWNLOAD_ATTEMPTS:
                time.sleep(delay)
            continue
        if not any(a["name"] == "video.mp4" for a in saved):
            return _fail(render_id, "VIDEO_MISSING", "AutoVideo 任务已完成，但没有提供 video.mp4")
        _timing(render_id, download_seconds=round(time.monotonic() - timer, 3),
                artifact_list_seconds=round(listing_seconds, 3), download_attempts=attempt,
                download_workers=4, artifacts=[row for _, row in results])
        store.update_render(render_id, status="COMPLETED", artifacts=saved, error_code=None,
                            error_message=None, completed_at=_now())
        return "COMPLETED"
    return _fail(render_id, "DOWNLOAD_FAILED", f"成片已在 AutoVideo 生成，但复制到本地失败：{last_error}")


def _backoff(delay: float) -> float:
    return min(max(delay * 2, 1.0), MAX_BACKOFF_SECONDS) if delay > 0 else 0.0


def _active(status: Any) -> str:
    return "QUEUED" if status == "QUEUED" else "RUNNING"


def _parse(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        try:
            parsed = datetime.strptime(str(value), "%Y-%m-%dT%H:%M:%S%z")
        except ValueError:
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def render_timings(render: dict) -> dict:
    """Expose measured phase times plus wall-clock creation-to-copy duration."""
    result = dict(render.get("timings") or {})
    started, finished = _parse(render.get("created_at")), _parse(render.get("completed_at"))
    if started and finished:
        result["elapsed_seconds"] = round(max(0, (finished - started).total_seconds()), 3)
    return result


def reveal(render: dict, name: str = "video.mp4") -> dict:
    """Show a finished render's file in the host's file manager (Finder on a Mac): ``{path, opened}``.

    The path is built from the render's own folder and a recorded artifact name, never from the request. Only a Mac
    can be asked to open it; elsewhere (or when ``open`` fails) the path alone comes back, for the page to show.
    """
    import subprocess
    import sys

    path = safe_file(render, name)
    if path is None:
        raise FileNotFoundError(name)
    opened = False
    if sys.platform == "darwin":
        try:
            opened = subprocess.run(["open", "-R", str(path)], timeout=10, check=False,
                                    capture_output=True).returncode == 0
        except (OSError, subprocess.SubprocessError):
            opened = False
    return {"path": str(path), "folder": str(path.parent), "opened": opened}


def safe_file(render: dict, name: str) -> Optional[Path]:
    """Path of a recorded, downloaded artifact, or None."""
    if not ARTIFACT_NAME.match(name):
        return None
    if not any(a.get("name") == name for a in render.get("artifacts") or []):
        return None
    path = files_dir(render["id"]) / name
    if path.is_file():
        return path
    # a week after it finished the folder moves to the archive disk (core/video/archive.py)
    archived = video_archive.archived_dir(get_settings().paths.config_dir, render["id"])
    path = archived / name if archived else None
    return path if path is not None and path.is_file() else None
