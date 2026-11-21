"""OpenCode-backed public topic discovery for matrix themes."""
from __future__ import annotations

import json
import time
from urllib.parse import urlsplit

from . import opencode_cli, opencode_pipeline
from .theme_metrics import BilibiliMetrics
from .video.audience_profiles import EDITORIAL_GUIDANCE


def _build_prompt(payload: dict) -> str:
    header = (
        "你是视频选题编辑与调研助手。根据下方矩阵定位、栏目清单与长期方向，"
        "使用 websearch（必要时辅以 webfetch）发掘近期公开平台与一手资料中值得制作视频的具体问题与主题。\n"
        "按各栏目 category.key 分别提出 1-2 个候选主题；不同栏目不重复同一问题，避开 recent_topics 中近期已推荐或已制作的相近题目。\n"
        "优先寻找真实用户痛点、最新产品或规则变化、值得实测对比的场景或有完整案例支撑的选题；不要编造热度、播放量或增长率。\n"
        "每条主题必须附带 1-3 条真实检索到的公开网页链接（sources），包含 title、url（http/https）、platform 和简短 excerpt。\n"
        "直接输出 JSON 代码块（含 themes 数组，每项含 category, title, angle, reason, relevance, sources）。"
    )
    return f"{header}{EDITORIAL_GUIDANCE}\n\n{json.dumps(payload, ensure_ascii=False, indent=2)}\n"


def discover(engine, matrix, categories: list[dict], config: dict, previous: list[dict],
             feedback: dict, platforms: list[str], day: str, metrics_client: BilibiliMetrics,
             *, platform_names: dict[str, tuple[str, str]], terms_fn, select_fn) -> tuple[list[dict], list[dict], dict]:
    """Run OpenCode discovery and normalize returned items into (signals, candidates, usage)."""
    gather_cfg = opencode_cli.gather_settings(engine.config_dir)
    platform_labels = [platform_names[p][0] for p in platforms if p in platform_names]
    cat_payload = [
        {
            "key": c["key"],
            "name": c["name"],
            "promise": c.get("promise", ""),
            "audience": c.get("audience", ""),
            "suggested_queries": terms_fn(c, engine, matrix, config),
        }
        for c in categories
    ]
    payload = {
        "date": day,
        "matrix": matrix.name,
        "domain": matrix.domain or matrix.name,
        "audience": matrix.content.get("audience", ""),
        "platforms": platform_labels,
        "categories": cat_payload,
        "recent_topics": [r["title"] for r in previous][:60],
        "extra_direction": config.get("direction", ""),
        "feedback": feedback,
    }
    engine._progress({"type": "phase", "phase": "discovering_themes", "category": matrix.name, "platform": "OpenCode"})
    timeout = int(gather_cfg.get("discovery_timeout_seconds") or gather_cfg.get("gather_timeout_seconds") or 240)
    text, meta = opencode_cli.run(
        _build_prompt(payload), config=gather_cfg, timeout=timeout, cancel_token=engine._cancel
    )
    parsed = opencode_pipeline._extract_json_payload(text)
    if isinstance(parsed, list):
        parsed = {"themes": parsed}
    if not isinstance(parsed, dict) or not isinstance(parsed.get("themes"), list):
        raise ValueError("OpenCode 主题发现未返回 themes 列表")

    category_keys = {c["key"] for c in categories}
    signals: list[dict] = []
    normalized_themes: list[dict] = []
    seen_per_cat: dict[str, set[str]] = {}
    for item in parsed["themes"][:100]:
        if not isinstance(item, dict):
            continue
        cat_key = str(item.get("category") or "").strip()
        if cat_key not in category_keys:
            continue
        raw_sources = item.get("sources") if isinstance(item.get("sources"), list) else []
        source_ids: list[str] = []
        cat_seen = seen_per_cat.setdefault(cat_key, set())
        for src in raw_sources[:4]:
            if isinstance(src, str):
                url, title, platform, excerpt = src.strip(), str(item.get("title") or ""), "OpenCode", ""
            elif isinstance(src, dict):
                url = str(src.get("url") or "").strip()
                title = str(src.get("title") or item.get("title") or "").strip()
                platform = str(src.get("platform") or "").strip() or "OpenCode"
                excerpt = str(src.get("excerpt") or "").strip()
            else:
                continue
            if not url.startswith(("http://", "https://")):
                continue
            if url in cat_seen:
                existing = next((s["id"] for s in signals if s["category"] == cat_key and s["url"] == url), None)
                if existing:
                    source_ids.append(existing)
                continue
            cat_seen.add(url)
            host = (urlsplit(url).hostname or "").lower()
            is_bili = host == "bilibili.com" or host.endswith(".bilibili.com")
            measured = metrics_client.collect(url) if is_bili else {"metrics": {}}
            if is_bili and platform == "OpenCode":
                platform = "B站"
            sid = f"s{len(signals) + 1}"
            signals.append({
                "id": sid,
                "category": cat_key,
                "platform": platform,
                "title": title[:200] or url,
                "url": url,
                "published_at": None,
                "excerpt": excerpt[:1200],
                "collected_at": time.time(),
                **measured,
                "signal_kind": "public_metrics" if measured.get("metrics") else "indexed_content",
            })
            source_ids.append(sid)
        if not source_ids:
            continue
        normalized_themes.append({
            "category": cat_key,
            "title": item.get("title"),
            "angle": item.get("angle") or "",
            "reason": item.get("reason"),
            "relevance": item.get("relevance", 80),
            "source_ids": source_ids,
        })
    candidates = select_fn(
        json.dumps({"themes": normalized_themes}, ensure_ascii=False),
        categories, signals, audience_first=True,
    )
    oc_usage: dict = {"opencode_calls": 1, "engine": "opencode"}
    for k, v in (meta.get("usage") or {}).items():
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            oc_usage[k] = v
    return signals, candidates, oc_usage
