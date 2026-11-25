import numpy as np
import pytest

from g2.audio_level import active_db, configured_target_db, normalize_speech_level


def _speech_like(rms_amp: float, seconds: float = 2.0, rate: int = 24000, spike: float = 0.0):
    rng = np.random.default_rng(42)
    n = int(rate * seconds)
    t = np.linspace(0, 20, n)
    gate = (np.sin(t) > -0.2).astype(np.float32)
    wave = (rng.standard_normal(n).astype(np.float32) * rms_amp * gate).clip(-1.0, 1.0)
    if spike:
        wave[rate // 2] = spike
    return wave


def test_quiet_voxcpm_track_is_raised_toward_target_without_clipping():
    # Roughly -24 dBFS active speech RMS, matching measured VoxCPM2 production output.
    raw = _speech_like(0.062, spike=0.48)
    out, info = normalize_speech_level(raw, 24000, target_db=-18.0)
    assert out.shape == raw.shape and out.dtype == np.float32
    assert info["applied"] is True
    assert -25.5 <= info["before_active_db"] <= -23.0
    assert -18.6 <= info["after_active_db"] <= -17.4
    assert info["peak_after"] <= 0.89
    assert float(np.corrcoef(raw, out)[0, 1]) > 0.995


def test_transient_spike_is_soft_limited_below_ceiling():
    raw = _speech_like(0.062, spike=0.75)
    out, info = normalize_speech_level(raw, 24000, target_db=-18.0)
    assert info["applied"] is True
    assert info["limited_samples"] >= 1
    assert info["peak_after"] <= 0.89
    assert abs(active_db(out, 24000) - info["after_active_db"]) < 0.05


def test_already_leveled_or_silent_audio_is_left_untouched():
    silent = np.zeros(24000, dtype=np.float32)
    out, info = normalize_speech_level(silent, 24000, target_db=-18.0)
    assert info["applied"] is False and np.array_equal(out, silent)

    loud = _speech_like(0.125)
    out2, info2 = normalize_speech_level(loud, 24000, target_db=None)
    assert info2["applied"] is False and np.array_equal(out2, loud)


def test_configured_target_db_parses_default_off_and_bounds():
    assert configured_target_db({}) == -18.0
    assert configured_target_db({"AUTOVIDEO_G2_TARGET_SPEECH_DB": "off"}) is None
    assert configured_target_db({"AUTOVIDEO_G2_TARGET_SPEECH_DB": "-16.5"}) == -16.5
    with pytest.raises(ValueError):
        configured_target_db({"AUTOVIDEO_G2_TARGET_SPEECH_DB": "-5"})
