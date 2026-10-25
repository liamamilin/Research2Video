"""Short captions bound to unchanged speech by local forced alignment."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import unicodedata


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


def chunks(text: str) -> list[tuple[int, int, int, int]]:
    """Prefer semantic punctuation; keep words and numerals indivisible."""
    words = word_spans(text)
    result, first, cursor = [], 0, 0
    while first < len(words):
        limit = min(first + 22, len(words))
        minimum = 8
        if 0 < len(words) - limit < 8:
            # Do not strand one or two characters (e.g. split 交易 into 交 / 易).
            # Leave a readable final phrase; allow an earlier punctuation boundary.
            limit = len(words) - 8
            minimum = 4
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
    previous = 0
    for item in items:
        start, end = item["start_ms"], item["end_ms"]
        if not 0 <= start <= end <= duration + 100 or start < previous:
            raise ValueError(f"{segment['id']} 对齐时间无效")
        previous = end
    result = []
    spans = chunks(segment["text"])
    for a, b, first, last in spans:
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
            raise ValueError("短字幕缺少配音段")
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


def prepare(pipeline, ir: dict) -> dict:
    from g2.voices import qwen_paths
    target = pipeline.ARTIFACTS / "caption-alignment.json"
    python = qwen_paths(pipeline.PROJECT)[0]
    model = pipeline.PROJECT / "g3/data/voice-models/qwen3-forced-aligner-0.6b-8bit/model.safetensors"
    if not python.is_file() or not model.is_file():
        raise pipeline.G2Fault("CAPTION_ALIGNER_NOT_CONFIGURED", "短句字幕需要本机字幕对齐运行时和固定模型，请完成模型配置")
    pipeline._run_fixed_command("caption-forced-alignment", [python, pipeline.PROJECT / "g2/align_captions.py",
        "--ir", pipeline.IR_PATH, "--output", target], pipeline.PROJECT, timeout=900)
    plan = json.loads(target.read_text())
    verify_plan(ir, plan)
    (pipeline.ARTIFACTS / "captions.srt").write_text(srt_text(plan["cues"]), encoding="utf-8")
    return plan


def supports_short_captions(ir: dict) -> bool:
    """Both current narrated-video executors share the same caption track."""
    return ir.get("presentation", {}).get("preset") in (None, "information_motion", "explanation", "information_cards")


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
