"""Information sources: the registry and how each source has been doing (web/services/source_catalog.py)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from core import sources
from pydantic import BaseModel, ConfigDict, Field
from web import audit
from web.deps import require_editor, require_viewer
from web.models import ApiError
from web.services import source_catalog, source_registry
from web.settings import get_settings

router = APIRouter(prefix="/api/sources", tags=["sources"])


@router.get("")
def list_sources(user=Depends(require_viewer)) -> dict:
    """Every registered source with the Jobs that use it and its last fetch; Jobs that name a source the registry lacks."""
    paths = get_settings().paths
    try:
        data = source_catalog.catalog(paths.config_dir, paths.jobs_dir, paths.state_dir)
        snap = source_registry.snapshot()
        return {**data, 'revision': snap['revision'], 'adapters': snap['adapters']}
    except sources.RegistryError as exc:
        raise HTTPException(500, detail=ApiError.make("bad_registry", str(exc))) from exc


class SourceFields(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    name: str | None = None
    spec: str | None = None
    tags: list[str] | None = None
    note: str | None = None


class SourceChange(BaseModel):
    model_config = ConfigDict(extra='forbid')
    expected_revision: str = Field(min_length=1)
    fields: SourceFields = Field(default_factory=SourceFields)


class SourceCreate(SourceChange):
    id: str = Field(min_length=1, max_length=100)


def _change(action, key, body, user):
    try:
        result = source_registry.change(action, key, body.fields.model_dump(exclude_none=True), body.expected_revision)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    audit.log('source_' + action, user=user['username'], target=key, details={'used_by': result['used_by'], 'backup': result['backup']})
    return result


@router.get('/{key}')
def get_source(key: str, user=Depends(require_viewer)) -> dict:
    data = source_registry.snapshot()
    if key not in data['sources']:
        raise HTTPException(404, '信息源不存在')
    return {'id': key, **data['sources'][key], 'revision': data['revision'],
            'used_by': source_registry.usages(data['sources'], key)}


@router.post('', status_code=201)
def create_source(body: SourceCreate, user=Depends(require_editor)) -> dict:
    return _change('create', body.id, body, user)


@router.patch('/{key}')
def update_source(key: str, body: SourceChange, user=Depends(require_editor)) -> dict:
    return _change('update', key, body, user)


@router.delete('/{key}')
def delete_source(key: str, expected_revision: str, user=Depends(require_editor)) -> dict:
    return _change('delete', key, SourceChange(expected_revision=expected_revision), user)
