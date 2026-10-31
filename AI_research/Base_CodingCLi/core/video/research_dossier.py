"""Productized columns carry research explanations and original detail to one author.

Selection reuses the existing event's links. It neither summarizes the saved
documents nor runs another model before writing.
"""
from __future__ import annotations

import hashlib
import re

from core.provenance import extract_urls, normalize_url
from .excerpt import heading_text

VERSION = 4
PRODUCTIONS = {
    "flash_daily": "monitoring/ai_flash_discovery",
    "ai_media_opensource_digest": "ai_media/daily_opensource_radar",
    "ai_media_arena_digest": "ai_media/weekly_model_arena",
    "ai_media_business_digest": "ai_media/weekly_business_watch",
    "ai_media_deep_dive": "ai_media/weekly_deep_research",
    "science_daily": "science/science_daily",
    "science_weekly": "science/science_weekly",
    "company_daily": "company/company_daily",
    "company_weekly": "company/company_weekly",
    "economy_daily": "economy/economy_daily",
    "economy_weekly": "economy/economy_weekly",
    "industry_daily": "industry/industry_daily",
    "industry_weekly": "industry/industry_weekly",
    "consumer_daily": "consumer/consumer_daily",
    "consumer_weekly": "consumer/consumer_weekly",
    "history_daily": "history/history_daily",
    "history_weekly": "history/history_weekly",
    "geography_daily": "geography/geography_daily",
    "geography_weekly": "geography/geography_weekly",
    "culture_daily": "culture/culture_daily",
    "culture_weekly": "culture/culture_weekly",
}


def enabled(script: dict) -> bool:
    """Enable joint writing only for the columns migrated so far."""
    return script.get("production") in PRODUCTIONS


def selection_brief(report: str, script: dict) -> str:
    """Give the existing domain-story organizer the column's viewing purpose.

    The writer still receives the original report. Existing AI requests retain
    their selection input; the domain migration reuses this same organizer call.
    """
    job = PRODUCTIONS.get(script.get("production"), "")
    context = script.get("content_context") or {}
    if not job or job.startswith(("ai_media/", "monitoring/")) or not context.get("notes"):
        return report
    lines = ["## 本栏目选材方向"]
    for key, label in (("column_name", "栏目"), ("audience", "观众"),
                       ("promise", "观看收获"), ("notes", "创作方向")):
        if context.get(key):
            lines.append(f"{label}：{context[key]}")
    lines += ["优先排列底稿已经展开、能实现本栏目观看收获的具体问题。"
              "原始材料中的其他事件作为补充候选，不因标题醒目替换底稿的主例子。",
              "", "## 已完成研究底稿", report]
    return "\n".join(lines)


def select_report(report: str, story: dict, pool: dict, script: dict | None = None) -> tuple[str, str]:
    """Keep whole relevant research sections, including their nested examples.

    A report without an identifiable section remains available as research
    context; the already selected event still owns the author's topic.
    """
    if not story:
        return report, "report"
    # A domain explainer can compare several routes under one explicit main
    # question. Those numbered sections are parts of the explanation, not
    # independent news candidates: keep the already loaded dossier together.
    job = PRODUCTIONS.get((script or {}).get("production"), "")
    context = (script or {}).get("content_context") or {}
    if (job and not job.startswith(("ai_media/", "monitoring/"))
            and context.get("type") == "explainer"
            and re.search(r"(?m)^##\s+本期主问题[：:]", heading_text(report))):
        return report, "column_explanation"
    selected = set(story.get("primary", []) + story.get("background", []))
    links = {normalize_url(u) for p in pool.get("packets", []) if p["id"] in selected for u in p["links"]}
    links.update(normalize_url(u) for u in story.get("primary_sources", []))
    bibliography = {}
    for line in report.splitlines():
        if match := re.match(r"\s*(?:\[(\d+)\]|(\d+)[.、)])\s*", line):
            bibliography[match[1] or match[2]] = extract_urls(line)
    # A candidate is commonly ### 2.1; its #### process and code blocks must
    # travel with it, rather than ending at the first nested heading.
    outline = heading_text(report)
    candidates = list(re.finditer(r"(?m)^###\s+(?:[A-Z]*\d+(?:\.\d+)*[.、]?\s+)", outline))
    headings = candidates if len(candidates) > 1 else list(re.finditer(r"(?m)^##\s+", outline))
    headings = sorted(headings + list(re.finditer(
        r"(?m)^#{1,3}\s*(?:\d+[.、 ]*)?(?:来源清单|参考文献)", outline)), key=lambda m: m.start())
    if not headings:
        return report, "report_context"
    bounds = [m.start() for m in headings] + [len(report)]
    kept = []
    subject = re.sub(r"[\W_]+", "", (story.get("subject") or "").casefold())
    for start, end in zip(bounds, bounds[1:]):
        block = report[start:end].strip()
        if re.match(r"^#{1,6}\s*(?:\d+[.、 ]*)?(?:来源清单|参考文献)", block):
            continue
        refs = [u for n in re.findall(r"\[(\d+)\]", block) for u in bibliography.get(n, [])]
        named_subject = bool(subject and len(subject) >= 3 and
                             subject in re.sub(r"[\W_]+", "", block.casefold()))
        if links & {normalize_url(u) for u in extract_urls(block) + refs} or named_subject:
            if refs:
                block += "\n\n本节引用：\n" + "\n".join(dict.fromkeys(refs))
            kept.append(block)
    return ("\n\n".join(kept), "selected_sections") if kept else (report, "report_context")


def receipt(report: str, material: str, selection: str) -> dict:
    """Describe the exact author inputs using the script's existing metadata."""
    return {"version": VERSION, "selection": selection,
            "report_sha256": hashlib.sha256(report.encode()).hexdigest(),
            "material_sha256": hashlib.sha256(material.encode()).hexdigest(),
            "report_chars": len(report), "material_chars": len(material)}
