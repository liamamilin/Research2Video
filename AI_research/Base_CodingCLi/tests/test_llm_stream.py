"""Incremental model text and fragmented tool calls share one real stream."""

import threading
from types import SimpleNamespace as NS

import pytest

from core.errors import LLMError
from core.llm import LLMClient, LLMConfig


def chunk(text=None, calls=None, finish=None, reasoning=None):
    return NS(usage=None, choices=[NS(finish_reason=finish,
        delta=NS(content=text, tool_calls=calls, reasoning_content=reasoning))])


def call(index, name=None, arguments=None, id=None):
    return NS(index=index, id=id, function=NS(name=name, arguments=arguments))


def client_for(chunks, **options):
    class Stream:
        closed = False

        def __iter__(self):
            return iter(chunks)

        def close(self):
            self.closed = True

    stream = Stream()
    requests = []

    def create(**kwargs):
        requests.append(kwargs)
        return stream

    client = LLMClient.__new__(LLMClient)
    client.config = LLMConfig(model="deepseek-test", base_url="http://test.invalid/v1", **options)
    client._cancel = threading.Event()
    client._progress = lambda _event: None
    client._client = NS(chat=NS(completions=NS(create=create)))
    client.usage = {"requests": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    return client, stream, requests


TOOLS = [{"type": "function", "function": {"name": "get_job", "parameters": {"type": "object"}}}]


def test_text_is_delivered_before_the_upstream_stream_finishes():
    deltas = []

    def upstream():
        yield chunk(text="第一段")
        assert deltas == ["第一段"]
        yield chunk(text="第二段", finish="stop")

    client, stream, requests = client_for(upstream(), stream=False,
        extra_body={"thinking": {"type": "disabled"}})
    result = client.chat([{"role": "user", "content": "hello"}], tools=TOOLS, stream=True, on_delta=deltas.append)
    assert deltas == ["第一段", "第二段"]
    assert result.content == "第一段第二段"
    assert requests[0]["stream"] is True
    assert requests[0]["tools"] == TOOLS
    assert requests[0]["extra_body"] == {"thinking": {"type": "disabled"}}
    assert stream.closed


def test_the_response_names_the_model_that_answered():
    client, _stream, _requests = client_for([chunk(text="hi", finish="stop")], stream=False)
    assert client.chat([{"role": "user", "content": "x"}], stream=True).model == "deepseek-test"


def test_filtered_incomplete_tool_stream_hands_the_original_request_to_the_fallback():
    client, rejected, requests = client_for([
        chunk(calls=[call(0, name="get_job", arguments='{"job_name":', id="rejected")], finish="content_filter"),
        NS(usage=NS(prompt_tokens=10, completion_tokens=2, total_tokens=12), choices=[]),
    ], fallback_models=["backup"])
    _, accepted, _ = client_for([
        chunk(calls=[call(0, name="get_job", arguments='{"job_name":"daily"}', id="accepted")], finish="tool_calls"),
    ])

    def create(**kwargs):
        requests.append(kwargs)
        return rejected if kwargs["model"] == "deepseek-test" else accepted

    client._client.chat.completions.create = create
    messages = [{"role": "user", "content": "查询今日任务"}]
    response = client.chat(messages, tools=TOOLS, stream=True)
    assert [r["model"] for r in requests] == ["deepseek-test", "backup"]
    assert all(r["messages"] == messages and r["tools"] == TOOLS for r in requests)
    assert response.model == "backup" and len(response.tool_calls) == 1
    assert response.tool_calls[0].id == "accepted" and response.tool_calls[0].arguments == {"job_name": "daily"}
    assert client.usage["total_tokens"] == 12
    assert rejected.closed and accepted.closed


def test_interleaved_tool_arguments_are_assembled_before_execution():
    client, stream, _requests = client_for([
        chunk(calls=[call(0, name="get_", arguments='{"job_', id="a"),
                     call(1, name="get_job", arguments='{"job_name":"b', id="b")]),
        chunk(calls=[call(0, name="job", arguments='name":"a"}'),
                     call(1, arguments='"}')], finish="tool_calls"),
        NS(usage=NS(prompt_tokens=10, completion_tokens=20, total_tokens=30), choices=[]),
    ])
    result = client.chat([], tools=TOOLS, stream=True)
    assert [(t.id, t.name, t.arguments) for t in result.tool_calls] == [
        ("a", "get_job", {"job_name": "a"}), ("b", "get_job", {"job_name": "b"})]
    assert client.usage["total_tokens"] == 30
    assert result.assistant_message["tool_calls"][0]["function"]["arguments"] == '{"job_name": "a"}'
    assert stream.closed


def test_reasoning_is_retained_for_the_model_but_never_sent_as_text():
    client, _stream, _requests = client_for([
        chunk(reasoning="private reasoning"), chunk(text="可见回复", finish="stop"),
    ])
    deltas = []
    response = client.chat([], tools=TOOLS, stream=True, on_delta=deltas.append)
    assert deltas == ["可见回复"]
    assert response.assistant_message["reasoning_content"] == "private reasoning"


@pytest.mark.parametrize("arguments,finish", [
    ('{"job_name":', "tool_calls"), ('[]', "tool_calls"),
    ('{"job_name":"a"}', "length"), ('{"job_name":"a"}', None),
])
def test_incomplete_or_invalid_tool_streams_never_become_commands(arguments, finish):
    client, stream, _requests = client_for([
        chunk(calls=[call(0, name="get_job", arguments=arguments, id="a")], finish=finish),
    ])
    with pytest.raises(LLMError):
        client.chat([], tools=TOOLS, stream=True)
    assert stream.closed


def test_a_partially_displayed_reply_is_not_retried_or_duplicated():
    class TemporaryFailure(Exception):
        status_code = 503

    def upstream():
        yield chunk(text="已显示的内容")
        raise TemporaryFailure("temporary failure")

    client, stream, requests = client_for(upstream(), max_retries=3)
    deltas = []
    with pytest.raises(LLMError):
        client.chat([], tools=TOOLS, stream=True, on_delta=deltas.append)
    assert deltas == ["已显示的内容"]
    assert len(requests) == 1
    assert stream.closed


def test_stream_options_rejection_retries_before_reading_any_text():
    client, stream, requests = client_for([chunk(text="hello", finish="stop")])
    create = client._client.chat.completions.create

    def reject_options(**kwargs):
        if "stream_options" in kwargs:
            requests.append(kwargs)
            raise ValueError("unsupported stream_options")
        return create(**kwargs)

    client._client.chat.completions.create = reject_options
    assert client.chat([], tools=TOOLS, stream=True).content == "hello"
    assert len(requests) == 2
    assert "stream_options" not in requests[-1]
    assert stream.closed
