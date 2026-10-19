"""The home board: how many scripts are at each stage, and what needs the user (spec/console-redesign.md)."""
from typing import Optional

from core.video import store

#: Stages in pipeline order. A script is counted once, at the furthest stage it has reached.
STAGES = (
    ("researching", "研究"),
    ("draft", "草稿"),
    ("approved", "定稿"),
    ("rendering", "成片中"),
    ("rendered", "成片完成"),
    ("published", "已发布"),
)
ATTENTION_LIMIT = 30


def _stage(script: dict, renders: list[dict], published: bool) -> str:
    if published:
        return "published"
    status = script["status"]
    if status in ("RESEARCHING", "WRITING"):
        return "researching"
    if status == "APPROVED":
        if any(r["status"] in store.ACTIVE_RENDER_STATUSES for r in renders):
            return "rendering"
        if any(r["status"] == "COMPLETED" for r in renders):
            return "rendered"
        return "approved"
    return "draft" if status == "DRAFT" else "failed"


def _title(script: dict) -> str:
    version = store.get_version(script["id"], script["current_version"]) if script["current_version"] else None
    return version["title"] if version else script["topic"].get("title", "")


def board(columns: dict, *, limit: int = 200) -> dict:
    from web.services import video_recheck

    published = store.published_script_ids()
    counts = {key: 0 for key, _ in STAGES}
    counts["failed"] = 0
    attention: list[dict] = []
    for script in store.list_scripts(limit=limit):
        renders = store.list_renders(script["id"])
        stage = _stage(script, renders, script["id"] in published)
        counts[stage] += 1
        item = {"script_id": script["id"], "title": _title(script), "column_key": script["column_key"]}
        if stage == "draft":
            attention.append({**item, "kind": "review", "text": "草稿等你审校定稿"})
        elif stage == "failed":
            attention.append({**item, "kind": "failed", "text": script.get("error") or "失败了"})
        elif stage == "rendered":
            attention.append({**item, "kind": "render_done", "text": "成片完成，看片后发布并记录链接"})
        elif stage == "approved":
            attention.append({**item, "kind": "render", "text": "已定稿，还没生成成片"})
        if stage in ("approved", "rendering", "rendered"):
            column = columns.get(script["column_key"])
            version = store.get_version(script["id"], script["current_version"])
            if column is not None and version is not None and column.recheck:
                fresh = video_recheck.freshness(script, version, column, store.list_rechecks(script["id"]))
                if fresh.get("warnings"):
                    attention.append({**item, "kind": "recheck", "text": fresh["warnings"][0]})
    return {
        "stages": [{"key": key, "label": label, "count": counts[key]} for key, label in STAGES],
        "failed": counts["failed"],
        "attention": attention[:ATTENTION_LIMIT],
    }


def shipments(columns: dict, *, limit: int = 200) -> list[dict]:
    """Every approved script with where its video and publication stand, newest first (the 成片与发布 page)."""
    from web.services import video_recheck

    published = store.published_script_ids()
    rows = []
    for script in store.list_scripts(limit=limit):
        if script["status"] != "APPROVED":
            continue
        renders = store.list_renders(script["id"])
        latest = renders[0] if renders else None
        version = store.get_version(script["id"], script["current_version"])
        column = columns.get(script["column_key"])
        warnings: list[str] = []
        if column is not None and column.recheck and version is not None and script["id"] not in published:
            warnings = video_recheck.freshness(script, version, column, store.list_rechecks(script["id"])).get("warnings", [])
        video = next((a for a in (latest or {}).get("artifacts") or [] if a["name"] == "video.mp4"), None)
        rows.append({
            "script_id": script["id"], "title": _title(script), "column_key": script["column_key"],
            "approved_version": script["approved_version"], "created_at": script["created_at"],
            "render": None if latest is None else {"id": latest["id"], "status": latest["status"],
                                                   "version": latest["version"], "error": latest.get("error_message")},
            "video_url": f"/api/video/renders/{latest['id']}/files/video.mp4" if video and latest["status"] == "COMPLETED" else None,
            "publications": store.list_publications(script["id"]),
            "warnings": warnings,
        })
    return rows
