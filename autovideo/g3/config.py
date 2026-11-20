from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import threading
import time

from g2 import llm_provider
from g2.draw_things import BASE_URL_ENV as DRAW_THINGS_URL_ENV, probe_draw_things


PROJECT_ROOT = Path(__file__).resolve().parents[1]
G2_ROOT = PROJECT_ROOT / "g2"
_DEFAULT_PROBE_DRAW_THINGS = probe_draw_things
_DRAW_THINGS_PROBE_TTL_SECONDS = 5.0
_DRAW_THINGS_PROBE_LOCK = threading.Lock()
_DRAW_THINGS_PROBE_CACHE: tuple[str, float, dict] | None = None


def _cached_probe_draw_things() -> dict:
    """Cache local Draw Things HTTP probe briefly to avoid a 1.5s probe on every API call."""
    global _DRAW_THINGS_PROBE_CACHE
    if probe_draw_things is not _DEFAULT_PROBE_DRAW_THINGS:
        return probe_draw_things()
    key = os.environ.get(DRAW_THINGS_URL_ENV, "")
    now = time.monotonic()
    with _DRAW_THINGS_PROBE_LOCK:
        cached = _DRAW_THINGS_PROBE_CACHE
        if cached is not None and cached[0] == key and now - cached[1] < _DRAW_THINGS_PROBE_TTL_SECONDS:
            return dict(cached[2])
    result = probe_draw_things()
    with _DRAW_THINGS_PROBE_LOCK:
        _DRAW_THINGS_PROBE_CACHE = (key, time.monotonic(), dict(result))
    return result


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
    runner_timeout_seconds: int = 3600

    @property
    def llm_settings_file(self) -> Path:
        """Where ``PUT /api/config/llm`` saves the provider choice; the generation subprocess reads the same file."""
        return self.data_dir / "llm-settings.json"

    def llm_environ(self) -> dict:
        return {**os.environ, "AUTOVIDEO_LLM_SETTINGS_FILE": str(self.llm_settings_file)}


def probe_config(settings: Settings) -> dict:
    provider = llm_provider.load(settings.key_file, settings.llm_environ())
    llm_ready = not provider.error and (not provider.needs_key or bool(llm_provider.api_key(provider, settings.key_file, settings.llm_environ())))
    tts_ready = settings.tts_model.is_file() and settings.tts_voices.is_file()
    node_ready = bool(shutil.which("node"))
    renderer_ready = (node_ready and settings.ffmpeg.is_file() and settings.ffprobe.is_file()
                      and settings.chrome.is_file()
                      and (settings.project_root / "g0/vendor/remotion-audiogram/node_modules").is_dir())
    draw_things = _cached_probe_draw_things()
    checks = {provider.code_prefix.lower(): llm_ready, "kokoro_zh": tts_ready, "remotion_renderer": renderer_ready,
              "draw_things": draw_things["ready"],
              "diagram_layout": node_ready and (settings.project_root / "g2/layout/node_modules/elkjs/lib/elk.bundled.js").is_file()}
    blockers = [f"{name.upper()}_NOT_CONFIGURED" for name, ready in checks.items() if not ready]
    blockers = [item for item in blockers if item not in {"DRAW_THINGS_NOT_CONFIGURED", "DIAGRAM_LAYOUT_NOT_CONFIGURED"}]
    return {"ready": not blockers, "provider": provider.label, "model": provider.model,
            "target_duration_seconds": [10, 90], "duration_modes": ["fixed", "content"], "checks": checks, "blockers": blockers,
            "draw_things": draw_things,
            "api_key_value_exposed": False}
