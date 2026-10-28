"""Bilibili publishing outbox, account-bound comment inbox, and opt-in automatic uploads."""

from __future__ import annotations

import fcntl
import json
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from core.platform_store import PlatformStore
from core.video import content_guard, credibility, store as video_store
from web.services import bilibili, video_render
from web.settings import get_settings

Error = bilibili.BilibiliError


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
            "adaptation": adaptation, "tid": store().settings()["tid"], "warnings": warnings,
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
    cover = next((p for name in ("cover.png", "cover.jpg") if (p := video_render.safe_file(render, name))), None)
    clean = {"title": title, "tags": tags, "description": description, "tid": int(payload["tid"]),
             "cover": str(cover.resolve()) if cover else "", "automatic": automatic,
             "first_comment": clean_first_comment(payload.get("first_comment", {}))}
    if automatic:  # tick already owns the same worker lock
        return store().enqueue(owner, render, clean)
    with identity_lock():
        if uid() != owner:
            raise Error("登录账号已改变，请刷新页面后重试。")
        return store().enqueue(owner, render, clean)


def clean_first_comment(value: dict) -> dict:
    """Validate an opt-in message; keep disabled template text available for later editing."""
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
        db.update_upload(task_id, status="SUBMITTED", bvid=bvid, error="平台尚未公开这条投稿，请在创作中心查看审核状态。")
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
                    completed = datetime.fromisoformat(candidate["completed_at"]).timestamp()
                    if completed < config["auto_since"]:
                        continue
                    pack = preview(candidate["render_id"])
                    # Reuse the pipeline's quality hold; freshness warnings also hold unattended publishing.
                    if pack["credibility"]["tier"] in ("serious", "verify", "unaudited") or pack["freshness_warnings"]:
                        continue
                    enqueue(candidate["render_id"], {**pack, "tid": config["tid"]}, automatic=True)
                except (Error, ValueError, TypeError):
                    continue
        queued = [u for u in db.pending_uploads(owner) if u["status"] == "QUEUED"]
        if queued:
            task = queued[0]
            try:
                render, _, _ = eligible(task["render_id"], automatic=bool(task["payload"].get("automatic")))
                if video_store.has_publication(task["script_id"], task["version"], "bilibili", owner):
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
            if not db.update_upload(task["id"], status="UPLOADING", expected="QUEUED"):
                return
            db.update_recovery(task["id"], {"started_at": time.time(), "phase": "upload",
                                          "lookup_count": 0, "next_lookup_at": 0, "note": ""}, expected="UPLOADING")
            credential = credentials()
            try:
                bvid = bilibili.upload(credential, video_render.safe_file(render, "video.mp4"), {**task["payload"], "cover": str(cover)}, work_dir)
                db.update_upload(task["id"], status="SUBMITTED", bvid=bvid)
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
            if task["status"] == "SUBMITTED" and task["updated_at"] < time.time() - 60:
                try:
                    reconcile(task["id"], task["bvid"])
                except Error:
                    pass
                break
        comment_tasks = db.first_comment_tasks(owner)
        if comment_tasks:
            process_first_comment(comment_tasks[0])
        if config["sync_comments"]:
            due = [v for v in db.videos(owner) if (v["last_attempt_at"] or v["synced_at"] or 0) < time.time() - 300]
            if due:
                sync(due[0]["bvid"])
