"""Account connection, guarded posting, and confirmed publication recording for Douyin."""

from __future__ import annotations

import base64
from contextlib import contextmanager
from datetime import datetime
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import time

from core.douyin import DouyinError, UID, WORK_ID, work_id, clean_first_comment
from core.douyin_store import DouyinStore, ACTIVE
from core.social.verification import check_progress, lookup_revision, MAX_UNRESOLVED_CHECKS
from core.video import store as video_store
from scripts.douyin_worker import private_json
from web.services import platform_manager, video_render
from web.settings import get_settings

PIN = "3847fff01f5434a8a32829833163cd6723cabc59"
ROOT = Path(__file__).resolve().parents[2]


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


def public_job(row: dict, *, comments: dict | None = None, checks: dict | None = None) -> dict:
    result = {**{k: row[k] for k in ("id", "kind", "status", "phase", "error", "work_id", "script_id", "version", "render_id", "created_at", "updated_at")},
              "payload": {k: row["payload"][k] for k in ("title", "description", "tags", "first_comment") if k in row["payload"]},
              "url": f"https://www.douyin.com/video/{row['work_id']}" if WORK_ID.fullmatch(row["work_id"]) else "",
              "can_retry": row["status"] in ("FAILED", "CANCELLED") and bool(row["result"].get("confirmed_not_submitted"))}
    if row['kind'] == 'UPLOAD':
        comment = (comments if comments is not None else store().comments(row['uid'])).get(row['id'])
        result['first_comment'] = None if not comment else {
            **{k: comment[k] for k in ('id', 'status', 'phase', 'error', 'created_at', 'updated_at')},
            'text': comment['payload']['text'], 'comment_id': comment['result'].get('comment_id', ''),
            'read_back': bool(comment['result'].get('read_back')),
            'can_retry': comment['status'] in ('FAILED', 'CANCELLED') and bool(comment['result'].get('confirmed_not_submitted'))}
        if checks is None:
            checks = verification_checks(store().list(uid=row['uid'], kind='RECONCILE'))
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
    checks = verification_checks(db.list(kind='RECONCILE'))
    for parent in db.list(kind='UPLOAD', statuses=('SUBMITTED', 'UNKNOWN')):
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
        (directory() / "account.json").unlink(missing_ok=True)
        (directory() / "accounts" / owner / "storage.json").unlink(missing_ok=True)
    return {"connected": False}


def candidates() -> list[dict]:
    from core.video import content_guard

    queued = {(r["script_id"], r["version"]) for r in store().list(uid=account().get("uid", ""), kind="UPLOAD")}
    with video_store.connect() as conn:
        rows = conn.execute("SELECT r.id AS render_id,r.script_id,r.version,r.title,r.completed_at,r.artifacts,"
                            "s.archived,s.status AS script_status,s.current_version,s.approved_version "
                            "FROM renders r JOIN scripts s ON s.id=r.script_id "
                            f"WHERE r.status='COMPLETED' AND {content_guard.PUBLISHABLE_SQL} AND NOT EXISTS "
                            "(SELECT 1 FROM publications p WHERE p.script_id=r.script_id AND p.version=r.version AND p.platform='douyin' AND (p.account_uid=? OR p.account_uid='')) "
                            "ORDER BY r.completed_at DESC,r.created_at DESC", (account().get("uid", ""),)).fetchall()
    seen, result = set(), []
    for row in rows:
        key = (row["script_id"], row["version"])
        if key in queued or key in seen:
            continue
        render = {"id": row["render_id"], "artifacts": json.loads(row["artifacts"])}
        if video_render.safe_file(render, "video.mp4") is None:
            continue
        seen.add(key)
        result.append({k: row[k] for k in row.keys() if k != "artifacts"})
    return result


def preview(render_id: str) -> dict:
    from web.services.distributions import preview_draft
    render, script, version = platform_manager.eligible(render_id)
    pack = video_render.publish_pack(script, version, platform_manager.column_for(script), render_id=render_id)
    from core.video.platform_publish import adapt
    from core.social.publish_text import default_comment
    adaptation = adapt(pack, "douyin")
    warnings = list(pack.get("warnings", [])) + list(pack.get("freshness", {}).get("warnings", []))
    if len(adaptation["payload"]["title"]) > 30:
        warnings.append("原发布包标题超过抖音 30 字限制，请编辑后投稿。")
    if len(adaptation["payload"]["description"]) > 1000:
        warnings.append("原发布包简介超过抖音 1000 字限制，请编辑后投稿。")
    return {**preview_draft(render, "douyin"), "render_id": render["id"], "script_id": script["id"], "version": render["version"],
            **adaptation["payload"], "adaptation": adaptation, "warnings": warnings,
            "first_comment": default_comment(store().comment_settings(account().get("uid", "")))}


def enqueue(render_id: str, payload: dict) -> dict:
    owner = uid()
    if payload.get("account_uid") and payload["account_uid"] != owner:
        raise DouyinError("平台账号已改变，请刷新后重试。")
    if not runtime()["ready"]:
        raise DouyinError("抖音上传组件尚未准备好。")
    render, script, _ = platform_manager.eligible(render_id)
    title, description = payload["title"].strip(), payload["description"].strip()
    tags = [v.strip().lstrip("#") for v in payload["tags"] if v.strip()]
    if not 1 <= len(title) <= 30 or len(description) > 1000 or len(tags) > 5 or any(len(t) > 20 or any(c in t for c in " ,，\n") for t in tags):
        raise DouyinError("抖音标题需为 1–30 字，简介最多 1000 字，话题最多 5 个，每个最多 20 字且不含分隔符。")
    from core.social.publish_text import combined_text
    if len(combined_text("douyin", title, description, tags)) > 1000:
        raise DouyinError("简介与话题合计超过 1000 字，请精简后投稿。")
    if video_store.has_publication(script["id"], render["version"], "douyin", owner):
        raise DouyinError("这个版本已记录抖音发布，请查看文稿发布记录。")
    cover = next((p for n in ("cover.png", "cover.jpg") if (p := video_render.safe_file(render, n))), None)
    clean = {"title": title, "description": description, "tags": tags,
             "video_path": str(video_render.safe_file(render, "video.mp4").resolve()),
             "cover": str(cover.resolve()) if cover else "", "first_comment": clean_first_comment(payload.get("first_comment", store().comment_settings(owner)))}
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
        existing = [r for r in store().list(statuses=ACTIVE) if r["parent_id"] == key]
        check = existing[0] if existing else store().create("RECONCILE", row["uid"], {"work_id": target}, parent_id=key)
    tick()
    return public_job(store().get(check["id"]))


def action(key: str, kind: str) -> dict:
    with dispatch_lock():
        row = upload_task(key)
        if kind == "cancel" and row["status"] == "QUEUED":
            store().update(key, expected="QUEUED", status="CANCELLED", result={"confirmed_not_submitted": True})
        elif kind == "retry" and row["status"] in ("FAILED", "CANCELLED") and row["result"].get("confirmed_not_submitted"):
            render, _, _ = platform_manager.eligible(row["render_id"])
            if video_store.has_publication(row["script_id"], row["version"], "douyin", row["uid"]):
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
    jobs = store().list(uid=owner) if owner else []
    comments = {c['parent_id']: c for c in jobs if c['kind'] == 'FIRST_COMMENT'}
    checks = verification_checks([c for c in jobs if c['kind'] == 'RECONCILE'])
    return {"account": account(), "runtime_ready": runtime()["ready"], "login_id": active_login,
            "settings": {"first_comment": store().comment_settings(owner)}, "capabilities": {"first_comment": True, "pin_comment": False},
            "uploads": [public_job(r, comments=comments, checks=checks) for r in jobs if r["kind"] == "UPLOAD"],
            "checks": [public_job(r) for r in jobs if r["kind"] in ("VERIFY", "RECONCILE")][:10],
            "candidates": candidates() if owner else []}


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
            pending = db.list(uid=current, kind="UPLOAD", statuses=("SUBMITTED",))
            latest_checks = verification_checks(db.list(kind='RECONCILE'))
            for row in pending:
                tracking = row['result'].get('verification', {})
                latest = latest_checks.get(row['id'])
                next_check = tracking.get('next_check_at') or (latest['updated_at'] + 120 if latest else 0)
                if row['work_id'] and not tracking.get('exhausted') and time.time() >= next_check:
                    db.create("RECONCILE", current, {"work_id": row["work_id"]}, parent_id=row["id"])
                    break
        if not runtime()["ready"]:
            return
        row = db.claim()
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
                if video_store.has_publication(row["script_id"], row["version"], "douyin", row["uid"]):
                    raise DouyinError("这个版本已记录抖音发布，已停止重复投稿。")
                db.update(row["id"], payload={**row["payload"], "video_path": str(video_render.safe_file(render, "video.mp4").resolve())})
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
