from pathlib import Path

import pytest
from PIL import Image

from g2.fast_cards import draw_card, font, lines, write_ass
from g2.production_plan import STYLES


def test_pixel_wrap_preserves_full_numbers_units_and_qualifiers():
    text = "官方称续航可达72小时，但这是特定测试条件下的数据，并不等于日常使用结果。"
    wrapped = lines(text, font(57), 650)
    assert "".join(wrapped) == text
    assert len(wrapped) > 1
    assert all(not line.startswith(tuple("，。！？：；、）》】”’")) for line in wrapped)
    assert all(font(57).getlength(line) <= 650 for line in wrapped)


def test_card_layout_keeps_text_and_review_notice_in_visible_regions(tmp_path):
    text = "官方称续航可达72小时，但这是特定测试条件下的数据，并不等于日常使用结果。"
    target = tmp_path / "card.png"
    result = draw_card({"props": {"title": "续航与测试条件", "text": text}}, title="变化速览",
        index=1, count=5, style=STYLES["editorial_light"], badge={"text": "速览", "color": "#1f7a4d"},
        notices=["本章节有内容待核实"], output=target)
    assert result["body_text_unchanged"]
    assert "".join(result["body_lines"]) == text
    assert Image.open(target).size == (1920, 1080)
    assert result["notices"] == ["本章节有内容待核实"]


def test_subtitles_escape_ass_markup_without_dropping_original_words(tmp_path):
    target = tmp_path / "captions.ass"
    write_ass({"narration": {"segments": [{"start_ms": 1500, "end_ms": 2700, "text": "条件{示例}\\N保留。"}]}}, target)
    text = target.read_text()
    assert "0:00:01.50,0:00:02.70" in text
    assert r"\{示例\}" in text
    assert r"\\N保留" in text
