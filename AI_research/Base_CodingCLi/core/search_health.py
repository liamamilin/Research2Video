"""Shared search health: per-provider cooldowns and an all-providers-down marker.

Kept in ``<state>/search_health.json`` so the web app and the scheduler host see
the same outage. A provider that is out of credits or rejects its key is skipped
for ``PERMANENT_COOLDOWN``; one that rate-limits, for ``RATE_LIMIT_COOLDOWN``.
When every provider failed, ``outage()`` reports it until a search answers again
or the cooldowns run out, which lets automatic work wait instead of failing.

Classification reads only the provider's HTTP status and error text. Nothing is
recorded until ``use_state_dir`` names a directory (``core.state`` does this), so
tests and one-off scripts never share cooldowns through a default path.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Optional

from core.errors import SEARCH_UNAVAILABLE_PREFIX
from core.fileio import atomic_write, file_lock

logger = logging.getLogger(__name__)

FILE_NAME = "search_health.json"
PERMANENT_COOLDOWN = 30 * 60
RATE_LIMIT_COOLDOWN = 10 * 60

_dir: Optional[str] = None

_PERMANENT_WORDS = ("no_more_credits", "credits limit", "insufficient", "quota", "invalid api key", "unauthorized")
_RATE_WORDS = ("ratelimit", "rate limit", "too many requests", "限流", "太频繁")


def use_state_dir(state_dir: Optional[str]) -> None:
    global _dir
    _dir = state_dir


def _path() -> Optional[str]:
    return os.path.join(_dir, FILE_NAME) if _dir else None


def classify(reason: str) -> Optional[str]:
    """``"permanent"`` (credits, key), ``"rate_limit"`` or ``None`` (worth trying again right away)."""
    text = (reason or "").lower()
    match = re.search(r"http (\d{3})", text)
    code = int(match.group(1)) if match else None
    if code in (401, 402, 403) or any(word in text for word in _PERMANENT_WORDS):
        return "permanent"
    if code == 429 or any(word in text for word in _RATE_WORDS):
        return "rate_limit"
    return None


def _read(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _update(change) -> None:
    path = _path()
    if path is None:
        return
    try:
        with file_lock(path, timeout=2):
            data = _read(path)
            change(data)
            atomic_write(path, json.dumps(data, ensure_ascii=False))
    except (OSError, TimeoutError):
        logger.warning("search health could not be saved; searches go on without it")


def cooling(now: Optional[float] = None) -> dict[str, dict]:
    """Providers to skip right now: name -> {"until", "kind", "reason"}."""
    path = _path()
    if path is None:
        return {}
    now = time.time() if now is None else now
    return {name: row for name, row in (_read(path).get("providers") or {}).items()
            if isinstance(row, dict) and row.get("until", 0) > now}


def record_failures(reasons: dict[str, str], now: Optional[float] = None) -> None:
    """Cool down every provider whose failure says waiting is needed."""
    now = time.time() if now is None else now
    found = {name: kind for name, why in reasons.items() if (kind := classify(why))}
    if not found:
        return

    def change(data: dict) -> None:
        providers = data.setdefault("providers", {})
        for name, kind in found.items():
            wait = PERMANENT_COOLDOWN if kind == "permanent" else RATE_LIMIT_COOLDOWN
            providers[name] = {"until": now + wait, "kind": kind, "reason": str(reasons[name])[:300]}
    _update(change)


def record_outage(reasons: dict[str, str], now: Optional[float] = None) -> None:
    now = time.time() if now is None else now

    def change(data: dict) -> None:
        since = (data.get("outage") or {}).get("since") or now
        data["outage"] = {"since": since, "at": now, "reasons": {k: str(v)[:300] for k, v in reasons.items()}}
    _update(change)


def record_answer(provider: str) -> None:
    """A provider answered: it is healthy and search is not down."""
    path = _path()
    if path is None:
        return
    data = _read(path)
    if not data.get("outage") and provider not in (data.get("providers") or {}):
        return  # the usual case: nothing to clear, so no write

    def change(data: dict) -> None:
        data.pop("outage", None)
        (data.get("providers") or {}).pop(provider, None)
    _update(change)


def outage(now: Optional[float] = None) -> Optional[dict]:
    """The current all-providers-down outage, while at least one provider is still cooling down."""
    path = _path()
    if path is None:
        return None
    data = _read(path)
    marker = data.get("outage")
    if not marker:
        return None
    now = time.time() if now is None else now
    until = max((row.get("until", 0) for row in (data.get("providers") or {}).values() if isinstance(row, dict)), default=0)
    if until <= now:
        return None  # the cooldowns ran out: let the next search find out whether it is back
    return {**marker, "until": until}


def clear() -> None:
    """Forget cooldowns and outage, e.g. after the search settings or keys were changed."""
    _update(lambda data: data.clear())


def is_outage_error(error: Optional[str]) -> bool:
    """Whether a recorded failure (a script's or a job's error text) was caused by search being unavailable."""
    return bool(error) and SEARCH_UNAVAILABLE_PREFIX in error
