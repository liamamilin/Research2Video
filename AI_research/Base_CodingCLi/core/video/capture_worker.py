"""Disposable browser worker; invoked only by capture_process with local JSON."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from core.fileio import atomic_write
from core.video import screenshots


def main() -> None:
    folder = Path(sys.argv[1])
    request = json.load(sys.stdin)
    options = request["options"]
    try:
        if request["operation"] == "screenshot":
            raw, metadata = screenshots._capture(**options)
        elif request["operation"] == "automatic":
            config_path = options.pop("config_path", "")
            if config_path:
                os.environ["AI_RESEARCH_WEB_CONFIG"] = config_path
            from web.services.video_visual_acquire import acquire

            policy = options["policy"]

            def checkpoint() -> None:
                atomic_write(str(folder / "progress.json"), json.dumps({"model_calls": policy.get("_model_calls", 0)}))

            policy["_on_model_call"] = checkpoint
            checkpoint()
            raw, metadata = acquire(**options)
        else:
            raise screenshots.ScreenshotError("未知截图任务")
        (folder / "image.png").write_bytes(raw)
        result = {"metadata": metadata}
    except Exception as exc:
        # Keep provider responses, page content and credentials out of errors.
        result = {"error": str(exc) if isinstance(exc, screenshots.ScreenshotError) else "网页截图失败，请稍后重试"}
    atomic_write(str(folder / "result.json"), json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
