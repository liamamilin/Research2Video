"""Materials too large for the model are researched in item-boundary batches and merged, never cut."""
import json
import re

import pytest

from core import research_batches as rb
from core.errors import LLMError
from core.llm import LLMConfig
from core.research import ResearchAgent, ResearchConfig
from core.search import SearchConfig

HEADER = "## 矩阵信息采集\n本期范围说明。\n\n"


def item(n, size=100):
    return f"### [条目{n}](https://x.test/{n})\n材料编号：M{n}；" + "字" * size + "\n\n"


def block(n, size=100):
    return HEADER + "".join(item(i, size) for i in range(n))


class Reply:
    def __init__(self, content, finish_reason="stop"):
        self.content, self.finish_reason = content, finish_reason


def agent_with(monkeypatch, answer):
    agent = ResearchAgent(LLMConfig(api_key="x", model="m", base_url="http://x.test"), SearchConfig(), ResearchConfig())
    prompts = []

    def chat(messages, tools=None):
        prompts.append(messages[-1]["content"])
        return answer(messages[-1]["content"], len(prompts))

    monkeypatch.setattr(agent.llm, "chat", chat)
    return agent, prompts


def draft_for(prompt):
    ids = re.findall(r"材料编号：(M\d+)", prompt.split("## 分批研究稿")[0])
    return Reply("# 稿\n" + " ".join(ids) + f'\n<!-- material-use: {json.dumps({"selected": ids[:1], "background": ids[1:]})} -->')


def test_batches_split_between_items_and_repeat_the_header():
    batches = rb.pack(HEADER, rb.split(block(10))[1], len(HEADER) + 3 * len(item(0)))
    assert len(batches) == 4 and all(b.startswith(HEADER) for b in batches)
    assert "".join(b[len(HEADER):] for b in batches) == block(10)[len(HEADER):]  # every item, whole, in order


def test_a_single_material_larger_than_a_batch_is_an_error_not_a_cut():
    with pytest.raises(rb.OversizedItem, match="条目1"):
        rb.pack(HEADER, [item(0), item(1, 5000)], 2000)


def test_the_merged_receipt_keeps_every_batchs_selection():
    drafts = ['a <!-- material-use: {"selected":["M1"],"background":["M2"],"deferred":["M3"]} -->',
              'b <!-- material-use: {"selected":["M2"],"background":["M4"],"deferred":["M1"]} -->']
    receipt = json.loads(re.search(r"\{.*\}", rb.joined_receipt(drafts)).group(0))
    assert receipt == {"selected": ["M1", "M2"], "background": ["M4"], "deferred": ["M3"]}


def test_oversized_sources_are_researched_in_batches_then_merged(monkeypatch):
    monkeypatch.setattr(rb, "MAX_PROMPT_CHARS", 1500)
    monkeypatch.setattr(rb, "BATCH_CHARS", 1500)
    agent, prompts = agent_with(monkeypatch, lambda prompt, n: (
        Reply("# 合并底稿\n<!-- material-use: {\"selected\":[\"bogus\"]} -->") if "## 分批研究稿" in prompt else draft_for(prompt)))
    out = agent.run_fetch_only("任务说明", sources=block(20))
    *batch_prompts, merge = prompts
    assert agent.batches == len(batch_prompts) > 1 and "## 分批研究稿" in merge
    assert all(p.startswith("任务说明") and HEADER in p and len(p) <= 1500 + 60 for p in batch_prompts)
    seen = [m for p in batch_prompts for m in re.findall(r"材料编号：(M\d+)", p)]
    assert seen == [f"M{i}" for i in range(20)]  # each material in exactly one batch
    assert "<!-- material-use" not in merge.split("## 分批研究稿")[1]  # drafts go in without their receipts
    receipt = json.loads(re.search(r"material-use: (\{.*\})", out).group(1))
    assert "bogus" not in receipt["selected"] and len(receipt["selected"]) == len(batch_prompts)
    assert {"https://x.test/0", "https://x.test/19"} <= agent.retrieved_urls


def test_the_providers_context_refusal_resizes_into_batches(monkeypatch):
    def answer(prompt, n):
        if n == 1:
            raise LLMError("Error code: 400 - This model's maximum context length is 1000 tokens; the prompt has 1800 tokens")
        return Reply("# 合并") if "## 分批研究稿" in prompt else draft_for(prompt)
    agent, prompts = agent_with(monkeypatch, answer)
    agent.run_fetch_only("任务说明", sources=block(30))
    assert agent.batches >= 3 and "## 分批研究稿" in prompts[-1]


def test_any_other_model_failure_still_stops_the_run(monkeypatch):
    def answer(prompt, n):
        raise LLMError("Error code: 402 - insufficient balance")
    agent, _ = agent_with(monkeypatch, answer)
    with pytest.raises(LLMError, match="402"):
        agent.run_fetch_only("任务说明", sources=block(30))


def test_a_cut_off_batch_draft_fails_loudly(monkeypatch):
    monkeypatch.setattr(rb, "MAX_PROMPT_CHARS", 1500)
    monkeypatch.setattr(rb, "BATCH_CHARS", 1500)
    agent, _ = agent_with(monkeypatch, lambda prompt, n: Reply("# 半截", "length"))
    with pytest.raises(LLMError, match="截断"):
        agent.run_fetch_only("任务说明", sources=block(20))


def test_material_that_fits_is_still_one_call(monkeypatch):
    agent, prompts = agent_with(monkeypatch, lambda prompt, n: Reply("# 一次写完"))
    assert agent.run_fetch_only("任务说明", sources=block(5)) == "# 一次写完" and len(prompts) == 1
    assert prompts[0] == "任务说明\n\n" + block(5)


def test_dossier_expansion_leaves_out_sources_that_no_longer_fit(monkeypatch):
    monkeypatch.setattr(rb, "MAX_PROMPT_CHARS", 1500)
    agent, prompts = agent_with(monkeypatch, lambda prompt, n: Reply("# 完整研究底稿 " + "内容" * 50))
    agent.expand_dossier("任务说明", "初始底稿", "原文", sources=block(20))
    agent.expand_dossier("任务说明", "初始底稿", "原文", sources=block(2))
    assert "条目0" not in prompts[0] and "条目0" in prompts[1]


def test_a_batch_cut_off_by_the_output_limit_is_redone_as_two_smaller_ones(monkeypatch):
    monkeypatch.setattr(rb, "MAX_PROMPT_CHARS", 1500)
    monkeypatch.setattr(rb, "BATCH_CHARS", 1500)

    def answer(prompt, n):
        if "## 分批研究稿" in prompt:
            return Reply("# 合并")
        ids = re.findall(r"材料编号：(M\d+)", prompt)
        return Reply("# 太长", "length") if len(ids) > 3 else draft_for(prompt)

    agent, prompts = agent_with(monkeypatch, answer)
    out = agent.run_fetch_only("任务说明", sources=block(20))
    kept = json.loads(re.search(r"material-use: (\{.*\})", out).group(1))
    assert sorted(kept["selected"] + kept["background"], key=lambda m: int(m[1:])) == [f"M{i}" for i in range(20)]
    assert agent.batches >= 7  # every finished batch holds at most the three materials the fake model can fit


def test_a_single_report_cut_off_by_the_output_limit_is_written_in_batches_instead(monkeypatch):
    def answer(prompt, n):
        if n == 1:
            return Reply("# 半截报告", "length")
        return Reply("# 合并") if "## 分批研究稿" in prompt else draft_for(prompt)
    agent, prompts = agent_with(monkeypatch, answer)
    out = agent.run_fetch_only("任务说明", sources=block(10))
    assert "半截" not in out and agent.batches >= 2 and "## 分批研究稿" in prompts[-1]
