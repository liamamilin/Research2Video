"""A Job whose reports are no item lists takes a config too: each report is one video (spec/production.md)."""

from __future__ import annotations

import os
import time

import pytest

from core import schedule_runner
from core.video import auto as video_auto
from core.video import autopilot_store as ap
from core.video import columns as video_columns
from core.video import length, outputs, production, recipes, reports, store
from tests.test_auto_write import _auto_off, _config_file, _pending  # noqa: F401
from tests.test_production import prod_env  # noqa: F401
from tests.test_video_scripts import (GOOD, ROOT, FakeLLM, _csrf, _login, _wait, autovideo, llm,  # noqa: F401
                                      research, script_json, video_env)
from web.services import video_autopilot as pilot
from web.services import video_render, video_workflow

JOB = "research/loose"  # no topics: its report is one file a day, output/x/{date}.md


@pytest.fixture
def whole_env(prod_env):  # noqa: F811
    out = prod_env / "output" / "x"
    out.mkdir(parents=True, exist_ok=True)
    for day, title in (("2026-10-18", "旧的一期"), ("2026-10-20", "新的一期")):
        (out / f"{day}.md").write_text(f"# {title}\n\n正文，来源 https://example.com/pricing\n", encoding="utf-8")
    (prod_env / "config" / "production" / "whole.yaml").write_text(f"job: {JOB}\nrecipe: deep_dive\n", encoding="utf-8")
    return prod_env


def _load(env):
    return production.load_all(str(env / "config"), str(env / "jobs"))


def test_a_recipe_says_it_can_be_written_from_a_whole_report():
    every = recipes.expanded(str(ROOT / "config"))
    assert {k for k, r in every.items() if r.whole_report} == {
        "deep_dive", "ai-non_tech-deep_dive-long", "report_digest", "ai-non_tech-digest-long", "top_finding",
        "ai-non_tech-headline-short", "action_list", "ai-non_tech-action_list-short", "topic_digest", "theme_research"}
    assert not every["flash"].whole_report and not every["ai-developer-roundup-long"].whole_report


def test_a_whole_report_config_is_one_video_per_report_and_never_researches(whole_env):
    every = _load(whole_env)
    whole = every["whole"]
    assert whole.usable, whole.errors
    assert (whole.split, whole.split_name, whole.reverify, whole.source) == ("whole", "整份报告一期", False, "")
    assert (whole.recipe, whole.audience_key) == ("deep_dive", "developer")


@pytest.mark.parametrize("body, message", [
    ("recipe: flash\n", "不是条目列表"),                          # a quick take is made from one item
    ("recipe: ai-developer-roundup-long\n", "不是条目列表"),      # a roundup needs items to pick
    ("recipe: deep_dive\nreverify: false\n", "调研不能关"),   # a knowledge video is not told without research
    ("recipe: deep_dive\nauto: 2\n", "auto 只能是 1"),
])
def test_a_whole_report_config_that_cannot_be_is_listed_with_why(whole_env, body, message):
    _config_file(whole_env, "bad").write_text(f"job: {JOB}\n{body}", encoding="utf-8")
    bad = _load(whole_env)["bad"]
    assert not bad.usable and message in "".join(bad.errors)


def test_a_whole_report_config_takes_an_audience_a_length_and_auto_like_any_other(whole_env):
    _config_file(whole_env, "kids").write_text(
        f"job: {JOB}\nrecipe: deep_dive\naudience: non_tech\nminutes: 8\nauto: 1\n", encoding="utf-8")
    kids = _load(whole_env)["kids"]
    assert kids.usable, kids.errors
    assert (kids.audience_key, kids.target_minutes, kids.auto, kids.split) == ("non_tech", 8.0, 1, "whole")


def test_recording_a_report_again_fills_in_the_job_it_was_recorded_without(whole_env):
    first = reports.record_file("output/x/2026-10-20.md")
    assert first["job"] == ""
    again = reports.record_file("output/x/2026-10-20.md", job=JOB)
    assert again["id"] == first["id"] and again["job"] == JOB
    assert reports.record_file("output/x/2026-10-20.md", job="other")["job"] == JOB  # never taken over


def test_api_a_whole_config_lists_the_jobs_reports_and_writes_from_one(client, whole_env, research, llm, autovideo):
    budget = length.budget_for(video_columns.get(str(ROOT / "config"), "deep_dive").target_minutes, "voxcpm_anchor")
    llm.replies = [script_json(budget - 5, citations={(0, 0): [GOOD]})]
    _login(client)
    pending = client.get("/api/production/whole/pending").json()
    assert [(r["date"], r["path"], r["written"]) for r in pending["reports"]] == [
        ("2026-10-20", "output/x/2026-10-20.md", False), ("2026-10-18", "output/x/2026-10-18.md", False)]
    assert pending["items"] == []
    elsewhere = client.post("/api/production/whole/write", json={"report_path": "output/flash/2026-10-19_ai_flash_discovery.md"},
                            headers=_csrf(client))
    assert elsewhere.status_code == 422 and elsewhere.json()["error"]["code"] == "not_from_this_job"  # not this Job's report
    assert client.post("/api/production/whole/write", json={}, headers=_csrf(client)).status_code == 422
    assert client.post("/api/production/whole/write", json={"topic_ids": ["x"]}, headers=_csrf(client)).status_code == 422
    made = client.post("/api/production/whole/write", json={"report_path": "output/x/2026-10-20.md"}, headers=_csrf(client))
    assert made.status_code == 202, made.text
    script = _wait(client, made.json()[0]["id"], {"DRAFT", "FAILED"})
    assert script["status"] == "DRAFT", script.get("error")
    assert research.jobs == []  # the report is the research
    stored = store.get_script(script["id"])
    assert (stored["column_key"], stored["production"]) == ("deep_dive", "whole")
    assert stored["topic"]["title"] == "新的一期" and stored["topic"]["from_report"] == "output/x/2026-10-20.md"
    again = client.post("/api/production/whole/write", json={"report_path": "output/x/2026-10-20.md"}, headers=_csrf(client))
    assert again.status_code == 409 and again.json()["error"]["code"] == "already_written"
    listed = client.get("/api/production/whole/pending").json()["reports"]
    assert [r["written"] for r in listed] == [True, False] and listed[0]["scripts"][0]["id"] == script["id"]
    # the report knows its Job, so the script is traced to its matrix and its config
    [row] = [r for r in outputs.collect(str(whole_env / "config"), str(whole_env / "jobs"))["scripts"] if r["id"] == script["id"]]
    assert (row["job"], row["matrix"], row["production"], row["production_guessed"]) == (JOB, "ai", "whole", False)


def test_api_a_whole_config_is_added_and_turned_on_for_auto_as_a_switch(client, whole_env):
    _login(client)
    added = client.post("/api/production", json={"job": JOB, "recipe": "deep_dive", "audience": "non_tech"}, headers=_csrf(client))
    assert added.status_code == 201, added.text
    key = added.json()["key"]
    assert added.json()["split"] == "whole"
    assert client.patch(f"/api/production/{key}", json={"auto": 3}, headers=_csrf(client)).status_code == 422
    on = client.patch(f"/api/production/{key}", json={"auto": 1}, headers=_csrf(client))
    assert on.status_code == 200 and on.json()["auto"] == 1
    assert client.post("/api/production", json={"job": JOB, "recipe": "flash"}, headers=_csrf(client)).status_code == 422


def _turn_on(env):
    production.set_auto(str(env / "config"), "whole", 1)
    newest = env / "output" / "x" / "2026-10-20.md"
    stamp = os.path.getmtime(_config_file(env, "whole")) + 500
    os.utime(newest, (stamp, stamp))
    return stamp + 120


def test_auto_picks_the_report_itself_and_never_reads_a_topic_source(whole_env, monkeypatch):
    now = _turn_on(whole_env)
    [found] = _pending(whole_env, now)
    assert found["job"] == JOB and found["report"].endswith("2026-10-20.md")
    assert found["picks"] == [{"column": "deep_dive", "count": 1, "whole": True, "production": "whole"}]  # no source
    spawned: list[str] = []
    monkeypatch.setattr(video_workflow, "spawn", lambda script_id, started_by="": spawned.append(script_id))
    monkeypatch.setattr(video_render, "start", lambda render_id, client=None: None)

    def forbidden(*args, **kwargs):
        raise AssertionError("a whole-report batch read a topic source")
    monkeypatch.setattr(pilot.topic_store, "load", forbidden)
    assert pilot.fire_auto(now) == 1 and pilot.fire_auto(now) == 0
    pilot.tick()
    [run] = ap.list_runs()
    [item] = run["items"]
    script = store.get_script(item["script_id"])
    assert (script["column_key"], script["production"], script["created_by"]) == ("deep_dive", "whole", "autopilot")
    assert script["topic"]["from_report"] == "output/x/2026-10-20.md" and script["topic"]["title"] == "新的一期"
    assert script["topic"]["report_id"] and spawned == [script["id"]]  # attached to the report row once its writer starts
    assert len(reports.scripts_of("output/x/2026-10-20.md", "deep_dive")) == 1  # but it counts as written from the start
    # written already (by hand, say): a second batch for the same report picks nothing
    assert pilot.pick_topics(run["spec"]) == []
    # put away, it no longer counts: archiving a script is how the user asks for the report to be written again
    store.update_script(script["id"], archived=1)
    [again] = pilot.pick_topics(run["spec"])
    assert again["topic"]["from_report"] == "output/x/2026-10-20.md"


def test_auto_leaves_a_whole_report_alone_when_it_was_written_by_hand(whole_env, monkeypatch):
    now = _turn_on(whole_env)
    record = reports.record_file("output/x/2026-10-20.md", job=JOB)
    script = store.create_script("deep_dive", {"id": None, "title": "手写的", "fields": {}})
    reports.attach(script["id"], "output/x/2026-10-20.md")
    assert pilot.pick_topics({"picks": [{"column": "deep_dive", "count": 1, "whole": True, "production": "whole"}],
                              "job": JOB, "report": str(whole_env / "output" / "x" / "2026-10-20.md")}) == []
    assert record["id"]


def test_the_scheduler_wakes_the_console_for_a_whole_report_too(whole_env):
    runtime = schedule_runner.Runtime(str(whole_env), str(whole_env / "state"), str(whole_env / "jobs"),
                                      str(whole_env / "config"), str(whole_env / "logs"))
    now = _turn_on(whole_env)
    assert schedule_runner.autopilot_due(str(whole_env / "state"), now, runtime)


def test_two_batches_share_the_limits_on_scripts_being_written(pilot_env_whole, monkeypatch):
    spawned = pilot_env_whole
    for n in range(2):
        run = ap.create_run({"picks": [], "refresh": False}, [
            {"column_key": "flash", "topic": {"id": f"2026-10-19/F{n}{i}", "title": f"选题 {n}{i}", "fields": {}}}
            for i in range(2)])
        ap.update_run(run["id"], stage="ITEMS")
    pilot.tick()
    assert len(spawned) == pilot.MAX_SCRIPTS  # not two per batch


@pytest.fixture
def pilot_env_whole(video_env, monkeypatch):
    spawned: list[str] = []
    monkeypatch.setattr(video_workflow, "spawn", lambda script_id, started_by="": spawned.append(script_id))
    monkeypatch.setattr(video_render, "start", lambda render_id, client=None: None)
    monkeypatch.setattr(pilot, "fresh_topics", lambda spec, run: True)
    return spawned


@pytest.mark.parametrize("recipe, audience, minutes", [
    ("report_digest", "", 4), ("report_digest", "non_tech", 5), ("top_finding", "", None),
    ("top_finding", "non_tech", None), ("action_list", "", 3), ("action_list", "non_tech", None)])
def test_each_shipped_whole_report_recipe_is_a_usable_config_on_a_job_without_items(whole_env, recipe, audience, minutes):
    lines = [f"job: {JOB}", f"recipe: {recipe}"] + ([f"audience: {audience}"] if audience else []) \
        + ([f"minutes: {minutes}"] if minutes else [])
    _config_file(whole_env, "new").write_text("\n".join(lines) + "\n", encoding="utf-8")
    made = _load(whole_env)["new"]
    assert made.usable, made.errors
    assert made.split == "whole" and not made.reverify
    assert (made.audience_key or "developer") == (audience or "developer")


def test_the_topics_grid_leaves_out_a_recipe_that_only_writes_from_a_whole_report(whole_env):
    # no items to pick: deep_dive stays (it also reads the opportunities source), the sourceless ones go
    rows = {r["cell"] for r in pilot.coverage()}
    assert {"deep_dive", "flash", "point_short"} <= rows
    assert not rows & {"report_digest", "top_finding", "action_list", "ai-non_tech-digest-long"}
