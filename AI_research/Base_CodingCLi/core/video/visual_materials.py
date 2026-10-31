"""Source-linked automatic visual intent; no browser or model work at render creation."""
from __future__ import annotations

import hashlib
import json
import re
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from core.video import research_dossier, screenshots, store
from core.video.visual_matching import entities

VERSION = 1


def fast_visuals(request: dict) -> bool:
    effective = {}
    for name in ('content_recipe', 'matrix'):
        effective.update((request.get('production_context', {}).get(name) or {}).get('settings') or {})
    effective.update({k: request[k] for k in ('production_mode', 'video_form') if k in request})
    return bool(request.get('visual_storytelling') and request.get('chapters')
                and (effective.get('production_mode') == 'fast' or effective.get('video_form') == 'information_cards'))


def attach(request: dict, materials: dict) -> dict:
    """Keep fast production when pictures are attached by either automatic or saved selection."""
    mixed = fast_visuals(request)
    request.update(materials)
    if mixed:
        request.update(production_mode='fast', video_form='information_cards', visual_style='editorial_light')
    return request


class VisualPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    version: int = Field(default=VERSION, ge=1, le=3)
    max_pages: int = Field(default=3, ge=1, le=8)
    max_images: int = Field(default=5, ge=1, le=12)
    max_seconds: int = Field(default=90, ge=10, le=240)
    attempts_per_paragraph: int = Field(default=2, ge=1, le=3)
    cache_hours: int = Field(default=6, ge=0, le=24)
    semantic_review: bool = False
    max_model_calls: int = Field(default=2, ge=0, le=4)
    ocr_review: bool = False
    vision_section: str = Field(default="", pattern=r"^[a-z_]{0,40}$")
    vision_required: bool = False
    vision_timeout: int = Field(default=35, ge=8, le=60)


def digest(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def summary(audit: dict | None) -> dict | None:
    if not audit:
        return None
    return {"state": audit.get("state", "skipped"), "reason": audit.get("reason", ""), "version": audit.get("version", 1),
            "images": len(audit.get("selection", {}).get("materials", [])), "needs": len(audit.get("needs", [])),
            "accepted": sum(d.get("status") == "accepted" for d in audit.get("decisions", [])),
            "text": sum(d.get("status") == "text" for d in audit.get("decisions", [])),
            "seconds": audit.get("seconds", 0), "model_calls": audit.get("model_calls", 0)}


def url_key(url: str) -> str:
    try:
        parts = urlsplit(url)
        return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/") or "/", parts.query, ""))
    except ValueError:
        return ""


def policy(config: dict | None) -> dict:
    """Invalid optional configuration disables pictures rather than stopping production."""
    try:
        return VisualPolicy.model_validate(config or {}).model_dump()
    except ValidationError:
        return {**VisualPolicy().model_dump(), "problem": "自动配图配置无效，已跳过"}


def kind_of(text: str) -> str:
    if re.search(r"价格|售价|资费|收费|费用|计费|订阅|\bpricing\b|\$\s*\d|美元|元[/／每]|月付|年付", text, re.I):
        return "pricing"
    if re.search(r"发布|推出|上线|公告|宣布|更新|\brelease[ds]?\b|\blaunch\b|announc", text, re.I):
        return "announcement"
    if re.search(r"功能|支持|能够|可以|界面|截图|页面|操作|文档|接口|\bfeature|\bAPI\b|\bscreenshot", text, re.I):
        return "feature"
    return "text"


def source_subject(url: str) -> str:
    """Name the visual object from an existing link, without inventing URLs."""
    parts = urlsplit(url)
    path = [p for p in parts.path.split("/") if p]
    if parts.hostname == "github.com" and len(path) >= 2:
        return path[1]
    host = (parts.hostname or "").split(".")
    return host[-2] if len(host) >= 2 else ""


def visual_goal(text: str, title: str, url: str) -> str:
    if re.search(r"示例|代码|输入|输出|结果|安装|调用|\bexample|\bdemo|\busage", text, re.I):
        return "展示相关的输入、代码示例或输出结果"
    if "github.com/" in url:
        return "展示项目 README 中的功能演示、用法或示例"
    return "展示当前讲解的产品界面或功能区域"


def needs_for(version: dict, report_sources: list[dict] | None = None) -> list[dict]:
    """Find visual objects in the supplied article links and authored paragraphs."""
    meta = version.get("meta") or {}
    sheet_row = store.get_fact_sheet(meta["fact_sheet_id"]) if meta.get("fact_sheet_id") else None
    sheet = (sheet_row or {}).get("sheet") or {}
    known = {fact["id"]: fact for fact in sheet.get("facts", [])}
    sources = {url_key(s["url"]): s for s in report_sources or [] if s.get("url")}
    out = []
    ci = 0
    for section in version["sections"]:
        paragraphs = [p for p in section["paragraphs"] if p["text"].strip()]
        if not paragraphs:
            continue
        for pi, paragraph in enumerate(paragraphs):
            text = paragraph["text"].strip()
            kind = kind_of(text)
            facts = [known[fid] for fid in paragraph.get("facts", []) if fid in known]
            urls = list(dict.fromkeys(u for f in facts for u in f.get("sources", [])))
            if not urls:
                urls.extend(u for u in paragraph.get("citations", []) if u not in urls)
            # A product's examples can be linked in the article without being a
            # citation on this exact paragraph. Match only supplied object names.
            names = entities(text)
            for source in report_sources or []:
                haystack = (source.get("title", "") + " " + source.get("url", "")).lower()
                if any(name in haystack for name in names) and source.get("url") not in urls:
                    urls.append(source["url"])
            urls = [u for u in urls if isinstance(u, str) and u.startswith(("https://", "http://"))]
            if not urls:
                continue
            urls.sort(key=lambda u: (not ("github.com/" in u or re.search(r"/docs?[/\.]|/examples?[/#]", u)),
                                     not str(sources.get(url_key(u), {}).get("kind", "")).startswith("一手")))
            out.append({"id": f"c{ci+1}p{pi+1}", "chapter_index": ci, "paragraph_index": pi,
                        "title": section["title"], "text": text, "kind": kind if kind != "text" else "feature", "urls": urls[:4],
                        "visual_subject": source_subject(urls[0]), "visual_goal": visual_goal(text, section["title"], urls[0]),
                        "facts": [{k: f.get(k, "") for k in ("id", "claim", "confidence", "caveat")} for f in facts],
                        "claims": text})
        ci += 1
    return out


def intent(script: dict, version: dict, request: dict, config: dict | None, report_sources: list[dict] | None = None) -> dict:
    chosen = policy(config)
    state, reason = "pending", "准备自动选择来源画面"
    context = request.get("production_context") or {}
    effective = {}
    for key in ("content_recipe", "matrix"):
        effective.update((context.get(key) or {}).get("settings") or {})
    effective.update({k: request[k] for k in ("production_mode", "video_form") if k in request})
    if not chosen["enabled"]:
        state, reason = "skipped", chosen.get("problem") or "自动配图未开启"
    elif screenshots.has_selection(script["id"], version["version"]):
        state, reason = "manual", "使用人工保存的配图选择"
    elif request.get("image_materials"):
        state, reason = "manual", "使用已提交的图片素材"
    elif (effective.get("production_mode") == "fast" or effective.get("video_form") == "information_cards") and not fast_visuals(request):
        state, reason = "skipped", "快速卡片使用文字画面"
    elif (script.get("topic", {}).get("article") or {}).get("kind") == "literary":
        state, reason = "skipped", "文学朗读保留原有画面形式"
    needs = needs_for(version, report_sources) if state == "pending" else []
    job = research_dossier.PRODUCTIONS.get(script.get("production"), "")
    if needs and job and not job.startswith(("ai_media/", "monitoring/")):
        # The same frozen column direction also guides source pictures. A
        # historical object or a map should not inherit the software UI goal.
        presentation = (script.get("content_context") or {}).get("presentation") or {}
        preferred = presentation.get("preferred_visuals") or []
        strategy = presentation.get("strategy") or ""
        if preferred or strategy:
            goal = ["展示当前段落正在讲解的对象、可见细节与对应关系。"]
            if preferred:
                goal.append("栏目画面偏好：" + "、".join(preferred) + "。")
            if strategy:
                goal.append("栏目画面方向：" + strategy)
            for need in needs:
                need["visual_goal"] = "\n".join(goal)
    if state == "pending" and not needs:
        state, reason = "skipped", "没有适合截图且具备可追溯来源的段落"
    return {"version": chosen["version"], "state": state, "reason": reason, "policy": chosen,
            "chapter_hash": screenshots.chapter_hash(version), "needs": needs, "decisions": [],
            "selection": {"form": "object_showcase", "materials": []}}
