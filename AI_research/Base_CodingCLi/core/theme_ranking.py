"""Explainable recommendation scores based on observed counters, age and topic relevance."""
from __future__ import annotations

import math
import time
from datetime import datetime


def published(signal: dict) -> float | None:
    value = signal.get("video_published_at")
    if isinstance(value, (int, float)) and value > 0:
        return float(value)
    try:
        return datetime.fromisoformat(str(signal.get("published_at") or "").replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def heat(signal: dict, now: float) -> dict | None:
    metrics = signal.get("metrics") or {}
    views = metrics.get("view")
    if type(views) is not int or views < 0:
        return None
    age = max(1, (now - published(signal)) / 86400) if published(signal) else None
    growth = signal.get("growth") or {}
    speed = growth.get("views_per_day")
    kind = "observed_growth" if isinstance(speed, (int, float)) else "lifetime_average" if age else "unavailable"
    if kind == "lifetime_average":
        speed = views / age
    reach = min(1, math.log10(views + 1) / 6)
    momentum = min(1, math.log10(max(0, speed or 0) + 1) / 4)
    # Shrink small-sample rates; one like on one view is not a viral signal.
    engagement = min(1, (metrics.get("like", 0) + 2 * metrics.get("favorite", 0)) / max(views, 500) / 0.15)
    freshness = math.exp(-age / 120) if age else 0
    score = 100 * (0.35 * reach + 0.30 * momentum + 0.25 * engagement + 0.10 * freshness)
    if signal.get("metrics_status") == "stale":
        score *= 0.6
    return {"heat_score": round(score, 1), "metrics": metrics, "signal_id": signal["id"],
            "observed_at": signal.get("metrics_observed_at"), "status": signal.get("metrics_status", "available"),
            "views_per_day": round(speed, 1) if speed is not None else None, "speed_kind": kind,
            "age_days": round(age, 1) if age is not None else None}


def rank(signals: list[dict], relevance=70, *, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    relevance = max(0, min(100, relevance)) if type(relevance) in (int, float) else 70
    seen, observed = set(), []
    for signal in signals:
        identity = signal.get("bvid") or signal.get("url") or signal["id"]
        if identity in seen:
            continue
        seen.add(identity)
        value = heat(signal, now)
        if value:
            observed.append(value)
    best = max(observed, key=lambda x: x["heat_score"]) if observed else None
    return {"version": 1, "score": round(0.7 * best["heat_score"] + 0.3 * relevance, 1) if best else round(relevance * 0.25, 1),
            "relevance": relevance, "measured_signals": len(observed), "primary": best,
            "basis": "真实计数与主题相关性" if best else "内容相关性，未取得热度数据",
            "formula": "播放规模35% · 播放速度30% · 互动25% · 新近度10%；综合分另结合主题相关性"}
