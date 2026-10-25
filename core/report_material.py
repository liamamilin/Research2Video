"""Keep the retrieved material beside its report for downstream authors.

The human research report and the original material are separate artifacts.
Content-addressed snapshots survive reruns; old reports without a snapshot
continue to work as before.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from core.fileio import atomic_write


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def save(report_path: str, report: str, parts: list[str]) -> dict:
    """Save the actual retrieved text, without another model transformation."""
    material = "\n\n---\n\n".join(dict.fromkeys(part.strip() for part in parts if part.strip()))
    if not material:
        return {}
    report_path = Path(report_path).resolve()
    sha = digest(material)
    path = report_path.with_name(f"{report_path.stem}.materials-{sha[:16]}.json")
    value = {"version": 1, "report_sha256": digest(report), "material": material}
    atomic_write(str(path), json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    return {"path": str(path), "sha256": sha, "report_sha256": digest(report)}


def read(reference: dict, report_path: str, report: str | None = None) -> str:
    """Open an intact local snapshot; stale or absent snapshots fall back safely."""
    if not reference or not report_path:
        return ""
    path = Path(str(reference.get("path") or "")).resolve()
    parent = Path(report_path).resolve().parent
    if path.parent != parent or not path.name.endswith(".json"):
        return ""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        material = value["material"]
        if not isinstance(material, str) or digest(material) != reference.get("sha256"):
            return ""
        if report is not None and digest(report) != reference.get("report_sha256"):
            return ""
        return material
    except (OSError, ValueError, KeyError, TypeError):
        return ""
