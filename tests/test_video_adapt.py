"""Video script adaptation (spec/video-adaptation.md): fact sheet, plan, draft, review, restyle.

The model is a fake that answers by step (it recognizes each step's system
prompt), so this runs offline.
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

import pytest

from core.llm import ChatResponse
from core.video import columns as video_columns
from core.video import length, store
from core.video.adapt import draft, facts, pipeline, review, styles
from core.video.adapt import plan as planning

ROOT = Path(__file__).resolve().parents[1]
PRICING = "https://platform.claude.com/docs/en/about-claude/pricing"
BATCH = "https://platform.claude.com/docs/en/build-with-claude/batch-processing"
SECONDHAND = "https://aicost.tools/blog/llm-batch-api-discount/"

REPORT = f"""# 缓存前缀纪律

### 1. 直接回答
缓存写入是输入价的 1.25 倍，Opus 5.5 和 Sonnet 5.5 的缓存读取是 0.05 倍（{PRICING}）。

### 4. 仍未确定的问题
批处理命中率区间只有二手转述（{SECONDHAND}）。

### 6. 来源清单
1. Pricing | 一手 | {PRICING}
2. Batch processing | 一手 | {BATCH}
3. 二手文章 | 二手 | {SECONDHAND}
"""
CITATION_CHECK = {"total": 3, "matched": 2, "unmatched": 1, "unmatched_examples": [SECONDHAND]}


def sentences_of(units: int, seed: str) -> str:
    """Chinese text of about ``units`` spoken units, in sentences of 25."""
    out, left = [], units
    while left > 0:
        n = min(25, left)
        out.append(seed * n + "。")
        left -= n
    return "".join(out)


SHEET = {
    "question": "时间戳会不会让缓存折扣失效",
    "answer": "会，而且会变成加价",
    "facts": [
        {"id": "F1", "claim": "缓存写入是输入价的 1.25 倍", "confidence": "已证实", "sources": [PRICING]},
        {"id": "F2", "claim": "Opus 5.5 和 Sonnet 5.5 的缓存读取是输入价的 0.05 倍", "confidence": "已证实",
         "sources": [PRICING]},
        {"id": "F3", "claim": "前缀每次都被打断时，比不开缓存贵 25%", "confidence": "推导", "sources": [],
         "derived_from": ["F1"], "formula": "1.25 − 1 = 0.25"},
        {"id": "F4", "claim": "批处理命中率在 30% 到 98% 之间", "confidence": "有迹象", "sources": [SECONDHAND]},
        {"id": "F5", "claim": "批处理缓存命中是尽力而为", "confidence": "已证实", "sources": [BATCH]},
        {"id": "F6", "claim": "回本要三次请求", "confidence": "推导", "derived_from": ["F1"]},
        {"id": "F1", "claim": "重复的编号", "confidence": "已证实", "sources": [PRICING]},
    ],
    "concepts": [{"term": "提示词缓存", "meaning": "复用不变的开头", "needs": []}],
}

ROLES = ("钩子", "机制", "反方", "行动")


def plan_json(beats: int = 4) -> dict:
    return {
        "audience": {"knows": ["用过大模型 API"], "lacks": ["缓存怎么收费"], "cares": "账单", "takeaway": "检查前缀"},
        "core_message": "缓存省不省钱取决于前缀稳不稳",
        "hook": {"kind": "反常识", "idea": "开了缓存反而更贵", "facts": ["F3"]},
        "glossary": [{"term": "提示词缓存", "plain": "复用不变的开头", "analogy": "复印店存档",
                      "mapping": {"存档费": "缓存写入"}, "breaks": "存档不会过期"}],
        "beats": [{"title": f"第{i + 1}节", "role": ROLES[i % 4], "point": "要点", "facts": ["F1", "F2", "F3"]
                   if i == 0 else ["F5"], "terms": ["提示词缓存"] if i == 0 else [], "budget": 1}
                  for i in range(beats)],
        "left_out": [{"fact": "F4", "reason": "只有二手来源", "to_description": True},
                     {"fact": "F6", "reason": "没写公式", "to_description": False}],
    }


def draft_json() -> dict:
    per = length.budget_for(6) // 12
    sections = []
    for s in range(4):
        paragraphs = []
        for p in range(3):
            text = sentences_of(per, "讲")
            para = {"text": text, "facts": [], "introduces": [], "recap": p == 2}
            if s == 0 and p == 0:
                para.update(text="提示词缓存就是复用不变的开头。" + sentences_of(per - 15, "讲"),
                            facts=["F3", "F1"], introduces=["提示词缓存"])
            if s == 1 and p == 0:
                para.update(text="提示词缓存省下的钱。" + sentences_of(per - 9, "讲"), facts=["F2"])
            paragraphs.append(para)
        sections.append({"title": f"第{s + 1}节", "paragraphs": paragraphs})
    return {"title": "一个时间戳让缓存变成罚款", "sections": sections,
            "description": {"summary": "前缀稳定才省钱", "details": ["写入 1.25 倍"], "open_questions": []}}


GOOD_READ = {"paragraphs": [], "retell": "前缀稳不稳决定缓存省不省钱",
             "scores": {"hook": 5, "clarity": 5, "pacing": 4, "style": 4}, "comments": ""}
CLEAN = {"issues": [], "core_message_match": True, "core_message_note": ""}


class StepLLM:
    """Answers each adaptation step from its own queue, recognized by the system prompt."""

    queues: dict[str, list] = {}
    calls: list[tuple[str, list[dict]]] = []

    STEPS = {
        "facts": facts.SYSTEM_PROMPT, "plan": planning.SYSTEM_PROMPT, "draft": draft.SYSTEM_PROMPT,
        "revise": draft.REVISE_PROMPT, "newcomer": review.NEWCOMER_PROMPT, "fidelity": review.FIDELITY_PROMPT,
    }

    def __init__(self, config, cancel_token=None, progress_cb=None):
        self.usage = {"requests": 0, "total_tokens": 0}

    def chat(self, messages, **kwargs):
        step = next(k for k, prompt in self.STEPS.items() if messages[0]["content"] == prompt)
        StepLLM.calls.append((step, list(messages)))
        self.usage["requests"] += 1
        queue = StepLLM.queues[step]
        reply = queue.pop(0) if len(queue) > 1 else queue[0]  # the last reply repeats
        return ChatResponse(content=reply if isinstance(reply, str) else json.dumps(reply, ensure_ascii=False))

    @classmethod
    def steps(cls) -> list[str]:
        return [step for step, _ in cls.calls]


@pytest.fixture
def llm(monkeypatch):
    StepLLM.queues = {"facts": [SHEET], "plan": [plan_json()], "draft": [draft_json()], "revise": [{"paragraphs": []}],
                      "newcomer": [GOOD_READ], "fidelity": [CLEAN]}
    StepLLM.calls = []
    monkeypatch.setattr("core.llm.LLMClient", StepLLM)
    return StepLLM


@pytest.fixture
def video_env(web_env, monkeypatch):
    tmp, _ = web_env
    for name in ("video_columns.yaml", "video_styles.yaml"):
        shutil.copy(ROOT / "config" / name, tmp / "config" / name)
    store.use_state_dir(str(tmp / "state"))
    store.init_db()
    from core import report_meta

    monkeypatch.setattr(report_meta, "META_FILE", str(tmp / "state" / "report_meta.jsonl"))
    rel = "output/video/c4.md"
    (tmp / "output" / "video").mkdir(parents=True, exist_ok=True)
    (tmp / rel).write_text(REPORT, encoding="utf-8")
    report_meta.append_record(rel, "video/deep_dive_c4", extra={"citation_check": CITATION_CHECK})
    monkeypatch.setattr("web.services.video_workflow.workspace_dir", lambda: str(tmp))
    return tmp


def column(**changes):
    base = video_columns.get(str(ROOT / "config"), "deep_dive")
    return video_columns.Column(**{**base.__dict__, **changes})


def style(tone: str = "explainer", depth: str = "intermediate"):
    return styles.resolve(str(ROOT / "config"), column(), depth, tone)


def adapt(**kwargs):
    defaults = dict(report=REPORT, citation_check=CITATION_CHECK, topic={"title": "缓存前缀纪律", "fields": {}},
                    column=column(), style=style(), llm_config=None)
    return pipeline.adapt(**{**defaults, **kwargs})


# --- Config ---------------------------------------------------------------------


def test_shipped_styles_cover_every_columns_defaults_and_tones():
    config = str(ROOT / "config")
    for col in video_columns.load(config).values():
        assert styles.resolve(config, col).label == "进阶 · 讲解通俗"
        for tone in col.allowed_tones:
            styles.resolve(config, col, tone=tone)
    with pytest.raises(styles.StyleError, match="不用"):
        styles.resolve(config, column(), tone="news")  # deep_dive does not allow news
    assert style("playful").as_dict()["presets"] == ["intermediate@1", "playful@1"]


# --- ① Fact sheet ------------------------------------------------------------------


def test_fact_sheet_keeps_traced_sources_and_settles_derived_facts():
    sheet, problems = facts.normalize(json.loads(json.dumps(SHEET)), {
        "https://platform.claude.com/docs/en/about-claude/pricing",
        "https://platform.claude.com/docs/en/build-with-claude/batch-processing"})
    by_id = {f["id"]: f for f in sheet["facts"]}
    assert len(sheet["facts"]) == 6  # the duplicate F1 is dropped
    assert by_id["F4"]["confidence"] == "未核实" and by_id["F4"]["sources"] == []  # untrusted source gone
    assert by_id["F3"]["confidence"] == "推导" and by_id["F3"]["sources"] == [PRICING]  # inherits its base
    assert by_id["F6"]["confidence"] == "未核实"  # derived without a formula
    assert sheet["dropped_sources"] == 1
    assert any("重复" in p for p in problems) and any("F6" in p for p in problems)


def test_fact_sheet_is_keyed_by_report_content_and_prompt_version(monkeypatch):
    first = facts.sheet_id(REPORT)
    assert first == facts.sheet_id(REPORT) != facts.sheet_id(REPORT + " ")
    monkeypatch.setattr(facts, "PROMPT_VERSION", facts.PROMPT_VERSION + 1)
    assert facts.sheet_id(REPORT) != first  # a new prompt never rewrites a sheet old versions point at


# --- ② Plan ----------------------------------------------------------------------------


def test_plan_gets_one_retry_for_structure_and_budgets_from_code(llm):
    sheet, _ = facts.normalize(json.loads(json.dumps(SHEET)), {PRICING, BATCH})
    llm.queues["plan"] = [plan_json(beats=2), plan_json(beats=4)]
    from core.llm import LLMClient

    plan, problems = planning.make(LLMClient(None), sheet, column(), {"title": "x", "fields": {}}, style(), 6)
    assert llm.steps() == ["plan", "plan"]
    assert "缺少这些角色的节" in llm.calls[1][1][1]["content"]  # the retry names the problem
    assert problems == [] and len(plan["beats"]) == 4
    assert sum(b["budget"] for b in plan["beats"]) == length.budget_for(6)


def test_a_plan_that_uses_too_many_facts_is_sent_back(llm, monkeypatch):
    # Found live: offered 43 facts, the plan used 40 and the newcomer drowned in numbers.
    sheet, _ = facts.normalize(json.loads(json.dumps(SHEET)), {PRICING, BATCH})
    monkeypatch.setattr(planning, "MIN_FACTS", 2)
    monkeypatch.setattr(planning, "UNITS_PER_FACT", length.budget_for(6) // 2)  # a budget of 2 facts
    llm.queues["plan"] = [plan_json(), plan_json()]
    from core.llm import LLMClient

    _, problems = planning.make(LLMClient(None), sheet, column(), {"title": "x", "fields": {}}, style(), 6)
    assert llm.steps() == ["plan", "plan"]
    assert "最多用 2 条事实" in llm.calls[0][1][1]["content"]
    assert "上限是 2 条" in llm.calls[1][1][1]["content"]
    assert any("上限是 2 条" in p for p in problems)  # still too many after the retry: reported


def test_fact_ids_written_with_their_claims_still_count(llm):
    # Found live: the plan model wrote "F14：缓存自 2026-02-05 起按 workspace 隔离。" and ten facts were dropped.
    sheet, _ = facts.normalize(json.loads(json.dumps(SHEET)), {PRICING, BATCH})
    wordy = plan_json()
    wordy["beats"][0]["facts"] = ["F1：缓存写入是输入价的 1.25 倍", "F2", "F2 Opus 5.5 和 Sonnet 5.5"]
    wordy["left_out"][0]["fact"] = "F4：批处理命中率在 30% 到 98% 之间"
    llm.queues["plan"] = [wordy]
    from core.llm import LLMClient

    plan, problems = planning.make(LLMClient(None), sheet, column(), {"title": "x", "fields": {}}, style(), 6)
    assert plan["beats"][0]["facts"] == ["F1", "F2"] and plan["left_out"][0]["fact"] == "F4"
    assert not any("不存在" in p for p in problems)
    known = {f["id"]: f for f in sheet["facts"]}
    assert draft.finalize_paragraph({"text": "一段。", "facts": ["F5：批处理……"]}, known)["facts"] == ["F5"]


def test_plan_problems_that_survive_the_retry_are_reported(llm):
    sheet, _ = facts.normalize(json.loads(json.dumps(SHEET)), {PRICING, BATCH})
    bad = plan_json(beats=2)
    bad["hook"]["facts"] = ["F4", "F99"]
    llm.queues["plan"] = [bad]
    from core.llm import LLMClient

    plan, problems = planning.make(LLMClient(None), sheet, column(), {"title": "x", "fields": {}}, style(), 6)
    assert plan["hook"]["facts"] == ["F4"]  # the unknown id is removed
    assert any("F99" in p for p in problems) and any("钩子" in p for p in problems)


# --- ③ Draft -----------------------------------------------------------------------------


def test_citations_come_from_the_facts_a_paragraph_names():
    sheet, _ = facts.normalize(json.loads(json.dumps(SHEET)), {PRICING, BATCH})
    known = {f["id"]: f for f in sheet["facts"]}
    cited = draft.finalize_paragraph({"text": "一段。", "facts": ["F2", "F5"]}, known)
    assert cited["evidence"] == "CITED" and cited["citations"] == [PRICING, BATCH]
    weak = draft.finalize_paragraph({"text": "一段。", "facts": ["F4"]}, known)
    assert weak["evidence"] == "NEEDS_REVIEW" and "F4" in weak["note"]
    unknown = draft.finalize_paragraph({"text": "一段。", "facts": ["F99"]}, known)
    assert unknown["facts"] == [] and unknown["evidence"] == "NEEDS_REVIEW"
    assert draft.finalize_paragraph({"text": "过渡。"}, known)["evidence"] == "UNCITED"


# --- ④a Rules -------------------------------------------------------------------------------


def test_numbers_in_model_names_are_not_counted():
    assert review.count_numbers("按 Opus 5.5 的价格算，20,000 token 跑 100 次，10 美元对 0.50 美元。") == 4
    assert review.count_numbers("GPT-6 和 v1.15.27 都发布了。") == 0


def _rule_ids(issues, severity=None):
    return {i["rule"] for i in issues if severity is None or i["severity"] == severity}


def test_rules_catch_what_a_listener_trips_over():
    sheet, _ = facts.normalize(json.loads(json.dumps(SHEET)), {PRICING, BATCH})
    plan = plan_json()
    sections = [
        {"title": "开场", "paragraphs": [
            {"text": "开场先说提示词缓存很贵。缓存写入 5 分钟是 1.25 倍，1 小时是 2 倍。", "facts": ["F4"],
             "introduces": [], "recap": False},
            {"text": "提示词缓存就是复用不变的开头，读取是 0.05 × 输入价。", "facts": ["F2"],
             "introduces": ["提示词缓存"], "recap": False},
        ]},
        {"title": "反方", "paragraphs": [
            {"text": "根据报告，这个区间我没拿到一手出处，得核实，降幅 41%–80%。", "facts": ["F99"],
             "introduces": [], "recap": False},
        ]},
    ]
    issues = review.rules(sections, column=column(), target_minutes=6, plan=plan, sheet=sheet, style=style())
    must = _rule_ids(issues, review.MUST)
    assert {"C1", "C4", "D2", "D4", "D5", "E1", "E3", "E4"} <= must
    c1 = next(i for i in issues if i["rule"] == "C1")
    assert (c1["section"], c1["paragraph"]) == (0, 0) and "解释之前" in c1["detail"]


def test_review_phrases_are_reviewer_talk_not_everyday_words():
    sections = [{"title": "开场", "paragraphs": [{"text": "读取的折扣一次没拿到。这个数我们没拿到一手来源。"}]}]
    d5 = [i for i in review.rules(sections, column=column(), target_minutes=6) if i["rule"] == "D5"]
    assert [i["detail"] for i in d5] == ["审稿用语进了口播：「没拿到一手」"]


def test_legacy_versions_get_only_the_checks_that_need_no_plan():
    sections = [{"title": "开场", "paragraphs": [{"text": "缓存写入 5 分钟是 1.25 倍，1 小时是 2 倍。没有 A/B 测试。"}]}]
    issues = review.rules(sections, column=column(), target_minutes=6)
    assert "C4" in _rule_ids(issues) and not _rule_ids(issues) & {"A1", "B1", "B3", "C1", "C7", "D2", "D4", "E1"}
    summary = review.summarize(issues, None, None)
    assert not summary["passed"] and summary["low_scores"] == []  # no model read, no scores to be low


# --- The whole pipeline -----------------------------------------------------------------------


def test_pipeline_passes_a_clean_script_in_five_calls(llm):
    adapted, sheet = adapt()
    assert llm.steps() == ["facts", "plan", "draft", "newcomer", "fidelity"]
    checks = adapted.meta["checks"]
    assert checks["summary"]["passed"], checks["rules"]
    assert checks["rounds"] == 0
    first = adapted.sections[0]["paragraphs"][0]
    assert first["citations"] == [PRICING] and first["evidence"] == "CITED"
    assert adapted.meta["style"]["presets"] == ["intermediate@1", "explainer@2"]
    assert adapted.meta["fact_sheet_id"] == facts.sheet_id(REPORT)
    assert adapted.meta["description"]["sources"][0] == {"url": PRICING, "facts": ["F3", "F1", "F2"]}
    assert "批处理命中率在 30% 到 98% 之间" in adapted.meta["description"]["not_covered"]
    assert adapted.length_check["status"] == "OK"
    # The newcomer never sees the facts; the report reaches only step ①.
    assert "F1" not in llm.calls[3][1][1]["content"]
    assert all(REPORT[:20] not in msgs[1]["content"] for step, msgs in llm.calls if step != "facts")


def test_pipeline_rewrites_only_flagged_paragraphs(llm):
    llm.queues["newcomer"] = [{**GOOD_READ, "paragraphs": [{"id": "S2P1", "questions": ["读取是跟什么比？"], "lost": []}]},
                              GOOD_READ]
    llm.queues["fidelity"] = [{**CLEAN, "issues": [{"id": "S3P2", "rule": "D2", "detail": "说成了整个系列"}]}, CLEAN]
    fixed = sentences_of(120, "改")
    llm.queues["revise"] = [{"paragraphs": [{"id": "S2P1", "text": fixed, "facts": ["F2"]},
                                            {"id": "S3P2", "text": fixed, "facts": []},
                                            {"id": "S4P3", "text": "不该改的段落。"}]}]
    before = draft_json()
    adapted, _ = adapt()
    assert llm.steps() == ["facts", "plan", "draft", "newcomer", "fidelity", "revise", "newcomer", "fidelity"]
    revise_request = llm.calls[5][1][1]["content"]
    assert "[S2P1]" in revise_request and "读取是跟什么比" in revise_request and "说成了整个系列" in revise_request
    # Only the changed paragraphs were re-checked for fidelity.
    assert "[S2P1]" in llm.calls[7][1][1]["content"] and "[S1P1]" not in llm.calls[7][1][1]["content"]
    texts = [[p["text"] for p in s["paragraphs"]] for s in adapted.sections]
    assert texts[1][0] == fixed and texts[2][1] == fixed
    assert texts[3][2] == before["sections"][3]["paragraphs"][2]["text"]  # not a target: untouched
    assert adapted.meta["checks"]["rounds"] == 1 and adapted.meta["checks"]["summary"]["passed"]


def test_pipeline_gives_up_after_two_rounds_and_says_what_is_left(llm):
    stuck = {**GOOD_READ, "paragraphs": [{"id": "S1P2", "questions": ["这是什么？"], "lost": []}]}
    llm.queues["newcomer"] = [stuck]
    llm.queues["revise"] = [{"paragraphs": [{"id": "S1P2", "text": sentences_of(120, "又")}]}]
    adapted, _ = adapt()
    assert llm.steps().count("revise") == pipeline.MAX_ROUNDS
    summary = adapted.meta["checks"]["summary"]
    assert not summary["passed"] and summary["questions"] == 1


def test_a_reused_fact_sheet_skips_step_one(llm):
    sheet, _ = facts.normalize(json.loads(json.dumps(SHEET)), {PRICING, BATCH})
    adapt(sheet=sheet)
    assert "facts" not in llm.steps()


# --- Workflow and API -----------------------------------------------------------------------


def _script_with_a_legacy_version(env: Path) -> dict:
    script = store.create_script("deep_dive", {"id": "2026-09-29/C4", "title": "缓存前缀纪律", "fields": {}})
    sections = [{"title": "开场", "paragraphs": [{"text": "缓存写入 5 分钟是 1.25 倍，1 小时是 2 倍。",
                                                 "citations": [PRICING], "evidence": "CITED"}]}]
    store.add_version(script["id"], title="旧版", sections=sections, length_check=length.measure(sections, 6),
                      edit_type="GENERATED")
    store.update_script(script["id"], status="DRAFT", report_path="output/video/c4.md")
    return store.get_script(script["id"])


def _login(client):
    assert client.post("/api/auth/login", json={"username": "editor", "password": "editor-pass-123"}).status_code == 200
    return {"X-CSRF-Token": client.cookies.get("ai_research_csrf") or ""}


def _wait(client, script_id, timeout=10.0):
    deadline = time.monotonic() + timeout
    while True:
        body = client.get(f"/api/video/scripts/{script_id}").json()
        if body["status"] != "WRITING" or time.monotonic() > deadline:
            return body
        time.sleep(0.05)


def test_restyle_adds_a_version_and_reuses_the_fact_sheet(client, video_env, llm):
    script = _script_with_a_legacy_version(video_env)
    csrf = _login(client)

    legacy = client.get(f"/api/video/scripts/{script['id']}").json()["version_shown"]
    assert "C4" in {i["rule"] for i in legacy["checks"]["rules"]}  # legacy versions get rule checks too

    made = client.post(f"/api/video/scripts/{script['id']}/restyle", json={"tone": "playful"}, headers=csrf)
    assert made.status_code == 202, made.text
    body = _wait(client, script["id"])
    assert body["status"] == "DRAFT" and body["error"] is None and body["stage"] is None
    shown = body["version_shown"]
    assert shown["version"] == 2 and shown["edit_type"] == "GENERATED"
    assert shown["meta"]["style"]["label"] == "进阶 · 风趣口语"
    assert [v["style"] for v in body["versions"]] == ["进阶 · 风趣口语", None]
    assert shown["checks"]["summary"]["passed"]

    facts_body = client.get(f"/api/video/scripts/{script['id']}/facts").json()
    assert facts_body["id"] == facts.sheet_id(REPORT) and len(facts_body["facts"]) == 6

    StepLLM.calls = []
    assert client.post(f"/api/video/scripts/{script['id']}/restyle", json={}, headers=csrf).status_code == 202
    assert _wait(client, script["id"])["version_shown"]["meta"]["style"]["tone"] == "explainer"
    assert "facts" not in StepLLM.steps()  # one fact sheet per report


def test_restyle_refusals(client, video_env, llm):
    script = _script_with_a_legacy_version(video_env)
    csrf = _login(client)
    url = f"/api/video/scripts/{script['id']}/restyle"
    assert client.post(url, json={"tone": "news"}, headers=csrf).status_code == 422  # not allowed in this column
    assert client.post(url, json={"tone": "nope"}, headers=csrf).status_code == 422
    store.update_script(script["id"], status="RESEARCHING")
    assert client.post(url, json={}, headers=csrf).status_code == 409
    store.update_script(script["id"], status="DRAFT", report_path="")
    assert client.post(url, json={}, headers=csrf).status_code == 409


def test_a_failed_restyle_keeps_the_script_usable(client, video_env, llm):
    script = _script_with_a_legacy_version(video_env)
    store.update_script(script["id"], status="APPROVED", approved_version=1)
    csrf = _login(client)
    llm.queues["draft"] = ["这不是 JSON"]
    assert client.post(f"/api/video/scripts/{script['id']}/restyle", json={}, headers=csrf).status_code == 202
    body = _wait(client, script["id"])
    assert body["status"] == "APPROVED" and body["approved_version"] == 1 and body["current_version"] == 1
    assert body["error"].startswith("换风格失败")


def test_editing_an_adapted_version_keeps_its_facts(client, video_env, llm):
    script = _script_with_a_legacy_version(video_env)
    csrf = _login(client)
    client.post(f"/api/video/scripts/{script['id']}/restyle", json={}, headers=csrf)
    shown = _wait(client, script["id"])["version_shown"]
    para = shown["sections"][0]["paragraphs"][0]
    para["text"] = "改过的开场。" + para["text"]
    para["citations"] = []  # whatever the page sends, citations follow the facts
    para["note"] = "审稿人的备注"
    edited = client.put(f"/api/video/scripts/{script['id']}", headers=csrf,
                        json={"title": shown["title"], "sections": shown["sections"]})
    assert edited.status_code == 200, edited.text
    version = edited.json()["version_shown"]
    first = version["sections"][0]["paragraphs"][0]
    assert first["facts"] == ["F3", "F1"] and first["citations"] == [PRICING] and first["note"] == "审稿人的备注"
    assert version["meta"]["style"]["tone"] == "explainer" and version["meta"]["plan"]["core_message"]
    assert version["checks"]["model_checks_stale"] is True

    # Editing the old legacy version keeps it legacy, even though an adapted version is current.
    old = client.get(f"/api/video/scripts/{script['id']}?version=1").json()["version_shown"]
    again = client.put(f"/api/video/scripts/{script['id']}", headers=csrf,
                       json={"title": old["title"], "sections": old["sections"], "base_version": 1}).json()
    assert "plan" not in again["version_shown"]["meta"]
    assert "facts" not in again["version_shown"]["sections"][0]["paragraphs"][0]


def test_a_column_set_to_adapt_writes_new_scripts_by_adaptation(video_env, llm, monkeypatch):
    from web.services import video_workflow

    real_get = video_columns.get
    monkeypatch.setattr(video_columns, "get", lambda d, k: video_columns.Column(**{**real_get(d, k).__dict__,
                                                                                  "writer": "adapt"}))
    script = store.create_script("deep_dive", {"id": None, "title": "缓存前缀纪律", "fields": {}})
    rel = "output/video/c4.md"

    class Done:
        status = "success"

        def __init__(self):
            from concurrent.futures import Future

            self.future = Future()
            self.future.set_result(None)

    def fake_start_job(job_name, *args, **kwargs):
        from core.state import StateManager

        StateManager.update(job_name, "success", output_path=rel)
        return Done()

    monkeypatch.setattr("web.runner.executor.start_job", fake_start_job)
    assert video_workflow.run(script["id"], llm_config=object()) == "DRAFT"
    version = store.get_version(script["id"], 1)
    assert version["meta"]["writer"] == "adapt" and version["meta"]["checks"]["summary"]["passed"]
    assert store.get_fact_sheet(facts.sheet_id(REPORT)) is not None


def test_an_interrupted_restyle_goes_back_to_draft(video_env):
    script = _script_with_a_legacy_version(video_env)
    with store.connect(write=True) as conn:
        conn.execute("UPDATE scripts SET status = 'WRITING', stage = '写稿', heartbeat_at = 0 WHERE id = ?",
                     (script["id"],))
    assert store.fail_interrupted() == 1
    back = store.get_script(script["id"])
    assert back["status"] == "DRAFT" and back["stage"] is None and "换风格" in back["error"]
