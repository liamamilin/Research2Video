"""Frozen-scope operations that can outlive a chat response."""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from web.deps import require_editor, require_viewer
from web.services import assistant_batches
from web.services.assistant_batch_store import BatchStore, view

router = APIRouter(prefix='/api/assistant/batches', tags=['assistant'])


class Scope(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    all: bool = False
    matrices: list[str] = Field(default_factory=list)
    ids: list[str] = Field(default_factory=list)
    jobs: list[str] = Field(default_factory=list)


class BatchSpec(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    kind: Literal['matrix_render', 'schedules', 'publish', 'produce']
    scope: Scope
    render: dict = Field(default_factory=dict)
    enabled: bool = False
    platform: str = ''
    account_uid: str = ''
    refresh: bool = True
    count: int = Field(default=1, ge=1, le=10)


class BatchAction(BaseModel):
    model_config = ConfigDict(extra='forbid')
    action: Literal['start', 'retry', 'cancel']


@router.get('')
def list_batches(user=Depends(require_viewer)):
    return {'items': [view(b) for b in BatchStore().list(user)]}


@router.get('/{key}')
def get_batch(key: str, user=Depends(require_viewer)):
    try:
        return view(BatchStore().get(key, user))
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post('', status_code=201)
def prepare_batch(body: BatchSpec, request: Request, user=Depends(require_editor)):
    try:
        return assistant_batches.prepare(body.model_dump(), request, user)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post('/{key}')
def manage_batch(key: str, body: BatchAction, user=Depends(require_editor)):
    try:
        return assistant_batches.manage(key, body.action, user)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
