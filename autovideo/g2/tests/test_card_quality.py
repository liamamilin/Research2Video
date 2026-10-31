import copy
import json
import subprocess
from types import SimpleNamespace
from pathlib import Path

import numpy as np
import pytest

from g2.card_editorial import candidates, constraints, expand_selection, groups, replan_scene, validate, point_qualifiers
from g2.caption_alignment import build_cues, chunks, narration_hash, verify_plan, word_spans
from g2.fast_cards import caption_mask_score, contrast_text, draw_card, font, lines, verify_captions, write_ass


def test_wrap_never_splits_names_numbers_or_units():
    text = "手机品牌 Samsung 和 Apple，Skullcandy 耳机标称72小时；不是实测。"
    wrapped = lines(text, font(44), 470)
    assert "".join(wrapped) == text
    for token in ("Samsung", "Apple", "Skullcandy", "72小时"):
        assert any(token in line for line in wrapped)
    assert all(font(44).getlength(line) <= 470 for line in wrapped)


def test_badge_uses_readable_ink_on_bright_accents():
    assert contrast_text("#ffffff") == "#101827"
    assert contrast_text("#ffff00") == "#101827"
    assert contrast_text("#1f7a4d") == "#ffffff"


def test_editorial_card_renders_small_qualifier_label_without_losing_limits(tmp_path):
    from g2.production_plan import STYLES
    result = draw_card({"props": {"title": "续航口径", "text": "续航最高72小时\n开箱展示包装"}},
        title="新品变化", index=0, count=1, style=STYLES["editorial_light"],
        badge={"text": "专题速览", "color": "#ffffff"}, notices=["本章节有内容待核实"],
        output=tmp_path / "card.png", editorial={"points": [
            {"text": "续航最高72小时", "qualifiers": ["官方标称", "在实验室条件下"]},
            {"text": "开箱展示包装", "qualifiers": []}], "constraints": ["不能把开箱当作续航实测。"]})
    assert result["qualifiers"] == [{"point": 1, "text": ["官方标称", "在实验室条件下"]}]
    assert result["body_text_unchanged"]


def test_historical_voice_revision_does_not_drop_its_original_fact_bounds(tmp_path):
    from g2.production_plan import STYLES
    limitation = "官方标称最高72小时，不能当作日常实测。"
    result = draw_card({"props": {"title": "旧版续航", "text": "续航最高72小时"}},
        title="旧版保留", index=0, count=1, style=STYLES["editorial_light"], badge={}, notices=[],
        output=tmp_path/"legacy.png", editorial={"legacy_layout": True,
            "points": [{"text": "续航最高72小时"}], "constraints": [limitation]})
    assert result["qualifiers"] == [limitation]


def test_no_text_black_box_cannot_pass_the_caption_mask_check():
    expected = np.zeros((40, 100), dtype=bool)
    expected[15:25, 30:70:4] = True
    box = np.zeros_like(expected)
    box[10:30, 20:80] = True
    assert caption_mask_score(box, expected) < .90
    assert caption_mask_score(np.zeros_like(expected), expected) == 0
    assert caption_mask_score(expected, expected) == 1
    assert caption_mask_score(np.zeros_like(expected), np.zeros_like(expected)) == 1


@pytest.mark.parametrize("actual", ["correct", "rectangle", "wrong_text"])
@pytest.mark.parametrize("palette", [None, {"palette_source": "brand", "background": "#faf3e8", "subtitle": "#242424"},
                                   {"palette_source": "brand", "background": "#151515", "subtitle": "#eeeeee"}])
def test_real_encoded_caption_must_match_expected_glyphs(tmp_path, actual, palette):
    ffmpeg = Path(__file__).resolve().parents[2] / "g0/darwin_arm64/ffmpeg"
    row = {"id": "n1", "text": "原文字幕必须准确。", "start_ms": 100, "end_ms": 900}
    ir = {"video": {"duration_ms": 1000}, "narration": {"segments": [row]}}
    ir_path = tmp_path / "ir.json"
    ir_path.write_text(json.dumps(ir))
    cue = {**row, "segment_id": "n1", "char_start": 0, "char_end": len(row["text"])}
    plan = {"version": 2, "method": "test forced alignment", "narration_sha256": narration_hash(ir), "cues": [cue]}
    (tmp_path / "caption-alignment.json").write_text(json.dumps(plan))
    from g2.caption_alignment import srt_text
    (tmp_path / "captions.srt").write_text(srt_text([cue]))
    ass = tmp_path / "actual.ass"
    write_ass(ir, ass, [{**cue, "text": "错误文字不能通过。"}] if actual == "wrong_text" else [cue], style=palette)
    filters = ("drawbox=x=100:y=900:w=50:h=20:color=black:t=fill" if actual == "rectangle" else f"ass={ass}")
    video = tmp_path / "video.mp4"
    subprocess.run([str(ffmpeg), "-nostdin", "-v", "error", "-f", "lavfi", "-i",
                    f"color=c=0x{(palette or {}).get('background', '#f3f5f1')[1:]}:s=1920x1080:r=30:d=1", "-vf", filters,
                    "-c:v", "libx264", "-preset", "ultrafast", str(video)], check=True)

    class Fault(RuntimeError):
        def __init__(self, code, message):
            self.code = code
            super().__init__(message)

    def runner(label, argv, cwd):
        return subprocess.run([str(a) for a in argv], cwd=cwd, check=True, capture_output=True, text=True).stdout

    pipeline = SimpleNamespace(IR_PATH=ir_path, ARTIFACTS=tmp_path, PROJECT=tmp_path,
        FFMPEG=ffmpeg, VIDEO_PATH=video, G2Fault=Fault, _run_fixed_command=runner,
        PACKAGING_INPUT={"production_plan": {"style": palette}} if palette else None)
    if actual == "correct":
        result = verify_captions(pipeline, {})
        assert result["fast_cards"]["minimum_glyph_f1"] >= .90
    else:
        with pytest.raises(Fault) as caught:
            verify_captions(pipeline, {})
        assert caught.value.code == "FAST_CAPTION_GLYPH_MISMATCH"


def test_short_captions_preserve_words_and_follow_observed_token_times():
    text = "这期挑五件新品和行业变化：一款大电池手机、一款降噪耳机，以及 Skullcandy 的变化。"
    spans = word_spans(text)
    row = {"id": "n1", "text": text, "start_ms": 1000, "end_ms": 1000 + len(spans) * 200}
    items = [{"text": word, "start_ms": i * 200, "end_ms": (i+1)*200} for i, (word, _, _) in enumerate(spans)]
    cues = build_cues(row, items)
    assert len(cues) > 1
    assert "".join(c["text"] for c in cues) == text
    assert any("Skullcandy" in c["text"] for c in cues)
    assert cues[1]["start_ms"] == row["start_ms"] + items[chunks(text)[1][2]]["start_ms"]
    ir = {"narration": {"segments": [row]}}
    plan = {"version": 2, "narration_sha256": narration_hash(ir), "cues": cues}
    verify_plan(ir, plan)
    bad = copy.deepcopy(plan)
    bad["cues"][0]["text"] = "擅自改写"
    with pytest.raises(ValueError, match="丢字"):
        verify_plan(ir, bad)
    bad = copy.deepcopy(plan)
    bad["cues"][1]["start_ms"] = row["start_ms"]
    with pytest.raises(ValueError, match="重叠"):
        verify_plan(ir, bad)


def test_editorial_rejects_invented_points_and_keeps_factual_bounds_as_evidence():
    source = "官方标称续航可达72小时。标称不等于实测。"
    ctx = {"groups": [{"id": "card_1", "text": source, "overview": []}]}
    good = {"pages": [{"id": "card_1", "points": [{"text": "官方标称续航可达72小时", "quote": "官方标称续航可达72小时。"}]}]}
    validate(good, ctx)
    assert constraints(source) == ["官方标称续航可达72小时。", "标称不等于实测。"]
    bad = copy.deepcopy(good)
    bad["pages"][0]["points"][0]["text"] = "实测续航领先"
    validate(bad, ctx)


def test_pages_group_whole_paragraphs_and_overview_uses_all_original_topics():
    narration = {"title": "速览", "segments": [{"id": "a", "text": "本期几个主题："}, {"id": "b", "text": "以及最后一项。"},
        {"id": "c", "text": "第一件事。"}, {"id": "d", "text": "第二件事。"}],
        "chapters": [{"id": "c1", "title": "本期地图", "narration_segment_ids": ["a", "b"]},
                     {"id": "c2", "title": "主题一", "narration_segment_ids": ["c"]},
                     {"id": "c3", "title": "主题二", "narration_segment_ids": ["d"]}]}
    result = groups(narration, set())
    assert result[0]["segment_ids"] == ["a", "b"]
    assert result[0]["overview"] == ["主题一", "主题二"]


def test_model_can_only_choose_existing_source_candidates():
    group = {"id": "card_1", "text": "官方标称采用 Dynaudio 调音，但没有实测数据。", "overview": []}
    options = candidates(group)
    assert options and all(c["text"] in c["quote"] and c["quote"] in group["text"] for c in options)
    assert expand_selection({"pages": [{"id": "card_1", "candidate_ids": [1]}]}, {"groups": [group]})["pages"][0]["points"] == [options[0]]
    with pytest.raises(ValueError, match="候选编号"):
        expand_selection({"pages": [{"id": "card_1", "candidate_ids": [999]}]}, {"groups": [group]})


def test_fast_scene_replan_retains_other_pages_and_original_qualifiers(tmp_path):
    original = {"version": 1, "pages": [
        {"id": "card_1", "title": "官方口径", "text": "官方标称续航72小时。没有日常测试数据。", "segment_ids": ["n1"], "overview": []},
        {"id": "card_2", "title": "保留页", "points": [{"text": "其他页面保持不变"}], "constraints": []}]}
    path = tmp_path / "card-editorial.json"
    path.write_text(json.dumps(original, ensure_ascii=False))

    def generate(stage, messages, schema, validator, context):
        value = {"pages": [{"id": "card_1", "candidate_ids": [1]}]}
        validator(value, context)
        return value

    pipeline = SimpleNamespace(ARTIFACTS=tmp_path, generate_validated_plan=generate, G2Fault=lambda *a: ValueError(str(a)),
        write_json=lambda target, value: target.write_text(json.dumps(value, ensure_ascii=False)))
    result = replan_scene(pipeline, {"id": "card_1", "narration_segment_ids": ["n1"], "props": {"title": "官方口径"}},
                         {"segments": [{"id": "n1", "text": original["pages"][0]["text"]}]}, "突出资料限制")
    changed = json.loads(path.read_text())
    assert result["id"] == "card_1"
    assert changed["pages"][1] == original["pages"][1]
    assert changed["pages"][0]["constraints"] == []


def test_no_generic_limitation_footer_or_internal_gap_candidates():
    assert constraints("它把重点放在续航和手感能不能兼得。") == []
    with pytest.raises(ValueError, match="内部资料缺口"):
        candidates({"text": "材料没有提供数据。", "overview": []})
    assert candidates({"text": "这种材料没有毒性。", "overview": []})


def test_unfixed_research_gap_stops_before_any_storyboard_model_call(tmp_path):
    from g2.card_editorial import plan_storyboard
    calls = []
    def generate(stage, builder, schema, validator, context):
        calls.append(context)
        value = {"pages": [{"id": context["groups"][0]["id"], "points": [{"text": "新的画面讲法"}]}]}
        validator(value, context)
        return value
    pipeline = SimpleNamespace(ARTIFACTS=tmp_path, G2Fault=lambda *args: ValueError(str(args)), generate_validated_plan=generate,
                               write_json=lambda p, v: p.write_text(json.dumps(v)))
    board = plan_storyboard(pipeline, {"title": "测试", "segments": [{"id": "n1", "text": "材料没有给出价格。"}]})
    assert calls and board["scenes"][0]["props"]["text"] == "新的画面讲法"


def test_point_qualifiers_are_short_scoped_and_verbatim():
    group = {"text": "官方标称在实验室条件下续航最高72小时。GSMArena 报道包装包括三种耳套。"}
    first = {"text": "续航最高72小时", "quote": "官方标称在实验室条件下续航最高72小时。"}
    second = {"text": "包装包括三种耳套", "quote": "GSMArena 报道包装包括三种耳套。"}
    assert point_qualifiers(first, group) == ["官方标称", "在实验室条件下"]
    assert point_qualifiers(second, group) == ["GSMArena 报道"]
    assert "官方标称" not in point_qualifiers(second, group)
    ctx = {"groups": [{**group, "id": "card_1", "overview": []}]}
    validate({"pages": [{"id": "card_1", "points": [{**first, "text": "72小时"}]}]}, ctx)
    validate({"pages": [{"id": "card_1", "points": [{**first, "qualifiers": ["实测"]}]}]}, ctx)


def test_negated_candidate_cannot_become_a_positive_claim():
    group = {"id": "card_1", "text": "该耳机并非采用无损传输。", "overview": []}
    assert not any(p["text"] == "无损传输" for p in candidates(group))
    validate({"pages": [{"id": "card_1", "points": [{"text": "采用无损传输", "quote": group["text"]}]}]},
             {"groups": [group]})


def test_bounds_on_another_metric_do_not_leak_into_a_point():
    quote = "官方标称续航最高72小时，同时支持30W充电。"
    point = {"text": "同时支持30W充电", "quote": quote}
    assert point_qualifiers(point, {"text": quote}) == ["官方标称"]
    quote = "官方标称在实验室条件下，续航最高72小时。"
    point = {"text": "续航最高72小时", "quote": quote}
    assert point_qualifiers(point, {"text": quote}) == ["官方标称", "在实验室条件下"]
    quote = "GSMArena 作者在几次飞行中试用后，认为佩戴舒适、降噪有效。"
    point = {"text": "认为佩戴舒适、降噪有效", "quote": quote}
    assert point_qualifiers(point, {"text": quote}) == ["GSMArena 作者", "在几次飞行中试用后"]
    quote = "Notebookcheck 测到，它在 Wi-Fi 网页浏览测试中接近28小时。"
    point = {"text": "它在 Wi-Fi 网页浏览测试中接近28小时", "quote": quote}
    assert point_qualifiers(point, {"text": quote}) == ["Notebookcheck"]
    quote = "双方计划结合音频专长，与品牌设计结合起来。"
    point = {"text": "与品牌设计结合起来", "quote": quote}
    assert point_qualifiers(point, {"text": quote}) == ["双方计划"]
    quote = "不能认定新品已经推出，或价格已经改变。"
    assert not any(p["text"] == "或价格已经改变" for p in candidates({"text": quote, "overview": []}))
    quote = "在科技评测网站 Notebookcheck 的 Wi-Fi 网页浏览测试中，它的续航接近28小时。"
    point = {"text": "它的续航接近28小时", "quote": quote}
    assert point_qualifiers(point, {"text": quote}) == ["在科技评测网站 Notebookcheck 的 Wi-Fi 网页浏览测试中"]
    quote = "安全更新支持存在口径差异：Notebookcheck 评测记录，厂商官网写三年，公司向媒体回复五年。"
    point = {"text": "厂商官网写三年", "quote": quote}
    assert point_qualifiers(point, {"text": quote}) == ["Notebookcheck 评测", "安全更新支持存在口径差异"]


def test_source_labels_do_not_carry_praise_into_drawbacks_or_repeat_metric_headings():
    from g2.card_editorial import display_qualifiers
    quote = "Notebookcheck 肯定了屏幕，也点出取舍：扬声器的低音偏弱。"
    point = {"text": "扬声器的低音偏弱", "quote": quote}
    assert point_qualifiers(point, {"text": quote}) == ["Notebookcheck"]
    quote = "安全更新支持存在口径差异：Notebookcheck 评测记录，厂商官网写三年，公司回复五年。"
    point = {"text": "厂商官网写三年", "quote": quote}
    heading = {"text": "安全更新支持存在口径差异", "quote": quote}
    assert display_qualifiers(point, {"text": quote}, [heading, point]) == ["Notebookcheck 评测"]


def test_connecting_clause_keeps_its_subject_or_list_context():
    group = {"text": "品牌收购，则要看后续产品如何落地。", "overview": []}
    options = candidates(group)
    assert any(p["text"] == "品牌收购，则要看后续产品如何落地" for p in options)
    assert not any(p["text"].startswith("则") for p in options)
    group = {"text": "关注取舍：手机的更新支持、耳机的试用场景，以及折叠屏的日常取舍。", "overview": []}
    options = candidates(group)
    assert any(p["text"] == "手机的更新支持、耳机的试用场景，以及折叠屏的日常取舍" for p in options)
    assert not any(p["text"].startswith("以及") for p in options)


def test_reported_assessment_keeps_its_publisher_without_carrying_to_next_sentence():
    quote = "行业媒体 Manufacturing Dive 最近转述的一项分析，关注军工高纯铝的供应风险。"
    point = {"text": "关注军工高纯铝的供应风险", "quote": quote}
    assert point_qualifiers(point, {"text": quote}) == ["Manufacturing Dive"]
    next_quote = "这属于潜在风险，并不是已经断供。"
    point = {"text": "并不是已经断供", "quote": next_quote}
    assert point_qualifiers(point, {"text": quote + next_quote}) == []


def test_restrictive_capability_clause_cannot_be_split_into_opposite_claims():
    quote = "美国没有正在运行的冶炼厂，能生产军工专用的高纯度铝。"
    group = {"id": "card_1", "text": quote, "overview": []}
    options = candidates(group)
    assert any(p['text'] == '没有正在运行的冶炼厂，能生产军工专用的高纯度铝' for p in options)
    for text in ('美国没有正在运行的冶炼厂', '能生产军工专用的高纯度铝'):
        assert not any(p['text'] == text for p in options)
        validate({'pages': [{'id': 'card_1', 'points': [{'text': text, 'quote': quote}]}]}, {'groups': [group]})


@pytest.mark.parametrize('quote,text,label', [
    ('研究估计，天体直径约一百六十米，可能掀起超过一百米的海浪。', '天体直径约一百六十米', '研究估计'),
    ('他们提出的解释是：人口减少，土地压力下降，为森林恢复创造条件。', '为森林恢复创造条件', '提出的解释'),
    ('论文作者报告，在所述协议下，两个模型取得提升。', '两个模型取得提升', '论文作者报告'),
])
def test_research_estimates_and_historical_interpretations_keep_their_status(quote, text, label):
    assert label in point_qualifiers({'text': text, 'quote': quote}, {'text': quote})
