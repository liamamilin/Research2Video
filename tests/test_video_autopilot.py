"""The unattended pipeline: picking, the approval gate, resuming at every stage, weekly plans.

Script writing and rendering are the other tests' business; here ``spawn`` and ``video_render.start`` only record
that they were asked, and the tests set the script and render rows the way those workers would.
"""

from __future__ import annotations

import os
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
    import datetime
    from core.video import topics as video_topics
    monkeypatch.setattr(video_topics, "_today", lambda: datetime.date(2026, 10, 19))  # the report's own day: none is stale
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
    assert gate["approved"] == "auto" and gate["label"] == "partial"  # audited, but the paragraph cites nothing
    pilot.tick()
    pilot.tick()  # a render in flight is followed, never submitted again
    assert len(store.list_renders(script_id)) == 1 and len(set(started)) == 1
    render = store.list_renders(script_id)[0]
    store.update_render(render["id"], status="COMPLETED")
    pilot.tick()
    assert item_of(run["id"], 0)["state"] == "DONE"
    assert ap.get_run(run["id"])["status"] == "DONE"


@pytest.mark.parametrize("kwargs, tier", [({"serious": 1}, "serious"), ({"status_ok": False}, "partial")])
def test_a_draft_is_approved_whatever_the_label_and_the_label_is_recorded(pilot_env, monkeypatch, kwargs, tier):
    run = batch(monkeypatch, 1)
    pilot.tick()
    script_id = item_of(run["id"], 0)["script_id"]
    draft(script_id, **kwargs)
    pilot.tick()
    item = item_of(run["id"], 0)
    assert item["state"] == "WORKING" and item["gate"]["approved"] == "auto" and item["gate"]["label"] == tier
    assert store.get_script(script_id)["status"] == "APPROVED"
    pilot.tick()
    assert len(store.list_renders(script_id)) == 1  # nothing waited for the user


def test_a_draft_whose_model_audit_never_ran_gets_its_audit_rerun_then_goes_on_labelled_unaudited(pilot_env, monkeypatch):
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
    assert item["gate"]["label"] == "unaudited" and item["gate"]["audited"] is False
    assert store.get_script(script_id)["status"] == "APPROVED"


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
    # a slot the host slept through by more than a day does not start a stale batch, but it is recorded as missed
    stale = ap.get_plan(plan["id"])["next_fire"] + 3 * 86400
    assert pilot.fire_due_plans(now=stale) == 0
    runs = ap.list_runs()
    assert len(runs) == 2 and runs[0]["status"] == "DONE" and not runs[0]["items"] and "错过了" in runs[0]["note"]


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
        def getresponse(self): return type("R", (), {"read": lambda self: b"{}"})()
        def close(self): pass

    monkeypatch.setattr("http.client.HTTPConnection", Conn)
    monkeypatch.setattr(runner, "_last_poke", 0.0)
    assert runner.poke_autopilot(runtime, 1000.0) is False and knocks == []  # nothing to do
    plan = pilot.save_plan("每周一批", "0 6 * * 1", {"picks": [{"column": "flash", "count": 1, "source": "flash"}],
                                                   "refresh": False})
    assert runner.poke_autopilot(runtime, plan["next_fire"] - 10, interval=0) is False  # not due yet
    assert runner.poke_autopilot(runtime, plan["next_fire"] + 1, interval=0) is True
    assert knocks == ["127.0.0.1:8765", "/api/health"]  # the first address answered with JSON
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


def test_api_archiving_hides_a_script_without_deleting_it(client, video_env):
    from tests.test_video_scripts import _csrf, _login

    _login(client)
    script = store.create_script("flash", {"id": "2026-10-19/F9", "title": "旧的测试文稿", "fields": {}})
    store.update_script(script["id"], status="DRAFT")
    busy = store.create_script("flash", {"id": "2026-10-19/F8", "title": "还在写的文稿", "fields": {}})
    assert client.post(f"/api/video/scripts/{busy['id']}/archive", headers=_csrf(client)).status_code == 409
    done = client.post(f"/api/video/scripts/{script['id']}/archive", headers=_csrf(client))
    assert done.status_code == 200 and done.json()["archived"] == 1
    assert [s["id"] for s in client.get("/api/video/scripts").json()] == [busy["id"]]
    assert [s["id"] for s in client.get("/api/video/scripts?archived=true").json()] == [script["id"]]
    assert "2026-10-19/F9" in store.topic_ids_in_use()  # still counts as written
    client.post(f"/api/video/scripts/{script['id']}/archive", headers=_csrf(client), json={"archived": False})
    assert len(client.get("/api/video/scripts").json()) == 2


def test_reference_videos_keep_only_single_video_pages():
    from core.search import SearchResult
    from core.video import reference

    assert reference.platform_of("https://www.bilibili.com/video/BV1xx411c7mD/") == "B 站"
    assert reference.platform_of("https://www.youtube.com/watch?v=abc123def45") == "YouTube"
    assert reference.platform_of("https://youtu.be/abc123def45") == "YouTube"
    assert reference.platform_of("https://space.bilibili.com/123") is None
    assert reference.platform_of("https://www.youtube.com/@channel") is None
    assert reference.platform_of("https://notbilibili.com/video/BV1xx") is None

    class Client:
        calls = 1

        def search(self, queries, objective=""):
            return [SearchResult("别人讲缓存", "https://www.bilibili.com/video/BV1xx411c7mD", "2026-10-01", ["讲提示缓存。"]),
                    SearchResult("某频道", "https://space.bilibili.com/1"),
                    SearchResult("别人讲缓存", "https://www.bilibili.com/video/BV1xx411c7mD")]

    found = reference.find(Client(), "提示缓存", limit=5)
    assert [v["platform"] for v in found] == ["B 站"] and found[0]["snippet"] == "讲提示缓存。"


def test_api_reference_search_returns_links_only(client, video_env, monkeypatch):
    from tests.test_video_scripts import _login
    from core.search import SearchResult, SearchClient

    monkeypatch.setattr(SearchClient, "search", lambda self, queries, objective="", after_date=None: [
        SearchResult("别人讲缓存", "https://www.bilibili.com/video/BV1xx411c7mD", None, ["x"])])
    _login(client, "viewer", "viewer-pass-123")
    body = client.get("/api/video/references", params={"q": "提示缓存"}).json()
    assert body["videos"][0]["url"].startswith("https://www.bilibili.com/video/")
    assert client.get("/api/video/references", params={"q": "短"}).status_code == 422


def test_a_held_script_the_user_approves_goes_on_to_its_render_and_an_old_gate_hold_is_let_through(pilot_env, monkeypatch):
    run = batch(monkeypatch, 2)
    pilot.tick()
    first, second = (item_of(run["id"], i) for i in (0, 1))
    store.update_script(first["script_id"], status="FAILED", error="x")
    for _ in range(pilot.MAX_RETRIES + 1):
        store.update_script(first["script_id"], status="FAILED", error="还是失败")
        pilot.tick()
    assert item_of(run["id"], 0)["state"] == "HELD"
    store.update_script(first["script_id"], status="APPROVED", approved_version=1)  # the user fixed it and approved
    draft(first["script_id"])
    store.update_script(first["script_id"], status="APPROVED", approved_version=1)
    pilot.tick()
    assert item_of(run["id"], 0)["state"] == "WORKING"
    # a draft the quality gate of an earlier version held is let through now
    draft(second["script_id"], serious=1)
    ap.update_item(second["id"], state="HELD", note="闸门没通过：事实审计有 1 条严重问题")
    ap.update_run(run["id"], status="DONE")
    pilot.tick()
    pilot.tick()
    assert store.get_script(second["script_id"])["status"] == "APPROVED"
    assert item_of(run["id"], 1)["gate"]["label"] == "serious"


def test_a_failed_render_is_submitted_once_more_then_held(pilot_env, monkeypatch):
    run = batch(monkeypatch, 1)
    pilot.tick()
    script_id = item_of(run["id"], 0)["script_id"]
    draft(script_id)
    pilot.tick()  # approved
    pilot.tick()  # first render
    for expected in (2, 2):
        render = store.list_renders(script_id)[0]
        store.update_render(render["id"], status="FAILED", error_message="AutoVideo 规划失败")
        pilot.tick()
        assert len(store.list_renders(script_id)) == expected
        if expected == 2:
            break
    second = [r for r in store.list_renders(script_id) if r["status"] != "FAILED"][0]
    store.update_render(second["id"], status="FAILED", error_message="又失败了")
    pilot.tick()
    assert len(store.list_renders(script_id)) == 2
    assert item_of(run["id"], 0)["state"] == "HELD" and "出片失败" in item_of(run["id"], 0)["note"]


def test_the_wake_up_skips_a_stranger_on_loopback_and_reaches_the_gateway(video_env, monkeypatch):
    from core import schedule_runner as runner
    from web.settings import get_settings

    state_dir = get_settings().paths.state_dir
    runtime = runner.Runtime(str(video_env), state_dir, str(video_env / "jobs"), str(video_env / "config"), str(video_env / "logs"))
    pilot.start_run({"picks": [{"column": "flash", "count": 1, "source": "flash"}], "refresh": False})
    asked: list[str] = []

    class Conn:
        def __init__(self, host, port, timeout=None): self.host = host
        def request(self, method, path): asked.append(self.host)
        def getresponse(self):
            body = b"404 page not found" if self.host == "127.0.0.1" else b'{"error": {"code": "app_starting"}}'
            return type("R", (), {"read": lambda s: body})()
        def close(self): pass

    class Sock:
        def __init__(self, *a): pass
        def connect(self, addr): pass
        def getsockname(self): return ("192.168.2.226", 1234)
        def close(self): pass

    monkeypatch.setattr("http.client.HTTPConnection", Conn)
    monkeypatch.setattr("socket.socket", Sock)
    monkeypatch.setattr(runner, "_last_poke", 0.0)
    assert runner.poke_autopilot(runtime, 1.0, interval=0) is True
    assert asked == ["127.0.0.1", "192.168.2.226"]


def test_the_wake_up_follows_the_gateway_port_in_web_yaml(video_env):
    from core import schedule_runner as runner

    runtime = runner.Runtime(str(video_env), str(video_env / "state"), str(video_env / "jobs"), str(video_env / "config"), str(video_env / "logs"))
    assert runner.gateway_port(runtime) == 8765  # no web.yaml: the default
    (video_env / "config" / "web.yaml").write_text("server:\n  port: 8775\n", encoding="utf-8")
    assert runner.gateway_port(runtime) == 8775


def test_todays_discovery_is_started_once_and_waited_for_not_restarted_every_tick(video_env, monkeypatch):
    # a tick thread left running by an earlier test's app would otherwise advance these runs behind the test's back
    from core.fileio import file_lock
    from web.settings import get_settings as real_settings

    with file_lock(os.path.join(real_settings().paths.state_dir, "autopilot-tick"), timeout=0):
        _discovery_once(video_env, monkeypatch)


def _discovery_once(video_env, monkeypatch):
    """Starting a job cancels the one already running under that name, so a start per tick would restart it forever."""
    out = video_env / "output"
    (out / "flash").mkdir(parents=True)
    spec = {"picks": [{"column": "flash", "count": 1, "source": "flash"}], "refresh": True}
    run, other = pilot.start_run(spec), pilot.start_run(spec)
    monkeypatch.setattr(pilot, "get_settings", lambda: type("S", (), {"paths": type("P", (), {"output_dir": str(out)})})())
    starts: list[int] = []
    running = {"now": False}
    monkeypatch.setattr(pilot, "_start_discovery", lambda: (starts.append(1), running.update(now=True)))
    monkeypatch.setattr(pilot, "_discovery_running", lambda: running["now"])
    for _ in range(5):  # five ticks while the discovery runs
        assert pilot.fresh_topics(run["spec"], ap.get_run(run["id"])) is False
    assert len(starts) == 1
    # the app restarted: the job is gone and no report appeared — one more start is allowed, then the batch goes on
    running["now"] = False
    assert pilot.fresh_topics(run["spec"], ap.get_run(run["id"])) is False and len(starts) == 2
    running["now"] = False
    assert pilot.fresh_topics(run["spec"], ap.get_run(run["id"])) is True and len(starts) == 2
    assert "没有跑出报告" in ap.get_run(run["id"])["note"]
    # a report for today ends the wait at once
    (out / "flash" / f"{time.strftime('%Y-%m-%d')}_ai_flash_discovery.md").write_text("x", encoding="utf-8")
    assert pilot.fresh_topics(other["spec"], other) is True and len(starts) == 2


def test_topics_know_which_scripts_cover_them(video_env):
    single = store.create_script("flash", {"id": "2026-10-19/F1", "title": "单条", "fields": {}})
    roundup = store.create_script("ai-developer-roundup-long", {"id": None, "title": "盘点", "items": ["2026-10-19/F1", "2026-10-19/F2"]})
    covered = store.scripts_by_topic()
    assert {s["id"] for s in covered["2026-10-19/F1"]} == {roundup["id"], single["id"]}
    assert [s["id"] for s in covered["2026-10-19/F2"]] == [roundup["id"]]


def test_cells_declare_topic_sources_and_picks_can_name_a_cell(video_env):
    from core.video import recipes as matrix
    import shutil
    from web.settings import get_settings as real_settings

    config_dir = real_settings().paths.config_dir
    assert matrix.topic_sources(config_dir, "point_short") == ["flash"]
    assert pilot.resolve_pick({"cell": "point_short", "count": 2}) == {"column": "point_short", "count": 2, "source": "flash"}
    assert pilot.resolve_pick({"column": "flash", "count": 1, "source": "flash"})["column"] == "flash"
    with pytest.raises(ValueError):  # a comparison has no topic source: it is chosen by hand
        pilot.resolve_pick({"cell": "ai-developer-comparison-long", "count": 1})
    run = pilot.start_run({"picks": [{"cell": "flash", "count": 1}], "refresh": False})
    assert run["spec"]["picks"][0]["cell"] == "flash"
    rows = {r["cell"]: r for r in pilot.coverage()}
    assert rows["flash"]["sources"] == ["flash"] and "open" in rows["flash"]
    assert rows.get("ai-developer-comparison-long", {"sources": []})["sources"] == []


def test_coverage_counts_written_per_cell_not_per_topic(video_env, monkeypatch):
    import shutil
    from web.settings import get_settings as real_settings

    config_dir = real_settings().paths.config_dir
    out = video_env / "output"
    (out / "flash").mkdir(parents=True)
    store.create_script("flash", {"id": "2026-10-19/F1", "title": "单条", "fields": {}})
    from core.video import flash as video_flash
    monkeypatch.setattr(video_flash, "load", lambda d, date=None: ("2026-10-19", [type("T", (), {"id": "2026-10-19/F1"})(), type("T", (), {"id": "2026-10-19/F2"})()]))
    rows = {r["cell"]: r for r in pilot.coverage()}
    assert (rows["flash"]["written"], rows["flash"]["open"]) == (1, 1)
    assert (rows["point_short"]["written"], rows["point_short"]["open"]) == (0, 2)  # same news, another cell


def _matrix_only(video_env):
    import shutil
    from web.settings import get_settings as real_settings

    config_dir = real_settings().paths.config_dir


def test_a_dismissed_topic_leaves_the_counts_and_the_picks_and_comes_back(video_env, monkeypatch):
    _matrix_only(video_env)
    topics = [type("T", (), {"id": f"2026-10-19/F{i}", "score": 9 - i, "stale": lambda self: False,
                             "as_dict": lambda self: {"id": self.id}})() for i in (1, 2, 3)]
    monkeypatch.setattr(pilot.video_flash, "load", lambda d, date=None: ("2026-10-19", topics))
    store.set_dismissed("2026-10-19/F1", True)
    row = {r["cell"]: r for r in pilot.coverage()}["flash"]
    assert (row["open"], row["dismissed"]) == (2, 1)
    picked = pilot.pick_topics({"picks": [{"cell": "flash", "count": 3}]})
    assert [i["topic"]["id"] for i in picked] == ["2026-10-19/F2", "2026-10-19/F3"]
    store.set_dismissed("2026-10-19/F1", False)
    assert {r["cell"]: r for r in pilot.coverage()}["flash"]["open"] == 3


def test_stale_news_ranks_after_fresher_news_but_is_still_picked(video_env, monkeypatch):
    _matrix_only(video_env)
    mk = lambda i, score, stale: type("T", (), {"id": f"2026-10-19/F{i}", "score": score, "stale": lambda self: stale,
                                                "as_dict": lambda self: {"id": self.id}})()
    topics = [mk(1, 9, True), mk(2, 7, False), mk(3, 8, False)]
    monkeypatch.setattr(pilot.video_flash, "load", lambda d, date=None: ("2026-10-19", topics))
    ids = [i["topic"]["id"] for i in pilot.pick_topics({"picks": [{"cell": "flash", "count": 3}]})]
    assert ids == ["2026-10-19/F3", "2026-10-19/F2", "2026-10-19/F1"]


def test_topic_age_marks_old_news_and_a_roundup_cell_cannot_be_picked_by_count(video_env):
    import datetime
    from core.video.topics import STALE_DAYS, Topic

    today = datetime.date(2026, 10, 20)
    old = Topic("x/F1", "x", "F1", "t", {"发布日期": "2026-10-13（模型上线）；涨价生效 2026-11-30"})
    assert old.event_date == "2026-10-13" and old.stale(today) and STALE_DAYS == 5
    assert not Topic("x/F2", "x", "F2", "t", {"发布日期": "2026-10-16"}).stale(today)
    assert not Topic("x/F3", "x", "F3", "t", {}).stale(today)
    _matrix_only(video_env)
    with pytest.raises(ValueError):
        pilot.resolve_pick({"cell": "ai-developer-roundup-long", "count": 2})
    row = {r["cell"]: r for r in pilot.coverage()}["ai-developer-roundup-long"]
    assert row["multi"] is True and row["sources"] == ["flash"]


def test_api_discovery_starts_once_and_refuses_while_running(client, video_env, monkeypatch):
    from tests.test_video_scripts import _csrf, _login

    running = {"now": False}
    starts: list[int] = []
    monkeypatch.setattr(pilot, "_discovery_running", lambda: running["now"])
    monkeypatch.setattr(pilot, "_start_discovery", lambda: (starts.append(1), running.update(now=True)))
    _login(client)
    first = client.post("/api/video/topics/discovery", headers=_csrf(client))
    second = client.post("/api/video/topics/discovery", headers=_csrf(client))  # a double click
    assert first.status_code == 202 and second.status_code == 409 and len(starts) == 1
    assert client.get("/api/video/topics/discovery").json()["running"] is True
    _login(client, "viewer", "viewer-pass-123")
    assert client.post("/api/video/topics/discovery", headers=_csrf(client)).status_code == 403
    assert client.get("/api/video/topics?source=flash").json()["rounds"] == []


def test_api_dismiss_is_for_editors_and_reversible(client, video_env):
    from tests.test_video_scripts import _csrf, _login

    _login(client)
    body = {"topic_id": "2026-10-19/F1"}
    assert client.post("/api/video/topics/dismiss", headers=_csrf(client), json=body).json()["dismissed"] is True
    assert "2026-10-19/F1" in store.dismissed_topic_ids()
    client.post("/api/video/topics/dismiss", headers=_csrf(client), json={**body, "dismissed": False})
    assert store.dismissed_topic_ids() == set()
    _login(client, "viewer", "viewer-pass-123")
    assert client.post("/api/video/topics/dismiss", headers=_csrf(client), json=body).status_code == 403


def test_api_a_batch_and_the_weekly_plan_can_be_set_per_cell(client, video_env):
    from tests.test_video_scripts import _csrf, _login

    _matrix_only(video_env)
    _login(client)
    spec = {"picks": [{"cell": "flash", "count": 2}, {"cell": "point_short", "count": 1}], "refresh": False}
    run = client.post("/api/video/autopilot/runs", headers=_csrf(client), json={"spec": spec})
    assert run.status_code == 202 and run.json()["spec"]["picks"][0] == {"cell": "flash", "count": 2, "source": "flash"} or run.json()["spec"]["picks"][0]["cell"] == "flash"
    plan = client.post("/api/video/autopilot/plans", headers=_csrf(client), json={"name": "p", "expression": "0 5 * * 2"}).json()
    edited = client.put(f"/api/video/autopilot/plans/{plan['id']}/spec", headers=_csrf(client), json={"spec": spec})
    assert edited.status_code == 200 and edited.json()["spec"]["picks"][1] == {"cell": "point_short", "count": 1, "source": "flash"} or edited.json()["spec"]["picks"][1]["cell"] == "point_short"
    assert edited.json()["expression"] == "0 5 * * 2"
    roundup = {"picks": [{"cell": "ai-developer-roundup-long", "count": 2}]}
    assert client.put(f"/api/video/autopilot/plans/{plan['id']}/spec", headers=_csrf(client), json={"spec": roundup}).status_code == 422
    assert client.put("/api/video/autopilot/plans/nope/spec", headers=_csrf(client), json={"spec": spec}).status_code == 404


def test_api_the_batch_shows_the_label_as_it_is_now_not_as_it_was_at_approval(client, pilot_env, monkeypatch):
    from tests.test_video_scripts import _login

    run = batch(monkeypatch, 1)
    pilot.tick()
    script_id = item_of(run["id"], 0)["script_id"]
    draft(script_id, serious=1)
    pilot.tick()
    _login(client)
    item = client.get("/api/video/autopilot").json()["runs"][0]["items"][0]
    assert item["credibility"]["tier"] == "serious" and item["gate"]["label"] == "serious"
    draft(script_id)  # a newer version, audited with nothing serious
    item = client.get("/api/video/autopilot").json()["runs"][0]["items"][0]
    assert item["credibility"]["tier"] != "serious" and item["gate"]["label"] == "serious"
