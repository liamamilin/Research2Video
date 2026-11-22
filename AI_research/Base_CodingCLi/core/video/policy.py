"""Shared priorities for research, drafting and review; recipes supply editorial targets."""

SOURCE_GUIDANCE = '根据素材，产出目标观众愿意看、听得懂、想继续看的视频。'

EDITORIAL_GUIDANCE = '根据素材，产出目标观众愿意看、听得懂、想继续看的视频。'

TITLE_GUIDANCE = (
    "标题必须严格基于最终口播正文，绝不编造正文没有的数字、结论或因果。"
    "优先把知名产品/模型/具体对象或熟悉场景放在前 12 字，带入正文里真实的关键数字（价格、比例、额度、日期、倍数）与明确反差或后果；"
    "避免写成菜单点击说明书（如“先点某按钮再选某项”），避免缺少主语或没有信息量的空泛疑问句。"
)

QUALITY_GUIDANCE = '根据素材，产出目标观众愿意看、听得懂、想继续看的视频。'

def is_core_issue(item: dict) -> bool:
    """Repair confirmed facts, source errors and concrete process leaks; keep heuristics advisory."""
    return (item.get("rule") == "D5" and item.get("source") == "audience_copy") or (
            item.get("audit") == "fact" and item.get("rule") != "B3"
            and (item.get("source") == "model" or item.get("severity") in ("serious", "must")))
