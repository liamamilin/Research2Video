"""Author requests stay free of production-split memos and collector bookkeeping."""
from core import report_material
from core.video import author_input_gates, joint_planning as joint, writer
from tests.test_video_scripts import llm, script_json, video_env  # noqa: F401


def test_scrub_removes_joint_split_and_packet_bookmarks():
    dirty = (
        "面向观众讲清变化。\n"
        "本条只回答：怎么用。观众收获：合集只报上线；单条给出操作。\n"
        "同批合集仅介绍：某产品上线了。这条展开已分配的独立问题，发布概况只作必要背景。\n"
        "材料编号：Mabc；来源角色：editorial。\n"
        "采集状态：新增材料；页面变化不等于事件刚刚发生。\n"
    )
    cleaned, found = author_input_gates.scrub(dirty)
    assert "同批合集" in found and "发布概况只作必要背景" in found
    assert "材料编号" in found and "采集状态" in found
    author_input_gates.require_clean(cleaned)
    assert "同批合集" not in cleaned
    assert "发布概况只作必要背景" not in cleaned
    assert "材料编号" not in cleaned


def test_writing_direction_and_for_narration_pass_the_gate():
    single = {
        "question": "ChatGPT 实时听写怎么用",
        "incremental_value": "合集只报功能上线；单条给出点麦克风后的操作",
        "audience": "常用语音输入的人",
        "angle": "howto",
        "material": "点麦克风",
        "source_ids": ["S1"],
    }
    direction = joint.writing_direction(single)
    author_input_gates.require_clean(direction)
    assert "同批合集" not in direction and "合集只" not in direction
    author_input_gates.require_clean(joint.writing_direction(role="episode"))
    packet = (
        "### [Title](https://example.com/a)\n"
        "材料编号：Mabc；来源角色：editorial。\n"
        "采集状态：新增材料；页面变化不等于事件刚刚发生。\n"
        "正文还在。\n"
    )
    spoken = report_material.for_narration(packet)
    author_input_gates.require_clean(spoken)
    assert "正文还在" in spoken


def test_write_scrubs_legacy_split_direction_before_the_model(video_env, llm):
    dirty = (
        "讲清这件新事。\n"
        "同批合集仅介绍：旧摘要。这条展开已分配的独立问题，发布概况只作必要背景。"
    )
    llm.replies = [script_json(200)]
    written = writer.write(
        report="### [产品](https://example.com/p)\n产品更新了导出。",
        citation_check=None,
        topic={"title": "导出怎么用", "fields": {"目标受众": "整理文件的人"}},
        column_style=dirty,
        target_minutes=1.25,
        llm_config=object(),
    )
    assert written.meta.get("author_input_scrubbed")
    author_input_gates.require_clean(written.meta["creative_direction"])
    assert "同批合集" not in written.meta["creative_direction"]
    assert "发布概况只作必要背景" not in llm.calls[0][1]["content"]
