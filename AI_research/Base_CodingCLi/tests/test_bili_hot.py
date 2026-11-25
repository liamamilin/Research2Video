"""B站 hot words as source material (core/bili_hot.py): chosen by the program, never stopping the Job."""

from __future__ import annotations

import datetime

from core import bili_hot, sources
from core.search import SearchResult
from core.video import heat


class FakeSearch:
    def __init__(self, trending=None, plays=None, refuse_trending=False):
        self._trending, self.plays, self.refuse_trending, self.calls = trending or [], plays or {}, refuse_trending, []

    def trending(self):
        if self.refuse_trending:
            raise heat.Unavailable("412")
        return self._trending

    def videos(self, keyword, begin, end):
        self.calls.append(keyword)
        return [{"title": f"{keyword} 实测 {i}", "play": p, "typename": "计算机技术", "duration": "5:00", "bvid": f"BV{i}"}
                for i, p in enumerate(self.plays.get(keyword, []))]


class FakeWeb:
    def __init__(self, fail=False):
        self.fail, self.queries = fail, []

    def search(self, queries, objective=""):
        self.queries.append(queries[0])
        if self.fail:
            raise RuntimeError("quota")
        return [SearchResult(title=f"{queries[0]} {i}", url=f"https://news.example/{len(self.queries)}/{i}",
                             publish_date="2026-11-10") for i in range(5)]


def state(tmp_path, search):
    return heat.Heat(str(tmp_path), search=search, clock=lambda: datetime.datetime(2026, 11, 10, 7).timestamp(),
                     sleeper=lambda s: None)


def test_choose_takes_ai_words_of_the_hot_list_then_the_hottest_watch_words_over_the_bar():
    trending = ["BLG夺冠", "DeepSeek改架构数学能力提升", "让九个AI大模型玩造桥", "王者新赛季", "豆包工作取消额度",
                "AI长片", "GPT-6.2 Astra 灰测", "Kimi新模型"]
    scored = {"豆包": {"score": 2_700_000}, "Cursor": {"score": 5000}, "Codex": {"score": 570_000},
              "Kimi": {"score": 190_000}, "Grok": {"score": 105_000}}
    chosen = bili_hot.choose(trending, scored)
    # named products only: the AI films and contests of 热搜 are no product news; three at most
    assert [c["word"] for c in chosen] == ["DeepSeek改架构数学能力提升", "豆包工作取消额度", "GPT-6.2 Astra 灰测",
                                           "豆包", "Codex", "Kimi"]
    assert chosen[0]["why"] == "B站热搜" and chosen[3]["heat"]["score"] == 2_700_000


def test_each_word_is_one_bilibili_item_then_its_latest_news(tmp_path):
    search = FakeSearch(trending=["DeepSeek改架构"], plays={"豆包": [900_000, 800_000], "Cursor": [10]})
    web = FakeWeb()
    got = bili_hot.fetch("豆包，Cursor", search_client=web, heat_state=state(tmp_path, search))
    titles = [r.title for r in got]
    assert titles[0] == "B站热词（2026-11-10）：DeepSeek改架构" and titles[4] == "B站热词（2026-11-10）：豆包"
    assert len(got) == 8 and all("Cursor" not in t for t in titles)  # Cursor is under the bar
    doubao = got[4]
    assert doubao.provider == "bili_hot" and doubao.publish_date == "2026-11-10"
    assert "1700000" in doubao.excerpts[0] and "《豆包 实测 0》900000 次" in doubao.excerpts[0]
    assert doubao.url.endswith("#2026-11-10")  # a new item every day, not an unchanged page
    assert web.queries == ["DeepSeek改架构 最新消息", "豆包 最新消息"]
    assert search.calls == ["豆包", "Cursor"]


def test_trouble_gives_what_was_found_and_never_raises(tmp_path):
    search = FakeSearch(plays={"豆包": [900_000]}, refuse_trending=True)
    got = bili_hot.fetch("豆包", search_client=FakeWeb(fail=True), heat_state=state(tmp_path, search))
    assert [r.title for r in got] == ["B站热词（2026-11-10）：豆包"]
    assert bili_hot.fetch("豆包", search_client=None, heat_state=state(tmp_path, FakeSearch(refuse_trending=True)))


def test_the_spec_is_a_registered_source_kind_and_goes_through_fetch_source(monkeypatch):
    seen = {}
    monkeypatch.setattr(bili_hot, "fetch", lambda target, limit=8, timeout=20, search_client=None:
                        seen.update(target=target, client=search_client) or [])
    client = object()
    assert sources.fetch_source("bili_hot:豆包,Kimi", search_client=client) == []
    assert seen == {"target": "豆包,Kimi", "client": client}
    registry = sources.load_registry("config")
    assert registry["bili_hot_ai"]["spec"].startswith("bili_hot:")
