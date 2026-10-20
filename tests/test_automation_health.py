"""Automation alerts on the home board (spec/video-architecture.md, phase 1).

The acceptance case is 2026-10-20 05:00: the weekly batch restarted flash discovery 70 times (234 searches in runs
that never finished), gave up after 70 minutes and wrote nothing — and no page said so.
"""

from __future__ import annotations

import json
import time

import pytest

from core.video import autopilot_store as ap
from tests.test_video_scripts import _csrf, _login, video_env  # noqa: F401
from web.services import automation_health as health
from web.services import video_autopilot as pilot

DAY = "2026-10-20"
FIVE = time.mktime(time.strptime(f"{DAY} 05:00:29", "%Y-%m-%d %H:%M:%S"))


@pytest.fixture
def jobs(video_env, monkeypatch):
    """The discovery job under its path, the way the Jobs page names it, and no rounds."""
    folder = video_env / "jobs" / "monitoring"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "ai_flash_discovery.yaml").write_text("name: 快讯发现\nprompt: 找新闻\n", encoding="utf-8")
    (video_env / "jobs" / "video").mkdir(exist_ok=True)
    (video_env / "jobs" / "video" / "flash_abc.yaml").write_text("name: x\nprompt: y\n", encoding="utf-8")
    monkeypatch.setattr("web.runner.pipeline.list_round_states", lambda: [])
    return video_env


def history(env, name: str, entries: list[dict]) -> None:
    folder = env / "state" / "history"
    folder.mkdir(parents=True, exist_ok=True)
    with open(folder / (name.replace("/", "_") + ".jsonl"), "a", encoding="utf-8") as fh:
        for entry in entries:
            fh.write(json.dumps({"job_name": name, **entry}, ensure_ascii=False) + "\n")


def ts(clock: str) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(time.mktime(time.strptime(f"{DAY} {clock}", "%Y-%m-%d %H:%M:%S"))))


def morning_of_the_20th(env) -> dict:
    """The records 2026-10-20 05:00 left: 70 cancelled discovery runs by the batch, then an empty batch."""
    history(env, "ai_flash_discovery", [
        {"ts": ts(f"05:{m:02d}:02" if m < 60 else f"06:{m - 60:02d}:02"), "user": "autopilot", "status": "cancelled",
         "usage": {"searches": 4 if m <= 24 else 3}} for m in range(1, 71)])  # 234 in all
    run = ap.create_run({"picks": [{"column": "flash", "count": 3, "source": "flash"}], "refresh": True},
                        [], plan_id="weekly", started_by="autopilot")
    ap.update_run(run["id"], started_at=FIVE, note="今天的快讯发现没有在限时内完成，用最新的一期")
    return run


def test_the_morning_of_the_20th_shows_up_with_its_cause(jobs, monkeypatch):
    run = morning_of_the_20th(jobs)
    monkeypatch.setattr(pilot, "fresh_topics", lambda spec, r: True)
    monkeypatch.setattr(pilot, "pick_topics", lambda spec: [])  # the 19th's report was all written
    pilot._tick_run(ap.get_run(run["id"]))
    done = ap.get_run(run["id"])
    # the cause is kept next to the outcome; it used to be overwritten by "no topics"
    assert done["status"] == "DONE" and "限时内" in done["note"] and "没有可写的新选题" in done["note"]

    found = {a["kind"]: a for a in health.alerts(now=FIVE + 6 * 3600)}
    assert found["batch_empty"]["title"].endswith("周计划批次一篇也没写") and "限时内" in found["batch_empty"]["detail"]
    loop = found["job_restarted"]
    assert "monitoring/ai_flash_discovery" in loop["title"] and "70 次" in loop["title"]
    assert "autopilot" in loop["detail"] and "234 次搜索" in loop["detail"]
    assert loop["link"] == "/jobs/monitoring%2Fai_flash_discovery"


def test_a_failure_alerts_but_one_cancel_by_hand_and_per_script_jobs_do_not(jobs):
    history(jobs, "monitoring/ai_flash_discovery", [
        {"ts": ts("14:12:41"), "user": "", "status": "failed", "error": "LLM account has insufficient credits"},
        {"ts": ts("15:00:00"), "user": "admin", "status": "cancelled"},
    ])
    history(jobs, "video/flash_abc", [{"ts": ts("15:00:00"), "user": "", "status": "failed", "error": "x"}])
    found = health.alerts(now=FIVE + 12 * 3600)
    assert [a["kind"] for a in found] == ["job_failed"]
    assert "insufficient credits" in found[0]["detail"]
    assert health.alerts(now=FIVE + 9 * 86400) == []  # older than a week


def test_a_skipped_weekly_slot_is_recorded_and_a_late_one_alerts(jobs):
    plan = ap.save_plan("每周二早上一批", "0 5 * * 2", {"picks": [], "refresh": False}, next_fire=FIVE)
    late = health.alerts(now=FIVE + 3 * 3600)
    assert [a["kind"] for a in late] == ["plan_late"] and "每周二早上一批" in late[0]["title"]

    pilot.fire_due_plans(now=FIVE + 30 * 3600)  # more than a day late: not made up, but no longer silent
    runs = ap.list_runs()
    assert len(runs) == 1 and runs[0]["status"] == "DONE" and "错过了" in runs[0]["note"]
    assert ap.get_plan(plan["id"])["next_fire"] > FIVE + 30 * 3600
    kinds = [a["kind"] for a in health.alerts(now=FIVE + 30 * 3600 + 60)]
    assert kinds == ["batch_empty"]


def test_failed_and_held_items_and_a_batch_stuck_picking(jobs):
    stuck = ap.create_run({"picks": [], "refresh": True}, [], started_by="admin")
    ap.update_run(stuck["id"], started_at=FIVE)
    items = ap.create_run({"picks": [], "refresh": False},
                          [{"column_key": "flash", "topic": {"id": "a", "title": "涨价"}},
                           {"column_key": "flash", "topic": {"id": "b", "title": "降价"}}], started_by="admin")
    ap.update_run(items["id"], started_at=FIVE + 60)
    first, second = items["items"]
    ap.update_item(first["id"], state="FAILED", note="调研失败")
    ap.update_item(second["id"], state="HELD", note="出片失败两次")
    found = {a["kind"]: a for a in health.alerts(now=FIVE + 4 * 3600)}
    assert set(found) == {"batch_stuck", "batch_items"}
    assert "失败 1 篇" in found["batch_items"]["title"] and "等你处理 1 篇" in found["batch_items"]["title"]
    assert "涨价：调研失败" in found["batch_items"]["detail"]


def test_the_batch_sees_a_discovery_started_from_the_jobs_page(jobs, monkeypatch):
    from web.runner.registry import TaskRegistry

    registry = TaskRegistry()
    monkeypatch.setattr(registry, "is_running", lambda name: name == "monitoring/ai_flash_discovery")
    monkeypatch.setattr(TaskRegistry, "__new__", lambda cls, *a, **k: registry)
    assert pilot._job_names("monitoring/ai_flash_discovery") == {"ai_flash_discovery", "monitoring/ai_flash_discovery"}
    assert pilot._discovery_running("monitoring/ai_flash_discovery")


def test_api_list_and_acknowledge(client, jobs):
    run = morning_of_the_20th(jobs)
    ap.update_run(run["id"], status="DONE")
    _login(client, "viewer", "viewer-pass-123")
    rows = client.get("/api/video/alerts").json()
    assert {r["kind"] for r in rows} >= {"batch_empty", "job_restarted"}
    key = next(r["key"] for r in rows if r["kind"] == "job_restarted")
    assert client.post("/api/video/alerts/ack", json={"key": key}, headers=_csrf(client)).status_code == 403
    _login(client)
    assert client.post("/api/video/alerts/ack", json={"key": key}, headers=_csrf(client)).status_code == 200
    assert key not in {r["key"] for r in client.get("/api/video/alerts").json()}
