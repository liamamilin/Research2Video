"""The agent's own reads are compacted only when they, not the task's material, outgrow the budget."""
from types import SimpleNamespace

from core.research import ResearchAgent


def agent(budget):
    return SimpleNamespace(config=SimpleNamespace(context_char_budget=budget))


def test_large_task_material_does_not_shrink_what_the_agent_reads():
    messages = [{"role": "user", "content": "材料" * 100000}] + \
               [{"role": "tool", "content": "page " * 1000} for _ in range(8)]
    ResearchAgent._compact_history(agent(150000), messages)
    assert all(len(m["content"]) == 5000 for m in messages if m["role"] == "tool")


def test_reads_beyond_the_budget_are_still_compacted_oldest_first():
    messages = [{"role": "tool", "content": "x" * 30000} for _ in range(10)]
    ResearchAgent._compact_history(agent(150000), messages)
    assert len(messages[0]["content"]) < 1000 and len(messages[-1]["content"]) == 30000
