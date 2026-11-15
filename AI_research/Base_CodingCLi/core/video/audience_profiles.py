"""Shared editorial instructions; configuration lives in config/content_strategy.yaml."""
from __future__ import annotations

from pathlib import Path

from core.yaml_cache import load_yaml

_PROFILE = load_yaml((Path(__file__).resolve().parents[2] / 'config/content_strategy.yaml').read_text())
EDITORIAL_GUIDANCE = _PROFILE['editorial_guidance']
CHANNELS = _PROFILE['channels']
JOBS = _PROFILE['jobs']
PRODUCTIONS = _PROFILE['productions']
SINGLE = frozenset(_PROFILE['single_question'])
KNOWLEDGE = frozenset(_PROFILE['knowledge'])
QUESTION_MATERIAL_DEFAULT = _PROFILE.get('question_material', {}).get('enabled') is True


def candidate_limit(channel: str) -> int | None:
    if channel in SINGLE or channel in KNOWLEDGE:
        return 1
    return _PROFILE['candidate_limits'].get(channel, _PROFILE['candidate_limits']['default'])


def selection_context(channel: str) -> str:
    context = CHANNELS[channel]
    if channel in SINGLE:
        context += "本栏围绕一个主问题展开：保持原研究底稿的主问题，只整理这一问题的材料，不拆成互不相关的快讯。"
    limit = candidate_limit(channel)
    count = f"本入口至多{limit}个候选。" if limit is not None else "候选数量由本期材料决定，保留有独立价值的变化，不凑题、不人为截为几条。"
    return context + count + EDITORIAL_GUIDANCE
