"""A Job's period in the words a person picks (manual, daily, weekdays, some weekdays, monthly) and the cron behind it.

The scheduler only knows cron (spec/product-redesign.md 2.5); this is the two-way translation for the matrix page. A cron
that none of the five shapes produces reads back as ``custom`` and is left to the schedule page.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from core import cron

KINDS = ("manual", "daily", "weekdays", "weekly", "monthly")
DAY_NAMES = ("日", "一", "二", "三", "四", "五", "六")  # cron day-of-week 0..6


def _time(value: str) -> tuple[int, int]:
    found = re.fullmatch(r"([01]?\d|2[0-3]):([0-5]\d)", (value or "").strip())
    if not found:
        raise ValueError("时间要写成 05:00 这样")
    return int(found.group(1)), int(found.group(2))


def to_cron(kind: str, time: str = "05:00", days: list[int] | None = None, day: int = 1) -> str:
    """The cron for a period; ``manual`` has none (an empty string)."""
    if kind not in KINDS:
        raise ValueError(f"周期只能是 {'、'.join(KINDS)} 之一")
    if kind == "manual":
        return ""
    hour, minute = _time(time)
    head = f"{minute} {hour}"
    if kind == "daily":
        return f"{head} * * *"
    if kind == "weekdays":
        return f"{head} * * 1-5"
    if kind == "weekly":
        picked = sorted(set(days or []))
        if not picked or any(isinstance(d, bool) or not isinstance(d, int) or not 0 <= d <= 6 for d in picked):
            raise ValueError("每周几天要至少选一天（0 是周日，6 是周六）")
        return f"{head} * * {','.join(str(d) for d in picked)}"
    if isinstance(day, bool) or not isinstance(day, int) or not 1 <= day <= 28:
        raise ValueError("每月几号要在 1–28 之间")
    return f"{head} {day} * *"


def from_cron(expression: str) -> dict:
    """``{kind, time, days, day}`` for a cron one of the shapes made, else ``{kind: "custom"}``."""
    fields = (expression or "").split()
    if len(fields) != 5 or not (fields[0].isdigit() and fields[1].isdigit()):
        return {"kind": "custom"}
    minute, hour, dom, month, dow = fields
    when = f"{int(hour):02d}:{int(minute):02d}"
    base = {"time": when, "days": [], "day": 1}
    if month != "*":
        return {"kind": "custom"}
    if dom == "*" and dow == "*":
        return {"kind": "daily", **base}
    if dom == "*" and dow == "1-5":
        return {"kind": "weekdays", **base}
    if dom == "*" and re.fullmatch(r"[0-6](,[0-6])*", dow):
        return {"kind": "weekly", **base, "days": sorted({int(d) for d in dow.split(",")})}
    if dow == "*" and dom.isdigit() and 1 <= int(dom) <= 28:
        return {"kind": "monthly", **base, "day": int(dom)}
    return {"kind": "custom"}


def runs_per_month(expression: str, now: datetime) -> int:
    """How many times it fires in the next 30 days (what one month of it costs, in runs)."""
    if not expression:
        return 0
    entry = cron.parse_schedule(expression)
    end = now + timedelta(days=30)
    return sum(1 for t in cron.fire_times(entry, now, count=62, days=30) if t <= end)
