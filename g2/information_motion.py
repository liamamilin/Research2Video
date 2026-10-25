"""Simple information motion, compiled to existing allowlisted components."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator
from PIL import ImageFont
import re

from g2.technical_text import altered_technical_text, display_width, wrap_units

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "g2/schemas/information-motion-plan.schema.json"
COMPONENTS = ("title_card", "concept_reveal", "comparison", "big_number", "bar_chart", "price_change")
# source_card is not in the planner's catalog: the model never writes it. place_source_card() adds it from the request's sources.
HIRAGINO = "/System/Library/Fonts/Hiragino Sans GB.ttc"

#: A narration this short (about 60-120 s) is a quick news item watched on a phone: every text size is scaled up.
#: The renderer reads the same decision from presentation.density, so text fit and drawing agree.
LARGE_TEXT_MAX_CHARS = 560
LARGE_TEXT_SCALE = 1.3


def short_form(narration: dict) -> bool:
    return sum(len(segment["text"]) for segment in narration["segments"]) <= LARGE_TEXT_MAX_CHARS


def scaled(size: int, large: bool) -> int:
    """Same rounding as the renderer's Math.round(size * 1.3)."""
    return int(size * LARGE_TEXT_SCALE + 0.5) if large else size


@lru_cache(maxsize=None)
def _font(size: int):
    return ImageFont.truetype(HIRAGINO, size)


class InformationFault(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


def narration_brief(narration_plan: dict) -> dict:
    """Send only the narration text the cues must quote, never the source excerpts.

    Each segment also carries source_quotes borrowed from the original material, and
    rewritten narration usually differs from them by a few characters. Sending both
    invites the model to copy the source excerpt into cue.quote, which the local
    validator must reject because cues are timed inside the narration phrase.
    """
    return {**narration_plan,
            "segments": [{key: value for key, value in segment.items() if key != "source_quotes"}
                         for segment in narration_plan["segments"]]}


def targets(component: str, props: dict) -> list[str]:
    if component == "title_card":
        return ["title", *(["subtitle"] if props.get("subtitle") else [])]
    if component == "concept_reveal":
        return ["title", *[f"line:{i}" for i in range(len(props["lines"]))]]
    if component == "big_number":
        return ["title", "line:0", "line:1", *(["line:2"] if props.get("note") else [])]  # value, label, note
    if component == "bar_chart":
        return [f"bar:{i}" for i in range(len(props["categories"]))]
    if component == "price_change":
        return ["title", *[f"line:{i}" for i in range(len(props["rows"]))]]
    return ["side:left", "side:right"]


def text_lines(text: str, size: int, width: int) -> int:
    """Count wrapped lines as the renderer lays them out: technical tokens move to the next line whole."""
    font = _font(size)
    count, line = 1, ""
    for unit in wrap_units(text):
        for piece in [unit] if display_width(font, unit) <= width else list(unit):
            if piece == "\n" or (line and display_width(font, line + piece) > width):
                count += 1
                line = "" if piece == "\n" else piece
            else:
                line += piece
    return count


def bar_value_text(props: dict, index: int) -> str:
    """A bar's value as the renderer prints it: the number, then the unit."""
    return f"{props['values'][index]:,.3f}".rstrip("0").rstrip(".") + f" {props['unit']}"


def visible_text(scene: dict) -> list[str]:
    props = scene["props"]
    if scene["component"] == "big_number":
        return [scene["headline"], props["title"], props["value"], props["label"], props.get("note") or ""]
    if scene["component"] == "price_change":
        return [scene["headline"], props["title"], props.get("unit") or "",
                *[text for row in props["rows"] for text in (row["label"], row["before"], row["after"], row.get("change") or "")]]
    if scene["component"] == "bar_chart":
        return [scene["headline"], *props["categories"], *[bar_value_text(props, i) for i in range(len(props["categories"]))]]
    if scene["component"] == "comparison":
        return [scene["headline"], *(text for side in ("left", "right") for text in [props[side]["label"], *props[side]["items"]])]
    return [scene["headline"], props["title"], props.get("subtitle") or "", *props.get("lines", [])]


#: Geometry shared with g1/renderer/src/Root.tsx (price_change): label column, gap, number sizes.
PRICE_LABEL_W, PRICE_GAP, PRICE_INNER_W = 400, 36, 1528
PRICE_SIDE = .42  # prefix and unit ("美元", "$") are drawn at this fraction of the number's size


def price_size(rows: int) -> int:
    return 180 if rows == 1 else 128


def split_value(text: str) -> tuple[str, str, str]:
    """('$', '5', ' 美元'): the digits are drawn large, what surrounds them small."""
    match = re.match(r"^(\D*?)(\d[\d,]*(?:\.\d+)?)(.*)$", text)
    return (match[1], match[2], match[3]) if match else ("", text, "")


def value_width(text: str, size: int) -> float:
    prefix, number, suffix = split_value(text)
    side = int(size * PRICE_SIDE)
    return sum(display_width(_font(use), part) for part, use in ((prefix, side), (number, size), (suffix, side)) if part)


def change_text(row: dict) -> str:
    """The badge as drawn: the sign comes from direction, so the model never writes a minus sign."""
    return ("−" if row["direction"] == "down" else "+") + row["change"]


def price_badge_size(large: bool) -> int:
    return scaled(52, large)


def _fit_fault(scene: dict, message: str) -> InformationFault:
    return InformationFault("INFORMATION_TEXT_FIT", f"{scene['id']}: {message}")


def validate_text_fit(scene: dict, large: bool = False) -> None:
    """Check the actual fixed panel capacity; overflow should become another scene.

    large is the short-form scale: every text size below goes through S(), as in the renderer.
    """
    S = lambda size: scaled(size, large)
    component, props = scene["component"], scene["props"]
    if component == "title_card" and large:
        title_size = S(66) if len(props["title"]) > 20 else S(83)
        sub_size = S(36) if len(props.get("subtitle", "")) > 65 else S(42)
        height = text_lines(props["title"], title_size, 1450) * title_size * 1.35 + 43
        if props.get("subtitle"):
            height += 40 + text_lines(props["subtitle"], sub_size, 1380) * sub_size * 1.65
        if height > 600:
            raise _fit_fault(scene, "title_card text exceeds its area at the large size; shorten the title or subtitle")
    elif component == "concept_reveal":
        count = len(props["lines"])
        compact = count == 4
        width = (1640 - 28 * ((2 if compact else count) - 1)) / (2 if compact else count)
        for line in props["lines"]:
            match = re.match(r"^([^：:]{1,16})[：:]\s*([\s\S]+)$", line)
            label, body = (match[1], match[2]) if match else (line, "")
            label_size = S(36 if compact or len(label) > 10 else 42)
            body_size = S(28 if compact else 30 if count == 3 else 34)
            text_width = int(width - (68 if compact else 72))
            height = (50 if compact else 68) + 31 + (10 if compact else 29)
            height += text_lines(label, label_size, text_width) * label_size * 1.4
            if body:
                height += (10 if compact else 22) + text_lines(body, body_size, text_width) * body_size * (1.5 if compact else 1.6)
            if height > (250 if compact else 440):
                raise InformationFault("INFORMATION_TEXT_FIT", f"{scene['id']}: card text exceeds its panel; shorten on-screen wording or split into another scene")
    elif component == "big_number":
        size = 220 if len(props["value"]) <= 6 else 180 if len(props["value"]) <= 9 else 140
        if text_lines(props["value"], size, 1496) > 1:
            raise InformationFault("INFORMATION_TEXT_FIT", f"{scene['id']}: big_number value must fit on one line; write only the number and its unit")
        label_lines = text_lines(props["label"], S(46), 1496)
        note_lines = text_lines(props["note"], S(32), 1496) if props.get("note") else 0
        height = size * 1.1 + 24 + label_lines * S(46) * 1.35 + (20 + note_lines * S(32) * 1.5 if note_lines else 0)
        if label_lines > 2 or note_lines > 2 or height > 440:
            raise InformationFault("INFORMATION_TEXT_FIT", f"{scene['id']}: big_number label or note is too long for its panel; shorten it")
    elif component == "price_change":
        rows = props["rows"]
        size, label_size, badge = price_size(len(rows)), S(46), price_badge_size(large)
        unit_size = S(36)
        height = (unit_size * 1.4 + 20 if props.get("unit") else 0) + 34 * (len(rows) - 1)
        for row in rows:
            lines = text_lines(row["label"], label_size, PRICE_LABEL_W)
            if lines > 2:
                raise _fit_fault(scene, f"price_change label {row['label']!r} wraps onto more than two lines; shorten it")
            room = PRICE_INNER_W - PRICE_LABEL_W - PRICE_GAP
            if row.get("change"):
                room -= display_width(_font(badge), change_text(row)) + 52 + PRICE_GAP
            used = value_width(row["before"], size) + value_width(row["after"], size) + size * .62 + size * .36
            if used > room:
                raise _fit_fault(scene, f"price_change numbers {row['before']!r} -> {row['after']!r} are too wide for the row; write only the number and its unit")
            height += max(size * 1.1, lines * label_size * 1.35)
        if height > 440:
            raise _fit_fault(scene, "price_change rows do not fit their panel; use fewer rows or shorter labels")
    elif component == "source_card":
        height = 26 * (len(props["sources"]) - 1)
        for source in props["sources"]:
            date_w = display_width(_font(S(30)), source["date"])
            lines = text_lines(source["title"], S(34), int(PRICE_INNER_W - date_w - 40))
            if lines > 2 or display_width(_font(S(28)), source["domain"]) > PRICE_INNER_W:
                raise InformationFault("INFORMATION_SOURCE_CARD", f"来源标题或域名过长，无法在来源卡上排下：{source['title']!r}；请缩短 sources 里的标题")
            height += lines * S(34) * 1.3 + S(28) * 1.5
        if height > 440:
            raise InformationFault("INFORMATION_SOURCE_CARD", "来源太多或标题太长，来源卡排不下；请减少 sources 或缩短标题")
    elif component == "bar_chart":
        count = len(props["categories"])
        if not 2 <= count <= 6:
            raise InformationFault("INFORMATION_TEXT_FIT", f"{scene['id']}: a bar_chart takes 2 to 6 bars; split a longer comparison into scenes")
        size = S(30 if count > 4 else 34)
        font = _font(S(32 if count > 4 else 38))
        label_w, value_w = (440, 360) if large else (360, 290)
        rows = []
        for index, category in enumerate(props["categories"]):
            lines = text_lines(category, size, label_w)
            if lines > 2:
                raise InformationFault("INFORMATION_TEXT_FIT", f"{scene['id']}: bar label {category!r} wraps onto more than two lines; shorten it")
            if display_width(font, bar_value_text(props, index)) > value_w:
                raise InformationFault("INFORMATION_TEXT_FIT", f"{scene['id']}: the value label {bar_value_text(props, index)!r} is too wide; keep the unit to one short word such as 美元 and put the basis (for example 每百万 token) in the headline")
            rows.append(max(44 if count > 4 else 58, lines * size * 1.3))
        if sum(rows) + (22 if count > 4 else 34) * (count - 1) > 420:
            raise InformationFault("INFORMATION_TEXT_FIT", f"{scene['id']}: the bars do not fit their panel; use fewer or shorter bars")
    elif component == "comparison":
        for side in ("left", "right"):
            data = props[side]
            size = S(30 if len("".join(data["items"])) > 130 else 34)
            label_size = S(34 if len(data["label"]) > 20 else 42)
            height = 68 + text_lines(data["label"], label_size, 718) * label_size * 1.4 + 30
            height += sum(text_lines(item, size, 685) * size * 1.5 for item in data["items"]) + 20 * (len(data["items"])-1)
            if height > 500:
                raise InformationFault("INFORMATION_TEXT_FIT", f"{scene['id']}/{side}: comparison text exceeds its panel; condense text or split the comparison")


VERBATIM_RULES = ""
LARGE_TEXT_RULES = ""

_CJK = re.compile(r"[\u4e00-\u9fff]")
_PUNCT = re.compile(r"[\s，。！？；：、,.!?;:“”\"'（）()「」『』《》\[\]\-—…·]")
_CN_DIGITS = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def _cn_int(text: str) -> int | None:
    """一 .. 九十九 and 百, enough for percentages written in words."""
    if text == "百":
        return 100
    if "十" in text:
        tens, _, ones = text.partition("十")
        if (tens and tens not in _CN_DIGITS) or (ones and ones not in _CN_DIGITS):
            return None
        return (_CN_DIGITS[tens] if tens else 1) * 10 + (_CN_DIGITS[ones] if ones else 0)
    return _CN_DIGITS.get(text)


def _plain(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else repr(round(float(value), 6))


def _derived_numbers(text: str, scaled: bool = True) -> list[tuple[int, str]]:
    """Numbers the text gives in another form: 2 万 (20000), 百分之五 (5), 半美元 (0.5), 九成 (90)."""
    found: list[tuple[int, str]] = []
    if scaled:
        for match in re.finditer(r"(?<![A-Za-z0-9_.])(\d[\d,]*(?:\.\d+)?)\s*([万亿])", text):
            found.append((match.start(), _plain(float(match[1].replace(",", "")) * (10_000 if match[2] == "万" else 100_000_000))))
    for match in re.finditer(r"百分之([零一二两三四五六七八九十百]+)", text):
        if (value := _cn_int(match[1])) is not None:
            found.append((match.start(), _plain(value)))
    for match in re.finditer(r"半(?=[美元块小时分钟个倍年天])", text):
        found.append((match.start(), "0.5"))
    for match in re.finditer(r"([一二两三四五六七八九])成", text):
        found.append((match.start(), _plain(_CN_DIGITS[match[1]] * 10)))
    return found


def numbers_in(text: str, scaled: bool = True) -> list[str]:
    """Numbers as written in text, in order: 1.25, 2 万 (also 20000 when scaled), 百分之五 (5), 半美元 (0.5), 九成 (90)."""
    # Chinese prose does not need spaces before numerals. Keep ASCII identifiers
    # protected so an embedded model/version suffix is not treated as a quantity.
    found = [(m.start(), _plain(float(m[1].replace(",", "")))) for m in re.finditer(r"(?<![A-Za-z0-9_.])(\d[\d,]*(?:\.\d+)?)", text)]
    found += _derived_numbers(text, scaled)
    out: list[str] = []
    for _, number in sorted(found):
        if number not in out:
            out.append(number)
    return out


def _flat(text: str) -> str:
    return _PUNCT.sub("", text)


def _grams(text: str, size: int) -> set[str]:
    flat = _flat(text)
    return {flat[i:i + size] for i in range(len(flat) - size + 1)}


_CONDITION = re.compile(r"^(?:要是|如果|假如|假设|只要|除非|一旦|倘若)|(?:(?<![小同有此那暂顿即临随])时|的话|的时候)$")
_MARKERS = re.compile(r"^(?:要是|如果|假如|假设|只要|除非|一旦|倘若)|(?:了|时|的话|的时候)+$")


def conditions(text: str) -> list[tuple[str, str]]:
    """(condition, what follows it) for each conditional sentence in narration text."""
    out = []
    for sentence in re.split(r"[。！？；!?;]", text):
        clauses = [c.strip() for c in re.split(r"[，,：:]", sentence) if c.strip()]
        for i, clause in enumerate(clauses[:-1]):
            if len(clause) >= 4 and _CONDITION.search(clause):
                consequent = "，".join(clauses[i + 1:])
                if len(_flat(consequent)) >= 4:
                    out.append((clause, consequent))
                break
    return out


def condition_dropped(condition: str, consequent: str, screen: str) -> bool:
    """The screen restates the consequent (4+ shared characters in a row) without the condition."""
    flat_screen = _flat(screen)
    restated = any(gram in flat_screen for clause in re.split(r"[，,]", consequent) for gram in _grams(clause, 4))
    if not restated:
        return False
    core = _MARKERS.sub("", condition)
    kept = [gram for gram in _grams(core, 2) if gram in flat_screen]
    own = [gram for gram in kept if gram not in _flat(consequent)]
    return not (len(kept) >= 2 and own)


def fidelity_issues(plan: dict, context: dict) -> list[str]:
    """Soft checks for verbatim narration: a retry may fix them, but they never fail the job."""
    texts = {s["id"]: s["text"] for s in context["narration_plan"]["segments"]}
    chapter_numbers = set(numbers_in("\n".join(texts.values())))
    issues = []
    for scene in plan["scenes"]:
        own = "\n".join(texts[sid] for sid in scene["narration_segment_ids"])
        screen = "\n".join(visible_text(scene))
        shown = numbers_in(screen)
        invented = [n for n in shown if n not in chapter_numbers]
        if invented:
            issues.append(f"{scene['id']} 的画面上有旁白里没有的数字 {invented}，画面数字只能来自旁白")
        key = numbers_in(own, scaled=False)
        if scene["component"] == "comparison":
            need = key[:4]
        elif scene["component"] in ("concept_reveal", "big_number", "bar_chart", "price_change") and len(key) >= 3:
            need = key[:(len(key) + 1) // 2]
        else:
            need = []  # a title card is short; it only must not invent numbers
        missing = [n for n in need if n not in shown]
        if missing:
            issues.append(f"{scene['id']} 的画面没有带上旁白里的关键数字 {missing}，请把它们连同对象和单位写上屏"
                          + ("；对比卡两侧要各自写出对应的数字" if scene["component"] == "comparison" else ""))
        for condition, consequent in conditions(own):
            if condition_dropped(condition, consequent, screen):
                issues.append(f"{scene['id']} 的画面复述了「{consequent}」，却丢了旁白里的条件「{condition}」；请把条件一起写上屏")
    return issues


def messages(context: dict, correction: str | None = None) -> list[dict]:
    system = '根据素材，产出目标观众愿意看、听得懂、想继续看的视频。\n当前步骤：为讲述设计信息动效。让观众看见数字的大小、对象的差别或事物间的关系，画面随着讲述帮助理解。接口输出 JSON，使用 component_props_schemas 中的受控组件。场景连续覆盖旁白，场景 id 为 s1、s2…；headline 与有 title 字段的 props.title 一致。结构见 required_schema。'
    from g2.audience_copy import GUIDANCE
    system += "\n" + GUIDANCE
    if context.get("verbatim"):
        system += VERBATIM_RULES
    if context.get("large_text"):
        system += LARGE_TEXT_RULES
    catalog = {name: json.loads((ROOT / f"schemas/components/{name}.schema.json").read_text()) for name in COMPONENTS}
    # The motion renderer reveals comparison sides. The legacy renderer's
    # separate highlight strip is not implemented in this presentation.
    catalog["comparison"]["properties"].pop("highlights", None)
    schema = json.loads(SCHEMA.read_text())
    scene_schema = schema["properties"]["scenes"]["items"]
    # The model composes the picture. Mechanical reveal timing belongs to
    # the compiler; older explicitly timed plans remain valid inputs.
    for field in ("cues", "source_quotes"):
        scene_schema["properties"].pop(field, None)
    payload = {"source_text_as_untrusted_data": context["source_text"],
               "narration_plan": narration_brief(context["narration_plan"]), "component_props_schemas": catalog,
               "required_schema": schema}
    if correction:
        payload["validation_feedback"] = correction
    return [{"role": "system", "content": system}, {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]


def validate_plan(plan: dict, context: dict) -> None:
    errors = list(Draft202012Validator(json.loads(SCHEMA.read_text())).iter_errors(plan))
    if errors:
        raise InformationFault("INVALID_INFORMATION_PLAN", errors[0].message)
    segments = context["narration_plan"]["segments"]
    expected = [s["id"] for s in segments]
    if [sid for scene in plan["scenes"] for sid in scene["narration_segment_ids"]] != expected:
        raise InformationFault("INFORMATION_COVERAGE", "cover every narration segment once, in order")
    if [s["id"] for s in plan["scenes"]] != [f"s{i+1}" for i in range(len(plan["scenes"]))]:
        raise InformationFault("INFORMATION_SCENE_IDS", "scene ids must be s1..sN")
    texts = {s["id"]: s["text"] for s in segments}
    segments_by_id = {s["id"]: s for s in segments}
    reference = "\n".join([context["source_text"], *texts.values()])
    # A chart that prints 0.5 for the narration's 半美元 is not altering a technical token.
    reference += "\n" + " ".join(number for _, number in _derived_numbers(reference))
    for scene in plan["scenes"]:
        component, props = scene["component"], scene["props"]
        schema = json.loads((ROOT / f"schemas/components/{component}.schema.json").read_text())
        errors = list(Draft202012Validator(schema).iter_errors(props))
        if errors:
            raise InformationFault("INFORMATION_PROPS", errors[0].message)
        if component == "bar_chart" and len(props["categories"]) != len(props["values"]):
            raise InformationFault("INFORMATION_PROPS", f"{scene['id']}: bar_chart needs one value per category")
        validate_text_fit(scene, bool(context.get("large_text")))
        if component not in ("comparison", "bar_chart") and props["title"] != scene["headline"]:
            raise InformationFault("INFORMATION_HEADLINE", scene["id"])
        if component == "comparison" and props.get("highlights"):
            raise InformationFault("INFORMATION_PROPS", "comparison uses side cues; omit highlights")
        if "cues" not in scene:
            continue
        required = targets(component, props)
        actual = [cue["target_id"] for cue in scene["cues"]]
        if sorted(actual) != sorted(required):
            raise InformationFault("INFORMATION_CUE_TARGETS", f"{scene['id']} needs each of {required} exactly once")
        previous = (-1, -1)
        opening = None
        for cue in scene["cues"]:
            sid = cue["segment_id"]
            if sid not in scene["narration_segment_ids"]:
                raise InformationFault("INFORMATION_CUE_QUOTE", f"{scene['id']} 的 target {cue['target_id']} 指向本场景未覆盖的段 {sid}")
            if cue["quote"] not in texts[sid]:
                source_hint = next((quote for quote in segments_by_id[sid].get("source_quotes", []) if cue["quote"] in quote), None)
                hint = f"；它其实是素材原句 {source_hint!r}，不是旁白" if source_hint else ""
                raise InformationFault("INFORMATION_CUE_QUOTE",
                    f"{scene['id']} 的 target {cue['target_id']} 引文 {cue['quote']!r} 不是段 {sid} 旁白的连续原文{hint}；"
                    f"该段旁白原文为 {texts[sid]!r}，请从中逐字复制一段连续文字")
            position = (expected.index(sid), texts[sid].index(cue["quote"]))
            if component == "big_number":
                # The number is often said after its label ("……是……的 25 倍"), so after the title the three
                # elements may come in any order; none may come before the title.
                opening = opening or position
                if position < opening:
                    raise InformationFault("INFORMATION_CUE_ORDER", f"{scene['id']} 的 cue {cue['target_id']} 出现在标题之前，请从本场首句开头开始")
                continue
            if position < previous:
                raise InformationFault("INFORMATION_CUE_ORDER", f"{scene['id']} 的 cue {cue['target_id']} 相对前一个元素回退了，请按旁白出现顺序排列")
            previous = position
        first = scene["cues"][0]
        if (first["target_id"] != required[0] or first["segment_id"] != scene["narration_segment_ids"][0]
                or not texts[first["segment_id"]].startswith(first["quote"])):
            raise InformationFault("INFORMATION_OPENING_CUE",
                f"{scene['id']} 的第一个元素必须是 target {required[0]}，且其 quote 为覆盖首段 {scene['narration_segment_ids'][0]} 旁白的开头连续片段；"
                f"当前为 target {first['target_id']}、segment {first['segment_id']}、quote {first['quote']!r}；"
                f"该段旁白开头为 {texts[scene['narration_segment_ids'][0]][:32]!r}，请从段首逐字复制")


def source_domain(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def build_source_card(sources: list[dict]) -> dict:
    """Source card props, derived only from the request's sources (title, domain of the url, date)."""
    from g2.package_kit import PackageConfigError
    from g2.packaging_library import source_props
    try:
        props = source_props(sources)
    except PackageConfigError as exc:
        raise InformationFault("INFORMATION_SOURCE_CARD", str(exc)) from exc
    schema = json.loads((ROOT / "schemas/components/source_card.schema.json").read_text())
    errors = list(Draft202012Validator(schema).iter_errors(props))
    if errors or any(not item["domain"] for item in props["sources"]):
        raise InformationFault("INFORMATION_SOURCE_CARD", f"sources 无法生成来源卡：{errors[0].message if errors else 'url 没有域名'}")
    validate_text_fit({"id": "source_card", "component": "source_card", "props": props}, True)
    return props


def check_source_card(props: dict, sources: list[dict]) -> None:
    """The one place where on-screen text is not tied to the narration.

    A source card shows data the request supplied (title, domain, date), which the narration never says, so the
    narration-grounding checks (numbers, technical tokens, cues) do not apply to it. The exemption is narrow:
    the card must equal what build_source_card() derives from the request, character for character. Nothing the
    card shows is added to the reference text, so a source title never makes a number or token on another
    scene acceptable.
    """
    if props != build_source_card(sources):
        raise InformationFault("INFORMATION_SOURCE_TEXT", "来源卡的文字必须逐字来自请求里的 sources（标题、域名、日期），不能自行增改")


def load_sources(pipeline) -> list[dict]:
    path = getattr(pipeline, "PROJECT_INPUT", None)
    path = path.parent / "input.json" if path else None
    saved = json.loads(path.read_text(encoding="utf-8")) if path and path.is_file() else {}
    # Library jobs append a separate source card after the complete narration.
    # Legacy package jobs retain their original narration-bound source scene.
    resolver = getattr(pipeline, "packaging_record", None)
    if saved.get("packaging_resolved") is not None or (resolver and resolver() is not None):
        return []
    return saved.get("sources") or []


TEXT_SCENES = ("title_card", "concept_reveal", "comparison")


def place_source_card(scenes: list[dict], last_chapter_ids: list[str], texts: dict[str, str], props: dict) -> tuple[list[dict], dict]:
    """Add the source card at the end of the final chapter, on a narration segment of its own.

    Scenes must cover the narration once, in order, so the card cannot be an extra scene: it takes the final
    segment of the last scene when that scene has another segment and no cue on it; otherwise it replaces a
    final text scene that has only that segment; otherwise it takes the last segment of an earlier scene of
    the chapter that can spare one. Data scenes (numbers, charts) are never replaced. Returns the scenes and a
    report that says where the card went, or why it did not.
    """
    ids = set(last_chapter_ids)
    cued = lambda scene, sid: any(cue["segment_id"] == sid for cue in scene["cues"])
    last = len(scenes) - 1
    if last < 0 or not set(scenes[last]["narration_segment_ids"]) <= ids:
        return scenes, {"placed": False, "reason": "no scene in the final chapter"}
    def card(donor: dict, sid: str) -> dict:
        text, n = texts[sid], len(props["sources"])
        cues = [{"target_id": "title", "segment_id": sid, "quote": text[:1]}]
        for i in range(n):
            cues.append({"target_id": f"line:{i}", "segment_id": sid, "quote": text[int(len(text) * (i + 1) / (n + 2)):][:1]})
        return {"id": f"{donor['id'].rsplit('_', 1)[0]}_src" if "_" in donor["id"] else "src", "component": "source_card", "props": props,
                "narration_segment_ids": [sid], "headline": props["title"], "source_quotes": [text], "cues": cues}
    def donate(index: int) -> tuple[list[dict], dict] | None:
        donor = scenes[index]
        sid = donor["narration_segment_ids"][-1]
        if len(donor["narration_segment_ids"]) < 2 or cued(donor, sid) or not set(donor["narration_segment_ids"]) <= ids:
            return None
        trimmed = {**donor, "narration_segment_ids": donor["narration_segment_ids"][:-1]}
        return ([*scenes[:index], trimmed, card(donor, sid), *scenes[index + 1:]],
                {"placed": True, "how": "took the last segment of a scene", "scene": donor["id"], "segment": sid})
    if (result := donate(last)):
        return result
    only = scenes[last]
    if len(only["narration_segment_ids"]) == 1 and only["component"] in TEXT_SCENES:
        sid = only["narration_segment_ids"][0]
        return ([*scenes[:last], card(only, sid)], {"placed": True, "how": "replaced the final text scene", "scene": only["id"], "segment": sid})
    for index in range(last - 1, -1, -1):
        if not set(scenes[index]["narration_segment_ids"]) <= ids:
            break
        if (result := donate(index)):
            return result
    return scenes, {"placed": False, "reason": "no final scene can spare a narration segment without losing a cue or a data scene"}


def plan_storyboard(pipeline, source: str, narration: dict) -> dict:
    from g2.content_video import source_units, chapter_source
    units = source_units(source)
    chapters = narration.get("chapters") or [{"id": "", "title": narration["title"], "narration_segment_ids": [s["id"] for s in narration["segments"]]}]
    scenes = []
    verbatim = bool(getattr(pipeline, "LOCK_NARRATION", False))
    for index, chapter in enumerate(chapters):
        ids = set(chapter["narration_segment_ids"])
        local = {"title": chapter["title"], "core_message": narration["core_message"],
                 "segments": [s for s in narration["segments"] if s["id"] in ids]}
        context = {"source_text": chapter_source(chapter, units) if chapter["id"] else source, "narration_plan": local,
                   **({"verbatim": True} if verbatim else {}), **({"large_text": True} if short_form(narration) else {})}
        def check(value, ctx):
            try:
                validate_plan(value, ctx)
            except InformationFault as exc:
                raise pipeline.G2Fault(exc.code, exc.message) from exc
        stage = f"chapter-{chapter['id']}-information" if chapter["id"] else "information"
        soft = {}
        local_plan = pipeline.generate_validated_plan(stage, messages, SCHEMA, check, context, **soft)
        for scene in local_plan["scenes"]:
            scenes.append({**scene, "id": f"{chapter['id']}_{scene['id']}" if chapter["id"] else scene["id"]})
    sources = load_sources(pipeline)
    if sources:
        try:
            props = build_source_card(sources)
        except InformationFault as exc:
            raise pipeline.G2Fault(exc.code, exc.message) from exc
        last_ids = chapters[-1]["narration_segment_ids"]
        texts = {s["id"]: s["text"] for s in narration["segments"]}
        scenes, report = place_source_card(scenes, last_ids, texts, props)
        pipeline.write_json(pipeline.ARTIFACTS / "source-card.json", {**report, "sources": sources})
        print(f"SOURCE CARD: {'placed on ' + report['segment'] if report['placed'] else 'skipped, ' + report['reason']}", flush=True)
    combined = {"scenes": scenes}
    pipeline.write_json(pipeline.ARTIFACTS / "information-plan.json", combined)
    return combined


def automatic_cues(scene: dict, rows: dict) -> list[dict]:
    """Spread component reveals over the measured speech, without model timing IDs.

    Phrase boundaries are measured. Within-phrase positions are estimates,
    as with the legacy character-position cues, and are recorded as such.
    """
    required = targets(scene["component"], scene["props"])
    speech = [rows[sid] for sid in scene["narration_segment_ids"]]
    total = sum(len(row["text"]) for row in speech)
    result = []
    for index, target in enumerate(required):
        position = min(total - 1, total * index // len(required))
        for row in speech:
            if position < len(row["text"]):
                result.append({"target_id": target, "segment_id": row["id"],
                               "quote": row["text"][position:position + 1], "character_index": position})
                break
            position -= len(row["text"])
    return result


def compile_storyboard(plan: dict, alignment: dict, pipeline) -> dict:
    rows = {r["id"]: r for r in alignment["rows"]}
    scenes, timing = [], []
    for scene in plan["scenes"]:
        beats = []
        order = list(rows)
        if scene["component"] == "source_card":
            try:
                check_source_card(scene["props"], load_sources(pipeline))
            except InformationFault as exc:
                raise pipeline.G2Fault(exc.code, exc.message) from exc
        automatic = "cues" not in scene
        cues = scene["cues"] if not automatic else automatic_cues(scene, rows)
        if scene["component"] == "big_number":  # validated in any order after the title; reveal in spoken order
            cues = sorted(cues, key=lambda c: (order.index(c["segment_id"]),
                          c.get("character_index", rows[c["segment_id"]]["text"].index(c["quote"]))))
        for cue in cues:
            row = rows[cue["segment_id"]]
            character = cue.get("character_index", row["text"].index(cue["quote"]))
            offset = int(character / len(row["text"]) * (row["end_ms"] - row["start_ms"]))
            beat = {"id": f"{scene['id']}_b{len(beats)}", "action": "reveal", "target_id": cue["target_id"],
                    "trigger": {"segment_id": row["id"], "boundary": "start", "offset_ms": offset}}
            beats.append(beat)
            timing.append({"scene_id": scene["id"], **cue, "automatic": automatic,
                           "estimated_start_ms": row["start_ms"] + offset})
        scenes.append({"id": scene["id"], "component": scene["component"], "props": scene["props"],
                       "narration_segment_ids": scene["narration_segment_ids"], "purpose": scene["headline"],
                       "visual_intent": {"viewer_understands": scene["headline"], "before": "要点尚未展开", "after": scene["headline"]}, "beats": beats})
    board = {"scenes": scenes}
    pipeline.write_json(pipeline.ARTIFACTS / "storyboard-plan.json", board)
    pipeline.write_json(pipeline.ARTIFACTS / "information-timing.json", {"cues": timing,
        "phrase_alignment": "measured TTS activity", "within_phrase_timing": "estimated from character position, not word alignment"})
    return board
