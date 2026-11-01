"""Continue an existing material task through its versioned creative context."""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from core.video.material_context import ContextConflict
from web import audit
from web.deps import require_editor

router = APIRouter()


class MaterialUpdateIn(BaseModel):
    model_config = ConfigDict(extra='forbid')
    material_ids: list[str] = Field(default_factory=list, max_length=12)
    urls: list[str] = Field(default_factory=list, max_length=12)
    text: str = Field(default='', max_length=200000)
    goal: str | None = Field(default=None, max_length=8000)
    title: str | None = Field(default=None, min_length=2, max_length=200)
    audience: str | None = Field(default=None, max_length=1000)
    mode: Literal['adapt', 'narration', 'research'] | None = None
    replace_materials: bool = False
    base_revision: int = Field(ge=1)
    regenerate: bool = True
    make_video: bool = False


@router.patch('/scripts/{script_id}/materials')
def update_material_task(script_id: str, body: MaterialUpdateIn, request: Request, user=Depends(require_editor)):
    from web.services.assistant_materials import update
    try:
        result = update(script_id, body, request, user)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(403, str(exc)) from exc
    except ContextConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    audit.log('video_material_context', user=user['username'], target=script_id, details={'revision': result['revision']})
    return result
