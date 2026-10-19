"""The unattended pipeline: picking, the approval gate, resuming at every stage, weekly plans.

Script writing and rendering are the other tests' business; here ``spawn`` and ``video_render.start`` only record
that they were asked, and the tests set the script and render rows the way those workers would.
"""

from __future__ import annotations

import time

import pytest

from core.video import autopilot_store as ap
from core.video import length, store
from core.video.adapt import review as adapt_review
from tests.test_video_scripts import (FakeAutoVideo, FakeResearch, REPORT, autovideo, llm, research,  # noqa: F401
                                      script_json, video_env)
from web.services import video_autopilot as pilot
from web.services import video_render, video_workflow

SECTIONS = [{"title": "开场", "paragraphs": [{"text": "一句话讲清楚。", "citations": [], "evidence": "UNCITED"}]}]


@pytest.fixture
def pilot_env(video_env, monkeypatch):
    spawned: list[str] = []
    started: list[str] = []
    monkeypatch.setattr(video_workflow, "spawn", lambda script_id, started_by="": spawned.append(script_id))
    monkeypatch.setattr(video_render, "start", lambda render_id, client=None: started.append(render_id))
    monkeypatch.setattr(pilot, "fresh_topics", lambda spec, run: True)
    return spawned, started


def topic(n: int) -> dict:
    return {"id": f"2026-10-19/F{n}", "title": f"选题 {n}", "fields": {}, "score": 10 - n}


def batch(monkeypatch, n=3, column="flash") -> dict:
    items = [{"column_key": column, "topic": topic(i)} for i in range(1, n + 1)]
    monkeypatch.setattr(pilot, "pick_topics", lambda spec: items)
    return pilot.start_run({"picks": [{"column": column, "count": n, "source": "flash"}], "refresh": False})


def draft(script_id: str, *, model_ran=True, serious=0, status_ok=True) -> None:
    """A finished draft the way the writer leaves it, with or without a model audit behind it."""
    reading = {"issues": []} if model_ran else None
    audit = {"issues": ([{"audit": "fact", "rule": "F1", "severity": "serious", "section": 0, "paragraph": 0,
                          "problem": "数字对不上", "why": "", "suggestion": ""}] if serious else [])} if model_ran else None
    checks = adapt_review.assemble([], reading, audit, None, SECTIONS)
    check = length.measure(SECTIONS, 1.5)
    check["status"] = "OK" if status_ok else "SHORT"
    store.add_version(script_id, title="标题", sections=SECTIONS, length_check=check, edit_type="GENERATED",
                      meta={"checks": checks})
    store.update_script(script_id, status="DRAFT", stage=None)


def item_of(run_id: str, slot: int) -> dict:
    return ap.get_run(run_id)["items"][slot]


def test_picking_takes_the_best_unwritten_topics_and_skips_taken_ones(video_env, tmp_path, monkeypatch):
    from core.video import flash as video_flash
    from tests.test_video_flash import DISCOVERY

    out = tmp_path / "output"
    (out / "flash").mkdir(parents=True)
    (out / "flash" / "2026-10-19_ai_flash_discovery.md").write_text(DISCOVERY, encoding="utf-8")
    monkeypatch.setattr(pilot, "get_settings", lambda: type("S", (), {"paths": type("P", (), {"output_dir": str(out)})})())
    first = pilot.pick_topics({"picks": [{"column": "flash", "count": 1, "source": "flash"}]})
    assert [i["topic"]["id"] for i in first] == ["2026-10-19/F1"]  # the higher score
    store.create_script("flash", first[0]["topic"])
    second = pilot.pick_topics({"picks": [{"column": "flash", "count": 5, "source": "flash"}]})
    assert [i["topic"]["id"] for i in second] == ["2026-10-19/F2"]  # F1 already has a script


def test_scripts_start_up_to_the_limit_then_the_rest_wait(pilot_env, monkeypatch):
    spawned, _ = pilot_env
    run = batch(monkeypatch, 3)
    pilot.tick()
    assert len(spawned) == pilot.MAX_SCRIPTS
    assert [i["state"] for i in ap.get_run(run["id"])["items"]] == ["WORKING", "WORKING", "PENDING"]
    pilot.tick()
    assert len(spawned) == pilot.MAX_SCRIPTS  # nothing new while both are still researching


def test_a_clean_draft_is_approved_automatically_and_rendered_once(pilot_env, monkeypatch, autovideo):
    spawned, started = pilot_env
    run = batch(monkeypatch, 1)
    pilot.tick()
    script_id = item_of(run["id"], 0)["script_id"]
    draft(script_id)
    pilot.tick()
    script = store.get_script(script_id)
    assert script["status"] == "APPROVED" and script["approved_version"] == 1
    gate = item_of(run["id"], 0)["gate"]
    assert gate["pass"] and gate["approved"] == "auto"
    pilot.tick()
    pilot.tick()  # a render in flight is followed, never submitted again
    assert len(store.list_renders(script_id)) == 1 and len(set(started)) == 1
    render = store.list_renders(script_id)[0]
    store.update_render(render["id"], status="COMPLETED")
    pilot.tick()
    assert item_of(run["id"], 0)["state"] == "DONE"
    assert ap.get_run(run["id"])["status"] == "DONE"


@pytest.mark.parametrize("kwargs, reason", [({"serious": 1}, "严重"), ({"status_ok": False}, "长度")])
def test_the_gate_holds_a_draft_with_a_serious_finding_or_the_wrong_length(pilot_env, monkeypatch, kwargs, reason):
    run = batch(monkeypatch, 1)
    pilot.tick()
    script_id = item_of(run["id"], 0)["script_id"]
    draft(script_id, **kwargs)
    pilot.tick()
    item = item_of(run["id"], 0)
    assert item["state"] == "HELD" and reason in item["note"]
    assert store.get_script(script_id)["status"] == "DRAFT" and not store.list_renders(script_id)


def test_a_draft_whose_model_audit_never_ran_gets_its_audit_rerun_then_is_held(pilot_env, monkeypatch):
    spawned, _ = pilot_env
    run = batch(monkeypatch, 1)
    pilot.tick()
    script_id = item_of(run["id"], 0)["script_id"]
    draft(script_id, model_ran=False)
    for _ in range(pilot.MAX_RETRIES):
        pilot.tick()
    assert spawned.count(script_id) == 1 + pilot.MAX_RETRIES  # the first start, then one rerun per retry
    pilot.tick()
    item = item_of(run["id"], 0)
    assert item["state"] == "HELD" and "没有真正跑完" in item["note"]
    assert store.get_script(script_id)["status"] == "DRAFT"


def test_a_failed_script_is_resumed_twice_then_held(pilot_env, monkeypatch):
    spawned, _ = pilot_env
    run = batch(monkeypatch, 1)
    pilot.tick()
    script_id = item_of(run["id"], 0)["script_id"]
    for attempt in range(pilot.MAX_RETRIES):
        store.update_script(script_id, status="FAILED", error="服务重启，研究或写稿被中断；可以重新生成。")
        pilot.tick()
        assert store.get_script(script_id)["status"] == "RESEARCHING"
        assert spawned.count(script_id) == 2 + attempt
    store.update_script(script_id, status="FAILED", error="还是失败")
    pilot.tick()
    assert item_of(run["id"], 0)["state"] == "HELD" and "还是失败" in item_of(run["id"], 0)["note"]


def test_a_restart_in_the_middle_of_every_stage_loses_nothing(pilot_env, monkeypatch):
    """The ledger holds only ids: a second 'process' that reads the same rows continues from the same place."""
    spawned, started = pilot_env
    run = batch(monkeypatch, 1)
    pilot.tick()
    script_id = item_of(run["id"], 0)["script_id"]
    # restart while researching: the sweep marks it failed, the next tick resumes it, no second script appears
    store.update_script(script_id, status="FAILED", error="服务重启，研究或写稿被中断；可以重新生成。")
    pilot.tick()
    assert len(store.list_scripts()) == 1 and item_of(run["id"], 0)["script_id"] == script_id
    draft(script_id)
    pilot.tick()  # approved
    pilot.tick()  # render submitted
    render = store.list_renders(script_id)[0]
    store.update_render(render["id"], status="RUNNING", autovideo_job_id="job-1")
    pilot.tick()  # restart while rendering: still the one render, still the same job id
    assert len(store.list_renders(script_id)) == 1
    assert store.get_render(render["id"])["autovideo_job_id"] == "job-1"


def test_two_ticks_at_once_do_not_double_every_step(pilot_env, monkeypatch):
    spawned, _ = pilot_env
    batch(monkeypatch, 1)
    from core.fileio import file_lock
    from web.settings import get_settings
    import os

    with file_lock(os.path.join(get_settings().paths.state_dir, "autopilot-tick"), timeout=0):
        assert pilot.tick() == 0  # another process holds the tick
    assert spawned == []


def test_a_weekly_plan_fires_once_per_slot(pilot_env, monkeypatch):
    plan = pilot.save_plan("每周一批", "0 6 * * 1", {"picks": [{"column": "flash", "count": 1, "source": "flash"}],
                                                   "refresh": False})
    assert plan["next_fire"] > time.time()
    assert pilot.fire_due_plans() == 0
    due = plan["next_fire"] + 60
    assert pilot.fire_due_plans(now=due) == 1
    assert pilot.fire_due_plans(now=due) == 0  # the slot moved on
    assert ap.get_plan(plan["id"])["next_fire"] > due
    assert len(ap.list_runs()) == 1
    # a slot the host slept through by more than a day does not start a stale batch
    stale = ap.get_plan(plan["id"])["next_fire"] + 3 * 86400
    assert pilot.fire_due_plans(now=stale) == 0 and len(ap.list_runs()) == 1


def test_resuming_a_script_reuses_the_research_and_the_saved_draft(video_env, research, llm, monkeypatch):
    """Cut off after research: only the writing is redone. Cut off after the draft: only the audits are."""
    script = store.create_script("flash", {"id": "2026-10-19/F1", "title": "一条快讯的选题标题", "fields": {}})
    llm.replies = [script_json(377)]
    assert video_workflow.run(script["id"]) == "DRAFT"
    assert len(research.jobs) == 1 and len(llm.calls) == 1
    first = store.get_script(script["id"])
    assert first["current_version"] == 1

    # the process died after the draft: the audit result is lost, the version keeps its rule-only checks
    store.update_script(script["id"], status="FAILED", error="服务重启，研究或写稿被中断；可以重新生成。")
    assert video_workflow.run(script["id"]) == "DRAFT"
    assert len(research.jobs) == 1  # no second research
    assert len(llm.calls) == 1  # the script was not written again
    assert store.get_script(script["id"])["current_version"] == 1 and len(store.list_versions(script["id"])) == 1

    # died after the research, before writing: the report is found again from the job's own state
    other = store.create_script("flash", {"id": "2026-10-19/F2", "title": "另一条快讯的选题标题", "fields": {}})
    from core.state import StateManager

    job = video_workflow.research_job_name("flash", other["id"])
    research(job, "test")  # runs the research once, as the first run would have
    store.update_script(other["id"], status="FAILED", error="服务重启，研究或写稿被中断；可以重新生成。")
    llm.replies = [script_json(377)]
    jobs_before = len(research.jobs)
    assert video_workflow.run(other["id"]) == "DRAFT"
    assert len(research.jobs) == jobs_before


def test_api_start_a_batch_plan_it_weekly_and_cancel(client, video_env):
    from tests.test_video_scripts import _csrf, _login

    _login(client, "viewer", "viewer-pass-123")
    assert client.get("/api/video/autopilot").status_code == 200
    assert client.post("/api/video/autopilot/runs", headers=_csrf(client)).status_code == 403
    _login(client)
    started = client.post("/api/video/autopilot/runs", headers=_csrf(client))
    assert started.status_code == 202 and started.json()["status"] == "RUNNING"
    bad = client.post("/api/video/autopilot/runs", headers=_csrf(client),
                      json={"spec": {"picks": [{"column": "nope", "count": 1}]}})
    assert bad.status_code == 422
    plan = client.post("/api/video/autopilot/plans", headers=_csrf(client),
                       json={"name": "每周一批", "expression": "0 6 * * 1"})
    assert plan.status_code == 201 and plan.json()["spec"]["picks"][0]["column"] == "flash"
    assert client.post("/api/video/autopilot/plans", headers=_csrf(client),
                       json={"name": "坏的", "expression": "不是 cron"}).status_code == 422
    overview = client.get("/api/video/autopilot").json()
    assert len(overview["plans"]) == 1 and len(overview["runs"]) == 1
    run_id = overview["runs"][0]["id"]
    assert client.post(f"/api/video/autopilot/runs/{run_id}/cancel", headers=_csrf(client)).json()["status"] == "CANCELLED"
    assert client.delete(f"/api/video/autopilot/plans/{plan.json()['id']}", headers=_csrf(client)).status_code == 200


def test_the_dispatcher_wakes_the_console_only_while_the_pipeline_has_work(video_env, monkeypatch):
    """The scheduler process runs without the web app; it reads video.db and knocks on the gateway."""
    from core import schedule_runner as runner
    from web.settings import get_settings

    state_dir = get_settings().paths.state_dir
    runtime = runner.Runtime(str(video_env), state_dir, str(video_env / "jobs"), str(video_env / "config"), str(video_env / "logs"))
    knocks: list[str] = []

    class Conn:
        def __init__(self, host, port, timeout=None): knocks.append(f"{host}:{port}")
        def request(self, method, path): knocks.append(path)
        def getresponse(self): return type("R", (), {"read": lambda self: b""})()
        def close(self): pass

    monkeypatch.setattr("http.client.HTTPConnection", Conn)
    monkeypatch.setattr(runner, "_last_poke", 0.0)
    assert runner.poke_autopilot(runtime, 1000.0) is False and knocks == []  # nothing to do
    plan = pilot.save_plan("每周一批", "0 6 * * 1", {"picks": [{"column": "flash", "count": 1, "source": "flash"}],
                                                   "refresh": False})
    assert runner.poke_autopilot(runtime, plan["next_fire"] - 10, interval=0) is False  # not due yet
    assert runner.poke_autopilot(runtime, plan["next_fire"] + 1, interval=0) is True
    assert knocks == ["127.0.0.1:8765", "/api/health"]
    assert runner.poke_autopilot(runtime, plan["next_fire"] + 2) is False  # throttled
    pilot.start_run({"picks": [{"column": "flash", "count": 1, "source": "flash"}], "refresh": False})
    assert runner.autopilot_due(state_dir, 0.0) is True  # a running batch keeps the console awake


def test_a_roundup_marks_each_of_its_items_as_written(video_env):
    store.create_script("ai-developer-roundup-long", {"id": None, "title": "盘点", "items": ["2026-10-19/F2", "2026-10-19/F3"]})
    failed = store.create_script("flash", {"id": "2026-10-19/F4", "title": "失败的", "fields": {}})
    store.update_script(failed["id"], status="FAILED")
    assert store.topic_ids_in_use() == {"2026-10-19/F2", "2026-10-19/F3"}


def test_the_style_audit_can_report_as_a_warning_for_a_roundup(llm):
    from core.video import audit as video_audit

    reply = '{"issues": [{"id": "S1P1", "quote": "一句话", "rule": "S1", "severity": "一般", "problem": "细节多", "why": "", "suggestion": ""}]}'
    llm.style_replies = [reply, reply]
    client = llm(None)
    assert video_audit.style_issues(client, SECTIONS, "规范")[0]["severity"] == adapt_review.MUST
    assert video_audit.style_issues(client, SECTIONS, "规范", adapt_review.WARN)[0]["severity"] == adapt_review.WARN
