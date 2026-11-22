"""Account connection, guarded posting, auto-upload policy, and confirmed publication recording for Douyin."""

from __future__ import annotations

import base64
from contextlib import contextmanager
from datetime import datetime
import fcntl
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import time
from urllib.parse import quote

import yaml

from core.douyin import DouyinError, UID, WORK_ID, work_id, clean_first_comment, clean_publish_window
from core.douyin_store import DouyinStore, ACTIVE
from core.social.verification import check_progress, lookup_revision, MAX_UNRESOLVED_CHECKS
from core.video import credibility, store as video_store
from scripts.douyin_worker import private_json
from web.services import platform_manager, video_render
from web.services.publish_cover import publish_covers
from web.settings import get_settings

PIN = "3847fff01f5434a8a32829833163cd6723cabc59"
ROOT = Path(__file__).resolve().parents[2]
UNKNOWN_LOOKUP_DAYS = 7
UNKNOWN_LOOKUP_MAX = 10
logger = logging.getLogger(__name__)


def directory() -> Path:
    return Path(get_settings().paths.state_dir).resolve() / "platforms/douyin"


def store() -> DouyinStore:
    return DouyinStore(get_settings().paths.state_dir)


def runtime() -> dict:
    base = Path(get_settings().paths.state_dir).resolve() / "platforms/runtime/social-auto-upload"
    source = base / PIN / "source"
    python = source / ".venv/bin/python"
    binaries = list((base / "browsers").glob("chromium-*/chrome-*/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"))
    binaries += list((base / "browsers").glob("chromium-*/chrome-linux*/chrome"))
    binaries += list((base / "browsers").glob("chromium-*/chrome-win*/chrome.exe"))
    ready = python.is_file() and (source / "conf.py").is_file() and bool(binaries)
    return {"ready": ready, "source": source, "python": python, "browsers": base / "browsers"}


@contextmanager
def dispatch_lock():
    root = directory()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (root / "dispatch.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def account() -> dict:
    path = directory() / "account.json"
    if not path.is_file():
        return {"connected": False}
    data = json.loads(path.read_text())
    return {"connected": True, **{k: data[k] for k in ("uid", "name", "verified_at") if k in data}}


def uid() -> str:
    value = account().get("uid", "")
    if not UID.fullmatch(value):
        raise DouyinError("请先连接抖音账号。")
    return value


def settings(owner: str | None = None) -> dict:
    owner_id = account().get("uid", "") if owner is None else owner
    db = store()
    return {"first_comment": db.comment_settings(owner_id), **db.policy_settings(owner_id)}


def save_settings(value: dict) -> dict:
    with dispatch_lock():
        owner = uid()
        db = store()
        current = db.policy_settings(owner)
        patch: dict = {}
        if "auto_upload" in value:
            enabled = bool(value["auto_upload"])
            patch["auto_upload"] = enabled
            if enabled and not current.get("auto_upload"):
                patch["auto_since"] = time.time()
        if "headless" in value:
            patch["headless"] = bool(value["headless"])
        if "publish_window" in value:
            patch["publish_window"] = clean_publish_window(value["publish_window"])
        if "first_comment" in value:
            db.save_comment_settings(owner, clean_first_comment(value["first_comment"]))
        if patch:
            db.save_policy_settings(owner, patch)
        return settings(owner)


def automatic_upload_excluded(script: dict) -> bool:
    """Apply the Douyin publishing hold to the research job recorded on the script."""
    path = Path(get_settings().paths.config_dir) / "douyin_auto_upload.yaml"
    try:
        cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
        excluded_jobs = (cfg or {}).get("excluded_jobs") or []
        if not isinstance(excluded_jobs, list) or not all(isinstance(job, str) for job in excluded_jobs):
            raise ValueError("excluded_jobs must be a list of job names")
    except FileNotFoundError:
        return False
    except (OSError, yaml.YAMLError, TypeError, ValueError) as exc:
        raise DouyinError("抖音自动投稿排除配置无法读取，请检查配置。") from exc
    topic = script.get("topic") or {}
    context = script.get("content_context") or {}
    jobs = (
        context.get("job"),
        (topic.get("episode") or {}).get("job"),
        (topic.get("matrix_dedup") or {}).get("job"),
        topic.get("knowledge_job"),
    )
    return any(job in excluded_jobs for job in jobs)


def window_allows(now: float, window: dict, taken: list[float]) -> bool:
    """Return True when an automatic Douyin upload can be dispatched now."""
    if not window or not window.get("enabled"):
        return True
    start, end = int(window.get("start", 8)), int(window.get("end", 23))
    spacing = max(1, int(window.get("spacing") or 30)) * 60
    if not 0 <= start < end <= 24:
        return True
    local = datetime.fromtimestamp(now).astimezone()
    if not (start <= local.hour < end):
        return False
    return all(abs(now - t) >= spacing for t in taken)


def held_note(window: dict) -> str:
    return (f"等待发布时段：{int(window.get('start', 8)):02d}:00–{int(window.get('end', 23)):02d}:00 内按间隔依次投稿，"
            f"每条至少间隔 {int(window.get('spacing') or 30)} 分钟")


def public_job(row: dict, *, comments: dict | None = None, checks: dict | None = None) -> dict:
    result = {**{k: row[k] for k in ("id", "kind", "status", "phase", "error", "work_id", "script_id", "version", "render_id", "created_at", "updated_at")},
              "payload": {k: row["payload"][k] for k in ("title", "description", "tags", "first_comment") if k in row["payload"]},
              "automatic": bool(row["payload"].get("automatic")),
              "url": f"https://www.douyin.com/video/{row['work_id']}" if WORK_ID.fullmatch(row["work_id"]) else "",
              "can_retry": row["status"] in ("FAILED", "CANCELLED") and bool(row["result"].get("confirmed_not_submitted")),
              "has_diagnostic": (directory() / "jobs" / row["id"] / "error.png").is_file(),
              "recovery": row["result"].get("recovery") or {}}
    if row['kind'] == 'UPLOAD':
        if comments is not None:
            comment = comments.get(row['id'])
        else:
            found_comments = store().list(uid=row['uid'], kind='FIRST_COMMENT', parent_id=row['id'])
            comment = found_comments[0] if found_comments else None
        result['first_comment'] = None if not comment else {
            **{k: comment[k] for k in ('id', 'status', 'phase', 'error', 'created_at', 'updated_at')},
            'text': comment['payload']['text'], 'comment_id': comment['result'].get('comment_id', ''),
            'read_back': bool(comment['result'].get('read_back')),
            'can_retry': comment['status'] in ('FAILED', 'CANCELLED') and bool(comment['result'].get('confirmed_not_submitted'))}
        if checks is None:
            checks = verification_checks(store().list(uid=row['uid'], kind='RECONCILE', parent_id=row['id']))
        check = checks.get(row['id'])
        tracking = row['result'].get('verification', {})
        result['verification'] = {'automatic': row['status'] == 'SUBMITTED' and bool(row['work_id']) and not tracking.get('exhausted'),
                                  **tracking, 'running': bool(check and check['status'] in ACTIVE),
                                  'status': (check or {}).get('status', ''), 'checked_at': (check or {}).get('updated_at'),
                                  'next_check_at': tracking.get('next_check_at'),
                                  'reason': tracking.get('reason') or (check or {}).get('error') or (check or {}).get('result', {}).get('check_reason', ''),
                                  'count': (check or {}).get('check_count', 0)}
    return result


def verification_checks(rows: list[dict]) -> dict:
    result = {}
    for row in reversed(rows):
        prior = result.get(row['parent_id'], {})
        result[row['parent_id']] = {**row, 'check_count': prior.get('check_count', 0) + 1,
                                  'consecutive_failures': prior.get('consecutive_failures', 0) + 1 if row['status'] == 'FAILED' else 0}
    return result


def settle_verification(db: DouyinStore) -> None:
    """Retain failed checks, back off, and stop a persistent unresolved lookup."""
    parents = db.list(kind='UPLOAD', statuses=('SUBMITTED', 'UNKNOWN'))
    if not parents:
        return
    checks = verification_checks(db.list(kind='RECONCILE', parent_ids=[p['id'] for p in parents]))
    for parent in parents:
        latest = checks.get(parent['id'])
        previous = parent['result'].get('verification', {})
        if (not latest or latest['status'] in ACTIVE or previous.get('last_check_id') == latest['id']
                or parent['uid'] != latest['uid']):
            continue
        error = latest['result'].get('lookup_error', {})
        state = error.get('state', 'unavailable') if latest['status'] == 'FAILED' else (
            'published' if latest['result'].get('public') else 'unknown')
        tracking = check_progress(previous, state, latest['updated_at'], lookup_revision({'work_id': parent['work_id']}))
        if not previous and latest['consecutive_failures'] >= MAX_UNRESOLVED_CHECKS:
            tracking.update(attempts=latest['check_count'], attempts_for_input=latest['consecutive_failures'],
                            exhausted=True, next_check_at=None)
            tracking['reason'] = '原作品已多次回查失败，回执已保留；处理回查问题后可继续核对原投稿'
        tracking['last_check_id'] = latest['id']
        db.update(parent['id'], expected=parent['status'], result={**parent['result'], 'verification': tracking})


def save_first_comment_template(value: dict) -> dict:
    with dispatch_lock():
        return store().save_comment_settings(uid(), clean_first_comment(value))


def add_first_comment(key: str, value: dict) -> dict:
    clean = clean_first_comment(value)
    if not clean['enabled']:
        raise DouyinError('请启用并填写作者评论。')
    with dispatch_lock():
        parent = upload_task(key)
        if parent['status'] not in ('SUBMITTED', 'PUBLISHED') or not parent['work_id']:
            raise DouyinError('取得可靠的投稿作品 ID 后才能安排作者评论。')
        child = _ensure_comment(store(), parent, clean)
        if child['payload']['text'] != clean['text']:
            raise DouyinError('此作品已有另一份作者评论记录，请先查看；不会替换或重复发送。')
    tick()
    return public_job(store().get(key))


def first_comment_action(key: str, action_name: str) -> dict:
    with dispatch_lock():
        parent = upload_task(key)
        db = store()
        child = db.comments(parent['uid']).get(key)
        if not child:
            raise DouyinError('没有这条作者评论任务。')
        if action_name == 'check' and child['status'] in ('UNKNOWN', 'SENT'):
            active = [r for r in db.list(uid=parent['uid'], kind='COMMENT_CHECK', statuses=ACTIVE) if r['parent_id'] == child['id']]
            if not active:
                db.create('COMMENT_CHECK', parent['uid'], parent_id=child['id'])
        elif action_name == 'cancel' and child['status'] in ('WAITING', 'QUEUED'):
            db.update(child['id'], expected=child['status'], status='CANCELLED', phase='已取消', result={'confirmed_not_submitted': True})
        elif action_name == 'retry' and child['status'] in ('FAILED', 'CANCELLED') and child['result'].get('confirmed_not_submitted'):
            db.update(child['id'], expected=child['status'], status='WAITING', phase='等待重新检查', error='', result={})
        else:
            raise DouyinError('仅能取消尚未发送的评论，或重试确认未提交的评论；结果待核对时不能重发。')
    tick()
    return public_job(store().get(key))


def _ensure_comment(db: DouyinStore, parent: dict, option: dict | None = None) -> dict | None:
    option = option or parent['payload'].get('first_comment', {})
    if not option.get('enabled'):
        return None
    clean = clean_first_comment(option)
    child = db.create('FIRST_COMMENT', parent['uid'], {'text': clean['text']},
                      render={'id': parent['render_id'], 'script_id': parent['script_id'], 'version': parent['version']}, parent_id=parent['id'])
    if parent['status'] == 'PUBLISHED' and child['status'] == 'WAITING':
        _activate_comment(db, child, parent)
        child = db.get(child['id'])
    return child


def _activate_comment(db: DouyinStore, child: dict, parent: dict) -> None:
    if not db.activate_comment(child['id'], child['uid'], parent['work_id']):
        db.update(child['id'], expected='WAITING', status='FAILED',
                  phase='已有同作品评论记录', error='同一账号的这条作品已有作者评论任务；已阻止重复发送。',
                  result={'confirmed_not_submitted': False, 'duplicate_work': True})


def start_login() -> dict:
    if not runtime()["ready"]:
        raise DouyinError("抖音上传组件尚未准备好，请先完成本机运行环境安装。")
    with dispatch_lock():
        db = store()
        active = db.list(statuses=ACTIVE)
        login = next((r for r in active if r["kind"] == "LOGIN"), None)
        if login:
            return public_job(login)
        if active:
            raise DouyinError("抖音任务正在运行，请完成后再连接或更换账号。")
        row = db.create("LOGIN")
        private_json(directory() / "login.json", {"id": row["id"]})
    tick()
    return public_job(store().get(row["id"]))


def login_status(key: str) -> dict:
    if not re.fullmatch(r"[a-f0-9]{32}", key):
        raise DouyinError("没有这次账号连接记录。")
    row = store().get(key)
    if not row or row["kind"] != "LOGIN":
        raise DouyinError("没有这次账号连接记录。")
    result = public_job(row)
    path = directory() / "jobs" / key / "qrcode.png"
    if row["status"] == "RUNNING" and path.is_file():
        raw = path.read_bytes()
        if len(raw) <= 2_000_000:
            result["image"] = "data:image/png;base64," + base64.b64encode(raw).decode()
    result["connected"] = row["status"] == "DONE" and account().get("uid") == row["result"].get("uid")
    return result


def verify() -> dict:
    with dispatch_lock():
        owner = uid()
        if store().list(statuses=ACTIVE):
            raise DouyinError("已有抖音任务在运行，请稍后检查。")
        row = store().create("VERIFY", owner)
    tick()
    return public_job(store().get(row["id"]))


def disconnect() -> dict:
    with dispatch_lock():
        if store().list(statuses=ACTIVE):
            raise DouyinError("抖音任务正在运行，请完成后再断开账号。")
        owner = uid()
        store().save_policy_settings(owner, {"auto_upload": False})
        (directory() / "account.json").unlink(missing_ok=True)
        (directory() / "accounts" / owner / "storage.json").unlink(missing_ok=True)
    return {"connected": False}


def candidate_catalog() -> tuple[list[dict], dict]:
    from core.video import content_guard

    owner = account().get("uid", "")
    queued = {(r["script_id"], r["version"]) for r in store().list(uid=owner, kind="UPLOAD")}
    with video_store.connect() as conn:
        rows = conn.execute("SELECT r.id AS render_id,r.script_id,r.version,r.title,r.completed_at,r.artifacts,"
                            "s.archived,s.status AS script_status,s.current_version,s.approved_version,"
                            "(json_extract(s.topic,'$.local_video') IS NOT NULL) AS local_video,"
                            f"({content_guard.PUBLISHABLE_SQL}) AS publishable,"
                            "EXISTS(SELECT 1 FROM publications p WHERE p.script_id=r.script_id AND p.version=r.version "
                            "AND p.platform='douyin' AND p.removed_at='' AND (p.account_uid=? OR p.account_uid='')) AS published "
                            "FROM renders r JOIN scripts s ON s.id=r.script_id "
                            "WHERE r.status='COMPLETED' "
                            "ORDER BY r.completed_at DESC,r.created_at DESC", (owner,)).fetchall()
    stats = {"completed_renders": len(rows), "available_versions": 0, "published_versions": 0,
             "queued_versions": 0, "unavailable_versions": 0, "ready_versions": 0}
    all_versions = {(row["script_id"], row["version"]) for row in rows}
    seen, result = set(), []
    has_video = video_render.file_checker("video.mp4")
    for row in rows:
        key = (row["script_id"], row["version"])
        if key in seen:
            continue
        render = {"id": row["render_id"], "artifacts": json.loads(row["artifacts"])}
        if not has_video(render):
            continue
        seen.add(key)
        stats["available_versions"] += 1
        if row["published"]:
            stats["published_versions"] += 1
        elif key in queued:
            stats["queued_versions"] += 1
        elif row["publishable"]:
            result.append({k: row[k] for k in ("render_id", "script_id", "version", "title", "completed_at",
                                               "archived", "script_status", "current_version", "approved_version", "local_video")})
    stats["unavailable_versions"] = len(all_versions - seen)
    stats["ready_versions"] = len(result)
    return result, stats


def candidates() -> list[dict]:
    return candidate_catalog()[0]


def cover_file(render_id: str, orientation: str, title: str = "") -> Path:
    if orientation not in ("portrait", "landscape"):
        raise DouyinError("不支持的封面比例。")
    render, _, _ = platform_manager.eligible(render_id)
    covers = publish_covers(render, (title or render.get("title") or "").strip(), portrait=True)
    raw = covers.get("cover_portrait" if orientation == "portrait" else "cover", "")
    if not raw or not Path(raw).is_file():
        raise DouyinError("当前成片尚未生成对应比例的封面。")
    return Path(raw).resolve()


def diagnostic_file(key: str) -> Path:
    upload_task(key)
    path = directory() / "jobs" / key / "error.png"
    if not path.is_file():
        raise DouyinError("这条任务没有失败截图记录。")
    return path.resolve()


def preview(render_id: str) -> dict:
    from web.services.distributions import preview_draft
    render, script, version = platform_manager.eligible(render_id)
    pack = video_render.publish_pack(script, version, platform_manager.column_for(script), render_id=render_id)
    from core.video.platform_publish import adapt
    from core.social.publish_text import default_comment
    adaptation = adapt(pack, "douyin")
    freshness_warnings = list(pack.get("freshness", {}).get("warnings", []))
    warnings = list(pack.get("warnings", [])) + freshness_warnings
    if len(adaptation["payload"]["title"]) > 30:
        warnings.append("原发布包标题超过抖音 30 字限制，请编辑后投稿。")
    if len(adaptation["payload"]["description"]) > 1000:
        warnings.append("原发布包简介超过抖音 1000 字限制，请编辑后投稿。")
    owner = account().get("uid", "")
    existing = next((public_job(u) for u in (store().list(uid=owner, kind="UPLOAD") if owner else [])
                     if u["script_id"] == script["id"] and u["version"] == render["version"]), None)
    cover_title = quote(adaptation["payload"]["title"][:30], safe="")
    covers = {
        "portrait_url": f"/api/platforms/douyin/covers/{quote(render['id'], safe='')}/portrait?title={cover_title}",
        "landscape_url": f"/api/platforms/douyin/covers/{quote(render['id'], safe='')}/landscape?title={cover_title}",
    }
    return {**preview_draft(render, "douyin"), "render_id": render["id"], "script_id": script["id"], "version": render["version"],
            **adaptation["payload"], "adaptation": adaptation, "warnings": warnings,
            "freshness_warnings": freshness_warnings, "credibility": credibility.label(version),
            "covers": covers, "existing_upload": existing,
            "first_comment": default_comment(store().comment_settings(owner))}


def enqueue(render_id: str, payload: dict, *, automatic: bool = False) -> dict:
    owner = uid()
    if payload.get("account_uid") and payload["account_uid"] != owner:
        raise DouyinError("平台账号已改变，请刷新后重试。")
    if not runtime()["ready"]:
        raise DouyinError("抖音上传组件尚未准备好。")
    render, script, _ = platform_manager.eligible(render_id)
    if automatic:
        if (script.get("topic") or {}).get("local_video"):
            raise DouyinError("本地视频不加入全局自动投稿。")
        if automatic_upload_excluded(script):
            raise DouyinError("此研究链路已配置跳过抖音自动投稿。")
        if (script.get("archived") or script["status"] != "APPROVED" or
                script.get("approved_version") != render["version"] or script.get("current_version") != render["version"]):
            raise DouyinError("自动投稿只处理未归档文稿的当前定稿成片。")
    title, description = payload["title"].strip(), payload["description"].strip()
    tags = [v.strip().lstrip("#") for v in payload["tags"] if v.strip()]
    if not 1 <= len(title) <= 30 or len(description) > 1000 or len(tags) > 5 or any(len(t) > 20 or any(c in t for c in " ,，\n") for t in tags):
        raise DouyinError("抖音标题需为 1–30 字，简介最多 1000 字，话题最多 5 个，每个最多 20 字且不含分隔符。")
    from core.social.publish_text import combined_text
    if len(combined_text("douyin", title, description, tags)) > 1000:
        raise DouyinError("简介与话题合计超过 1000 字，请精简后投稿。")
    if video_store.has_publication(script["id"], render["version"], "douyin", owner):
        raise DouyinError("这个版本已记录抖音发布，请查看文稿发布记录。")
    clean = {"title": title, "description": description, "tags": tags, "automatic": automatic,
             "video_path": str(video_render.safe_file(render, "video.mp4").resolve()),
             **publish_covers(render, title, portrait=True),
             "first_comment": clean_first_comment(payload.get("first_comment", store().comment_settings(owner)))}
    if automatic:
        row = store().create("UPLOAD", owner, clean, render=render)
        _ensure_comment(store(), row)
        return public_job(store().get(row["id"]))
    with dispatch_lock():
        if uid() != owner or any(r["kind"] == "LOGIN" for r in store().list(statuses=ACTIVE)):
            raise DouyinError("账号正在更换，请刷新后重新投稿。")
        row = store().create("UPLOAD", owner, clean, render=render)
        _ensure_comment(store(), row)
    tick()
    return public_job(store().get(row["id"]))


def upload_task(key: str) -> dict:
    row = store().get(key)
    if not row or row["kind"] != "UPLOAD" or row["uid"] != uid():
        raise DouyinError("找不到当前抖音账号的这条投稿任务。")
    return row


def reconcile(key: str, value: str = "") -> dict:
    row = upload_task(key)
    target = work_id(value or row["work_id"])
    if row["work_id"] and row["work_id"] != target:
        raise DouyinError("已有平台回执，不能替换为另一条视频。")
    if row["status"] not in ("UNKNOWN", "SUBMITTED"):
        raise DouyinError("当前任务无需核对。")
    with dispatch_lock():
        existing = [r for r in store().list(statuses=ACTIVE) if r["parent_id"] == key and r["kind"] == "RECONCILE"]
        check = existing[0] if existing else store().create("RECONCILE", row["uid"], {"work_id": target}, parent_id=key)
    tick()
    return public_job(store().get(check["id"]))


def lookup_upload(key: str) -> dict:
    row = upload_task(key)
    if row["work_id"]:
        return reconcile(key)
    if row["status"] != "UNKNOWN":
        raise DouyinError("只有结果待核对且缺少作品 ID 的任务需要自动回查。")
    with dispatch_lock():
        existing = [r for r in store().list(statuses=ACTIVE) if r["parent_id"] == key and r["kind"] == "LOOKUP"]
        check = existing[0] if existing else store().create("LOOKUP", row["uid"], parent_id=key)
    tick()
    return public_job(store().get(check["id"]))


def mark_removed(key: str) -> dict:
    with dispatch_lock():
        row = upload_task(key)
        if row["status"] not in ("SUBMITTED", "PUBLISHED"):
            raise DouyinError("只有已投稿或已公开的抖音作品可以标记为已删除。")
        note = f"作品 {row['work_id'] or ''} 已标记在抖音删除，可按需重新投稿。".strip()
        if not store().mark_removed(key, row["uid"], note):
            raise DouyinError("这条投稿状态已变化，请刷新后再试。")
        for record in video_store.list_publications(row["script_id"]):
            if record.get("platform") == "douyin" and record.get("version") == row["version"]:
                if not row["work_id"] or row["work_id"] in (record.get("url") or ""):
                    video_store.delete_publication(record["id"])
    return public_job(store().get(key))


def reupload(key: str) -> dict:
    with dispatch_lock():
        row = upload_task(key)
        if row["status"] != "REMOVED":
            raise DouyinError("只有已标记删除的抖音投稿可以重新投稿。")
        render, _, _ = platform_manager.eligible(row["render_id"])
        video_path = str(video_render.safe_file(render, "video.mp4").resolve())
        if not store().requeue_removed(key, row["uid"], video_path):
            raise DouyinError("这条投稿状态已变化，请刷新后再试。")
    tick()
    return public_job(store().get(key))


def action(key: str, kind: str) -> dict:
    with dispatch_lock():
        row = upload_task(key)
        if kind == "cancel" and row["status"] == "QUEUED":
            store().update(key, expected="QUEUED", status="CANCELLED", result={"confirmed_not_submitted": True})
        elif kind == "retry" and row["status"] in ("FAILED", "CANCELLED") and row["result"].get("confirmed_not_submitted"):
            render, _, _ = platform_manager.eligible(row["render_id"])
            if video_store.has_publication(row["script_id"], row["version"], "douyin", row["uid"],
                                           deleted=tuple(row["payload"].get("removed_work_ids") or ())):
                raise DouyinError("这个版本已记录抖音发布，不能再次投稿。")
            payload = {**row["payload"], "video_path": str(video_render.safe_file(render, "video.mp4").resolve())}
            store().update(key, expected=row["status"], status="QUEUED", error="", result={}, payload=payload)
        else:
            raise DouyinError("只能取消排队任务，或重试已确认未提交的任务；结果不明时请先核对。")
    tick()
    return public_job(store().get(key))


def overview() -> dict:
    owner = account().get("uid", "")
    login_path = directory() / "login.json"
    active_login = json.loads(login_path.read_text()).get("id") if login_path.is_file() else None
    db = store()
    uploads = db.list(uid=owner, kind="UPLOAD") if owner else []
    comments = db.comments(owner) if owner else {}
    pending_ids = [u["id"] for u in uploads if u["status"] in ("SUBMITTED", "UNKNOWN")]
    checks = verification_checks(db.list(uid=owner, kind="RECONCILE", parent_ids=pending_ids)) if (owner and pending_ids) else {}
    recent_checks = db.list(uid=owner, kinds=("VERIFY", "RECONCILE", "LOOKUP"), limit=10) if owner else []
    available, summary = candidate_catalog() if owner else ([], None)
    return {"account": account(), "runtime_ready": runtime()["ready"], "login_id": active_login,
            "settings": settings(owner), "capabilities": {"first_comment": True, "pin_comment": False},
            "uploads": [public_job(r, comments=comments, checks=checks) for r in uploads],
            "checks": [public_job(r) for r in recent_checks],
            "candidates": available, "candidate_summary": summary}


def _settle(db: DouyinStore) -> None:
    settle_verification(db)
    path = directory() / "login.json"
    active = json.loads(path.read_text()).get("id") if path.is_file() else ""
    for row in db.list(statuses=("DONE",)):
        if row["kind"] == "LOGIN" and row["id"] == active:
            owner = row["result"]
            if not UID.fullmatch(owner.get("uid", "")):
                continue
            target = directory() / "accounts" / owner["uid"] / "storage.json"
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            temporary = directory() / "jobs" / row["id"] / "storage.json"
            if temporary.is_file():
                private_json(target, json.loads(temporary.read_text()))
            elif not target.is_file() or account().get("uid") != owner["uid"]:
                db.update(row["id"], status="FAILED", error="登录凭证没有保存完成，请重新扫码。")
                continue
            private_json(directory() / "account.json", {**owner, "verified_at": time.time()})
            temporary.unlink(missing_ok=True)
            path.unlink(missing_ok=True)
        elif row["kind"] == "VERIFY" and account().get("uid") == row["uid"]:
            private_json(directory() / "account.json", {**row["result"], "verified_at": row["updated_at"]})
            db.update(row["id"], status="RECORDED")
        elif row["kind"] == "COMMENT_CHECK":
            db.update(row["id"], status="RECORDED")
        elif row["kind"] == "LOOKUP":
            parent = db.get(row["parent_id"])
            receipt = row["result"]
            if parent and parent["uid"] == row["uid"] and parent["status"] == "UNKNOWN" and not parent["work_id"]:
                prev = parent["result"].get("recovery") or {}
                count = int(prev.get("lookup_count") or 0) + 1
                now = time.time()
                schedule = {"lookup_count": count, "last_checked_at": now,
                            "next_lookup_at": now + min(3600, 60 * 2 ** min(count, 6))}
                wid = str(receipt.get("work_id") or "")
                taken_elsewhere = bool(wid and any(
                    u["id"] != parent["id"] and u["work_id"] == wid
                    for u in db.list(uid=row["uid"], kind="UPLOAD")
                ))
                if receipt.get("matched") and WORK_ID.fullmatch(wid) and not taken_elsewhere:
                    db.update(
                        parent["id"], expected="UNKNOWN", status="SUBMITTED", work_id=wid,
                        phase="已自动匹配创作中心稿件，等待核对公开状态", error="",
                        result={**parent["result"], "accepted": True, "work_id": wid, "uid": row["uid"],
                                "recovery": {**schedule, "matched_work_id": wid, "note": "已自动匹配创作中心稿件，等待核对公开状态。"}},
                    )
                else:
                    note = ("匹配到多条或已被其他任务关联的作品，保留原任务等待人工核对。"
                            if (receipt.get("candidates", 0) > 1 or taken_elsewhere)
                            else f"已按标题回查创作中心（{receipt.get('pages', 1)} 页），暂未找到匹配；继续自动回查，不会重复投稿。")
                    db.update(parent["id"], expected="UNKNOWN",
                              result={**parent["result"], "recovery": {**schedule, "note": note}})
            db.update(row["id"], status="RECORDED")
        elif row["kind"] == "RECONCILE":
            parent = db.get(row["parent_id"])
            receipt = row["result"]
            if parent and parent["uid"] == row["uid"] and parent["status"] in ("UNKNOWN", "SUBMITTED"):
                if receipt.get("uid") == row["uid"] and WORK_ID.fullmatch(receipt.get("work_id", "")) and (not parent["work_id"] or parent["work_id"] == receipt["work_id"]):
                    db.update(parent["id"], status="SUBMITTED", work_id=receipt["work_id"], result={**parent['result'], **receipt},
                              error="" if receipt.get("public") else "账号归属已核对，尚未确认已公开。")
            db.update(row["id"], status="RECORDED")
    for row in db.list(kind="UPLOAD", statuses=("SUBMITTED",)):
        receipt = row["result"]
        if (not receipt.get("public") or receipt.get("uid") != row["uid"] or
                not WORK_ID.fullmatch(row["work_id"]) or
                receipt.get("work_id") != row["work_id"] or receipt.get("evidence") != "anonymous_video_detail"):
            continue
        when = receipt.get("published_at") or time.time()
        if not isinstance(when, (int, float)) or when > time.time() + 86400 or when < 946684800:
            when = time.time()
        video_store.add_publication(row["script_id"], version=row["version"], render_id=row["render_id"],
                                    platform="douyin", url=f"https://www.douyin.com/video/{row['work_id']}",
                                    published_on=datetime.fromtimestamp(when).astimezone().date().isoformat(),
                                    title=row["payload"]["title"], note="抖音账号归属和匿名公开状态已核对。", recorded_by="platform-manager",
                                    account_uid=row["uid"], account_name=account().get("name", "") if account().get("uid") == row["uid"] else "")
        db.update(row["id"], expected="SUBMITTED", status="PUBLISHED", phase="已公开并回写文稿", error="")
    children = db.list(kind='FIRST_COMMENT')
    for parent in db.list(kind='UPLOAD', statuses=('QUEUED', 'RUNNING', 'SUBMITTED', 'PUBLISHED')):
        if not any(c["parent_id"] == parent["id"] for c in children):
            _ensure_comment(db, parent)
    for child in children:
        parent = db.get(child['parent_id'])
        if parent and parent['uid'] == child['uid'] and parent['status'] == 'PUBLISHED' and child['status'] == 'WAITING':
            _activate_comment(db, child, parent)


def _shorten_douyin_title(title: str, limit: int = 30) -> str:
    """Fit a longer Bilibili title into Douyin's 30-char cap at natural punctuation when possible."""
    text = " ".join(title.split()).strip()
    if len(text) <= limit:
        return text
    best = ""
    for match in re.finditer(r"[：:|｜，,；;？?！!]|——", text):
        idx = match.start()
        if 8 <= idx <= limit:
            best = text[:idx].rstrip("：:|｜，,；; ")
    if best and 8 <= len(best) <= limit:
        return best
    return text[: limit - 1].rstrip("：:|｜，,；; ") + "…"


def _fit_douyin_auto_pack(pack: dict) -> dict:
    """Adapt title/description/tags for unattended Douyin publishing without losing the full title."""
    from core.social.publish_text import combined_text

    original_title = str(pack.get("title") or "").strip()
    title = _shorten_douyin_title(original_title, 30)
    description = str(pack.get("description") or "").strip()
    if original_title and title != original_title and not description.startswith(original_title):
        description = f"{original_title}\n\n{description}" if description else original_title
    tags = []
    for raw in pack.get("tags") or []:
        clean = re.sub(r"[\s,，]+", "", str(raw).strip().lstrip("#"))
        if clean and len(clean) <= 20 and clean not in tags:
            tags.append(clean)
        if len(tags) >= 5:
            break
    if len(description) > 1000:
        description = description[:999].rstrip() + "…"
    total = len(combined_text("douyin", title, description, tags))
    if total > 1000:
        excess = total - 1000
        keep = max(0, len(description) - excess - 1)
        description = (description[:keep].rstrip() + "…") if keep > 0 else ""
    return {**pack, "title": title, "description": description, "tags": tags}


def _auto_enqueue(db: DouyinStore, owner: str, policy: dict) -> None:
    if not policy.get("auto_upload"):
        return
    since = float(policy.get("auto_since") or 0)
    for candidate in candidates():
        if (candidate.get("local_video") or candidate["archived"] or candidate["script_status"] != "APPROVED" or
                candidate["version"] != candidate["approved_version"] or candidate["version"] != candidate["current_version"]):
            continue
        try:
            if platform_manager._timestamp(candidate["completed_at"]) < since:
                continue
            pack = preview(candidate["render_id"])
            if pack["credibility"]["tier"] in ("serious", "verify", "unaudited") or pack["freshness_warnings"]:
                continue
            fitted = _fit_douyin_auto_pack(pack)
            if not 1 <= len(fitted["title"]) <= 30 or len(fitted["description"]) > 1000:
                continue
            enqueue(candidate["render_id"], fitted, automatic=True)
        except (DouyinError, platform_manager.Error, ValueError, TypeError) as exc:
            logger.debug("Douyin auto upload skipped %s: %s", candidate["render_id"], exc)
            continue


def _pick_queued_job(db: DouyinStore, owner: str, policy: dict) -> dict | None:
    queued = list(reversed(db.list(statuses=("QUEUED",))))
    if not queued:
        return None
    # Non-upload jobs (LOGIN, VERIFY, RECONCILE, LOOKUP, FIRST_COMMENT, COMMENT_CHECK) and manual uploads go first.
    for row in queued:
        if row["kind"] != "UPLOAD" or not row["payload"].get("automatic"):
            return db.claim(row["id"])
    window = policy.get("publish_window") or {}
    now = time.time()
    for row in queued:
        if row["uid"] != owner:
            continue
        if window_allows(now, window, db.reserved_publish_times(owner, row["id"])):
            return db.claim(row["id"])
        note = held_note(window)
        if row["phase"] != note:
            db.update(row["id"], expected="QUEUED", phase=note)
    return None


def tick() -> None:
    with dispatch_lock():
        db = store()
        _settle(db)
        # A still-running browser process survives web-server restarts and holds this lock.
        with (directory() / "worker.lock").open("a") as worker:
            try:
                fcntl.flock(worker, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return
            db.recover()
        current = account().get("uid", "")
        policy = db.policy_settings(current) if current else {}
        if current and runtime()["ready"]:
            _auto_enqueue(db, current, policy)
        active = db.list(statuses=ACTIVE)
        if not active and current:
            checks_by_comment = {}
            for check in db.list(uid=current, kind='COMMENT_CHECK'):
                checks_by_comment.setdefault(check['parent_id'], []).append(check)
            for comment in db.list(uid=current, kind='FIRST_COMMENT', statuses=('UNKNOWN', 'SENT')):
                if comment['result'].get('read_back'):
                    continue
                checks = checks_by_comment.get(comment['id'], [])
                if len(checks) < 5 and (not checks or time.time() - checks[0]['updated_at'] > 120):
                    db.create('COMMENT_CHECK', current, parent_id=comment['id'])
                    break
            active = db.list(statuses=ACTIVE)
        if not active and current:
            now = time.time()
            for row in db.list(uid=current, kind="UPLOAD", statuses=("UNKNOWN",)):
                if row["work_id"]:
                    continue
                rec = row["result"].get("recovery") or {}
                if now - float(row["created_at"]) > UNKNOWN_LOOKUP_DAYS * 86400 or int(rec.get("lookup_count") or 0) >= UNKNOWN_LOOKUP_MAX:
                    if not rec.get("auto_stopped"):
                        db.update(row["id"], expected="UNKNOWN", result={**row["result"], "recovery": {
                            **rec, "auto_stopped": True, "next_lookup_at": 0,
                            "note": f"已停止自动回查；请到创作中心确认后手动填写作品链接，或点「自动回查一次」。",
                        }})
                    continue
                if row["updated_at"] > now - 60 or float(rec.get("next_lookup_at") or 0) > now:
                    continue
                db.create("LOOKUP", current, parent_id=row["id"])
                break
            active = db.list(statuses=ACTIVE)
        if not active and current:
            pending = db.list(uid=current, kind="UPLOAD", statuses=("SUBMITTED",))
            latest_checks = verification_checks(db.list(uid=current, kind='RECONCILE', parent_ids=[r['id'] for r in pending])) if pending else {}
            for row in pending:
                tracking = row['result'].get('verification', {})
                latest = latest_checks.get(row['id'])
                next_check = tracking.get('next_check_at') or (latest['updated_at'] + 120 if latest else 0)
                if row['work_id'] and not tracking.get('exhausted') and time.time() >= next_check:
                    db.create("RECONCILE", current, {"work_id": row["work_id"]}, parent_id=row["id"])
                    break
        if not runtime()["ready"]:
            return
        row = _pick_queued_job(db, current, policy)
        if not row:
            return
        if row['kind'] == 'FIRST_COMMENT':
            parent = db.get(row['parent_id'])
            if row['uid'] != current or not parent or parent['uid'] != current or parent['status'] != 'PUBLISHED' or row['work_id'] != parent['work_id']:
                db.update(row['id'], status='FAILED', error='原账号或作品公开状态已改变，请核对后重试。', result={'confirmed_not_submitted': True})
                return
        if row["kind"] == "UPLOAD":
            try:
                if row["uid"] != current:
                    raise DouyinError("原投稿账号已断开，请连接原账号后重试。")
                render, _, _ = platform_manager.eligible(row["render_id"])
                if video_store.has_publication(row["script_id"], row["version"], "douyin", row["uid"],
                                               deleted=tuple(row["payload"].get("removed_work_ids") or ())):
                    raise DouyinError("这个版本已记录抖音发布，已停止重复投稿。")
                db.update(row["id"], payload={
                    **row["payload"],
                    "video_path": str(video_render.safe_file(render, "video.mp4").resolve()),
                    "headless": bool(policy.get("headless", True)),
                })
            except (DouyinError, platform_manager.Error) as exc:
                db.update(row["id"], status="FAILED", error=str(exc), result={"confirmed_not_submitted": True})
                return
        rt = runtime()
        environment = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "DISPLAY")}
        environment.update(PYTHONPATH=os.pathsep.join((str(rt["source"]), str(ROOT))),
                           PLAYWRIGHT_BROWSERS_PATH=str(rt["browsers"]), PYTHONUNBUFFERED="1")
        try:
            subprocess.Popen([str(rt["python"]), str(ROOT / "scripts/douyin_worker.py"),
                              "--state-dir", str(Path(get_settings().paths.state_dir).resolve()), "--job", row["id"]],
                             cwd=rt["source"], env=environment, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError:
            db.update(row["id"], status="FAILED", error="无法启动抖音连接组件，请检查本机安装。",
                      result={"confirmed_not_submitted": True})
