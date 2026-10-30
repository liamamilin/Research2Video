"""Bounded local rendering resources; independent of video content and quality."""
from __future__ import annotations

import os
import subprocess
import sys


def physical_memory_bytes() -> int | None:
    try:
        if sys.platform == "darwin":
            result = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True,
                                    text=True, timeout=2, check=False)
            return int(result.stdout.strip()) if result.returncode == 0 else None
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def render_resources(*, video_form: str | None = None, presentation_preset: str | None = None) -> dict:
    """Reserve CPU and RAM for the UI, encoder and local voice model."""
    # Mixed storytelling compiles information_motion to the internal explanation
    # preset. Keep its resolved product form; older timelines only have a preset.
    if not video_form or video_form == "auto":
        video_form = "information_motion" if presentation_preset == "explanation" else presentation_preset
    cpus = max(1, os.cpu_count() or 1)
    memory = physical_memory_bytes()
    requested = os.environ.get("AUTOVIDEO_G2_RENDER_CONCURRENCY", "auto")
    if requested == "auto":
        # Local 1080p information-motion benchmarks on macOS: 1/2/4/6 tabs
        # took 68/80/101/107 s. More tabs contend for screenshot resources.
        # Keep the former two-tab ceiling for forms/platforms not measured.
        ceiling = 1 if sys.platform == "darwin" and video_form == "information_motion" else 2
        memory_limit = max(1, (memory - 4 * 1024 ** 3) // (2 * 1024 ** 3)) if memory else 2
        concurrency = max(1, min(ceiling, cpus // 2, memory_limit))
    else:
        if not requested.isascii() or not requested.isdecimal() or not 1 <= int(requested) <= min(cpus, 32):
            raise ValueError(f"AUTOVIDEO_G2_RENDER_CONCURRENCY must be auto or an integer in 1..{min(cpus, 32)}")
        concurrency = int(requested)
    return {"concurrency": concurrency, "requested": requested, "logical_cpus": cpus,
            "physical_memory_bytes": memory, "video_form": video_form, "presentation_preset": presentation_preset,
            "width": 1920, "height": 1080,
            "fps": 30, "crf": 18}
