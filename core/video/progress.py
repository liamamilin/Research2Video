"""Current-version progress shared by list pages; older attempts remain history."""
from __future__ import annotations

from . import store


def is_published(script: dict, versions: set[int | None]) -> bool:
    """An unversioned legacy record can only identify an approved first version."""
    return script["current_version"] in versions or (
        None in versions and script["current_version"] == 1 and script["status"] == "APPROVED" and len(versions) == 1)


def render_state(script: dict, records: list[dict]) -> dict:
    """Prefer active work, then an existing completed video, over a failed retry."""
    current = [r for r in records if r["version"] == script["current_version"]]
    active = next((r for r in current if r["status"] in store.ACTIVE_RENDER_STATUSES), None)
    completed = next((r for r in current if r["status"] == "COMPLETED"), None)
    return {"render": active or completed or next(iter(current), None), "has_video": completed is not None,
            "has_previous_video": any(r["version"] != script["current_version"] and r["status"] == "COMPLETED" for r in records)}
