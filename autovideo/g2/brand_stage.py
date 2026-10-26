"""Trusted brand compositor shared by hybrid, FFmpeg and Remotion productions."""
from __future__ import annotations

import json
from pathlib import Path

from g2.branding import BrandError, frozen_input

FONT = "/System/Library/Fonts/Hiragino Sans GB.ttc"


def artwork(config: dict, folder: Path, assets: Path, label: str = "") -> tuple[Path, Path]:
    from PIL import Image, ImageDraw, ImageFont
    def font(text, maximum, width):
        for size in range(maximum, 15, -1):
            face = ImageFont.truetype(FONT, size)
            if face.getlength(text) <= width:
                return face
        return ImageFont.truetype(FONT, 16)

    logo = None
    if config["assets"].get("logo"):
        with Image.open(assets / config["assets"]["logo"]["file"]) as image:
            logo = image.convert("RGBA")
    corner = Image.new("RGBA", (520, 76))
    draw = ImageDraw.Draw(corner)
    draw.rounded_rectangle((0, 0, 520, 76), 18, fill=config["background"] + "ef")
    if logo:
        mark = logo.copy(); mark.thumbnail((48, 48))
        corner.alpha_composite(mark, (18 + (48-mark.width)//2, (76-mark.height)//2))
    else:
        draw.rounded_rectangle((22, 23, 50, 51), 7, fill=config["accent"])
    face = font(config["name"], 26 if label else 28, 426)
    draw.text((80, 27 if label else 38), config["name"], font=face, fill=config["text"], anchor="lm")
    if label:
        draw.text((80, 57), label, font=font(label, 18, 426), fill=config["accent"], anchor="lm")
    corner_path = folder / "corner.png"; corner.save(corner_path)
    tail = Image.new("RGBA", (1920, 1080), config["background"])
    draw = ImageDraw.Draw(tail)
    draw.line((860, 300, 1060, 300), fill=config["accent"], width=5)
    if logo:
        mark = logo.copy(); mark.thumbnail((136, 136))
        tail.alpha_composite(mark, ((1920-mark.width)//2, 350+(136-mark.height)//2))
    else:
        draw.rounded_rectangle((916, 372, 1004, 460), 22, fill=config["accent"])
    draw.text((960, 565), config["name"], font=font(config["name"], 82, 1560), fill=config["text"], anchor="mm")
    draw.text((960, 690), config["tagline"], font=font(config["tagline"], 42, 1450), fill=config["text"], anchor="mm")
    draw.text((960, 870), "RESEARCH  /  INSIGHT  /  VIDEO", font=ImageFont.truetype(FONT, 22), fill=config["accent"], anchor="mm")
    tail_path = folder / "outro.png"; tail.convert("RGB").save(tail_path)
    if config.get("intro_seconds", 0):
        head = Image.new("RGBA", (1920, 1080), config["background"])
        draw = ImageDraw.Draw(head)
        face = font(config["name"], 96, 1320)
        left = round((1920 - 188 - face.getlength(config["name"]))/2)
        if logo:
            mark = logo.copy(); mark.thumbnail((144, 144))
            head.alpha_composite(mark, (left+(144-mark.width)//2, (1080-mark.height)//2))
        else:
            draw.rounded_rectangle((left, 468, left+144, 612), 32, fill=config["accent"])
        draw.text((left+188, 540), config["name"], font=face, fill=config["text"], anchor="lm")
        head.convert("RGB").save(folder / "intro.png")
    return corner_path, tail_path


def sonic_track(config: dict, assets: Path, output: Path, *, opening: bool = False, tail_lead: float = .2) -> dict:
    import numpy as np
    import soundfile as sf
    duration = float(config["intro_seconds"] if opening else config["outro_seconds"])
    lead = .05 if opening else tail_lead
    audio = np.zeros((round(duration*48000), 2), dtype="float32")
    meta = {"enabled": False, "start_seconds": lead, "duration_seconds": 0}
    if config["assets"].get("sonic"):
        path = assets / config["assets"]["sonic"]["file"]
        sound, rate = sf.read(path, dtype="float32", always_2d=True)
        if rate != 48000:
            positions = np.arange(round(len(sound)*48000/rate))*rate/48000
            sound = np.column_stack([np.interp(positions, np.arange(len(sound)), channel)
                                     for channel in sound.T]).astype("float32")
        if sound.shape[1] == 1:
            sound = np.repeat(sound, 2, axis=1)
        start = round(lead*48000)
        length = min(len(sound), len(audio)-start)
        if opening:
            length = min(length, round(min(.7, duration-.25)*48000))
        sound = sound[:length].copy()
        fade = min(round(.18*48000), length//2)
        sound[:fade] *= np.linspace(0, 1, fade)[:, None]
        sound[-fade:] *= np.linspace(1, 0, fade)[:, None]
        peak = float(np.max(np.abs(sound)))
        if peak < .0001:
            raise BrandError("品牌声标近乎无声")
        # Cap uploaded sound levels; do not amplify quiet user recordings.
        sound *= min(1, .55/peak)
        audio[start:start+length] = sound
        meta.update(enabled=True, duration_seconds=length/48000, peak=float(np.max(np.abs(sound))))
    sf.write(output, audio, 48000, subtype="PCM_16")
    return meta


def build(runner, ffmpeg: Path, ffprobe: Path, request: dict, input_dir: Path,
          source: Path, output_dir: Path) -> dict:
    record = frozen_input(input_dir, request)
    if record is None:
        return {}
    config = record["profile"]
    output_dir.mkdir(parents=True, exist_ok=True)
    info = json.loads(runner("brand-probe", [ffprobe, "-v", "error", "-show_streams", "-of", "json", source]))
    video = next(s for s in info["streams"] if s["codec_type"] == "video")
    if (video["width"], video["height"], video["r_frame_rate"]) != (1920, 1080, "30/1"):
        raise BrandError("品牌合成需使用 1920×1080、30fps 的制作输出")
    content_frames = int(video["nb_frames"])
    duration = content_frames/30
    intro_frames = round(float(config.get("intro_seconds", 0))*30)
    intro_seconds = intro_frames/30
    tail_frames = round(float(config["outro_seconds"])*30)
    tail_seconds = tail_frames/30
    from g2 import brand_cta
    cta_frames = round(brand_cta.SECONDS*30) if (config.get("cta") or {}).get("enabled") else 0
    cta_seconds = cta_frames/30
    brand_card_frames = tail_frames-cta_frames
    config = {**config, "outro_seconds": tail_seconds}
    if "intro_seconds" in config:
        config["intro_seconds"] = intro_seconds
    label = (request.get("packaging_resolved") or {}).get("components", {}).get("label", "")
    corner, outro = artwork(config, output_dir, input_dir / "brand", label)
    sonic = sonic_track(config, input_dir / "brand", output_dir / "sonic.wav", tail_lead=cta_seconds+.2)
    intro_sonic = sonic_track(config, input_dir / "brand", output_dir / "intro.wav", opening=True) if intro_frames else {"enabled": False}
    head_video = (f"[4:v]fps=30,trim=end_frame={intro_frames},setpts=PTS-STARTPTS,format=yuv420p,"
                  "zoompan=z='min(zoom+0.001,1.025)':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':d=1:s=1920x1080:fps=30,"
                  f"fade=t=in:st=0:d=0.12:color=0x{config['background'][1:]},"
                  f"fade=t=out:st={max(0, intro_seconds-.1)}:d=0.1:color=0x{config['background'][1:]}[head];") if intro_frames else ""
    video_concat = "[head][body][tail]concat=n=3:v=1:a=0,fps=30[v];" if intro_frames else "[body][tail]concat=n=2:v=1:a=0,fps=30[v];"
    audio_concat = (f"[5:a]atrim=duration={intro_seconds},asetpts=PTS-STARTPTS[ah];"
                    "[ah][a0][a1]concat=n=3:v=0:a=1[a]") if intro_frames else "[a0][a1]concat=n=2:v=0:a=1[a]"
    extra_inputs = (["-loop", "1", "-framerate", "30", "-i", output_dir / "intro.png", "-i", output_dir / "intro.wav"] if intro_frames else [])
    cta_filters = ""
    if cta_frames:
        cta_filters = brand_cta.filters(6 if intro_frames else 4, cta_frames, config)
        for path in brand_cta.artwork(config, output_dir, FONT):
            extra_inputs.extend(["-loop", "1", "-framerate", "30", "-i", path])
    reveal = config.get("corner_reveal_seconds", 0)
    corner_filters = (f"[1:v]fps=30,format=rgba,fade=t=in:st=0:d={reveal}:alpha=1[corner];"
                      f"[content][corner]overlay=x='W-w-48+24*(1-min(t/{reveal},1))':y=36:shortest=1[body];") if reveal else "[content][1:v]overlay=x=W-w-48:y=36:shortest=1[body];"
    filters = (
        "[0:v]setpts=PTS-STARTPTS[content];"
        + corner_filters +
        f"[2:v]fps=30,trim=end_frame={brand_card_frames},setpts=PTS-STARTPTS,"
        f"format=yuv420p,fade=t=in:st=0:d=0.25:color=0x{config['background'][1:]}"
        f"[{'brand_card' if cta_frames else 'tail'}];"
        + cta_filters + head_video + video_concat +
        f"[0:a]aformat=sample_rates=48000:channel_layouts=stereo,apad,atrim=duration={duration},asetpts=PTS-STARTPTS[a0];"
        f"[3:a]atrim=duration={tail_seconds},asetpts=PTS-STARTPTS[a1];"
        + audio_concat
    )
    target = output_dir / "video.mp4"
    runner("brand-compose", [ffmpeg, "-nostdin", "-v", "error", "-y", "-i", source,
           "-loop", "1", "-framerate", "30", "-i", corner, "-loop", "1", "-framerate", "30", "-i", outro,
           "-i", output_dir / "sonic.wav",
           *extra_inputs,
           "-filter_complex", filters, "-map", "[v]", "-map", "[a]",
           "-frames:v", str(intro_frames+content_frames+tail_frames), "-r", "30", "-fps_mode", "cfr", "-c:v", "libx264", "-preset", "fast", "-crf", "18",
           "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", target])
    # Full video and audio decode is required before publishing the new final artifact.
    runner("brand-decode", [ffmpeg, "-nostdin", "-v", "error", "-xerror", "-i", target, "-f", "null", "-"])
    final = json.loads(runner("brand-final-probe", [ffprobe, "-v", "error", "-show_streams", "-of", "json", target]))
    final_video = next(s for s in final["streams"] if s["codec_type"] == "video")
    audio = next(s for s in final["streams"] if s["codec_type"] == "audio")
    expected = intro_seconds + duration + tail_seconds
    if int(final_video["nb_frames"]) != intro_frames+content_frames+tail_frames or abs(float(audio["duration"])-expected) > .1:
        raise BrandError("品牌成片音画时长不一致")
    meta = {"status": "PASS", "brand_sha256": record["sha256"], "profile": config,
            "intro_seconds": intro_seconds, "intro_sonic": intro_sonic,
            "content_seconds": duration, "outro_seconds": tail_seconds, "duration_seconds": expected,
            "corner_reveal_seconds": reveal, "brand_card_seconds": brand_card_frames/30,
            "cta": {"enabled": bool(cta_frames), "seconds": cta_seconds, "start_seconds": intro_seconds+duration,
                    "actions": [key for key, _ in brand_cta.ACTIONS] if cta_frames else []},
            "sonic": {**sonic, "start_seconds": intro_seconds+duration+sonic["start_seconds"]},
            "narration_shift_seconds": intro_seconds, "decoded_all_streams": True,
            "frames": int(final_video["nb_frames"])}
    (output_dir / "brand-qa.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    # Only final captions/chapters shift. The content source and its QA stay intact.
    for name in ("captions.srt", "chapters.json"):
        candidates = [folder / name for folder in (source.parent, output_dir.parent / "package", output_dir.parent)]
        original = next((p for p in candidates if p.is_file()), None)
        if original:
            if name == "captions.srt":
                from g2.package_stage import shift_srt
                (output_dir / name).write_text(shift_srt(original.read_text(encoding="utf-8"), round(intro_seconds*1000)), encoding="utf-8")
            else:
                chapters = json.loads(original.read_text(encoding="utf-8"))
                chapters.update(duration_seconds=expected, timeline="branded")
                offset_ms = round(intro_seconds*1000)
                for chapter in chapters.get("chapters", []):
                    chapter["start_ms"] += offset_ms
                    chapter["end_ms"] += offset_ms
                if intro_frames:
                    chapters["brand_offset_ms"] = offset_ms
                    chapters.setdefault("chapters", []).insert(0, {"id": "brand_intro", "title": config["name"],
                        "start_ms": 0, "end_ms": offset_ms, "scene_ids": []})
                if cta_frames:
                    chapters.setdefault("chapters", []).append({"id": "brand_cta", "title": "点赞 · 收藏 · 关注",
                        "start_ms": round((intro_seconds+duration)*1000),
                        "end_ms": round((intro_seconds+duration+cta_seconds)*1000), "scene_ids": []})
                chapters.setdefault("chapters", []).append({"id": "brand_outro", "title": config["name"],
                    "start_ms": round((intro_seconds+duration+cta_seconds)*1000), "end_ms": round(expected*1000), "scene_ids": []})
                (output_dir / name).write_text(json.dumps(chapters, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta


def run(pipeline, request: dict) -> dict:
    import os
    source = pipeline.ARTIFACTS / "mix/video.mp4"
    if not source.is_file():
        source = pipeline.ARTIFACTS / "package/video.mp4"
    if not source.is_file():
        source = pipeline.VIDEO_PATH
    def runner(label, argv, timeout=900):
        return pipeline._run_fixed_command(label, argv, pipeline.PROJECT, timeout=timeout)
    return build(runner, pipeline.FFMPEG, pipeline.FFPROBE, request,
                 Path(os.environ["AUTOVIDEO_G2_INTENT_FILE"]).parent, source, pipeline.ARTIFACTS / "brand")
