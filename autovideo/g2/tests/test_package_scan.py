"""Package scan consolidation must retain every frame and reject partial previews."""
import copy
import json
from pathlib import Path

import pytest
from PIL import Image

from g2 import package_stage as ps


def scan_result():
    return {"streams": [
        {"index": 0, "codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080,
         "avg_frame_rate": "30/1", "nb_read_frames": "2"},
        {"index": 1, "codec_type": "audio", "codec_name": "aac", "sample_rate": "48000",
         "channels": 2, "nb_read_frames": "2"}],
        "format": {"duration": "0.066667"},
        "frames": [
            {"media_type": "video", "stream_index": 0, "width": 1920, "height": 1080, "pix_fmt": "yuv420p"},
            {"media_type": "audio", "stream_index": 1, "channels": 2},
            {"media_type": "video", "stream_index": 0, "width": 1920, "height": 1080, "pix_fmt": "yuv420p"},
            {"media_type": "audio", "stream_index": 1, "channels": 2}]}


@pytest.mark.parametrize("change", ["video_format", "audio_format", "truncated_scan", "missing_frames"])
def test_consolidated_scan_rejects_midstream_changes_and_incomplete_results(tmp_path, change):
    original = scan_result()
    result = copy.deepcopy(original)
    if change == "video_format":
        result["frames"][2]["pix_fmt"] = "yuv444p"
    elif change == "audio_format":
        result["frames"][3]["channels"] = 1
    elif change == "truncated_scan":
        result["frames"].pop()
    else:
        result.pop("frames")
    calls = []

    def runner(label, argv):
        calls.append(label)
        if label == "probe":
            return json.dumps(result)
        assert label == "package-decode"
        return ""

    with pytest.raises(ps.PackageFault) as caught:
        ps.verify_package(runner, "ffmpeg", "ffprobe", tmp_path / "video.mp4",
                          content_probe={s["codec_type"]: s for s in original["streams"]},
                          intro_frames=0, content_frames=2, outro_frames=0,
                          voice_wav=tmp_path / "not-read.wav", work=tmp_path)
    assert caught.value.code == "PACKAGE_FORMAT_MISMATCH"
    assert calls == ["package-decode", "probe"]


def test_batched_preview_handles_duplicate_frame_picks_and_ignores_stale_files(tmp_path):
    calls = []
    # intro=1/content=1/outro=1 produces eight cells but only three unique frames.
    Image.new("RGB", (20, 20), "white").save(tmp_path / "frame-001.png")

    def runner(label, argv):
        calls.append((label, argv))
        pattern = Path(argv[-1])
        for index, color in enumerate(("red", "green", "blue"), 1):
            Image.new("RGB", (40, 40), color).save(str(pattern) % index)
        return ""

    target = tmp_path / "contact.jpg"
    ps.contact_sheet(runner, "ffmpeg", tmp_path / "video.mp4", target, 1, 1, 1, tmp_path)
    assert len(calls) == 1
    with Image.open(target) as sheet:
        # Cell order stays intro x3, content x2, outro x3, despite deduplication.
        expected = [(255, 0, 0)] * 3 + [(0, 128, 0)] * 2 + [(0, 0, 255)] * 3
        for index, color in enumerate(expected):
            actual = sheet.getpixel(((index % 3) * 640 + 320, (index // 3) * 390 + 200))
            assert max(abs(a - b) for a, b in zip(actual, color)) <= 2
    assert (tmp_path / "frame-001.png").exists()
    assert not list(tmp_path.glob("sheet-*"))


def test_incomplete_preview_fails_without_publishing_a_sheet(tmp_path):
    target = tmp_path / "contact.jpg"
    Image.new("RGB", (20, 20)).save(tmp_path / "frame-001.png")
    with pytest.raises(ps.PackageFault) as caught:
        ps.contact_sheet(lambda *args: "", "ffmpeg", tmp_path / "video.mp4", target, 1, 1, 1, tmp_path)
    assert caught.value.code == "PACKAGE_PREVIEW_MISMATCH"
    assert not target.exists()
