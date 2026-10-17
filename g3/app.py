from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
import re
from typing import Literal
from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.concurrency import run_in_threadpool

from g3.input_documents import DocumentError, MAX_UPLOAD_BYTES, load_import, read_document, save_import

from g2.authored_script import script_text
from g2.voices import VOICE_IDS, SPEEDS, catalog
from g3 import revision_support
from g3.config import Settings, probe_config
from g3.store import JobStore, StoreError
from g3.worker import G2ProcessRunner, SingleWorker


WEB_ROOT = Path(__file__).resolve().parent / "web"
ARTIFACT_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,40}\.(?:mp4|srt|json|txt)$")


class CreativeIntent(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    audience: str = ""
    purpose: str = ""
    tone: str = ""
    focus: str = ""

    @field_validator("audience", "purpose", "tone", "focus")
    @classmethod
    def check_text(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("创作偏好不能包含空字符")
        return value


class NarrationChapter(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=32, description="章节标题，原样用作视频章节标题")
    paragraphs: list[str] = Field(min_length=1, description="本章讲稿段落，按顺序逐字配音")

    @field_validator("title")
    @classmethod
    def check_title(cls, value: str) -> str:
        if "\x00" in value or "\n" in value or "\r" in value:
            raise ValueError("章节标题须为一行文字")
        return value

    @field_validator("paragraphs")
    @classmethod
    def check_paragraphs(cls, value: list[str]) -> list[str]:
        value = [paragraph.strip() for paragraph in value]
        if not all(value):
            raise ValueError("讲稿段落不能为空")
        if any("\x00" in paragraph for paragraph in value):
            raise ValueError("讲稿不能包含空字符")
        return value


class CreateJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str | None = Field(default=None, min_length=2, max_length=80)
    creative_intent: CreativeIntent = Field(default_factory=CreativeIntent)
    input_mode: Literal["material", "narration"] = Field(
        default="material",
        description="material：source_text 是素材，AutoVideo 组织讲稿；narration：chapters 是定稿讲稿，逐字配音，只规划画面")
    source_text: str | None = Field(default=None, min_length=50,
                                    description="material 模式必填的素材正文；narration 模式不传，由 chapters 生成")
    chapters: list[NarrationChapter] | None = Field(default=None, min_length=1,
                                                    description="narration 模式必填：按顺序的章节标题与讲稿段落")
    input_document_id: str | None = None
    target_duration_sec: int = Field(default=60, ge=10, le=90)
    duration_mode: Literal["fixed", "content"] = "fixed"
    theme: Literal["minimal_dark"] = "minimal_dark"
    voice_id: str = "zf_xiaoxiao"
    video_speed: Literal[.5, 1.0, 1.5, 2.0] = 1.0

    @field_validator("voice_id")
    @classmethod
    def validate_voice(cls,value):
        if value not in VOICE_IDS:raise ValueError("请选择已支持的本地声音")
        return value
    use_draw_things: bool = False
    video_form: Literal["knowledge_cards", "process_diagram", "information_motion", "auto"] = "knowledge_cards"

    @field_validator("source_text")
    @classmethod
    def validate_source_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if len(value) < 50:
            raise ValueError("source_text must contain at least 50 non-whitespace characters")
        if "\x00" in value:
            raise ValueError("source_text cannot contain NUL characters")
        return value

    @model_validator(mode="after")
    def validate_content_options(self):
        if self.input_mode == "narration":
            if self.chapters is None:
                raise ValueError("按原稿配音需要 chapters（章节标题与讲稿段落）")
            if self.source_text is not None:
                raise ValueError("按原稿配音时不传 source_text；讲稿放在 chapters 中")
            if self.input_document_id is not None:
                raise ValueError("按原稿配音不使用 input_document_id")
            if self.duration_mode != "content":
                raise ValueError("按原稿配音需要 duration_mode: content，时长由讲稿决定")
            self.source_text = script_text([chapter.model_dump() for chapter in self.chapters])
        elif self.chapters is not None:
            raise ValueError("chapters 仅用于 input_mode: narration")
        elif self.source_text is None:
            raise ValueError("source_text 为必填项")
        if self.duration_mode == "content":
            if self.video_form not in {"auto", "process_diagram", "information_motion"}:
                raise ValueError("按内容组织请选择流程动态图解或简洁信息动效")
        if self.video_form in {"auto", "information_motion"} and self.use_draw_things:
            raise ValueError("简洁信息动效使用浅色排版，请关闭场景背景图")
        if self.duration_mode == "fixed" and len(self.source_text) > 2000:
            raise ValueError("指定时长短片的素材上限为 2000 字符；完整材料请选择按内容自动组织")
        return self


class ContentRevisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    mode: Literal["source", "narration"]
    text: str = Field(min_length=1)
    title: str | None = Field(default=None, min_length=2, max_length=80)
    creative_intent: CreativeIntent | None = None

    @model_validator(mode="after")
    def check_text(self):
        if "\x00" in self.text:
            raise ValueError("内容不能包含空字符")
        if self.mode == "source" and len(self.text) < 50:
            raise ValueError("原稿至少需要 50 个字符；可直接修订较短的讲稿")
        return self


class VisualRevisionRequest(BaseModel):
    model_config=ConfigDict(extra="forbid",str_strip_whitespace=True)
    scene_id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,47}$")
    changes: dict[str,str] = Field(default_factory=dict)
    instruction: str = ""

    @model_validator(mode="after")
    def validate_edit(self):
        if bool(self.changes)==bool(self.instruction):raise ValueError("请选择直接修改文字或 AI 调整画面中的一种")
        if "\x00" in self.instruction:raise ValueError("修改要求含无效字符")
        return self


class MediaRevisionRequest(BaseModel):
    model_config=ConfigDict(extra="forbid")
    voice_id: str | None = None
    video_speed: Literal[.5,1.0,1.5,2.0] | None = None

    @model_validator(mode="after")
    def validate_edit(self):
        if self.voice_id is None and self.video_speed is None:raise ValueError("请选择声音或倍速")
        if self.voice_id is not None and self.voice_id not in VOICE_IDS:raise ValueError("声音不受支持")
        return self


class SceneRegenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    prompt_extra: str = Field(default="", max_length=300)

    @field_validator("prompt_extra")
    @classmethod
    def validate_prompt_extra(cls, value: str) -> str:
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("prompt_extra cannot contain control characters")
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

    def check_voice(voice_id):
        if not next((v["available"] for v in catalog(settings.project_root) if v["id"]==voice_id),False):
            raise _error(503,"VOICE_NOT_READY","所选本地声音模型尚未就绪，请先选择其他声音。")

    @application.get("/api/voices")
    def voices():
        return {"items":catalog(settings.project_root),"speeds":list(SPEEDS)}

    @application.get("/api/voices/{voice_id}/sample")
    def voice_sample(voice_id:str):
        if voice_id not in VOICE_IDS:raise _error(404,"VOICE_NOT_FOUND","声音不存在。")
        check_voice(voice_id)
        import subprocess
        path=settings.project_root/"g3/data/voice-samples"/voice_id/"sample.wav"
        if not path.is_file():
            try:
                result=subprocess.run([str(settings.g2_python),str(settings.project_root/"g2/voice_preview.py"),voice_id],cwd=settings.project_root,capture_output=True,timeout=300)
                if result.returncode:raise RuntimeError("local voice failed")
            except (OSError,RuntimeError,subprocess.TimeoutExpired) as exc:
                raise _error(503,"VOICE_SAMPLE_FAILED","本地声音试听生成失败，请稍后重试或选择其他声音。") from exc
        return FileResponse(path,media_type="audio/wav")

    @application.get("/api/jobs/{job_id}/scenes")
    def scenes(job_id:str):
        try:return {"items":revision_support.scenes(store,job_id)}
        except StoreError as exc:raise _error(409,exc.code,str(exc)) from exc

    @application.get("/api/jobs/{job_id}/scenes/{scene_id}/preview")
    def scene_preview(job_id:str,scene_id:str):
        try:return FileResponse(revision_support.preview(store,settings,job_id,scene_id),media_type="image/png")
        except (StoreError,ValueError) as exc:raise _error(409,getattr(exc,"code","PREVIEW_INVALID"),str(exc)) from exc

    @application.post("/api/jobs/{job_id}/visual-revisions",status_code=202)
    def visual_revision(job_id:str,request:VisualRevisionRequest):
        if not preflight(settings).get("ready"):raise _error(503,"CONFIG_NOT_READY","本地生成配置尚未就绪。")
        try:
            child=revision_support.create_revision(store,job_id,request.model_dump(),"visual_revision")
            return {"job_id":child["job_id"],"status":child["status"]}
        except StoreError as exc:raise _error(409,exc.code,str(exc)) from exc

    @application.post("/api/jobs/{job_id}/media-revisions",status_code=202)
    def media_revision(job_id:str,request:MediaRevisionRequest):
        if not preflight(settings).get("ready"):raise _error(503,"CONFIG_NOT_READY","本地生成配置尚未就绪。")
        if request.voice_id is not None:check_voice(request.voice_id)
        try:
            child=revision_support.create_revision(store,job_id,request.model_dump(),"media_revision")
            return {"job_id":child["job_id"],"status":child["status"]}
        except StoreError as exc:raise _error(409,exc.code,str(exc)) from exc

    @application.post("/api/input-documents", status_code=201)
    async def upload_document(file: UploadFile):
        try:
            raw = await file.read(MAX_UPLOAD_BYTES + 1)
            if len(raw) > MAX_UPLOAD_BYTES:
                raise DocumentError("DOCUMENT_TOO_LARGE", "单个文件最大 32 MB；可拆分文档后上传。")
            return await run_in_threadpool(save_import, settings.data_dir, file.filename or "", raw)
        except DocumentError as exc:
            raise _error(413 if exc.code == "DOCUMENT_TOO_LARGE" else 422, exc.code, str(exc)) from exc
        finally:
            await file.close()

    @application.get("/api/jobs/{job_id}/input-document")
    def input_document(job_id: str):
        try:
            store.get_job(job_id)
            root = store.source_path(job_id).parent / "document"
            if not root.exists():
                return {"document": None}
            document = read_document(root)
            return {"document": document["metadata"], "download_url": f"/api/jobs/{job_id}/input-document/original"}
        except StoreError as exc:
            raise _error(404, exc.code, str(exc)) from exc
        except DocumentError as exc:
            raise _error(409, exc.code, str(exc)) from exc

    @application.get("/api/jobs/{job_id}/input-document/original")
    def original_document(job_id: str):
        try:
            UUID(job_id)
            document = read_document(store.source_path(job_id).parent / "document")
            return FileResponse(document["path"], filename=document["metadata"]["filename"], media_type="application/octet-stream")
        except DocumentError as exc:
            raise _error(409, exc.code, str(exc)) from exc
        except ValueError as exc:
            raise _error(404, "JOB_NOT_FOUND", "任务不存在。") from exc
        except StoreError as exc:
            raise _error(404, exc.code, str(exc)) from exc

    @application.post("/api/jobs", status_code=202)
    def create_job(request: CreateJobRequest):
        check_voice(request.voice_id)
        readiness = preflight(settings)
        if not readiness.get("ready"):
            raise _error(503, "CONFIG_NOT_READY", "服务配置未完成；请检查 DeepSeek key、Kokoro 与渲染依赖。")
        if request.use_draw_things and not readiness.get("checks", {}).get("draw_things"):
            raise _error(503, "DRAW_THINGS_NOT_READY",
                         "Draw Things 本地 HTTP API 尚未连接；请在 Draw Things「设置 → 高级」开启 HTTP API 服务器后重试。")
        if request.video_form == "process_diagram":
            if not readiness.get("checks", {}).get("diagram_layout"):
                raise _error(503, "DIAGRAM_LAYOUT_NOT_READY", "流程动态图解依赖尚未就绪，请完成本地组件安装。")
            if request.use_draw_things:
                raise _error(422, "DIAGRAM_BACKGROUND_UNSUPPORTED", "流程动态图解使用步骤与路径画面，请关闭场景背景图选项。")
        job_id = str(uuid4())
        title = request.title or "".join(request.source_text.split())[:28] or "未命名视频"
        payload = request.model_dump()
        payload["title"] = title
        if payload["chapters"] is None:
            payload.pop("chapters")
        try:
            document = load_import(settings.data_dir, request.input_document_id) if request.input_document_id else None
            job = store.create_job(job_id, payload, input_document=document)
        except DocumentError as exc:
            raise _error(409, exc.code, str(exc)) from exc
        except (OSError, StoreError) as exc:
            raise _error(500, getattr(exc, "code", "WORKSPACE_CREATE_FAILED"), "无法创建安全的任务工作区。") from exc
        return {"job_id": job_id, "status": job["status"], "created_at": job["created_at"],
                "status_url": f"/api/jobs/{job_id}"}

    @application.get("/api/jobs")
    def list_jobs(limit: int = Query(default=20, ge=1, le=50), offset: int = Query(default=0, ge=0),
                  q: str = Query(default="", max_length=200),
                  status: Literal["", "QUEUED", "RUNNING", "COMPLETED", "FAILED", "CANCELLED", "NEEDS_INPUT"] = ""):
        return store.list_jobs(limit=limit, offset=offset, query=q, status=status)

    @application.get("/api/jobs/{job_id}")
    def get_job(job_id: str):
        try:
            UUID(job_id)
            return store.get_job(job_id)
        except ValueError as exc:
            raise _error(404, "JOB_NOT_FOUND", "任务不存在。") from exc
        except StoreError as exc:
            raise _error(404, exc.code, str(exc)) from exc

    @application.get("/api/jobs/{job_id}/editable-content")
    def editable_content(job_id: str):
        try:
            UUID(job_id)
            return store.editable_content(job_id)
        except ValueError as exc:
            raise _error(404, "JOB_NOT_FOUND", "任务不存在。") from exc
        except StoreError as exc:
            raise _error(404 if exc.code == "JOB_NOT_FOUND" else 409, exc.code, str(exc)) from exc

    @application.get("/api/jobs/{job_id}/revisions")
    def revisions(job_id: str):
        try:
            UUID(job_id)
            return {"items": store.content_revisions(job_id)}
        except ValueError as exc:
            raise _error(404, "JOB_NOT_FOUND", "任务不存在。") from exc
        except StoreError as exc:
            raise _error(404, exc.code, str(exc)) from exc

    @application.post("/api/jobs/{job_id}/revisions", status_code=202)
    def create_revision(job_id: str, request: ContentRevisionRequest):
        try:
            UUID(job_id)
            parent = store.get_job(job_id)
            if parent["status"] not in {"COMPLETED", "FAILED", "CANCELLED"}:
                raise _error(409, "REVISION_NOT_ALLOWED", "请在当前任务结束后创建修订版。")
            draft = store.editable_content(job_id)
            if request.mode == "narration" and not draft["narration_text"]:
                raise _error(409, "NARRATION_UNAVAILABLE", "本任务暂无可修订讲稿，请先修改原稿。")
            readiness = preflight(settings)
            if not readiness.get("ready"):
                raise _error(503, "CONFIG_NOT_READY", "本地生成配置尚未就绪。")
            options = dict(draft["options"])
            if request.mode == "narration":
                form = parent.get("resolved_video_form") or parent["video_form"]
                if form not in {"process_diagram", "information_motion"}:
                    raise _error(409, "NARRATION_FORM_UNSUPPORTED", "此历史形式请通过修改原稿生成新版。")
                options.update(video_form=form, duration_mode="content", use_draw_things=False)
            payload = {**options, "title": request.title or (parent["title"][:74] + " · 修订版"),
                       "source_text": request.text, "operation_type": "content_revision",
                       "parent_job_id": job_id, "parent_attempt_no": parent["attempt_count"],
                       "revision_mode": request.mode,
                       "creative_intent": request.creative_intent.model_dump() if request.creative_intent is not None else draft["creative_intent"]}
            if request.mode == "source":
                try:
                    checked = CreateJobRequest(**{k: payload[k] for k in ("title", "source_text", "target_duration_sec", "duration_mode", "theme", "voice_id", "video_form", "use_draw_things", "creative_intent", "video_speed")})
                except ValidationError as exc:
                    raise _error(422, "REVISION_INPUT_INVALID", str(exc.errors()[0]["msg"])) from exc
                payload.update(checked.model_dump())
            if payload["use_draw_things"] and not readiness.get("checks", {}).get("draw_things"):
                raise _error(503, "DRAW_THINGS_NOT_READY", "Draw Things 尚未连接。")
            if payload["video_form"] == "process_diagram" and not readiness.get("checks", {}).get("diagram_layout"):
                raise _error(503, "DIAGRAM_LAYOUT_NOT_READY", "流程图组件尚未就绪。")
            child_id = str(uuid4())
            child = store.create_job(child_id, payload)
            return {"job_id": child_id, "parent_job_id": job_id, "status": child["status"], "status_url": f"/api/jobs/{child_id}"}
        except ValueError as exc:
            raise _error(404, "JOB_NOT_FOUND", "任务不存在。") from exc
        except StoreError as exc:
            raise _error(404 if exc.code == "JOB_NOT_FOUND" else 409, exc.code, str(exc)) from exc

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

    @application.post("/api/jobs/{job_id}/scene-images/{scene_id}/regenerate", status_code=202)
    def regenerate_scene(job_id: str, scene_id: str, request: SceneRegenerationRequest):
        try:
            UUID(job_id)
            readiness = preflight(settings)
            renderer_ready = (readiness.get("checks") or {}).get("remotion_renderer", readiness.get("ready"))
            if not renderer_ready:
                raise _error(503, "RENDERER_NOT_READY", "本地渲染配置未就绪，暂不能更新成片。")
            if not readiness.get("draw_things", {}).get("ready"):
                raise _error(503, "DRAW_THINGS_NOT_READY", "Draw Things 本地 HTTP API 尚未连接。")
            edited_job_id = str(uuid4())
            job = store.create_scene_edit_job(edited_job_id, job_id, scene_id, request.prompt_extra)
            return {"job_id": edited_job_id, "parent_job_id": job_id, "scene_id": scene_id,
                    "status": job["status"], "status_url": f"/api/jobs/{edited_job_id}"}
        except ValueError as exc:
            raise _error(404, "JOB_NOT_FOUND", "任务不存在。") from exc
        except StoreError as exc:
            code = 404 if exc.code in {"JOB_NOT_FOUND", "SCENE_IMAGE_NOT_FOUND"} else 409
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
