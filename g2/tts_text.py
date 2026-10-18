"""Text-to-speech input fixes that leave the script untouched: captions and the exact-text check keep the author's words.

The VoxCPM2 voice reads "Opus 5.5" as "five point five" in English, so a decimal number is spelled out in Chinese
("五点五") before it reaches the voice. It also reads a bare "2" before 万 as "二" ("二万", which nobody says); spoken
Chinese uses 两 before a magnitude or a measure word ("两万 token", "两倍"). Everything else goes through as written.
"""
import re

_DIGITS = "零一二三四五六七八九"
_DECIMAL = re.compile(r"(?<![\d.])(\d{1,4})\.(\d+)(?![\d]|\.\d)")


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


def _decimal(match: re.Match) -> str:
    whole, fraction = match.group(1), match.group(2)
    return _integer(int(whole)) + "点" + "".join(_DIGITS[int(char)] for char in fraction)


#: A lone 2 (not part of a longer number, not an ordinal or a date) before one of these is said 两.
_TWO_BEFORE = ("万", "亿", "千", "百", "个", "倍", "次", "种", "档", "年", "天", "周", "小时", "分钟", "秒", "美元", "美分", "块",
               "条", "步", "层", "行", "句", "段", "项", "家", "位", "遍", "回")
_TWO = re.compile(r"(?<![\d.第])(?<!第 )2(?![\d]|\.\d)\s*(?=(?:" + "|".join(_TWO_BEFORE) + "))")


def for_voice(text: str) -> str:
    """The text the voice reads: backticks dropped (they only mark code in the script), decimals in Chinese, 两 for 2."""
    text = _DECIMAL.sub(_decimal, text.replace("`", ""))
    return _TWO.sub("两", text)
