"""Small read projections for list pages; detail endpoints retain complete records."""
from __future__ import annotations


def publication(row: dict) -> dict:
    # In particular, exclude publish_pack/description snapshots and attempt logs.
    return {key: value for key, value in row.items() if key in {
        "id", "script_id", "version", "render_id", "platform", "url", "published_on",
        "title", "note", "recorded_by", "account_uid", "account_name", "work_id",
        "confirmed_at", "removed_at",
    }}


def compact(row: dict) -> dict:
    result = dict(row)
    if "topic" in row:
        result["topic"] = {k: v for k, v in row["topic"].items()
                           if k in {"id", "title", "article", "local_video", "content_mode"}}
        if isinstance(result["topic"].get("article"), dict):
            result["topic"]["article"] = {k: v for k, v in result["topic"]["article"].items()
                                          if k in {"mode", "kind"}}
        if isinstance(result["topic"].get("local_video"), dict):
            result["topic"]["local_video"] = {k: v for k, v in result["topic"]["local_video"].items()
                                              if k in {"file_name", "duration", "width", "height", "size"}}
    if row.get("content_context"):
        result["content_context"] = {k: v for k, v in row["content_context"].items()
                                     if k in {"column", "column_name", "matrix", "source_matrix", "type", "content_mode"}}
    if row.get("length_check"):
        result["length_check"] = {**{k: v for k, v in row["length_check"].items() if k in {
            "spoken_length", "estimated_seconds", "target_minutes", "budget", "ratio", "status", "dropped_citations",
        }}, "sections": []}
    if "publications" in row:
        result["publications"] = [publication(p) for p in row["publications"]]
    if "distributions" in row:
        result["distributions"] = [{k: v for k, v in d.items() if k in {
            "platform", "status", "account_uid", "connected", "current_account", "blocked_reason",
        }} for d in row["distributions"]]
    return result
