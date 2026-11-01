"""The collection period of one report, frozen before fetching or writing."""

import re
from dataclasses import replace
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

ZONE = ZoneInfo("Asia/Shanghai")


def freeze(job: dict, now: datetime) -> dict:
    if "collection_days" not in job or job.get("_collection_window"):
        return job
    days = job["collection_days"]
    if type(days) is not int or not 1 <= days <= 31:
        raise ValueError("collection_days 应为 1–31 天")
    end = now.replace(tzinfo=ZONE) if now.tzinfo is None else now.astimezone(ZONE)
    start = end - timedelta(days=days)
    return {
        **job,
        "_collection_window": {
            "days": days,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "timezone": "Asia/Shanghai",
        },
    }


def instruction(window: dict | None) -> str:
    if not window:
        return ""
    return (
        f"\n\n## 本期采集范围\n近 {window['days']} 天：{window['start']} 至 {window['end']}（{window['timezone']}）。"
        "围绕此范围的新发布、更新和案例组织本期材料；联网检索也使用这个时间范围。"
        "当前产品说明、价格与排行榜快照可作解释背景，不把采集日期当成发布日期。"
        "日期不明的材料标明背景，不能说成这周刚发布。研究底稿开头保留本期时间范围，"
        "具体例子、过程和原始素材仍充分保留供写稿使用。"
    )


def date_of(value: str | None):
    if not value:
        return None
    try:
        normalized = re.sub(
            r"([+-]\d{2})(\d{2})$", r"\1:\2", value.replace("Z", "+00:00")
        )
        return datetime.fromisoformat(normalized)
    except ValueError:
        try:
            return parsedate_to_datetime(value)
        except (TypeError, ValueError, OverflowError):
            return None


def select(items: list, window: dict | None, *, reference: bool = False) -> list:
    if not window:
        return items
    start, end = (
        datetime.fromisoformat(window["start"]),
        datetime.fromisoformat(window["end"]),
    )
    dated, background = [], []
    for item in items:
        when = date_of(item.publish_date)
        # Creation dates of models and old docs are not the date of their current capabilities/prices.
        snapshot = reference or item.provider in {"aa", "hf_models", "wb"}
        if when is None or snapshot:
            background.append(
                replace(
                    item,
                    excerpts=["当前参考背景，未据此认定为本期新增。", *item.excerpts],
                )
            )
            continue
        when = (
            when.replace(tzinfo=start.tzinfo)
            if when.tzinfo is None
            else when.astimezone(start.tzinfo)
        )
        inside = (
            start.date() <= when.date() <= end.date()
            if len(item.publish_date or "") == 10
            else start < when <= end
        )
        if inside:
            dated.append(item)
    if window["days"] > 1:
        groups = {}
        for item in dated:
            published = date_of(item.publish_date)
            published = (
                published.replace(tzinfo=start.tzinfo)
                if published.tzinfo is None
                else published.astimezone(start.tzinfo)
            )
            day = published.date().isoformat()
            groups.setdefault(day, []).append(item)
        dated = [
            rows[i]
            for i in range(max((len(v) for v in groups.values()), default=0))
            for rows in groups.values()
            if i < len(rows)
        ]
    return dated + background


def dates(window: dict) -> list[str]:
    start, end = (
        datetime.fromisoformat(window["start"]).date(),
        datetime.fromisoformat(window["end"]).date(),
    )
    return [
        (end - timedelta(days=i)).isoformat() for i in range((end - start).days + 1)
    ]
