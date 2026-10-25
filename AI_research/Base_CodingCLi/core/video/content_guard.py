"""Exclude validation samples and archived duplicate stories from publishing."""

SAMPLE_CREATORS = ("chain-validation", "prompt-quality-validation", "多矩阵质量验收")
MEMORY_DAYS = 30
MAIN_STORY_SHARE = 1.0
# All platform candidate queries use the same policy, including archived historical renders.
PUBLISHABLE_SQL = ("s.created_by NOT IN ('chain-validation','prompt-quality-validation','多矩阵质量验收') "
                   "AND COALESCE(json_extract(s.topic,'$.test_sample'),0)=0 "
                   "AND json_extract(s.topic,'$.content_archive') IS NULL")


def is_sample(script: dict) -> bool:
    return script.get("created_by") in SAMPLE_CREATORS or bool((script.get("topic") or {}).get("test_sample"))


def publishing_block(script: dict) -> str:
    if is_sample(script):
        return "这是验收样片，已与正式投稿隔离。"
    archived = (script.get("topic") or {}).get("content_archive")
    return f"这篇文稿已因内容重复归档：{archived.get('reason', '')}" if archived else ""
