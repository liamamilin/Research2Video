"""Extra angles on the hottest item (core/video/angles.py): siblings never block each other, a repeat angle does."""

from __future__ import annotations

import datetime
from types import SimpleNamespace

import pytest

from core.video import angles, content_guard as guard, heat, production, store, story_pool as pool
from tests.test_video_content_guard import guard_env, sections  # noqa: F401
from tests.test_video_scripts import video_env  # noqa: F401
from tests.test_video_story_pool import create_pool, event, fresh


def test_an_angle_needs_its_markers_in_the_item_text():
    assert angles.supported("豆包工作个人版取消 5 小时额度，每月 39 元；在设置里打开") == ["money", "howto"]
    assert angles.supported("Plus 每月 20 美元") == ["money"]
    assert angles.supported("在 Codex 里输入 /review 命令") == ["howto"]
    assert angles.supported("豆包工作×中国移动联名版官宣") == []


def test_a_variant_is_the_item_with_its_own_id_angle_and_field():
    topic = {"id": "2026-11-10/F1@r1", "title": "豆包工作取消额度", "fields": {"一句话": "x"},
             "_story_fallbacks": [{"id": "other"}]}
    money, howto = angles.variants(topic, "每月 39 元，设置里打开")
    assert money["id"] == "2026-11-10/F1@r1~money" and money["base_id"] == topic["id"] and money["angle"] == "money"
    assert money["fields"] == {"一句话": "x", "讲述角度": "算账值不值"} and "_story_fallbacks" not in money
    assert howto["angle"] == "howto" and topic["fields"] == {"一句话": "x"}


def _angled(root, angle):
    row = fresh(root)
    store.update_script(row["id"], topic={**row["topic"], "angle": angle})
    return store.get_script(row["id"])


def test_the_item_and_its_angles_claim_the_same_event_and_only_a_same_angle_repeat_is_refused(guard_env, monkeypatch):
    candidates = create_pool(monkeypatch, [event()])
    base, money, howto, money_again = (fresh(guard_env), _angled(guard_env, "money"),
                                       _angled(guard_env, "howto"), _angled(guard_env, "money"))
    told, _ = pool.take(base["id"], candidates, [])
    by_money, _ = pool.take(money["id"], candidates, [])
    by_howto, _ = pool.take(howto["id"], candidates, [])
    assert by_money["title"] == by_howto["title"] == told["title"]
    assert by_money["event_key"] == told["event_key"] + "#money" and by_money["angle"] == "money"
    assert "angle" not in told
    with pytest.raises(pool.NoCandidate):
        pool.take(money_again["id"], candidates, [])  # 算账 of this event is already being written
    assert "算账值不值" in pool.brief(by_money) and "算账值不值" not in pool.brief(told)
    # the gate after writing sees the same thing: siblings are no duplicates of each other
    for row, story in ((base, told), (money, by_money), (howto, by_howto)):
        script = {**store.get_script(row["id"]), "topic": {**store.get_script(row["id"])["topic"], "story": story}}
        assert pool.duplicate(story, pool.reservations(story["scope"], row["id"])) == []
        guard.check(script, sections(), str(guard_env / "config"), str(guard_env / "jobs"))


def test_angle_min_is_validated_and_needs_the_heat_bar():
    assert production._selection({"heat_min": 1, "angle_min": 5}) == ({"heat_min": 1, "angle_min": 5}, "")
    assert production._selection({"angle_min": 5})[1]
    assert production._selection({"heat_min": 1, "angle_min": -5})[1]
    assert production._selection({"heat_min": 1}) == ({"heat_min": 1}, "")


@pytest.fixture
def flash_report(video_env, monkeypatch):
    from core.video import topics as video_topics
    from tests.test_video_flash import DISCOVERY

    folder = video_env / "output/flash"
    folder.mkdir(parents=True)
    (folder / "2026-10-19_ai_flash_discovery.md").write_text(DISCOVERY, encoding="utf-8")
    monkeypatch.setattr(video_topics, "_today", lambda: datetime.date(2026, 10, 19))
    return video_env


def _ranked(monkeypatch, scores, angle_min=500000):
    """Heat that scores items in report order with ``scores`` (None: unscored), and a production with the bars."""
    monkeypatch.setattr(production, "get", lambda c, j, key: SimpleNamespace(selection={"heat_min": 1, "angle_min": angle_min}))

    class FakeHeat:
        def __init__(self, state_dir):
            pass

        def rank(self, candidates, minimum, llm):
            out = []
            for candidate, value in zip(candidates, scores):
                out.append(candidate if value is None else {**candidate, "heat": {"keyword": "k", "score": value, "videos": 1}})
            return out, [], ""

    monkeypatch.setattr(heat, "Heat", FakeHeat)


SPEC = {"picks": [{"column": "flash", "all": True, "source": "flash", "production": "hot"}]}


def test_the_hottest_item_over_the_bar_gets_the_angles_its_text_supports_right_after_it(flash_report, monkeypatch):
    from web.services import video_autopilot as pilot

    _ranked(monkeypatch, [900000, 600000])
    monkeypatch.setattr(pilot, "_item_text", lambda topic: "每月 20 美元，在设置里打开")
    notes = []
    ids = [i["topic"]["id"] for i in pilot.pick_topics(SPEC, notes)]
    first = ids[0]
    assert ids[1:3] == [first + "~money", first + "~howto"]
    assert sum("~" in i for i in ids) == 2  # one item per batch, not every hot one
    assert any("加做 算账值不值、怎么上手" in n for n in notes)
    for item in pilot.pick_topics(SPEC, []):
        store.create_script("flash", item["topic"])
    assert [i["topic"]["id"] for i in pilot.pick_topics(SPEC, [])] == []  # a rerun adds nothing again


def test_no_angles_below_the_bar_without_supporting_text_or_without_a_score(flash_report, monkeypatch):
    from web.services import video_autopilot as pilot

    monkeypatch.setattr(pilot, "_item_text", lambda topic: "每月 20 美元，在设置里打开")
    _ranked(monkeypatch, [400000, 300000])
    assert not any("~" in i["topic"]["id"] for i in pilot.pick_topics(SPEC, []))
    _ranked(monkeypatch, [None, None])
    assert not any("~" in i["topic"]["id"] for i in pilot.pick_topics(SPEC, []))
    _ranked(monkeypatch, [900000])
    monkeypatch.setattr(pilot, "_item_text", lambda topic: "联名版官宣")
    notes = []
    assert not any("~" in i["topic"]["id"] for i in pilot.pick_topics(SPEC, notes))
    assert any("只做一条" in n for n in notes)
