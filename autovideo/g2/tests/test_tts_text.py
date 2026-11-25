import json

import pytest

from g2 import voxcpm_voice
from g2.tts_text import for_voice
from g2.voices import VOICE_IDS, VOXCPM_VOICES, catalog, mlx_engine


@pytest.mark.parametrize("text, spoken", [
    ("Opus 5.5 输入价每百万 token 4 美元。", "Opus 五点五 输入价每百万 token 4 美元。"),
    ("按输入价的 1.25 倍收。", "按输入价的 一点二五 倍收。"),
    ("每次 0.4 美分，只付 0.05。", "每次 零点四 美分，只付 零点零五。"),
    ("涨了 10.5 个点", "涨了 十点五 个点"),
    ("共 105.25 万", "共 一百零五点二五 万"),
    ("满 2020.5 次", "满 两千零二十点五 次"),
])
def test_decimals_are_spelled_in_chinese(text, spoken):
    assert for_voice(text) == spoken


@pytest.mark.parametrize("text", [
    "v1.2.3 版本", "192.168.0.1", "第 5 点和 10 美分", "没有小数的句子。", "version 1. Next",
])
def test_everything_else_goes_through_as_written(text):
    assert for_voice(text) == text


@pytest.mark.parametrize("text, spoken", [
    ("同一份开头 2 万 token，跑 100 次。", "同一份开头 两万 token，跑 100 次。"),
    ("2 万 token 就是 8 美分。", "两万 token 就是 8 美分。"),
    ("1 小时那档是 2 倍。", "1 小时那档是 两倍。"),
    ("要等2分钟，再试2次", "要等两分钟，再试两次"),
    ("花了 2 美元。", "花了 两美元。"),
])
def test_a_lone_two_before_a_magnitude_or_measure_word_is_said_liang(text, spoken):
    assert for_voice(text) == spoken


@pytest.mark.parametrize("text", [
    "第 2 个", "第2次", "2 号线", "2 月 5 日", "12 万", "22 倍", "20 倍", "版本 2", "2025 年", "2",
])
def test_two_is_left_alone_when_it_is_part_of_something_else(text):
    assert for_voice(text) == text


def test_a_decimal_starting_with_two_is_read_as_a_decimal():
    assert for_voice("2.5 倍") == "二点五 倍"


def test_backticks_only_mark_code_and_are_dropped():
    assert for_voice("把 `cache_control` 放在 5.5 之前") == "把 cache_control 放在 五点五 之前"


@pytest.mark.parametrize("text, spoken", [
    ("单项通过 63.84%，完整任务仅 6.93%。", "单项通过 百分之六十三点八四，完整任务仅 百分之六点九三。"),
    ("同比增长 103%，降幅 2%。", "同比增长 百分之一百零三，降幅 百分之二。"),
    ("小费按 20%-15% 或 5~10% 计算。", "小费按 百分之二十到百分之十五 或 百分之五到百分之十 计算。"),
    ("从 440—620 美元，预算 5-10 万美元，耗 1-2 小时。", "从 440到620 美元，预算 5到10 万美元，耗 1到两小时。"),
    ("吞吐 229.7 t/s，最高 100 tok/s，峰值 50 req/s。", "吞吐 两百二十九点七 每秒 token，最高 100 每秒 token，峰值 50 每秒请求。"),
    ("带宽 12.5 GB/s，车速 120 km/h，每台 61 美元/台。", "带宽 十二点五 每秒 GB，车速 120 公里每小时，每台 61 美元每台。"),
])
def test_percentages_ranges_and_unit_slashes_are_spoken_naturally(text, spoken):
    assert for_voice(text) == spoken


@pytest.mark.parametrize("text", [
    "2026-11-23", "案件号 2026-117348", "GPT-6", "ACE/USDT", "Megaspeed/Alice", "11/11", "0.28.4", "H2O2",
])
def test_dates_ids_and_non_rate_slashes_are_not_altered(text):
    assert for_voice(text) == text


@pytest.mark.parametrize("text, spoken", [
    ("减少 CO2 排放，释放 O2 和 H2O。", "减少 二氧化碳 排放，释放 氧气 和 水。"),
    ("CO₂ 浓度上升，企业转向 SaaS 订阅。", "二氧化碳 浓度上升，企业转向 萨斯 订阅。"),
])
def test_spoken_lexicon_normalizes_unambiguous_formulas_and_terms(text, spoken):
    assert for_voice(text) == spoken


def test_unit_count_matches_the_length_estimate_rule():
    # a CJK character, a Latin word and a digit each count one; punctuation counts nothing
    assert voxcpm_voice.units("Opus 5.5 输入价，每百万 token。") == 10  # Opus 1, "5" "5" 2, 输入价 3, 每百万 3, token 1
    assert voxcpm_voice.units("，。 ") == 0


def test_voxcpm_voice_is_listed_and_unavailable_until_installed(tmp_path):
    assert set(VOXCPM_VOICES) <= set(VOICE_IDS)
    item = next(v for v in catalog(tmp_path) if v["id"] == "voxcpm_anchor")
    assert item["engine"] == "VoxCPM2" and item["available"] is False
    assert next(v for v in catalog(tmp_path) if v["id"] == "zf_xiaoxiao")["available"] is True


def test_voxcpm_voice_needs_every_file_and_the_ready_marker(tmp_path):
    for name in ("g3/data/voice-runtime/bin/python", "g3/data/voice-models/voxcpm2-8bit/config.json",
                 "g3/data/voice-models/voxcpm2-8bit/model.safetensors", "g2/voice_assets/voxcpm2_anchor.wav"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
    assert next(v for v in catalog(tmp_path) if v["id"] == "voxcpm_anchor")["available"] is False
    (tmp_path / "g3/data/voice-models/voxcpm2-8bit/.ready").write_bytes(b"")
    assert next(v for v in catalog(tmp_path) if v["id"] == "voxcpm_anchor")["available"] is True


def test_mlx_engine_maps_each_local_voice_and_leaves_kokoro_alone(tmp_path):
    assert mlx_engine("zf_xiaoxiao", tmp_path) is None
    vox = mlx_engine("voxcpm_anchor", tmp_path)
    assert vox["provider"] == "local_voxcpm_mlx" and vox["entry"] == tmp_path / "g2/voxcpm_voice.py"
    qwen = mlx_engine("qwen_vivian", tmp_path)
    assert qwen["provider"] == "local_qwen_mlx" and qwen["entry"] == tmp_path / "g2/qwen_voice.py"
    assert vox["python"] == qwen["python"]  # one runtime, one allowlisted executable


def test_voxcpm_entry_rejects_bad_input_before_loading_anything(tmp_path):
    root = voxcpm_voice.ROOT
    work = root / "g3/data/test-voxcpm-entry"
    work.mkdir(parents=True, exist_ok=True)
    try:
        plan = work / "plan.json"
        plan.write_text(json.dumps({"segments": [{"id": "s1", "text": "你好。"}]}))
        with pytest.raises(ValueError, match="Unsupported"):
            voxcpm_voice.generate(plan, work / "out", "qwen_vivian")
        outside = tmp_path / "plan.json"
        outside.write_text(plan.read_text())
        with pytest.raises(ValueError, match="workspace"):
            voxcpm_voice.generate(outside, work / "out", "voxcpm_anchor")
        plan.write_text(json.dumps({"segments": [{"id": "../x", "text": "你好。"}]}))
        with pytest.raises(ValueError, match="segment id"):
            voxcpm_voice.generate(plan, work / "out", "voxcpm_anchor")
        plan.write_text(json.dumps({"voice_settings": {"steps": 500}, "segments": []}))
        with pytest.raises(ValueError, match="steps"):
            voxcpm_voice.generate(plan, work / "out", "voxcpm_anchor")
    finally:
        for item in work.glob("*"):
            item.unlink() if item.is_file() else None
        work.rmdir()
