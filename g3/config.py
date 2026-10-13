from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil


PROJECT_ROOT = Path(__file__).resolve().parents[1]
G2_ROOT = PROJECT_ROOT / "g2"


@dataclass(frozen=True)
class Settings:
    project_root: Path = PROJECT_ROOT
    data_dir: Path = Path(os.environ.get("AUTOVIDEO_DATA_DIR", Path(__file__).resolve().parent / "data"))
    key_file: Path = PROJECT_ROOT / ".env"
    g2_python: Path = PROJECT_ROOT / "g0/.venv/bin/python"
    g2_script: Path = G2_ROOT / "run_g2.py"
    tts_model: Path = PROJECT_ROOT / "g0/assets/kokoro-v1.0.onnx"
    tts_voices: Path = PROJECT_ROOT / "g0/assets/voices-v1.0.bin"
    ffmpeg: Path = PROJECT_ROOT / "g0/darwin_arm64/ffmpeg"
    ffprobe: Path = PROJECT_ROOT / "g0/darwin_arm64/ffprobe"
    chrome: Path = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
    worker_poll_seconds: float = 0.35
    runner_timeout_seconds: int = 1800


def probe_config(settings: Settings) -> dict:
    llm_ready = bool(os.environ.get("DEEPSEEK_API_KEY", "").strip())
    if not llm_ready and settings.key_file.is_file():
        try:
            for line in settings.key_file.read_text(encoding="utf-8").splitlines():
                name, separator, raw_value = line.partition("=")
                if separator and name.strip() == "DEEPSEEK_API_KEY" and raw_value.strip().strip("\"'"):
                    llm_ready = True
                    break
        except OSError:
            llm_ready = False
    tts_ready = settings.tts_model.is_file() and settings.tts_voices.is_file()
    node_ready = bool(shutil.which("node"))
    renderer_ready = (node_ready and settings.ffmpeg.is_file() and settings.ffprobe.is_file()
                      and settings.chrome.is_file()
                      and (settings.project_root / "g0/vendor/remotion-audiogram/node_modules").is_dir())
    checks = {"deepseek": llm_ready, "kokoro_zh": tts_ready, "remotion_renderer": renderer_ready}
    blockers = [f"{name.upper()}_NOT_CONFIGURED" for name, ready in checks.items() if not ready]
    return {"ready": not blockers, "provider": "DeepSeek API", "model": "deepseek-flash",
            "target_duration_seconds": [10, 20], "checks": checks, "blockers": blockers,
            "api_key_value_exposed": False}
