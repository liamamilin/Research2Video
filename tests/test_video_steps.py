"""A script's steps and the one executor that starts them (spec/video-steps.md)."""

from __future__ import annotations

import pytest

from core.video import length, steps, store
from core.video import columns as video_columns
from tests.test_video_reports import _flash
from tests.test_video_scripts import (GOOD, ROOT, _approved_script, _csrf, _login, _wait, autovideo, llm,  # noqa: F401
                                      research, script_json, video_env)
from web.services import video_executor, video_workflow


def states(script_id: str) -> dict:
    return {s["step"]: s["state"] for s in video_executor.steps_of(store.get_script(script_id))}


def test_the_steps_read_off_the_records_through_a_scripts_life(video_env):
    script = store.create_script("flash", {"id": "2026-10-19/F1", "title": "一条快讯"})
    assert states(script["id"]) == {"research": "RUNNING", "write": "PENDING", "audit": "PENDING",
                                    "approve": "PENDING", "render": "PENDING"}  # created RESEARCHING
    store.update_script(script["id"], status="FAILED", error="研究没有完成")
    assert states(script["id"])["research"] == "FAILED"
    store.update_script(script["id"], status="WRITING", report_path="output/video/r.md", error=None)
    assert states(script["id"])["research"] == "DONE" and states(script["id"])["write"] == "RUNNING"
    sections = [{"title": "开场", "paragraphs": [{"text": "一句话。", "citations": [], "evidence": "UNCITED"}]}]
    store.add_version(script["id"], title="t", sections=sections, length_check=length.measure(sections, 1.25),
                      edit_type="GENERATED", meta={"checks": {"model_ran": False}})
    store.update_script(script["id"], status="DRAFT", stage="事实审计")
    assert states(script["id"])["write"] == "DONE" and states(script["id"])["audit"] == "RUNNING"
    store.update_script(script["id"], stage=None)
    assert states(script["id"])["audit"] == "INCOMPLETE" and states(script["id"])["approve"] == "WAITING"
    store.update_script(script["id"], status="APPROVED", approved_version=1)
    assert states(script["id"])["approve"] == "DONE" and states(script["id"])["render"] == "WAITING"
    store.add_version(script["id"], title="t", sections=sections, length_check=length.measure(sections, 1.25),
                      edit_type="EDITED", meta={"checks": {"model_ran": False, "model_checks_stale": True}})
    audit = video_executor.steps_of(store.get_script(script["id"]))[2]
    assert audit["state"] == "INCOMPLETE" and "改过稿子" in audit["note"]

    cut = store.create_script("point_short", {"title": "切一点", "from_report": "output/video/r.md"})
    assert states(cut["id"])["research"] == "SKIPPED"


def test_a_render_step_follows_the_approved_versions_renders(video_env, autovideo):
    from web.services import video_render

    script = _approved_script()
    render = video_render.create(script)
    assert states(script["id"])["render"] == "RUNNING"
    store.update_render(render["id"], status="FAILED", error_message="分镜失败")
    assert states(script["id"])["render"] == "FAILED"
    again = video_render.create(store.get_script(script["id"]))
    store.update_render(again["id"], status="COMPLETED")
    assert states(script["id"])["render"] == "DONE"


def test_api_rewrite_from_the_same_report_makes_a_new_version_without_researching(client, video_env, research, llm,
                                                                                  autovideo):
    _flash(video_env)
    budget = length.budget_for(video_columns.get(str(ROOT / "config"), "flash").target_minutes, "voxcpm_anchor")
    llm.replies = [script_json(budget - 5, citations={(0, 0): [GOOD]}), script_json(budget - 5, text_seed="写")]
    _login(client)
    made = client.post("/api/video/scripts", json={"column": "flash", "topic_id": "2026-10-19/F1"}, headers=_csrf(client))
    first = _wait(client, made.json()["id"], {"DRAFT", "FAILED"})
    assert first["status"] == "DRAFT", first.get("error")
    assert [s["state"] for s in first["steps"]][:2] == ["DONE", "DONE"]

    rerun = client.post(f"/api/video/scripts/{first['id']}/steps/write/rerun", headers=_csrf(client))
    assert rerun.status_code == 202, rerun.text
    second = _wait(client, first["id"], {"DRAFT", "FAILED"})
    assert second["status"] == "DRAFT" and second["current_version"] == 2, second.get("error")
    assert len(research.jobs) == 1 and second["report_path"] == first["report_path"]
    assert [(e["step"], e["event"], e["by"]) for e in second["events"]] == [
        ("research", "start", "editor"), ("write", "rerun", "editor")]

    # a research that finished is not redone from here; an approve is not a rerun
    assert client.post(f"/api/video/scripts/{first['id']}/steps/research/rerun", headers=_csrf(client)).status_code == 409
    assert client.post(f"/api/video/scripts/{first['id']}/steps/approve/rerun", headers=_csrf(client)).status_code == 409
    assert client.post(f"/api/video/scripts/{first['id']}/steps/render/rerun", headers=_csrf(client)).status_code == 409
    client.post(f"/api/video/scripts/{first['id']}/approve", headers=_csrf(client), json={})
    assert client.get(f"/api/video/scripts/{first['id']}").json()["events"][-1]["step"] == "approve"

    _login(client, "viewer", "viewer-pass-123")
    assert client.post(f"/api/video/scripts/{first['id']}/steps/write/rerun", headers=_csrf(client)).status_code == 403


def test_a_failed_rewrite_keeps_the_draft_and_its_versions(video_env, llm, monkeypatch):
    (video_env / "output").mkdir(exist_ok=True)
    (video_env / "output" / "r.md").write_text("# 报告\n内容", encoding="utf-8")
    script = store.create_script("flash", {"id": None, "title": "一条"})
    sections = [{"title": "开场", "paragraphs": [{"text": "一句话。", "citations": [], "evidence": "UNCITED"}]}]
    store.add_version(script["id"], title="t", sections=sections, length_check=length.measure(sections, 1.25),
                      edit_type="GENERATED")
    store.update_script(script["id"], status="DRAFT", report_path="output/r.md")
    llm.replies = ["这不是 JSON"] * 5
    video_workflow.run_rewrite(script["id"], started_by="editor")
    after = store.get_script(script["id"])
    assert after["status"] == "DRAFT" and after["current_version"] == 1 and "写稿失败" in (after["error"] or "")

    # an approved script whose rewrite fails stays approved, on the same version (it may be rendered, published)
    store.update_script(script["id"], status="APPROVED", approved_version=1, error=None)
    llm.replies = ["这不是 JSON"] * 5
    video_workflow.run_rewrite(script["id"], started_by="editor", previous_status="APPROVED")
    after = store.get_script(script["id"])
    assert after["status"] == "APPROVED" and after["approved_version"] == 1 and after["current_version"] == 1


@pytest.mark.parametrize("status, report, step, ok", [
    ("FAILED", "", "research", True),          # resume: the job's finished report is reused if there is one
    ("DRAFT", "output/r.md", "research", False),
    ("DRAFT", "output/r.md", "audit", True),   # a one-call script: only the audits run again
    ("RESEARCHING", "", "write", False),       # busy
    ("APPROVED", "output/r.md", "audit", False),  # it would come back a draft and lose its approval
])
def test_which_reruns_are_allowed(video_env, monkeypatch, status, report, step, ok):
    spawned: list[str] = []
    monkeypatch.setattr(video_workflow, "spawn", lambda script_id, started_by="": spawned.append(script_id))
    script = store.create_script("flash", {"id": None, "title": "一条"})
    if report or status == "APPROVED":
        sections = [{"title": "开场", "paragraphs": [{"text": "一句话。", "citations": [], "evidence": "UNCITED"}]}]
        store.add_version(script["id"], title="t", sections=sections, length_check=length.measure(sections, 1.25),
                          edit_type="GENERATED")
    store.update_script(script["id"], status=status, report_path=report,
                        approved_version=1 if status == "APPROVED" else None)
    if ok:
        video_executor.rerun(script["id"], step, "editor")
        assert spawned == [script["id"]] and steps.events(script["id"])[-1]["step"] == step
    else:
        with pytest.raises(video_executor.StepError):
            video_executor.rerun(script["id"], step, "editor")
        assert spawned == []


def test_an_adapted_script_has_no_separate_audit_to_rerun(video_env, monkeypatch):
    monkeypatch.setattr(video_workflow, "spawn", lambda *a, **k: None)
    script = store.create_script("deep_dive", {"id": None, "title": "深度"})
    monkeypatch.setattr(video_executor, "column_of", lambda s: type("C", (), {"writer": "adapt"})())
    sections = [{"title": "开场", "paragraphs": [{"text": "一句话。", "citations": [], "evidence": "UNCITED"}]}]
    store.add_version(script["id"], title="t", sections=sections, length_check=length.measure(sections, 6),
                      edit_type="GENERATED")
    store.update_script(script["id"], status="DRAFT", report_path="output/r.md")
    with pytest.raises(video_executor.StepError, match="重写"):
        video_executor.rerun(script["id"], "audit", "editor")


def test_a_batch_scripts_steps_are_logged_as_autopilot(video_env, monkeypatch):
    from core.video import autopilot_store as ap
    from web.services import video_autopilot as pilot

    monkeypatch.setattr(video_workflow, "spawn", lambda *a, **k: None)
    monkeypatch.setattr(pilot, "fresh_topics", lambda spec, run: True)
    monkeypatch.setattr(pilot, "pick_topics", lambda spec: [{"column_key": "flash", "topic": {"id": "x/F1", "title": "一条快讯"}}])
    run = pilot.start_run({"picks": [{"column": "flash", "count": 1, "source": "flash"}], "refresh": False})
    pilot.tick()
    script_id = ap.get_run(run["id"])["items"][0]["script_id"]
    store.update_script(script_id, status="FAILED", error="中断")
    pilot.tick()
    assert [(e["step"], e["event"], e["by"]) for e in steps.events(script_id)] == [
        ("research", "start", "autopilot"), ("research", "resume", "autopilot")]
