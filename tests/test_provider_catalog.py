"""Live catalog refresh, official pricing tiers, and failure recovery."""

import json

import httpx
import pytest

from core.providers import get_provider
from web.services.provider_catalog import get_catalog, parse_opencode_prices


HTML = """<table><tr><th>Model</th><th>Input</th><th>Output</th></tr>
<tr><td>GLM-5.3</td><td>$1.40</td><td>$4.40</td></tr>
<tr><td>Beam Free</td><td>Free</td><td>Free</td></tr>
<tr><td>DeepSeek V4.1 Flash (Off-Peak)</td><td>$0.15</td><td>$0.60</td></tr>
<tr><td>DeepSeek V4.1 Flash (Peak)</td><td>$0.30</td><td>$1.20</td></tr>
<tr><td>MiniMax M3 (50% off)</td><td><del>$0.60</del> $0.30</td><td><s>$2.40</s> $1.20</td></tr>
<tr><td>Only Output Free</td><td>$1</td><td>Free</td></tr></table>"""


def test_prices_match_endpoint_ids_preserve_tiers_and_ignore_old_discounts():
    prices = parse_opencode_prices(HTML, ["glm-5.3", "beam-free", "deepseek-v4.1-flash", "minimax-m3"])
    assert prices["glm-5.3"]["input"] == 1.4
    assert prices["glm-5.3"]["output"] == 4.4
    assert prices["deepseek-v4.1-flash"]["output"] == 0.6
    assert prices["deepseek-v4.1-flash"]["output_max"] == 1.2
    assert len(prices["deepseek-v4.1-flash"]["tiers"]) == 2
    assert prices["minimax-m3"]["input"] == 0.3
    assert prices["beam-free"]["input"] == prices["beam-free"]["output"] == 0
    assert "only-output-free" not in prices


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setenv("OPENCODE_API_KEY", "fixture-key")
    return get_provider("opencode-go")


def responder(monkeypatch, models=None, html=HTML):
    calls = []

    def get(url, **kwargs):
        calls.append((url, kwargs))
        req = httpx.Request("GET", url)
        if url.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": m} for m in (models or [])]}, request=req)
        return httpx.Response(200, text=html, request=req)

    monkeypatch.setattr(httpx, "get", get)
    return calls


def test_refresh_updates_shared_persisted_models_and_free_list_without_touching_selection(provider, monkeypatch, tmp_path):
    calls = responder(monkeypatch, ["glm-5.3", "beam-free", "only-output-free"])
    first = get_catalog(provider, str(tmp_path))
    assert first["free_models"] == ["beam-free"]
    assert first["prices"]["only-output-free"]["input"] == 1
    assert calls[0][1]["headers"]["Authorization"] == "Bearer fixture-key"
    assert "Authorization" not in calls[1][1]["headers"]
    assert calls[1][0].startswith("https://opencode.ai/")
    snapshot = tmp_path / "provider-models" / "opencode-go.json"
    assert "fixture-key" not in snapshot.read_text()
    assert get_catalog(provider, str(tmp_path)) == first
    assert len(calls) == 2  # ordinary page loads use the persisted snapshot

    calls = responder(monkeypatch, ["glm-5.3", "new-free"])
    latest = get_catalog(provider, str(tmp_path), refresh=True)
    assert latest["models"] == ["glm-5.3", "new-free"]
    assert latest["free_models"] == ["new-free"]
    assert "beam-free" not in latest["prices"]
    assert json.loads(snapshot.read_text())["models"] == latest["models"]
    assert len(calls) == 2


def test_failed_refresh_preserves_last_successful_snapshot(provider, monkeypatch, tmp_path):
    responder(monkeypatch, ["glm-5.3", "beam-free"])
    first = get_catalog(provider, str(tmp_path))

    def fail(*args, **kwargs):
        raise httpx.ConnectError("private upstream details must not be returned")

    monkeypatch.setattr(httpx, "get", fail)
    result = get_catalog(provider, str(tmp_path), refresh=True)
    assert result["models"] == first["models"]
    assert result["updated_at"] == first["updated_at"]
    assert result["stale"] is True
    assert "ConnectError" in result["error"]
    assert "private" not in result["error"]


def test_price_failure_keeps_dated_prices_but_removes_delisted_models(provider, monkeypatch, tmp_path):
    responder(monkeypatch, ["glm-5.3", "beam-free"])
    first = get_catalog(provider, str(tmp_path))
    responder(monkeypatch, ["glm-5.3"], html="<html>pricing page changed</html>")
    result = get_catalog(provider, str(tmp_path), refresh=True)
    assert result["models"] == ["glm-5.3"]
    assert result["free_models"] == []
    assert result["prices"]["glm-5.3"] == first["prices"]["glm-5.3"]
    assert result["pricing_updated_at"] == first["pricing_updated_at"]
    assert "未更新" in result["pricing_error"]
    assert result["error"] is None


def test_missing_or_changed_key_does_not_reuse_another_accounts_catalog(provider, monkeypatch, tmp_path):
    responder(monkeypatch, ["beam-free"])
    get_catalog(provider, str(tmp_path))
    monkeypatch.delenv("OPENCODE_API_KEY")
    assert get_catalog(provider, str(tmp_path))["models"] == []
    monkeypatch.setenv("OPENCODE_API_KEY", "new-account")
    responder(monkeypatch, ["glm-5.3"])
    assert get_catalog(provider, str(tmp_path))["models"] == ["glm-5.3"]


def test_other_gateway_does_not_inherit_opencode_prices_or_assume_free(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENDESIGN_API_KEY", "fixture")
    calls = responder(monkeypatch, ["glm-5.3", "beam-free"])
    result = get_catalog(get_provider("open-design"), str(tmp_path))
    assert result["prices"] == {}
    assert result["free_models"] == []
    assert "价格未知" in result["pricing_error"]
    assert len(calls) == 1
