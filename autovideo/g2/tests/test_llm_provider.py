import io
import json
import urllib.error
import http.client

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
    monkeypatch.setattr(g2, "MODELS", [provider.model, *provider.fallback_models])
    monkeypatch.setattr(g2, "_ACTIVE_MODEL", 0)
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
    probe = config.probe_config(replace(config.Settings(), key_file=env, data_dir=tmp_path / "data"))
    assert probe["provider"] == "opencode Go" and probe["model"] == "minimax-m3.1-flash-free"
    assert probe["checks"]["llm"] is True and "deepseek" not in probe["checks"] and probe["api_key_value_exposed"] is False
    env.write_text("AUTOVIDEO_LLM_PROVIDER=opencode\n")
    assert config.probe_config(replace(config.Settings(), key_file=env, data_dir=tmp_path / "data"))["checks"]["llm"] is False


def test_a_402_moves_to_the_next_model_for_the_rest_of_the_run_and_nothing_else_does(monkeypatch, tmp_path):
    import urllib.error

    seen = []

    def urlopen(request, timeout=None):
        model = json.loads(request.data)["model"]
        seen.append(model)
        if model == "dead-free":
            raise urllib.error.HTTPError(request.full_url, 402, "Payment Required", {}, None)
        return Reply(json.dumps({"id": "r1", "model": model, "choices": [
            {"message": {"content": '{"ok": true}'}, "finish_reason": "stop"}], "usage": {}}).encode())

    provider = lp.replace(lp.PRESETS["opencode"], model="dead-free", fallback_models=("paid-a", "paid-b"))
    assert _call(monkeypatch, tmp_path, provider, urlopen) == {"ok": True}
    assert seen == ["dead-free", "paid-a"] and g2.active_model() == "paid-a"
    assert g2.deepseek_json_call("plan", [{"role": "user", "content": "x"}], {}, 1) == {"ok": True}
    assert seen[2:] == ["paid-a"]  # no second try of the dead one
    assert [r["model"] for r in g2.CALL_RECORDS][-1] == "paid-a"


def test_other_errors_and_a_402_with_no_fallback_left_still_fail(monkeypatch, tmp_path):
    import urllib.error

    def urlopen(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, 402, "Payment Required", {}, None)

    provider = lp.replace(lp.PRESETS["opencode"], model="a", fallback_models=("b",))
    with pytest.raises(g2.G2Fault) as caught:
        _call(monkeypatch, tmp_path, provider, urlopen)
    assert "402" in str(caught.value)
    assert g2.active_model() == "b"


def test_fallback_models_come_from_the_saved_settings_without_the_main_model_or_repeats(tmp_path):
    saved = tmp_path / "s.json"
    saved.write_text(json.dumps({"base_url": "https://opencode.ai/zen/go/v1", "model": "m", "key_env": "K",
                                 "fallback_models": ["m", "x", "x", " y "]}))
    provider = lp.load(None, {"AUTOVIDEO_LLM_SETTINGS_FILE": str(saved)})
    assert provider.fallback_models == ("x", "y")
    assert lp.validate_settings({"base_url": "https://a/v1", "model": "m", "fallback_models": "m, z"})["fallback_models"] == ["z"]


def test_transient_520_retries_same_request_and_preserves_each_attempt(monkeypatch, tmp_path):
    sent, delays = [], []
    monkeypatch.setattr(g2.time, "sleep", delays.append)

    def urlopen(request, timeout=None):
        sent.append(request.data)
        if len(sent) < 3:
            raise urllib.error.HTTPError(request.full_url, 520, "secret error body", {}, io.BytesIO(b"secret-key"))
        return Reply(json.dumps({"choices": [{"message": {"content": '{"ok": true}'}}], "usage": {}}).encode())

    assert _call(monkeypatch, tmp_path, lp.PRESETS["opendesign"], urlopen) == {"ok": True}
    assert len(sent) == 3 and sent[0] == sent[1] == sent[2] and delays == [2, 4]
    assert len(g2.CALL_RECORDS) == 3 and len(list((tmp_path / "calls").glob('*.response.json'))) == 3
    assert len({r["response_file"] for r in g2.CALL_RECORDS}) == 3
    diagnostics = (tmp_path / "logs/http-retries.jsonl").read_text()
    assert "520" in diagnostics and "secret" not in diagnostics


def test_persistent_520_stops_after_three_requests_without_switching_models(monkeypatch, tmp_path):
    seen, delays = [], []
    monkeypatch.setattr(g2.time, "sleep", delays.append)

    def urlopen(request, timeout=None):
        seen.append(json.loads(request.data)["model"])
        raise urllib.error.HTTPError(request.full_url, 520, "x", {}, None)

    provider = lp.replace(lp.PRESETS["opendesign"], fallback_models=("another-model",))
    with pytest.raises(g2.G2Fault, match="HTTP 520"):
        _call(monkeypatch, tmp_path, provider, urlopen)
    assert seen == [provider.model] * 3 and delays == [2, 4]


def test_transport_retries_respect_the_shared_call_budget(monkeypatch, tmp_path):
    delays = []
    monkeypatch.setattr(g2, "MAX_TOTAL_CALLS", 1)
    monkeypatch.setattr(g2.time, "sleep", delays.append)

    def urlopen(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, 520, "x", {}, None)

    with pytest.raises(g2.G2Fault, match="HTTP 520"):
        _call(monkeypatch, tmp_path, lp.PRESETS["opendesign"], urlopen)
    assert len(g2.CALL_RECORDS) == 1 and delays == []


@pytest.mark.parametrize("status", [400, 401, 402, 403, 429])
def test_client_and_quota_errors_do_not_enter_the_transient_retry_loop(monkeypatch, tmp_path, status):
    sent = []
    monkeypatch.setattr(g2.time, "sleep", lambda _: pytest.fail("client errors must not wait and retry"))

    def urlopen(request, timeout=None):
        sent.append(request)
        raise urllib.error.HTTPError(request.full_url, status, "x", {}, None)

    with pytest.raises(g2.G2Fault):
        _call(monkeypatch, tmp_path, lp.PRESETS["opendesign"], urlopen)
    assert len(sent) == len(g2.CALL_RECORDS) == 1


@pytest.mark.parametrize("failure", [http.client.RemoteDisconnected("secret provider message"),
    http.client.IncompleteRead(b"secret partial response", 100), ConnectionResetError("reset"),
    TimeoutError("timed out"), urllib.error.URLError(ConnectionResetError("reset"))])
def test_connection_loss_retries_only_the_current_model_request(monkeypatch, tmp_path, failure):
    calls, delays = [], []
    monkeypatch.setattr(g2.time, "sleep", delays.append)

    def urlopen(request, timeout=None):
        calls.append(json.loads(request.data))
        if len(calls) == 1:
            raise failure
        return Reply(json.dumps({"choices": [{"message": {"content": '{"ok": true}'},
                                               "finish_reason": "stop"}], "usage": {}}).encode())

    assert _call(monkeypatch, tmp_path, lp.PRESETS["opendesign"], urlopen) == {"ok": True}
    assert len(calls) == 2 and calls[0] == calls[1] and delays == [2]
    assert len(g2.CALL_RECORDS) == 2 and g2.CALL_RECORDS[0]["status"] == "failed"
    assert len(list((tmp_path / "calls").glob("*.response.json"))) == 2
    logs = (tmp_path / "logs/http-retries.jsonl").read_text()
    assert "secret" not in logs
    assert "secret" not in (tmp_path / "calls/narration-attempt-1.response.json").read_text()


@pytest.mark.parametrize("budget,expected", [(16, 3), (1, 1)])
def test_disconnect_exhaustion_has_a_specific_error_and_preserves_the_budget(monkeypatch, tmp_path, budget, expected):
    monkeypatch.setattr(g2, "MAX_TOTAL_CALLS", budget)
    monkeypatch.setattr(g2.time, "sleep", lambda _: None)

    def urlopen(request, timeout=None):
        raise http.client.RemoteDisconnected("no response")

    with pytest.raises(g2.G2Fault, match="LLM_UNAVAILABLE.*RemoteDisconnected"):
        _call(monkeypatch, tmp_path, lp.PRESETS["opendesign"], urlopen)
    assert len(g2.CALL_RECORDS) == expected
