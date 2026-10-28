"""The pilot reads existing research links and retains useful body sections."""
from threading import Event
from types import SimpleNamespace

import pytest

from core.ai_dossier_sources import detail_sections, enrich, selected_urls
from core.errors import CancelledError
from core.research import ResearchAgent
from core.search import SearchResult

URL = "https://example.com/project"


def test_urls_follow_research_order_and_only_use_supplied_links():
    known = " ".join(f"https://example.com/{i}" for i in range(6))
    report = "https://unknown.example/a " + " ".join(f"https://example.com/{i}" for i in [4, 2, 1, 0, 3])
    assert selected_urls(report, known) == [f"https://example.com/{i}" for i in [4, 2, 1]]


def test_existing_page_adapter_reads_task_process_without_search(monkeypatch):
    from core import sources
    monkeypatch.setattr(sources, "_get_page", lambda url, timeout: (
        "<article><h1>项目正文</h1><h2>操作示例</h2><p>"
        "输入一组重叠区间，项目先按起点排序，再合并重叠部分，输出合并后的区间列表。"
        "这段具体处理过程应当保留给研究底稿和作者。"
        "</p></article>"))
    client = SimpleNamespace(search=lambda *a, **kw: pytest.fail("page read must not search"))
    results = sources.fetch_source("page:" + URL, limit=24, timeout=15, search_client=client)
    assert "合并重叠部分" in "\n".join(item.to_markdown() for item in results)
    assert all(item.provider == "page" for item in results)


def test_page_adapter_fallback_reads_the_same_url_without_search(monkeypatch):
    from core import sources
    def unavailable(url, timeout):
        raise OSError("direct page unavailable")
    monkeypatch.setattr(sources, "_get_page", unavailable)
    read_urls = []
    def read_page(url):
        read_urls.append(url)
        return SearchResult("项目正文", url, excerpts=[
            "## 操作示例\n输入一段代码，项目读取函数接口并生成调用，最后输出处理结果。"
            "正文应通过已有页面读取回退进入同一次任务的材料，且不执行新的搜索。"])
    client = SimpleNamespace(read_page=read_page,
                             search=lambda *a, **kw: pytest.fail("page read must not search"))
    results = sources.fetch_source("page:" + URL, limit=24, timeout=15, search_client=client)
    assert read_urls == [URL]
    assert "函数接口" in "\n".join(item.to_markdown() for item in results)


def test_other_jobs_never_read_extra_pages():
    def fetch(*a, **kw):
        pytest.fail("another matrix must not acquire new reads")
    assert enrich({"_file": "science/daily"}, URL, URL, fetch=fetch) == ("", {})


def test_ai_reads_body_without_search_queries_and_keeps_failures():
    calls = []
    def fetch(spec, **kwargs):
        calls.append((spec, kwargs))
        if spec.endswith("bad"):
            raise RuntimeError("page unavailable")
        return [SearchResult("Usage", URL, excerpts=["Input file → apply patch → test output."])]
    bad = "https://example.com/bad"
    text, receipt = enrich({"_file": "ai_media/daily_opensource_radar"}, URL + " " + bad,
                           URL + " " + bad, fetch=fetch)
    assert "apply patch" in text
    assert {row["status"] for row in receipt["pages"]} == {"read", "unavailable"}
    assert receipt["extra_search_queries"] == 0 and len(calls) == 2
    assert all(call[0].startswith("page:") for call in calls)


def test_cancellation_is_not_reported_as_a_missing_page():
    cancel = Event()
    cancel.set()
    with pytest.raises(CancelledError):
        enrich({"_file": "ai_media/weekly_deep_research"}, URL, URL, cancel_token=cancel)


def test_process_sections_and_document_end_survive_large_page_selection():
    rows = [SearchResult("Introduction", URL, excerpts=["intro"])]
    rows += [SearchResult(f"Background {i}", URL, excerpts=["x" * 5500]) for i in range(8)]
    rows += [SearchResult("Usage examples", URL, excerpts=["complete input and output"]),
             SearchResult("Conclusion", URL, excerpts=["final result"])]
    kept = detail_sections(rows)
    assert rows[0] in kept and rows[-2] in kept and rows[-1] in kept
    assert all(row.excerpts[0] in [r.excerpts[0] for r in rows] for row in kept)
    assert sum(len(row.to_markdown()) for row in kept) <= 24000


def test_same_researcher_expands_without_resetting_search_usage():
    calls = []
    agent = ResearchAgent.__new__(ResearchAgent)
    agent._cancel, agent._deadline = Event(), 0
    agent.materials, agent.retrieved_urls = [], set()
    agent._progress = lambda *a: None
    agent.search = SimpleNamespace(calls=6)
    agent.llm = SimpleNamespace(chat=lambda messages, **kw: (
        calls.append((messages, kw)) or SimpleNamespace(content="完整研究底稿", finish_reason="stop")))
    assert agent.expand_dossier("研究任务", "初始底稿", "原文例子 " + URL) == "完整研究底稿"
    assert agent.search.calls == 6 and agent.materials == ["原文例子 " + URL]
    assert calls[0][1]["tools"] is None
    assert "初始底稿" in calls[0][0][1]["content"] and URL in agent.retrieved_urls


@pytest.mark.parametrize("reason,content", [
    ("length", "# 尚未写完的底稿"),
    ("content_filter", "# 被供应商中断的部分正文"),
    ("stop", "  "),
    ("stop", "抱歉，我无法完成这个请求。"),
    ("stop", "Internal server error"),
])
def test_incomplete_expansion_uses_existing_engine_fallback(reason, content):
    agent = ResearchAgent.__new__(ResearchAgent)
    agent._cancel, agent._deadline = Event(), 0
    agent.materials, agent.retrieved_urls = [], set()
    agent._progress = lambda *a: None
    agent.llm = SimpleNamespace(chat=lambda *a, **kw: SimpleNamespace(
        content=content, finish_reason=reason))
    with pytest.raises(RuntimeError, match="沿用原研究结果"):
        agent.expand_dossier("研究任务", "初始完整底稿", "新读正文 " + URL)
    assert agent.materials == ["新读正文 " + URL]
    assert URL in agent.retrieved_urls


def test_body_remains_available_when_expansion_deadline_has_elapsed():
    agent = ResearchAgent.__new__(ResearchAgent)
    agent.materials, agent.retrieved_urls = [], set()
    def elapsed():
        raise RuntimeError("research deadline elapsed")
    agent._check_cancel = elapsed
    with pytest.raises(RuntimeError, match="deadline elapsed"):
        agent.expand_dossier("研究任务", "初始完整底稿", "已读到的详细步骤 " + URL)
    assert agent.materials == ["已读到的详细步骤 " + URL]
    assert URL in agent.retrieved_urls


@pytest.mark.parametrize("expansion_fails", [False, True])
def test_native_engine_saves_body_snapshot_and_initial_report(tmp_path, monkeypatch, expansion_fails):
    import json
    from core import ai_dossier_sources, report_meta
    from core.engine import ResearchEngine
    monkeypatch.chdir(tmp_path)
    (tmp_path / "jobs/ai_media").mkdir(parents=True)
    (tmp_path / "jobs/ai_media/daily_opensource_radar.yaml").write_text(
        "name: 研究底稿\nenabled: true\nsearch: false\nsources: [rss:example]\n"
        "prompt: 研究任务\noutput: output/{date}_dossier.md\n")
    (tmp_path / "config").mkdir()
    (tmp_path / "config/system.yaml").write_text("{}")
    monkeypatch.setattr(report_meta, "META_FILE", str(tmp_path / "state/report_meta.jsonl"))
    monkeypatch.setattr(ai_dossier_sources, "enrich", lambda *a, **kw: (
        "正文中的具体过程 " + URL, {"pages": [{"url": URL, "status": "read"}], "extra_search_queries": 0}))

    class Agent:
        def __init__(self, **kwargs):
            self.materials, self.retrieved_urls = [], {URL}
        def run_fetch_only(self, *a, **kw):
            return "# 初始研究底稿\n原有例子 " + URL
        def expand_dossier(self, prompt, report, material):
            self.materials.append(material)
            if expansion_fails:
                raise RuntimeError("model unavailable")
            return "# 深化研究底稿\n原有例子和正文过程 " + URL
        def get_usage(self):
            return {"searches": 0, "total_tokens": 50}
        def close(self):
            pass

    monkeypatch.setattr("core.engine.ResearchAgent", Agent)
    engine = ResearchEngine(str(tmp_path / "config"), str(tmp_path / "jobs"), workspace_dir=str(tmp_path))
    monkeypatch.setattr(engine, "_fetch_sources_strict", lambda job: (
        "原有来源 " + URL, ([SearchResult("项目", URL)], {"rss:example": 1})))
    result = engine.run_job("ai_media/daily_opensource_radar")
    assert result
    report = (tmp_path / result).read_text()
    assert ("初始研究底稿" if expansion_fails else "深化研究底稿") in report
    metadata = json.loads((tmp_path / "state/report_meta.jsonl").read_text().splitlines()[-1])
    detail = metadata["research_dossier"]
    assert detail["expanded"] is not expansion_fails
    from pathlib import Path
    assert "初始研究底稿" in Path(detail["initial_report"]).read_text()
    snapshot = json.loads(Path(metadata["material"]["path"]).read_text())
    assert "原有来源" in snapshot["material"] and "正文中的具体过程" in snapshot["material"]
