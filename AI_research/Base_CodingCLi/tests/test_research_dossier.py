"""Research explanations and original details survive the AI author boundary."""
import json

import pytest

from core import report_material, report_meta
from core.video import research_dossier as dossier, store, story_pool, writer
from tests.test_video_scripts import llm, script_json, video_env  # noqa: F401
from web.services import video_workflow as workflow

PRIMARY = "https://project.example/releases/1"
OTHER = "https://other.example/news"
REPORT = f"### 2.1 主项目\n只在底稿出现的机制解释：反馈如何使下一步输入改变。{PRIMARY}"
MATERIAL = f"### [原始项目]({PRIMARY})\n只在原文出现的例子：输入代码、返回补丁、再次执行测试。"


@pytest.mark.parametrize("production", dossier.PRODUCTIONS)
def test_ai_bindings_deliver_both_inputs_once(video_env, llm, monkeypatch, production):
    path = video_env / "dossier.md"
    path.write_text(REPORT)
    ref = report_material.save(str(path), REPORT, [MATERIAL])
    monkeypatch.setattr(report_meta, "get", lambda _: {"material": ref})
    llm.replies = [script_json(300, citations={(0, 0): [PRIMARY]})]
    script = store.create_script("report_digest", {"title": "主项目", "from_report": str(path)}, production=production)
    assert workflow.run(script["id"], llm_config=object()) == "DRAFT"
    assert len(llm.calls) == 1
    request = llm.calls[0][1]["content"]
    assert "## 本期研究底稿" in request and REPORT in request
    assert "## 对应原始材料" in request and MATERIAL in request
    meta = store.get_version(script["id"], 1)["meta"]
    assert meta["input_kind"] == "report_and_original_material"
    assert meta["research_dossier"]["report_chars"] == len(REPORT)
    assert meta["research_dossier"]["material_chars"] == len(MATERIAL)


def test_other_binding_keeps_original_single_input(video_env, llm, monkeypatch):
    path = video_env / "ordinary.md"
    path.write_text(REPORT)
    ref = report_material.save(str(path), REPORT, [MATERIAL])
    monkeypatch.setattr(report_meta, "get", lambda _: {"material": ref})
    llm.replies = [script_json(300)]
    script = store.create_script("report_digest", {"title": "原入口", "from_report": str(path)},
                                 production="science_daily")
    assert workflow.run(script["id"], llm_config=object()) == "DRAFT"
    request = llm.calls[0][1]["content"]
    assert MATERIAL in request and REPORT not in request
    assert "research_dossier" not in store.get_version(script["id"], 1)["meta"]


def test_older_ai_report_can_write_without_snapshot(video_env, llm):
    path = video_env / "older.md"
    path.write_text(REPORT)
    llm.replies = [script_json(300)]
    script = store.create_script("report_digest", {"title": "旧报告", "from_report": str(path)}, production="flash_daily")
    assert workflow.run(script["id"], llm_config=object()) == "DRAFT"
    assert REPORT in llm.calls[0][1]["content"]
    assert store.get_version(script["id"], 1)["meta"]["input_kind"] == "report"


def test_flash_keeps_selected_explanation_and_original_example(video_env, llm, monkeypatch):
    path = video_env / "flash.md"
    report = REPORT + f"\n\n### 2.2 无关项目\n不应进入的研究解释。{OTHER}"
    path.write_text(report)
    ref = report_material.save(str(path), report, [MATERIAL, f"### Other\n不应进入的原文。{OTHER}"])
    monkeypatch.setattr(report_meta, "get", lambda _: {"material": ref})
    llm.replies = [script_json(300)]
    script = store.create_script("flash", {"title": "主项目", "from_report": str(path), "from_item": "one"},
                                 production="flash_daily")
    assert workflow.run(script["id"], llm_config=object()) == "DRAFT"
    request = llm.calls[0][1]["content"]
    assert "反馈如何使下一步输入改变" in request and "返回补丁" in request
    assert "不应进入" not in request and OTHER not in request


def test_selected_event_does_not_replace_report_with_its_brief(video_env, llm, monkeypatch):
    path = video_env / "event.md"
    report = REPORT + f"\n\n### 2.2 其他事件\n不应进入的解释。{OTHER}"
    path.write_text(report)
    ref = report_material.save(str(path), report, [MATERIAL])
    monkeypatch.setattr(report_meta, "get", lambda _: {"material": ref})
    event = {"title": "主项目", "subject": "主项目", "action": "release", "edition": "1", "event_date": "",
             "summary": "已选事件", "primary": ["S1"], "background": [], "primary_sources": [PRIMARY],
             "changes": [], "event_key": "one", "pool_id": "test"}
    pool = {"packets": [{"id": "S1", "text": MATERIAL, "links": [PRIMARY]}]}
    monkeypatch.setattr(story_pool, "load_or_create", lambda *a: pool)
    def take(script_id, *args):
        with store.connect(write=True) as conn:
            conn.execute("INSERT INTO video_story_claims VALUES(?,?,?,?,?,?,'ACTIVE',?,?)",
                         ("claim", "test", "one", script_id, "test", json.dumps(event), store._now(), store._now()))
        return event, []
    monkeypatch.setattr(story_pool, "take", take)
    monkeypatch.setattr(story_pool, "selection", lambda *a: {})
    monkeypatch.setattr(workflow.content_guard, "check", lambda *a: None)
    llm.replies = [script_json(300)]
    script = store.create_script("report_digest", {"title": "整期报告", "from_report": str(path),
                                 "matrix_dedup": {"job": dossier.PRODUCTIONS["ai_media_opensource_digest"],
                                                  "policy": story_pool.POLICY}},
                                 production="ai_media_opensource_digest")
    assert workflow.run(script["id"], llm_config=object()) == "DRAFT"
    request = llm.calls[0][1]["content"]
    assert "反馈如何使下一步输入改变" in request and "返回补丁" in request
    assert "不应进入" not in request
    assert store.get_version(script["id"], 1)["meta"]["research_dossier"]["selection"] == "selected_sections"


def test_source_references_preserve_nested_process_and_ignore_other_candidate():
    report = ("# 本期导读\n\n### 2.1 主项目\n解释 [1]\n\n#### 操作过程\n步骤一、步骤二与输出。\n\n"
              "### 2.2 其他项目\n无关例子 [2]\n\n### 来源清单\n1. 原始资料 " + PRIMARY + "\n2. 其他 " + OTHER)
    selected, mode = dossier.select_report(report, {"primary": ["S1"]},
                                           {"packets": [{"id": "S1", "links": [PRIMARY]}]})
    assert mode == "selected_sections"
    assert "步骤一、步骤二与输出" in selected and PRIMARY in selected
    assert "无关例子" not in selected and OTHER not in selected


def test_unlinked_report_remains_context_for_explicit_story():
    report = "## 为什么需要反馈\n机制解释\n\n## 实例\n一个完整过程"
    assert dossier.select_report(report, {"primary_sources": [PRIMARY]}, {}) == (report, "report_context")


def test_named_event_keeps_its_explanation_when_urls_only_appear_in_bibliography():
    report = ("### 2.1 Flint\n本地模型怎样处理工具返回。\n\n### 2.2 另一个项目\n无关正文\n\n"
              "### 来源清单\n1. " + PRIMARY + "\n2. " + OTHER)
    selected, mode = dossier.select_report(report, {"subject": "Flint", "primary_sources": [PRIMARY]}, {})
    assert mode == "selected_sections" and "处理工具返回" in selected
    assert "无关正文" not in selected


def test_ai_item_keeps_nested_example_in_report_of(video_env):
    path = video_env / "nested.md"
    path.write_text(REPORT + "\n\n#### 操作过程\n嵌套的输入与输出\n\n### 2.2 其他项目\n不应进入")
    script = {"report_path": str(path), "production": "flash_daily",
              "topic": {"title": "主项目", "from_item": "one"}}
    selected = workflow.report_of(script)
    assert "嵌套的输入与输出" in selected and "不应进入" not in selected
    script["production"] = "science_daily"
    assert "嵌套的输入与输出" not in workflow.report_of(script)


@pytest.mark.parametrize("fence", ["```", "~~~~"])
def test_code_headings_cannot_cut_off_ai_item_process(fence):
    from core.video.excerpt import item_excerpt

    report = ("### 2.1 主项目\n解释 [1]\n\n#### 运行步骤\n" + fence + "bash\n"
              "# H200 用另一个启动脚本\n### 2.99 这是代码示例，不是新条目\nserve_h200.sh\n"
              + fence + "\n继续解释工具返回和最终输出。\n\n### 2.2 其他项目\n其他项目正文 [2]\n\n"
              "### 来源清单\n1. " + PRIMARY + "\n2. " + OTHER)
    selected = item_excerpt(report, "主项目", include_nested=True)
    assert "serve_h200.sh" in selected and "最终输出" in selected
    assert "其他项目正文" not in selected
    selected, mode = dossier.select_report(report, {"primary_sources": [PRIMARY]}, {})
    assert mode == "selected_sections"
    assert "serve_h200.sh" in selected and "最终输出" in selected
    assert "其他项目正文" not in selected


def test_author_input_preserves_document_tails_and_links_from_both_inputs(llm):
    report = "底稿解释" * 10000 + f"\n底稿末尾的任务结果 {PRIMARY}"
    material = "原文过程" * 10000 + f"\n原文末尾的输出 {OTHER}"
    llm.replies = [script_json(300, citations={(0, 0): [PRIMARY, OTHER]})]
    result = writer.write(report=report, original_material=material, citation_check=None,
                          topic={"title": "同一问题"}, column_style="深入讲清机制", target_minutes=3, llm_config=object())
    request = llm.calls[0][1]["content"]
    assert report in request and material in request
    assert result.sections[0]["paragraphs"][0]["citations"] == [PRIMARY, OTHER]
