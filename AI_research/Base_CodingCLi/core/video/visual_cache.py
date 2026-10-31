"""Verified screenshot reuse; new claims must still match the cached visible source content."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from core.fileio import atomic_write, file_lock
from core.video import screenshots, store, visual_materials, visual_matching


def key(need: dict, url: str, policy: dict) -> str:
    return visual_materials.digest({"url": visual_materials.url_key(url), "kind": need["kind"],
                                    "selection_revision": 2,
                                    "visual_subject": need.get("visual_subject", ""), "visual_goal": need.get("visual_goal", ""),
                                    "version": policy["version"], "entities": visual_matching.entities(need["claims"]),
                                    "prices": visual_matching.prices(need["claims"]), "cycles": sorted(visual_matching.cycles(need["claims"]))})


def path(need: dict, url: str, policy: dict) -> Path:
    return Path(store.db_path()).parent / "video_visual_cache" / (key(need, url, policy) + ".json")


def reusable(record: dict, need: dict, policy: dict) -> bool:
    try:
        at = datetime.fromisoformat(record["captured_at"])
        age = (datetime.now(timezone.utc) - at).total_seconds()
        lifetime = policy["cache_hours"] * 3600
        if need["kind"] == "pricing":
            lifetime = min(lifetime, 900)
        if not 0 <= age < lifetime:
            return False
        quality = record.get("quality") or {}
        if int(quality.get("version", 0)) < policy["version"]:
            return False
        if policy.get("ocr_review") and not (quality.get("image") or {}).get("ocr_lines"):
            return False
        if policy.get("vision_required"):
            from core.video.image_quality import understanding_key
            vision = (quality.get("image") or {}).get("vision") or {}
            if vision.get("state") != "passed" or vision.get("claim_sha256") != understanding_key(need):
                return False
        if policy["version"] >= 2:
            okay, _, _ = visual_matching.compatible(need, {"text": quality.get("region_text", ""), "box": {}}, record["title"])
            if not okay:
                return False
            if need["kind"] == "pricing" and policy.get("ocr_review") and not need.get("visual_subject"):
                okay, _, _ = visual_matching.compatible(need, {"text": quality["image"]["visible_text"], "box": {}}, record["title"])
                if not okay:
                    return False
        return True
    except (KeyError, ValueError, TypeError, OverflowError):
        return False


def read(need: dict, url: str, policy: dict) -> tuple[bytes, dict] | None:
    try:
        pointer = json.loads(path(need, url, policy).read_text(encoding="utf-8"))
        record, image = screenshots.load(pointer["script_id"], pointer["screenshot_id"])
        if visual_materials.url_key(record["url"]) != visual_materials.url_key(url) or not reusable(record, need, policy):
            return None
        return image.read_bytes(), record
    except (OSError, KeyError, ValueError, TypeError):
        return None


def remember(need: dict, url: str, policy: dict, asset: dict) -> None:
    dest = path(need, url, policy)
    with file_lock(str(dest)):
        atomic_write(str(dest), json.dumps({"script_id": asset["script_id"], "screenshot_id": asset["id"]}))
