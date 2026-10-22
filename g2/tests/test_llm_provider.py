import io
import json
import urllib.error

import pytest

import g2.run_g2 as g2
from g2 import llm_provider as lp


def test_deepseek_is_the_default_and_keeps_its_numbers():
    p = lp.load(None, {})
    assert (p.name, p.model, p.url, p.key_env) == ("deepseek", "deepseek-flash", "https://api.deepseek.com/chat/completions",
                                                   "DEEPSEEK_API_KEY")
    assert p.thinking and p.json_mode and p.max_tokens == 100_000 and (p.input_per_m, p.output_per_m) == (0.30, 1.20)
    assert p.code("AUTH_FAILED") == "DEEPSEEK_AUTH_FAILED" and not p.error


def test_another_provider_is_chosen_in_env_and_overridden_one_setting_at_a_time(tmp_path):
    env = tmp_path / ".env"
    env.write_text("AUTOVIDEO_LLM_PROVIDER=opencode\nAUTOVIDEO_LLM_MODEL=some-free-model\n# AUTOVIDEO_LLM_MAX_TOKENS=1\n")
    p = lp.load(env, {})
    assert (p.name, p.model, p.key_env, p.thinking, p.max_tokens) == ("opencode", "some-free-model", "OPENCODE_API_KEY", False, 32_768)
    assert p.url == "https://opencode.ai/zen/go/v1/chat/completions" and p.code("AUTH_FAILED") == "LLM_AUTH_FAILED"
    over = lp.load(env, {"AUTOVIDEO_LLM_MAX_TOKENS": "8000", "AUTOVIDEO_LLM_THINKING": "1", "AUTOVIDEO_LLM_OUTPUT_PER_M": "0.5"})
    assert (over.max_tokens, over.thinking, over.output_per_m, over.model) == (8000, True, 0.5, "some-free-model")  # env wins


def test_every_preset_is_usable_as_it_stands():
    for name in lp.PRESETS:
        p = lp.load(None, {"AUTOVIDEO_LLM_PROVIDER": name})
        assert not p.error and p.url.endswith("/chat/completions") and p.model and p.key_env, name
    assert lp.load(None, {"AUTOVIDEO_LLM_PROVIDER": "opendesign"}).key_env == "OPENDESIGN_API_KEY"


def test_bad_settings_come_back_as_an_error_not_an_exception():
    assert "不认识" in lp.load(None, {"AUTOVIDEO_LLM_PROVIDER": "nope"}).error
    assert "BASE_URL" in lp.load(None, {"AUTOVIDEO_LLM_PROVIDER": "custom"}).error
    assert "数值" in lp.load(None, {"AUTOVIDEO_LLM_MAX_TOKENS": "many"}).error
    ok = lp.load(None, {"AUTOVIDEO_LLM_PROVIDER": "custom", "AUTOVIDEO_LLM_BASE_URL": "http://localhost:11434/v1",
                        "AUTOVIDEO_LLM_MODEL": "qwen", "AUTOVIDEO_LLM_API_KEY_ENV": "LOCAL_KEY"})
    assert not ok.error and ok.url == "http://localhost:11434/v1/chat/completions"


def test_the_key_comes_from_env_then_dotenv_then_the_named_second_file(tmp_path):
    p = lp.PRESETS["opencode"]
    other = tmp_path / "other.env"
    other.write_text('OPENCODE_API_KEY="from-other"\n')
    env = tmp_path / ".env"
    env.write_text(f"AUTOVIDEO_LLM_ENV_FILE={other}\n")
    assert lp.api_key(p, env, {}) == "from-other"
    env.write_text(f"AUTOVIDEO_LLM_ENV_FILE={other}\nOPENCODE_API_KEY=from-dotenv\n")
    assert lp.api_key(p, env, {}) == "from-dotenv"
    assert lp.api_key(p, env, {"OPENCODE_API_KEY": "from-env"}) == "from-env"
    assert lp.api_key(p, tmp_path / "missing.env", {}) == ""


class Reply(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _call(monkeypatch, tmp_path, provider, urlopen):
    monkeypatch.setattr(g2, "PROJECT", tmp_path)
    monkeypatch.setattr(g2, "CALLS_DIR", tmp_path / "calls")
    monkeypatch.setattr(g2, "LOGS", tmp_path / "logs")
    monkeypatch.setattr(g2, "CALL_RECORDS", [])
    monkeypatch.setattr(g2, "PROVIDER", provider)
    monkeypatch.setattr(g2, "MODEL", provider.model)
    monkeypatch.setattr(g2, "MAX_TOKENS_PER_CALL", provider.max_tokens)  # both are read from PROVIDER at import
    monkeypatch.setattr(g2, "LLM_INPUT_PER_M", provider.input_per_m)
    monkeypatch.setattr(g2, "LLM_OUTPUT_PER_M", provider.output_per_m)
    monkeypatch.setattr(g2, "load_deepseek_api_key", lambda _: "secret-key")
    monkeypatch.setattr(g2.urllib.request, "urlopen", urlopen)
    return g2.deepseek_json_call("narration", [{"role": "user", "content": "x"}], {}, 1)


def test_a_free_provider_gets_its_url_headers_and_plain_body_and_costs_nothing(monkeypatch, tmp_path):
    sent = {}

    def urlopen(request, timeout=None):
        sent.update(url=request.full_url, headers={k.lower(): v for k, v in request.header_items()},
                    body=json.loads(request.data))
        return Reply(json.dumps({"id": "r1", "model": "minimax-m3.1-flash-free", "choices": [
            {"message": {"content": '{"ok": true}'}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50}}).encode())

    result = _call(monkeypatch, tmp_path, lp.PRESETS["opencode"], urlopen)
    assert result == {"ok": True}
    assert sent["url"] == "https://opencode.ai/zen/go/v1/chat/completions"
    assert sent["headers"]["user-agent"] == "AutoVideo/1.0" and sent["headers"]["authorization"] == "Bearer secret-key"
    assert sent["headers"]["x-opencode-session"] == f"ses_{g2.RUN_ID}"  # opencode answers 400 without it
    assert sent["body"]["model"] == "minimax-m3.1-flash-free" and sent["body"]["max_tokens"] == 32_768
    assert "thinking" not in sent["body"] and "reasoning_effort" not in sent["body"]
    assert sent["body"]["response_format"] == {"type": "json_object"}
    record = g2.CALL_RECORDS[0]
    assert record["provider"] == "opencode_api" and record["peak_uncached_cost_upper_bound_usd"] == 0
    assert "secret-key" not in (tmp_path / "calls" / "narration-attempt-1.request.json").read_text()


def test_deepseek_still_asks_for_thinking(monkeypatch, tmp_path):
    sent = {}

    def urlopen(request, timeout=None):
        sent.update(body=json.loads(request.data))
        return Reply(json.dumps({"choices": [{"message": {"content": "{}"}, "finish_reason": "stop"}], "usage": {}}).encode())

    _call(monkeypatch, tmp_path, lp.PRESETS["deepseek"], urlopen)
    assert sent["body"]["thinking"] == {"type": "enabled"} and sent["body"]["reasoning_effort"] == "high"
    assert sent["body"]["max_tokens"] == 100_000


@pytest.mark.parametrize("status, suffix", [(401, "AUTH_FAILED"), (402, "HTTP_ERROR"), (429, "RATE_LIMIT")])
def test_http_failures_are_named_after_the_provider_in_use(monkeypatch, tmp_path, status, suffix):
    def urlopen(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, status, "x", {}, io.BytesIO(b"{}"))

    with pytest.raises(g2.G2Fault, match=f"LLM_{suffix}.*opencode Go returned HTTP {status}"):
        _call(monkeypatch, tmp_path, lp.PRESETS["opencode"], urlopen)
    with pytest.raises(g2.G2Fault, match=f"DEEPSEEK_{suffix}"):
        _call(monkeypatch, tmp_path, lp.PRESETS["deepseek"], urlopen)


def test_unusable_settings_fail_before_any_network_call(monkeypatch, tmp_path):
    def urlopen(*args, **kwargs):
        raise AssertionError("no request should be made")

    bad = lp.load(None, {"AUTOVIDEO_LLM_PROVIDER": "custom"})
    with pytest.raises(g2.G2Fault, match="LLM_PROVIDER_INVALID"):
        _call(monkeypatch, tmp_path, bad, urlopen)


def test_probe_config_reports_the_chosen_provider(tmp_path, monkeypatch):
    from dataclasses import replace

    from g3 import config

    env = tmp_path / ".env"
    env.write_text("AUTOVIDEO_LLM_PROVIDER=opencode\nOPENCODE_API_KEY=k\n")
    for name in ("AUTOVIDEO_LLM_PROVIDER", "OPENCODE_API_KEY", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    probe = config.probe_config(replace(config.Settings(), key_file=env))
    assert probe["provider"] == "opencode Go" and probe["model"] == "minimax-m3.1-flash-free"
    assert probe["checks"]["llm"] is True and "deepseek" not in probe["checks"] and probe["api_key_value_exposed"] is False
    env.write_text("AUTOVIDEO_LLM_PROVIDER=opencode\n")
    assert config.probe_config(replace(config.Settings(), key_file=env))["checks"]["llm"] is False
