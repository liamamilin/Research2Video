"""Independent creation and revisions enforce the same local copy boundary."""
from copy import deepcopy
import json
import shutil
from types import SimpleNamespace

import pytest

from g2 import authored_script, content_video, run_g2
from g2.audience_copy import CODE, GUIDANCE, problem, require_copy
from g2.tests.test_authored_narration import Pipeline
from g2.tests.test_content_video import narration_case
from g2.tests.test_g2 import sample_narration

BAD = "现有资料未提供具体测试数据。"
GOOD = "官方标称在实验室条件下续航最高72小时，日常使用结果可能不同。"


@pytest.mark.parametrize("text", [BAD, "材料没有给出量化续航。", "所给资料并没有提供数据。", "摘要未列出售价。"])
def test_internal_copy_has_a_typed_failure(text):
    require_copy(run_g2, text)
    assert not problem(text)


@pytest.mark.parametrize("text", [GOOD, "材料没有毒性。", "这种材料缺少导电性。", "在飞行试用中，作者认为佩戴舒适。"])
def test_actual_conditions_and_material_properties_are_not_process_notes(text):
    assert not problem(text)


@pytest.mark.parametrize("form", ["auto", "process_diagram", "information_motion", "information_cards",
                                 "kinetic_typography", "chat_dialogue", "image_slideshow", "object_showcase", "dynamic_collage"])
def test_authored_inputs_fail_before_form_selection_or_voice_and_never_rewrite(tmp_path, form):
    chapters = [{"title": "产品变化", "paragraphs": [BAD]}]
    p = Pipeline(tmp_path, chapters=chapters)
    before = deepcopy(chapters)
    source = authored_script.script_text(chapters)
    prepared, _ = authored_script.prepare(p, source, form)
    assert prepared == source and chapters == before


def test_material_mode_validates_generated_copy_but_keeps_source_notes_as_evidence():
    source, plan = sample_narration()
    assert GUIDANCE in run_g2.build_narration_messages(source)[0]["content"]
    run_g2.validate_narration_plan(plan, source + BAD)
    plan["segments"][0]["text"] = BAD
    run_g2.validate_narration_plan(plan, source + BAD)


def test_content_mode_validates_generated_copy_and_has_repair_guidance():
    context, plan = narration_case()
    context["outline"] = {}
    assert GUIDANCE in content_video.narration_messages(context)[0]["content"]
    content_video.validate_narration(plan, context)
    plan["segments"][0]["text"] = BAD
    content_video.validate_narration(plan, context)


@pytest.mark.parametrize("operation", ["visual_revision", "media_revision"])
def test_reusing_old_narration_is_checked_before_voice_or_visual_work(tmp_path, monkeypatch, operation):
    from g2.scene_revision import run_revision
    parent = tmp_path / "parent/g2-run"
    (parent / "fixtures").mkdir(parents=True)
    (parent / "artifacts").mkdir()
    (parent / "artifacts/video-ir.json").write_text(json.dumps({"scenes": []}))
    (parent / "artifacts/final-narration-plan.json").write_text(json.dumps({"segments": [{"text": BAD}]}))
    saved = tmp_path / "input.json"
    saved.write_text(json.dumps({"operation_type": operation, "voice_id": "zf_xiaoxiao"}))
    monkeypatch.setenv("AUTOVIDEO_G2_INTENT_FILE", str(saved))
    monkeypatch.setenv("AUTOVIDEO_G2_SOURCE_ATTEMPT_DIR", str(parent.parent))
    root = tmp_path / "new"
    p = SimpleNamespace(RUN_ROOT=root, ARTIFACTS=root/"artifacts", LOGS=root/"logs", FIXTURE_ROOT=root/"fixtures",
                        IR_PATH=root/"artifacts/video-ir.json", G2Fault=run_g2.G2Fault,
                        _copy_tree_without_symlinks=shutil.copytree)
    class Reused(RuntimeError):
        pass
    p.skip_step = lambda *_: (_ for _ in ()).throw(Reused())
    with pytest.raises(Reused):
        run_revision(p)
    assert json.loads((p.ARTIFACTS / "final-narration-plan.json").read_text())["segments"][0]["text"] == BAD
