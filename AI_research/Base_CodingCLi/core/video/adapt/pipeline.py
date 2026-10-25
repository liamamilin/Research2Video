"""Run the adaptation: fact sheet → plan → draft → three audits, with up to two rounds of fixes.

A fact-repair round rewrites the flagged paragraphs, serious factual problems first,
then re-runs the rule checks and the newcomer's read on the whole script (flow
is a whole-script property) and the fact audit on the changed paragraphs. The
best version seen is kept.

Concrete listening problems then get a whole-script editorial proposal. It may
change the title and structure; a fresh full audit and editorial comparison
decide whether to keep it. Failed proposals preserve the checked original.

A serious factual problem that survives the rounds loses its sentence. The
auditor's quote must really be in the paragraph; the opening paragraph and the
core message are never cut (they are flagged for the reviewer instead); a
paragraph is never emptied; every deletion is recorded. Nothing here blocks
approval: whatever is left is shown to the reviewer.
"""

from __future__ import annotations

import copy
import json
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Optional
from core.errors import CancelledError

from .. import editorial, length, writer
from ..policy import is_core_issue
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
    except CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - optional checks must not prevent the fact audit
        problems.append(f"表达审计没有完成（只作提醒）：{exc}")
    try:
        if previous and previous.get("audit") is not None and changed is not None:
            fact_check = review.fact_audit(auditor, sections, plan, sheet, reading or {}, hints, only=changed)
            kept = [i for i in previous["audit"]["issues"] if pid(i["section"], i["paragraph"]) not in changed]
            fact_check["issues"] = kept + fact_check["issues"]
        else:
            fact_check = review.fact_audit(auditor, sections, plan, sheet, reading or {}, hints)
    except CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - keep the draft and expose the unfinished fact audit
        problems.append(f"事实审计没有完成：{exc}")
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
    """An unresolved factual finding needs review regardless of its severity."""
    for item in issues:
        if not is_core_issue(item) or item["paragraph"] is None:
            continue
        para = sections[item["section"]]["paragraphs"][item["paragraph"]]
        para["evidence"] = "NEEDS_REVIEW"
        para["review_reason"] = item["problem"]
        note = f"事实审计：{item['problem']}"
        para["note"] = f"{para['note']}；{note}" if para.get("note") and note not in para["note"] else note


#: A rewrite of this many paragraphs or more that comes back unreadable is tried again with the worst half: a long
#: JSON reply is where the free models cut off or slip (seen on a paper explainer with 20 flagged spots).
REVISE_RETRY_FROM = 4


def _revise(client, sections, todo, plan, sheet, style):
    try:
        return draft.revise(client, sections, todo, plan, sheet, style)
    except AdaptError:
        if len(todo) < REVISE_RETRY_FROM:
            raise
        half = dict(list(todo.items())[:max(2, len(todo) // 2)])
        return draft.revise(client, sections, half, plan, sheet, style)


def _resolved(initial: list[dict], final: list[dict]) -> list[dict]:
    """The first audit's issues, each marked fixed or not by the end."""
    still = {(i["rule"], i["section"], i["paragraph"]) for i in final}
    return [{**i, "resolved": "deleted" if i.get("deleted") else (i["rule"], i["section"], i["paragraph"]) not in still}
            for i in initial]


def _sum_usage(*usages: dict) -> dict:
    out: dict = {}
    for usage in usages:
        for key, value in (usage or {}).items():
            out[key] = out.get(key, 0) + value
    return out


def _plan(client, *, report, citation_check, topic, column, style, sheet, on_sheet, stage,
          note: str = "", previous: Optional[dict] = None) -> tuple[dict, list[str], dict]:
    """Steps ① and ②: (plan, problems so far, fact sheet)."""
    problems: list[str] = []
    if sheet is None:
        stage("拆事实")
        sheet, sheet_problems = facts.extract(client, report, citation_check)
        problems += [f"事实底稿：{p}" for p in sheet_problems]
        if on_sheet:
            on_sheet(sheet)

    stage("定方案")
    plan, plan_problems = planning.make(client, sheet, column, topic, style, column.target_minutes,
                                        note=note, previous=previous)
    problems += plan_problems
    return plan, problems, sheet


def _editorial_plan(plan: dict, brief: dict, column) -> dict:
    """Use the new story's facts and purpose, never old positional quiz targets."""
    beats = brief.get("beats") or [{"point": brief["throughline"], "facts": brief["facts"]}]
    budgets = length.allocate(length.budget_for(column.target_minutes, column.voice_id), [1] * len(beats))
    return {**plan, "core_message": brief["angle"], "selected_facts": brief["facts"],
            "question": "", "goals": [], "chain": [],
            "glossary": [], "misconceptions": [], "actions": [], "must_cover": [], "left_out": [],
            "scope_out": brief.get("leave_out") or [],
            "hook": {"kind": "editorial", "idea": brief.get("opening", ""),
                     "facts": brief.get("opening_facts") or []},
            "beats": [{"title": b["point"][:32], "point": b["point"], "role": "", "terms": [],
                       "facts": b.get("facts") or [], "connection": b.get("connection", ""), "budget": units}
                      for b, units in zip(beats, budgets)]}


def _write(client, auditor, *, report, plan, sheet, column, style, problems: list[str], stage,
           usage_before: Optional[dict] = None) -> Adapted:
    """Steps ③–⑤ from a plan: draft, audits and fix rounds. ``problems`` are the plan step's, kept first."""
    problems = list(problems)
    column_style = column.script_style + "\n" + json.dumps(style.as_dict(), ensure_ascii=False)
    stage("写稿")
    title, sections, raw_description = draft.write(client, plan, sheet, style, column)
    writer._require_chinese(sections)

    stage("审校")
    state = _audit(client, auditor, sections, plan, sheet, column, style, problems)
    initial = copy.deepcopy(state["checks"]["issues"])
    rounds = 0
    repair_rounds = 0
    editorial_used = False
    editorial_attempt = None
    editorial_attempts = []

    def repair_current(sections, state):
        nonlocal rounds, repair_rounds
        best = (sections, state, rounds)
        while repair_rounds < getattr(column, "revise_rounds", MAX_ROUNDS) and state["audit"] is not None:
            todo = review.targets(sections, state["checks"])
            if not todo:
                break
            rounds += 1
            repair_rounds += 1
            stage("修改")
            try:
                sections, changed = _revise(client, sections, todo, plan, sheet, style)
            except AdaptError as exc:
                problems.append(f"第 {rounds} 轮修改没有成功：{exc}")
                break
            if not changed:
                break
            stage("审校")
            state = _audit(client, auditor, sections, plan, sheet, column, style, problems, previous=state, changed=changed)
            if (state["audit"] is not None
                    and state["checks"]["summary"]["rank"] <= best[1]["checks"]["summary"]["rank"]
                    and (state["checks"]["summary"]["passed"] or not best[1]["checks"]["summary"]["passed"])):
                best = (sections, state, rounds)
        return copy.deepcopy(best[0]), copy.deepcopy(best[1])

    # Whole-script editing can solve the cause of a listening problem and its
    # factual symptoms together; every proposed manuscript is audited below.
    if not review.editorial_targets(sections, state["checks"]):
        sections, state = repair_current(sections, state)

    for _ in range(editorial.MAX_ROUNDS):
        feedback = review.editorial_targets(sections, state["checks"])
        if not feedback or state["audit"] is None:
            break
        for key, notes in review.targets(sections, state["checks"]).items():
            feedback.setdefault(key, []).extend(notes)
        editorial_used = True
        rounds += 1
        editorial_attempt = {"applied": False, "mode": "whole_script", "before_title": title,
                             "before": copy.deepcopy(sections), "targets": feedback,
                             "prompt_version": editorial.PROMPT_VERSION}
        editorial_attempts.append(editorial_attempt)
        attempt_problems: list[str] = []
        try:
            stage("主编打磨")
            column_style = column.script_style + "\n" + json.dumps(style.as_dict(), ensure_ascii=False)
            proposal = editorial.rewrite(client, title=title, sections=sections,
                                         material=editorial.source_material(sheet), direction=plan,
                                         feedback=feedback, column_style=column_style,
                                         target_minutes=column.target_minutes, sheet=sheet,
                                         history=editorial_attempts[:-1])
            candidate = draft.finalize(proposal["sections"], sheet)
            for raw_section, section in zip(proposal["sections"], candidate):
                for raw, para in zip(raw_section["paragraphs"], section["paragraphs"]):
                    if raw.get("needs_review"):
                        para.update(evidence="NEEDS_REVIEW",
                                    review_reason=raw.get("review_reason") or "主编改写仍有未解决的事实疑点")
            writer._require_chinese(candidate)
            candidate_plan = _editorial_plan(plan, proposal["editorial_brief"], column)
            editorial_attempt.update(title=proposal["title"], sections=copy.deepcopy(candidate),
                                     editorial_brief=proposal["editorial_brief"])
            stage("整篇复审")
            # No old paragraph ids or findings survive reordering or deletion.
            candidate_state = _audit(client, auditor, candidate, candidate_plan, sheet, column, style, attempt_problems)
            repairs = []
            for _ in range(getattr(column, "revise_rounds", MAX_ROUNDS)):
                if candidate_state["checks"]["summary"]["passed"] or candidate_state["audit"] is None:
                    break
                todo = review.targets(candidate, candidate_state["checks"])
                if not todo:
                    break
                before_repair = copy.deepcopy(candidate)
                candidate, changed = _revise(client, candidate, todo, candidate_plan, sheet, style)
                if not changed:
                    break
                rounds += 1
                stage("编辑稿事实复审")
                candidate_state = _audit(client, auditor, candidate, candidate_plan, sheet, column, style, attempt_problems)
                repairs.append({"before": before_repair, "sections": copy.deepcopy(candidate),
                                "checks": copy.deepcopy(candidate_state["checks"])})
            editorial_attempt["source_repairs"] = repairs
            editorial_attempt["sections"] = copy.deepcopy(candidate)
            editorial_attempt["checks"] = copy.deepcopy(candidate_state["checks"])
            if candidate_state["checks"]["summary"]["passed"]:
                decision = editorial.compare(client, before_title=title, before=sections,
                                             after_title=proposal["title"], after=candidate,
                                             column_style=column_style)
                editorial_attempt["decision"] = decision
                if decision["choice"] == "B":
                    title, sections, state = proposal["title"], candidate, candidate_state
                    plan = candidate_plan
                    raw_description = proposal.get("description") or {"summary": title}
                    editorial_attempt["applied"] = True
            else:
                editorial_attempt["reason"] = "编辑稿未通过事实复核，保留此前已核对的文稿"
        except CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - retain the checked draft and failed attempt
            attempt_problems.append(f"主编打磨没有完成：{type(exc).__name__}: {exc}")
        editorial_attempt["problems"] = attempt_problems
        if not editorial_attempt["applied"] and not editorial_attempt.get("decision"):
            break

    if not state["checks"]["summary"]["passed"]:
        sections, state = repair_current(sections, state)

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
              "initial": _resolved(initial, state["checks"]["issues"]), "editorial_attempted": editorial_used}
    headline_result = None
    if checks.get("audits_complete") and checks["summary"]["passed"]:
        from .. import headline
        stage("标题打磨")
        headline_result = headline.finish(client, title=title, sections=sections, column_style=column_style)
        title = headline_result["title"]
    budgets = [b["budget"] for b in plan["beats"]] if len(plan["beats"]) == len(sections) else None
    meta = {
        "writer": "adapt",
        "style": style.as_dict(),
        "prompts": prompt_versions(),
        "fact_sheet_id": facts.sheet_id(report),
        "plan": plan,
        "checks": checks,
        **({"headline": headline_result} if headline_result else {}),
        "description": draft.description(raw_description, sections, plan, sheet),
        "usage": _sum_usage(usage_before, client.usage) if usage_before else dict(client.usage),
        **({"editorial_attempt": editorial.retained_attempt(editorial_attempts),
            "editorial_attempts": editorial_attempts} if editorial_attempt is not None else {}),
        "problems": problems + writer.lint(sections),
    }
    if auditor is not client:
        meta["review_usage"] = dict(auditor.usage)
    return Adapted(title=title, sections=sections,
                   length_check=length.measure(sections, column.target_minutes, section_budgets=budgets,
                                               voice=column.voice_id),
                   meta=meta)


def _clients(llm_config, review_llm_config, cancel_token):
    from core.llm import LLMClient

    client = LLMClient(llm_config, cancel_token=cancel_token)
    auditor = LLMClient(review_llm_config, cancel_token=cancel_token) if review_llm_config else client
    return client, auditor


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
    """(script, fact sheet): all five steps in one go.

    Pass ``sheet`` to reuse the one made for this report; ``on_sheet`` gets a
    newly made sheet at once, so a later step failing does not waste it.
    ``review_llm_config`` runs the fact audit on another model.
    """
    client, auditor = _clients(llm_config, review_llm_config, cancel_token)
    stage = on_stage or (lambda _name: None)
    plan, problems, sheet = _plan(client, report=report, citation_check=citation_check, topic=topic, column=column,
                                  style=style, sheet=sheet, on_sheet=on_sheet, stage=stage)
    return _write(client, auditor, report=report, plan=plan, sheet=sheet, column=column, style=style,
                  problems=problems, stage=stage), sheet


@dataclass
class Planned:
    """Steps ① and ② done: the plan waits for the user before the draft is written."""
    plan: dict
    problems: list[str]
    usage: dict


def make_plan(
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
    note: str = "",
    previous: Optional[dict] = None,
) -> tuple[Planned, dict]:
    """(plan, fact sheet), the first half of ``adapt``. ``note`` is the user's word on ``previous`` plan, for a redo."""
    client, _ = _clients(llm_config, None, cancel_token)
    plan, problems, sheet = _plan(client, report=report, citation_check=citation_check, topic=topic, column=column,
                                  style=style, sheet=sheet, on_sheet=on_sheet, stage=on_stage or (lambda _name: None),
                                  note=note, previous=previous)
    return Planned(plan=plan, problems=problems, usage=dict(client.usage)), sheet


def write_from_plan(
    *,
    report: str,
    planned: Planned,
    sheet: dict,
    column,
    style,
    llm_config,
    review_llm_config=None,
    on_stage: Optional[Callable[[str], None]] = None,
    cancel_token: Optional[threading.Event] = None,
) -> Adapted:
    """The second half of ``adapt``: the same draft, audits and fixes, from a plan made (and maybe edited) earlier.

    The version's usage adds up both halves."""
    client, auditor = _clients(llm_config, review_llm_config, cancel_token)
    return _write(client, auditor, report=report, plan=planned.plan, sheet=sheet, column=column, style=style,
                  problems=planned.problems, stage=on_stage or (lambda _name: None), usage_before=planned.usage)
