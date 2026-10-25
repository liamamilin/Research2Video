import copy
import json

import pytest
from PIL import ImageFont

from g2 import authored_script as authored
from g2 import information_motion as motion
from g2 import process_diagram as diagrams
from g2 import run_g2
from g2.technical_text import altered_technical_text, technical_tokens, wrap_units

CHAPTERS = [
    {"title": "开场：你要的数字已经内置", "paragraphs": [
        "你在给 crewAI 的 eval --models 做模型横评，最想知道两件事：每个模型跑一次花多少钱、又花了多久。",
        "好消息是：从 crewAI 1.15.27 起，crewai eval --models 自带每次运行的成本与耗时报告。"]},
    {"title": "三个必须实测的口径", "paragraphs": [
        "LiteLLM 返回 response_cost，再用 cost_per_token 和 completion_cost 对照模型成本表折算成美元。",
        "v1.15.28 又给 eval 加了『报告停止的模型在哪里中断』，所以横评前建议锁版本。"]},
]


class Pipeline:
    G2Fault = run_g2.G2Fault
    MAX_TOTAL_CALLS = 16
    MAX_INPUT_TOKENS_PER_CALL = 10000
    MAX_TOKENS_PER_CALL = 100000
    MAX_PROJECTED_API_COST_USD = 0
    choice = {"recommended_form": "information_motion", "reason": "并列的说明和对照适合卡片"}

    def __init__(self, root, chapters=CHAPTERS, title="crewAI 成本与耗时"):
        self.PROJECT_INPUT = root / "input/source.txt"
        self.ARTIFACTS = root / "artifacts"
        self.IR_PATH = root / "video-ir.json"
        self.ARTIFACTS.mkdir(parents=True)
        self.PROJECT_INPUT.parent.mkdir(parents=True)
        saved = {"title": title, "input_mode": "narration"}
        if chapters is not None:
            saved["chapters"] = chapters
        (self.PROJECT_INPUT.parent / "input.json").write_text(json.dumps(saved, ensure_ascii=False))
        self.calls = []

    def write_json(self, path, value):
        path.write_text(json.dumps(value, ensure_ascii=False))

    def write_fact_ledger(self, plan, source):
        self.ledger = plan

    def generate_validated_plan(self, stage, builder, schema, validator, context):
        self.calls.append((stage, builder(context, None)))
        validator(self.choice, context)
        return copy.deepcopy(self.choice)


def segments_of(plan):
    return [{"id": s["id"], "text": s["text"], "chapter_id": s["chapter_id"]} for s in plan["segments"]]


def test_chapters_are_voiced_word_for_word_with_their_titles(tmp_path):
    pipeline, source = Pipeline(tmp_path), authored.script_text(CHAPTERS)
    plan = authored.plan(pipeline, source)
    assert [c["title"] for c in plan["chapters"]] == [c["title"] for c in CHAPTERS]
    assert [s["chapter_id"] for s in plan["segments"]][0] == "c1" and plan["segments"][-1]["chapter_id"] == "c2"
    assert "".join(s["text"] for s in plan["segments"]) == source.replace("\n", "")
    assert all(len(s["text"]) <= 80 for s in plan["segments"])
    assert any("用 cost_per_token 和 completion_cost 对照" in s["text"] for s in plan["segments"])
    units = [uid for c in plan["chapters"] for uid in c["source_unit_ids"]]
    assert units == [f"u{i + 1}" for i in range(len(units))] and plan["chapters"][1]["source_unit_ids"]
    evidence = json.loads((pipeline.ARTIFACTS / "authored-narration.json").read_text())
    assert evidence["origin"] == "narration_create" and evidence["model_rewrite"] is False
    assert evidence["exact_text_check"]["status"] == "PASS"
    assert pipeline.MAX_TOTAL_CALLS >= 3 + 3 * len(CHAPTERS)


@pytest.mark.parametrize("change", [
    lambda s: s[2].update(text="前一步确认了数字是版本自带的，" + s[2]["text"]),      # inserted transition
    lambda s: s[0].update(text="接着上回说，" + s[0]["text"]),                        # implied previous episode
    lambda s: s[-2].update(text=s[-2]["text"].replace("用 cost_per_token 和 completion_cost 对照", "按")),  # detail dropped
    lambda s: s.pop(),                                                                  # missing ending
    lambda s: s[1].update(text=s[1]["text"].replace("crewai eval", "crewaieval")),      # space removed inside a phrase
    lambda s: s[1].update(text=s[1]["text"].replace("--models", "–models")),            # flag altered
    lambda s: s[2].update(chapter_id="c1"),                                             # moved to another chapter
])
def test_rewording_insertions_and_drops_fail_with_a_clear_error(tmp_path, change):
    plan = authored.plan(Pipeline(tmp_path), authored.script_text(CHAPTERS))
    segments = segments_of(plan)
    change(segments)
    with pytest.raises(authored.NarrationMismatch) as error:
        authored.verify_exact(CHAPTERS, segments)
    assert error.value.code == "NARRATION_TEXT_MISMATCH"


def test_splitting_and_whitespace_are_the_only_allowed_differences():
    chapters = [{"title": "空白", "paragraphs": ["第一句  有两个空格。第二句\t有制表符。", "第三段 eval --models 结束。"]}]
    segments = [{"id": "c1_n1", "chapter_id": "c1", "text": "第一句 有两个空格。"},
                {"id": "c1_n2", "chapter_id": "c1", "text": "第二句 有制表符。"},
                {"id": "c1_n3", "chapter_id": "c1", "text": "第三段 eval"},
                {"id": "c1_n4", "chapter_id": "c1", "text": "--models 结束。"}]
    assert authored.verify_exact(chapters, segments)["status"] == "PASS"


def test_mismatch_message_names_the_chapter_position_and_both_texts(tmp_path):
    plan = authored.plan(Pipeline(tmp_path), authored.script_text(CHAPTERS))
    segments = segments_of(plan)
    segments[-2]["text"] = segments[-2]["text"].replace("用 cost_per_token 和 completion_cost 对照", "按")
    with pytest.raises(authored.NarrationMismatch, match="三个必须实测的口径.*用 cost_per_token.*按模型成本表"):
        authored.verify_exact(CHAPTERS, segments)


def test_saved_chapters_must_match_the_stored_script(tmp_path):
    pipeline = Pipeline(tmp_path)
    with pytest.raises(run_g2.G2Fault, match="NARRATION_TEXT_MISMATCH"):
        authored.plan(pipeline, authored.script_text(CHAPTERS).replace("两件事", "三件事"))


def test_narration_revision_still_uses_one_chapter_named_after_the_job(tmp_path):
    pipeline = Pipeline(tmp_path, chapters=None, title="交接讲解 · 修订版")
    plan = authored.plan(pipeline, "先说明交付目标。\n\n需要确认的内容，不要凭猜测补齐。")
    assert [c["title"] for c in plan["chapters"]] == ["交接讲解 · 修订版"]
    assert [s["text"] for s in plan["segments"]] == ["先说明交付目标。", "需要确认的内容，不要凭猜测补齐。"]
    assert json.loads((pipeline.ARTIFACTS / "authored-narration.json").read_text())["origin"] == "narration_revision"


def write_outputs(pipeline, plan, *, captions=None, titles=None):
    segments = [{"id": s["id"], "text": s["text"], "start_ms": 100 * i, "end_ms": 100 * i + 90}
                for i, s in enumerate(plan["segments"])]
    structure = {"mode": "content", "chapters": [
        {"id": c["id"], "title": (titles or {}).get(c["id"], c["title"]), "narration_segment_ids": c["narration_segment_ids"]}
        for c in plan["chapters"]]}
    pipeline.IR_PATH.write_text(json.dumps({"narration": {"segments": segments}, "content_structure": structure}, ensure_ascii=False))
    texts = captions or [s["text"] for s in segments]
    (pipeline.ARTIFACTS / "captions.srt").write_text("\n\n".join(
        f"{i + 1}\n00:00:00,000 --> 00:00:01,000\n{t}" for i, t in enumerate(texts)) + "\n", encoding="utf-8")
    return segments


def test_final_ir_captions_and_titles_are_rechecked(tmp_path):
    pipeline, source = Pipeline(tmp_path), authored.script_text(CHAPTERS)
    plan = authored.plan(pipeline, source)
    write_outputs(pipeline, plan)
    result = authored.verify_outputs(pipeline, source)
    assert result["status"] == "PASS" and "captions.srt" in result["checked"]


@pytest.mark.parametrize("case", ["caption", "title", "ir"])
def test_final_check_rejects_drifted_captions_titles_or_ir(tmp_path, case):
    pipeline, source = Pipeline(tmp_path), authored.script_text(CHAPTERS)
    plan = authored.plan(pipeline, source)
    texts = [s["text"] for s in plan["segments"]]
    if case == "caption":
        texts[1] = texts[1].replace("--models", "–models")
        write_outputs(pipeline, plan, captions=texts)
    elif case == "title":
        write_outputs(pipeline, plan, titles={"c2": "口径"})
    else:
        plan["segments"][0]["text"] = "接着上回说，" + plan["segments"][0]["text"]
        write_outputs(pipeline, plan)
    with pytest.raises(run_g2.G2Fault, match="NARRATION_TEXT_MISMATCH"):
        authored.verify_outputs(pipeline, source)


def test_auto_form_is_chosen_without_touching_the_script(tmp_path):
    pipeline, source = Pipeline(tmp_path), authored.script_text(CHAPTERS)
    prepared, brief = authored.prepare(pipeline, source, "auto")
    assert prepared == source and brief["selected_form"] == "information_motion"
    assert brief["selection_mode"] == "automatic" and brief["excluded_units"] == [] and brief["source_rewritten"] is False
    stage, messages = pipeline.calls[0]
    assert stage == "narration-form" and "根据素材，产出目标观众" in messages[0]["content"]
    assert json.loads(messages[1]["content"])["script_chapters_as_untrusted_data"][1]["title"] == "三个必须实测的口径"
    pipeline.choice = {"recommended_form": "knowledge_cards", "reason": "x"}
    with pytest.raises(run_g2.G2Fault, match="NARRATION_FORM_SCHEMA"):
        authored.prepare(pipeline, source, "auto")


def test_manual_form_needs_no_model_call(tmp_path):
    pipeline, source = Pipeline(tmp_path), authored.script_text(CHAPTERS)
    _, brief = authored.prepare(pipeline, source, "process_diagram")
    assert brief["selected_form"] == "process_diagram" and brief["selection_mode"] == "manual" and pipeline.calls == []


def test_long_sentences_split_at_punctuation_before_spaces():
    text = "这一句话很长，" * 6 + "最后用 crewai eval --models 命令跑一次最小 sweep 并把输出原件存档写清版本号和你的定义。"
    parts = authored.split_script(text)
    assert all("crewai eval --models" in p or "--models" not in p for p in parts)
    assert "".join(parts) == text


def test_voice_skips_backticks_but_keeps_every_word():
    assert run_g2.spoken_text("用 `cost_per_token` 对照") == "用 cost_per_token 对照"


def test_technical_tokens_must_be_copied_verbatim():
    reference = "crewai eval --models 自带报告；v1.15.24 加入；从 1.15.27 起；用 cost_per_token 对照；comparison['models'] 读取。"
    assert technical_tokens("eval --models 与 `cost_per_token`") == ["--models", "cost_per_token"]
    assert altered_technical_text("eval --models 自带", reference) is None
    assert altered_technical_text("v1.15.24 与 1.15.27+", reference) is None
    assert altered_technical_text("cost/time 齐全就存档", reference) is None
    for bad in ["–models 报告", "-models", "--model", "comparison[\"models\"]", "cost-per-token", "v1.15.27", "1.15.24"]:
        assert altered_technical_text(bad, reference), bad
    assert wrap_units("用 eval --models 对照")[2:5] == ["eval", " ", "--models"]


def information_context():
    segments = [{"id": "n1", "text": "crewai eval --models 自带成本与耗时报告。"}, {"id": "n2", "text": "升级到 1.15.27 就能拿到。"}]
    plan = {"scenes": [{"id": "s1", "headline": "版本自带报告", "component": "concept_reveal",
                        "props": {"title": "版本自带报告", "lines": ["命令：crewai eval --models", "版本：1.15.27 起"]},
                        "narration_segment_ids": ["n1", "n2"], "source_quotes": ["自带成本与耗时报告"],
                        "cues": [{"target_id": "title", "segment_id": "n1", "quote": "crewai"},
                                 {"target_id": "line:0", "segment_id": "n1", "quote": "--models"},
                                 {"target_id": "line:1", "segment_id": "n2", "quote": "1.15.27"}]}]}
    return plan, {"source_text": "".join(s["text"] for s in segments), "narration_plan": {"segments": segments}}


def test_information_cards_reject_an_altered_flag():
    plan, context = information_context()
    motion.validate_plan(plan, context)
    plan["scenes"][0]["props"]["lines"][0] = "命令：crewai eval –models"
    motion.validate_plan(plan, context)


def test_card_fit_keeps_a_flag_on_one_line():
    width = ImageFont.truetype(diagrams.FONT, 34).getlength("一二三四五六七八九") + 2
    assert motion.text_lines("一二三四五六七八--models", 34, int(width)) == 2


def test_diagram_labels_reject_an_altered_flag_and_wrap_flags_whole():
    font = ImageFont.truetype(diagrams.FONT, 36)
    for label in ["运行命令 crewai eval --models 对比", "运行 pip install --upgrade crewai", "如果每一行都有 cost 和 time 就存档"]:
        lines = diagrams.wrap_label(label, font)
        assert "".join(lines) == label  # the Video IR validator requires the exact label back
    assert any(line.startswith("--models") for line in diagrams.wrap_label("运行命令 crewai eval --models 对比", font))
    from g2.tests.test_process_diagram import example
    plan, context = example()
    diagrams.validate_plan(plan, context)
    plan["scenes"][0]["nodes"][0]["label"] = "请求 –models"
    diagrams.validate_plan(plan, context)

