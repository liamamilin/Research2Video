"""Video topics from the intelligence pipeline's content-opportunity stage (P7).

Each round ends with ``07_product_content_opportunities.md``: numbered topics
(``### C1. …``) written for this creator's audience, each with an audience,
pain point, angle, why-it-matters with sources, suggested title and outline,
and a 1–10 score. A topic's id is ``<round date>/<code>``, stable for the
round, so a script can say which topic it came from.
"""

from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass, field

PIPELINE_DIR = "practical_ai_intelligence"
CONTENT_FILE = "07_product_content_opportunities.md"

_TOPIC = re.compile(r"^#{2,4}\s*(C\d+)\s*[.．、:：]?\s*(.+?)\s*$")
_FIELD = re.compile(r"^\s*[-*]\s*\*\*(.+?)\*\*\s*[:：]\s*(.*)$")
_SCORE = re.compile(r"(\d+(?:\.\d+)?)\s*/\s*10")
_HEADING = re.compile(r"^#{1,4}\s")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass
class Topic:
    id: str
    round: str
    code: str
    title: str
    fields: dict[str, str] = field(default_factory=dict)

    @property
    def score(self) -> float | None:
        match = _SCORE.search(self.fields.get("评分", ""))
        return float(match.group(1)) if match else None

    def as_dict(self) -> dict:
        return {**asdict(self), "score": self.score}


def parse(report: str, round_date: str) -> list[Topic]:
    """Topics (``C<n>`` headings) in the order the report lists them."""
    topics: list[Topic] = []
    current: Topic | None = None
    for line in report.splitlines():
        heading = _TOPIC.match(line)
        if heading:
            if current is not None:
                topics.append(current)
            code = heading.group(1)
            current = Topic(id=f"{round_date}/{code}", round=round_date, code=code,
                            title=_plain(heading.group(2)))
            continue
        if current is None:
            continue
        if _HEADING.match(line):  # any other heading ends the topic
            topics.append(current)
            current = None
            continue
        match = _FIELD.match(line)
        if match:
            # Bold markers would reach the research prompt verbatim.
            current.fields[_plain(match.group(1))] = match.group(2).replace("**", "").strip()
    if current is not None:
        topics.append(current)
    return topics


def _plain(text: str) -> str:
    return text.replace("**", "").replace("__", "").strip()


def latest_round(output_dir: str) -> str | None:
    """Newest round that has a content-opportunity report."""
    base = os.path.join(output_dir, PIPELINE_DIR)
    if not os.path.isdir(base):
        return None
    for date in sorted((d for d in os.listdir(base) if _DATE.match(d)), reverse=True):
        if os.path.isfile(os.path.join(base, date, CONTENT_FILE)):
            return date
    return None


def load(output_dir: str, round_date: str | None = None) -> tuple[str | None, list[Topic]]:
    """The round's topics (the latest round when none is given)."""
    round_date = round_date or latest_round(output_dir)
    if round_date is None:
        return None, []
    path = os.path.join(output_dir, PIPELINE_DIR, round_date, CONTENT_FILE)
    try:
        with open(path, encoding="utf-8") as fh:
            return round_date, parse(fh.read(), round_date)
    except OSError:
        return round_date, []


def find(output_dir: str, topic_id: str) -> Topic | None:
    round_date, _, code = topic_id.partition("/")
    if not _DATE.match(round_date):
        return None
    if re.fullmatch(r"F\d+", code):  # a flash-news item, see flash.py
        from . import flash
        _, topics = flash.load(output_dir, round_date)
    else:
        _, topics = load(output_dir, round_date)
    return next((t for t in topics if t.id == topic_id), None)
