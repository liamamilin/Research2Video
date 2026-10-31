"""Independent business columns and their existing production bindings."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from core import matrices
from core.video import content_columns, production, store
from web import audit
from web.deps import require_editor, require_viewer
from web.models import ApiError
from web.settings import get_settings

router = APIRouter(tags=["content-columns"])


def _error(status: int, code: str, message: str):
    return HTTPException(status, detail=ApiError.make(code, message))


def _catalog():
    paths = get_settings().paths
    matrix_map = matrices.load_all(paths.config_dir)
    configs = production.load_all(paths.config_dir, paths.jobs_dir)
    columns = content_columns.load_all(paths.config_dir, configs=configs, matrix_map=matrix_map)
    return paths, matrix_map, configs, columns


def _stats(columns) -> dict:
    owners = {pid: c.key for c in columns.values() if not c.errors for pid in c.production_ids}
    with store.connect() as conn:
        rows = conn.execute("""SELECT content_column_id, production, COUNT(*) AS scripts,
            MAX(updated_at) AS updated_at FROM scripts
            WHERE archived = 0 GROUP BY content_column_id, production""").fetchall()
    result = {}
    for row in rows:
        key = row["content_column_id"] or owners.get(row["production"], "")
        if key:
            item = result.setdefault(key, {"scripts": 0, "updated_at": ""})
            item["scripts"] += row["scripts"]
            item["updated_at"] = max(item["updated_at"], row["updated_at"])
    return result


def _shown(column, matrix_map, configs, stats):
    matrix = matrix_map.get(column.matrix)
    owner = {job: m for m in matrix_map.values() for job in m.jobs}
    bindings = []
    for pid in column.production_ids:
        config = configs.get(pid)
        if config is None:
            bindings.append({"key": pid, "name": pid, "errors": ["生产配置不存在"], "auto": 0})
            continue
        source = owner.get(config.job)
        context = {"matrix": column.matrix, "render_inheritance": column.render_inheritance, "render": column.render}
        bindings.append({**config.as_dict(), "source_matrix": source.key if source else "",
                         "source_matrix_name": source.name if source else "",
                         "effective_render": content_columns.render_settings(get_settings().paths.config_dir,
                                                                               source, context, config.render)})
    return {**column.as_dict(), "matrix_name": matrix.content.get("name", matrix.name) if matrix else "",
            "bindings": bindings, "auto_bindings": sum(bool(b.get("auto")) and not b.get("errors") for b in bindings),
            "scripts": stats.get(column.key, {}).get("scripts", 0),
            "updated_at": stats.get(column.key, {}).get("updated_at", "")}


@router.get("/api/column-types")
def list_types(user=Depends(require_viewer)):
    return list(content_columns.types(get_settings().paths.config_dir).values())


@router.get("/api/presentation-recipes")
def presentation_recipes(user=Depends(require_viewer)):
    from core.video.autovideo import AutoVideoError, AutoVideoUnreachable
    from web.services.video_render import get_client
    try:
        catalog = get_client().creative_resources()
        return {"reachable": True, "recipes": catalog.get("recipes", []), "error": ""}
    except (AutoVideoError, AutoVideoUnreachable) as exc:
        return {"reachable": False, "recipes": [], "error": str(exc)}


@router.get("/api/content-columns")
def list_columns(matrix: str = "", user=Depends(require_viewer)):
    paths, matrix_map, configs, columns = _catalog()
    stats = _stats(columns)
    return {"types": list(content_columns.types(paths.config_dir).values()),
            "columns": [_shown(c, matrix_map, configs, stats) for c in columns.values()
                        if not matrix or c.matrix == matrix]}


@router.get("/api/content-columns/{key}")
def get_column(key: str, user=Depends(require_viewer)):
    _, matrix_map, configs, columns = _catalog()
    column = columns.get(key)
    if column is None:
        raise _error(404, "column_not_found", "没有这个栏目")
    return _shown(column, matrix_map, configs, _stats(columns))


@router.get("/api/content-columns/{key}/scripts")
def column_scripts(key: str, limit: int = Query(10, ge=1, le=50), user=Depends(require_viewer)):
    _, _, _, columns = _catalog()
    c = columns.get(key)
    if c is None:
        raise _error(404, "column_not_found", "没有这个栏目")
    ids = c.production_ids if not c.errors else ()
    places = ",".join("?" for _ in ids) or "NULL"
    where = f"(s.content_column_id = ? OR (s.content_column_id = '' AND s.production IN ({places}))) AND s.archived = 0"
    with store.connect() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM scripts s WHERE {where}", (key, *ids)).fetchone()[0]
        rows = conn.execute(f"""SELECT s.id, s.status, s.current_version, s.updated_at, s.production, s.content_column_id,
            s.topic, v.title FROM scripts s LEFT JOIN script_versions v
            ON v.script_id = s.id AND v.version = s.current_version WHERE {where}
            ORDER BY s.updated_at DESC, s.id DESC LIMIT ?""", (key, *ids, limit)).fetchall()
    import json
    return {"total": total, "items": [{"id": row["id"], "title": row["title"] or json.loads(row["topic"]).get("title", ""),
                                       "status": row["status"], "version": row["current_version"],
                                       "updated_at": row["updated_at"], "production": row["production"],
                                       "legacy_association": not bool(row["content_column_id"])} for row in rows]}


class ColumnIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = ""
    expected_revision: str | None = None
    name: str = Field(min_length=1, max_length=80)
    matrix: str
    type: str
    audience: str = ""
    promise: str = Field(min_length=1, max_length=2000)
    production_ids: list[str] = Field(default_factory=list)
    render_inheritance: str = "source_compatible"
    render: dict = Field(default_factory=dict)
    presentation: dict = Field(default_factory=dict)
    status: str = "configured"
    notes: str = ""
    content_mode: str | None = None


def _save(key: str, body: ColumnIn, user, *, creating: bool):
    paths = get_settings().paths
    existing = content_columns.load_all(paths.config_dir)
    if creating and key in existing:
        raise _error(409, "column_exists", "栏目标识已存在")
    if not creating and key not in existing:
        raise _error(404, "column_not_found", "没有这个栏目")
    if not creating and body.expected_revision is None:
        raise _error(422, "revision_required", "保存栏目需要当前 revision")
    data = body.model_dump(exclude={"key", "expected_revision"})
    data["content_mode"] = body.content_mode or (existing[key].content_mode if key in existing else "episode")
    try:
        content_columns.save(paths.config_dir, key, data,
                             expected_revision="" if creating else body.expected_revision)
    except FileExistsError as exc:
        raise _error(409, "column_changed", str(exc)) from exc
    except (ValueError, TypeError) as exc:
        raise _error(422, "invalid_column", str(exc)) from exc
    audit.log("content_column_create" if creating else "content_column_update", user=user["username"], target=key)
    return get_column(key, user=user)


@router.post("/api/content-columns", status_code=201)
def create_column(body: ColumnIn, user=Depends(require_editor)):
    return _save(body.key, body, user, creating=True)


@router.put("/api/content-columns/{key}")
def update_column(key: str, body: ColumnIn, user=Depends(require_editor)):
    return _save(key, body, user, creating=False)
