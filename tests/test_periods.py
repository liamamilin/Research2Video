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
