"""Usage and timing: tokens, models, stage durations, an optional dollar conversion. Administrators only."""
from __future__ import annotations

import threading
import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse

from core import telemetry
from core.telemetry import ingest, pricing, query, retention
from web import audit
from web.models import ApiError
from web.deps import require_admin
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
            matrix: str = "", column_key: str = "", form: str = "", user=Depends(require_admin)):
    syncing = _ensure_synced()
    start, end = _window(days, since, until)
    return {**query.summary(str(get_settings().paths.config_dir), start, end, _filters(matrix, column_key, form)),
            "syncing": syncing}


@router.get("/scripts")
def scripts(days: int = Query(30, ge=0, le=3650), since: Optional[float] = None, until: Optional[float] = None,
            matrix: str = "", column_key: str = "", form: str = "", limit: int = Query(50, ge=1, le=200),
            offset: int = Query(0, ge=0), user=Depends(require_admin)):
    syncing = _ensure_synced()
    start, end = _window(days, since, until)
    return {**query.scripts(str(get_settings().paths.config_dir), start, end, _filters(matrix, column_key, form),
                            limit, offset), "syncing": syncing}


@router.get("/compare")
def compare(by: str = Query("column_key", pattern="^(column_key|form|matrix|recipe|path)$"),
            days: int = Query(30, ge=0, le=3650), since: Optional[float] = None, until: Optional[float] = None,
            matrix: str = "", column_key: str = "", form: str = "", user=Depends(require_admin)):
    start, end = _window(days, since, until)
    return query.compare(str(get_settings().paths.config_dir), by, start, end, _filters(matrix, column_key, form))


@router.get("/performance")
def performance(by: str = Query("column_key", pattern="^(column_key|form|matrix|recipe|path)$"),
                days: int = Query(30, ge=0, le=3650), user=Depends(require_admin)):
    """What published videos cost against their plays on Bilibili, per video and grouped by ``by``."""
    from web.services import telemetry_performance
    _ensure_synced()
    return telemetry_performance.load(str(get_settings().paths.config_dir), by, days)


@router.get("/weekly-review")
def weekly(start: Optional[float] = None, stage: str = Query("", pattern="^(|24h|7d)$"), user=Depends(require_admin)):
    """One week's published videos by their 24-hour plays, cut by form, matrix, column, title and timing: computed
    now, or as saved once its figures were final (``stage``)."""
    from web.services import weekly_review
    paths = get_settings().paths
    return weekly_review.load(str(paths.config_dir), str(paths.state_dir), start, stage)


@router.get("/weekly-review/report", response_class=PlainTextResponse)
def weekly_report(start: float, stage: str = Query(..., pattern="^(24h|7d)$"), user=Depends(require_admin)):
    """A saved week's report as Markdown, for download."""
    from core.weekly_review_store import ReviewStore
    from pathlib import Path
    path = Path(ReviewStore(str(get_settings().paths.state_dir)).report_path(start, stage))
    if not path.is_file():
        raise HTTPException(404, "这一周还没有存档")
    return PlainTextResponse(path.read_text(encoding="utf-8"), media_type="text/markdown; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="weekly-review-{path.stem}.md"'})


@router.get("/assistant")
def assistant_usage(days: int = Query(30, ge=0, le=3650), since: Optional[float] = None, until: Optional[float] = None,
                    limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0), user=Depends(require_admin)):
    """The assistant request by request: turns, tokens, cost, which tools it called and how large their answers were."""
    from web.services import telemetry_assistant
    syncing = _ensure_synced()
    start, end = _window(days, since, until)
    settings = get_settings()
    return {**telemetry_assistant.report(str(settings.paths.config_dir), str(settings.paths.state_dir), start, end, limit, offset),
            "syncing": syncing}


@router.get("/assistant/requests/{request_id}")
def assistant_request(request_id: str, user=Depends(require_admin)):
    from core.telemetry import assistant
    return assistant.request_detail(str(get_settings().paths.config_dir), request_id)


@router.get("/scripts/{script_id}")
def script_detail(script_id: str, user=Depends(require_admin)):
    return query.script_detail(str(get_settings().paths.config_dir), script_id)


@router.get("/pricing")
def price_table(user=Depends(require_admin)):
    return {"prices": [p.public() for p in pricing.load(str(get_settings().paths.config_dir))]}


@router.get("/storage")
def storage(user=Depends(require_admin)):
    return retention.stats()


@router.post("/purge")
def purge(payload: dict, request: Request, user=Depends(require_admin)):
    """Delete records older than ``older_than_days``; ``dry_run`` counts them without deleting."""
    days, dry_run = payload.get("older_than_days"), bool(payload.get("dry_run"))
    if isinstance(days, bool) or not isinstance(days, (int, float)) or days < retention.MIN_DAYS:
        raise HTTPException(status_code=422, detail=ApiError.make(
            "invalid_days", f"至少保留 {retention.MIN_DAYS} 天的记录，older_than_days 必须是不小于它的数字"))
    result = retention.purge(days, dry_run=dry_run)
    if not dry_run:
        audit.log("telemetry_purge", user=user["username"], result="success",
                  details={"older_than_days": days, "calls": result["calls"], "spans": result["spans"]},
                  ip=request.client.host if request.client else None)
    return result
