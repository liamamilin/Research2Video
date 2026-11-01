"""Turn recorded tokens into dollars, and say where each number came from.

Order of trust: what the provider charged (``reported``), the unit price a render recorded at call time
(``snapshot``), the table in ``config/pricing.yaml`` (``table``), otherwise ``unpriced`` and tokens only. Nothing here
changes a record: prices are applied when read, so editing the table re-prices history.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import yaml

logger = logging.getLogger(__name__)

FILE_NAME = "pricing.yaml"


@dataclass(frozen=True)
class Price:
    model: str
    input: float
    output: float
    cache_read: Optional[float] = None
    host: str = ""
    effective_from: float = 0.0
    note: str = ""

    def public(self) -> dict:
        return {"host": self.host, "model": self.model, "input": self.input, "cache_read": self.cache_read,
                "output": self.output, "note": self.note}


def load(config_dir: str) -> list[Price]:
    try:
        with open(os.path.join(config_dir, FILE_NAME), encoding="utf-8") as handle:
            rows = (yaml.safe_load(handle) or {}).get("prices") or []
    except FileNotFoundError:
        return []
    except (OSError, yaml.YAMLError) as exc:
        logger.warning("pricing table unavailable: %s", exc)
        return []
    table = []
    for row in rows:
        try:
            effective = row.get("effective_from")
            table.append(Price(
                model=str(row["model"]).lower(), input=float(row["input"]), output=float(row["output"]),
                cache_read=None if row.get("cache_read") is None else float(row["cache_read"]),
                host=str(row.get("host") or "").lower(), note=str(row.get("note") or ""),
                effective_from=datetime.strptime(str(effective), "%Y-%m-%d").timestamp() if effective else 0.0))
        except (KeyError, TypeError, ValueError) as exc:
            logger.warning("pricing row ignored (%s): %r", exc, row)
    return table


def find(table: list[Price], provider: str, model: str, ts: float = 0.0) -> Optional[Price]:
    """The price for ``model`` at ``provider``: one naming that host wins over a general one; the latest in force wins."""
    provider, model = (provider or "").lower(), (model or "").lower()
    fits = [p for p in table if p.model == model and p.effective_from <= (ts or float("inf"))
            and (not p.host or (provider and p.host in provider))]
    if not fits:
        return None
    return max(fits, key=lambda p: (bool(p.host), p.effective_from))


def tokens_cost(prompt: int, cached: int, completion: int, rate_in: float, rate_cache: Optional[float],
                rate_out: float) -> float:
    cached = min(max(cached, 0), prompt)
    rate_cache = rate_in if rate_cache is None else rate_cache
    return ((prompt - cached) * rate_in + cached * rate_cache + completion * rate_out) / 1_000_000


def cost_of(group: dict, table: list[Price]) -> tuple[Optional[float], str]:
    """``group`` is a sum over calls that share a provider, model, price snapshot and reporting state."""
    if group.get("kind") == "search":
        return None, "unpriced"
    if not group.get("prompt") and not group.get("completion"):
        return 0.0, "reported"  # a failed request: nothing was generated
    if group.get("reported_n") and group["reported_n"] == group["n"]:
        return float(group["reported"] or 0), "reported"
    if group.get("snap_in") is not None and group.get("snap_out") is not None:
        return tokens_cost(group["prompt"], group["cached"], group["completion"], group["snap_in"], None,
                           group["snap_out"]), "snapshot"
    price = find(table, group.get("provider", ""), group.get("model", ""), group.get("ts", 0.0))
    if price is None:
        return None, "unpriced"
    return tokens_cost(group["prompt"], group["cached"], group["completion"], price.input, price.cache_read,
                       price.output), "table"
