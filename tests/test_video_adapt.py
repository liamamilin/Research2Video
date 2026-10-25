"""Video script adaptation (spec/video-adaptation.md, spec/video-adaptation-audit.md).

Fact sheet, plan with its comprehension path, draft, the three audits, the
fix-then-delete handling of serious errors, restyling. The model is a fake that
answers by step (it recognizes each step's system prompt), so this runs
offline. What a real model notices cannot be tested here; these tests pin what
code decides.
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

import pytest

from core.llm import ChatResponse
from core.video import columns as video_columns
from tests import golden_video_cells as golden
from core.video import editorial, length, store
from core.video.adapt import common, draft, facts, pipeline, review, styles
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
CORE = "缓存省不省钱取决于前缀稳不稳"


def plan_json(beats: int = 4) -> dict:
    return {
        "question": "为什么一个时间戳会让缓存变贵",
        "audience": {"knows": ["用过大模型 API"], "lacks": ["缓存怎么收费"], "cares": "账单", "takeaway": "检查前缀"},
        "goals": [{"question": f"问题{i}：为什么前缀要稳定", "answer": f"答案要点{i}", "facts": ["F1"]}
                  for i in range(1, 4)],
        "chain": [{"step": "写入要加价", "facts": ["F1"]}, {"step": "前缀一变就每次都写入", "facts": ["F3"]}],
        "glossary": [{"term": "提示词缓存", "plain": "复用不变的开头", "needs": [], "analogy": "复印店存档",
                      "mapping": {"存档费": "缓存写入"}, "breaks": "存档不会过期"}],
        "misconceptions": [{"wrong": "开了缓存就一定省钱", "fix": "讲清写入加价"}],
        "actions": [{"condition": "系统提示里有会变的内容时", "step": "把会变的内容挪到后面",
                     "verify": "连发两次看读取量", "needs": ["提示词缓存"]}],
        "must_cover": [{"item": "批处理", "beat": 3}],
        "core_message": CORE,
        "hook": {"kind": "反常识", "idea": "开了缓存反而更贵", "facts": ["F3"]},
        "beats": [{"title": f"第{i + 1}节", "role": ROLES[i % 4], "point": "要点", "facts": ["F1", "F2", "F3"]
                   if i == 0 else ["F5"], "terms": ["提示词缓存"] if i == 0 else [], "budget": 1}
                  for i in range(beats)],
        "scope_out": ["OpenAI 的缓存"],
        "left_out": [{"fact": "F4", "reason": "只有二手来源", "to_description": True},
                     {"fact": "F6", "reason": "没写公式", "to_description": False}],
        "fit": {"ok": True, "note": ""},
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


GOOD_READ = {"paragraphs": [], "answers": [{"id": f"G{i}", "answer": f"观众的回答{i}"} for i in range(1, 4)],
             "retell": "前缀稳不稳决定缓存省不省钱", "scores": {"hook": 5, "clarity": 5, "pacing": 4, "style": 4},
             "comments": ""}
CLEAN = {"issues": [], "core_message_match": True, "core_message_note": "",
         "grades": [{"id": f"G{i}", "correct": True, "note": ""} for i in range(1, 4)]}


def finding(key: str, quote: str, severity: str = "严重", rule: str = "D1", problem: str = "编造的细节") -> dict:
    return {"id": key, "quote": quote, "rule": rule, "severity": severity, "problem": problem,
            "why": "观众会当真", "suggestion": "删掉"}


class StepLLM:
    """Answers each adaptation step from its own queue, recognized by the system prompt."""

    queues: dict[str, list] = {}
    calls: list[tuple[str, list[dict], object]] = []

    STEPS = {
        "facts": facts.SYSTEM_PROMPT, "plan": planning.SYSTEM_PROMPT, "draft": draft.SYSTEM_PROMPT,
        "revise": draft.REVISE_PROMPT, "newcomer": review.NEWCOMER_PROMPT, "audit": review.FACT_AUDIT_PROMPT,
        "editorial": editorial.REWRITE_PROMPT, "compare": editorial.COMPARE_PROMPT,
        "copyedit": editorial.COPYEDIT_PROMPT,
        "rebrief": editorial.BRIEF_PROMPT,
    }

    def __init__(self, config, cancel_token=None, progress_cb=None):
        self.config = config
        self.usage = {"requests": 0, "total_tokens": 0}

    def chat(self, messages, **kwargs):
        from core.video import headline
        if messages[0]["content"] == headline.PROMPT:
            self.usage["requests"] += 1
            StepLLM.calls.append(("headline", list(messages), self.config))
            queue = StepLLM.queues.get("headline") or [{"candidates": [], "choice": -1, "reason": "原题看点清楚"}]
            reply = queue.pop(0) if len(queue) > 1 else queue[0]
            return ChatResponse(content=json.dumps(reply, ensure_ascii=False))
        step = next(k for k, prompt in self.STEPS.items() if messages[0]["content"] == prompt)
        StepLLM.calls.append((step, list(messages), self.config))
        self.usage["requests"] += 1
        queue = StepLLM.queues["editorial" if step == "copyedit" else step]
        reply = queue.pop(0) if len(queue) > 1 else queue[0]  # the last reply repeats
        return ChatResponse(content=reply if isinstance(reply, str) else json.dumps(reply, ensure_ascii=False))

    @classmethod
    def steps(cls) -> list[str]:
        return [step for step, _, _ in cls.calls]

    @classmethod
    def request(cls, step: str, nth: int = 0) -> str:
        return [msgs[1]["content"] for s, msgs, _ in cls.calls if s == step][nth]


@pytest.fixture
def llm(monkeypatch):
    StepLLM.queues = {"facts": [SHEET], "plan": [plan_json()], "draft": [draft_json()], "revise": [{"paragraphs": []}],
                      "newcomer": [GOOD_READ], "audit": [CLEAN], "editorial": [draft_json()],
                      "compare": [{"choice": "B", "reason": "用途更清楚", "remaining": []}],
                      "rebrief": [{"angle": CORE, "viewer_value": "弄清缓存账单", "throughline": "成本与使用条件",
                                   "facts": ["F1", "F3"], "material": json.dumps(SHEET, ensure_ascii=False)}]}
    StepLLM.calls = []
    monkeypatch.setattr("core.llm.LLMClient", StepLLM)
    return StepLLM


@pytest.fixture
def video_env(web_env, monkeypatch):
    tmp, _ = web_env
    shutil.copy(ROOT / "config" / "video_styles.yaml", tmp / "config" / "video_styles.yaml")
    golden.install_recipes(tmp / "config", replace=(("  pipeline: single\n", "  pipeline: adapt\n"),))
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


def sheet_of() -> dict:
    return facts.normalize(json.loads(json.dumps(SHEET)), {PRICING, BATCH})[0]


def adapt(**kwargs):
    defaults = dict(report=REPORT, citation_check=CITATION_CHECK, topic={"title": "缓存前缀纪律", "fields": {}},
                    column=column(), style=style(), llm_config=None)
    return pipeline.adapt(**{**defaults, **kwargs})


def make_plan(llm) -> tuple[dict, list[str]]:
    from core.llm import LLMClient

    return planning.make(LLMClient(None), sheet_of(), column(), {"title": "x", "fields": {}}, style(), 6)


# --- Config ---------------------------------------------------------------------


def test_shipped_styles_cover_every_columns_defaults_and_tones():
    config = str(ROOT / "config")
    labels = {"deep_dive": "进阶 · 讲解通俗", "flash": "进阶 · 新闻播报", "point_short": "进阶 · 讲解通俗",
              "ai-non_tech-deep_dive-long": "入门 · 讲解通俗", "ai-non_tech-quick_take-short": "入门 · 新闻播报",
              "ai-non_tech-one_point-short": "入门 · 讲解通俗", "ai-non_tech-roundup-long": "入门 · 简洁干货",
              "ai-developer-roundup-long": "进阶 · 简洁干货", "ai-developer-comparison-long": "进阶 · 讲解通俗",
              "report_digest": "进阶 · 简洁干货", "ai-non_tech-digest-long": "入门 · 简洁干货",
              "top_finding": "进阶 · 简洁干货", "ai-non_tech-headline-short": "入门 · 简洁干货",
              "action_list": "进阶 · 讲解通俗", "ai-non_tech-action_list-short": "入门 · 讲解通俗",
              "topic_digest": "入门 · 简洁干货"}
    columns = video_columns.load(config)
    assert set(columns) == set(labels)
    for key, col in columns.items():
        assert styles.resolve(config, col).label == labels[key]
        for tone in col.allowed_tones:
            styles.resolve(config, col, tone=tone)
    assert styles.resolve(config, column(), tone="news").tone.key == "news"
    assert style("playful").as_dict()["presets"] == ["intermediate@4", "playful@5"]


def test_length_tolerance_flags_a_fifth_over_target():
    # C4 v6 ran 437 s against 360 s and showed as on target under ±25%.
    assert length.status(1879, length.budget_for(6)) == "OVER"
    assert length.status(1650, length.budget_for(6)) == "OK"


# --- ① Fact sheet ------------------------------------------------------------------


def test_fact_sheet_keeps_traced_sources_and_settles_derived_facts():
    sheet, problems = facts.normalize(json.loads(json.dumps(SHEET)), {PRICING, BATCH})
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


def test_fact_extraction_and_cache_use_the_final_report_not_the_providers_thinking(llm):
    process = "先纠正原选题并核对资料。"
    raw = "<think>" + process + "</think>\n" + REPORT
    sheet, _ = facts.extract(llm(None), raw, CITATION_CHECK)
    assert process not in llm.request("facts") and REPORT in llm.request("facts")
    assert sheet["facts"] and facts.sheet_id(raw) == facts.sheet_id(REPORT)


@pytest.mark.parametrize("confidence", ["厂商自述", "有冲突"])
def test_sourced_claims_do_not_require_independent_replication_or_resolution(confidence):
    raw = {"facts": [{"id": "F1", "claim": "厂商称该配置吞吐更高，另一个来源给出不同结果",
                      "confidence": confidence, "sources": [PRICING]}]}
    sheet, problems = facts.normalize(raw, {PRICING})
    assert not problems and sheet["facts"][0]["confidence"] == confidence
    para = draft.finalize_paragraph({"text": "按厂商的说法，这个配置吞吐更高；另一个来源的结果不同。",
                                     "facts": ["F1"]}, common.facts_by_id(sheet))
    assert para["evidence"] == "CITED"
    assert not review.rules([{"paragraphs": [para]}], column=column(), target_minutes=1,
                            plan={"core_message": "不同测试的结果有差异"}, sheet=sheet)
    untraced, _ = facts.normalize(raw, set())
    assert untraced["facts"][0]["confidence"] == "未核实"
    assert draft.finalize_paragraph({"text": "据说吞吐更高", "facts": ["F1"]},
                                    common.facts_by_id(untraced))["evidence"] == "NEEDS_REVIEW"


# --- ② Plan and its comprehension path ------------------------------------------------------


def test_a_short_narrative_needs_no_course_goals_glossary_opposition_or_action(llm):
    short = {"core_message": CORE, "hook": {"facts": ["F1"]},
             "beats": [{"title": "缓存并非自动省钱", "role": "机制", "facts": ["F1", "F2", "F3"]}]}
    llm.queues["plan"] = [short]
    made, problems = make_plan(llm)
    assert llm.steps() == ["plan"]  # recipe section counts never trigger a retry
    assert len(made["beats"]) == 1
    assert all(made[key] == [] for key in ("goals", "chain", "glossary", "misconceptions", "actions", "must_cover"))
    assert problems == []


def test_scoped_adapt_audit_uses_full_context_and_ignores_unrelated_findings_and_actions(llm):
    sections = [{"paragraphs": [{"text": "以下是厂商给出的这组测试。", "facts": ["F1"]},
                                {"text": "吞吐有所提升。", "facts": ["F1"]}]}]
    llm.queues["audit"] = [{"issues": [finding("S1P1", "以下是厂商给出的这组测试。")],
                            "actions": [{"id": "S1P1", "works": False, "severity": "严重"}]}]
    checked = review.fact_audit(llm(None), sections, {}, sheet_of(), {}, [], only={"S1P2"})
    assert checked["issues"] == []
    request = llm.request("audit")
    assert "以下是厂商给出的这组测试。" in request.split("## 全文上下文", 1)[1].split("## 要检查的段落", 1)[0]
    targets = request.split("## 要检查的段落", 1)[1]
    assert "[S1P2]" in targets and "[S1P1]" not in targets


def test_plan_gets_one_retry_for_structure_and_budgets_from_code(llm):
    llm.queues["plan"] = [plan_json(beats=2), plan_json(beats=4)]
    plan, problems = make_plan(llm)
    assert llm.steps() == ["plan", "plan"]
    assert "必讲内容没有安排到有效的节" in llm.request("plan", 1)  # dangling references still need correction
    assert problems == [] and len(plan["beats"]) == 4
    assert sum(b["budget"] for b in plan["beats"]) == length.budget_for(6, column().voice_id)


def test_optional_planning_fields_do_not_force_extra_generation(llm):
    plan = plan_json()
    plan["actions"][0]["needs"] = ["缓存断点"]
    plan["actions"][0]["verify"] = ""
    llm.queues["plan"] = [plan]
    made, problems = make_plan(llm)
    assert llm.steps() == ["plan"]
    assert made["actions"][0]["verify"] == ""
    assert problems == []


def test_named_concepts_do_not_force_definitions_in_an_expert_narrative(llm):
    sheet = {"facts": [{"id": "F1", "claim": "本地框架把轨迹写在 agent 可写的目录里", "confidence": "已证实",
                        "sources": [PRICING]}],
             "concepts": [{"term": "写权限边界（write boundary）", "meaning": "被调查者能不能改这条记录", "needs": []}]}
    plan = plan_json()
    for beat in plan["beats"]:
        beat["facts"] = ["F1"]
    plan["hook"]["facts"] = []
    plan["left_out"] = []
    plan["goals"] = [{"question": f"问题{i}", "answer": "", "facts": ["F1"]} for i in range(3)]
    plan["chain"] = [{"step": "轨迹在可写目录里", "facts": ["F1"]}, {"step": "所以它能被改", "facts": ["F1"]}]
    plan["actions"] = [{"condition": "你做事故复盘时", "step": "把记录放到写权限边界之外", "verify": "删本地记录后外部还在",
                        "needs": []}]
    problems = planning.check(json.loads(json.dumps(plan)), sheet, column(), 6)
    assert not any("没有解释的概念" in p for p in problems)

    sections = [{"title": "一", "paragraphs": [{"text": "先看记录放在哪。", "facts": []},
                                              {"text": "记录要在 write boundary 之外。", "facts": []}]}]
    c1 = [i for i in review.rules(sections, column=column(), target_minutes=6, plan=plan, sheet=sheet, style=style())
          if i["rule"] == "C1"]
    assert c1 == []  # understanding is judged in context by the reader, not by matching a term


def test_the_comprehension_path_must_hold_together(llm):
    bad = plan_json()
    bad["goals"] = bad["goals"][:2] + [{"question": "没有依据的目标", "answer": "", "facts": []}]
    bad["chain"] = [{"step": "只有一步", "facts": ["F4"]}]  # F4 is left out, not placed in any section
    bad["must_cover"] = [{"item": "批处理", "beat": 9}]
    bad["fit"] = {"ok": False, "note": "建议只讲缓存，不讲批处理"}
    llm.queues["plan"] = [bad]
    _, problems = make_plan(llm)
    text = "\n".join(problems)
    for expected in ("没有依据的事实", "没有安排进任何一节：F4", "必讲内容没有安排到有效的节",
                     "讲不清。建议只讲缓存"):
        assert expected in text, expected


def test_necessary_facts_and_goals_are_not_limited_by_duration(llm):
    sheet = sheet_of()
    sheet["facts"] += [{"id": f"F{i}", "claim": f"条件{i}", "sources": [PRICING], "confidence": "已证实"}
                       for i in range(7, 30)]
    plan = plan_json()
    plan["goals"] = plan["goals"][:1]
    plan["chain"] = plan["chain"][:1]
    plan["beats"][0]["facts"] += [f"F{i}" for i in range(7, 30)]
    llm.queues["plan"] = [plan]
    made, problems = planning.make(llm(None), sheet, column(target_minutes=1), {}, style(), 1)
    assert llm.steps() == ["plan"] and problems == []
    assert len(made["beats"][0]["facts"]) > 20 and len(made["goals"]) == 1


def test_fact_ids_written_with_their_claims_still_count(llm):
    # Found live: the plan model wrote "F14：缓存自 2026-02-05 起按 workspace 隔离。" and ten facts were dropped.
    wordy = plan_json()
    wordy["beats"][0]["facts"] = ["F1：缓存写入是输入价的 1.25 倍", "F2", "F2 Opus 5.5 和 Sonnet 5.5"]
    wordy["left_out"][0]["fact"] = "F4：批处理命中率在 30% 到 98% 之间"
    llm.queues["plan"] = [wordy]
    plan, problems = make_plan(llm)
    assert plan["beats"][0]["facts"] == ["F1", "F2"] and plan["left_out"][0]["fact"] == "F4"
    assert not any("不存在" in p for p in problems)
    known = {f["id"]: f for f in sheet_of()["facts"]}
    assert draft.finalize_paragraph({"text": "一段。", "facts": ["F5：批处理……"]}, known)["facts"] == ["F5"]


def test_plan_problems_that_survive_the_retry_are_reported(llm):
    bad = plan_json(beats=2)
    bad["hook"]["facts"] = ["F4", "F99"]
    llm.queues["plan"] = [bad]
    plan, problems = make_plan(llm)
    assert plan["hook"]["facts"] == ["F4"]  # the unknown id is removed
    assert any("F99" in p for p in problems) and any("钩子" in p for p in problems)


# --- ③ Draft -----------------------------------------------------------------------------


def test_citations_come_from_the_facts_a_paragraph_names():
    known = {f["id"]: f for f in sheet_of()["facts"]}
    cited = draft.finalize_paragraph({"text": "一段。", "facts": ["F2", "F5"]}, known)
    assert cited["evidence"] == "CITED" and cited["citations"] == [PRICING, BATCH]
    weak = draft.finalize_paragraph({"text": "一段。", "facts": ["F4"]}, known)
    assert weak["evidence"] == "NEEDS_REVIEW" and "F4" in weak["note"]
    unknown = draft.finalize_paragraph({"text": "一段。", "facts": ["F99"]}, known)
    assert unknown["facts"] == [] and unknown["evidence"] == "NEEDS_REVIEW"
    assert draft.finalize_paragraph({"text": "过渡。"}, known)["evidence"] == "UNCITED"


def test_sentences_are_deleted_only_where_the_quote_really_is():
    text = "假设你在跑批处理。批处理跑了两万次调用。月底一看，缓存读取几乎为零。"
    assert common.remove_sentence(text, "批处理跑了两万次调用") == "假设你在跑批处理。月底一看，缓存读取几乎为零。"
    assert common.remove_sentence(text, "月底一看缓存读取几乎为零") == "假设你在跑批处理。批处理跑了两万次调用。"
    assert common.remove_sentence(text, "这句话不在段落里") is None
    sections = [{"title": "一", "paragraphs": [{"text": "只有一句话。"}]}]
    assert draft.drop_sentence(sections, 0, 0, "只有一句话") is None  # would empty the paragraph
    assert sections[0]["paragraphs"][0]["text"] == "只有一句话。"


# --- ④ Rule checks ---------------------------------------------------------------------------


def _rule_ids(issues, severity=None):
    return {i["rule"] for i in issues if severity is None or i["severity"] == severity}


def test_numbers_in_model_names_are_not_counted():
    assert review.count_numbers("按 Opus 5.5 的价格算，20,000 token 跑 100 次，10 美元对 0.50 美元。") == 4
    assert review.count_numbers("GPT-6 和 v1.15.27 都发布了。") == 0


def test_quantities_are_traced_to_the_facts_with_their_units():
    facts_text = "Opus 5.5、20,000 token 稳定前缀、100 次调用：不开缓存 $8.00，稳定缓存约 $0.50；缓存读取 0.05×"
    assert common.untraced("两万 token 的前缀跑 100 次，稳定命中约 0.5 美元，省 95%。", facts_text) == []
    assert common.untraced("批处理跑了两万次调用。", facts_text) == ["「两万次」在标注的事实里单位不同"]
    assert common.untraced("批处理跑了两万次调用。", "比不开缓存贵 25%") == ["「两万次」在标注的事实里找不到"]
    # Not caching: a database index. Units convert; a ratio the script computed itself is not in the facts.
    assert common.untraced("加索引后查询从 1200 毫秒降到 40 毫秒，快了 30 倍。",
                           "加索引前查询耗时 1.2 秒，加索引后 40 ms") == ["「30倍」在标注的事实里找不到"]
    # Small Chinese numerals are words, not data.
    assert common.untraced("一个开关，三件事，十分重要。", "") == []


def test_rules_catch_what_a_listener_trips_over():
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
    issues = review.rules(sections, column=column(), target_minutes=6, plan=plan, sheet=sheet_of(), style=style())
    assert {"D2", "D4"} <= _rule_ids(issues, review.MUST)
    assert {"D6", "E1"} <= _rule_ids(issues, review.WARN)
    assert not {"C4", "C5", "C6", "E4"} & _rule_ids(issues)
    assert "D6" in _rule_ids(issues, review.WARN)  # 5 分钟, 1 小时, 2 倍 are in none of the paragraph's facts


def test_transitions_and_repeated_numbers_are_not_judged_by_keyword_or_count():
    sections = [
        {"title": "一", "paragraphs": [{"text": "这一节讲索引。加索引后查询从 1200 毫秒降到 40 毫秒，表有 300 万行。"}]},
        {"title": "二", "paragraphs": [{"text": "接下来看看为什么会变快。"},
                                      {"text": "再说一遍：1200 毫秒、40 毫秒、300 万行。"}]},
    ]
    issues = review.rules(sections, column=column(), target_minutes=6)
    e5 = [i for i in issues if i["rule"] == "E5"]
    assert e5 == []
    e6 = [i for i in issues if i["rule"] == "E6"]
    assert e6 == []


def test_review_phrases_are_reviewer_talk_not_everyday_words():
    sections = [{"title": "开场", "paragraphs": [{"text": "读取的折扣一次没拿到。这个数我们没拿到一手来源。"}]}]
    d5 = [i for i in review.rules(sections, column=column(), target_minutes=6) if i["rule"] == "D5"]
    assert d5 == []  # a disclosed source limitation is not automatically reviewer leakage


def test_source_attribution_has_no_fixed_repetition_quota():
    said = "Vercel 官方说法：这个功能已经上线。"
    sections = [{"title": "一", "paragraphs": [{"text": said}, {"text": said + "按官方说法，团队不用重试。"}]},
                {"title": "二", "paragraphs": [{"text": said + "官方还称不会重复。"}, {"text": "接入前先在测试环境验证。"},
                                              {"text": "厂商自述的数字，还没有独立验证。"}]}]
    e7 = [i for i in review.rules(sections, column=column(), target_minutes=6) if i["rule"] == "E7"]
    assert e7 == []
    quiet = [{"title": "一", "paragraphs": [{"text": said}, {"text": "团队不用再手动重试。"}]}]
    assert not [i for i in review.rules(quiet, column=column(), target_minutes=6) if i["rule"] == "E7"]


def test_a_teaser_for_what_comes_next_is_advice_but_an_ordinary_next_step_is_not():
    sections = [{"title": "开场", "paragraphs": [{"text": "榜单只筛候选。接下来看它怎么改片。"},
                                              {"text": "下一步先在测试环境跑一遍。"}]}]
    e8 = [i for i in review.rules(sections, column=column(), target_minutes=6) if i["rule"] == "E8"]
    assert e8 == []


def test_source_provenance_words_are_reviewer_talk():
    sections = [{"title": "开场", "paragraphs": [{"text": "二手 AI 审稿称，评测可能复用了外壳。"},
                                              {"text": "逐门名称还没有确认。"}, {"text": "先确认你的账单再选型。"}]}]
    d5 = [(i["paragraph"], i["problem"]) for i in review.rules(sections, column=column(), target_minutes=6) if i["rule"] == "D5"]
    assert d5 == []  # ordinary provenance words need context, not a global blacklist


def test_legacy_versions_get_only_the_checks_that_need_no_plan():
    sections = [{"title": "开场", "paragraphs": [{"text": "缓存写入 5 分钟是 1.25 倍，1 小时是 2 倍。没有 A/B 测试。"}]}]
    issues = review.rules(sections, column=column(), target_minutes=6)
    assert "C4" not in _rule_ids(issues)
    assert not _rule_ids(issues) & {"A1", "B1", "B3", "C1", "C7", "D2", "D4", "D6", "E1"}
    checks = review.assemble(issues, None, None, None, sections)
    assert not checks["model_ran"] and not checks["summary"]["passed"] and checks["summary"]["low_scores"] == []


def test_checks_stored_by_the_first_prompt_version_still_read():
    old = {"rules": [{"rule": "C4", "severity": "must", "section": 0, "paragraph": 1, "detail": "一句话里有 3 个数字"}],
           "newcomer": {"paragraphs": {"S1P1": {"questions": ["X 是什么？"], "lost": []}}, "retell": "r",
                        "scores": {"hook": 4}, "comments": ""},
           "fidelity": {"issues": [{"id": "S2P1", "rule": "D2", "detail": "说成了整个系列"}],
                        "core_message_match": True, "core_message_note": ""},
           "summary": {}, "rounds": 2}
    new = review.normalize(old, section_count=2)
    assert new["version"] == 2 and new["rounds"] == 2 and new["model_ran"]
    assert {(i["rule"], i["audit"]) for i in new["issues"]} == {("C4", "communication"), ("N1", "comprehension"),
                                                               ("D2", "fact")}
    assert review.normalize(new, 2) is new


# --- The whole pipeline -----------------------------------------------------------------------


def test_pipeline_finishes_a_clean_script_with_independent_title_selection(llm):
    adapted, _ = adapt()
    assert llm.steps() == ["facts", "plan", "draft", "newcomer", "audit", "headline"]
    checks = adapted.meta["checks"]
    assert checks["summary"]["passed"], checks["issues"]
    assert checks["rounds"] == 0 and checks["deletions"] == [] and checks["quiz"]["score"] == 1.0
    first = adapted.sections[0]["paragraphs"][0]
    assert first["citations"] == [PRICING] and first["evidence"] == "CITED"
    assert adapted.meta["style"]["presets"] == ["intermediate@4", "explainer@5"]
    assert adapted.meta["prompts"] == {"facts": facts.PROMPT_VERSION, "plan": planning.PROMPT_VERSION,
                                       "draft": draft.PROMPT_VERSION, "review": review.PROMPT_VERSION}
    assert adapted.meta["fact_sheet_id"] == facts.sheet_id(REPORT)
    assert adapted.meta["description"]["sources"][0] == {"url": PRICING, "facts": ["F3", "F1", "F2"]}
    assert "批处理命中率在 30% 到 98% 之间" in adapted.meta["description"]["not_covered"]
    assert adapted.length_check["status"] == "OK"


def test_adaptation_edits_a_real_listening_problem_once_without_a_score_gate(llm):
    before = draft_json()["sections"][0]["paragraphs"][0]["text"]
    llm.queues["newcomer"] = [{**GOOD_READ, "paragraphs": [{"id": "S1P1", "lost": ["开场像内部纠错，看不出为什么要听"]}]}, GOOD_READ]
    llm.queues["editorial"] = [{"title": "缓存账单为什么没降", "sections": [{"title": "先看写入", "paragraphs": [
        {"text": "缓存开着，账单却未必会降。先看写入发生了什么。", "facts": ["F1", "F3"]}]}]}]
    adapted, _ = adapt()
    assert llm.steps() == ["facts", "plan", "draft", "newcomer", "audit", "rebrief", "editorial", "newcomer", "audit", "compare", "headline"]
    assert adapted.meta["checks"]["summary"]["passed"]
    assert adapted.meta["checks"]["editorial_attempted"]
    assert adapted.sections[0]["paragraphs"][0]["text"] != before
    assert adapted.title == "缓存账单为什么没降" and len(adapted.sections) == 1


def test_adaptation_may_drop_a_whole_branch_and_rechecks_shifted_paragraph_ids(llm):
    sections = [{"title": "主线", "paragraphs": [{"text": "缓存写入也有成本。", "facts": ["F1"]}]},
                {"title": "旁支", "paragraphs": [{"text": "先介绍完整历史。", "facts": []}]},
                {"title": "收获", "paragraphs": [{"text": "要看前缀是否稳定。", "facts": ["F3"]}]}]
    llm.queues["revise"] = [{"paragraphs": [{"id": "S2P1", "text": "", "facts": []}]}]
    revised, changed = draft.revise(llm(None), sections, {"S2P1": ["旁支无助理解，可以删除整节"]},
                                    plan_json(), sheet_of(), style())
    assert [s["title"] for s in revised] == ["主线", "收获"]
    assert changed == {"S1P1", "S2P1", "S3P1"}  # old findings are discarded and all new IDs are checked
    assert len(sections) == 3


def test_a_failed_fact_check_of_an_editorial_edit_keeps_the_checked_adaptation(llm):
    original = draft_json()["sections"][0]["paragraphs"][0]["text"]
    llm.queues["newcomer"] = [{**GOOD_READ, "paragraphs": [{"id": "S1P1", "lost": ["开场没有推进"]}]}, GOOD_READ]
    llm.queues["editorial"] = [{"title": "缓存也有成本", "sections": [{"title": "费用", "paragraphs": [
        {"text": "缓存可以省钱，先看它怎么收费。", "facts": ["F1"]}]}]}]
    llm.queues["audit"] = [CLEAN, "不能解析的复审回复"]
    adapted, _ = adapt()
    assert adapted.sections[0]["paragraphs"][0]["text"] == original
    assert adapted.meta["checks"]["summary"]["passed"]
    assert not adapted.meta["editorial_attempt"]["applied"]
    assert adapted.meta["editorial_attempt"]["problems"]
    assert not any("事实审计没有完成" in p for p in adapted.meta["problems"])


def test_an_editorial_preference_for_the_original_preserves_its_title_and_description(llm):
    llm.queues["newcomer"] = [{**GOOD_READ, "paragraphs": [{"id": "S1P1", "lost": ["开场铺垫有点长"]}]}, GOOD_READ]
    llm.queues["editorial"] = [{"title": "看看缓存的变化", "sections": [{"title": "变化", "paragraphs": [
        {"text": "缓存发生了一些变化，值得关注。", "facts": ["F1"]}]}], "description": {"summary": "新介绍"}}]
    llm.queues["compare"] = [{"choice": "A", "reason": "B 丢了具体机制，结尾空泛", "remaining": []}]
    adapted, _ = adapt()
    assert adapted.title == draft_json()["title"]
    assert adapted.meta["description"]["summary"] == draft_json()["description"]["summary"]
    assert not adapted.meta["editorial_attempt"]["applied"]
    assert adapted.meta["editorial_attempt"]["decision"]["choice"] == "A"


def test_editorial_uncertainty_is_not_erased_by_adaptation_finalization(llm):
    llm.queues["newcomer"] = [{**GOOD_READ, "paragraphs": [{"id": "S1P1", "lost": ["主线有点散"]}]}, GOOD_READ]
    llm.queues["editorial"] = [{"title": "缓存更省钱了吗", "sections": [{"title": "成本", "paragraphs": [
        {"text": "缓存也有写入成本。", "facts": ["F1"], "needs_review": True,
         "review_reason": "当前结论还有一个适用条件待核对"}]}]}]
    adapted, _ = adapt()
    assert adapted.title == draft_json()["title"]
    assert not adapted.meta["editorial_attempt"]["applied"]
    assert not adapted.meta["editorial_attempt"]["checks"]["summary"]["passed"]
    assert "compare" not in llm.steps()


def test_the_newcomer_gets_the_quiz_but_not_the_answers_or_facts(llm):
    adapt()
    newcomer = llm.request("newcomer")
    assert "问题1：为什么前缀要稳定" in newcomer
    assert "答案要点1" not in newcomer and "F1" not in newcomer and "1.25 倍" not in newcomer
    audit = llm.request("audit")
    assert "答案要点1" in audit and "观众的回答1" in audit
    assert all(REPORT[:20] not in msgs[1]["content"] for step, msgs, _ in llm.calls if step != "facts")


def test_a_wrong_quiz_answer_is_advice_without_rewriting_verified_facts(llm):
    llm.queues["audit"] = [{**CLEAN, "grades": [{"id": "G1", "correct": True}, {"id": "G2", "correct": False,
                                                                                "note": "没听出来"},
                                                 {"id": "G3", "correct": True}]}]
    adapted, _ = adapt()
    checks = adapted.meta["checks"]
    assert checks["quiz"]["score"] == 0.67 and checks["summary"]["passed"]
    assert "revise" not in llm.steps()
    assert any(i["rule"] == "N3" and "G2" in i["problem"] for i in checks["issues"])


def test_pipeline_rewrites_only_flagged_paragraphs_serious_first(llm):
    llm.queues["newcomer"] = [{**GOOD_READ, "paragraphs": [{"id": "S2P1", "questions": ["读取是跟什么比？"], "lost": []}]},
                              GOOD_READ]
    llm.queues["audit"] = [{**CLEAN, "issues": [finding("S3P2", "讲讲讲", rule="D2", problem="说成了整个系列")]}, CLEAN]
    fixed = sentences_of(120, "改")
    llm.queues["revise"] = [{"paragraphs": [{"id": "S2P1", "text": fixed, "facts": ["F2"]},
                                            {"id": "S3P2", "text": fixed, "facts": []},
                                            {"id": "S4P3", "text": "不该改的段落。"}]}]
    before = draft_json()
    adapted, _ = adapt()
    assert llm.steps() == ["facts", "plan", "draft", "newcomer", "audit", "revise", "newcomer", "audit", "headline"]
    revise_request = llm.request("revise")
    assert "【严重】D2：说成了整个系列" in revise_request
    assert "读取是跟什么比" not in revise_request  # reader advice alone cannot rewrite correct paragraphs
    # Only the changed paragraphs were audited again.
    assert "[S2P1]" in llm.request("audit", 1)  # context retains attribution and transitions
    scoped = llm.request("audit", 1).split("## 要检查的段落", 1)[1]
    assert "[S3P2]" in scoped and "[S2P1]" not in scoped
    texts = [[p["text"] for p in s["paragraphs"]] for s in adapted.sections]
    assert texts[1][0] == before["sections"][1]["paragraphs"][0]["text"] and texts[2][1] == fixed
    assert texts[3][2] == before["sections"][3]["paragraphs"][2]["text"]  # not a target: untouched
    checks = adapted.meta["checks"]
    assert checks["rounds"] == 1 and checks["summary"]["passed"]
    assert {(i["rule"], i["resolved"]) for i in checks["initial"] if i["severity"] != review.WARN} == {
        ("D2", True)}


def test_a_big_rewrite_that_comes_back_unreadable_is_retried_with_the_worst_half(monkeypatch):
    from core.video.adapt import draft, pipeline
    from core.video.adapt.common import AdaptError

    seen = []

    def revise(client, sections, todo, plan, sheet, style):
        seen.append(list(todo))
        if len(seen) == 1:
            raise AdaptError("模型没有返回 JSON")
        return sections, set(todo)

    monkeypatch.setattr(draft, "revise", revise)
    todo = {f"S1P{i}": ["x"] for i in range(1, 9)}
    _, changed = pipeline._revise(None, [], todo, {}, {}, None)
    assert seen[1] == ["S1P1", "S1P2", "S1P3", "S1P4"] and changed == set(seen[1])
    seen.clear()
    few = {"S1P1": ["x"], "S1P2": ["x"]}
    with pytest.raises(AdaptError):  # a small rewrite is not split: there is nothing to halve
        pipeline._revise(None, [], few, {}, {}, None)
    assert len(seen) == 1


def test_unresolved_minor_fact_findings_still_require_review(llm):
    llm.queues["audit"] = [{**CLEAN, "issues": [finding("S2P2", "讲讲讲", severity="一般", rule="D3",
                                                          problem="1.28 次应为 1.26 次")]}]
    adapted, _ = adapt()
    assert "revise" in llm.steps()
    minor = [i for i in adapted.meta["checks"]["issues"] if i["rule"] == "D3"]
    assert minor and minor[0]["severity"] == review.WARN and not adapted.meta["checks"]["summary"]["passed"]
    assert adapted.sections[1]["paragraphs"][1]["evidence"] == "NEEDS_REVIEW"


def test_a_serious_error_that_survives_the_rounds_loses_its_sentence(llm):
    script = draft_json()
    script["sections"][1]["paragraphs"][1]["text"] = "批处理跑了两万次调用。" + sentences_of(120, "讲")
    llm.queues["draft"] = [script]
    llm.queues["audit"] = [{**CLEAN, "issues": [finding("S2P2", "批处理跑了两万次调用")]}]
    adapted, _ = adapt()
    assert "两万次调用" in llm.request("audit")  # the code's untraced quantity reached the auditor
    assert "【严重】D1：编造的细节（原句：批处理跑了两万次调用）" in llm.request("revise")
    para = adapted.sections[1]["paragraphs"][1]
    assert "两万次" not in para["text"] and para["text"].startswith("讲")
    checks = adapted.meta["checks"]
    assert [(d["section"], d["paragraph"], d["quote"]) for d in checks["deletions"]] == [(1, 1, "批处理跑了两万次调用")]
    assert checks["deletions"][0]["before"].startswith("批处理跑了两万次调用")
    assert not any(i["severity"] == review.SERIOUS for i in checks["issues"])
    assert any(i["rule"] == "D1" and i["resolved"] == "deleted" for i in checks["initial"])
    assert adapted.length_check["spoken_length"] < length.measure(script["sections"], 6)["spoken_length"]


def test_an_action_that_fails_when_followed_is_a_serious_issue_even_if_the_audit_lists_no_error(llm):
    script = draft_json()
    script["sections"][2]["paragraphs"][1]["text"] = "把会变的东西挪到末尾。" + sentences_of(120, "讲")
    llm.queues["draft"] = [script]
    llm.queues["audit"] = [{**CLEAN, "actions": [
        {"id": "S3P2", "relies_on": "缓存只认断点之前的内容", "simulate": "挪到末尾后仍在断点之前，前缀照样变化",
         "works": False, "severity": "严重", "quote": "把会变的东西挪到末尾", "problem": "只挪位置不挪断点，缓存照样失效",
         "suggestion": "放到断点之后"},
        {"id": "S3P1", "relies_on": "底稿没有", "simulate": "", "works": True}]}]
    adapted, _ = adapt()
    assert "【严重】D7：只挪位置不挪断点，缓存照样失效（原句：把会变的东西挪到末尾）" in llm.request("revise")
    checks = adapted.meta["checks"]
    assert [(d["section"], d["paragraph"], d["rule"]) for d in checks["deletions"]] == [(2, 1, "D7")]
    assert any(i["rule"] == "D7" and i["resolved"] == "deleted" for i in checks["initial"])
    assert not any(i["rule"] == "D7" for i in checks["issues"])


def test_an_action_missing_a_condition_is_preserved_for_repair_and_review(llm):
    script = draft_json()
    script["sections"][2]["paragraphs"][1]["text"] = "读到零，多半是前缀没稳住。" + sentences_of(120, "讲")
    llm.queues["draft"] = [script]
    llm.queues["audit"] = [{**CLEAN, "actions": [
        {"id": "S3P2", "relies_on": "前缀太短不缓存", "simulate": "读零也可能是前缀太短", "works": False,
         "severity": "一般", "quote": "读到零，多半是前缀没稳住", "problem": "漏了前缀过短这个原因"}]}]
    adapted, _ = adapt()
    checks = adapted.meta["checks"]
    assert checks["deletions"] == [] and "读到零，多半是前缀没稳住" in adapted.sections[2]["paragraphs"][1]["text"]
    d7 = [i for i in checks["issues"] if i["rule"] == "D7"]
    assert [(i["severity"], i["section"], i["paragraph"]) for i in d7] == [("warn", 2, 1)]
    assert not checks["summary"]["passed"]
    assert adapted.sections[2]["paragraphs"][1]["evidence"] == "NEEDS_REVIEW"


def test_the_opening_and_the_core_message_are_never_cut(llm):
    script = draft_json()
    script["sections"][2]["paragraphs"][0]["text"] = f"一句话：{CORE}。" + sentences_of(110, "讲")
    llm.queues["draft"] = [script]
    llm.queues["audit"] = [{**CLEAN, "issues": [finding("S1P1", "提示词缓存就是复用不变的开头"),
                                                finding("S3P1", CORE)]}]
    adapted, _ = adapt()
    checks = adapted.meta["checks"]
    assert checks["deletions"] == []
    left = {(i["section"], i["paragraph"]): i["unhandled"] for i in checks["issues"] if i["severity"] == review.SERIOUS}
    assert "开场段" in left[(0, 0)] and "主旨" in left[(2, 0)]
    opening = adapted.sections[0]["paragraphs"][0]
    assert opening["text"].startswith("提示词缓存就是") and opening["evidence"] == "NEEDS_REVIEW"
    assert "事实审计" in opening["note"]


def test_the_fact_audit_can_run_on_its_own_model(llm):
    adapted, _ = adapt(llm_config="writer", review_llm_config="reviewer")
    by_step = {step: config for step, _, config in llm.calls}
    assert by_step["draft"] == "writer" and by_step["plan"] == "writer"
    assert by_step["newcomer"] == "writer" and by_step["audit"] == "reviewer"
    assert adapted.meta["review_usage"]["requests"] == 1


def test_reader_questions_do_not_trigger_rewrite_rounds(llm):
    stuck = {**GOOD_READ, "paragraphs": [{"id": "S1P2", "questions": [f"疑问{i}" for i in range(6)], "lost": []}]}
    llm.queues["newcomer"] = [stuck]
    llm.queues["revise"] = [{"paragraphs": [{"id": "S1P2", "text": sentences_of(120, "又")}]}]
    adapted, _ = adapt()
    assert llm.steps().count("revise") == 0
    summary = adapted.meta["checks"]["summary"]
    assert summary["passed"] and summary["questions"] == 6


def test_a_reused_fact_sheet_skips_step_one(llm):
    adapt(sheet=sheet_of())
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
    assert legacy["checks"]["model_ran"] is False  # rule checks alone still cannot invent a fact-audit pass

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
    assert client.post(url, json={"tone": "news"}, headers=csrf).status_code == 202
    _wait(client, script["id"])
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
    assert version["meta"]["prompts"]["review"] == review.PROMPT_VERSION
    assert version["checks"]["model_checks_stale"] is True and version["checks"]["version"] == 2

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


def test_short_scripts_get_number_and_dash_findings_as_warnings():
    sections = [{"title": "开场", "paragraphs": [
        {"text": "缓存写入 5 分钟是 1.25 倍，1 小时是 2 倍。降幅 41%–80%。", "facts": [], "introduces": [], "recap": False}]}]
    issues = review.rules(sections, column=column(), target_minutes=1)
    assert "E1" in _rule_ids(issues, review.WARN)
    assert "C4" not in _rule_ids(issues)
    assert not {"C4", "E1"} & _rule_ids(issues, review.MUST)


def test_a_reply_missing_one_comma_or_with_one_extra_is_still_read():
    from core.video.adapt.common import parse_json

    assert parse_json('{"a": [{"x": 1} {"y": 2}], "b": "q" "c": 3,}') == {"a": [{"x": 1}, {"y": 2}], "b": "q", "c": 3}
    assert parse_json('```json\n{"n": [1, 2,],}\n```') == {"n": [1, 2]}
    assert parse_json('{"text": "a, }"}') == {"text": "a, }"}  # a reply that already parses is left alone


def test_a_json_reply_gets_three_tries_and_the_later_ones_are_calmer():
    from pydantic import BaseModel

    from core.llm import ChatResponse
    from core.video.adapt.common import AdaptError, call_json

    class Out(BaseModel):
        ok: bool

    class Client:
        def __init__(self, replies):
            self.replies, self.temps = list(replies), []

        def chat(self, messages, **kwargs):
            self.temps.append(kwargs["temperature"])
            return ChatResponse(content=self.replies.pop(0))

    client = Client(["这不是 JSON", "", '{"ok": true}'])
    assert call_json(client, "s", "u", Out, temperature=0.6).ok and client.temps == [0.6, 0.2, 0.2]
    bad = Client(["不是", "不是", "不是"])
    with pytest.raises(AdaptError, match="3次"):
        call_json(bad, "s", "u", Out, temperature=0.6)
