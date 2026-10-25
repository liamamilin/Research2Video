"""The source card a short video ends on: what the script cites, from the report's source list."""

from __future__ import annotations

from core.video import columns as video_columns
from core.video import sources
from tests import golden_video_cells as golden

LONG = "A very long title about a pricing page that goes on well past the limit of forty characters"
REPORT = f"""# 报告

### 5. 适合快讯的讲法
1. 这一行不在来源清单里 | 一手 | 2026-10-01 | https://elsewhere.example.com/x

### 6. 来源清单
1. 定价页 | 一手（官方定价页，含限时价说明） | 2026-10-16 | https://ai.google.dev/pricing
2. {LONG}（Google 官方博客） | 一手 | 2026-10-13 | https://blog.example.com/launch/
3. 官方论坛帖 | 一手论坛（含员工回复） | 2026-10-13 / 2026-10-14 | https://forum.example.com/t/1
4. 套餐说明 | 一手 | 未标注（检索于 2026-10-19） | https://ai.google.dev/plans
5. 媒体报道 | 二手 | 2026-10-15 | https://news.example.com/a
6. 日期写错的页面 | 一手 | 2026-13-45 | https://bad.example.com/d
7. 没有网址的行 | 一手 | 2026-10-01 | 见官网
"""


def _version(*paragraphs: list[str]) -> dict:
    return {"sections": [{"title": "节", "paragraphs": [{"text": "x", "citations": c} for c in paragraphs]}]}


def test_the_source_list_is_only_the_section_headed_so():
    urls = [s["url"] for s in sources.report_sources(REPORT)]
    assert "https://elsewhere.example.com/x" not in urls
    assert urls[0] == "https://ai.google.dev/pricing" and "见官网" not in urls


def test_most_cited_first_hand_sources_come_first_and_the_card_holds_three():
    version = _version(["https://news.example.com/a", "https://ai.google.dev/pricing"],
                       ["https://ai.google.dev/pricing", "https://forum.example.com/t/1"],
                       ["https://forum.example.com/t/1", "https://blog.example.com/launch"],
                       ["https://news.example.com/a"])
    cards = sources.source_cards(version, REPORT)
    # the trailing slash in the list and none in the citation are the same page; the media report is cited
    # as often as the others but is not first-hand
    assert [c["url"] for c in cards] == ["https://ai.google.dev/pricing", "https://forum.example.com/t/1",
                                         "https://blog.example.com/launch/"]
    assert [c["date"] for c in cards] == ["2026-10-16", "2026-10-13", "2026-10-13"]


def test_a_source_without_a_publish_date_is_left_out_not_dated_wrongly():
    version = _version(["https://ai.google.dev/plans", "https://bad.example.com/d", "https://news.example.com/a"])
    assert [c["url"] for c in sources.source_cards(version, REPORT)] == ["https://news.example.com/a"]


def test_urls_that_are_not_in_the_list_are_not_shown():
    assert sources.source_cards(_version(["https://invented.example.com/p"]), REPORT) == []
    assert sources.source_cards(_version([]), REPORT) == []


def test_titles_fit_the_card():
    cards = sources.source_cards(_version(["https://blog.example.com/launch/"]), REPORT)
    title = cards[0]["title"]
    assert len(title) <= sources.TITLE_MAX and title.endswith("…") and "Google" not in title
    assert sources.source_cards(_version(["https://ai.google.dev/pricing"]), REPORT)[0]["title"] == "定价页"
    assert sources._title("Gemini 4 Argon: Built to solve your hardest problems") == "Gemini 4 Argon"
    assert sources._title("What's new in Gemini 4 Argon — generateContent API reference") == "What's new in Gemini 4 Argon"
    assert sources._title("A: " + "x" * 60).endswith("…")  # a head too short to mean anything is not used


def test_a_column_turns_the_card_on_with_a_flag_and_rejects_anything_else(tmp_path):
    folder = golden.install_recipes(tmp_path)
    columns = video_columns.load(str(tmp_path))
    assert columns["flash"].source_card and columns["deep_dive"].source_card
    flash = folder / "flash.yaml"
    text = flash.read_text(encoding="utf-8")
    flash.write_text(text.replace("    use: common\n", "    use: common\n    source_card: {enabled: false}\n"), encoding="utf-8")
    assert not video_columns.get(str(tmp_path), "flash").source_card
    flash.write_text(text.replace("    use: common\n", "    use: common\n    source_card: {enabled: yes please}\n"),
                     encoding="utf-8")
    try:
        video_columns.get(str(tmp_path), "flash")
    except video_columns.ColumnError as exc:
        assert "flash" in str(exc) and "source_card" in str(exc)
    else:
        raise AssertionError("a non-boolean source_card was accepted")


DEEP_REPORT = """### 6. 来源清单

1. Prompt caching | Claude Platform Docs | 一手 | 持续更新，检索于 2026-10-17 | https://docs.example.com/caching
2. Mid-conversation messages | Claude Platform Docs | 一手 | 随 Opus 5.5 于 2026-09-22 开放（beta 头 `x`） | https://docs.example.com/mid
3. Introducing Opus 5.5 | Anthropic | 一手 | 2026-09-22 | https://www.example.com/opus
4. 一篇博客 | dev.to | 二手 | 2026-07-29 | https://blog.example.com/p
5. 某个页面 | Docs | 一手 | 检索于 2026-10-17 | https://docs.example.com/other
"""


def test_a_source_list_with_a_site_column_is_read_too():
    listed = {s["url"]: s for s in sources.report_sources(DEEP_REPORT)}
    assert listed["https://docs.example.com/caching"]["kind"] == "一手"
    assert listed["https://blog.example.com/p"]["kind"] == "二手" and listed["https://blog.example.com/p"]["date"] == "2026-07-29"


def test_a_living_page_is_shown_as_of_the_day_it_was_checked_and_other_dates_are_not_borrowed():
    version = _version(["https://docs.example.com/caching", "https://blog.example.com/p"],
                       ["https://docs.example.com/mid", "https://docs.example.com/other", "https://www.example.com/opus"])
    cards = sources.source_cards(version, DEEP_REPORT)
    # the first-hand pages come before the blog; the page whose only date is a feature's release date is left out,
    # and so is the one that says it was checked but does not say it is kept up to date
    assert [(c["url"], c["date"]) for c in cards] == [("https://docs.example.com/caching", "2026-10-17"),
                                                      ("https://www.example.com/opus", "2026-09-22"),
                                                      ("https://blog.example.com/p", "2026-07-29")]


TABLE_REPORT = """# 历史与考古动态
### 1. 本期要点
正文里另一个地址 https://example.com/not-a-reference
### 3. 来源清单
| 来源 | 标题 | 日期 | 链接（链接原样照抄） |
|---|---|---|---|
| museum.example | 日食传说 | Wed, 21 Oct 2026 11:45:00 +0000 | https://museum.example/eclipse/ |
| library.example | 林肯书信 | Wed, 21 Oct 2026 17:00:01 +0000 | [阅读原文](https://library.example/letter/) |
"""


def test_history_report_tables_preserve_titles_urls_and_rfc_dates():
    entries = sources.report_sources(TABLE_REPORT)
    assert [entry["title"] for entry in entries] == ["日食传说", "林肯书信"]
    assert [entry["url"] for entry in entries] == ["https://museum.example/eclipse/", "https://library.example/letter/"]
    assert [sources._iso_date(entry["date"]) for entry in entries] == ["2026-10-21", "2026-10-21"]
    assert all(not entry["kind"] for entry in entries)  # a site name does not establish first-hand provenance


def test_markdown_bibliographies_keep_link_labels_and_ignore_following_sections():
    report = """## 参考资料
- [官方说明](https://example.com/docs/)
### 补充文献
- [案例](https://example.com/case)
## 下一部分
[不是来源](https://example.com/elsewhere)
"""
    entries = sources.report_sources(report)
    assert [(e["title"], e["url"]) for e in entries] == [("官方说明", "https://example.com/docs/"),
                                                          ("案例", "https://example.com/case")]


def test_source_deduplication_matches_tracking_variants_but_preserves_the_original_link():
    report = "## Sources\n- [原文](https://www.example.com/p/?utm_source=r)\n- https://example.com/p\n"
    entries = sources.report_sources(report)
    assert len(entries) == 1 and entries[0]["url"] == "https://www.example.com/p/?utm_source=r"


def test_inline_source_fallback_is_explicit_and_dates_are_not_guessed():
    report = "2026-10-23 的分析，参见 [资料](https://example.com/data)。"
    assert sources.report_sources(report) == []
    entry = sources.report_sources(report, fallback=True)[0]
    assert entry["title"] == "资料" and not entry["date"]
    assert sources._iso_date("not a date") == ""
