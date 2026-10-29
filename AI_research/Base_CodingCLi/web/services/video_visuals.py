"""Prepare source screenshots inside the existing recoverable render worker."""
from __future__ import annotations

import copy
import hashlib
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from core.fileio import atomic_write
from core.video import image_quality, screenshots, store, visual_cache, visual_materials, visual_focus

logger = logging.getLogger("ai_research.web.video_visuals")


def _with_materials(payload: dict, materials: list[dict]) -> dict:
    if materials:
        visual_materials.attach(payload, dict(video_form="object_showcase", production_mode="standard", use_draw_things=False,
                       image_materials=[{k: row[k] for k in ("image_id", "caption", "role", "placements", "source", "focus") if k in row} for row in materials]))
    return payload


def acquire(need: dict, url: str, policy: dict, *, remaining: float) -> tuple[bytes, dict]:
    from core.video.capture_process import run
    from web.settings import get_settings

    def checkpoint(progress: dict) -> None:
        policy["_model_calls"] = max(policy.get("_model_calls", 0), progress.get("model_calls", 0))
        if callable(policy.get("_on_model_call")):
            policy["_on_model_call"]()

    return run("automatic", {
        "need": need, "url": url, "remaining": remaining,
        "policy": {key: value for key, value in policy.items() if key != "_on_model_call"},
        "config_path": get_settings().source_path,
    }, timeout_seconds=min(60, remaining), on_progress=checkpoint)


def prepare(render: dict, client) -> tuple[dict, dict]:
    """Return a fully prepared payload and audit record; picture failures fall back to text."""
    payload = copy.deepcopy(render["request"])
    audit = copy.deepcopy(render.get("visuals") or {})
    if audit.get("state") == "completed":
        materials = audit.get("selection", {}).get("materials", [])
        for row in materials:
            screenshots.load(render["script_id"], row["screenshot_id"])
        return _with_materials(payload, materials), audit
    if audit.get("state") not in {"pending", "running"}:
        return payload, audit
    policy = copy.deepcopy(audit["policy"])
    policy["_model_calls"] = audit.get("model_calls", 0)
    policy["_paragraphs"] = audit["needs"]
    started = time.monotonic()
    prior_seconds = float(audit.get("seconds", 0))
    materials = list(audit.get("selection", {}).get("materials", []))
    decisions = list(audit.get("decisions", []))
    completed = {d["need_id"] for d in decisions if d.get("status") in {"accepted", "text"}}
    pages = list(audit.get("pages", []))
    evidence_dir = Path(store.db_path()).parent / "video_renders" / render["id"] / "visuals"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    audit.update(state="running", reason="正在获取和检查来源页面", started_at=audit.get("started_at") or datetime.now(timezone.utc).isoformat())

    def checkpoint() -> None:
        audit.update(decisions=decisions, pages=pages, model_calls=policy["_model_calls"], seconds=round(prior_seconds + time.monotonic() - started, 3),
                     selection={"form": "object_showcase", "materials": materials})
        store.update_render(render["id"], visuals=audit, active_stage="自动配图")

    policy["_on_model_call"] = checkpoint
    checkpoint()
    for need in audit["needs"]:
        if need["id"] in completed:
            continue
        remaining = policy["max_seconds"] - prior_seconds - (time.monotonic() - started)
        if remaining <= 0 or len(materials) >= policy["max_images"]:
            decisions.append({"need_id": need["id"], "status": "text", "reason": "已达到自动配图时间或图片预算"})
            checkpoint()
            continue
        accepted = False
        problems = []
        for url in need["urls"][:policy["attempts_per_paragraph"]]:
            # Reuse page-wide failures across paragraphs and worker recovery.
            # A rejected region/match remains specific to its original paragraph.
            unavailable = next((p for p in pages if p.get("failure_scope") == "page"
                                and visual_materials.url_key(p["url"]) == visual_materials.url_key(url)), None)
            if unavailable:
                audit["page_failures_reused"] = audit.get("page_failures_reused", 0) + 1
                problems.append("复用本次页面失败记录：" + unavailable["reason"])
                continue
            remaining = policy["max_seconds"] - prior_seconds - (time.monotonic() - started)
            if remaining < 1:
                problems.append("已达到自动配图时间预算")
                break
            pending = next((p for p in reversed(pages) if p["need_id"] == need["id"] and p["url"] == url
                            and p.get("screenshot_id") and p.get("state") != "failed"), None)
            owned_asset = None
            if pending:
                try:
                    owned_asset, image = screenshots.load(render["script_id"], pending["screenshot_id"])
                    cached = image.read_bytes(), owned_asset
                except screenshots.ScreenshotError:
                    pending = None
                    cached = None
            else:
                cached = visual_cache.read(need, url, policy)
            visited = {visual_materials.url_key(p["url"]) for p in pages if not p.get("cached")}
            if not cached and visual_materials.url_key(url) not in visited and len(visited) >= policy["max_pages"]:
                problems.append("已达到页面预算")
                break
            page_record = pending or {"url": url, "need_id": need["id"], "cached": bool(cached)}
            if not pending:
                pages.append(page_record)
            checkpoint()
            try:
                raw, metadata = cached or acquire(need, url, policy, remaining=remaining)
                metadata = copy.deepcopy(metadata)
                metadata.setdefault("quality", {}).setdefault("version", policy["version"])
                attempt_path = evidence_dir / f"capture-{len(pages):02d}.png"
                attempt_path.write_bytes(raw)
                page_record["evidence"] = str(attempt_path)
                atomic_write(str(attempt_path.with_suffix(".json")), json.dumps(metadata, ensure_ascii=False))
                if policy["version"] >= 3 and not cached:
                    remaining = policy["max_seconds"] - prior_seconds - (time.monotonic() - started)
                    if remaining <= 1:
                        raise screenshots.ScreenshotError("截图复核时间不足，保留文字画面")
                    metadata.setdefault("quality", {})["image"] = image_quality.verify(raw, need, metadata, policy, remaining=remaining)
                    atomic_write(str(attempt_path.with_suffix(".json")), json.dumps(metadata, ensure_ascii=False))
                quality = metadata.get("quality", {})
                vision = (quality.get("image") or {}).get("vision") or {}
                matched = set(vision.get("paragraph_ids", [])) if need.get("visual_subject") and vision.get("state") == "passed" else {need["id"]}
                matching_needs = [n for n in audit["needs"] if n["id"] in matched]
                placements = [{k: n[k] for k in ("chapter_index", "paragraph_index")} for n in matching_needs]
                sha = hashlib.sha256(raw).hexdigest()
                duplicate = next((m for m in materials if m["source"]["sha256"] == sha), None)
                if duplicate:
                    duplicate["placements"].extend(p for p in placements if p not in duplicate["placements"])
                    decisions.append({"need_id": need["id"], "status": "accepted" if need["id"] in matched else "text", "reason": "复用已匹配的同一图片",
                                      "screenshot_id": duplicate["screenshot_id"], "source_url": url, "cached": True})
                    for other in matching_needs:
                        if other["id"] != need["id"] and other["id"] not in completed:
                            decisions.append({"need_id": other["id"], "status": "accepted", "reason": "复用图片理解已匹配的素材",
                                              "screenshot_id": duplicate["screenshot_id"], "source_url": url, "cached": True})
                    completed.update(matched)
                    accepted = True
                    checkpoint()
                    break
                metadata.update(automatic=True, render_id=render["id"], need_id=need["id"])
                asset = owned_asset or screenshots.save(render["script_id"], render["version"], raw, metadata, created_by="automatic-visuals")
                page_record["screenshot_id"] = asset["id"]
                checkpoint()
                remaining = policy["max_seconds"] - prior_seconds - (time.monotonic() - started)
                if remaining <= 1:
                    raise screenshots.ScreenshotError("图片登记时间不足，保留文字画面")
                asset = screenshots.register(render["script_id"], asset["id"], client, timeout=min(15, remaining))
                visual_cache.remember(need, url, policy, asset)
                caption = (vision.get("summary") or quality.get("heading") or need.get("title") or metadata["title"])[:80]
                row = {"screenshot_id": asset["id"], "image_id": asset["image_id"], "caption": caption,
                       "role": "overall" if quality.get("visual_kind") == "image" else "detail",
                       "placements": placements,
                       "source": {k: asset[k] for k in ("url", "final_url", "title", "captured_at", "sha256")}}
                focus = visual_focus.for_asset(asset, need)
                if focus:
                    row["focus"] = focus
                materials.append(row)
                decisions.append({"need_id": need["id"], "status": "accepted" if need["id"] in matched else "text",
                                  "reason": "已选择相应讲解点的页面截图" if need["id"] in matched else "该素材用于更匹配的其他段落",
                                  "screenshot_id": asset["id"], "source_url": url, "cached": bool(cached), "quality": metadata.get("quality", {})})
                for other in matching_needs:
                    if other["id"] != need["id"] and other["id"] not in completed:
                        decisions.append({"need_id": other["id"], "status": "accepted", "reason": "复用图片理解已匹配的素材",
                                          "screenshot_id": asset["id"], "source_url": url, "cached": True})
                completed.update(matched)
                accepted = True
                checkpoint()
                break
            except Exception as exc:
                # URL/page/model output and credentials must not become logs.
                problem = str(exc) if isinstance(exc, screenshots.ScreenshotError) else f"素材获取或登记失败（{type(exc).__name__}）"
                problems.append(problem[:240])
                page_record.update(state="failed", reason=problem[:240])
                if isinstance(exc, screenshots.PageUnavailable):
                    page_record["failure_scope"] = "page"
                checkpoint()
        if not accepted:
            decisions.append({"need_id": need["id"], "status": "text", "reason": "；".join(problems) or "没有可用截图"})
            checkpoint()
    _with_materials(payload, materials)
    audit.update(state="completed" if materials else "degraded",
                 reason=f"自动配图 {len(materials)} 张，其余段落保留文字画面" if materials else "没有通过检查的截图，沿用原有文字画面",
                 finished_at=datetime.now(timezone.utc).isoformat())
    checkpoint()
    return payload, audit


def freeze(render: dict, client) -> dict:
    """Preserve an effective request before submit_started_at; never recapture an uncertain submission."""
    if render.get("submission_request") or render.get("submit_started_at"):
        return render
    try:
        payload, audit = prepare(render, client)
    except Exception as exc:
        logger.warning("automatic visual preparation failed for render %s (%s); using original request", render["id"], type(exc).__name__)
        payload = copy.deepcopy(render["request"])
        audit = {**(render.get("visuals") or {}), "state": "degraded", "reason": f"自动配图异常，沿用原画面（{type(exc).__name__}）"}
    audit["submission_sha256"] = visual_materials.digest(payload)
    return store.freeze_render_submission(render["id"], payload, audit)


def submission(render: dict) -> dict:
    payload = render.get("submission_request") or render["request"]
    expected = (render.get("visuals") or {}).get("submission_sha256")
    if expected and expected != visual_materials.digest(payload):
        raise RuntimeError("出片提交快照校验失败，拒绝发送已改变的素材")
    return copy.deepcopy(payload)
