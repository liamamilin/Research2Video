"""Screen layouts and reveals must fit and follow the measured voice."""
import copy

from PIL import Image, ImageChops
import pytest

from g2 import card_editorial as editorial
from g2.fast_cards import draw_card, point_windows
from g2.production_plan import STYLES


def context():
    return {"groups": [{"id": "card_1", "title": "这台电脑能做什么", "chapter_id": "c1",
                        "segment_ids": ["n1", "n2"], "text": "先看本地运行。然后看云端运行。", "overview": []}]}


def test_model_layout_and_speech_binding_survive_compilation():
    raw = {"pages": [{"id": "card_1", "layout": "comparison", "heading": "在哪运行？", "points": [
        {"label": "本地", "text": "电脑上运行", "segment_id": "n1"},
        {"label": "云端", "text": "服务器上运行", "segment_id": "n2"}]}]}
    expanded = editorial.expand_selection(raw, context())
    editorial.validate(expanded, context())
    board, pages = editorial.compile_pages(expanded, context()["groups"])
    assert board["scenes"][0]["props"]["title"] == "在哪运行？"
    assert pages[0]["layout"] == "comparison"
    assert pages[0]["points"][1]["segment_id"] == "n2"
    assert board["scenes"][0]["props"]["points"][1]["segment_id"] == "n2"
    assert board["scenes"][0]["props"]["layout"] == "comparison"
    wrong = copy.deepcopy(expanded)
    wrong["pages"][0]["points"][0]["segment_id"] = "other_chapter_n1"
    with pytest.raises(ValueError, match="本页的旁白"):
        editorial.validate(wrong, context())


def test_reveals_use_measured_speech_and_preserve_every_frame():
    page = {"layout": "comparison", "points": [{"segment_id": "n1"}, {"segment_id": "n2"}]}
    windows = point_windows(page, {"start_frame": 60, "end_frame": 210}, [
        {"id": "n1", "start_ms": 2000}, {"id": "n2", "start_ms": 4300}])
    assert windows == [{"start_frame": 60, "end_frame": 129, "visible": [0]},
                       {"start_frame": 129, "end_frame": 210, "visible": [0, 1]}]
    assert sum(w["end_frame"] - w["start_frame"] for w in windows) == 150
    assert point_windows(None, {"start_frame": 60, "end_frame": 210}, []) == [
        {"start_frame": 60, "end_frame": 210, "visible": None}]


def test_readable_copy_is_not_rejected_by_an_arbitrary_character_limit():
    from jsonschema import Draft202012Validator
    import json
    from pathlib import Path
    points = [{"label": "本地运行", "text": "模型留在自己的电脑上运行，文件和修改记录一起留在项目里，打开工作区就能继续。"},
              {"label": "云端运行", "text": "任务交给服务器继续执行，关闭浏览器后仍能运行，再打开页面查看已经完成的步骤。"}]
    assert sum(len(p["label"] + p["text"]) for p in points) > 80
    editorial.validate({"pages": [{"id": "card_1", "layout": "comparison", "points": points}]}, context())
    board, _ = editorial.compile_pages({"pages": [{"id": "card_1", "layout": "comparison", "points": points}]}, context()["groups"])
    schema = json.loads((Path(__file__).parents[2] / 'schemas/components/kinetic_text.schema.json').read_text())
    Draft202012Validator(schema).validate(board["scenes"][0]["props"])
    points[0]["text"] *= 40
    with pytest.raises(ValueError, match="排版"):
        editorial.validate({"pages": [{"id": "card_1", "layout": "comparison", "points": points}]}, context())


def test_compiled_card_keeps_layout_copy_and_local_speech_references(tmp_path):
    from g2.tests.test_creative_forms import creative_ir
    from g1.video_ir import validate_video_ir, ValidationFault

    ir, root = creative_ir(tmp_path, 'information_cards')
    props = {'title': '本地与云端', 'layout': 'comparison', 'points': [
        {'text': '模型在自己的电脑上运行，文件和修改记录一起留在项目中，打开工作区就能继续处理。', 'segment_id': 'n1'},
        {'text': '任务交给服务器继续执行，关闭浏览器后仍能继续运行，回来以后可以直接查看已经完成的全部步骤。'}]}
    props['text'] = '\n'.join(point['text'] for point in props['points'])
    assert len(props['text']) > 80
    ir['scenes'][0]['props'] = props
    assert validate_video_ir(ir, root)['total_frames'] == 30
    props['points'][0]['segment_id'] = 'unknown'
    with pytest.raises(ValidationFault, match='CARD_SEGMENT_MISMATCH'):
        validate_video_ir(ir, root)
    props['points'][0]['segment_id'] = 'n1'
    props['text'] = 'different text'
    with pytest.raises(ValidationFault, match='CARD_TEXT_MISMATCH'):
        validate_video_ir(ir, root)


@pytest.mark.parametrize("layout,points", [
    ("focus", [{"text": "模型就在你的电脑上运行", "label": "本地 AI"}]),
    ("comparison", [{"text": "电脑上运行", "label": "本地"}, {"text": "服务器上运行", "label": "云端"}]),
    ("steps", [{"text": "选择项目"}, {"text": "打开文件"}, {"text": "运行程序"}]),
    ("notes", [{"text": "项目文件"}, {"text": "运行结果"}, {"text": "修改记录"}, {"text": "下一步工作"}]),
])
def test_visual_components_fit_and_reveal_without_changing_screen_copy(tmp_path, layout, points):
    page = {"layout": layout, "points": points}
    scene = {"props": {"title": "理解运行方式", "text": "\n".join(p["text"] for p in points)}}
    args = dict(title="一个实际问题", index=0, count=4, style=STYLES["editorial_light"],
                badge={}, notices=[], editorial=page)
    first, final = tmp_path / "first.png", tmp_path / "final.png"
    draw_card(scene, **args, output=first, visible_points=set())
    result = draw_card(scene, **args, output=final)
    assert result["body_text_unchanged"]
    assert Image.open(final).size == (1920, 1080)
    assert ImageChops.difference(Image.open(first), Image.open(final)).getbbox() is not None
