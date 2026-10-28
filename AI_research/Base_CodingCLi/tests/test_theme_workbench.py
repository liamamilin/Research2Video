"""Discovery diversity, durable handover, matrix isolation and opt-in production."""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
import yaml

from core import theme_context, theme_discovery
from core.theme_store import ThemeStore
from core.video import store as videos
from tests.test_video_scripts import ROOT, _csrf, _login, video_env  # noqa: F401


def candidate(category, title="应该怎样选择适合自己的 AI 套餐？"):
    return {"category": category, "title": title, "reason": "公开讨论反复提出这个问题",
            "signals": [{"id": "s1", "url": "https://example.org/one", "metrics": {"view": 123}}]}


def test_daily_slots_are_per_category_and_nearby_history_is_deduplicated(tmp_path):
    store = ThemeStore(str(tmp_path))
    made = store.publish("a", "2026-10-27", [candidate("one"), candidate("one", "还有哪些值得关注的问题？"),
        candidate("two", "同一份资料怎样通过提问获得清楚的回答？"), candidate("three")])
    assert len(made) == 2 and {r["category"] for r in made} == {"one", "two"}
    assert not store.publish("a", "2026-10-27", [candidate("one", "同一分类的另一个题目可以重复推荐吗？")])
    assert not store.publish("a", "2026-10-28", [candidate("one")])
    assert len(store.publish("b", "2026-10-27", [candidate("one")])) == 1
    assert len(store.list("a")) == 2 and len(store.list("b")) == 1


def test_concurrent_discovery_cannot_fill_a_category_twice(tmp_path):
    store = ThemeStore(str(tmp_path))
    with ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(lambda n: store.publish("a", "2026-10-27", [candidate("one", f"第{n}个不同的具体主题是什么？")]), range(2)))
    assert sum(map(len, result)) == 1


def test_model_can_only_select_actual_signals_from_its_category():
    categories = [{"key": "one", "audience": "新用户"}, {"key": "two", "audience": "普通用户"}]
    signals = [{"id": "s1", "category": "one", "url": "https://example.org/one", "metrics": {"view": 123}},
               {"id": "s2", "category": "two", "url": "https://example.org/two", "metrics": {}}]
    response = {"themes": [
        {"category": "one", "title": "应该怎样选择适合自己的套餐？", "reason": "实际讨论线索", "source_ids": ["s1"], "metrics": {"view": 99999}},
        {"category": "one", "title": "同一个分类的第二个主题", "reason": "不能占两个位置", "source_ids": ["s1"]},
        {"category": "two", "title": "跨分类引用其他线索", "reason": "无本类资料", "source_ids": ["s1", "fake"]},
    ]}
    selected = theme_discovery.select(json.dumps(response), categories, signals)
    assert len(selected) == 1 and selected[0]["signals"][0]["metrics"] == {"view": 123}


@pytest.fixture
def theme_env(video_env, monkeypatch):
    root = video_env
    (root / "config/matrices").mkdir(exist_ok=True)
    (root / "config/content_columns").mkdir(exist_ok=True)
    (root / "config/production").mkdir(exist_ok=True)
    (root / "jobs/demo").mkdir(exist_ok=True)
    (root / "config/column_types.yaml").write_text((ROOT / "config/column_types.yaml").read_text())
    (root / "config/sources.yaml").write_text("official:\n  name: Official\n  spec: page:https://example.org/guide\n")
    for key in ("demo", "other"):
        (root / f"config/matrices/{key}.yaml").write_text(yaml.safe_dump({"name": key, "domain": "ChatGPT", "jobs": ["demo/source"],
            "content": {"role": "domain", "audience": "中文普通用户"}}, allow_unicode=True))
    # Membership is unique; the other matrix is only used to test isolation.
    (root / "config/matrices/other.yaml").write_text("name: Other\ndomain: Other\njobs: []\n")
    (root / "jobs/demo/source.yaml").write_text("name: 素材研究\nprompt: 保持原有素材流程\noutput: output/demo/{date}.md\nsources: [source:official]\n")
    (root / "config/production/demo_value.yaml").write_text("job: demo/source\nrecipe: theme_research\nauto: 0\n")
    (root / "config/content_columns/demo_value.yaml").write_text(yaml.safe_dump({"name": "T06 套餐价值", "matrix": "demo",
        "type": "comparison", "promise": "我是否有必要付费？", "production_ids": ["demo_value"], "audience": "用了一个月免费版的人"}, allow_unicode=True))
    from web.services import managed_scheduler
    monkeypatch.setattr(managed_scheduler, "ensure_online", lambda: {"online": True})
    return root


def test_manual_theme_api_is_scoped_and_viewer_is_read_only(client, theme_env):
    _login(client, "viewer", "viewer-pass-123")
    url = "/api/matrices/demo/themes"
    assert client.get(url).status_code == 200
    assert client.post(url, json={"category": "demo_value", "title": "免费版用了一个月，应该升级 Plus 吗？"}, headers=_csrf(client)).status_code == 403
    _login(client)
    response = client.post(url, json={"category": "demo_value", "title": "免费版用了一个月，应该升级 Plus 吗？"}, headers=_csrf(client))
    assert response.status_code == 201
    theme = response.json()
    assert theme["audience"] == "用了一个月免费版的人"
    assert client.get("/api/matrices/other/themes").json()["themes"] == []
    assert client.post(f"/api/matrices/other/themes/{theme['id']}/start", json={}, headers=_csrf(client)).status_code == 404
    assert client.post(url, json={"category": "demo_value", "title": "    "}, headers=_csrf(client)).status_code == 422


def test_topic_research_reuses_production_context_and_is_idempotent(client, theme_env, monkeypatch):
    from web.services import video_workflow
    started = []
    monkeypatch.setattr(video_workflow, "spawn", lambda sid, by: started.append(sid))
    _login(client)
    url = "/api/matrices/demo/themes"
    theme = client.post(url, json={"category": "demo_value", "title": "免费版用了一个月，应该升级 Plus 吗？",
        "angle": "围绕日常任务帮助选择", "notes": "用户自己的使用经历"}, headers=_csrf(client)).json()
    path = f"{url}/{theme['id']}/start"
    first = client.post(path, json={"auto_video": True}, headers=_csrf(client))
    assert first.status_code == 202
    second = client.post(path, json={"auto_video": True}, headers=_csrf(client)).json()
    assert first.json()["script_id"] == second["script_id"] and len(started) == 1
    script = videos.get_script(started[0])
    assert script["content_matrix_id"] == "demo" and script["content_column_id"] == "demo_value"
    assert script["production"] == "demo_value"
    spec = video_workflow.job_spec(video_workflow.video_columns.get(str(theme_env / "config"), script["column_key"]), script["topic"], script["id"])
    assert "用户自己的使用经历" in spec["prompt"] and "不再生成多个候选选题" in spec["prompt"]
    assert spec["sources"] == ["source:official"] and "source_history" not in spec
    assert client.patch(f"{url}/{theme['id']}", json={"title": "不能在启动后改题", "angle": "", "audience": "", "notes": ""}, headers=_csrf(client)).status_code == 409


def test_theme_without_binding_uses_generic_recipe_and_matrix_identity(client, theme_env, monkeypatch):
    from web.services import video_workflow
    monkeypatch.setattr(video_workflow, "spawn", lambda *args: None)
    _login(client)
    url = "/api/matrices/other/themes"
    data = client.get(url).json()
    assert data["categories"][0]["key"] == "general"
    theme = client.post(url, json={"category": "general", "title": "一个独立主题应该怎样展开？"}, headers=_csrf(client)).json()
    started = client.post(f"{url}/{theme['id']}/start", json={}, headers=_csrf(client)).json()
    script = videos.get_script(started["script_id"])
    assert script["content_matrix_id"] == "other" and script["topic"]["theme_research"]


def test_daily_schedule_can_be_paused_without_changing_feed_schedule(client, theme_env):
    from core.schedule_store import ScheduleStore
    _login(client)
    url = "/api/matrices/demo/themes/schedule"
    result = client.put(url, json={"enabled": True, "time": "09:15", "platforms": ["bilibili", "zhihu"]}, headers=_csrf(client))
    assert result.status_code == 200, result.text
    plan = result.json()["schedule"]
    assert plan["job_name"] == "topic_discovery/demo" and plan["timezone"] == "Asia/Shanghai"
    assert (theme_env / "jobs/topic_discovery/demo.yaml").exists()
    stopped = client.put(url, json={"enabled": False, "time": "09:15", "platforms": ["bilibili"]}, headers=_csrf(client))
    assert not stopped.json()["schedule"]["enabled"]
    assert len(ScheduleStore(str(theme_env / "state")).list()) == 1


def test_discover_api_registers_a_job_once_and_respects_service_maintenance(client, theme_env, monkeypatch):
    from types import SimpleNamespace
    from web.runner import executor
    from web.runner.registry import TaskRegistry
    from web.services import theme_workbench
    starts = []
    def start_job(name, by, **kwargs):
        starts.append((name, by))
        return SimpleNamespace(task_id="discovery-run")
    monkeypatch.setattr(TaskRegistry(), "is_running", lambda job: bool(starts))
    monkeypatch.setattr(executor, "start_job", start_job)
    _login(client)
    url = "/api/matrices/demo/themes/discover"
    first = client.post(url, json={}, headers=_csrf(client))
    assert first.status_code == 202 and first.json()["run_id"] == "discovery-run"
    assert client.post(url, json={}, headers=_csrf(client)).status_code == 202
    assert len(starts) == 1 and starts[0][0] == "topic_discovery/demo"
    import os
    (theme_env / "state/assistant_maintenance.json").write_text(json.dumps({"pid": os.getpid(), "status": "applying", "action": "restart"}))
    assert client.post(url, json={}, headers=_csrf(client)).status_code == 409
    row = theme_workbench.create("demo", {"category": "demo_value", "title": "维护期间不开始新的主题研究"})
    assert client.post(f"/api/matrices/demo/themes/{row['id']}/start", json={}, headers=_csrf(client)).status_code == 409


def test_auto_video_tick_only_renders_opted_in_themes_once(theme_env, monkeypatch):
    from web.services import theme_workbench, video_executor
    themes = ThemeStore(str(theme_env / "state"))
    row = themes.create("demo", "demo_value", "免费版用了一个月，应该升级吗？")
    script = videos.create_script("theme_research", {"title": row["title"]})
    videos.update_script(script["id"], status="DRAFT", current_version=1)
    themes.attach(row["id"], script["id"], True)
    events = []
    monkeypatch.setattr(video_executor, "approve", lambda *args: events.append("approve"))
    monkeypatch.setattr(video_executor, "render", lambda *args: events.append("render"))
    monkeypatch.setattr(videos, "list_renders", lambda sid: [] if not events else [{"id": "r", "status": "RUNNING"}])
    theme_workbench.tick(); theme_workbench.tick()
    assert events == ["approve", "render"]


@pytest.mark.parametrize("script_status,render_status,opt_in,expected", [
    ("DRAFT", None, False, "started"),
    ("FAILED", None, True, "attention"),
    ("APPROVED", "FAILED", True, "attention"),
    ("APPROVED", "COMPLETED", True, "completed"),
])
def test_production_settles_terminal_work_and_never_renders_a_script_only_theme(
        theme_env, monkeypatch, script_status, render_status, opt_in, expected):
    from web.services import theme_workbench, video_executor
    themes = ThemeStore(str(theme_env / "state"))
    row = themes.create("demo", "demo_value", "是否需要为日常学习升级付费套餐？")
    script = videos.create_script("theme_research", {"title": row["title"]})
    videos.update_script(script["id"], status=script_status, current_version=1)
    themes.attach(row["id"], script["id"], opt_in)
    monkeypatch.setattr(videos, "list_renders", lambda sid: [{"id": "r", "status": render_status}] if render_status else [])
    def forbidden(*args):
        raise AssertionError("unexpected automatic production")
    monkeypatch.setattr(video_executor, "approve", forbidden)
    monkeypatch.setattr(video_executor, "render", forbidden)
    theme_workbench.tick(); theme_workbench.tick()
    assert themes.get(row["id"])["status"] == expected
    assert themes.active() == []


def test_discovery_runs_through_normal_engine_and_keeps_raw_signals(theme_env, monkeypatch):
    from core.engine import ResearchEngine
    from core.search import SearchResult
    from web.services import theme_workbench
    name = theme_workbench.ensure_job("demo")
    class Search:
        calls = 0
        def __init__(self, *args, **kwargs):
            pass
        def search(self, queries, **kwargs):
            self.calls += 1
            domain = queries[0].split()[0].removeprefix("site:")
            return [SearchResult("一个真实用户提出的套餐问题", f"https://{domain}/question/one", excerpts=["想知道是否值得付费"])]
    class Model:
        usage = {"requests": 1, "total_tokens": 100}
        def __init__(self, *args, **kwargs):
            pass
        def chat(self, messages, **kwargs):
            payload = json.loads(messages[1]["content"])
            assert payload["signals"][0]["metrics"] == {}
            return type("Reply", (), {"content": json.dumps({"themes": [{"category": "demo_value", "title": "免费版用了一个月，应该升级 Plus 吗？",
                "reason": "实际用户的问题", "source_ids": ["s1"]}]})})()
        def close(self):
            pass
    monkeypatch.setattr(theme_discovery, "SearchClient", Search)
    monkeypatch.setattr(theme_discovery, "LLMClient", Model)
    monkeypatch.setattr(theme_discovery.LLMConfig, "from_system", lambda *args: None)
    monkeypatch.setattr(theme_discovery, "public_metrics", lambda url: {})
    engine = ResearchEngine(config_dir=str(theme_env / "config"), jobs_dir=str(theme_env / "jobs"), workspace_dir=str(theme_env))
    output = engine.run_job(name)
    assert output and (theme_env / output).is_file()
    themes = ThemeStore(str(theme_env / "state"))
    assert len(themes.list("demo")) == 1
    run = themes.runs("demo")[0]
    assert run["status"] == "success" and run["result"]["usage"]["searches"] == 1
    assert (theme_env / output).with_suffix(".signals.json").is_file()
    engine.run_job(name)
    assert len(themes.list("demo")) == 1


def test_page_tool_keeps_fuller_material_for_topic_authors(monkeypatch):
    from core.research import ResearchAgent, ResearchConfig
    from core.llm import LLMConfig
    from core.search import SearchConfig, SearchResult
    agent = ResearchAgent(LLMConfig(api_key="x", base_url="http://x.test", model="m"), SearchConfig(), ResearchConfig())
    monkeypatch.setattr(agent.search, "read_page", lambda url: SearchResult("详细教程", url, excerpts=["具体输入、操作和可见结果。" * 20]))
    text = agent._execute_tool("read_webpage", {"url": "https://example.org/guide"})
    assert "具体输入" in text and agent.materials and "https://example.org/guide" in agent.retrieved_urls
    assert agent._execute_tool("read_webpage", {"url": "file:///etc/passwd"}).startswith("Error:")
    agent.close()
