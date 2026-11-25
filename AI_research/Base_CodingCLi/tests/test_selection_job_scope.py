"""The selection refinement must retain the research Job's own topic boundaries."""
import pytest

from core.video import audience_strategy as strategy
from tests.test_audience_rollout import chosen, MATERIAL


@pytest.mark.parametrize("job_name,scope", [
    ("chatgpt_acquisition/t09_updates", "只研究 ChatGPT 用户可用的变化；其他产品更新只能作比较背景"),
    ("chatgpt_acquisition/t05_comparison", "比较至少两家公司的 AI 产品，不限定为 ChatGPT"),
    ("ai_media/ai_tool_tutorials", "必须有具体输入、操作步骤和输出，专业教程保留前置条件"),
    ("science/science_weekly", "解释实验现象和机制，不改写为购买软件建议"),
])
def test_both_selection_passes_receive_job_scope_without_system_configuration(monkeypatch, job_name, scope):
    requests = []
    class Client:
        usage = {}
        def __init__(self, *args, **kwargs): pass
        def close(self): pass

    def model(client, system, user, *args, **kwargs):
        requests.append((system, user))
        return chosen()

    monkeypatch.setattr("core.llm.LLMClient", Client)
    monkeypatch.setattr(strategy, "call_json", model)
    job = {"_file": job_name, "content_strategy": strategy.MARKER,
           "name": "原栏目名称", "description": "原栏目要回答的问题", "keywords": ["指定对象"],
           "prompt": scope, "unrelated_setting": "must-not-be-forwarded"}
    report, detail = strategy.prepare_report(job, "# 已有研究\n原研究底稿", MATERIAL, object())
    assert len(requests) == 2
    for system, user in requests:
        assert scope in user
        assert "原栏目名称" in user and "原栏目要回答的问题" in user and "指定对象" in user
        assert "must-not-be-forwarded" not in user
    assert detail.get("status") != "kept_original"
    assert detail["candidate_count"] == 1
    assert "原栏目名称" not in report  # instructions are context, not manuscript material

