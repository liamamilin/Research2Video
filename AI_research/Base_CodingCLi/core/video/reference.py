"""Reference videos: other people's videos on a topic, found by web search. Read-only.

Nothing is downloaded or reposted; this returns titles and links (B 站, YouTube) so the user can look at how others
covered a topic. It costs one web search run."""

from __future__ import annotations

import re
from urllib.parse import urlparse

#: (host suffix, path pattern that is a single video rather than a channel or a search page, platform)
VIDEO_PAGES = (
    ("bilibili.com", re.compile(r"^/video/(BV\w+|av\d+)", re.I), "B 站"),
    ("youtube.com", re.compile(r"^/watch"), "YouTube"),
    ("youtu.be", re.compile(r"^/[\w-]{6,}"), "YouTube"),
)


def platform_of(url: str) -> str | None:
    """'B 站' / 'YouTube' when ``url`` is one video's page, else None."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    for suffix, pattern, name in VIDEO_PAGES:
        if (host == suffix or host.endswith("." + suffix)) and pattern.search(parsed.path):
            return name
    return None


def queries(topic: str) -> list[str]:
    topic = " ".join(topic.split())[:120]
    return [f"{topic} B站 视频", f"site:bilibili.com/video {topic}", f"{topic} youtube video"]


def find(client, topic: str, limit: int = 8) -> list[dict]:
    """Up to ``limit`` single-video pages for ``topic``, best-ranked first; [] when the search finds none."""
    out: list[dict] = []
    seen: set[str] = set()
    for result in client.search(queries(topic), objective=f"找关于「{topic}」的视频页面"):
        platform = platform_of(result.url)
        if platform is None or result.url in seen:
            continue
        seen.add(result.url)
        snippet = " ".join((result.excerpts or [""])[0].split())[:160]
        out.append({"title": (result.title or result.url)[:120], "url": result.url, "platform": platform,
                    "date": result.publish_date or "", "snippet": snippet})
        if len(out) >= limit:
            break
    return out
