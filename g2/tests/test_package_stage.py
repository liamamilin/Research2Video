import copy
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from g2 import package_stage as ps
from g2 import package_kit
from g2.package_kit import PackageConfigError, synth_sounds, validate_package

ROOT = Path(__file__).resolve().parents[2]
FFMPEG, FFPROBE = ROOT / "g0/darwin_arm64/ffmpeg", ROOT / "g0/darwin_arm64/ffprobe"
RATE = 24000
GOOD = {"accent": "#1F7A4D",
        "intro": {"template": "stinger", "title": "快讯", "subtitle": "2026-10-19", "seconds": 0.5, "sound": "synth_sting"},
        "outro": {"template": "end_card", "title": "快讯", "lines": ["来源见简介"], "seconds": 0.5, "sound": "synth_sting"}}


def run(argv):
    result = subprocess.run([str(a) for a in argv], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    assert result.returncode == 0, result.stdout
    return result.stdout


@pytest.fixture(scope="module")
def content(tmp_path_factory):
    """A 2.5 s content video: test pattern plus a speech-like AM-noise voice, AAC 48 kHz stereo like Remotion's."""
    root = tmp_path_factory.mktemp("content")
    rng = np.random.default_rng(7)
    seconds = 2.5
    n = int(RATE * seconds)
    envelope = (np.sin(np.linspace(0, 40, n)) > -0.3).astype(np.float32)
    voice = (rng.standard_normal(n).astype(np.float32) * 0.12 * envelope).clip(-1, 1)
    wav = root / "voice.wav"
    sf.write(wav, voice, RATE, subtype="PCM_16")
    video = root / "content.mp4"
    run([FFMPEG, "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=30", "-i", wav,
         "-t", str(seconds), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000", "-ac", "2",
         "-b:a", "192k", video])
    (root / "captions.srt").write_text("1\n00:00:00,000 --> 00:00:01,500\n第一句\n\n2\n00:00:01,600 --> 00:00:02,400\n第二句\n", encoding="utf-8")
    (root / "chapters.json").write_text(json.dumps({"mode": "content", "duration_seconds": 2.5, "chapters": [
        {"id": "c1", "title": "一", "start_ms": 0, "end_ms": 1500}, {"id": "c2", "title": "二", "start_ms": 1500, "end_ms": 2500}]}), encoding="utf-8")
    return {"root": root, "video": video, "wav": wav}


def package(content, tmp_path, config=GOOD):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    shutil.copy(content["root"] / "captions.srt", artifacts)
    shutil.copy(content["root"] / "chapters.json", artifacts)
    record = ps.build_package(ps.default_runner, FFMPEG, FFPROBE, config=config, content_video=content["video"],
                              voice_wav=content["wav"], artifacts=artifacts)
    return record, artifacts / "package"


def test_packaged_video_passes_every_check_and_shifts_timelines(content, tmp_path):
    record, out = package(content, tmp_path)
    checks = record["checks"]
    assert record["intro"]["frames"] == 15 and record["outro"]["frames"] == 15 and record["content"]["frames"] == 75
    assert checks["duration"]["frames"] == 105 and checks["full_decode"] == "PASS"
    assert checks["content_audio"]["correlation"] >= 0.98 and checks["content_audio"]["shifted_by_frames"] == 15
    assert all(r["margin_db"] >= 3 for r in checks["seam_loudness"]["regions"].values())
    assert checks["format"]["uniform_across_frames"] and checks["format"]["sample_rate"] == "48000"
    assert record["offset_ms"] == 500 and record["outro"]["offset_ms"] == 3000
    assert "00:00:00,500 --> 00:00:02,000" in (out / "captions.srt").read_text(encoding="utf-8")
    shifted = json.loads((out / "chapters.json").read_text(encoding="utf-8"))
    assert [c["start_ms"] for c in shifted["chapters"]] == [500, 2000] and shifted["duration_seconds"] == 3.5
    assert json.loads((content["root"] / "chapters.json").read_text())["chapters"][0]["start_ms"] == 0  # source untouched
    assert (out / "contact-sheet.jpg").is_file() and json.loads((out / "package-qa.json").read_text())["status"] == "PASS"
    assert not (out / "work").exists()


def test_intro_only_without_sound_is_silent_and_skips_loudness(content, tmp_path):
    config = {"accent": "#223344", "intro": {"template": "stinger", "title": "快讯", "seconds": 0.5}}
    record, _ = package(content, tmp_path, config)
    assert record["outro"]["frames"] == 0 and record["checks"]["seam_loudness"]["regions"] == {}
    assert record["checks"]["duration"]["frames"] == 90


def test_content_correlation_is_rechecked_after_the_intro_offset(content, tmp_path):
    _, out = package(content, tmp_path)
    work = tmp_path / "w"
    work.mkdir()
    probe = ps.probe(ps.default_runner, FFPROBE, content["video"], frames=True)
    kwargs = dict(content_probe=probe, voice_wav=content["wav"], work=work, cue_names={"intro": "synth_sting", "outro": "synth_sting"})
    with pytest.raises(ps.PackageFault) as caught:  # same total length, but the intro/content boundary is 10 frames off
        ps.verify_package(ps.default_runner, FFMPEG, FFPROBE, out / "video.mp4", intro_frames=25, content_frames=65,
                          outro_frames=15, **kwargs)
    assert caught.value.code == "PACKAGE_AUDIO_MISMATCH"
    with pytest.raises(ps.PackageFault) as caught:
        ps.verify_package(ps.default_runner, FFMPEG, FFPROBE, out / "video.mp4", intro_frames=15, content_frames=75,
                          outro_frames=18, **kwargs)
    assert caught.value.code == "PACKAGE_DURATION_MISMATCH"


def test_format_and_decode_failures_have_their_own_codes(content, tmp_path):
    _, out = package(content, tmp_path)
    work = tmp_path / "w"
    work.mkdir()
    probe = ps.probe(ps.default_runner, FFPROBE, content["video"], frames=True)
    wrong_rate = copy.deepcopy(probe)
    wrong_rate["audio"]["sample_rate"] = "44100"
    args = dict(intro_frames=15, content_frames=75, outro_frames=15, voice_wav=content["wav"], work=work)
    with pytest.raises(ps.PackageFault) as caught:
        ps.verify_package(ps.default_runner, FFMPEG, FFPROBE, out / "video.mp4", content_probe=wrong_rate, **args)
    assert caught.value.code == "PACKAGE_FORMAT_MISMATCH"
    mono = copy.deepcopy(probe)
    mono["audio"]["channels"] = 1
    with pytest.raises(ps.PackageFault) as caught:
        ps.verify_package(ps.default_runner, FFMPEG, FFPROBE, out / "video.mp4", content_probe=mono, **args)
    assert caught.value.code == "PACKAGE_FORMAT_MISMATCH"
    broken = tmp_path / "broken.mp4"
    data = (out / "video.mp4").read_bytes()
    broken.write_bytes(data[:len(data) // 2] + bytes(len(data) // 4) + data[3 * len(data) // 4:])
    with pytest.raises(ps.PackageFault) as caught:
        ps.verify_package(ps.default_runner, FFMPEG, FFPROBE, broken, content_probe=probe, **args)
    assert caught.value.code in {"PACKAGE_DECODE_FAILED", "PACKAGE_COMMAND_FAILED"}


def test_a_cue_louder_than_the_voice_fails_the_seam_loudness_check(content, tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "SEAM_TARGET_MARGIN_DB", -3.0)  # mix the cue 3 dB above the speech level
    with pytest.raises(ps.PackageFault) as caught:
        package(content, tmp_path)
    assert caught.value.code == "PACKAGE_SEAM_LOUDNESS" and "dB" in str(caught.value)


def test_cue_is_mixed_below_speech_and_clipped_to_the_clip(content, tmp_path):
    work = tmp_path / "w"
    work.mkdir()
    path, meta = ps.cue_track(ps.default_runner, FFMPEG, work, "synth_sting", 15, 48000, 2, -20.0, "t")
    audio, rate = sf.read(path, dtype="float32")
    assert audio.shape == (15 * 1600, 2) and rate == 48000 and meta["trimmed"] is True
    assert -20.0 - ps.active_db(audio[:, 0], rate) == pytest.approx(ps.SEAM_TARGET_MARGIN_DB, abs=0.6)
    assert abs(audio[-1, 0]) < 1e-3  # faded out inside the clip, no bleed into the content


def test_missing_content_and_invalid_config_are_explicit(content, tmp_path):
    with pytest.raises(ps.PackageFault) as caught:
        ps.build_package(ps.default_runner, FFMPEG, FFPROBE, config=GOOD, content_video=tmp_path / "none.mp4",
                         voice_wav=content["wav"], artifacts=tmp_path)
    assert caught.value.code == "PACKAGE_CONTENT_MISSING"
    with pytest.raises(ps.PackageFault) as caught:
        ps.build_package(ps.default_runner, FFMPEG, FFPROBE, config={"accent": "red"}, content_video=content["video"],
                         voice_wav=content["wav"], artifacts=tmp_path)
    assert caught.value.code == "PACKAGE_CONFIG_INVALID"


def test_srt_and_chapter_shifting_is_exact():
    assert ps.shift_srt("1\n00:59:59,900 --> 01:00:00,100\nx\n", 1500) == "1\n01:00:01,400 --> 01:00:01,600\nx\n"


# --- request validation ----------------------------------------------------------------------------

def mutate(path, value):
    config = copy.deepcopy(GOOD)
    target = config
    for key in path[:-1]:
        target = target[key]
    if value is KeyError:
        del target[path[-1]]
    else:
        target[path[-1]] = value
    return config


@pytest.mark.parametrize("config,message", [
    ({**GOOD, "extra": 1}, "不支持的字段"),
    (mutate(["accent"], "green"), "accent"),
    (mutate(["accent"], "#12345"), "accent"),
    (mutate(["accent"], KeyError), "accent"),
    (mutate(["intro", "template"], "end_card"), "stinger"),
    (mutate(["outro", "template"], "stinger"), "end_card"),
    (mutate(["intro", "template"], "fancy"), "stinger"),
    (mutate(["intro", "seconds"], 0.4), "seconds"),
    (mutate(["outro", "seconds"], 5.1), "seconds"),
    (mutate(["intro", "seconds"], "1"), "seconds"),
    (mutate(["intro", "seconds"], True), "seconds"),
    (mutate(["intro", "title"], "标" * 13), "intro.title"),
    (mutate(["intro", "title"], ""), "intro.title"),
    (mutate(["intro", "subtitle"], "副" * 25), "intro.subtitle"),
    (mutate(["intro", "title"], "两行\n标题"), "单行"),
    (mutate(["outro", "lines"], ["行"] * 4), "outro.lines"),
    (mutate(["outro", "lines"], ["行" * 31]), "outro.lines"),
    (mutate(["outro", "lines"], []), "outro.lines"),
    (mutate(["intro", "sound"], "Synth-Sting"), "sound"),
    (mutate(["intro", "sound"], "nope_sound"), "不在套件里"),
    (mutate(["intro", "extra"], 1), "不支持的字段"),
    (mutate(["outro", "subtitle"], "不属于片尾"), "不支持的字段"),
    (mutate(["intro", "lines"], ["x"]), "不支持的字段"),
    ({"accent": "#112233"}, "至少"),
    ({**GOOD, "badge": "角" * 13}, "badge"),
    ({**GOOD, "badge": "两行\n角标"}, "单行"),
    ({**GOOD, "badge": 5}, "badge"),
    ([], "对象"),
])
def test_package_requests_are_validated(config, message):
    with pytest.raises(PackageConfigError, match=message):
        validate_package(config)


def test_a_valid_package_is_normalised_and_either_half_is_optional():
    normal = validate_package(GOOD)
    assert normal["accent"] == "#1f7a4d" and normal["intro"]["seconds"] == 0.5
    assert set(validate_package({"accent": "#112233", "outro": GOOD["outro"]})) == {"accent", "outro"}


# --- sound kit -------------------------------------------------------------------------------------

def test_kit_sound_is_the_deterministic_synthesis_with_licence_metadata():
    entry = next(item for item in package_kit.load_kit()["sounds"] if item["name"] == "synth_sting")
    assert entry["source"] and entry["license"] and entry["author"]
    wav = synth_sounds.synth_sting()
    assert wav == synth_sounds.synth_sting()
    path = package_kit.sound_asset("synth_sting")["path"]
    with sf.SoundFile(path) as stream:
        assert stream.samplerate == entry["sample_rate"] == 48000 and stream.frames == len(wav)
    assert entry["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()


def test_an_altered_or_missing_kit_file_is_refused(tmp_path, monkeypatch):
    shutil.copytree(package_kit.KIT_ROOT, tmp_path / "kit")
    monkeypatch.setattr(package_kit, "KIT_ROOT", tmp_path / "kit")
    (tmp_path / "kit/sounds/synth_sting.wav").write_bytes(b"swapped")
    with pytest.raises(PackageConfigError, match="sha256"):
        package_kit.sound_asset("synth_sting")
    (tmp_path / "kit/sounds/synth_sting.wav").unlink()
    with pytest.raises(PackageConfigError, match="缺失"):
        package_kit.sound_asset("synth_sting")


def test_badge_is_optional_and_normalised():
    assert validate_package({**GOOD, "badge": " 快讯 "})["badge"] == "快讯"
    assert "badge" not in validate_package(GOOD)
    assert validate_package({"accent": "#112233", "badge": "快讯", "outro": GOOD["outro"]})["badge"] == "快讯"


def test_renderer_label_comes_from_the_package_only_when_there_is_one():
    source = (ROOT / "g1/renderer/src/Root.tsx").read_text(encoding="utf-8")
    assert "badge ? badge.text : <>AUTOVIDEO" in source  # no badge prop keeps the product label unchanged
    run = (ROOT / "g2/run_g2.py").read_text(encoding="utf-8")
    assert 'props["badge"] = {"text": package.get("badge", ""), "color": package["accent"]}' in run
