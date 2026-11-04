"""Experiment records, duplicate warnings and the user's adoption choice (spec §9a steps ⑤ ⑥, §10).

The module never writes a config: ``decide`` only records the choice and the values the suggestion would replace.
"""
from __future__ import annotations

import json

from core.experiments.definition import Experiment
from core.experiments.store import ExperimentStore

CHOICES = {"adopt": "采用 B", "keep": "保留 A", "defer": "暂不处理"}


def _signature(definition: dict) -> str:
    arm = (definition.get("arms") or {}).get("B") or {}
    return json.dumps({"kind": definition.get("kind"), "overrides": arm.get("overrides") or {}}, sort_keys=True,
                      ensure_ascii=False)


def duplicates(store: ExperimentStore, experiment: Experiment) -> list[dict]:
    """Earlier experiments that changed the same switches to the same values, so the user sees what was learnt."""
    mine = _signature(experiment.model_dump())
    out = []
    for row in store.list():
        if row["id"] == experiment.id or _signature(row["definition"]) != mine:
            continue
        result = store.result(row["id"]) or {}
        out.append({"id": row["id"], "name": row["definition"].get("name") or row["id"], "status": row["status"],
                    "verdict": (result.get("verdict") or {}).get("verdict")})
    return out


def listing(store: ExperimentStore) -> list[dict]:
    """Every ended or decided experiment: what it changed, the conclusion, the detectable effect and the choice."""
    out = []
    for row in store.list():
        if row["status"] not in ("ended", "decided"):
            continue
        result = store.result(row["id"]) or {}
        decided = next((e for e in reversed(store.events(row["id"])) if e["kind"] in ("decide", "decided")), None)
        primary = result.get("primary") or {}
        definition = row["definition"]
        out.append({
            "id": row["id"], "name": definition.get("name") or row["id"], "kind": definition.get("kind"),
            "hypothesis": definition.get("hypothesis") or "", "status": row["status"],
            "changes": (definition.get("arms") or {}).get("B", {}).get("overrides") or {},
            "metric": primary.get("metric") or (definition.get("metric") or {}).get("primary"),
            "verdict": (result.get("verdict") or {}).get("verdict") if result else None,
            "estimate": primary.get("estimate"), "low": primary.get("low"), "high": primary.get("high"),
            "minimum_detectable": (result.get("verdict") or {}).get("minimum_detectable"),
            "n": result.get("n"), "decision": (decided or {}).get("detail"),
        })
    return out


def decide(store: ExperimentStore, experiment: Experiment, choice: str, by: str, config_dir: str, note: str = "") -> None:
    """Record the choice. "adopt" and "keep" close the experiment (decided); "defer" leaves it ended."""
    if choice not in CHOICES:
        raise ValueError("未知选择")
    found = store.get(experiment.id)
    if not found or found["status"] not in ("ended", "decided"):
        raise ValueError("实验还没结束，不能处理")
    if not store.result(experiment.id):
        raise ValueError("结论还没存档（收尾期没结束）")
    before = {}
    if choice == "adopt":
        from core.experiments import adoption
        from core.video import content_columns
        columns = {k: {"name": c.name, "matrix": c.matrix, "notes": c.notes, "presentation": c.presentation,
                       "render": c.render} for k, c in content_columns.load_all(config_dir).items()}
        used = {a["column_key"] for a in store.assignments(experiment.id) if a["arm"] == "B"}
        before = {f"{s['column'] or '-'}:{s['path']}": {"from": s.get("from"), "to": s["to"]}
                  for s in adoption.suggestions(experiment, columns, used)}
    detail = {"choice": choice, "label": CHOICES[choice], "before": before, "note": note[:500]}
    if choice == "defer" or found["status"] == "decided":
        with store.connect(write=True) as db:
            store._event(db, experiment.id, "decided", by, detail)
        return
    store.move(experiment.id, "decide", by, detail)  # records the "decide" event with the same detail
