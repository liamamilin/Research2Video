"""The account's narrator (core/video/persona.py): first person, introduced right after the hook, never faking tests."""

from __future__ import annotations

import json
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
    assert "第一人称" in text and "以「我是洛洛，」开头" in text and "我是洛洛，我们下期见。" in text
    assert "不说“我实测”" in text and "据某某报道" in text
    assert narrator.prompt({}) == ""


def _paragraphs(*texts):
    return [SimpleNamespace(text=t) for t in texts]


def test_the_introduction_follows_the_first_sentence_only_when_the_author_left_it_out():
    hook = _paragraphs("降价20%，跑一个任务却要26.5美元。这笔账先别急着说划算。", "第二段。")
    assert narrator.ensure_introduced(hook, LUOLUO)
    assert hook[0].text == "降价20%，跑一个任务却要26.5美元。我是洛洛。这笔账先别急着说划算。"
    said = _paragraphs("先看一个数字。", "我是洛洛，这期帮你算清楚。")
    assert not narrator.ensure_introduced(said, LUOLUO) and said[0].text == "先看一个数字。"
    single = _paragraphs("只有一句话没有句号")
    narrator.ensure_introduced(single, LUOLUO)
    assert single[0].text == "只有一句话没有句号我是洛洛。"
    assert not narrator.ensure_introduced(_paragraphs("一句。"), {})


def test_a_written_script_is_told_by_the_narrator(llm, monkeypatch):
    monkeypatch.setattr(narrator, "load", lambda config_dir=None: LUOLUO)
    payload = json.loads(script_json(300))
    payload["sections"][0]["paragraphs"][0]["text"] = "先看两个数。三家最高档差了十倍。"
    llm.replies = [json.dumps(payload, ensure_ascii=False)]
    written = writer.write(report=REPORT, citation_check=None, topic={"title": "本期"}, column_style="",
                           target_minutes=1, llm_config=None)
    assert written.sections[0]["paragraphs"][0]["text"].startswith("先看两个数。我是洛洛。三家最高档")
    assert "你是这个账号固定的讲述者「洛洛」" in llm.calls[0][0]["content"]


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


def test_the_chatgpt_matrix_tells_every_script_how_its_viewers_can_use_chatgpt():
    from core import matrices
    from core.video import content_columns, production
    from core.video import columns as video_columns

    premise = matrices.load_all("config")["chatgpt_acquisition"].content["premise"]
    assert "国内应用商店下载不到" in premise and "电脑网页端" in premise
    config = production.get("config", "jobs", "chatgpt_t05_comparison")
    context = content_columns.snapshot("config", config.key, config.job)
    assert context["premise"] == premise
    column = content_columns.apply_script_context(video_columns.get("config", config.recipe), context)
    assert "观众前提：" + premise in column.script_style
    with open("jobs/chatgpt_acquisition/t05_comparison.yaml", encoding="utf-8") as fh:
        assert "不假设观众已经在手机上装好了 ChatGPT" in fh.read()


def test_a_matrix_premise_must_be_text():
    import pytest
    from core import matrices

    assert matrices.content_metadata({"premise": "x"}) == {"premise": "x"}
    with pytest.raises(ValueError):
        matrices.content_metadata({"premise": ["x"]})
