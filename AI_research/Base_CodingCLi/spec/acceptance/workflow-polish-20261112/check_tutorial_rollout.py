"""Read the scheduled report, batch, manuscript and render for a writing Job."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import subprocess
import sys
from contextlib import closing
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from core import report_identity
from core.config import load_system_config
from core.video import episodes, persona, production, topic_store, writer
from core.video.manuscript_workflow import JOB_PROMPTS, enabled_jobs
from web.services.video_render import chapters, narration_source

JOB = "ai_media/ai_tool_tutorials"
TIMEZONE = ZoneInfo("Asia/Shanghai")


def read_db(root: Path, name: str) -> sqlite3.Connection:
    db = sqlite3.connect(f"{(root / 'state' / name).as_uri()}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    return db


def matching_batches(db: sqlite3.Connection, root: Path, report: Path, job: str = JOB) -> list[dict]:
    identity = report_identity.describe(str(report), str(root))
    batches = []
    for row in db.execute(
        "SELECT id, status, note, spec FROM autopilot_runs "
        "WHERE json_extract(spec, '$.job') = ? ORDER BY started_at",
        (job,),
    ):
        spec = json.loads(row["spec"])
        if spec.get("report_identity") != identity["id"]:
            continue
        if (root / spec["report"]).resolve() != report:
            continue
        batches.append({key: row[key] for key in ("id", "status", "note")})
    return batches


def check_drafts(db: sqlite3.Connection, root: Path, batch_id: str) -> list[dict]:
    items = []
    for row in db.execute(
        "SELECT i.id, i.state, i.note, s.id AS script_id, s.status AS script_status, s.production, "
        "s.topic, s.content_context, "
        "v.title, v.sections, v.meta, v.created_at AS draft_created_at, r.id AS render_id, r.status AS render_status, "
        "r.version AS render_version, r.source_sha256, r.artifacts "
        "FROM autopilot_items i LEFT JOIN scripts s ON s.id = i.script_id "
        "LEFT JOIN script_versions v ON v.script_id = s.id AND v.version = 1 "
        "LEFT JOIN renders r ON r.id = i.render_id WHERE i.run_id = ? ORDER BY i.slot",
        (batch_id,),
    ):
        item = {key: row[key] for key in ("id", "state", "note", "script_id", "script_status", "production")}
        meta = json.loads(row["meta"] or "{}")
        if row["meta"] is not None:
            editorial = meta.get("manuscript_workflow") or {}
            item.update(title=row["title"], profile=editorial.get("profile"), revision=editorial.get("result"))
            item["revised_paragraphs"] = [change["id"] for change in editorial.get("changes", [])]
            item["draft_created_at"] = row["draft_created_at"]
            item["_creative_direction"] = meta.get("creative_direction", "")
            item["author_system_prompt_sha256"] = meta.get("author_system_prompt_sha256")
            item["_topic"] = json.loads(row["topic"] or "{}")
            item["_context"] = json.loads(row["content_context"] or "{}")
            item["reader_prompt_sha256"] = editorial.get("reader_prompt_sha256")
            item["revision_prompt_sha256"] = editorial.get("revision_prompt_sha256")
            item["has_independent_read"] = "reading" in editorial
            item["editorial_error"] = editorial.get("error")
        if row["render_id"]:
            item["render"] = {"id": row["render_id"], "status": row["render_status"], "version": row["render_version"]}
            if row["sections"] is not None and row["render_version"] == 1:
                version = {"title": row["title"], "sections": json.loads(row["sections"]), "meta": meta}
                _, source_hash = narration_source(chapters(version))
                item["render"]["matches_first_draft"] = source_hash == row["source_sha256"]
            for artifact in json.loads(row["artifacts"] or "[]"):
                if artifact["name"] == "video.mp4":
                    path = root / "state" / "video_renders" / row["render_id"] / "video.mp4"
                    if path.is_file():
                        with path.open("rb") as media:
                            digest = hashlib.sha256()
                            while block := media.read(1024 * 1024):
                                digest.update(block)
                        item["render"]["local_video_hash_matches"] = digest.hexdigest() == artifact["sha256"]
                    break
        items.append(item)
    return items


def verify_prompts(item: dict, profile, narrator: dict) -> None:
    topic, context = item["_topic"], item["_context"]
    system = writer.author_system_prompt(
        topic, narrator, greeting=episodes.greeting(topic, context), tutorial=writer.tutorial_context(context),
        episode_prompt=profile.episode_prompt, episode_lead=profile.episode_lead,
    )
    expected = hashlib.sha256(system.encode()).hexdigest()
    item["expected_author_system_prompt_sha256"] = expected
    item["matches_job_author_system_prompt"] = item["author_system_prompt_sha256"] == expected
    item["matches_job_author_direction"] = not profile.draft or profile.draft in item["_creative_direction"]
    item["matches_job_reader_prompt"] = item["reader_prompt_sha256"] == hashlib.sha256(profile.read.encode()).hexdigest()
    revision = item["revision_prompt_sha256"]
    if revision or item["revision"] == "revised":
        item["matches_job_revision_prompt"] = revision == hashlib.sha256(profile.revise.encode()).hexdigest()


def verified_first_draft(item: dict) -> bool:
    return all(item.get(field) for field in (
        "matches_configured_profile", "matches_job_author_system_prompt",
        "matches_job_author_direction", "matches_job_reader_prompt", "has_independent_read",
    )) and not item.get("editorial_error") and item.get("matches_job_revision_prompt") is not False


def verify_render(root: Path, item: dict) -> None:
    """Decode the actual MP4 only after prompt and source identity checks pass."""
    render = item.get("render") or {}
    if not (verified_first_draft(item) and render.get("status") == "COMPLETED"
            and render.get("matches_first_draft") and render.get("local_video_hash_matches")):
        return
    ffmpeg = ROOT.parents[1] / "autovideo/g0/darwin_arm64/ffmpeg"
    path = root / "state" / "video_renders" / render["id"] / "video.mp4"
    subprocess.run([ffmpeg, "-nostdin", "-v", "error", "-xerror", "-i", path,
                    "-map", "0:v:0", "-map", "0:a:0", "-f", "null", "-"], check=True)
    render["complete_decode"] = True


def inspect(root: Path, *, due_at: float | None, report: Path | None, job: str = JOB) -> dict:
    config_dir = str(root / "config")
    jobs_dir = str(root / "jobs")
    enabled = job in enabled_jobs(config_dir, load_system_config(config_dir).get("video") or {})
    profile = JOB_PROMPTS.get(job) if enabled else None
    narrator = persona.load(config_dir)
    result: dict = {"job": job, "configured_profile": profile.version if profile else None}
    if profile and profile.production_id:
        result["configured_production"] = profile.production_id
    if due_at is not None:
        with closing(read_db(root, "schedules.db")) as db:
            run = db.execute(
                "SELECT id, status, attempt, output_path, error FROM schedule_runs "
                "WHERE job_name = ? AND due_at = ? ORDER BY created_at DESC LIMIT 1",
                (job, due_at),
            ).fetchone()
        result["due_at"] = datetime.fromtimestamp(due_at, TIMEZONE).isoformat()
        result["schedule_run"] = dict(run) if run else None
        if not run or run["status"] != "success" or not run["output_path"]:
            return result
        report = Path(run["output_path"])
    assert report is not None
    report = (root / report).resolve()
    result["report_path"] = str(report)
    if not report.is_file():
        result["report_available"] = False
        return result
    result["report_identity"] = report_identity.describe(str(report), str(root))["id"]
    source = topic_store.resolve(jobs_dir, job)
    bindings = production.load_all(config_dir, jobs_dir)
    accepts_whole_report = any(binding.job == job and binding.usable and binding.auto and binding.split == "whole"
                               for binding in bindings.values())
    result["has_topics"] = topic_store.report_has_content(source, str(report), str(root)) if source else None
    result["accepts_whole_report"] = accepts_whole_report
    with closing(read_db(root, "video.db")) as db:
        batches = matching_batches(db, root, report, job)
        for batch in batches:
            batch["items"] = check_drafts(db, root, batch["id"])
            for item in batch["items"]:
                if "profile" in item:
                    expected = result["configured_profile"]
                    if profile and profile.production_id and item["production"] != profile.production_id:
                        expected = None
                    item["matches_configured_profile"] = item["profile"] == expected
                    if expected:
                        verify_prompts(item, profile, narrator)
                for field in ("_creative_direction", "_topic", "_context"):
                    item.pop(field, None)
    result["batches"] = batches
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, default=ROOT)
    parser.add_argument("--job", default=JOB, help="Research Job name, defaulting to the AI tool tutorial")
    parser.add_argument("--decode", action="store_true", help="Fully decode renders whose first draft passed prompt checks")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--slot", type=datetime.fromisoformat, help="Local Shanghai time or an ISO timestamp with an offset")
    target.add_argument("--report", type=Path, help="Inspect an existing report without a schedule record")
    args = parser.parse_args()
    when = args.slot
    if when and when.tzinfo is None:
        when = when.replace(tzinfo=TIMEZONE)
    workspace = args.workspace.resolve()
    result = inspect(workspace, due_at=when.timestamp() if when else None, report=args.report, job=args.job)
    if args.decode:
        for batch in result.get("batches", []):
            for item in batch["items"]:
                verify_render(workspace, item)
    print(json.dumps(result, ensure_ascii=False, indent=2))
