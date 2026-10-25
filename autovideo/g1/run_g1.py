"""G1 real-audio IR validation and fixed, allowlisted Remotion rendering."""
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
import wave
from contextlib import redirect_stdout
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parent
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))
FIXTURE = ROOT / "fixtures"
ASSETS = FIXTURE / "assets"
SEGMENTS_DIR = ASSETS / "segments"
ARTIFACTS = ROOT / "artifacts"
LOGS = ROOT / "logs"
IR_PATH = FIXTURE / "communication-noise.video-ir.json"
VIDEO_PATH = ARTIFACTS / "G1_通信噪声.mp4"
NODE = Path(shutil.which("node") or "")
RENDERER = ROOT / "renderer"
CLI = PROJECT / "g0/vendor/remotion-audiogram/node_modules/@remotion/cli/remotion-cli.js"
PYTHON = PROJECT / "g0/.venv/bin/python"
FFMPEG = PROJECT / "g0/darwin_arm64/ffmpeg"
FFPROBE = PROJECT / "g0/darwin_arm64/ffprobe"
CHROME = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
MODEL = PROJECT / "g0/assets/kokoro-v1.0.onnx"
VOICES = PROJECT / "g0/assets/voices-v1.0.bin"
FPS = 30
SAMPLE_RATE = 24000
VOICE = "zf_xiaoxiao"
PHRASES = [
    "小安准备把一条信息发给小宁。",
    "信号沿着信道向接收方传送。",
    "这时，噪声进入信道并干扰信号。",
    "信息到达时，内容可能已经和原文不同。",
    "这就是通信过程中噪声造成的影响。",
]


def save(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        if hasattr(hashlib, "file_digest"):
            return hashlib.file_digest(stream, "sha256").hexdigest()
        return hashlib.sha256(stream.read()).hexdigest()


def record(label: str, argv: list[str], cwd: Path, result: subprocess.CompletedProcess, elapsed: float):
    LOGS.mkdir(parents=True, exist_ok=True)
    output_path = LOGS / f"{label}.log"
    if output_path.exists():
        suffix = 2
        while (LOGS / f"{label}-{suffix:02}.log").exists():
            suffix += 1
        output_path = LOGS / f"{label}-{suffix:02}.log"
    output_path.write_text(result.stdout or "", encoding="utf-8")
    item = {"time_utc": datetime.now(timezone.utc).isoformat(), "label": label,
            "argv": list(map(str, argv)), "cwd": str(cwd), "exit_code": result.returncode,
            "elapsed_seconds": round(elapsed, 3), "output": str(output_path.relative_to(ROOT))}
    with (LOGS / "commands.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(item, ensure_ascii=False) + "\n")
    print(f"COMMAND {label}: exit={result.returncode}, elapsed={item['elapsed_seconds']}s, log={item['output']}", flush=True)
    if result.returncode:
        print((result.stdout or "")[-7000:], flush=True)
        raise RuntimeError(f"command failed: {label} (see {output_path})")
    return result.stdout or ""


def run_command(label: str, argv: list[str | Path], cwd: Path, timeout=900):
    executable = Path(argv[0]).resolve()
    allowed = {NODE.resolve(), PYTHON.resolve(), FFMPEG.resolve(), FFPROBE.resolve()}
    if executable not in allowed:
        raise RuntimeError(f"executable is not on the fixed tool allowlist: {executable}")
    args = list(map(str, argv))
    print(f"RUN {label}: {json.dumps(args, ensure_ascii=False)}", flush=True)
    start = time.monotonic()
    result = subprocess.run(args, cwd=cwd, env=os.environ.copy(), stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, timeout=timeout, check=False)
    return record(label, args, cwd, result, time.monotonic() - start)


def run_step(label, fn):
    start = time.monotonic()
    error = None
    try:
        fn()
    except Exception as exc:
        error = exc
    elapsed = round(time.monotonic() - start, 3)
    LOGS.mkdir(parents=True, exist_ok=True)
    with (LOGS / "commands.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"time_utc": datetime.now(timezone.utc).isoformat(), "label": label,
            "argv": [str(PYTHON), "-u", "g1/run_g1.py", label], "cwd": str(PROJECT),
            "exit_code": 0 if error is None else 1, "elapsed_seconds": elapsed, "output": "logs/execution.log"}, ensure_ascii=False) + "\n")
    if error is not None:
        print(f"STEP {label}: FAIL in {elapsed}s: {error}", flush=True)
        raise error
    print(f"STEP {label}: PASS in {elapsed}s", flush=True)


def prepare():
    import numpy as np
    import soundfile as sf
    from kokoro_onnx import Kokoro
    from misaki import zh

    required = [NODE, MODEL, VOICES, CLI, FFMPEG, FFPROBE, CHROME]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise FileNotFoundError("G1 local media toolchain is incomplete: " + ", ".join(missing))
    if VIDEO_PATH.exists() or (ASSETS / "communications_noise.wav").exists():
        raise FileExistsError("G1 outputs already exist; archive this run before preparing a new one")
    ASSETS.mkdir(parents=True, exist_ok=True)
    SEGMENTS_DIR.mkdir(parents=True, exist_ok=True)
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    start = time.monotonic()
    engine = Kokoro(str(MODEL), str(VOICES))
    g2p = zh.ZHG2P()
    chunks: list[np.ndarray] = []
    segment_rows = []
    cursor = int(SAMPLE_RATE * 0.25)
    chunks.append(np.zeros(cursor, dtype=np.float32))
    pauses_ms = [430, 480, 460, 500, 0]
    for index, text in enumerate(PHRASES):
        phonemes, _ = g2p(text)
        if "❓" in phonemes:
            raise ValueError(f"Chinese G2P could not resolve phrase: {text}")
        samples, sample_rate = engine.create(phonemes, voice=VOICE, speed=1.0, is_phonemes=True, trim=True)
        if sample_rate != SAMPLE_RATE or not len(samples):
            raise RuntimeError(f"unexpected TTS output: {sample_rate} Hz, {len(samples)} samples")
        # Keep a real-utterance fixture for each phrase and a single lossless timeline source.
        phrase_id = f"n{index + 1}"
        sf.write(SEGMENTS_DIR / f"{phrase_id}.wav", samples, SAMPLE_RATE, subtype="PCM_16")
        active = np.flatnonzero(np.abs(samples) > 0.003)
        if not len(active):
            raise RuntimeError(f"TTS returned a silent segment: {phrase_id}")
        first = cursor + max(0, int(active[0]) - 1200)
        last = cursor + min(len(samples), int(active[-1]) + 1200)
        segment_rows.append({"id": phrase_id, "text": text,
            "start_ms": (first * 1000 + SAMPLE_RATE // 2) // SAMPLE_RATE,
            "end_ms": (last * 1000 + SAMPLE_RATE // 2) // SAMPLE_RATE,
            "clip_start_sample": cursor, "clip_end_sample": cursor + len(samples),
            "speech_first_sample": int(active[0]), "speech_last_sample": int(active[-1]),
            "pause_after_ms": pauses_ms[index], "sample_rate": SAMPLE_RATE})
        chunks.append(samples)
        cursor += len(samples)
        pause_samples = round(pauses_ms[index] * SAMPLE_RATE / 1000)
        if pause_samples:
            chunks.append(np.zeros(pause_samples, dtype=np.float32))
            cursor += pause_samples
        print(json.dumps(segment_rows[-1], ensure_ascii=False), flush=True)

    audio = np.concatenate(chunks)
    total_frames = math.ceil(len(audio) / (SAMPLE_RATE / FPS))
    audio = np.pad(audio, (0, total_frames * (SAMPLE_RATE // FPS) - len(audio)))
    duration_ms = (total_frames * 1000 + FPS // 2) // FPS
    duration = len(audio) / SAMPLE_RATE
    if not 10 <= duration <= 20:
        raise RuntimeError(f"real TTS timeline must be 10–20 seconds, measured {duration:.3f}s")
    audio_path = ASSETS / "communications_noise.wav"
    sf.write(audio_path, audio, SAMPLE_RATE, subtype="PCM_16")

    source_text = "".join(PHRASES)
    asset = {"id": "communication_voice", "type": "audio", "path": "assets/communications_noise.wav",
             "origin": "local Kokoro-82M v1.0 TTS; separately generated Chinese phrases",
             "sha256": digest(audio_path)}
    narration_segments = [{key: value for key, value in row.items() if key in {"id", "text", "start_ms", "end_ms"}}
                          for row in segment_rows]
    beats = [
        {"id": "reveal_sender", "action": "reveal", "target_id": "node:sender", "trigger": {"segment_id": "n1", "boundary": "start", "offset_ms": 0}},
        {"id": "reveal_channel", "action": "reveal", "target_id": "node:channel", "trigger": {"segment_id": "n1", "boundary": "end", "offset_ms": 0}},
        {"id": "reveal_sender_edge", "action": "reveal", "target_id": "edge:sender_to_channel", "trigger": {"segment_id": "n1", "boundary": "end", "offset_ms": 80}},
        {"id": "reveal_receiver", "action": "reveal", "target_id": "node:receiver", "trigger": {"segment_id": "n2", "boundary": "start", "offset_ms": 0}},
        {"id": "reveal_receiver_edge", "action": "reveal", "target_id": "edge:channel_to_receiver", "trigger": {"segment_id": "n2", "boundary": "start", "offset_ms": 0}},
        {"id": "activate_main_path", "action": "activate_path", "target_id": "path:main", "trigger": {"segment_id": "n2", "boundary": "start", "offset_ms": 450}},
        {"id": "disrupt_channel", "action": "mark_disrupted", "target_id": "edge:channel_to_receiver", "trigger": {"segment_id": "n3", "boundary": "start", "offset_ms": 0}},
        {"id": "highlight_receiver", "action": "highlight", "target_id": "node:receiver", "trigger": {"segment_id": "n4", "boundary": "start", "offset_ms": 0}},
        {"id": "highlight_channel", "action": "highlight", "target_id": "node:channel", "trigger": {"segment_id": "n5", "boundary": "start", "offset_ms": 0}},
    ]
    ir = {
        "schema_version": "0.2",
        "video": {"title": "信息传输中的噪声干扰", "width": 1920, "height": 1080, "fps": FPS, "duration_ms": duration_ms},
        "source": {"language": "zh-CN", "original_text": source_text,
                   "text_sha256": hashlib.sha256(source_text.encode("utf-8")).hexdigest()},
        "style": {"preset": "minimal_dark", "seed": 20261013},
        "assets": [asset],
        "narration": {"text": source_text, "audio_asset_id": asset["id"], "segments": narration_segments,
                      "alignment": {"method": "provider", "granularity": "phrase"}},
        "scenes": [{"id": "communication_flow", "start_ms": 0, "duration_ms": duration_ms,
            "component": "flow_diagram",
            "props": {"nodes": [{"id": "sender", "label": "发送端 · 小安"}, {"id": "channel", "label": "传输信道"}, {"id": "receiver", "label": "接收端 · 小宁"}],
                      "edges": [{"id": "sender_to_channel", "from": "sender", "to": "channel"}, {"id": "channel_to_receiver", "from": "channel", "to": "receiver"}],
                      "paths": [{"id": "main", "edge_ids": ["sender_to_channel", "channel_to_receiver"]}]},
            "narration_segment_ids": [row["id"] for row in narration_segments],
            "purpose": "说明信息沿信道传送时受到噪声干扰后可能发生改变",
            "visual_intent": {"viewer_understands": "噪声会在传输过程中干扰信号，使接收内容与发送内容不同。",
                              "before": "发送端和信道逐步显现，传输路径尚未启动。",
                              "after": "信号沿路径移动，在信道到接收端的边上出现噪声干扰。"},
            "beats": beats}],
        "audio_mix": {"narration_gain_db": 0},
    }
    save(IR_PATH, ir)
    alignment = {"method": "separate real TTS utterances with measured PCM activity bounds",
        "granularity": "phrase", "voice": VOICE, "provider": "local_kokoro_onnx", "model": "Kokoro-82M-v1.0",
        "activity_threshold": 0.003, "edge_guard_ms": 50, "sample_rate": SAMPLE_RATE,
        "duration_seconds": duration, "total_frames": total_frames, "segments": segment_rows,
        "limitation": "Phrase boundaries only; no word-level alignment accuracy is claimed."}
    save(ARTIFACTS / "alignment.json", alignment)
    save(ARTIFACTS / "tts-metrics.json", {"provider": "local_kokoro_onnx", "model": "Kokoro-82M-v1.0",
        "voice": VOICE, "sample_rate": SAMPLE_RATE, "duration_seconds": duration,
        "generation_seconds": round(time.monotonic()-start, 3), "cloud_requests": 0, "api_cost": 0,
        "model_sha256": digest(MODEL), "voice_file_sha256": digest(VOICES), "audio_sha256": digest(audio_path)})
    def stamp(ms):
        return f"{ms//3600000:02}:{ms//60000%60:02}:{ms//1000%60:02},{ms%1000:03}"
    (ARTIFACTS / "captions.srt").write_text("\n\n".join(
        f"{i+1}\n{stamp(s['start_ms'])} --> {stamp(s['end_ms'])}\n{s['text']}"
        for i, s in enumerate(narration_segments)) + "\n", encoding="utf-8")
    print(f"REAL TTS READY: {duration:.3f}s, {total_frames} frames; audio SHA-256 {asset['sha256']}", flush=True)


def validate():
    import soundfile as sf
    from g1.video_ir import schema_action_catalog, validate_video_ir

    ir = json.loads(IR_PATH.read_text(encoding="utf-8"))
    compiled = validate_video_ir(ir, FIXTURE)
    audio, sample_rate = sf.read(ASSETS / "communications_noise.wav", dtype="float32")
    actual_duration = len(audio) / sample_rate
    planned_duration = ir["video"]["duration_ms"] / 1000
    if abs(actual_duration - planned_duration) > 1 / FPS:
        raise RuntimeError(f"audio/video duration mismatch: {actual_duration:.6f}s vs {planned_duration:.6f}s")
    save(ARTIFACTS / "compiled-timeline.json", compiled)
    save(ARTIFACTS / "action-catalog.json", schema_action_catalog())
    print(f"VALID IR 0.2: {compiled['total_frames']} frames; {len(compiled['beats'])} beats; actual WAV {actual_duration:.3f}s", flush=True)


def test():
    run_command("pytest-g1", [PYTHON, "-m", "pytest", "g1/tests/test_video_ir_v0_2_runtime.py", "-q", "--disable-warnings"], PROJECT, timeout=300)


def render():
    ir = json.loads(IR_PATH.read_text(encoding="utf-8"))
    compiled = json.loads((ARTIFACTS / "compiled-timeline.json").read_text(encoding="utf-8"))
    props = {"ir": {**compiled, "audio_public_file": "assets/communications_noise.wav"}}
    props_path = ARTIFACTS / "render-props.json"
    save(props_path, props)
    if not (RENDERER / "node_modules").exists():
        (RENDERER / "node_modules").symlink_to(PROJECT / "g0/vendor/remotion-audiogram/node_modules", target_is_directory=True)
    argv = [NODE, CLI, "render", "src/index.ts", "G1CommunicationNoise", VIDEO_PATH,
        f"--props={props_path}", f"--public-dir={FIXTURE}", "--width=1920", "--height=1080", "--fps=30",
        f"--duration={compiled['total_frames']}", "--codec=h264", "--audio-codec=aac", "--pixel-format=yuv420p",
        "--concurrency=2", "--crf=18", f"--browser-executable={CHROME}", "--overwrite=false"]
    run_command("remotion-render", argv, RENDERER, timeout=900)


def verify():
    import numpy as np
    import soundfile as sf
    from PIL import Image, ImageDraw, ImageFont
    from g1.video_ir import validate_video_ir

    ir = json.loads(IR_PATH.read_text(encoding="utf-8"))
    compiled = validate_video_ir(ir, FIXTURE)
    probe_raw = run_command("ffprobe", [FFPROBE, "-v", "error", "-count_frames", "-show_streams", "-show_format", "-of", "json", VIDEO_PATH], PROJECT)
    probe = json.loads(probe_raw)
    save(ARTIFACTS / "ffprobe.json", probe)
    streams = {stream["codec_type"]: stream for stream in probe["streams"]}
    video, audio = streams["video"], streams["audio"]
    if video["codec_name"] != "h264" or audio["codec_name"] != "aac":
        raise RuntimeError("expected H.264 video and AAC audio streams")
    if (video["width"], video["height"], video["avg_frame_rate"]) != (1920, 1080, "30/1"):
        raise RuntimeError("render dimensions or frame rate do not match Video IR v0.2")
    frames = int(video["nb_read_frames"])
    seconds = float(probe["format"]["duration"])
    if frames != compiled["total_frames"] or not 10 <= seconds <= 20:
        raise RuntimeError(f"unexpected video length: {frames} frames / {seconds:.3f}s")
    run_command("full-decode", [FFMPEG, "-nostdin", "-hide_banner", "-v", "error", "-xerror", "-i", VIDEO_PATH,
        "-map", "0:v:0", "-map", "0:a:0", "-f", "null", "-"], PROJECT)
    decoded = ARTIFACTS / "decoded-audio.wav"
    run_command("decoded-audio", [FFMPEG, "-nostdin", "-hide_banner", "-v", "error", "-y", "-i", VIDEO_PATH,
        "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), decoded], PROJECT)
    original, rate = sf.read(ASSETS / "communications_noise.wav", dtype="float32")
    decoded_samples, _ = sf.read(decoded, dtype="float32")
    n = min(len(original), len(decoded_samples))
    correlation = float(np.corrcoef(original[:n], decoded_samples[:n])[0, 1])
    rms = float(np.sqrt(np.mean(np.square(decoded_samples))))
    if correlation < 0.98 or rms < 0.01:
        raise RuntimeError(f"audio does not match real TTS fixture (r={correlation:.4f}, RMS={rms:.4f})")

    keyframes = ARTIFACTS / "keyframes"
    keyframes.mkdir(parents=True, exist_ok=True)
    targets = {"reveal_sender": "首次显现发送端", "activate_main_path": "信号路径激活", "disrupt_channel": "信道噪声干扰"}
    beat_map = {beat["id"]: beat for beat in compiled["beats"]}
    captures = {}
    for beat_id, label in targets.items():
        beat_frame = beat_map[beat_id]["frame"]
        for relative, suffix in [(-3, "before"), (4, "after")]:
            frame = max(0, min(frames - 1, beat_frame + relative))
            image_path = keyframes / f"{beat_id}-{suffix}-f{frame:04}.png"
            run_command(f"frame-{beat_id}-{suffix}", [FFMPEG, "-nostdin", "-hide_banner", "-v", "error", "-y", "-i", VIDEO_PATH,
                "-vf", f"select=eq(n\\,{frame})", "-frames:v", "1", "-update", "1", image_path], PROJECT)
            captures[f"{beat_id}_{suffix}"] = {"frame": frame, "path": str(image_path.relative_to(ROOT))}
    comparisons = {}
    for beat_id, crop_box in [("reveal_sender", (220, 330, 620, 560)),
                              ("activate_main_path", (560, 365, 1370, 510)),
                              ("disrupt_channel", (560, 365, 1370, 660))]:
        before = np.asarray(Image.open(ROOT / captures[f"{beat_id}_before"]["path"]).convert("RGB").crop(crop_box), dtype=np.int16)
        after = np.asarray(Image.open(ROOT / captures[f"{beat_id}_after"]["path"]).convert("RGB").crop(crop_box), dtype=np.int16)
        changed = np.max(np.abs(after-before), axis=2) > 25
        ratio = float(np.mean(changed))
        mean_delta = float(np.mean(np.abs(after-before)))
        comparisons[beat_id] = {"crop": crop_box, "changed_pixel_ratio": ratio, "mean_absolute_channel_delta": mean_delta}
        if ratio < 0.003 or mean_delta < 1.0:
            raise RuntimeError(f"beat does not create observable pixel change: {beat_id}, ratio={ratio:.4f}, delta={mean_delta:.3f}")

    contact = Image.new("RGB", (1920, 3*580), "#101827")
    draw = ImageDraw.Draw(contact)
    font = ImageFont.truetype("/System/Library/Fonts/Hiragino Sans GB.ttc", 25)
    for index, beat_id in enumerate(targets):
        before = Image.open(ROOT / captures[f"{beat_id}_before"]["path"]).convert("RGB")
        after = Image.open(ROOT / captures[f"{beat_id}_after"]["path"]).convert("RGB")
        before.thumbnail((920, 518)); after.thumbnail((920, 518))
        y = index*580+42
        contact.paste(before, (20, y)); contact.paste(after, (980, y))
        draw.text((25, index*580+8), f"{targets[beat_id]} · 动作前", font=font, fill="#F3F6FC")
        draw.text((985, index*580+8), f"{targets[beat_id]} · 动作后", font=font, fill="#F3F6FC")
    contact.save(ARTIFACTS / "contact-sheet.jpg", quality=90)
    qa = {"status": "PASS", "gate": "G1", "schema": "Video IR v0.2", "video": {
        "codec": video["codec_name"], "width": video["width"], "height": video["height"], "fps": video["avg_frame_rate"],
        "frames": frames, "duration_seconds": seconds, "full_decode": "PASS"},
        "audio": {"codec": audio["codec_name"], "sample_rate": audio["sample_rate"], "channels": audio["channels"],
                  "decoded_tts_correlation": correlation, "decoded_rms": rms, "full_decode": "PASS"},
        "timeline": {"absolute_boundary_frame_rule": "floor((ms*30+500)/1000)", "total_frames": compiled["total_frames"],
                     "scene_count": len(compiled["scenes"]), "beat_count": len(compiled["beats"])},
        "visual_beats": {"captures": captures, "comparisons": comparisons},
        "captions": {"phrase_count": len(ir["narration"]["segments"]), "srt": "captions.srt", "burned_in": True,
                     "alignment_granularity": "phrase", "word_alignment_claimed": False}}
    save(ARTIFACTS / "qa.json", qa)
    print(f"MEDIA PASS: {seconds:.3f}s, {frames} frames, real TTS audio correlation={correlation:.5f}", flush=True)


def write_manifest():
    items = []
    for folder in [FIXTURE / "assets", ARTIFACTS, LOGS]:
        for path in sorted(folder.rglob("*")):
            if folder == LOGS and path.relative_to(LOGS).parts[0].startswith("attempt-"):
                continue
            if path.is_file() and path.name != "manifest.json":
                items.append({"path": str(path.relative_to(ROOT)), "bytes": path.stat().st_size, "sha256": digest(path)})
    for path in [IR_PATH, PROJECT / "G1_REPORT.md"]:
        if path.is_file():
            items.append({"path": str(path.relative_to(PROJECT)), "bytes": path.stat().st_size, "sha256": digest(path)})
    save(ARTIFACTS / "manifest.json", {"created_utc": datetime.now(timezone.utc).isoformat(), "files": items})


def report():
    qa = json.loads((ARTIFACTS / "qa.json").read_text(encoding="utf-8"))
    command_items = [json.loads(line) for line in (LOGS / "commands.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    successful_tests = [item for item in command_items if item["label"] == "pytest-g1" and item["exit_code"] == 0]
    latest_test = successful_tests[-1] if successful_tests else None
    test_log = latest_test["output"] if latest_test else "未通过"
    test_text = (ROOT / test_log).read_text(encoding="utf-8", errors="replace") if latest_test else ""
    import re
    match = re.search(r"(\d+) passed", test_text)
    test_count = int(match.group(1)) if match else 0
    passed = latest_test is not None and test_count > 0
    status = "PASS" if qa["status"] == "PASS" and passed else "PARTIAL"
    frames = qa["visual_beats"]["comparisons"]
    doc = f'''# G1 验收报告

**Status: {status}** · 2026-10-13（Asia/Shanghai）

G1 的 Video IR v0.2 校验、六类固定组件 schema 检查、通信噪声真实音画 Golden Case、绝对时间线转换和 Beat 前后实际像素变化均已执行。{status} 仅表示本地 G1 验收门槛，不代表 G2 或产品化流程已完成。

## 交付物

| 产物 | 路径 |
|---|---|
| 中文有声视频 | `g1/artifacts/G1_通信噪声.mp4` |
| 可重放 Video IR v0.2 输入 | `g1/fixtures/communication-noise.video-ir.json` |
| 真实合成旁白及逐句音频 | `g1/fixtures/assets/communications_noise.wav`、`g1/fixtures/assets/segments/` |
| 短语对齐与字幕 | `g1/artifacts/alignment.json`、`g1/artifacts/captions.srt` |
| 编译后帧时间线与动作目录 | `g1/artifacts/compiled-timeline.json`、`g1/artifacts/action-catalog.json` |
| Beat 前后关键帧 | `g1/artifacts/keyframes/`、`g1/artifacts/contact-sheet.jpg` |
| ffprobe、完整解码及媒体 QA | `g1/artifacts/ffprobe.json`、`g1/artifacts/qa.json`、`g1/artifacts/decoded-audio.wav` |
| 文件哈希清单 | `g1/artifacts/manifest.json` |
| 命令、退出码和原始输出 | `g1/logs/commands.jsonl`、`g1/logs/*.log`、`g1/logs/execution.log` |

## 验收结果

- Video IR v0.2 顶层与六组件 Schema、组件 props、动作 registry、引用、目标、资产路径/hash、源文本 SHA-256、场景连续覆盖和中文字幕安全宽度在渲染前校验。非法输入由带错误码与字段路径的异常拒绝。
- 短语时间由五段分别生成的真实中文 Kokoro WAV 取实际 PCM 活动范围；只声明 phrase 级。30fps 全局绝对边界使用 `floor((ms*30+500)/1000)`，并验证 `150ms → 5 frames` 与 1001/2002/3003ms。
- **六个固定组件**均有 props schema 与静态 renderer 分支。Renderer 只接收通过 Python 校验的 JSON；没有把 IR 文本转换或执行为 JS/TS/shell。
- Golden Case 流程为 `reveal → activate_path → mark_disrupted`。实际渲染视频从连续帧中提取动作前后 PNG；局部区域变化率分别为：显现 `{frames['reveal_sender']['changed_pixel_ratio']:.3%}`、路径 `{frames['activate_main_path']['changed_pixel_ratio']:.3%}`、干扰 `{frames['disrupt_channel']['changed_pixel_ratio']:.3%}`。画面含中文烧录字幕与 H.264/AAC 真实声音。
- 媒体：{qa['video']['width']}×{qa['video']['height']}，{qa['video']['fps']} fps，{qa['video']['frames']} 帧，{qa['video']['duration_seconds']:.3f} 秒；视频与音频完整解码成功。AAC 解码音频和原始 TTS WAV 相关系数 `{qa['audio']['decoded_tts_correlation']:.5f}`，证明音轨来自本次真实合成语音。
- Validator 自动化结果：**{test_count} passed**，最终日志 `{test_log}`；必须负例含未注册组件/动作、额外 props、title_card 非法动作、无效 path/target、scene/segment 引用错误、重复 ID、路径逃逸、符号链接、hash 错误、超宽字幕及图表长度不匹配。

## 真实执行命令

成功的最终验收命令：

```console
g0/.venv/bin/python -u g1/run_g1.py test
g0/.venv/bin/python -u g1/run_g1.py validate
g0/.venv/bin/python -u g1/run_g1.py render
g0/.venv/bin/python -u g1/run_g1.py verify
g0/.venv/bin/python -u g0/verify_design_snapshot.py
```

`commands.jsonl` 按顺序记录真实 Python、pytest、Remotion、ffprobe、FFmpeg 参数、工作目录、退出码和耗时。首次 `all` 调试、两次沙箱内 Chrome `SIGABRT` 以及之后通过自动审核的渲染重试也都保存在日志里。Remotion 使用项目锁定的 Remotion 4.0.535、Node.js 22.21.1 与 Chrome；TTS 使用 G0 本机已有模型文件（权重未复制进 Git），无云端请求。

## 风险与限制

- `alignment.method=provider, granularity=phrase` 表示独立 TTS utterance 的样本边界来源；没有词级 forced alignment 或逐词精度主张。
- 只完整渲染通信 `flow_diagram` Golden Case；其它五个组件验证了严格 props schema 与静态 renderer 分支，但没有各自制作单独的真实音频成片。
- 本机中文系统字体、macOS ARM64 FFmpeg 和 Chrome 固定路径仍需跨系统适配。Remotion 许可、Kokoro 代码与权重、字体许可的商业再分发仍须按项目实际部署形态复核。
- 未执行 G2，也未引入 LLM provider、云 TTS、图片/视频生成模型或任意生成代码。

## 文件变更与提交

变更文件包括 `.gitignore`、本报告、`g1/video_ir.py`（严格 validator/frame compiler）、`g1/run_g1.py`（TTS、受控渲染与 QA）、`g1/renderer/src/Root.tsx` 和 `index.ts`（六个固定组件）、`g1/tests/test_video_ir_v0_2_runtime.py`、`g1/README.md`，以及 `g1/fixtures/`、`g1/artifacts/`、`g1/logs/` 中列出的真实输入与证据。冻结的 00–14 设计包、Video IR v0.2 schema 与原始 G0 文件保持原样。Git commit 记录在交付消息与 `git log -1`。
'''
    (PROJECT / "G1_REPORT.md").write_text(doc, encoding="utf-8")


def all_steps():
    run_step("prepare", prepare)
    test()
    run_step("validate", validate)
    run_step("render", render)
    run_step("verify", verify)
    run_step("report", report)
    write_manifest()


def main():
    LOGS.mkdir(parents=True, exist_ok=True)
    with (LOGS / "execution.log").open("a", encoding="utf-8") as log, redirect_stdout(_Tee(sys.stdout, log)):
        if len(sys.argv) != 2 or sys.argv[1] not in {"prepare", "test", "validate", "render", "verify", "report", "all"}:
            raise SystemExit("usage: g1/run_g1.py {prepare|test|validate|render|verify|report|all}")
        action = sys.argv[1]
        if action == "all": all_steps()
        elif action == "test": test()
        else: run_step(action, globals()[action])
        if action == "report":
            write_manifest()
        print(f"G1 {action}: COMPLETE", flush=True)


class _Tee:
    def __init__(self, *streams): self.streams = streams
    def write(self, value):
        for stream in self.streams: stream.write(value); stream.flush()
        return len(value)
    def flush(self):
        for stream in self.streams: stream.flush()


if __name__ == "__main__":
    main()
