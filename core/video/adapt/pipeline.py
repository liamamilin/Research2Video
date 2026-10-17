"""Run the adaptation: fact sheet → plan → draft → review, with up to two rounds of fixes.

A round rewrites only the flagged paragraphs, then re-runs the rule checks and
the newcomer's read on the whole script (flow is a whole-script property) and
the fidelity check on the changed paragraphs. The best version seen is kept;
whatever is still wrong goes to the reviewer, never hidden.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Optional

from .. import length, writer
from . import draft, facts, review
from . import plan as planning
from .common import AdaptError

MAX_ROUNDS = 2


@dataclass
class Adapted:
    title: str
    sections: list[dict]
    length_check: dict
    meta: dict


def _review(client, sections, plan, sheet, column, style, problems: list[str],
            previous: Optional[dict] = None, changed: Optional[set[str]] = None) -> dict:
    rule_issues = review.rules(sections, column=column, target_minutes=column.target_minutes,
                               plan=plan, sheet=sheet, style=style)
    reading = fact_check = None
    try:
        reading = review.newcomer(client, sections, plan, style)
        if previous and previous.get("fidelity") is not None and changed is not None:
            fact_check = review.fidelity(client, sections, plan, sheet, reading["retell"], only=changed)
            kept = [i for i in previous["fidelity"]["issues"] if i["id"] not in changed]
            fact_check["issues"] = kept + fact_check["issues"]
        else:
            fact_check = review.fidelity(client, sections, plan, sheet, reading["retell"])
    except AdaptError as exc:
        problems.append(f"模型审校没有完成：{exc}")
    return {"rules": rule_issues, "newcomer": reading, "fidelity": fact_check,
            "summary": review.summarize(rule_issues, reading, fact_check)}


def adapt(
    *,
    report: str,
    citation_check: Optional[dict],
    topic: dict,
    column,
    style,
    llm_config,
    sheet: Optional[dict] = None,
    on_sheet: Optional[Callable[[dict], None]] = None,
    on_stage: Optional[Callable[[str], None]] = None,
    cancel_token: Optional[threading.Event] = None,
) -> tuple[Adapted, dict]:
    """(script, fact sheet).

    Pass ``sheet`` to reuse the one made for this report; ``on_sheet`` gets a
    newly made sheet at once, so a later step failing does not waste it.
    """
    from core.llm import LLMClient

    client = LLMClient(llm_config, cancel_token=cancel_token)
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
    checks = _review(client, sections, plan, sheet, column, style, problems)
    best = (sections, checks)
    rounds = 0
    while not checks["summary"]["passed"] and rounds < MAX_ROUNDS and checks["newcomer"] is not None:
        todo = review.targets(sections, checks["rules"], checks["newcomer"], checks["fidelity"] or {})
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
        checks = _review(client, sections, plan, sheet, column, style, problems, previous=checks, changed=changed)
        if checks["summary"]["score"] <= best[1]["summary"]["score"]:
            best = (sections, checks)
    sections, checks = best
    writer._require_chinese(sections)

    budgets = [b["budget"] for b in plan["beats"]] if len(plan["beats"]) == len(sections) else None
    check = length.measure(sections, column.target_minutes, section_budgets=budgets)
    return Adapted(
        title=title,
        sections=sections,
        length_check=check,
        meta={
            "writer": "adapt",
            "style": style.as_dict(),
            "fact_sheet_id": facts.sheet_id(report),
            "plan": plan,
            "checks": {**checks, "rounds": rounds},
            "description": draft.description(raw_description, sections, plan, sheet),
            "usage": dict(client.usage),
            "problems": problems + writer.lint(sections),
        },
    ), sheet
