"""Budgeted platform copies. Never rewrite facts or truncate user prose and URLs."""
from __future__ import annotations

import re
from copy import deepcopy

from core.social.publish_text import combined_text, count


def limits(platform: str) -> dict:
    """Current integration limits, shared with preflight and UI counters."""
    if platform in {"bilibili", "douyin"}:
        bili = platform == "bilibili"
        return {"platform": platform, "name": "B站" if bili else "抖音", "title_min": 1,
                "title_limit": 80 if bili else 30, "description_limit": 2000 if bili else 1000,
                "tag_limit": 10 if bili else 5, "tag_min": 1 if bili else 0, "tag_length_limit": 20,
                "combined_limit": None if bili else 1000, "combined_label": "简介和话题合计" if not bili else "简介",
                "counter": "codepoint", "comment_limit": 1000 if bili else 500, "comment_counter": "codepoint"}
    from core.social.registry import definition
    spec = definition(platform)
    return {**{k: spec[k] for k in ("title_min", "title_limit", "description_limit", "tag_limit", "combined_limit", "name")},
            "platform": platform, "tag_min": 0, "tag_length_limit": spec["tag_length_limit"],
            "combined_label": "标题、简介和话题合计", "counter": "codepoint",
            "comment_limit": spec["first_comment_limit"], "comment_counter": "utf16"}


def inspect(platform: str, payload: dict) -> dict:
    """Measure the same composition used by upload adapters, including separators."""
    spec = limits(platform)
    title, description, tags = payload.get("title", ""), payload.get("description", ""), payload.get("tags", [])
    title = title.strip()
    combined = count(combined_text(platform, title, description, tags))
    remaining = spec["description_limit"]
    if spec["combined_limit"] is not None:
        remaining = min(remaining, max(0, spec["combined_limit"] - count(combined_text(platform, title, "", tags))
                                       - (2 if platform == "kuaishou" and title else 0)))
    issues = []
    if not spec["title_min"] <= count(title) <= spec["title_limit"]:
        issues.append(f"标题需为 {spec['title_min']}–{spec['title_limit']} 字，当前 {count(title)} 字。")
    if count(description) > spec["description_limit"]:
        issues.append(f"简介最多 {spec['description_limit']} 字，当前 {count(description)} 字。")
    if not spec["tag_min"] <= len(tags) <= spec["tag_limit"]:
        issues.append(f"话题需为 {spec['tag_min']}–{spec['tag_limit']} 个，当前 {len(tags)} 个。")
    for tag in tags:
        if (not tag.strip() or count(tag) > spec["tag_length_limit"] or re.search(r"[\r\n,，]", tag)
                or platform == "douyin" and re.search(r"\s", tag)):
            issues.append(f"话题格式不正确，每个最多 {spec['tag_length_limit']} 字，不能含分隔符。")
            break
    if spec["combined_limit"] is not None and combined > spec["combined_limit"]:
        issues.append(f"{spec['combined_label']}超限：当前 {combined} 字，最多 {spec['combined_limit']} 字。")
    return {"limits": spec, "counts": {"title": count(title), "description": count(description), "tags": len(tags),
            "combined": combined, "description_budget": remaining}, "issues": issues, "valid": not issues}


_CLAUSE = re.compile(r"[：:，,；;｜|—]\s*")
_QUALIFIER = re.compile(r"仅|限于|限时|只在|只适|只有|如果|除非|假如|前提|条件|测试|实测|样机|尚未|暂未|可能|预计|据称|声称|待核|常压|不代表|不等于|不能推断|未经|在.+下")
_CLAIM = re.compile(r"\d|[%％$￥]|提升|降低|超过|低于|高于|最[快好强低高]|倍")


def short_title(title: str, spec: dict) -> str:
    if count(title) <= spec["title_limit"]:
        return title
    # Only an intact, non-quantitative opening clause can stand alone. A
    # qualification after the separator must not turn into an unconditional claim.
    match = _CLAUSE.search(title)
    if match:
        head, tail = title[:match.start()].strip(), title[match.end():]
        if spec["title_min"] <= count(head) <= spec["title_limit"] and not _CLAIM.search(head) and not _QUALIFIER.search(tail):
            return head
    return title


def _tags(values: list[str], spec: dict) -> list[str]:
    out = []
    for raw in values:
        tag = raw.strip().lstrip("#")
        if (tag and tag not in out and count(tag) <= spec["tag_length_limit"] and not re.search(r"[\r\n,，]", tag)
                and not (spec["platform"] == "douyin" and re.search(r"\s", tag))):
            out.append(tag)
    return out[:spec["tag_limit"]]


def _manual_body(description: str, refs: list[dict]) -> str | None:
    headings = {"参考来源（文稿引用）：", "报告补充资料：", "主要来源："}
    lines = description.splitlines()
    known = {r["url"] for r in refs}
    known.update(f"· {r['title']}{('（' + r['date'] + '）') if r.get('date') else ''} {r['url']}" for r in refs)
    start = next((i for i, line in enumerate(lines) if line.strip() in headings), None)
    if start is None or not any(line.strip() in known for line in lines[start + 1:]):
        return None
    if any(line.strip() and line.strip() not in known | headings for line in lines[start + 1:]):
        return None
    return "\n".join(lines[:start]).rstrip()


def adapt(pack: dict, platform: str, current: dict | None = None) -> dict:
    """Suggest a copy while retaining required prose, complete URLs and originals."""
    spec = limits(platform)
    original = deepcopy(current or {k: pack.get(k, [] if k == "tags" else "") for k in ("title", "description", "tags")})
    candidate = deepcopy(original)
    candidate["title"] = short_title(original.get("title", "").strip(), spec)
    candidate["tags"] = _tags(original.get("tags", []), spec)
    content = pack.get("content") or {}
    refs = list(pack.get("references") or [])
    structured = current is None or original.get("description", "") == pack.get("description", "")
    manual_body = None if structured else _manual_body(original.get("description", ""), refs)
    reflow_refs = structured or manual_body is not None
    notes = []
    if structured:
        required = list(content.get("required") or [])
        if not required:
            # Older/test packs may not have block metadata. Preserve their prose.
            required = [pack.get("description", "")]
    else:
        # Manual prose is indivisible: splitting at a paragraph can remove a
        # caveat. Recognize the exact original auto-generated reference suffix only.
        desc = original.get("description", "")
        required = [manual_body if manual_body is not None else desc]
        notes.append("保留手动编辑的正文；未自动删句或改写。")
    candidate["description"] = "\n\n".join(x for x in required if x)
    selected = []
    if reflow_refs:
        body = candidate["description"]
        for ref in refs:
            line = ref["url"]
            proposed = body + ("\n\n" if body else "") + "主要来源：\n" + "\n".join([*selected, line])
            check = inspect(platform, {**candidate, "description": proposed})
            if (count(proposed) <= spec["description_limit"] and
                    (spec["combined_limit"] is None or check["counts"]["combined"] <= spec["combined_limit"] - 12)):
                selected.append(line)
        if selected:
            candidate["description"] = body + ("\n\n" if body else "") + "主要来源：\n" + "\n".join(selected)
        if len(selected) < len(refs):
            notes.append(f"简介保留 {len(selected)}/{len(refs)} 条主要来源；完整出处保留在原发布包中。")
        # Chapters are useful on longer platforms, only when the whole block fits.
        chapter_text = content.get("chapters", "") if structured else ""
        if chapter_text:
            proposal = {**candidate, "description": candidate["description"] + "\n\n" + chapter_text}
            if inspect(platform, proposal)["valid"]:
                candidate = proposal
            else:
                notes.append("章节时间保留在原发布包中。")
    if candidate["title"] != original.get("title", ""):
        notes.append("标题使用原题中完整的开头分句，未截断型号或数字。")
    if candidate["tags"] != original.get("tags", []):
        notes.append(f"按本平台规则整理话题，保留 {len(candidate['tags'])} 个。")
    check = inspect(platform, candidate)
    if reflow_refs and refs and not selected:
        check["issues"].append("主要来源未能放入简介，请整理正文或提供可用出处；完整出处仍保留。")
        check["valid"] = False
    if not check["valid"]:
        notes.append("剩余内容无法安全压缩，请按标出的问题手动编辑；未截断原句、型号或网址。")
    return {"payload": candidate, "before": original, "check": check, "notes": notes,
            "references": refs, "source_description": pack.get("description", ""),
            "references_in_description": len(selected) if reflow_refs else None}
