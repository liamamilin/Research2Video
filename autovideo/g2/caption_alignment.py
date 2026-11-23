"""Short captions bound to unchanged speech by local forced alignment."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import unicodedata


class CaptionTimingOutsideAudio(ValueError):
    """The aligner's word timestamps extend beyond the measured phrase."""


def word_spans(text: str) -> list[tuple[str, int, int]]:
    result, pending, start = [], "", 0
    for i, char in enumerate(text):
        cjk = "\u3400" <= char <= "\u9fff" or "\uf900" <= char <= "\ufaff"
        kept = char == "'" or unicodedata.category(char).startswith(("L", "N"))
        if cjk or not kept:
            if pending:
                result.append((pending, start, i))
                pending = ""
            if cjk:
                result.append((char, i, i + 1))
        else:
            if not pending:
                start = i
            pending += char
    if pending:
        result.append((pending, start, len(text)))
    return result


def _word_weight(word: str) -> float:
    """Visual width in CJK-character equivalents so long Latin tokens do not create wide cues."""
    if len(word) == 1 and ("\u3400" <= word <= "\u9fff" or "\uf900" <= word <= "\ufaff"):
        return 1.0
    return max(1.0, len(word) * 0.55)


def chunks(text: str) -> list[tuple[int, int, int, int]]:
    """Prefer semantic punctuation; keep words and numerals indivisible within readable visual width."""
    words = word_spans(text)
    weights = [_word_weight(w[0]) for w in words]
    max_visual_width = 24.0
    result, first, cursor = [], 0, 0
    while first < len(words):
        max_end = min(first + 22, len(words))
        limit = first
        width = 0.0
        while limit < max_end:
            next_w = weights[limit]
            if limit > first and width + next_w > max_visual_width:
                break
            width += next_w
            limit += 1
        minimum = min(8, max(2, (limit - first) // 2)) if limit - first < 8 else 8
        remaining_words = len(words) - limit
        remaining_width = sum(weights[limit:]) if remaining_words else 0.0
        if 0 < remaining_words < 8 and remaining_width < 8.0:
            # Do not strand one or two characters (e.g. split 交易 into 交 / 易).
            # Leave a readable final phrase; allow an earlier punctuation boundary.
            limit = max(first + 1, len(words) - 8)
            minimum = min(4, max(1, limit - first))
        if limit == len(words):
            last = limit
        else:
            choices = [i for i in range(first + minimum, limit + 1)
                       if re.search(r"[，。！？；：、,;:!?]", text[words[i - 1][2]:words[i][1]])]
            last = choices[-1] if choices else limit
        if last < len(words) and words[last - 1][0][-1:].isdigit():
            unit = re.match(r"(?:小时|分钟|毫安时|秒|年|月|日|天|块|个|倍|元|万|亿|%|％)", text[words[last][1]:])
            if unit:
                unit_end = words[last][1] + len(unit.group())
                while last < len(words) and words[last][1] < unit_end:
                    last += 1
        end = words[last][1] if last < len(words) else len(text)
        result.append((cursor, end, first, last))
        cursor, first = end, last
    return result


def build_cues(segment: dict, items: list[dict]) -> list[dict]:
    words = word_spans(segment["text"])
    if [w[0] for w in words] != [i["text"] for i in items]:
        raise ValueError(f"{segment['id']} 对齐文本与原文不一致")
    duration = segment["end_ms"] - segment["start_ms"]
    if not words:
        # Older sentence splitting could leave a closing quote in its own
        # silent segment. Preserve that character and its original interval.
        if duration <= 0:
            raise ValueError(f"{segment['id']} 对齐时间无效")
        return [{"segment_id": segment["id"], "text": segment["text"],
                 "char_start": 0, "char_end": len(segment["text"]),
                 "start_ms": segment["start_ms"], "end_ms": segment["end_ms"]}]
    # The model reports times on a discrete grid. A zero-width suffix may
    # land just beyond the measured phrase even when its preceding speech fits.
    trailing = len(items)
    while trailing:
        item = items[trailing - 1]
        if not duration <= item["start_ms"] == item["end_ms"] <= duration + 100:
            break
        trailing -= 1
    previous = 0
    outside_audio = False
    for index, item in enumerate(items):
        start, end = item["start_ms"], item["end_ms"]
        if index == len(items) - 1 and start < duration:
            # The aligner can report an end beyond the available audio.
            # Clipping only the last end preserves every word onset.
            end = min(end, duration)
        if not 0 <= start <= end or start < previous:
            raise ValueError(f"{segment['id']} 对齐时间无效")
        outside_audio |= end > duration + 100 or (start >= duration and index < trailing)
        previous = end
    if outside_audio:
        raise CaptionTimingOutsideAudio(f"{segment['id']} 对齐时间无效：逐词时间超出语音段")
    result = []
    spans = chunks(segment["text"])
    for a, b, first, last in spans:
        if first >= trailing and result:
            result[-1].update(text=segment["text"][result[-1]["char_start"]:b],
                              char_end=b, end_ms=segment["end_ms"])
            continue
        start = max(0, items[first]["start_ms"] - 50)
        end = min(duration, items[last - 1]["end_ms"] + 80)
        if first:
            start = max(start, (items[first - 1]["end_ms"] + items[first]["start_ms"]) // 2)
        if last < len(items):
            end = min(end, (items[last - 1]["end_ms"] + items[last]["start_ms"]) // 2)
        if end - start < 200:
            raise ValueError(f"{segment['id']} 短字幕对齐区间过短，需检查语音")
        result.append({"segment_id": segment["id"], "text": segment["text"][a:b], "char_start": a, "char_end": b,
                       "start_ms": segment["start_ms"] + start, "end_ms": segment["start_ms"] + end})
    return result


def proportional_cues(segment: dict) -> list[dict]:
    """Distribute a segment's measured [start_ms, end_ms] across readable chunks by character weight."""
    text = segment["text"]
    start_ms, end_ms = int(segment["start_ms"]), int(segment["end_ms"])
    duration = end_ms - start_ms
    if duration <= 0:
        raise ValueError(f"{segment['id']} 对齐时间无效")
    spans = chunks(text) if word_spans(text) else [(0, len(text), 0, 0)]
    max_chunks = max(1, duration // 200)
    if len(spans) > max_chunks:
        merged: list[tuple[int, int, int, int]] = []
        group_size = (len(spans) + max_chunks - 1) // max_chunks
        for i in range(0, len(spans), group_size):
            batch = spans[i:i + group_size]
            merged.append((batch[0][0], batch[-1][1], batch[0][2], batch[-1][3]))
        spans = merged
    weights = [max(1, len(re.sub(r"\s+", "", text[a:b]))) for a, b, _, _ in spans]
    total_weight = sum(weights)
    cues: list[dict] = []
    cursor_ms = start_ms
    acc = 0
    for idx, ((a, b, _, _), weight) in enumerate(zip(spans, weights)):
        acc += weight
        if idx == len(spans) - 1:
            cue_end = end_ms
        else:
            remaining = len(spans) - 1 - idx
            target = start_ms + round(duration * acc / total_weight)
            cue_end = max(cursor_ms + 1, min(target, end_ms - remaining))
        cues.append({"segment_id": segment["id"], "text": text[a:b], "char_start": a, "char_end": b,
                     "start_ms": cursor_ms, "end_ms": cue_end})
        cursor_ms = cue_end
    return cues


def align_segment(segment: dict, items: list[dict], *, allow_proportional_fallback: bool = False) -> tuple[list[dict], dict]:
    """Keep a single short caption on its measured phrase when word times overshoot.

    When ``allow_proportional_fallback`` is True (used by the offline aligner runner),
    a per-segment aligner failure degrades cleanly to proportional chunk timing within
    the segment's measured speech interval instead of aborting the whole video job.
    """
    try:
        return build_cues(segment, items), {"segment_id": segment["id"], "items": items}
    except CaptionTimingOutsideAudio as exc:
        text = segment["text"]
        if segment["end_ms"] - segment["start_ms"] >= 200 and len(chunks(text)) == 1:
            cue = {"segment_id": segment["id"], "text": text, "char_start": 0, "char_end": len(text),
                   "start_ms": segment["start_ms"], "end_ms": segment["end_ms"]}
            evidence = {"segment_id": segment["id"], "items": [], "method": "measured_phrase",
                        "rejected_items": items}
            return [cue], evidence
        if not allow_proportional_fallback:
            raise
        return proportional_cues(segment), {"segment_id": segment["id"], "items": [],
                                            "method": "proportional_fallback",
                                            "fallback_reason": str(exc), "rejected_items": items}
    except ValueError as exc:
        if not allow_proportional_fallback:
            raise
        return proportional_cues(segment), {"segment_id": segment["id"], "items": [],
                                            "method": "proportional_fallback",
                                            "fallback_reason": str(exc), "rejected_items": items}


def narration_hash(ir: dict) -> str:
    return hashlib.sha256(json.dumps(ir["narration"]["segments"], sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def verify_plan(ir: dict, plan: dict) -> None:
    if plan.get("version") != 2 or plan.get("narration_sha256") != narration_hash(ir):
        raise ValueError("短字幕与当前配音时间线不一致")
    audio_id = ir["narration"].get("audio_asset_id")
    # Compiled timelines omit assets; raw Video IR binds the track to its WAV.
    if audio_id and "assets" in ir:
        audio = next((a for a in ir.get("assets", []) if a["id"] == audio_id), None)
        if not audio or plan.get("audio_sha256") != audio["sha256"]:
            raise ValueError("短字幕与当前配音音轨不一致")
    rows = ir["narration"]["segments"]
    expected = []
    previous = -1
    for row in rows:
        local = [c for c in plan["cues"] if c["segment_id"] == row["id"]]
        if not local:
            raise ValueError(f"短字幕缺少配音段：{row['id']}")
        cursor = 0
        for cue in local:
            if cue["char_start"] != cursor or cue["text"] != row["text"][cursor:cue["char_end"]]:
                raise ValueError("短字幕丢字或改变原文")
            if not row["start_ms"] <= cue["start_ms"] < cue["end_ms"] <= row["end_ms"] or cue["start_ms"] < previous:
                raise ValueError("短字幕越界或重叠")
            cursor, previous = cue["char_end"], cue["end_ms"]
            expected.append(cue)
        if cursor != len(row["text"]):
            raise ValueError("短字幕未完整覆盖原文")
    if expected != plan["cues"]:
        raise ValueError("短字幕顺序或归属无效")


def srt_text(cues: list[dict]) -> str:
    def stamp(ms):
        return f"{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}"
    return "\n\n".join(f"{i+1}\n{stamp(c['start_ms'])} --> {stamp(c['end_ms'])}\n{c['text']}" for i, c in enumerate(cues)) + "\n"


def fallback_plan(ir: dict, reason: str) -> dict:
    """Build a verified proportional-timing caption plan when forced alignment fails or times out."""
    audio_id = ir["narration"].get("audio_asset_id")
    audio = next((a for a in ir.get("assets", []) if a["id"] == audio_id), None) if audio_id else None
    cues: list[dict] = []
    evidence: list[dict] = []
    for segment in ir["narration"]["segments"]:
        seg_cues = proportional_cues(segment)
        cues.extend(seg_cues)
        evidence.append({"segment_id": segment["id"], "items": [], "method": "proportional_fallback",
                         "fallback_reason": reason[:300]})
    plan = {
        "version": 2,
        "method": "proportional_fallback",
        "fallback_segments": len(ir["narration"]["segments"]),
        "fallback_reason": reason[:300],
        "narration_sha256": narration_hash(ir),
        "audio_sha256": audio["sha256"] if audio else None,
        "cues": cues,
        "word_evidence": evidence,
    }
    verify_plan(ir, plan)
    return plan


def prealign(pipeline, alignment: dict, speed: float) -> dict | None:
    """Run forced alignment right after TTS finishes so it overlaps with STORYBOARD tail work."""
    from g2.media_settings import prepare_retimed_audio
    from g2.voices import qwen_paths

    python = qwen_paths(pipeline.PROJECT)[0]
    model = pipeline.PROJECT / "g3/data/voice-models/qwen3-forced-aligner-0.6b-8bit/model.safetensors"
    if not python.is_file() or not model.is_file():
        return None
    target = pipeline.ARTIFACTS / "caption-alignment.json"
    early_ir_path = pipeline.FIXTURE_ROOT / "early-caption-ir.json"
    try:
        retimed = prepare_retimed_audio(pipeline, alignment, speed)
        rel_audio = retimed["audio_path"].relative_to(pipeline.FIXTURE_ROOT).as_posix()
        early_ir = {
            "assets": [{"id": "communication_voice", "type": "audio", "path": rel_audio, "sha256": retimed["audio_sha256"]}],
            "narration": {"audio_asset_id": "communication_voice", "segments": retimed["segments"]},
        }
        pipeline.write_json(early_ir_path, early_ir)
        timeout = max(900, min(1800, len(retimed["segments"]) * 8))
        pipeline._run_fixed_command("caption-forced-alignment", [python, pipeline.PROJECT / "g2/align_captions.py",
            "--ir", early_ir_path, "--output", target], pipeline.PROJECT, timeout=timeout)
        plan = json.loads(target.read_text())
        verify_plan(early_ir, plan)
        return plan
    except Exception as exc:  # noqa: BLE001 - BUILD_IR will cleanly run prepare() if early alignment failed
        print(f"CAPTION PREALIGN SKIPPED: {type(exc).__name__}: {str(exc)[:180]}", flush=True)
        return None
    finally:
        if early_ir_path.is_file():
            early_ir_path.unlink()


def prepare(pipeline, ir: dict) -> dict:
    from g2.voices import qwen_paths
    target = pipeline.ARTIFACTS / "caption-alignment.json"
    if target.is_file():
        try:
            existing = json.loads(target.read_text())
            verify_plan(ir, existing)
            (pipeline.ARTIFACTS / "captions.srt").write_text(srt_text(existing["cues"]), encoding="utf-8")
            print("CAPTION ALIGNMENT: reused pre-aligned forced-alignment track", flush=True)
            return existing
        except (OSError, ValueError, KeyError, TypeError):
            pass
    python = qwen_paths(pipeline.PROJECT)[0]
    model = pipeline.PROJECT / "g3/data/voice-models/qwen3-forced-aligner-0.6b-8bit/model.safetensors"
    if not python.is_file() or not model.is_file():
        raise pipeline.G2Fault("CAPTION_ALIGNER_NOT_CONFIGURED", "短句字幕需要本机字幕对齐运行时和固定模型，请完成模型配置")
    timeout = max(900, min(1800, len(ir["narration"]["segments"]) * 8))
    try:
        pipeline._run_fixed_command("caption-forced-alignment", [python, pipeline.PROJECT / "g2/align_captions.py",
            "--ir", pipeline.IR_PATH, "--output", target], pipeline.PROJECT, timeout=timeout)
        plan = json.loads(target.read_text())
        verify_plan(ir, plan)
    except (pipeline.G2Fault, OSError, ValueError, KeyError, TypeError) as exc:
        if isinstance(exc, pipeline.G2Fault) and exc.code not in {"COMMAND_TIMEOUT", "COMMAND_FAILED"}:
            raise
        reason = f"{getattr(exc, 'code', type(exc).__name__)}: {exc}"
        plan = fallback_plan(ir, reason)
        pipeline.write_json(target, plan)
        print(f"CAPTION FALLBACK: forced alignment degraded to proportional timing ({reason[:200]})", flush=True)
    (pipeline.ARTIFACTS / "captions.srt").write_text(srt_text(plan["cues"]), encoding="utf-8")
    return plan


def supports_short_captions(ir: dict) -> bool:
    """Narrated-video executors share the same forced-aligned short caption track."""
    return ir.get("presentation", {}).get("preset") in (
        None, "information_motion", "explanation", "information_cards", "illustrated_explainer",
    )


def render_cues(pipeline, ir: dict) -> list[dict] | None:
    """Give trusted renderers only the checked caption artifact, keeping Video IR intact."""
    path = pipeline.ARTIFACTS / "caption-alignment.json"
    if not path.exists():
        return None  # Historical outputs without a short track retain their phrase captions.
    plan = json.loads(path.read_text())
    verify_plan(ir, plan)
    if (pipeline.ARTIFACTS / "captions.srt").read_text() != srt_text(plan["cues"]):
        raise ValueError("字幕文件与逐字覆盖计划不一致")
    return plan["cues"]
