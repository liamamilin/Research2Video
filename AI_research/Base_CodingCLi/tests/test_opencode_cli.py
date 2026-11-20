"""OpenCode CLI webfetch helper, gather worker, and page-reader fallback order."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from core import opencode_cli, opencode_gather, source_pages
from core.search import SearchResult


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


def test_webfetch_markdown_invokes_cli(monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        assert "--auto" in command and "--model" in command
        assert "opencode-go/beam-free" in command
        assert any("https://example.com/x" in part for part in command)
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


def test_gather_wanted_default_all_and_flags():
    cfg_all = {"enabled": True, "apply_to": "all"}
    assert opencode_gather.wanted({"search": False}, cfg_all)
    assert opencode_gather.wanted({"search": True}, cfg_all)
    assert not opencode_gather.wanted({"search": True, "opencode_gather": False}, cfg_all)
    cfg_fetch = {"enabled": True, "apply_to": "fetch_only"}
    assert opencode_gather.wanted({"search": False}, cfg_fetch)
    assert not opencode_gather.wanted({"search": True}, cfg_fetch)
    assert opencode_gather.wanted({"search": True, "opencode_gather": True}, cfg_fetch)


def test_gather_appends_block(monkeypatch, tmp_path):
    monkeypatch.setattr(opencode_cli, "gather_settings", lambda config_dir=None: {
        "enabled": True, "apply_to": "all", "binary": "opencode",
        "gather_timeout_seconds": 60,
    })
    monkeypatch.setattr(opencode_cli, "available", lambda cfg=None: True)
    monkeypatch.setattr(opencode_cli, "run", lambda prompt, **k: (
        "## 整理结论\n够用\n\n## 关键数字与对比\n- 无\n\n"
        "## 材料条目\n### 1. 示例\n- **要点**：细节\n\n## 成稿可用角度\n- 角度A\n\n## 未覆盖\n- 无",
        {"model": "opencode-go/beam-free"},
    ))
    engine = SimpleNamespace(config_dir="config", workspace_dir=str(tmp_path), _cancel=None)
    block, receipt = opencode_gather.gather(engine, {"search": True, "name": "t", "_file": "x/y"}, "源材料")
    assert block.startswith("## OpenCode 搜集整理") and "材料条目" in block
    assert "关键数字与对比" in block and "成稿可用角度" in block
    assert receipt["model"] == "opencode-go/beam-free" and receipt.get("path")
