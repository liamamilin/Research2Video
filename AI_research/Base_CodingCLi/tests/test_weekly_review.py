import json
from datetime import datetime

from core.platform_analytics_store import AnalyticsStore
from core.telemetry import weekly_review as wr

H = 3600
MONDAY = datetime(2026, 10, 26).timestamp()


def _video(bvid, pub, h24, **extra):
    """A video whose plays reach ``h24`` a day after ``pub``, read every half hour."""
    snaps = [(pub + i * 1800, round(h24 * i / 48)) for i in range(0, 49)]
    return {"bvid": bvid, "title": extra.pop("title", bvid), "pubdate": pub, "duration": extra.pop("duration", 200),
            "view": h24, "snapshots": snaps, "form": "单篇", "matrix": "AI", "column": "C", "structure": "", **extra}


def test_views_at_interpolates_between_snapshots():
    assert wr.views_at([(0, 0), (24 * H - 900, 90), (24 * H + 900, 110)], 0, 24, now=30 * H) == ("ok", 100)


def test_views_at_says_pending_before_the_window_closes():
    assert wr.views_at([(0, 0), (10 * H, 50)], 0, 24, now=20 * H) == ("pending", None)


def test_views_at_says_gap_when_collection_was_off():
    # collection stopped at 10h and came back two days later
    assert wr.views_at([(0, 0), (10 * H, 50), (60 * H, 300)], 0, 24, now=70 * H) == ("gap", None)
    assert wr.views_at([(0, 0), (10 * H, 50)], 0, 24, now=70 * H) == ("gap", None)


def test_views_at_uses_first_reading_when_collection_resumed_just_after():
    assert wr.views_at([(0, 0), (25 * H, 120)], 0, 24, now=30 * H) == ("ok", 120)


def test_structure_reads_the_named_structure():
    assert wr.structure("同题盲测：几家答案直接能对比") == "B 同题盲测"
    assert wr.structure("用误区纠正，再补一张换用成本表") == "F 误区纠正"
    assert wr.structure("随便讲讲") == ""


def test_week_start_is_local_monday():
    assert wr.week_start(datetime(2026, 11, 1, 23, 59).timestamp()) == MONDAY


def test_review_flags_only_groups_with_enough_videos():
    pub = MONDAY + 12 * H
    videos = [_video(f"a{i}", pub + i * H, 100, matrix="AI") for i in range(3)]
    videos += [_video(f"b{i}", pub + i * H, 10, matrix="经济") for i in range(3)]
    videos += [_video("c0", pub, 1000, matrix="少数")]  # one hit in a group too small to judge
    review = wr.review(videos, MONDAY, now=MONDAY + 7 * 86400)
    matrix = next(d for d in review["dimensions"] if d["key"] == "matrix")
    flags = {g["value"]: g["flag"] for g in matrix["groups"]}
    assert review["summary"]["median_24h"] == 100
    assert flags == {"AI": "", "经济": "low", "少数": ""}
    assert review["top"][0]["bvid"] == "c0"
    assert any("经济" in f and "明显偏低" in f for f in review["findings"])
    assert review["suggestions"] == []  # one low week is not a trend


def test_review_suggests_after_two_low_weeks_and_counts_gaps():
    videos = []
    for week in (MONDAY - 7 * 86400, MONDAY):
        pub = week + 12 * H
        videos += [_video(f"{week}a{i}", pub + i * H, 100, matrix="AI") for i in range(3)]
        videos += [_video(f"{week}b{i}", pub + i * H, 10, matrix="经济") for i in range(3)]
    gap = _video("gap", MONDAY + 2 * 86400, 50)
    gap["snapshots"] = gap["snapshots"][:10]
    videos.append(gap)
    review = wr.review(videos, MONDAY, now=MONDAY + 7 * 86400)
    assert review["summary"]["gap"] == 1 and review["summary"]["previous_videos"] == 6
    assert any("经济" in s and "减少或暂停" in s for s in review["suggestions"])
    assert any("1 条视频" in f and "没有采集快照" in f for f in review["findings"])


def test_review_tags_titles_and_keeps_structure_only_when_present():
    pub = MONDAY + 12 * H
    videos = [_video("p", pub, 100, title="ChatGPT Plus 20 美元值不值", structure="A 价格反差与分任务对决"),
              _video("q", pub, 5, title="海底的一个坑")]
    review = wr.review(videos, MONDAY, now=MONDAY + 7 * 86400)
    items = {i["bvid"]: i for i in review["videos"]}
    assert (items["p"]["product"], items["p"]["money"]) == ("是", "是")
    assert (items["q"]["product"], items["q"]["money"]) == ("否", "否")
    keys = [d["key"] for d in review["dimensions"]]
    assert "structure" in keys
    assert "structure" not in [d["key"] for d in wr.review(videos[1:], MONDAY, now=MONDAY + 7 * 86400)["dimensions"]]


def test_view_series_reads_many_videos_at_once(tmp_path):
    store = AnalyticsStore(str(tmp_path))
    with store.connect(write=True) as db:
        for bvid, at, view in (("BV1", 10, 1), ("BV1", 20, 3), ("BV2", 15, 7), ("BV3", 15, 9), ("BV1", 5, 0)):
            db.execute("INSERT INTO analytics_snapshots VALUES ('u',?,?,?,'catalog')", (bvid, at, json.dumps({"view": view})))
    assert store.view_series("u", ["BV1", "BV2"], since=8) == {"BV1": [(10, 1), (20, 3)], "BV2": [(15, 7)]}
