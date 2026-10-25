"""Local audience-copy boundary for every form and narration input path.

Research has its own writer/audit checks. AutoVideo is independently usable, so
its public handoff must also reject internal research notes without rewriting
an authored script or treating a missing summary as a missing source fact.
"""
from __future__ import annotations

import re

CODE = "AUDIENCE_COPY_INTERNAL_NOTE"
GUIDANCE = '根据素材，产出目标观众愿意看、听得懂、想继续看的视频。'

INTERNAL_GAP = re.compile(
    r"(?<![\u4e00-\u9fffA-Za-z0-9])(?:材料|这份材料|现有材料|现有资料|所给资料|这些资料|研究摘要|内部报告|本次摘要|资料|摘要)"
    r"(?:里|中)?(?:也|还|并)?(?:(?:没有|未|没)(?:提供|给出|说明|提及|列出|收录|写明|交代|讲明|讲清|讲|给|说)"
    r"|(?:没有|未|没)(?:相关|具体|量化|日常)?(?:测试)?(?:数据|价格|售价|结论|出处)"
    r"|缺少(?:相关|具体|测试)?(?:数据|证据|来源|结论))[^。！？；\n]*"
    r"|缺少缺点结论"
)


def problem(text: str) -> str:
    return ""


def require_copy(pipeline, text: str) -> None:
    message = problem(text)
    if message:
        raise pipeline.G2Fault(CODE, message)
