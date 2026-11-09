"""Illustrated explainer: per-chapter scene descriptions, overlays and Draw Things pictures.

Design: AREC spec/illustrated-explainer.md. One model call writes, per chapter of the
authored script, a literal scene (who does what, one prop), up to two keywords and at
most one overlay (a trusted diagram part). The program substitutes the fixed cast,
validates every overlay against its schema and the chapter text, and draws one
picture per chapter with a style pack. A rejected overlay falls back to keywords and
is recorded; it never blocks the video. Draw Things being unreachable fails the job.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]
PLAN_SCHEMA = ROOT / "g2/schemas/illustration-plan.schema.json"
COMPONENT_SCHEMA = ROOT / "schemas/components/illustration_scene.schema.json"
STYLES = ROOT / "g2/illustration_styles"
DEFAULT_STYLE = "flat"

SYSTEM = """你是插画分镜师。视频每一章配一张插画，观众会看着它听几十秒到一两分钟。讲稿已经定稿，直接为每章设计画面。

一、人物表 cast：全片反复出现的人，每人一个代号（A、B、C，最多 3 人），用英文写死外貌和衣着：发型、上衣颜色款式、下装颜色。
画面描述里提到这些人时只写占位符 {A}、{B}，不再写外貌衣着，程序会统一替换。

二、每章 scene（英文，35 词以内）：只画字面的具体场景。一到两个人在一个具体地方做一件看得见的事，手边一个关键物件。
- 本章讲的是看不见的机制或比喻时，也不画比喻本身，画本章里最贴近的一个日常动作。比喻和机制交给 overlay。
- 一张图只画一个动作，不要左右对比、多格、剖面、夸张尺寸堆叠。
- 两人同框时写清各自在画面里的位置（left/right）。
- 账单、标牌、纸张、屏幕、贴纸一律写成空白纯色块（blank, plain colored, no writing），尽量不画文件和招牌。
- 不要任何文字、数字、标签、图表；不写画风、配色、构图词。

三、每章 keywords：最多 2 个中文关键词，每个不超过 12 字，取自本章讲稿。

四、每章 overlay：画在画面左侧留白里的一个示意，帮助观众看懂本章最关键的关系。只能从下面五种里选一种，没有合适的就写 null。优先用能表达关系的对比、算式、流向、构成；清单只用在本章确实是并列的几条做法或几个问题时，全片清单最多 2 次。所有文字必须来自本章讲稿，数字必须是本章讲稿里出现过的数字，不要编造。
1. 对比 {"kind":"compare","glyph":"pipe|pool|arrows|dots|bar|reading","relation":"big_small|small_big|same","items":[{"label":"≤6字","note":"≤12字，可省略"},{"label":"...","note":"..."}]}
   两项用同一种图形比大小或多少。glyph：pipe 粗细管子、pool 大小水池、arrows 箭头多少、dots 圆点疏密、bar 柱子高低、reading 两个读数框。relation：big_small 表示第一项大，small_big 表示第二项大，same 表示一样。
2. 构成 {"kind":"composition","items":["≤6字",...2到5项],"total":"≤12字，合起来是什么"}
3. 算式 {"kind":"formula","terms":["≤6字",...2到3项],"operators":["+|-|×|÷",...比 terms 少一个],"result":"≤14字","bars":[{"label":"≤8字","value":数字},{"label":"...","value":数字}]}
   bars 可省略。bars 画的是 result 本身的大小（例如"每杯摊多少"），不是算式里某个词块的大小：只有讲稿直接给出两种情况下 result 的数字时才写 bars，两个 value 都必须是本章讲稿里的数字；拿不准就不写 bars。
4. 清单 {"kind":"steps","style":"number|question","items":["≤10字",...2到4项]}
5. 流向 {"kind":"flow","source":"≤6字","target":"≤6字","label":"≤10字"}

只输出 JSON：{"cast":{"A":"..."},"chapters":[{"chapter":1,"scene":"... {A} ...","keywords":["..."],"overlay":{...} 或 null,"note":"中文一句说明画的是什么"}]}"""

_component = None


def _overlay_validator() -> Draft202012Validator:
    global _component
    if _component is None:
        schema = json.loads(COMPONENT_SCHEMA.read_text(encoding="utf-8"))
        _component = Draft202012Validator(schema["properties"]["overlay"])
    return _component


def style_keys() -> tuple[str, ...]:
    """Style packs a job may choose (request field ``illustration_style``): one JSON file each."""
    return tuple(sorted(path.stem for path in STYLES.glob("*.json")))


def load_style(key: str = DEFAULT_STYLE) -> dict:
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,31}", key or ""):
        raise ValueError("画风编号无效")
    path = STYLES / f"{key}.json"
    if not path.is_file():
        raise ValueError(f"没有这个画风：{key}")
    return json.loads(path.read_text(encoding="utf-8"))


_DIGITS = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_UNITS = {"十": 10, "百": 100, "千": 1000, "万": 10000}


def _chinese_value(run: str) -> int | None:
    """三百 → 300, 八十 → 80, 三十三 → 33, 十二 → 12; None for runs that are not a plain number."""
    total, section, digit = 0, 0, None
    for char in run:
        if char in _DIGITS:
            if digit is not None:
                return None
            digit = _DIGITS[char]
        elif char == "万":
            total += (section + (digit or 0)) * 10000
            section, digit = 0, None
        else:
            section += (1 if digit is None else digit) * _UNITS[char]
            digit = None
    return total + section + (digit or 0)


def _numbers(text: str) -> set[str]:
    found = {n.rstrip("0").rstrip(".") if "." in n else n for n in re.findall(r"\d+(?:\.\d+)?", text)}
    for run in re.findall(r"[零一二两三四五六七八九十百千万]+", text):
        value = _chinese_value(run)
        if value is not None:
            found.add(str(value))
    return found


def _texts(overlay: dict) -> list[str]:
    kind = overlay["kind"]
    if kind == "compare":
        return [v for item in overlay["items"] for v in (item["label"], item.get("note", ""))]
    if kind == "composition":
        return [*overlay["items"], overlay["total"]]
    if kind == "formula":
        return [*overlay["terms"], overlay["result"], *(b["label"] for b in overlay.get("bars", []))]
    if kind == "steps":
        return list(overlay["items"])
    return [overlay["source"], overlay["target"], overlay["label"]]


def overlay_problem(overlay, chapter_text: str) -> str | None:
    """Return why an overlay cannot be shown, or None when it is valid."""
    if overlay is None:
        return None
    errors = sorted(_overlay_validator().iter_errors(overlay), key=lambda e: list(e.path))
    if errors:
        return "格式不符：" + errors[0].message[:160]
    if overlay["kind"] == "formula" and len(overlay["operators"]) != len(overlay["terms"]) - 1:
        return "算式的运算符数量须比词块少一个"
    known = _numbers(chapter_text)
    used: set[str] = set()
    for text in _texts(overlay):
        used |= _numbers(text)
    for bar in overlay.get("bars", []) if overlay["kind"] == "formula" else []:
        used |= _numbers(f"{bar['value']}")
    unknown = sorted(n for n in used if n not in known)
    if unknown:
        return "示意里的数字不在本章讲稿中：" + "、".join(unknown)
    return None


def messages(context: dict, feedback: str | None = None) -> list[dict]:
    body = "\n\n".join(f"第{i}章 {c['title']}\n{c['text']}" for i, c in enumerate(context["chapters"], 1))
    if feedback:
        body += "\n\n" + feedback
    return [{"role": "system", "content": SYSTEM}, {"role": "user", "content": body}]


def check_plan(plan: dict, context: dict, fault) -> None:
    """Hard checks only: chapter coverage and the cast. Overlays are checked softly in finalize()."""
    count = len(context["chapters"])
    if [c["chapter"] for c in plan["chapters"]] != list(range(1, count + 1)):
        raise fault("ILLUSTRATION_PLAN_CHAPTERS", f"须按顺序为全部 {count} 章各写一条画面")
    cast = plan.get("cast") or {}
    for chapter in plan["chapters"]:
        names = set(re.findall(r"\{(\w+)\}", chapter["scene"]))
        if names - set(cast):
            raise fault("ILLUSTRATION_PLAN_CAST", f"第{chapter['chapter']}章用了人物表里没有的人：{'、'.join(sorted(names - set(cast)))}")
        if re.search(r"[一-鿿]", chapter["scene"]):
            raise fault("ILLUSTRATION_PLAN_SCENE", f"第{chapter['chapter']}章画面描述须用英文")


MAX_STEPS = 2


def soft_problems(plan: dict, context: dict) -> list[str]:
    """Advisory: earns one more attempt, never fails the job (generate_validated_plan keeps the first plan)."""
    steps = [c["chapter"] for c in plan["chapters"] if isinstance(c.get("overlay"), dict) and c["overlay"].get("kind") == "steps"]
    if len(steps) > MAX_STEPS:
        return [f"清单用了 {len(steps)} 次（第{'、'.join(map(str, steps))}章），全片最多 {MAX_STEPS} 次；"
                "请把其中讲关系的章节换成对比、算式、流向或构成，没有合适的就写 null"]
    return []


def finalize(plan: dict, context: dict) -> tuple[list[dict], list[dict]]:
    """Substitute the cast and keep only valid overlays; returns (chapters, dropped)."""
    cast = plan.get("cast") or {}
    chapters, dropped = [], []
    for row, source in zip(plan["chapters"], context["chapters"]):
        scene = re.sub(r"\{(\w+)\}", lambda m: cast[m.group(1)].strip(), row["scene"]).strip()
        keywords = [w.strip() for w in row.get("keywords", []) if w.strip()][:2]
        overlay = row.get("overlay")
        problem = overlay_problem(overlay, source["title"] + "\n" + source["text"])
        if problem:
            dropped.append({"chapter": row["chapter"], "overlay": overlay, "reason": problem})
            overlay = None
        chapters.append({"scene": scene, "keywords": keywords, "overlay": overlay, "note": row.get("note", "")})
    return chapters, dropped


def prompt(style: dict, scene: str) -> str:
    return f"{style['prefix']} {style['characters']} {scene}, {style['suffix']}"


def seed_for(narration: dict) -> int:
    """One seed per video, stable for the same script so a retry redraws the same pictures."""
    text = "\n".join(s["text"] for s in narration["segments"])
    return int(hashlib.sha256(text.encode()).hexdigest()[:8], 16) % (2 ** 31 - 1) + 1


def generate(pipeline, narration: dict, style_key: str = DEFAULT_STYLE) -> tuple[list[dict], list[dict]]:
    """Plan scenes and overlays, then draw one picture per chapter. Returns (assets, chapter extras)."""
    from g2.creative_forms import _save_image
    from g2.draw_things import DrawThingsFault, _read_json, _validate_png, base_url, probe_draw_things

    probe = probe_draw_things()
    if not probe["ready"]:
        raise pipeline.G2Fault("DRAW_THINGS_UNAVAILABLE", "插画讲解需要本机 Draw Things：" + (probe["error"] or "无法连接")
                               + "。请打开 Draw Things 并在设置里开启 HTTP API 服务器后重试。")
    style = load_style(style_key)
    segments = {s["id"]: s["text"] for s in narration["segments"]}
    context = {"chapters": [{"title": c["title"], "text": "".join(segments[sid] for sid in c["narration_segment_ids"])}
                            for c in narration["chapters"]]}
    plan = pipeline.generate_validated_plan("illustration-storyboard", messages, PLAN_SCHEMA,
                                            lambda value, ctx: check_plan(value, ctx, pipeline.G2Fault), context,
                                            soft_validator=soft_problems)
    chapters, dropped = finalize(plan, context)
    folder = pipeline.ASSETS / "slideshow"
    folder.mkdir(parents=True, exist_ok=False)
    seed = seed_for(narration)
    assets, records = [], []
    for index, chapter in enumerate(chapters):
        text = prompt(style, chapter["scene"])
        body = {**style["params"], "seed": seed, "batch_count": 1, "batch_size": 1,
                "prompt": text, "negative_prompt": style["negative"]}
        try:
            response = _read_json(base_url() + "/sdapi/v1/txt2img", method="POST",
                                  body=json.dumps(body, ensure_ascii=False).encode(), timeout=900)
            raw = base64.b64decode(response["images"][0], validate=True)
            _validate_png(raw)
        except DrawThingsFault as exc:
            raise pipeline.G2Fault(exc.code, f"第{index + 1}章插画生成失败：{exc}") from exc
        except (KeyError, IndexError, ValueError, TypeError) as exc:
            raise pipeline.G2Fault("ILLUSTRATION_IMAGE_INVALID", f"第{index + 1}章未收到有效插画") from exc
        assets.append(_save_image(folder, index, raw, "Draw Things local illustration; not documentary evidence"))
        if index == 0:
            # the clean chapter-1 picture (no overlay, no caption): the upload cover uses it as its frame
            (pipeline.ARTIFACTS / "cover-frame.png").write_bytes(raw)
        records.append({"chapter": index + 1, "prompt": text, "seed": seed, "note": chapter["note"]})
    pipeline.write_json(pipeline.ARTIFACTS / "illustrations.json", {
        "style": {"key": style["key"], "name": style["name"], "version": style["version"]},
        "cast": plan.get("cast") or {}, "seed": seed, "images": records,
        "chapters": [{"keywords": c["keywords"], "overlay": c["overlay"]} for c in chapters],
        "overlays_dropped": dropped})
    pipeline.write_json(pipeline.ARTIFACTS / "slideshow-images.json", {
        "mode": "local_generated", "assignment": "one illustration per chapter", "images": assets})
    return assets, [{"keywords": c["keywords"], "overlay": c["overlay"]} for c in chapters]
