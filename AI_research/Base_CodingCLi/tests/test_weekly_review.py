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


def _saved_review(start, findings=("一条结论",)):
    pub = start + 12 * H
    review = wr.review([_video("v1", pub, 40, title="ChatGPT | 定价"), _video("v2", pub, 4)], start, now=start + 8 * 86400)
    review["findings"] = list(findings)
    return review


def test_store_keeps_report_and_numbers_and_never_rewrites(tmp_path):
    from core.weekly_review_store import ReviewStore
    store = ReviewStore(str(tmp_path))
    assert store.save(_saved_review(MONDAY), "24h", now=MONDAY + 8 * 86400)
    assert not store.save(_saved_review(MONDAY, findings=("改过的",)), "24h", now=MONDAY + 9 * 86400)
    kept = store.get(MONDAY, "24h")
    assert kept["findings"] == ["一条结论"] and kept["saved_at"] == MONDAY + 8 * 86400
    assert {v["bvid"]: v["h24"] for v in kept["videos"]} == {"v1": 40, "v2": 4}
    report = (tmp_path / "weekly_reviews" / "2026-10-26_24h.md").read_text(encoding="utf-8")
    assert report.startswith("# 每周复盘 2026-10-26 至 2026-11-01（24 小时定稿）")
    assert "- 一条结论" in report and "ChatGPT ｜ 定价" in report  # a pipe in a title does not break the table
    assert store.saved() == [{"start": MONDAY, "stage": "24h", "saved_at": MONDAY + 8 * 86400, "summary": kept["summary"],
                              "findings": 1, "suggestions": 0}]


def test_archive_saves_each_stage_once_it_is_final(tmp_path, monkeypatch):
    from web.services import weekly_review as service
    pub = MONDAY + 12 * H
    gathered = []
    monkeypatch.setattr(service.platform_analytics, "current", lambda: (None, "u"))
    monkeypatch.setattr(service, "_gather", lambda config_dir, collector, uid: gathered.append(1) or [_video("v1", pub, 40)])
    monkeypatch.setattr(service, "_weeks_since", lambda now: [w for w in (MONDAY, MONDAY - 7 * 86400) if w < wr.week_start(now)])

    # the week before is final at both stages; this week's Sunday videos are not a day old yet
    assert service.archive_due("cfg", str(tmp_path), now=MONDAY + 7 * 86400 + H) == [(MONDAY - 7 * 86400, "24h"), (MONDAY - 7 * 86400, "7d")]
    assert service.archive_due("cfg", str(tmp_path), now=MONDAY + 8 * 86400) == [(MONDAY, "24h")]
    calls = len(gathered)
    assert service.archive_due("cfg", str(tmp_path), now=MONDAY + 9 * 86400) == []
    assert len(gathered) == calls  # nothing due: the videos are not even read
    assert service.archive_due("cfg", str(tmp_path), now=MONDAY + 14 * 86400) == [(MONDAY, "7d")]
    empty = service.ReviewStore(str(tmp_path)).get(MONDAY - 7 * 86400, "24h")
    assert empty["summary"]["videos"] == 0  # a week with nothing published is kept as such


def test_collection_survives_a_review_that_cannot_be_saved(monkeypatch, caplog):
    from web.services import weekly_review as service
    monkeypatch.setattr(service, "archive_due", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    service.archive_after_collection("unused")
    assert "could not be saved" in caplog.text


def test_watch_numbers_sit_next_to_plays_and_short_videos_stand_out():
    pub = MONDAY + 12 * H
    short = [_video(f"s{i}", pub, 10, duration=100, watch={"completion": 60.0, "avg_seconds": 70, "exit3s": 20.0}) for i in range(3)]
    long = [_video(f"l{i}", pub, 10, duration=700, watch={"completion": 6.0, "avg_seconds": 60, "exit3s": 50.0}) for i in range(4)]
    unread = _video("u", pub, 10)
    review = wr.review(short + long + [unread], MONDAY, now=MONDAY + 7 * 86400)
    watch = review["summary"]["watch"]
    assert watch["watch_measured"] == 7 and watch["completion"] == 6.0 and watch["exit3s"] == 50.0
    bands = {g["value"]: g for g in next(d for d in review["dimensions"] if d["key"] == "duration")["groups"]}
    assert bands["2 分钟内"]["completion"] == 60.0 and bands["2 分钟内"]["watch_measured"] == 3
    assert bands["8 分钟以上"]["avg_seconds"] == 60
    assert not [f for f in review["findings"] if "完播率" in f]  # length alone explains completion
    by_column = [_video(f"c{i}", pub, 10, column="快讯" if i < 3 else "T05", duration=300,
                        watch={"completion": 40.0 if i < 3 else 8.0}) for i in range(7)]
    findings = wr.review(by_column, MONDAY, now=MONDAY + 7 * 86400)["findings"]
    assert "栏目「快讯」完播率中位数 40%，整体 8%，明显偏高（3 条）。" in findings
    row = next(v for v in review["videos"] if v["bvid"] == "s0")
    assert row["watched_share"] == 70.0 and next(v for v in review["videos"] if v["bvid"] == "u")["completion"] is None


def test_report_shows_watch_numbers_and_old_reviews_still_render():
    from core.weekly_review_store import markdown
    pub = MONDAY + 12 * H
    review = wr.review([_video(f"v{i}", pub, 10, watch={"completion": 9.75, "avg_seconds": 73, "exit3s": 62.5,
                                                        "cover_pass": 64.69}) for i in range(3)], MONDAY, now=MONDAY + 8 * 86400)
    report = markdown(review, "24h")
    assert "完播率中位数 9.75%，平均观看 73 秒，3 秒内退出 62.5%，封面点击超过同类 64.69%" in report
    assert "| 9.75% | 73 秒 | 62.5% |" in report
    old = _saved_review(MONDAY)
    old["summary"].pop("watch")
    for dimension in old["dimensions"]:
        for group in dimension["groups"]:
            for key in ("watch_measured", "completion", "avg_seconds", "exit3s", "cover_pass"):
                group.pop(key)
    assert "观看情况" not in markdown(old, "24h")


def test_review_compares_cover_styles_by_plays_and_cover_clicks():
    pub = MONDAY + 12 * H
    videos = ([_video(f"n{i}", pub, 60, cover="数字封面", watch={"cover_pass": 80.0}) for i in range(3)] +
              [_video(f"t{i}", pub, 10, cover="标题封面", watch={"cover_pass": 50.0}) for i in range(3)] +
              [_video("x", pub, 10)])
    review = wr.review(videos, MONDAY, now=MONDAY + 7 * 86400)
    groups = {g["value"]: g for g in next(d for d in review["dimensions"] if d["key"] == "cover")["groups"]}
    assert groups["数字封面"]["median"] == 60 and groups["数字封面"]["cover_pass"] == 80.0
    assert groups["标题封面"]["cover_pass"] == 50.0 and groups["未标注"]["videos"] == 1
    assert any(f.startswith("封面样式「数字封面」24 小时中位数 60") for f in review["findings"])


def test_cover_style_is_read_from_the_uploaded_cover(tmp_path):
    import sqlite3
    from web.services import publish_cover as pc
    number, title_v2, old = tmp_path / "r1-abc-v2.jpg", tmp_path / "r2-def-v2.jpg", tmp_path / "r3-ghi.jpg"
    (tmp_path / "r1-abc-copy.json").write_text(json.dumps({"copy": {"big": "差十倍"}}), encoding="utf-8")
    (tmp_path / "r2-def-copy.json").write_text(json.dumps({"copy": None, "reason": "超时"}), encoding="utf-8")
    assert [pc.style(str(p)) for p in (number, title_v2, old)] == [pc.NUMBER_LED, pc.TITLE, pc.TITLE]
    assert pc.style("") == pc.FRAME
    with sqlite3.connect(tmp_path / "platforms.db") as db:
        db.execute("CREATE TABLE uploads (render_id TEXT, payload TEXT, status TEXT)")
        db.executemany("INSERT INTO uploads VALUES (?,?,?)", [("r1", json.dumps({"cover": str(number)}), "PUBLISHED"),
                                                              ("r4", json.dumps({"cover": ""}), "PUBLISHED"),
                                                              ("r5", json.dumps({"cover": str(number)}), "FAILED")])
    assert pc.upload_styles(str(tmp_path)) == {"r1": pc.NUMBER_LED, "r4": pc.FRAME}


def test_review_tracks_delivered_materials_and_frozen_programme_separately_from_requests():
    from core.video.presentation import snapshot
    version = {'meta': {'prompt_version': 13, 'creative_direction': '三维度比较'}}
    request = {'image_materials': [{}, {}, {}]}
    unseen = snapshot(version, request, None)
    assert unseen['requested_images'] == 3 and unseen['used_images'] is None
    assert unseen['material_usage'] == '未读取成片分镜'
    board = {'scenes': [
        {'component': 'object_showcase', 'props': {'image': {'image_asset_id': 'a'}}},
        {'component': 'material_collage', 'props': {'images': [{'image_asset_id': 'a'}, {'image_asset_id': 'b'}]}},
        {'component': 'illustration_scene', 'props': {'layout': 'diagram', 'chapter_index': 1}},
        {'component': 'illustration_scene', 'props': {'layout': 'diagram', 'chapter_index': 1}}]}
    used = snapshot(version, request, board)
    assert used['used_images'] == 2 and used['material_scenes'] == 2 and used['diagram_chapters'] == 1
    assert used['programme'] == unseen['programme']
    assert snapshot({'meta': {**version['meta'], 'creative_direction': '新写法'}}, request, board)['programme'] != used['programme']
    video = _video('visual', MONDAY + 12 * H, 100, programme=used['programme'],
                   material_usage=used['material_usage'], presentation=used)
    review = wr.review([video], MONDAY, now=MONDAY + 7 * 86400)
    assert review['videos'][0]['presentation'] == used
    assert {'programme', 'material_usage'} <= {d['key'] for d in review['dimensions']}
