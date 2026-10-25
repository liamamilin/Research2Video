"""Read-only aggregation of account/version targets across independent upload workers."""
from __future__ import annotations

import json

from core.video import store
from web.services import douyin, platform_manager, social_publishing
from core.social.registry import PLATFORMS as SOCIAL_PLATFORMS

PLATFORMS = {"bilibili": "哔哩哔哩", "douyin": "抖音", **{key:value[0] for key,value in SOCIAL_PLATFORMS.items()}}


def accounts() -> dict[str, dict]:
    return {"bilibili": platform_manager.account(), "douyin": douyin.account(), **{key:social_publishing.account(key) for key in SOCIAL_PLATFORMS}}


def overview(scripts: list[dict]) -> dict[str, list[dict]]:
    """Batch reads, including disconnected historical accounts. Never expose provider payloads or credentials."""
    ids = {s["id"] for s in scripts}
    if not ids:
        return {}
    profiles = accounts()
    with store.connect() as conn:
        publications = [dict(r) for r in conn.execute("SELECT * FROM publications ORDER BY created_at DESC") if r["script_id"] in ids]
        drafts = [dict(r) for r in conn.execute("SELECT script_id,version,platform,account_uid,render_id,updated_at FROM distribution_drafts") if r["script_id"] in ids]
    tasks = []
    with platform_manager.store().connect() as conn:
        for r in conn.execute("SELECT id,uid,script_id,version,render_id,status,error,bvid,updated_at FROM uploads ORDER BY created_at DESC"):
            if r["script_id"] in ids:
                tasks.append({**dict(r), "platform": "bilibili", "work_id": r["bvid"]})
    with douyin.store().connect() as conn:
        for r in conn.execute("SELECT id,uid,script_id,version,render_id,status,error,work_id,phase,updated_at FROM jobs WHERE kind='UPLOAD' ORDER BY created_at DESC"):
            if r["script_id"] in ids:
                tasks.append({**dict(r), "platform": "douyin"})
    for platform in SOCIAL_PLATFORMS:
        for task in social_publishing.store(platform).list(kind="UPLOAD"):
            if task['script_id'] in ids:
                tasks.append({**task,"platform":platform})
    targets = {}

    def target(script_id, version, platform, owner):
        key = (script_id, version, platform, owner)
        if key not in targets:
            profile = profiles.get(platform, {})
            current = bool(owner and profile.get("connected") and owner == profile.get("uid"))
            targets[key] = {"script_id": script_id, "version": version, "platform": platform,
                            "account_uid": owner, "account_name": profile.get("name", "") if current else "",
                            "connected": current, "current_account": current, "status": "UNPUBLISHED",
                            "task": None, "publications": [], "draft": None, "can_prepare": False, "blocked_reason": ""}
        return targets[key]

    with store.connect() as conn:
        versions = {(r["script_id"], r["version"]) for r in conn.execute("SELECT DISTINCT script_id,version FROM renders") if r["script_id"] in ids}
    versions.update((s["id"], s["current_version"]) for s in scripts)
    for script_id, version in versions:
        for platform, profile in profiles.items():
            target(script_id, version, platform, profile.get("uid", "") if profile.get("connected") else "")
    for p in publications:
        row = target(p["script_id"], p["version"], p["platform"], p["account_uid"])
        row["publications"].append(p)
        row["account_name"] = row["account_name"] or p["account_name"]
    for t in tasks:
        row = target(t["script_id"], t["version"], t["platform"], t["uid"])
        if row["task"] is None:
            row["task"] = {k: t[k] for k in ("id", "status", "error", "render_id", "work_id", "updated_at")}
    for d in drafts:
        target(d["script_id"], d["version"], d["platform"], d["account_uid"])["draft"] = {k: d[k] for k in ("render_id", "updated_at")}
    result = {s["id"]: [] for s in scripts}
    from core.video.content_guard import publishing_block

    isolation = {s["id"]: publishing_block(s) for s in scripts}
    for (script_id, version, platform, owner), row in targets.items():
        task = row["task"]
        state = (task or {}).get("status")
        row["status"] = ("UPLOADING" if state == "RUNNING" else state) or ("DRAFT" if row["draft"] else "UNPUBLISHED")
        # A failed later attempt must not erase an already confirmed work.
        if row["publications"] or state == "PUBLISHED":
            row["status"] = "PUBLISHED"
        unknown = targets.get((script_id, version, platform, ""))
        if owner and unknown and unknown["publications"]:
            row["blocked_reason"] = "该版本有账号待核对的旧记录。可在下方补录同一链接并选择账号，核对归属后再继续。"
        row["can_prepare"] = bool(row["current_account"] and not row["publications"] and not task
                                  and not row["blocked_reason"] and not isolation[script_id])
        result[script_id].append(row)
    return result


def preview_draft(render: dict, platform: str) -> dict:
    profile = accounts()[platform]
    owner = profile.get("uid", "") if profile.get("connected") else ""
    return {"account_uid": owner, "draft": store.distribution_draft(render, platform, owner)}


def save_draft(render_id: str, platform: str, owner: str, payload: dict, previous_payload: dict | None = None) -> dict:
    render, _, _ = platform_manager.eligible(render_id)
    profile = accounts().get(platform, {})
    if not owner or not profile.get("connected") or owner != profile.get("uid"):
        raise ValueError("平台账号已改变，请刷新页面后重新编辑。")
    if store.has_publication(render["script_id"], render["version"], platform, owner):
        raise ValueError("此目标已有发布记录，请查看历史。")
    if platform == "bilibili":
        existing = platform_manager.store().script_uploads(owner, render["script_id"])
    elif platform == "douyin":
        existing = douyin.store().list(uid=owner, kind="UPLOAD")
    else:
        existing = social_publishing.store(platform).list(uid=owner,kind="UPLOAD")
    if any(t["script_id"] == render["script_id"] and t["version"] == render["version"] for t in existing):
        raise ValueError("此目标已有投稿任务，请查看投稿进度。")
    validate_draft(payload, platform)
    if previous_payload is not None:
        validate_draft(previous_payload, platform)
    return store.save_distribution_draft(render, platform, owner, payload, previous_payload)


def validate_draft(payload: dict, platform: str) -> None:
    """Bound editable data without imposing submission limits on unfinished work."""
    from core.social.adapter import AdapterError
    error = AdapterError if platform in SOCIAL_PLATFORMS else ValueError
    # A draft is editable content, never a provider payload, path or credential.
    allowed = {"title", "description", "tags", "tid", "first_comment"} if platform == "bilibili" else {"title", "description", "tags", "first_comment"}
    if not isinstance(payload, dict) or set(payload) - allowed or len(json.dumps(payload, ensure_ascii=False)) > 60000:
        raise error("发布包字段或长度不正确。")
    if not isinstance(payload.get("title", ""), str) or not isinstance(payload.get("description", ""), str):
        raise error("发布包标题和简介需要是文字。")
    tags = payload.get("tags", [])
    if not isinstance(tags, list) or len(tags) > 30 or any(not isinstance(t, str) or len(t) > 300 for t in tags):
        raise error("发布包标签格式不正确。")
    if "tid" in payload and (type(payload["tid"]) is not int or not 0 <= payload["tid"] <= 65535):
        raise error("投稿分区格式不正确。")
    if "first_comment" in payload:
        comment = payload["first_comment"]
        if (not isinstance(comment, dict) or set(comment) - {"enabled", "text", "pin"}
                or any(type(comment.get(k, False)) is not bool for k in ("enabled", "pin"))
                or not isinstance(comment.get("text", ""), str)):
            raise error("首评选项格式不正确。")


def adapt_preview(render_id: str, platform: str, payload: dict) -> dict:
    """Preview text only. No model, draft mutation or publisher task is started."""
    from core.video.platform_publish import adapt
    from web.services import video_render
    validate_draft(payload, platform)
    render, script, version = platform_manager.eligible(render_id)
    pack = video_render.publish_pack(script, version, platform_manager.column_for(script), render_id=render_id)
    return adapt(pack, platform, payload)


def history(render_id: str, platform: str) -> dict:
    """Read only the selected version and currently connected platform account."""
    render, _, _ = platform_manager.eligible(render_id)
    profile = accounts().get(platform, {})
    owner = profile.get("uid", "") if profile.get("connected") else ""
    return {"account_uid": owner, "versions": store.distribution_draft_history(render, platform, owner) if owner else []}


def migrate_confirmed_accounts() -> int:
    """Backfill only exact work matches to confirmed worker receipts, never the current login by itself."""
    from datetime import datetime, timezone
    from core.video.publication_identity import identify

    receipts = {}
    with platform_manager.store().connect() as conn:
        for r in conn.execute("SELECT uid,script_id,version,bvid,updated_at FROM uploads WHERE status='PUBLISHED' AND bvid<>''"):
            receipts[(r['script_id'], r['version'], 'bilibili', r['bvid'])] = (r['uid'], r['updated_at'])
    with douyin.store().connect() as conn:
        for r in conn.execute("SELECT uid,script_id,version,work_id,updated_at FROM jobs WHERE kind='UPLOAD' AND status='PUBLISHED' AND work_id<>''"):
            receipts[(r['script_id'], r['version'], 'douyin', r['work_id'])] = (r['uid'], r['updated_at'])
    profiles = accounts()
    changed = 0
    with store.connect(write=True) as conn:
        for p in conn.execute("SELECT * FROM publications WHERE account_uid='' OR work_id=''").fetchall():
            work_id, _ = identify(p['platform'], p['url'])
            receipt = receipts.get((p['script_id'], p['version'], p['platform'], work_id))
            owner = p['account_uid'] or (receipt[0] if receipt else '')
            if receipt and p['account_uid'] and p['account_uid'] != receipt[0]:
                continue
            profile = profiles.get(p['platform'], {})
            sources = set(json.loads(p['sources']) or ['automatic' if p['recorded_by'] == 'platform-manager' else 'manual'])
            if receipt:
                sources.add('automatic')
            confirmed = p['confirmed_at'] or (datetime.fromtimestamp(receipt[1], timezone.utc).isoformat() if receipt else '')
            name = p['account_name'] or (profile.get('name', '') if owner and owner == profile.get('uid') else '')
            if (work_id, owner, name, json.dumps(sorted(sources)), confirmed) != (p['work_id'], p['account_uid'], p['account_name'], p['sources'], p['confirmed_at']):
                conn.execute("UPDATE publications SET work_id=?,account_uid=?,account_name=?,sources=?,confirmed_at=? WHERE id=?",
                             (work_id, owner, p['account_name'] or (profile.get('name', '') if owner and owner == profile.get('uid') else ''),
                              json.dumps(sorted(sources)), confirmed, p['id']))
                changed += 1
    return changed
