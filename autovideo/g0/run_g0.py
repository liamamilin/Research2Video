"""G0-only fixed experiment. No eval, shell=True, generated JS/TS, or IR compiler.

Remotion executes unchanged upstream source; authored input is JSON/local media.
All subprocesses use explicit argv, project paths, timeouts, and captured logs.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parent
TEMPLATE = ROOT / "vendor/remotion-audiogram"
OUT = ROOT / "artifacts"
LOGS = ROOT / "logs"
FFMPEG = ROOT / "darwin_arm64/ffmpeg"
FFPROBE = ROOT / "darwin_arm64/ffprobe"
NODE = Path(shutil.which("node"))
CLI = TEMPLATE / "node_modules/@remotion/cli/remotion-cli.js"
FONT = "/System/Library/Fonts/Hiragino Sans GB.ttc"


def save(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest() if hasattr(hashlib, "file_digest") else hashlib.sha256(stream.read()).hexdigest()


def command(label, argv, *, cwd=ROOT, timeout=600):
    allowed = {str(NODE), str(FFMPEG), str(FFPROBE), "/usr/bin/git", sys.executable}
    assert str(argv[0]) in allowed, "Executable not allowlisted"
    argv = [str(x) for x in argv]
    if (LOGS / f"{label}.log").exists():
        attempt = 2
        while (LOGS / f"{label}-{attempt:02}.log").exists():
            attempt += 1
        label = f"{label}-{attempt:02}"
    start = time.monotonic()
    print(f"RUN {label}: {argv}", flush=True)
    env = os.environ.copy()
    env.update({"HF_HOME": str(ROOT / ".hf-cache"), "XDG_CACHE_HOME": str(ROOT / ".cache")})
    result = subprocess.run(argv, cwd=cwd, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, timeout=timeout, text=True)
    (LOGS / f"{label}.log").write_text(result.stdout)
    record = {"time_utc": datetime.now(timezone.utc).isoformat(), "label": label,
              "argv": argv, "cwd": str(cwd), "exit_code": result.returncode,
              "elapsed_seconds": round(time.monotonic() - start, 3),
              "output": f"logs/{label}.log"}
    with (LOGS / "commands.jsonl").open("a") as file:
        file.write(json.dumps(record, ensure_ascii=False) + "\n")
    print(f"DONE {label}: exit={result.returncode}, seconds={record['elapsed_seconds']}", flush=True)
    if result.returncode:
        print(result.stdout[-6000:], flush=True)
        raise RuntimeError(f"{label} failed")
    return result.stdout


def audit_sources():
    sources = {str(p.relative_to(TEMPLATE)): digest(p)
               for p in sorted((TEMPLATE / "src").rglob("*")) if p.is_file()}
    sources["remotion.config.ts"] = digest(TEMPLATE / "remotion.config.ts")
    path = ROOT / "trusted-source.json"
    if path.exists():
        assert json.loads(path.read_text())["sha256"] == sources, "Upstream source changed"
    else:
        save(path, {"repository": "https://github.com/remotion-dev/template-audiogram",
                    "commit": "a7a1befca086f72eaf721a8b85d9e5038453da94", "sha256": sources})


def prepare():
    import numpy as np
    import soundfile as sf
    from kokoro_onnx import Kokoro
    from misaki import zh
    from PIL import Image, ImageDraw, ImageFont

    audit_sources()
    plan = json.loads((ROOT / "narration.json").read_text())
    start = time.monotonic()
    kokoro = Kokoro(str(ROOT / "assets/kokoro-v1.0.onnx"), str(ROOT / "assets/voices-v1.0.bin"))
    g2p = zh.ZHG2P()
    pieces, boundaries, cursor = [], [], 0
    sample_rate = 24000
    lead = np.zeros(6000, np.float32)
    pieces.append(lead)
    cursor += len(lead)
    for item in plan["segments"]:
        phonemes, _ = g2p(item["text"])
        assert "❓" not in phonemes, "Unpronounceable text"
        samples, rate = kokoro.create(phonemes, voice=plan["voice"], speed=plan["speed"],
                                      is_phonemes=True, trim=True)
        assert rate == sample_rate
        sf.write(OUT / f"{item['id']}.wav", samples, rate, subtype="PCM_16")
        # Boundaries come from the actual generated sample arrays, not text length.
        active = np.flatnonzero(np.abs(samples) > 0.003)
        assert len(active) > 0
        start_sample = cursor + max(0, int(active[0]) - 1200)
        end_sample = cursor + min(len(samples), int(active[-1]) + 1200)
        gap = np.zeros(round(item["pause_after_ms"] * rate / 1000), np.float32)
        boundaries.append({"id": item["id"], "text": item["text"],
            "start_ms": (start_sample * 1000 + rate // 2) // rate,
            "end_ms": (end_sample * 1000 + rate // 2) // rate,
            "clip_start_sample": cursor, "clip_end_sample": cursor + len(samples),
            "speech_first_sample": int(active[0]), "speech_last_sample": int(active[-1]),
            "samples": len(samples), "pause_after_ms": item["pause_after_ms"],
            "phonemes": phonemes})
        pieces.extend([samples, gap])
        cursor += len(samples) + len(gap)
        print(json.dumps(boundaries[-1], ensure_ascii=False), flush=True)
    audio = np.concatenate(pieces)
    # Pad only the tail to the next frame; never stretch the generated voice.
    total_frames = math.ceil(len(audio) / (sample_rate / 30))
    audio = np.pad(audio, (0, total_frames * 800 - len(audio)))
    duration = len(audio) / sample_rate
    assert 10 <= duration <= 20, f"Real audio length outside G0: {duration}"
    sf.write(OUT / "narration.wav", audio, sample_rate, subtype="PCM_16")
    save(OUT / "alignment.json", {"method": "separate_tts_utterances_with_measured_pcm_boundaries",
         "granularity": "phrase", "activity_threshold": 0.003, "edge_guard_ms": 50,
         "sample_rate": sample_rate, "duration_seconds": duration,
         "total_frames": total_frames, "segments": boundaries,
         "limitation": "Phrase boundaries only; no word alignment or ASR accuracy claim."})
    def stamp(ms):
        return f"{ms//3600000:02}:{ms//60000%60:02}:{ms//1000%60:02},{ms%1000:03}"
    (OUT / "captions.srt").write_text("\n\n".join(
         f"{i+1}\n{stamp(b['start_ms'])} --> {stamp(b['end_ms'])}\n{b['text']}"
         for i, b in enumerate(boundaries)) + "\n")
    # Template-native captions are hidden; FFmpeg places readable Chinese PNGs
    # from the same exact timeline at the bottom, avoiding English-only pagination.
    native = [{"text": b["text"], "startMs": b["start_ms"], "endMs": b["end_ms"],
               "timestampMs": b["start_ms"], "confidence": None, "pageBreakAfter": True}
              for b in boundaries]
    public = TEMPLATE / "public"
    shutil.copy2(OUT / "narration.wav", public / "dialogue.wav")
    save(public / "captions.json", native)
    save(ROOT / "props.json", {"captions": None,
       "titleColor": "transparent", "captionsTextColor": "transparent",
       "onlyDisplayCurrentSentence": True,
       "visualizer": {"type": "oscilloscope", "color": "#4DB6E8", "numberOfSamples": "256",
                      "windowInSeconds": 0.1, "posterization": 1, "amplitude": 1, "padding": 50}})
    # Asset replacement only. It fixes the trusted template's header height to 250px.
    Image.new("RGB", (250, 250), "black").save(public / "podcast-cover.jpeg")
    regular = lambda size: ImageFont.truetype(FONT, size)
    background = Image.new("RGB", (1920, 1080), "#0B1020")
    draw = ImageDraw.Draw(background)
    draw.text((120, 84), "AUTOVIDEO  /  G0 技术样片", font=regular(28), fill="#4DB6E8")
    draw.text((120, 143), plan["title"], font=regular(78), fill="#F2F5FA")
    draw.text((124, 251), "把真实中文语音，看成一条会动的曲线", font=regular(34), fill="#B2C0D4")
    draw.rounded_rectangle((120, 344, 1800, 662), radius=28, fill="black", outline="#24334B", width=2)
    draw.text((156, 369), "真实语音  →  实时波形", font=regular(29), fill="#B2C0D4")
    draw.text((157, 604), "说话：上下起伏     ·     停顿：接近水平", font=regular(28), fill="#B2C0D4")
    draw.line((120, 788, 1800, 788), fill="#24334B", width=2)
    draw.text((120, 987), "本地中文 TTS · 实际音频驱动 · 1080p / 30 fps", font=regular(24), fill="#B2C0D4")
    background.save(OUT / "background.png")
    for index, b in enumerate(boundaries):
        overlay = Image.new("RGBA", (1920, 1080), (0, 0, 0, 0))
        painter = ImageDraw.Draw(overlay)
        font = regular(54)
        text = b["text"]
        box = painter.textbbox((0, 0), text, font=font)
        assert box[2] - box[0] < 1680, "Chinese subtitle overflow"
        painter.text((960, 874), text, font=font, fill="#F2F5FA", anchor="mt")
        overlay.save(OUT / f"caption-{index+1}.png")
    pause = Image.new("RGBA", (1920, 1080), (0, 0, 0, 0))
    ImageDraw.Draw(pause).text((960, 874), "短暂停顿 · 观察波形", font=regular(42), fill="#4DB6E8", anchor="mt")
    pause.save(OUT / "pause.png")
    save(OUT / "tts-metrics.json", {"provider": plan["provider"], "model": plan["model"],
        "voice": plan["voice"], "elapsed_seconds": time.monotonic()-start,
        "duration_seconds": duration, "real_time_factor": (time.monotonic()-start)/duration,
        "model_sha256": digest(ROOT / "assets/kokoro-v1.0.onnx"),
        "voices_sha256": digest(ROOT / "assets/voices-v1.0.bin"),
        "cloud_requests": 0, "api_cost": 0, "local_compute_cost": "NOT MEASURED"})
    print(f"REAL TTS: {duration:.3f}s, {total_frames} frames", flush=True)


def render():
    audit_sources()
    alignment = json.loads((OUT / "alignment.json").read_text())
    command("remotion-render", [NODE, CLI, "render", "src/index.ts", "Audiogram",
        OUT / "remotion-waveform.mp4", "--props=" + str(ROOT / "props.json"),
        "--width=1920", "--height=1080", "--fps=30",
        f"--duration={alignment['total_frames']}", "--codec=h264", "--audio-codec=aac",
        "--pixel-format=yuv420p", "--concurrency=2", "--crf=18",
        "--browser-executable=/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "--overwrite=false"], cwd=TEMPLATE, timeout=600)


def finish():
    a = json.loads((OUT / "alignment.json").read_text())
    argv = [FFMPEG, "-nostdin", "-hide_banner", "-n", "-i", OUT / "remotion-waveform.mp4",
            "-framerate", "30", "-loop", "1", "-i", OUT / "background.png"]
    for i in range(1, 5):
        argv += ["-framerate", "30", "-loop", "1", "-i", OUT / f"caption-{i}.png"]
    argv += ["-framerate", "30", "-loop", "1", "-i", OUT / "pause.png"]
    filters = ["[0:v]crop=1680:150:120:330,scale=1600:150[wave]", "[1:v][wave]overlay=160:425[base]"]
    previous = "base"
    for i, b in enumerate(a["segments"]):
        target = f"cap{i}"
        filters.append(f"[{previous}][{i+2}:v]overlay=0:0:enable='gte(t,{b['start_ms']/1000})*lt(t,{b['end_ms']/1000})'[{target}]")
        previous = target
    n3, n4 = a["segments"][2:4]
    filters.append(f"[{previous}][6:v]overlay=0:0:enable='gte(t,{n3['clip_end_sample']/a['sample_rate']})*lt(t,{n4['clip_start_sample']/a['sample_rate']})'[final]")
    argv += ["-filter_complex", ";".join(filters), "-map", "[final]", "-map", "0:a:0",
             "-frames:v", str(a["total_frames"]), "-fps_mode", "passthrough", "-c:v", "libx264", "-preset", "fast",
             "-crf", "18", "-pix_fmt", "yuv420p", "-c:a", "copy", "-movflags", "+faststart",
             OUT / "G0_声音与波形.mp4"]
    command("final-compose", argv)


def verify():
    import numpy as np
    import soundfile as sf
    from PIL import Image
    video = OUT / "G0_声音与波形.mp4"
    a = json.loads((OUT / "alignment.json").read_text())
    result = command("ffprobe", [FFPROBE, "-v", "error", "-count_frames", "-show_streams",
                     "-show_format", "-of", "json", video])
    probe = json.loads(result)
    save(OUT / "ffprobe.json", probe)
    streams = {s["codec_type"]: s for s in probe["streams"]}
    v, audio = streams["video"], streams["audio"]
    assert v["codec_name"] == "h264" and audio["codec_name"] == "aac"
    assert (v["width"], v["height"], v["avg_frame_rate"]) == (1920, 1080, "30/1")
    assert int(v["nb_read_frames"]) == a["total_frames"]
    assert abs(float(probe["format"]["duration"]) - a["duration_seconds"]) <= 1/30
    command("full-decode", [FFMPEG, "-nostdin", "-hide_banner", "-v", "error", "-xerror", "-i", video,
                            "-map", "0:v:0", "-map", "0:a:0", "-f", "null", "-"])
    command("audio-analysis", [FFMPEG, "-nostdin", "-hide_banner", "-i", video, "-vn", "-af",
                                "volumedetect", "-f", "null", "-"])
    command("loudness", [FFMPEG, "-nostdin", "-hide_banner", "-i", video, "-vn", "-af",
                         "ebur128=peak=true", "-f", "null", "-"])
    command("decoded-audio", [FFMPEG, "-nostdin", "-hide_banner", "-n", "-i", video, "-vn",
                              "-ac", "1", "-ar", "24000", OUT / "decoded-audio.wav"])
    original, rate = sf.read(OUT / "narration.wav")
    decoded, _ = sf.read(OUT / "decoded-audio.wav")
    length = min(len(original), len(decoded))
    correlation = float(np.corrcoef(original[:length], decoded[:length])[0,1])
    assert correlation > 0.98, "Encoded audio differs from real generated TTS"
    n3, n4 = a["segments"][2:4]
    silent_frame = round((n3["clip_end_sample"] + 0.55 * rate) / rate * 30)
    frames = [0, 22, 56, 130, 210, silent_frame, 340, a["total_frames"]-15, a["total_frames"]-1]
    frames = sorted(set(f for f in frames if 0 <= f < a["total_frames"]))
    keyframes = OUT / "keyframes"
    keyframes.mkdir(exist_ok=True)
    for frame in frames:
        command(f"frame-{frame:04}", [FFMPEG, "-nostdin", "-hide_banner", "-v", "error", "-n", "-i", video,
             "-vf", f"select=eq(n\\,{frame})", "-frames:v", "1", "-update", "1", keyframes / f"frame-{frame:04}.png"])
    # Compare the meaningful waveform region, excluding captions and titles.
    def wave_metrics(frame):
        data = np.array(Image.open(keyframes / f"frame-{frame:04}.png"))
        region = data[425:575, 120:1800]
        cyan = (region[:,:,0] < 135) & (region[:,:,1] > 110) & (region[:,:,2] > 150)
        ys = np.nonzero(cyan)[0]
        return {"cyan_pixels": int(cyan.sum()), "vertical_span_px": int(ys.max()-ys.min()+1) if len(ys) else 0}
    metrics = {str(f): wave_metrics(f) for f in frames}
    assert max(m["vertical_span_px"] for m in metrics.values()) > metrics[str(silent_frame)]["vertical_span_px"] + 8
    assert metrics[str(silent_frame)]["vertical_span_px"] <= 16
    assert max(m["vertical_span_px"] for m in metrics.values()) < 145, "Waveform clipped by crop"
    assert max(len(b["text"]) for b in a["segments"]) * 54 < 1680
    save(OUT / "qa.json", {"status": "PASS", "scope": "G0 mechanical checks only",
        "video": {"codec": v["codec_name"], "size": [v["width"], v["height"]],
                  "fps": v["avg_frame_rate"], "frames": int(v["nb_read_frames"]),
                  "duration": float(probe["format"]["duration"]), "full_decode": "PASS"},
        "audio": {"codec": audio["codec_name"], "sample_rate": audio["sample_rate"],
                  "correlation_with_real_tts": correlation, "full_decode": "PASS"},
        "animation": {"source": "actual PCM waveform via unchanged Remotion Oscilloscope",
                      "silent_frame": silent_frame, "keyframe_wave_metrics": metrics},
        "captions": {"segments": 4, "source": "measured generated-utterance sample boundaries",
                     "word_alignment": "NOT CLAIMED", "same_srt_and_burned_timeline": True},
        "human_listening_review": "NOT PERFORMED", "human_playback_acceptance": "PENDING USER"})
    thumbs = Image.new("RGB", (960*2, 540*math.ceil(len(frames)/2)), "#0B1020")
    for i,f in enumerate(frames):
        im = Image.open(keyframes / f"frame-{f:04}.png").resize((960,540))
        thumbs.paste(im, ((i%2)*960, (i//2)*540))
    thumbs.save(OUT / "contact-sheet.jpg")
    manifest = {"stage": "G0", "input": "narration.json", "provider": "local_kokoro_onnx",
                "trusted_source": "trusted-source.json", "artifacts": {}}
    for path in sorted(OUT.rglob("*")):
        if path.is_file() and path.name != "manifest.json":
            manifest["artifacts"][str(path.relative_to(ROOT))] = {"bytes": path.stat().st_size, "sha256": digest(path)}
    save(OUT / "manifest.json", manifest)
    print(json.dumps(json.loads((OUT / "qa.json").read_text()), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    LOGS.mkdir(exist_ok=True)
    actions = {"prepare": prepare, "render": render, "finish": finish, "verify": verify}
    assert len(sys.argv) == 2 and sys.argv[1] in actions
    actions[sys.argv[1]]()
