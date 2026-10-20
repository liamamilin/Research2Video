"""The one place that starts a script's steps, for the user and for the unattended pipeline alike.

A script's steps (core/video/steps.py) are research → write → audit → approve → render. Everything that starts one —
creating a script, the user's buttons, a batch's tick — goes through here, so each start is logged the same way and
"what comes next" is decided in one function:

* ``create``: a new script; its research starts at once (the user asked for it now).
* ``rerun``: the user runs one step again — resume a failed research, rewrite from the same report, rerun the audit,
  render the approved version. A rerun never re-researches a finished report (that costs searches; start a new
  script for that).
* ``advance_auto``: what a script made by a batch does next. The batch (web/services/video_autopilot.py) keeps only
  its own bookkeeping — which topics, how many at once, its items' notes.

Scripts the user made wait for the user at "approve" and "render"; only batch scripts go on by themselves.
See spec/video-steps.md.
"""

from __future__ import annotations

import logging
from typing import Optional

from core.video import columns as video_columns
from core.video import credibility, steps, store
from web.services import video_render, video_workflow
from web.settings import get_settings

logger = logging.getLogger("ai_research.web.video_executor")

#: A stage that fails in a batch is retried this many times, then the item is held for the user.
MAX_RETRIES = 2
#: A render AutoVideo failed is submitted again this many times (a render is 15-30 minutes of its time).
RENDER_RETRIES = 1


class StepError(RuntimeError):
    """This step cannot run now; the message says why."""


def column_of(script: dict):
    return video_columns.get(get_settings().paths.config_dir, script["column_key"])


def steps_of(script: dict) -> list[dict]:
    """The script's steps as its records show them now."""
    version = store.get_version(script["id"], script["current_version"]) if script.get("current_version") else None
    try:
        mode = column_of(script).research_mode
    except Exception:  # noqa: BLE001 - a recipe gone or broken: the steps still read
        mode = "job"
    return steps.derive(script, version, store.list_renders(script["id"]), research_mode=mode)


def create(column_key: str, topic: dict, started_by: str) -> dict:
    """A new script, researching (or writing, from a given report) right away."""
    script = video_workflow.start(column_key, topic, started_by=started_by)
    first = "write" if topic.get("from_report") else "research"
    steps.log(script["id"], first, "start", "用已有的研究报告" if first == "write" else "", by=started_by)
    return script


# --- the user's reruns -----------------------------------------------------

RERUNNABLE = ("research", "write", "audit", "render")


def rerun(script_id: str, step: str, by: str) -> dict:
    """Run one step of a script again, by hand. Raises StepError when it cannot run now."""
    script = store.get_script(script_id)
    if script is None:
        raise StepError("没有这篇文稿")
    if step not in RERUNNABLE:
        raise StepError(f"这一步不能重跑：{step}（定稿在文稿页点「定稿」）")
    if script["status"] in ("RESEARCHING", "WRITING") or (script["status"] == "DRAFT" and script.get("stage")):
        raise StepError("文稿正在调研或写稿，等它完成")
    if script["status"] == "PLAN_READY":
        raise StepError("方案在等你确认：先确认、带意见重做或放弃方案")
    if step == "research":
        return _resume_research(script, by)
    if step == "write":
        return _rewrite(script, by)
    if step == "audit":
        return _rerun_audit(script, by)
    return _render(script, by)


def _resume_research(script: dict, by: str) -> dict:
    current = {s["step"]: s for s in steps_of(script)}["research"]
    if current["state"] != "FAILED":
        raise StepError("调研没有失败，不用重跑；要换一份调研就新建一篇文稿（会花搜索额度）")
    store.update_script(script["id"], status="RESEARCHING", error=None)
    video_workflow.spawn(script["id"], by)
    steps.log(script["id"], "research", "resume", "从断点继续：Job 已跑完的报告会直接用", by=by)
    return store.get_script(script["id"])


def _rewrite(script: dict, by: str) -> dict:
    if not script.get("report_path"):
        raise StepError("还没有研究报告，先完成调研")
    try:
        video_workflow.rewrite(script["id"], started_by=by)
    except video_workflow.RestyleError as exc:
        raise StepError(str(exc)) from exc
    steps.log(script["id"], "write", "rerun", "用同一份研究报告重写，存成新版本", by=by)
    return store.get_script(script["id"])


def _rerun_audit(script: dict, by: str) -> dict:
    if script["status"] == "APPROVED":
        # the audit resumes as a draft: an approved (maybe rendered, maybe published) script would lose its approval
        raise StepError("已经定稿了：重跑审计会把它退回草稿。要重审就先重写，或改稿后再定稿")
    if not script.get("current_version") or script["status"] != "DRAFT":
        raise StepError("还没有草稿，不能审计")
    if column_of(script).writer == "adapt":
        raise StepError("五步改编的审校是写稿的一部分，要重来就重写")
    store.update_script(script["id"], status="DRAFT", stage="事实审计", error=None)
    video_workflow.spawn(script["id"], by)  # a script with a saved draft resumes at its audits
    steps.log(script["id"], "audit", "rerun", "重跑事实和风格审计（不改稿子）", by=by)
    return store.get_script(script["id"])


def _render(script: dict, by: str, voice_id: Optional[str] = None) -> dict:
    if script["status"] != "APPROVED":
        raise StepError("请先定稿再出片")
    try:
        render = video_render.create(script, voice_id=voice_id)
    except video_render.RenderError as exc:
        raise StepError(str(exc)) from exc
    video_render.start(render["id"])
    steps.log(script["id"], "render", "start", f"出片 v{script['approved_version']}", by=by)
    return render


# --- a plan waiting for the user (writing.pause_after_plan) -------------------

def confirm_plan(script_id: str, by: str, edits: Optional[dict] = None) -> dict:
    """The user confirms the waiting plan, maybe reworded; the draft is written from it."""
    try:
        script = video_workflow.confirm_plan(script_id, edits=edits, started_by=by)
    except video_workflow.RestyleError as exc:
        raise StepError(str(exc)) from exc
    changed = script.pop("edits")
    steps.log(script_id, "write", "confirm", "确认方案" + (f"，改了 {'、'.join(changed)}" if changed else "，没有改动")
              + "；按方案写稿", by=by)
    return script


def redo_plan(script_id: str, by: str, note: str) -> dict:
    try:
        script = video_workflow.redo_plan(script_id, note=note, started_by=by)
    except video_workflow.RestyleError as exc:
        raise StepError(str(exc)) from exc
    steps.log(script_id, "write", "redo_plan", f"带意见重做方案：{note.strip()[:200]}", by=by)
    return script


def discard_plan(script_id: str, by: str) -> dict:
    try:
        script = video_workflow.discard_plan(script_id, started_by=by)
    except video_workflow.RestyleError as exc:
        raise StepError(str(exc)) from exc
    steps.log(script_id, "write", "discard_plan", "放弃方案", by=by)
    return script


def render(script_id: str, by: str, voice_id: Optional[str] = None) -> dict:
    """The user's 出片 button: the approved version to AutoVideo. Returns the render."""
    script = store.get_script(script_id)
    if script is None:
        raise StepError("没有这篇文稿")
    return _render(script, by, voice_id)


def approve(script_id: str, version: int, by: str) -> None:
    store.update_script(script_id, status="APPROVED", approved_version=version)
    steps.log(script_id, "approve", "approve", f"v{version}", by=by)


# --- a batch's script --------------------------------------------------------

def judge(script: dict, column) -> dict:
    """The credibility label of the current version, and what it let through. It never stops the pipeline."""
    version = store.get_version(script["id"], script["current_version"])
    label = credibility.label(version)
    checks = video_workflow.rule_checks(version, column)
    summary = checks.get("summary") or {}
    return {"label": label["tier"], "text": label["text"], "reasons": label["reasons"],
            "cited_share": label["cited_share"], "audited": bool(checks.get("model_ran")),
            "ignored": {"must": max(0, int(summary.get("must", 0)) - int(summary.get("rule_must", 0))),
                        "warn": int(summary.get("warn", 0))},
            "version": version["version"]}


def advance_auto(script: dict, retries: dict, *, can_start_script: bool, can_start_render: bool) -> dict:
    """Do the next missing thing for a batch's script. Nothing here waits for the user.

    Returns ``{"took": {"script", "render"}, "bump": retry key or None, "hold": note or None, "item": fields}``:
    what newly started (for the batch's limits), and what the batch should note on its item."""
    out = {"took": {"script": False, "render": False}, "bump": None, "hold": None, "item": {}}
    status = script["status"]
    column = column_of(script)
    tries = lambda key: int((retries or {}).get(key, 0))  # noqa: E731
    if status in ("RESEARCHING", "WRITING"):
        return out
    if status == "PLAN_READY":  # a batch's write never pauses; this one was paused by hand
        out["hold"] = "方案在等你确认"
        return out
    if status == "FAILED":
        if tries("script") >= MAX_RETRIES:
            out["hold"] = f"写稿失败 {MAX_RETRIES + 1} 次：{script.get('error')}"
            return out
        if not can_start_script:
            return out
        failed_step = "research" if not script.get("report_path") else "write"
        store.update_script(script["id"], status="RESEARCHING", error=None)
        video_workflow.spawn(script["id"], "autopilot")
        steps.log(script["id"], failed_step, "resume", f"第 {tries('script') + 1} 次重试", by="autopilot")
        out.update(bump="script", item={"note": "写稿失败，从断点继续"})
        out["took"]["script"] = True
        return out
    if status == "DRAFT":
        if script.get("stage"):  # audits running
            return out
        verdict = judge(script, column)
        if not verdict["audited"] and tries("audit") < MAX_RETRIES and can_start_script:
            video_workflow.spawn(script["id"], "autopilot")  # current_version set → only the audits rerun
            steps.log(script["id"], "audit", "rerun", f"审计没有跑完，第 {tries('audit') + 1} 次重跑", by="autopilot")
            out.update(bump="audit", item={"note": "审计没有跑完，重跑审计"})
            out["took"]["script"] = True
            return out
        if not verdict["audited"] and tries("audit") < MAX_RETRIES:
            return out  # wait for a free slot to rerun the audit
        # whatever the label, the video is made: it is shown next to the video, and the user decides what to publish
        store.update_script(script["id"], status="APPROVED", approved_version=verdict["version"])
        steps.log(script["id"], "approve", "approve", f"自动定稿 v{verdict['version']}（{verdict['text']}）", by="autopilot")
        out["item"] = {"gate": {**verdict, "approved": "auto"}, "note": f"已自动定稿（{verdict['text']}），准备出片"}
        return out
    if status == "APPROVED":
        return _render_auto(script, can_start_render, out)
    return out


def _render_auto(script: dict, can_start_render: bool, out: dict) -> dict:
    renders = store.list_renders(script["id"])
    current = [r for r in renders if r["version"] == script["approved_version"]]
    active = [r for r in current if r["status"] in store.ACTIVE_RENDER_STATUSES]
    done = [r for r in current if r["status"] == "COMPLETED"]
    if done:
        out["item"] = {"state": "DONE", "render_id": done[0]["id"], "note": "成片完成，等你看片和发布"}
        return out
    if active:
        out["item"] = {"render_id": active[0]["id"], "note": f"出片中（{active[0]['status']}）"}
        video_render.start(active[0]["id"])  # no-op when this process already follows it
        return out
    failed = [r for r in current if r["status"] in ("FAILED", "CANCELLED")]
    if len(failed) > RENDER_RETRIES:
        out["hold"] = f"出片失败 {len(failed)} 次：{failed[0].get('error_message')}"
        return out
    if not can_start_render:
        return out
    try:
        render = video_render.create(script)
    except video_render.RenderError as exc:
        out["hold"] = f"不能出片：{exc}"
        return out
    video_render.start(render["id"])
    steps.log(script["id"], "render", "start", f"出片 v{script['approved_version']}"
              + (f"（第 {len(failed) + 1} 次）" if failed else ""), by="autopilot")
    out["item"] = {"render_id": render["id"], "note": "已提交出片"}
    out["took"]["render"] = True
    return out
