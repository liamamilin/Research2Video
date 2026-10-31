"""Class-balanced public topic discovery, executed as an ordinary scheduled research Job."""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from . import matrices, theme_context, theme_ranking
from .errors import CancelledError
from .fileio import atomic_write
from .llm import LLMClient, LLMConfig
from .lock import LockManager
from .search import SearchClient, SearchConfig
from .state import StateManager
from .theme_store import ThemeStore, similar
from .theme_metrics import BilibiliMetrics
from .theme_library import ThemeLibrary

logger = logging.getLogger(__name__)
PLATFORMS = {"bilibili": ("B站", "bilibili.com"), "zhihu": ("知乎", "zhihu.com"),
             "youtube": ("YouTube", "youtube.com"), "xiaohongshu": ("小红书", "xiaohongshu.com"),
             "reddit": ("Reddit", "reddit.com")}


def configuration(config_dir: str, key: str) -> dict:
    path = Path(config_dir) / "topic_discovery" / f"{key}.yaml"
    value = yaml.safe_load(path.read_text()) if path.exists() else {}
    return {"time": "09:00", "timezone": "Asia/Shanghai", "mode": "ideas", "direction": "",
            "platforms": ["bilibili", "zhihu", "youtube"],
            **(value or {})}


def public_metrics(url: str, state_dir: str = "state") -> dict:
    return BilibiliMetrics(state_dir).collect(url)["metrics"]


def search_terms(category: dict, engine, matrix, config: dict) -> list[str]:
    from .config import load_job
    from .video import production
    configured = (config.get("queries") or {}).get(category["key"])
    if isinstance(configured, list):
        queries = [q.strip()[:120] for q in configured if isinstance(q, str) and q.strip()][:2]
        if queries:
            return queries
    if category.get("keywords"):
        return [str(q)[:120] for q in category["keywords"][:2]]
    bindings = production.load_all(engine.config_dir, engine.jobs_dir)
    for key in category["production_ids"]:
        if key in bindings:
            keywords = (load_job(engine.jobs_dir, bindings[key].job) or {}).get("keywords") or []
            if keywords:
                return [str(q)[:120] for q in keywords[:2]]
    return [f"{(matrix.keywords or [matrix.domain or matrix.name])[0]} {category['name']}"[:120]]


def select(response: str, categories: list[dict], signals: list[dict]) -> list[dict]:
    text = response.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    data = json.loads(text)
    if not isinstance(data, dict) or not isinstance(data.get("themes"), list):
        raise ValueError("主题发现未返回 themes 列表")
    category_map, signal_map = {c["key"]: c for c in categories}, {s["id"]: s for s in signals}
    candidates = []
    for item in data["themes"][:100]:
        if not isinstance(item, dict):
            continue
        key = item.get("category")
        if key not in category_map:
            continue
        title, reason = item.get("title"), item.get("reason")
        if not isinstance(title, str) or not 4 <= len(title.strip()) <= 180 or not isinstance(reason, str) or not reason.strip():
            continue
        refs = item.get("source_ids")
        refs = refs if isinstance(refs, list) else []
        actual = [signal_map[sid] for sid in refs if isinstance(sid, str) and sid in signal_map
                  and signal_map[sid]["category"] == key]
        if not actual:
            continue
        if type(item.get("relevance")) in (int, float) and item["relevance"] < 60:
            continue
        # Counters, URLs and collection times come from collectors, never from the model reply.
        candidates.append({"category": key, "title": title.strip(), "reason": reason.strip()[:1500],
                       "angle": str(item.get("angle") or "")[:1000],
                       "audience": category_map[key]["audience"], "signals": actual,
                       "ranking": theme_ranking.rank(actual, item.get("relevance", 70))})
    chosen, occupied = [], set()
    for item in sorted(candidates, key=lambda c: c["ranking"]["score"], reverse=True):
        if item["category"] in occupied or any(similar(item["title"], old["title"]) for old in chosen):
            continue
        occupied.add(item["category"])
        chosen.append(item)
    return chosen


def run(engine, job: dict) -> str | None:
    key = str(job["topic_discovery"])
    config = configuration(engine.config_dir, key)
    if config["mode"] == "ideas" and not job.get("platform_discovery_only"):
        from .theme_ideas import run_daily
        return run_daily(engine, job, config)
    day = datetime.now(ZoneInfo(config["timezone"])).date().isoformat()
    state_dir = Path(engine.workspace_dir) / "state"
    storage = ThemeStore(str(state_dir))
    canonical = job.get("_file") or f"topic_discovery/{key}"
    run_id, llm = None, None
    started = time.time()
    usage = {"searches": 0}
    try:
        with LockManager(canonical, stale_after=2700):
            matrix = matrices.load_all(engine.config_dir).get(key)
            if matrix is None or matrix.archived:
                raise ValueError("矩阵不存在或已归档")
            categories = theme_context.categories(engine.config_dir, key)
            run_id = storage.begin_run(key, day)
            client = SearchClient(SearchConfig.from_system(engine.sys), cancel_token=engine._cancel,
                                  progress_cb=engine._progress)
            signals, failures = [], {}
            selected = config["platforms"] or (["bilibili", "zhihu", "youtube"] if job.get("platform_discovery_only") else [])
            platforms = [p for p in selected if p in PLATFORMS]
            if not platforms:
                raise ValueError("请选择至少一个支持的公开平台")
            metrics_client = BilibiliMetrics(str(state_dir))
            rotation = datetime.fromisoformat(day).toordinal()
            for index, category in enumerate(categories):
                if engine._cancel.is_set():
                    raise CancelledError("主题发现已取消")
                # Enrich the existing discovery cadence without multiplying paid searches.
                selected_platforms = [platforms[(rotation + index) % len(platforms)]]
                terms = search_terms(category, engine, matrix, config)
                terms = [terms[(rotation + index) % len(terms)]]
                for platform in selected_platforms:
                    label, domain = PLATFORMS[platform]
                    engine._progress({"type": "phase", "phase": "discovering_themes", "category": category["name"], "platform": label})
                    try:
                        results = client.search([f"site:{domain} {q}" for q in terms], objective=f"寻找{category['promise']}的相关公开视频与真实需求，兼顾近期作品和有较多关注的作品")
                    except CancelledError:
                        raise
                    except Exception as exc:
                        failures[category["key"] + ":" + platform] = str(exc)[:300]
                        continue
                    from urllib.parse import urlsplit
                    seen = set()
                    limit = 6 if platform == "bilibili" else 4
                    for result in results:
                        host = urlsplit(result.url).hostname or ""
                        if not (host == domain or host.endswith("." + domain)) or result.url in seen:
                            continue
                        seen.add(result.url)
                        measured = metrics_client.collect(result.url) if platform == "bilibili" else {"metrics": {}}
                        signals.append({"id": f"s{len(signals) + 1}", "category": category["key"], "platform": label,
                                        "title": result.title, "url": result.url, "published_at": result.publish_date,
                                        "excerpt": "\n".join(result.excerpts)[:1200], "collected_at": time.time(),
                                        **measured, "signal_kind": "public_metrics" if measured["metrics"] else "indexed_content"})
                        if len(seen) >= limit:
                            break
            usage["searches"] = client.calls
            if not signals:
                raise ValueError("本轮没有取得可用平台线索；保留原有推荐")
            ThemeLibrary(str(state_dir)).ingest(key, signals)
            llm = LLMClient(LLMConfig.from_system(engine.sys, "ai"), cancel_token=engine._cancel,
                            progress_cb=engine._progress)
            previous = [r for r in storage.exclusions(key) if not (r["origin"] == "discovered" and r["day"] == day
                        and r["status"] == "recommended" and not r["script_id"] and not r.get("edited"))]
            response = llm.chat([
                {"role": "system", "content": (
                    "你是视频选题编辑。按提供的矩阵栏目分类，从公开内容线索提炼值得讲给目标观众的问题。"
                    "每类提出最多三个不同的候选问题，最终程序会结合真实计数选每类一条；不同类不重复同一问题。"
                    "栏目交付目标决定角度，不能把所有栏目都变成新闻。避开已推荐和已制作的相近主题。"
                    "推荐理由说明观众问题及具体线索，使用自然语言，不出现s1等内部编号。不要编造热度、播放量、增长率或用户规模。"
                    "提供relevance为0至100的栏目与观众相关性分值，避免用高播放但不相关的作品凑题。"
                    "indexed_content 只有搜索索引线索，不能称为流量榜；public_metrics 的数字仅适用于对应作品。"
                    "输出 JSON 对象 {themes:[{category,title,angle,reason,relevance,source_ids:[]}]}；source_ids只能引用本栏目提供的ID。")},
                {"role": "user", "content": json.dumps({"matrix": matrix.name, "audience": matrix.content.get("audience", ""),
                    "categories": categories, "recent_topics": [r["title"] for r in previous],
                    "extra_direction": config.get("direction", ""), "feedback": storage.feedback_profile(key),
                    "signals": signals}, ensure_ascii=False)},
            ], tools=None)
            usage.update(llm.usage)
            candidates = select(response.content, categories, signals)
            if engine._cancel.is_set():
                raise CancelledError("主题发现已取消")
            made = storage.publish(key, day, candidates, refresh=True)
            result = {"recommended": len(made), "categories": len(categories), "signals": len(signals),
                      "empty_categories": [c["key"] for c in categories if c["key"] not in {r["category"] for r in candidates}],
                      "failures": failures, "usage": usage,
                      "metrics": {"measured_signals": sum(bool(s["metrics"]) for s in signals),
                                  "unique_videos": len({s.get("bvid") for s in signals if s["metrics"]}),
                                  "requests": metrics_client.requests, "cache_hits": metrics_client.hits, "error": metrics_client.blocked},
                      "category_coverage": {c["key"]: {"signals": sum(s["category"] == c["key"] for s in signals),
                          "measured": sum(s["category"] == c["key"] and bool(s["metrics"]) for s in signals)} for c in categories}}
            folder = Path(engine.workspace_dir) / "output/topic_discovery" / key
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / f"{day}_{run_id[:8]}.md"
            text = f"# {matrix.name} · 每日主题推荐\n\n日期：{day}\n\n"
            for row in made:
                text += f"## {row['title']}\n\n分类：{row['category']}\n\n{row['reason']}\n\n"
                text += "\n".join(f"- [{s['title']}]({s['url']})" for s in row["signals"]) + "\n\n"
            if not made:
                text += "本轮没有新增的合适主题，已有推荐保留。\n"
            atomic_write(str(path), text)
            atomic_write(str(path.with_suffix(".signals.json")), json.dumps(signals, ensure_ascii=False, indent=2))
            storage.finish_run(run_id, "success", result)
            reference = str(path.relative_to(engine.workspace_dir))
            StateManager.update(canonical, "success", output_path=reference, usage=usage,
                                duration_seconds=round(time.time() - started, 1), user=engine.user)
            return reference
    except Exception as exc:
        status = "cancelled" if isinstance(exc, CancelledError) else "failed"
        if run_id:
            storage.finish_run(run_id, status, {"usage": usage}, str(exc)[:1000])
        StateManager.update(canonical, status, error=str(exc)[:1000], usage=usage, user=engine.user)
        logger.warning("Theme discovery %s: %s", key, exc)
        return None
    finally:
        if llm:
            llm.close()
