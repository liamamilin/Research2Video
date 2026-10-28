"""The AI pilot's research explanations and original detail reach one author.

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
}


def enabled(script: dict) -> bool:
    """Keep the first release on the five existing production bindings."""
    return script.get("production") in PRODUCTIONS


def select_report(report: str, story: dict, pool: dict) -> tuple[str, str]:
    """Keep whole relevant research sections, including their nested examples.

    A report without an identifiable section remains available as research
    context; the already selected event still owns the author's topic.
    """
    if not story:
        return report, "report"
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
