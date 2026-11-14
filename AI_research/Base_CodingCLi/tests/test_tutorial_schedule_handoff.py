"""Exercise the configured tutorial through the morning schedule and the native queues."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import json
import shutil
import time

import pytest

from core import activity, events, lock, report_meta, schedule_runner, source_history, sources, state
from core.engine import ResearchEngine
from core.llm import ChatResponse
from core.research import _FETCH_SYSTEM_PROMPT
from core.research_window import select
from core.schedule_store import ScheduleStore
from core.search import SearchResult
from core.video import autopilot_store, manuscript_workflow, store
from tests.test_platforms import BV, CREDENTIAL, PAYLOAD
from tests.test_video_scripts import FakeLLM, ROOT, autovideo, llm, script_json, video_env  # noqa: F401
from web.services import bilibili, platform_manager, video_autopilot, video_render, video_workflow
from web.settings import get_settings


@pytest.mark.parametrize("has_topic", [True, False])
def test_morning_tutorial_handoff(video_env, llm, autovideo, monkeypatch, has_topic):
    root = video_env
    monkeypatch.chdir(root)
    for rel in (
        "jobs/ai_media/ai_tool_tutorials.yaml",
        "config/matrices/ai_media.yaml",
        "config/sources.yaml",
        "config/production_policy.yaml",
        "config/production/ai_tool_tutorials.yaml",
        "config/content_columns/ai_tutorial.yaml",
        "config/column_types.yaml",
        "config/video_recipes/chatgpt_acquisition.yaml",
        "config/manuscript.yaml",
    ):
        dest = root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / rel, dest)
    (root / "config/system.yaml").write_text("ai:\n  model: test-model\n")
    for module, key, value in (
        (state, "_STATE_DIR", root / "state"),
        (state, "_HISTORY_DIR", root / "state/history"),
        (lock, "_LOCK_DIR", root / "state/locks"),
        (report_meta, "META_DIR", root / "state"),
    ):
        monkeypatch.setattr(module, key, str(value))
    monkeypatch.setattr(activity, "use_state_dir", lambda _: None)
    monkeypatch.setattr(activity, "mark_activity", lambda: None)
    monkeypatch.setattr(events, "_DB_PATH_OVERRIDE", str(root / "state/events.db"))

    runtime = schedule_runner.Runtime.from_settings(get_settings())
    schedule = ScheduleStore(runtime.state_dir, config_dir=runtime.config_dir, jobs_dir=runtime.jobs_dir)
    job = "ai_media/ai_tool_tutorials"
    morning = datetime.fromisoformat("2026-11-14T10:30:00+08:00")
    due = morning.timestamp()
    plan = schedule.save(job, "30 10 * * *", timezone="Asia/Shanghai", missed_policy="latest", now=due-60)
    monkeypatch.setattr(ResearchEngine, "_now", lambda _: morning.replace(tzinfo=None))
    monkeypatch.setattr(schedule_runner, "poke_autopilot", lambda *_: None)
    title = "WorkBuddy 在结果区定位生成的文件"
    url = "https://www.codebuddy.cn/docs/workbuddy/Results#产物"
    overview = "https://www.codebuddy.cn/docs/workbuddy/Results"
    source = SearchResult("结果区的文件", url, None, ["结果区汇集任务完成后生成的文件。"], "page")
    window = {"days": 1, "start": "2026-11-13T10:30:00+08:00", "end": morning.isoformat()}
    _, baseline = source_history.prepare(root / "state/source_history", job, select([source], window))
    source_history.commit(baseline)
    monkeypatch.setattr(sources, "fetch_source", lambda spec, **_: [
        source
    ] if "workbuddy/Results" in spec else [])

    profile = manuscript_workflow.JOB_PROMPTS[job]
    stock_reply = FakeLLM.chat
    paragraph = manuscript_workflow.pid(0, 1)
    revision = "结果区是 WorkBuddy 集中展示任务产物的地方。"

    def reply(self, messages, **kwargs):
        system, request = [message["content"] for message in messages[:2]]
        if system == _FETCH_SYSTEM_PROMPT:
            assert "2026-11-14T10:30:00+08:00" in request
            assert "结果区汇集任务完成后生成的文件" in request
            assert "未变化 1 条" in request and "当前来源内容" in request
            assert "请说明暂无新题材" not in request
            result = (f"### {title}\n- **系列**：WorkBuddy\n- **一句话**：定位已生成的文件。\n"
                      f"- **操作与演示**：在结果区找到文件卡片。\n- **来源网址**：{url}\n  - [结果区概览]({overview})"
                      if has_topic else "本次暂无新题材")
        elif "整理成教程候选" in system:
            assert "同一个操作" in system and "相同结果" in system
            packet = json.loads(request)["materials"][0]["id"]
            result = json.dumps({"events": [{"title": title, "subject": title, "action": "analysis",
                                             "summary": "在结果区找到文件卡片", "primary": [packet]}]})
        elif system == profile.read:
            result = json.dumps({"takeaway": "定位结果", "keep_quote": "找到文件", "issue_quote": "结果区",
                                 "revision": "简短介绍结果区", "targets": [paragraph]})
        elif system == profile.revise:
            result = json.dumps({"paragraphs": [{"id": paragraph, "text": revision}]})
        else:
            return stock_reply(self, messages, **kwargs)
        return ChatResponse(content=result)

    monkeypatch.setattr(FakeLLM, "chat", reply)
    monkeypatch.setattr(FakeLLM, "close", lambda _: None, raising=False)
    monkeypatch.setattr(FakeLLM, "_active_model", lambda _: "test-model", raising=False)
    monkeypatch.setattr("core.research.LLMClient", FakeLLM)
    monkeypatch.setattr(manuscript_workflow, "LLMClient", FakeLLM)
    draft = json.loads(script_json(1200, citations={(0, 0): [url]},
                                  extra="大家好，我是洛洛。先在 WorkBuddy 的结果区找到任务生成的文件。"))
    llm.replies = [json.dumps({**draft, "title": title}, ensure_ascii=False)] * 4
    assert schedule_runner.dispatch_once(runtime, schedule, due-1, spawn=lambda *_: pytest.fail("提前运行")) == 0
    actual_start = due + (3 if has_topic else 90)
    with ThreadPoolExecutor(max_workers=1) as worker:
        def run_worker(paths, run):
            worker.submit(schedule_runner.work, paths, run["id"]).result()

        assert schedule_runner.dispatch_once(runtime, schedule, actual_start, spawn=run_worker) == 1
    [receipt] = schedule.runs(plan["id"])
    assert receipt["status"] == "success", receipt["error"]
    assert receipt["due_at"] == due
    assert schedule_runner.dispatch_once(runtime, schedule, actual_start + 1,
                                         spawn=lambda *_: pytest.fail("重复运行")) == 0
    settled = time.time() + 61
    assert schedule_runner.autopilot_due(runtime.state_dir, settled, runtime) is has_topic
    if not has_topic:
        assert video_autopilot.fire_auto(settled) == 0
        assert not autopilot_store.list_runs()
        return

    monkeypatch.setattr(video_workflow, "spawn", lambda script_id, started_by: video_workflow.run(script_id, started_by=started_by))
    monkeypatch.setattr(video_render, "start", video_render.process)
    assert video_autopilot.fire_auto(settled) == 1
    for _ in range(5):
        video_autopilot.tick()
    [run] = autopilot_store.list_runs()
    [item] = run["items"]
    assert item["state"] == "DONE", item["note"]
    script = store.get_script(item["script_id"])
    assert script["topic"]["fields"]["来源网址"].splitlines() == [url, overview]
    version = store.get_version(script["id"], script["approved_version"])
    assert version["meta"]["manuscript_workflow"]["profile"] == profile.version
    assert version["meta"]["manuscript_workflow"]["result"] == "revised"
    assert version["sections"][0]["paragraphs"][1]["text"] == revision
    assert autovideo.posts[0]["production_recipe"] == "mixed_tutorial"
    assert video_render.safe_file(store.get_render(item["render_id"]), "video.mp4").is_file()

    monkeypatch.setattr(bilibili.BilibiliClient, "verify", lambda _: {"uid": "123", "name": "测试账号"})
    monkeypatch.setattr(bilibili, "biliup_binary", lambda: "/tmp/fake-biliup")
    monkeypatch.setattr(bilibili, "prepare_cover", lambda video, *_: video)
    sent = []
    monkeypatch.setattr(bilibili, "upload", lambda _creds, _video, payload, _folder: sent.append(payload) or BV)
    platform_manager.connect(CREDENTIAL)
    db = platform_manager.store()
    db.save_settings({"auto_upload": True, "tid": 231, "publish_window": {"enabled": True, "start": 8, "end": 23, "spacing": 30}})
    db.save_settings({"auto_since": 0})
    reserved_at = morning + timedelta(hours=2)
    prior = db.enqueue("123", {"id": "prior-render", "script_id": "prior-script", "version": 1},
                       {**PAYLOAD, "dtime": reserved_at.timestamp()})
    db.update_upload(prior["id"], status="SUBMITTED", bvid="BV1zz411x7zz")
    with monkeypatch.context() as clock:
        clock.setattr(platform_manager.time, "time", lambda: (reserved_at - timedelta(minutes=20)).timestamp())
        platform_manager.tick()
        [waiting] = db.script_uploads("123", script["id"])
        assert waiting["status"] == "QUEUED" and waiting["payload"]["held"] and not sent
        clock.setattr(platform_manager.time, "time", lambda: (reserved_at + timedelta(minutes=30)).timestamp())
        # These isolated receipts have no corresponding video at the remote platform.
        clock.setattr(platform_manager, "reconcile", lambda *_: None)
        platform_manager.tick()
        platform_manager.tick()
    [upload] = db.script_uploads("123", script["id"])
    assert upload["status"] == "SUBMITTED" and upload["bvid"] == BV
    assert len(sent) == 1
