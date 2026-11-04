"""「A/B 测试」tab (spec §9). Administrators only. Nothing here writes outside ``state/experiments.db``."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import BaseModel, ValidationError

from core.experiments import registry
from core.experiments.definition import Experiment, conflicts
from core.experiments.store import ExperimentStore
from web import audit
from web.deps import require_admin
from web.settings import get_settings

router = APIRouter(prefix="/api/experiments", tags=["experiments"])


def _store() -> ExperimentStore:
    return ExperimentStore(get_settings().paths.state_dir)


def _parse(body: dict) -> Experiment:
    try:
        return Experiment.model_validate(body)
    except ValidationError as exc:
        raise HTTPException(422, "；".join(e["msg"].removeprefix("Value error, ") for e in exc.errors())) from exc


def start_problems(experiment: Experiment, store: ExperimentStore) -> list[str]:
    """Why this draft may not start yet (§9「开始前程序做校验」, §6, §7). Empty means it may."""
    problems = []
    for path in experiment.write_paths():
        switch = registry.find(path)
        if switch and switch.status == "planned":
            problems.append(f"开关「{switch.label}」还没开发（等待开发）")
        elif switch and switch.status == "unconfirmed":
            problems.append(f"开关「{switch.label}」待确认，不能使用")
    if experiment.needs_preview() and not store.preview_confirmed(experiment.id):
        problems.append("画面类、封面类开关要先预览 B 并确认")
    for other in store.active():
        problems += conflicts(experiment, Experiment.model_validate(other["definition"]))
    render_keys = {p.split(".", 1)[1].split(".")[0] for p in experiment.write_paths() if p.startswith("render.")}
    if render_keys:
        from core.video import production
        settings = get_settings()
        for config in production.load_all(settings.paths.config_dir, settings.paths.jobs_dir).values():
            shared = render_keys & set(config.render or {})
            if shared:
                problems.append(f"生产配置「{config.key}」设了 {'、'.join(sorted(shared))}，会盖掉 B 组的值")
    return problems


@router.get("")
def overview(user=Depends(require_admin)) -> dict:
    store = _store()
    return {"enabled": store.enabled(), "experiments": store.list(), "registry": registry.as_rows()}


class SwitchIn(BaseModel):
    enabled: bool


@router.put("/settings")
def set_switch(body: SwitchIn, request: Request, user=Depends(require_admin)) -> dict:
    _store().set_enabled(body.enabled, user["username"])
    audit.log("experiments_switch", user=user["username"], result="on" if body.enabled else "off",
              ip=request.client.host if request.client else None)
    return {"enabled": body.enabled}


@router.post("", status_code=201)
def create(request: Request, body: dict = Body(...), user=Depends(require_admin)) -> dict:
    experiment = _parse(body)
    store = _store()
    if store.get(experiment.id):
        raise HTTPException(409, "已有同名实验，换一个标识")
    store.save(experiment, user["username"])
    audit.log("experiment_create", user=user["username"], target=experiment.id)
    return store.get(experiment.id)


@router.put("/{experiment_id}")
def update(experiment_id: str, request: Request, body: dict = Body(...), user=Depends(require_admin)) -> dict:
    experiment = _parse({**body, "id": experiment_id})
    store = _store()
    if not store.get(experiment_id):
        raise HTTPException(404, "没有这个实验")
    try:
        store.save(experiment, user["username"])
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    audit.log("experiment_update", user=user["username"], target=experiment_id)
    return store.get(experiment_id)


@router.get("/{experiment_id}")
def detail(experiment_id: str, user=Depends(require_admin)) -> dict:
    store = _store()
    found = store.get(experiment_id)
    if not found:
        raise HTTPException(404, "没有这个实验")
    assignments = store.assignments(experiment_id)
    problems = start_problems(Experiment.model_validate(found["definition"]), store) if found["status"] == "draft" else []
    return {**found, "events": store.events(experiment_id), "start_problems": problems,
            "arms": {arm: sum(1 for a in assignments if a["arm"] == arm) for arm in ("A", "B")}}


@router.get("/{experiment_id}/results")
def results_view(experiment_id: str, user=Depends(require_admin)) -> dict:
    """Live numbers, or the frozen snapshot once the wind-down is over (§8). Reading never writes."""
    from core.experiments import results
    store = _store()
    _definition(store, experiment_id)
    frozen = store.result(experiment_id)
    if frozen:
        return {**frozen, "frozen": True}
    live = results.evaluate(store, experiment_id, get_settings().paths.state_dir)
    return {**live, "frozen": False}


class PreviewIn(BaseModel):
    source_render_id: str | None = None


def _definition(store: ExperimentStore, experiment_id: str) -> Experiment:
    found = store.get(experiment_id)
    if not found:
        raise HTTPException(404, "没有这个实验")
    return Experiment.model_validate(found["definition"])


@router.get("/{experiment_id}/preview")
def preview_state(experiment_id: str, user=Depends(require_admin)) -> dict:
    from core.experiments import preview
    store = _store()
    experiment = _definition(store, experiment_id)
    shown = preview.latest(store, experiment_id)
    return {"kind": experiment.preview_kind(), "preview": shown, "confirmed": store.preview_confirmed(experiment_id),
            "sources": preview.sources(get_settings().paths.state_dir, experiment),
            "a_video": f"/api/video/renders/{shown['source_render_id']}/files/video.mp4" if shown else None,
            "b_video": f"/api/experiments/{experiment_id}/preview/video" if shown and "video.mp4" in shown["files"] else None,
            "covers": {arm: f"/api/experiments/{experiment_id}/preview/cover-{arm}" for arm in ("a", "b")}
            if shown and "cover-b.jpg" in shown["files"] else None}


@router.post("/{experiment_id}/preview", status_code=202)
def preview_request(experiment_id: str, body: PreviewIn, user=Depends(require_admin)) -> dict:
    from core.experiments import preview
    store = _store()
    _definition(store, experiment_id)
    try:
        queued = preview.request(store, get_settings().paths.state_dir, experiment_id, user["username"],
                                 body.source_render_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    audit.log("experiment_preview", user=user["username"], target=experiment_id)
    return queued


@router.post("/{experiment_id}/preview/confirm")
def preview_confirm(experiment_id: str, user=Depends(require_admin)) -> dict:
    from core.experiments import preview
    store = _store()
    _definition(store, experiment_id)
    try:
        preview.confirm(store, experiment_id, user["username"])
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    audit.log("experiment_preview_confirm", user=user["username"], target=experiment_id)
    return {"confirmed": True}


@router.get("/{experiment_id}/preview/{name}")
def preview_file(experiment_id: str, name: str, user=Depends(require_admin)):
    from fastapi.responses import FileResponse
    from core.experiments import preview
    store = _store()
    shown = preview.latest(store, experiment_id)
    files = {"video": "video.mp4", "cover-a": "cover-a.jpg", "cover-b": "cover-b.jpg"}
    if not shown or files.get(name) not in shown["files"]:
        raise HTTPException(404, "没有这个预览文件")
    path = preview.folder(get_settings().paths.state_dir, shown["id"]) / files[name]
    if not path.is_file():
        raise HTTPException(404, "没有这个预览文件")
    return FileResponse(path, media_type="video/mp4" if name == "video" else "image/jpeg")


@router.post("/{experiment_id}/{action}")
def move(experiment_id: str, action: Literal["start", "pause", "resume", "end"], request: Request,
         user=Depends(require_admin)) -> dict:
    store = _store()
    found = store.get(experiment_id)
    if not found:
        raise HTTPException(404, "没有这个实验")
    if action in ("start", "resume"):
        experiment = Experiment.model_validate(found["definition"])
        problems = start_problems(experiment, store) if action == "start" else [
            p for p in start_problems(experiment, store) if "写入位置重叠" in p]
        if problems:
            raise HTTPException(409, "；".join(problems))
    try:
        moved = store.move(experiment_id, action, user["username"])
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    audit.log(f"experiment_{action}", user=user["username"], target=experiment_id)
    return moved
