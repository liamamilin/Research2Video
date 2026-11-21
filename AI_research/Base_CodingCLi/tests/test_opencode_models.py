"""OpenCode free→paid model selection."""

from __future__ import annotations

import pytest

from core import opencode_models as models


def test_looks_free():
    assert models.looks_free("opencode-go/beam-free")
    assert models.looks_free("commandcode/x:free")
    assert not models.looks_free("opencode-go/deepseek-v4.1-flash")


def test_free_models_for_provider_filters_prefix():
    listed = [
        "opencode-go/beam-free",
        "opencode-go/deepseek-v4.1-flash",
        "opencode/flint-40b-free",
        "commandcode/x:free",
    ]
    assert models.free_models_for_provider(listed, "opencode-go") == ["opencode-go/beam-free"]


def test_candidate_ladder_auto_free_then_paid(monkeypatch):
    monkeypatch.setattr(models, "list_models", lambda binary: [
        "opencode-go/beam-free", "opencode-go/hy4-flash-free", "opencode-go/deepseek-v4.1-flash",
    ])
    ladder = models.candidate_ladder({
        "model_provider": "opencode-go",
        "free_models": [],
        "paid_models": ["opencode-go/deepseek-v4.1-flash", "opencode-go/glm-5.3-flash"],
    }, binary="opencode")
    assert ladder[:2] == ["opencode-go/beam-free", "opencode-go/hy4-flash-free"]
    assert "opencode-go/glm-5.3-flash" in ladder


def test_select_model_falls_through_free_to_paid(monkeypatch):
    monkeypatch.setattr(models, "candidate_ladder", lambda config, binary: [
        "opencode-go/beam-free", "opencode-go/deepseek-v4.1-flash",
    ])

    def probe(binary, model, **k):
        return model.endswith("flash")

    monkeypatch.setattr(models, "probe_model", probe)
    chosen, receipt = models.select_model({}, binary="opencode", cache={})
    assert chosen == "opencode-go/deepseek-v4.1-flash"
    assert receipt["kind"] == "paid"
    assert receipt["tried"][0]["ok"] is False and receipt["tried"][1]["ok"] is True


def test_select_model_raises_when_all_fail(monkeypatch):
    monkeypatch.setattr(models, "candidate_ladder", lambda config, binary: ["opencode-go/beam-free"])
    monkeypatch.setattr(models, "probe_model", lambda *a, **k: False)
    with pytest.raises(RuntimeError, match="均不可用"):
        models.select_model({}, binary="opencode")
