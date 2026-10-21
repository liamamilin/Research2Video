"""Video script recipes API: list, read, check, save, copy and delete the files in ``config/video_recipes/``.

Like a job file, a recipe is edited as YAML text (comments kept). Saving checks it first: a recipe that does not
build is refused with the reason, so a bad edit cannot break writing for that recipe. See spec/video-recipes.md.
"""

from __future__ import annotations

import os
import re
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from core.video import columns as video_columns
from core.video import recipes, store
from web import audit
from web.deps import require_editor, require_viewer
from web.models import ApiError
from web.services import yaml_io
from web.settings import get_settings

router = APIRouter(prefix="/api/video/recipes", tags=["video"])


def _config_dir() -> str:
    return get_settings().paths.config_dir


def _path(key: str) -> str:
    try:
        return recipes.path_for(_config_dir(), key)
    except video_columns.ColumnError as exc:
        raise HTTPException(422, detail=ApiError.make("bad_recipe_key", str(exc))) from exc


def _existing(key: str) -> str:
    path = _path(key)
    if not os.path.isfile(path):
        raise HTTPException(404, detail=ApiError.make("recipe_not_found", f"没有这个配方：{key}"))
    return path


def _warnings(recipe: recipes.Recipe) -> list[str]:
    """What builds but will not work: a topic source no job declares."""
    from web.services import video_autopilot

    if recipe.column is None:
        return []
    return [f"topic.sources 里的 {name} 没有哪个 Job 声明（在 Job 的 YAML 里写 topics: alias: {name}）"
            for name in recipe.column.topic_sources if not video_autopilot.known_source(name)]


def _audiences(recipe: recipes.Recipe) -> list[dict]:
    if not recipe.audiences or recipe.error or not all(d in recipe.tags for d in recipes.DIMENSIONS):
        return []
    return [{"key": a, "recipe": recipe.key if i == 0 else recipes.variant_key(recipe, a)}
            for i, a in enumerate(recipe.audiences)]


def _summary(recipe: recipes.Recipe, used: dict[str, int]) -> dict:
    column = recipe.column
    return {
        "key": recipe.key,
        "label": recipe.label,
        "status": recipe.status,
        "reason": recipe.reason,
        "error": recipe.error,
        "usable": recipe.usable,
        "cell": recipe.cell_id,
        "tags": recipe.tags,
        "audiences": _audiences(recipe),
        "name": column.name if column else recipe.key,
        "pipeline": ("adapt" if column.writer == "adapt" else "single") if column else None,
        "research_mode": column.research_mode if column else None,
        "target_minutes": column.target_minutes if column else None,
        "topic_kind": column.topic_kind if column else None,
        "topic_sources": list(column.topic_sources) if column else [],
        "warnings": _warnings(recipe),
        "uses": {section: {"kind": "research" if section == "research" else section, "name": u["name"]}
                 for section, u in recipe.uses.items()},
        "scripts": sum(used.get(k, 0) for k in {recipe.key, *(a["recipe"] for a in _audiences(recipe))}),
        "mtime": os.path.getmtime(recipe.path) if recipe.path and os.path.isfile(recipe.path) else None,
        "sha256": recipe.sha256,
    }


def _check(key: str, content: str) -> recipes.Recipe:
    try:
        return recipes.validate(key, content, _config_dir())
    except video_columns.ColumnError as exc:
        raise HTTPException(422, detail=ApiError.make("recipe_invalid", str(exc))) from exc


# --- shared parts: research templates and output / writing parts ---------------------------------------------
# (declared before /{key}, which would otherwise take "parts" for a recipe key)


def _part_path(kind: str, name: str) -> str:
    try:
        return recipes.part_file(_config_dir(), kind, name)
    except video_columns.ColumnError as exc:
        raise HTTPException(422, detail=ApiError.make("bad_part", str(exc))) from exc


def _part(kind: str, name: str) -> dict:
    path = _part_path(kind, name)
    if not os.path.isfile(path):
        raise HTTPException(404, detail=ApiError.make("part_not_found", f"没有这个部件：{kind}/{name}"))
    row = next((p for p in recipes.parts(_config_dir()) if p["kind"] == kind and p["name"] == name), {})
    with open(path, encoding="utf-8") as fh:
        content = fh.read()
    return {**row, "kind": kind, "name": name, "content": content, "mtime": os.path.getmtime(path)}


class PartSaveIn(BaseModel):
    content: str = Field(max_length=200_000)
    expected_mtime: Optional[float] = None


def _check_part(kind: str, name: str, content: str, used_by: list[str]) -> None:
    """The edit must be a mapping, and every recipe that uses the part must still build with it."""
    try:
        data = yaml_io.parse_yaml(content)
    except ValueError as exc:
        raise HTTPException(422, detail=ApiError.make("part_invalid", str(exc))) from exc
    if not isinstance(data, dict) or not data:
        raise HTTPException(422, detail=ApiError.make("part_invalid", "部件应是非空的 YAML 映射"))
    path = _part_path(kind, name)
    with recipes.trying(path, content):
        every = recipes.expanded(_config_dir())
    broken = [f"{key}：{r.error}" for key, r in every.items() if r.base in used_by and r.error]
    if broken:
        raise HTTPException(422, detail=ApiError.make("part_breaks_recipes", "改了以后这些配方就用不了：" + "；".join(broken)))


@router.get("/parts")
def list_parts(user=Depends(require_viewer)) -> list[dict]:
    return recipes.parts(_config_dir())


@router.get("/parts/{kind}/{name}")
def get_part(kind: str, name: str, user=Depends(require_viewer)) -> dict:
    return _part(kind, name)


@router.put("/parts/{kind}/{name}")
def save_part(kind: str, name: str, body: PartSaveIn, user=Depends(require_editor)) -> dict:
    current = _part(kind, name)
    _check_part(kind, name, body.content, current.get("used_by") or [])
    path = _part_path(kind, name)
    if body.expected_mtime is not None and yaml_io.check_conflict(path, body.expected_mtime) is not None:
        raise HTTPException(409, detail=ApiError.make("conflict", "部件文件已被别处修改。请先复制你的修改再刷新。"))
    backup = (yaml_io.save_backup(f"video_part_{kind}_{name}.yaml", current["content"])
              if current["content"] != body.content else None)
    yaml_io.atomic_write(path, body.content)
    audit.log("video_part_update", user=user["username"], target=f"{kind}/{name}", result="success",
              details={"backup": backup, "used_by": current.get("used_by")})
    return _part(kind, name)


class PartCreateIn(BaseModel):
    name: str
    copy_from: str = Field(description="An existing part of the same kind to start from")


@router.post("/parts/{kind}", status_code=201)
def create_part(kind: str, body: PartCreateIn, user=Depends(require_editor)) -> dict:
    content = _part(kind, body.copy_from)["content"]
    path = _part_path(kind, body.name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        yaml_io.atomic_create(path, content)
    except FileExistsError as exc:
        raise HTTPException(409, detail=ApiError.make("part_exists", f"部件 {kind}/{body.name} 已存在")) from exc
    audit.log("video_part_create", user=user["username"], target=f"{kind}/{body.name}", result="success",
              details={"copy_from": body.copy_from})
    return _part(kind, body.name)


@router.get("")
def list_recipes(user=Depends(require_viewer)) -> list[dict]:
    used = store.count_scripts_by_column()
    return [_summary(r, used) for r in recipes.load(_config_dir()).values()]


@router.get("/{key}")
def get_recipe(key: str, user=Depends(require_viewer)) -> dict:
    path = _existing(key)
    with open(path, encoding="utf-8") as fh:
        content = fh.read()
    recipe = recipes.parse(key, content, path, _config_dir())
    return {**_summary(recipe, store.count_scripts_by_column()), "content": content,
            "column": recipe.column.as_dict() if recipe.column else None}


class RecipeCheckIn(BaseModel):
    content: str = Field(max_length=200_000)


@router.post("/{key}/validate")
def validate_recipe(key: str, body: RecipeCheckIn, user=Depends(require_viewer)) -> dict:
    """Whether ``content`` would build, and what it builds to; never writes."""
    recipe = recipes.parse(key, body.content, config_dir=_config_dir())
    return {"ok": not recipe.error, "error": recipe.error, "warnings": _warnings(recipe),
            "column": recipe.column.as_dict() if recipe.column else None}


class RecipeSaveIn(BaseModel):
    content: str = Field(max_length=200_000)
    expected_mtime: Optional[float] = None


@router.put("/{key}")
def save_recipe(key: str, body: RecipeSaveIn, user=Depends(require_editor)) -> dict:
    path = _existing(key)
    _check(key, body.content)
    if body.expected_mtime is not None and yaml_io.check_conflict(path, body.expected_mtime) is not None:
        raise HTTPException(409, detail=ApiError.make(
            "conflict", "配方文件已被别处修改。刷新会丢弃编辑器里未保存的修改，请先复制再刷新。"))
    with open(path, encoding="utf-8") as fh:
        old = fh.read()
    backup = yaml_io.save_backup(f"video_recipe_{key}.yaml", old) if old != body.content else None
    yaml_io.atomic_write(path, body.content)
    audit.log("video_recipe_update", user=user["username"], target=key, result="success",
              details={"backup": backup})
    return get_recipe(key, user)


class RecipeCreateIn(BaseModel):
    key: str
    copy_from: Optional[str] = Field(default=None, description="Start from this recipe's file (status becomes experimental)")
    content: Optional[str] = Field(default=None, max_length=200_000)


@router.post("", status_code=201)
def create_recipe(body: RecipeCreateIn, user=Depends(require_editor)) -> dict:
    if (body.copy_from is None) == (body.content is None):
        raise HTTPException(422, detail=ApiError.make("recipe_source", "新配方要么从已有配方复制（copy_from），要么给出内容（content）"))
    path = _path(body.key)
    if body.content is not None:
        content = body.content
    else:
        with open(_existing(body.copy_from), encoding="utf-8") as fh:
            content = fh.read()
        # a copy has not been rendered yet: it starts experimental
        content, n = re.subn(r"(?m)^status:.*$", "status: experimental", content, count=1)
        if not n:
            content = "status: experimental\n" + content
    _check(body.key, content)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        yaml_io.atomic_create(path, content)
    except FileExistsError as exc:
        raise HTTPException(409, detail=ApiError.make("recipe_exists", f"配方 {body.key} 已存在")) from exc
    audit.log("video_recipe_create", user=user["username"], target=body.key, result="success",
              details={"copy_from": body.copy_from})
    return get_recipe(body.key, user)


@router.delete("/{key}")
def delete_recipe(key: str, user=Depends(require_editor)) -> dict:
    path = _existing(key)
    counts = store.count_scripts_by_column()
    recipe = recipes.load(_config_dir()).get(key)
    used = sum(counts.get(k, 0) for k in {key, *(a["recipe"] for a in (_audiences(recipe) if recipe else []))})
    if used:
        raise HTTPException(409, detail=ApiError.make(
            "recipe_in_use", f"有 {used} 篇文稿用这个配方，删掉后它们打不开；不想再用就把 status 改成 blocked"))
    with open(path, encoding="utf-8") as fh:
        backup = yaml_io.save_backup(f"video_recipe_{key}.yaml", fh.read())
    os.remove(path)
    audit.log("video_recipe_delete", user=user["username"], target=key, result="success", details={"backup": backup})
    return {"ok": True, "backup": backup}
