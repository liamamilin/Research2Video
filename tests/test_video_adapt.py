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
from core.video import length, store
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
    }

    def __init__(self, config, cancel_token=None, progress_cb=None):
        self.config = config
        self.usage = {"requests": 0, "total_tokens": 0}

    def chat(self, messages, **kwargs):
        step = next(k for k, prompt in self.STEPS.items() if messages[0]["content"] == prompt)
        StepLLM.calls.append((step, list(messages), self.config))
        self.usage["requests"] += 1
        queue = StepLLM.queues[step]
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
                      "newcomer": [GOOD_READ], "audit": [CLEAN]}
    StepLLM.calls = []
    monkeypatch.setattr("core.llm.LLMClient", StepLLM)
    return StepLLM


@pytest.fixture
def video_env(web_env, monkeypatch):
    tmp, _ = web_env
    shutil.copy(ROOT / "config" / "video_styles.yaml", tmp / "config" / "video_styles.yaml")
    golden.install_recipes(tmp / "config")
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
              "ai-non_tech-one_point-short": "入门 · 讲解通俗",
              "ai-developer-roundup-long": "进阶 · 简洁干货", "ai-developer-comparison-long": "进阶 · 讲解通俗"}
    columns = video_columns.load(config)
    assert set(columns) == set(labels)
    for key, col in columns.items():
        assert styles.resolve(config, col).label == labels[key]
        for tone in col.allowed_tones:
            styles.resolve(config, col, tone=tone)
    with pytest.raises(styles.StyleError, match="不用"):
        styles.resolve(config, column(), tone="news")  # deep_dive does not allow news
    assert style("playful").as_dict()["presets"] == ["intermediate@1", "playful@2"]


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


# --- ② Plan and its comprehension path ------------------------------------------------------


def test_plan_gets_one_retry_for_structure_and_budgets_from_code(llm):
    llm.queues["plan"] = [plan_json(beats=2), plan_json(beats=4)]
    plan, problems = make_plan(llm)
    assert llm.steps() == ["plan", "plan"]
    assert "缺少这些角色的节" in llm.request("plan", 1)  # the retry names the problem
    assert problems == [] and len(plan["beats"]) == 4
    assert sum(b["budget"] for b in plan["beats"]) == length.budget_for(6, column().voice_id)


def test_an_action_resting_on_an_unexplained_concept_is_sent_back(llm):
    # Found on C4: v7 advised "move it to the end" without ever explaining cache breakpoints.
    bad = plan_json()
    bad["actions"][0]["needs"] = ["缓存断点"]
    bad["actions"][0]["verify"] = ""
    llm.queues["plan"] = [bad, plan_json()]
    _, problems = make_plan(llm)
    retry = llm.request("plan", 1)
    assert "依赖的概念没有解释：缓存断点" in retry and "缺少适用条件或验证方法" in retry
    assert problems == []


def test_concepts_the_fact_sheet_names_must_be_explained_before_actions_use_them(llm):
    # Found on C4 v8: an action said "before the cache breakpoint", breakpoints were never explained,
    # and the plan's own needs list had left them out. Not caching here: agent audit trails.
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
    assert any("没有解释的概念：写权限边界" in p for p in problems)
    plan["glossary"].append({"term": "写权限边界", "plain": "被调查者改不了的地方"})
    assert not any("没有解释的概念" in p for p in planning.check(json.loads(json.dumps(plan)), sheet, column(), 6))

    sections = [{"title": "一", "paragraphs": [{"text": "先看记录放在哪。", "facts": []},
                                              {"text": "记录要在 write boundary 之外。", "facts": []}]}]
    plan["glossary"].pop()
    c1 = [i for i in review.rules(sections, column=column(), target_minutes=6, plan=plan, sheet=sheet, style=style())
          if i["rule"] == "C1"]
    assert [(i["severity"], i["section"], i["paragraph"]) for i in c1] == [(review.WARN, 0, 1)]


def test_the_comprehension_path_must_hold_together(llm):
    bad = plan_json()
    bad["goals"] = bad["goals"][:2] + [{"question": "没有依据的目标", "answer": "", "facts": []}]
    bad["chain"] = [{"step": "只有一步", "facts": ["F4"]}]  # F4 is left out, not placed in any section
    bad["must_cover"] = [{"item": "批处理", "beat": 9}]
    bad["fit"] = {"ok": False, "note": "建议只讲缓存，不讲批处理"}
    llm.queues["plan"] = [bad]
    _, problems = make_plan(llm)
    text = "\n".join(problems)
    for expected in ("没有依据的事实", "因果链至少要有两步", "没有安排进任何一节：F4", "必讲内容没有安排到有效的节",
                     "讲不清。建议只讲缓存"):
        assert expected in text, expected


def test_a_plan_that_uses_too_many_facts_is_sent_back(llm, monkeypatch):
    # Found live: offered 43 facts, the plan used 40 and the newcomer drowned in numbers.
    monkeypatch.setattr(planning, "MIN_FACTS", 2)
    monkeypatch.setattr(planning, "UNITS_PER_FACT", length.budget_for(6) // 2)  # a budget of 2 facts
    llm.queues["plan"] = [plan_json(), plan_json()]
    _, problems = make_plan(llm)
    assert "最多用 2 条事实" in llm.request("plan", 0)
    assert "上限是 2 条" in llm.request("plan", 1)
    assert any("上限是 2 条" in p for p in problems)  # still too many after the retry: reported


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
    assert {"C1", "C4", "D2", "D4", "D5", "E1", "E3", "E4"} <= _rule_ids(issues, review.MUST)
    assert "D6" in _rule_ids(issues, review.WARN)  # 5 分钟, 1 小时, 2 倍 are in none of the paragraph's facts
    c1 = next(i for i in issues if i["rule"] == "C1")
    assert (c1["section"], c1["paragraph"]) == (0, 0) and "解释之前" in c1["problem"]
    assert c1["audit"] == review.COMPREHENSION and c1["why"] and c1["suggestion"]
    c4 = next(i for i in issues if i["rule"] == "C4")
    assert c4["audit"] == review.COMMUNICATION and "1.25" in c4["quote"]


def test_mechanical_signposts_and_repeated_numbers_are_flagged():
    sections = [
        {"title": "一", "paragraphs": [{"text": "这一节讲索引。加索引后查询从 1200 毫秒降到 40 毫秒，表有 300 万行。"}]},
        {"title": "二", "paragraphs": [{"text": "接下来看看为什么会变快。"},
                                      {"text": "再说一遍：1200 毫秒、40 毫秒、300 万行。"}]},
    ]
    issues = review.rules(sections, column=column(), target_minutes=6)
    e5 = [i for i in issues if i["rule"] == "E5"]
    assert [(i["section"], i["paragraph"]) for i in e5] == [(0, 0)]  # a natural transition is not flagged
    e6 = [i for i in issues if i["rule"] == "E6"]
    assert [(i["section"], i["paragraph"]) for i in e6] == [(1, 1)]


def test_review_phrases_are_reviewer_talk_not_everyday_words():
    sections = [{"title": "开场", "paragraphs": [{"text": "读取的折扣一次没拿到。这个数我们没拿到一手来源。"}]}]
    d5 = [i for i in review.rules(sections, column=column(), target_minutes=6) if i["rule"] == "D5"]
    assert [i["problem"] for i in d5] == ["审稿用语进了口播：「没拿到一手」"]


def test_legacy_versions_get_only_the_checks_that_need_no_plan():
    sections = [{"title": "开场", "paragraphs": [{"text": "缓存写入 5 分钟是 1.25 倍，1 小时是 2 倍。没有 A/B 测试。"}]}]
    issues = review.rules(sections, column=column(), target_minutes=6)
    assert "C4" in _rule_ids(issues)
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


def test_pipeline_passes_a_clean_script_in_five_calls(llm):
    adapted, _ = adapt()
    assert llm.steps() == ["facts", "plan", "draft", "newcomer", "audit"]
    checks = adapted.meta["checks"]
    assert checks["summary"]["passed"], checks["issues"]
    assert checks["rounds"] == 0 and checks["deletions"] == [] and checks["quiz"]["score"] == 1.0
    first = adapted.sections[0]["paragraphs"][0]
    assert first["citations"] == [PRICING] and first["evidence"] == "CITED"
    assert adapted.meta["style"]["presets"] == ["intermediate@1", "explainer@2"]
    assert adapted.meta["prompts"] == {"facts": 2, "plan": 2, "draft": 2, "review": 3}
    assert adapted.meta["fact_sheet_id"] == facts.sheet_id(REPORT)
    assert adapted.meta["description"]["sources"][0] == {"url": PRICING, "facts": ["F3", "F1", "F2"]}
    assert "批处理命中率在 30% 到 98% 之间" in adapted.meta["description"]["not_covered"]
    assert adapted.length_check["status"] == "OK"


def test_the_newcomer_gets_the_quiz_but_not_the_answers_or_facts(llm):
    adapt()
    newcomer = llm.request("newcomer")
    assert "问题1：为什么前缀要稳定" in newcomer
    assert "答案要点1" not in newcomer and "F1" not in newcomer and "1.25 倍" not in newcomer
    audit = llm.request("audit")
    assert "答案要点1" in audit and "观众的回答1" in audit
    assert all(REPORT[:20] not in msgs[1]["content"] for step, msgs, _ in llm.calls if step != "facts")


def test_a_wrong_quiz_answer_keeps_the_script_from_passing(llm):
    llm.queues["audit"] = [{**CLEAN, "grades": [{"id": "G1", "correct": True}, {"id": "G2", "correct": False,
                                                                                "note": "没听出来"},
                                                 {"id": "G3", "correct": True}]}]
    adapted, _ = adapt()
    checks = adapted.meta["checks"]
    assert checks["quiz"]["score"] == 0.67 and not checks["summary"]["passed"]
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
    assert llm.steps() == ["facts", "plan", "draft", "newcomer", "audit", "revise", "newcomer", "audit"]
    revise_request = llm.request("revise")
    assert revise_request.index("【严重】D2：说成了整个系列") < revise_request.index("读取是跟什么比")
    # Only the changed paragraphs were audited again.
    assert "[S2P1]" in llm.request("audit", 1) and "[S1P1]" not in llm.request("audit", 1)
    texts = [[p["text"] for p in s["paragraphs"]] for s in adapted.sections]
    assert texts[1][0] == fixed and texts[2][1] == fixed
    assert texts[3][2] == before["sections"][3]["paragraphs"][2]["text"]  # not a target: untouched
    checks = adapted.meta["checks"]
    assert checks["rounds"] == 1 and checks["summary"]["passed"]
    assert {(i["rule"], i["resolved"]) for i in checks["initial"] if i["severity"] != review.WARN} == {
        ("N1", True), ("D2", True)}


def test_minor_findings_are_shown_but_not_revised(llm):
    llm.queues["audit"] = [{**CLEAN, "issues": [finding("S2P2", "讲讲讲", severity="一般", rule="D3",
                                                          problem="1.28 次应为 1.26 次")]}]
    adapted, _ = adapt()
    assert "revise" not in llm.steps()
    minor = [i for i in adapted.meta["checks"]["issues"] if i["rule"] == "D3"]
    assert minor and minor[0]["severity"] == review.WARN and adapted.meta["checks"]["summary"]["passed"]


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


def test_an_action_that_only_leaves_out_a_condition_is_a_reminder_not_a_deletion(llm):
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
    assert checks["summary"]["passed"]  # a reminder never blocks the approval


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


def test_pipeline_gives_up_after_two_rounds_and_says_what_is_left(llm):
    stuck = {**GOOD_READ, "paragraphs": [{"id": "S1P2", "questions": [f"疑问{i}" for i in range(6)], "lost": []}]}
    llm.queues["newcomer"] = [stuck]
    llm.queues["revise"] = [{"paragraphs": [{"id": "S1P2", "text": sentences_of(120, "又")}]}]
    adapted, _ = adapt()
    assert llm.steps().count("revise") == pipeline.MAX_ROUNDS
    summary = adapted.meta["checks"]["summary"]
    assert not summary["passed"] and summary["questions"] == 6


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
    assert "C4" in {i["rule"] for i in legacy["checks"]["issues"]}  # legacy versions get rule checks too

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
    assert version["meta"]["prompts"]["review"] == 3
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
    assert {"C4", "E1"} <= _rule_ids(issues, review.WARN)
    assert not {"C4", "E1"} & _rule_ids(issues, review.MUST)
