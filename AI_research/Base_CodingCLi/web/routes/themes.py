"""Manual topics and daily recommendations scoped to each matrix."""
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal

from web import audit
from web.deps import require_editor, require_viewer
from web.services import theme_workbench as workbench

router = APIRouter(prefix="/api/matrices/{matrix_key}/themes", tags=["themes"])


def call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except (ValueError, FileExistsError, RuntimeError) as exc:
        raise HTTPException(409, str(exc)) from exc


class ThemeFields(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=4, max_length=180)
    angle: str = Field(default="", max_length=1000)
    audience: str = Field(default="", max_length=1000)
    notes: str = Field(default="", max_length=20000)


class ThemeCreate(ThemeFields):
    category: str = Field(min_length=1, max_length=100)


class Start(BaseModel):
    model_config = ConfigDict(extra="forbid")
    auto_video: bool = False
    minutes: float = Field(default=4, ge=2, le=8, multiple_of=0.5)


class Generate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    brief: str = Field(default="", max_length=4000)
    category_key: str = Field(default="", max_length=100)
    pattern_id: str = Field(default="", max_length=100)


class Schedule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool
    time: str = "09:00"
    platforms: list[str] = Field(default_factory=lambda: ["bilibili", "zhihu", "youtube"], max_length=5)
    mode: Literal["ideas", "discovery"] | None = None
    direction: str | None = Field(default=None, max_length=4000)


class Feedback(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    action: Literal["interested", "dismissed", "clear"]
    reason: Literal["", "not_relevant", "too_generic", "hard_to_show", "not_now"] = ""
    note: str = Field(default="", max_length=1000)


@router.get("")
def list_themes(matrix_key: str, user=Depends(require_viewer)):
    return call(workbench.snapshot, matrix_key)


@router.post("", status_code=201)
def create_theme(matrix_key: str, body: ThemeCreate, user=Depends(require_editor)):
    result = call(workbench.create, matrix_key, body.model_dump())
    audit.log("theme_create", user=user["username"], target=result["id"])
    return result


@router.put("/schedule")
def schedule(matrix_key: str, body: Schedule, user=Depends(require_editor)):
    result = call(workbench.set_schedule, matrix_key, enabled=body.enabled, at=body.time, platforms=body.platforms,
                  mode=body.mode, direction=body.direction)
    audit.log("theme_discovery_schedule", user=user["username"], target=matrix_key, details=body.model_dump())
    return result


@router.post("/discover", status_code=202)
def discover(matrix_key: str, user=Depends(require_editor)):
    return call(workbench.discover, matrix_key, user["username"])


@router.post("/generate", status_code=202)
def generate(matrix_key: str, body: Generate, user=Depends(require_editor)):
    return call(workbench.generate, matrix_key, user["username"], **body.model_dump())


@router.get("/history")
def history(matrix_key: str, offset: int = Query(default=0, ge=0), user=Depends(require_viewer)):
    return call(workbench.history, matrix_key, offset=offset)


@router.post("/{theme_id}/used")
def used(matrix_key: str, theme_id: str, user=Depends(require_editor)):
    call(workbench.require_theme, matrix_key, theme_id)
    result = call(workbench.storage().mark_used, theme_id)
    audit.log("theme_used", user=user["username"], target=theme_id)
    return result


@router.patch("/{theme_id}")
def edit(matrix_key: str, theme_id: str, body: ThemeFields, user=Depends(require_editor)):
    call(workbench.require_theme, matrix_key, theme_id)
    return call(workbench.storage().edit, theme_id, **body.model_dump())


@router.post("/{theme_id}/dismiss")
def dismiss(matrix_key: str, theme_id: str, user=Depends(require_editor)):
    call(workbench.require_theme, matrix_key, theme_id)
    return call(workbench.storage().dismiss, theme_id, True)


@router.post("/{theme_id}/feedback")
def feedback(matrix_key: str, theme_id: str, body: Feedback, user=Depends(require_editor)):
    call(workbench.require_theme, matrix_key, theme_id)
    return call(workbench.storage().feedback, theme_id, **body.model_dump())


@router.get("/{theme_id}/usage")
def usage(matrix_key: str, theme_id: str, user=Depends(require_viewer)):
    value = call(workbench.require_theme, matrix_key, theme_id)
    from web.services.theme_usage import usage_for_theme
    return usage_for_theme(value)


@router.post("/{theme_id}/start", status_code=202)
def start(matrix_key: str, theme_id: str, body: Start, user=Depends(require_editor)):
    result = call(workbench.start, matrix_key, theme_id, user["username"], **body.model_dump())
    audit.log("theme_start", user=user["username"], target=theme_id, details={"auto_video": body.auto_video})
    return result
