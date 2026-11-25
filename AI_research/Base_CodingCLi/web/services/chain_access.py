"""Small rollout switch; normal viewer/editor/admin permissions still apply."""

from pathlib import Path
import re

import yaml
from fastapi import HTTPException
from web.settings import get_settings


def snapshot(key: str | None = None) -> dict:
    path = Path(get_settings().paths.config_dir) / "chain_access.yaml"
    raw = yaml.safe_load(path.read_text()) if path.exists() else {}
    raw = raw or {}
    mode = raw.get("mode", "read_only")
    allowed = raw.get("writable_chains", [])
    created_here = False
    if key and re.fullmatch(r"[a-z][a-z0-9_-]{1,59}", key) and raw.get("allow_create"):
        metadata = Path(get_settings().paths.config_dir) / "chains" / (key + ".yaml")
        if metadata.is_file():
            value = yaml.safe_load(metadata.read_text()) or {}
            created_here = not value.get("imported", True) and not value.get("archived")
    return {
        "implementation_version": "20261118-chain-v1",
        "mode": mode,
        "writable_chains": allowed,
        "can_create": mode == "enabled" and bool(raw.get("allow_create", False)),
        "can_write": mode == "enabled"
        and (allowed == ["*"] or key in allowed or created_here),
    }


def require_write(key: str | None = None, create: bool = False) -> None:
    access = snapshot(key)
    if not access["can_create" if create else "can_write"]:
        raise HTTPException(
            409, "链路管理当前为只读或该链路尚未开放操作；现有任务继续执行"
        )
