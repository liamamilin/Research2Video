"""Provider cache/cost telemetry survives both transports without double counting."""
import threading
from types import SimpleNamespace as NS

import pytest

from core.llm import LLMClient, LLMConfig, _usage_counts
from tests.test_llm_stream import client_for, chunk


def usage():
    return NS(prompt_tokens=1000, completion_tokens=200, total_tokens=1200,
              prompt_tokens_details=NS(cached_tokens=800),
              completion_tokens_details=NS(reasoning_tokens=100), cost=0.001)


def test_cache_and_reasoning_are_not_added_to_total():
    value = _usage_counts(usage())
    assert value['total_tokens'] == 1200
    assert value['cache_read_tokens'] == 800
    assert value['reasoning_tokens'] == 100
    assert value['cost_usd'] == .001
    assert value['cost_reported_requests'] == 1
    assert _usage_counts({'prompt_tokens': 10, 'completion_tokens': 5}) == {
        'prompt_tokens': 10, 'completion_tokens': 5, 'total_tokens': 15}


def test_stream_usage_is_counted_once_with_provider_metadata():
    client, _, _ = client_for([chunk(text='结果', finish='stop'), NS(usage=usage(), choices=[])])
    result = client.chat([{'role': 'user', 'content': 'hello'}], stream=True)
    assert result.usage['cache_read_tokens'] == 800
    assert client.usage['requests'] == 1
    assert client.usage['total_tokens'] == 1200
    assert client.usage['cost_usd'] == .001


def test_nonstream_usage_keeps_unknown_cost_unknown():
    client, _, _ = client_for([])
    first = NS(usage=usage(), choices=[NS(finish_reason='stop',
        message=NS(content='结果', tool_calls=[], reasoning_content=None))], model='deepseek-test')
    client._parse_response(first)
    first.usage = NS(prompt_tokens=10, completion_tokens=5, total_tokens=15)
    client._parse_response(first)
    assert client.usage['requests'] == 2
    assert client.usage['total_tokens'] == 1215
    assert client.usage['cost_reported_requests'] == 1
    assert client.usage['cache_reported_requests'] == 1


def test_conversation_session_is_stable_before_first_call(monkeypatch):
    made = []

    class Transport:
        closed = False

        def close(self):
            self.closed = True

    def make(self):
        transport = Transport()
        made.append((transport, self._resolved_headers()))
        return transport

    monkeypatch.setattr(LLMClient, '_make_client', make)
    config = LLMConfig(model='model', base_url='http://test.invalid',
                       extra_headers={'x-opencode-session': '{session_id}'})
    first, second = LLMClient(config), LLMClient(config)
    first.set_session_id('assistant_conversation')
    second.set_session_id('assistant_conversation')
    assert first._resolved_headers() == second._resolved_headers()
    assert made[0][0].closed and made[1][0].closed
    assert first._client is None
    first._chat_started = True
    with pytest.raises(ValueError, match='已经开始'):
        first.set_session_id('other')
    with pytest.raises(ValueError):
        second.set_session_id('invalid\nheader')
