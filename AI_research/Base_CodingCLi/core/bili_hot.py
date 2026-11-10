"""``bili_hot:<word>,<word>,...``: what B站 viewers are watching this week, as source material for a Job.

The AI flash Job reads English official feeds, so the week's Chinese hot topics (豆包, DeepSeek, 腾讯混元...) seldom
became items, and the heat bar (core/video/heat.py) then had few items left to make. This source adds them:

* the AI words of B站热搜 (``HOT_TERMS``), up to ``PICK_TRENDING``;
* the watch words given in the spec, scored with the heat module (last week's top plays), the hottest ``PICK_WORDS``
  at or above ``MIN_HEAT``.

For each chosen word it gives one item naming the word, its heat and the most-played B站 videos (what viewers are
watching), then the web search's latest news on it, so the research can write the change itself. The program picks
the words; nothing here asks a model. Searches count against the heat module's daily cap; any failure gives what was
found (possibly nothing), never an error that stops the Job's other sources.
"""
from __future__ import annotations

from datetime import datetime
import logging
import re
from typing import Optional
import urllib.parse

from .search import SearchClient, SearchResult

logger = logging.getLogger(__name__)

HOT_TERMS = re.compile(r"AI|人工智能|大模型|智能体|GPT|OpenAI|Claude|DeepSeek|豆包|千问|通义|Kimi|混元|Gemini|Grok|"
                       r"文心|智谱|MiniMax|Codex|Cursor|Agent", re.I)
PICK_TRENDING = 3
PICK_WORDS = 3
MIN_HEAT = 100000
NEWS_PER_WORD = 3


def _state_dir() -> str:
    try:
        from web.settings import get_settings
        return get_settings().paths.state_dir
    except Exception:  # noqa: BLE001 - run outside the web app: the workspace's own state folder
        return "state"


def words(target: str) -> list[str]:
    return [w.strip() for w in re.split(r"[,，]", target or "") if w.strip()]


def choose(trending: list[str], scored: dict[str, dict], *, picks: int = PICK_WORDS,
           trending_picks: int = PICK_TRENDING, minimum: int = MIN_HEAT) -> list[dict]:
    """The words to bring in: AI words of 热搜 first (in rank), then the hottest watch words that clear ``minimum``."""
    chosen = [{"word": w, "why": "B站热搜"} for w in trending if HOT_TERMS.search(w)][:trending_picks]
    hot = sorted(((w, v) for w, v in scored.items() if v.get("score", 0) >= minimum), key=lambda x: -x[1]["score"])
    chosen += [{"word": w, "why": "近 7 天热度", "heat": v} for w, v in hot[:picks]]
    return chosen


def item(choice: dict, day: str) -> SearchResult:
    word, value = choice["word"], choice.get("heat") or {}
    lines = [f"这是 B站观众本周关注的话题（来源：{choice['why']}），不是新闻正文。"]
    if value:
        lines.append(f"B站近 7 天标题含「{word}」的视频，播放最高的 5 条合计约 {value['score']} 次。")
    if value.get("top"):
        lines.append("播放最高的视频：" + "；".join(f"《{v['title']}》{v['play']} 次" for v in value["top"]))
    lines.append("后面的网页材料里如有它这周的实质新进展，可以单独成条；没有就不要硬写。")
    url = f"https://search.bilibili.com/all?keyword={urllib.parse.quote(word)}#{day}"
    return SearchResult(title=f"B站热词（{day}）：{word}", url=url, publish_date=day, excerpts=["\n".join(lines)],
                        provider="bili_hot")


def fetch(target: str, limit: int = 8, timeout: int = 20, *, search_client: Optional[SearchClient] = None,
          heat_state=None) -> list[SearchResult]:
    """Items for the chosen hot words, each followed by its latest web news when a search client is given."""
    from core.video import heat

    state = heat_state or heat.Heat(_state_dir())
    day = datetime.fromtimestamp(state.clock()).strftime("%Y-%m-%d")
    try:
        trending = state.search.trending()
    except Exception as exc:  # noqa: BLE001 - 热搜 is one of two signals
        logger.warning("bili_hot: trending unavailable (%s)", exc)
        trending = []
    scored, why = state.scores(words(target))
    if why:
        logger.info("bili_hot: some watch words unscored: %s", why)
    out: list[SearchResult] = []
    for choice in choose(trending, scored):
        out.append(item(choice, day))
        if search_client is None:
            continue
        try:
            news = search_client.search([f"{choice['word']} 最新消息"],
                                        objective=f"找到「{choice['word']}」最近一周的新发布、新功能、价格或重要变化的原始报道")
        except Exception as exc:  # noqa: BLE001 - the B站 item alone still says what viewers watch
            logger.warning("bili_hot: news search for %s failed (%s)", choice["word"], type(exc).__name__)
            continue
        out.extend(news[:NEWS_PER_WORD])
    return out[:max(limit, 1) * (1 + NEWS_PER_WORD)]
