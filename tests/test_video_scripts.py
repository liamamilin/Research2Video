"""Video scripts: topics, script writing, the topic → draft workflow, the API and renders.

Research jobs, the model and AutoVideo are replaced by fakes, so this runs
offline.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import time
import uuid
from concurrent.futures import Future
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from core.llm import ChatResponse
from core.video import columns as video_columns
from core.video import length, store
from core.video import topics as video_topics
from core.video import writer
from core.video.autovideo import AutoVideoClient, AutoVideoError

ROOT = Path(__file__).resolve().parents[1]
GOOD = "https://docs.crewai.com/en/concepts/cli"
GOOD_2 = "https://github.com/crewAIInc/crewAI/releases/tag/1.15.27"
BAD = "https://made-up.example.com/never-retrieved"
OUTSIDE = "https://not-in-the-report.example.org/page"

REPORT = f"""# crewAI eval 的每次运行成本

### 1. 直接回答
1.15.27 起 crewai eval --models 会报告每次运行的成本与耗时（{GOOD_2}）。

### 6. 来源清单
1. CLI 文档 | 一手 | 2026-10-01 | {GOOD}
2. 发布说明 | 一手 | 2026-10-08 | {GOOD_2}
3. 一篇没检索到的文章 | 二手 | 2026-10-09 | {BAD}
"""

CONTENT_REPORT = """# 产品与内容机会分析报告

## 1. 顶级内容机会

### C1. 降价潮全景图：把 $/token 换算成 $/任务

- **目标受众**：已付费订阅 AI 编码工具的开发者
- **核心角度**：只谈**每任务**实际成本
- **为什么有人在乎**：同日发布两款降价模型
- **建议大纲**：① 价格对照 → ② 每任务成本
- **评分**：9/10

### C2. 长上下文计费陷阱

- **评分**：8/10

## 2. 工具推荐机会
"""


def _chinese(units: int, seed: str = "讲") -> str:
    return (seed * units)[:units]


def script_json(total_units: int = 1548, *, citations=None, text_seed="讲", extra=None) -> str:
    """A model reply: 4 sections × 3 paragraphs, ~``total_units`` spoken units."""
    per = max(total_units // 12, 1)
    sections = []
    for s in range(4):
        paragraphs = []
        for p in range(3):
            paragraphs.append({
                "text": _chinese(per, text_seed),
                "citations": (citations or {}).get((s, p), []),
                "needs_review": False,
            })
        sections.append({"title": f"第{s + 1}节", "paragraphs": paragraphs})
    if extra:
        sections[0]["paragraphs"][0]["text"] = extra + sections[0]["paragraphs"][0]["text"]
    return json.dumps({"title": "crewAI eval 每次运行的成本与延迟", "sections": sections}, ensure_ascii=False)


class FakeLLM:
    """Stands in for core.llm.LLMClient; replies are queued per test."""

    replies: list[str] = []
    calls: list[list[dict]] = []

    def __init__(self, config, cancel_token=None, progress_cb=None):
        self.usage = {"requests": 0, "total_tokens": 0}

    def chat(self, messages, **kwargs):
        FakeLLM.calls.append(list(messages))
        self.usage["requests"] += 1
        return ChatResponse(content=FakeLLM.replies.pop(0))


@pytest.fixture
def llm(monkeypatch):
    FakeLLM.replies = []
    FakeLLM.calls = []
    monkeypatch.setattr("core.llm.LLMClient", FakeLLM)
    return FakeLLM


@pytest.fixture
def video_env(web_env, monkeypatch):
    tmp, users = web_env
    (tmp / "config").mkdir(exist_ok=True)
    shutil.copy(ROOT / "config" / "video_columns.yaml", tmp / "config" / "video_columns.yaml")
    store.use_state_dir(str(tmp / "state"))
    store.init_db()
    from core import report_meta

    monkeypatch.setattr(report_meta, "META_FILE", str(tmp / "state" / "report_meta.jsonl"))
    from web.services import video_render

    monkeypatch.setattr(video_render, "poll_seconds", lambda: 0.0)
    return tmp


class FakeResearch:
    """Stands in for web.runner.executor.start_job: writes a report, records state."""

    def __init__(self, tmp: Path, status: str = "success") -> None:
        self.tmp = tmp
        self.status = status
        self.jobs: list[str] = []

    def __call__(self, job_name, started_by, config_dir="config", jobs_dir="jobs", verbose=False):
        from core import report_meta
        from core.state import StateManager

        self.jobs.append(job_name)
        future: Future = Future()
        future.set_result(None)
        task = type("Task", (), {"future": future, "status": self.status, "job_name": job_name})()
        if self.status != "success":
            StateManager.update(job_name, "failed", error="search quota exhausted")
            return task
        rel = f"output/video/{job_name.split('/')[-1]}.md"
        path = self.tmp / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(REPORT, encoding="utf-8")
        StateManager.update(job_name, "success", output_path=rel)
        report_meta.append_record(rel, job_name, extra={"citation_check": {
            "total": 3, "matched": 2, "unmatched": 1, "unmatched_examples": [BAD]}})
        return task


@pytest.fixture
def research(video_env, monkeypatch) -> FakeResearch:
    fake = FakeResearch(video_env)
    monkeypatch.setattr("web.runner.executor.start_job", fake)
    return fake


# --- Length, topics, columns -------------------------------------------------


def test_spoken_units_count_words_not_letters():
    assert length.spoken_units("用 crewAI eval 跑 3 次。") == 3 + 2 + 1  # 用跑次 + crewAI eval + 3
    assert length.budget_for(6) == round(360 * length.UNITS_PER_SECOND)
    assert sum(length.allocate(100, [1, 2, 0])) == 100


def test_topics_come_from_the_newest_round_with_a_content_report(tmp_path):
    base = tmp_path / video_topics.PIPELINE_DIR
    (base / "2026-10-01").mkdir(parents=True)
    (base / "2026-10-01" / video_topics.CONTENT_FILE).write_text(CONTENT_REPORT, encoding="utf-8")
    (base / "2026-10-05").mkdir()  # newer round, content stage never ran
    round_date, topics = video_topics.load(str(tmp_path))
    assert round_date == "2026-10-01"
    assert [t.id for t in topics] == ["2026-10-01/C1", "2026-10-01/C2"]
    assert topics[0].score == 9 and topics[0].fields["目标受众"] == "已付费订阅 AI 编码工具的开发者"
    assert video_topics.find(str(tmp_path), "2026-10-01/C2").title == "长上下文计费陷阱"


def test_the_shipped_column_config_is_valid():
    columns = video_columns.load(str(ROOT / "config"))
    deep = columns["deep_dive"]
    assert deep.name == "深度解读" and deep.target_minutes == 6
    assert "{topic_title}" in deep.research_prompt and "{date}" in deep.research_prompt


def test_a_bad_column_config_names_the_column(tmp_path):
    (tmp_path / "video_columns.yaml").write_text("broken:\n  target_minutes: 40\n  research_prompt: x\n",
                                                 encoding="utf-8")
    with pytest.raises(video_columns.ColumnError, match="broken"):
        video_columns.load(str(tmp_path))


# --- Writer ------------------------------------------------------------------


def _write(**kwargs):
    defaults = dict(report=REPORT, citation_check={"unmatched_examples": [BAD]},
                    topic={"title": "crewAI eval", "fields": {}}, column_style="深度解读",
                    target_minutes=6, llm_config=None)
    return writer.write(**{**defaults, **kwargs})


def test_writer_keeps_only_citations_the_report_traced(llm):
    llm.replies = [script_json(citations={(0, 0): [GOOD], (0, 1): [BAD], (0, 2): [OUTSIDE, GOOD_2]})]
    written = _write()
    first = written.sections[0]["paragraphs"]
    assert first[0]["evidence"] == "CITED" and first[0]["citations"] == [GOOD]
    assert first[1]["evidence"] == "NEEDS_REVIEW" and first[1]["citations"] == []  # failed the run's check
    assert first[2]["evidence"] == "NEEDS_REVIEW" and first[2]["citations"] == [GOOD_2]  # OUTSIDE not in report
    assert written.sections[1]["paragraphs"][0]["evidence"] == "UNCITED"
    assert written.length_check["dropped_citations"] == 2
    assert written.length_check["status"] == "OK"


def test_writer_revises_once_to_length(llm):
    llm.replies = [script_json(3000), script_json(1548)]
    written = _write()
    assert len(llm.calls) == 2
    assert "预算是" in llm.calls[1][-1]["content"]
    assert written.meta["attempts"] == 2 and written.length_check["status"] == "OK"


def test_writer_keeps_a_flagged_draft_when_the_revision_is_unusable(llm):
    llm.replies = [script_json(3000), "我无法完成这个请求"]
    written = _write()
    assert written.length_check["status"] == "OVER"  # flagged for the reviewer, not truncated


def test_writer_refuses_a_script_autovideo_cannot_voice(llm):
    llm.replies = [script_json(1548, text_seed="word ")]
    with pytest.raises(writer.ScriptError, match="中文"):
        _write()


def test_writer_flags_phrases_that_leak_the_process(llm):
    llm.replies = [script_json(1548, extra="接着上回说，")]
    assert any("接着上回" in p for p in _write().meta["problems"])


def test_writer_reads_a_fenced_reply(llm):
    llm.replies = ["好的，文稿如下：\n```json\n" + script_json() + "\n```"]
    assert _write().title == "crewAI eval 每次运行的成本与延迟"


# --- Workflow ------------------------------------------------------------------


def test_workflow_researches_then_writes_a_draft(video_env, research, llm):
    from web.services import video_workflow

    llm.replies = [script_json(citations={(0, 0): [GOOD]})]
    script = store.create_script("deep_dive", {"id": None, "title": "crewAI eval 的成本", "fields": {}})
    assert video_workflow.run(script["id"], started_by="editor", llm_config=object()) == "DRAFT"

    done = store.get_script(script["id"])
    assert done["status"] == "DRAFT" and done["current_version"] == 1
    assert done["research_job"] == research.jobs[0] == f"video/deep_dive_{script['id'][:8]}"
    job_yaml = (video_env / "jobs" / "video" / f"deep_dive_{script['id'][:8]}.yaml").read_text(encoding="utf-8")
    assert "crewAI eval 的成本" in job_yaml and "{topic_title}" not in job_yaml and "{date}" in job_yaml
    version = store.get_version(script["id"], 1)
    assert version["edit_type"] == "GENERATED"
    assert version["sections"][0]["paragraphs"][0]["citations"] == [GOOD]
    assert REPORT[:20] in FakeLLM.calls[0][1]["content"]  # the report reached the writer


def test_failed_research_fails_the_script_with_the_reason(video_env, monkeypatch, llm):
    from web.services import video_workflow

    monkeypatch.setattr("web.runner.executor.start_job", FakeResearch(video_env, status="failed"))
    script = store.create_script("deep_dive", {"id": None, "title": "x 的成本问题", "fields": {}})
    assert video_workflow.run(script["id"], llm_config=object()) == "FAILED"
    failed = store.get_script(script["id"])
    assert failed["status"] == "FAILED" and "search quota exhausted" in failed["error"]
    assert llm.calls == []


# --- AutoVideo fake ------------------------------------------------------------


class FakeAutoVideo:
    def __init__(self, statuses=("QUEUED", "RUNNING", "COMPLETED")) -> None:
        self.statuses = list(statuses)
        self.posts: list[dict] = []
        self.jobs: dict[str, dict] = {}
        self.files = {"video.mp4": b"\x00\x00\x00\x18ftypmp42 fake", "captions.srt": "1\n字幕\n".encode()}
        self.served: dict[str, bytes] = {}
        self.drop_create_response = False

    def client(self) -> AutoVideoClient:
        return AutoVideoClient("http://autovideo.test", transport=httpx.MockTransport(self.handle))

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST" and path == "/api/jobs":
            payload = json.loads(request.content)
            self.posts.append(payload)
            job_id = str(uuid.uuid4())
            if payload.get("input_mode") == "narration":  # AutoVideo's source.txt rule
                source = "\n\n".join("\n".join(p.strip() for p in c["paragraphs"]) for c in payload["chapters"])
            else:
                source = payload["source_text"]
            self.jobs[job_id] = {
                "job_id": job_id, "title": payload.get("title"), "status": "QUEUED",
                "source_sha256": hashlib.sha256(source.strip().encode()).hexdigest(),
                "created_at": datetime.now(timezone.utc).isoformat(), "attempt_count": 1,
            }
            if self.drop_create_response:
                raise httpx.ReadTimeout("lost", request=request)
            return httpx.Response(202, json={"job_id": job_id, "status": "QUEUED"})
        if request.method == "GET" and path == "/api/jobs":
            q = request.url.params.get("q", "")
            items = [j for j in self.jobs.values() if q in (j["title"] or "")]
            return httpx.Response(200, json={"items": items[::-1]})
        parts = path.strip("/").split("/")
        job = self.jobs.get(parts[2]) if len(parts) >= 3 else None
        if job is None:
            return httpx.Response(404, json={"detail": {"code": "JOB_NOT_FOUND", "message": "任务不存在"}})
        if len(parts) == 3:
            if self.statuses:
                job["status"] = self.statuses.pop(0)
            return httpx.Response(200, json=job)
        if parts[3:] == ["artifacts"]:
            return httpx.Response(200, json={"items": [
                {"name": n, "content_type": "video/mp4" if n.endswith(".mp4") else "text/plain",
                 "bytes": len(d), "sha256": hashlib.sha256(d).hexdigest(),
                 "download_url": f"/api/jobs/{job['job_id']}/artifacts/{n}"}
                for n, d in self.files.items()]})
        name = parts[4]
        return httpx.Response(200, content=self.served.get(name, self.files[name]))


@pytest.fixture
def autovideo(monkeypatch) -> FakeAutoVideo:
    from web.services import video_render

    fake = FakeAutoVideo()
    monkeypatch.setattr(video_render, "get_client", fake.client)
    return fake


def _approved_script(title: str = "定稿的选题") -> dict:
    script = store.create_script("deep_dive", {"id": None, "title": title, "fields": {}})
    sections = [{"title": "开场", "paragraphs": [{"text": _chinese(80), "citations": [], "evidence": "UNCITED"}]},
                {"title": "结论", "paragraphs": [{"text": "跑 crewai eval --models 看成本。", "citations": [GOOD],
                                                 "evidence": "CITED"}]}]
    store.add_version(script["id"], title="一篇定稿", sections=sections,
                      length_check=length.measure(sections, 6), edit_type="GENERATED")
    store.update_script(script["id"], status="APPROVED", approved_version=1)
    return store.get_script(script["id"])


def test_render_submits_the_approved_words_for_verbatim_narration(video_env, autovideo):
    from web.services import video_render

    render = video_render.create(_approved_script())
    assert video_render.process(render["id"]) == "COMPLETED"
    sent = autovideo.posts[0]
    assert sent["input_mode"] == "narration" and "source_text" not in sent
    assert sent["duration_mode"] == "content" and sent["video_form"] == "auto"
    assert sent["chapters"][0]["title"] == "开场"
    assert sent["chapters"][1]["paragraphs"] == ["跑 crewai eval --models 看成本。"]
    assert GOOD not in json.dumps(sent, ensure_ascii=False)  # citations stay behind
    done = store.get_render(render["id"])
    assert {a["name"] for a in done["artifacts"]} == {"video.mp4", "captions.srt"}
    assert (video_render.files_dir(render["id"]) / "video.mp4").read_bytes() == autovideo.files["video.mp4"]


def test_a_lost_create_response_is_reconciled_not_resubmitted(video_env, autovideo):
    from web.services import video_render

    autovideo.drop_create_response = True
    render = video_render.create(_approved_script())
    assert video_render.process(render["id"]) == "COMPLETED"
    assert len(autovideo.posts) == 1


def test_a_mismatched_download_is_not_kept(video_env, autovideo):
    from web.services import video_render

    autovideo.served["video.mp4"] = b"tampered"
    render = video_render.create(_approved_script())
    assert video_render.process(render["id"]) == "FAILED"
    assert store.get_render(render["id"])["error_code"] == "DOWNLOAD_FAILED"
    assert not (video_render.files_dir(render["id"]) / "video.mp4").exists()


def test_an_absolute_download_url_is_refused(tmp_path, autovideo):
    with pytest.raises(AutoVideoError) as caught:
        autovideo.client().download({"download_url": "http://elsewhere.test/v.mp4"}, tmp_path / "v.mp4")
    assert caught.value.code == "BAD_ARTIFACT_URL"


def test_long_section_titles_are_cut_to_autovideos_limit(video_env):
    from web.services import video_render

    version = {"sections": [{"title": "很长" * 30, "paragraphs": [{"text": "一段。"}, {"text": "  "}]}]}
    chapter = video_render.chapters(version)[0]
    assert len(chapter["title"]) == video_render.CHAPTER_TITLE_MAX
    assert chapter["paragraphs"] == ["一段。"]  # empty paragraphs are dropped


def test_one_render_in_flight_per_script(video_env):
    from web.services import video_render

    script = _approved_script()
    video_render.create(script)
    with pytest.raises(video_render.RenderError):
        video_render.create(script)


# --- HTTP API ------------------------------------------------------------------


def _login(client, username="editor", password="editor-pass-123"):
    assert client.post("/api/auth/login", json={"username": username, "password": password}).status_code == 200


def _csrf(client) -> dict:
    return {"X-CSRF-Token": client.cookies.get("ai_research_csrf") or ""}


def _wait(client, script_id: str, until: set[str], timeout: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        body = client.get(f"/api/video/scripts/{script_id}").json()
        if body["status"] in until or time.monotonic() > deadline:
            return body
        time.sleep(0.05)


def test_api_from_topic_to_video(client, video_env, research, llm, autovideo):
    base = video_env / "output" / video_topics.PIPELINE_DIR / "2026-10-01"
    base.mkdir(parents=True)
    (base / video_topics.CONTENT_FILE).write_text(CONTENT_REPORT, encoding="utf-8")
    llm.replies = [script_json(citations={(0, 0): [GOOD]})]
    _login(client)

    topics = client.get("/api/video/topics").json()
    assert topics["round"] == "2026-10-01" and topics["topics"][0]["scripted"] is False

    created = client.post("/api/video/scripts", json={"topic_id": "2026-10-01/C1"}, headers=_csrf(client))
    assert created.status_code == 202, created.text
    script_id = created.json()["id"]
    body = _wait(client, script_id, {"DRAFT", "FAILED"})
    assert body["status"] == "DRAFT", body.get("error")
    assert body["topic"]["fields"]["核心角度"] == "只谈每任务实际成本"
    assert client.get("/api/video/topics").json()["topics"][0]["scripted"] is True

    # Rendering before approval is refused.
    assert client.post(f"/api/video/scripts/{script_id}/render", json={}, headers=_csrf(client)).status_code == 409

    # Edit → version 2, then approve it.
    shown = body["version_shown"]
    shown["sections"][0]["paragraphs"][0]["text"] = "改过的开场，" + shown["sections"][0]["paragraphs"][0]["text"]
    edited = client.put(f"/api/video/scripts/{script_id}", headers=_csrf(client),
                        json={"title": shown["title"], "sections": shown["sections"]})
    assert edited.status_code == 200, edited.text
    assert edited.json()["version_shown"]["version"] == 2
    assert edited.json()["version_shown"]["edit_type"] == "EDITED"
    approved = client.post(f"/api/video/scripts/{script_id}/approve", json={}, headers=_csrf(client)).json()
    assert approved["status"] == "APPROVED" and approved["approved_version"] == 2

    render = client.post(f"/api/video/scripts/{script_id}/render", json={}, headers=_csrf(client))
    assert render.status_code == 202, render.text
    render_id = render.json()["id"]
    deadline = time.monotonic() + 10
    while (r := client.get(f"/api/video/renders/{render_id}").json())["status"] not in ("COMPLETED", "FAILED"):
        assert time.monotonic() < deadline
        time.sleep(0.05)
    assert r["status"] == "COMPLETED"
    first_paragraph = autovideo.posts[0]["chapters"][0]["paragraphs"][0]
    assert first_paragraph.startswith("改过的开场")  # the approved version, not v1
    video = next(f for f in r["files"] if f["name"] == "video.mp4")
    served = client.get(video["url"])
    assert served.status_code == 200 and served.content == autovideo.files["video.mp4"]


def test_api_manual_topic_and_validation(client, video_env, research, llm):
    llm.replies = [script_json()]
    _login(client)
    assert client.post("/api/video/scripts", json={}, headers=_csrf(client)).status_code == 422
    assert client.post("/api/video/scripts", json={"column": "nope", "topic": {"title": "一个手写选题"}},
                       headers=_csrf(client)).status_code == 422
    made = client.post("/api/video/scripts", json={"topic": {"title": "一个手写的选题", "angle": "讲成本"}},
                       headers=_csrf(client))
    assert made.status_code == 202
    assert _wait(client, made.json()["id"], {"DRAFT", "FAILED"})["status"] == "DRAFT"


def test_api_viewers_read_but_cannot_create(client, video_env):
    _login(client, "viewer", "viewer-pass-123")
    assert client.get("/api/video/scripts").status_code == 200
    assert client.get("/api/video/columns").json()[0]["key"] == "deep_dive"
    response = client.post("/api/video/scripts", json={"topic": {"title": "不该被创建"}}, headers=_csrf(client))
    assert response.status_code == 403


def test_api_unknown_ids(client, video_env):
    _login(client)
    assert client.get("/api/video/scripts/nope").status_code == 404
    assert client.get("/api/video/renders/nope/files/video.mp4").status_code == 404


# --- Recovery that does not kill live work ---------------------------------------
# Found live: the gateway started two short-lived extra app instances (port
# taken, exit at once); each ran startup recovery and failed a script whose
# research was running fine in the real instance.


def test_startup_recovery_spares_work_that_is_still_beating(video_env):
    import time as _time

    live = store.create_script("deep_dive", {"id": None, "title": "正在研究的选题", "fields": {}})
    dead = store.create_script("deep_dive", {"id": None, "title": "进程已死的选题", "fields": {}})
    with store.connect(write=True) as conn:
        conn.execute("UPDATE scripts SET heartbeat_at = ? WHERE id = ?", (_time.time() - 600, dead["id"]))
    assert store.fail_interrupted() == 1
    assert store.get_script(live["id"])["status"] == "RESEARCHING"
    assert store.get_script(dead["id"])["status"] == "FAILED"


def test_only_orphaned_renders_are_resumed(video_env):
    import time as _time
    from web.services import video_render

    followed = video_render.create(_approved_script("有人跟踪的出片"))
    orphan = video_render.create(_approved_script("没人跟踪的出片"))
    store.touch_render(followed["id"])
    with store.connect(write=True) as conn:
        conn.execute("UPDATE renders SET heartbeat_at = ? WHERE id = ?", (_time.time() - 600, orphan["id"]))
    assert store.orphaned_render_ids() == [orphan["id"]]


def test_heartbeat_beats_while_the_work_runs(video_env):
    script = store.create_script("deep_dive", {"id": None, "title": "心跳测试的选题", "fields": {}})
    with store.connect(write=True) as conn:
        conn.execute("UPDATE scripts SET heartbeat_at = 0 WHERE id = ?", (script["id"],))
    with store.beating(store.touch_script, script["id"], every=0.01):
        time.sleep(0.05)
    assert store.get_script(script["id"])["heartbeat_at"] > 0
