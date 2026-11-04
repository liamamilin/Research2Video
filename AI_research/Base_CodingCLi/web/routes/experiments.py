"""「A/B 测试」tab, phase 1: read-only. Administrators only (spec §9)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from core.experiments import registry
from core.experiments.store import ExperimentStore
from web.deps import require_admin
from web.settings import get_settings

router = APIRouter(prefix="/api/experiments", tags=["experiments"])


def _store() -> ExperimentStore:
    return ExperimentStore(get_settings().paths.state_dir)


@router.get("")
def overview(user=Depends(require_admin)) -> dict:
    store = _store()
    return {"enabled": store.enabled(), "experiments": store.list(), "registry": registry.as_rows()}


@router.get("/{experiment_id}")
def detail(experiment_id: str, user=Depends(require_admin)) -> dict:
    store = _store()
    found = store.get(experiment_id)
    if not found:
        raise HTTPException(404, "没有这个实验")
    assignments = store.assignments(experiment_id)
    return {**found, "events": store.events(experiment_id),
            "arms": {arm: sum(1 for a in assignments if a["arm"] == arm) for arm in ("A", "B")}}
