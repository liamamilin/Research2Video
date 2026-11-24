"""OpenCode CLI webfetch helper, gather worker, and page-reader fallback order."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from core import opencode_cli, opencode_gather, source_pages
from core.search import SearchResult
from core.video import audience_strategy


def test_resolve_binary_falls_back_to_home_install_when_path_is_starved(monkeypatch, tmp_path):
    installed = tmp_path / ".opencode" / "bin" / "opencode"
    installed.parent.mkdir(parents=True)
    installed.write_text("#!/bin/sh\n", encoding="utf-8")
    installed.chmod(0o755)
    monkeypatch.setattr(opencode_cli.shutil, "which", lambda binary: None)
    monkeypatch.setattr(opencode_cli.Path, "home", lambda: tmp_path)
    assert opencode_cli.resolve_binary({"binary": "opencode"}) == str(installed.resolve())


def test_resolve_binary_expands_tilde_config(monkeypatch, tmp_path):
    installed = tmp_path / "bin" / "opencode"
    installed.parent.mkdir(parents=True)
    installed.write_text("#!/bin/sh\n", encoding="utf-8")
    installed.chmod(0o755)
    monkeypatch.setattr(opencode_cli.shutil, "which", lambda binary: None)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert opencode_cli.resolve_binary({"binary": "~/bin/opencode"}) == str(installed.resolve())

def test_parse_run_jsonl_prefers_webfetch_tool_output():
    stdout = "\n".join([
        '{"type":"tool_use","part":{"tool":"webfetch","state":{"status":"completed","output":"正文段落甲"}}}',
        '{"type":"text","part":{"text":"模型改写后的摘要"}}',
    ])
    assert opencode_cli._parse_run_jsonl(stdout) == "正文段落甲"


def test_parse_run_jsonl_rejects_short_failure_messages():
    with pytest.raises(RuntimeError, match="403"):
        opencode_cli._parse_run_jsonl(
            '{"type":"text","part":{"text":"webfetch 请求失败，返回 HTTP 403，未能获取到正文内容。"}}')


def test_subprocess_env_keeps_browse_and_disables_other_user_mcps(tmp_path):
    import json

    env = opencode_cli.subprocess_env(base_env={"PATH": "/usr/bin"})
    assert env["NO_COLOR"] == "1"
    assert "OPENCODE_CONFIG_CONTENT" not in env  # nothing discovered, nothing to disable

    # Auto-discovers v1 (`mcp.<name>`) and v2 (`mcp.servers.<name>`) user MCPs.
    cfg_dir = tmp_path / ".config" / "opencode"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "opencode.json").write_text(json.dumps({
        "mcp": {
            "godot-ai": {"type": "local", "command": ["godot-ai"], "enabled": True},
            "blender": {"type": "local", "command": ["blender"], "enabled": True},
            "servers": {
                "browse": {"type": "local", "command": ["browse", "mcp"]},
            },
        },
    }), encoding="utf-8")
    env_home = opencode_cli.subprocess_env(base_env={"HOME": str(tmp_path)})
    servers = json.loads(env_home["OPENCODE_CONFIG_CONTENT"])["mcp"]["servers"]
    assert "browse" not in servers  # browser stays available
    for name in ("godot-ai", "blender"):
        assert servers[name] == {"type": "local", "command": ["true"], "disabled": True}

    # allowed_mcps: [] turns every user MCP off (model probes, thin page reader).
    none_allowed = opencode_cli.subprocess_env(
        cfg={"allowed_mcps": []}, base_env={"HOME": str(tmp_path)},
    )
    servers = json.loads(none_allowed["OPENCODE_CONFIG_CONTENT"])["mcp"]["servers"]
    assert servers["browse"] == {"type": "local", "command": ["true"], "disabled": True}

    # Explicit disabled_mcps overrides discovery; [] opts out cleanly.
    opt_out = opencode_cli.subprocess_env(
        cfg={"disabled_mcps": []}, base_env={"HOME": str(tmp_path)},
    )
    assert "OPENCODE_CONFIG_CONTENT" not in opt_out

    merged = opencode_cli.subprocess_env(
        cfg={"disabled_mcps": ["browse"]},
        base_env={"OPENCODE_CONFIG_CONTENT": '{"mcp":{"servers":{"other":{"type":"local","command":["x"],"disabled":false}}}}'},
    )
    merged_cfg = json.loads(merged["OPENCODE_CONFIG_CONTENT"])
    assert merged_cfg["mcp"]["servers"]["browse"] == {
        "type": "local", "command": ["true"], "disabled": True,
    }
    assert merged_cfg["mcp"]["servers"]["other"]["disabled"] is False


def test_run_appends_tool_policy(monkeypatch):
    seen = {}

    def fake_sub(command, **kwargs):
        seen["prompt"] = command[-1]
        return 0, '{"type":"text","part":{"text":"done"}}\n', ""

    monkeypatch.setattr(opencode_cli.shutil, "which", lambda binary: "/bin/opencode")
    monkeypatch.setattr(opencode_cli, "_run_subprocess", fake_sub)
    text, _ = opencode_cli.run("调研任务", config={"binary": "opencode"}, model="m")
    assert text == "done"
    assert seen["prompt"].startswith("调研任务")
    assert "一律用 websearch" in seen["prompt"] and "截图" in seen["prompt"]


def test_webfetch_markdown_invokes_cli(monkeypatch, tmp_path):
    import json

    cfg_dir = tmp_path / ".config" / "opencode"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "opencode.json").write_text(
        '{"mcp":{"servers":{"browse":{"type":"local","command":["browse","mcp"]}}}}', encoding="utf-8")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        assert "--auto" in command and "--model" in command
        assert "opencode-go/beam-free" in command
        assert any("https://example.com/x" in part for part in command)
        cfg = json.loads(kwargs["env"]["OPENCODE_CONFIG_CONTENT"])
        assert cfg["mcp"]["servers"]["browse"]["disabled"] is True  # thin reader skips MCPs
        return SimpleNamespace(returncode=0, stdout=(
            '{"type":"tool_use","part":{"tool":"webfetch","state":'
            '{"status":"completed","output":"Hello from page"}}}\n'
        ), stderr="")

    monkeypatch.setattr(opencode_cli.shutil, "which", lambda binary: "/bin/opencode")
    monkeypatch.setattr(opencode_cli.subprocess, "run", fake_run)
    monkeypatch.setattr("core.opencode_models.select_model",
                        lambda config, binary, **k: ("opencode-go/beam-free", {"cached": False}))
    assert opencode_cli.webfetch_markdown("https://example.com/x", config={
        "enabled": True, "binary": "opencode", "timeout_seconds": 30, "workdir": "",
    }) == "Hello from page"
    assert calls and calls[0][0] == "/bin/opencode"


def test_fetch_page_uses_opencode_before_paid(monkeypatch):
    order = []

    def get_page(url, timeout):
        order.append("http")
        raise RuntimeError("HTTP 403")

    monkeypatch.setattr(source_pages, "_read_via_browser",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no chrome")))
    monkeypatch.setattr(source_pages, "_read_via_opencode",
                        lambda *a, **k: order.append("opencode") or [
                            SearchResult("From opencode", "https://example.com/a",
                                         excerpts=["opencode body"])])
    monkeypatch.setattr(source_pages, "_read_via_paid",
                        lambda *a, **k: order.append("paid") or [
                            SearchResult("From paid", "https://example.com/a", excerpts=["paid"])])
    monkeypatch.setattr("core.opencode_cli.available", lambda cfg=None: True)
    monkeypatch.setattr("core.opencode_cli.settings", lambda config_dir=None: {"enabled": True})

    got = source_pages.fetch_page(
        "https://example.com/a", 4, 10, get_page=get_page,
        search_client=SimpleNamespace(), browser=True, allow_paid=True, allow_opencode=True)
    assert got[0].title == "From opencode" and order == ["http", "opencode"]


def test_gather_wanted_enables_non_knowledge_jobs_under_apply_all():
    cfg_all = {"enabled": True, "apply_to": "all"}
    assert opencode_gather.wanted({"search": False}, cfg_all)
    assert opencode_gather.wanted({"search": True}, cfg_all)
    assert not opencode_gather.wanted({"search": False, "knowledge": True}, cfg_all)
    assert opencode_gather.wanted({"search": False, "knowledge": True, "opencode_gather": True}, cfg_all)
    assert not opencode_gather.wanted({"search": False, "opencode_gather": False}, cfg_all)
    cfg_fetch = {"enabled": True, "apply_to": "fetch_only"}
    assert opencode_gather.wanted({"search": False}, cfg_fetch)
    assert not opencode_gather.wanted({"search": True}, cfg_fetch)
    assert opencode_gather.wanted({"search": True, "opencode_gather": True}, cfg_fetch)
    cfg_flag = {"enabled": True, "apply_to": "flagged"}
    assert not opencode_gather.wanted({"search": True}, cfg_flag)
    assert opencode_gather.wanted({"search": False, "opencode_gather": True}, cfg_flag)


def test_gather_objective_includes_keywords():
    text = opencode_gather._objective({
        "name": "快讯", "domain": "AI", "keywords": ["模型", "开源"],
        "audience_channel": "flash", "opencode_task": "盯住本周发布",
        "prompt": "写一份快讯\n第二行",
    })
    assert "关键词：模型、开源" in text and "专项调研要求：" in text and "盯住本周发布" in text


def test_normalize_packets_makes_audience_packets():
    body = (
        "## 调研结论\n覆盖了发布\n\n"
        "## 材料条目\n"
        "### 示例发布\n"
        "- 链接：https://news.example/a\n"
        "- 要点：细节甲\n\n"
        "### [已规范](https://news.example/b)\n"
        "细节乙\n"
    )
    block, urls = opencode_gather.normalize_packets(body)
    assert block.startswith("### [")
    assert urls == ["https://news.example/a", "https://news.example/b"]
    packets = audience_strategy.packets(
        "### [声明源](https://declared.example/x)\n声明正文\n\n" + block)
    assert [p["url"] for p in packets] == [
        "https://declared.example/x", "https://news.example/a", "https://news.example/b"]
    assert packets[0]["text"].strip() == "### [声明源](https://declared.example/x)\n声明正文"
    assert packets[1]["title"] == "示例发布"
    assert "细节甲" in packets[1]["text"]
    assert "覆盖了发布" in packets[2]["text"]  # overview rides on last OpenCode packet


def test_gather_appends_normalized_block(monkeypatch, tmp_path):
    monkeypatch.setattr(opencode_cli, "gather_settings", lambda config_dir=None: {
        "enabled": True, "apply_to": "all", "binary": "opencode",
        "gather_timeout_seconds": 60,
    })
    monkeypatch.setattr(opencode_cli, "available", lambda cfg=None: True)
    captured = {}

    def fake_run(prompt, **k):
        captured["prompt"] = prompt
        return (
            "## 调研结论\n够用\n\n## 关键数字与对比\n- 无\n\n"
            "## 材料条目\n### 示例\n- https://news.example/item\n- **要点**：细节\n\n"
            "## 成稿可用角度\n- 角度A\n\n## 未覆盖\n- 无",
            {"model": "opencode-go/beam-free"},
        )

    monkeypatch.setattr(opencode_cli, "run", fake_run)
    engine = SimpleNamespace(config_dir="config", workspace_dir=str(tmp_path), _cancel=None)
    job = {"search": True, "name": "t", "_file": "x/y", "keywords": ["release"], "domain": "AI"}
    block, receipt = opencode_gather.gather(engine, job, "源材料应被忽略")
    assert block.startswith("### [示例](https://news.example/item)")
    assert receipt["urls"] == ["https://news.example/item"] and receipt["packet_count"] == 1
    assert "### [条目标题](https://" in captured["prompt"]
    assert "源材料应被忽略" not in captured["prompt"]
    assert "已取得的声明源材料" not in captured["prompt"]
    assert "并行" in captured["prompt"]
    assert audience_strategy.packets(block)[0]["url"] == "https://news.example/item"
    assert receipt["model"] == "opencode-go/beam-free" and receipt.get("path")
    assert receipt.get("keywords") == "release"


def test_engine_continues_with_opencode_when_declared_sources_fail(tmp_path, monkeypatch):
    from core import ai_dossier_sources, report_meta, state
    from core.engine import ResearchEngine
    from core.errors import SourcesUnavailableError

    monkeypatch.chdir(tmp_path)
    (tmp_path / "config").mkdir()
    (tmp_path / "config/system.yaml").write_text("gather:\n  opencode_cli:\n    enabled: true\n    apply_to: all\n")
    job_path = tmp_path / "jobs/demo.yaml"
    job_path.parent.mkdir(parents=True)
    job_path.write_text("name: Demo\nsearch: false\nsources: [page:https://blocked.example/news]\n"
                        "prompt: 写研究报告\noutput: output/{date}_demo.md\n")
    monkeypatch.setattr(state, "_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(state, "_HISTORY_DIR", str(tmp_path / "state/history"))
    monkeypatch.setattr(report_meta, "META_FILE", str(tmp_path / "state/report_meta.jsonl"))
    monkeypatch.setattr(ai_dossier_sources, "enrich", lambda *a, **k: ("", {}))
    monkeypatch.setattr(opencode_gather, "gather", lambda eng, job, *a, **k: (
        "### [OpenCode发现](https://arxiv.org/abs/2611.00001)\n\n论文实验方法与结论细节。\n",
        {"urls": ["https://arxiv.org/abs/2611.00001"], "packet_count": 1},
    ))

    seen = {}

    class Agent:
        def __init__(self, **kw):
            self.materials, self.retrieved_urls = [], set()

        def run_fetch_only(self, prompt, **kw):
            seen["sources"] = kw.get("sources", "")
            seen["urls"] = kw.get("fetched_urls", [])
            return "# 研究报告\n论文实验方法 https://arxiv.org/abs/2611.00001"

        def get_usage(self):
            return {"searches": 0, "total_tokens": 1}

        def close(self):
            pass

    monkeypatch.setattr("core.engine.ResearchAgent", Agent)
    engine = ResearchEngine(str(tmp_path / "config"), str(tmp_path / "jobs"), workspace_dir=str(tmp_path))
    monkeypatch.setattr(engine, "_fetch_sources_strict",
                        lambda job: (_ for _ in ()).throw(SourcesUnavailableError({"page": "HTTP 403"})))

    out = engine.run_job("demo")
    assert out is not None
    assert "论文实验方法与结论细节" in seen["sources"]
    assert "https://arxiv.org/abs/2611.00001" in seen["urls"]
    meta = state.StateManager.get("demo")
    assert meta["last_status"] == "success"
    assert "403" in (meta.get("last_usage") or {}).get("declared_sources_skipped", "")


def test_fetch_call_rejects_refusal_and_gateway_errors(monkeypatch):
    from core.errors import LLMError
    from core.llm import LLMConfig
    from core.research import ResearchAgent
    from core.search import SearchConfig

    agent = ResearchAgent(LLMConfig(model="m", base_url="http://localhost"), SearchConfig())
    monkeypatch.setattr(agent.llm, "chat", lambda *a, **k: SimpleNamespace(
        content="抱歉，我无法完成这份报告。", finish_reason="stop"))
    with pytest.raises(LLMError, match="模型输出不是可用报告"):
        agent._fetch_call("写报告")


def test_audience_direct_skips_redundant_agent_draft_and_dossier(tmp_path, monkeypatch):
    from core import ai_dossier_sources, report_meta, state
    from core.engine import ResearchEngine

    monkeypatch.chdir(tmp_path)
    (tmp_path / "config").mkdir()
    (tmp_path / "config/system.yaml").write_text(
        "research:\n  audience_direct: true\n"
        "gather:\n  opencode_cli:\n    enabled: false\n"
        "question_material:\n  enabled: true\n  scope: focused\n"
    )
    job_path = tmp_path / "jobs/chains/ai_science_updates.yaml"
    job_path.parent.mkdir(parents=True)
    job_path.write_text(
        "name: 科学\nsearch: false\nsources: [rss:https://a.test/feed]\n"
        "prompt: 写报告\noutput: output/{date}_sci.md\n"
        "audience_channel: ai_science\ncontent_strategy: audience-v1\nquestion_material: question-v1\n"
    )
    monkeypatch.setattr(state, "_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(state, "_HISTORY_DIR", str(tmp_path / "state/history"))
    monkeypatch.setattr(report_meta, "META_FILE", str(tmp_path / "state/report_meta.jsonl"))
    monkeypatch.setattr(ai_dossier_sources, "enrich",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("dossier must be skipped")))
    monkeypatch.setattr("core.engine.ResearchAgent",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("R7 agent must be skipped")))

    def fake_prepare(job, report, mat, cfg, **k):
        assert report == ""
        return (
            "# 直连成稿\n### 1.1 新进展\nhttps://a.test/1",
            {"strategy": audience_strategy.MARKER, "channel": "ai_science",
             "candidate_count": 1, "usage": {"total_tokens": 12}},
        )

    monkeypatch.setattr(audience_strategy, "prepare_report", fake_prepare)
    engine = ResearchEngine(str(tmp_path / "config"), str(tmp_path / "jobs"), workspace_dir=str(tmp_path))
    monkeypatch.setattr(engine, "_fetch_sources_strict", lambda j: (
        "### [标题](https://a.test/1)\n\n正文细节。", ([SearchResult("标题", "https://a.test/1")], {"rss": 1})
    ))
    monkeypatch.setattr("core.video.question_material.enhance",
                        lambda j, r, m, d, s, **k: (r, "", {"status": "skipped_scope"}))
    out = engine.run_job("chains/ai_science_updates")
    assert out is not None
    assert "直连成稿" in (tmp_path / out).read_text()
    meta = state.StateManager.get("chains/ai_science_updates")
    assert (meta.get("last_usage") or {}).get("audience_direct") is True


def test_question_material_focused_scope_filters_multi_item_with_opencode():
    from core.video import question_material as qm

    focused = {"question_material": {"enabled": True, "scope": "focused"}}
    flash_job = {"_file": "monitoring/ai_flash_discovery", "content_strategy": "audience-v1",
                 "audience_channel": "flash", "question_material": "question-v1"}
    knowledge_job = {"_file": "science/science_illustrated", "content_strategy": "audience-v1",
                     "audience_channel": "science_illustrated", "question_material": "question-v1"}
    single_job = {"_file": "ai_media/weekly_deep_research", "content_strategy": "audience-v1",
                  "audience_channel": "ai_deep", "question_material": "question-v1"}

    assert not qm.in_scope(flash_job, focused, opencode_gathered=True)
    assert qm.in_scope(flash_job, focused, opencode_gathered=False)
    assert qm.in_scope({**flash_job, "question_material_force": True}, focused, opencode_gathered=True)
    assert qm.in_scope(knowledge_job, focused, opencode_gathered=True)
    assert qm.in_scope(single_job, focused, opencode_gathered=True)


def test_parse_usage_aggregates_opencode_jsonl_tokens():
    stdout = "\n".join([
        '{"type":"step_finish","part":{"tokens":{"input":100,"output":40,"reasoning":10,"cache":{"read":20}},"cost":0.001}}',
        '{"type":"step_finish","part":{"tokens":{"input":50,"output":25},"cost":0.0005}}',
        '{"type":"tool_use","part":{"tool":"websearch","state":{"status":"completed"}}}',
        '{"type":"tool_use","part":{"tool":"websearch","state":{"status":"completed"}}}',
        '{"type":"tool_use","part":{"tool":"browse_screenshot","state":{"status":"completed"}}}',
    ])
    usage = opencode_cli.parse_usage(stdout)
    assert usage["tool_calls"] == {"websearch": 2, "browse_screenshot": 1}
    assert usage["prompt_tokens"] == 150
    assert usage["completion_tokens"] == 65
    assert usage["reasoning_tokens"] == 10
    assert usage["cache_read_tokens"] == 20
    assert usage["total_tokens"] == 245
    assert usage["opencode_steps"] == 2
    assert usage["cost_usd"] == 0.0015


def test_opencode_pipeline_enabled_exemptions_and_soft_filter():
    from core import opencode_pipeline as pipe

    cfg = {"enabled": True, "pipeline_mode": "opencode_topic_v1"}
    assert pipe.enabled({"name": "flash"}, cfg)
    assert not pipe.enabled({"name": "legacy", "research_mode": "legacy"}, cfg)
    assert not pipe.enabled({"name": "synth", "prompt": "汇总 {upstream_reports}"}, cfg)
    assert not pipe.enabled({"name": "know", "knowledge": True}, cfg)
    assert pipe.enabled({"name": "know_opt", "knowledge": True, "research_mode": "opencode_topic_v1"}, cfg)

    candidates = [
        pipe.TopicCandidate(title=f"独立选题 {i}", seed_urls=[f"https://example.com/{i}"], research_questions=["q"])
        for i in range(6)
    ] + [
        pipe.TopicCandidate(title="小更新 A", seed_urls=["https://example.com/a"], cluster_key="prod"),
        pipe.TopicCandidate(title="小更新 B", seed_urls=["https://example.com/b"], cluster_key="prod"),
        pipe.TopicCandidate(title="无链接传闻", seed_urls=[]),
    ]
    selected, receipt = pipe.soft_filter(candidates, job={"name": "t"}, use_llm=False)
    assert len(selected) == 7
    assert len(receipt["merged"]) == 1
    assert len(receipt["dropped"]) == 1 and receipt["dropped"][0]["reason"] == "missing_seed_urls"


def test_opencode_pipeline_gather_topics_cancels_queued(tmp_path, monkeypatch):
    import threading
    from core import opencode_pipeline as pipe
    from core.errors import CancelledError

    cancel = threading.Event()
    started = []

    def fake_run(prompt, **kwargs):
        started.append(prompt[:20])
        cancel.set()
        raise CancelledError("cancelled")

    monkeypatch.setattr(opencode_cli, "run", fake_run)
    topics = [
        pipe.TopicCandidate(title=f"题 {i}", seed_urls=[f"https://example.com/{i}"])
        for i in range(5)
    ]
    with pytest.raises(CancelledError):
        pipe.gather_topics(
            {"_file": "x/y", "name": "t"}, topics,
            config={"max_concurrency": 1, "per_topic_timeout_seconds": 30},
            workspace_dir=str(tmp_path), today="2026-11-21", cancel_token=cancel,
        )
    assert len(started) == 1


def test_opencode_pipeline_end_to_end_usage_and_cache(tmp_path, monkeypatch):
    import json
    from core import opencode_pipeline as pipe

    calls = {"n": 0}
    disc_json = json.dumps({"candidates": [{
        "title": "新模型发布",
        "why_now": "今日更新",
        "audience": "开发者",
        "promise": "讲清变化",
        "seed_urls": ["https://example.com/news"],
        "research_questions": ["价格多少？"],
    }]}, ensure_ascii=False)
    dossier_md = (
        "## 核心结论与背景\n定价 2 美元（https://example.com/news）。\n\n"
        "## 关键证据与展开\n#### 价格\n输入 2 美元（https://example.com/pricing）。\n\n"
        "## 材料条目\n### [官方定价](https://example.com/pricing)\n- 官方页。\n\n"
        "## 口径与信息缺口\n- 一手已确认。\n"
    )

    def fake_run(_prompt, **_kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return disc_json, {
                "model": "mock",
                "usage": {
                    "prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150,
                    "tool_calls": {"websearch": 2},
                },
            }
        return dossier_md, {
            "model": "mock",
            "usage": {
                "prompt_tokens": 200, "completion_tokens": 80, "total_tokens": 280,
                "tool_calls": {"websearch": 3, "webfetch": 4},
            },
        }

    monkeypatch.setattr(opencode_cli, "gather_settings", lambda _d=None: {
        "enabled": True, "soft_filter_llm": False, "max_concurrency": 2,
        "discovery_timeout_seconds": 60, "per_topic_timeout_seconds": 60,
    })
    monkeypatch.setattr(opencode_cli, "available", lambda _c=None: True)
    monkeypatch.setattr(opencode_cli, "run", fake_run)

    engine = SimpleNamespace(
        config_dir=str(tmp_path), workspace_dir=str(tmp_path), sys={"ai": {}},
        _cancel=None, _progress=lambda _e: None, _reported_events=lambda: "",
        _now=lambda: SimpleNamespace(strftime=lambda _f: "2026-11-21"),
    )
    job = {"_file": "monitoring/ai_flash_discovery", "name": "AI Flash", "audience_channel": "flash",
           "research_mode": "opencode_topic_v1"}
    report_md, raw_mat, receipt = pipe.run(engine, job, "### [News](https://example.com/news)\nBody")
    assert receipt["topic_count"] == 1
    assert receipt["usage"]["opencode_calls"] == 2
    assert receipt["usage"]["total_tokens"] == 430
    assert receipt["usage"]["tool_calls"] == {"websearch": 5, "webfetch": 4}
    assert "### 1.1 新模型发布" in report_md and "### 来源清单" in report_md
    assert "https://example.com/pricing" in receipt["urls"]
    assert raw_mat.strip()


def test_run_subprocess_reaps_descendant_process_group_on_timeout_and_cancel(tmp_path):
    import os
    import sys
    import threading
    import time
    from core.errors import CancelledError

    if os.name == "nt":
        pytest.skip("POSIX process groups only")

    def _spawn_cmd(pid_file):
        code = (
            "import subprocess, sys, time\n"
            "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
            f"open({str(pid_file)!r}, 'w').write(str(child.pid))\n"
            "time.sleep(30)\n"
        )
        return [sys.executable, "-c", code]

    def _wait_dead(pid: int, timeout: float = 3.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return True
            time.sleep(0.05)
        return False

    # 1. Timeout reaps descendant process in the session group.
    pid_file_t = tmp_path / "child_timeout.pid"
    with pytest.raises(RuntimeError, match="超时"):
        opencode_cli._run_subprocess(
            _spawn_cmd(pid_file_t), limit=1, workdir=str(tmp_path),
            env=dict(os.environ), chosen="test-model", cancel_token=None,
        )
    assert pid_file_t.is_file()
    assert _wait_dead(int(pid_file_t.read_text().strip()))

    # 2. Cancellation reaps descendant process in the session group.
    pid_file_c = tmp_path / "child_cancel.pid"
    cancel = threading.Event()

    def _trigger_cancel():
        deadline = time.monotonic() + 3.0
        while not pid_file_c.is_file() and time.monotonic() < deadline:
            time.sleep(0.05)
        cancel.set()

    t = threading.Thread(target=_trigger_cancel, daemon=True)
    t.start()
    with pytest.raises(CancelledError):
        opencode_cli._run_subprocess(
            _spawn_cmd(pid_file_c), limit=10, workdir=str(tmp_path),
            env=dict(os.environ), chosen="test-model", cancel_token=cancel,
        )
    t.join(timeout=2)
    assert pid_file_c.is_file()
    assert _wait_dead(int(pid_file_c.read_text().strip()))
