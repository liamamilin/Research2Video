"""Pausing the five-step adaptation after its plan (writing.pause_after_plan, spec/video-steps.md).

The split must not change what is written: a plan confirmed unchanged gives the very requests ``adapt`` sends in one
go, after the plan has been through the database. A batch never pauses."""

from __future__ import annotations

import copy
import time

import pytest

from core.video import columns as video_columns
from core.video import plans, recipes, steps, store
from core.video.adapt import facts, pipeline
from tests import golden_video_cells as golden
from tests.test_video_adapt import (CITATION_CHECK, REPORT, StepLLM, _login, _script_with_a_legacy_version,  # noqa: F401
                                    adapt, column, llm, style, video_env)
from web.services import video_board, video_executor, video_workflow

TOPIC = {"title": "缓存前缀纪律", "fields": {}}


@pytest.fixture
def pausing(video_env):
    """The shipped recipes, with the adapting ones set to pause after the plan."""
    golden.install_recipes(video_env / "config", replace=(("  pipeline: adapt\n",
                                                             "  pipeline: adapt\n  pause_after_plan: true\n"),))
    assert video_columns.get(str(video_env / "config"), "deep_dive").pause_after_plan
    return video_env


def _requests() -> list[tuple[str, list[dict]]]:
    return [(step, messages) for step, messages, _ in StepLLM.calls]


def _paused_then_written(*, cached_sheet: bool, edits: dict | None = None):
    """make_plan → stored plan (and fact sheet) → reloaded → write_from_plan, as the paused workflow does it."""
    sheet_id = facts.sheet_id(REPORT)
    cached = store.get_fact_sheet(sheet_id) if cached_sheet else None
    planned, _ = pipeline.make_plan(report=REPORT, citation_check=CITATION_CHECK, topic=TOPIC, column=column(),
                                    style=style(), llm_config=None, sheet=cached["sheet"] if cached else None,
                                    on_sheet=lambda s: store.save_fact_sheet(sheet_id, report_path="r.md", sheet=s))
    script = store.create_script("deep_dive", dict(TOPIC))
    row = plans.save(script["id"], plan=planned.plan, problems=planned.problems, usage=planned.usage,
                     sheet_id=sheet_id, style={"depth": "intermediate", "tone": "explainer"}, note="",
                     previous_status="FAILED", created_by="editor")
    row = plans.get(row["id"])
    changed = plans.apply_edits(row["plan"], edits or {})
    assert plans.confirm(row["id"], plan=row["plan"], edits=changed, by="editor")
    row = plans.get(row["id"])
    adapted = pipeline.write_from_plan(report=REPORT, sheet=store.get_fact_sheet(sheet_id)["sheet"], column=column(),
                                       style=style(), llm_config=None,
                                       planned=pipeline.Planned(row["plan"], row["problems"], row["usage"]))
    return adapted, changed


@pytest.mark.parametrize("cached_sheet", [False, True])
def test_a_plan_confirmed_unchanged_sends_the_same_requests_as_adapt(video_env, llm, cached_sheet):
    if cached_sheet:
        store.save_fact_sheet(facts.sheet_id(REPORT), report_path="r.md", sheet=adapt()[1])
    StepLLM.calls = []
    whole, _ = adapt(sheet=store.get_fact_sheet(facts.sheet_id(REPORT))["sheet"] if cached_sheet else None)
    one_go = _requests()

    StepLLM.calls = []
    split, changed = _paused_then_written(cached_sheet=cached_sheet)
    assert changed == []
    assert _requests() == one_go  # byte for byte, the plan having been through the database
    assert split.sections == whole.sections and split.title == whole.title
    assert split.meta == whole.meta  # usage included: the two halves add up to the one go
    expected = ["plan", "draft"] if cached_sheet else ["facts", "plan"]
    assert [s for s, _ in one_go][:2] == expected


def test_resending_the_same_words_is_not_an_edit():
    plan = {"question": "q", "core_message": "c", "hook": {"kind": "问题", "idea": "h", "facts": []},
            "beats": [{"title": "a", "point": "p"}, {"title": "b", "point": "p2"}]}
    same = copy.deepcopy(plan)
    assert plans.apply_edits(same, {"question": "q", "core_message": "c", "hook_idea": "h",
                                    "beats": [{"title": "a", "point": "p"}, {"title": "b", "point": "p2"}]}) == []
    assert same == plan


def test_edited_words_reach_the_draft_request(video_env, llm):
    StepLLM.calls = []
    adapted, changed = _paused_then_written(cached_sheet=False, edits={
        "core_message": "改过的主旨：先稳住前缀", "hook_idea": "改过的钩子", "beats": [{"point": "改过的要点"}, {}, {}, {}]})
    assert changed == ["core_message", "hook.idea", "beats[1].point"]
    request = StepLLM.request("draft")
    assert "改过的主旨：先稳住前缀" in request and "改过的钩子" in request and "改过的要点" in request
    assert adapted.meta["plan"]["core_message"] == "改过的主旨：先稳住前缀"


def test_edits_keep_the_plans_structure():
    plan = {"question": "q", "core_message": "c", "hook": {"kind": "问题", "idea": "h", "facts": []},
            "beats": [{"title": "a", "point": "p"}, {"title": "b", "point": "p"}]}
    with pytest.raises(ValueError):
        plans.apply_edits(copy.deepcopy(plan), {"beats": [{"title": "x"}]})  # a beat dropped
    with pytest.raises(ValueError):
        plans.apply_edits(copy.deepcopy(plan), {"core_message": "  "})
    edited = copy.deepcopy(plan)
    assert plans.apply_edits(edited, {"beats": [{"title": "a2"}, {}]}) == ["beats[1].title"]
    assert list(edited) == list(plan) and edited["beats"][0] == {"title": "a2", "point": "p"}


def test_the_redo_note_is_added_only_when_given(llm):
    from core.llm import LLMClient
    from core.video.adapt import plan as planning
    from tests.test_video_adapt import sheet_of

    planning.make(LLMClient(None), sheet_of(), column(), TOPIC, style(), 6)
    planning.make(LLMClient(None), sheet_of(), column(), TOPIC, style(), 6, note="  ", previous={"x": 1})
    first, blank = StepLLM.request("plan", 0), StepLLM.request("plan", 1)
    assert first == blank
    planning.make(LLMClient(None), sheet_of(), column(), TOPIC, style(), 6, note="开场换成场景", previous={"x": 1})
    redo = StepLLM.request("plan", 2)
    assert redo.startswith(first) and "## 编辑看过的方案\n{\"x\": 1}" in redo and redo.endswith("开场换成场景")


# --- the workflow ------------------------------------------------------------------


def _new_script(created_by: str = "editor") -> dict:
    return store.create_script("deep_dive", {**TOPIC, "from_report": "output/video/c4.md"}, created_by=created_by)


def _wait(client, script_id, timeout=10.0) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        body = client.get(f"/api/video/scripts/{script_id}").json()
        if body["status"] != "WRITING" or time.monotonic() > deadline:
            return body
        time.sleep(0.05)


def _states(script_id: str) -> dict:
    return {s["step"]: s["state"] for s in video_executor.steps_of(store.get_script(script_id))}


def test_a_paused_script_waits_with_its_plan_and_is_written_once_confirmed(client, pausing, llm):
    script = _new_script()
    assert video_workflow.run(script["id"], started_by="editor") == "PLAN_READY"
    assert StepLLM.steps() == ["facts", "plan"]  # nothing written yet
    after = store.get_script(script["id"])
    assert after["current_version"] == 0 and after["stage"] is None
    assert _states(script["id"])["write"] == "WAITING" and _states(script["id"])["audit"] == "PENDING"

    csrf = _login(client)
    body = client.get(f"/api/video/scripts/{script['id']}").json()
    waiting = body["pending_plan"]
    assert waiting["plan"]["beats"] and waiting["style"] == {"depth": "intermediate", "tone": "explainer"}
    # nothing else may run while the plan waits
    assert client.post(f"/api/video/scripts/{script['id']}/steps/write/rerun", headers=csrf).status_code == 409
    assert client.post(f"/api/video/scripts/{script['id']}/approve", headers=csrf).status_code == 409
    assert {a["kind"] for a in video_board.board({})["attention"]} == {"plan"}

    StepLLM.calls = []
    made = client.post(f"/api/video/scripts/{script['id']}/plan/confirm", json={"core_message": "新的主旨"},
                       headers=csrf)
    assert made.status_code == 202, made.text
    body = _wait(client, script["id"])
    assert body["status"] == "DRAFT" and body["error"] is None and body["pending_plan"] is None
    assert StepLLM.steps() == ["draft", "newcomer", "audit"]  # the plan is not made again
    meta = body["version_shown"]["meta"]
    assert meta["plan"]["core_message"] == "新的主旨"
    assert meta["plan_confirmed"]["edits"] == ["core_message"] and meta["plan_confirmed"]["by"] == "editor"
    assert meta["usage"]["requests"] == 5  # both halves
    assert [p["state"] for p in plans.history(script["id"])] == ["USED"]
    assert plans.history(script["id"])[0]["version"] == 1
    assert [e["event"] for e in body["events"] if e["step"] == "write"] == ["confirm"]
    # a second click finds nothing waiting
    assert client.post(f"/api/video/scripts/{script['id']}/plan/confirm", headers=csrf).status_code == 409


def test_a_plan_confirms_only_once(pausing, llm):
    script = _new_script()
    video_workflow.run(script["id"], started_by="editor")
    row = plans.pending(script["id"])
    assert plans.confirm(row["id"], plan=row["plan"], edits=[], by="a")
    assert not plans.confirm(row["id"], plan=row["plan"], edits=[], by="b")


def test_redo_with_a_note_makes_a_new_plan_and_keeps_the_fact_sheet(client, pausing, llm):
    script = _new_script()
    video_workflow.run(script["id"], started_by="editor")
    first = plans.pending(script["id"])
    csrf = _login(client)
    StepLLM.calls = []
    assert client.post(f"/api/video/scripts/{script['id']}/plan/redo", json={"note": "开场换成读者的场景"},
                       headers=csrf).status_code == 202
    body = _wait(client, script["id"])
    assert body["status"] == "PLAN_READY" and body["pending_plan"]["id"] != first["id"]
    assert body["pending_plan"]["note"] == "开场换成读者的场景"
    assert StepLLM.steps() == ["plan"]  # the fact sheet is reused
    assert "开场换成读者的场景" in StepLLM.request("plan") and "## 编辑看过的方案" in StepLLM.request("plan")
    assert [p["state"] for p in plans.history(script["id"])] == ["SUPERSEDED", "PENDING"]
    assert client.post(f"/api/video/scripts/{script['id']}/plan/redo", json={"note": ""},
                       headers=csrf).status_code == 422


def test_a_failed_redo_keeps_the_old_plan(pausing, llm):
    script = _new_script()
    video_workflow.run(script["id"], started_by="editor")
    first = plans.pending(script["id"])
    llm.queues["plan"] = ["这不是 JSON"]
    assert video_workflow.run_redo_plan(script["id"], first["id"], "再短一点", started_by="editor") == "PLAN_READY"
    after = store.get_script(script["id"])
    assert after["status"] == "PLAN_READY" and "重做方案失败" in after["error"]
    assert plans.pending(script["id"])["id"] == first["id"]


def test_discarding_a_first_plan_leaves_the_script_to_be_rewritten(client, pausing, llm):
    script = _new_script()
    video_workflow.run(script["id"], started_by="editor")
    csrf = _login(client)
    body = client.post(f"/api/video/scripts/{script['id']}/plan/discard", headers=csrf).json()
    assert body["status"] == "FAILED" and "方案已放弃" in body["error"]
    assert [p["state"] for p in plans.history(script["id"])] == ["DISCARDED"]
    assert _states(script["id"])["write"] == "FAILED"
    StepLLM.calls = []
    video_workflow.run_rewrite(script["id"], started_by="editor", previous_status="FAILED")
    assert store.get_script(script["id"])["status"] == "PLAN_READY" and StepLLM.steps() == ["plan"]


def test_a_rewrite_of_an_approved_script_can_pause_and_both_ways_out_keep_the_approval(pausing, llm):
    script = _script_with_a_legacy_version(pausing)
    store.update_script(script["id"], status="APPROVED", approved_version=1, created_by="editor")
    assert video_workflow.run_rewrite(script["id"], started_by="editor", previous_status="APPROVED") == "PLAN_READY"
    assert _states(script["id"])["write"] == "WAITING"  # not DONE: it has a version, but this write waits
    assert store.get_script(script["id"])["approved_version"] == 1

    video_workflow.discard_plan(script["id"], started_by="editor")
    after = store.get_script(script["id"])
    assert after["status"] == "APPROVED" and after["approved_version"] == 1 and after["error"] is None

    # confirmed, then the draft fails: the plan waits again, the approval is untouched
    video_workflow.run_rewrite(script["id"], started_by="editor", previous_status="APPROVED")
    row = plans.pending(script["id"])
    assert plans.confirm(row["id"], plan=row["plan"], edits=[], by="editor")
    store.update_script(script["id"], status="WRITING")
    llm.queues["draft"] = ["这不是 JSON"]
    assert video_workflow.run_from_plan(script["id"], started_by="editor") == "PLAN_READY"
    after = store.get_script(script["id"])
    assert after["approved_version"] == 1 and after["current_version"] == 1 and "按方案写稿失败" in after["error"]
    assert plans.pending(script["id"])["id"] == row["id"]


def test_a_batch_script_never_pauses(pausing, llm):
    script = _new_script(created_by="autopilot")
    assert video_workflow.run(script["id"], started_by="autopilot") == "DRAFT"
    assert StepLLM.steps() == ["facts", "plan", "draft", "newcomer", "audit"]
    # nor when the user rewrites it: its batch would find nothing to do and stall
    StepLLM.calls = []
    assert video_workflow.run_rewrite(script["id"], started_by="editor") == "DRAFT"
    assert "draft" in StepLLM.steps() and plans.history(script["id"]) == []


def test_a_restart_shows_the_waiting_plan_again_at_no_cost(pausing, llm):
    script = _new_script()
    video_workflow.run(script["id"], started_by="editor")
    row = plans.pending(script["id"])
    # a redo cut off by a restart: the sweep fails the script, its plan still waits
    store.update_script(script["id"], status="WRITING", stage="定方案", heartbeat_at=0)
    store.fail_interrupted()
    assert store.get_script(script["id"])["status"] == "FAILED"
    StepLLM.calls = []
    assert video_workflow.run(script["id"], started_by="editor") == "PLAN_READY"
    assert StepLLM.calls == [] and plans.pending(script["id"])["id"] == row["id"]


def test_a_confirmed_plan_cut_off_by_a_restart_is_written_without_a_new_plan(pausing, llm):
    script = _new_script()
    video_workflow.run(script["id"], started_by="editor")
    row = plans.pending(script["id"])
    plans.confirm(row["id"], plan=row["plan"], edits=[], by="editor")
    store.update_script(script["id"], status="WRITING", heartbeat_at=0)
    store.fail_interrupted()
    StepLLM.calls = []
    assert video_workflow.run(script["id"], started_by="autopilot") == "DRAFT"  # e.g. a resume
    assert StepLLM.steps() == ["draft", "newcomer", "audit"]
    assert plans.history(script["id"])[0]["state"] == "USED"


def test_the_switch_is_off_by_default_and_only_for_the_adaptation():
    from tests.test_video_recipes import MINIMAL

    for key, col in video_columns.load(str(golden.ROOT / "config")).items():
        assert col.pause_after_plan is False, key
    with pytest.raises(video_columns.ColumnError, match="pause_after_plan"):
        recipes.validate("x", MINIMAL.replace("  pipeline: single\n", "  pipeline: single\n  pause_after_plan: true\n"))
    with pytest.raises(video_columns.ColumnError, match="pause_after_plan"):
        recipes.validate("x", MINIMAL.replace("  pipeline: single\n", "  pipeline: single\n  pause_after_plan: 1\n"))
