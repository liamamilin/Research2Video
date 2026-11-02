"""Saved weekly reviews: each week's report and the numbers behind it, kept as written once they were final.

A week is saved twice: once every video in it is a day old (the 24-hour figures are final) and once every video is a
week old (the 7-day figures are). Each save is two files under ``state/weekly_reviews``: ``<monday>_<stage>.md``, the
report to read, and ``<monday>_<stage>.json``, the review with every video's numbers at that moment. A saved week is
never rewritten, so later changes to columns, names or rules do not alter what a past week was judged to be.
(Not under ``output/``: that folder holds research reports, which production reads.)
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path

#: (stage, label, days after the week starts when it is final)
STAGES = (("24h", "24 小时定稿", 8), ("7d", "7 天定稿", 14))
_NAME = re.compile(r"^(\d{4}-\d{2}-\d{2})_(24h|7d)\.json$")


def _day(start: float) -> str:
    return datetime.fromtimestamp(start).strftime("%Y-%m-%d")


def _num(value) -> str:
    return "—" if value is None else f"{value:g}" if isinstance(value, float) else str(value)


def _table(header: list[str], rows: list[list]) -> list[str]:
    clean = lambda cell: _num(cell).replace("|", "｜").replace("\n", " ")
    return ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)] + ["| " + " | ".join(map(clean, r)) + " |" for r in rows]


def markdown(review: dict, stage: str) -> str:
    """The review as a report a person reads."""
    label = dict((key, name) for key, name, _ in STAGES)[stage]
    s = review["summary"]
    week = f"{_day(review['start'])} 至 {_day(review['end'] - 86400)}"
    share = "—" if s["top_share"] is None else f"{round(s['top_share'] * 100)}%"
    lines = [f"# 每周复盘 {week}（{label}）", "",
             f"生成于 {datetime.fromtimestamp(review['now']).strftime('%Y-%m-%d %H:%M')}。按每条视频发布后 24 小时的播放比较，用中位数；"
             f"一组少于 {review['min_n']} 条只列出不下结论，中位数达到整体 {review['ratio']:g} 倍或一半以下记为明显偏高或偏低。", "",
             "## 概况", "",
             f"- 本周发布 {s['videos']} 条：有 24 小时数据 {s['ok']} 条，未满 24 小时 {s['pending']} 条，采集中断 {s['gap']} 条",
             f"- 24 小时播放中位数 {_num(s['median_24h'])}（上周 {_num(s['previous_median_24h'])}，{s['previous_videos']} 条）",
             f"- 目前总播放 {s['views']}，最高一条占 {share}", "",
             "## 结论", ""]
    lines += [f"- {f}" for f in review["findings"]] or ["- 没有哪一组明显偏离整体。"]
    lines += ["", "## 建议（需要确认后再改配置）", ""]
    lines += [f"- {f}" for f in review["suggestions"]] or ["- 数据还不足以给出连续两周成立的建议。"]
    for dimension in review["dimensions"]:
        flag = {"high": "明显偏高", "low": "明显偏低", "": ""}
        lines += ["", f"## 按{dimension['label']}", ""]
        lines += _table([dimension["label"], "视频", "有 24h 数据", "24h 中位数", "上周中位数", "最高", "判断", "最高的一条"],
                        [[g["value"], g["videos"], g["measured"], g["median"], g["previous_median"], g["max"], flag[g["flag"]], g["best_title"]]
                         for g in dimension["groups"]])
    lines += ["", "## 本周每一条", ""]
    state = {"ok": "", "pending": "未满 24 小时", "gap": "采集中断"}
    lines += _table(["发布", "标题", "栏目", "形式", "24h", "72h", "7 天", "目前", "点赞", "收藏", "备注"],
                    [[datetime.fromtimestamp(v["pubdate"]).strftime("%m-%d %H:%M"), v["title"], v["column"], v["form"], v["h24"], v["h72"],
                      v["h168"], v["view"], (v.get("metrics") or {}).get("like"), (v.get("metrics") or {}).get("favorite"),
                      " ".join(filter(None, [state[v["state"]], v.get("structure") or "", v["bvid"]]))] for v in review["videos"]])
    return "\n".join(lines) + "\n"


class ReviewStore:
    def __init__(self, state_dir: str):
        self.root = Path(state_dir) / "weekly_reviews"

    def _path(self, start: float, stage: str, suffix: str) -> Path:
        return self.root / f"{_day(start)}_{stage}{suffix}"

    def save(self, review: dict, stage: str, now: float) -> bool:
        """Keep ``review`` as its week's ``stage``; False when that week and stage is already kept."""
        self.root.mkdir(parents=True, exist_ok=True)
        data = self._path(review["start"], stage, ".json")
        if data.exists():
            return False
        report = self._path(review["start"], stage, ".md")
        report.write_text(markdown(review, stage), encoding="utf-8")
        body = json.dumps({**review, "stage": stage, "saved_at": now}, ensure_ascii=False, indent=1)
        try:
            # the data file last and exclusively: its presence is what marks the week as kept
            with open(data, "x", encoding="utf-8") as fh:
                fh.write(body)
        except FileExistsError:
            return False
        return True

    def get(self, start: float, stage: str) -> dict | None:
        try:
            return json.loads(self._path(start, stage, ".json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def report_path(self, start: float, stage: str) -> str:
        return str(self._path(start, stage, ".md"))

    def saved(self) -> list[dict]:
        """Every kept week and stage, newest week first: what was saved when, and how many findings it had."""
        out = []
        for name in sorted(os.listdir(self.root), reverse=True) if self.root.is_dir() else []:
            if not _NAME.match(name):
                continue
            try:
                data = json.loads((self.root / name).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            out.append({"start": data["start"], "stage": data["stage"], "saved_at": data["saved_at"], "summary": data["summary"],
                        "findings": len(data["findings"]), "suggestions": len(data["suggestions"])})
        return sorted(out, key=lambda r: (-r["start"], r["saved_at"]))
