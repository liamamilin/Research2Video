"""B站 heat of a topic: how much last week's B站 videos about its product were watched.

The 11-10 benchmark (spec/benchmark-20261110/ai-report.md) found our AI videos chose long-tail developer news
while viewers watched the week's big names. A production with ``selection.heat_min`` keeps only the report items
whose product clears that bar, hottest first; the rest are not made (the user's choice, 11-10).

The judgement is the program's: the model only names the product viewers would search for; the score is the sum of
the plays of the top videos whose title contains that name, published in the last ``WINDOW_DAYS`` days, leaving out
games, anime, music and clips under ``MIN_SECONDS``. Searching needs the publishing account's login (anonymous
searches are refused), so it is capped per day, cached per keyword and day, and spaced. When B站 refuses, times out
or the cap is reached, the items are kept in their original order and the note says why: heat never stops a video.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
import logging
from pathlib import Path
import re
import time
from typing import Any, Callable, Optional
import urllib.parse

from core.fileio import atomic_write, file_lock

logger = logging.getLogger(__name__)

WINDOW_DAYS = 7
MIN_SECONDS = 90
TOP = 5
DAILY_SEARCHES = 40
BUDGET_SECONDS = 120.0
SPACING = 1.5
EXCLUDED_TYPES = frozenset({
    "单机游戏", "网络游戏", "手机游戏", "电子竞技", "音游", "桌游棋牌", "GMV", "MAD·AMV", "同人·手书", "动画综合",
    "鬼畜剧场", "鬼畜调教", "音MAD", "人力VOCALOID", "原创音乐", "翻唱", "音乐综合", "MV", "演奏", "VOCALOID·UTAU",
    "舞蹈综合", "宅舞", "仿妆cos", "影视剪辑", "短片", "配音"})
_MIX = [46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39,
        12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57,
        62, 11, 36, 20, 34, 44, 52]
_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
       "Chrome/130.0 Safari/537.36")
KEYWORD_PROMPT = (
    "下面是几条 AI 资讯。为每条给出 B站观众最可能用来搜这件事的一个词：这一条里具体产品、功能或套餐的中文观众常用叫法，"
    "要具体到这一条：不能只写公司或总品牌（单独的 Claude、OpenAI、ChatGPT、Google、腾讯、GitHub 不行，"
    "要写成 Claude Code、ChatGPT Pro、腾讯混元、GitHub Copilot 这样）；用“腾讯混元”而不是“Hy”；"
    "不带版本号、参数和动作（“Claude Fable 5.2 降价”→“Claude Fable”，“Kimi K3.1 Pro 发布”→“Kimi”）。"
    "只用标题和摘要里出现的名字；没有具体产品就写空字符串。"
    '输出 JSON：{"items":[{"id":"","keyword":""}]}')


class Unavailable(RuntimeError):
    """B站 would not answer a search now (refused, rate-limited, timed out or not logged in)."""


def _clean(text: str) -> str:
    return re.sub(r"\s+", "", re.sub(r"<[^>]+>", "", str(text or ""))).lower()


def _seconds(value: Any) -> int:
    if isinstance(value, (int, float)):
        return int(value)
    total = 0
    for part in str(value or "0").split(":"):
        total = total * 60 + int(part or 0)
    return total


def score(rows: list[dict], keyword: str) -> dict:
    """Sum of the top ``TOP`` plays among the results that are about ``keyword`` (every word of it in the title: "GPT Astra"
    matches "GPT-6.2-Astra实测") and are not games, anime or clips."""
    words = [_clean(w) for w in re.split(r"[\s\-_·/]+", str(keyword or "")) if _clean(w)]
    about = sorted((row for row in rows
                    if words and all(w in _clean(row.get("title")) for w in words)
                    and row.get("typename") not in EXCLUDED_TYPES and _seconds(row.get("duration")) >= MIN_SECONDS),
                   key=lambda row: -int(row.get("play") or 0))
    plays = [int(row.get("play") or 0) for row in about]
    top = [{"title": re.sub(r"<[^>]+>", "", str(row.get("title") or "")), "bvid": str(row.get("bvid") or ""),
            "play": int(row.get("play") or 0)} for row in about[:3]]
    return {"score": sum(plays[:TOP]), "videos": len(plays), "top": top}


def title_head(title: str) -> str:
    """A fallback keyword: the title up to its first colon or comma."""
    return re.split(r"[：:，,（(]", str(title or ""), maxsplit=1)[0].strip()[:30]


class Search:
    """WBI-signed B站 video search with the publishing account's cookies; nothing but the search call is logged in."""

    def __init__(self, state_dir: str, timeout: float = 15):
        path = Path(state_dir) / "platforms" / "bilibili" / "credentials.json"
        try:
            cookies = json.loads(path.read_text())["cookie_info"]["cookies"]
            self.cookies = {c["name"]: c["value"] for c in cookies}
        except (OSError, ValueError, KeyError, TypeError):
            self.cookies = {}
        self.timeout = timeout
        self._mixin = ""

    def _get(self, url: str, params: dict) -> dict:
        import httpx

        headers = {"User-Agent": _UA, "Referer": "https://www.bilibili.com/"}
        try:
            with httpx.Client(headers=headers, cookies=self.cookies, timeout=self.timeout) as client:
                response = client.get(url, params=params)
                response.raise_for_status()
                return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise Unavailable(f"B站连接失败（{type(exc).__name__}）") from exc

    def _sign(self, params: dict) -> dict:
        if not self._mixin:
            data = self._get("https://api.bilibili.com/x/web-interface/nav", {}).get("data") or {}
            image = data.get("wbi_img") or {}
            raw = "".join(image.get(key, "").rsplit("/", 1)[-1].split(".")[0] for key in ("img_url", "sub_url"))
            if len(raw) < 64:
                raise Unavailable("B站签名参数取不到")
            self._mixin = "".join(raw[i] for i in _MIX)[:32]
        signed = {**params, "wts": int(time.time())}
        clean = {k: "".join(c for c in str(v) if c not in "!'()*") for k, v in sorted(signed.items())}
        clean["w_rid"] = hashlib.md5((urllib.parse.urlencode(clean) + self._mixin).encode()).hexdigest()
        return clean

    def trending(self, limit: int = 50) -> list[str]:
        """B站热搜 words, in rank order (no login needed)."""
        reply = self._get("https://api.bilibili.com/x/web-interface/wbi/search/square", self._sign({"limit": limit}))
        rows = ((reply.get("data") or {}).get("trending") or {}).get("list") or []
        if reply.get("code") != 0:
            raise Unavailable(f"B站热搜取不到（{reply.get('code')}）")
        return [str(row.get("keyword") or "").strip() for row in rows if str(row.get("keyword") or "").strip()]

    def videos(self, keyword: str, begin: int, end: int) -> list[dict]:
        if not self.cookies.get("SESSDATA"):
            raise Unavailable("B站账号未登录，搜索会被拦")
        reply = self._get("https://api.bilibili.com/x/web-interface/wbi/search/type", self._sign(
            {"search_type": "video", "keyword": keyword, "page": 1, "order": "click",
             "pubtime_begin_s": begin, "pubtime_end_s": end}))
        data = reply.get("data") or {}
        if reply.get("code") != 0 or ("result" not in data and data.get("v_voucher")):
            raise Unavailable(f"B站拒绝了搜索（{reply.get('code')}）")
        return data.get("result") or []


class Heat:
    """Daily state in ``state/video_heat.json``: searches used, why B站 is off for the day, scores and keywords."""

    def __init__(self, state_dir: str, *, search: Optional[Search] = None, clock: Callable[[], float] = time.time,
                 sleeper: Callable[[float], None] = time.sleep, spacing: float = SPACING,
                 budget: float = BUDGET_SECONDS, daily: int = DAILY_SEARCHES):
        self.path = Path(state_dir) / "video_heat.json"
        self.search = search if search is not None else Search(state_dir)
        self.clock, self.sleep = clock, sleeper
        self.spacing, self.budget, self.daily = spacing, budget, daily

    def _day(self) -> str:
        return datetime.fromtimestamp(self.clock()).strftime("%Y-%m-%d")

    def _load(self) -> dict:
        try:
            state = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            state = {}
        if state.get("day") != self._day():
            state = {"day": self._day(), "searches": 0, "blocked": "", "scores": {},
                     "keywords": state.get("keywords", {})}
        return state

    def _save(self, state: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(str(self.path), json.dumps(state, ensure_ascii=False, indent=1))

    def keywords(self, candidates: list[dict], llm_config) -> dict[str, str]:
        """The search word per candidate id: the model's, remembered per id; the title head when the model fails."""
        with file_lock(str(self.path), timeout=30):
            known = self._load().get("keywords", {})
        missing = [c for c in candidates if c["id"] not in known]
        found: dict[str, str] = {}
        if missing and llm_config is not None:
            try:
                from core.llm import LLMClient
                ask = [{"id": c["id"], "title": c.get("title", ""),
                        "summary": str((c.get("fields") or {}).get("一句话", ""))[:160]} for c in missing]
                reply = LLMClient(llm_config).chat([{"role": "system", "content": KEYWORD_PROMPT},
                                                    {"role": "user", "content": json.dumps(ask, ensure_ascii=False)}],
                                                   temperature=0)
                raw = reply.content
                for row in json.loads(raw[raw.find("{"):raw.rfind("}") + 1]).get("items", []):
                    if isinstance(row, dict) and row.get("id") in {c["id"] for c in missing}:
                        found[row["id"]] = str(row.get("keyword") or "").strip()[:30]
            except Exception as exc:  # noqa: BLE001 - the title head still names the product most of the time
                logger.warning("heat keywords failed (%s)", type(exc).__name__)
        for c in missing:
            found.setdefault(c["id"], title_head(c.get("title", "")))
        if found:
            with file_lock(str(self.path), timeout=30):
                state = self._load()
                state["keywords"] = {**state.get("keywords", {}), **found}
                self._save(state)
        return {c["id"]: {**known, **found}.get(c["id"], "") for c in candidates}

    def scores(self, keywords: list[str]) -> tuple[dict[str, dict], str]:
        """Scores for ``keywords`` (cached for the day) and, when some could not be had, why."""
        started, out, why = self.clock(), {}, ""
        for keyword in dict.fromkeys(k for k in keywords if k):
            with file_lock(str(self.path), timeout=30):
                state = self._load()
            if keyword in state["scores"]:
                out[keyword] = state["scores"][keyword]
                continue
            if state["blocked"]:
                why = state["blocked"]
                break
            if state["searches"] >= self.daily:
                why = f"今天的 B站搜索次数已用完（{self.daily} 次）"
                break
            if self.clock() - started > self.budget:
                why = "取热度超时"
                break
            end = int(self.clock())
            try:
                rows = self.search.videos(keyword, end - WINDOW_DAYS * 86400, end)
                value: Optional[dict] = score(rows, keyword)
                blocked = ""
            except Unavailable as exc:
                value, blocked = None, str(exc)
            with file_lock(str(self.path), timeout=30):
                state = self._load()
                state["searches"] += 1
                if value is None:
                    state["blocked"] = blocked
                else:
                    state["scores"][keyword] = value
                self._save(state)
            if value is None:
                why = blocked
                break
            out[keyword] = value
            self.sleep(self.spacing)
        return out, why

    def rank(self, candidates: list[dict], minimum: int, llm_config) -> tuple[list[dict], list[dict], str]:
        """(kept hottest first, dropped below ``minimum``, note). Unscored candidates are kept, after the scored ones."""
        if not candidates:
            return [], [], ""
        words = self.keywords(candidates, llm_config)
        scored, why = self.scores(list(words.values()))
        kept, dropped, unscored = [], [], []
        for candidate in candidates:
            keyword = words.get(candidate["id"], "")
            if not keyword:
                dropped.append({**candidate, "heat": {"keyword": "", "score": 0, "videos": 0}})
                continue
            if keyword not in scored:
                unscored.append(candidate)
                continue
            value = {"keyword": keyword, "score": scored[keyword]["score"], "videos": scored[keyword]["videos"]}
            (kept if value["score"] >= minimum else dropped).append({**candidate, "heat": value})
        kept.sort(key=lambda c: -c["heat"]["score"])
        note = ""
        if dropped:
            note = (f"热度不够（B站近 {WINDOW_DAYS} 天不足 {minimum} 播放），少做 {len(dropped)} 条："
                    + "、".join(f"{c.get('title', '')[:16]}（{c['heat']['keyword'] or '无产品名'} {c['heat']['score']}）"
                               for c in dropped))
        if unscored:
            note = "；".join(filter(None, [note, f"{why or '热度未取到'}，{len(unscored)} 条按原顺序保留"]))
        return kept + unscored, dropped, note
