"""Original material survives the report step and reaches automatic generation."""
from pathlib import Path

from core import report_material, report_meta
from core.video import publish, store
from tests.test_video_scripts import GOOD, REPORT, llm, script_json, video_env  # noqa: F401
from web.services import video_render, video_workflow


def test_material_snapshots_survive_reruns_and_detect_a_changed_report(tmp_path):
    path = tmp_path / "report.md"
    first = report_material.save(str(path), "first report", ["first source"])
    second = report_material.save(str(path), "second report", ["second source"])
    assert first["path"] != second["path"]
    assert report_material.read(first, str(path), "first report") == "first source"
    assert report_material.read(first, str(path), "second report") == ""
    assert report_material.read(second, str(path), "second report") == "second source"
    Path(first["path"]).write_text('{"material":"changed"}')
    assert report_material.read(first, str(path)) == ""


def test_invalid_snapshot_path_is_not_opened(tmp_path):
    secret = tmp_path / "outside/secret.json"
    secret.parent.mkdir()
    secret.write_text('{"material":"private"}')
    assert report_material.read({"path": str(secret), "sha256": report_material.digest("private")},
                                str(tmp_path / "report.md")) == ""


def test_original_material_reaches_author_and_its_links_reach_publish_package(video_env, llm, monkeypatch):
    path = video_env / "report.md"
    path.write_text(REPORT)
    source = "### [Original](https://original.example/news) — published 2026-10-24\n\n具体产品资料，而不是怎么写视频的备忘。"
    ref = report_material.save(str(path), REPORT, [source])
    monkeypatch.setattr(report_meta, "get", lambda _: {"material": ref})
    llm.replies = [script_json(300, citations={(0, 0): ["https://original.example/news"]})]
    script = store.create_script("report_digest", {"title": "素材试写", "from_report": str(path)})
    assert video_workflow.run(script["id"], llm_config=object()) == "DRAFT"
    request = llm.calls[0][1]["content"]
    assert source in request and REPORT not in request
    version = store.get_version(script["id"], 1)
    assert version["meta"]["material"] == ref
    assert version["meta"]["input_kind"] == "original_material"
    assert version["sections"][0]["paragraphs"][0]["citations"] == ["https://original.example/news"]
    refs = publish.reference_lines(version, REPORT)
    original_ref = next(r for r in refs if r["url"] == "https://original.example/news")
    assert original_ref["cited"] and original_ref["title"] == "Original" and original_ref["date"] == "2026-10-24"
    assert any(card["url"] == "https://original.example/news"
               for card in video_render.source_card_request(store.get_script(script["id"]), version))


def test_discovery_uses_only_the_selected_items_original_material(video_env, llm, monkeypatch):
    path = video_env / "report.md"
    body = f"## First\n关于第一个条目的说明 [source]({GOOD})\n\n## Second\n其他选题"
    path.write_text(body)
    material = f"### [First]({GOOD})\n原始细节\n\n---\n\n### [Other](https://other.example/news)\n无关条目"
    ref = report_material.save(str(path), body, [material])
    monkeypatch.setattr(report_meta, "get", lambda _: {"material": ref})
    llm.replies = [script_json(300)]
    script = store.create_script("flash", {"title": "First", "from_report": str(path), "from_item": "first"})
    assert video_workflow.run(script["id"], llm_config=object()) == "DRAFT"
    request = llm.calls[0][1]["content"]
    assert "原始细节" in request and "无关条目" not in request


def test_missing_material_keeps_older_reports_usable(video_env, llm, monkeypatch):
    path = video_env / "report.md"
    path.write_text(REPORT)
    monkeypatch.setattr(report_meta, "get", lambda _: {"material": {"path": str(path) + ".missing.json"}})
    llm.replies = [script_json(300)]
    script = store.create_script("report_digest", {"title": "素材试写", "from_report": str(path)})
    assert video_workflow.run(script["id"], llm_config=object()) == "DRAFT"
    assert REPORT in llm.calls[0][1]["content"]
    assert store.get_version(script["id"], 1)["meta"]["input_kind"] == "report"


def test_rewrite_reads_the_selected_source_after_topic_renaming(video_env):
    path = video_env / "renamed-topic.md"
    path.write_text("### 1.1 原选题\n- **一句话**：原来保存的素材摘要\n\n### 1.2 其他\n无关内容")
    script = {"report_path": str(path), "topic": {"title": "链路样片 · 新名称", "from_item": "old-id",
              "fields": {"一句话": "原来保存的素材摘要"}}}
    assert "原来保存的素材摘要" in video_workflow.report_of(script)
    assert "无关内容" not in video_workflow.report_of(script)
    script["topic"]["from_item_title"] = "原选题"
    script["topic"]["fields"] = {}
    assert "原来保存的素材摘要" in video_workflow.report_of(script)
