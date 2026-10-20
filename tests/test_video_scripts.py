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
from tests import golden_video_cells as golden
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
    #: the fact audit of a script written in one call is a separate call; it finds nothing unless a test queues findings
    audit_replies: list[str] = []
    audit_calls: list[list[dict]] = []
    #: the style audit is a second call after the fact audit
    style_replies: list[str] = []
    style_calls: list[list[dict]] = []

    def __init__(self, config, cancel_token=None, progress_cb=None):
        self.usage = {"requests": 0, "total_tokens": 0}

    def chat(self, messages, **kwargs):
        self.usage["requests"] += 1
        if messages[0]["content"].startswith("你是栏目风格审计员"):
            FakeLLM.style_calls.append(list(messages))
            return ChatResponse(content=FakeLLM.style_replies.pop(0) if FakeLLM.style_replies else '{"issues": []}')
        if messages[0]["content"].startswith("你是事实审计员"):
            FakeLLM.audit_calls.append(list(messages))
            return ChatResponse(content=FakeLLM.audit_replies.pop(0) if FakeLLM.audit_replies else '{"issues": []}')
        FakeLLM.calls.append(list(messages))
        return ChatResponse(content=FakeLLM.replies.pop(0))


@pytest.fixture
def llm(monkeypatch):
    FakeLLM.replies = []
    FakeLLM.calls = []
    FakeLLM.audit_replies = []
    FakeLLM.audit_calls = []
    FakeLLM.style_replies = []
    FakeLLM.style_calls = []
    monkeypatch.setattr("core.llm.LLMClient", FakeLLM)
    return FakeLLM


#: the jobs that declare ``topics:`` (core/video/topic_store.py), copied as shipped
TOPIC_SOURCE_JOBS = ("monitoring/ai_flash_discovery.yaml", "practical_ai_intelligence/07_product_content_opportunities.yaml")


def install_topic_sources(jobs_dir: Path) -> None:
    shipped = Path(__file__).resolve().parents[1] / "jobs"
    for rel in TOPIC_SOURCE_JOBS:
        (jobs_dir / rel).parent.mkdir(parents=True, exist_ok=True)
        (jobs_dir / rel).write_text((shipped / rel).read_text(encoding="utf-8"), encoding="utf-8")


@pytest.fixture
def video_env(web_env, monkeypatch):
    tmp, users = web_env
    (tmp / "config").mkdir(exist_ok=True)
    # These tests drive the one-call writer; the default column is `adapt`, which test_video_adapt.py covers.
    golden.install_recipes(tmp / "config", replace=(("  pipeline: adapt\n", "  pipeline: single\n"),))
    install_topic_sources(tmp / "jobs")
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


def test_segments_are_counted_the_way_autovideo_cuts_a_script():
    assert length.segment_count("先说一句。再说一句！还有吗？") == 3
    assert length.segment_count("第一段。\n第二段没有句号") == 2
    long_sentence = "，".join(["这是一个比较长的分句内容" * 2] * 4) + "。"  # over 80 characters: cut at a comma
    assert len(long_sentence) > 80 and length.segment_count(long_sentence) == 2
    assert length.segment_count("") == 0


def test_the_estimate_adds_the_pauses_and_matches_the_real_renders():
    # (units, sentences, chapters, real seconds): C4 v10 and the crewAI eval script, voiced by AutoVideo
    for units, segments, chapters, real in ((1683, 92, 6, 406.9), (1518, 63, 5, 353.9)):
        assert abs(length.estimated_seconds(units, segments, chapters) / real - 1) < 0.02
    assert length.estimated_seconds(0, 0, 0) == 0


def test_the_estimate_matches_the_voxcpm2_render_of_c4_v10():
    # the rendered script: 6 chapters, 19 paragraphs, 92 pieces, 1683 units; AutoVideo measured 390.03 s
    shape = json.loads((ROOT / "tests/fixtures_c4_v10_shape.json").read_text(encoding="utf-8"))
    sections = [{"title": str(i), "paragraphs": [{"text": text} for text in paragraphs]} for i, paragraphs in enumerate(shape)]
    check = length.measure(sections, 6, voice="voxcpm_anchor")
    assert check["segments"] == 92 and check["spoken_length"] == 1683
    assert abs(check["estimated_seconds"] / 390.03 - 1) < 0.02


def test_the_estimate_follows_the_voice_that_reads_it():
    assert length.speech_rate("voxcpm_anchor") > length.speech_rate(None) == length.SPEECH_UNITS_PER_SECOND
    # a voice nobody measured is counted at the default rate, and the default budget does not move
    assert length.estimated_seconds(1683, 92, 6, "zf_xiaoxiao") == length.estimated_seconds(1683, 92, 6)
    assert length.budget_for(6) == 1512 == length.budget_for(6, "")
    faster = length.budget_for(6, "voxcpm_anchor")
    assert faster > 1512 and abs(faster / 360 - length.units_per_second("voxcpm_anchor")) < 0.01
    sections = [{"title": "章", "paragraphs": [{"text": "这是一句话。" * 400}]}]
    check = length.measure(sections, 6, voice="voxcpm_anchor")
    assert check["voice"] == "voxcpm_anchor" and check["units_per_second"] == length.units_per_second("voxcpm_anchor")
    assert check["budget"] == faster and length.measure(sections, 6)["voice"] is None


@pytest.mark.parametrize("current, following, boundary, ms", length.GOLDEN)
def test_content_pauses_follow_the_rule_autovideo_uses(current, following, boundary, ms):
    assert round(length.content_pause(current, following, boundary) * 1000) == ms


def test_content_aware_voices_count_the_rests_by_what_the_pieces_say():
    sections = [
        {"title": "一", "paragraphs": [{"text": "开头不改呢？只有第一次十美分。"}, {"text": "再说账。好。"}]},
        {"title": "二", "paragraphs": [{"text": "先说钱。缓存有两个价。"}]},
    ]
    pieces, rests = length.content_pause_seconds(sections)
    assert pieces == 6
    # 问句 .52 + 段落 .66 + 短句 .46 (再说账。->好。) + 章 .85 + 短句 .46 (先说钱。->缓存有两个价。)
    assert abs(rests - (0.52 + 0.66 + 0.46 + 0.85 + 0.46)) < 1e-9
    flat = length.measure(sections, None)
    timed = length.measure(sections, None, voice="voxcpm_anchor")
    assert flat["segments"] == timed["segments"] == 6
    units = timed["spoken_length"]
    assert abs(timed["estimated_seconds"] - round(units / length.speech_rate("voxcpm_anchor")
                                                  + length.EDGE_MARGIN_SECONDS * 6 + rests)) <= 1


def test_a_script_of_short_sentences_is_judged_by_its_time_not_its_units():
    short = [{"title": "章", "paragraphs": [{"text": "好。" * 800}]}]  # 800 units, 800 sentences
    check = length.measure(short, 6)
    assert check["spoken_length"] == 800 and check["segments"] == 800
    assert check["estimated_seconds"] > 360 * 1.15 and check["status"] == "OVER"
    assert length.status(800, check["budget"]) == "UNDER"  # by units alone it would have looked short
    assert abs(check["ratio"] - check["estimated_seconds"] / 360) < 0.003  # the ratio is the time against the target


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


def test_a_bad_column_config_names_the_column_and_leaves_the_others_usable(tmp_path):
    golden.write_flat(tmp_path, "broken:\n  target_minutes: 40\n  research_prompt: x\nfine:\n  research_prompt: x\n")
    assert list(video_columns.load(str(tmp_path))) == ["fine"]  # one bad file does not stop the others
    with pytest.raises(video_columns.ColumnError, match="broken"):
        video_columns.get(str(tmp_path), "broken")


def test_a_column_may_target_a_short_video(tmp_path):
    body = "short:\n  target_minutes: {}\n  research_prompt: x\n"
    golden.write_flat(tmp_path, body.format("1.5"))
    assert video_columns.get(str(tmp_path), "short").target_minutes == 1.5
    golden.write_flat(tmp_path, body.format("1"))
    assert video_columns.get(str(tmp_path), "short").target_minutes == 1
    for bad in ("0.5", "16", "soon"):
        golden.write_flat(tmp_path, body.format(bad))
        with pytest.raises(video_columns.ColumnError, match="short"):
            video_columns.get(str(tmp_path), "short")


def test_the_budget_of_a_short_video_follows_its_seconds():
    assert length.budget_for(1.5, "voxcpm_anchor") == round(90 * length.units_per_second("voxcpm_anchor"))
    assert length.budget_for(1.5) < length.budget_for(2) < length.budget_for(6)


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


def test_workflow_retries_a_script_reply_cut_off_mid_json(video_env, research, llm):
    from web.services import video_workflow

    llm.replies = ['{"title": "AI 动态盘点", "sections": [{"title": "一", "paragraphs": [{"text": "半截', script_json(citations={(0, 0): [GOOD]})]
    script = store.create_script("deep_dive", {"id": None, "title": "被截断的回复", "fields": {}})
    assert video_workflow.run(script["id"], started_by="editor", llm_config=object()) == "DRAFT"


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


def test_the_render_uses_the_columns_voice_unless_one_is_chosen(video_env, autovideo):
    from web.services import video_render

    column_voice = video_columns.get(str(ROOT / "config"), "deep_dive").voice_id
    assert column_voice  # the column names the voice its length estimate assumes
    default = video_render.create(_approved_script("默认声音"))
    chosen = video_render.create(_approved_script("指定声音"), voice_id="zf_xiaoxiao")
    for render in (default, chosen):
        autovideo.statuses[:] = ["QUEUED", "RUNNING", "COMPLETED"]  # the fake serves each status once
        assert video_render.process(render["id"]) == "COMPLETED"
    assert [post["voice_id"] for post in autovideo.posts] == [column_voice, "zf_xiaoxiao"]


PACKAGE_YAML = """\
  package:
    accent: "#1f7a4d"
    badge: 快讯
    intro: {template: stinger, title: 快讯, subtitle: "{date}", seconds: 1.5, sound: synth_sting}
    outro: {template: end_card, title: 快讯, lines: ["来源见简介"], seconds: 3}
"""


def _column_with_package(tmp_path, block: str):
    golden.write_flat(tmp_path, "short:\n  target_minutes: 1.5\n  research_prompt: x\n" + block)
    return video_columns.get(str(tmp_path), "short")


def test_a_column_may_declare_its_intro_and_outro(tmp_path):
    column = _column_with_package(tmp_path, PACKAGE_YAML)
    assert column.package == {
        "accent": "#1f7a4d", "badge": "快讯",
        "intro": {"template": "stinger", "title": "快讯", "subtitle": "{date}", "seconds": 1.5, "sound": "synth_sting"},
        "outro": {"template": "end_card", "title": "快讯", "lines": ["来源见简介"], "seconds": 3}}
    assert column.as_dict()["package"] == column.package
    assert _column_with_package(tmp_path, "").package == {}


@pytest.mark.parametrize("block", [
    "  package: {intro: {template: end_card, title: 快讯}}\n",           # an outro template as an intro
    "  package: {intro: {template: stinger}}\n",                          # no title
    "  package: {intro: {template: stinger, title: 这个标题实在是太长了没法放下}}\n",
    "  package: {intro: {template: stinger, title: 快讯, seconds: 12}}\n",
    "  package: {intro: {template: stinger, title: 快讯, sound: ../x}}\n",
    "  package: {intro: {template: stinger, title: 快讯, color: red}}\n",   # unknown key
    "  package: {outro: {template: end_card, title: 快讯, lines: []}}\n",
    "  package: {accent: red}\n",
    "  package: {badge: 这个角标实在是太长了没法放下}\n",
    "  package: {badge: \"\"}\n",
    "  package: {middle: {}}\n",
    "  package: nope\n",
])
def test_a_bad_package_block_names_the_column(tmp_path, block):
    with pytest.raises(video_columns.ColumnError, match="short"):
        _column_with_package(tmp_path, block)


def test_the_package_date_is_the_topics_not_the_render_day(tmp_path):
    from web.services import video_render

    column = _column_with_package(tmp_path, PACKAGE_YAML)
    dated = {"round": "2026-10-19", "fields": {"发布日期": "2026-10-14"}}
    assert video_render.topic_date(dated) == "10月14日"
    assert video_render.topic_date({"round": "2026-10-19", "fields": {}}) == "10月19日"
    assert video_render.topic_date({"fields": {}}) == ""
    assert video_render.package_request(column, dated)["intro"]["subtitle"] == "10月14日"
    undated = video_render.package_request(column, {"id": None, "title": "手写选题", "fields": {}})
    assert "subtitle" not in undated["intro"] and undated["intro"]["title"] == "快讯"  # no date, no wrong date
    assert undated["outro"]["lines"] == ["来源见简介"] and undated["accent"] == "#1f7a4d"


def test_the_render_carries_the_columns_package_only_when_it_has_one(video_env, autovideo):
    from web.services import video_render

    first = video_render.create(_approved_script("没有包装"))
    autovideo.statuses[:] = ["QUEUED", "RUNNING", "COMPLETED"]
    assert video_render.process(first["id"]) == "COMPLETED"
    assert "package" not in autovideo.posts[0]

    path = video_env / "config" / "video_recipes" / "deep_dive.yaml"
    text = path.read_text(encoding="utf-8")
    assert "  voice_id: voxcpm_anchor\n" in text
    path.write_text(text.replace("  voice_id: voxcpm_anchor\n", "  voice_id: voxcpm_anchor\n" + PACKAGE_YAML, 1), encoding="utf-8")
    script = _approved_script("有包装")
    store.update_script(script["id"], topic={"id": None, "title": "有包装", "fields": {"发布日期": "2026-10-14"}})
    second = video_render.create(store.get_script(script["id"]))
    autovideo.statuses[:] = ["QUEUED", "RUNNING", "COMPLETED"]
    assert video_render.process(second["id"]) == "COMPLETED"
    sent = autovideo.posts[1]["package"]
    assert sent["intro"]["subtitle"] == "10月14日" and sent["outro"]["template"] == "end_card"


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


# --- Short columns: flash items, a point cut from an earlier research -------------

FLASH_REPORT = f"""### 1. 条目

### 1.1 crewAI eval 开始报告每次运行成本
- **一句话**：crewai eval --models 会报告每次运行的成本与耗时
- **发布日期**：2026-10-17
- **变化**：无成本字段 → 每次运行报告成本与耗时
- **对开发者的影响**：不用自己算每次评测花了多少。
- **评分**：8/10
- **证据来源**：[1]

### 3. 来源清单
1. 版本发布说明 | 一手 | 2026-10-17 | {GOOD_2}
"""


def test_the_writer_names_numbers_the_report_does_not_contain():
    sections = [{"title": "章", "paragraphs": [
        {"text": "1.15.27 起每次运行多花 77 倍，约 1,500 美分。"}, {"text": "再来 77 倍和 3 次。"}]}]
    report = "版本 1.15.27 之后；每次 1500 美分；3 次。"
    assert writer.ungrounded_numbers(sections, report) == ["77"]
    problems = writer.lint(sections, report)
    assert any("77" in p and "研究报告" in p for p in problems)
    assert writer.lint(sections) == []  # no report to check against


def test_numbers_match_by_value_not_by_spelling():
    sections = [{"title": "章", "paragraphs": [{"text": "缓存涨到 0.2 美元，Priority 输入 3.6 美元，共 1500 次。"}]}]
    assert writer.ungrounded_numbers(sections, "缓存 $0.20；Priority $3.60 输入；1,500 次") == []
    assert writer.ungrounded_numbers(sections, "缓存 $0.25；Priority $3.65") == ["0.2", "3.6", "1500"]


def test_api_flash_items_are_topics_and_a_point_is_cut_from_an_earlier_research(client, video_env, research, llm, autovideo):
    base = video_env / "output" / "flash"
    base.mkdir(parents=True)
    (base / "2026-10-19_ai_flash_discovery.md").write_text(FLASH_REPORT, encoding="utf-8")
    # each column's script is sized to its own budget; an over-long reply would be sent back for a rewrite
    budgets = [length.budget_for(video_columns.get(str(ROOT / "config"), key).target_minutes, "voxcpm_anchor")
               for key in ("flash", "point_short")]
    llm.replies = [script_json(budget - 5, citations={(0, 0): [GOOD]}) for budget in budgets]
    _login(client)

    listed = client.get("/api/video/topics", params={"source": "flash"}).json()
    assert listed["round"] == "2026-10-19" and listed["topics"][0]["id"] == "2026-10-19/F1"
    assert listed["topics"][0]["score"] == 8 and listed["topics"][0]["scripted"] is False
    assert client.get("/api/video/topics", params={"source": "nope"}).status_code == 422

    created = client.post("/api/video/scripts", json={"column": "flash", "topic_id": "2026-10-19/F1"},
                          headers=_csrf(client))
    assert created.status_code == 202, created.text
    first = _wait(client, created.json()["id"], {"DRAFT", "FAILED"})
    assert first["status"] == "DRAFT", first.get("error")
    assert len(research.jobs) == 1
    spec = (video_env / "jobs" / f"{research.jobs[0]}.yaml").read_text(encoding="utf-8")
    assert "无成本字段 → 每次运行报告成本与耗时" in spec and GOOD_2 in spec and "{topic_" not in spec
    assert first["version_shown"]["length_check"]["target_minutes"] == video_columns.get(str(ROOT / "config"), "flash").target_minutes
    # the version records which recipe wrote it: its file and the hash of what the file said
    made_with = first["version_shown"]["meta"]["cell"]
    assert made_with["recipe"] == "flash" and made_with["cell"] == "ai-developer-quick_take-short"
    assert len(made_with["sha256"]) == 64

    # A point cut from that script's research: no second research job, the same report and sources.
    cut = client.post("/api/video/scripts", headers=_csrf(client), json={
        "column": "point_short", "from_script": first["id"],
        "topic": {"title": "每次运行成本怎么看", "angle": "只讲成本字段", "audience": "开发者"}})
    assert cut.status_code == 202, cut.text
    second = _wait(client, cut.json()["id"], {"DRAFT", "FAILED"})
    assert second["status"] == "DRAFT", second.get("error")
    assert len(research.jobs) == 1
    assert second["report_path"] == first["report_path"] and second["topic"]["parent_script"] == first["id"]

    missing = client.post("/api/video/scripts", headers=_csrf(client), json={
        "column": "point_short", "from_script": "no-such-script", "topic": {"title": "某个知识点", "angle": "x"}})
    assert missing.status_code == 422


def test_the_render_carries_a_source_card_only_for_a_column_that_asks_and_a_report_that_lists_dated_sources(
        video_env, autovideo):
    from web.services import video_render

    (video_env / "output").mkdir(exist_ok=True)
    (video_env / "output" / "report.md").write_text(REPORT, encoding="utf-8")
    plain = _approved_script("没有来源卡")
    store.update_script(plain["id"], report_path="output/report.md")
    first = video_render.create(store.get_script(plain["id"]))
    autovideo.statuses[:] = ["QUEUED", "RUNNING", "COMPLETED"]
    assert video_render.process(first["id"]) == "COMPLETED"
    assert "sources" not in autovideo.posts[0]  # the column did not ask for one

    path = video_env / "config" / "video_recipes" / "deep_dive.yaml"
    text = path.read_text(encoding="utf-8")
    assert "  voice_id: voxcpm_anchor\n" in text
    path.write_text(text.replace("  voice_id: voxcpm_anchor\n", "  voice_id: voxcpm_anchor\n  source_card: true\n", 1), encoding="utf-8")
    carded = _approved_script("有来源卡")
    store.update_script(carded["id"], report_path="output/report.md")
    second = video_render.create(store.get_script(carded["id"]))
    autovideo.statuses[:] = ["QUEUED", "RUNNING", "COMPLETED"]
    assert video_render.process(second["id"]) == "COMPLETED"
    assert autovideo.posts[1]["sources"] == [{"title": "CLI 文档", "url": GOOD, "date": "2026-10-01"}]

    unreadable = _approved_script("报告不见了")  # no report on file: the video still renders, without the card
    third = video_render.create(store.get_script(unreadable["id"]))
    autovideo.statuses[:] = ["QUEUED", "RUNNING", "COMPLETED"]
    assert video_render.process(third["id"]) == "COMPLETED"
    assert "sources" not in autovideo.posts[2]


def test_a_script_can_choose_one_of_the_columns_video_forms_and_the_render_uses_it(client, video_env, research, llm, autovideo):
    from web.services import video_render

    _login(client)
    wrong = client.post("/api/video/scripts", headers=_csrf(client), json={
        "column": "flash", "topic": {"title": "某个选题的标题", "angle": "x"}, "video_form": "process_diagram"})
    assert wrong.status_code == 422 and "information_motion" in wrong.text  # flash only has the one form
    columns = {c["key"]: c for c in client.get("/api/video/columns").json()}
    assert columns["deep_dive"]["allowed_forms"] == ["auto", "information_motion", "process_diagram"]
    assert all("status" in c for c in columns.values())  # every recipe carries its own status

    # the choice is recorded on the script and is what the render asks AutoVideo for; a source card, which
    # AutoVideo takes only on the information-motion forms, is left out of a flow diagram
    (video_env / "output").mkdir(exist_ok=True)
    (video_env / "output" / "report.md").write_text(REPORT, encoding="utf-8")
    path = video_env / "config" / "video_recipes" / "deep_dive.yaml"
    path.write_text(path.read_text(encoding="utf-8").replace("  voice_id: voxcpm_anchor\n", "  voice_id: voxcpm_anchor\n  source_card: true\n", 1), encoding="utf-8")
    for form, expected in ((None, "auto"), ("process_diagram", "process_diagram")):
        script = _approved_script(f"形式 {form}")
        topic = {**script["topic"], **({"video_form": form} if form else {})}
        store.update_script(script["id"], report_path="output/report.md", topic=topic)
        render = video_render.create(store.get_script(script["id"]))
        autovideo.statuses[:] = ["QUEUED", "RUNNING", "COMPLETED"]
        assert video_render.process(render["id"]) == "COMPLETED"
        sent = autovideo.posts[-1]
        assert sent["video_form"] == expected
        assert ("sources" in sent) == (form is None)
    bad = _approved_script("不允许的形式")
    store.update_script(bad["id"], topic={**bad["topic"], "video_form": "knowledge_cards"})
    with pytest.raises(video_render.RenderError, match="不能用视频形式"):
        video_render.create(store.get_script(bad["id"]))


def test_a_topic_without_an_audience_is_for_the_cells_audience(client, video_env, research, llm):
    llm.replies = [script_json(300)]
    _login(client)
    made = client.post("/api/video/scripts", headers=_csrf(client), json={
        "column": "ai-non_tech-one_point-short", "topic": {"title": "一个没有写受众的选题", "angle": "x"}})
    assert made.status_code == 202, made.text
    script = _wait(client, made.json()["id"], {"DRAFT", "FAILED"})
    assert script["topic"]["fields"]["目标受众"] == "普通用户"
    assert script["column"]["key"] == "ai-non_tech-one_point-short"


ROUNDUP_REPORT = FLASH_REPORT.replace("### 3. 来源清单", """### 1.2 某框架 2.0 弃用旧接口
- **一句话**：旧接口在 2.0 里不再可用
- **发布日期**：2026-10-18
- **变化**：旧接口可用 → 2.0 起移除
- **对开发者的影响**：升级前要改调用方式。
- **评分**：7/10
- **证据来源**：[1]

### 3. 来源清单""")


def _roundup_budget(key: str) -> int:
    return length.budget_for(video_columns.get(str(ROOT / "config"), key).target_minutes, "voxcpm_anchor")


def test_api_a_roundup_is_written_from_chosen_flash_items_without_research(client, video_env, research, llm):
    base = video_env / "output" / "flash"
    base.mkdir(parents=True)
    (base / "2026-10-19_ai_flash_discovery.md").write_text(ROUNDUP_REPORT, encoding="utf-8")
    key = "ai-developer-roundup-long"
    llm.replies = [script_json(_roundup_budget(key) - 5, citations={(0, 0): [GOOD]})]
    _login(client)

    made = client.post("/api/video/scripts", headers=_csrf(client), json={
        "column": key, "topic_ids": ["2026-10-19/F2", "2026-10-19/F1"], "video_form": "information_motion"})
    assert made.status_code == 202, made.text
    script = _wait(client, made.json()["id"], {"DRAFT", "FAILED"})
    assert script["status"] == "DRAFT", script.get("error")
    assert research.jobs == []  # no research of its own: the discovery report is the evidence
    assert script["report_path"] == "output/flash/2026-10-19_ai_flash_discovery.md"
    fields = script["topic"]["fields"]
    assert fields["条目数"] == "2" and script["topic"]["items"] == ["2026-10-19/F2", "2026-10-19/F1"]
    assert fields["条目"].splitlines()[0].startswith("1. ")
    asked = llm.calls[0][-1]["content"]
    assert "条目数：2" in asked and fields["条目"].splitlines()[1] in asked
    assert script["version_shown"]["length_check"]["target_minutes"] == 4
    assert script["column"]["topic_kind"] == "roundup"


@pytest.mark.parametrize("body, code", [
    ({"topic_ids": []}, "topic_required"),
    ({"topic_ids": ["2026-10-19/F1"]}, "roundup_items"),
    ({"topic_ids": ["2026-10-19/F1", "2026-10-19/F1"]}, "roundup_items"),
    ({"topic_ids": ["2026-10-19/F1", "2026-10-19/F99"]}, "unknown_topic"),
    ({"topic_ids": ["2026-10-19/F1", "2026-10-19/C1"]}, "unknown_topic"),
    ({"topic_id": "2026-10-19/F1", "topic_ids": ["2026-10-19/F1", "2026-10-19/F2"]}, "topic_required"),
])
def test_api_a_roundup_needs_two_or_more_distinct_flash_items(client, video_env, research, llm, body, code):
    base = video_env / "output" / "flash"
    base.mkdir(parents=True)
    (base / "2026-10-19_ai_flash_discovery.md").write_text(ROUNDUP_REPORT, encoding="utf-8")
    _login(client)
    refused = client.post("/api/video/scripts", headers=_csrf(client), json={"column": "ai-developer-roundup-long", **body})
    assert refused.status_code == 422
    assert code in refused.text
    assert research.jobs == []


def test_api_a_comparison_researches_its_two_subjects_and_a_single_topic_column_refuses_them(client, video_env, research, llm):
    key = "ai-developer-comparison-long"
    llm.replies = [script_json(_roundup_budget(key) - 5, citations={(0, 0): [GOOD]})]
    _login(client)

    made = client.post("/api/video/scripts", headers=_csrf(client), json={
        "column": key, "topic": {"subjects": ["Claude Code", "Codex CLI"], "dimensions": "价格、上下文长度"}})
    assert made.status_code == 202, made.text
    script = _wait(client, made.json()["id"], {"DRAFT", "FAILED"})
    assert script["status"] == "DRAFT", script.get("error")
    spec = (video_env / "jobs" / f"{research.jobs[0]}.yaml").read_text(encoding="utf-8")
    assert "Claude Code；Codex CLI" in spec and "价格、上下文长度" in spec and "{topic_" not in spec
    assert script["topic"]["title"] == "Claude Code 和 Codex CLI：价格、上下文长度"
    asked = llm.calls[0][-1]["content"]
    assert "对比对象：Claude Code；Codex CLI" in asked and "对比维度：价格、上下文长度" in asked

    for subjects in (["只有一个"], ["同一个", "同一个"], []):
        bad = client.post("/api/video/scripts", headers=_csrf(client), json={
            "column": key, "topic": {"subjects": subjects, "dimensions": "价格"}})
        assert bad.status_code == 422 and "comparison_subjects" in bad.text
    wrong = client.post("/api/video/scripts", headers=_csrf(client), json={
        "column": "deep_dive", "topic": {"title": "一个普通选题", "subjects": ["A", "B"]}})
    assert wrong.status_code == 422 and "topic_not_for_column" in wrong.text


def test_api_cells_list_the_matrix_for_a_picker(client, video_env):
    _login(client)
    rows = client.get("/api/video/cells").json()
    assert rows and all({"id", "key", "status", "usable", "label", "column"} <= set(r) for r in rows)
    assert {r["key"] for r in rows} >= {"deep_dive", "flash", "point_short"}
    assert {r["id"] for r in rows} >= {"ai-developer-quick_take-short"}  # a recipe tagged with all four is placed
    assert client.get("/api/video/cells").status_code == 200


def test_api_a_short_script_gets_a_fact_audit_against_its_report(client, video_env, research, llm):
    key = "point_short"
    llm.replies = [script_json(_roundup_budget(key) - 5, citations={(0, 0): [GOOD]})]
    llm.audit_replies = ['{"issues": [{"id": "S1P1", "quote": "讲讲讲", "rule": "D2", "severity": "严重", '
                         '"problem": "把厂商自述说成已证实", "why": "报告没有独立来源", "suggestion": "改成官方说"},'
                         '{"id": "S9P9", "quote": "x", "rule": "D1", "severity": "严重", "problem": "越界的编号"}]}']
    _login(client)
    made = client.post("/api/video/scripts", headers=_csrf(client), json={
        "column": key, "topic": {"title": "一个要审计的选题", "angle": "x"}})
    script = _wait(client, made.json()["id"], {"DRAFT", "FAILED"})
    assert script["status"] == "DRAFT", script.get("error")
    assert len(llm.audit_calls) == 1 and "研究报告" in llm.audit_calls[0][-1]["content"]
    checks = script["version_shown"]["checks"]
    facts = [i for i in checks["issues"] if i["audit"] == "fact" and i["source"] == "model"]
    assert [(i["rule"], i["severity"], i["section"], i["paragraph"]) for i in facts] == [("D2", "serious", 0, 0)]
    assert checks["model_ran"] is True and checks["summary"]["serious"] == 1 and checks["summary"]["passed"] is False
    first = script["version_shown"]["sections"][0]["paragraphs"][0]
    assert first["evidence"] == "NEEDS_REVIEW" and "把厂商自述说成已证实" in first["note"]


def test_api_a_failed_audit_keeps_the_draft_and_says_why(client, video_env, research, llm):
    key = "point_short"
    llm.replies = [script_json(_roundup_budget(key) - 5, citations={(0, 0): [GOOD]})]
    llm.audit_replies = ["不是 JSON", "还是不是 JSON"]
    _login(client)
    made = client.post("/api/video/scripts", headers=_csrf(client), json={
        "column": key, "topic": {"title": "一个审计会失败的选题", "angle": "x"}})
    script = _wait(client, made.json()["id"], {"DRAFT", "FAILED"})
    assert script["status"] == "DRAFT", script.get("error")
    assert script["version_shown"]["checks"]["model_ran"] is False
    assert any("事实审计没有完成" in p for p in script["version_shown"]["meta"]["problems"])


def test_api_publish_pack_uses_the_renders_chapters(client, video_env, autovideo):
    from web.services import video_render

    script = _approved_script("发布包的选题")
    _login(client)
    first = client.get(f"/api/video/scripts/{script['id']}/publish")
    assert first.status_code == 200, first.text
    assert first.json()["title"] == "一篇定稿" and first.json()["chapters"] == []
    assert "还没有出片，所以没有章节时间。" in first.json()["warnings"]
    assert client.get("/api/video/scripts/no-such/publish").status_code == 404

    render = store.create_render(script["id"], 1, title="一篇定稿", source_text="x", source_sha256="0" * 64, request={})
    folder = video_render.files_dir(render["id"])
    folder.mkdir(parents=True)
    (folder / "chapters.json").write_text(json.dumps({"timeline": "packaged", "chapters": [
        {"title": "开场", "start_ms": 1500}, {"title": "展开", "start_ms": 30000}, {"title": "结论", "start_ms": 50000}]}),
        encoding="utf-8")
    store.update_render(render["id"], status="COMPLETED", artifacts=[{"name": "chapters.json"}])
    second = client.get(f"/api/video/scripts/{script['id']}/publish").json()
    assert [c["time"] for c in second["chapters"]] == ["00:00", "00:30", "00:50"]
    assert "00:30 展开" in second["description"]


def test_api_quality_compares_each_approved_script_with_its_draft(client, video_env):
    script = store.create_script("flash", {"id": None, "title": "质量的选题", "fields": {}})
    draft = [{"title": "节", "paragraphs": [{"text": "第一段内容不动。", "citations": [], "evidence": "UNCITED"},
                                          {"text": "第二段说得太绝对了。", "citations": [], "evidence": "UNCITED"}]}]
    final = [{"title": "节", "paragraphs": [{"text": "第一段内容不动。", "citations": [], "evidence": "UNCITED"},
                                          {"text": "第二段说得没那么绝对了。", "citations": [], "evidence": "UNCITED"}]}]
    store.add_version(script["id"], title="草稿", sections=draft, length_check=length.measure(draft, 1.25), edit_type="GENERATED")
    store.add_version(script["id"], title="定稿", sections=final, length_check=length.measure(final, 1.25), edit_type="EDITED")
    store.update_script(script["id"], status="APPROVED", approved_version=2)
    _login(client)
    body = client.get("/api/video/quality").json()
    assert body["columns"]["flash"]["scripts"] == 1
    row = body["scripts"][0]
    assert (row["unchanged"], row["edited"]) == (1, 1) and row["draft_version"] == 1 and row["approved_version"] == 2


def test_api_a_short_script_is_also_checked_against_its_columns_style(client, video_env, research, llm):
    key = "point_short"
    llm.replies = [script_json(_roundup_budget(key) - 5, citations={(0, 0): [GOOD]})]
    llm.style_replies = ['{"issues": [{"id": "S2P1", "quote": "讲讲讲", "rule": "S1", "severity": "一般", '
                         '"problem": "栏目规范说不讲上限参数，这段讲了输出上限", "why": "占掉篇幅", "suggestion": "删掉这句"},'
                         '{"id": "S9P9", "quote": "x", "rule": "S1", "problem": "越界"}]}']
    _login(client)
    made = client.post("/api/video/scripts", headers=_csrf(client), json={
        "column": key, "topic": {"title": "一个要查风格的选题", "angle": "x"}})
    script = _wait(client, made.json()["id"], {"DRAFT", "FAILED"})
    assert script["status"] == "DRAFT", script.get("error")
    assert len(llm.style_calls) == 1 and "栏目「一个知识点」" in llm.style_calls[0][-1]["content"]
    style = [i for i in script["version_shown"]["checks"]["issues"] if i["rule"] == "S1"]
    assert [(i["severity"], i["section"], i["paragraph"], i["source"]) for i in style] == [("must", 1, 0, "model")]
    assert script["version_shown"]["checks"]["summary"]["by_audit"]["communication"]["must"] >= 1


def test_api_a_failed_style_audit_keeps_the_fact_audit(client, video_env, research, llm):
    key = "point_short"
    llm.replies = [script_json(_roundup_budget(key) - 5, citations={(0, 0): [GOOD]})]
    llm.style_replies = ["不是 JSON", "还是不是 JSON"]
    _login(client)
    made = client.post("/api/video/scripts", headers=_csrf(client), json={
        "column": key, "topic": {"title": "一个风格审计会失败的选题", "angle": "x"}})
    script = _wait(client, made.json()["id"], {"DRAFT", "FAILED"})
    assert script["status"] == "DRAFT"
    assert script["version_shown"]["checks"]["model_ran"] is True  # the fact audit ran
    assert any("风格审计没有完成" in p for p in script["version_shown"]["meta"]["problems"])


# --- Publications and the pre-publish recheck -----------------------------------------


def _approved(client, column="flash", title="Gemini 4 Argon 上线"):
    script = store.create_script(column, {"id": "2026-10-19/F1", "title": title, "fields": {}})
    sections = [{"title": "开场", "paragraphs": [{"text": "Google 开放了 Gemini 4 Argon，输入价 2 美元。",
                                                 "citations": [GOOD], "evidence": "CITED"}]}]
    store.add_version(script["id"], title=title, sections=sections, length_check=length.measure(sections, 1.25),
                      edit_type="GENERATED")
    store.update_script(script["id"], status="APPROVED", approved_version=1)
    return store.get_script(script["id"])


def test_api_a_publication_records_a_link_and_a_date_and_marks_the_script_and_topic(client, video_env):
    script = _approved(client)
    _login(client)
    url = f"/api/video/scripts/{script['id']}/publications"
    made = client.post(url, headers=_csrf(client), json={"url": "https://www.bilibili.com/video/BV1xx", "published_on": "2026-10-19"})
    assert made.status_code == 201, made.text
    assert made.json()["version"] == 1 and made.json()["title"] == "Gemini 4 Argon 上线" and made.json()["published_on"] == "2026-10-19"
    detail = client.get(f"/api/video/scripts/{script['id']}").json()
    assert [p["url"] for p in detail["publications"]] == ["https://www.bilibili.com/video/BV1xx"]
    assert [s["published"] for s in client.get("/api/video/scripts").json()] == [True]
    # a second upload (b23.tv short links count) is another record, newest day first
    client.post(url, headers=_csrf(client), json={"url": "https://b23.tv/abc", "published_on": "2026-10-20"})
    assert [p["published_on"] for p in client.get(f"/api/video/scripts/{script['id']}").json()["publications"]] == ["2026-10-20", "2026-10-19"]
    gone = client.delete(f"{url}/{made.json()['id']}", headers=_csrf(client))
    assert gone.status_code == 200 and len(client.get(f"/api/video/scripts/{script['id']}").json()["publications"]) == 1
    assert client.delete(f"{url}/{made.json()['id']}", headers=_csrf(client)).status_code == 404


@pytest.mark.parametrize("body", [
    {"url": "ftp://www.bilibili.com/video/x"},
    {"url": "https://evil.example.com/video/BV1"},
    {"url": "https://notbilibili.com/video/BV1"},
    {"url": "https://www.bilibili.com/video/BV1", "published_on": "10/19/2026"},
    {"url": "https://www.bilibili.com/video/BV1", "published_on": "2999-01-01"},
    {"url": "https://www.bilibili.com/video/BV1", "version": 9},
    {"url": "https://www.bilibili.com/video/BV1", "render_id": "nope"},
])
def test_api_a_publication_needs_a_bilibili_link_a_real_date_and_known_version_and_render(client, video_env, body):
    script = _approved(client)
    _login(client)
    assert client.post(f"/api/video/scripts/{script['id']}/publications", headers=_csrf(client), json=body).status_code in (404, 422)
    assert client.get(f"/api/video/scripts/{script['id']}").json()["publications"] == []


def test_api_a_published_topic_is_flagged_in_the_topic_list(client, video_env):
    base = video_env / "output" / "flash"
    base.mkdir(parents=True)
    (base / "2026-10-19_ai_flash_discovery.md").write_text(FLASH_REPORT, encoding="utf-8")
    script = _approved(client)
    _login(client)
    before = client.get("/api/video/topics", params={"source": "flash"}).json()["topics"][0]
    assert before["published"] is False
    client.post(f"/api/video/scripts/{script['id']}/publications", headers=_csrf(client), json={"url": "https://b23.tv/x"})
    assert client.get("/api/video/topics", params={"source": "flash"}).json()["topics"][0]["published"] is True


class RecheckResearch(FakeResearch):
    """The recheck's search job: writes a report with the verdict it is told to."""

    def __init__(self, tmp, report: str):
        super().__init__(tmp)
        self.report = report

    def __call__(self, job_name, started_by, config_dir="config", jobs_dir="jobs", verbose=False):
        global REPORT
        saved, REPORT = REPORT, self.report
        try:
            return super().__call__(job_name, started_by, config_dir=config_dir, jobs_dir=jobs_dir, verbose=verbose)
        finally:
            REPORT = saved


def test_api_a_flash_script_can_be_rechecked_and_the_verdict_reaches_the_publish_pack(client, video_env, monkeypatch):
    script = _approved(client)
    (video_env / "output" / "video").mkdir(parents=True, exist_ok=True)
    (video_env / "output" / "video" / "r.md").write_text(REPORT, encoding="utf-8")
    store.update_script(script["id"], report_path="output/video/r.md")
    fake = RecheckResearch(video_env, "### 结论\n结论：有变化\n### 变化清单\n| 输入价 | 12 月涨到 4 美元 | https://x | 2026-10-20 |\n### 建议\n改第一句")
    monkeypatch.setattr("web.runner.executor.start_job", fake)
    _login(client)
    made = client.post(f"/api/video/scripts/{script['id']}/recheck", headers=_csrf(client))
    assert made.status_code == 202, made.text
    for _ in range(100):
        rows = client.get(f"/api/video/scripts/{script['id']}").json()["rechecks"]
        if rows and rows[0]["status"] != "RUNNING":
            break
        time.sleep(0.1)
    assert rows[0]["status"] == "DONE" and rows[0]["verdict"] == "有变化" and "12 月涨到 4 美元" in rows[0]["summary"]
    spec = (video_env / "jobs" / f"{fake.jobs[0]}.yaml").read_text(encoding="utf-8")
    assert "Google 开放了 Gemini 4 Argon" in spec and GOOD in spec and "{script}" not in spec
    pack = client.get(f"/api/video/scripts/{script['id']}/publish").json()
    assert pack["freshness"]["required"] and pack["freshness"]["last"]["verdict"] == "有变化"
    assert any("复核发现稿子里的事实有变化" in w for w in pack["warnings"])


def test_api_recheck_is_only_for_approved_flash_scripts(client, video_env):
    flash = _approved(client)
    deep = _approved(client, "deep_dive", "深度解读的选题")
    draft = store.create_script("flash", {"id": None, "title": "没定稿的", "fields": {}})
    _login(client)
    assert client.post(f"/api/video/scripts/{deep['id']}/recheck", headers=_csrf(client)).status_code == 409
    assert client.post(f"/api/video/scripts/{draft['id']}/recheck", headers=_csrf(client)).status_code == 409
    assert client.post("/api/video/scripts/nope/recheck", headers=_csrf(client)).status_code == 404
    assert flash["id"]


def test_recheck_parses_a_verdict_and_says_so_when_there_is_none():
    from web.services import video_recheck

    assert video_recheck.parse("### 结论\n结论：无变化\n### 变化清单\n无")[0] == "无变化"
    assert video_recheck.parse("结论: 无法确认\n来源打不开")[0] == "无法确认"
    verdict, summary = video_recheck.parse("我查了一下，好像没什么事")
    assert verdict == "无法确认" and "没有给出可识别的结论" in summary


def test_freshness_asks_for_a_recheck_after_a_day_and_trusts_a_recent_one():
    from datetime import datetime, timezone
    from web.services import video_recheck

    def stamp(hours_ago, now):
        return datetime.fromtimestamp(now - hours_ago * 3600, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S%z")

    now = 1_800_000_000.0
    column = video_columns.get(str(ROOT / "config"), "flash")
    script = {"created_at": stamp(30, now)}
    version = {"version": 1}
    old = video_recheck.freshness(script, version, column, [], now)
    assert old["stale"] and any("建议点「发布前复核」" in w for w in old["warnings"])
    young = video_recheck.freshness({"created_at": stamp(5, now)}, version, column, [], now)
    assert young["warnings"] == [] and not young["stale"]
    done = {"version": 1, "status": "DONE", "verdict": "无变化", "summary": "", "id": "r", "finished_at": stamp(2, now)}
    assert video_recheck.freshness(script, version, column, [done], now)["warnings"] == []
    stale = {**done, "finished_at": stamp(26, now)}
    assert any("上次复核是 26 小时前" in w for w in video_recheck.freshness(script, version, column, [stale], now)["warnings"])
    deep = video_columns.get(str(ROOT / "config"), "deep_dive")
    assert video_recheck.freshness(script, version, deep, [], now) == {"required": False, "warnings": []}


def test_the_audits_of_a_short_script_run_with_capped_timeouts(client, video_env, research, llm, monkeypatch):
    seen = []
    real = FakeLLM.__init__

    def spy(self, config, cancel_token=None, progress_cb=None):
        seen.append((config.request_timeout, config.timeout))
        real(self, config, cancel_token, progress_cb)

    monkeypatch.setattr(FakeLLM, "__init__", spy)
    llm.replies = [script_json(_roundup_budget("point_short") - 5, citations={(0, 0): [GOOD]})]
    _login(client)
    made = client.post("/api/video/scripts", headers=_csrf(client), json={
        "column": "point_short", "topic": {"title": "一个审计要限时的选题", "angle": "x"}})
    assert _wait(client, made.json()["id"], {"DRAFT", "FAILED"})["status"] == "DRAFT"
    writer_attempt, audit_attempt = seen[0], seen[-1]
    assert audit_attempt == (120, 240) and writer_attempt[0] > 120


def test_api_the_home_board_counts_stages_and_lists_what_needs_the_user(client, video_env):
    script = _approved(client)
    _login(client)
    board = client.get("/api/video/board").json()
    counts = {s["key"]: s["count"] for s in board["stages"]}
    assert counts["approved"] == 1 and counts["published"] == 0 and board["failed"] == 0
    assert [(a["kind"], a["script_id"]) for a in board["attention"] if a["kind"] != "recheck"] == [("render", script["id"])]
    client.post(f"/api/video/scripts/{script['id']}/publications", headers=_csrf(client), json={"url": "https://b23.tv/x"})
    board = client.get("/api/video/board").json()
    assert {s["key"]: s["count"] for s in board["stages"]}["published"] == 1 and board["attention"] == []


def _assistant(name, arguments, role="editor"):
    from types import SimpleNamespace

    from web.services import assistant_tools

    request = SimpleNamespace(method="GET", client=SimpleNamespace(host="127.0.0.1"), headers={}, cookies={})
    return assistant_tools.execute(name, arguments, request, {"username": "tester", "role": role})


def test_assistant_video_tools_query_start_and_record_without_posting_anywhere(client, video_env, llm, research):
    script = _approved(client)
    found = _assistant("query_video", {"action": "find", "text": "Argon"})
    assert [s["script_id"] for s in found["scripts"]] == [script["id"]]
    assert _assistant("query_video", {"action": "get", "script_id": script["id"]})["approved_version"] == 1
    assert {s["key"]: s["count"] for s in _assistant("query_video", {"action": "board"})["stages"]}["approved"] == 1

    # recording a link only stores it; the receipt is the record, nothing is sent anywhere
    done = _assistant("manage_video", {"action": "add_publication", "script_id": script["id"],
                                       "url": "https://b23.tv/abc", "published_on": "2026-10-19"})
    assert done["status"] == "recorded" and done["href"] == f"/video/{script['id']}"
    assert [s["published"] for s in _assistant("query_video", {"action": "list"})["scripts"]] == [True]
    with pytest.raises(Exception):
        _assistant("manage_video", {"action": "add_publication", "script_id": script["id"], "url": "https://evil.example.com/x"})

    # starting a script says what it costs and stops at a draft (it is not approved or rendered)
    started = _assistant("create_video", {"column": "deep_dive", "title": "助手发起的选题"})
    assert started["status"] == "started" and "草稿" in started["cost"]


def test_assistant_video_writes_need_an_editor(client, video_env):
    from fastapi import HTTPException

    script = _approved(client)
    with pytest.raises(HTTPException):
        _assistant("manage_video", {"action": "approve", "script_id": script["id"]}, role="viewer")
    assert _assistant("query_video", {"action": "board"}, role="viewer")["failed"] == 0


def test_api_shipments_list_approved_scripts_with_their_publications(client, video_env):
    script = _approved(client)
    store.create_script("deep_dive", {"id": None, "title": "还是草稿", "fields": {}})
    _login(client)
    rows = client.get("/api/video/shipments").json()
    assert [r["script_id"] for r in rows] == [script["id"]] and rows[0]["render"] is None and rows[0]["publications"] == []
    client.post(f"/api/video/scripts/{script['id']}/publications", headers=_csrf(client), json={"url": "https://b23.tv/x"})
    rows = client.get("/api/video/shipments").json()
    assert [p["url"] for p in rows[0]["publications"]] == ["https://b23.tv/x"] and rows[0]["warnings"] == []


def test_a_draft_whose_audits_died_with_the_process_keeps_its_draft_and_loses_the_stage(video_env):
    script = _approved_script("审计到一半")
    store.update_script(script["id"], status="DRAFT", stage="事实审计", heartbeat_at=1.0)
    assert store.fail_interrupted() == 1
    after = store.get_script(script["id"])
    assert after["status"] == "DRAFT" and after["stage"] is None and "审计被中断" in after["error"]
    assert after["current_version"] == 1


def test_a_crash_after_the_draft_was_saved_keeps_the_draft(video_env, research, llm, monkeypatch):
    from web.services import video_workflow

    llm.replies = [script_json(citations={(0, 0): [GOOD]})]
    monkeypatch.setattr(video_workflow, "_audit_short", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    script = store.create_script("deep_dive", {"id": None, "title": "审计崩了", "fields": {}})
    assert video_workflow.run(script["id"], started_by="editor", llm_config=object()) == "DRAFT"
    after = store.get_script(script["id"])
    assert after["current_version"] == 1 and after["status"] == "DRAFT" and "boom" in after["error"]


def test_a_cover_headline_is_not_cut_inside_a_model_name():
    from core.video import publish

    column = type("C", (), {"package": {}, "name": "快讯"})()
    assert publish.cover_text("OpenAI 弃用 GPT-4o 与 GPT-4.1 系列，2027年1月14日 API 下线", column)["headline"] == "OpenAI 弃用"
    assert publish.cover_text("短标题", column)["headline"] == "短标题"
