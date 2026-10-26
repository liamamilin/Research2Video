from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import struct
from typing import Iterator
from uuid import UUID

from g3.file_integrity import artifact_digest


STAGES = ("PLAN", "VOICE_ALIGN", "STORYBOARD", "SCENE_IMAGES", "BUILD_IR", "RENDER", "QUALITY_CHECK", "PACKAGE", "AUDIO_MIX")
TERMINAL_STATES = {"COMPLETED", "FAILED", "CANCELLED"}
SCENE_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,47}$")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


class StoreError(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


class JobStore:
    """SQLite state store. Claims and status transitions use BEGIN IMMEDIATE."""

    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir).expanduser().resolve()
        self.db_path = self.data_dir / "jobs.sqlite3"
        self.job_root = self.data_dir / "jobs"

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=15, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=15000")
        return conn

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialize(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if not self.job_root.is_symlink():
            self.job_root.mkdir(parents=True, exist_ok=True)
        conn = self._connect()
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    source_rel_path TEXT NOT NULL,
                    source_sha256 TEXT NOT NULL,
                    source_char_count INTEGER NOT NULL,
                    target_duration_sec INTEGER NOT NULL,
                    theme TEXT NOT NULL,
                    voice_id TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('QUEUED','RUNNING','COMPLETED','FAILED','CANCELLED')),
                    active_stage TEXT,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    error_code TEXT,
                    error_message TEXT,
                    retryable INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 1,
                    operation_type TEXT NOT NULL DEFAULT 'generate',
                    parent_job_id TEXT,
                    parent_attempt_no INTEGER,
                    scene_id TEXT,
                    prompt_extra TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS jobs_queue_idx ON jobs(status, created_at, job_id);
                CREATE TABLE IF NOT EXISTS attempts (
                    job_id TEXT NOT NULL REFERENCES jobs(job_id),
                    attempt_no INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    workspace_rel_path TEXT NOT NULL,
                    runner_pid INTEGER,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    error_code TEXT,
                    error_message TEXT,
                    PRIMARY KEY(job_id, attempt_no)
                );
                CREATE TABLE IF NOT EXISTS stages (
                    job_id TEXT NOT NULL,
                    attempt_no INTEGER NOT NULL,
                    stage TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    duration_seconds REAL,
                    error_code TEXT,
                    error_message TEXT,
                    PRIMARY KEY(job_id, attempt_no, stage),
                    FOREIGN KEY(job_id, attempt_no) REFERENCES attempts(job_id, attempt_no)
                );
                CREATE TABLE IF NOT EXISTS artifacts (
                    job_id TEXT NOT NULL,
                    attempt_no INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    relative_path TEXT NOT NULL,
                    content_type TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    bytes INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(job_id, attempt_no, name),
                    FOREIGN KEY(job_id, attempt_no) REFERENCES attempts(job_id, attempt_no)
                );
            """)
            columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)").fetchall()}
            additions = {
                "operation_type": "TEXT NOT NULL DEFAULT 'generate'",
                "parent_job_id": "TEXT",
                "parent_attempt_no": "INTEGER",
                "scene_id": "TEXT",
                "prompt_extra": "TEXT NOT NULL DEFAULT ''",
                "video_form": "TEXT NOT NULL DEFAULT 'knowledge_cards'",
                "duration_mode": "TEXT NOT NULL DEFAULT 'fixed'",
                "input_analysis_json": "TEXT",
                "creative_intent_json": "TEXT NOT NULL DEFAULT '{}'",
                "revision_mode": "TEXT",
                "video_speed": "REAL NOT NULL DEFAULT 1",
                "input_mode": "TEXT NOT NULL DEFAULT 'material'",
                "production_recipe_json": "TEXT",
                "production_plan_json": "TEXT",
                "background_music_json": "TEXT",
                "idempotency_key": "TEXT",
                "request_sha256": "TEXT",
            }
            for name, definition in additions.items():
                if name not in columns:
                    conn.execute(f"ALTER TABLE jobs ADD COLUMN {name} {definition}")
            conn.execute("CREATE INDEX IF NOT EXISTS jobs_parent_idx ON jobs(parent_job_id, created_at)")
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS jobs_submission_idx ON jobs(idempotency_key) WHERE idempotency_key IS NOT NULL")
            conn.execute("PRAGMA user_version=10")
        finally:
            conn.close()

    def record_input_analysis(self, job_id: str, attempt_no: int, brief: dict) -> None:
        compact = {k: brief[k] for k in ("requested_form", "selected_form", "recommended_form", "selection_mode", "status", "reason", "issues")}
        compact["retained_units"] = len(brief["ordered_unit_ids"])
        compact["excluded_units"] = len(brief["excluded_units"])
        with self._transaction() as conn:
            conn.execute("UPDATE jobs SET input_analysis_json=?,updated_at=?,version=version+1 WHERE job_id=? AND attempt_count=? AND status='RUNNING'",
                         (json.dumps(compact, ensure_ascii=False), utc_now(), job_id, attempt_no))

    def _require_storage(self) -> None:
        if (self.data_dir / ".storage-migration").exists():
            raise StoreError("STORAGE_MIGRATING", "正在切换视频输出目录，请稍后重试")
        parts = self.job_root.resolve().parts
        if len(parts) >= 3 and parts[1] == "Volumes" and not os.path.ismount(Path(*parts[:3])):
            raise StoreError("STORAGE_OFFLINE", "视频输出硬盘未连接，请连接后重试")
        if self.job_root.is_symlink() and not self.job_root.is_dir():
            raise StoreError("STORAGE_OFFLINE", "视频输出目录不可用，请连接硬盘后重试")

    def create_job(self, job_id: str, request: dict, *, input_document: dict | None = None) -> dict:
        UUID(job_id)
        key = request.get("idempotency_key")
        fingerprint = self._request_digest(request)
        job_dir = self.job_root / job_id
        created = False
        try:
            # Serialize the lookup, workspace creation and insert. A concurrent
            # replay must see the committed first job before creating any files.
            with self._transaction() as conn:
                existing = self._submission(conn, key, fingerprint, self._compatible_request_digests(request))
                if existing:
                    return self.get_job(existing)
                self._require_storage()
                job_dir.mkdir(exist_ok=False)
                created = True
                self._create_job(conn, job_id, request, input_document=input_document)
                conn.execute("UPDATE jobs SET idempotency_key=?,request_sha256=? WHERE job_id=?",
                             (key, fingerprint, job_id))
        except BaseException:
            if created:
                import shutil
                shutil.rmtree(job_dir, ignore_errors=True)
            raise
        return self.get_job(job_id)

    @staticmethod
    def _request_digest(request: dict) -> str:
        submitted = {k: v for k, v in request.items() if k != "idempotency_key"}
        if "_production_submitted" in submitted:
            from g2.production_plan import FIELDS
            explicit = submitted.pop("_production_submitted")
            for key in FIELDS | {"production_recipe", "production_context", "background_music"}:
                submitted.pop(key, None)
            submitted["production_submission"] = explicit
        return sha256_bytes(json.dumps(submitted, ensure_ascii=False, sort_keys=True,
                                       separators=(",", ":")).encode("utf-8"))

    @staticmethod
    def _compatible_request_digests(request: dict) -> set[str]:
        def old_digest(value):
            return sha256_bytes(json.dumps({k: v for k, v in value.items() if k != "idempotency_key"},
                ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())
        # Preserve exact replays from before production plans were introduced.
        legacy = {k: v for k, v in request.items() if k != "_production_submitted"}
        for key, default in {"production_mode": "standard", "visual_style": "legacy", "production_context": None,
                             "content_notices": []}.items():
            if legacy.get(key) == default:
                legacy.pop(key, None)
        return {old_digest(request), old_digest(legacy)}

    @staticmethod
    def _submission(conn: sqlite3.Connection, key: str | None, fingerprint: str, compatible: set[str] | None = None) -> str | None:
        if key is None:
            return None
        row = conn.execute("SELECT job_id,request_sha256 FROM jobs WHERE idempotency_key=?", (key,)).fetchone()
        if row is None:
            return None
        if row["request_sha256"] != fingerprint and row["request_sha256"] not in (compatible or set()):
            raise StoreError("IDEMPOTENCY_CONFLICT", "此提交标识已用于另一组文稿或制作设置，请为新出片使用新的标识。")
        return row["job_id"]

    def find_submission(self, request: dict) -> dict | None:
        """Look up an exact replay before checking today's generation readiness."""
        conn = self._connect()
        try:
            job_id = self._submission(conn, request.get("idempotency_key"), self._request_digest(request), self._compatible_request_digests(request))
        finally:
            conn.close()
        return self.get_job(job_id) if job_id else None

    def _create_job(self, conn: sqlite3.Connection, job_id: str, request: dict,
                    *, input_document: dict | None = None) -> None:
        from g2.production_plan import prepare_request
        job_dir = (self.job_root / job_id).resolve()
        if not job_dir.is_relative_to(self.job_root.resolve()):
            raise StoreError("INVALID_WORKSPACE", "job workspace escaped its root")
        input_dir = job_dir / "input"
        input_dir.mkdir()
        from g3.creative_images import freeze_images
        from g3.music_assets import freeze as freeze_music
        from g3.brand_profiles import freeze as freeze_brand
        from g2.creative_library import prepare_request as prepare_resources
        request = prepare_resources(request)
        request = freeze_music(self.data_dir, input_dir, freeze_images(self.data_dir, input_dir, request))
        request = freeze_brand(self.data_dir, input_dir, request)
        request = prepare_request(request)
        source_text = request["source_text"]
        source_path = input_dir / "source.txt"
        tmp_path = input_dir / "source.txt.tmp"
        raw = source_text.encode("utf-8")
        with tmp_path.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp_path, source_path)
        request_without_source = {k: v for k, v in request.items() if k != "source_text"}
        if input_document is not None:
            document_root = input_dir / "document"
            document_root.mkdir()
            metadata = {**input_document["metadata"],
                        "source_edited": source_text != input_document["text"],
                        "submitted_source_sha256": sha256_bytes(raw)}
            (document_root / f"original.{metadata['format']}").write_bytes(input_document["raw"])
            (document_root / "extracted.txt").write_text(input_document["text"], encoding="utf-8")
            (document_root / "document.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
            request_without_source["input_document"] = metadata
        input_json = input_dir / "input.json"
        input_json.write_text(json.dumps({**request_without_source, "source_sha256": sha256_bytes(raw)},
                                         ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        now = utc_now()
        # job_dir is resolved (the jobs folder may be a symlink to another disk), so build the stored path from the
        # unresolved root instead of taking it relative to data_dir
        source_rel = (self.job_root.relative_to(self.data_dir) / job_id / "input" / "source.txt").as_posix()
        conn.execute("""INSERT INTO jobs
                    (job_id,title,source_rel_path,source_sha256,source_char_count,target_duration_sec,theme,voice_id,
                     status,active_stage,created_at,updated_at,operation_type,parent_job_id,parent_attempt_no,scene_id,prompt_extra,video_form,duration_mode)
                    VALUES(?,?,?,?,?,?,?,?, 'QUEUED','PLAN',?,?,?,?,?,?,?,?,?)""",
                    (job_id, request["title"], source_rel, sha256_bytes(raw), len(source_text),
                     request["target_duration_sec"], request["theme"], request["voice_id"], now, now,
                     request.get("operation_type", "generate"), request.get("parent_job_id"),
                     request.get("parent_attempt_no"), request.get("scene_id"), request.get("prompt_extra", ""),
                     request.get("video_form", "knowledge_cards"), request.get("duration_mode", "fixed")))
        conn.execute("UPDATE jobs SET creative_intent_json=?,revision_mode=?,video_speed=?,input_mode=? WHERE job_id=?",
                             (json.dumps(request.get("creative_intent", {}), ensure_ascii=False), request.get("revision_mode"),
                              request.get("video_speed",1), request.get("input_mode", "material"), job_id))
        conn.execute("UPDATE jobs SET background_music_json=? WHERE job_id=?",
                     (json.dumps(request["background_music_resolved"], ensure_ascii=False) if request.get("background_music_resolved") else None, job_id))
        conn.execute("UPDATE jobs SET production_plan_json=? WHERE job_id=?",
                     (json.dumps(request["production_plan"], ensure_ascii=False), job_id))
        conn.execute("UPDATE jobs SET production_recipe_json=? WHERE job_id=?",
                             (json.dumps(request["production_recipe_resolved"], ensure_ascii=False)
                              if request.get("production_recipe_resolved") else None, job_id))

    def editable_content(self, job_id: str) -> dict:
        job = self.get_job(job_id)
        source_path = self.source_path(job_id)
        source = source_path.read_text(encoding="utf-8")
        if sha256_bytes(source.encode("utf-8")) != job["source_sha256"]:
            raise StoreError("JOB_INPUT_CHANGED", "原稿文件校验失败，暂不能基于它修订。")
        input_path = source_path.parent / "input.json"
        if input_path.is_symlink() or not input_path.resolve().is_relative_to((self.job_root / job_id).resolve()):
            raise StoreError("INVALID_WORKSPACE", "任务输入路径无效。")
        try:
            saved = json.loads(input_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise StoreError("JOB_INPUT_INVALID", "原任务设置不可用。") from exc
        narration = None
        if job["status"] == "COMPLETED":
            try:
                path, _ = self.artifact_path(job_id, "narration.json")
                plan = json.loads(path.read_text(encoding="utf-8"))
                narration = "\n\n".join(s["text"] for s in plan["segments"])
            except StoreError as exc:
                if exc.code != "ARTIFACT_NOT_FOUND":
                    raise
        keys = ("target_duration_sec", "duration_mode", "theme", "voice_id", "video_form", "use_draw_things", "video_speed")
        options = {key: saved.get(key, job.get(key)) for key in keys}
        options["use_draw_things"] = saved.get("use_draw_things", False)
        for key in ("package", "packaging", "packaging_resolved", "sources", "production_recipe", "production_recipe_resolved", "production_plan", "production_mode", "visual_style", "production_context", "content_notices", "visual_storytelling", "slideshow_images", "image_materials", "slideshow_images_resolved", "background_music", "background_music_resolved", "brand", "brand_resolved"):
            if saved.get(key) is not None:
                options[key] = saved[key]
        return {"job_id": job_id, "title": job["title"], "source_text": source, "narration_text": narration,
                "creative_intent": job["creative_intent"], "options": options}

    def content_revisions(self, job_id: str) -> list[dict]:
        self.get_job(job_id)
        conn = self._connect()
        try:
            rows = conn.execute("SELECT * FROM jobs WHERE parent_job_id=? AND operation_type IN ('content_revision','visual_revision','media_revision','music_revision') ORDER BY created_at DESC", (job_id,)).fetchall()
            return [self._job_view(row, False) for row in rows]
        finally:
            conn.close()

    def create_scene_edit_job(self, job_id: str, parent_job_id: str, scene_id: str,
                              prompt_extra: str = "") -> dict:
        """Queue a versioned scene edit that reuses a verified completed job's inputs."""
        UUID(parent_job_id)
        if not SCENE_ID_PATTERN.fullmatch(scene_id):
            raise StoreError("SCENE_IMAGE_NOT_FOUND", "scene image not found")
        if not isinstance(prompt_extra, str) or len(prompt_extra) > 300 or any(
                ord(character) < 32 or ord(character) == 127 for character in prompt_extra):
            raise StoreError("SCENE_EDIT_PROMPT_INVALID", "补充画面要求无效，最多 300 个字符且不能包含控制字符")
        parent = self.get_job(parent_job_id)
        if parent["status"] != "COMPLETED":
            raise StoreError("SCENE_EDIT_NOT_ALLOWED", "only completed jobs can be used for a scene edit")
        input_candidate = self.job_root / parent_job_id / "input" / "input.json"
        if input_candidate.is_symlink():
            raise StoreError("INVALID_WORKSPACE", "job input is outside its workspace")
        try:
            input_path = input_candidate.resolve(strict=True)
        except OSError as exc:
            raise StoreError("JOB_INPUT_INVALID", "original job input is unavailable") from exc
        if not input_path.is_relative_to((self.job_root / parent_job_id).resolve()):
            raise StoreError("INVALID_WORKSPACE", "job input is outside its workspace")
        try:
            input_payload = json.loads(input_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise StoreError("JOB_INPUT_INVALID", "original job input is unavailable") from exc
        if not isinstance(input_payload, dict):
            raise StoreError("JOB_INPUT_INVALID", "original job input is invalid")
        if input_payload.get("use_draw_things") is not True:
            raise StoreError("SCENE_EDIT_NOT_ALLOWED", "scene edits require a job that used Draw Things")
        self.scene_edit_source(parent_job_id, parent["attempt_count"], scene_id)
        source_text = self.source_path(parent_job_id).read_text(encoding="utf-8")
        try:
            UUID(job_id)
        except ValueError as exc:
            raise StoreError("INVALID_JOB_ID", "job id must be a UUID") from exc
        request = {
            "title": f"{parent['title']} · {scene_id} 场景修订"[:80],
            "source_text": source_text,
            "target_duration_sec": parent["target_duration_sec"],
            "theme": parent["theme"],
            "voice_id": parent["voice_id"],
            "use_draw_things": True,
            "video_form": parent["video_form"],
            "duration_mode": parent["duration_mode"],
            "creative_intent": parent["creative_intent"],
            "video_speed": parent.get("video_speed",1),
            "operation_type": "scene_edit",
            "parent_job_id": parent_job_id,
            "parent_attempt_no": parent["attempt_count"],
            "scene_id": scene_id,
            "prompt_extra": prompt_extra,
        }
        for key in ("package", "packaging", "packaging_resolved", "sources", "production_recipe", "production_recipe_resolved", "production_plan", "production_mode", "visual_style", "production_context", "content_notices", "visual_storytelling", "slideshow_images", "image_materials", "slideshow_images_resolved", "background_music", "background_music_resolved", "brand", "brand_resolved"):
            if input_payload.get(key) is not None:
                request[key] = input_payload[key]
        return self.create_job(job_id, request)

    def scene_edit_source(self, parent_job_id: str, attempt_no: int, scene_id: str) -> dict:
        """Return the verified source attempt and artifacts needed by a scene edit."""
        UUID(parent_job_id)
        parent = self.get_job(parent_job_id)
        if parent["status"] != "COMPLETED" or int(parent["attempt_count"]) != int(attempt_no):
            raise StoreError("SCENE_EDIT_SOURCE_UNAVAILABLE", "source video is no longer the completed version")
        image_records = self._scene_image_records(parent_job_id)
        if not any(item["scene_id"] == scene_id for item in image_records):
            raise StoreError("SCENE_IMAGE_NOT_FOUND", "scene image not found")
        artifact_paths = {}
        for name in ("video.mp4", "captions.srt", "storyboard.json", "scene-backgrounds.json", "video-ir.json"):
            path, meta = self.artifact_path(parent_job_id, name)
            artifact_paths[name] = {"path": path, "sha256": meta["sha256"]}
        attempt_root = (self.job_root / parent_job_id / "attempts" / str(attempt_no)).resolve(strict=True)
        if not attempt_root.is_relative_to((self.job_root / parent_job_id).resolve()):
            raise StoreError("INVALID_WORKSPACE", "source attempt is outside its job workspace")
        g2_root = attempt_root / "g2-run"
        fixture_root = g2_root / "fixtures"
        if g2_root.is_symlink() or fixture_root.is_symlink() or not fixture_root.is_dir():
            raise StoreError("SCENE_EDIT_SOURCE_UNAVAILABLE", "source video timeline is unavailable")
        return {"attempt_root": attempt_root, "fixture_root": fixture_root, "artifacts": artifact_paths}

    def claim_next(self) -> dict | None:
        with self._transaction() as conn:
            try:
                self._require_storage()
            except StoreError:
                return None
            row = conn.execute("SELECT * FROM jobs WHERE status='QUEUED' ORDER BY created_at,job_id LIMIT 1").fetchone()
            if row is None:
                return None
            job_id = row["job_id"]
            attempt_no = int(row["attempt_count"]) + 1
            now = utc_now()
            workspace_rel = f"jobs/{job_id}/attempts/{attempt_no}"
            changed = conn.execute("""UPDATE jobs SET status='RUNNING',attempt_count=?,active_stage='PLAN',
                cancel_requested=0,error_code=NULL,error_message=NULL,retryable=0,input_analysis_json=NULL,updated_at=?,version=version+1
                WHERE job_id=? AND status='QUEUED'""", (attempt_no, now, job_id)).rowcount
            if changed != 1:
                return None
            conn.execute("INSERT INTO attempts(job_id,attempt_no,status,workspace_rel_path,started_at) VALUES(?,?, 'RUNNING',?,?)",
                         (job_id, attempt_no, workspace_rel, now))
            for stage in STAGES:
                status = "RUNNING" if stage == STAGES[0] else "PENDING"
                conn.execute("INSERT INTO stages(job_id,attempt_no,stage,status,started_at) VALUES(?,?,?,?,?)",
                             (job_id, attempt_no, stage, status, now if stage == STAGES[0] else None))
            claimed = dict(row)
            claimed.update({"attempt_no": attempt_no, "workspace_rel_path": workspace_rel,
                            "source_abs_path": str((self.data_dir / row["source_rel_path"]).resolve())})
            return claimed

    def set_runner_pid(self, job_id: str, attempt_no: int, pid: int | None) -> None:
        with self._transaction() as conn:
            conn.execute("UPDATE attempts SET runner_pid=? WHERE job_id=? AND attempt_no=? AND status='RUNNING'",
                         (pid, job_id, attempt_no))

    def update_stage(self, job_id: str, attempt_no: int, stage: str, status: str,
                     duration_seconds: float | None = None, error_code: str | None = None,
                     error_message: str | None = None) -> None:
        if stage not in STAGES:
            return
        now = utc_now()
        with self._transaction() as conn:
            old = conn.execute("SELECT started_at FROM stages WHERE job_id=? AND attempt_no=? AND stage=?",
                               (job_id, attempt_no, stage)).fetchone()
            if old is None:
                return
            started_at = old["started_at"] or (now if status == "RUNNING" else None)
            completed_at = now if status in {"SUCCEEDED", "SKIPPED", "FAILED", "CANCELLED", "INTERRUPTED"} else None
            conn.execute("""UPDATE stages SET status=?,started_at=?,completed_at=?,duration_seconds=?,
                error_code=?,error_message=? WHERE job_id=? AND attempt_no=? AND stage=?""",
                (status, started_at, completed_at, duration_seconds, error_code, error_message,
                 job_id, attempt_no, stage))
            # Planning and voice can overlap. Keep the earliest running stage as
            # the summary, including when the later stage finishes first.
            states = {row["stage"]: row["status"] for row in conn.execute(
                "SELECT stage,status FROM stages WHERE job_id=? AND attempt_no=?", (job_id, attempt_no))}
            active = next((item for item in STAGES if states.get(item) == "FAILED"), None)
            active = active or next((item for item in STAGES if states.get(item) == "RUNNING"), None)
            if active:
                conn.execute("UPDATE jobs SET active_stage=?,updated_at=?,version=version+1 WHERE job_id=? AND status='RUNNING'",
                             (active, now, job_id))

    def request_cancel(self, job_id: str) -> dict:
        with self._transaction() as conn:
            row = conn.execute("SELECT status FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if row is None:
                raise StoreError("JOB_NOT_FOUND", "job not found")
            if row["status"] == "QUEUED":
                now = utc_now()
                conn.execute("UPDATE jobs SET status='CANCELLED',active_stage=NULL,updated_at=?,version=version+1 WHERE job_id=? AND status='QUEUED'",
                             (now, job_id))
            elif row["status"] == "RUNNING":
                conn.execute("UPDATE jobs SET cancel_requested=1,updated_at=?,version=version+1 WHERE job_id=? AND status='RUNNING'",
                             (utc_now(), job_id))
            elif row["status"] not in TERMINAL_STATES:
                raise StoreError("INVALID_STATE", "job cannot be cancelled in its current state")
        return self.get_job(job_id)

    def cancel_requested(self, job_id: str) -> bool:
        conn = self._connect()
        try:
            row = conn.execute("SELECT cancel_requested FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            return bool(row and row["cancel_requested"])
        finally:
            conn.close()

    def mark_cancelled(self, job_id: str, attempt_no: int) -> None:
        now = utc_now()
        with self._transaction() as conn:
            conn.execute("UPDATE stages SET status='CANCELLED',completed_at=? WHERE job_id=? AND attempt_no=? AND status IN ('RUNNING','PENDING')",
                         (now, job_id, attempt_no))
            conn.execute("UPDATE attempts SET status='CANCELLED',completed_at=?,runner_pid=NULL WHERE job_id=? AND attempt_no=?",
                         (now, job_id, attempt_no))
            conn.execute("UPDATE jobs SET status='CANCELLED',active_stage=NULL,cancel_requested=0,updated_at=?,version=version+1 WHERE job_id=?",
                         (now, job_id))

    def retry(self, job_id: str) -> dict:
        with self._transaction() as conn:
            row = conn.execute("SELECT status,retryable FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if row is None:
                raise StoreError("JOB_NOT_FOUND", "job not found")
            if row["status"] != "FAILED" or not row["retryable"]:
                raise StoreError("RETRY_NOT_ALLOWED", "only retryable failed jobs can be retried")
            conn.execute("""UPDATE jobs SET status='QUEUED',active_stage='PLAN',cancel_requested=0,
                error_code=NULL,error_message=NULL,retryable=0,updated_at=?,version=version+1 WHERE job_id=? AND status='FAILED'""",
                         (utc_now(), job_id))
        return self.get_job(job_id)

    def mark_failed(self, job_id: str, attempt_no: int, stage: str | None, error_code: str,
                    error_message: str, retryable: bool) -> None:
        now = utc_now()
        with self._transaction() as conn:
            if stage in STAGES:
                conn.execute("UPDATE stages SET status='FAILED',completed_at=?,error_code=?,error_message=? WHERE job_id=? AND attempt_no=? AND stage=?",
                             (now, error_code, error_message[:500], job_id, attempt_no, stage))
            conn.execute("UPDATE stages SET status='INTERRUPTED',completed_at=?,error_code=?,error_message=? WHERE job_id=? AND attempt_no=? AND status='RUNNING'",
                         (now, error_code, error_message[:500], job_id, attempt_no))
            conn.execute("UPDATE stages SET status='SKIPPED',completed_at=? WHERE job_id=? AND attempt_no=? AND status='PENDING'",
                         (now, job_id, attempt_no))
            conn.execute("UPDATE attempts SET status='FAILED',completed_at=?,runner_pid=NULL,error_code=?,error_message=? WHERE job_id=? AND attempt_no=?",
                         (now, error_code, error_message[:500], job_id, attempt_no))
            conn.execute("""UPDATE jobs SET status='FAILED',active_stage=COALESCE(?,active_stage),cancel_requested=0,error_code=?,error_message=?,retryable=?,
                updated_at=?,version=version+1 WHERE job_id=? AND status='RUNNING'""",
                (stage if stage in STAGES else None, error_code, error_message[:500], int(retryable), now, job_id))

    def mark_completed(self, job_id: str, attempt_no: int, artifacts: list[dict]) -> bool:
        now = utc_now()
        with self._transaction() as conn:
            job = conn.execute("SELECT cancel_requested FROM jobs WHERE job_id=? AND status='RUNNING'", (job_id,)).fetchone()
            if job is None:
                return False
            if job["cancel_requested"]:
                conn.execute("UPDATE stages SET status='CANCELLED',completed_at=? WHERE job_id=? AND attempt_no=? AND status IN ('RUNNING','PENDING')",
                             (now, job_id, attempt_no))
                conn.execute("UPDATE attempts SET status='CANCELLED',completed_at=?,runner_pid=NULL WHERE job_id=? AND attempt_no=?",
                             (now, job_id, attempt_no))
                conn.execute("UPDATE jobs SET status='CANCELLED',active_stage=NULL,cancel_requested=0,updated_at=?,version=version+1 WHERE job_id=?",
                             (now, job_id))
                return False
            conn.execute("UPDATE stages SET status='SUCCEEDED',completed_at=? WHERE job_id=? AND attempt_no=? AND stage='QUALITY_CHECK'",
                         (now, job_id, attempt_no))
            conn.execute("UPDATE stages SET status='SKIPPED',completed_at=? WHERE job_id=? AND attempt_no=? AND stage IN ('PACKAGE','AUDIO_MIX') AND status IN ('PENDING','RUNNING')",
                         (now, job_id, attempt_no))
            conn.execute("UPDATE attempts SET status='COMPLETED',completed_at=?,runner_pid=NULL WHERE job_id=? AND attempt_no=?",
                         (now, job_id, attempt_no))
            for artifact in artifacts:
                conn.execute("""INSERT OR REPLACE INTO artifacts
                    (job_id,attempt_no,name,relative_path,content_type,sha256,bytes,created_at)
                    VALUES(?,?,?,?,?,?,?,?)""",
                    (job_id, attempt_no, artifact["name"], artifact["relative_path"], artifact["content_type"],
                     artifact["sha256"], artifact["bytes"], now))
            conn.execute("""UPDATE jobs SET status='COMPLETED',active_stage='COMPLETE',cancel_requested=0,
                error_code=NULL,error_message=NULL,retryable=0,updated_at=?,version=version+1 WHERE job_id=?""",
                (now, job_id))
            return True

    def recover_running(self) -> list[dict]:
        """Convert abandoned RUNNING rows into a precise retryable failure on app startup."""
        recovered: list[dict] = []
        now = utc_now()
        with self._transaction() as conn:
            rows = conn.execute("SELECT job_id,attempt_count,active_stage FROM jobs WHERE status='RUNNING'").fetchall()
            for row in rows:
                attempt = conn.execute("SELECT runner_pid FROM attempts WHERE job_id=? AND attempt_no=?",
                                       (row["job_id"], row["attempt_count"])).fetchone()
                recovered.append({"job_id": row["job_id"], "attempt_no": row["attempt_count"],
                                  "runner_pid": attempt["runner_pid"] if attempt else None})
                conn.execute("UPDATE stages SET status='INTERRUPTED',completed_at=?,error_code='WORKER_INTERRUPTED',error_message='服务重启时任务进程已中断' WHERE job_id=? AND attempt_no=? AND status='RUNNING'",
                             (now, row["job_id"], row["attempt_count"]))
                conn.execute("UPDATE stages SET status='SKIPPED',completed_at=? WHERE job_id=? AND attempt_no=? AND status='PENDING'",
                             (now, row["job_id"], row["attempt_count"]))
                conn.execute("UPDATE attempts SET status='FAILED',completed_at=?,runner_pid=NULL,error_code='WORKER_INTERRUPTED',error_message='服务重启时任务进程已中断' WHERE job_id=? AND attempt_no=?",
                             (now, row["job_id"], row["attempt_count"]))
                conn.execute("""UPDATE jobs SET status='FAILED',cancel_requested=0,error_code='WORKER_INTERRUPTED',
                    error_message='服务重启时任务进程已中断，可在任务详情中重试',retryable=1,updated_at=?,version=version+1
                    WHERE job_id=? AND status='RUNNING'""", (now, row["job_id"]))
        return recovered

    def list_jobs(self, limit: int = 20, offset: int = 0, query: str = "", status: str = "",
                  idempotency_key: str = "") -> dict:
        conn = self._connect()
        try:
            clauses, params = [], []
            if idempotency_key:
                clauses.append("idempotency_key=?")
                params.append(idempotency_key)
            if query.strip():
                clauses.append("(instr(lower(title), lower(?)) > 0 OR instr(lower(job_id), lower(?)) > 0)")
                params.extend([query.strip(), query.strip()])
            if status == "NEEDS_INPUT":
                clauses.append("status='FAILED' AND error_code='INPUT_REQUIRES_CLARIFICATION'")
            elif status:
                clauses.append("status=?")
                params.append(status)
                if status == "FAILED":
                    clauses.append("COALESCE(error_code,'') <> 'INPUT_REQUIRES_CLARIFICATION'")
            where = " WHERE " + " AND ".join(clauses) if clauses else ""
            total = conn.execute("SELECT count(*) FROM jobs" + where, params).fetchone()[0]
            rows = conn.execute("SELECT * FROM jobs" + where + " ORDER BY created_at DESC,job_id DESC LIMIT ? OFFSET ?",
                                [*params, limit, offset]).fetchall()
            summary = conn.execute("""SELECT COUNT(*) AS total,
                COALESCE(SUM(status='COMPLETED'),0) AS completed,
                COALESCE(SUM(status IN ('RUNNING','QUEUED')),0) AS running,
                COALESCE(SUM(status='FAILED' AND error_code='INPUT_REQUIRES_CLARIFICATION'),0) AS needs_input
                FROM jobs""").fetchone()
            return {"items": [self._job_view(row, include_history=False) for row in rows], "total": total,
                    "limit": limit, "offset": offset, "summary": dict(summary)}
        finally:
            conn.close()

    def get_job(self, job_id: str) -> dict:
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if row is None:
                raise StoreError("JOB_NOT_FOUND", "job not found")
            return self._job_view(row, include_history=True, conn=conn)
        finally:
            conn.close()

    def _job_view(self, row: sqlite3.Row, include_history: bool, conn: sqlite3.Connection | None = None) -> dict:
        result = {"job_id": row["job_id"], "title": row["title"], "status": row["status"],
                  "active_stage": row["active_stage"], "attempt_count": row["attempt_count"],
                  "cancel_requested": bool(row["cancel_requested"]), "error_code": row["error_code"],
                  "error_message": row["error_message"], "retryable": bool(row["retryable"]),
                  "source_sha256": row["source_sha256"], "source_char_count": row["source_char_count"],
                  "target_duration_sec": row["target_duration_sec"], "theme": row["theme"], "voice_id": row["voice_id"],
                  "created_at": row["created_at"], "updated_at": row["updated_at"], "version": row["version"],
                  "operation_type": row["operation_type"], "parent_job_id": row["parent_job_id"],
                  "parent_attempt_no": row["parent_attempt_no"], "scene_id": row["scene_id"]}
        result["creative_intent"] = json.loads(row["creative_intent_json"])
        result["revision_mode"] = row["revision_mode"]
        result["input_mode"] = row["input_mode"]
        result["idempotency_key"] = row["idempotency_key"]
        result["background_music_resolved"] = json.loads(row["background_music_json"]) if row["background_music_json"] else None
        result["background_music"] = result["background_music_resolved"]["selection"] if result["background_music_resolved"] else {"enabled": False}
        result["production_recipe_resolved"] = json.loads(row["production_recipe_json"]) if row["production_recipe_json"] else None
        result["production_plan"] = json.loads(row["production_plan_json"]) if row["production_plan_json"] else None
        result["production_mode"] = (result["production_plan"] or {}).get("settings", {}).get("production_mode", "standard")
        result["visual_style"] = (result["production_plan"] or {}).get("settings", {}).get("visual_style", "legacy")
        result["video_speed"] = row["video_speed"]
        result["video_form"] = row["video_form"]
        result["duration_mode"] = row["duration_mode"]
        result["input_analysis"] = json.loads(row["input_analysis_json"]) if row["input_analysis_json"] else None
        result["resolved_video_form"] = result["input_analysis"]["selected_form"] if result["input_analysis"] else (None if row["video_form"] == "auto" else row["video_form"])
        if not include_history:
            return result
        if conn is None:
            raise RuntimeError("connection required for job history")
        attempts = conn.execute("SELECT * FROM attempts WHERE job_id=? ORDER BY attempt_no DESC", (row["job_id"],)).fetchall()
        result["attempts"] = []
        for attempt in attempts:
            item = {"attempt_no": attempt["attempt_no"], "status": attempt["status"],
                    "started_at": attempt["started_at"], "completed_at": attempt["completed_at"],
                    "error_code": attempt["error_code"], "error_message": attempt["error_message"]}
            stages = conn.execute("SELECT * FROM stages WHERE job_id=? AND attempt_no=? ORDER BY rowid",
                                  (row["job_id"], attempt["attempt_no"])).fetchall()
            item["stages"] = [{"stage": s["stage"], "status": s["status"],
                                "started_at": s["started_at"], "completed_at": s["completed_at"],
                                "duration_seconds": s["duration_seconds"], "error_code": s["error_code"],
                                "error_message": s["error_message"]} for s in stages]
            result["attempts"].append(item)
        return result

    def source_path(self, job_id: str) -> Path:
        UUID(job_id)
        conn = self._connect()
        try:
            row = conn.execute("SELECT source_rel_path FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if row is None:
                raise StoreError("JOB_NOT_FOUND", "job not found")
            path = (self.data_dir / row["source_rel_path"]).resolve(strict=True)
            if not path.is_relative_to((self.job_root / job_id).resolve()):
                raise StoreError("INVALID_WORKSPACE", "source path escaped job workspace")
            return path
        finally:
            conn.close()

    def add_artifact(self, job_id: str, attempt_no: int, artifact: dict) -> None:
        with self._transaction() as conn:
            conn.execute("""INSERT OR REPLACE INTO artifacts
                (job_id,attempt_no,name,relative_path,content_type,sha256,bytes,created_at)
                VALUES(?,?,?,?,?,?,?,?)""",
                (job_id, attempt_no, artifact["name"], artifact["relative_path"], artifact["content_type"],
                 artifact["sha256"], artifact["bytes"], utc_now()))

    def get_artifacts(self, job_id: str) -> list[dict]:
        conn = self._connect()
        try:
            row = conn.execute("SELECT status,attempt_count FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if row is None:
                raise StoreError("JOB_NOT_FOUND", "job not found")
            if row["status"] != "COMPLETED":
                return []
            rows = conn.execute("SELECT * FROM artifacts WHERE job_id=? AND attempt_no=? ORDER BY name",
                                (job_id, row["attempt_count"])).fetchall()
            return [{"name": item["name"], "content_type": item["content_type"], "bytes": item["bytes"],
                     "sha256": item["sha256"], "download_url": f"/api/jobs/{job_id}/artifacts/{item['name']}"}
                    for item in rows]
        finally:
            conn.close()

    def artifact_path(self, job_id: str, name: str) -> tuple[Path, dict]:
        UUID(job_id)
        conn = self._connect()
        try:
            job = conn.execute("SELECT status,attempt_count FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if job is None:
                raise StoreError("JOB_NOT_FOUND", "job not found")
            if job["status"] != "COMPLETED":
                raise StoreError("ARTIFACT_NOT_READY", "verified artifacts are only available for completed jobs")
            item = conn.execute("SELECT * FROM artifacts WHERE job_id=? AND attempt_no=? AND name=?",
                                (job_id, job["attempt_count"], name)).fetchone()
            if item is None:
                raise StoreError("ARTIFACT_NOT_FOUND", "artifact not found")
            try:
                workspace = (self.job_root / job_id / "attempts" / str(job["attempt_count"])).resolve(strict=True)
            except FileNotFoundError as exc:
                raise StoreError("ARTIFACT_ARCHIVED", "the job folder was moved off this disk; restore it to download") from exc
            relative = Path(item["relative_path"])
            if relative.is_absolute() or ".." in relative.parts or not relative.parts:
                raise StoreError("INVALID_ARTIFACT_PATH", "artifact path is outside the job attempt workspace")
            candidate = workspace
            for part in relative.parts:
                candidate = candidate / part
                if candidate.is_symlink():
                    raise StoreError("INVALID_ARTIFACT_PATH", "artifact path contains a symbolic link")
            path = candidate.resolve(strict=True)
            if not path.is_relative_to(workspace) or not path.is_file():
                raise StoreError("INVALID_ARTIFACT_PATH", "artifact path is outside the job attempt workspace")
            if artifact_digest(path) != item["sha256"]:
                raise StoreError("ARTIFACT_HASH_MISMATCH", "artifact integrity check failed")
            return path, dict(item)
        finally:
            conn.close()

    def _scene_image_records(self, job_id: str) -> list[dict]:
        UUID(job_id)
        conn = self._connect()
        try:
            job = conn.execute("SELECT status,attempt_count FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            if job is None:
                raise StoreError("JOB_NOT_FOUND", "job not found")
            if job["status"] != "COMPLETED":
                return []
            attempt_root = self.job_root / job_id / "attempts" / str(job["attempt_count"])
        finally:
            conn.close()

        try:
            resolved_root = attempt_root.resolve(strict=True)
            if not resolved_root.is_relative_to((self.job_root / job_id).resolve()):
                raise StoreError("INVALID_SCENE_IMAGE_PATH", "scene images are outside the job workspace")
            manifest_path = resolved_root / "g2-run" / "artifacts" / "scene-backgrounds.json"
            try:
                verified_manifest_path, manifest_artifact = self.artifact_path(job_id, "scene-backgrounds.json")
            except StoreError as exc:
                if exc.code == "ARTIFACT_NOT_FOUND":
                    return []
                raise
            if (manifest_artifact["relative_path"] != "g2-run/artifacts/scene-backgrounds.json"
                    or verified_manifest_path != manifest_path.resolve(strict=True)):
                raise StoreError("INVALID_SCENE_IMAGE_PATH", "scene image manifest is outside the expected artifact path")
            if manifest_path.is_symlink() or not manifest_path.is_file():
                raise StoreError("SCENE_IMAGE_MANIFEST_INVALID", "verified scene image manifest is unavailable")
            if not manifest_path.resolve(strict=True).is_relative_to(resolved_root):
                raise StoreError("INVALID_SCENE_IMAGE_PATH", "scene image manifest is outside the job workspace")
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except StoreError:
            raise
        except (OSError, ValueError) as exc:
            raise StoreError("SCENE_IMAGE_MANIFEST_INVALID", "scene image manifest is unavailable or invalid") from exc

        images = manifest.get("images") if isinstance(manifest, dict) else None
        if not isinstance(images, list) or len(images) != 3:
            raise StoreError("SCENE_IMAGE_MANIFEST_INVALID", "scene image manifest must contain exactly three images")
        records = []
        scene_ids = set()
        for index, item in enumerate(images, start=1):
            if not isinstance(item, dict):
                raise StoreError("SCENE_IMAGE_MANIFEST_INVALID", "scene image record is invalid")
            scene_id = item.get("scene_id")
            expected_relpath = f"assets/scene-backgrounds/scene-{index:02d}.png"
            if (not isinstance(scene_id, str) or not SCENE_ID_PATTERN.fullmatch(scene_id)
                    or scene_id in scene_ids or item.get("path") != expected_relpath):
                raise StoreError("SCENE_IMAGE_MANIFEST_INVALID", "scene image mapping is invalid")
            scene_ids.add(scene_id)
            candidate = resolved_root / "g2-run" / "fixtures" / expected_relpath
            try:
                path = candidate.resolve(strict=True)
                raw = path.read_bytes()
            except OSError as exc:
                raise StoreError("SCENE_IMAGE_NOT_FOUND", "scene image is missing") from exc
            if (candidate.is_symlink() or not path.is_relative_to(resolved_root)
                    or len(raw) < 24 or raw[:8] != b"\x89PNG\r\n\x1a\n" or raw[12:16] != b"IHDR"):
                raise StoreError("INVALID_SCENE_IMAGE_PATH", "scene image is not a valid task-local PNG")
            digest = hashlib.sha256(raw).hexdigest()
            if digest != item.get("sha256"):
                raise StoreError("SCENE_IMAGE_HASH_MISMATCH", "scene image integrity check failed")
            width, height = struct.unpack(">II", raw[16:24])
            if not (1 <= width <= 4096 and 1 <= height <= 4096):
                raise StoreError("SCENE_IMAGE_INVALID", "scene image dimensions are invalid")
            records.append({"scene_id": scene_id, "path": path, "bytes": len(raw),
                            "width": width, "height": height, "sha256": digest})
        return records

    def get_scene_images(self, job_id: str) -> list[dict]:
        return [{"scene_id": item["scene_id"], "bytes": item["bytes"],
                 "width": item["width"], "height": item["height"], "sha256": item["sha256"],
                 "image_url": f"/api/jobs/{job_id}/scene-images/{item['scene_id']}"}
                for item in self._scene_image_records(job_id)]

    def scene_image_path(self, job_id: str, scene_id: str) -> Path:
        if not SCENE_ID_PATTERN.fullmatch(scene_id):
            raise StoreError("SCENE_IMAGE_NOT_FOUND", "scene image not found")
        for item in self._scene_image_records(job_id):
            if item["scene_id"] == scene_id:
                return item["path"]
        raise StoreError("SCENE_IMAGE_NOT_FOUND", "scene image not found")
