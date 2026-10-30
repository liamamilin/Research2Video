"""Retry the original engine job and resume its linked console download lifecycle."""
import json
import hashlib
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT.parent / "AI_research/Base_CodingCLi"))


def main():
    from core.video import store
    from web.services import video_render
    from web.settings import get_settings
    from core.fileio import file_lock
    script_id = "6276a698e1344996a2003b071eeb2048"
    render_id = "e86100649a83470c931607fde27d15ee"
    engine_job = "0ab6095d-ab66-475e-b027-171c41d320a0"
    out = Path(__file__).resolve().parent
    script, render = store.get_script(script_id), store.get_render(render_id)
    assert script["status"] == "APPROVED" and script["approved_version"] == render["version"] == 1
    assert render["script_id"] == script_id and render["autovideo_job_id"] == engine_job
    client = video_render.get_client()
    for duplicate in store.list_renders(script_id):
        if duplicate["id"] == render_id or duplicate["status"] not in store.ACTIVE_RENDER_STATUSES:
            continue
        canonical = lambda body: {k: v for k, v in (body or {}).items() if k != "idempotency_key"}
        assert duplicate["status"] == "QUEUED" and duplicate["version"] == render["version"]
        assert canonical(duplicate["request"]) == canonical(render["request"]), "A different production request is active"
        other = client.get_job(duplicate["autovideo_job_id"])
        assert other["status"] == "QUEUED" and other["attempt_count"] == 0, "Do not interrupt a running request"
        client._request("POST", f"/api/jobs/{other['job_id']}/cancel")
        lock = Path(get_settings().paths.state_dir) / "video_render_locks" / hashlib.sha256(duplicate["id"].encode()).hexdigest()
        with file_lock(str(lock), timeout=30):
            store.update_render(duplicate["id"], status="CANCELLED", completed_at=video_render._now(),
                                error_message="相同参数的排队请求已取消，改为复用原任务音轨和分镜恢复")
        (out / "duplicate-cancelled.json").write_text(json.dumps({"render_id": duplicate["id"],
            "engine_job": other["job_id"], "previous_status": "QUEUED", "same_frozen_request": True}, indent=2))
    before = out / "before.json"
    if not before.exists():
        before.write_text(json.dumps({k: render[k] for k in ("id", "script_id", "version", "status", "error_code", "error_message", "attempt_count", "completed_at", "source_sha256")}, ensure_ascii=False, indent=2))
    started = time.monotonic()
    remote = client.get_job(engine_job)
    if remote["status"] == "FAILED":
        assert remote.get("retryable"), "Failed task is not eligible for a retry"
        client._request("POST", f"/api/jobs/{engine_job}/retry")
        remote = client.get_job(engine_job)
    assert remote["status"] in {"QUEUED", "RUNNING", "COMPLETED"}, remote["status"]
    if render["status"] != "COMPLETED":
        store.update_render(render_id, status="DOWNLOADING" if remote["status"] == "COMPLETED" else "QUEUED",
                            active_stage=remote.get("active_stage"), completed_at=None, error_code=None, error_message=None)
        print(json.dumps({"render_id": render_id, "engine_job": engine_job, "status": remote["status"]}), flush=True)
        result = video_render.process(render_id, client=client)
        assert result == "COMPLETED", store.get_render(render_id).get("error_message")
    final = store.get_render(render_id)
    receipt = {"script_id": script_id, "render_id": render_id, "engine_job": engine_job,
        "status": final["status"], "attempt_count": final["attempt_count"], "version": final["version"],
        "recovery_seconds": round(time.monotonic()-started, 3), "source_sha256": final["source_sha256"],
        "video": str(video_render.files_dir(render_id).resolve() / "video.mp4"),
        "artifacts": [{"name": f["name"], "size": f.get("size")} for f in final["artifacts"]]}
    (out / "result.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
    print(json.dumps(receipt, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
