"""PACKAGE stage: attach a stinger (intro) and an end card (outro) to the verified content video.

The content video, its narration check and media QA are never touched. The packaged file is a separate
artifact whose own checks (decode, duration, format, shifted voice correlation, seam loudness) fail with
explicit PACKAGE_* codes. Text on the clips comes only from the validated request, never from a model.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import subprocess
from pathlib import Path

from g2.package_kit import PackageConfigError, sound_asset, validate_package

FPS = 30
WIDTH, HEIGHT = 1920, 1080
FONT = Path("/System/Library/Fonts/Hiragino Sans GB.ttc")
CORRELATION_MIN = 0.98
RMS_MIN = 0.01
ACTIVE_GATE_DB = -45.0  # 50 ms windows quieter than this do not count as sound
SEAM_TARGET_MARGIN_DB = 6.0  # cue is mixed this far below the speech level
SEAM_MIN_MARGIN_DB = 3.0  # QA fails if the cue is closer than this to the speech level
PEAK_LIMIT = 0.9
DURATION_TOLERANCE = 1 / FPS + 0.002
OUT_DIR_NAME = "package"


class PackageFault(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


def default_runner(label: str, argv: list, timeout: int = 900) -> str:
    result = subprocess.run([str(a) for a in argv], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            timeout=timeout, check=False)
    if result.returncode:
        raise PackageFault("PACKAGE_COMMAND_FAILED", f"{label} exited {result.returncode}: {(result.stdout or '')[-300:]}")
    return result.stdout or ""


def frames_for(seconds: float) -> int:
    return max(1, round(seconds * FPS))


def active_db(samples, rate: int) -> float:
    """RMS level in dBFS over 50 ms windows that are louder than the gate; -120 when there is no sound."""
    import numpy as np
    window = max(1, rate // 20)
    count = len(samples) // window
    if count == 0:
        return -120.0
    blocks = np.asarray(samples[:count * window], dtype=np.float64).reshape(count, window)
    energy = np.mean(blocks * blocks, axis=1)
    loud = energy > 10 ** (ACTIVE_GATE_DB / 10)
    if not loud.any():
        return -120.0
    return float(10 * math.log10(max(float(np.mean(energy[loud])), 1e-12)))


def probe(runner, ffprobe, path: Path, *, frames: bool = False) -> dict:
    argv = [ffprobe, "-v", "error", *(["-count_frames"] if frames else []), "-show_streams", "-show_format",
            "-of", "json", path]
    info = json.loads(runner("probe", argv))
    streams = {s["codec_type"]: s for s in info["streams"]}
    if "video" not in streams or "audio" not in streams:
        raise PackageFault("PACKAGE_FORMAT_MISMATCH", f"{path.name} 缺少视频或音频流")
    return {"video": streams["video"], "audio": streams["audio"], "format": info["format"]}


# ---------------------------------------------------------------- templates

def _luminance(color: str) -> float:
    r, g, b = (int(color[i:i + 2], 16) / 255 for i in (1, 3, 5))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _fit_font(text: str, size: int, max_width: int, minimum: int = 28):
    from PIL import ImageFont
    while True:
        font = ImageFont.truetype(str(FONT), size, index=0)
        if font.getlength(text) <= max_width or size <= minimum:
            return font
        size -= 2


def _text_layer(text: str, font, color: str):
    from PIL import Image, ImageDraw
    box = font.getbbox(text)
    layer = Image.new("RGBA", (box[2] - min(box[0], 0) + 8, box[3] + 12), (0, 0, 0, 0))
    ImageDraw.Draw(layer).text((max(-box[0], 0), 0), text, font=font, fill=color)
    return layer


def _ease(x: float) -> float:
    x = min(1.0, max(0.0, x))
    return 1 - (1 - x) ** 3


def _paste(canvas, layer, center_x: int, y: int, progress: float, rise: int = 28):
    from PIL import Image
    alpha = layer.getchannel("A").point(lambda v: int(v * progress))
    faded = layer.copy()
    faded.putalpha(alpha)
    canvas.alpha_composite(faded, (int(center_x - layer.width / 2), int(y + (1 - progress) * rise)))


def render_clip_frames(kind: str, cfg: dict, accent: str, frames: int, out_dir: Path) -> None:
    """Draw the registered template ('stinger' or 'end_card') frame by frame as PNG files."""
    from PIL import Image, ImageDraw
    if not FONT.is_file():
        raise PackageFault("PACKAGE_RENDER_FAILED", "缺少中文字体 Hiragino Sans GB")
    out_dir.mkdir(parents=True, exist_ok=True)
    on_accent = "#ffffff" if _luminance(accent) < 0.62 else "#101827"
    if kind == "stinger":
        title = _text_layer(cfg["title"], _fit_font(cfg["title"], 170, 1600), on_accent)
        sub = _text_layer(cfg["subtitle"], _fit_font(cfg["subtitle"], 64, 1500), on_accent) if cfg.get("subtitle") else None
        for i in range(frames):
            t = i / FPS
            canvas = Image.new("RGBA", (WIDTH, HEIGHT), accent)
            draw = ImageDraw.Draw(canvas)
            line_w = int(560 * _ease(t / 0.45))
            draw.rectangle((WIDTH // 2 - line_w // 2, 632, WIDTH // 2 + line_w // 2, 640), fill=on_accent)
            _paste(canvas, title, WIDTH // 2, 380, _ease(t / 0.35))
            if sub:
                _paste(canvas, sub, WIDTH // 2, 680, _ease((t - 0.2) / 0.35))
            canvas.convert("RGB").save(out_dir / f"f{i:04d}.png")
    elif kind == "end_card":
        ink, soft = "#101827", "#475467"
        title = _text_layer(cfg["title"], _fit_font(cfg["title"], 150, 1400), ink)
        lines = [_text_layer(line, _fit_font(line, 52, 1500), soft) for line in cfg.get("lines", [])]
        for i in range(frames):
            t = i / FPS
            canvas = Image.new("RGBA", (WIDTH, HEIGHT), "#f6f8fb")
            draw = ImageDraw.Draw(canvas)
            draw.rectangle((0, 0, 28, HEIGHT), fill=accent)
            span = int(480 * _ease(t / 0.5))
            top = 330 if lines else 400
            draw.rectangle((WIDTH // 2 - span // 2, top + 235, WIDTH // 2 + span // 2, top + 243), fill=accent)
            _paste(canvas, title, WIDTH // 2, top, _ease(t / 0.35))
            for n, layer in enumerate(lines):
                _paste(canvas, layer, WIDTH // 2, top + 290 + n * 86, _ease((t - 0.2 - 0.12 * n) / 0.35))
            canvas.convert("RGB").save(out_dir / f"f{i:04d}.png")
    else:
        raise PackageFault("PACKAGE_CONFIG_INVALID", f"未注册的模板：{kind}")


def encode_clip(runner, ffmpeg, frames_dir: Path, out: Path) -> None:
    runner(f"package-clip-{frames_dir.name.split('-')[0]}", [ffmpeg, "-nostdin", "-hide_banner", "-v", "error", "-y", "-framerate", str(FPS),
                            "-i", frames_dir / "f%04d.png", "-c:v", "libx264", "-preset", "medium", "-crf", "16",
                            "-pix_fmt", "yuv420p", "-r", str(FPS), "-an", out])


# ---------------------------------------------------------------- audio

def cue_track(runner, ffmpeg, work: Path, name: str | None, frames: int, rate: int, channels: int,
              speech_db: float, tag: str) -> tuple[Path, dict]:
    """Write the clip's audio: the kit cue mixed ``SEAM_TARGET_MARGIN_DB`` under the speech level, or silence."""
    import numpy as np
    import soundfile as sf
    total = round(frames * rate / FPS)
    mono = np.zeros(total, dtype=np.float32)
    meta: dict = {"sound": None}
    if name:
        asset = sound_asset(name)
        converted = work / f"{tag}-cue-src.wav"
        runner(f"package-cue-{tag}", [ffmpeg, "-nostdin", "-hide_banner", "-v", "error", "-y", "-i", asset["path"],
                               "-ar", str(rate), "-ac", "1", "-c:a", "pcm_f32le", converted])
        cue, cue_rate = sf.read(converted, dtype="float32")
        assert cue_rate == rate
        cue = cue[:total].copy()
        fade = min(len(cue), round(rate * 0.06))
        if fade and len(cue) == total:
            cue[-fade:] *= np.linspace(1, 0, fade, dtype=np.float32)
        raw_db = active_db(cue, rate)
        gain = 10 ** ((speech_db - SEAM_TARGET_MARGIN_DB - raw_db) / 20) if raw_db > -100 else 0.0
        peak = float(np.max(np.abs(cue))) if len(cue) else 0.0
        if peak * gain > PEAK_LIMIT:
            gain = PEAK_LIMIT / peak
        mono[:len(cue)] = cue * gain
        meta = {"sound": name, "sha256": asset["sha256"], "source": asset["source"], "license": asset["license"],
                "gain_db": round(20 * math.log10(gain), 2) if gain > 0 else None,
                "mixed_active_db": round(active_db(mono, rate), 2), "trimmed": len(cue) < asset["duration_seconds"] * rate - 1}
    data = np.repeat(mono[:, None], channels, axis=1) if channels > 1 else mono
    path = work / f"{tag}-audio.wav"
    sf.write(path, data, rate, subtype="FLOAT")
    return path, meta


# ---------------------------------------------------------------- checks

def _frame_set(runner, ffprobe, path: Path, stream: str, fields: str) -> set:
    out = runner("probe-frames", [ffprobe, "-v", "error", "-select_streams", stream, "-show_entries",
                                  f"frame={fields}", "-of", "json", path])
    names = fields.split(",")
    return {tuple(frame.get(name) for name in names) for frame in json.loads(out).get("frames", [])}


def verify_package(runner, ffmpeg, ffprobe, packaged: Path, *, content_probe: dict, intro_frames: int,
                   content_frames: int, outro_frames: int, voice_wav: Path, work: Path,
                   cue_names: dict | None = None) -> dict:
    import numpy as np
    import soundfile as sf
    expected_frames = intro_frames + content_frames + outro_frames
    expected_seconds = expected_frames / FPS
    checks: dict = {}
    try:
        runner("package-decode", [ffmpeg, "-nostdin", "-hide_banner", "-v", "error", "-xerror", "-i", packaged,
                                  "-map", "0:v:0", "-map", "0:a:0", "-f", "null", "-"])
    except Exception as exc:
        raise PackageFault("PACKAGE_DECODE_FAILED", f"打包后的成片无法完整解码：{str(exc)[-200:]}") from exc
    checks["full_decode"] = "PASS"
    info = probe(runner, ffprobe, packaged, frames=True)
    video, audio = info["video"], info["audio"]
    content_video, content_audio = content_probe["video"], content_probe["audio"]
    want = (content_video["width"], content_video["height"], content_video["avg_frame_rate"],
            content_audio["sample_rate"], int(content_audio["channels"]))
    got = (video["width"], video["height"], video["avg_frame_rate"], audio["sample_rate"], int(audio["channels"]))
    if want != got or (video["codec_name"], audio["codec_name"]) != ("h264", "aac"):
        raise PackageFault("PACKAGE_FORMAT_MISMATCH", f"打包成片 {got} 与内容视频 {want} 的尺寸/帧率/采样率/声道不一致")
    sizes = _frame_set(runner, ffprobe, packaged, "v:0", "width,height,pix_fmt")
    audio_formats = _frame_set(runner, ffprobe, packaged, "a:0", "sample_rate,channels")
    if len(sizes) != 1 or len(audio_formats) != 1:
        raise PackageFault("PACKAGE_FORMAT_MISMATCH", f"成片中途格式变化：视频 {sorted(sizes)}，音频 {sorted(audio_formats)}")
    checks["format"] = {"width": got[0], "height": got[1], "fps": got[2], "sample_rate": got[3], "channels": got[4],
                        "uniform_across_frames": True}
    frames = int(video["nb_read_frames"])
    seconds = float(info["format"]["duration"])
    if frames != expected_frames or abs(seconds - expected_seconds) > DURATION_TOLERANCE:
        raise PackageFault("PACKAGE_DURATION_MISMATCH",
                           f"{frames} 帧 / {seconds:.3f}s；应为 {expected_frames} 帧 / {expected_seconds:.3f}s（片头 {intro_frames} + 内容 {content_frames} + 片尾 {outro_frames}）")
    checks["duration"] = {"frames": frames, "seconds": seconds, "expected_frames": expected_frames,
                          "expected_seconds": expected_seconds, "tolerance_seconds": DURATION_TOLERANCE}
    voice, voice_rate = sf.read(voice_wav, dtype="float32")
    decoded_path = work / "decoded-audio.wav"
    runner("package-decoded-audio", [ffmpeg, "-nostdin", "-hide_banner", "-v", "error", "-y", "-i", packaged,
                                     "-vn", "-ac", "1", "-ar", str(voice_rate), decoded_path])
    decoded, _ = sf.read(decoded_path, dtype="float32")
    if abs(len(decoded) / voice_rate - expected_seconds) > DURATION_TOLERANCE:
        raise PackageFault("PACKAGE_DURATION_MISMATCH",
                           f"音轨 {len(decoded) / voice_rate:.3f}s 与应有时长 {expected_seconds:.3f}s 不符")
    offset = round(intro_frames * voice_rate / FPS)
    segment = decoded[offset:offset + len(voice)]
    n = min(len(segment), len(voice))
    if n < len(voice) * 0.99:
        raise PackageFault("PACKAGE_AUDIO_MISMATCH", f"内容音轨在片头 {intro_frames} 帧之后只剩 {n}/{len(voice)} 个采样")
    correlation = float(np.corrcoef(voice[:n], segment[:n])[0, 1])
    rms = float(np.sqrt(np.mean(np.square(segment))))
    if not correlation >= CORRELATION_MIN or rms < RMS_MIN:
        raise PackageFault("PACKAGE_AUDIO_MISMATCH", f"错开 {intro_frames} 帧后相关系数 {correlation:.5f}（需 ≥ {CORRELATION_MIN}），RMS {rms:.5f}")
    checks["content_audio"] = {"shifted_by_frames": intro_frames, "offset_samples": offset, "correlation": correlation,
                               "rms": rms}
    speech_db = active_db(segment, voice_rate)
    regions = {"intro": decoded[:round(intro_frames * voice_rate / FPS)],
               "outro": decoded[round((intro_frames + content_frames) * voice_rate / FPS):] if outro_frames else decoded[:0]}
    loudness = {"speech_active_db": round(speech_db, 2), "min_margin_db": SEAM_MIN_MARGIN_DB, "regions": {}}
    for part, samples in regions.items():
        if not (cue_names or {}).get(part):
            continue
        level = active_db(samples, voice_rate)
        peak = float(np.max(np.abs(samples))) if len(samples) else 0.0
        loudness["regions"][part] = {"active_db": round(level, 2), "margin_db": round(speech_db - level, 2),
                                     "peak": round(peak, 4)}
        if speech_db - level < SEAM_MIN_MARGIN_DB or peak > 0.99:
            raise PackageFault("PACKAGE_SEAM_LOUDNESS",
                               f"{part} 提示音 {level:.1f} dBFS，比人声 {speech_db:.1f} dBFS 只低 {speech_db - level:.1f} dB（需 ≥ {SEAM_MIN_MARGIN_DB}），峰值 {peak:.3f}")
    checks["seam_loudness"] = loudness
    return checks


# ---------------------------------------------------------------- srt / chapters

_STAMP = re.compile(r"(\d+):(\d{2}):(\d{2}),(\d{3})")


def _ms(match: re.Match) -> int:
    h, m, s, ms = map(int, match.groups())
    return ((h * 60 + m) * 60 + s) * 1000 + ms


def shift_srt(text: str, offset_ms: int) -> str:
    def fix(match):
        total = _ms(match) + offset_ms
        return f"{total // 3600000:02}:{total // 60000 % 60:02}:{total // 1000 % 60:02},{total % 1000:03}"
    return _STAMP.sub(fix, text)


def shift_chapters(chapters: dict, offset_ms: int, total_seconds: float) -> dict:
    shifted = json.loads(json.dumps(chapters))
    for chapter in shifted.get("chapters", []):
        chapter["start_ms"] += offset_ms
        chapter["end_ms"] += offset_ms
    shifted["duration_seconds"] = total_seconds
    shifted["package_offset_ms"] = offset_ms
    shifted["timeline"] = "packaged"
    return shifted


# ---------------------------------------------------------------- orchestration

def build_package(runner, ffmpeg, ffprobe, *, config: dict, content_video: Path, voice_wav: Path, artifacts: Path,
                  work: Path | None = None) -> dict:
    """Render clips, concatenate, verify, and write the package artifacts. Returns the package record."""
    import numpy as np
    import soundfile as sf
    try:
        config = validate_package(config)
    except PackageConfigError as exc:
        raise PackageFault("PACKAGE_CONFIG_INVALID", str(exc)) from exc
    if not content_video.is_file():
        raise PackageFault("PACKAGE_CONTENT_MISSING", "没有可打包的内容视频")
    out_dir = artifacts / OUT_DIR_NAME
    work = work or out_dir / "work"
    work.mkdir(parents=True, exist_ok=True)
    content = probe(runner, ffprobe, content_video, frames=True)
    cv, ca = content["video"], content["audio"]
    if (cv["width"], cv["height"], cv["avg_frame_rate"]) != (WIDTH, HEIGHT, f"{FPS}/1"):
        raise PackageFault("PACKAGE_FORMAT_MISMATCH", "内容视频不是 1920×1080 / 30fps")
    rate, channels = int(ca["sample_rate"]), int(ca["channels"])
    if channels not in (1, 2):
        raise PackageFault("PACKAGE_FORMAT_MISMATCH", f"不支持 {channels} 声道")
    content_frames = int(cv["nb_read_frames"])
    voice, voice_rate = sf.read(voice_wav, dtype="float32")
    # The reference level is what the content video actually plays (its mix can differ from the voice WAV).
    heard = work / "content-audio.wav"
    runner("package-content-audio", [ffmpeg, "-nostdin", "-hide_banner", "-v", "error", "-y", "-i", content_video,
                                     "-vn", "-ac", "1", "-ar", str(voice_rate), heard])
    speech_db = active_db(sf.read(heard, dtype="float32")[0], voice_rate)
    if speech_db <= -100:
        raise PackageFault("PACKAGE_AUDIO_MISMATCH", "内容配音近乎无声，无法对齐提示音响度")

    parts: list[tuple[str, dict, int]] = []
    for part, kind in (("intro", "stinger"), ("outro", "end_card")):
        if part in config:
            parts.append((part, config[part], frames_for(config[part]["seconds"])))
    clips: dict[str, dict] = {}
    for part, cfg, frames in parts:
        frame_dir = work / f"{part}-frames"
        try:
            render_clip_frames(cfg["template"], cfg, config["accent"], frames, frame_dir)
            encode_clip(runner, ffmpeg, frame_dir, work / f"{part}.mp4")
        except PackageFault:
            raise
        except Exception as exc:
            raise PackageFault("PACKAGE_RENDER_FAILED", f"{part} 渲染失败：{type(exc).__name__}: {str(exc)[:200]}") from exc
        audio_path, meta = cue_track(runner, ffmpeg, work, cfg.get("sound"), frames, rate, channels, speech_db, part)
        clips[part] = {"frames": frames, "video": work / f"{part}.mp4", "audio": audio_path, "sound": meta}
        for png in frame_dir.glob("f*.png"):
            png.unlink()
        frame_dir.rmdir()

    intro_frames = clips.get("intro", {}).get("frames", 0)
    outro_frames = clips.get("outro", {}).get("frames", 0)
    layout = "mono" if channels == 1 else "stereo"
    fmt = f"aformat=sample_fmts=fltp:sample_rates={rate}:channel_layouts={layout}"
    ordered: list[tuple[str, int, Path, Path | None]] = []
    if "intro" in clips:
        ordered.append(("intro", intro_frames, clips["intro"]["video"], clips["intro"]["audio"]))
    ordered.append(("content", content_frames, content_video, None))
    if "outro" in clips:
        ordered.append(("outro", outro_frames, clips["outro"]["video"], clips["outro"]["audio"]))
    input_args: list = []
    graph: list[str] = []
    streams: list[str] = []
    next_input = 0
    for n, (name, frames, video_path, audio_path) in enumerate(ordered):
        total = round(frames * rate / FPS)
        input_args += ["-i", video_path]
        v_idx = next_input
        next_input += 1
        if audio_path is None:
            a_idx = v_idx
        else:
            input_args += ["-i", audio_path]
            a_idx = next_input
            next_input += 1
        graph.append(f"[{v_idx}:v]trim=end_frame={frames},fps={FPS},scale={WIDTH}:{HEIGHT},format=yuv420p,setsar=1,"
                     f"setpts=PTS-STARTPTS[v{n}]")
        graph.append(f"[{a_idx}:a]{fmt},apad=whole_len={total},atrim=end_sample={total},asetpts=PTS-STARTPTS[a{n}]")
        streams.append(f"[v{n}][a{n}]")
    graph.append("".join(streams) + f"concat=n={len(ordered)}:v=1:a=1[v][a]")
    packaged = out_dir / "video.mp4"
    runner("package-concat", [ffmpeg, "-nostdin", "-hide_banner", "-v", "error", "-y", *input_args,
                              "-filter_complex", ";".join(graph), "-map", "[v]", "-map", "[a]",
                              "-c:v", "libx264", "-preset", "medium", "-crf", "16", "-pix_fmt", "yuv420p",
                              "-r", str(FPS), "-c:a", "aac", "-b:a", "192k", "-ar", str(rate), "-ac", str(channels),
                              "-movflags", "+faststart", packaged])
    cue_names = {part: clips[part]["sound"].get("sound") for part in clips}
    checks = verify_package(runner, ffmpeg, ffprobe, packaged, content_probe=content, intro_frames=intro_frames,
                            content_frames=content_frames, outro_frames=outro_frames, voice_wav=voice_wav,
                            work=work, cue_names=cue_names)
    offset_ms = round(intro_frames * 1000 / FPS)
    total_seconds = (intro_frames + content_frames + outro_frames) / FPS
    captions = artifacts / "captions.srt"
    if captions.is_file():
        (out_dir / "captions.srt").write_text(shift_srt(captions.read_text(encoding="utf-8"), offset_ms), encoding="utf-8")
    chapters = artifacts / "chapters.json"
    if chapters.is_file():
        (out_dir / "chapters.json").write_text(json.dumps(
            shift_chapters(json.loads(chapters.read_text(encoding="utf-8")), offset_ms, total_seconds),
            ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    contact_sheet(runner, ffmpeg, packaged, out_dir / "contact-sheet.jpg", intro_frames, content_frames, outro_frames, work)
    record = {"status": "PASS", "config": config, "fps": FPS,
              "intro": {"frames": intro_frames, "seconds": intro_frames / FPS, "offset_ms": 0,
                        "sound": clips.get("intro", {}).get("sound")},
              "content": {"frames": content_frames, "seconds": content_frames / FPS, "offset_ms": offset_ms,
                          "source": content_video.name},
              "outro": {"frames": outro_frames, "seconds": outro_frames / FPS,
                        "offset_ms": offset_ms + round(content_frames * 1000 / FPS),
                        "sound": clips.get("outro", {}).get("sound")},
              "total_frames": intro_frames + content_frames + outro_frames, "total_seconds": total_seconds,
              "offset_ms": offset_ms, "checks": checks,
              "files": {"video": "package/video.mp4", "captions": "package/captions.srt" if captions.is_file() else None,
                        "chapters": "package/chapters.json" if chapters.is_file() else None,
                        "contact_sheet": "package/contact-sheet.jpg"},
              "timelines": "captions.srt/chapters.json 保持内容时间线；package/ 下的同名文件已加上片头时长"}
    (out_dir / "package.json").write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out_dir / "package-qa.json").write_text(json.dumps({"status": "PASS", "checks": checks}, ensure_ascii=False, indent=2) + "\n",
                                             encoding="utf-8")
    for leftover in work.glob("*"):
        if leftover.is_file():
            leftover.unlink()
    work.rmdir()
    return record


def contact_sheet(runner, ffmpeg, packaged: Path, target: Path, intro: int, content: int, outro: int, work: Path) -> None:
    from PIL import Image, ImageDraw, ImageFont
    total = intro + content + outro
    picks = []
    if intro:
        picks += [("片头 开始", 0), ("片头 中段", intro // 2), ("片头 末帧", intro - 1)]
    picks += [("内容 首帧", intro), ("内容 末帧", intro + content - 1)]
    if outro:
        picks += [("片尾 首帧", intro + content), ("片尾 中段", intro + content + outro // 2), ("片尾 末帧", total - 1)]
    cell_w, cell_h = 640, 360
    cols = 3
    rows = math.ceil(len(picks) / cols)
    sheet = Image.new("RGB", (cell_w * cols, (cell_h + 30) * rows), "#101827")
    font = ImageFont.truetype(str(FONT), 20)
    draw = ImageDraw.Draw(sheet)
    for n, (label, frame) in enumerate(picks):
        png = work / f"sheet-{n}.png"
        runner("package-sheet", [ffmpeg, "-nostdin", "-hide_banner", "-v", "error", "-y", "-i", packaged,
                                 "-vf", f"select=eq(n\\,{frame})", "-vsync", "0", "-frames:v", "1", png])
        image = Image.open(png).convert("RGB").resize((cell_w, cell_h))
        x, y = (n % cols) * cell_w, (n // cols) * (cell_h + 30)
        sheet.paste(image, (x, y + 30))
        draw.text((x + 10, y + 4), f"{label} · 帧 {frame}", font=font, fill="#F3F6FC")
        png.unlink()
    sheet.save(target, quality=90)
