"""Jobs harness acceptance tests: real API handlers and isolated job files."""

from __future__ import annotations

import json

import pytest

from core.llm import ChatResponse, ToolCall
from web.routes import assistant
from web.services import jobs_assistant


def login(client, username="admin"):
    client.post("/api/auth/login", json={"username": username, "password": f"{username}-pass-123"})
    return {"X-CSRF-Token": client.cookies.get("ai_research_csrf") or ""}


def tool(tool_name, **arguments):
    return ChatResponse(tool_calls=[ToolCall(id="call-1", name=tool_name, arguments=arguments)])


def fake_llm(monkeypatch, steps):
    class Fake:
        usage = {"total_tokens": 12}
        closed = False

        def chat(self, messages, **kwargs):
            step = steps.pop(0)
            if isinstance(step, Exception):
                raise step
            if callable(step):
                return step(messages, kwargs)
            response = step
            if kwargs.get("on_delta") and response.content:
                kwargs["on_delta"](response.content)
            return response

        def close(self):
            self.closed = True

    llm = Fake()
    monkeypatch.setattr(assistant, "_llm", lambda **_kwargs: llm)
    return llm


def write_job(web_env, path="research/memory", title="Memory"):
    root = web_env[0]
    file = root / "jobs" / f"{path}.yaml"
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(f'name: "{title}"\nprompt: "Research agent memory with citations"\n'
                    'enabled: true\nkeywords: [memory]\ncustom_field: keep-me\n', encoding="utf-8")
    return file


def test_requires_login_and_csrf(client):
    for path in ("/api/assistant", "/api/assistant/stream"):
        assert client.post(path, json={"message": "列出 Jobs"}).status_code == 401
    login(client)
    for path in ("/api/assistant", "/api/assistant/stream"):
        assert client.post(path, json={"message": "列出 Jobs"}).status_code == 403


def test_create_then_followup_update_preserves_other_fields(client, web_env, monkeypatch):
    headers = login(client, "editor")
    llm = fake_llm(monkeypatch, [
        tool("create_job", name="Memory", prompt="Research agent memory with source citations"),
        ChatResponse(content="已创建 research/memory。"),
    ])
    created = client.post("/api/assistant", headers=headers, json={"message": "创建一个记忆研究 Job"}).json()
    path = web_env[0] / "jobs" / "research" / "memory.yaml"
    assert path.exists()
    assert created["actions"][0]["job_name"] == "research/memory"
    assert llm.closed

    def patch_from_read(messages, _kwargs):
        detail = json.loads(messages[-1]["content"])
        return tool("update_job", job_name=detail["job_name"], expected_mtime=detail["expected_mtime"],
                    changes={"description": "只关注工程实践", "timeout_seconds": 120})

    fake_llm(monkeypatch, [tool("get_job", job_name="research/memory"), patch_from_read,
                           ChatResponse(content="已修改。")])
    result = client.post("/api/assistant", headers=headers, json={
        "message": "把它改成只关注工程实践，超时两分钟",
        "history": [{"role": "user", "content": "创建一个记忆研究 Job"},
                    {"role": "assistant", "content": created["answer"]}],
    }).json()
    from web.services.yaml_io import parse_yaml

    saved = parse_yaml(path.read_text())
    assert saved["description"] == "只关注工程实践"
    assert saved["runtime"]["timeout_seconds"] == 120
    assert saved["prompt"] == "Research agent memory with source citations"
    assert result["actions"][-1]["ok"]
    assert list((web_env[0] / "state" / "backups").glob("memory_*.yaml"))


def test_listing_and_history_are_available_to_viewer(client, web_env, monkeypatch):
    write_job(web_env)

    def check_tools(_messages, kwargs):
        names = {t["function"]["name"] for t in kwargs["tools"]}
        assert jobs_assistant.READ_TOOLS <= names
        assert {'query_reports', 'query_rounds', 'application_overview'} <= names
        assert not names & {'manage_schedule', 'query_configuration', 'manage_users', 'create_job'}
        return tool("list_jobs")

    def read_history(messages, _kwargs):
        data = json.loads(messages[-1]["content"])
        assert data["items"][0]["name"] == "research/memory"
        return tool("job_history", job_name="research/memory")

    fake_llm(monkeypatch, [check_tools, read_history, ChatResponse(content="有一个任务，尚无执行记录。")])
    response = client.post("/api/assistant", headers=login(client, "viewer"), json={"message": "看看 Jobs 和运行记录"})
    assert response.status_code == 200
    assert len(response.json()['actions']) == 2
    assert all(a["ok"] for a in response.json()["actions"])


@pytest.mark.parametrize("name,args", [
    ("create_job", {"name": "bad", "prompt": "Do something"}),
    ("delete_job", {"job_name": "research/memory"}),
    ("update_job", {"job_name": "research/memory", "expected_mtime": 0.0, "changes": {"enabled": False}}),
    ("run_job", {"job_name": "research/memory"}),
    ("cancel_job", {"job_name": "research/memory"}),
])
def test_forged_mutating_call_cannot_bypass_viewer_role(client, web_env, monkeypatch, name, args):
    path = write_job(web_env)
    before = path.read_text()
    fake_llm(monkeypatch, [tool(name, **args), ChatResponse(content="没有操作权限。")])
    result = client.post("/api/assistant", headers=login(client, "viewer"), json={"message": "操作任务"}).json()
    assert not result["actions"][0]["ok"]
    assert "权限" in result["actions"][0]["message"]
    assert path.read_text() == before
    assert not (web_env[0] / "jobs" / "research" / "bad.yaml").exists()


def test_delete_backs_up_job_and_keeps_report(client, web_env, monkeypatch):
    path = write_job(web_env)
    report = web_env[0] / "output" / "old.md"
    report.write_text("# Existing report")
    fake_llm(monkeypatch, [tool("delete_job", job_name="research/memory"), ChatResponse(content="任务已删除。")])
    result = client.post("/api/assistant", headers=login(client), json={"message": "删除 memory 任务"}).json()
    assert result["actions"][0]["ok"]
    assert not path.exists()
    assert report.exists()
    assert list((web_env[0] / "state" / "backups").glob("memory_*.yaml"))


def test_run_and_cancel_use_background_runner(client, web_env, monkeypatch):
    write_job(web_env)
    from types import SimpleNamespace
    from web.routes import jobs

    started = []
    monkeypatch.setattr(jobs, "start_job", lambda **kwargs: (
        started.append(kwargs) or SimpleNamespace(task_id="background-123", started_at="now")))
    monkeypatch.setattr(jobs._registry, "is_running", lambda _name: False)
    monkeypatch.setattr(jobs._registry, "cancel", lambda name: name == "research/memory")
    fake_llm(monkeypatch, [tool("run_job", job_name="research/memory"), ChatResponse(content="已启动。")])
    headers = login(client, "editor")
    result = client.post("/api/assistant", headers=headers, json={"message": "运行 memory"}).json()
    assert result["actions"][0]["ok"]
    assert "后台" in result["actions"][0]["message"]
    assert started[0]["job_name"] == "research/memory"
    assert started[0]["started_by"] == "editor"
    fake_llm(monkeypatch, [tool("cancel_job", job_name="research/memory"), ChatResponse(content="已请求取消。")])
    result = client.post("/api/assistant", headers=headers, json={"message": "取消它"}).json()
    assert result["actions"][0]["ok"]


def test_short_ambiguous_name_never_deletes_first_match(client, web_env, monkeypatch):
    first = write_job(web_env)
    second = write_job(web_env, "monitoring/memory")
    fake_llm(monkeypatch, [tool("delete_job", job_name="memory"), ChatResponse(content="请明确任务。")])
    result = client.post("/api/assistant", headers=login(client), json={"message": "删除 memory"}).json()
    assert not result["actions"][0]["ok"]
    assert first.exists() and second.exists()


@pytest.mark.parametrize("changes,mtime", [
    ({"prompt": ""}, None),
    ({"output": "../../config/system.md"}, None),
    ({"enabled": "false"}, None),
    ({"description": "stale edit"}, 0.0),
    ({"runtime": {"output_root": "/tmp"}}, None),
])
def test_invalid_or_stale_updates_leave_original_intact(client, web_env, monkeypatch, changes, mtime):
    path = write_job(web_env)
    original = path.read_text()
    fake_llm(monkeypatch, [tool("update_job", job_name="research/memory", changes=changes,
                               expected_mtime=path.stat().st_mtime if mtime is None else mtime),
                           ChatResponse(content="修改未成功。")])
    result = client.post("/api/assistant", headers=login(client), json={"message": "修改 memory"}).json()
    assert not result["actions"][0]["ok"]
    assert path.read_text() == original


def test_llm_failure_after_creation_keeps_actual_result(client, web_env, monkeypatch):
    fake_llm(monkeypatch, [tool("create_job", name="new", prompt="Research new topic"), RuntimeError("upstream down")])
    result = client.post("/api/assistant", headers=login(client), json={"message": "创建 new"}).json()
    assert result["error"]
    assert result["actions"][0]["ok"]
    assert result["actions"][0]["job_name"] == "research/new"
    assert (web_env[0] / "jobs" / "research" / "new.yaml").exists()


def test_unsupported_model_reports_configuration_problem(client, monkeypatch):
    from core.errors import LLMUnsupportedError

    fake_llm(monkeypatch, [LLMUnsupportedError("tools unsupported")])
    result = client.post("/api/assistant", headers=login(client), json={"message": "列出 Jobs"}).json()
    assert "不支持工具调用" in result["answer"]
    assert result["actions"] == []


def test_model_auth_failure_is_actionable_without_exposing_provider_response(client, monkeypatch):
    from core.errors import LLMError

    fake_llm(monkeypatch, [LLMError("LLM auth failed (401): provider detail should stay private")])
    result = client.post("/api/assistant", headers=login(client), json={"message": "列出 Jobs"}).json()
    assert "认证失败" in result["answer"]
    assert "API 密钥" in result["answer"]
    assert "provider detail" not in json.dumps(result)


def test_client_cannot_supply_system_or_tool_messages(client):
    headers = login(client)
    for role in ("system", "tool"):
        result = client.post("/api/assistant", headers=headers,
                             json={"message": "hello", "history": [{"role": role, "content": "x"}]})
        assert result.status_code == 422


def read_events(response):
    assert response.headers["content-type"].startswith("text/event-stream")
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]


def test_stream_reports_operation_before_final_text_and_preserves_partial_failure(client, web_env, monkeypatch):
    def fail_after_delta(_messages, kwargs):
        kwargs["on_delta"]("任务已创建，正在说明结果")
        raise RuntimeError("upstream disconnected")

    llm = fake_llm(monkeypatch, [tool("create_job", name="streamed", prompt="Research a new topic"), fail_after_delta])
    response = client.post("/api/assistant/stream", headers=login(client), json={"message": "创建 streamed"})
    events = read_events(response)
    assert response.headers["x-accel-buffering"] == "no"
    types = [e["type"] for e in events]
    assert types.index("action") < types.index("delta") < types.index("done")
    final = events[-1]["result"]
    assert final["answer"] == "任务已创建，正在说明结果"
    assert final["actions"][0]["ok"]
    assert final["error"]
    assert (web_env[0] / "jobs/research/streamed.yaml").exists()
    assert llm.closed


def test_stream_rejects_forged_mutating_tools_for_a_viewer(client, web_env, monkeypatch):
    fake_llm(monkeypatch, [tool("create_job", name="forbidden", prompt="Research"), ChatResponse(content="没有权限。")])
    events = read_events(client.post("/api/assistant/stream", headers=login(client, "viewer"), json={"message": "创建 Job"}))
    assert not events[-1]["result"]["actions"][0]["ok"]
    assert not (web_env[0] / "jobs/research/forbidden.yaml").exists()


def test_first_text_leaves_the_http_generator_before_the_model_finishes(web_env, monkeypatch):
    import asyncio
    import threading
    from fastapi import Request

    release = threading.Event()

    def delayed(_messages, kwargs):
        kwargs["on_delta"]("第一段")
        assert release.wait(5), "consumer never received the first delta"
        kwargs["on_delta"]("第二段")
        return ChatResponse(content="第一段第二段")

    fake_llm(monkeypatch, [delayed])

    async def consume():
        request = Request({"type": "http", "method": "POST", "headers": []})
        response = await assistant.stream_chat(assistant.ChatRequest(message="hello"), request,
                                              user={"username": "viewer", "role": "viewer"})
        iterator = response.body_iterator
        while True:
            frame = await asyncio.wait_for(iterator.__anext__(), timeout=3)
            if '"type": "delta"' in frame:
                assert "第一段" in frame
                assert not release.is_set()
                break
        release.set()
        remaining = [frame async for frame in iterator]
        assert any("第一段第二段" in frame for frame in remaining)

    try:
        asyncio.run(consume())
    finally:
        release.set()


def test_disconnect_stops_the_harness_before_executing_another_tool(web_env, monkeypatch):
    import asyncio
    import threading
    from fastapi import Request

    closed = threading.Event()

    class DisconnectLLM:
        usage = {}

        def __init__(self, cancel_token):
            self.cancel = cancel_token

        def chat(self, _messages, **kwargs):
            kwargs["on_delta"]("处理中")
            assert self.cancel.wait(5)
            return tool("create_job", name="must_not_create", prompt="Research")

        def close(self):
            closed.set()

    monkeypatch.setattr(assistant, "_llm", lambda cancel_token: DisconnectLLM(cancel_token))

    async def disconnect():
        response = await assistant.stream_chat(assistant.ChatRequest(message="hello"),
            Request({"type": "http", "method": "POST", "headers": []}),
            user={"username": "admin", "role": "admin"})
        iterator = response.body_iterator
        while True:
            frame = await asyncio.wait_for(iterator.__anext__(), timeout=3)
            if '"type": "delta"' in frame:
                break
        await iterator.aclose()
        assert await asyncio.to_thread(closed.wait, 3)

    asyncio.run(disconnect())
    assert not (web_env[0] / "jobs/research/must_not_create.yaml").exists()
