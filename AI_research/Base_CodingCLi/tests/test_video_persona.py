"""The account's narrator (core/video/persona.py): first person, introduced right after the hook, never faking tests."""

from __future__ import annotations

import json

import pytest
from types import SimpleNamespace

from core.video import writer
from core.video import persona as narrator
from tests.test_video_scripts import REPORT, llm, script_json  # noqa: F401

LUOLUO = {"name": "洛洛", "introduce": "我是洛洛。", "farewell": "我是洛洛，我们下期见。"}


def test_the_persona_file_names_the_narrator_and_its_absence_turns_it_off(tmp_path):
    from importlib import reload
    import core.video.persona as real
    real = reload(real)  # the conftest stub replaced load on the module object; read the file itself here
    assert real.load(str(tmp_path)) == {}
    (tmp_path / "persona.yaml").write_text("name: 洛洛\n", encoding="utf-8")
    assert real.load(str(tmp_path)) == LUOLUO
    (tmp_path / "persona.yaml").write_text("name: ''\n", encoding="utf-8")
    assert real.load(str(tmp_path)) == {}


def test_the_prompt_asks_for_the_first_person_and_forbids_claiming_tests():
    text = narrator.prompt(LUOLUO)
    assert "第一人称" in text and "大家好，我是洛洛，今天我们聊聊" in text and "我是洛洛，我们下期见。" in text
    assert "不把名字单独写成一句或一段" in text
    assert "不说“我实测”" in text and "据某某报道" in text
    assert narrator.prompt({}) == ""
    opening = narrator.prompt(LUOLUO, opening_only=True)
    assert "大家好，我是洛洛，今天我们聊聊" in opening
    assert LUOLUO["farewell"] not in opening


def _paragraphs(*texts):
    return [SimpleNamespace(text=t) for t in texts]


@pytest.mark.parametrize("texts, expected", [
    (("先看一张毕业照。", "我是洛洛。", "女孩接受采访。"), True),
    (("降价了。我是洛洛。这笔账怎么算？",), True),
    (("先看这个结果。", "大家好，我是洛洛。"), True),
    (("大家好，我是洛洛。你是不是也遇到过这种情况？",), True),
    (("先看这个结果。", "大家好，我是洛洛。", "今天讲讲原因。"), True),
    (("同一段代码，11 月跑和 12 月跑，账单能差一倍。", "大家好，我是洛洛。今天聊聊你的账单和流水线。"), False),
    (("一个问题。", "先说背景。", "我是洛洛，今天聊聊这个问题。"), False),
    (("先看一个数字。", "大家好，我是洛洛，今天算算每次任务的花费。"), False),
    (("一键就能完成吗？", "大家好，我是洛洛，今天用同一份表格走一遍操作。"), False),
    (("先看结果。",), True),
])
def test_opening_identity_is_kept_with_its_topic(texts, expected):
    assert narrator.opening_needs_introduction(_paragraphs(*texts), LUOLUO) is expected
    assert not narrator.opening_needs_introduction(_paragraphs(*texts), {})


def test_a_configured_identity_is_not_replaced_with_luoluo():
    persona = {"name": "阿青", "introduce": "这里是阿青。", "farewell": "下次见。"}
    assert "大家好，这里是阿青，今天" in narrator.prompt(persona)
    assert narrator.opening_needs_introduction(_paragraphs("结果出乎意料。", "这里是阿青。"), persona)
    assert not narrator.opening_needs_introduction(_paragraphs("结果出乎意料。", "这里是阿青，今天看看原因。"), persona)


@pytest.mark.parametrize("opening, expected", [
    ("大家好，我是洛洛，今天来看看账单。", True),
    ("账单翻倍了。大家好，我是洛洛，今天来看看账单。", False),
    ("账单翻倍了。我是洛洛，今天来看看账单。", True),
    ("账单翻倍了。大家好，我是洛洛。", True),
    ("账单翻倍了。大家好，我是洛洛，今天聊账单。我是洛洛。", True),
])
def test_configured_greeting_follows_the_hook_and_flows_into_topic(opening, expected):
    persona = {**LUOLUO, "opening_greeting": "大家好"}
    assert narrator.opening_needs_introduction([{"text": opening}], persona) is expected


@pytest.mark.parametrize("separator", ["，", "。"])
def test_a_written_script_keeps_a_natural_introduction_without_an_extra_call(llm, monkeypatch, separator):
    monkeypatch.setattr(narrator, "load", lambda config_dir=None: LUOLUO)
    payload = json.loads(script_json(300))
    opening = f"先看两个数。大家好，我是洛洛{separator}今天看看三家最高档为什么差了十倍。"
    payload["sections"][0]["paragraphs"][0]["text"] = opening
    llm.replies = [json.dumps(payload, ensure_ascii=False)]
    written = writer.write(report=REPORT, citation_check=None, topic={"title": "本期"}, column_style="",
                           target_minutes=1, llm_config=None)
    assert written.sections[0]["paragraphs"][0]["text"] == opening
    assert len(llm.calls) == 1
    assert "你是这个账号固定的讲述者「洛洛」" in llm.calls[0][0]["content"]


@pytest.mark.parametrize("intro", ["我是洛洛。", "先看背景。"])
def test_bare_or_missing_introduction_is_authored_in_context_not_spliced(llm, monkeypatch, intro):
    monkeypatch.setattr(narrator, "load", lambda config_dir=None: LUOLUO)
    payload = json.loads(script_json(300))
    payload["sections"][0]["paragraphs"] = [{"text": "同样的预算，能跑多少次？"}, {"text": intro}]
    revised = {"title": "模型试图换标题", "paragraphs": [
        {"text": "同样的预算，能跑多少次？"},
        {"text": "大家好，我是洛洛，今天算算每次运行的花费，再看哪些任务更划算。"}]}
    llm.replies = [json.dumps(payload, ensure_ascii=False), json.dumps(revised, ensure_ascii=False)]
    written = writer.write(report=REPORT, citation_check=None, topic={"title": "本期"}, column_style="",
                           target_minutes=1, llm_config=None)
    assert written.sections[0]["paragraphs"][1]["text"] == revised["paragraphs"][1]["text"]
    assert written.sections[0]["title"] == payload["sections"][0]["title"]
    assert written.sections[1]["paragraphs"][0]["text"] == payload["sections"][1]["paragraphs"][0]["text"]
    assert len(llm.calls) == 2
    assert written.meta["introduction"] == {"status": "rewritten", "requests": 1}


def test_an_unsuccessful_opening_rewrite_preserves_the_draft_without_looping():
    from core.llm import ChatResponse
    source = writer._parse(script_json(300))
    client = SimpleNamespace(chat=lambda *args, **kwargs: ChatResponse(content='not JSON'))
    result, receipt = writer.introduce_opening(source, LUOLUO, client)
    assert result is source
    assert receipt == {"status": "unchanged", "requests": 1, "reason": "ScriptError"}


def test_a_note_to_the_picture_is_rewritten_as_narration_and_others_are_reported():
    assert writer.spoken_only("画面里可以是一个人往空杯子里倒水，杯子空。") == "想象一个人往空杯子里倒水，杯子空。"
    assert writer.spoken_only("先说结论。画面还是那杯水，水快满了。") == "先说结论。还是那杯水，水快满了。"
    keep = "把镜头拉远一点。它在画面上会出现难看的网格。"
    assert writer.spoken_only(keep) == keep
    sections = [{"title": "s", "paragraphs": [{"text": keep}]}]
    assert not any("给画面的说明" in p for p in writer.lint(sections))
    sections[0]["paragraphs"][0]["text"] = "先讲道理。这里画面切到工厂。"
    assert any("给画面的说明" in p for p in writer.lint(sections))
    assert writer.PICTURE_RULE in writer.SYSTEM_PROMPT and writer.PICTURE_RULE in writer.EPISODE_PROMPT


def test_a_written_script_never_keeps_the_picture_note(llm):
    payload = json.loads(script_json(300))
    payload["sections"][0]["paragraphs"][0]["text"] = "画面里可以是一个人往空杯子里倒水。"
    llm.replies = [json.dumps(payload, ensure_ascii=False)]
    written = writer.write(report=REPORT, citation_check=None, topic={"title": "本期"}, column_style="",
                           target_minutes=1, llm_config=None)
    assert written.sections[0]["paragraphs"][0]["text"] == "想象一个人往空杯子里倒水。"


def test_a_series_item_promises_its_next_episode(llm):
    llm.replies = [script_json(300)]
    writer.write(report=REPORT, citation_check=None, topic={"title": "本期", "next_episode": "额度没用完为什么停了"},
                 column_style="", target_minutes=1, llm_config=None)
    assert "「额度没用完为什么停了」" in llm.calls[0][0]["content"] and "道别前用一句话预告它" in llm.calls[0][0]["content"]


def test_hooks_stay_with_the_viewer_and_the_named_angle_is_kept():
    assert "不用和观众无关的全球或海外统计开场" in writer.OPENING_RULE
    assert writer.ANGLE_RULE in writer.SYSTEM_PROMPT and writer.ANGLE_RULE in writer.EPISODE_PROMPT


def test_the_chatgpt_matrix_does_not_assign_every_script_an_access_preamble():
    from core import matrices
    from core.video import content_columns, production
    from core.video import columns as video_columns

    assert not matrices.load_all("config")["chatgpt_acquisition"].content.get("premise")
    config = production.get("config", "jobs", "chatgpt_t05_comparison")
    context = content_columns.snapshot("config", config.key, config.job)
    assert not context.get("premise")
    column = content_columns.apply_script_context(video_columns.get("config", config.recipe), context)
    assert "观众前提：" not in column.script_style


def test_a_matrix_premise_must_be_text():
    import pytest
    from core import matrices

    assert matrices.content_metadata({"premise": "x"}) == {"premise": "x"}
    with pytest.raises(ValueError):
        matrices.content_metadata({"premise": ["x"]})
