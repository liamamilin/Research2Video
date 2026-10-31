"""The LLM client's transport layer: retries, classification, parsing, usage.

This is the code that decides whether a failed request costs one retry or a
whole run, and whether a truncated answer is reported as one. None of it ran in
any test before: tests/test_llm.py only exercised a reasoning-echo heuristic
through ``__new__``, so the HTTP path had no coverage at all.
"""

from __future__ import annotations

import threading
import types

import pytest

from core.errors import CancelledError, LLMError, LLMUnsupportedError
from core.llm import ChatResponse, LLMClient, LLMConfig, ToolCall


def make_client(**overrides) -> LLMClient:
    """A client wired to fakes, skipping __init__'s network-touching setup."""
    settings = {"model": "test-model", "base_url": "http://llm.invalid/v1",
                "api_key": "k", "max_retries": 2, "stream": False}
    settings.update(overrides)
    config = LLMConfig(**settings)
    client = LLMClient.__new__(LLMClient)
    client.config = config
    client._cancel = threading.Event()
    client.usage = {"requests": 0, "prompt_tokens": 0, "completion_tokens": 0,
                    "total_tokens": 0}
    client._client = None
    return client


def reply(content="hello", tool_calls=None, finish_reason="stop"):
    return ChatResponse(content=content, tool_calls=tool_calls or [],
                        finish_reason=finish_reason)


class FakeAPIStatusError(Exception):
    """Stands in for openai.APIStatusError, which takes a response."""

    def __init__(self, code: int):
        super().__init__(f"HTTP {code}")
        self.status_code = code
        self.response = types.SimpleNamespace(status_code=code, headers={})


# --- retry policy ------------------------------------------------------------

@pytest.mark.parametrize("code", [408, 409, 429, 500, 502, 503, 504])
def test_transient_statuses_are_retried(code, monkeypatch):
    client = make_client()
    calls = {"n": 0}

    def boom(_messages, **_kw):
        calls["n"] += 1
        raise FakeAPIStatusError(code)

    monkeypatch.setattr(client, "_chat_cancellable", boom)
    monkeypatch.setattr(client, "_sleep_cancellable", lambda _s: None)

    with pytest.raises(LLMError):
        client.chat([{"role": "user", "content": "x"}])
    # 1 initial attempt + max_retries.
    assert calls["n"] == client.config.max_retries + 1


@pytest.mark.parametrize("code", [400, 401, 403, 404, 422])
def test_client_errors_are_not_retried(code, monkeypatch):
    """Retrying a 401 just burns money; it will never become a 200."""
    client = make_client()
    calls = {"n": 0}

    def boom(_messages, **_kw):
        calls["n"] += 1
        raise FakeAPIStatusError(code)

    monkeypatch.setattr(client, "_chat_cancellable", boom)
    with pytest.raises(LLMError):
        client.chat([{"role": "user", "content": "x"}])
    assert calls["n"] == 1


class FakeAPIError(Exception):
    """Stands in for openai.APIError: a code from the body, no HTTP status."""

    def __init__(self, code: str):
        super().__init__(f"[code={code}] Error reading stream: connection reset by peer")
        self.code = code


def test_a_stream_reset_upstream_is_retried(monkeypatch):
    """Seen live: a gateway reported a mid-stream reset as a plain APIError."""
    client = make_client()
    state = {"n": 0}

    def flaky(_messages, **_kw):
        state["n"] += 1
        if state["n"] == 1:
            raise FakeAPIError("upstream_stream_interrupted")
        return reply("recovered")

    monkeypatch.setattr(client, "_chat_cancellable", flaky)
    monkeypatch.setattr(client, "_sleep_cancellable", lambda _s: None)
    assert client.chat([{"role": "user", "content": "x"}]).content == "recovered"
    assert state["n"] == 2


def test_other_provider_error_codes_are_not_retried(monkeypatch):
    client = make_client()
    calls = {"n": 0}

    def boom(_messages, **_kw):
        calls["n"] += 1
        raise FakeAPIError("invalid_request")

    monkeypatch.setattr(client, "_chat_cancellable", boom)
    with pytest.raises(LLMError):
        client.chat([{"role": "user", "content": "x"}])
    assert calls["n"] == 1


class FakeQuotaError(Exception):
    """Stands in for the 402 an OpenAI-compatible gateway sends when the
    account is out of money, e.g. ``{"code": "insufficient_balance", ...}``."""

    def __init__(self, code: str = "insufficient_balance"):
        super().__init__(f"[code={code}] Coding Plan quota is exhausted and wallet "
                         "balance is insufficient.")
        self.code = code


def test_a_quota_exhausted_model_falls_back_to_the_next_one(monkeypatch):
    client = make_client(model="free", fallback_models=["paid1", "paid2"])
    calls: list[str] = []

    def answer(_messages, **_kw):
        model = client._active_model()
        calls.append(model)
        if model == "free":
            raise FakeQuotaError()
        return reply(f"answered by {model}")

    monkeypatch.setattr(client, "_chat_cancellable", answer)
    monkeypatch.setattr(client, "_sleep_cancellable", lambda _s: None)
    result = client.chat([{"role": "user", "content": "x"}])
    # ``reply()`` builds a canned ChatResponse, so it doesn't set .model the
    # way the real _chat_once(_stream) does; that one-line pass-through is
    # exercised by the fake-transport tests in test_llm_stream.py instead.
    assert result.content == "answered by paid1"
    assert calls == ["free", "paid1"]  # one call per model, no backoff retry of "free"
    assert client._active_model() == "paid1"  # sticky: later calls skip the exhausted model


def test_quota_exhausted_with_no_fallback_configured_raises_at_once(monkeypatch):
    client = make_client(model="free")  # no fallback_models
    calls = {"n": 0}

    def boom(_messages, **_kw):
        calls["n"] += 1
        raise FakeQuotaError()

    monkeypatch.setattr(client, "_chat_cancellable", boom)
    with pytest.raises(LLMError, match="insufficient"):
        client.chat([{"role": "user", "content": "x"}])
    assert calls["n"] == 1  # no backoff retries of a model that cannot recover


def test_all_fallbacks_exhausted_names_every_model_tried(monkeypatch):
    client = make_client(model="free", fallback_models=["paid1", "paid2"])
    calls = {"n": 0}

    def boom(_messages, **_kw):
        calls["n"] += 1
        raise FakeQuotaError()

    monkeypatch.setattr(client, "_chat_cancellable", boom)
    with pytest.raises(LLMError) as caught:
        client.chat([{"role": "user", "content": "x"}])
    assert calls["n"] == 3  # free, paid1, paid2 -- each tried exactly once
    for model in ("free", "paid1", "paid2"):
        assert model in str(caught.value)


def test_a_fallback_model_gets_its_own_transient_retry_budget(monkeypatch):
    """A quota error on model 1 must not burn model 2's retry allowance."""
    client = make_client(model="free", fallback_models=["paid1"], max_retries=2)
    state = {"n": 0}

    def answer(_messages, **_kw):
        state["n"] += 1
        if state["n"] == 1:
            raise FakeQuotaError()  # switches to paid1
        if state["n"] in (2, 3):
            raise FakeAPIStatusError(503)  # paid1: transient, within its own budget
        return reply("ok")

    monkeypatch.setattr(client, "_chat_cancellable", answer)
    monkeypatch.setattr(client, "_sleep_cancellable", lambda _s: None)
    assert client.chat([{"role": "user", "content": "x"}]).content == "ok"
    assert state["n"] == 4


def test_duplicate_fallback_models_are_tried_only_once(monkeypatch):
    client = make_client(model="free", fallback_models=["free", "paid1", "paid1"])
    calls: list[str] = []

    def answer(_messages, **_kw):
        model = client._active_model()
        calls.append(model)
        if model == "free":
            raise FakeQuotaError()
        return reply("ok")

    monkeypatch.setattr(client, "_chat_cancellable", answer)
    monkeypatch.setattr(client, "_sleep_cancellable", lambda _s: None)
    client.chat([{"role": "user", "content": "x"}])
    assert calls == ["free", "paid1"]


def test_a_partially_displayed_reply_blocks_fallback_too(monkeypatch):
    """The no-replay-after-display rule applies to a quota fallback as well."""
    client = make_client(model="free", fallback_models=["paid1"], stream=True)
    deltas = []

    def answer(_messages, **kwargs):
        kwargs["on_delta"]("partial")
        raise FakeQuotaError()

    monkeypatch.setattr(client, "_chat_cancellable", answer)
    with pytest.raises(LLMError):
        client.chat([{"role": "user", "content": "x"}], on_delta=deltas.append)
    assert deltas == ["partial"]
    assert client._active_model() == "free"  # never switched


def test_reasoning_echo_follows_a_fallback_switch(monkeypatch):
    client = make_client(model="longcat", fallback_models=["deepseek-v4.1-flash"])
    assert client._should_echo_reasoning() is False
    client._model_idx = 1
    assert client._should_echo_reasoning() is True


def test_a_retry_that_succeeds_returns_normally(monkeypatch):
    client = make_client()
    state = {"n": 0}

    def flaky(_messages, **_kw):
        state["n"] += 1
        if state["n"] < 3:
            raise FakeAPIStatusError(503)
        return reply("recovered")

    monkeypatch.setattr(client, "_chat_cancellable", flaky)
    monkeypatch.setattr(client, "_sleep_cancellable", lambda _s: None)
    assert client.chat([{"role": "user", "content": "x"}]).content == "recovered"
    assert state["n"] == 3


def test_backoff_grows_and_is_capped(monkeypatch):
    client = make_client(max_retries=8)
    delays: list[float] = []
    monkeypatch.setattr(client, "_sleep_cancellable", delays.append)
    monkeypatch.setattr("core.llm.random.random", lambda: 0.0)
    monkeypatch.setattr(client, "_chat_cancellable",
                        lambda *_a, **_kw: (_ for _ in ()).throw(FakeAPIStatusError(500)))
    with pytest.raises(LLMError):
        client.chat([{"role": "user", "content": "x"}])
    assert delays == sorted(delays), "backoff did not increase"
    assert all(d <= 30.0 for d in delays), "backoff exceeded the cap"


def test_cancellation_before_a_call_is_honoured(monkeypatch):
    client = make_client()
    client._cancel.set()
    monkeypatch.setattr(client, "_chat_cancellable",
                        lambda *_a, **_kw: pytest.fail("must not call the API"))
    with pytest.raises(CancelledError):
        client.chat([{"role": "user", "content": "x"}])


def test_cancellation_is_not_retried(monkeypatch):
    client = make_client()
    calls = {"n": 0}

    def cancel_midway(_messages, **_kw):
        calls["n"] += 1
        raise CancelledError("stop")

    monkeypatch.setattr(client, "_chat_cancellable", cancel_midway)
    with pytest.raises(CancelledError):
        client.chat([{"role": "user", "content": "x"}])
    assert calls["n"] == 1, "a cancel was retried"


def test_unsupported_tool_calling_is_not_retried(monkeypatch):
    client = make_client()
    calls = {"n": 0}

    def unsupported(_messages, **_kw):
        calls["n"] += 1
        raise LLMUnsupportedError("no tools here")

    monkeypatch.setattr(client, "_chat_cancellable", unsupported)
    with pytest.raises(LLMUnsupportedError):
        client.chat([{"role": "user", "content": "x"}], tools=[{"type": "function"}])
    assert calls["n"] == 1


def test_default_streaming_preserves_existing_tool_call_behavior(monkeypatch):
    """Tool calls retain the one-shot default unless streaming is requested."""
    client = make_client(stream=True)
    seen: list[dict] = []

    def capture(_messages, **kw):
        seen.append(kw)
        return reply("ok")

    monkeypatch.setattr(client, "_chat_cancellable", capture)
    client.chat([{"role": "user", "content": "x"}])
    client.chat([{"role": "user", "content": "x"}], tools=[{"type": "function"}])
    assert seen[0].get("_stream") is True
    assert seen[1].get("_stream") is None and seen[1].get("tools")


# --- response handling -------------------------------------------------------

def test_usage_accumulates_across_calls(monkeypatch):
    client = make_client()

    def counted(_messages, **_kw):
        client.usage["requests"] += 1
        client.usage["prompt_tokens"] += 100
        client.usage["completion_tokens"] += 20
        return reply("ok")

    monkeypatch.setattr(client, "_chat_cancellable", counted)
    client.chat([{"role": "user", "content": "a"}])
    client.chat([{"role": "user", "content": "b"}])
    assert client.usage["requests"] == 2
    assert client.usage["prompt_tokens"] == 200
    assert client.usage["completion_tokens"] == 40


def test_finish_reason_survives_into_the_response(monkeypatch):
    """'length' means the answer was cut off; research.py now fails on it."""
    client = make_client()
    monkeypatch.setattr(client, "_chat_cancellable",
                        lambda *_a, **_kw: reply("half a report", finish_reason="length"))
    assert client.chat([{"role": "user", "content": "x"}]).finish_reason == "length"


def test_assistant_message_carries_tool_calls():
    response = ChatResponse(
        content="", finish_reason="tool_calls",
        tool_calls=[ToolCall(id="c1", name="search_web", arguments={"queries": ["x"]})])
    message = response.assistant_message
    assert message["tool_calls"][0]["function"]["name"] == "search_web"
    assert message["tool_calls"][0]["function"]["arguments"] == '{"queries": ["x"]}'


def test_tool_arguments_survive_non_string_payloads():
    """Some endpoints return the arguments already decoded."""
    call = ToolCall(id="c1", name="read_file", arguments={"path": "a.md"})
    payload = call if isinstance(call, dict) else {
        "id": call.id, "type": "function",
        "function": {"name": call.name, "arguments": call.arguments}}
    assert "read_file" in str(payload)


def test_content_normalisation_handles_structured_content():
    """Some providers return content as a list of parts rather than a string."""
    from core.llm import _normalize_content

    assert _normalize_content("plain") == "plain"
    # Parts are joined with newlines, so a multi-part answer keeps its shape.
    assert _normalize_content([{"type": "text", "text": "a"},
                               {"type": "text", "text": "b"}]) == "a\nb"
    assert _normalize_content(None) == ""


# --- configuration ------------------------------------------------------------

def test_config_is_read_from_system_yaml():
    cfg = LLMConfig.from_system({
        "ai": {"model": "m", "base_url": "http://x/v1", "api_key_env": "MY_KEY",
               "max_retries": 5, "timeout": 900, "request_timeout": 60,
               "max_tokens": 4096, "temperature": 0.7, "stream": False},
    })
    assert (cfg.model, cfg.max_retries, cfg.timeout) == ("m", 5, 900)
    assert (cfg.request_timeout, cfg.max_tokens) == (60, 4096)
    assert cfg.stream is False


def test_config_tolerates_a_missing_section():
    """An empty section yields defaults, not an AttributeError at call time."""
    cfg = LLMConfig.from_system({})
    assert cfg.max_retries >= 0 and cfg.timeout > 0
    assert cfg.stream is True  # the shipped default


def test_reasoning_echo_is_model_specific():
    def client(model, mode="auto"):
        c = LLMClient.__new__(LLMClient)
        c.config = LLMConfig(model=model, base_url="http://x", reasoning_content=mode)
        return c

    assert client("deepseek-v4.1-flash")._should_echo_reasoning() is True
    assert client("gpt-5.4")._should_echo_reasoning() is False
    # An explicit setting always wins.
    assert client("gpt-5.4", "always")._should_echo_reasoning() is True
    assert client("deepseek-v4.1-flash", "never")._should_echo_reasoning() is False


def _auth_client(api_key="", api_key_env=""):
    c = LLMClient.__new__(LLMClient)
    c.config = LLMConfig(model="m", base_url="http://x/v1",
                         api_key=api_key, api_key_env=api_key_env)
    return c


def test_a_401_with_a_rejected_key_names_the_provider():
    """The original incident: an amr URL with a different vendor's key."""
    c = _auth_client(api_key="sk-fixture-other-provider", api_key_env="LLM_API_KEY")
    c.config.base_url = "https://amr-link.open-design.ai/v1"
    msg = c._auth_message(401, "invalid_api_key")
    assert "Open Design" in msg
    assert "不能混用" in msg


def test_a_401_with_a_rejected_key_at_an_unknown_endpoint_stays_plain():
    c = _auth_client(api_key="k", api_key_env="LLM_API_KEY")
    c.config.base_url = "https://nobody.example/v1"
    msg = c._auth_message(401, "invalid_api_key")
    assert "401" in msg and "invalid_api_key" in msg
    assert "服务商" not in msg


def test_a_401_with_a_key_sent_is_reported_plainly():
    msg = _auth_client(api_key="k", api_key_env="LLM_API_KEY")._auth_message(401, "invalid_api_key")
    assert "401" in msg and "invalid_api_key" in msg
    assert "未发送" not in msg


def test_a_401_names_the_secret_pasted_into_the_env_field():
    """The incident: the key was in api_key_env, so nothing was sent.

    A bare ``invalid_api_key`` hides that, so the message has to say the header
    was absent and why.
    """
    c = _auth_client(api_key="", api_key_env="sk-amr-abc123")
    msg = c._auth_message(401, "invalid_api_key")
    assert "未发送 Authorization 头" in msg
    assert "sk-amr-abc123" in msg
    assert "密钥环境变量" in msg


def test_a_401_names_the_missing_env_var():
    msg = _auth_client(api_key="", api_key_env="LLM_API_KEY")._auth_message(401, "invalid_api_key")
    assert "未发送 Authorization 头" in msg
    assert "LLM_API_KEY" in msg
    assert "未设置" in msg


def test_from_system_records_the_env_name_for_diagnostics():
    cfg = LLMConfig.from_system(
        {"ai": {"model": "m", "base_url": "http://x/v1", "api_key_env": "SOME_KEY"}})
    assert cfg.api_key_env == "SOME_KEY"


def test_a_key_pasted_into_the_env_field_is_logged_as_such(caplog):
    import logging

    with caplog.at_level(logging.ERROR, logger="core.llm"):
        cfg = LLMConfig.from_system({
            "ai": {"model": "m", "base_url": "http://x/v1",
                   "api_key_env": "sk-amr-pasted"}})
    assert cfg.api_key == ""
    assert any("not an environment-variable name" in r.message for r in caplog.records)


# --- falling back for more than an empty account -----------------------------

class FakeBodyError(Exception):
    """An error with a status and the provider's own words, like the 410 ``ModelDeprecated`` that ended
    minimax-m3.1-flash-free on 2026-10-23."""

    def __init__(self, status: int, text: str):
        super().__init__(text)
        self.status_code = status
        self.response = types.SimpleNamespace(status_code=status, headers={})


@pytest.mark.parametrize("status,text", [
    (410, "ModelDeprecated: Model x has been deprecated."),
    (401, "invalid_api_key for this model"),
    (403, "your plan cannot use this model"),
    (404, "model not found"),
    (400, "The model `x` does not exist"),
    (422, "Unknown model: x"),
])
def test_a_model_the_endpoint_will_not_serve_falls_back(status, text, monkeypatch):
    client = make_client(model="old", fallback_models=["new"])
    calls: list[str] = []

    def answer(_messages, **_kw):
        calls.append(client._active_model())
        if client._active_model() == "old":
            raise FakeBodyError(status, text)
        return reply("from new")

    monkeypatch.setattr(client, "_chat_cancellable", answer)
    monkeypatch.setattr(client, "_sleep_cancellable", lambda _s: None)
    assert client.chat([{"role": "user", "content": "x"}]).content == "from new"
    assert calls == ["old", "new"]  # one call each, no backoff on a model that cannot recover
    assert client.model_switches[0]["from"] == "old" and client.model_switches[0]["to"] == "new"


def test_a_bad_request_or_a_tool_complaint_is_not_a_reason_to_switch(monkeypatch):
    for status, text in ((400, "messages: field required"), (422, "temperature must be <= 2")):
        client = make_client(model="old", fallback_models=["new"])
        calls: list[str] = []

        def answer(_messages, **_kw):
            calls.append(client._active_model())
            raise FakeBodyError(status, text)

        monkeypatch.setattr(client, "_chat_cancellable", answer)
        with pytest.raises(LLMError):
            client.chat([{"role": "user", "content": "x"}])
        assert calls == ["old"]  # every model would reject it the same way
    client = make_client(model="old", fallback_models=["new"])
    monkeypatch.setattr(client, "_chat_cancellable",
                        lambda _m, **_k: (_ for _ in ()).throw(FakeBodyError(400, "this model does not support tool_choice")))
    with pytest.raises(LLMUnsupportedError):
        client.chat([{"role": "user", "content": "x"}])
    assert client._active_model() == "old"


def test_every_model_refusing_the_key_names_them_all(monkeypatch):
    client = make_client(model="a", fallback_models=["b", "c"])
    calls = {"n": 0}

    def answer(_messages, **_kw):
        calls["n"] += 1
        raise FakeBodyError(401, "invalid_api_key")

    monkeypatch.setattr(client, "_chat_cancellable", answer)
    with pytest.raises(LLMError) as caught:
        client.chat([{"role": "user", "content": "x"}])
    assert calls["n"] == 3
    for model in ("a", "b", "c"):
        assert model in str(caught.value)


def test_a_model_that_keeps_failing_transiently_hands_over_to_the_next(monkeypatch):
    client = make_client(model="flaky", fallback_models=["steady"], max_retries=2)
    calls: list[str] = []

    def answer(_messages, **_kw):
        calls.append(client._active_model())
        if client._active_model() == "flaky":
            raise FakeAPIStatusError(503)
        return reply("ok")

    monkeypatch.setattr(client, "_chat_cancellable", answer)
    monkeypatch.setattr(client, "_sleep_cancellable", lambda _s: None)
    assert client.chat([{"role": "user", "content": "x"}]).content == "ok"
    assert calls == ["flaky"] * 3 + ["steady"]  # its own retry budget first, then the fallback
    assert client.model_switches[0]["reason"] == "重试用尽仍失败"


def test_transient_failures_without_a_fallback_still_end_after_the_retry_budget(monkeypatch):
    client = make_client(model="only", max_retries=2)
    calls = {"n": 0}

    def boom(_messages, **_kw):
        calls["n"] += 1
        raise FakeAPIStatusError(503)

    monkeypatch.setattr(client, "_chat_cancellable", boom)
    monkeypatch.setattr(client, "_sleep_cancellable", lambda _s: None)
    with pytest.raises(LLMError):
        client.chat([{"role": "user", "content": "x"}])
    assert calls["n"] == 3


def test_an_empty_reply_is_retried_and_then_handed_to_the_next_model(monkeypatch):
    client = make_client(model="silent", fallback_models=["talker"], max_retries=1)
    calls: list[str] = []

    def answer(_messages, **_kw):
        calls.append(client._active_model())
        return reply("") if client._active_model() == "silent" else reply("words")

    monkeypatch.setattr(client, "_chat_cancellable", answer)
    monkeypatch.setattr(client, "_sleep_cancellable", lambda _s: None)
    assert client.chat([{"role": "user", "content": "x"}]).content == "words"
    assert calls == ["silent", "silent", "talker"]


def test_an_empty_reply_that_is_a_tool_call_or_a_truncation_is_not_an_empty_reply(monkeypatch):
    client = make_client(model="m", fallback_models=["n"])
    call = ToolCall(id="1", name="search", arguments={})
    for response in (reply("", tool_calls=[call]), reply("", finish_reason="length")):
        monkeypatch.setattr(client, "_chat_cancellable", lambda _m, _r=response, **_k: _r)
        assert client.chat([{"role": "user", "content": "x"}]) is response
    assert client._active_model() == "m"


def test_an_empty_reply_from_every_model_fails_and_says_so(monkeypatch):
    client = make_client(model="a", fallback_models=["b"], max_retries=0)
    monkeypatch.setattr(client, "_chat_cancellable", lambda _m, **_k: reply(""))
    monkeypatch.setattr(client, "_sleep_cancellable", lambda _s: None)
    with pytest.raises(LLMError, match="a, b"):
        client.chat([{"role": "user", "content": "x"}])


@pytest.mark.parametrize("stream", [False, True])
def test_provider_filter_uses_configured_fallback_with_the_unchanged_request(monkeypatch, stream):
    client = make_client(model="a", fallback_models=["b"], max_retries=2, stream=stream)
    calls = []
    messages = [{"role": "user", "content": "介绍定时任务的使用入口"}]

    def answer(given, **kwargs):
        assert given == messages
        calls.append(client._active_model())
        return reply("discard this partial result", finish_reason="content_filter") if len(calls) == 1 else reply("完整结果")

    monkeypatch.setattr(client, "_chat_cancellable", answer)
    monkeypatch.setattr(client, "_sleep_cancellable", lambda _s: pytest.fail("a filtered completion must not back off"))
    assert client.chat(messages).content == "完整结果"
    assert calls == ["a", "b"]
    assert client.model_switches == [{"from": "a", "to": "b", "reason": "返回过滤结果（content_filter）"}]


@pytest.mark.parametrize("fallbacks", [[], ["b", "a", "b", "c"]])
def test_provider_filter_fails_after_each_configured_model_once(monkeypatch, fallbacks):
    client = make_client(model="a", fallback_models=fallbacks, max_retries=2)
    calls = []

    def filtered(_messages, **kwargs):
        calls.append(client._active_model())
        return reply("", finish_reason="content_filter")

    monkeypatch.setattr(client, "_chat_cancellable", filtered)
    monkeypatch.setattr(client, "_sleep_cancellable", lambda _s: pytest.fail("must not repeat a filtered model"))
    with pytest.raises(LLMError, match="content_filter") as caught:
        client.chat([{"role": "user", "content": "x"}])
    assert calls == list(dict.fromkeys(["a", *fallbacks]))
    if fallbacks:
        assert "tried: a, b, c" in str(caught.value)


def test_provider_filter_does_not_replay_already_displayed_stream_text(monkeypatch):
    client = make_client(model="a", fallback_models=["b"], max_retries=2)
    calls = []
    displayed = []

    def filtered(_messages, **kwargs):
        calls.append(client._active_model())
        kwargs["on_delta"]("已显示的前缀")
        return reply("已显示的前缀", finish_reason="content_filter")

    monkeypatch.setattr(client, "_chat_cancellable", filtered)
    with pytest.raises(LLMError, match="content_filter"):
        client.chat([{"role": "user", "content": "x"}], stream=True, on_delta=displayed.append)
    assert calls == ["a"]
    assert displayed == ["已显示的前缀"]
