"""Real local Qwen3-VL image understanding, paragraph choice and focus suggestions.

No mock model and no generated images. Inputs are the public-page screenshot and
the controlled Chrome price fixtures already produced by the other acceptance scripts.
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path
from uuid import uuid4

RESEARCH = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(RESEARCH))
parser = argparse.ArgumentParser()
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--fixtures", type=Path, required=True)
parser.add_argument("--model", default="yanying-vision:8b")
args = parser.parse_args()
out = args.output.resolve()
out.mkdir(parents=True, exist_ok=False)
(out / "config").mkdir()
import yaml
config = {"visual_ai": {"model": args.model, "base_url": "http://127.0.0.1:11434/v1", "api_key_env": "",
                        "stream": False, "extra_body": {"response_format": {"type": "json_object"}}}}
(out / "config/system.yaml").write_text(yaml.safe_dump(config))
(out / "web.yaml").write_text(yaml.safe_dump({"auth": {"secret_key": uuid4().hex + uuid4().hex},
    "paths": {"config_dir": str(out / "config"), "state_dir": str(out / "state")}}))
os.environ["AI_RESEARCH_WEB_CONFIG"] = str(out / "web.yaml")
os.environ["AI_RESEARCH_ENV"] = "development"
from core.video import image_quality, screenshots, visual_materials

feature = {"id": "c1p2", "text": "Playwright 支持截取页面中的指定元素。", "claims": "Playwright 支持截取页面中的指定元素。",
           "kind": "feature", "urls": ["https://playwright.dev/python/docs/screenshots"]}
price = {"id": "c1p3", "text": "Atlas Pro 月付价格为每月20美元。", "claims": "Atlas Pro 月付价格为每月20美元。", "kind": "pricing"}
intro = {"id": "c1p1", "text": "下面介绍产品背景和研究范围。", "claims": "介绍产品背景和研究范围。", "kind": "text"}
fixtures = args.fixtures.resolve()
cases = [
    ("public_feature_match", RESEARCH / "spec/acceptance/automatic-visuals-20261025/source-region.png", feature, True),
    ("feature_is_not_a_price_table", RESEARCH / "spec/acceptance/automatic-visuals-20261025/source-region.png", price, False),
    ("price_match", fixtures / "monthly_price/capture.png", price, True),
    ("price_is_not_a_feature", fixtures / "monthly_price/capture.png", feature, False),
    ("wrong_visible_amount", fixtures / "pixel_price_changed/capture.png", price, False),
]
results = []
for name, image, need, expected in cases:
    policy = visual_materials.policy({"enabled": True, "version": 3, "vision_section": "visual_ai", "vision_required": True, "vision_timeout": 60})
    policy["_paragraphs"] = [intro, feature, price]
    started = time.monotonic()
    review = {}
    try:
        review = image_quality.multimodal_review(image.read_bytes(), need, policy, remaining=60)
        accepted, reason = True, review["reason"]
    except screenshots.ScreenshotError as exc:
        accepted, reason = False, str(exc)
    understood = bool(review) or reason.startswith(("视觉模型检查未通过", "图片内容没有匹配", "视觉模型识别的价格条件不符"))
    result = {"case": name, "image": str(image), "expected_accepted": expected, "accepted": accepted,
              "seconds": round(time.monotonic()-started, 3), "reason": reason, "review": review,
              "model_calls": policy.get("_model_calls", 0), "semantic_completed": understood, "passed": expected == accepted and understood}
    results.append(result)
    print(json.dumps(result, ensure_ascii=False), flush=True)
    (out / "qa.json").write_text(json.dumps({"model": args.model, "real_local_vision": True, "cases": results}, ensure_ascii=False, indent=2))
assert all(row["passed"] for row in results)
