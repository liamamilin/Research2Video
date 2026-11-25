"""The account's narrator (core/video/persona.py): identity without a compulsory introduction or fake personal tests."""

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


def test_persona_keeps_identity_and_farewell_without_an_opening_template():
    text = narrator.prompt(LUOLUO)
    assert "洛洛" in text and LUOLUO["farewell"] in text
    assert narrator.prompt({}) == ""
    assert LUOLUO["farewell"] not in narrator.prompt(LUOLUO, opening_only=True)


def _paragraphs(*texts):
    return [SimpleNamespace(text=t) for t in texts]


@pytest.mark.parametrize("texts, expected", [
    (("先看一张毕业照。", "我是洛洛。", "女孩接受采访。"), False),
    (("降价了。我是洛洛。这笔账怎么算？",), False),
    (("先看结果。",), False),
    (("大家好，我是洛洛。今天看看原因。",), False),
    (("先看结果。我是洛洛。", "我是洛洛，今天看看原因。"), True),
])
def test_identity_is_optional_but_not_repeated(texts, expected):
    assert narrator.opening_needs_introduction(_paragraphs(*texts), LUOLUO) is expected
    assert not narrator.opening_needs_introduction(_paragraphs(*texts), {})


def test_configured_identity_and_greeting_do_not_impose_an_opening_order():
    persona = {"name": "阿青", "introduce": "这里是阿青。", "farewell": "下次见。", "opening_greeting": "各位好"}
    assert "阿青" in narrator.prompt(persona)
    for text in ("结果出乎意料。", "这里是阿青。", "各位好，这里是阿青。结果出乎意料。"):
        assert not narrator.opening_needs_introduction(_paragraphs(text), persona)
    assert narrator.opening_needs_introduction(_paragraphs("这里是阿青。", "这里是阿青。"), persona)


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
def test_bare_or_missing_introduction_does_not_rewrite_the_opening(llm, monkeypatch, intro):
    monkeypatch.setattr(narrator, "load", lambda config_dir=None: LUOLUO)
    payload = json.loads(script_json(300))
    opening = [{"text": "同样的预算，能跑多少次？"}, {"text": intro}]
    payload["sections"][0]["paragraphs"] = opening
    llm.replies = [json.dumps(payload, ensure_ascii=False)]
    written = writer.write(report=REPORT, citation_check=None, topic={"title": "本期"}, column_style="",
                           target_minutes=1, llm_config=None)
    assert [p["text"] for p in written.sections[0]["paragraphs"]] == [p["text"] for p in opening]
    assert len(llm.calls) == 1
    assert written.meta["introduction"] == {"status": "authored", "requests": 0}


def test_repeated_identity_is_repaired_once_without_rewriting_other_sections(llm, monkeypatch):
    monkeypatch.setattr(narrator, "load", lambda config_dir=None: LUOLUO)
    payload = json.loads(script_json(300))
    payload["sections"][0]["paragraphs"] = [{"text": "我是洛洛。先看两个数。"}, {"text": "大家好，我是洛洛。现在看结果。"}]
    revised = {"title": "model changed heading", "paragraphs": [{"text": "我是洛洛。先看两个数。现在看结果。"}]}
    llm.replies = [json.dumps(payload, ensure_ascii=False), json.dumps(revised, ensure_ascii=False)]
    written = writer.write(report=REPORT, citation_check=None, topic={"title": "本期"}, column_style="",
                           target_minutes=1, llm_config=None)
    assert written.sections[0]["paragraphs"][0]["text"] == revised["paragraphs"][0]["text"]
    assert written.sections[0]["title"] == payload["sections"][0]["title"]
    assert written.sections[1]["paragraphs"][0]["text"] == payload["sections"][1]["paragraphs"][0]["text"]
    assert len(llm.calls) == 2


def test_an_unsuccessful_opening_rewrite_preserves_the_draft_without_looping():
    from core.llm import ChatResponse
    source = writer._parse(script_json(300))
    source.sections[0].paragraphs[0].text = "我是洛洛。先看结果。我是洛洛。"
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
    assert writer.OPENING_RULE in writer.SYSTEM_PROMPT and writer.OPENING_RULE in writer.EPISODE_PROMPT
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
