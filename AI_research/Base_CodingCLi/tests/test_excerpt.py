"""One item's part of a discovery report (core/video/excerpt.py)."""

from __future__ import annotations

from core.video import excerpt
from tests.test_video_flash import DISCOVERY


def test_the_item_and_only_the_sources_it_cites():
    part = excerpt.item_excerpt(DISCOVERY, "Opus 5.5 输入价降到 4 美元")
    assert part.startswith("### 1.1 Opus 5.5 输入价降到 4 美元\n- **一句话**")
    assert "输入 $5 → $4" in part and part.rstrip().endswith("2. 发布说明 | 一手 | 2026-10-16 | https://example.com/releases/2.0.")
    assert "某框架" not in part.split("### 来源清单")[0] and "未收录" not in part and "本期概览" not in part
    assert part.count("### 来源清单") == 1 and "1. Opus 5.5 定价 | 一手 | 2026-10-14 | https://example.com/pricing" in part


def test_an_item_ends_at_the_next_heading_and_sources_it_does_not_cite_are_left_out():
    part = excerpt.item_excerpt(DISCOVERY, "某框架 2.0 弃用旧接口")
    assert "Opus" not in part and "某框架 2.0 弃用旧接口" in part
    assert "https://example.com/releases/2.0." in part and "https://example.com/pricing" not in part  # it cites [2] only


def test_no_such_item_is_none_rather_than_the_whole_report():
    assert excerpt.item_excerpt(DISCOVERY, "不存在的条目") is None
    assert excerpt.item_excerpt(DISCOVERY, "") is None


def test_an_item_with_no_cited_sources_gets_no_source_list():
    text = "### 1.1 无来源的条目\n- **一句话**：x\n\n### 来源清单\n1. a | 一手 | 2026-10-01 | https://example.com/a\n"
    assert excerpt.item_excerpt(text, "无来源的条目") == "### 1.1 无来源的条目\n- **一句话**：x\n"


def test_renamed_item_uses_its_frozen_summary_without_other_topics():
    text = "### 1.1 原标题\n- **一句话**：本地编程的新变化 [1]\n\n### 1.2 其他选题\n- **一句话**：其他事情 [2]\n\n### 来源清单\n1. 原始来源 https://example.com/one\n2. 其他来源 https://example.com/two\n"
    part = excerpt.item_excerpt(text, "修改后的标题", summary="本地编程的新变化 [1]")
    assert part.startswith("### 1.1 原标题")
    assert "https://example.com/one" in part
    assert "其他选题" not in part and "https://example.com/two" not in part
    assert excerpt.item_excerpt(text, "修改后的标题", summary="本地编程") is None


def test_ambiguous_summary_does_not_choose_an_item():
    text = "### 1.1 一个条目\n- **一句话**：同一句话\n### 1.2 另一个条目\n- **一句话**：同一句话\n"
    assert excerpt.item_excerpt(text, "新标题", summary="同一句话") is None
