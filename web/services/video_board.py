"""The home board: how many scripts are at each stage, and what needs the user (spec/console-redesign.md)."""
from typing import Optional

from core.video import credibility, outputs, progress, store

#: Stages in pipeline order. A script is counted once, at the furthest stage it has reached.
STAGES = (
    ("researching", "研究"),
    ("draft", "草稿"),
    ("approved", "定稿"),
    ("rendering", "成片中"),
    ("rendered", "成片完成"),
    ("published", "已发布"),
)


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
    return "draft" if status in ("DRAFT", "PLAN_READY") else "failed"


def _label(script: dict) -> Optional[dict]:
    version = store.get_version(script["id"], script["current_version"]) if script["current_version"] else None
    return credibility.label(version, auditing=bool(script.get("stage"))) if version else None


def _title(script: dict) -> str:
    version = store.get_version(script["id"], script["current_version"]) if script["current_version"] else None
    return version["title"] if version else script["topic"].get("title", "")


def _next_action(script: dict, stage: str, label: dict | None, renders: list[dict], freshness: list[str]) -> dict:
    """One next action per current version. Recommendations never approve or publish anything."""
    if stage == "published":
        return {"action": "published", "group": "done", "action_label": "发布记录", "note": "当前版本已记录发布链接"}
    if stage in ("researching", "rendering") or script.get("stage"):
        return {"action": "working", "group": "working", "action_label": "查看进度", "note": "任务正在执行，完成后会自动更新"}
    if script["status"] == "FAILED":
        return {"action": "failed", "group": "todo", "action_label": "处理失败", "note": script.get("error") or "任务未完成，查看原因后重试"}
    if script["status"] == "PLAN_READY":
        return {"action": "plan", "group": "todo", "action_label": "确认方案", "note": "讲述方案已完成，确认后继续写稿"}
    if label and label["tier"] in ("serious", "verify", "unaudited"):
        return {"action": "review", "group": "todo", "action_label": "检查审计" if label["tier"] == "unaudited" else "核实文稿",
                "note": (label.get("reasons") or [label["text"]])[0]}
    if label and label["tier"] == "spoken":
        return {"action": "review", "group": "todo", "action_label": "修改口播",
                "note": (label.get("reasons") or [label["text"]])[0]}
    if freshness:
        return {"action": "recheck", "group": "todo", "action_label": "复核时效", "note": freshness[0]}
    if script["status"] == "DRAFT":
        return {"action": "approve", "group": "todo", "action_label": "查看并定稿", "note": "草稿已完成，确认当前文稿后可继续出片"}
    if stage == "rendered":
        return {"action": "publish", "group": "publish", "action_label": "看片与发布", "note": "成片已完成，看片后手动上传并记录链接"}
    if renders and renders[0]["status"] in ("FAILED", "CANCELLED"):
        return {"action": "retry_render", "group": "todo", "action_label": "检查出片", "note": "本版本上次出片未完成，可查看详情后重试"}
    return {"action": "render", "group": "todo", "action_label": "生成成片", "note": "当前版本已定稿，可以生成成片"}


def board(columns: dict, *, limit: int = 100000, config_dir: str = "config", jobs_dir: str = "jobs") -> dict:
    from web.services import video_recheck

    published = store.published_versions_by_script()
    counts = {key: 0 for key, _ in STAGES}
    counts["failed"] = 0
    attention: list[dict] = []
    scripts = store.list_scripts(limit=limit)
    owners, matrices = outputs.script_owners(config_dir, jobs_dir, scripts)
    scripts = [s for s in scripts if not owners[s["id"]]["matrix_archived"]]
    versions = store.current_versions([s["id"] for s in scripts])
    render_statuses = store.render_statuses_by_script()
    items = []
    for script in scripts:
        every_render = render_statuses.get(script["id"], [])
        renders = [r for r in every_render if r["version"] == script["current_version"]]
        published_versions = published.get(script["id"], set())
        is_published = progress.is_published(script, published_versions)
        stage = _stage(script, renders, is_published)
        counts[stage] += 1
        version = versions.get(script["id"])
        label = credibility.label(version, auditing=bool(script.get("stage"))) if version else None
        column = columns.get(script["column_key"])
        freshness = []
        if stage in ("approved", "rendering", "rendered"):
            if column is not None and version is not None and column.recheck:
                freshness = video_recheck.freshness(script, version, column, store.list_rechecks(script["id"])).get("warnings", [])
        item = {"script_id": script["id"], "title": version["title"] if version else script["topic"].get("title", ""),
                "column_key": script["column_key"], "column_label": " · ".join(filter(None, [column.name, column.audience])) if column else script["column_key"],
                "created_at": script["created_at"], "updated_at": script["updated_at"], "credibility": label,
                "stage": stage, "status": script["status"], "version": script["current_version"],
                "has_video": any(r["status"] == "COMPLETED" for r in renders),
                "has_previous_video": any(r["version"] != script["current_version"] and r["status"] == "COMPLETED" for r in every_render),
                **{k: v for k, v in owners[script["id"]].items() if k != "matrix_archived"},
                **_next_action(script, stage, label, renders, freshness)}
        items.append(item)
        kind = {"failed": "failed", "retry_render": "failed", "plan": "plan", "review": "review", "approve": "review",
                "recheck": "recheck", "render": "render", "publish": "render_done"}.get(item["action"])
        if kind:
            attention.append({**{k: item[k] for k in ("script_id", "title", "column_key", "created_at", "credibility")},
                              "kind": kind, "text": item["note"]})
    return {
        "stages": [{"key": key, "label": label, "count": counts[key]} for key, label in STAGES],
        "failed": counts["failed"],
        "attention": attention,
        "items": items,
        "matrices": matrices,
        "updated_at": store._now(),
    }


def shipments(columns: dict, *, limit: int = 100000, config_dir: str = "config", jobs_dir: str = "jobs") -> list[dict]:
    """Approved current versions and their own videos/publications, excluding archived matrices."""
    from web.services import video_recheck

    scripts = store.list_scripts(limit=limit)
    owners, _ = outputs.script_owners(config_dir, jobs_dir, scripts)
    scripts = [s for s in scripts if s["status"] == "APPROVED" and not owners[s["id"]]["matrix_archived"]]
    versions = store.current_versions([s["id"] for s in scripts])
    published = store.published_versions_by_script()
    render_statuses = store.render_statuses_by_script()
    chosen_renders = {s["id"]: progress.render_state(s, render_statuses.get(s["id"], []))["render"] for s in scripts}
    details = store.shipment_rows([s["id"] for s in scripts], [r["id"] for r in chosen_renders.values() if r])
    rows = []
    for script in scripts:
        owner = owners[script["id"]]
        chosen = chosen_renders[script["id"]]
        latest = details["renders"].get(chosen["id"]) if chosen else None
        version = versions.get(script["id"])
        column = columns.get(script["column_key"])
        warnings: list[str] = []
        if column is not None and column.recheck and version is not None and not progress.is_published(script, published.get(script["id"], set())):
            warnings = video_recheck.freshness(script, version, column, details["rechecks"].get(script["id"], [])).get("warnings", [])
        video = next((a for a in (latest or {}).get("artifacts") or [] if a["name"] == "video.mp4"), None)
        publications = [p for p in details["publications"].get(script["id"], []) if p["version"] == script["current_version"]
                        or (p["version"] is None and script["current_version"] == 1)]
        rows.append({
            "script_id": script["id"], "title": version["title"] if version else script["topic"].get("title", ""),
            "column_key": script["column_key"], "column_label": owner.get("production_name") or (column.name if column else script["column_key"]),
            **{k: v for k, v in owner.items() if k != "matrix_archived"},
            "approved_version": script["approved_version"], "created_at": script["created_at"],
            "credibility": credibility.label(version) if version else None, "auto": script.get("created_by") == "autopilot",
            "render": None if latest is None else {"id": latest["id"], "status": latest["status"],
                                                   "version": latest["version"], "error": latest.get("error_message"),
                                                   "completed_at": latest.get("completed_at")},
            "video_url": f"/api/video/renders/{latest['id']}/files/video.mp4" if video and latest["status"] == "COMPLETED" else None,
            "publications": publications, "warnings": warnings,
        })
    return rows
