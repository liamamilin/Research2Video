"""Information sources: the registry and how each source has been doing (web/services/source_catalog.py)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from core import sources
from web.deps import require_viewer
from web.models import ApiError
from web.services import source_catalog
from web.settings import get_settings

router = APIRouter(prefix="/api/sources", tags=["sources"])


@router.get("")
def list_sources(user=Depends(require_viewer)) -> dict:
    """Every registered source with the Jobs that use it and its last fetch; Jobs that name a source the registry lacks."""
    paths = get_settings().paths
    try:
        return source_catalog.catalog(paths.config_dir, paths.jobs_dir, paths.state_dir)
    except sources.RegistryError as exc:
        raise HTTPException(500, detail=ApiError.make("bad_registry", str(exc))) from exc
