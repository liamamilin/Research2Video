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
from core.video.audience_copy import submission_problem
from core.video.policy import is_core_issue
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
    first = "write" if topic.get("from_report") or topic.get("article") else "research"
    steps.log(script["id"], first, "start", "使用手动导入的文章" if topic.get("article") else
              "用已有的研究报告" if first == "write" else "", by=started_by)
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
    if not script.get("report_path") and not script["topic"].get("article"):
        raise StepError("还没有研究报告，先完成调研")
    try:
        video_workflow.rewrite(script["id"], started_by=by)
    except video_workflow.RestyleError as exc:
        raise StepError(str(exc)) from exc
    steps.log(script["id"], "write", "rerun", "用同一篇文章生成新版本" if script["topic"].get("article") else
              "用同一份研究报告重写，存成新版本", by=by)
    return store.get_script(script["id"])


def _rerun_audit(script: dict, by: str) -> dict:
    if script["status"] == "APPROVED":
        # the audit resumes as a draft: an approved (maybe rendered, maybe published) script would lose its approval
        raise StepError("已经定稿了：重跑审计会把它退回草稿。要重审就先重写，或改稿后再定稿")
    if not script.get("current_version") or script["status"] != "DRAFT":
        raise StepError("还没有草稿，不能审计")
    if column_of(script).writer == "adapt" and not script["topic"].get("article"):
        raise StepError("五步改编的审校是写稿的一部分，要重来就重写")
    store.update_script(script["id"], status="DRAFT", stage="事实审计", error=None)
    video_workflow.spawn(script["id"], by)  # a script with a saved draft resumes at its audits
    steps.log(script["id"], "audit", "rerun", "重新对照文章核对当前版本" if script["topic"].get("article") else
              "重跑事实、风格和新观众审计，必要时自动改写复审", by=by)
    return store.get_script(script["id"])


def _render(script: dict, by: str, voice_id: Optional[str] = None, production_mode: Optional[str] = None) -> dict:
    if script["status"] != "APPROVED":
        raise StepError("请先定稿再出片")
    try:
        render = video_render.create(script, voice_id=voice_id, recover_download=True, **({"production_mode": production_mode} if production_mode is not None else {}))
    except video_render.RenderError as exc:
        raise StepError(str(exc)) from exc
    video_render.start(render["id"])
    resumed = render["status"] == "DOWNLOADING"
    steps.log(script["id"], "render", "resume" if resumed else "start",
              "继续复制已生成的成片" if resumed else f"出片 v{script['approved_version']}", by=by)
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


def render(script_id: str, by: str, voice_id: Optional[str] = None, production_mode: Optional[str] = None) -> dict:
    """The user's 出片 button: the approved version to AutoVideo. Returns the render."""
    script = store.get_script(script_id)
    if script is None:
        raise StepError("没有这篇文稿")
    return _render(script, by, voice_id, **({"production_mode": production_mode} if production_mode is not None else {}))


def approve(script_id: str, version: int, by: str) -> None:
    found = store.get_version(script_id, version)
    if found is None:
        raise StepError("没有这个文稿版本")
    from core.video.article_writer import readiness
    problem = readiness(found)
    if problem:
        raise StepError(problem)
    if (found.get("meta") or {}).get("article"):
        from core.video.articles import ArticleError
        from web.services.video_articles import source_of
        try:
            source_of(store.get_script(script_id))
        except ArticleError as exc:
            raise StepError(str(exc)) from exc
    problem = submission_problem(found)
    if problem:
        raise StepError(problem)
    store.update_script(script_id, status="APPROVED", approved_version=version)
    steps.log(script_id, "approve", "approve", f"v{version}", by=by)


# --- a batch's script --------------------------------------------------------

#: Credibility tiers (core/video/credibility.py) that keep a batch's script from being rendered unseen.
HOLD_TIERS = ("serious", "verify", "unaudited")


def judge(script: dict, column) -> dict:
    """The credibility label of the current version, and what it let through (``advance_auto`` holds the tiers in
    HOLD_TIERS)."""
    version = store.get_version(script["id"], script["current_version"])
    label = credibility.label(version)
    checks = video_workflow.rule_checks(version, column)
    summary = checks.get("summary") or {}
    core = [i for i in checks.get("issues", []) if is_core_issue(i)]
    return {"label": label["tier"], "text": label["text"],
            "reasons": label["reasons"] + ([] if label["tier"] in HOLD_TIERS else [i["problem"] for i in core]),
            "core_issues": len(core),
            "cited_share": label["cited_share"],
            "audited": bool(checks.get("model_ran")),
            "ignored": {"must": max(0, int(summary.get("must", 0)) - int(summary.get("rule_must", 0))),
                        "warn": int(summary.get("warn", 0))},
            "version": version["version"]}


def advance_auto(script: dict, retries: dict, *, can_start_script: bool, can_start_render: bool) -> dict:
    """Do the next missing thing for a batch's script. Nothing here waits for the user, but a draft in HOLD_TIERS.

    Returns ``{"took": {"script", "render"}, "bump": retry key or None, "hold": note or None, "item": fields}``:
    what newly started (for the batch's limits), and what the batch should note on its item."""
    out = {"took": {"script": False, "render": False}, "bump": None, "hold": None, "item": {}}
    status = script["status"]
    column = column_of(script)
    tries = lambda key: int((retries or {}).get(key, 0))  # noqa: E731
    if status in ("RESEARCHING", "WRITING"):
        return out
    if status in ("DRAFT", "APPROVED") and script.get("current_version"):
        from core.video import content_guard

        # Older daily/weekly drafts also pass this gate before spending another render attempt.
        paths = get_settings().paths
        guarded = content_guard.with_policy(script, paths.jobs_dir)
        if (not content_guard.protected(script["id"])
                and content_guard.writing_peer(guarded["topic"], paths.config_dir, paths.jobs_dir, script_id=script["id"])):
            out["item"] = {"note": "等待同矩阵更早的文稿完成，再检查主选题重复"}
            return out
        version = store.get_version(script["id"], script["current_version"])
        try:
            content_guard.check(guarded, version["sections"], paths.config_dir, paths.jobs_dir)
        except content_guard.DuplicateContent as exc:
            if content_guard.archive_duplicate(script["id"], str(exc), [p["script_id"] for p in exc.peers]):
                out["item"] = {"state": "CANCELLED", "note": str(exc)}
            else:
                out["hold"] = str(exc)
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
        needs_audit = verdict["label"] != "generated" and (not verdict["audited"] or verdict["label"] == "verify")
        if needs_audit and tries("audit") < MAX_RETRIES and can_start_script:
            reason = "仍有待核实段落" if verdict["audited"] else "审计没有跑完"
            video_workflow.spawn(script["id"], "autopilot")  # current_version set → only the audits rerun
            steps.log(script["id"], "audit", "rerun", f"{reason}，第 {tries('audit') + 1} 次重跑", by="autopilot")
            out.update(bump="audit", item={"note": f"{reason}，重跑审计"})
            out["took"]["script"] = True
            return out
        if needs_audit and tries("audit") < MAX_RETRIES:
            return out  # wait for a free slot to rerun the audit
        if verdict["label"] in HOLD_TIERS or verdict["core_issues"]:
            # Automatic repair/retries are exhausted. Keep the concrete verdict for review; explicit approval
            # by the user can still send it on (reopen_approved).
            out["hold"] = f"文稿没过审（{verdict['text']}），没有出片：" + "；".join(verdict["reasons"][:3]) + "。看过文稿、确认后再出片"
            out["item"] = {"gate": {**verdict, "approved": False}}
            return out
        # any other label: the video is made, the label is shown next to it, and the user decides what to publish
        store.update_script(script["id"], status="APPROVED", approved_version=verdict["version"])
        steps.log(script["id"], "approve", "approve", f"自动定稿 v{verdict['version']}（{verdict['text']}）", by="autopilot")
        out["item"] = {"gate": {**verdict, "approved": "auto"}, "note": f"已自动定稿（{verdict['text']}），准备出片"}
        return out
    if status == "APPROVED":
        return _render_auto(script, can_start_render, out, retries)
    return out


def _render_auto(script: dict, can_start_render: bool, out: dict, retries: Optional[dict] = None) -> dict:
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
    # failures before ``renders_forgiven_before`` (set by video_autopilot.retry_held_renders, once what broke them is
    # fixed: an account out of balance, a key) do not count against the script
    forgiven = str((retries or {}).get("renders_forgiven_before") or "")
    failed = [r for r in current if r["status"] in ("FAILED", "CANCELLED") and r["created_at"] > forgiven]
    latest = current[0] if current else None
    if latest and latest["status"] == "FAILED" and latest.get("error_code") in {"SUBMIT_UNCONFIRMED", "DOWNLOAD_FAILED"}:
        out["hold"] = ("提交结果尚未确认，请先在 AutoVideo 作品库核对任务" if latest["error_code"] == "SUBMIT_UNCONFIRMED"
                       else "成片已生成但复制失败，点击重跑可继续复制，无需重新生成")
        return out
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
