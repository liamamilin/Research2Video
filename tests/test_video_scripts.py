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
    # These tests drive the one-call writer; the default column is `adapt`, which test_video_adapt.py covers.
    columns_yaml = golden.shipped_flat_yaml()
    assert "  writer: adapt\n" in columns_yaml
    (tmp / "config" / "video_columns.yaml").write_text(columns_yaml.replace("  writer: adapt\n", "  writer: legacy\n"),
                                                       encoding="utf-8")
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


def test_a_bad_column_config_names_the_column(tmp_path):
    (tmp_path / "video_columns.yaml").write_text("broken:\n  target_minutes: 40\n  research_prompt: x\n",
                                                 encoding="utf-8")
    with pytest.raises(video_columns.ColumnError, match="broken"):
        video_columns.load(str(tmp_path))


def test_a_column_may_target_a_short_video(tmp_path):
    body = "short:\n  target_minutes: {}\n  research_prompt: x\n"
    (tmp_path / "video_columns.yaml").write_text(body.format("1.5"), encoding="utf-8")
    assert video_columns.load(str(tmp_path))["short"].target_minutes == 1.5
    (tmp_path / "video_columns.yaml").write_text(body.format("1"), encoding="utf-8")
    assert video_columns.load(str(tmp_path))["short"].target_minutes == 1
    for bad in ("0.5", "16", "soon"):
        (tmp_path / "video_columns.yaml").write_text(body.format(bad), encoding="utf-8")
        with pytest.raises(video_columns.ColumnError, match="short"):
            video_columns.load(str(tmp_path))


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
    (tmp_path / "video_columns.yaml").write_text(
        "short:\n  target_minutes: 1.5\n  research_prompt: x\n" + block, encoding="utf-8")
    return video_columns.load(str(tmp_path))["short"]


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

    path = video_env / "config" / "video_columns.yaml"
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
    # the version records which matrix cell wrote it; this environment's columns are flat ones, which have none
    assert "cell" in first["version_shown"]["meta"] and first["version_shown"]["meta"]["cell"] is None

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

    path = video_env / "config" / "video_columns.yaml"
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
