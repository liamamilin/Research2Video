"""Topics as records: any job that declares ``topics:`` feeds them, and the user can add some (spec/video-topics.md)."""

from __future__ import annotations

import os
import time

import pytest

from core.video import store, topic_store
from tests.test_video_flash import DISCOVERY
from tests.test_video_scripts import CONTENT_REPORT, _csrf, _login, video_env  # noqa: F401
from web.services import video_autopilot as pilot

DEEP_REPORT = """# 深度选题

### 1. 长上下文到底贵在哪
- **核心角度**：按 token 计费之外的缓存和重试成本
- **目标受众**：在做 agent 的开发者
- **评分**：8/10

### 2. 本地模型什么时候划算
- **核心角度**：一张卡的回本时间
- **评分**：6/10
"""

DEEP_JOB = """name: 深度选题发现
prompt: 找三个值得做 10 分钟深度解读的题目
output: "output/deep_topics/{date}_deep.md"
topics:
  alias: deep_topics
  format: items
  prefix: D
"""


def write(env, rel: str, text: str) -> str:
    path = env / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return str(path)


def reports(env) -> None:
    write(env, "output/flash/2026-10-19_ai_flash_discovery.md", DISCOVERY)
    write(env, "output/practical_ai_intelligence/2026-10-01/07_product_content_opportunities.md", CONTENT_REPORT)


def test_the_shipped_jobs_declare_the_two_sources_the_recipes_use(video_env):
    every = topic_store.sources(str(video_env / "jobs"))
    assert set(every) == {"flash", "opportunities"}
    assert every["flash"].job == "monitoring/ai_flash_discovery" and every["flash"].refresh is True
    assert every["opportunities"].refresh is False  # a round step: the pipeline never starts it on its own
    assert not any(s.error for s in every.values())


def test_reports_are_imported_once_with_the_ids_they_always_had(video_env):
    reports(video_env)
    pilot.sync_topics()
    date, flash = topic_store.load("flash")
    assert date == "2026-10-19" and [t.id for t in flash] == ["2026-10-19/F1", "2026-10-19/F2"]
    assert flash[0].report_path == "output/flash/2026-10-19_ai_flash_discovery.md"
    _, rounds = topic_store.load("opportunities")
    assert [t.id for t in rounds][:2] == ["2026-10-01/C1", "2026-10-01/C2"]
    assert topic_store.get("2026-10-01/C2").title == "长上下文计费陷阱"

    with store.connect() as conn:
        imported = conn.execute("SELECT imported_at FROM topic_imports").fetchall()
    pilot.sync_topics()  # nothing changed: nothing is read again
    with store.connect() as conn:
        assert conn.execute("SELECT imported_at FROM topic_imports").fetchall() == imported


def test_a_rewritten_report_replaces_its_topics(video_env):
    reports(video_env)
    pilot.sync_topics()
    path = video_env / "output/flash/2026-10-19_ai_flash_discovery.md"
    path.write_text(DISCOVERY.replace("### 1.2", "### 1.2 （删掉）\n\n### 9.9 不再是条目\n#### x") + "\n", encoding="utf-8")
    os.utime(path, (time.time() + 5, time.time() + 5))
    pilot.sync_topics()
    assert len(topic_store.load("flash")[1]) >= 2


def test_any_job_can_be_a_source_and_a_recipe_can_read_it(video_env):
    write(video_env, "jobs/research/deep_topics.yaml", DEEP_JOB)
    write(video_env, "output/deep_topics/2026-10-20_deep.md", DEEP_REPORT)
    recipe = video_env / "config" / "video_recipes" / "deep_dive.yaml"
    recipe.write_text(recipe.read_text(encoding="utf-8").replace("sources: [opportunities]", "sources: [deep_topics]"),
                      encoding="utf-8")
    pilot.sync_topics()
    assert [t.id for t in topic_store.load("deep_topics")[1]] == ["2026-10-20/D1", "2026-10-20/D2"]
    row = {r["cell"]: r for r in pilot.coverage()}["deep_dive"]
    assert (row["sources"], row["open"], row["unknown_sources"]) == (["deep_topics"], 2, [])
    picked = pilot.pick_topics({"picks": [{"cell": "deep_dive", "count": 1}]})
    assert picked[0]["topic"]["id"] == "2026-10-20/D1" and picked[0]["topic"]["fields"]["核心角度"].startswith("按 token")


@pytest.mark.parametrize("block, message", [
    ("topics:\n  format: items\n", "prefix"),
    ("topics:\n  format: items\n  prefix: F\n", "prefix"),               # F and C are the built-in formats' codes
    ("topics:\n  format: rss\n", "format"),
    ("topics:\n  alias: flash\n  format: flash\n", "重名"),               # the shipped flash job has that alias
    ("topics:\n  format: flash\n  schedule: daily\n", "schedule"),
])
def test_a_bad_declaration_is_listed_with_why_and_not_imported(video_env, block, message):
    write(video_env, "jobs/research/bad.yaml", 'name: 坏\nprompt: x\noutput: "output/bad/{date}.md"\n' + block)
    write(video_env, "output/bad/2026-10-20.md", DEEP_REPORT)
    bad = topic_store.resolve(str(video_env / "jobs"), "research/bad")
    assert bad is not None and message in bad.error
    assert topic_store.report_files(bad, str(video_env)) == []


def test_an_edited_job_is_read_again_and_an_unchanged_one_is_not_reparsed(video_env, monkeypatch):
    import yaml

    jobs = str(video_env / "jobs")
    path = write(video_env, "jobs/research/deep_topics.yaml", DEEP_JOB)
    assert topic_store.resolve(jobs, "deep_topics").prefix == "D"
    parsed = []
    real = yaml.safe_load
    monkeypatch.setattr(yaml, "safe_load", lambda *a, **k: parsed.append(1) or real(*a, **k))
    topic_store.sources(jobs)
    assert parsed == []  # nothing changed on disk: nothing parsed (the topics page asks once per recipe)
    write(video_env, "jobs/research/deep_topics.yaml", DEEP_JOB.replace("prefix: D", "prefix: DT"))
    assert topic_store.resolve(jobs, "deep_topics").prefix == "DT" and len(parsed) == 1
    import os
    os.remove(path)
    assert topic_store.resolve(jobs, "deep_topics") is None


def test_a_job_without_a_dated_output_cannot_be_a_source(video_env):
    write(video_env, "jobs/research/nodate.yaml", 'name: x\nprompt: y\noutput: "output/x.md"\ntopics:\n  format: items\n  prefix: X\n')
    assert "{date}" in topic_store.resolve(str(video_env / "jobs"), "research/nodate").error


def test_only_sources_that_allow_it_are_started_by_a_batch(video_env, monkeypatch):
    started: list[str] = []
    monkeypatch.setattr(pilot, "_start_discovery", lambda job: started.append(job))
    monkeypatch.setattr(pilot, "_discovery_running", lambda job: False)
    spec = {"picks": [{"cell": "flash", "count": 1}, {"cell": "deep_dive", "count": 1}], "refresh": True}
    run = pilot.start_run(spec)
    assert pilot.fresh_topics(run["spec"], run) is False
    assert started == ["monitoring/ai_flash_discovery"]  # the round step behind deep_dive is never started


def test_api_sources_manual_topics_and_a_typed_title_lands_in_the_pool(client, video_env, monkeypatch):
    reports(video_env)
    old = video_env / "output/practical_ai_intelligence/2026-06-01/07_product_content_opportunities.md"
    old.parent.mkdir(parents=True)
    old.write_text("# 旧格式，没有 C1 标题\n", encoding="utf-8")
    _login(client)
    sources = {s["name"]: s for s in client.get("/api/video/topics/sources").json()}
    assert sources["flash"]["latest"] == "2026-10-19" and sources["opportunities"]["report_error"] == ""  # old one is old
    assert sources["manual"]["count"] == 0

    added = client.post("/api/video/topics", headers=_csrf(client),
                        json={"title": "为什么我不再用向量库", "angle": "小项目用全文检索就够", "fields": {"发布日期": "2026-10-20"}})
    assert added.status_code == 201 and added.json()["id"] == "manual/1" and added.json()["source"] == "manual"
    listed = client.get("/api/video/topics?source=manual").json()["topics"]
    assert [t["title"] for t in listed] == ["为什么我不再用向量库"] and listed[0]["fields"]["发布日期"] == "2026-10-20"
    assert client.post("/api/video/topics", headers=_csrf(client), json={"title": "短"}).status_code == 422

    assert client.get("/api/video/topics?source=rss").status_code == 422
    from web.services import video_workflow

    monkeypatch.setattr(video_workflow, "spawn", lambda script_id, started_by="": None)
    made = client.post("/api/video/scripts", headers=_csrf(client),
                       json={"column": "deep_dive", "topic": {"title": "手填的一个题目", "angle": "x"}})
    assert made.status_code == 202, made.text
    assert store.get_script(made.json()["id"])["topic"]["id"] == "manual/2"
    assert client.get("/api/video/topics?source=manual").json()["topics"][0]["scripted"] is True

    _login(client, "viewer", "viewer-pass-123")
    assert client.post("/api/video/topics", headers=_csrf(client), json={"title": "看的人不能加"}).status_code == 403


def test_api_a_recipe_naming_a_source_no_job_declares_gets_a_warning(client, video_env):
    _login(client)
    flash = client.get("/api/video/recipes/flash").json()
    assert flash["warnings"] == []
    checked = client.post("/api/video/recipes/flash/validate", headers=_csrf(client),
                          json={"content": flash["content"].replace("sources: [flash]", "sources: [rss]")}).json()
    assert checked["ok"] is True and "rss" in checked["warnings"][0]


def test_a_refreshable_source_can_be_run_from_the_topics_page_and_others_cannot(client, video_env, monkeypatch):
    monkeypatch.setattr(pilot, "_start_discovery", lambda job: None)
    monkeypatch.setattr(pilot, "_discovery_running", lambda job: False)
    _login(client)
    assert client.get("/api/video/topics/discovery?source=flash").json()["job"] == "monitoring/ai_flash_discovery"
    assert client.post("/api/video/topics/discovery?source=opportunities", headers=_csrf(client)).status_code == 422


def test_a_job_made_from_the_topic_discovery_template_is_a_source_and_reads_the_days_last_run(client, video_env):
    import shutil

    shipped = os.path.join(os.path.dirname(__file__), "..", "jobs", "_templates", "_topic_discovery.yaml")
    (video_env / "jobs" / "_templates").mkdir(exist_ok=True)
    shutil.copy(shipped, video_env / "jobs" / "_templates" / "_topic_discovery.yaml")
    _login(client)
    made = client.post("/api/jobs", headers=_csrf(client), json={"name": "深度选题", "template": "_topic_discovery",
                                                                  "category": "monitoring", "keywords": ["agent"]})
    assert made.status_code in (200, 201), made.text
    source = next(s for s in topic_store.sources(str(video_env / "jobs")).values() if s.label == "深度选题")
    assert not source.error and source.prefix == "D" and source.refresh is False  # never started by a batch
    write(video_env, "output/topic_discovery/2026-10-20_深度选题.md", DEEP_REPORT)
    later = write(video_env, "output/topic_discovery/2026-10-20_深度选题_1.md", DEEP_REPORT.replace("长上下文", "更长的上下文"))
    os.utime(later, (time.time() + 5, time.time() + 5))
    pilot.sync_topics()
    date, found = topic_store.load(source.name)
    assert date == "2026-10-20" and found[0].title == "更长的上下文到底贵在哪" and found[0].id == "2026-10-20/D1"
