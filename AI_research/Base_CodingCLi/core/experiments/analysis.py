"""The analysis engine (spec §8): pure functions over plain numbers, standard library only.

Inputs are per-video rows ``{"arm": "A"|"B", "value": float|None, "stratum": str}``. The program decides; nobody reads
a chart and calls it. Conclusions come from one primary metric's stratified bootstrap 90% interval.
"""
from __future__ import annotations

import math
import random
import statistics
from collections import defaultdict
from typing import Iterable, Literal, Optional

WEEK = 7 * 86400
LOW_VOLUME = "低产量"
MIN_PER_COLUMN = 4
WEEKLY_BELOW = 2.0
LEVEL = 0.90
RESAMPLES = 10_000
SEED = 20261104
SRM_ALPHA = 0.01
#: (z for one-sided 5%) + (z for 80% power): the α=10% two-sided design of §8.
Z_MDE = 1.6449 + 0.8416
APPLIED_FLOOR = 0.80
HARM_MIN = 15
MISSING_GAP = 0.15   # two arms' missing shares more than 15 points apart: not trustworthy (§8)
REFERENCE_VIEWS = 10  # the reference result keeps videos with at least this many plays at 48 hours

Better = Literal["higher", "lower"]


# ---- completion adjusted for length --------------------------------------------------------------------------------

def fit_duration(points: Iterable[tuple[float, float]]) -> Optional[dict]:
    """Expected completion by length: a straight line on log(seconds), fitted on videos from before the start."""
    pts = [(math.log(d), c) for d, c in points if d and d > 0 and c is not None]
    if len(pts) < 10 or len({x for x, _ in pts}) < 2:
        return None
    slope, intercept = statistics.linear_regression([x for x, _ in pts], [y for _, y in pts])
    return {"slope": slope, "intercept": intercept, "n": len(pts)}


def adjusted_completion(completion: Optional[float], seconds: Optional[float], fit: Optional[dict]) -> Optional[float]:
    """Actual completion minus the completion expected at this length; None when either is unknown."""
    if completion is None or not seconds or seconds <= 0 or not fit:
        return None
    return completion - (fit["slope"] * math.log(seconds) + fit["intercept"])


# ---- strata ------------------------------------------------------------------------------------------------------

def stratum_keys(videos: list[dict], start: float) -> list[str]:
    """One key per video from ``column``, ``matrix`` and ``pubdate`` (§8).

    A column with fewer than four videos joins its matrix, or the low-volume stratum when it has none. A group that
    averages under two videos a week is compared over four-week windows, the rest week by week.
    """
    count = defaultdict(int)
    for v in videos:
        count[v.get("column") or ""] += 1
    base = [(v.get("column") or "") if count[v.get("column") or ""] >= MIN_PER_COLUMN and v.get("column")
            else (v.get("matrix") or LOW_VOLUME) for v in videos]
    weeks = [max(0, int((v["pubdate"] - start) // WEEK)) for v in videos]
    span = max(weeks, default=0) + 1
    per_group = defaultdict(int)
    for b in base:
        per_group[b] += 1
    return [f"{b}|m{w // 4}" if per_group[b] / span < WEEKLY_BELOW else f"{b}|w{w}" for b, w in zip(base, weeks)]


def _groups(rows: list[dict]) -> tuple[dict[str, tuple[list, list]], list[str]]:
    by: dict[str, tuple[list, list]] = defaultdict(lambda: ([], []))
    for r in rows:
        if r.get("value") is None:
            continue
        by[r["stratum"]][0 if r["arm"] == "A" else 1].append(float(r["value"]))
    usable = {k: v for k, v in by.items() if v[0] and v[1]}
    return usable, sorted(k for k in by if k not in usable)


# ---- statistics ----------------------------------------------------------------------------------------------------

def _centered(groups: dict[str, tuple[list, list]]) -> tuple[list[float], list[float]]:
    a, b = [], []
    for xs, ys in groups.values():
        middle = statistics.median(xs + ys)
        a += [x - middle for x in xs]
        b += [y - middle for y in ys]
    return a, b


def _difference(groups) -> Optional[float]:
    a, b = _centered(groups)
    return statistics.median(b) - statistics.median(a) if a and b else None


def _ratio(groups) -> Optional[float]:
    a = [x for xs, _ in groups.values() for x in xs]
    b = [y for _, ys in groups.values() for y in ys]
    if not a or not b or statistics.median(a) <= 0:
        return None
    return statistics.median(b) / statistics.median(a) - 1


def estimate(rows: list[dict], kind: Literal["difference", "ratio"] = "difference", *,
             resamples: int = RESAMPLES, seed: int = SEED, level: float = LEVEL) -> dict:
    """The B − A difference of stratum-centred medians (or B/A − 1 of medians) with a stratified bootstrap interval.

    Each resample redraws A and B within every stratum and centres again, so the interval carries the centring's own
    noise; centring once and resampling the residuals would make it too narrow.
    """
    groups, skipped = _groups(rows)
    stat = _difference if kind == "difference" else _ratio
    point = stat(groups) if groups else None
    n_a = sum(len(a) for a, _ in groups.values())
    n_b = sum(len(b) for _, b in groups.values())
    out = {"estimate": point, "low": None, "high": None, "n_a": n_a, "n_b": n_b,
           "strata": len(groups), "skipped_strata": skipped, "sd": None}
    if point is None:
        return out
    a, b = _centered(groups)
    if len(a) > 1 and len(b) > 1:
        out["sd"] = math.sqrt((statistics.variance(a) * (len(a) - 1) + statistics.variance(b) * (len(b) - 1))
                              / (len(a) + len(b) - 2))
    rng = random.Random(seed)
    draws = []
    for _ in range(resamples):
        sample = {k: ([rng.choice(xs) for _ in xs], [rng.choice(ys) for _ in ys]) for k, (xs, ys) in groups.items()}
        value = stat(sample)
        if value is not None:
            draws.append(value)
    if len(draws) >= max(10, resamples // 2):
        draws.sort()
        tail = (1 - level) / 2
        out["low"] = draws[int(math.floor(tail * (len(draws) - 1)))]
        out["high"] = draws[int(math.ceil((1 - tail) * (len(draws) - 1)))]
    return out


def srm_p(n_a: int, n_b: int, share_b: int) -> float:
    """Exact two-sided binomial p-value that B's count came from ``share_b`` percent."""
    n = n_a + n_b
    if n == 0:
        return 1.0
    p = share_b / 100
    probs = [math.comb(n, k) * p ** k * (1 - p) ** (n - k) for k in range(n + 1)]
    observed = probs[n_b]
    return min(1.0, sum(q for q in probs if q <= observed * (1 + 1e-9)))


def minimum_detectable(sd: Optional[float], n_a: int, n_b: int) -> Optional[float]:
    """The smallest difference this sample could detect with 80% power at α=10%."""
    if not sd or not n_a or not n_b:
        return None
    return Z_MDE * sd * math.sqrt(1 / n_a + 1 / n_b)


def _oriented(result: dict, better: Better) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """(estimate, low, high) with "better" always positive."""
    e, lo, hi = result.get("estimate"), result.get("low"), result.get("high")
    if better == "higher":
        return e, lo, hi
    return (None if e is None else -e, None if hi is None else -hi, None if lo is None else -lo)


def guardrail_status(result: dict, better: Better, threshold: float) -> Literal["ok", "worse", "hint"]:
    """``worse`` vetoes: the whole interval lies beyond the threshold on the worse side. ``hint`` only warns."""
    e, lo, hi = _oriented(result, better)
    if hi is not None and hi < -threshold:
        return "worse"
    if e is not None and e < -threshold:
        return "hint"
    return "ok"


def verdict(primary: dict, better: Better, *, ended: bool, srm: float, guardrails: list[dict] = (),
            applied_rate: Optional[float] = None) -> dict:
    """The programme's conclusion, in the order of §8. ``guardrails`` items carry ``status`` from guardrail_status."""
    notes = []
    if applied_rate is not None and applied_rate < APPLIED_FLOOR:
        notes.append(f"B 组实际生效率 {applied_rate:.0%}，低于 80%")
    hints = [g["metric"] for g in guardrails if g.get("status") == "hint"]
    if hints:
        notes.append(f"护栏指标变差，样本不足以确认：{'、'.join(hints)}")
    if not ended:
        return {"verdict": "running", "notes": notes}
    if srm < SRM_ALPHA:
        return {"verdict": "srm", "notes": notes}
    e, lo, hi = _oriented(primary, better)
    mde = minimum_detectable(primary.get("sd"), primary.get("n_a", 0), primary.get("n_b", 0))
    if lo is not None and lo > 0 and not any(g.get("status") == "worse" for g in guardrails):
        return {"verdict": "b_better", "notes": notes}
    if hi is not None and hi < 0:
        return {"verdict": "b_worse", "notes": notes}
    return {"verdict": "no_difference", "minimum_detectable": mde, "notes": notes}


def harm_hint(views_a: list[float], views_b: list[float], missing_a: float, missing_b: float) -> Optional[str]:
    """Suggest stopping early (§8): only for harm, never to declare B better. The user decides."""
    if len(views_a) < HARM_MIN or len(views_b) < HARM_MIN:
        return None
    if statistics.median(views_b) < statistics.median(views_a) / 2:
        return "B 组 24 小时播放中位数不到 A 组一半，建议提前停止"
    if missing_b - missing_a > 0.20:
        return "B 组缺失率比 A 组高 20 个点以上，建议提前停止"
    return None
