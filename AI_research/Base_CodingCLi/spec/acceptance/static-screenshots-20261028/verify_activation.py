"""Read-only production inventory and isolated replay of the five live AI bindings.

Browser/model responses are replayed from this round's three real samples.
All new scripts, images and render submissions stay in the evidence directory.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import sqlite3
import sys
import time
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
AUTO = ROOT.parents[1] / "autovideo"
sys.path[:0] = [str(ROOT), str(AUTO)]

from core.config import load_job
from core.video import content_columns, production, store
from g2 import explanation
from g2.production_plan import resolve_defaults
from tests.test_video_visuals import Engine
from web.services import video_render, video_visuals
from web.settings import get_settings

BINDINGS = {
    "flash_daily": ("ai_flash", "product-v3/product/render.json", 0),
    "ai_media_opensource_digest": ("ai_opensource", "opensource-v4/opensource/render.json", 1),
    "ai_media_arena_digest": ("ai_model_review", "product-v3/product/render.json", 0),
    "ai_media_business_digest": ("ai_business", "product-v3/product/render.json", 0),
    "ai_media_deep_dive": ("ai_deep_research", "code-docs-v3/documentation/render.json", 2),
}
SOURCES = [
    "core/video/sources.py", "core/video/visual_materials.py", "core/video/page_regions.js",
    "core/video/visual_matching.py", "core/video/image_quality.py", "core/video/visual_focus.py",
    "core/video/visual_cache.py", "web/services/video_render.py", "web/services/video_visuals.py",
    "web/services/video_visual_acquire.py",
]


def write(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def inventory(destination: Path, *, backup: bool) -> dict:
    result = {"checked_at": time.time(), "databases": {}, "files": {}}
    for name, path, tables in (
        ("research", ROOT / "state/video.db", ("scripts", "script_versions", "renders", "autopilot_runs")),
        ("engine", AUTO / "g3/data/jobs.sqlite3", ("jobs", "attempts", "artifacts")),
    ):
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as db:
            db.row_factory = sqlite3.Row
            if backup:
                with sqlite3.connect(destination / (name + ".sqlite3")) as target:
                    db.backup(target)
            result["databases"][name] = {}
            for table in tables:
                rows = [dict(row) for row in db.execute(f'SELECT * FROM "{table}"')]
                result["databases"][name][table] = rows
    terminal = {row["job_id"] for row in result["databases"]["engine"]["jobs"]
                if row["status"] in {"COMPLETED", "FAILED", "CANCELLED"}}
    for base in (ROOT / "state/video_renders", ROOT / "state/video_screenshots", AUTO / "g3/data/jobs"):
        for directory, _, names in os.walk(base):
            folder = Path(directory)
            if base.name == "jobs" and folder != base and folder.relative_to(base).parts[0] not in terminal:
                continue
            for name in names:
                path = folder / name
                if path.is_file():
                    stat = path.stat()
                    result["files"][str(path)] = [stat.st_size, stat.st_mtime_ns]
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    base = ROOT / "state/validation/static-screenshots-20261028"
    before = inventory(out, backup=True)
    write(out / "inventory-before.json", before)
    settings = get_settings()
    configs = production.load_all(settings.paths.config_dir, settings.paths.jobs_dir)
    runtime = json.loads((ROOT / "state/app_runtime.json").read_text())
    revisions = {name: {"mtime": (ROOT / name).stat().st_mtime,
                        "sha256": hashlib.sha256((ROOT / name).read_bytes()).hexdigest()} for name in SOURCES}
    assert all(runtime["started"] > item["mtime"] for item in revisions.values()), "Service needs idle maintenance"
    evidence = {"runtime": runtime, "research_sources": revisions, "restart_performed": False,
                "reason": "Existing managed instance started after all screenshot changes",
                "engine_storyboard": {"path": str(AUTO / "g2/explanation.py"),
                    "sha256": hashlib.sha256((AUTO / "g2/explanation.py").read_bytes()).hexdigest(),
                    "loading": "G2ProcessRunner starts a fresh run_g2.py process for each attempt"}, "columns": []}
    delivery = json.loads((base / "delivery.json").read_text())
    isolated = out / "isolated-state"
    isolated.mkdir(exist_ok=False)
    store.use_state_dir(str(isolated))
    store.init_db()
    for key, (column_key, sample_path, sample_index) in BINDINGS.items():
        config = configs[key]
        column = content_columns.for_production(settings.paths.config_dir, key, configs=configs)
        assert config.usable and config.auto > 0 and column and column.usable and column.key == column_key
        assert load_job(settings.paths.jobs_dir, config.job)["enabled"]
        context = content_columns.snapshot(settings.paths.config_dir, key, config.job)
        sample = json.loads((base / sample_path).read_text())
        source = delivery["samples"][sample_index]
        asset_dir = Path(source["delivery"]) / "真实截图"
        raw = (asset_dir / "01.png").read_bytes()
        asset = json.loads((asset_dir / "01.json").read_text())
        # Reuse only the captured pixels and measured metadata, never old asset ownership.
        metadata = {k: copy.deepcopy(v) for k, v in asset.items() if k in {
            "url", "final_url", "title", "mode", "selector", "clip", "scroll", "viewport", "captured_at", "quality"}}
        script = store.create_script(config.recipe, {"title": sample["title"], "fields": {}},
                                     created_by="isolated-activation-replay", production=key, content_context=context)
        sections = [{"title": chapter["title"], "paragraphs": [
            {"text": text, "citations": [source["source_url"]] if i in (1, 2) else [],
             "evidence": "CITED" if i in (1, 2) else "UNCITED"}
            for i, text in enumerate(chapter["paragraphs"])]} for chapter in sample["request"]["chapters"]]
        store.add_version(script["id"], title=sample["title"], sections=sections, length_check={}, edit_type="EDITED")
        store.update_script(script["id"], status="APPROVED", approved_version=1)
        engine = Engine()
        with patch.object(video_render, "get_client", engine.client), \
             patch.object(video_visuals, "acquire", return_value=(raw, metadata)), \
             patch.object(video_visuals.image_quality, "verify", return_value=metadata["quality"]["image"]):
            render = video_render.create(store.get_script(script["id"]))
            assert render["visuals"]["state"] == "pending"
            assert all(need["visual_subject"] and need["visual_goal"] for need in render["visuals"]["needs"])
            frozen = video_visuals.freeze(render, engine.client())
        request = frozen["submission_request"]
        assert frozen["visuals"]["state"] == "completed" and engine.uploads == 1
        assert request["visual_storytelling"] and request["chapters"] == sample["request"]["chapters"]
        image = request["image_materials"][0]
        assert image["placements"] == [{"chapter_index": 0, "paragraph_index": 1}, {"chapter_index": 0, "paragraph_index": 2}]
        assert len(image["focus"]["regions"]) == 3
        effective = request["production_context"]["content_recipe"]["settings"]
        assert effective["voice_id"] == "voxcpm_anchor"
        assert request["production_context"]["matrix"]["settings"]["video_speed"] == 1.25
        resolved, _ = resolve_defaults(request)
        assert resolved["voice_id"] == effective["voice_id"] and resolved["video_speed"] == 1.25
        # Compile the known failing flow choice with real measured image regions.
        # Timing is synthetic here; real timed MP4s are the three earlier samples.
        from types import SimpleNamespace
        storyboard_dir = out / key
        storyboard_dir.mkdir()
        pipeline = SimpleNamespace(ARTIFACTS=storyboard_dir, write_json=write)
        pic = {**{k: copy.deepcopy(image[k]) for k in ("caption", "role", "focus")},
               "image_path": "assets/slideshow/image-01.png", "image_asset_id": "slide_image_1"}
        text = sections[0]["paragraphs"][1]["text"]
        board = explanation.compile_storyboard(pipeline,
            {"groups": [{"id": "p1", "title": sample["title"], "text": text,
                         "segment_ids": ["n1"], "image_indices": [0]}],
             "pages": [{"id": "p1", "component": "flow_diagram", "props": {"title": sample["title"]},
                        "material_cues": [{"image_index": 0, "region_index": 1, "phrase": text[:5]}]}]},
            {"rows": [{"id": "n1", "text": text, "start_ms": 100, "end_ms": 15100}]},
            {"image_materials": [pic]}, [{"id": pic["image_asset_id"], "path": pic["image_path"]}])
        scene = board["scenes"][0]
        assert scene["component"] == "object_showcase" and scene["props"]["images"][0] == pic
        assert [cue["targets"] for cue in scene["props"]["shot_plan"]["cues"]] == [["region:0:1"]]
        write(storyboard_dir / "compiled-storyboard.json", board)
        write(out / (key + "-submission.json"), request)
        evidence["columns"].append({"key": column.key, "name": column.name, "production": key,
            "job": config.job, "recipe": config.recipe, "auto": config.auto,
            "visual_state": frozen["visuals"]["state"], "images": len(request["image_materials"]),
            "placements": image["placements"], "focus_regions": len(image["focus"]["regions"]),
            "voice_id": effective["voice_id"], "video_speed": 1.25,
            "compiled_component": scene["component"], "compiled_focus": "region:0:1",
            "replay": "previous real screenshot and model response; engine upload mocked; no live job"})
    after = inventory(out, backup=False)
    write(out / "inventory-after.json", after)
    missing = [path for path in before["files"] if path not in after["files"]]
    changed = [path for path, stat in before["files"].items() if path in after["files"] and after["files"][path] != stat]
    assert not missing and not changed, {"missing": missing, "changed": changed}
    for database, tables in before["databases"].items():
        for table, rows in tables.items():
            # A running task may progress, but no existing row may disappear.
            keys = [key for key in ("id", "job_id", "attempt_no", "name", "script_id", "version") if rows and key in rows[0]]
            old_ids = {tuple(row[key] for key in keys) for row in rows}
            new_ids = {tuple(row[key] for key in keys) for row in after["databases"][database][table]}
            assert old_ids <= new_ids, (database, table, old_ids - new_ids)
    assert json.loads((ROOT / "state/app_runtime.json").read_text())["instance_id"] == runtime["instance_id"]
    evidence["preservation"] = {"historical_files_unchanged": len(before["files"]),
        "existing_database_rows_preserved": True, "same_service_instance": True,
        "database_backups": ["research.sqlite3", "engine.sqlite3"],
        "counts_before": {db: {table: len(rows) for table, rows in tables.items()}
                          for db, tables in before["databases"].items()}}
    evidence["checked_at"] = time.time()
    write(out / "activation.json", evidence)
    print(json.dumps({"columns": evidence["columns"], "preservation": evidence["preservation"],
                      "evidence": str(out)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
