"""Burst flash runs from B站热搜 (web/services/hot_trigger.py): new AI words only, throttled, capped, spaced."""

from __future__ import annotations

from datetime import datetime
import json
import os

from web.services import hot_trigger as ht


def at(h, m=0, day=11):
    return datetime(2026, 11, day, h, m).timestamp()


def make(tmp_path, words, clock, started=None, outcome="started"):
    calls = started if started is not None else []
    return ht.HotTrigger(str(tmp_path / "state"), str(tmp_path), trending=lambda: list(words),
                         start=lambda: calls.append(1) or outcome, clock=lambda: clock[0])


def report(tmp_path, text, when):
    folder = tmp_path / "output/flash"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"2026-11-11_ai_flash_discovery{len(list(folder.iterdir())) or ''}.md"
    path.write_text(text, encoding="utf-8")
    os.utime(path, (when, when))


def test_a_new_ai_word_starts_one_run_and_is_not_started_again(tmp_path):
    clock, started = [at(14)], []
    report(tmp_path, "### 1.1 Claude Fable 降价", at(7, 40))
    words = ["BLG夺冠", "DeepSeek改架构数学能力提升", "王者新赛季"]
    assert "已加跑" in make(tmp_path, words, clock, started).check()
    assert started == [1]
    clock[0] = at(15)
    assert make(tmp_path, words, clock, started).check() == ""  # seen today
    assert started == [1]


def test_it_looks_at_most_every_half_hour_and_only_in_the_daytime(tmp_path):
    clock, started = [at(14)], []
    words = ["豆包工作取消额度"]
    make(tmp_path, [], clock, started).check()
    clock[0] = at(14, 10)
    assert make(tmp_path, words, clock, started).check() == ""  # too soon
    clock[0] = at(23)
    assert make(tmp_path, words, clock, started).check() == ""  # night: the 07:30 run covers it
    assert started == []


def test_words_already_in_today_s_reports_or_too_soon_after_one_wait(tmp_path):
    clock, started = [at(14)], []
    report(tmp_path, "### 1.1 DeepSeek改架构数学能力提升：……", at(12))
    assert make(tmp_path, ["DeepSeek改架构数学能力提升"], clock, started).check() == ""
    note = make(tmp_path, ["Kimi新模型发布"], [at(14, 31)], started).check()
    assert "不到 3 小时" in note and started == []


def test_at_most_two_extra_runs_a_day_and_a_refusal_is_reported(tmp_path):
    started = []
    for i, word in enumerate(["Kimi新模型", "豆包新功能"]):
        assert "已加跑" in make(tmp_path, [word], [at(10 + i)], started).check()
    note = make(tmp_path, ["千问新版本"], [at(13)], started).check()
    assert "已加跑 2 次" in note and len(started) == 2
    refused = make(tmp_path, ["Grok降价"], [at(10, day=12)], [], outcome="快讯正在运行").check()
    assert "没有加跑：快讯正在运行" in refused
    state = json.loads((tmp_path / "state/hot_trigger.json").read_text())
    assert state["day"] == "2026-11-12" and state["runs"] == []


def test_trending_failure_is_only_a_note(tmp_path):
    def broken():
        raise OSError("412")
    trigger = ht.HotTrigger(str(tmp_path / "state"), str(tmp_path), trending=broken, start=lambda: "started",
                            clock=lambda: at(14))
    assert "热搜取不到" in trigger.check()


def test_hot_trigger_passes_the_original_trigger_timestamp(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(ht, "start_flash", lambda *, triggered_at: calls.append(triggered_at) or "started")
    timestamp = at(16, 36)
    trigger = ht.HotTrigger(str(tmp_path / "state"), str(tmp_path),
                            trending=lambda: ["DeepSeek新功能"], clock=lambda: timestamp)
    assert "已加跑" in trigger.check()
    assert calls == [timestamp]


def test_busy_or_cooling_down_hot_word_can_start_after_the_next_check(tmp_path):
    word = "DeepSeek新功能"
    report(tmp_path, "### 上午快讯", at(10))
    assert "不到 3 小时" in make(tmp_path, [word], [at(12)]).check()
    assert "快讯正在运行" in make(tmp_path, [word], [at(13)], outcome="快讯正在运行").check()
    assert "已加跑" in make(tmp_path, [word], [at(13, 30)]).check()
    assert make(tmp_path, [word], [at(14)]).check() == ""
