"""产出: every script by where it came from, matrix › Job › the run's report (spec/outputs.md)."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from core.video import outputs
from web.deps import require_viewer
from web.settings import get_settings

router = APIRouter(prefix="/api/outputs", tags=["outputs"])


@router.get("")
def list_outputs(archived: bool = False, user=Depends(require_viewer)) -> dict:
    from web.services import read_cache

    paths = get_settings().paths
    key = (archived, paths.config_dir, paths.jobs_dir, read_cache.state_revision(paths.state_dir))
    return read_cache.cached(
        "outputs", key, 15.0,
        lambda: outputs.collect(paths.config_dir, paths.jobs_dir, archived=archived),
    )
