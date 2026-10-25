"""产出: every script by where it came from, matrix › Job › the run's report (spec/outputs.md)."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from core.video import outputs
from web.deps import require_viewer
from web.settings import get_settings

router = APIRouter(prefix="/api/outputs", tags=["outputs"])


@router.get("")
def list_outputs(archived: bool = False, user=Depends(require_viewer)) -> dict:
    paths = get_settings().paths
    return outputs.collect(paths.config_dir, paths.jobs_dir, archived=archived)
