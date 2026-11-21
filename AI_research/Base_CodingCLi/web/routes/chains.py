from contextlib import contextmanager
from fastapi import APIRouter, Depends, HTTPException
from web.deps import require_editor, require_viewer
from web.services import chains, chain_runner, source_registry
from core import sources
from web.services import chain_access

router = APIRouter(prefix="/api/chains", tags=["chains"])


@contextmanager
def run_admission():
    from web.services.assistant_maintenance import run_admission as admit

    try:
        with admit():
            yield
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("")
def listing(user=Depends(require_viewer)):
    configs = chains.production.load_all(chains.paths().config_dir, chains.paths().jobs_dir)
    return {
        "items": chains.summary(configs=configs),
        "archived": chains.all_chains(True, configs=configs),
        "role": user["role"],
        "access": chain_access.snapshot(),
    }


@router.get("/catalog")
def catalog(user=Depends(require_viewer)):
    snap = source_registry.snapshot()
    return {
        **snap,
        "access": chain_access.snapshot(),
        "chains": [
            {"key": m["key"], "job": m["job"], "name": m["name"]}
            for m in chains.all_chains()
        ],
    }


@router.post("/sources/preview")
def source_preview(body: dict, user=Depends(require_editor)):
    specs = sources.resolve(body.get("sources", []), chains.paths().config_dir)
    results = []
    for spec in specs[:4]:
        try:
            items = sources.fetch_source(spec, limit=3, timeout=15)
            results.append(
                {
                    "spec": spec,
                    "items": [
                        {"title": x.title, "url": x.url, "date": x.publish_date}
                        for x in items
                    ],
                    "error": "" if items else "未取得条目，请检查来源类型或地址",
                }
            )
        except Exception as e:
            results.append({"spec": spec, "items": [], "error": str(e)})
    return {"results": results}


@router.post("", status_code=201)
def create(body: dict, user=Depends(require_editor)):
    chain_access.require_write(create=True)
    try:
        return chains.create(body, user)
    except (ValueError, KeyError) as e:
        raise HTTPException(422, str(e)) from e


@router.get("/runs/{rid}")
def run_detail(rid: str, user=Depends(require_viewer)):
    return chain_runner.get(rid)


@router.post("/runs/{rid}/cancel")
def cancel(rid: str, user=Depends(require_editor)):
    chain_access.require_write(chain_runner.get(rid)["chain_id"])
    if chain_runner.get(rid).get("native_schedule") and user["role"] != "admin":
        raise HTTPException(403, "停止周期执行需要管理员")
    return chain_runner.cancel(rid)


@router.post("/runs/{rid}/retry")
def retry(rid: str, user=Depends(require_editor)):
    chain_access.require_write(chain_runner.get(rid)["chain_id"])
    with run_admission():
        return chain_runner.retry(rid, user)


@router.get("/{key}")
def get(key: str, user=Depends(require_viewer)):
    return chains.detail(key)


@router.put("/{key}")
def update(key: str, body: dict, user=Depends(require_editor)):
    chain_access.require_write(key)
    try:
        return chains.update(key, body, user)
    except (ValueError, KeyError) as e:
        raise HTTPException(422, str(e)) from e


@router.post("/{key}/toggle")
def toggle(key: str, body: dict, user=Depends(require_editor)):
    chain_access.require_write(key)
    return chains.toggle(key, body, user)


@router.get("/{key}/delete-preview")
def delete_preview(key: str, user=Depends(require_viewer)):
    return chains.delete_preview(key)


@router.delete("/{key}")
def delete(key: str, revision: str, user=Depends(require_editor)):
    chain_access.require_write(key)
    preview = chains.delete_preview(key)
    if (
        not preview.get("archive_only")
        and not preview["keep_job"]
        and chains.detail(key)["schedules"]
        and user["role"] != "admin"
    ):
        raise HTTPException(403, "删除研究周期需要管理员；可先移除本链路视频输出")
    return chains.delete(key, revision)


@router.post("/{key}/run", status_code=202)
def run(key: str, body: dict, user=Depends(require_editor)):
    chain_access.require_write(key)
    with run_admission():
        return chain_runner.start(key, body, user)


@router.get("/{key}/scope")
def scope(key: str, user=Depends(require_viewer)):
    return chains.scope(key)
