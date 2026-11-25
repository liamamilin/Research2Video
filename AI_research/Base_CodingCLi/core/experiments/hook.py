"""The one write point (spec §2, §5, §7): asked once when an automatic script is created.

``arm_for`` returns the ``content_context`` to freeze: unchanged when nothing applies, otherwise with
``experiments`` recorded (both arms, for reconciliation) and, for arm B, the registered overrides written field by
field. The caller wraps it so that any error means "unchanged" (arm A).
"""
from __future__ import annotations

import copy
from pathlib import Path

from core.experiments import background
from core.experiments.definition import Experiment
from core.experiments.store import ExperimentStore

AUTOMATIC = "autopilot"


def _set(context: dict, path: str, value) -> None:
    """Write one dotted field without replacing the block around it (§6「覆盖只改那一个字段」)."""
    parts = path.split(".")
    node = context
    for part in parts[:-1]:
        child = node.get(part)
        node[part] = child = dict(child) if isinstance(child, dict) else {}
        node = child
    node[parts[-1]] = copy.deepcopy(value)


def stratum_column(context: dict, column_key: str) -> str:
    """The column used for blocks and strata: the content column, else the recipe key (§7)."""
    return context.get("column") or column_key or ""


def arm_for(state_dir: str | Path, context: dict, *, column_key: str, created_by: str) -> dict:
    if created_by != AUTOMATIC:
        return context
    store = ExperimentStore(state_dir)
    if not store.enabled():
        return context
    running = store.running()
    if not running:
        return context
    column = stratum_column(context, column_key)
    out = copy.deepcopy(context)
    marks = list(out.get("experiments") or [])
    for row in sorted(running, key=lambda r: r["started_at"] or r["created_at"]):
        experiment = Experiment.model_validate(row["definition"])
        if not experiment.scope.matches(out, column):
            continue
        if experiment.needs_column() and not out.get("column"):
            continue  # the switch cannot reach this script, so it takes no part (§7「范围」)
        token, arm = store.assign(experiment.id, column, experiment.block_size(), experiment.share_b,
                                  background.code_version())
        if arm == "B":
            for path, value in experiment.arms["B"].overrides.items():
                _set(out, path, value)
        marks.append({"id": experiment.id, "arm": arm, "token": token})
    if not marks:
        return context
    out["experiments"] = marks
    return out
