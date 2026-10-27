"""Trusted still-card executor over the same validated IR and measured voice.

No model, network, arbitrary code, embedded packaging or synthetic timestamps.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
import sys
import time

from PIL import Image, ImageDraw, ImageFont


def font(size: int):
    candidates = ("/System/Library/Fonts/Hiragino Sans GB.ttc",
                  "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc")
    for path in candidates:
        if Path(path).is_file():
            return ImageFont.truetype(path, size)
    raise ValueError("快速卡片需要可用的中文字体")


def lines(text: str, face, width: int) -> list[str]:
    """Pixel wrap with indivisible Latin words, model numbers and numeric units."""
    result, current = [], ""
    atoms = re.findall(r"[A-Za-z0-9]+(?:[.'’+-][A-Za-z0-9]+)*(?:%|％|℃|°C|小时|分钟|秒|毫安时|元|块)?|\n|.", text)
    current_atoms = []
    for char in atoms:
        if char == "\n":
            result.append(current)
            current, current_atoms = "", []
        elif current and face.getlength(current + char) > width:
            if char in "，。！？：；、）》】”’％%,.!?:;" and len(current_atoms) > 1:
                result.append("".join(current_atoms[:-1]))
                current_atoms = [current_atoms[-1], char]
            elif current[-1] in "（《【“‘(":
                result.append(current[:-1])
                current_atoms = [current[-1], char]
            else:
                result.append(current)
                current_atoms = [char]
            current = "".join(current_atoms)
        else:
            current += char
            current_atoms.append(char)
    if current:
        result.append(current)
    return result


def draw_text(draw, text: str, xy: tuple[int, int], *, width: int, size: int,
              max_lines: int, color: str, minimum: int = 24) -> list[str]:
    for selected in range(size, min(size, minimum) - 1, -2):
        face = font(selected)
        wrapped = lines(text, face, width)
        if len(wrapped) <= max_lines and all(face.getlength(line) <= width for line in wrapped):
            break
    else:
        raise ValueError("文字超过快速卡片的安全排版容量，请拆分段落")
    for index, line in enumerate(wrapped):
        draw.text((xy[0], xy[1] + index * (selected + 22)), line, font=face, fill=color)
    return wrapped


def contrast_text(color: str) -> str:
    rgb = [int(color[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4 for v in rgb]
    light = sum(a * b for a, b in zip(linear, (.2126, .7152, .0722)))
    return "#ffffff" if (1.05 / (light + .05)) >= ((light + .05) / .05) else "#101827"


def draw_editorial(draw, page: dict, style: dict, accent: str,
                   visible: set[int] | None = None) -> list[str]:
    """Lay out the model's screen copy inside measured, trusted panels."""
    import math

    points = page["points"]
    layout = page.get("layout", "notes")
    active = set(range(len(points))) if visible is None else visible
    result = []
    if layout == "focus" and len(points) == 1:
        point = points[0]
        color = style["text"] if 0 in active else style.get("inactive", "#c7d2ca")
        if point.get("label"):
            draw_text(draw, point["label"], (155, 330), width=1570, size=34,
                      max_lines=1, minimum=26, color=accent if 0 in active else color)
        body = lines(point["text"], font(100), 1570)
        y = 450 if point.get("label") else 340 + max(0, (370 - len(body) * 122) // 2)
        result += draw_text(draw, point["text"], (155, y), width=1570,
                            size=100, max_lines=2 if point.get("label") else 3,
                            minimum=42, color=color)
        return result

    comparison = layout == "comparison" and len(points) == 2
    columns = 2 if comparison or len(points) > 3 else 1
    if len(points) > 12:
        columns = 3
    rows = math.ceil(len(points) / columns)
    gap = 26
    panel_w = (1680 - gap * (columns - 1)) // columns
    panel_h = (451 - gap * (rows - 1)) // rows
    if panel_h < 42:
        raise ValueError("画面项目无法以可读字号放入卡片，请拆分画面")
    for index, point in enumerate(points):
        # Reading order goes left to right, then to the following row.
        x = 120 + index % columns * (panel_w + gap)
        y = 315 + index // columns * (panel_h + gap)
        shown = index in active
        color = style["text"] if shown else style.get("inactive", "#c7d2ca")
        if comparison:
            draw.rounded_rectangle((x, y, x + panel_w, y + panel_h), radius=22,
                                   fill=style.get("soft", "#eaf2ed" if index == 0 else "#f2f4f0"))
        inset = 32 if comparison else 18
        label = point.get("label", "")
        if label:
            draw_text(draw, label, (x + inset, y + 18), width=panel_w - 2 * inset,
                      size=30, max_lines=1, minimum=24, color=accent if shown else color)
        elif not comparison and panel_h >= 75:
            draw.text((x + inset, y + 14), f"{index + 1:02d}", font=font(25), fill=accent if shown else color)
        offset = 68 if label or (not comparison and panel_h >= 75) else 15
        size = 62 if comparison else 50 if rows <= 2 else 40 if rows <= 4 else 30
        max_lines = max(1, int((panel_h - offset - 16) / (min(size, 30) + 22)))
        # draw_text fits width; the returned line count also controls height.
        room = panel_h - offset - 10
        for use in range(size, 23, -2):
            wrapped = lines(point["text"], font(use), panel_w - 2 * inset)
            if len(wrapped) * (use + 22) <= room and all(font(use).getlength(line) <= panel_w - 2 * inset for line in wrapped):
                break
        else:
            raise ValueError("卡片文字超出组件的实际排版空间，请拆分画面")
        result += draw_text(draw, point["text"], (x + inset, y + offset), width=panel_w - 2 * inset,
                            size=use, max_lines=max_lines, minimum=use, color=color)
        if layout == "steps" and columns == 1 and index + 1 < len(points):
            draw.line((x + 30, y + panel_h + 3, x + 30, y + panel_h + gap - 3), fill=accent, width=3)
            draw.polygon([(x + 24, y + panel_h + gap - 10), (x + 36, y + panel_h + gap - 10),
                          (x + 30, y + panel_h + gap - 3)], fill=accent)
    return result


def point_windows(page: dict | None, timing: dict, segments: list[dict]) -> list[dict]:
    """Use measured speech boundaries for progressive screen reveals, never invented timings."""
    start, end = timing["start_frame"], timing["end_frame"]
    if not page or not page.get("layout"):
        return [{"start_frame": start, "end_frame": end, "visible": None}]
    starts = {s["id"]: round(s["start_ms"] * .03) for s in segments}
    onsets = [max(start, min(end - 1, starts.get(p.get("segment_id"), start))) for p in page["points"]]
    boundaries = sorted({start, end, *onsets})
    return [{"start_frame": left, "end_frame": right,
             "visible": [i for i, onset in enumerate(onsets) if onset <= left]}
            for left, right in zip(boundaries, boundaries[1:]) if right > left]


def draw_card(scene: dict, *, title: str, index: int, count: int, style: dict,
              badge: dict, notices: list[str], output: Path, editorial: dict | None = None,
              visible_points: set[int] | None = None, header: dict | None = None) -> dict:
    image = Image.new("RGB", (1920, 1080), style["background"])
    draw = ImageDraw.Draw(image)
    accent = style["accent"] if style.get("palette_source") == "brand" else badge.get("color") or style["accent"]
    if header:
        left, top = header["left"], header["top"]
        available = 1920-left-header["right"]-header["width"]-header["gap"]
        face = font(26)
        heading = title
        while heading and face.getlength(heading) > available:
            heading = heading[:-2] + "…" if len(heading) > 2 else ""
        draw.text((left, top+27), heading, font=face, fill=style["muted"], anchor="lm")
        draw.text((left, top+57), f"{index + 1:02d} / {count:02d}", font=font(18), fill=style["muted"], anchor="lm")
    else:
        draw.rounded_rectangle((80, 62, 130, 72), radius=5, fill=accent)
        draw_text(draw, title, (152, 43), width=1370, size=30, max_lines=2,
                  color=style["muted"], minimum=24)
    if badge.get("text") and not header:
        draw.rounded_rectangle((1615, 42, 1840, 101), radius=16, fill=accent)
        draw_text(draw, badge["text"], (1638, 51), width=182, size=27, max_lines=1,
                  color=contrast_text(accent), minimum=14)
    draw_text(draw, scene["props"]["title"], (80, 140), width=1540, size=44,
              max_lines=2, color=style["text"], minimum=24)
    if not header:
        draw.text((1710, 157), f"{index + 1:02d} / {count:02d}", font=font(25), fill=style["muted"])
    draw.rounded_rectangle((80, 275, 1840, 806), radius=30, fill=style["surface"])
    if editorial and editorial.get("layout"):
        wrapped = draw_editorial(draw, editorial, style, accent, visible_points)
        displayed_qualifiers = []
    elif editorial:
        points = editorial["points"]
        overview = bool(editorial.get("overview"))
        legacy = editorial.get("legacy_layout", False)
        wrapped = []
        step = 86 if overview else 116 if legacy else 160
        displayed_qualifiers = []
        for n, point in enumerate(points):
            y = 315 + n * step
            draw.rounded_rectangle((120, y + 8, 170, y + 58), radius=14, fill=accent)
            draw.text((134, y + 11), str(n + 1), font=font(29), fill=contrast_text(accent))
            wrapped.extend(draw_text(draw, point["text"], (205, y), width=1540, size=48 if legacy else 44,
                                     max_lines=1 if overview else 2, minimum=38, color=style["text"]))
            qualifiers = point.get("qualifiers", [])
            if qualifiers:
                draw_text(draw, " · ".join(qualifiers), (205, y + 116), width=1540,
                          size=25, minimum=22, max_lines=1, color=style["muted"])
                displayed_qualifiers.append({"point": n + 1, "text": qualifiers})
        if legacy and editorial.get("constraints"):
            # Historical voice/speed revisions keep their original display
            # contract. New manuscripts use point-scoped labels exclusively.
            draw.rounded_rectangle((110, 683, 1810, 788), radius=16, fill="#eef2ee")
            draw_text(draw, "口径与限制", (135, 690), width=250, size=21, max_lines=1, color=style["muted"])
            draw_text(draw, "  ·  ".join(editorial["constraints"]), (135, 722), width=1650,
                      size=27, minimum=22, max_lines=2, color=style["text"])
            displayed_qualifiers = editorial["constraints"]
    else:
        initial_lines = lines(scene["props"]["text"], font(57), 1530)
        body_y = 324 + max(0, (400 - len(initial_lines) * 79) // 2)
        wrapped = draw_text(draw, scene["props"]["text"], (166, body_y), width=1530,
                            size=57, max_lines=5, color=style["text"], minimum=34)
    if notices:
        draw.rounded_rectangle((80, 823, 1840, 875), radius=12, fill="#fff0cf")
        draw_text(draw, "；".join(notices), (104, 829), width=1690, size=25,
                  max_lines=1, color=style["warning"], minimum=18)
    draw.line((80, 1048, 1840, 1048), fill=style.get("line", "#dce5dd"), width=4)
    draw.line((80, 1048, 80 + round(1760 * (index + 1) / count), 1048), fill=accent, width=5)
    image.save(output)
    return {"body_lines": wrapped, "body_text_unchanged": "".join(wrapped) == scene["props"]["text"].replace("\n", ""),
            "notices": notices, "qualifiers": displayed_qualifiers if editorial else []}


def _stamp(ms: int) -> str:
    centiseconds = round(ms / 10)
    return f"{centiseconds // 360000}:{centiseconds // 6000 % 60:02d}:{centiseconds // 100 % 60:02d}.{centiseconds % 100:02d}"


def _ass_text(value: str) -> str:
    # ASS control characters must never interpret manuscript text as formatting.
    return value.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")


def write_ass(ir: dict, output: Path, cues: list[dict] | None = None, *, style: dict | None = None) -> None:
    header = """[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 2
[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,Hiragino Sans GB,44,&H00202718,&H00202718,&H00F1F5F3,&H00F1F5F3,0,0,0,0,100,100,0,0,3,6,0,2,170,170,105,1
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    if style and style.get("palette_source") == "brand":
        def ass_color(color: str) -> str:
            return "&H00" + color[5:7] + color[3:5] + color[1:3]
        header = header.replace("&H00202718", ass_color(style["subtitle"]))
        header = header.replace("&H00F1F5F3", ass_color(style["background"]))
    events = []
    face = font(44)
    for row in (cues if cues is not None else ir["narration"]["segments"]):
        wrapped = lines(row["text"].strip(), face, 1540)
        if len(wrapped) > 2 or any(face.getlength(line) > 1540 for line in wrapped):
            raise ValueError("字幕超过两行，请拆分讲稿短句")
        text = "\\N".join(_ass_text(line) for line in wrapped)
        events.append(f"Dialogue: 0,{_stamp(row['start_ms'])},{_stamp(row['end_ms'])},Caption,,0,0,0,,{text}")
    output.write_text(header + "\n".join(events) + "\n", encoding="utf-8")


def _quote(path: Path) -> str:
    return str(path).replace("'", "'\\''")


def render(pipeline, compiled: dict) -> None:
    from g2.production_plan import verify
    started = time.monotonic()
    saved = pipeline.PACKAGING_INPUT
    if not saved:
        raise pipeline.G2Fault("PRODUCTION_PLAN_INVALID", "快速制作缺少冻结的制作计划")
    plan = verify(saved)
    if plan["executor"]["id"] != "ffmpeg_cards":
        raise pipeline.G2Fault("PRODUCTION_EXECUTOR_MISMATCH", "快速执行器与制作计划不一致")
    ir = json.loads(pipeline.IR_PATH.read_text())
    root = pipeline.ARTIFACTS / "fast-cards"
    root.mkdir(exist_ok=False)
    record = pipeline.packaging_record()
    badge = record["badge"] if record else {"text": "", "color": plan["style"]["accent"]}
    editorial_path = pipeline.ARTIFACTS / "card-editorial.json"
    editorial_record = json.loads(editorial_path.read_text()) if editorial_path.exists() else {}
    editorial = {p["id"]: p for p in editorial_record.get("pages", [])}
    if editorial_record.get("version", 1) >= 2 and saved.get("operation_type") != "visual_revision":
        from g2.card_editorial import validate
        validate({"pages": [{"id": p["id"], "points": p["points"],
                              **({"layout": p["layout"]} if p.get("layout") else {})} for p in editorial.values()]},
                 {"groups": list(editorial.values())})
    entries, evidence = ["ffconcat version 1.0"], []
    for index, (scene, timing) in enumerate(zip(ir["scenes"], compiled["scenes"])):
        chapter_ids = {chapter["id"] for chapter in ir.get("content_structure", {}).get("chapters", [])
                       if any(sid in chapter["narration_segment_ids"] for sid in scene["narration_segment_ids"])}
        notices = [notice["message"] for notice in saved.get("content_notices", [])
                   if notice.get("chapter_index") is None or f"c{notice['chapter_index'] + 1}" in chapter_ids]
        page = editorial.get(scene["id"])
        if scene["props"].get("layout"):
            if page and (page["layout"] != scene["props"]["layout"] or
                         any({k: v for k, v in point.items() if k in {"text", "label", "segment_id"}} != props
                             for point, props in zip(page["points"], scene["props"]["points"])) or
                         len(page["points"]) != len(scene["props"]["points"])):
                raise pipeline.G2Fault("FAST_CARD_EDITORIAL_MISMATCH", "画面布局与已编译组件不一致")
            page = {**(page or {}), "layout": scene["props"]["layout"], "points": scene["props"]["points"]}
        if page:
            page = {**page, "legacy_layout": editorial_record.get("version", 1) < 2
                    and page.get("qualifier_layout_version", 1) < 2}
        if page and "\n".join(p["text"] for p in page["points"]) != scene["props"]["text"]:
            if saved.get("operation_type") != "visual_revision" or saved.get("scene_id") != scene["id"]:
                raise pipeline.G2Fault("FAST_CARD_EDITORIAL_MISMATCH", "画面重点与经过验证的计划不一致")
            page = {**page, "points": [{"text": text} for text in scene["props"]["text"].splitlines()]}
        windows = point_windows(page, timing, ir["narration"]["segments"])
        for stage, window in enumerate(windows):
            path = root / (f"card-{index:04d}.png" if stage == 0 else f"card-{index:04d}-{stage}.png")
            layout = draw_card(scene, header=plan.get("brand_layout"), title=ir["video"]["title"], index=index, count=len(ir["scenes"]),
                               style=plan["style"], badge=badge, notices=notices, output=path, editorial=page,
                               visible_points=set(window["visible"]) if window["visible"] is not None else None)
            frames = window["end_frame"] - window["start_frame"]
            entries += [f"file '{_quote(path)}'", "option framerate 30", f"duration {frames / 30:.12f}"]
        evidence.append({"scene_id": scene["id"], "narration_segment_ids": scene["narration_segment_ids"],
                         "text": scene["props"]["text"], "frames": timing["end_frame"] - timing["start_frame"],
                         "layout": page.get("layout") if page else None, "reveal_windows": windows, **layout})
    entries += [f"file '{_quote(path)}'", "option framerate 30"]
    concat = root / "cards.ffconcat"
    concat.write_text("\n".join(entries) + "\n", encoding="utf-8")
    ass = root / "captions.ass"
    from g2.caption_alignment import verify_plan
    captions = json.loads((pipeline.ARTIFACTS / "caption-alignment.json").read_text())
    verify_plan(ir, captions)
    write_ass(ir, ass, captions["cues"], style=plan["style"])
    pipeline.write_json(pipeline.ARTIFACTS / "fast-card-plan.json", {
        "version": 4, "method": "model-directed visual cards; measured speech reveals; short aligned captions",
        "manual_page_copy": False, "pages": evidence, "embedded_packaging": False,
        "production_plan_sha256": plan["sha256"]})
    ass_path = str(ass).replace("\\", "\\\\").replace(":", "\\:").replace("'", "'\\''")
    # Upsample the sparse card stream BEFORE subtitles so every cue is rendered.
    filters = f"fps=30,ass=filename='{ass_path}',format=yuv420p"
    encoders = []
    if sys.platform == "darwin":
        encoders.append(("h264_videotoolbox", ["-b:v", "5500k", "-maxrate", "7500k", "-bufsize", "15000k", "-allow_sw", "0"]))
    encoders.append(("libx264", ["-preset", "veryfast", "-crf", "20"]))
    failures = []
    for encoder, options in encoders:
        target = root / f"attempt-{encoder}.mp4"
        try:
            pipeline._run_fixed_command(f"fast-cards-{encoder}", [
                pipeline.FFMPEG, "-nostdin", "-hide_banner", "-v", "warning", "-n", "-safe", "0", "-f", "concat",
                "-i", concat, "-i", pipeline.ASSETS / "communications_noise.wav", "-vf", filters,
                "-map", "0:v:0", "-map", "1:a:0", "-frames:v", str(compiled["total_frames"]),
                "-t", str(compiled["total_frames"] / 30), "-c:v", encoder, *options, "-profile:v", "high",
                "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", "-movflags", "+faststart", target,
            ], pipeline.PROJECT, timeout=max(1200, round(compiled["total_frames"] / 30 * 6)))
            target.replace(pipeline.VIDEO_PATH)
            break
        except pipeline.G2Fault as exc:
            failures.append({"encoder": encoder, "code": exc.code, "message": str(exc)[:300]})
    else:
        raise pipeline.G2Fault("FAST_CARDS_ENCODE_FAILED", "快速卡片编码失败；尝试与日志已保留")
    pipeline.write_json(pipeline.ARTIFACTS / "production-execution.json", {
        "schema_version": 1, "production_plan_sha256": plan["sha256"], "executor": "ffmpeg_cards",
        "encoder": encoder, "fallbacks": failures, "render_seconds": round(time.monotonic() - started, 3),
        "form": "information_cards", "style": plan["style"], "page_count": len(evidence),
        "editorial_layout_version": 4, "caption_plan_version": 2,
        "manual_page_copy": False, "packaging_owner": "shared_package_stage", "music": plan["music"]})


def verify_captions(pipeline, qa: dict) -> dict:
    """Match the expected glyph mask, including empty gaps, not arbitrary dark pixels."""
    import numpy as np
    from g2.caption_alignment import verify_plan, srt_text
    ir = json.loads(pipeline.IR_PATH.read_text())
    plan = json.loads((pipeline.ARTIFACTS / "caption-alignment.json").read_text())
    verify_plan(ir, plan)
    if (pipeline.ARTIFACTS / "captions.srt").read_text() != srt_text(plan["cues"]):
        raise pipeline.G2Fault("FAST_CAPTION_TEXT_MISMATCH", "字幕文件与逐字覆盖计划不一致")
    targets = {round((row["start_ms"] + row["end_ms"]) * 30 / 2000) for row in plan["cues"]}
    for left, right in zip(plan["cues"], plan["cues"][1:]):
        if right["start_ms"] - left["end_ms"] >= 250:
            targets.add(round((left["end_ms"] + right["start_ms"]) * 30 / 2000))
    targets = sorted(targets)
    folder = pipeline.ARTIFACTS / "fast-caption-check"
    folder.mkdir(exist_ok=False)
    reference = pipeline.ARTIFACTS / "caption-reference"
    reference.mkdir(exist_ok=False)
    ass = reference / "expected.ass"
    style = ((getattr(pipeline, "PACKAGING_INPUT", None) or {}).get("production_plan") or {}).get("style", {})
    write_ass(ir, ass, plan["cues"], style=style)
    ass_path = str(ass).replace("\\", "\\\\").replace(":", "\\:").replace("'", "'\\''")
    expression = "+".join(f"eq(n\\,{frame})" for frame in targets)
    pipeline._run_fixed_command("fast-caption-pixels", [pipeline.FFMPEG, "-nostdin", "-v", "error", "-y",
        "-i", pipeline.VIDEO_PATH, "-vf", f"select={expression}", "-vsync", "0", folder / "frame-%04d.png"], pipeline.PROJECT)
    files = sorted(folder.glob("*.png"))
    pipeline._run_fixed_command("fast-caption-reference", [pipeline.FFMPEG, "-nostdin", "-v", "error", "-y",
        "-f", "lavfi", "-i", f"color=c=0x{style.get('background', '#f3f5f1')[1:]}:s=1920x1080:r=30:d={ir['video']['duration_ms'] / 1000}",
        "-vf", f"ass=filename='{ass_path}',select={expression}", "-vsync", "0", reference / "frame-%04d.png"], pipeline.PROJECT)
    expected = sorted(reference.glob("*.png"))
    if len(files) != len(targets) or len(expected) != len(targets):
        raise pipeline.G2Fault("FAST_CAPTION_MISSING", "未提取完整字幕检查帧")
    counts, scores = [], []
    for path, wanted in zip(files, expected):
        actual = np.asarray(Image.open(path).convert("RGB"))[878:1008, 90:1830]
        expected_pixels = np.asarray(Image.open(wanted).convert("RGB"))[878:1008, 90:1830]
        if style.get("palette_source") == "brand":
            bg = np.array([int(style["background"][i:i+2], 16) for i in (1, 3, 5)])
            fg = np.array([int(style["subtitle"][i:i+2], 16) for i in (1, 3, 5)])
            delta = fg-bg
            observed = ((actual.astype(float)-bg) @ delta) / max(1, delta @ delta) > .55
            required = ((expected_pixels.astype(float)-bg) @ delta) / max(1, delta @ delta) > .55
        else:
            observed = np.all(actual < 110, axis=2)
            required = np.all(expected_pixels < 110, axis=2)
        score = caption_mask_score(observed, required)
        if score < .90:
            raise pipeline.G2Fault("FAST_CAPTION_GLYPH_MISMATCH", f"字幕与原文的字形不一致：{path.name}，匹配 {score:.3f}")
        scores.append(score)
        if required.any():
            counts.append(int(observed.sum()))
    qa["fast_cards"] = {"caption_cues_checked": len(plan["cues"]), "caption_frames_checked": len(targets),
                        "minimum_caption_dark_pixels": min(counts), "minimum_glyph_f1": min(scores),
                        "exact_srt_coverage": True, "alignment": plan["method"],
                        "executor": (getattr(pipeline, 'PACKAGING_INPUT', None) or {}).get('production_plan', {}).get('executor', {}).get('id', 'ffmpeg_cards'),
                        "manual_page_copy": False}
    return qa


def caption_mask_score(observed, required) -> float:
    import numpy as np
    total = int(observed.sum()) + int(required.sum())
    return 1.0 if total == 0 else 2 * int(np.logical_and(observed, required).sum()) / total
