from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
import re
from typing import Literal
from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.middleware.trustedhost import TrustedHostMiddleware

from g3.config import Settings, probe_config
from g3.store import JobStore, StoreError
from g3.worker import G2ProcessRunner, SingleWorker


WEB_ROOT = Path(__file__).resolve().parent / "web"
ARTIFACT_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,40}\.(?:mp4|srt|json|txt)$")


class CreateJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str | None = Field(default=None, min_length=2, max_length=80)
    source_text: str = Field(min_length=50, max_length=2000)
    target_duration_sec: int = Field(default=18, ge=10, le=20)
    theme: Literal["minimal_dark"] = "minimal_dark"
    voice_id: Literal["zf_xiaoxiao"] = "zf_xiaoxiao"
    use_draw_things: bool = False

    @field_validator("source_text")
    @classmethod
    def validate_source_text(cls, value: str) -> str:
        value = value.strip()
        if len(value) < 50:
            raise ValueError("source_text must contain at least 50 non-whitespace characters")
        if "\x00" in value:
            raise ValueError("source_text cannot contain NUL characters")
        return value


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


def create_app(settings: Settings | None = None, *, start_worker: bool = True, preflight=probe_config) -> FastAPI:
    settings = settings or Settings()
    store = JobStore(settings.data_dir)
    processor = G2ProcessRunner(settings, store)
    worker = SingleWorker(settings, store, processor)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        store.initialize()
        for abandoned in store.recover_running():
            processor.reap_orphan(abandoned.get("runner_pid"))
        if start_worker:
            application.state.worker_primary = worker.start()
        else:
            application.state.worker_primary = False
        yield
        if start_worker:
            worker.stop()

    application = FastAPI(title="AutoVideo Local", version="0.1.0", lifespan=lifespan)
    application.state.settings = settings
    application.state.store = store
    application.state.preflight = preflight
    application.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "testserver"])
    application.mount("/static", StaticFiles(directory=WEB_ROOT / "static"), name="static")

    @application.get("/", response_class=HTMLResponse)
    def index():
        page = WEB_ROOT / "static/index.html"
        if not page.is_file():
            return HTMLResponse("G3 UI bundle missing; run the documented build command.", status_code=503)
        return HTMLResponse(page.read_text(encoding="utf-8"))

    @application.get("/api/health")
    def health():
        return {"status": "ok", "worker_primary": bool(application.state.worker_primary)}

    @application.get("/api/config/status")
    def config_status():
        status = preflight(settings)
        return status

    @application.post("/api/jobs", status_code=202)
    def create_job(request: CreateJobRequest):
        readiness = preflight(settings)
        if not readiness.get("ready"):
            raise _error(503, "CONFIG_NOT_READY", "服务配置未完成；请检查 DeepSeek key、Kokoro 与渲染依赖。")
        if request.use_draw_things and not readiness.get("checks", {}).get("draw_things"):
            raise _error(503, "DRAW_THINGS_NOT_READY",
                         "Draw Things 本地 HTTP API 尚未连接；请在 Draw Things「设置 → 高级」开启 HTTP API 服务器后重试。")
        job_id = str(uuid4())
        title = request.title or "".join(request.source_text.split())[:28] or "未命名视频"
        payload = request.model_dump()
        payload["title"] = title
        try:
            job = store.create_job(job_id, payload)
        except (OSError, StoreError) as exc:
            raise _error(500, getattr(exc, "code", "WORKSPACE_CREATE_FAILED"), "无法创建安全的任务工作区。") from exc
        return {"job_id": job_id, "status": job["status"], "created_at": job["created_at"],
                "status_url": f"/api/jobs/{job_id}"}

    @application.get("/api/jobs")
    def list_jobs(limit: int = Query(default=20, ge=1, le=50), offset: int = Query(default=0, ge=0)):
        return store.list_jobs(limit=limit, offset=offset)

    @application.get("/api/jobs/{job_id}")
    def get_job(job_id: str):
        try:
            UUID(job_id)
            return store.get_job(job_id)
        except ValueError as exc:
            raise _error(404, "JOB_NOT_FOUND", "任务不存在。") from exc
        except StoreError as exc:
            raise _error(404, exc.code, str(exc)) from exc

    @application.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: str):
        try:
            UUID(job_id)
            job = store.get_job(job_id)
            if job["status"] not in {"QUEUED", "RUNNING"}:
                raise _error(409, "INVALID_STATE", "只有等待或运行中的任务可以取消。")
            return store.request_cancel(job_id)
        except ValueError as exc:
            raise _error(404, "JOB_NOT_FOUND", "任务不存在。") from exc
        except StoreError as exc:
            code = 404 if exc.code == "JOB_NOT_FOUND" else 409
            raise _error(code, exc.code, str(exc)) from exc

    @application.post("/api/jobs/{job_id}/retry", status_code=202)
    def retry_job(job_id: str):
        try:
            UUID(job_id)
            readiness = preflight(settings)
            if not readiness.get("ready"):
                raise _error(503, "CONFIG_NOT_READY", "服务配置未完成，暂不能重试。")
            job = store.retry(job_id)
            return {"job_id": job_id, "status": job["status"], "attempt_count": job["attempt_count"]}
        except ValueError as exc:
            raise _error(404, "JOB_NOT_FOUND", "任务不存在。") from exc
        except StoreError as exc:
            code = 404 if exc.code == "JOB_NOT_FOUND" else 409
            raise _error(code, exc.code, str(exc)) from exc

    @application.get("/api/jobs/{job_id}/artifacts")
    def list_artifacts(job_id: str):
        try:
            UUID(job_id)
            return {"items": store.get_artifacts(job_id)}
        except ValueError as exc:
            raise _error(404, "JOB_NOT_FOUND", "任务不存在。") from exc
        except StoreError as exc:
            raise _error(404, exc.code, str(exc)) from exc

    @application.get("/api/jobs/{job_id}/artifacts/{name}")
    def download_artifact(job_id: str, name: str):
        if not ARTIFACT_NAME_PATTERN.fullmatch(name):
            raise _error(404, "ARTIFACT_NOT_FOUND", "制品不存在。")
        try:
            path, meta = store.artifact_path(job_id, name)
            download_name = {"video.mp4": "AutoVideo.mp4", "captions.srt": "captions.srt"}.get(name, name)
            return FileResponse(path, media_type=meta["content_type"], filename=download_name)
        except ValueError as exc:
            raise _error(404, "JOB_NOT_FOUND", "任务不存在。") from exc
        except StoreError as exc:
            code = 404 if exc.code in {"JOB_NOT_FOUND", "ARTIFACT_NOT_FOUND"} else 409
            raise _error(code, exc.code, str(exc)) from exc

    @application.get("/api/jobs/{job_id}/scene-images")
    def list_scene_images(job_id: str):
        try:
            UUID(job_id)
            return {"items": store.get_scene_images(job_id)}
        except ValueError as exc:
            raise _error(404, "JOB_NOT_FOUND", "任务不存在。") from exc
        except StoreError as exc:
            code = 404 if exc.code == "JOB_NOT_FOUND" else 409
            raise _error(code, exc.code, str(exc)) from exc

    @application.get("/api/jobs/{job_id}/scene-images/{scene_id}")
    def get_scene_image(job_id: str, scene_id: str):
        try:
            UUID(job_id)
            path = store.scene_image_path(job_id, scene_id)
            return FileResponse(path, media_type="image/png", headers={"Cache-Control": "no-store"})
        except ValueError as exc:
            raise _error(404, "JOB_NOT_FOUND", "任务不存在。") from exc
        except StoreError as exc:
            code = 404 if exc.code in {"JOB_NOT_FOUND", "SCENE_IMAGE_NOT_FOUND"} else 409
            raise _error(code, exc.code, str(exc)) from exc

    return application


app = create_app()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("g3.app:app", host="127.0.0.1", port=8000, reload=False)
