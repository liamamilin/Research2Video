"""Real anonymous browser → local OCR → registration → real worker/TTS/MP4 → verified download.

All data is isolated. The engine keeps attempts under its ignored data directory;
the research ledger and downloads go to the explicit output directory. No publish.
"""
import argparse
import json
import os
import sys
from pathlib import Path
from uuid import uuid4

RESEARCH = Path(__file__).resolve().parents[3]
WORKSPACE = RESEARCH.parents[1]
AUTOVIDEO = WORKSPACE / "autovideo"
sys.path[:0] = [str(RESEARCH), str(AUTOVIDEO)]
parser = argparse.ArgumentParser()
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--semantic", action="store_true")
parser.add_argument("--vision", action="store_true")
args = parser.parse_args()
out = args.output.resolve()
out.mkdir(parents=True, exist_ok=False)
for folder in ("config", "state", "jobs", "output", "logs"):
    (out / folder).mkdir()
os.environ["AREC_NO_AUTOPILOT_LOOP"] = "1"
os.environ["AI_RESEARCH_ENV"] = "development"
os.environ["AI_RESEARCH_WEB_CONFIG"] = str(out / "web.yaml")

import httpx
import yaml
from fastapi.testclient import TestClient
from core.config import load_system_config
from core.video import length, store
from core.video.autovideo import AutoVideoClient
from tests import golden_video_cells
from g3.app import create_app
from g3.config import Settings

golden_video_cells.install_recipes(out / "config")
policy = {"enabled": True, "version": 3, "max_pages": 3, "max_images": 5, "max_seconds": 120,
          "semantic_review": args.semantic, "ocr_review": True, "max_model_calls": 2}
if args.vision:
    policy.update(vision_section="visual_ai", vision_required=True, vision_timeout=60)
system = load_system_config(str(RESEARCH / "config"))
if args.vision:
    system["visual_ai"] = {"model": "yanying-vision:8b", "base_url": "http://127.0.0.1:11434/v1",
                           "api_key_env": "", "stream": False, "extra_body": {"response_format": {"type": "json_object"}}}
system["video"] = {"visual_materials": policy, "poll_seconds": .35}
(out / "config/system.yaml").write_text(yaml.safe_dump(system, allow_unicode=True))
(out / "web.yaml").write_text(yaml.safe_dump({"server": {"host": "127.0.0.1", "port": 18769},
    "auth": {"secret_key": uuid4().hex + uuid4().hex, "cookie_secure": False},
    "paths": {name + "_dir": str(out / name) for name in ("config", "state", "jobs", "output", "logs")}}))
from web.services import video_render
from web.settings import get_settings

store.use_state_dir(str(out / "state"))
store.init_db()
from core.video import image_quality
image_quality.ocr_binary(timeout=90)  # Environment setup, outside the per-render acquisition budget.
script = store.create_script("deep_dive", {"title": "网页截图与来源核对", "fields": {}}, created_by="isolated-visual-acceptance")
url = "https://playwright.dev/python/docs/screenshots"
sections = [{"title": "画面与来源", "paragraphs": [
    {"text": "先说明这期内容的来源和适用范围。", "citations": [], "evidence": "UNCITED"},
    {"text": "Playwright 支持截取页面中的指定元素。", "citations": [url], "evidence": "CITED"},
    {"text": "截图保留来源与获取时间，供后续核对。", "citations": [], "evidence": "UNCITED"}]}]
report = out / "output/source.md"
report.write_text("# 网页截图\n\nPlaywright 支持指定元素截图。\n\n## 参考来源\n\n1. Playwright 截图说明 | 一手 | 持续更新 | " + url + "\n")
store.add_version(script["id"], title="网页截图与来源核对", sections=sections,
                  length_check=length.measure(sections, 6), edit_type="EDITED")
store.update_script(script["id"], status="APPROVED", approved_version=1, report_path=str(report))
engine_data = AUTOVIDEO / "g3/data/acceptance-automatic-visuals" / uuid4().hex
settings = Settings(data_dir=engine_data, runner_timeout_seconds=300)
with TestClient(create_app(settings, start_worker=True)) as engine:
    def transport(request):
        response = engine.request(request.method, request.url.raw_path.decode(), content=request.content, headers=dict(request.headers))
        return httpx.Response(response.status_code, content=response.content, headers=response.headers)
    client = AutoVideoClient("http://127.0.0.1:8011", transport=httpx.MockTransport(transport))
    video_render.get_client = lambda: client
    render = video_render.create(store.get_script(script["id"]), voice_id="zf_xiaoxiao")
    print(json.dumps({"phase": "created", "render_id": render["id"], "engine_data": str(engine_data)}, ensure_ascii=False), flush=True)
    result = video_render.process(render["id"], client=client)
    finished = store.get_render(render["id"])
    (out / "render.json").write_text(json.dumps(finished, ensure_ascii=False, indent=2))
    if result != "COMPLETED":
        raise RuntimeError(str({"status": result, "error": finished.get("error_message"), "visuals": finished.get("visuals", {}).get("reason")}))
    assert finished["visuals"]["state"] == "completed", finished["visuals"]
    assert len(finished["submission_request"]["image_materials"]) >= 1
    assert finished["submission_request"]["image_materials"][0]["placements"] == [{"chapter_index": 0, "paragraph_index": 1}]
    if args.vision:
        vision = finished["visuals"]["decisions"][0]["quality"]["image"]["vision"]
        assert vision["state"] == "passed" and "c1p2" in vision["paragraph_ids"]
        assert finished["submission_request"]["image_materials"][0]["focus"]["regions"]
    job_id = finished["autovideo_job_id"]
    ir = json.loads(engine.get(f"/api/jobs/{job_id}/artifacts/video-ir.json").content)
    mapped = [s for s in ir["scenes"] if s["component"] == "object_showcase"]
    text = [s for s in ir["scenes"] if s["component"] == "kinetic_text"]
    assert mapped and text
    for scene in mapped:
        assert "Playwright" in scene["props"]["text"]
    qa = {"status": "PASS", "render_id": render["id"], "engine_job_id": job_id,
          "engine_data": str(engine_data), "visuals": finished["visuals"], "mapped_scenes": len(mapped),
          "text_scenes": len(text), "voice": "real local Kokoro Chinese TTS", "real_renderer": True,
          "verified_downloads": len(finished["artifacts"]), "files": str(out / "state/video_renders" / render["id"])}
    (out / "qa.json").write_text(json.dumps(qa, ensure_ascii=False, indent=2))
    print(json.dumps({k: qa[k] for k in ("status", "render_id", "engine_job_id", "mapped_scenes", "text_scenes", "verified_downloads", "files")}, ensure_ascii=False), flush=True)
