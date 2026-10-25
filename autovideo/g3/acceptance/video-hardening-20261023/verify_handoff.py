"""Exercise both real applications in-process, using a real MP4 and injected transport failures.

This verifies submission/download recovery, not new LLM or TTS generation.
Use fresh --output paths; previous outputs are never overwritten.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace

import httpx
from fastapi.testclient import TestClient


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--research-root", type=Path, required=True)
    parser.add_argument("--media", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    project = Path(__file__).resolve().parents[3]
    sys.path[:0] = [str(project), str(args.research_root.resolve(strict=True))]
    from g3.app import create_app
    from g3.config import Settings
    from g3.store import sha256_bytes
    from core.video import store
    from core.video.autovideo import AutoVideoClient
    from web.services import video_render

    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    media = args.media.resolve(strict=True)
    counts = {"submit": 0, "lookup": 0, "video.mp4": 0, "qa.json": 0}
    settings = Settings(data_dir=out / "autovideo", project_root=project)
    with TestClient(create_app(settings, start_worker=False, preflight=lambda _: {"ready": True, "checks": {}})) as api:
        engine = api.app.state.store
        def handle(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            response = api.request(request.method, str(request.url.copy_with(scheme="http", host="testserver")),
                                   content=request.content, headers={"Content-Type": "application/json"})
            if request.method == "POST" and path == "/api/jobs":
                counts["submit"] += 1
                assert response.status_code == 202, response.text
                job_id = response.json()["job_id"]
                claim = engine.claim_next()
                assert claim["job_id"] == job_id
                root = engine.data_dir / claim["workspace_rel_path"]
                root.mkdir(parents=True)
                shutil.copyfile(media, root / "video.mp4")
                (root / "qa.json").write_text('{"status":"PASS","scope":"transport fixture"}')
                engine.mark_completed(job_id, claim["attempt_no"], [
                    {"name": name, "relative_path": name, "content_type": kind,
                     "bytes": (root / name).stat().st_size, "sha256": sha256_bytes((root / name).read_bytes())}
                    for name, kind in (("video.mp4", "video/mp4"), ("qa.json", "application/json"))])
                # Server committed the task; the caller never received its ID.
                raise httpx.ReadTimeout("lost submission response", request=request)
            if path == "/api/jobs":
                counts["lookup"] += 1
            if path.endswith("/artifacts"):
                data = response.json()
                data["items"].sort(key=lambda item: item["name"] != "video.mp4")
                return httpx.Response(response.status_code, json=data)
            if "/artifacts/" in path:
                name = path.rsplit("/", 1)[-1]
                counts[name] += 1
                if name == "qa.json" and counts[name] == 1:
                    raise httpx.ReadTimeout("copy interrupted after the MP4", request=request)
            return httpx.Response(response.status_code, content=response.content, headers=response.headers)

        state = out / "research"
        store.use_state_dir(str(state))
        store.init_db()
        video_render.get_settings = lambda: SimpleNamespace(paths=SimpleNamespace(state_dir=state))
        video_render.poll_seconds = lambda: 0
        video_render.offline_mounts = lambda: []
        text = "这是文稿到视频接入的离线故障验收，检查提交身份、响应丢失恢复和成片下载复用。" * 2
        chapters = [{"title": "接入验收", "paragraphs": [text]}]
        source, digest = video_render.narration_source(chapters)
        script = store.create_script("fixture", {"title": "接入验收"})
        render = store.create_render(script["id"], 1, title="接入验收", source_text=source, source_sha256=digest,
            request={"title": "接入验收", "input_mode": "narration", "chapters": chapters,
                     "duration_mode": "content", "video_form": "information_motion", "packaging": {"enabled": False}})
        client = AutoVideoClient("http://testserver", transport=httpx.MockTransport(handle))
        assert video_render.process(render["id"], client=client) == "COMPLETED"
        saved = store.get_render(render["id"])
        downloaded = video_render.files_dir(render["id"]) / "video.mp4"
        assert sha256_bytes(downloaded.read_bytes()) == sha256_bytes(media.read_bytes())
        assert counts == {"submit": 1, "lookup": 1, "video.mp4": 1, "qa.json": 2}
        result = {"status": "PASS", "scope": "real API/store/client transport recovery; no model or TTS calls",
                  "counts": counts, "job_count": engine.list_jobs()["total"], "render_status": saved["status"],
                  "video_bytes": media.stat().st_size, "video_sha256": sha256_bytes(media.read_bytes()),
                  "avoided_repeat_video_transfer_bytes": media.stat().st_size,
                  "job_id": saved["autovideo_job_id"], "render_id": saved["id"]}
        (out / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
