"""Knowledge Jobs write from the model's own knowledge only when they say so (core/knowledge_job.py)."""
from pathlib import Path

import yaml

from core import knowledge_job

ROOT = Path(__file__).resolve().parents[1]
JOB = {"name": "x", "prompt": "p", "search": False, "knowledge": True, "output": "output/x/{date}_x.md"}


def test_declaration_must_be_explicit_and_consistent():
    assert knowledge_job.problem(JOB) is None and knowledge_job.enabled(JOB)
    assert knowledge_job.problem({"search": False}) is None and not knowledge_job.enabled({"search": False})
    assert "true" in knowledge_job.problem({**JOB, "knowledge": "yes"})
    assert "search: false" in knowledge_job.problem({**JOB, "search": True})
    assert "search: false" in knowledge_job.problem({k: v for k, v in JOB.items() if k != "search"})
    assert knowledge_job.problem({**JOB, "sources": ["source:x"]}) is None  # sources are topic ideas only
    assert "sources" in knowledge_job.problem({**JOB, "sources": "source:x"})
    assert "{date}" in knowledge_job.problem({**JOB, "output": "output/x/fixed.md"})


def test_past_titles_are_read_from_this_jobs_reports_newest_first(tmp_path):
    folder = tmp_path / "output/x"; folder.mkdir(parents=True)
    (folder / "2026-11-01_x.md").write_text("*说明*\n\n# 第一个问题\n正文", encoding="utf-8")
    (folder / "2026-11-02_x.md").write_text("# 第二个问题\n", encoding="utf-8")
    (folder / "2026-11-02_x.md.report.json").write_text("{}", encoding="utf-8")
    (folder / "2026-11-03_other.md").write_text("# 别的 Job\n", encoding="utf-8")
    titles = knowledge_job.past_titles(JOB, str(tmp_path))
    assert titles == ["第二个问题", "第一个问题"]
    block = knowledge_job.block(titles)
    # a second report the same day is saved as <name>_1.md; its question counts too (11-10: the cat question repeated)
    (folder / "2026-11-02_x_1.md").write_text("# 同一天第二个问题\n", encoding="utf-8")
    (folder / "2026-11-02_x_10.md").write_text("# 同一天第十一个问题\n", encoding="utf-8")
    (folder / "2026-11-02_xy.md").write_text("# 另一个 Job\n", encoding="utf-8")
    assert knowledge_job.past_titles(JOB, str(tmp_path)) == [
        "同一天第十一个问题", "同一天第二个问题", "第二个问题", "第一个问题"]
    assert "- 第二个问题" in block and "不要再选" in block
    assert "还没有往期底稿" in knowledge_job.block([])


def test_shipped_illustrated_jobs_are_valid_knowledge_jobs():
    for path in (ROOT / "jobs/economy/economy_illustrated.yaml", ROOT / "jobs/science/science_illustrated.yaml"):
        job = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert knowledge_job.enabled(job) and knowledge_job.problem(job) is None
        assert "list_files" not in job["prompt"]  # a fetch-only run has no tools


def test_topic_ideas_list_titles_gists_and_links_and_say_so_when_empty():
    from core.search import SearchResult
    items = [SearchResult(title=" Why is the sky blue? ", url="https://a.test/1", excerpts=["Light   scatters\n off air."]),
             SearchResult(title="What is inflation?", url="https://b.test/2")]
    block = knowledge_job.topic_block(items)
    assert "- Why is the sky blue? —— Light scatters off air.（https://a.test/1）" in block
    assert "- What is inflation?（https://b.test/2）" in block and "只用来选题" in block
    assert "按题目清单选题" in knowledge_job.topic_block([])


def test_shipped_jobs_use_registered_topic_sources():
    from core.sources import resolve
    for path in (ROOT / "jobs/economy/economy_illustrated.yaml", ROOT / "jobs/science/science_illustrated.yaml"):
        job = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert len(resolve(job["sources"], str(ROOT / "config"))) == 2
        assert "选题参考" in job["prompt"]
