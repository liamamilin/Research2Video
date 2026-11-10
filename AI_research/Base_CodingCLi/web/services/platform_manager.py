"""Bilibili publishing outbox, account-bound comment inbox, and opt-in automatic uploads."""

from __future__ import annotations

import fcntl
import json
import logging
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

from core.platform_store import PlatformStore
from core.video import content_guard, credibility, store as video_store
from web.services import bilibili, video_render
from web.services.publish_cover import publish_cover
from web.settings import get_settings

Error = bilibili.BilibiliError
logger = logging.getLogger(__name__)


def directory() -> Path:
    return Path(get_settings().paths.state_dir) / "platforms" / "bilibili"


def store() -> PlatformStore:
    return PlatformStore(get_settings().paths.state_dir)


def credentials() -> dict | None:
    path = directory() / "credentials.json"
    return json.loads(path.read_text()) if path.exists() else None


def client(expected_uid: str = "") -> bilibili.BilibiliClient:
    data = credentials()
    if data is None:
        raise Error("请先连接哔哩哔哩账号。")
    api = bilibili.BilibiliClient(data)
    if expected_uid and api.cookies.get("DedeUserID") != expected_uid:
        raise Error("登录账号已改变，请刷新页面后重试。")
    return api


@contextmanager
def account_lock():
    folder = directory()
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / "worker.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise Error("平台任务正在运行，请稍后再更换或断开账号。") from exc
        yield


@contextmanager
def identity_lock():
    """Serialize short credential mutations and enqueues without blocking behind a video upload."""
    folder = directory()
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / "identity.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def account() -> dict:
    data = credentials()
    path = directory() / "account.json"
    profile = json.loads(path.read_text()) if path.exists() else {}
    if not data:
        return {"connected": False}
    return {"connected": True, **profile, "upload_authorized": bilibili.upload_authorized(data)}


def connect(data: dict) -> dict:
    normalized = bilibili.normalize_credentials(data)
    profile = bilibili.BilibiliClient(normalized).verify()
    mid = str(normalized["token_info"]["mid"])
    if profile["uid"] != mid:
        raise Error("凭证的账号 ID 与验证结果不一致。")
    with account_lock(), identity_lock():
        # Pause before replacing either part of the account, under the worker's own lock.
        patch = {"auto_upload": False, "sync_comments": False}
        if account().get("uid") != profile["uid"]:
            patch["first_comment"] = {"enabled": False, "text": "", "pin": False}
        store().save_settings(patch)
        bilibili.private_json(directory() / "credentials.json", normalized)
        bilibili.private_json(directory() / "account.json", profile)
        from web.services.platform_analytics import reconnected
        reconnected(get_settings().paths.state_dir, profile["uid"])
    return account()


def verify() -> dict:
    profile = client(uid()).verify()
    if profile["uid"] != account().get("uid"):
        raise Error("账号凭证已改变，请重新连接账号。")
    bilibili.private_json(directory() / "account.json", profile)
    return profile


def disconnect() -> None:
    with account_lock(), identity_lock():
        store().save_settings({"auto_upload": False, "sync_comments": False,
                               "first_comment": {"enabled": False, "text": "", "pin": False}})
        for name in ("credentials.json", "account.json", "login.json"):
            (directory() / name).unlink(missing_ok=True)


def uid() -> str:
    profile = account()
    if not profile.get("connected") or not profile.get("uid"):
        raise Error("请先连接哔哩哔哩账号。")
    return profile["uid"]


def categories() -> dict:
    """Cache only category names/IDs per account; keep a usable list during outages."""
    owner = uid()
    path = directory() / f"categories-{owner}.json"
    try:
        cached = json.loads(path.read_text())
        if cached.get("uid") != owner or not cached.get("categories"):
            cached = {}
    except (OSError, ValueError):
        cached = {}
    if cached and time.time() - cached.get("updated_at", 0) < 6 * 3600:
        return {k: cached[k] for k in ("categories", "updated_at")} | {"stale": False}
    try:
        rows = client(owner).categories()
    except Error:
        if not cached:
            raise
        return {k: cached[k] for k in ("categories", "updated_at")} | {"stale": True}
    result = {"categories": rows, "updated_at": time.time()}
    bilibili.private_json(path, {"uid": owner, **result})
    return {**result, "stale": False}


def _timestamp(value: str) -> float:
    """Renders record ``+0800`` offsets, which ``fromisoformat`` before Python 3.11 rejects."""
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S%z").timestamp()
    except ValueError:
        return datetime.fromisoformat(value).timestamp()


_logged: set[tuple[str, str]] = set()


def _log_once(level: int, render_id: str, reason: str) -> None:
    """The worker re-checks every candidate each pass; say why a render stays out only when the reason is new."""
    if (render_id, reason) not in _logged:
        _logged.add((render_id, reason))
        logger.log(level, "Auto upload %s %s", render_id, reason)


def _matrix_keys(script: dict) -> list[str]:
    """The script's matrices, most specific first: where its Job's reports come from, then the column's domain.
    Individual scripts have no column context, so their Job is read from the topic; archived matrices never count."""
    from core import matrices

    context = script.get("content_context") or {}
    topic = script.get("topic") or {}
    job = ((topic.get("episode") or {}).get("job") or (topic.get("matrix_dedup") or {}).get("job")
           or topic.get("knowledge_job") or context.get("job", ""))
    found = None
    if job:
        found = next((m for m in matrices.load_all(get_settings().paths.config_dir).values()
                      if not m.archived and job in m.jobs), None)
    keys = [context.get("source_matrix", ""), found.key if found else "", context.get("matrix", ""),
            found.content.get("parent", "") if found else ""]
    return [key for key in dict.fromkeys(keys) if key]


def category_for(script: dict) -> int:
    """The Bilibili partition for a script: its matrix's entry in ``tid_by_matrix``, else the account default."""
    config = store().settings()
    by_matrix = config.get("tid_by_matrix") or {}
    for key in _matrix_keys(script):
        if int(by_matrix.get(key) or 0) >= 1:
            return int(by_matrix[key])
    return int(config.get("tid") or 0)


def season_for(script: dict, config: dict) -> int:
    """The 合集 a script's video joins: its recipe's entry first (e.g. all 快讯), then its matrices, most specific first."""
    rules = config.get("collections") or {}
    by_recipe, by_matrix = rules.get("by_recipe") or {}, rules.get("by_matrix") or {}
    if int(by_recipe.get(script.get("column_key", "")) or 0) > 0:
        return int(by_recipe[script["column_key"]])
    for key in _matrix_keys(script):
        if int(by_matrix.get(key) or 0) > 0:
            return int(by_matrix[key])
    return 0


COLLECTION_RETRY_SECONDS = 600
COLLECTION_MAX_ATTEMPTS = 30


def process_collection(task: dict) -> None:
    """Called under the worker lock: put a published video into its 合集, idempotently.
    A video that is not open yet, or that the platform refuses for now, is retried later."""
    db = store()
    season = int(task.get("collection_season") or 0)
    if not season:
        script = video_store.get_script(task["script_id"])
        season = season_for(script, db.settings()) if script else 0
    if not season:
        db.set_collection(task["id"], status="NONE")
        return

    def wait(reason: str) -> None:
        exhausted = int(task.get("collection_attempts") or 0) + 1 >= COLLECTION_MAX_ATTEMPTS
        db.set_collection(task["id"], status="FAILED" if exhausted else "WAITING", season=season, error=reason, attempt=True)

    api = client(task["uid"])
    try:
        info = api.owned_video(task["bvid"], task["uid"])
        if info.get("state") != 0:
            return wait("视频尚未公开，公开后再加入合集。")
        section = api.season_sections().get(season)
        if not section:
            return db.set_collection(task["id"], status="FAILED", season=season,
                                     error=f"账号里找不到合集 {season}，请检查合集设置。")
        aid = int(info["aid"])
        if aid in api.section_aids(section):
            return db.set_collection(task["id"], status="DONE", season=season)
        state = api.season_episode_state([aid]).get(aid) or {}
        if state.get("code"):
            return wait(state.get("msg") or f"B 站暂不允许加入合集（{state['code']}）。")
        api.add_to_season(section, [{"aid": aid, "cid": state.get("cid") or info.get("cid"), "title": info.get("title") or ""}])
    except Error as exc:
        # Uncertain outcomes are safe to retry: the next pass reads the section before adding again.
        return wait(str(exc))
    db.set_collection(task["id"], status="DONE", season=season)


def column_for(script: dict):
    if (script.get('topic') or {}).get('local_video'):
        return None
    from web.routes.video import _known_columns

    column = _known_columns().get(script["column_key"])
    if column is None:
        raise Error("找不到这篇文稿的视频配方。")
    return column


def eligible(render_id: str, *, automatic: bool = False) -> tuple[dict, dict, dict]:
    render = video_store.get_render(render_id)
    script = video_store.get_script(render["script_id"]) if render else None
    if not render or render["status"] != "COMPLETED" or not script:
        raise Error("请选择已完成的成片。")
    if reason := content_guard.publishing_block(script):
        raise Error(reason)
    if automatic and (script.get('topic') or {}).get('local_video'):
        raise Error('本地视频按指定平台投稿，不加入全局自动投稿。')
    if automatic and (script.get("archived") or script["status"] != "APPROVED" or
                      script.get("approved_version") != render["version"] or script.get("current_version") != render["version"]):
        raise Error("自动投稿只处理未归档文稿的当前定稿成片。")
    if video_render.safe_file(render, "video.mp4") is None:
        raise Error("成片文件不可用，请检查归档磁盘。")
    version = video_store.get_version(script["id"], render["version"])
    if version is None:
        raise Error("找不到成片对应的文稿版本。")
    return render, script, version


def preview(render_id: str) -> dict:
    from web.services.distributions import preview_draft
    render, script, version = eligible(render_id)
    pack = video_render.publish_pack(script, version, column_for(script), render_id=render_id)
    from core.video.platform_publish import adapt
    from core.social.publish_text import default_comment
    adaptation = adapt(pack, "bilibili")
    warnings = list(pack["warnings"])
    if script.get("archived"):
        warnings.append("这篇文稿已归档，仍可手动投稿已有成片。")
    if script.get("current_version") != render["version"]:
        warnings.append(f"本次投稿使用 v{render['version']} 成片；文稿当前为 v{script['current_version']}，请确认选中了需要的版本。")
    return {**preview_draft(render, "bilibili"), "render_id": render["id"], **adaptation["payload"],
            "adaptation": adaptation, "tid": category_for(script), "warnings": warnings,
            "first_comment": default_comment(store().settings()["first_comment"]),
            "freshness_warnings": pack.get("freshness", {}).get("warnings", []),
            "credibility": credibility.label(version),
            "existing_upload": next((u for u in script_uploads(script["id"]) if u["version"] == render["version"]), None)}


def script_uploads(script_id: str) -> list[dict]:
    """Read this document's tasks for the currently connected account."""
    profile = account()
    return store().script_uploads(profile["uid"], script_id) if profile.get("connected") and profile.get("uid") else []


def upload_task(task_id: str) -> dict:
    """Read one account-bound task, including older outbox entries."""
    row = store().get_upload(task_id)
    if not row or row["uid"] != uid():
        raise Error("找不到当前账号的这条投稿任务。")
    return row


SCHEDULE_LEAD = 4.5 * 3600  # biliup needs dtime more than 4 hours after submission; the rest covers the upload
HOLD = "hold"  # wait here and submit once the window opens: the platform's timer cannot reach it


PRIME = {"prime_start": 19, "prime_end": 22, "prime_heat": 100000}  # B站 evening peak; topics this hot wait for it


def publish_at(now: float, window: dict, taken: list[float], *, held: bool = False, heat: int = 0) -> int | str | None:
    """When an automatic upload goes public: ``None`` now, a ``dtime`` timer, or ``HOLD`` (wait here, ask again).

    Every upload keeps ``spacing`` minutes from the others (11-10: in the daytime too, so one topic's videos and a
    morning's batch no longer go out back to back). A topic at least ``prime_heat`` hot (core/video/heat.py) goes out
    in the evening peak ``prime_start``–``prime_end`` when a slot there is free; otherwise the ordinary rule applies.
    Night renders go public from the window's opening, in order and spaced. A slot the platform's 4-hour timer can
    reach becomes a ``dtime``; an earlier one is held here and submitted at its time.
    """
    if not window.get("enabled"):
        return None
    start, end = int(window.get("start", 8)), int(window.get("end", 23))
    spacing = max(1, int(window.get("spacing") or 30)) * 60
    if not 0 <= start < end <= 24:
        return None
    local = datetime.fromtimestamp(now).astimezone()
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    free = lambda slot: all(abs(slot - t) >= spacing for t in taken)  # noqa: E731
    prime_start = int(window.get("prime_start", PRIME["prime_start"]))
    prime_end = int(window.get("prime_end", PRIME["prime_end"]))
    if heat >= int(window.get("prime_heat", PRIME["prime_heat"])) and start <= prime_start < prime_end <= end:
        # Only today's peak, and only while it is still ahead: a hot topic is not held a day for tomorrow's.
        slot = (midnight + timedelta(hours=prime_start)).timestamp()
        close = (midnight + timedelta(hours=prime_end)).timestamp()
        while now < slot < close:
            if free(slot):
                return int(slot) if slot >= now + SCHEDULE_LEAD else HOLD
            slot += spacing
    if start <= local.hour < end:
        return None if free(now) else HOLD
    for offset in range(8):
        day = midnight + timedelta(days=offset)
        slot, close = (day + timedelta(hours=start)).timestamp(), (day + timedelta(hours=end)).timestamp()
        if close <= now:
            continue
        while slot < close:
            if free(slot):
                return int(slot) if slot >= now + SCHEDULE_LEAD else HOLD
            slot += spacing
    return int(now + SCHEDULE_LEAD)


def held_note(window: dict) -> str:
    return (f"等待发布时段：{int(window.get('start', 8)):02d}:00–{int(window.get('end', 23)):02d}:00 内按热度依次投稿，"
            f"每条至少间隔 {int(window.get('spacing') or 30)} 分钟；热门选题留到 "
            f"{int(window.get('prime_start', PRIME['prime_start'])):02d}:00 晚高峰。")


def _publish_times(db, owner: str, task_id: str) -> list[float]:
    """When other automatic uploads go (or went) public: their timers and the moments held ones were released."""
    return [float(t) for u in db.uploads(owner) if u["id"] != task_id and u["status"] in ("QUEUED", "UPLOADING", "SUBMITTED", "PUBLISHED")
            for t in (u["payload"].get("dtime"), u["payload"].get("released_at")) if t]


def scheduled_note(dtime: float) -> str:
    return "定时发布：将于 " + datetime.fromtimestamp(dtime).astimezone().strftime("%m-%d %H:%M") + " 公开。"


def enqueue(render_id: str, payload: dict, *, automatic: bool = False) -> dict:
    owner = uid()
    if payload.get("account_uid") and payload["account_uid"] != owner:
        raise Error("登录账号已改变，请刷新页面后重试。")
    render, script, version = eligible(render_id, automatic=automatic)
    existing = next((u for u in script_uploads(script["id"]) if u["version"] == render["version"]), None)
    if existing:
        return existing
    if video_store.has_publication(script["id"], render["version"], "bilibili", owner):
        raise Error("这个版本已经记录发布，请先查看文稿中的发布记录。")
    if not bilibili.biliup_binary():
        raise Error("未找到 biliup；安装后才能加入投稿队列。")
    title = str(payload.get("title", "")).strip()
    tags = [str(t).strip() for t in payload.get("tags", []) if str(t).strip()]
    if not 1 <= len(title) <= 80 or not 1 <= len(tags) <= 10 or any(len(t) > 20 or "," in t for t in tags):
        raise Error("标题需为 1–80 字；标签需为 1–10 个，每个不超过 20 字且不能含逗号。")
    description = str(payload.get("description", ""))
    from core.video.platform_publish import inspect
    preflight = inspect("bilibili", {"title": title, "description": description, "tags": tags})
    if not preflight["valid"]:
        raise Error("；".join(preflight["issues"]))
    if automatic and not payload.get("adaptation", {}).get("check", {}).get("valid", True):
        raise Error("平台发布文案仍需人工整理，请查看发布包。")
    if len(description) > 2000 or not 1 <= int(payload.get("tid") or 0) <= 65535:
        raise Error("简介最多 2000 字，并且需要选择投稿分区。")
    first_comment = payload.get("first_comment")
    if first_comment is None:
        first_comment = store().settings()["first_comment"]
    clean = {"title": title, "tags": tags, "description": description, "tid": int(payload["tid"]),
             "cover": publish_cover(render, title), "automatic": automatic,
             "first_comment": clean_first_comment(first_comment)}
    if automatic:  # tick already owns the same worker lock
        return store().enqueue(owner, render, clean)
    with identity_lock():
        if uid() != owner:
            raise Error("登录账号已改变，请刷新页面后重试。")
        return store().enqueue(owner, render, clean)


def clean_first_comment(value: dict) -> dict:
    """Validate an opt-in message; keep disabled template text available for later editing."""
    from core.social.publish_text import comment_options
    value = comment_options(value)
    text = str(value.get("text", "")).strip()
    enabled = bool(value.get("enabled", False))
    if len(text) > 1000 or (enabled and not text):
        raise Error("启用首条评论时需填写 1–1000 字的评论内容。")
    return {"enabled": enabled, "text": text, "pin": bool(value.get("pin")) if enabled else False}


def save_first_comment_template(value: dict) -> dict:
    clean = clean_first_comment(value)
    with account_lock():
        uid()
        return store().save_settings({"first_comment": clean})["first_comment"]


def candidate_catalog() -> tuple[list[dict], dict]:
    """All usable completed versions for manual publishing; current workflow state is descriptive only."""
    result = []
    current = uid() if account().get("connected") else ""
    queued = store().upload_versions(current)
    with video_store.connect() as conn:
        rows = conn.execute("SELECT r.id AS render_id,r.script_id,r.version,r.title,r.completed_at,r.artifacts,"
            "s.current_version,s.approved_version,s.status AS script_status,s.archived,"
            "(json_extract(s.topic,'$.local_video') IS NOT NULL) AS local_video,"
            f"({content_guard.PUBLISHABLE_SQL}) AS publishable,"
            "EXISTS(SELECT 1 FROM publications p WHERE p.script_id=r.script_id AND p.version=r.version "
            "AND p.platform='bilibili' AND (p.account_uid=? OR p.account_uid='')) AS published FROM renders r JOIN scripts s ON s.id=r.script_id "
            "JOIN script_versions v ON v.script_id=r.script_id AND v.version=r.version "
            "WHERE r.status='COMPLETED' "
            "ORDER BY r.completed_at DESC,r.created_at DESC,r.id DESC", (current,)).fetchall()
    stats = {"completed_renders": len(rows), "available_versions": 0, "published_versions": 0,
             "queued_versions": 0, "unavailable_versions": 0, "ready_versions": 0}
    all_versions = {(row["script_id"], row["version"]) for row in rows}
    available = set()
    for row in rows:
        key = (row["script_id"], row["version"])
        if key in available:
            continue
        render = {"id": row["render_id"], "artifacts": json.loads(row["artifacts"])}
        if video_render.safe_file(render, "video.mp4") is None:
            continue
        available.add(key)
        stats["available_versions"] += 1
        if row["published"]:
            stats["published_versions"] += 1
        elif key in queued:
            stats["queued_versions"] += 1
        elif row["publishable"]:
            result.append({k: row[k] for k in ("render_id", "script_id", "version", "title", "completed_at",
                                             "current_version", "approved_version", "script_status", "archived", "local_video")})
    stats["unavailable_versions"] = len(all_versions - available)
    stats["ready_versions"] = len(result)
    return result, stats


def candidates() -> list[dict]:
    return candidate_catalog()[0]


def overview() -> dict:
    profile = account()
    owner = profile.get("uid", "") if profile.get("connected") else ""
    db = store()
    available, summary = candidate_catalog()
    return {"account": profile, "settings": db.settings(), "biliup_ready": bool(bilibili.biliup_binary()), "publication_flow": True,
            "uploads": db.uploads(owner), "videos": db.videos(owner), "comments": db.comments(owner),
            "candidates": available, "candidate_summary": summary}


def watch(bvid: str) -> dict:
    owner = uid()
    info = client(owner).owned_video(bvid, owner)
    store().watch(owner, bvid, info.get("title", bvid))
    return {"bvid": bvid, "title": info.get("title", bvid)}


def sync(bvid: str, page: int = 1, root: str = "") -> dict:
    owner = uid()
    api = client(owner)
    db = store()
    db.sync_attempt(owner, bvid)
    try:
        info = api.owned_video(bvid, owner)
        db.watch(owner, bvid, info.get("title", bvid))
        rows, more = api.thread(info, root, page) if root else api.comments(info, page)
    except Error as exc:
        db.sync_attempt(owner, bvid, str(exc))
        raise
    # Never offer to reply to our own comments or to the reply just sent by this app.
    rows = [row for row in rows if str(row.get("mid")) != owner]
    added = db.save_comments(owner, bvid, rows)
    return {"added": added, "page": page, "has_more": more, "root": root}


def send_reply(bvid: str, rpid: str, text: str) -> dict:
    owner = uid()
    db = store()
    row = db.get_comment(owner, bvid, rpid)
    if not row:
        raise Error("找不到这条评论，请先同步。")
    api = client(owner)
    info = api.owned_video(bvid, owner)
    if not db.claim_reply(owner, bvid, rpid, text):
        raise Error("该评论已回复、发送中或结果待核对；两次回复也需间隔至少 15 秒。")
    try:
        reply_rpid = api.reply(info, row, text)
    except Exception as exc:
        db.finish_reply(owner, bvid, rpid, error="发送结果未确认，请到原评论核对，避免重复回复。")
        raise Error("发送结果未确认，请到原评论核对。") from exc
    db.finish_reply(owner, bvid, rpid, reply_rpid=reply_rpid)
    return db.get_comment(owner, bvid, rpid)


def reconcile(task_id: str, bvid: str) -> dict:
    db = store()
    row = upload_task(task_id)
    if row["status"] == "PUBLISHED" and row["bvid"] == bvid:
        return row
    if row["status"] not in ("UNKNOWN", "SUBMITTED"):
        raise Error("只能为当前账号待核对的投稿回填 BV 号。")
    info = client(row["uid"]).owned_video(bvid, row["uid"])
    if info.get("state") != 0:
        dtime = float(row["payload"].get("dtime") or 0)
        db.update_upload(task_id, status="SUBMITTED", bvid=bvid, error=scheduled_note(dtime) if dtime > time.time() else
                         "平台尚未公开这条投稿，请在创作中心查看审核状态。")
        return db.get_upload(task_id)
    db.watch(uid(), bvid, info.get("title", bvid))
    url = f"https://www.bilibili.com/video/{bvid}"
    try:
        published_at = datetime.fromtimestamp(float(info["pubdate"])).astimezone() if float(info.get("pubdate") or 0) > 0 else datetime.now().astimezone()
    except (KeyError, TypeError, ValueError, OverflowError, OSError):
        published_at = datetime.now().astimezone()
    video_store.add_publication(row["script_id"], version=row["version"], render_id=row["render_id"],
        url=url, published_on=published_at.date().isoformat(), title=info.get("title") or row["payload"]["title"],
        note="平台管理确认视频已公开，自动回写发布记录。", recorded_by="platform-manager",
        account_uid=row["uid"], account_name=account().get("name", ""))
    db.update_upload(task_id, status="PUBLISHED", bvid=bvid)
    return db.get_upload(task_id)


GONE_CODES = (-404, 62002, 62004)  # the video page answers "nothing here" / "not visible"


def mark_removed(task_id: str) -> dict:
    """Record a submission the creator deleted on B站, after checking it exists nowhere on the account.

    The creator list, searched by title, still shows pending and rejected submissions; the video page
    finds a public one whose title was edited. Only when both miss it, by an explicit answer, is it gone."""
    task = upload_task(task_id)
    if task["status"] not in ("SUBMITTED", "PUBLISHED") or not task["bvid"]:
        raise Error("只有已投稿的稿件可以标记为已在 B 站删除。")
    api = client(task["uid"])
    listed = api.archives(max_pages=10, keyword=task["payload"]["title"])
    if any((row.get("Archive") or row).get("bvid") == task["bvid"] for row in listed["archives"]):
        raise Error(f"{task['bvid']} 仍在 B 站稿件列表里，没有标记。")
    if not listed.get("complete"):
        raise Error("同标题的稿件太多，没能读完，没有标记。")
    try:
        api.owned_video(task["bvid"], task["uid"])
    except bilibili.BilibiliRejected as exc:
        if exc.code not in GONE_CODES:
            raise
    else:
        raise Error(f"{task['bvid']} 在 B 站仍可访问，没有标记。")
    store().mark_removed(task_id, task["uid"], f"{task['bvid']} 已在 B 站删除")
    for record in video_store.list_publications(task["script_id"]):
        if task["bvid"] in (record.get("url") or ""):
            video_store.delete_publication(record["id"])  # the link is dead; restorable like any removed record
    return upload_task(task_id)


def reupload(task_id: str) -> dict:
    """Queue a deleted submission again, as a new manual upload of the same render and text."""
    with account_lock():
        task = upload_task(task_id)
        if task["status"] != "REMOVED":
            raise Error("只有已在 B 站删除的投稿可以重新投稿。")
        eligible(task["render_id"])
        if not store().requeue_removed(task_id, task["uid"]):
            raise Error("这条投稿的状态已经变化，请刷新后再试。")
        return upload_task(task_id)


def lookup_upload(task_id: str) -> dict:
    """Explicit read-only recovery shares the same lock as the automatic uploader."""
    with account_lock():
        return _lookup_upload(task_id)


def _lookup_upload(task_id: str) -> dict:
    from web.services.bilibili_recovery import matching_archives

    db = store()
    row = upload_task(task_id)
    if row["status"] in ("SUBMITTED", "UNKNOWN") and row["bvid"]:
        return reconcile(task_id, row["bvid"])
    if row["status"] != "UNKNOWN":
        return row
    count = row["recovery"].get("lookup_count", 0)+1
    now = time.time()
    schedule = {"lookup_count": count, "last_checked_at": now,
                "next_lookup_at": now+min(3600, 60*2**min(count, 6))}
    try:
        result = client(row["uid"]).archives(keyword=row["payload"]["title"])
        matches = matching_archives(row, result["archives"])
    except Error as exc:
        db.update_recovery(task_id, {**schedule, "note": str(exc)}, expected="UNKNOWN")
        return db.get_upload(task_id)
    if len(matches) == 1:
        match = matches[0]
        # A receipt already attached to another local task cannot be claimed a second time.
        if db.attach_upload_receipt(task_id, row["uid"], match["bvid"]):
            db.update_recovery(task_id, {**schedule, "note": "已匹配 B 站稿件，等待公开确认。",
                                         "matched_bvid": match["bvid"]}, expected="SUBMITTED")
            try:
                saved = reconcile(task_id, match["bvid"])
                if saved["status"] == "PUBLISHED":
                    db.update_recovery(task_id, {"note": "已匹配 B 站稿件并恢复发布记录。"}, expected="PUBLISHED")
            except Error:
                pass  # Retain the known receipt and let normal publication polling continue.
            return db.get_upload(task_id)
        else:
            matches.append(match)
    note = ("匹配到多条或已被其他任务关联的稿件，保留原任务等待进一步核对。" if matches else
            f"已按标题核对 B 站稿件（{result['pages']} 页），暂未找到匹配；继续自动核对，尚未重新投稿。")
    db.update_recovery(task_id, {**schedule, "note": note}, expected="UNKNOWN")
    return db.get_upload(task_id)


def _recover_unknown_uploads(owner: str) -> None:
    now = time.time()
    for row in store().unknown_uploads(owner):
        recovery = row["recovery"]
        if row["updated_at"] > now-60 or recovery.get("next_lookup_at", 0) > now:
            continue
        _lookup_upload(row["id"])
        break  # Bound each worker pass; persist backoff so restarts do not cause a polling storm.


def retry_first_comment(task_id: str, *, pin: bool = False) -> dict:
    """Retry only an explicitly rejected send, or pin the already confirmed comment."""
    with account_lock():
        row = upload_task(task_id)
        if not store().retry_first_comment(task_id, row["uid"], pin=pin):
            raise Error("只能重试明确失败的评论，或已发送评论的置顶；发送结果待核对时请先到视频下检查。")
        return upload_task(task_id)


def process_first_comment(task: dict) -> None:
    """Called under the worker lock, after publication; persist the comment ID before pinning."""
    db = store()
    config = task["payload"].get("first_comment", {})
    if not config.get("enabled") or task["status"] != "PUBLISHED":
        return
    api = client(task["uid"])
    try:
        info = api.owned_video(task["bvid"], task["uid"])
        if info.get("state") != 0:
            raise Error("视频尚未公开，首条评论继续等待。")
    except Error as exc:
        db.first_comment_preflight_error(task["id"], str(exc), pin=task["first_comment_status"] == "SENT")
        return
    if task["first_comment_status"] == "WAITING":
        if not db.claim_first_comment(task["id"], task["uid"]):
            return
        try:
            rpid = api.post_comment(info, config["text"])
        except bilibili.BilibiliRejected as exc:
            db.finish_first_comment(task["id"], status="FAILED", error=str(exc))
            return
        except Exception:
            db.finish_first_comment(task["id"], status="UNKNOWN",
                                    error="评论发送结果未确认，请到视频下核对；系统不会自动重发。")
            return
        db.finish_first_comment(task["id"], status="SENT", rpid=rpid)
    task = db.get_upload(task["id"])
    if config.get("pin") and db.claim_first_comment_pin(task["id"], task["uid"]):
        try:
            api.pin_comment(info, task["first_comment_rpid"])
        except bilibili.BilibiliRejected as exc:
            db.finish_first_comment_pin(task["id"], status="FAILED", error=str(exc))
        except Exception:
            db.finish_first_comment_pin(task["id"], status="UNKNOWN", error="置顶结果未确认，可检查后仅重试置顶。")
        else:
            db.finish_first_comment_pin(task["id"], status="PINNED")


def topics_of(script_ids: list[str]) -> dict[str, dict]:
    """Per script: the B站 heat its topic was picked with (0 when none), its topic id, and for an extra angle
    (core/video/angles.py) the id of the item it retells."""
    if not script_ids:
        return {}
    with video_store.connect() as conn:
        rows = conn.execute("SELECT id, COALESCE(json_extract(topic,'$.heat.score'),0) AS heat, "
                            "json_extract(topic,'$.id') AS topic_id, json_extract(topic,'$.base_id') AS base_id FROM scripts "
                            "WHERE id IN (SELECT value FROM json_each(?))", (json.dumps(sorted(set(script_ids))),)).fetchall()
    return {row["id"]: {"heat": int(row["heat"] or 0), "topic_id": row["topic_id"] or "", "base_id": row["base_id"] or ""}
            for row in rows}


def heat_of(script_ids: list[str]) -> dict[str, int]:
    """B站 heat each script's topic was picked with (core/video/heat.py); 0 when it had none."""
    return {sid: info["heat"] for sid, info in topics_of(script_ids).items()}


def base_pending(upload: dict, pending: list[dict], topics: dict[str, dict], now: float) -> bool:
    """An extra angle waits while the video that tells its item is still queued or not yet public on its timer."""
    base = (topics.get(upload["script_id"]) or {}).get("base_id")
    if not base:
        return False
    return any((topics.get(u["script_id"]) or {}).get("topic_id") == base
               and (u["status"] == "QUEUED" or float(u["payload"].get("dtime") or 0) > now) for u in pending)


def by_priority(waiting: list[dict], heat: dict[str, int] | None = None) -> list[dict]:
    """The order queued uploads go out (11-10, 发片优先级): what a person queued by hand first, then the hottest topic,
    then the oldest. A topic told from several angles shares its heat, so its videos follow one another in order."""
    heat = heat_of([u["script_id"] for u in waiting]) if heat is None else heat
    return sorted(waiting, key=lambda u: (bool(u["payload"].get("automatic")), -heat.get(u["script_id"], 0), u["created_at"]))


def tick() -> None:
    """Single worker across threads/processes; unknown submissions are never automatically retried."""
    folder = directory()
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / "worker.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        db = store()
        db.recover_uploads()
        if not account().get("connected"):
            return
        owner = uid()
        config = db.settings()
        if config["auto_upload"]:
            for candidate in candidates():
                if (candidate.get('local_video') or candidate["archived"] or candidate["script_status"] != "APPROVED" or
                    candidate["version"] != candidate["approved_version"] or candidate["version"] != candidate["current_version"]):
                    continue
                try:
                    if _timestamp(candidate["completed_at"]) < config["auto_since"]:
                        continue
                    pack = preview(candidate["render_id"])
                    # Reuse the pipeline's quality hold; freshness warnings also hold unattended publishing.
                    if pack["credibility"]["tier"] in ("serious", "verify", "unaudited") or pack["freshness_warnings"]:
                        _log_once(logging.INFO, candidate["render_id"], "held: credibility %s, freshness %s"
                                  % (pack["credibility"]["tier"], pack["freshness_warnings"]))
                        continue
                    enqueue(candidate["render_id"], pack, automatic=True)
                except (Error, ValueError, TypeError) as exc:
                    _log_once(logging.WARNING, candidate["render_id"], f"skipped: {exc}")
                    continue
        task, dtime = None, None
        pending = db.pending_uploads(owner)
        queued = [u for u in pending if u["status"] == "QUEUED"]
        topics = topics_of([u["script_id"] for u in pending])
        heat = {sid: info["heat"] for sid, info in topics.items()}
        for waiting in by_priority(queued, heat):
            if not waiting["payload"].get("automatic"):
                task = waiting
                break
            window = config.get("publish_window") or {}
            angle = bool((topics.get(waiting["script_id"]) or {}).get("base_id"))
            if angle and base_pending(waiting, pending, topics, time.time()):
                if not waiting["payload"].get("held"):
                    db.hold_upload(waiting["id"], "等同一选题讲发布的那条先公开，再按间隔接着发。")
                continue
            # an extra angle follows its item instead of taking an evening-peak slot of its own
            dtime = publish_at(time.time(), window, _publish_times(db, owner, waiting["id"]),
                               held=bool(waiting["payload"].get("held")),
                               heat=0 if angle else heat.get(waiting["script_id"], 0))
            if dtime != HOLD:
                task = waiting
                break
            if not waiting["payload"].get("held"):
                db.hold_upload(waiting["id"], held_note(window))
        if task:
            try:
                render, _, _ = eligible(task["render_id"], automatic=bool(task["payload"].get("automatic")))
                if video_store.has_publication(task["script_id"], task["version"], "bilibili", owner,
                                               deleted=tuple(task["payload"].get("removed_bvids") or ())):
                    raise Error("这个版本已经补录发布，已停止重复投稿。")
                verify()
                if not bilibili.biliup_binary():
                    raise Error("未找到 biliup。")
                work_dir = folder / "uploads" / task["id"]
                work_dir.mkdir(parents=True, exist_ok=True)
                work_dir.chmod(0o700)
                # Cover extraction has no remote side effects; failures here are safe to retry.
                cover = bilibili.prepare_cover(video_render.safe_file(render, "video.mp4"), work_dir, task["payload"].get("cover", ""))
            except Error as exc:
                db.update_upload(task["id"], status="FAILED", error=str(exc), expected="QUEUED")
                return
            if task["payload"].get("automatic"):
                if not db.schedule_upload(task["id"], dtime):
                    return
                task = db.get_upload(task["id"])
            if not db.update_upload(task["id"], status="UPLOADING", expected="QUEUED"):
                return
            db.update_recovery(task["id"], {"started_at": time.time(), "phase": "upload",
                                          "lookup_count": 0, "next_lookup_at": 0, "note": ""}, expected="UPLOADING")
            credential = credentials()
            try:
                bvid = bilibili.upload(credential, video_render.safe_file(render, "video.mp4"), {**task["payload"], "cover": str(cover)}, work_dir)
                dtime = task["payload"].get("dtime")
                db.update_upload(task["id"], status="SUBMITTED", bvid=bvid, error=scheduled_note(dtime) if dtime else "")
            except bilibili.UploadFailed as exc:
                db.update_upload(task["id"], status="FAILED", error=str(exc))
            except Error as exc:
                db.update_upload(task["id"], status="UNKNOWN", error=str(exc))
            except Exception:
                db.update_upload(task["id"], status="UNKNOWN", error="投稿结果需核对，请到创作中心检查并回填 BV 号。")
            finally:
                try:
                    evidence = json.loads((work_dir / "diagnostic.json").read_text())
                    current = db.get_upload(task["id"])
                    db.update_recovery(task["id"], evidence, expected=current["status"])
                except (OSError, ValueError):
                    pass
                if credential and bilibili.upload_authorized(credential):
                    bilibili.private_json(folder / "credentials.json", credential)
        _recover_unknown_uploads(owner)
        # Submission acceptance isn't publication. Keep polling until the owned video becomes public.
        for task in db.pending_uploads(owner):
            if float(task["payload"].get("dtime") or 0) > time.time():
                continue  # scheduled: the platform keeps it private until then
            if task["status"] == "SUBMITTED" and task["updated_at"] < time.time() - 60:
                try:
                    reconcile(task["id"], task["bvid"])
                except Error:
                    pass
                break
        comment_tasks = db.first_comment_tasks(owner)
        if comment_tasks:
            process_first_comment(comment_tasks[0])
        collection_tasks = db.collection_tasks(owner, retry_after=COLLECTION_RETRY_SECONDS)
        if collection_tasks:
            process_collection(collection_tasks[0])
        if config["sync_comments"]:
            due = [v for v in db.videos(owner) if (v["last_attempt_at"] or v["synced_at"] or 0) < time.time() - 300]
            if due:
                sync(due[0]["bvid"])
