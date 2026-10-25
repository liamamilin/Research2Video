"""Real worker/TTS/MP4 acceptance for camera, sequential targets, annotations and comparison.

The public documentation image is real. The price image is a clearly labelled
synthetic HTML fixture; it demonstrates layout only and is not product evidence.
"""
import argparse
import json
import sys
import time
from pathlib import Path
from uuid import uuid4

RESEARCH = Path(__file__).resolve().parents[3]
WORKSPACE = RESEARCH.parents[1]
sys.path[:0] = [str(RESEARCH), str(WORKSPACE / "autovideo")]
from fastapi.testclient import TestClient
from g3.app import create_app
from g3.config import Settings

parser = argparse.ArgumentParser()
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--fixtures", type=Path, required=True)
args = parser.parse_args()
out = args.output.resolve()
out.mkdir(parents=True, exist_ok=False)
data = WORKSPACE / "autovideo/g3/data/acceptance-automatic-visuals" / uuid4().hex
source = RESEARCH / "spec/acceptance/automatic-visuals-20261025/source-region.png"
price = args.fixtures.resolve() / "monthly_price/capture.png"
regions = [
    {"x1": 10, "y1": 35, "x2": 425, "y2": 200, "label": "指定元素截图", "style": "box"},
    {"x1": 8, "y1": 260, "x2": 570, "y2": 410, "label": "单个元素的截图说明", "style": "circle"},
    {"x1": 10, "y1": 750, "x2": 900, "y2": 960, "label": "元素定位与截图代码", "style": "underline"}]
from PIL import Image
pw, ph = Image.open(price).size
results = []
with TestClient(create_app(Settings(data_dir=data, runner_timeout_seconds=300), start_worker=True)) as engine:
    ids = []
    for path in (source, price):
        response = engine.post("/api/creative-images", files={"file": (path.name, path.read_bytes(), "image/png")})
        assert response.status_code == 201, response.text
        ids.append(response.json()["image_id"])
    material = {"image_id": ids[0], "caption": "Playwright 元素截图说明", "role": "detail",
                "focus": {"width": 847, "height": 262, "regions": regions}}
    other = {"image_id": ids[1], "caption": "虚构测试价格页，仅作布局演示", "role": "comparison",
             "focus": {"width": pw, "height": ph, "regions": [{"x1": 10, "y1": 250, "x2": 500, "y2": 500, "style": "highlight", "label": "测试计费区域"}]}}
    for name, form, text, materials in [
        ("continuous-focus", "object_showcase", "先看页面中的元素截图标题，再看下面的功能说明，接着看截图代码示例，整个镜头始终保留来源页面的布局关系，帮助观众跟随讲解找到当前正在说明的内容。", [material]),
        ("side-by-side", "dynamic_collage", "左侧展示指定元素的截图说明，右侧展示用于测试的月付价格页面，两张图片并列显示，观众可以同时看到不同页面的重点内容。", [material, other])]:
        folder = out / name
        folder.mkdir()
        response = engine.post("/api/jobs", json={"title": "截图聚焦镜头验收", "input_mode": "narration",
            "chapters": [{"title": "截图聚焦", "paragraphs": [text]}], "duration_mode": "content", "video_form": form,
            "image_materials": materials, "voice_id": "zf_xiaoxiao", "production_recipe": "science_explainer", "use_draw_things": False})
        assert response.status_code == 202, response.text
        job_id = response.json()["job_id"]
        print(json.dumps({"case": name, "job_id": job_id}, ensure_ascii=False), flush=True)
        deadline = time.monotonic()+300
        while time.monotonic() < deadline:
            job = engine.get(f"/api/jobs/{job_id}").json()
            if job["status"] in {"COMPLETED", "FAILED", "CANCELLED", "NEEDS_INPUT"}:
                break
            time.sleep(.5)
        (folder / "job.json").write_text(json.dumps(job, ensure_ascii=False, indent=2))
        assert job["status"] == "COMPLETED", job
        for artifact in ("content.mp4", "video.mp4", "video-ir.json", "qa.json", "package-qa.json", "execution-log.txt"):
            result = engine.get(f"/api/jobs/{job_id}/artifacts/{artifact}")
            assert result.status_code == 200
            (folder / artifact).write_bytes(result.content)
        ir = json.loads((folder / "video-ir.json").read_text())
        assert ir["scenes"][0]["props"]["images"][0]["focus"]["regions"] == regions
        qa = json.loads((folder / "qa.json").read_text())
        result = {"case": name, "job_id": job_id, "status": "PASS", "seconds": qa["video"]["duration_seconds"],
                  "full_decode": qa["video"]["full_decode"], "real_tts": qa["real_tts"], "scene_count": len(ir["scenes"])}
        results.append(result)
        print(json.dumps(result, ensure_ascii=False), flush=True)
(out / "qa.json").write_text(json.dumps({"engine_data": str(data), "fixtures_labelled": True, "cases": results}, ensure_ascii=False, indent=2))
