"""Audit log and global log routes."""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException, Query, status

from web import audit as audit_module
from web.deps import require_admin, require_viewer
from web.models import ApiError
from core.fileio import tail_lines

router = APIRouter(tags=["logs"])


@router.get("/api/audit")
def get_audit_log(
    limit: int = Query(100, ge=1, le=1000),
    user=Depends(require_admin),
):
    """Return the most recent audit log entries."""
    entries = audit_module.tail(limit=limit)
    return {"entries": entries, "total": len(entries)}


@router.get("/api/logs/global")
def get_global_logs(
    lines: int = Query(200, ge=10, le=5000),
    user=Depends(require_viewer),
):
    """Return the tail of ai_research.log."""
    from web.settings import get_settings
    settings = get_settings()
    log_path = os.path.join(settings.paths.logs_dir, "ai_research.log")

    if not os.path.isfile(log_path):
        return {"lines": [], "total": 0}

    try:
        recent = tail_lines(log_path, lines)
    except OSError:
        return {"lines": [], "total": 0}

    return {
        "lines": [l.rstrip("\n") for l in recent],
        "total": len(recent),
        "path": log_path,
    }
