from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
import json
import os
import re
from datetime import date as calendar_date
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, ValidationError, field_validator, model_validator
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.concurrency import run_in_threadpool

from g2.music_library import MusicError, selection as music_selection
from g2.branding import BrandError, selection as brand_selection
from g3 import brand_profiles
from g3 import music_assets
from g2.music_engine import GenerationError, readiness as music_readiness
from g3.music_generation import MusicGeneration
from g3.creative_images import ImageImportError, MAX_BYTES as MAX_IMAGE_BYTES, import_image, load_image, image_path
from g3.input_documents import DocumentError, MAX_UPLOAD_BYTES, load_import, read_document, save_import

from g2 import llm_provider
from g2.creative_library import CreativeResourceError, public_catalog, preview as resource_preview
from g2.production_plan import ProductionPlanError, resolve_defaults, capabilities as production_capabilities
from g2.authored_script import script_text
from g2.package_kit import PackageConfigError, validate_package
from g2.packaging_library import catalog as packaging_catalog, validate_selection
from g2.voices import VOICE_IDS, SPEEDS, catalog
from g3 import revision_support
from g3.config import Settings, probe_config
from g3.store import JobStore, StoreError
from g3.worker import G2ProcessRunner, SingleWorker


WEB_ROOT = Path(__file__).resolve().parent / "web"
ARTIFACT_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,40}\.(?:mp4|srt|json|txt|wav)$")


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


MAX_SOURCES = 3


class NarrationSource(BaseModel):
    """One reference shown on the closing source card. The card's text comes from here, not from the narration."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=40, description="来源标题，原样显示在来源卡上")
    url: str = Field(min_length=8, max_length=300, description="http(s) 网址；来源卡显示其域名")
    date: str = Field(description="发布日期，YYYY-MM-DD")

    @field_validator("title")
    @classmethod
    def check_title(cls, value: str) -> str:
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("来源标题须为一行文字，不能含控制字符")
        return value

    @field_validator("url")
    @classmethod
    def check_url(cls, value: str) -> str:
        parts = urlsplit(value)
        if parts.scheme not in {"http", "https"} or not parts.hostname or any(c.isspace() or ord(c) < 32 for c in value):
            raise ValueError("来源网址须为带域名的 http(s) 网址，且不含空白")
        return value

    @field_validator("date")
    @classmethod
    def check_date(cls, value: str) -> str:
        try:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
                raise ValueError
            calendar_date.fromisoformat(value)
        except ValueError:
            raise ValueError("来源日期须为存在的 YYYY-MM-DD 日期") from None
        return value


class ContentNotice(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    chapter_index: int | None = Field(default=None, ge=0)
    message: str = Field(min_length=1, max_length=120)


class ImagePlacement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chapter_index: int = Field(ge=0)
    paragraph_index: int = Field(ge=0)


class ScreenshotSource(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    url: str = Field(min_length=1, max_length=2048)
    final_url: str = Field(min_length=1, max_length=2048)
    title: str = Field(max_length=300)
    captured_at: str = Field(max_length=40)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ImageFocusRegion(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    x1: int = Field(ge=0, le=1000)
    y1: int = Field(ge=0, le=1000)
    x2: int = Field(ge=0, le=1000)
    y2: int = Field(ge=0, le=1000)
    label: str = Field(default="", max_length=32)
    style: Literal["box", "circle", "underline", "highlight"] = "box"

    @model_validator(mode="after")
    def valid_rectangle(self):
        if self.x2 <= self.x1 or self.y2 <= self.y1:
            raise ValueError("重点区域须为图片内的有效矩形")
        if any(ord(c) < 32 or ord(c) == 127 for c in self.label):
            raise ValueError("重点说明须为单行文字")
        return self


class ImageFocus(BaseModel):
    model_config = ConfigDict(extra="forbid")
    width: int = Field(ge=128, le=8192)
    height: int = Field(ge=128, le=8192)
    regions: list[ImageFocusRegion] = Field(min_length=1, max_length=3)


class ImageMaterial(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    image_id: str
    caption: str = Field(default="", max_length=80)
    role: Literal["reference", "overall", "detail", "comparison"] = "reference"
    placements: list[ImagePlacement] | None = Field(default=None, min_length=1, max_length=100)
    source: ScreenshotSource | None = None
    focus: ImageFocus | None = None

    @field_validator("caption")
    @classmethod
    def validate_caption(cls, value):
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("图片说明须为一行文字，不能含控制字符")
        return value

    @field_validator("image_id")
    @classmethod
    def validate_image_id(cls, value):
        if str(UUID(value)) != value:
            raise ValueError("图片编号无效")
        return value


class RecommendationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    source_text: str = Field(default="", max_length=100000)
    production_recipe: str | None = None
    image_materials: list[ImageMaterial] = Field(default_factory=list, max_length=12)


class CreateJobRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    idempotency_key: str | None = Field(default=None, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$",
                                        description="调用方一次出片的唯一标识；同标识同请求返回原任务，不重复生成；设置不同返回 409。")

    production_recipe: str | None = Field(default=None, description="可选制作配方：ai_news、how_to、science_explainer；显式设置优先")

    production_mode: Literal["standard", "fast"] = "standard"
    visual_style: Literal["legacy", "editorial_light"] = "legacy"
    production_context: dict | None = None
    content_notices: list[ContentNotice] = Field(default_factory=list, max_length=50)
    _submitted: dict = PrivateAttr(default_factory=dict)

    @model_validator(mode="wrap")
    @classmethod
    def recipe_defaults(cls, value, handler):
        if not isinstance(value, dict):
            return handler(value)
        original = dict(value)
        resolved, _ = resolve_defaults(value)
        result = handler(resolved)
        result._submitted = original
        return result

    title: str | None = Field(default=None, min_length=2, max_length=80)
    creative_intent: CreativeIntent = Field(default_factory=CreativeIntent)
    input_mode: Literal["material", "narration"] = Field(
        default="material",
        description="material：source_text 是素材，AutoVideo 组织讲稿；narration：chapters 是定稿讲稿，逐字配音，只规划画面")
    source_text: str | None = Field(default=None, min_length=1,
                                    description="material 模式必填的素材正文；narration 模式不传，由 chapters 生成")
    chapters: list[NarrationChapter] | None = Field(default=None, min_length=1,
                                                    description="narration 模式必填：按顺序的章节标题与讲稿段落")
    sources: list[NarrationSource] | None = Field(default=None, min_length=1, max_length=MAX_SOURCES,
                                                  description="来源卡数据（标题、网址、日期），最多 3 条；所有视频形式均可用，不传则记录 no_sources")
    package: dict | None = Field(default=None, description="兼容旧调用方的完整包装配置；新调用方使用 packaging，省略时启用通用默认。"
                                                           "形如 {accent, intro?, outro?}，契约见 UPSTREAM_INTEGRATION.md")
    packaging: dict | None = Field(default=None, description="包装库配置；省略继承 common。enabled:false 明确关闭。与旧 package 互斥。")
    brand: dict | None = None

    @field_validator("brand")
    @classmethod
    def validate_brand(cls, value):
        return brand_selection(value)

    background_music: dict | None = Field(default=None, description="背景音乐开关、素材 id、相对音量、自动压低、循环和淡入淡出；省略保持原旁白。")

    @field_validator("background_music")
    @classmethod
    def validate_background_music(cls, value):
        return music_selection(value)

    input_document_id: str | None = None
    target_duration_sec: int = Field(default=60, ge=10, le=90)
    duration_mode: Literal["fixed", "content"] = "fixed"
    theme: Literal["minimal_dark"] = "minimal_dark"
    voice_id: str = "zf_xiaoxiao"
    video_speed: Literal[.5, 1.0, 1.25, 1.5, 2.0] = 1.0

    @field_validator("voice_id")
    @classmethod
    def validate_voice(cls,value):
        if value not in VOICE_IDS:raise ValueError("请选择已支持的本地声音")
        return value
    use_draw_things: bool = False
    video_form: Literal["knowledge_cards", "process_diagram", "information_motion", "auto", "kinetic_typography", "image_slideshow", "chat_dialogue", "information_cards", "object_showcase", "dynamic_collage"] = "knowledge_cards"
    visual_storytelling: bool = False
    slideshow_images: list[str] = Field(default_factory=list, max_length=12)
    image_materials: list[ImageMaterial] = Field(default_factory=list, max_length=12)

    @field_validator("slideshow_images")
    @classmethod
    def validate_images(cls, values):
        if len(values) != len(set(values)):
            raise ValueError("轮播图片不能重复")
        for value in values:
            if str(UUID(value)) != value:
                raise ValueError("图片编号无效")
        return values

    @field_validator("source_text")
    @classmethod
    def validate_source_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("source_text cannot be empty")
        if "\x00" in value:
            raise ValueError("source_text cannot contain NUL characters")
        return value

    @field_validator("package")
    @classmethod
    def check_package(cls, value):
        if value is None:
            return None
        try:
            return validate_package(value)
        except PackageConfigError as exc:
            raise ValueError(str(exc)) from None

    @field_validator("packaging")
    @classmethod
    def check_packaging(cls, value):
        try:
            return validate_selection(value) if value is not None else None
        except PackageConfigError as exc:
            raise ValueError(str(exc)) from None

    @model_validator(mode="after")
    def validate_content_options(self):
        mixed_cards = (self.production_mode == "fast" and self.video_form == "information_cards"
                       and self.visual_storytelling and self.input_mode == "narration" and self.chapters)
        if self.image_materials:
            if self.video_form not in {"object_showcase", "dynamic_collage"} and not mixed_cards:
                raise ValueError("image_materials 用于对象展示或动态拼贴")
            ids = [item.image_id for item in self.image_materials]
            if len(set(ids)) != len(ids) or (self.slideshow_images and self.slideshow_images != ids):
                raise ValueError("图片素材编号重复或与图片清单不一致")
            self.slideshow_images = ids
            mapped = [item.placements is not None for item in self.image_materials]
            if mixed_cards and not all(mapped):
                raise ValueError("快速混合画面的图片需要指定讲稿段落 placements")
            if any(mapped):
                if not all(mapped) or self.input_mode != "narration" or not self.chapters:
                    raise ValueError("段落配图须全部明确指定 placements，并使用分章讲稿模式")
                for item in self.image_materials:
                    positions = [(p.chapter_index, p.paragraph_index) for p in item.placements]
                    if len(set(positions)) != len(positions) or any(
                        c >= len(self.chapters) or p >= len(self.chapters[c].paragraphs) for c, p in positions
                    ):
                        raise ValueError("图片须对应讲稿中存在且不重复的章节和段落")
        if any(n.chapter_index is not None and (self.chapters is None or n.chapter_index >= len(self.chapters)) for n in self.content_notices):
            raise ValueError("核实提示须引用讲稿中存在的章节")
        if self.package is not None and self.packaging is not None:
            raise ValueError("packaging 与旧 package 不能同时提交")
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
            if self.video_form not in {"auto", "process_diagram", "information_motion", "kinetic_typography", "image_slideshow", "chat_dialogue", "information_cards", "object_showcase", "dynamic_collage"}:
                raise ValueError("按内容组织请选择流程动态图解或简洁信息动效，也可选择动态排版、图片轮播和聊天对话体")
        if self.slideshow_images and self.video_form not in {"image_slideshow", "object_showcase", "dynamic_collage"} and not mixed_cards:
            raise ValueError("图片素材只用于图片轮播、对象展示或动态拼贴")
        if self.video_form in {"object_showcase", "dynamic_collage"}:
            if not self.slideshow_images:
                raise ValueError("对象展示和动态拼贴需要已有图片；纯文稿可选择动态排版或信息图")
            if self.use_draw_things:
                raise ValueError("对象展示和动态拼贴使用已有图片，请关闭自动场景图")
        if self.video_form == "image_slideshow":
            self.use_draw_things = not bool(self.slideshow_images)
        if self.video_form in {"auto", "information_motion", "kinetic_typography", "chat_dialogue", "information_cards"} and self.use_draw_things:
            raise ValueError("简洁信息动效使用浅色排版，请关闭场景背景图")
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
    video_speed: Literal[.5,1.0,1.25,1.5,2.0] | None = None

    @model_validator(mode="after")
    def validate_edit(self):
        if self.voice_id is None and self.video_speed is None:raise ValueError("请选择声音或倍速")
        if self.voice_id is not None and self.voice_id not in VOICE_IDS:raise ValueError("声音不受支持")
        return self


class MusicRevisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    background_music: dict

    @field_validator("background_music")
    @classmethod
    def validate_music(cls, value):
        return music_selection(value)


class MusicGenerationRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    name: str = Field(min_length=2, max_length=80)
    style: Literal['ambient', 'piano', 'electronic'] = 'ambient'
    description: str = Field(default='', max_length=220)
    duration_seconds: int = Field(default=24, ge=10, le=30, strict=True)
    bpm: int | None = Field(default=None, ge=50, le=160, strict=True)
    seed: int | None = Field(default=None, ge=0, le=2147483647, strict=True)
    idempotency_key: str | None = Field(default=None, pattern=r'^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$')

    @field_validator('name', 'description')
    @classmethod
    def safe_text(cls, value):
        if any(ord(c) < 32 and c not in '\n\t' for c in value):
            raise ValueError('音乐名称和描述不能含控制字符')
        return value


class MusicReviewRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    asset_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    listened: Literal[True]
    no_unexpected_vocals: bool = Field(strict=True)
    natural_loop: bool = Field(strict=True)
    narration_friendly: bool = Field(strict=True)
    note: str = Field(default='', max_length=300)


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
    music_generation = MusicGeneration(settings)
    worker = SingleWorker(settings, store, processor, idle_task=music_generation.run_one, on_start=music_generation.recover)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        store.initialize()
        music_generation.jobs.initialize()
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
    application.state.music_generation = music_generation
    application.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "testserver"])
    application.mount("/static", StaticFiles(directory=WEB_ROOT / "static"), name="static")

    @application.exception_handler(BrandError)
    async def brand_error(_request, exc):
        return JSONResponse(status_code=422, content={"detail": {"code": "BRAND_INVALID", "message": str(exc)}})

    @application.exception_handler(MusicError)
    async def music_error(_request, exc):
        return JSONResponse(status_code=422, content={"detail": {"code": "MUSIC_INVALID", "message": str(exc)}})

    @application.exception_handler(GenerationError)
    async def generation_error(_request, exc):
        status = 404 if exc.code == 'MUSIC_JOB_NOT_FOUND' else 409 if exc.code in {'MUSIC_SUBMISSION_CONFLICT','MUSIC_JOB_STATE'} else 422
        return JSONResponse(status_code=status,content={'detail':{'code':exc.code,'message':str(exc)}})

    @application.exception_handler(ImageImportError)
    async def creative_image_error(_request, exc):
        return JSONResponse(status_code=422, content={"detail": {"code": "CREATIVE_IMAGE_INVALID", "message": str(exc)}})

    @application.exception_handler(CreativeResourceError)
    async def invalid_saved_recipe(_request, exc):
        return JSONResponse(status_code=409, content={"detail": {"code": "PRODUCTION_RECIPE_INVALID", "message": str(exc)}})

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

    def llm_state():
        environ = settings.llm_environ()
        provider = llm_provider.load(settings.key_file, environ)
        key = llm_provider.api_key(provider, settings.key_file, environ)
        return provider, key, {
            "label": provider.label, "base_url": provider.base_url, "model": provider.model, "key_env": provider.key_env,
            "env_file": provider.env_file, "fallback_models": list(provider.fallback_models), "source": provider.source, "needs_key": provider.needs_key,
            "key_ready": bool(key) or not provider.needs_key, "error": provider.error,
        }

    @application.get("/api/config/llm")
    def get_llm_config():
        return llm_state()[2]

    def checked_llm_settings(payload: dict):
        try:
            return llm_provider.validate_settings(payload)
        except ValueError as exc:
            raise _error(422, "LLM_SETTINGS_INVALID", str(exc))

    @application.put("/api/config/llm")
    def put_llm_config(payload: dict):
        saved = checked_llm_settings(payload)
        path = settings.llm_settings_file
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp, path)
        return llm_state()[2]

    @application.delete("/api/config/llm")
    def reset_llm_config():
        settings.llm_settings_file.unlink(missing_ok=True)
        return llm_state()[2]

    @application.post("/api/config/llm/test")
    def test_llm_config(payload: dict | None = None):
        """One tiny request to the saved provider (or, with a body, to that candidate without saving it)."""
        if payload:
            saved = checked_llm_settings(payload)
            environ = {**settings.llm_environ(), "AUTOVIDEO_LLM_SETTINGS_FILE": ""}
            candidate = settings.data_dir / "llm-settings.candidate.json"
            candidate.parent.mkdir(parents=True, exist_ok=True)
            candidate.write_text(json.dumps(saved, ensure_ascii=False), encoding="utf-8")
            try:
                environ["AUTOVIDEO_LLM_SETTINGS_FILE"] = str(candidate)
                provider = llm_provider.load(settings.key_file, environ)
                key = llm_provider.api_key(provider, settings.key_file, environ)
            finally:
                candidate.unlink(missing_ok=True)
        else:
            provider, key, _ = llm_state()
        return llm_provider.ping(provider, key)

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

    @application.get("/api/music-assets")
    def list_music():
        from g3.music_curation import enrich
        items = enrich(settings.data_dir, music_assets.listing(settings.data_dir))
        return {"items": items, "music_generation": music_readiness()['ready']}

    @application.post('/api/music-assets/{identity}/analyze')
    async def analyze_music(identity: str):
        from g3.music_curation import analyze
        return await run_in_threadpool(analyze, settings.data_dir, identity)

    @application.get('/api/music-assets/{identity}/loop-preview')
    def music_loop_preview(identity: str, sha256: str = Query(pattern=r'^[a-f0-9]{64}$')):
        from g3.music_curation import preview
        return FileResponse(preview(settings.data_dir, identity, sha256), media_type='audio/wav')

    @application.get('/api/music-assets/{identity}/narration-preview')
    def music_narration_preview(identity: str, sha256: str = Query(pattern=r'^[a-f0-9]{64}$')):
        from g3.music_curation import narration_preview
        return FileResponse(narration_preview(settings.data_dir, identity, sha256), media_type='audio/wav')

    @application.post('/api/music-assets/{identity}/review')
    def review_music(identity: str, request: MusicReviewRequest):
        from g3.music_curation import save_review
        return save_review(settings.data_dir, identity, request.model_dump())

    @application.get('/api/music-generation/status')
    def music_generation_status():
        return music_readiness()

    @application.post('/api/music-jobs',status_code=202)
    def create_music(request: MusicGenerationRequest):
        status = music_readiness()
        if not status['ready']:
            raise _error(503,'MUSIC_NOT_READY',status['message'])
        return music_generation.jobs.create(request.model_dump())

    @application.get('/api/music-jobs')
    def list_music_jobs(limit: int = Query(default=20,ge=1,le=50)):
        return {'items':music_generation.jobs.listing(limit)}

    @application.get('/api/music-jobs/{identity}')
    def music_job(identity: str):
        return music_generation.jobs.get(identity)

    @application.post('/api/music-jobs/{identity}/cancel')
    def cancel_music(identity: str):
        return music_generation.jobs.cancel(identity)

    @application.post('/api/music-jobs/{identity}/retry',status_code=202)
    def retry_music(identity: str):
        parent = music_generation.jobs.get(identity)
        if parent['status'] not in {'FAILED','CANCELLED'}:
            raise GenerationError('MUSIC_JOB_STATE','失败或已取消的音乐任务才可重新生成')
        status = music_readiness()
        if not status['ready']:
            raise _error(503,'MUSIC_NOT_READY',status['message'])
        return music_generation.jobs.create({**parent['request'],'seed':parent['settings']['seed']},parent_id=identity)

    @application.post("/api/music-assets", status_code=201)
    async def upload_music(file: UploadFile):
        try:
            raw = await file.read(music_assets.MAX_BYTES + 1)
            return await run_in_threadpool(music_assets.import_audio, settings.data_dir, raw, file.filename or "")
        finally:
            await file.close()

    @application.get("/api/music-assets/{identity}/preview")
    def music_preview(identity: str):
        _meta, path = music_assets.ensure(settings.data_dir, identity)
        return FileResponse(path, media_type="audio/wav")

    @application.post("/api/jobs/{job_id}/music-revisions", status_code=202)
    def music_revision(job_id: str, request: MusicRevisionRequest):
        try:
            parent, _ir, _base = revision_support.source(store, job_id, allow_legacy=True)
            draft = store.editable_content(job_id)
            body = {**draft["options"], "background_music": request.background_music,
                    "source_text": draft["source_text"], "creative_intent": draft["creative_intent"],
                    "title": parent["title"][:70] + " · 配乐修订", "operation_type": "music_revision",
                    "parent_job_id": job_id, "parent_attempt_no": parent["attempt_count"]}
            child = store.create_job(str(uuid4()), body)
            return {"job_id": child["job_id"], "parent_job_id": job_id, "status": child["status"]}
        except StoreError as exc:
            raise _error(409, exc.code, str(exc)) from exc

    @application.post("/api/creative-images", status_code=201)
    async def upload_creative_image(file: UploadFile):
        try:
            raw = await file.read(MAX_IMAGE_BYTES + 1)
            return await run_in_threadpool(import_image, settings.data_dir, raw, file.filename or "图片")
        except ImageImportError as exc:
            raise _error(422, "CREATIVE_IMAGE_INVALID", str(exc)) from exc
        finally:
            await file.close()

    @application.get("/api/creative-images/{image_id}")
    def creative_image(image_id: str):
        try:
            load_image(settings.data_dir, image_id)
            return FileResponse(image_path(settings.data_dir, image_id), media_type="image/png")
        except ImageImportError as exc:
            raise _error(404, "CREATIVE_IMAGE_UNAVAILABLE", str(exc)) from exc

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

    @application.get("/api/packaging")
    def get_packaging_library():
        return packaging_catalog()

    @application.get("/api/creative-resources")
    def get_creative_resources():
        return public_catalog()

    @application.get("/api/creative-resources/{form_id}/preview")
    def get_resource_preview(form_id: str):
        try:
            path, media_type = resource_preview(form_id)
            return FileResponse(path, media_type=media_type)
        except OSError as exc:
            raise _error(404, "RESOURCE_UNAVAILABLE", str(exc)) from exc
        except CreativeResourceError as exc:
            raise _error(409, "RESOURCE_CHANGED", str(exc)) from exc

    @application.get("/api/production/capabilities")
    def get_production_capabilities():
        return production_capabilities()

    @application.post("/api/creative-recommendations")
    def get_creative_recommendations(request: RecommendationRequest):
        from g2.creative_recommendations import recommend
        try:
            images = [item.model_dump(exclude_none=True) for item in request.image_materials]
            if len({row['image_id'] for row in images}) != len(images):
                raise ImageImportError('图片素材不能重复')
            for row in images:
                load_image(settings.data_dir, row['image_id'])
            from g3.music_curation import enrich
            return recommend(request.source_text, request.production_recipe, images, enrich(settings.data_dir, music_assets.listing(settings.data_dir)))
        except (ImageImportError, CreativeResourceError) as exc:
            raise _error(422, "CREATIVE_RECOMMENDATION_INVALID", str(exc)) from exc

    @application.post("/api/production/resolve")
    def resolve_production(request: CreateJobRequest):
        from g2.production_plan import prepare_request
        payload = request.model_dump(exclude_none=True)
        payload["_production_submitted"] = {k: v for k, v in request._submitted.items()
                                            if k in {"production_recipe", "production_context", "package", "production_mode", "visual_style", "video_form", "duration_mode", "target_duration_sec", "voice_id", "video_speed", "creative_intent", "packaging", "background_music", "brand"}}
        try:
            import tempfile
            with tempfile.TemporaryDirectory(prefix="brand-preview-", dir=settings.data_dir) as folder:
                payload = brand_profiles.freeze(settings.data_dir, Path(folder), payload)
                return {"preview": True, "plan": prepare_request(payload)["production_plan"]}
        except (ProductionPlanError, PackageConfigError, CreativeResourceError) as exc:
            raise _error(422, "PRODUCTION_PLAN_INVALID", str(exc)) from exc

    from g3.brand_routes import router as brand_router
    application.include_router(brand_router(settings.data_dir))

    @application.post("/api/jobs", status_code=202)
    def create_job(request: CreateJobRequest):
        payload = request.model_dump()
        payload["image_materials"] = [item.model_dump(exclude_none=True) for item in request.image_materials]
        payload["_production_submitted"] = {k: v for k, v in request._submitted.items()
                                            if k in {"production_recipe", "production_context", "package", "production_mode", "visual_style", "video_form", "duration_mode", "target_duration_sec", "voice_id", "video_speed", "creative_intent", "packaging", "background_music", "brand"}}
        payload["title"] = request.title or "".join(request.source_text.split())[:28] or "未命名视频"
        for optional in ("chapters", "sources", "package", "packaging", "background_music", "brand"):
            if payload[optional] is None:
                payload.pop(optional)
        def response(job: dict) -> dict:
            return {"job_id": job["job_id"], "status": job["status"], "created_at": job["created_at"],
                    "status_url": f"/api/jobs/{job['job_id']}"}
        try:
            existing = store.find_submission(payload)
        except StoreError as exc:
            raise _error(409, exc.code, str(exc)) from exc
        if existing:
            return response(existing)
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
        try:
            document = load_import(settings.data_dir, request.input_document_id) if request.input_document_id else None
            job = store.create_job(job_id, payload, input_document=document)
        except BrandError as exc:
            raise _error(422, "BRAND_INVALID", str(exc)) from exc
        except ImageImportError as exc:
            raise _error(422, "CREATIVE_IMAGE_INVALID", str(exc)) from exc
        except DocumentError as exc:
            raise _error(409, exc.code, str(exc)) from exc
        except PackageConfigError as exc:
            raise _error(422, "PACKAGING_INVALID", str(exc)) from exc
        except ProductionPlanError as exc:
            raise _error(422, "PRODUCTION_PLAN_INVALID", str(exc)) from exc
        except CreativeResourceError as exc:
            raise _error(422, "PRODUCTION_RECIPE_INVALID", str(exc)) from exc
        except (OSError, StoreError) as exc:
            if getattr(exc, "code", "") == "IDEMPOTENCY_CONFLICT":
                raise _error(409, exc.code, str(exc)) from exc
            raise _error(500, getattr(exc, "code", "WORKSPACE_CREATE_FAILED"), "无法创建安全的任务工作区。") from exc
        return response(job)

    @application.get("/api/jobs")
    def list_jobs(limit: int = Query(default=20, ge=1, le=50), offset: int = Query(default=0, ge=0),
                  q: str = Query(default="", max_length=200),
                  idempotency_key: str = Query(default="", max_length=128),
                  status: Literal["", "QUEUED", "RUNNING", "COMPLETED", "FAILED", "CANCELLED", "NEEDS_INPUT"] = ""):
        return store.list_jobs(limit=limit, offset=offset, query=q, status=status, idempotency_key=idempotency_key)

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
            if any(item.get("placements") for item in draft["options"].get("image_materials", [])):
                raise _error(409, "IMAGE_MAPPING_REQUIRES_REVIEW", "此任务有明确的段落配图。修改讲稿后请在研究侧重新确认配图并新建任务；声音、倍速和配乐仍可单独修订。")
            if request.mode == "narration" and not draft["narration_text"]:
                raise _error(409, "NARRATION_UNAVAILABLE", "本任务暂无可修订讲稿，请先修改原稿。")
            readiness = preflight(settings)
            if not readiness.get("ready"):
                raise _error(503, "CONFIG_NOT_READY", "本地生成配置尚未就绪。")
            options = dict(draft["options"])
            if request.mode == "narration":
                form = parent.get("resolved_video_form") or parent["video_form"]
                if form not in {"process_diagram", "information_motion", "kinetic_typography", "image_slideshow", "chat_dialogue", "information_cards", "object_showcase", "dynamic_collage"}:
                    raise _error(409, "NARRATION_FORM_UNSUPPORTED", "此历史形式请通过修改原稿生成新版。")
                options.update(video_form=form, duration_mode="content", use_draw_things=form == "image_slideshow" and not options.get("slideshow_images"))
            payload = {**options, "title": request.title or (parent["title"][:74] + " · 修订版"),
                       "source_text": request.text, "operation_type": "content_revision",
                       "parent_job_id": job_id, "parent_attempt_no": parent["attempt_count"],
                       "revision_mode": request.mode,
                       "creative_intent": request.creative_intent.model_dump() if request.creative_intent is not None else draft["creative_intent"]}
            if payload.get("content_notices"):
                payload["content_notices"] = [{"chapter_index": None, "message": "原稿有内容待核实，修订后仍需核对来源"}]
            if request.mode == "source":
                try:
                    fields = ("title", "source_text", "target_duration_sec", "duration_mode", "theme", "voice_id", "video_form", "use_draw_things", "creative_intent", "video_speed", "slideshow_images", "image_materials", "visual_storytelling", "production_mode", "visual_style", "production_context", "content_notices")
                    packaging_fields = ("packaging",) if payload.get("packaging_resolved") else ("package",)
                    checked = CreateJobRequest(**{k: payload[k] for k in (*fields, *packaging_fields) if k in payload})
                except ValidationError as exc:
                    raise _error(422, "REVISION_INPUT_INVALID", str(exc.errors()[0]["msg"])) from exc
                payload.update({k: v for k, v in checked.model_dump().items() if k not in {"package", "packaging", "sources", "production_recipe", "background_music", "brand"}})
                payload["image_materials"] = [item.model_dump(exclude_none=True) for item in checked.image_materials]
            if payload["use_draw_things"] and not readiness.get("checks", {}).get("draw_things"):
                raise _error(503, "DRAW_THINGS_NOT_READY", "Draw Things 尚未连接。")
            if payload["video_form"] == "process_diagram" and not readiness.get("checks", {}).get("diagram_layout"):
                raise _error(503, "DIAGRAM_LAYOUT_NOT_READY", "流程图组件尚未就绪。")
            child_id = str(uuid4())
            child = store.create_job(child_id, payload)
            return {"job_id": child_id, "parent_job_id": job_id, "status": child["status"], "status_url": f"/api/jobs/{child_id}"}
        except BrandError as exc:
            raise _error(422, "BRAND_INVALID", str(exc)) from exc
        except ImageImportError as exc:
            raise _error(409, "CREATIVE_IMAGE_INVALID", str(exc)) from exc
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
