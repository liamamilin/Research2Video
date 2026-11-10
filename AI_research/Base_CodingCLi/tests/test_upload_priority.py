"""Queued uploads go out by priority (11-10): hand-queued first, then the hottest topic, then the oldest."""

from __future__ import annotations

from core.video import store
from tests.test_video_scripts import video_env  # noqa: F401
from web.services import platform_manager as pm


def upload(uid, script_id, created, automatic=True):
    return {"id": uid, "script_id": script_id, "created_at": created, "payload": {"automatic": automatic}}


def test_hand_queued_then_hottest_then_oldest(monkeypatch):
    monkeypatch.setattr(pm, "heat_of", lambda ids: {"hot": 900000, "warm": 30000})
    queue = [upload("cold-old", "cold", 1), upload("warm", "warm", 2), upload("hand", "cold", 5, automatic=False),
             upload("hot", "hot", 9), upload("cold-new", "cold2", 3)]
    assert [u["id"] for u in pm.by_priority(queue)] == ["hand", "hot", "warm", "cold-old", "cold-new"]
    assert pm.by_priority([]) == []


def test_heat_is_read_from_the_topic_the_script_was_picked_with(video_env):
    hot = store.create_script("flash", {"id": "r/F1", "title": "豆包", "heat": {"keyword": "豆包", "score": 2700000}})
    angle = store.create_script("flash", {"id": "r/F1~money", "title": "豆包", "angle": "money",
                                          "heat": {"keyword": "豆包", "score": 2700000}})
    plain = store.create_script("flash", {"id": "r/F2", "title": "插画讲解"})
    assert pm.heat_of([hot["id"], angle["id"], plain["id"], "missing"]) == {
        hot["id"]: 2700000, angle["id"]: 2700000, plain["id"]: 0}
