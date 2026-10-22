"""Tests for API-key management and per-service connectivity tests."""

from __future__ import annotations

import os

import pytest


def _login(client, username="admin", password="admin-pass-123"):
    return client.post("/api/auth/login",
                       json={"username": username, "password": password})


def _csrf(client) -> dict:
    return {"X-CSRF-Token": client.cookies.get("ai_research_csrf") or ""}


class _FakeLLMClient:
    """A connectivity-test double that always answers; no network, no key check."""

    def __init__(self, cfg):
        self.cfg = cfg

    def chat(self, messages, **kwargs):
        class R:
            content = "ok"
        return R()

    def close(self):
        pass


def _write_config(tmp_path, search_provider="parallel"):
    (tmp_path / "config" / "system.yaml").write_text(
        "ai:\n"
        "  model: \"test-model\"\n"
        "  base_url: \"https://llm.test/v1\"\n"
        "  api_key_env: \"LLM_API_KEY\"\n"
        "  embedding_model: \"emb-model\"\n"
        "  embedding_base_url: \"http://localhost:11434/v1\"\n"
        "  embedding_api_key_env: \"EMBEDDING_API_KEY\"\n"
        "search:\n"
        f"  provider: \"{search_provider}\"\n"
        "  api_key_env: \"PARALLEL_API_KEY\"\n"
        "  api_key_envs:\n"
        "    parallel: \"PARALLEL_API_KEY\"\n"
        "    tavily: \"TAVILY_API_KEY\"\n"
        "extraction:\n"
        "  api_key_env: \"EXTRACTION_API_KEY\"\n",
        encoding="utf-8",
    )


@pytest.fixture()
def secrets_env(web_env, monkeypatch):
    tmp_path, _ = web_env
    _write_config(tmp_path)
    env_file = tmp_path / ".env"
    env_file.write_text('LLM_API_KEY="old-llm-key"\nPARALLEL_API_KEY="parallel-secret"\n',
                        encoding="utf-8")
    monkeypatch.setattr("web.routes.config._ENV_PATH_OVERRIDE", str(env_file))
    monkeypatch.setenv("LLM_API_KEY", "old-llm-key")
    monkeypatch.setenv("PARALLEL_API_KEY", "parallel-secret")
    # the real .env is loaded into the process: a provider key filled in there must not leak into these slots
    for name in ("EMBEDDING_API_KEY", "TAVILY_API_KEY", "EXTRACTION_API_KEY", "OPENDESIGN_API_KEY", "OPENCODE_API_KEY",
                 "DEEPSEEK_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    return env_file


def test_secrets_list_masks_values(client, secrets_env):
    _login(client)
    r = client.get("/api/config/secrets")
    assert r.status_code == 200
    items = {item["env"]: item for item in r.json()["secrets"]}
    assert items["LLM_API_KEY"]["configured"] is True
    assert items["LLM_API_KEY"]["masked"] == "old-****ey"
    assert items["TAVILY_API_KEY"]["configured"] is False
    assert items["EMBEDDING_API_KEY"]["label"] == "Embedding 模型"
    assert "old-llm-key" not in r.text


def test_secrets_requires_admin(client, secrets_env):
    assert client.get("/api/config/secrets").status_code == 401
    _login(client, "viewer", "viewer-pass-123")
    assert client.get("/api/config/secrets").status_code == 403


def test_secrets_update_writes_env_file(client, secrets_env, monkeypatch):
    _login(client)
    r = client.put("/api/config/secrets",
                   json={"values": {"TAVILY_API_KEY": "tv-secret", "LLM_API_KEY": None}},
                   headers=_csrf(client))
    assert r.status_code == 200

    text = secrets_env.read_text(encoding="utf-8")
    assert 'TAVILY_API_KEY="tv-secret"' in text
    assert "LLM_API_KEY" not in text
    assert 'PARALLEL_API_KEY="parallel-secret"' in text
    import os
    assert oct(os.stat(secrets_env).st_mode)[-3:] == "600"
    items = {item["env"]: item for item in r.json()["secrets"]}
    assert items["TAVILY_API_KEY"]["configured"] is True
    assert items["LLM_API_KEY"]["configured"] is False


def test_secrets_update_rejects_unknown_and_bad_values(client, secrets_env):
    _login(client)
    r = client.put("/api/config/secrets",
                   json={"values": {"SOMETHING_ELSE": "x"}}, headers=_csrf(client))
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "unknown_secret"

    r2 = client.put("/api/config/secrets",
                    json={"values": {"TAVILY_API_KEY": "a\nb"}}, headers=_csrf(client))
    assert r2.status_code == 422
    assert r2.json()["error"]["code"] == "invalid_value"

    r3 = client.put("/api/config/secrets", json={"values": []}, headers=_csrf(client))
    assert r3.status_code == 422


def test_secrets_update_requires_csrf(client, secrets_env):
    _login(client)
    r = client.put("/api/config/secrets", json={"values": {"TAVILY_API_KEY": "x"}})
    assert r.status_code == 403


def test_test_llm_uses_unsaved_overrides_and_key(client, secrets_env, monkeypatch):
    captured = {}

    class FakeLLMClient:
        def __init__(self, cfg):
            captured["cfg"] = cfg

        def chat(self, messages, **kwargs):
            class R:
                content = "ok"
            return R()

        def close(self):
            pass

    monkeypatch.setattr("web.routes.config.LLMClient", FakeLLMClient)
    _login(client)

    r = client.post("/api/config/test-llm",
                    json={"config": {"ai": {"model": "form-model"}},
                          "api_key": "typed-key"},
                    headers=_csrf(client))
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["model"] == "form-model"
    assert captured["cfg"].api_key == "typed-key"


def test_every_provider_preset_has_a_key_slot_even_when_not_active(client, secrets_env):
    """Found live: switching ai away from a provider made that provider's key
    slot disappear from the settings page, which read as the key being lost
    even though .env still held it. Every preset's slot stays listed."""
    _login(client)
    items = {item["env"]: item for item in client.get("/api/config/secrets").json()["secrets"]}
    assert items["LLM_API_KEY"]["label"] == "AI 模型（LLM）"  # active: specific label wins
    assert items["OPENDESIGN_API_KEY"]["label"] == "服务商 · Open Design（amr-link）"
    assert items["OPENDESIGN_API_KEY"]["configured"] is False
    assert items["DEEPSEEK_API_KEY"]["label"] == "服务商 · DeepSeek 官方"
    assert items["OPENAI_API_KEY"]["label"] == "服务商 · OpenAI"
    # opencode-go and opencode-zen share one var; neither ai nor review_ai is
    # using it here, so both labels show rather than silently picking one.
    assert items["OPENCODE_API_KEY"]["label"] == "服务商 · opencode Go（订阅）、opencode zen（按量计费）"
    assert "" not in items  # ollama needs no key: not listed as an empty-name slot


def test_a_key_can_be_saved_for_a_provider_that_is_not_active_yet(client, secrets_env):
    """The actual bug report: you could not pre-stage DeepSeek's key while ai
    was still on a different provider -- `unknown_secret` rejected it."""
    _login(client)
    r = client.put("/api/config/secrets",
                   json={"values": {"DEEPSEEK_API_KEY": "sk-test-dummy-deepseek-0001"}},
                   headers=_csrf(client))
    assert r.status_code == 200, r.text
    items = {item["env"]: item for item in r.json()["secrets"]}
    assert items["DEEPSEEK_API_KEY"]["configured"] is True


def test_switching_providers_never_touches_another_providers_key(client, secrets_env, monkeypatch):
    """ai starts on opencode Go, switches to DeepSeek, then back. At every
    step, a key saved for a provider ai is not actively using stays exactly
    as saved -- the defect this whole feature exists to close."""
    _login(client)
    client.put("/api/config/system",
              json={"patch": {"ai": {"base_url": "https://opencode.ai/zen/go/v1",
                                     "api_key_env": "OPENCODE_API_KEY", "model": "longcat-2.5-preview-free"}}},
              headers=_csrf(client))
    client.put("/api/config/secrets",
              json={"values": {"OPENCODE_API_KEY": "sk-test-opencode", "DEEPSEEK_API_KEY": "sk-test-deepseek"}},
              headers=_csrf(client))
    opencode_before = os.environ.get("OPENCODE_API_KEY")

    r = client.put("/api/config/system",
                   json={"patch": {"ai": {"base_url": "https://api.deepseek.com/v1",
                                          "api_key_env": "DEEPSEEK_API_KEY", "model": "deepseek-chat"}}},
                   headers=_csrf(client))
    assert r.status_code == 200, r.text
    items = {item["env"]: item for item in client.get("/api/config/secrets").json()["secrets"]}
    assert items["OPENCODE_API_KEY"]["configured"] is True  # ai's old key: untouched
    assert os.environ.get("OPENCODE_API_KEY") == opencode_before
    assert items["OPENCODE_API_KEY"]["label"] == "服务商 · opencode Go（订阅）、opencode zen（按量计费）"
    assert items["DEEPSEEK_API_KEY"]["configured"] is True  # ai's new key: as saved
    assert items["DEEPSEEK_API_KEY"]["label"] == "服务商 · DeepSeek 官方 · 当前用于 AI 模型（LLM）"  # named for whose key it is

    r = client.put("/api/config/system",
                   json={"patch": {"ai": {"base_url": "https://opencode.ai/zen/go/v1",
                                          "api_key_env": "OPENCODE_API_KEY", "model": "longcat-2.5-preview-free"}}},
                   headers=_csrf(client))
    assert r.status_code == 200, r.text
    items = {item["env"]: item for item in client.get("/api/config/secrets").json()["secrets"]}
    assert items["OPENCODE_API_KEY"]["configured"] is True
    assert os.environ.get("OPENCODE_API_KEY") == opencode_before  # never rewritten by the round trip
    assert items["DEEPSEEK_API_KEY"]["configured"] is True  # DeepSeek's key: still there too


def test_review_ai_key_is_listed_once_configured(client, web_env, monkeypatch):
    tmp_path, _ = web_env
    (tmp_path / "config" / "system.yaml").write_text(
        "ai:\n"
        "  model: \"free-model\"\n"
        "  base_url: \"https://llm.test/v1\"\n"
        "  api_key_env: \"OPENCODE_API_KEY\"\n"
        "review_ai:\n"
        "  model: \"strong-model\"\n"
        "  base_url: \"https://llm.test/v1\"\n"
        "  api_key_env: \"REVIEW_API_KEY\"\n",
        encoding="utf-8",
    )
    env_file = tmp_path / ".env"
    env_file.write_text('OPENCODE_API_KEY="go-key"\nREVIEW_API_KEY="review-key"\n', encoding="utf-8")
    monkeypatch.setattr("web.routes.config._ENV_PATH_OVERRIDE", str(env_file))
    monkeypatch.setenv("OPENCODE_API_KEY", "go-key")
    monkeypatch.setenv("REVIEW_API_KEY", "review-key")

    _login(client)
    items = {item["env"]: item for item in client.get("/api/config/secrets").json()["secrets"]}
    assert items["OPENCODE_API_KEY"]["label"] == "服务商 · opencode Go（订阅）、opencode zen（按量计费） · 当前用于 AI 模型（LLM）"
    assert items["REVIEW_API_KEY"]["label"] == "审校模型（review_ai）"
    assert items["REVIEW_API_KEY"]["configured"] is True


def test_test_llm_can_target_the_review_section(client, secrets_env, monkeypatch):
    monkeypatch.setattr("web.routes.config.LLMClient", _FakeLLMClient)
    _login(client)
    r = client.post("/api/config/test-llm",
                    json={"section": "review_ai", "config": {"review_ai": {"model": "audit-model"}}},
                    headers=_csrf(client))
    assert r.status_code == 200, r.text
    assert r.json()["model"] == "audit-model"


def test_test_llm_tries_the_configured_model_alone_never_its_fallbacks(client, secrets_env, monkeypatch):
    seen = []

    class Recording(_FakeLLMClient):
        def __init__(self, cfg):
            super().__init__(cfg)
            seen.append(cfg.fallback_models)

    monkeypatch.setattr("web.routes.config.LLMClient", Recording)
    _login(client)
    r = client.post("/api/config/test-llm", headers=_csrf(client), json={
        "section": "review_ai", "config": {"review_ai": {"model": "dead-free", "fallback_models": ["minimax-m3"]}}})
    assert r.status_code == 200, r.text
    assert seen == [[]]  # a dead primary answered by its fallback would read as "available"


def test_test_llm_rejects_an_unknown_section(client, secrets_env):
    _login(client)
    r = client.post("/api/config/test-llm", json={"section": "search"}, headers=_csrf(client))
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "invalid_section"


def test_test_llm_reports_failure(client, secrets_env, monkeypatch):
    class Boom:
        def __init__(self, cfg):
            pass

        def chat(self, *a, **k):
            raise RuntimeError("connection refused")

        def close(self):
            pass

    monkeypatch.setattr("web.routes.config.LLMClient", Boom)
    _login(client)
    body = client.post("/api/config/test-llm", json={}, headers=_csrf(client)).json()
    assert body["ok"] is False
    assert "connection refused" in body["error"]


def test_test_search_uses_unsaved_overrides(client, secrets_env, monkeypatch):
    captured = {}

    class FakeSearchClient:
        def __init__(self, cfg):
            captured["cfg"] = cfg

        def search(self, queries, objective=""):
            class R:
                url = "https://hit.test/1"
            return [R()]

    monkeypatch.setattr("web.routes.config.SearchClient", FakeSearchClient)
    _login(client)

    body = client.post("/api/config/test-search",
                       json={"config": {"search": {"provider": "tavily"}},
                             "api_key": "tv-typed"},
                       headers=_csrf(client)).json()
    assert body["ok"] is True
    assert body["provider"] == "tavily"
    assert body["sample_url"] == "https://hit.test/1"
    assert captured["cfg"].api_keys["tavily"] == "tv-typed"


def test_test_embedding(client, secrets_env, monkeypatch):
    class FakeClient:
        model = "emb-model"
        base_url = "http://localhost:11434/v1"
        api_key = ""

        def embed(self, texts):
            return [[0.1, 0.2, 0.3]]

    monkeypatch.setattr("core.embeddings.EmbeddingClient.from_system",
                        classmethod(lambda cls, cfg: FakeClient()))
    _login(client)
    body = client.post("/api/config/test-embedding", json={}, headers=_csrf(client)).json()
    assert body["ok"] is True
    assert body["dims"] == 3


def test_test_embedding_failure(client, secrets_env, monkeypatch):
    def boom(cls, cfg):
        raise RuntimeError("embedding_model is not configured")

    monkeypatch.setattr("core.embeddings.EmbeddingClient.from_system", classmethod(boom))
    _login(client)
    body = client.post("/api/config/test-embedding", json={}, headers=_csrf(client)).json()
    assert body["ok"] is False
    assert "embedding_model" in body["error"]


def test_test_endpoints_require_admin(client, secrets_env):
    _login(client, "viewer", "viewer-pass-123")
    for path in ("test-llm", "test-search", "test-embedding"):
        assert client.post(f"/api/config/{path}", json={}, headers=_csrf(client)).status_code == 403


def test_one_providers_key_slot_names_every_feature_using_it(client, secrets_env, monkeypatch):
    """The cleanup pass and the review model on one provider share its one key: one slot, both uses named."""
    import yaml

    config = secrets_env.parent / "config" / "system.yaml"
    data = yaml.safe_load(config.read_text(encoding="utf-8"))
    data["review_ai"] = {**data["ai"], "base_url": "https://opencode.ai/zen/go/v1", "api_key_env": "OPENCODE_API_KEY"}
    data["extraction"] = {"enabled": False, "model": "m", "base_url": "https://opencode.ai/zen/v1",
                          "api_key_env": "OPENCODE_API_KEY"}
    config.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    _login(client)
    items = {item["env"]: item for item in client.get("/api/config/secrets").json()["secrets"]}
    assert items["OPENCODE_API_KEY"]["label"].endswith("当前用于 审校模型（review_ai）、报告清理")
    assert "EXTRACTION_API_KEY" not in items
