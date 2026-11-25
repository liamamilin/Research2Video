"""Consistent speech loudness leveling for narrated master tracks.

Measured production VoxCPM2 tracks sit tightly around -24.1 dBFS active speech RMS
(spread 0.55 dB across 18 renders), roughly 6 dB quieter than standard Chinese video
platforms (~ -16 LUFS / -18 dBFS active speech). Applying bounded linear gain with a
transparent soft-knee peak limiter raises playback comfort without shifting alignment,
changing sample count, or clipping transients.
"""
from __future__ import annotations

import math
import os

ACTIVE_GATE_DB = -45.0
DEFAULT_TARGET_SPEECH_DB = -18.0
MIN_SOURCE_DB = -40.0
DEADBAND_DB = 0.5
MAX_GAIN_DB = 8.0
MIN_GAIN_DB = -6.0
SOFT_KNEE = 0.80
PEAK_LIMIT = 0.89


def active_db(samples, sample_rate: int) -> float:
    """RMS level in dBFS over 50 ms windows louder than ACTIVE_GATE_DB (matches package_stage.active_db)."""
    import numpy as np

    window = max(1, sample_rate // 20)
    count = len(samples) // window
    if count == 0:
        return -120.0
    blocks = np.asarray(samples[: count * window], dtype=np.float64).reshape(count, window)
    energy = np.mean(blocks * blocks, axis=1)
    loud = energy > 10 ** (ACTIVE_GATE_DB / 10)
    if not loud.any():
        return -120.0
    return float(10 * math.log10(max(float(np.mean(energy[loud])), 1e-12)))


def configured_target_db(environ: dict[str, str] | None = None) -> float | None:
    """Target active speech dBFS from AUTOVIDEO_G2_TARGET_SPEECH_DB, or None when disabled."""
    raw = (environ if environ is not None else os.environ).get("AUTOVIDEO_G2_TARGET_SPEECH_DB", "").strip().lower()
    if not raw:
        return DEFAULT_TARGET_SPEECH_DB
    if raw in {"off", "none", "0", "false"}:
        return None
    value = float(raw)
    if not -30.0 <= value <= -10.0:
        raise ValueError("AUTOVIDEO_G2_TARGET_SPEECH_DB must be between -30 and -10 dBFS, or 'off'")
    return value


def normalize_speech_level(
    samples,
    sample_rate: int,
    *,
    target_db: float | None = DEFAULT_TARGET_SPEECH_DB,
    max_gain_db: float = MAX_GAIN_DB,
    min_gain_db: float = MIN_GAIN_DB,
    knee: float = SOFT_KNEE,
    peak_limit: float = PEAK_LIMIT,
):
    """Bring mono speech track toward target active dBFS with transparent soft-knee peak protection."""
    import numpy as np

    arr = np.asarray(samples, dtype=np.float32)
    before_db = active_db(arr, sample_rate)
    peak_before = float(np.max(np.abs(arr))) if arr.size else 0.0
    if target_db is None or before_db < MIN_SOURCE_DB or abs(target_db - before_db) < DEADBAND_DB:
        return arr, {
            "enabled": target_db is not None,
            "applied": False,
            "target_active_db": target_db,
            "before_active_db": round(before_db, 2),
            "after_active_db": round(before_db, 2),
            "gain_db": 0.0,
            "peak_before": round(peak_before, 4),
            "peak_after": round(peak_before, 4),
            "limited_samples": 0,
        }
    gain_db = max(min_gain_db, min(max_gain_db, target_db - before_db))
    gain = 10.0 ** (gain_db / 20.0)
    scaled = arr.astype(np.float64) * gain
    mag = np.abs(scaled)
    over = mag > knee
    limited_samples = int(np.count_nonzero(over))
    if limited_samples:
        headroom = max(1e-4, peak_limit - knee)
        scaled = np.where(over, np.sign(scaled) * (knee + headroom * np.tanh((mag - knee) / headroom)), scaled)
    out = np.clip(scaled, -peak_limit, peak_limit).astype(np.float32)
    after_db = active_db(out, sample_rate)
    peak_after = float(np.max(np.abs(out))) if out.size else 0.0
    return out, {
        "enabled": True,
        "applied": True,
        "target_active_db": round(float(target_db), 2),
        "before_active_db": round(before_db, 2),
        "after_active_db": round(after_db, 2),
        "gain_db": round(gain_db, 2),
        "peak_before": round(peak_before, 4),
        "peak_after": round(peak_after, 4),
        "limited_samples": limited_samples,
    }
