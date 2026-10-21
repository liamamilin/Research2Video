"""A Job's period in a person's words, and setting it from the matrix page (spec/matrices.md)."""

from __future__ import annotations

import time
from datetime import datetime

import pytest

from core import periods
from tests.test_matrices import matrix_env  # noqa: F401
from tests.test_video_scripts import _csrf, _login, video_env  # noqa: F401
from web.services import managed_scheduler as managed

FLASH = "monitoring/ai_flash_discovery"


def _admin(client):
    _login(client, "admin", "admin-pass-123")


@pytest.mark.parametrize("args, expression", [
    (("daily", "05:00"), "0 5 * * *"),
    (("weekdays", "9:30"), "30 9 * * 1-5"),
    (("weekly", "08:00", [4, 1, 1]), "0 8 * * 1,4"),
    (("monthly", "05:00", None, 15), "0 5 15 * *"),
    (("manual",), ""),
])
def test_each_period_is_one_cron_and_reads_back(args, expression):
    assert periods.to_cron(*args) == expression
    read = periods.from_cron(expression)
    assert read["kind"] == (args[0] if expression else "custom")


@pytest.mark.parametrize("args", [("hourly",), ("daily", "25:00"), ("daily", "5"), ("weekly", "05:00", []),
                                  ("weekly", "05:00", [7]), ("monthly", "05:00", None, 29), ("monthly", "05:00", None, 0)])
def test_a_period_that_cannot_be_is_refused(args):
    with pytest.raises(ValueError):
        periods.to_cron(*args)


def test_a_cron_none_of_the_periods_makes_is_custom_and_a_month_is_counted_in_runs():
    assert periods.from_cron("*/5 * * * *") == {"kind": "custom"} and periods.from_cron("0 5 1 6 *")["kind"] == "custom"
    now = datetime(2026, 10, 21, 12, 0).astimezone()
    assert periods.runs_per_month("0 5 * * *", now) == 30 and periods.runs_per_month("0 5 15 * *", now) == 1
    assert periods.runs_per_month("", now) == 0


@pytest.fixture
def online(matrix_env, monkeypatch):  # noqa: F811
    def up():
        managed.store().heartbeat({"checked_at": time.time(), "status": "ok", "runtime": managed.runtime().identity()})
        return {"online": True}

    monkeypatch.setattr(managed, "ensure_online", up)
    return matrix_env


def _put(client, body, job=FLASH):
    return client.put(f"/api/matrices/ai/jobs/{job}/period", json=body, headers=_csrf(client))


def test_a_timed_period_asks_for_confirmation_then_shows_on_the_member(client, online):
    _admin(client)
    refused = _put(client, {"kind": "weekly", "time": "05:00", "days": [2]})
    assert refused.status_code == 409 and "每月约 4" in refused.text
    assert managed.store().list() == []  # nothing saved without the yes
    made = _put(client, {"kind": "weekly", "time": "05:00", "days": [2], "confirm": True})
    assert made.status_code == 200, made.text
    member = made.json()["members"][0]
    assert member["schedule"]["schedule"] == "0 5 * * 2" and member["schedule"]["enabled"]
    assert member["schedule"]["period"] == {"kind": "weekly", "time": "05:00", "days": [2], "day": 1}
    assert member["schedule"]["runs_per_month"] in (4, 5)


def test_changing_a_running_period_needs_no_second_yes_and_manual_removes_the_plan(client, online):
    _admin(client)
    _put(client, {"kind": "daily", "confirm": True})
    plan = managed.store().list()[0]
    changed = _put(client, {"kind": "weekdays", "time": "07:30"})
    assert changed.status_code == 200 and changed.json()["members"][0]["schedule"]["schedule"] == "30 7 * * 1-5"
    assert [p["id"] for p in managed.store().list()] == [plan["id"]]  # edited, not a second plan
    manual = _put(client, {"kind": "manual"})
    assert manual.status_code == 200 and manual.json()["members"][0]["schedule"] is None
    assert managed.store().list() == []


def test_a_paused_plan_is_turned_back_on_by_picking_a_period_after_confirming(client, online):
    _admin(client)
    _put(client, {"kind": "daily", "confirm": True})
    plan = managed.store().list()[0]
    managed.store().toggle(plan["id"], False)
    assert _put(client, {"kind": "daily"}).status_code == 409  # paused: turning it on spends money again
    back = _put(client, {"kind": "daily", "confirm": True})
    assert back.status_code == 200 and back.json()["members"][0]["schedule"]["enabled"]


def test_preview_shows_the_cron_next_runs_and_cost_without_saving(client, online):
    _admin(client)
    seen = client.get(f"/api/matrices/ai/jobs/{FLASH}/period", params={"kind": "weekly", "time": "06:00", "days": "1,4"}).json()
    assert seen["schedule"] == "0 6 * * 1,4" and len(seen["next_runs"]) == 3 and seen["runs_per_month"] >= 8
    assert managed.store().list() == []
    bad = client.get(f"/api/matrices/ai/jobs/{FLASH}/period", params={"kind": "weekly", "days": ""})
    assert bad.status_code == 422


def test_only_a_member_and_only_an_admin_can_have_its_period_set(client, online):
    _admin(client)
    assert _put(client, {"kind": "daily", "confirm": True}, job="research/games").status_code == 404  # not in this matrix
    client.cookies.clear()
    _login(client)  # an editor
    assert _put(client, {"kind": "daily", "confirm": True}).status_code == 403


# --- the matrix's runs ---------------------------------------------------------------------------------------------

def _history(video_env, job, rows):  # noqa: F811
    from core.state import StateManager, use_state_dir

    use_state_dir(str(video_env / "state"))
    for ts, user, status, path, searches in rows:
        StateManager.append_history(job, status, output_path=path, duration_seconds=60.0, user=user, usage={"searches": searches})
    # append_history stamps "now"; the tests need set times, so rewrite them
    import json

    file = video_env / "state" / "history" / (job.replace("/", "_") + ".jsonl")
    lines = [json.loads(line) for line in file.read_text(encoding="utf-8").splitlines()]
    for line, (ts, *_rest) in zip(lines, rows):
        line["ts"] = ts
    file.write_text("".join(json.dumps(line, ensure_ascii=False) + "\n" for line in lines), encoding="utf-8")


def test_the_runs_of_a_matrix_are_every_members_newest_first_with_what_came_of_them(client, matrix_env):
    _history(matrix_env, FLASH, [("2026-10-18T05:00:00+0800", "scheduler", "success", "output/flash/a.md", 20),
                                 ("2026-10-19T14:12:00+0800", "", "failed", "output/flash/b.md", 0),
                                 ("2026-10-20T10:00:00+0800", "admin", "success", "output/flash/c.md", 18)])
    _history(matrix_env, "research/deep", [("2026-10-19T09:00:00+0800", "autopilot", "cancelled", "", 2)])
    _login(client)
    body = client.get("/api/matrices/ai/runs").json()
    assert body["total"] == 4 and not body["more"]
    assert [(r["ts"][:10], r["job"], r["status"]) for r in body["runs"]] == [
        ("2026-10-20", FLASH, "success"), ("2026-10-19", FLASH, "failed"), ("2026-10-19", "research/deep", "cancelled"),
        ("2026-10-18", FLASH, "success")]
    first, failed, _cancelled, scheduled = body["runs"]
    assert (first["trigger"], first["searches"], first["report_path"], first["name"]) == ("manual", 18, "output/flash/c.md", "AI Flash Discovery")
    assert scheduled["trigger"] == "scheduled" and failed["trigger"] == "" and first["scripts"] == 0
    oldest = client.get("/api/matrices/ai/runs", params={"order": "asc"}).json()["runs"]
    assert [r["ts"][:10] for r in oldest] == ["2026-10-18", "2026-10-19", "2026-10-19", "2026-10-20"]


def test_the_runs_can_be_filtered_and_paged(client, matrix_env):
    _history(matrix_env, FLASH, [("2026-10-18T05:00:00+0800", "scheduler", "success", "", 1),
                                 ("2026-10-19T05:00:00+0800", "scheduler", "failed", "", 1),
                                 ("2026-10-20T05:00:00+0800", "scheduler", "success", "", 1)])
    _login(client)
    ok = client.get("/api/matrices/ai/runs", params={"status": "success"}).json()
    assert ok["total"] == 2 and {r["status"] for r in ok["runs"]} == {"success"}
    page = client.get("/api/matrices/ai/runs", params={"limit": 2}).json()
    assert len(page["runs"]) == 2 and page["more"]
    rest = client.get("/api/matrices/ai/runs", params={"limit": 2, "offset": 2}).json()
    assert len(rest["runs"]) == 1 and not rest["more"]
    assert client.get("/api/matrices/ai/runs", params={"job": "research/deep"}).json()["total"] == 0
    assert client.get("/api/matrices/ai/runs", params={"status": "weird"}).status_code == 422
    assert client.get("/api/matrices/ai/runs", params={"job": "research/games"}).status_code == 404
    assert client.get("/api/matrices/ai/runs", params={"limit": 0}).status_code == 422
    assert client.get("/api/matrices/none/runs").status_code == 404


def test_the_calendar_lists_the_next_week_of_timed_runs_and_warns_when_too_many_are_due_together(client, online):
    _admin(client)
    for job in (FLASH, "research/deep"):
        assert _put(client, {"kind": "daily", "time": "05:00", "confirm": True}, job=job).status_code == 200
    body = client.get("/api/matrices/ai/calendar").json()
    assert set(body["jobs"]) == {FLASH, "research/deep"}
    assert len(body["runs"]) in (14, 15) and body["runs"] == sorted(body["runs"], key=lambda r: r["ts"])
    assert body["jobs"][FLASH]["period"]["kind"] == "daily" and body["clashes"] == []  # two at once is what it can run
    from tests.test_matrices import write  # a third Job at the same minute is one too many
    write(online / "config", "ai", "name: AI 矩阵\ndomain: AI\nkeywords: []\njobs: [monitoring/ai_flash_discovery, research/deep, research/games]\n")
    assert _put(client, {"kind": "daily", "time": "05:00", "confirm": True}, job="research/games").status_code == 200
    clashes = client.get("/api/matrices/ai/calendar").json()["clashes"]
    assert clashes and len(clashes[0]["jobs"]) == 3  # three due at 05:00


# --- pausing the whole matrix ----------------------------------------------------------------------------------

def test_pausing_a_matrix_stops_its_timed_runs_and_resuming_turns_the_same_ones_back_on(client, online):
    _admin(client)
    _put(client, {"kind": "daily", "confirm": True})
    _put(client, {"kind": "weekdays", "time": "07:00", "confirm": True}, job="research/deep")
    plans = {p["job_name"]: p for p in managed.store().list()}
    managed.store().toggle(plans["research/deep"]["id"], False)  # already off by hand before the pause
    paused = client.post("/api/matrices/ai/pause", headers=_csrf(client))
    assert paused.status_code == 200 and paused.json()["paused"] == [FLASH]
    assert not any(p["enabled"] for p in managed.store().list())
    assert [m["schedule"]["enabled"] for m in paused.json()["members"]] == [False, False]
    assert client.put("/api/matrices/ai", json={"name": "改名了", "domain": "AI", "keywords": []}, headers=_csrf(client)).json()["paused"] == [FLASH]
    refused = client.post("/api/matrices/ai/resume", json={}, headers=_csrf(client))
    assert refused.status_code == 409 and "1 个 Job" in refused.text and not any(p["enabled"] for p in managed.store().list())
    back = client.post("/api/matrices/ai/resume", json={"confirm": True}, headers=_csrf(client))
    assert back.status_code == 200 and back.json()["paused"] == []
    on = {p["job_name"]: bool(p["enabled"]) for p in managed.store().list()}
    assert on == {FLASH: True, "research/deep": False}  # the one switched off by hand stays off


def test_pausing_needs_an_admin_and_a_matrix_with_nothing_timed_just_records_nothing(client, online):
    _login(client)  # an editor
    assert client.post("/api/matrices/ai/pause", headers=_csrf(client)).status_code == 403
    client.cookies.clear()
    _admin(client)
    assert client.post("/api/matrices/ai/pause", headers=_csrf(client)).json()["paused"] == []
    assert client.post("/api/matrices/ai/resume", json={}, headers=_csrf(client)).status_code == 200  # nothing to turn on
