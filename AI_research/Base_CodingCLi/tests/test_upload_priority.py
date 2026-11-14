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


def test_an_extra_angle_waits_for_its_item_s_video_then_follows_it():
    topics = {"base": {"heat": 900000, "topic_id": "r/F1", "base_id": ""},
              "money": {"heat": 900000, "topic_id": "r/F1~money", "base_id": "r/F1"},
              "other": {"heat": 0, "topic_id": "r/F2", "base_id": ""}}
    angle = {"script_id": "money", "status": "QUEUED", "payload": {}}
    queued_base = {"script_id": "base", "status": "QUEUED", "payload": {}}
    timed_base = {"script_id": "base", "status": "SUBMITTED", "payload": {"dtime": 2000}}
    assert pm.base_pending(angle, [queued_base, angle], topics, now=1000)
    assert pm.base_pending(angle, [timed_base, angle], topics, now=1000)  # scheduled for the evening peak
    assert not pm.base_pending(angle, [timed_base, angle], topics, now=2001)  # public now: the angle may follow
    assert not pm.base_pending(angle, [angle], topics, now=1000)  # no video of the item queued: nothing to wait for
    assert not pm.base_pending({"script_id": "other", "status": "QUEUED", "payload": {}}, [queued_base], topics, 1000)


def test_the_next_episode_waits_for_the_one_that_promised_it():
    topics = {"ep1": {"heat": 0, "topic_id": "r/F1", "base_id": ""}, "ep2": {"heat": 0, "topic_id": "r/F2", "base_id": "r/F1", "after_id": "r/F1"}}
    ep2 = {"script_id": "ep2", "status": "QUEUED", "payload": {}}
    assert pm.base_pending(ep2, [{"script_id": "ep1", "status": "QUEUED", "payload": {}}, ep2], topics, now=1000)
    assert pm.base_pending(ep2, [ep2], topics, now=1000)  # not produced or failed: still wait
    assert pm.base_pending(ep2, [{"script_id": "ep1", "status": "SUBMITTED", "payload": {}}, ep2], topics, now=1000)
    assert not pm.base_pending(ep2, [ep2], topics, now=1000, published={"r/F1"})


def test_cancelled_episode_is_skipped_but_preceding_unpublished_episode_still_matters():
    topics = {'c': {'base_id': 'B', 'after_id': 'B'}}
    c = {'script_id': 'c'}
    assert pm.base_pending(c, [], topics, 1000, cancelled={'B': 'A'})
    assert not pm.base_pending(c, [], topics, 1000, published={'A'}, cancelled={'B': 'A'})
    assert not pm.base_pending(c, [], topics, 1000, cancelled={'B': ''})


def test_hot_flash_is_timely_but_money_and_comparison_keep_peak_eligibility(video_env):
    flash = store.create_script('flash', {'id': 'news', 'heat': {'score': 900000}})
    money = store.create_script('flash', {'id': 'money', 'angle': 'money', 'heat': {'score': 900000}})
    compare = store.create_script('chatgpt_acquisition', {'id': 'comparison', 'heat': {'score': 900000}})
    data = pm.topics_of([s['id'] for s in (flash, money, compare)])
    assert data[flash['id']]['timely']
    assert not data[money['id']]['timely'] and not data[compare['id']]['timely']


def test_series_publication_uses_stable_slot_and_current_account(video_env):
    row = store.create_script('flash', {'id': 'replacement', 'episode_id': 'slot-B'})
    published = store.add_publication(row['id'], url='https://www.bilibili.com/video/BV1234567890',
                                      published_on='2026-11-11', platform='bilibili', account_uid='one')
    assert pm.published_episodes('one') == {'slot-B'}
    assert pm.published_episodes('two') == set()
    store.delete_publication(published['id'])
    assert pm.published_episodes('one') == set()


def test_a_retried_cancelled_slot_is_no_longer_skipped(video_env):
    from core.video import autopilot_store as ap
    item = {'column_key': 'flash', 'topic': {'id': 'old-B', 'episode_id': 'B', 'after_id': 'A'}}
    old = ap.create_run({}, [item])
    ap.update_item(old['items'][0]['id'], state='CANCELLED')
    assert pm.cancelled_episodes() == {'B': 'A'}
    ap.create_run({}, [{**item, 'topic': {**item['topic'], 'id': 'retry-B'}}])
    assert pm.cancelled_episodes() == {}
