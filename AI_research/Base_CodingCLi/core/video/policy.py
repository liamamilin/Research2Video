"""Shared priorities for research, drafting and review; recipes supply editorial targets."""

SOURCE_GUIDANCE = '根据素材，产出目标观众愿意看、听得懂、想继续看的视频。'

EDITORIAL_GUIDANCE = '根据素材，产出目标观众愿意看、听得懂、想继续看的视频。'

TITLE_GUIDANCE = '根据素材，产出目标观众愿意看、听得懂、想继续看的视频。'

QUALITY_GUIDANCE = '根据素材，产出目标观众愿意看、听得懂、想继续看的视频。'

def is_core_issue(item: dict) -> bool:
    """Repair confirmed facts, source errors and concrete process leaks; keep heuristics advisory."""
    return (item.get("rule") == "D5" and item.get("source") == "audience_copy") or (
            item.get("audit") == "fact" and item.get("rule") != "B3"
            and (item.get("source") == "model" or item.get("severity") in ("serious", "must")))
