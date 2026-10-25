"""Rerender a preserved real video IR with the current packaging library.

No model calls or new TTS: this isolates renderer, packaging and media checks.
Output must be a new directory within the AutoVideo checkout.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import sys

PROJECT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--label", default="组件库验收")
    args = parser.parse_args()
    source = args.source_run.resolve(strict=True)
    out = args.output.resolve()
    if not source.is_relative_to(PROJECT) or not out.is_relative_to(PROJECT):
        raise ValueError("验收输入输出须在项目内")
    out.mkdir(parents=True, exist_ok=False)
    shutil.copytree(source / "fixtures", out / "fixtures")
    (out / "artifacts").mkdir()
    (out / "input").mkdir()
    original_input = source.parents[2] / "input" / "input.json"
    payload = json.loads(original_input.read_text()) if original_input.is_file() else {}
    # This is explicitly test metadata, not an assertion about the reused video's sources.
    payload.pop("package", None)
    payload.pop("packaging_resolved", None)
    payload["packaging"] = {"preset": "common", "recipe": "acceptance", "label": args.label}
    payload["sources"] = [{"title": "组件库验收参考资料", "url": "https://www.remotion.dev/docs", "date": "2026-10-23"}]
    from g2.packaging_library import prepare_request
    payload = prepare_request(payload)
    (out / "input/input.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    (out / "input/source.txt").write_text("组件库验收：复用已保存的真实讲稿、语音和分镜，不重新生成内容。")
    for name in ("captions.srt", "chapters.json", "narration-plan.json", "alignment.json"):
        if (source / "artifacts" / name).is_file():
            shutil.copyfile(source / "artifacts" / name, out / "artifacts" / name)
    os.environ.update(AUTOVIDEO_G2_RUN_DIR=str(out), AUTOVIDEO_G2_RUN_ID=out.name,
                      AUTOVIDEO_G2_DURATION_MODE="content", AUTOVIDEO_G2_SOURCE_FILE=str(out / "input/source.txt"),
                      AUTOVIDEO_G2_INTENT_FILE=str(out / "input/input.json"))
    from g2 import run_g2 as pipeline
    from g1.video_ir import validate_video_ir
    compiled = validate_video_ir(json.loads(pipeline.IR_PATH.read_text()), pipeline.FIXTURE_ROOT)
    pipeline.render(compiled, renderer_root=out / "renderer")
    qa = pipeline.verify_media(compiled, {})
    qa.update(real_llm=False, narration_reused=True,
              scope="Current renderer and packaging; preserved real narration/IR; no model calls")
    pipeline.write_json(pipeline.ARTIFACTS / "qa.json", qa)
    result = pipeline.run_package_stage(pipeline.package_request_from_intent())
    pipeline.write_manifest(renderer_root=out / "renderer")
    assert result["intro"]["frames"] > 0 and result["outro"]["frames"] > 0 and result["source_card"]["frames"] > 0
    print(json.dumps({"status": result["status"], "output": str(out / "artifacts/package/video.mp4"),
                      "duration_seconds": result["total_seconds"], "correlation": result["checks"]["content_audio"]["correlation"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
