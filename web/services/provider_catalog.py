"""Refresh provider model lists and prices without issuing billable generations."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from html.parser import HTMLParser
from pathlib import Path

import httpx

from core.fileio import atomic_write, file_lock
from core.providers import ProviderPreset

logger = logging.getLogger(__name__)
CACHE_SECONDS = 3600
PRICING_URLS = {
    "opencode-go": "https://opencode.ai/v2/docs/console/go/",
    "opencode-zen": "https://opencode.ai/v2/docs/console/models/",
}


class _Tables(HTMLParser):
    """Read table cells, excluding superseded (struck-through) prices."""

    def __init__(self):
        super().__init__()
        self.tables: list[list[list[str]]] = []
        self.table = None
        self.row = None
        self.cell = None
        self.deleted = 0

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.table = []
        elif tag == "tr":
            self.row = []
        elif tag in ("td", "th"):
            self.cell = []
        elif tag in ("s", "del"):
            self.deleted += 1
        elif tag == "br" and self.cell is not None:
            self.cell.append(" ")

    def handle_endtag(self, tag):
        if tag in ("s", "del"):
            self.deleted = max(0, self.deleted - 1)
        elif tag in ("td", "th") and self.cell is not None:
            if self.row is not None:
                self.row.append(" ".join("".join(self.cell).split()))
            self.cell = None
        elif tag == "tr" and self.row is not None:
            if self.table is not None:
                self.table.append(self.row)
            self.row = None
        elif tag == "table" and self.table is not None:
            self.tables.append(self.table)
            self.table = None

    def handle_data(self, data):
        if self.cell is not None and not self.deleted:
            self.cell.append(data)


def _model_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", re.sub(r"\([^)]*\)", "", name).lower())


def _dollars(cell: str) -> float | None:
    if cell.strip().lower() == "free":
        return 0.0
    match = re.fullmatch(r"\$([0-9]+(?:\.[0-9]+)?)", cell.strip())
    return float(match[1]) if match else None


def parse_opencode_prices(html: str, models: list[str]) -> dict:
    """Match only this endpoint's IDs; retain context/peak tiers as ranges."""
    parser = _Tables()
    parser.feed(html)
    ids = {_model_key(model): model for model in models}
    prices: dict = {}
    for table in parser.tables:
        if not table or table[0][:3] != ["Model", "Input", "Output"]:
            continue
        for row in table[1:]:
            if len(row) < 3:
                continue
            model = ids.get(_model_key(row[0]))
            inp, out = _dollars(row[1]), _dollars(row[2])
            if model is None or inp is None or out is None:
                continue
            entry = prices.setdefault(model, {"input": inp, "input_max": inp,
                                              "output": out, "output_max": out, "tiers": []})
            entry["input"] = min(entry["input"], inp)
            entry["input_max"] = max(entry["input_max"], inp)
            entry["output"] = min(entry["output"], out)
            entry["output_max"] = max(entry["output_max"], out)
            tier = {"label": row[0], "input": inp, "output": out}
            if tier not in entry["tiers"]:
                entry["tiers"].append(tier)
    return prices


def _read_snapshot(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("models"), list):
            return data
    except (OSError, ValueError):
        pass
    return {}


def get_catalog(preset: ProviderPreset, state_dir: str, refresh: bool = False) -> dict:
    """Return a persisted catalog; a failed refresh preserves the last good list."""
    path = Path(state_dir) / "provider-models" / f"{preset.id}.json"
    with file_lock(str(path), timeout=30):
        previous = _read_snapshot(path)
        empty = {"models": [], "free_models": [], "prices": {}, "updated_at": None,
                 "pricing_updated_at": None, "pricing_source": PRICING_URLS.get(preset.id),
                 "pricing_error": None, "stale": False, "error": None}
        key = os.environ.get(preset.api_key_env, "") if preset.api_key_env else ""
        if preset.api_key_env and not key:
            return {**empty, "error": f"还没填密钥 {preset.api_key_env}"}
        # A key change must not inherit another account's available models.
        account = hashlib.sha256(key.encode()).hexdigest()
        if previous.get("account") != account:
            previous = {}
        if not refresh and previous and time.time() - previous.get("updated_at", 0) < CACHE_SECONDS:
            return {k: v for k, v in previous.items() if k != "account"}

        headers = {k: v.replace("{session_id}", "models") for k, v in preset.extra_headers.items()}
        if key:
            headers["Authorization"] = f"Bearer {key}"
        try:
            reply = httpx.get(preset.base_url.rstrip("/") + "/models", headers=headers, timeout=10.0)
            reply.raise_for_status()
            items = reply.json().get("data")
            if not isinstance(items, list):
                raise ValueError("missing model list")
            models = sorted({i["id"] for i in items if isinstance(i, dict)
                             and isinstance(i.get("id"), str) and i["id"].strip()})
            if items and not models:
                raise ValueError("invalid model list")
        except (httpx.HTTPError, ValueError, AttributeError) as exc:
            reason = f"服务商返回 {exc.response.status_code}" if isinstance(exc, httpx.HTTPStatusError) else type(exc).__name__
            return {**empty, **{k: v for k, v in previous.items() if k != "account"},
                    "stale": bool(previous), "error": f"取不到模型列表：{reason}"}

        data = {**empty, "models": models, "updated_at": time.time()}
        source = PRICING_URLS.get(preset.id)
        if source:
            try:
                # Public documentation never receives a saved API key.
                response = httpx.get(source, headers={"User-Agent": "BaseCodingCLi-ResearchAgent/2.0"},
                                     timeout=10.0, follow_redirects=True)
                response.raise_for_status()
                prices = parse_opencode_prices(response.text, models)
                if models and not prices:
                    raise ValueError("pricing table did not match")
                data.update(prices=prices, pricing_updated_at=time.time())
            except (httpx.HTTPError, ValueError) as exc:
                data.update(prices={m: p for m, p in previous.get("prices", {}).items() if m in models},
                            pricing_updated_at=previous.get("pricing_updated_at"),
                            pricing_error=f"官网价格未更新，已有价格仅供参考：{type(exc).__name__}")
        else:
            data["pricing_error"] = ("本地模型不收 API 费，运行资源由本机承担。" if preset.id == "ollama"
                                     else "暂未接入该服务商的可核验价格来源，价格未知；请以服务商账单为准。")

        for model in models:
            # OpenCode documents the -free IDs; do not infer zero cost for other gateways.
            if preset.id == "ollama" or (preset.id in PRICING_URLS and model.endswith("-free")):
                data["prices"].setdefault(model, {"input": 0, "input_max": 0,
                                                 "output": 0, "output_max": 0, "tiers": []})
        data["free_models"] = [m for m in models if m in data["prices"]
                               and data["prices"][m]["input_max"] == data["prices"][m]["output_max"] == 0]
        try:
            atomic_write(str(path), json.dumps({**data, "account": account}, ensure_ascii=False))
        except OSError:
            logger.warning("Could not persist model catalog for %s", preset.id)
        return data
