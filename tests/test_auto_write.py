"""Unattended writing by config: ``auto: N`` writes the Job's new reports without being asked (spec/production.md)."""

from __future__ import annotations

import os

import pytest

from core import schedule_runner
from core.video import auto as video_auto
from core.video import autopilot_store as ap
from core.video import production, store
from tests.test_production import prod_env  # noqa: F401
from tests.test_video_scripts import _csrf, _login, video_env  # noqa: F401
from web.services import video_autopilot as pilot
from web.services import video_render, video_workflow

JOB = "monitoring/ai_flash_discovery"


def _report(env):
    return env / "output" / "flash" / "2026-10-19_ai_flash_discovery.md"


def _config_file(env, key):
    return env / "config" / "production" / f"{key}.yaml"


def _fresh(env, key="flash", count=3):
    """Turn auto on for a config, then make the Job's report come out after it, long enough ago to be settled."""
    production.set_auto(str(env / "config"), key, count)
    stamp = os.path.getmtime(_config_file(env, key)) + 500
    os.utime(_report(env), (stamp, stamp))
    return stamp + 120  # "now"


def _pending(env, now):
    return video_auto.pending(str(env / "config"), str(env / "jobs"), str(env / "state"), str(env), now)


@pytest.fixture
def pilot_env(prod_env, monkeypatch):  # noqa: F811
    spawned: list[str] = []
    monkeypatch.setattr(video_workflow, "spawn", lambda script_id, started_by="": spawned.append(script_id))
    monkeypatch.setattr(video_render, "start", lambda render_id, client=None: None)
    started: list[str] = []
    monkeypatch.setattr(pilot, "_start_discovery", lambda job: started.append(job))
    return prod_env, spawned, started


def test_auto_is_a_count_of_items_and_only_for_a_recipe_that_writes_one_video_per_item(prod_env):
    config, jobs = str(prod_env / "config"), str(prod_env / "jobs")
    for name, body in {"on": f"job: {JOB}\nrecipe: flash\nauto: 3\n", "off": f"job: {JOB}\nrecipe: flash\nauto: 0\n",
                       "roundup": f"job: {JOB}\nrecipe: ai-developer-roundup-long\nauto: 2\n",
                       "many": f"job: {JOB}\nrecipe: flash\nauto: 11\n", "word": f"job: {JOB}\nrecipe: flash\nauto: '3'\n",
                       "yes": f"job: {JOB}\nrecipe: flash\nauto: true\n"}.items():
        _config_file(prod_env, name).write_text(body, encoding="utf-8")
    every = production.load_all(config, jobs)
    assert every["on"].usable and every["on"].auto == 3 and every["on"].as_dict()["auto"] == 3
    assert every["off"].usable and every["off"].auto == 0
    assert every["flash"].auto == 0  # off unless asked for
    assert "不能自动写" in "".join(every["roundup"].errors)
    for key in ("many", "word", "yes"):
        assert "auto 应是每次写几条" in "".join(every[key].errors), key


def test_set_auto_keeps_the_rest_of_the_file_and_off_removes_it(prod_env):
    config = str(prod_env / "config")
    path = _config_file(prod_env, "flash")
    before = path.read_text(encoding="utf-8")
    assert production.set_auto(config, "flash", 2) and "auto: 2" in path.read_text(encoding="utf-8")
    assert production.set_auto(config, "flash", None)
    assert "auto:" not in path.read_text(encoding="utf-8")
    assert [l for l in path.read_text(encoding="utf-8").splitlines() if not l.startswith("#")] == \
        [l for l in before.splitlines() if not l.startswith("#")]
    assert not production.set_auto(config, "nope", 2)


def test_only_a_report_that_came_out_after_auto_was_turned_on_is_written(prod_env):
    production.set_auto(str(prod_env / "config"), "flash", 3)
    stamp = os.path.getmtime(_config_file(prod_env, "flash"))
    os.utime(_report(prod_env), (stamp - 5000, stamp - 5000))  # the report that was already there, however old
    assert _pending(prod_env, stamp + 9999) == []
    os.utime(_report(prod_env), (stamp + 500, stamp + 500))
    found = _pending(prod_env, stamp + 9999)
    assert [(f["job"], [p["production"] for p in f["picks"]]) for f in found] == [(JOB, ["flash"])]
    assert found[0]["picks"][0] == {"column": "flash", "count": 3, "source": "flash", "production": "flash"}
    assert found[0]["report"].endswith("2026-10-19_ai_flash_discovery.md")


def test_a_report_still_being_written_is_left_alone(prod_env):
    now = _fresh(prod_env)
    stamp = now - 120
    assert _pending(prod_env, stamp + video_auto.SETTLE_SECONDS - 1) == []
    assert len(_pending(prod_env, stamp + video_auto.SETTLE_SECONDS)) == 1


def test_a_batch_per_job_and_report_with_a_pick_for_each_auto_config_in_key_order(prod_env):
    config = str(prod_env / "config")
    production.set_auto(config, "point_short", 2)
    production.set_auto(config, "flash", 3)
    stamp = max(os.path.getmtime(_config_file(prod_env, k)) for k in ("flash", "point_short")) + 500
    os.utime(_report(prod_env), (stamp, stamp))
    [found] = _pending(prod_env, stamp + 120)
    assert [(p["production"], p["count"]) for p in found["picks"]] == [("flash", 3), ("point_short", 2)]


def test_firing_records_the_report_so_it_is_written_once_and_each_script_records_its_config(pilot_env):
    env, spawned, started = pilot_env
    now = _fresh(env)
    assert pilot.fire_auto(now) == 1
    assert pilot.fire_auto(now) == 0  # the report is handled
    assert _pending(env, now) == []
    [run] = ap.list_runs()
    assert run["spec"]["job"] == JOB and run["spec"]["report"].endswith("_ai_flash_discovery.md")
    assert run["spec"]["refresh"] is False and run["started_by"] == "autopilot"
    pilot.tick()
    run = ap.get_run(run["id"])
    assert run["items"] and {i["production"] for i in run["items"]} == {"flash"}
    assert started == []  # the Job's own schedule made the report: the batch does not start it
    for item in run["items"]:
        script = store.get_script(item["script_id"])
        assert (script["production"], script["created_by"], script["column_key"]) == ("flash", "autopilot", "flash")
    assert spawned and len(run["items"]) <= 3


def test_auto_without_research_writes_from_the_item_and_a_variant_column_resolves(pilot_env):
    env, spawned, _ = pilot_env
    _config_file(env, "bare").write_text(f"job: {JOB}\nrecipe: flash\nreverify: false\n", encoding="utf-8")
    now = _fresh(env, "bare", 1)
    assert pilot.fire_auto(now) == 1
    pilot.tick()
    [run] = ap.list_runs()
    [item] = run["items"]
    script = store.get_script(item["script_id"])
    assert (script["column_key"], script["production"]) == ("flash-nr", "bare")
    assert script["topic"]["from_item"] == item["topic"]["id"] and script["topic"]["from_report"].endswith("_ai_flash_discovery.md")


def test_the_scheduler_wakes_the_console_for_a_new_report_and_not_for_an_old_one(prod_env):
    runtime = schedule_runner.Runtime(str(prod_env), str(prod_env / "state"), str(prod_env / "jobs"),
                                      str(prod_env / "config"), str(prod_env / "logs"))
    state = str(prod_env / "state")
    now = _fresh(prod_env)
    assert not schedule_runner.autopilot_due(state, now)  # without the runtime it only knows plans and running batches
    assert schedule_runner.autopilot_due(state, now, runtime)
    ap.finished_run({"picks": [], "job": JOB, "report": str(_report(prod_env))}, "写过了")
    assert not schedule_runner.autopilot_due(state, now, runtime)


def test_api_turning_auto_on_and_off(client, prod_env):
    _login(client)
    on = client.patch("/api/production/flash", json={"auto": 2}, headers=_csrf(client))
    assert on.status_code == 200 and on.json()["auto"] == 2
    assert [c["auto"] for c in client.get("/api/production").json() if c["key"] == "flash"] == [2]
    roundup = client.patch("/api/production/ai-developer-roundup-long", json={"auto": 2}, headers=_csrf(client))
    assert roundup.status_code == 422 and roundup.json()["error"]["code"] == "auto_needs_each"
    assert client.patch("/api/production/flash", json={"auto": 11}, headers=_csrf(client)).status_code == 422
    assert client.patch("/api/production/nope", json={"auto": 1}, headers=_csrf(client)).status_code == 404
    off = client.patch("/api/production/flash", json={"auto": None}, headers=_csrf(client))
    assert off.status_code == 200 and off.json()["auto"] == 0
    added = client.post("/api/production", json={"job": JOB, "recipe": "point_short", "auto": 2}, headers=_csrf(client))
    assert added.status_code == 201 and added.json()["auto"] == 2
    bad = client.post("/api/production", json={"job": JOB, "recipe": "ai-developer-roundup-long", "auto": 2},
                      headers=_csrf(client))
    assert bad.status_code == 422  # never left behind
    _login(client, "viewer", "viewer-pass-123")
    assert client.patch("/api/production/flash", json={"auto": 1}, headers=_csrf(client)).status_code == 403
