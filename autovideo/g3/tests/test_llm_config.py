from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from g2 import llm_provider
from g3.app import create_app
from g3.config import Settings, probe_config


@pytest.fixture
def clean_env(monkeypatch):
    for name in list(__import__("os").environ):
        if name.startswith("AUTOVIDEO_LLM_") or name.endswith("_API_KEY"):
            monkeypatch.delenv(name, raising=False)


def make_client(tmp_path: Path, env_text: str = ""):
    key_file = tmp_path / ".env"
    key_file.write_text(env_text)
    settings = Settings(data_dir=tmp_path / "data", key_file=key_file, project_root=Path(__file__).resolve().parents[2])
    app = create_app(settings, start_worker=False, preflight=lambda s: probe_config(s))
    return TestClient(app), settings


def test_default_is_deepseek_and_a_missing_key_is_reported(tmp_path, clean_env):
    client, _ = make_client(tmp_path)
    body = client.get("/api/config/llm").json()
    assert body["label"].startswith("DeepSeek") and body["source"] == "默认"
    assert body["key_ready"] is False and body["key_env"] == "DEEPSEEK_API_KEY"


def test_saving_a_provider_changes_what_the_service_and_the_subprocess_read(tmp_path, clean_env):
    other = tmp_path / "arec.env"
    other.write_text("OPENDESIGN_API_KEY=secret-value\n")
    client, settings = make_client(tmp_path)
    saved = client.put("/api/config/llm", json={
        "base_url": "https://amr-link.open-design.ai/v1", "model": "deepseek-v4-flash",
        "key_env": "OPENDESIGN_API_KEY", "env_file": str(other)}).json()
    assert saved["source"] == "设置页" and saved["key_ready"] is True and "secret-value" not in json.dumps(saved)
    assert settings.llm_settings_file.is_file()
    # the generation subprocess gets the same file through the environment
    provider = llm_provider.load(settings.key_file, {"AUTOVIDEO_LLM_SETTINGS_FILE": str(settings.llm_settings_file)})
    assert provider.model == "deepseek-v4-flash" and provider.name == "opendesign"
    assert client.get("/api/config/status").json()["provider"] == provider.label
    assert client.delete("/api/config/llm").json()["source"] == "默认"
    assert not settings.llm_settings_file.exists()


def test_an_unknown_address_is_a_plain_openai_compatible_service_and_may_need_no_key(tmp_path, clean_env):
    client, _ = make_client(tmp_path)
    body = client.put("/api/config/llm", json={"base_url": "http://127.0.0.1:11434/v1", "model": "qwen3:8b", "key_env": ""}).json()
    assert body["needs_key"] is False and body["key_ready"] is True
    assert client.get("/api/config/status").json()["checks"]["llm"] is True


@pytest.mark.parametrize("payload,fragment", [
    ({"base_url": "ftp://x", "model": "m"}, "http"),
    ({"base_url": "https://x/v1", "model": " "}, "模型"),
    ({"base_url": "https://x/v1", "model": "m", "key_env": "sk-abc def"}, "变量名"),
    ({"base_url": "https://x/v1", "model": "m", "env_file": "/nonexistent/.env"}, "找不到"),
])
def test_bad_settings_are_refused_with_a_sentence_and_nothing_is_saved(tmp_path, clean_env, payload, fragment):
    client, settings = make_client(tmp_path)
    reply = client.put("/api/config/llm", json=payload)
    assert reply.status_code == 422 and fragment in json.dumps(reply.json(), ensure_ascii=False)
    assert not settings.llm_settings_file.exists()


def test_testing_a_candidate_does_not_save_it_and_never_leaks_the_key(tmp_path, clean_env, monkeypatch):
    seen = {}

    def fake_ping(provider, key, timeout=30.0):
        seen.update(model=provider.model, key=key)
        return {"ok": True, "status": 200, "message": "可用"}

    monkeypatch.setattr(llm_provider, "ping", fake_ping)
    other = tmp_path / "arec.env"
    other.write_text("OPENDESIGN_API_KEY=secret-value\n")
    client, settings = make_client(tmp_path)
    reply = client.post("/api/config/llm/test", json={"base_url": "https://amr-link.open-design.ai/v1",
                                                      "model": "glm-5.3-flash", "key_env": "OPENDESIGN_API_KEY", "env_file": str(other)})
    assert reply.json()["ok"] is True and seen == {"model": "glm-5.3-flash", "key": "secret-value"}
    assert not settings.llm_settings_file.exists() and not list((tmp_path / "data").glob("*.candidate.json"))
