"""「A/B 测试」tab (spec §9). Administrators only. Nothing here writes outside ``state/experiments.db``."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import BaseModel, ValidationError

from core.experiments import registry
from core.experiments.definition import Experiment
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
    from core.experiments import checks
    settings = get_settings()
    return checks.start_problems(experiment, store, settings.paths.config_dir, settings.paths.jobs_dir)


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
    from core.experiments import records
    return {**store.get(experiment.id), "duplicates": records.duplicates(store, experiment)}


@router.get("/records")
def records_view(user=Depends(require_admin)) -> dict:
    """「实验记录」(§9a step ⑤): every ended experiment, what it tested and what came of it."""
    from core.experiments import records
    return {"items": records.listing(_store())}


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


@router.get("/{experiment_id}/suggestion")
def suggestion(experiment_id: str, user=Depends(require_admin)) -> dict:
    """「建议改动」: offered once the conclusion is「B 更好」; the user saves it on the existing config page."""
    from core.experiments import adoption
    from core.video import content_columns
    store = _store()
    experiment = _definition(store, experiment_id)
    frozen = store.result(experiment_id) or {}
    verdict = (frozen.get("verdict") or {}).get("verdict")
    if verdict != "b_better":
        return {"verdict": verdict, "items": []}
    settings = get_settings()
    columns = {k: {"name": c.name, "matrix": c.matrix, "notes": c.notes, "presentation": c.presentation, "render": c.render}
               for k, c in content_columns.load_all(settings.paths.config_dir).items()}
    used = {a["column_key"] for a in store.assignments(experiment_id) if a["arm"] == "B"}
    return {"verdict": verdict, "items": adoption.suggestions(experiment, columns, used)}


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


class DecideIn(BaseModel):
    choice: Literal["adopt", "keep", "defer"]
    note: str = ""


@router.post("/{experiment_id}/decide")
def decide(experiment_id: str, body: DecideIn, user=Depends(require_admin)) -> dict:
    """Record what the user chose and the values before the change (§9a step ⑥, §10). Changes no config."""
    from core.experiments import records
    store = _store()
    experiment = _definition(store, experiment_id)
    try:
        records.decide(store, experiment, body.choice, user["username"], get_settings().paths.config_dir, body.note)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    audit.log("experiment_decide", user=user["username"], target=experiment_id, result=body.choice)
    return store.get(experiment_id)


@router.post("/{experiment_id}/{action}")
def move(experiment_id: str, action: Literal["start", "pause", "resume", "end", "queue", "unqueue"], request: Request,
         user=Depends(require_admin)) -> dict:
    store = _store()
    found = store.get(experiment_id)
    if not found:
        raise HTTPException(404, "没有这个实验")
    if action in ("start", "resume", "queue"):
        from core.experiments import checks
        experiment = Experiment.model_validate(found["definition"])
        problems = start_problems(experiment, store)
        if action == "resume":
            problems = [p for p in problems if checks.CONFLICT in p]
        elif action == "queue":
            problems = checks.queue_problems(problems)
        if problems:
            raise HTTPException(409, "；".join(problems))
    try:
        moved = store.move(experiment_id, action, user["username"])
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    audit.log(f"experiment_{action}", user=user["username"], target=experiment_id)
    return moved
