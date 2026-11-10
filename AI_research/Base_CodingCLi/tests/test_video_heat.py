"""Topic heat (core/video/heat.py): hot items first, cold ones not made, and B站 trouble never stops a video."""

from __future__ import annotations

import datetime
from types import SimpleNamespace

import pytest
import yaml

from core.video import heat
from tests.test_video_scripts import video_env  # noqa: F401


def row(title, play, typename="计算机技术", duration="5:00"):
    return {"title": title, "play": play, "typename": typename, "duration": duration}


class FakeSearch:
    def __init__(self, plays=None, refuse=False):
        self.plays, self.refuse, self.calls = plays or {}, refuse, []

    def videos(self, keyword, begin, end):
        self.calls.append(keyword)
        assert end - begin == heat.WINDOW_DAYS * 86400
        if self.refuse:
            raise heat.Unavailable("B站拒绝了搜索（-412）")
        return [row(f"{keyword} 实测 {i}", p) for i, p in enumerate(self.plays.get(keyword, []))]


def make(tmp_path, search, **kw):
    clock = kw.pop("clock", lambda: datetime.datetime(2026, 11, 10, 9).timestamp())
    return heat.Heat(str(tmp_path), search=search, clock=clock, sleeper=lambda s: None, **kw)


def cands(*titles):
    return [{"id": f"r/F{i}", "title": t, "fields": {}} for i, t in enumerate(titles, 1)]


@pytest.fixture
def words(monkeypatch):
    """The model's keyword per id, without calling a model."""
    table = {}
    monkeypatch.setattr(heat.Heat, "keywords", lambda self, candidates, llm: {c["id"]: table.get(c["id"], "") for c in candidates})
    return table


def test_score_counts_only_videos_about_every_word_of_the_product_and_not_games_or_clips():
    rows = [row("<em>GPT</em>-6.2-<em>Astra</em>实测", 100), row("Astra 新游戏", 900, typename="单机游戏"),
            row("GPT Astra 一分钟", 800, duration="0:59"), row("GPT 闲聊", 700)] + [row(f"GPT Astra {i}", 10) for i in range(6)]
    got = heat.score(rows, "GPT Astra")
    assert (got["score"], got["videos"]) == (140, 7)  # top 5: 100 + 4 × 10
    assert got["top"][0] == {"title": "GPT-6.2-Astra实测", "bvid": "", "play": 100} and len(got["top"]) == 3
    assert heat.score(rows, "") == {"score": 0, "videos": 0, "top": []}


def test_hot_items_go_first_and_cold_ones_are_dropped_with_the_reason(tmp_path, words):
    search = FakeSearch({"Codex": [300000, 200000], "nanobot": [42], "Claude Code": [50000]})
    words.update({"r/F1": "nanobot", "r/F2": "Claude Code", "r/F3": "Codex", "r/F4": ""})
    kept, dropped, note = make(tmp_path, search).rank(cands("nanobot 修了", "Claude Code 更新", "Codex 收购", "其他"), 20000, None)
    assert [c["id"] for c in kept] == ["r/F3", "r/F2"]
    assert kept[0]["heat"] == {"keyword": "Codex", "score": 500000, "videos": 2}
    assert {c["id"] for c in dropped} == {"r/F1", "r/F4"}
    assert "少做 2 条" in note and "nanobot 42" in note and "无产品名" in note


def test_when_bilibili_refuses_every_item_is_kept_in_order_and_the_day_stops_searching(tmp_path, words):
    search = FakeSearch(refuse=True)
    words.update({"r/F1": "A", "r/F2": "B"})
    first = make(tmp_path, search)
    kept, dropped, note = first.rank(cands("a", "b"), 20000, None)
    assert [c["id"] for c in kept] == ["r/F1", "r/F2"] and dropped == []
    assert "-412" in note and "按原顺序保留" in note
    assert search.calls == ["A"]
    again = make(tmp_path, search).rank(cands("a", "b"), 20000, None)
    assert search.calls == ["A"] and [c["id"] for c in again[0]] == ["r/F1", "r/F2"]  # blocked for the day


def test_a_keyword_is_searched_once_a_day_and_the_daily_cap_holds(tmp_path, words):
    search = FakeSearch({"A": [30000], "B": [30000], "C": [30000]})
    words.update({"r/F1": "A", "r/F2": "B", "r/F3": "C"})
    make(tmp_path, search, daily=2).rank(cands("a", "b", "c"), 20000, None)
    assert search.calls == ["A", "B"]
    kept, _, note = make(tmp_path, search, daily=2).rank(cands("a", "b", "c"), 20000, None)
    assert search.calls == ["A", "B"]  # cached, and C waits for tomorrow
    assert [c["id"] for c in kept] == ["r/F1", "r/F2", "r/F3"] and "次数已用完" in note
    tomorrow = make(tmp_path, search, daily=2, clock=lambda: datetime.datetime(2026, 11, 11, 9).timestamp())
    tomorrow.rank(cands("a", "b", "c")[2:], 20000, None)
    assert search.calls[-1] == "C"


def test_the_model_names_the_product_and_its_failure_falls_back_to_the_title_head(tmp_path, monkeypatch):
    class Reply:
        content = '{"items":[{"id":"r/F1","keyword":"腾讯混元"}]}'

    class Client:
        def __init__(self, config):
            pass

        def chat(self, messages, temperature=0):
            assert "不能只写公司或总品牌" in messages[0]["content"]
            return Reply()

    import core.llm
    monkeypatch.setattr(core.llm, "LLMClient", Client)
    h = make(tmp_path, FakeSearch())
    assert h.keywords(cands("腾讯发布 Hy4 正式版", "Cursor Live Apps：一句描述"), object()) == {
        "r/F1": "腾讯混元", "r/F2": "Cursor Live Apps"}
    monkeypatch.setattr(core.llm, "LLMClient", lambda config: pytest.fail("remembered keywords need no model"))
    assert h.keywords(cands("腾讯发布 Hy4 正式版"), object()) == {"r/F1": "腾讯混元"}


def test_selection_is_validated_and_kept_when_the_form_saves(tmp_path):
    from core.video import production

    assert production._selection(None) == ({}, "")
    assert production._selection({"heat_min": 20000}) == ({"heat_min": 20000}, "")
    assert production._selection({"heat_min": -1})[1]
    assert production._selection({"heat_min": True})[1]
    assert production._selection({"other": 1})[1]
    folder = tmp_path / "production"
    folder.mkdir()
    (folder / "p.yaml").write_text(yaml.safe_dump({"job": "j", "recipe": "flash", "selection": {"heat_min": 5}}))
    production.save(str(tmp_path), "p", job="j", recipe="flash", auto=1)
    assert yaml.safe_load((folder / "p.yaml").read_text())["selection"] == {"heat_min": 5}


def test_picking_with_a_heat_bar_drops_cold_items_from_the_batch_and_its_fallbacks(video_env, monkeypatch):
    from core.video import production, topics as video_topics
    from tests.test_video_flash import DISCOVERY
    from web.services import video_autopilot as pilot

    folder = video_env / "output/flash"
    folder.mkdir(parents=True)
    (folder / "2026-10-19_ai_flash_discovery.md").write_text(DISCOVERY, encoding="utf-8")
    monkeypatch.setattr(video_topics, "_today", lambda: datetime.date(2026, 10, 19))
    real_get = production.get
    monkeypatch.setattr(production, "get", lambda c, j, key: SimpleNamespace(selection={"heat_min": 100})
                        if key == "hot" else real_get(c, j, key))
    seen = {}

    class FakeHeat:
        def __init__(self, state_dir):
            pass

        def rank(self, candidates, minimum, llm):
            seen["ids"] = [c["id"] for c in candidates]
            cold = candidates[0]
            return candidates[1:][::-1], [cold], f"热度不够，少做 1 条：{cold['title']}"

    monkeypatch.setattr(heat, "Heat", FakeHeat)
    notes = []
    picked = pilot.pick_topics({"picks": [{"column": "flash", "all": True, "source": "flash", "production": "hot"}]}, notes)
    ids = [p["topic"]["id"] for p in picked]
    assert seen["ids"][0] not in ids and ids == seen["ids"][1:][::-1]
    assert all(f["id"] != seen["ids"][0] for p in picked for f in p["topic"].get("_story_fallbacks", []))
    assert notes and "少做 1 条" in notes[0]
    plain = pilot.pick_topics({"picks": [{"column": "flash", "all": True, "source": "flash"}]})
    assert len(plain) == len(seen["ids"])  # no production, no bar


def test_a_heat_failure_keeps_the_batch_as_it_was(video_env, monkeypatch):
    from core.video import production, topics as video_topics
    from tests.test_video_flash import DISCOVERY
    from web.services import video_autopilot as pilot

    folder = video_env / "output/flash"
    folder.mkdir(parents=True)
    (folder / "2026-10-19_ai_flash_discovery.md").write_text(DISCOVERY, encoding="utf-8")
    monkeypatch.setattr(video_topics, "_today", lambda: datetime.date(2026, 10, 19))
    monkeypatch.setattr(production, "get", lambda c, j, key: SimpleNamespace(selection={"heat_min": 100}))

    class Broken:
        def __init__(self, state_dir):
            raise OSError("disk")

    monkeypatch.setattr(heat, "Heat", Broken)
    notes = []
    spec = {"picks": [{"column": "flash", "all": True, "source": "flash", "production": "hot"}]}
    assert len(pilot.pick_topics(spec, notes)) == len(pilot.pick_topics({"picks": [{"column": "flash", "all": True, "source": "flash"}]}))
    assert notes == ["热度排序出错（OSError），按原顺序"]
