"""Conservative text/quantity checks over trusted-code-extracted, untrusted page regions."""
from __future__ import annotations

import re
from decimal import Decimal

from core.video.screenshots import ScreenshotError

GENERIC = set("the and for with from this that page pages screenshot screenshots full element elements feature features supports support pricing price prices usd eur cny monthly annually yearly month year per input output token tokens api plan plans pro free release released announcement official documentation docs a an is of to in on can use using".split())
TRANSLATIONS = {"截图": ("screenshot",), "元素": ("element",), "整页": ("full page",), "网页": ("page",),
                "价格": ("pricing", "price", "$"), "计费": ("billing", "billed"), "功能": ("feature",),
                "发布": ("release", "launch", "announc"), "缓存": ("cache", "caching"), "工作流": ("workflow",)}


def entities(text: str) -> list[str]:
    return list(dict.fromkeys(word.lower() for word in re.findall(r"\b[A-Za-z][A-Za-z0-9_.-]{2,}\b", text)
                             if word.lower() not in GENERIC))[:12]


def terms(text: str) -> set[str]:
    found = {word.lower() for word in re.findall(r"\b[A-Za-z][A-Za-z0-9_.-]{2,}\b", text)} - GENERIC
    for run in re.findall(r"[\u4e00-\u9fff]{2,}", text):
        found.update(run[i:i+2] for i in range(len(run)-1))
    for key, values in TRANSLATIONS.items():
        if key in text:
            found.update(values)
    return found


def prices(text: str) -> list[tuple[str, str]]:
    found = []
    for before, amount, after in re.findall(r"(\$|USD\s*|US\$|€|EUR\s*|¥|￥|CNY\s*)?(\d+(?:[,.]\d+)*)(\s*(?:美元|美金|欧元|人民币|元))?", text, re.I):
        unit = (before + after).strip().lower()
        if not unit:
            continue
        currency = "USD" if "$" in unit or "usd" in unit or "美" in unit else "EUR" if "€" in unit or "eur" in unit or "欧" in unit else "CNY"
        try:
            found.append((str(Decimal(amount.replace(",", "")).normalize()), currency))
        except Exception:
            continue
    return list(dict.fromkeys(found))


def cycles(text: str) -> set[str]:
    out = set()
    if re.search(r"每月|月付|按月|/月|monthly|per\s+month|/\s*mo(?:nth)?\b", text, re.I): out.add("month")
    if re.search(r"每年|年付|按年|/年|annually|annual|yearly|per\s+year|/\s*year", text, re.I): out.add("year")
    if re.search(r"每百万|百万\s*token|million\s*(?:input\s*|output\s*)?tokens|/\s*(?:1\s*)?m\s*tokens", text, re.I): out.add("million_tokens")
    if re.search(r"每周|周付|weekly|per\s+week|/week", text, re.I): out.add("week")
    if re.search(r"每天|每日|日付|daily|per\s+day|/day", text, re.I): out.add("day")
    if re.search(r"每季度|季付|quarterly|per\s+quarter", text, re.I): out.add("quarter")
    if re.search(r"每用户|每位用户|每个用户|每席位|per\s+(?:user|seat)|/user|/seat", text, re.I): out.add("seat")
    return out


def compatible(need: dict, candidate: dict, page_title: str) -> tuple[bool, str, float]:
    text = candidate["text"]
    claim = need["claims"]
    names = entities(claim)
    if names and not any(name in (text + " " + page_title).lower() for name in names):
        return False, "页面或区域缺少讲稿所讲的产品实体", 0
    proper_names = [word.lower() for word in re.findall(r"\b[A-Z][A-Za-z0-9_.-]{2,}\b|\bv\d+(?:\.\d+)+", claim)
                    if word.lower() not in GENERIC]
    if any(word not in (text + " " + page_title).lower() for word in proper_names):
        return False, "产品名称或版本与讲稿不一致", 0
    overlapping = {word for word in terms(claim + " " + need["text"]) if word in text.lower()}
    if not overlapping:
        return False, "区域内容没有对应段落的可核对词项", 0
    expected = prices(claim)
    if need["kind"] == "pricing":
        if not expected:
            return False, "价格段落缺少可精确核对的金额与币种", 0
        if not set(expected).issubset(prices(text)):
            return False, "价格金额或币种与讲稿不一致", 0
        wanted, actual = cycles(claim), cycles(text)
        if not wanted.issubset(actual):
            return False, "价格计费周期或单位与讲稿不一致", 0
        if "year" in actual and "year" not in wanted:
            return False, "区域存在讲稿没有确认的年付条件", 0
        for tier in re.findall(r"(?<![A-Za-z0-9])(Plus|Pro|Premium|Business|Enterprise|Team|Free|Starter|Standard|Basic|Max|Ultra)(?![A-Za-z0-9])", claim, re.I):
            if not re.search(r"(?<![A-Za-z0-9])" + re.escape(tier) + r"(?![A-Za-z0-9])", text, re.I):
                return False, "价格套餐与讲稿不一致", 0
        for word, aliases in (("输入", ("input", "输入")), ("输出", ("output", "输出")), ("缓存", ("cached", "cache", "缓存"))):
            if word in claim and not any(alias in text.lower() for alias in aliases):
                return False, "价格适用条件与讲稿不一致", 0
    # Prefer an exact, compact region over a whole page that happens to contain all words.
    score = len(overlapping) + min(4, sum(name in text.lower() for name in names) * 2)
    score += 3 if need["kind"] == "pricing" and expected else 0
    score -= min(3, len(text) / 2500)
    score -= max(0, candidate.get("box", {}).get("height", 0) - 700) / 300
    return True, "来源、实体及展示条件通过文本检查", round(score, 3)


def rank(need: dict, page: dict) -> list[dict]:
    if page.get("blocked"):
        raise ScreenshotError(page["blocked"])
    accepted, reasons = [], []
    for candidate in page.get("candidates", []):
        okay, reason, score = compatible(need, candidate, page.get("title", ""))
        if okay:
            accepted.append({**candidate, "score": score, "match_reason": reason})
        else:
            reasons.append(reason)
    if not accepted:
        raise ScreenshotError("没有匹配的截图区域：" + (reasons[0] if reasons else "页面缺少可见正文区域"))
    return sorted(accepted, key=lambda item: item["score"], reverse=True)
