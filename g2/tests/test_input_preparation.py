import copy
import pytest
from g2 import input_preparation as prep
from g2.content_video import source_units

def case():
    source="目标是交付一页摘要。\n目标是交付一页摘要。\n第1页。\n缺少的信息标明待确认。"
    ctx={"source_text":source,"units":source_units(source)}
    plan={"status":"ready","recommended_form":"information_motion","reason":"并列的任务说明适合卡片", "ordered_unit_ids":["u1","u4"],"excluded_units":[{"unit_id":"u2","reason":"duplicate","duplicate_of":"u1"},{"unit_id":"u3","reason":"paste_noise","duplicate_of":None}],"issues":[{"kind":"duplicate","message":"保留一次重复内容","source_quotes":["目标是交付一页摘要。"],"blocking":False}]}
    return plan,ctx

def test_every_unit_accounted_and_exact_duplicate_only():
    plan,ctx=case();prep.validate_plan(plan,ctx)
    plan["excluded_units"][0]["duplicate_of"]="u4"
    with pytest.raises(prep.InputFault,match="INPUT_DUPLICATE_REFERENCE"):prep.validate_plan(plan,ctx)

@pytest.mark.parametrize("change,code",[
 (lambda p:p["ordered_unit_ids"].pop(),"INPUT_SOURCE_COVERAGE"),
 (lambda p:p["issues"][0].update(source_quotes=["虚构的原文"]),"INPUT_SOURCE_QUOTE"),
 (lambda p:p.update(status="needs_input"),"INPUT_STATUS"),
])
def test_bad_coverage_provenance_and_status_rejected(change,code):
    plan,ctx=case();change(plan)
    with pytest.raises(prep.InputFault,match=code):prep.validate_plan(plan,ctx)

def test_reordering_keeps_original_units_and_blocking_issue_is_explicit():
    plan,ctx=case();plan["ordered_unit_ids"].reverse();prep.validate_plan(plan,ctx)
    plan["status"]="needs_input";plan["issues"][0].update(kind="unclear",blocking=True)
    prep.validate_plan(plan,ctx)

def test_manual_override_and_original_source_are_preserved(tmp_path):
    import json
    plan,ctx=case()
    class Pipeline:
        ARTIFACTS=tmp_path
        @staticmethod
        def generate_validated_plan(stage,builder,schema,validator,context):
            validator(plan,context);return copy.deepcopy(plan)
        @staticmethod
        def write_json(path,value):path.write_text(json.dumps(value))
    prepared,brief=prep.prepare(Pipeline,ctx["source_text"],"process_diagram")
    assert brief["selected_form"]=="process_diagram"
    assert brief["recommended_form"]=="information_motion"
    assert prepared.count("目标是交付一页摘要。") == 1
    assert "第1页" not in prepared
    assert brief["source_units"]==ctx["units"]
    assert brief["source_rewritten"] is False


def test_editorial_notes_are_explicitly_accounted_for():
    plan,ctx=case();plan["excluded_units"][1]["reason"]="editorial_note"
    prep.validate_plan(plan,ctx)
    assert plan["excluded_units"][1]["unit_id"]=="u3"
