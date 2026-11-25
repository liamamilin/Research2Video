"""Text-to-speech input fixes that leave the script untouched: captions and the exact-text check keep the author's words.

The VoxCPM2 voice reads "Opus 5.5" as "five point five" in English, so a decimal number is spelled out in Chinese
("五点五") before it reaches the voice. Percentages ("63.6%" -> "百分之六十三点六"), numeric ranges ("440—620" /
"5-10万美元"), and common unit slashes ("tok/s", "美元/台") are also normalized to natural spoken Chinese. A bare "2"
before 万 or a measure word becomes 两 ("两万 token", "两倍"). Everything else goes through as written.
"""
import re

_DIGITS = "零一二三四五六七八九"
_DECIMAL = re.compile(r"(?<![\d.])(\d{1,4})\.(\d+)(?![\d]|\.\d)")
_PERCENT_RANGE = re.compile(
    r"(?<![\d.\-–—~～])(\d{1,4})(?:\.(\d+))?\s*(?:[%％]\s*)?[-–—~～]\s*(\d{1,4})(?:\.(\d+))?\s*[%％]"
)
_PERCENT = re.compile(r"(?<![\d.])(\d{1,4})(?:\.(\d+))?\s*[%％]")
_RANGE_DASH = re.compile(
    r"(?<![\d.\-–—~～])(\d{1,4}(?:\.\d+)?)\s*[–—~～]\s*(\d{1,4}(?:\.\d+)?)(?![\d.\-–—~～])"
)
_RANGE_UNITS = (
    "万", "亿", "千", "百", "美元", "美分", "欧元", "英镑", "元", "块", "倍", "个", "天", "年", "月", "周",
    "小时", "分钟", "秒", "次", "家", "条", "位", "款", "台", "人", "户", "度", "毫米", "公里", "米", "克", "斤",
)
_RANGE_HYPHEN = re.compile(
    r"(?<![A-Za-z\d.\-])(\d{1,4}(?:\.\d+)?)\s*-\s*(\d{1,4}(?:\.\d+)?)(?=\s*(?:" + "|".join(_RANGE_UNITS) + "))"
)
_RATE_SLASHS = (
    (re.compile(r"(?<![A-Za-z/])(?:tokens?|tok)/s(?![A-Za-z/])", re.IGNORECASE), "每秒 token"),
    (re.compile(r"(?<![A-Za-z/])req/s(?![A-Za-z/])", re.IGNORECASE), "每秒请求"),
    (re.compile(r"(?<![A-Za-z/])(GB|MB|KB)/s(?![A-Za-z/])", re.IGNORECASE), r"每秒 \1"),
    (re.compile(r"(?<![A-Za-z/])km/h(?![A-Za-z/])", re.IGNORECASE), "公里每小时"),
    (re.compile(r"(?<=[\d\s])t/s(?![A-Za-z/])", re.IGNORECASE), "每秒 token"),
)
_PER_UNIT_CN = re.compile(r"(美元|美分|欧元|英镑|元|块)/([台个克杯次月年天时度人户家条份])")
# High-confidence, unambiguous Chinese spoken lexicon (script & captions stay verbatim).
_SPOKEN_LEXICON = (
    (re.compile(r"(?<![A-Za-z0-9])CO(?:2|₂)(?![A-Za-z0-9])"), "二氧化碳"),
    (re.compile(r"(?<![A-Za-z0-9])H(?:2|₂)O(?![A-Za-z0-9])"), "水"),
    (re.compile(r"(?<![A-Za-z0-9])O(?:2|₂)(?![A-Za-z0-9])"), "氧气"),
    (re.compile(r"(?<![A-Za-z0-9])SaaS(?![A-Za-z0-9])"), "萨斯"),
)


def _integer(value: int) -> str:
    """0..9999 as spoken Chinese (10 -> 十, 105 -> 一百零五, 2000 -> 两千, 200 -> 两百, 22 -> 二十二)."""
    if value < 10:
        return _DIGITS[value]
    if value < 20:
        return "十" + (_DIGITS[value % 10] if value % 10 else "")
    units = [(1000, "千"), (100, "百"), (10, "十")]
    parts, rest, zero_pending = [], value, False
    for size, name in units:
        digit, rest = divmod(rest, size)
        if digit:
            if zero_pending:
                parts.append("零")
                zero_pending = False
            parts.append(("两" if digit == 2 and size >= 100 else _DIGITS[digit]) + name)
        elif parts:
            zero_pending = True
    if rest:
        if zero_pending:
            parts.append("零")
        parts.append(_DIGITS[rest])
    return "".join(parts)


def _spoken_number(whole: str, fraction: str | None = None) -> str:
    base = _integer(int(whole))
    if fraction:
        return base + "点" + "".join(_DIGITS[int(char)] for char in fraction)
    return base


def _decimal(match: re.Match) -> str:
    return _spoken_number(match.group(1), match.group(2))


def _percent(match: re.Match) -> str:
    return "百分之" + _spoken_number(match.group(1), match.group(2))


def _percent_range(match: re.Match) -> str:
    left = _spoken_number(match.group(1), match.group(2))
    right = _spoken_number(match.group(3), match.group(4))
    return f"百分之{left}到百分之{right}"


#: A lone 2 (not part of a longer number, not an ordinal or a date) before one of these is said 两.
_TWO_BEFORE = ("万", "亿", "千", "百", "个", "倍", "次", "种", "档", "年", "天", "周", "小时", "分钟", "秒", "美元", "美分", "块",
               "条", "步", "层", "行", "句", "段", "项", "家", "位", "遍", "回")
_TWO = re.compile(r"(?<![\d.第])(?<!第 )2(?![\d]|\.\d)\s*(?=(?:" + "|".join(_TWO_BEFORE) + "))")


def for_voice(text: str) -> str:
    """The text the voice reads: drop backticks, speak percentages/ranges/unit slashes/decimals in Chinese, 两 for 2."""
    text = text.replace("`", "")
    text = _PERCENT_RANGE.sub(_percent_range, text)
    text = _PERCENT.sub(_percent, text)
    text = _RANGE_DASH.sub(r"\1到\2", text)
    text = _RANGE_HYPHEN.sub(r"\1到\2", text)
    for pattern, spoken in _RATE_SLASHS:
        text = pattern.sub(spoken, text)
    for pattern, spoken in _SPOKEN_LEXICON:
        text = pattern.sub(spoken, text)
    text = _PER_UNIT_CN.sub(r"\1每\2", text)
    text = _DECIMAL.sub(_decimal, text)
    return _TWO.sub("两", text)
