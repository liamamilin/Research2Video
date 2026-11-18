"""Keep the retrieved material beside its report for downstream authors.

The human research report and the original material are separate artifacts.
Content-addressed snapshots survive reruns; old reports without a snapshot
continue to work as before.
"""
from __future__ import annotations

import hashlib
import json
import re
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



# The collector's status blocks are operational metadata, not story material.
# Saved reports may echo these labels as Markdown headings or inline bold labels.
_COLLECTION_STATUS = re.compile(
    r"^(?:#{1,6}[ \t]+)?(?:\*\*)?"
    r"(本次未取得内容的来源|采集范围说明)(?:\*\*)?"
    r"(?:[ \t]*[：:][ \t]*(?:\*\*)?(.*))?$"
)
# Older research reports can append a shot note to an otherwise useful line.
# Only the explicit report label is special; source articles may use it normally.
_SHOT_NOTE = re.compile(r"(?:^[ \t]*(?:[-*+] )?|(?<=[。！？])[ \t]*)\*\*画面\*\*[ \t]*[：:]")


def for_narration(text: str, *, research_report: bool = False) -> str:
    """Keep research content and leave operational notes in the saved artifacts."""
    lines = text.splitlines(keepends=True)
    kept = []
    in_fence = ""
    i = 0
    while i < len(lines):
        line = lines[i]
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if marker:
            mark = marker[1]
            if not in_fence:
                in_fence = mark
            elif mark.startswith(in_fence) and not line[marker.end():].strip():
                in_fence = ""
            kept.append(line)
            i += 1
            continue
        heading = line.rstrip()
        detail = None if in_fence else _COLLECTION_STATUS.fullmatch(heading)
        if detail is not None or (not in_fence and heading == "## Fetched sources"):
            i += 1
            # Inline report notes end on this line; block forms use a list.
            if detail is None or not detail[2]:
                after = i
                while after < len(lines) and not lines[after].strip():
                    after += 1
                while after < len(lines) and lines[after].startswith("- "):
                    after += 1
                if after > i and lines[after - 1].startswith("- "):
                    i = after
            continue
        shot_note = _SHOT_NOTE.search(line) if research_report and not in_fence else None
        if shot_note:
            before = line[:shot_note.start()].rstrip()
            if before and before not in {"-", "*", "+"}:
                kept.append(before + line[len(line.rstrip("\r\n")):])
        else:
            kept.append(line)
        i += 1
    return "".join(kept)
