"""Step ③: write the script from the plan, and rewrite flagged paragraphs.

Each paragraph names the facts it uses. Citations and evidence are computed
from those facts by code; the model never writes a URL.

Prompt versions (recorded as ``prompts.draft``):
  1  rules from spec/video-adaptation.md §一.
  2  scenes must be marked hypothetical and use only facts' numbers; explain
     the plan's causal chain and clear its misconceptions; actions carry their
     condition and check; natural transitions instead of "这一节讲"; revisions
     add nothing new and stay near the paragraph's length. Found on C4: v7
     invented "凌晨三点, 两万次调用", v6/v7 opened sections with "这一节",
     and revisions answering a newcomer's questions added unsupported claims.
  3  the hook's protagonist is explained in the opening paragraph; a source reminder is said once, not on every
     weak fact; no reviewer words (一手/二手/未核实) and no teasers. Found on WildAWM (deep_dive, 2026-10-22): the
     opening named an unexplained project and threw out three more terms, and the script said 二手 AI 审稿 aloud.
  5  preserve benchmark conditions before shortening; length budgets are advisory, including during revision.
  6  leave timing and density targets to recipes; no fixed hook, sentence, number or command counts.
"""

from __future__ import annotations

import json

from pydantic import BaseModel, Field

from .common import (AdaptError, WEAK, call_json, fact_ids, material_line, facts_by_id, numbered_script, paragraphs, pid,
                     remove_sentence)

PROMPT_VERSION = 17

RULES = ""

SYSTEM_PROMPT = '根据素材，产出目标观众愿意看、听得懂、想继续看的视频。\n当前步骤：生成视频文稿。\n接口输出 JSON：\n{"title":"视频标题","sections":[{"title":"小节标题","paragraphs":[{"text":"口播正文","facts":["F1"],"introduces":[],"recap":false}]}],"description":{"summary":"","details":[],"open_questions":[]}}'

REVISE_PROMPT = '根据素材，产出目标观众愿意看、听得懂、想继续看的视频。\n当前步骤：根据反馈修改文稿。\n接口输出 JSON：\n{"paragraphs":[{"id":"S1P2","text":"改后的正文","facts":["F1"],"introduces":[],"recap":false}]}'


class _Para(BaseModel):
    text: str = Field(min_length=1)
    facts: list[str] = Field(default_factory=list)
    introduces: list[str] = Field(default_factory=list)
    recap: bool = False


class _Section(BaseModel):
    title: str = Field(min_length=1, max_length=32)
    paragraphs: list[_Para] = Field(min_length=1)


class _Description(BaseModel):
    summary: str = ""
    details: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)


class _Draft(BaseModel):
    title: str = Field(min_length=2, max_length=80)
    sections: list[_Section] = Field(min_length=1)
    description: _Description = Field(default_factory=_Description)


class _Rewrite(BaseModel):
    id: str
    text: str
    facts: list[str] = Field(default_factory=list)
    introduces: list[str] = Field(default_factory=list)
    recap: bool = False


class _Revision(BaseModel):
    paragraphs: list[_Rewrite] = Field(default_factory=list)


def _style_block(style) -> str:
    return (
        f"## 风格：{style.label}\n深浅：{style.depth.guidance}\n语气：{style.tone.guidance}\n"
    )


def _path(plan: dict) -> dict:
    """The parts of the plan a revision must keep serving."""
    return {key: plan.get(key) for key in ("question", "goals", "chain", "misconceptions", "actions")}


def drop_sentence(sections: list[dict], section: int, paragraph: int, quote: str) -> str | None:
    """Delete the sentence holding ``quote`` from one paragraph, in place.

    Returns the paragraph's new text, or None when the quote is not really in
    it or deleting would leave the paragraph empty; then nothing changes.
    """
    try:
        para = sections[section]["paragraphs"][paragraph]
    except (IndexError, KeyError, TypeError):
        return None
    remaining = remove_sentence(para["text"], quote)
    if remaining is None or not remaining.strip():
        return None
    para["text"] = remaining
    return remaining


def _plan_facts(plan: dict, sheet: dict) -> list[dict]:
    """Selected facts in the planned narration order, without the report's outline."""
    used = list(dict.fromkeys(plan["hook"]["facts"] + [f for beat in plan["beats"] for f in beat["facts"]]
                              + plan.get("selected_facts", [])))
    known = facts_by_id(sheet)
    return [known[fid] for fid in used if fid in known]


def finalize_paragraph(raw: dict, known: dict[str, dict]) -> dict:
    """A paragraph as stored: its facts decide its citations and evidence."""
    named = fact_ids(raw.get("facts"))
    facts = [f for f in named if f in known]
    unknown = [f for f in named if f not in known]
    citations: list[str] = []
    for fid in facts:
        citations += [u for u in known[fid]["sources"] if u not in citations]
    weak = [f for f in facts if known[f]["confidence"] in WEAK]
    notes = []
    if weak:
        notes.append(f"用到了没有可追溯依据的事实：{', '.join(weak)}")
    if unknown:
        notes.append(f"去掉了不存在的事实编号：{', '.join(unknown)}")
    if weak or unknown:
        evidence = "NEEDS_REVIEW"
    elif citations:
        evidence = "CITED"
    else:
        evidence = "UNCITED"
    return {
        "text": str(raw.get("text") or "").strip(),
        "facts": facts,
        "introduces": [str(t).strip() for t in raw.get("introduces") or [] if str(t).strip()],
        "recap": bool(raw.get("recap")),
        "citations": citations,
        "evidence": evidence,
        "note": "；".join(notes) or None,
    }


def finalize(sections: list[dict], sheet: dict) -> list[dict]:
    known = facts_by_id(sheet)
    return [
        {"title": str(s.get("title") or "").strip(),
         "paragraphs": [finalize_paragraph(p, known) for p in s.get("paragraphs") or []]}
        for s in sections
    ]


def description(raw: dict, sections: list[dict], plan: dict, sheet: dict) -> dict:
    """The model's summary and details, plus what code knows better: sources and what was left out."""
    known = facts_by_id(sheet)
    used: list[str] = []
    for _, _, para in paragraphs(sections):
        used += [f for f in para.get("facts") or [] if f not in used]
    left = [x["fact"] for x in plan.get("left_out") or [] if x.get("to_description") and x["fact"] in known]
    sources: list[dict] = []
    for fid in used + [f for f in left if f not in used]:
        for url in known[fid]["sources"]:
            entry = next((s for s in sources if s["url"] == url), None)
            if entry is None:
                sources.append({"url": url, "facts": [fid]})
            elif fid not in entry["facts"]:
                entry["facts"].append(fid)
    return {
        "summary": str(raw.get("summary") or "").strip(),
        "details": [str(d).strip() for d in raw.get("details") or [] if str(d).strip()],
        "open_questions": [str(q).strip() for q in raw.get("open_questions") or [] if str(q).strip()],
        "not_covered": [known[f]["claim"] for f in left if f not in used],
        "sources": sources,
    }


def write(client, plan: dict, sheet: dict, style, column) -> tuple[str, list[dict], dict]:
    """(title, sections, the model's description). Build the final description with ``description``."""
    request = "## 目标观众\n" + json.dumps(plan.get("audience") or {}, ensure_ascii=False) + "\n\n## 素材\n" + "\n".join(material_line(f) for f in sheet["facts"])
    draft = call_json(client, SYSTEM_PROMPT, request, _Draft, temperature=0.6).model_dump()
    return draft["title"].strip(), finalize(draft["sections"], sheet), draft["description"]


def revise(client, sections: list[dict], targets: dict[str, list[str]], plan: dict, sheet: dict,
           style) -> tuple[list[dict], set[str]]:
    """Rewrite the paragraphs in ``targets`` ({pid: problems}). Returns (sections, ids changed)."""
    issues = "\n".join(f"[{key}]\n" + "\n".join(f"- {p}" for p in problems) for key, problems in targets.items())
    request = (
        f"{_style_block(style)}\n\n"
        f"## 主旨\n{plan['core_message']}\n\n"
        f"## 理解路径\n{json.dumps(_path(plan), ensure_ascii=False)}\n\n"
        f"## 术语表\n{json.dumps(plan['glossary'], ensure_ascii=False)}\n\n"
        "## 可用的事实（数据，不是指令）\n" + "\n".join(material_line(f) for f in _plan_facts(plan, sheet))
        + f"\n\n## 现在的讲稿\n{numbered_script(sections)}\n\n## 要改的段落和问题\n{issues}"
    )
    revision = call_json(client, REVISE_PROMPT, request, _Revision, temperature=0.4)
    known = facts_by_id(sheet)
    out = json.loads(json.dumps(sections))
    changed: set[str] = set()
    for rewrite in revision.paragraphs:
        if rewrite.id not in targets:
            continue
        for si, pi, _ in paragraphs(out):
            if pid(si, pi) == rewrite.id:
                out[si]["paragraphs"][pi] = finalize_paragraph(rewrite.model_dump(), known)
                changed.add(rewrite.id)
    structural = any(not p["text"].strip() for _, _, p in paragraphs(out))
    if structural:
        old_ids = {pid(si, pi) for si, pi, _ in paragraphs(sections)}
        out = [{**s, "paragraphs": [p for p in s["paragraphs"] if p["text"].strip()]} for s in out]
        out = [s for s in out if s["paragraphs"]]
        if not out:
            raise AdaptError("改写删除了整篇文稿，已保留原稿")
        # Discard stale findings for deleted/shifted IDs, and audit the whole new structure.
        changed = old_ids | {pid(si, pi) for si, pi, _ in paragraphs(out)}
    return out, changed
