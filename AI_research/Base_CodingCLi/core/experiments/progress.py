"""「实验进展」for the weekly review (spec §9): experiments running in a week or ended in it. Read-only."""
from __future__ import annotations

from pathlib import Path

from core.experiments.store import ExperimentStore

VERDICTS = {"running": "进行中", "srm": "分组失衡，结论不可信", "b_better": "B 更好", "b_worse": "B 更差",
            "no_difference": "没有分出差异"}


def week(state_dir: str | Path, start: float, end: float) -> list[dict]:
    if not (Path(state_dir) / "experiments.db").is_file():
        return []
    store = ExperimentStore(state_dir)
    out = []
    for row in store.list():
        began = row["started_at"]
        if not began or began >= end:
            continue
        events = store.events(row["id"])
        ended = next((e["at"] for e in events if e["kind"] == "end"), None)
        if ended is not None and ended < start:
            continue
        arms = store.assignments(row["id"])
        result = store.result(row["id"])
        out.append({"id": row["id"], "name": row["definition"].get("name") or row["id"], "status": row["status"],
                    "A": sum(1 for a in arms if a["arm"] == "A"), "B": sum(1 for a in arms if a["arm"] == "B"),
                    "ended_this_week": ended is not None and start <= ended < end,
                    "verdict": VERDICTS.get(((result or {}).get("verdict") or {}).get("verdict", "running"), "进行中")})
    return out
