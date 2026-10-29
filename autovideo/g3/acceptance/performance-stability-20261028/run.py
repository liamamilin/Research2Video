"""Small real API/worker video and a warm voice-cache replay; isolated job store."""
import json
from pathlib import Path
import shutil
import sys
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def main():
    retry = "--retry" in sys.argv
    from fastapi.testclient import TestClient
    from g3.app import create_app
    from g3.config import Settings
    out = Path(__file__).resolve().parent / "real"
    if retry:
        location = json.loads((out / "location.json").read_text())
        data = Path(location["engine_data"])
    else:
        out.mkdir(exist_ok=False)
        data = ROOT / "g3/data/acceptance-performance-stability-20261028" / uuid4().hex
        data.mkdir(parents=True)
        shutil.copyfile(ROOT / "g3/data/llm-settings.json", data / "llm-settings.json")
        (data / "llm-settings.json").chmod(0o600)
    payload = {"title": "视频生产状态与章节复用", "input_mode": "narration", "duration_mode": "content",
        "chapters": [
            {"title": "状态更新", "paragraphs": ["视频从写稿进入制作时，页面需要继续更新。正在工作的任务显示进度，完成之后显示可以播放的成片。"]},
            {"title": "章节复用", "paragraphs": ["长文稿可以分成独立章节。如果后面的配音失败，已经完成的章节应当保留，重试时继续生成剩下的内容。"]}],
        "voice_id": "voxcpm_anchor", "production_mode": "fast", "visual_storytelling": True,
        "use_draw_things": False, "packaging": {"sound": {"enabled": False}}}
    save(out / "input.json", payload)
    started = time.monotonic()
    with TestClient(create_app(Settings(data_dir=data, runner_timeout_seconds=1800), start_worker=True)) as engine:
        response = engine.post(f"/api/jobs/{location['job_id']}/retry") if retry else engine.post("/api/jobs", json=payload)
        assert response.status_code == 202, response.text
        jid = response.json()["job_id"]
        save(out / "location.json", {"job_id": jid, "engine_data": str(data)})
        print(json.dumps({"job_id": jid, "engine_data": str(data)}), flush=True)
        previous = None
        while time.monotonic() - started < 1800:
            job = engine.get(f"/api/jobs/{jid}").json()
            status = (job["status"], job.get("active_stage"))
            if status != previous:
                print(json.dumps(status, ensure_ascii=False), flush=True)
                previous = status
            if status[0] in {"COMPLETED", "FAILED", "CANCELLED", "NEEDS_INPUT"}:
                break
            time.sleep(2)
        save(out / "job.json", job)
        assert job["status"] == "COMPLETED", {k: job.get(k) for k in ("status", "error_code", "error_message")}
        for name in ("content.mp4", "video.mp4", "video-ir.json", "qa.json", "package-qa.json"):
            response = engine.get(f"/api/jobs/{jid}/artifacts/{name}")
            assert response.status_code == 200, name
            (out / name).write_bytes(response.content)
    attempt = max((data / "jobs" / jid / "attempts").iterdir(), key=lambda p: int(p.name)) / "g2-run"
    for name in ("voice-input.json", "voice-report.json", "production-execution.json", "caption-alignment.json"):
        shutil.copyfile(attempt / "artifacts" / name, out / name)
    save(out / "receipt.json", {"job_id": jid, "attempt": str(attempt), "seconds": round(time.monotonic()-started, 3),
        "normal_api_and_worker": True, "real_model": True, "real_local_voice": True})
    print(json.dumps({"completed": jid, "seconds": round(time.monotonic()-started, 3)}), flush=True)


if __name__ == "__main__":
    main()
