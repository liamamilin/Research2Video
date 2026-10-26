"""Real compositor smoke tests: opening sound, final timeline and legacy omission."""
import json
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from g2.brand_stage import build, FONT
from g2.package_stage import default_runner
from g3 import brand_profiles
from scripts.install_demo_brand import install

PROJECT = Path(__file__).resolve().parents[2]
FFMPEG = PROJECT / "g0/darwin_arm64/ffmpeg"
FFPROBE = PROJECT / "g0/darwin_arm64/ffprobe"


@pytest.mark.skipif(not FFMPEG.is_file() or not Path(FONT).is_file(), reason="local media runtime required")
@pytest.mark.parametrize("intro, cta_enabled", [(None, False), (1, False), (0, True), (1, True)])
def test_brand_opening_preserves_audio_and_shifts_only_final_timeline(tmp_path, intro, cta_enabled):
    data = tmp_path / "data"
    config = install(data)
    config.pop("assets")
    cta_seconds = 1.5 if cta_enabled else 0
    if not cta_enabled:
        config.pop("cta")
        config.pop("corner_reveal_seconds")
    if intro is None:
        config.pop("intro_seconds")
    else:
        config["intro_seconds"] = intro
    brand_profiles.save(data, "demo", config)
    input_dir = tmp_path / "input"; input_dir.mkdir()
    request = brand_profiles.freeze(data, input_dir, {"brand": {"profile": "demo"}})
    source = tmp_path / "content.mp4"
    default_runner("source", [FFMPEG, "-nostdin", "-v", "error", "-f", "lavfi", "-i",
        "color=c=white:s=1920x1080:r=30:d=1", "-f", "lavfi", "-i", "sine=frequency=220:sample_rate=48000:duration=1",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000", "-ac", "2", source])
    (tmp_path / "captions.srt").write_text("1\n00:00:00,100 --> 00:00:00,800\n内容\n")
    (tmp_path / "chapters.json").write_text(json.dumps({"chapters": [{"id": "c1", "start_ms": 100, "end_ms": 800}]}))
    original_captions = (tmp_path / "captions.srt").read_bytes()
    output = tmp_path / "brand"
    qa = build(default_runner, FFMPEG, FFPROBE, request, input_dir, source, output)
    shift = intro or 0
    assert qa["frames"] == (5+shift)*30
    assert qa["narration_shift_seconds"] == shift
    assert qa["sonic"]["start_seconds"] == pytest.approx(shift+1.2+cta_seconds)
    assert (tmp_path / "captions.srt").read_bytes() == original_captions
    assert f"00:00:0{shift},100" in (output / "captions.srt").read_text()
    chapters = json.loads((output / "chapters.json").read_text())["chapters"]
    assert next(c for c in chapters if c["id"] == "c1")["start_ms"] == 100+shift*1000
    assert chapters[-1]["start_ms"] == (shift+1+cta_seconds)*1000
    for path, wav in ((source, tmp_path / "source.wav"), (output / "video.mp4", tmp_path / "final.wav")):
        default_runner("audio", [FFMPEG, "-nostdin", "-v", "error", "-i", path, "-ar", "48000", "-ac", "2", "-c:a", "pcm_f32le", wav])
    original, _ = sf.read(tmp_path / "source.wav", always_2d=True)
    final, _ = sf.read(tmp_path / "final.wav", always_2d=True)
    compared = final[shift*48000:shift*48000+len(original)]
    assert np.corrcoef(original.ravel(), compared.ravel())[0, 1] > .995
    if intro:
        assert qa["intro_sonic"]["enabled"]
        assert qa["intro_sonic"]["start_seconds"]+qa["intro_sonic"]["duration_seconds"] < shift
        assert np.sqrt(np.mean(final[:round(.7*48000)]**2)) > .01
    else:
        assert not qa["intro_sonic"]["enabled"]
    if cta_seconds:
        from PIL import Image
        assert qa["cta"]["actions"] == ["like", "save", "follow"]
        assert qa["brand_card_seconds"] == 2.5
        assert next(c for c in chapters if c["id"] == "brand_cta")["start_ms"] == (shift+1)*1000
        # The CTA adds no time and does not place its sound under either speech or icons.
        assert np.max(np.abs(final[round((shift+1.1)*48000):round((shift+2.5)*48000)])) < .002
        for seconds, name in ((shift, "first"), (shift+.7, "revealed")):
            default_runner("frame", [FFMPEG, "-nostdin", "-v", "error", "-ss", str(seconds), "-i", output / "video.mp4", "-frames:v", "1", tmp_path / f"{name}.png"])
        first = np.asarray(Image.open(tmp_path / "first.png").convert("RGB"))
        revealed = np.asarray(Image.open(tmp_path / "revealed.png").convert("RGB"))
        assert np.mean(first[:900]) > 250  # Source is white; there is no fullscreen brand opening.
        assert np.mean(np.abs(first[36:112, 1352:1872].astype(float)-revealed[36:112, 1352:1872])) > 5
