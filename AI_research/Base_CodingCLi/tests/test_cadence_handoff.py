"""A queued issue reaches the native saved report, raw materials and programme author unchanged."""

import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import yaml

from core import ai_dossier_sources, lock, report_material, report_meta, sources, state
from core.engine import ResearchEngine
from core.schedule_store import ScheduleStore
from core.search import SearchResult
from core.video import episodes, writer


def test_native_issue_handoff_keeps_original_range_and_details_after_configuration_edits(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    config = tmp_path / "config"
    (config / "matrices").mkdir(parents=True)
    (config / "source_supply").mkdir()
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    (config / "system.yaml").write_text("{}")
    (config / "matrices/demo.yaml").write_text("name: Demo\njobs: [demo]\n")
    (config / "source_supply/demo.yaml").write_text(
        "jobs:\n  demo:\n    mode: context\ndetail_pages: 0\n"
    )
    (jobs / "demo.yaml").write_text(
        "name: Demo\nprompt: Original explanation\nsearch: false\nsources: [rss:https://example.test/feed]\noutput: output/{date}_demo.md\n"
    )
    policy = {
        "schema_version": 1,
        "matrices": ["demo"],
        "window": {"mode": "previous_slot"},
    }
    (config / "production_policy.yaml").write_text(yaml.safe_dump(policy))
    storage = ScheduleStore(str(tmp_path / "state"))
    due = datetime.fromisoformat("2026-11-02T02:40:00+08:00").timestamp()
    plan = storage.save("demo", "40 2 * * 1", timezone="Asia/Shanghai", now=due - 60)
    storage.enqueue_due(due)
    frozen = json.loads(storage.runs(plan["id"])[0]["context"])
    (jobs / "demo.yaml").write_text(
        "name: Changed\nprompt: Later incompatible direction\n"
    )
    policy["window"] = {"mode": "rolling", "days": 14}
    (config / "production_policy.yaml").write_text(yaml.safe_dump(policy))
    for name, value in [
        ("_STATE_DIR", str(tmp_path / "state")),
        ("_HISTORY_DIR", str(tmp_path / "state/history")),
    ]:
        monkeypatch.setattr(state, name, value)
    monkeypatch.setattr(lock, "_LOCK_DIR", str(tmp_path / "state/locks"))
    monkeypatch.setattr(report_meta, "META_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(
        report_meta, "META_FILE", str(tmp_path / "state/report_meta.jsonl")
    )
    monkeypatch.setattr(report_meta, "_cache", {"mtime": 0, "map": {}})
    monkeypatch.setattr(ai_dossier_sources, "enrich", lambda *a, **kw: ("", {}))
    monkeypatch.setattr(
        sources,
        "fetch_source",
        lambda *a, **kw: [
            SearchResult(
                "Specific case",
                "https://example.test/a",
                "2026-10-30",
                ["输入代码→返回补丁→执行测试，具体过程保留。"],
                "rss",
            )
        ],
    )

    class Research:
        def __init__(self, **kw):
            self.materials = []
            self.retrieved_urls = set()

        def run_fetch_only(self, prompt, **kw):
            assert (
                "Original explanation" in prompt and "Later incompatible" not in prompt
            )
            assert "近 7 天" in prompt and "2026-10-26T02:40:00" in prompt
            return "# 本期研究底稿\n反馈如何改变下一步输入：返回补丁后，测试结果反馈给下一轮。https://example.test/a"

        def get_usage(self):
            return {"total_tokens": 1, "searches": 0}

        def close(self):
            pass

    monkeypatch.setattr("core.engine.ResearchAgent", Research)
    engine = ResearchEngine(str(config), str(jobs), workspace_dir=str(tmp_path))
    engine.run_context = frozen
    engine._now = lambda: datetime(
        2026, 11, 3, 8
    )  # Queue delay crosses a calendar date.
    saved = engine.run_job("demo")
    assert saved
    meta = report_meta.get(saved)
    assert meta["production_issue"]["issue_id"] == frozen["issue_id"]
    report = Path(saved).read_text()
    material = report_material.read(meta["material"], saved, report)
    programme = episodes.topic(
        {"id": "report", "report_path": saved},
        str(tmp_path),
        job="demo",
        production="demo",
    )
    programme = episodes.publication_topic(
        programme, {"column": "demo", "column_name": "研究专题"}
    )
    assert programme["episode"]["publication"]["issue_date"] == "2026-11-02"

    class Author:
        usage = {}
        model_switches = []

        def __init__(self, *a, **kw):
            pass

        def chat(self, messages, **kw):
            request = messages[-1]["content"]
            assert (
                "反馈如何改变下一步输入" in request
                and "输入代码→返回补丁→执行测试" in request
            )
            assert "2026-10-26T02:40:00" in request
            return SimpleNamespace(
                content=json.dumps(
                    {
                        "title": "研究专题",
                        "sections": [
                            {
                                "title": "具体机制",
                                "episode_role": "item",
                                "paragraphs": [
                                    {
                                        "text": "返回补丁后执行测试，再把结果放进下一轮输入。"
                                    }
                                ],
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                model="fake",
            )

    monkeypatch.setattr("core.llm.LLMClient", Author)
    result = writer.write(
        report=report,
        original_material=material,
        citation_check=None,
        topic=programme,
        column_style="",
        target_minutes=1,
        llm_config=object(),
    )
    assert len(result.sections) == 1 and result.title == "研究专题｜2026年11月02日"
