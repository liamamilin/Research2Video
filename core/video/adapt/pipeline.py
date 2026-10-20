"""Run the adaptation: fact sheet → plan → draft → three audits, with up to two rounds of fixes.

A round rewrites only the flagged paragraphs, serious factual problems first,
then re-runs the rule checks and the newcomer's read on the whole script (flow
is a whole-script property) and the fact audit on the changed paragraphs. The
best version seen is kept.

A serious factual problem that survives the rounds loses its sentence. The
auditor's quote must really be in the paragraph; the opening paragraph and the
core message are never cut (they are flagged for the reviewer instead); a
paragraph is never emptied; every deletion is recorded. Nothing here blocks
approval: whatever is left is shown to the reviewer.
"""

from __future__ import annotations

import copy
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Optional

from .. import length, writer
from . import draft, facts, review
from . import plan as planning
from .common import AdaptError, pid

MAX_ROUNDS = 2


@dataclass
class Adapted:
    title: str
    sections: list[dict]
    length_check: dict
    meta: dict


def prompt_versions() -> dict:
    return {"facts": facts.PROMPT_VERSION, "plan": planning.PROMPT_VERSION,
            "draft": draft.PROMPT_VERSION, "review": review.PROMPT_VERSION}


def _audit(client, auditor, sections, plan, sheet, column, style, problems: list[str],
           previous: Optional[dict] = None, changed: Optional[set[str]] = None) -> dict:
    """All three audits. Returns {"checks", "reading", "audit"}.

    The newcomer reads with the writing model ``client``: a stronger model asks several times as many
    questions of the same script, which no script can pass. The fact audit runs on ``auditor``.
    """
    rule_issues = review.rules(sections, column=column, target_minutes=column.target_minutes,
                               plan=plan, sheet=sheet, style=style)
    hints = [i for i in rule_issues if i["rule"] == "D6"]
    reading = fact_check = None
    try:
        reading = review.newcomer(client, sections, plan, style)
        if previous and previous.get("audit") is not None and changed is not None:
            fact_check = review.fact_audit(auditor, sections, plan, sheet, reading, hints, only=changed)
            kept = [i for i in previous["audit"]["issues"] if pid(i["section"], i["paragraph"]) not in changed]
            fact_check["issues"] = kept + fact_check["issues"]
        else:
            fact_check = review.fact_audit(auditor, sections, plan, sheet, reading, hints)
    except AdaptError as exc:
        problems.append(f"模型审计没有完成：{exc}")
    return {"checks": review.assemble(rule_issues, reading, fact_check, plan, sections),
            "reading": reading, "audit": fact_check}


def _squash(text: str) -> str:
    return re.sub(r"[\s，。！？；、：「」“”\"'（）()]", "", text or "")


def _delete_serious(sections: list[dict], state: dict, plan: dict) -> list[dict]:
    """Delete the sentences of serious findings the rounds did not fix. Returns the deletions."""
    deletions = []
    core = _squash(plan.get("core_message", ""))
    for item in (state.get("audit") or {}).get("issues") or []:
        if item["severity"] != review.SERIOUS or item["paragraph"] is None:
            continue
        si, pi = item["section"], item["paragraph"]
        quote = item.get("quote") or ""
        if (si, pi) == (0, 0):
            item["unhandled"] = "开场段不自动删除，请人工处理"
            continue
        if core and (core in _squash(quote) or (len(_squash(quote)) >= 6 and _squash(quote) in core)):
            item["unhandled"] = "这句承载主旨，不自动删除，请人工处理"
            continue
        before = sections[si]["paragraphs"][pi]["text"]
        if draft.drop_sentence(sections, si, pi, quote) is None:
            item["unhandled"] = "原句没在段落里找到，或者删掉会让这一段变空，请人工处理"
            continue
        item["deleted"] = True
        deletions.append({"section": si, "paragraph": pi, "quote": quote, "before": before,
                          "rule": item["rule"], "reason": item["problem"]})
    return deletions


def _mark_evidence(sections: list[dict], issues: list[dict]) -> None:
    """A paragraph with a serious finding left is not "cited", whatever its links say."""
    for item in issues:
        if item["severity"] != review.SERIOUS or item["paragraph"] is None:
            continue
        para = sections[item["section"]]["paragraphs"][item["paragraph"]]
        para["evidence"] = "NEEDS_REVIEW"
        note = f"事实审计：{item['problem']}"
        para["note"] = f"{para['note']}；{note}" if para.get("note") and note not in para["note"] else note


def _resolved(initial: list[dict], final: list[dict]) -> list[dict]:
    """The first audit's issues, each marked fixed or not by the end."""
    still = {(i["rule"], i["section"], i["paragraph"]) for i in final}
    return [{**i, "resolved": "deleted" if i.get("deleted") else (i["rule"], i["section"], i["paragraph"]) not in still}
            for i in initial]


def adapt(
    *,
    report: str,
    citation_check: Optional[dict],
    topic: dict,
    column,
    style,
    llm_config,
    review_llm_config=None,
    sheet: Optional[dict] = None,
    on_sheet: Optional[Callable[[dict], None]] = None,
    on_stage: Optional[Callable[[str], None]] = None,
    cancel_token: Optional[threading.Event] = None,
) -> tuple[Adapted, dict]:
    """(script, fact sheet).

    Pass ``sheet`` to reuse the one made for this report; ``on_sheet`` gets a
    newly made sheet at once, so a later step failing does not waste it.
    ``review_llm_config`` runs the fact audit on another model.
    """
    from core.llm import LLMClient

    client = LLMClient(llm_config, cancel_token=cancel_token)
    auditor = LLMClient(review_llm_config, cancel_token=cancel_token) if review_llm_config else client
    stage = on_stage or (lambda _name: None)
    problems: list[str] = []

    if sheet is None:
        stage("拆事实")
        sheet, sheet_problems = facts.extract(client, report, citation_check)
        problems += [f"事实底稿：{p}" for p in sheet_problems]
        if on_sheet:
            on_sheet(sheet)

    stage("定方案")
    plan, plan_problems = planning.make(client, sheet, column, topic, style, column.target_minutes)
    problems += plan_problems

    stage("写稿")
    title, sections, raw_description = draft.write(client, plan, sheet, style, column)
    writer._require_chinese(sections)

    stage("审校")
    state = _audit(client, auditor, sections, plan, sheet, column, style, problems)
    initial = copy.deepcopy(state["checks"]["issues"])
    best = (sections, state)
    rounds = 0
    while not state["checks"]["summary"]["passed"] and rounds < getattr(column, "revise_rounds", MAX_ROUNDS) and state["reading"] is not None:
        todo = review.targets(sections, state["checks"])
        if not todo:
            break
        rounds += 1
        stage("修改")
        try:
            sections, changed = draft.revise(client, sections, todo, plan, sheet, style)
        except AdaptError as exc:
            problems.append(f"第 {rounds} 轮修改没有成功：{exc}")
            break
        if not changed:
            break
        stage("审校")
        state = _audit(client, auditor, sections, plan, sheet, column, style, problems, previous=state, changed=changed)
        if state["checks"]["summary"]["rank"] <= best[1]["checks"]["summary"]["rank"]:
            best = (sections, state)
    sections, state = copy.deepcopy(best[0]), copy.deepcopy(best[1])

    deletions = _delete_serious(sections, state, plan)
    if deletions:
        state["audit"]["issues"] = [i for i in state["audit"]["issues"] if not i.get("deleted")]
        rule_issues = review.rules(sections, column=column, target_minutes=column.target_minutes,
                                   plan=plan, sheet=sheet, style=style)
        state["checks"] = review.assemble(rule_issues, state["reading"], state["audit"], plan, sections)
    for item in initial:  # the record says which first-round findings ended up deleted
        if any(d["section"] == item["section"] and d["paragraph"] == item["paragraph"] and d["rule"] == item["rule"]
               for d in deletions):
            item["deleted"] = True
    writer._require_chinese(sections)
    _mark_evidence(sections, state["checks"]["issues"])

    checks = {**state["checks"], "rounds": rounds, "deletions": deletions,
              "initial": _resolved(initial, state["checks"]["issues"])}
    budgets = [b["budget"] for b in plan["beats"]] if len(plan["beats"]) == len(sections) else None
    meta = {
        "writer": "adapt",
        "style": style.as_dict(),
        "prompts": prompt_versions(),
        "fact_sheet_id": facts.sheet_id(report),
        "plan": plan,
        "checks": checks,
        "description": draft.description(raw_description, sections, plan, sheet),
        "usage": dict(client.usage),
        "problems": problems + writer.lint(sections),
    }
    if auditor is not client:
        meta["review_usage"] = dict(auditor.usage)
    return Adapted(title=title, sections=sections,
                   length_check=length.measure(sections, column.target_minutes, section_budgets=budgets,
                                               voice=column.voice_id),
                   meta=meta), sheet
