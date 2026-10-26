from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import signal
import subprocess
import threading
import time
from typing import BinaryIO

from g3.config import Settings
from g3.store import JobStore, STAGES

logger = logging.getLogger(__name__)


STEP_TO_STAGE = {
    "narration-plan": "PLAN",
    "tts-and-align": "VOICE_ALIGN",
    "storyboard-plan": "STORYBOARD",
    "draw-things-backgrounds": "SCENE_IMAGES",
    "compile-ir": "BUILD_IR",
    "render": "RENDER",
    "verify-media": "QUALITY_CHECK",
    "package": "PACKAGE",
    "mix-background-music": "AUDIO_MIX",
}
PACKAGE_FINAL_ERRORS = {"PACKAGE_CONFIG_INVALID", "PACKAGE_CONTENT_MISSING", "PACKAGE_UNVERIFIED", "PACKAGE_FORMAT_MISMATCH", "PACKAGING_SOURCE_CONFLICT"}
STEP_LINE = re.compile(r"^STEP ([a-z0-9-]+): (START|PASS|FAIL|SKIPPED)(?: in ([0-9.]+)s)?")
ERROR_LINE = re.compile(r"^G2 FAIL: ([A-Z0-9_]+)(?::\s*(.*))?$")


def read_progress_lines(stream: BinaryIO, pending: bytes, *, final: bool = False) -> tuple[list[str], bytes]:
    """Read appended bytes once; keep incomplete UTF-8 and lines until complete."""
    parts = (pending + stream.read()).split(b"\n")
    pending = parts.pop()
    if final and pending:
        parts.append(pending)
        pending = b""
    return [line.decode("utf-8", errors="replace") for line in parts], pending


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_artifact(path: Path, attempt_root: Path, name: str, content_type: str) -> dict | None:
    if not path.is_file() or path.is_symlink():
        return None
    resolved_root = attempt_root.resolve(strict=True)
    resolved_file = path.resolve(strict=True)
    if not resolved_file.is_relative_to(resolved_root):
        return None
    return {"name": name, "relative_path": resolved_file.relative_to(resolved_root).as_posix(),
            "content_type": content_type, "sha256": _sha256(resolved_file),
            "bytes": resolved_file.stat().st_size}


class G2ProcessRunner:
    def __init__(self, settings: Settings, store: JobStore):
        self.settings = settings
        self.store = store

    def reap_orphan(self, pid: int | None) -> bool:
        if not pid or pid <= 1:
            return False
        try:
            inspected = subprocess.run(["ps", "-p", str(pid), "-o", "command="],
                                       stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                       text=True, timeout=2, check=False)
            command = inspected.stdout.strip()
            expected = str(self.settings.g2_script.resolve())
            if (inspected.returncode != 0 or expected not in command
                    or not any(f" {mode}" in command for mode in ("all", "scene-edit", "revision", "music-revision"))):
                return False
            os.killpg(pid, signal.SIGTERM)
            return True
        except (OSError, subprocess.SubprocessError):
            return False

    def _stop_process(self, process: subprocess.Popen, grace_seconds: float = 5) -> None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            process.wait(timeout=grace_seconds)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=2)

    def execute(self, job: dict, stop_event: threading.Event) -> None:
        job_id = job["job_id"]
        attempt_no = job["attempt_no"]
        attempt_root = (self.settings.data_dir / job["workspace_rel_path"]).resolve()
        if not attempt_root.is_relative_to((self.store.job_root / job_id).resolve()):
            self.store.mark_failed(job_id, attempt_no, "PLAN", "INVALID_WORKSPACE",
                                   "attempt workspace is outside the job directory", False)
            return
        attempt_root.mkdir(parents=True, exist_ok=False)
        run_dir = attempt_root / "g2-run"
        run_id = f"job_{job_id.replace('-', '')}_a{attempt_no}"
        env = os.environ.copy()
        env["AUTOVIDEO_LLM_SETTINGS_FILE"] = str(self.settings.llm_settings_file)
        env["AUTOVIDEO_G2_SOURCE_FILE"] = job["source_abs_path"]
        env["AUTOVIDEO_G2_RUN_ID"] = run_id
        env["AUTOVIDEO_G2_RUN_DIR"] = str(run_dir)
        env["AUTOVIDEO_G2_TARGET_DURATION_SEC"] = str(job["target_duration_sec"])
        env["AUTOVIDEO_G2_DURATION_MODE"] = job.get("duration_mode", "fixed")
        env.pop("AUTOVIDEO_G2_PREVIOUS_RUN_DIR", None)
        if job.get("duration_mode") == "content" and attempt_no > 1:
            previous = attempt_root.parent / str(attempt_no - 1) / "g2-run"
            if previous.is_dir() and previous.resolve().is_relative_to((self.store.job_root / job_id).resolve()):
                env["AUTOVIDEO_G2_PREVIOUS_RUN_DIR"] = str(previous)
        operation_type = job.get("operation_type", "generate")
        command_mode = "all"
        if operation_type == "scene_edit":
            try:
                source = self.store.scene_edit_source(job["parent_job_id"], job["parent_attempt_no"], job["scene_id"])
            except Exception as exc:
                self.store.mark_failed(job_id, attempt_no, "SCENE_IMAGES", getattr(exc, "code", "SCENE_EDIT_SOURCE_UNAVAILABLE"),
                                       str(exc)[:400], False)
                return
            command_mode = "scene-edit"
            env["AUTOVIDEO_G2_SOURCE_ATTEMPT_DIR"] = str(source["attempt_root"])
            env["AUTOVIDEO_G2_PARENT_JOB_ID"] = job["parent_job_id"]
            env["AUTOVIDEO_G2_SCENE_ID"] = job["scene_id"]
            env["AUTOVIDEO_G2_SCENE_GUIDANCE"] = job.get("prompt_extra") or ""
        elif operation_type == "music_revision":
            try:
                from g3.revision_support import source
                _, _, parent_root = source(self.store, job["parent_job_id"], allow_legacy=True)
                env["AUTOVIDEO_G2_SOURCE_ATTEMPT_DIR"] = str(parent_root)
                command_mode = "music-revision"
            except Exception as exc:
                self.store.mark_failed(job_id, attempt_no, "PLAN", getattr(exc, "code", "MUSIC_SOURCE_INVALID"), str(exc)[:400], False)
                return
        elif operation_type in {"visual_revision","media_revision"}:
            try:
                from g3.revision_support import source
                _,_,parent_root=source(self.store,job["parent_job_id"])
                env["AUTOVIDEO_G2_SOURCE_ATTEMPT_DIR"]=str(parent_root)
                command_mode="revision"
                env["AUTOVIDEO_G2_REUSE_NARRATION"]="1"
            except Exception as exc:
                self.store.mark_failed(job_id,attempt_no,"PLAN",getattr(exc,"code","REVISION_SOURCE_INVALID"),str(exc)[:400],False)
                return
        elif operation_type not in {"generate", "content_revision"}:
            self.store.mark_failed(job_id, attempt_no, "PLAN", "UNKNOWN_JOB_OPERATION",
                                   "任务操作类型不受支持", False)
            return
        input_path = Path(job["source_abs_path"]).parent / "input.json"
        try:
            input_payload = json.loads(input_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            input_payload = {}
        try:
            from g2.music_library import frozen_input
            frozen_input(input_path.parent, input_payload)
        except ValueError as exc:
            self.store.mark_failed(job_id, attempt_no, "AUDIO_MIX", "MUSIC_INPUT_INVALID", str(exc), False)
            return
        if input_payload.get("production_recipe") or input_payload.get("production_recipe_resolved"):
            from g2.creative_library import CreativeResourceError, prepare_request
            from g2.package_kit import PackageConfigError
            try:
                if not input_payload.get("production_recipe_resolved"):
                    raise CreativeResourceError("制作配方缺少已保存的版本快照")
                verified = prepare_request(input_payload)
                if verified["production_recipe_resolved"] != input_payload["production_recipe_resolved"]:
                    raise CreativeResourceError("任务设置与已保存的制作配方快照不一致")
            except (CreativeResourceError, PackageConfigError) as exc:
                self.store.mark_failed(job_id, attempt_no, "PLAN", "PRODUCTION_RECIPE_INVALID", str(exc), False)
                return
            (attempt_root / "production-recipe.json").write_text(
                json.dumps(input_payload["production_recipe_resolved"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if input_payload.get("production_plan") is not None or job.get("production_plan_json") is not None:
            from g2.production_plan import verify, ProductionPlanError
            try:
                plan = verify({**input_payload, "source_sha256": job["source_sha256"]})
                if plan != json.loads(job.get("production_plan_json") or "null"):
                    raise ProductionPlanError("任务与保存的制作计划不一致")
            except ProductionPlanError as exc:
                self.store.mark_failed(job_id, attempt_no, "PLAN", "PRODUCTION_PLAN_INVALID", str(exc), False)
                return
            (attempt_root / "production-plan.json").write_text(
                json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        env["AUTOVIDEO_G2_VOICE_ID"] = job["voice_id"]
        env["AUTOVIDEO_G2_VIDEO_SPEED"] = str(job.get("video_speed",1))
        env["AUTOVIDEO_G2_INTENT_FILE"] = str(input_path)
        locked = input_payload.get("revision_mode") == "narration" or input_payload.get("input_mode") == "narration"
        env["AUTOVIDEO_G2_LOCK_NARRATION"] = "1" if locked else "0"
        env["AUTOVIDEO_G2_USE_DRAW_THINGS"] = "1" if input_payload.get("use_draw_things") is True else "0"
        env["AUTOVIDEO_G2_VIDEO_FORM"] = job.get("video_form", "knowledge_cards")
        log_path = attempt_root / "worker.log"
        active_stage = "PLAN"
        running_stages = {active_stage}
        explicit_stage_starts = False
        failed_stage = None
        pending_log = b""
        started = time.monotonic()
        timeout_seconds = self.settings.runner_timeout_seconds + (300 if input_payload.get("package") else 0)
        workload_timeout_loaded = False
        input_analysis_loaded = False
        error_code = None
        error_message = None
        retryable = True
        process = None

        try:
            with log_path.open("xb") as log_stream, log_path.open("rb") as progress_stream:
                process = subprocess.Popen(
                    [str(self.settings.g2_python), "-u", str(self.settings.g2_script), command_mode],
                    cwd=self.settings.project_root, env=env, stdout=log_stream,
                    stderr=subprocess.STDOUT, shell=False, start_new_session=True,
                )
                self.store.set_runner_pid(job_id, attempt_no, process.pid)
                self.store.update_stage(job_id, attempt_no, active_stage, "RUNNING")
                while True:
                    if not input_analysis_loaded:
                        try:
                            brief = json.loads((run_dir / "artifacts/input-brief.json").read_text())
                            self.store.record_input_analysis(job_id, attempt_no, brief)
                            input_analysis_loaded = True
                        except (OSError, ValueError, KeyError):
                            pass
                    is_cancelled = self.store.cancel_requested(job_id)
                    is_stopping = stop_event.is_set()
                    if is_cancelled or is_stopping:
                        self._stop_process(process)
                        process.wait()
                        if is_cancelled:
                            self.store.mark_cancelled(job_id, attempt_no)
                        else:
                            self.store.mark_failed(job_id, attempt_no, active_stage, "WORKER_INTERRUPTED",
                                                   "服务停止时任务进程被中断，可以重启后重试", True)
                        return
                    if job.get("duration_mode") == "content" and not workload_timeout_loaded:
                        try:
                            measured = json.loads((run_dir / "artifacts/alignment.json").read_text())
                            # Preserve the startup/model allowance and add time for measured render work.
                            timeout_seconds += max(0, float(measured["duration_seconds"])) * 6
                            workload_timeout_loaded = True
                        except (OSError, ValueError, KeyError, TypeError):
                            pass
                    if time.monotonic() - started > timeout_seconds:
                        self._stop_process(process)
                        error_code, error_message = "GENERATION_TIMEOUT", f"生成任务超过本次执行时限（{round(timeout_seconds / 60)} 分钟）"
                        break
                    # Poll before reading: an exited process has flushed its last
                    # lines, so parallel completion/failure records cannot be lost.
                    return_code = process.poll()
                    current_lines, pending_log = read_progress_lines(progress_stream, pending_log,
                                                                    final=return_code is not None)
                    for line in current_lines:
                        match = STEP_LINE.match(line.strip())
                        if match:
                            step, result, elapsed = match.groups()
                            stage = STEP_TO_STAGE.get(step)
                            if stage:
                                if result == "START":
                                    explicit_stage_starts = True
                                    running_stages.add(stage)
                                    self.store.update_stage(job_id, attempt_no, stage, "RUNNING")
                                elif result in {"PASS", "SKIPPED"}:
                                    running_stages.discard(stage)
                                    stage_status = "SUCCEEDED" if result == "PASS" else "SKIPPED"
                                    self.store.update_stage(job_id, attempt_no, stage, stage_status,
                                                            float(elapsed) if elapsed else None)
                                    index = STAGES.index(stage)
                                    if not explicit_stage_starts and index + 1 < len(STAGES):
                                        active_stage = STAGES[index + 1]
                                        running_stages.add(active_stage)
                                        self.store.update_stage(job_id, attempt_no, active_stage, "RUNNING")
                                else:
                                    running_stages.discard(stage)
                                    failed_stage = failed_stage or stage
                                    self.store.update_stage(job_id, attempt_no, stage, "FAILED")
                                if failed_stage:
                                    active_stage = failed_stage
                                elif running_stages:
                                    active_stage = min(running_stages, key=STAGES.index)
                        error_match = ERROR_LINE.match(line.strip())
                        if error_match:
                            error_code = error_match.group(1)
                            error_message = (error_match.group(2) or "生成流程失败").strip()[:400]
                    if return_code is not None:
                        break
                    stop_event.wait(0.35)
                self.store.set_runner_pid(job_id, attempt_no, None)
                return_code = process.poll()
        except Exception as exc:
            if process is not None and process.poll() is None:
                self._stop_process(process)
            self.store.set_runner_pid(job_id, attempt_no, None)
            error_code = "WORKER_LAUNCH_FAILED"
            error_message = f"{type(exc).__name__}: {str(exc)[:300]}"
            return_code = -1

        if self.store.cancel_requested(job_id):
            self.store.mark_cancelled(job_id, attempt_no)
            return
        if return_code != 0:
            error_code = error_code or "GENERATION_FAILED"
            if error_code in {"DEEPSEEK_API_KEY_MISSING", "DEEPSEEK_AUTH_FAILED", "LLM_API_KEY_MISSING", "LLM_AUTH_FAILED", "LLM_PROVIDER_INVALID", "RENDER_CONFIG_INVALID", "UNSUPPORTED_OUTPUT", "DIAGRAM_CONTENT_UNSUPPORTED", "CONTENT_FORM_UNSUPPORTED", "INPUT_REQUIRES_CLARIFICATION", "NARRATION_TEXT_MISMATCH"} | PACKAGE_FINAL_ERRORS:
                retryable = False
            self.store.mark_failed(job_id, attempt_no, active_stage, error_code,
                                   error_message or "视频生成失败，请查看任务阶段错误并重试", retryable)
            self._write_failure_report(attempt_root, error_code, error_message or "生成流程失败", active_stage)
            return

        qa_path = run_dir / "artifacts/qa.json"
        try:
            qa = json.loads(qa_path.read_text(encoding="utf-8"))
            if qa.get("status") != "PASS":
                raise ValueError("G2 QA result was not PASS")
        except Exception as exc:
            self.store.mark_failed(job_id, attempt_no, "QUALITY_CHECK", "UNVERIFIED_OUTPUT",
                                   f"成片缺少通过验收的 QA 报告：{type(exc).__name__}", False)
            self._write_failure_report(attempt_root, "UNVERIFIED_OUTPUT", "成片未通过机械验收", "QUALITY_CHECK")
            return

        artifacts_dir = run_dir / "artifacts"
        package_dir = artifacts_dir / "package"
        packaged = bool(input_payload.get("package"))
        if packaged:
            try:
                package_qa = json.loads((package_dir / "package-qa.json").read_text(encoding="utf-8"))
                if package_qa.get("status") != "PASS" or not (package_dir / "video.mp4").is_file():
                    raise ValueError("package QA was not PASS")
            except Exception as exc:
                self.store.mark_failed(job_id, attempt_no, "PACKAGE", "PACKAGE_UNVERIFIED",
                                       f"打包后的成片缺少通过的检查报告：{type(exc).__name__}", False)
                self._write_failure_report(attempt_root, "PACKAGE_UNVERIFIED", "打包成片未通过检查", "PACKAGE")
                return
        video_name = "G2_自动生成.mp4" if (artifacts_dir / "G2_自动生成.mp4").is_file() else "G2_CaseA_通信噪声.mp4"
        artifact_specs = [
            (attempt_root / "production-recipe.json", "production-recipe.json", "application/json"),
            (attempt_root / "production-plan.json", "production-plan.json", "application/json"),
            (artifacts_dir / "fast-card-plan.json", "fast-card-plan.json", "application/json"),
            (artifacts_dir / "card-editorial.json", "card-editorial.json", "application/json"),
            (artifacts_dir / "caption-alignment.json", "caption-alignment.json", "application/json"),
            (artifacts_dir / "production-execution.json", "production-execution.json", "application/json"),
            (artifacts_dir / "tts-metrics.json", "tts-metrics.json", "application/json"),
            (artifacts_dir / "voice-report.json", "voice-report.json", "application/json"),
            (artifacts_dir / video_name, "video.mp4", "video/mp4"),
            (artifacts_dir / "captions.srt", "captions.srt", "application/x-subrip"),
            (qa_path, "qa.json", "application/json"),
            (artifacts_dir / "manifest.json", "manifest.json", "application/json"),
            (artifacts_dir / "storyboard-plan.json", "storyboard.json", "application/json"),
            (artifacts_dir / "scene-backgrounds.json", "scene-backgrounds.json", "application/json"),
            (artifacts_dir / "scene-edit.json", "scene-edit.json", "application/json"),
            (run_dir / "fixtures/video-ir-v0.2.json", "video-ir.json", "application/json"),
            (artifacts_dir / "model-cost.json", "model-cost.json", "application/json"),
            (artifacts_dir / "reused-plans.json", "reused-plans.json", "application/json"),
            (artifacts_dir / "chapters.json", "chapters.json", "application/json"),
            (artifacts_dir / "content-outline-plan.json", "content-outline.json", "application/json"),
            (artifacts_dir / "final-narration-plan.json", "narration.json", "application/json"),
            (artifacts_dir / "authored-narration.json", "authored-narration.json", "application/json"),
            (artifacts_dir / "packaging-resolved.json", "packaging-resolved.json", "application/json"),
            (artifacts_dir / "render-resources.json", "render-resources.json", "application/json"),
            (artifacts_dir / "generation-schedule.json", "generation-schedule.json", "application/json"),
            (artifacts_dir / "narration-check.json", "narration-check.json", "application/json"),
            (artifacts_dir / "scene-revision.json", "scene-revision.json", "application/json"),
            (artifacts_dir / "media-revision.json", "media-revision.json", "application/json"),
            (artifacts_dir / "playback.json", "playback.json", "application/json"),
            (artifacts_dir / "input-brief.json", "input-brief.json", "application/json"),
            (artifacts_dir / "prepared-source.txt", "prepared-source.txt", "text/plain; charset=utf-8"),
            (artifacts_dir / "creative-form-plan.json", "creative-form-plan.json", "application/json"),
            (artifacts_dir / "slideshow-images.json", "slideshow-images.json", "application/json"),
            (artifacts_dir / "information-plan.json", "information-plan.json", "application/json"),
            (artifacts_dir / "information-timing.json", "information-timing.json", "application/json"),
            (artifacts_dir / "diagram-plan.json", "diagram-plan.json", "application/json"),
            (artifacts_dir / "diagram-layout.json", "diagram-layout.json", "application/json"),
            (artifacts_dir / "diagram-timing.json", "diagram-timing.json", "application/json"),
            (log_path, "execution-log.txt", "text/plain; charset=utf-8"),
        ]
        if packaged:
            # video.mp4 / captions.srt / chapters.json describe the packaged file; the content-only
            # versions keep their own names. Everything that reads the content timeline uses content.mp4.
            replaced = {"video.mp4": "content.mp4", "captions.srt": "content-captions.srt",
                        "chapters.json": "content-chapters.json"}
            artifact_specs = [(path, replaced.get(name, name), ctype) for path, name, ctype in artifact_specs]
            artifact_specs += [
                (package_dir / "video.mp4", "video.mp4", "video/mp4"),
                (package_dir / "captions.srt", "captions.srt", "application/x-subrip"),
                (package_dir / "chapters.json", "chapters.json", "application/json"),
                (package_dir / "package.json", "package.json", "application/json"),
                (package_dir / "package-qa.json", "package-qa.json", "application/json"),
                (package_dir / "contact-sheet.jpg", "package-contact-sheet.jpg", "image/jpeg"),
            ]
        mixed = bool((input_payload.get("background_music") or {}).get("enabled")) or operation_type == "music_revision"
        if mixed:
            mix_dir = artifacts_dir / "mix"
            try:
                mix_qa = json.loads((mix_dir / "mix-qa.json").read_text())
                if mix_qa.get("status") != "PASS" or not (mix_dir / "video.mp4").is_file():
                    raise ValueError("mix QA missing")
            except Exception:
                self.store.mark_failed(job_id, attempt_no, "AUDIO_MIX", "MIX_UNVERIFIED", "混音缺少通过的验收报告", False)
                return
            artifact_specs = [(path, "unmixed.mp4" if name == "video.mp4" else name, kind) for path, name, kind in artifact_specs]
            artifact_specs += [(mix_dir / "video.mp4", "video.mp4", "video/mp4"),
                               (mix_dir / "music-mix.json", "music-mix.json", "application/json"),
                               (mix_dir / "mix-qa.json", "mix-qa.json", "application/json"),
                               (mix_dir / "mixed-audio.wav", "mixed-audio.wav", "audio/wav"),
                               (mix_dir / "music-bed.wav", "music-bed.wav", "audio/wav")]
        artifacts = []
        for path, name, content_type in artifact_specs:
            item = safe_artifact(path, attempt_root, name, content_type)
            if item:
                artifacts.append(item)
        if not any(item["name"] == "video.mp4" for item in artifacts):
            self.store.mark_failed(job_id, attempt_no, "QUALITY_CHECK", "OUTPUT_MISSING",
                                   "视频文件缺失，不能将任务标记为完成", False)
            return
        self.store.mark_completed(job_id, attempt_no, artifacts)

    @staticmethod
    def _write_failure_report(attempt_root: Path, code: str, message: str, stage: str) -> None:
        payload = {"status": "FAILED", "error_code": code, "message": message[:400], "stage": stage}
        target = attempt_root / "failure-report.json"
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, target)


class SingleWorker:
    def __init__(self, settings: Settings, store: JobStore, processor: G2ProcessRunner, *, idle_task=None, on_start=None):
        self.settings = settings
        self.store = store
        self.processor = processor
        self.idle_task = idle_task
        self.on_start = on_start
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self._lock_file = None

    def start(self) -> bool:
        lock_path = self.settings.data_dir / "worker.lock"
        self._lock_file = lock_path.open("a+")
        try:
            fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self._lock_file.close()
            self._lock_file = None
            return False
        if self.on_start:
            self.on_start()
        self.thread = threading.Thread(target=self._loop, name="autovideo-single-worker", daemon=True)
        self.thread.start()
        return True

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=12)
            if self.thread.is_alive():
                logger.warning("Video worker is still stopping; retaining its exclusive lock")
                return
        if self._lock_file:
            try:
                fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_UN)
            finally:
                self._lock_file.close()
                self._lock_file = None

    def _loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                job = self.store.claim_next()
            except Exception:
                # A temporary SQLite lock or filesystem outage must not kill
                # the sole queue consumer for the rest of the app's lifetime.
                logger.exception("Could not read video queue; retrying")
                self.stop_event.wait(max(1.0, self.settings.worker_poll_seconds))
                continue
            if job is None:
                try:
                    if self.idle_task and self.idle_task(self.stop_event):
                        continue
                except Exception:
                    logger.exception("Video worker idle task failed; queue remains active")
                self.stop_event.wait(self.settings.worker_poll_seconds)
                continue
            try:
                self.processor.execute(job, self.stop_event)
            except Exception as exc:
                self.store.mark_failed(job["job_id"], job["attempt_no"], job.get("active_stage") or "PLAN",
                                       "WORKER_UNEXPECTED_ERROR", f"{type(exc).__name__}: {str(exc)[:300]}", True)
