"""Read-only usage and timing: tokens, models, stage durations, and an optional dollar conversion."""
from __future__ import annotations

import threading
import time
from typing import Optional

from fastapi import APIRouter, Depends, Query

from core import telemetry
from core.telemetry import ingest, pricing, query
from web.deps import require_viewer
from web.settings import get_settings

router = APIRouter(prefix="/api/telemetry", tags=["telemetry"])

SYNC_EVERY = 60.0
_sync = {"at": 0.0, "running": False}
_sync_lock = threading.Lock()


def _read_new_records() -> None:
    settings = get_settings()
    try:
        ingest.run_all(str(settings.paths.state_dir) + "/history")
    finally:
        _sync["running"] = False


def _ensure_synced() -> bool:
    """Start reading finished renders and old logs in the background; True while that is running."""
    with _sync_lock:
        if not _sync["running"] and time.time() - _sync["at"] > SYNC_EVERY:
            _sync.update(at=time.time(), running=True)
            threading.Thread(target=_read_new_records, name="telemetry-ingest", daemon=True).start()
        return _sync["running"]


def _window(days: int, since: Optional[float], until: Optional[float]) -> tuple[Optional[float], Optional[float]]:
    if since is None and days > 0:
        since = time.time() - days * 86400
    return since, until


def _filters(matrix: str, column_key: str, form: str) -> dict:
    return {"matrix": matrix, "column_key": column_key, "form": form}


@router.get("/summary")
def summary(days: int = Query(30, ge=0, le=3650), since: Optional[float] = None, until: Optional[float] = None,
            matrix: str = "", column_key: str = "", form: str = "", user=Depends(require_viewer)):
    syncing = _ensure_synced()
    start, end = _window(days, since, until)
    return {**query.summary(str(get_settings().paths.config_dir), start, end, _filters(matrix, column_key, form)),
            "syncing": syncing}


@router.get("/scripts")
def scripts(days: int = Query(30, ge=0, le=3650), since: Optional[float] = None, until: Optional[float] = None,
            matrix: str = "", column_key: str = "", form: str = "", limit: int = Query(50, ge=1, le=200),
            offset: int = Query(0, ge=0), user=Depends(require_viewer)):
    syncing = _ensure_synced()
    start, end = _window(days, since, until)
    return {**query.scripts(str(get_settings().paths.config_dir), start, end, _filters(matrix, column_key, form),
                            limit, offset), "syncing": syncing}


@router.get("/scripts/{script_id}")
def script_detail(script_id: str, user=Depends(require_viewer)):
    return query.script_detail(str(get_settings().paths.config_dir), script_id)


@router.get("/pricing")
def price_table(user=Depends(require_viewer)):
    return {"prices": [p.public() for p in pricing.load(str(get_settings().paths.config_dir))]}
