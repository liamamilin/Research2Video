"""Local-first creative workspace; no scheduler or automatic paid calls."""
import json
import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .models import ImportBundle, Project, Revision, creative_prompt
from .store import Conflict, Store

ROOT = Path(__file__).resolve().parents[1]


def create_app(data_dir: Path | None = None) -> FastAPI:
    api = FastAPI(title="音乐创作工坊", version="0.1.0")
    store = Store((data_dir or Path(os.environ.get("MUSIC_WORKSHOP_DATA_DIR", ROOT / "data"))) / "workshop.sqlite3")
    api.state.store = store

    @api.exception_handler(KeyError)
    async def not_found(_request: Request, _error: KeyError):
        return JSONResponse(status_code=404, content={"detail": "作品不存在"})

    @api.exception_handler(Conflict)
    async def conflict(_request: Request, error: Conflict):
        return JSONResponse(status_code=409, content={"detail": str(error)})

    @api.middleware("http")
    async def local_mutations(request: Request, call_next):
        # Browser writes must originate from this local application's origin.
        if request.method in {"POST", "PUT", "DELETE", "PATCH"}:
            origin = request.headers.get("origin")
            if origin and origin != str(request.base_url).rstrip("/"):
                return JSONResponse(status_code=403, content={"detail": "请从本地工作台保存作品"})
        return await call_next(request)

    @api.get("/api/health")
    def health():
        return {"status": "ok", "version": "0.1.0", "scheduler": False}

    @api.get("/api/projects")
    def projects():
        return store.list()

    @api.post("/api/projects", status_code=201)
    def create(project: Project):
        return store.save(project)

    @api.get("/api/projects/{project_id}")
    def get(project_id: str):
        return store.get(project_id)

    @api.put("/api/projects/{project_id}")
    def update(project_id: str, revision: Revision):
        return store.save(revision.project, project_id, revision.version)

    @api.get("/api/projects/{project_id}/prompt")
    def prompt(project_id: str):
        return {"text": creative_prompt(Project.model_validate(store.get(project_id)["project"]))}

    @api.get("/api/projects/{project_id}/export")
    def export(project_id: str):
        row = store.get(project_id)
        bundle = {"schema_version": 1, "project": row["project"]}
        return Response(json.dumps(bundle, ensure_ascii=False, indent=2), media_type="application/json",
                        headers={"Content-Disposition": f'attachment; filename="music-project-{project_id}.json"'})

    @api.post("/api/import", status_code=201)
    def import_project(bundle: ImportBundle):
        return store.save(bundle.project)

    @api.get("/api/example")
    def example():
        return ImportBundle.model_validate_json((ROOT / "examples/blue-button.json").read_text())

    @api.get("/")
    def home():
        return FileResponse(ROOT / "web/index.html")

    api.mount("/static", StaticFiles(directory=ROOT / "web"), name="static")
    return api


app = create_app()
