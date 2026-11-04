"""Weekly review: how the videos published in one week were watched, cut along the dimensions we can act on.

The yardstick is each video's plays 24 hours after it went up, read off the collector's snapshots. Next to it sit
the creator centre's latest completion rate, average watch time and 3-second exits for each video, when read. One hit can hold
most of a week's plays, so groups are compared by median, and a group with fewer than ``MIN_N`` measured videos is
shown without being judged. Every finding and suggestion is a fixed rule applied to these numbers; nothing here
changes configuration — the user decides.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from statistics import median
from typing import Optional

#: Hours after publishing that are read off the snapshots; the first one is the yardstick.
WINDOWS = (24, 72, 168)
#: Fewer measured videos than this in a group: shown, never judged.
MIN_N = 3
#: A group whose median is at least this many times the week's (or at most its inverse) is called out.
RATIO = 2.0
#: Structures the T05 column offers (config/content_columns/chatgpt_t05_comparison.yaml), in their letter order.
STRUCTURES = ("价格反差与分任务对决", "同题盲测", "按人群选购", "新功能同台", "单项擂台", "误区纠正", "换用成本")
#: Title tags. Heuristics, named so the review can say what it looked for.
PRODUCTS = re.compile(r"ChatGPT|OpenAI|GPT|Codex|Claude|Anthropic|Gemini|Google AI|Grok|xAI|Qwen|通义|千问|Kimi|"
                      r"DeepSeek|豆包|元宝|文心|智谱|GLM|Mistral|Llama|Copilot|Cursor|Perplexity", re.I)
MONEY = re.compile(r"\$|美元|块钱|\d+\s*元|价格|涨价|降价|半价|免费|付费|订阅|套餐|额度|账单|成本|Plus|Pro\b", re.I)
DURATIONS = ((120, "2 分钟内"), (240, "2–4 分钟"), (480, "4–8 分钟"), (None, "8 分钟以上"))
SLOTS = ((6, "凌晨 0–6 点"), (12, "上午 6–12 点"), (18, "下午 12–18 点"), (24, "晚上 18–24 点"))
DIMENSIONS = (("form", "形式"), ("matrix", "矩阵"), ("column", "栏目"), ("structure", "对比结构（T05）"),
              ("product", "标题写了产品名"), ("money", "标题写了钱"), ("duration", "时长"), ("slot", "发布时段"))


def structure(text: str) -> str:
    """The T05 structure a candidate's 推荐结构 field names ("A 价格反差…"), or "" when it names none."""
    text = text or ""
    found = [(text.find(name), i) for i, name in enumerate(STRUCTURES) if name in text]
    if not found:
        return ""
    i = min(found)[1]
    return f"{'ABCDEFG'[i]} {STRUCTURES[i]}"


def views_at(snapshots: list[tuple[float, int]], pubdate: float, hours: int, now: float) -> tuple[str, Optional[int]]:
    """Plays ``hours`` after publishing: ("ok", n), ("pending", None) before that time, ("gap", None) when no snapshot
    sits close enough to it (collection was off). Snapshots are (observed_at, plays), oldest first."""
    target = pubdate + hours * 3600
    if now < target:
        return "pending", None
    tolerance = max(3 * 3600, hours * 900)
    before = next((s for s in reversed(snapshots) if s[0] <= target), None)
    after = next((s for s in snapshots if s[0] >= target), None)
    if after is None or after[0] - target > tolerance:
        return "gap", None
    if before is None or target - before[0] > tolerance or after[0] == before[0]:
        return "ok", after[1]  # nothing just before (collection resumed): the first reading after is close enough
    return "ok", round(before[1] + (after[1] - before[1]) * (target - before[0]) / (after[0] - before[0]))


def _band(seconds: int) -> str:
    return next(label for limit, label in DURATIONS if limit is None or seconds < limit)


def _slot(pubdate: float) -> str:
    hour = datetime.fromtimestamp(pubdate).hour
    return next(label for limit, label in SLOTS if hour < limit)


def week_start(when: float) -> float:
    """Local Monday 00:00 of the week ``when`` falls in."""
    day = datetime.fromtimestamp(when).replace(hour=0, minute=0, second=0, microsecond=0)
    return (day - timedelta(days=day.weekday())).timestamp()


#: Watch numbers from the creator centre (``video["watch"]``), all percentages except avg_seconds.
WATCH = ("completion", "avg_seconds", "exit3s", "cover_pass")


def _watch(video: dict) -> dict:
    watch = video.get("watch") or {}
    values = {key: watch.get(key) if isinstance(watch.get(key), (int, float)) else None for key in WATCH}
    duration = video.get("duration") or 0
    values["watched_share"] = round(values["avg_seconds"] / duration * 100, 1) if values["avg_seconds"] is not None and duration else None
    return values


def _measure(video: dict, now: float) -> dict:
    snaps = sorted((t, v) for t, v in video.get("snapshots") or [] if type(v) is int)
    windows = {f"h{h}": views_at(snaps, video["pubdate"], h, now) for h in WINDOWS}
    title = video.get("title") or ""
    return {"bvid": video["bvid"], "title": title, "pubdate": video["pubdate"], "duration": video.get("duration") or 0,
            "view": video.get("view"), "metrics": video.get("metrics") or {},
            **{key: value for key, (_, value) in windows.items()}, "state": windows["h24"][0],
            "form": video.get("form") or "未标注", "matrix": video.get("matrix") or "未标注",
            "column": video.get("column") or "未标注", "structure": video.get("structure") or "",
            "product": "是" if PRODUCTS.search(title) else "否", "money": "是" if MONEY.search(title) else "否",
            "duration_band": _band(video.get("duration") or 0), "slot": _slot(video["pubdate"]), **_watch(video)}


def _key(item: dict, dimension: str) -> str:
    return item["duration_band"] if dimension == "duration" else item[dimension]


def _median(values: list[int]) -> Optional[float]:
    return float(median(values)) if values else None


def _groups(items: list[dict], previous: list[dict], dimension: str, overall: Optional[float]) -> list[dict]:
    values = {_key(i, dimension) for i in items if _key(i, dimension)}
    out = []
    for value in values:
        members = [i for i in items if _key(i, dimension) == value]
        measured = [i for i in members if i["h24"] is not None]
        before = [i["h24"] for i in previous if _key(i, dimension) == value and i["h24"] is not None]
        mid = _median([i["h24"] for i in measured])
        prior = _median(before) if len(before) >= MIN_N else None
        flag = ""
        if len(measured) >= MIN_N and mid is not None and overall:
            flag = "high" if mid >= overall * RATIO else "low" if mid <= overall / RATIO else ""
        best = max(measured, key=lambda i: i["h24"], default=None)
        out.append({"value": value, "videos": len(members), "measured": len(measured), "median": mid,
                    "max": best["h24"] if best else None, "best_title": best["title"] if best else "",
                    "previous_median": prior, "previous_measured": len(before), "flag": flag, **_watch_medians(members)})
    out.sort(key=lambda g: (g["median"] is None, -(g["median"] or 0), g["value"]))
    return out


def _watch_medians(items: list[dict]) -> dict:
    """Median completion, watch time and early exits of the videos that have them, and how many do."""
    read = [i for i in items if i["completion"] is not None]
    pick = lambda key: _median([i[key] for i in items if i[key] is not None])
    return {"watch_measured": len(read), "completion": pick("completion"), "avg_seconds": pick("avg_seconds"),
            "exit3s": pick("exit3s"), "cover_pass": pick("cover_pass")}


def _watch_findings(dimensions: list[dict], overall: dict) -> list[str]:
    """Groups whose median completion is at least RATIO times the week's, or at most its inverse (not by length)."""
    base = overall["completion"]
    if not base:
        return []
    out = []
    for dimension in dimensions:
        if dimension["key"] == "duration":
            continue  # shorter videos are finished more often by construction; compare lengths by early exits instead
        for group in dimension["groups"]:
            if group["watch_measured"] < MIN_N or group["completion"] is None:
                continue
            if group["completion"] >= base * RATIO or group["completion"] <= base / RATIO:
                word = "高" if group["completion"] >= base * RATIO else "低"
                out.append(f"{dimension['label']}「{group['value']}」完播率中位数 {group['completion']:g}%，"
                           f"整体 {base:g}%，明显偏{word}（{group['watch_measured']} 条）。")
    return out


def _findings(dimensions: list[dict], overall: Optional[float], counts: dict) -> tuple[list[str], list[str]]:
    findings, suggestions = [], []
    if counts["gap"]:
        findings.append(f"{counts['gap']} 条视频在发布后 24 小时附近没有采集快照（采集当时停了），不计入中位数。")
    if counts["pending"]:
        findings.append(f"{counts['pending']} 条视频还没满 24 小时，下周再看它们。")
    for dimension in dimensions:
        for group in dimension["groups"]:
            if not group["flag"]:
                continue
            word = "高" if group["flag"] == "high" else "低"
            trend = ""
            if group["previous_median"] is not None:
                trend = f"；上周 {group['previous_median']:g}"
            findings.append(f"{dimension['label']}「{group['value']}」24 小时中位数 {group['median']:g}，"
                            f"整体 {overall:g}，明显偏{word}（{group['measured']} 条{trend}）。")
            prior = group["previous_median"]
            two_weeks = prior is not None and overall and (prior <= overall / RATIO if group["flag"] == "low" else prior >= overall * RATIO)
            if dimension["key"] in ("matrix", "column") and two_weeks:
                if group["flag"] == "low":
                    suggestions.append(f"{dimension['label']}「{group['value']}」连续两周明显低于整体"
                                       f"（{prior:g} → {group['median']:g}），可以考虑减少或暂停。")
                else:
                    suggestions.append(f"{dimension['label']}「{group['value']}」连续两周明显高于整体"
                                       f"（{prior:g} → {group['median']:g}），可以考虑加量。")
            elif dimension["key"] in ("product", "money") and group["value"] == "是" and group["flag"] == "high":
                suggestions.append(f"标题{dimension['label'][2:]}的视频中位数是整体的 {group['median'] / overall:.1f} 倍，"
                                   "写标题时优先把它写出来。")
    return findings, suggestions


def review(videos: list[dict], start: float, now: float) -> dict:
    """The review of the week starting ``start`` (local Monday 00:00), compared with the week before.

    ``videos`` are published workflow videos: bvid, title, pubdate, duration, view (current plays), metrics (current
    counters), snapshots [(observed_at, plays)], form, matrix, column, structure."""
    end = start + 7 * 86400
    measured = [_measure(v, now) for v in videos if start - 7 * 86400 <= v["pubdate"] < end]
    items = [i for i in measured if i["pubdate"] >= start]
    previous = [i for i in measured if i["pubdate"] < start]
    counts = {state: sum(i["state"] == state for i in items) for state in ("ok", "pending", "gap")}
    overall = _median([i["h24"] for i in items if i["h24"] is not None])
    prior = _median([i["h24"] for i in previous if i["h24"] is not None])
    current = [i["view"] for i in items if type(i["view"]) is int]
    total = sum(current)
    dimensions = []
    for key, label in DIMENSIONS:
        groups = _groups(items, previous, key, overall)
        if key == "structure" and not groups:
            continue  # no T05 item with a structure published this week
        dimensions.append({"key": key, "label": label, "groups": groups})
    findings, suggestions = _findings(dimensions, overall, counts)
    watch = _watch_medians(items)
    findings += _watch_findings(dimensions, watch)
    top = sorted((i for i in items if i["h24"] is not None), key=lambda i: -i["h24"])[:5]
    return {"start": start, "end": end, "now": now, "min_n": MIN_N, "ratio": RATIO,
            "summary": {"videos": len(items), **counts, "median_24h": overall, "previous_median_24h": prior,
                        "previous_videos": len(previous), "views": total, "watch": watch,
                        "top_share": round(max(current) / total, 3) if total else None},
            "dimensions": dimensions, "top": top, "findings": findings, "suggestions": suggestions,
            "videos": sorted(items, key=lambda i: -i["pubdate"])}
