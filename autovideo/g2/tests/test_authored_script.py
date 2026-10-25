import re
import pytest
from g2.authored_script import split_script
from g2 import run_g2 as pipeline


def test_authored_script_preserves_repetition_punctuation_and_all_words():
    text="先说明目标。\n\n先说明目标。背景：用于新同事培训；标准：列出待确认事项！"
    parts=split_script(text)
    assert parts.count("先说明目标。") == 2
    assert re.sub(r"\s","","".join(parts))==re.sub(r"\s","",text)


def test_long_paragraphs_are_caption_sized_without_clipping():
    text=("需要保留完整信息，不能截断旁白。"*30)+"结束。"
    parts=split_script(text)
    assert max(map(len,parts))<=80
    assert "".join(parts)==text
    with pytest.raises(ValueError):split_script(" \n ")


def test_custom_optional_intent_is_forwarded_and_empty_intent_does_not_change_messages(tmp_path,monkeypatch):
    source=[{"role":"system","content":"规则"},{"role":"user","content":"素材"}]
    monkeypatch.setattr(pipeline,"PROJECT",tmp_path)
    path=tmp_path/"input.json";monkeypatch.setenv("AUTOVIDEO_G2_INTENT_FILE",str(path))
    path.write_text('{"creative_intent": {}}')
    assert pipeline.creative_messages(source)==source
    path.write_text('{"creative_intent": {"audience":"自定义受众","focus":"保留边界"}}')
    result=pipeline.creative_messages(source)
    assert '自定义受众' in result[-1]["content"]
    assert result[1]==source[1]


def test_natural_script_duration_extension_does_not_relax_legacy_timeline(tmp_path):
    from g2.tests.test_information_motion import information_ir
    from g1.video_ir import validate_video_ir, ValidationFault
    root,ir=information_ir(tmp_path)
    ir["video"]["duration_ms"]=ir["scenes"][0]["duration_ms"]=900
    ir["narration"]["segments"][0].update(start_ms=100,end_ms=600)
    ir["content_structure"]={"mode":"content","chapters":[{"id":"c1","title":"短讲解","narration_segment_ids":["n1"]}]}
    assert validate_video_ir(ir,root)["total_frames"]==27
    ir.pop("content_structure")
    with pytest.raises(ValidationFault,match="SCHEMA_VALIDATION"):
        validate_video_ir(ir,root)


def test_split_marked_flags_the_piece_that_opens_each_paragraph():
    from g2.authored_script import split_marked
    marked = split_marked("先说钱。缓存有两个价。\n再说账？没有。\n\n第三段只有一句。")
    assert [text for text, _ in marked] == ["先说钱。", "缓存有两个价。", "再说账？", "没有。", "第三段只有一句。"]
    assert [opens for _, opens in marked] == [True, False, True, False, True]
    assert split_script("先说钱。缓存有两个价。\n再说账？没有。") == [text for text, _ in split_marked("先说钱。缓存有两个价。\n再说账？没有。")]
