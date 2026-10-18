import numpy as np
import pytest

from g2 import pauses


@pytest.mark.parametrize("current, following, boundary, expected", pauses.GOLDEN)
def test_gap_table_shared_with_arec(current, following, boundary, expected):
    assert pauses.gap_ms(current, following, boundary) == expected


def test_a_question_gets_a_longer_beat_than_a_statement():
    assert pauses.gap_ms("这是为什么呢？", "因为开头变了。") > pauses.gap_ms("这是为什么呢。", "因为开头变了。")


def test_boundaries_are_ordered_sentence_paragraph_chapter():
    sentence = pauses.gap_ms("请求本身就是这个顺序：工具定义在前，系统提示在中，消息在后。", "好。")
    paragraph = pauses.gap_ms("请求本身就是这个顺序：工具定义在前，系统提示在中，消息在后。", "好。", "paragraph")
    chapter = pauses.gap_ms("请求本身就是这个顺序：工具定义在前，系统提示在中，消息在后。", "好。", "chapter")
    assert sentence < paragraph < chapter


def test_every_gap_stays_inside_the_bounds_for_sentences():
    for current in ("好。", "好？", "好；", "一" * 60 + "。", "一" * 60 + "？", "短，"):
        for following in ("但是这样。", "其他。"):
            assert pauses.MIN_MS <= pauses.gap_ms(current, following) <= pauses.MAX_MS


def test_trim_keeps_a_fixed_margin_of_the_voices_own_silence():
    rate = 24000
    speech = np.full(rate, 0.2, dtype=np.float32)
    padded = np.concatenate([np.zeros(rate // 2, np.float32), speech, np.zeros(rate // 3, np.float32)])
    trimmed = pauses.trim(padded, rate)
    margin = round(rate * pauses.LEAD_MARGIN_MS / 1000) + round(rate * pauses.TAIL_MARGIN_MS / 1000)
    assert len(trimmed) == len(speech) + margin
    assert np.all(trimmed[: round(rate * pauses.LEAD_MARGIN_MS / 1000)] == 0)
    silent = np.zeros(100, np.float32)
    assert pauses.trim(silent, rate) is silent  # nothing to anchor on: left as it is
