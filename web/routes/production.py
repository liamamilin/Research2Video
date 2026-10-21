"""Production configs: list them, add or remove one, see what a Job's newest report has left to write, write it.

See spec/production.md. A config is a recipe tied to a matrix Job; writing from it is creating a script with that
recipe (the same path as the topics page), with the config recorded on the script.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from core.video import outputs, production, recipes, store
from web import audit
from web.deps import require_editor, require_viewer
from web.models import ApiError
from web.settings import get_settings

router = APIRouter(prefix="/api/production", tags=["production"])


def _error(code: int, kind: str, message: str) -> HTTPException:
    return HTTPException(status_code=code, detail=ApiError.make(kind, message))


def _all() -> dict[str, production.Config]:
    paths = get_settings().paths
    return production.load_all(paths.config_dir, paths.jobs_dir)


def _get(key: str) -> production.Config:
    config = _all().get(key)
    if config is None:
        raise _error(404, "config_not_found", f"没有这套生产配置：{key}")
    return config


@router.get("")
def list_configs(job: str = "", user=Depends(require_viewer)) -> list[dict]:
    paths = get_settings().paths
    made = outputs.count_by_config(paths.config_dir, paths.jobs_dir)  # scripts made before configs count too
    return [{**c.as_dict(), "scripts": made.get(c.key, 0)} for c in _all().values() if not job or c.job == job]


@router.get("/{key}/pending")
def pending(key: str, user=Depends(require_viewer)) -> dict:
    """The newest report's items, each saying whether this config's recipe already wrote it or it was set aside."""
    from web.routes.video import _topics

    config = _get(key)
    if not config.usable:
        raise _error(422, "config_broken", "；".join(config.errors))
    topic_store = _topics()
    report_date, topics = topic_store.load(config.source)
    covered = store.scripts_by_topic()
    dismissed = store.dismissed_topic_ids()
    items = []
    for topic in topics:
        mine = [s for s in covered.get(topic.id, []) if s["column_key"] == config.recipe]
        items.append({**topic.as_dict(), "written": bool(mine), "scripts": mine, "dismissed": topic.id in dismissed})
    return {"report_date": report_date, "report_path": topics[0].report_path if topics else "", "items": items}


class ConfigIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job: str
    recipe: str
    audience: str = ""
    minutes: Optional[float] = None
    reverify: Optional[bool] = None
    auto: Optional[int] = None
    name: str = ""


@router.post("", status_code=201)
def add_config(body: ConfigIn, user=Depends(require_editor)) -> dict:
    paths = get_settings().paths
    every = _all()
    recipe, audience = body.recipe.strip(), body.audience.strip()
    column, _ = production.column_key(recipes.expanded(paths.config_dir), recipe, audience, body.minutes, body.reverify)
    stem = column if production.KEY.fullmatch(column) else "config"  # named after the column it writes with
    key, n = stem, 2
    while key in every:
        key, n = f"{stem}-{n}", n + 1
    production.save(paths.config_dir, key, job=body.job.strip(), recipe=recipe, name=body.name.strip(), audience=audience,
                    minutes=body.minutes, reverify=body.reverify, auto=body.auto)
    made = production.get(paths.config_dir, paths.jobs_dir, key)
    if made is None or not made.usable:
        production.remove(paths.config_dir, key)  # never leave a config that cannot write
        raise _error(422, "invalid_config", "；".join(made.errors) if made else "建不起来")
    audit.log("production_add", user=user["username"], target=key, details={"job": body.job, "recipe": body.recipe, "audience": audience, "minutes": body.minutes,
                                                   "reverify": body.reverify, "auto": body.auto})
    return {**made.as_dict(), "scripts": 0}


class AutoIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    auto: Optional[int] = None  # items written per new report; none or 0 turns it off


@router.patch("/{key}")
def set_auto(key: str, body: AutoIn, user=Depends(require_editor)) -> dict:
    """Turn the unattended writing of a config on or off. Only reports that come out afterwards are written."""
    config = _get(key)
    count = body.auto or None
    if count is not None:
        if not config.usable:
            raise _error(422, "config_broken", "；".join(config.errors))
        if config.split != "each":
            raise _error(422, "auto_needs_each", "盘点一期要挑几条合成，不能自动写：自动只用于每条一期的配方")
        if not 1 <= count <= production.MAX_AUTO:
            raise _error(422, "auto_count", f"每次写 1 到 {production.MAX_AUTO} 条")
    paths = get_settings().paths
    production.set_auto(paths.config_dir, key, count)
    audit.log("production_auto", user=user["username"], target=key, details={"auto": count})
    made = production.get(paths.config_dir, paths.jobs_dir, key)
    return {**made.as_dict(), "scripts": outputs.count_by_config(paths.config_dir, paths.jobs_dir).get(key, 0)}


@router.delete("/{key}")
def remove_config(key: str, user=Depends(require_editor)) -> dict:
    _get(key)
    production.remove(get_settings().paths.config_dir, key)
    audit.log("production_remove", user=user["username"], target=key)
    return {"ok": True, "note": "已经写好的文稿和成片都还在"}


class WriteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topic_ids: list[str] = Field(min_length=1, max_length=10)


@router.post("/{key}/write", status_code=202)
def write(key: str, body: WriteIn, request: Request, user=Depends(require_editor)) -> list[dict]:
    """Each item its own script (每条一期), or the items together in one (挑几条合一期), as the recipe splits."""
    from web.routes import video

    config = _get(key)
    if not config.usable:
        raise _error(422, "config_broken", "；".join(config.errors))
    topic_store = video._topics()
    for topic_id in body.topic_ids:
        found = topic_store.get(topic_id)
        if found is None or found.source != config.source:
            raise _error(422, "not_from_this_job", f"{topic_id} 不是 {config.job} 报告里的条目")
    if config.split == "some":
        asks = [video.ScriptCreateIn(column=config.recipe, topic_ids=body.topic_ids)]
    else:
        asks = [video.ScriptCreateIn(column=config.recipe, topic_id=t) for t in dict.fromkeys(body.topic_ids)]
    made, failed = [], []
    for ask in asks:
        try:
            script = video.create_script(ask, request, user)
        except HTTPException as exc:
            if not made:
                raise
            # the earlier ones are already researching: say what started and what did not, instead of only failing
            failed.append({"topic_id": ask.topic_id, "error": exc.detail})
            continue
        store.update_script(script["id"], production=config.key)
        made.append({**script, "production": config.key})
    audit.log("production_write", user=user["username"], target=key, details={"scripts": len(made), "failed": len(failed)})
    return [*made, *({"failed": f} for f in failed)]
