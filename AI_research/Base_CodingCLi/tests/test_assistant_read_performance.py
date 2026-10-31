"""Publication cards share account-scoped reads without losing older receipts."""
import asyncio
from collections import Counter

import pytest
from fastapi import HTTPException

from web.services import assistant_management as management, assistant_workspace as workspace
from web.services import assistant_safety as safety


def records(targets):
    return {'id': 'conversation', 'refs': [], 'context': None, 'requests': [
        {'id': 'request', 'status': 'completed', 'message': '读取已有发布回执', 'created': 1,
         'conversation_id': 'conversation'}]}, [
        {'id': 'operation', 'request_id': 'request', 'tool': 'publication_upload', 'arguments': {},
         'status': 'completed', 'created': 1,
         'result': {'publication_tasks': targets, 'submitted': True}}]


def receipt(key):
    return {'id': key, 'status': 'PUBLISHED', 'account_uid': 'current', 'script_id': 'script',
            'version': 1, 'render_id': 'render', 'payload': {'title': key}, 'url': '', 'work_id': key}


@pytest.mark.parametrize('platform', ['bilibili', 'douyin', 'kuaishou'])
def test_publication_cards_read_each_platform_once(platform, monkeypatch):
    calls = []
    rows = [receipt(str(index)) for index in range(12)]
    base = management.platform_path(platform)

    async def native(request, method, path):
        calls.append(path)
        assert path == base, 'a card must not repeat the full native task read'
        await asyncio.sleep(0)
        return 200, {'uploads': rows}

    monkeypatch.setattr(workspace, 'native', native)
    monkeypatch.setattr(management.application, 'native', native)
    monkeypatch.setattr(management, 'account', lambda _: {'uid': 'current', 'name': '账号'})
    targets = [{'platform': platform, 'id': row['id']} for row in rows]
    conversation, operations = records(targets)
    result = asyncio.run(workspace.project(None, {'role': 'admin'}, conversation, operations))
    assert Counter(calls) == {base: 1}
    cards = result['tasks'][0]['publications']
    assert len(cards) == 12 and all(card['status'] == 'PUBLISHED' for card in cards)
    assert all(card['account_uid'] == 'current' for card in cards)


@pytest.mark.parametrize('catalog_unavailable', [False, True])
def test_missing_or_unavailable_catalog_keeps_native_task_fallback(catalog_unavailable, monkeypatch):
    base = management.platform_path('bilibili')
    calls = []

    async def native(request, method, path):
        calls.append(path)
        if path == base:
            if catalog_unavailable:
                raise HTTPException(503, 'catalog unavailable')
            return 200, {'uploads': []}
        assert path in (base + '/uploads/older-task', base + '/uploads/another-task')
        return 200, receipt(path.rsplit('/', 1)[-1])

    monkeypatch.setattr(workspace, 'native', native)
    monkeypatch.setattr(management.application, 'native', native)
    monkeypatch.setattr(management, 'account', lambda _: {'uid': 'current', 'name': '账号'})
    conversation, operations = records([{'platform': 'bilibili', 'id': key} for key in ('older-task', 'another-task')])
    result = asyncio.run(workspace.project(None, {'role': 'admin'}, conversation, operations))
    assert Counter(calls) == {base: 1, base + '/uploads/older-task': 1, base + '/uploads/another-task': 1}
    assert result['tasks'][0]['publications'][0]['status'] == 'PUBLISHED'


def test_single_receipt_avoids_loading_the_whole_platform(monkeypatch):
    calls = []

    async def native(request, method, path):
        calls.append(path)
        assert path.endswith('/uploads/only-task')
        return 200, receipt('only-task')

    monkeypatch.setattr(management.application, 'native', native)
    monkeypatch.setattr(management, 'account', lambda _: {'uid': 'current', 'name': '账号'})
    conversation, operations = records([{'platform': 'bilibili', 'id': 'only-task'}])
    result = asyncio.run(workspace.project(None, {'role': 'admin'}, conversation, operations))
    assert len(calls) == 1 and result['tasks'][0]['publications'][0]['status'] == 'PUBLISHED'


def test_old_account_receipt_remains_unavailable(monkeypatch):
    async def native(request, method, path):
        if path.endswith('/uploads/old-account-task'):
            raise HTTPException(409, '找不到当前账号的投稿任务')
        return 200, {'uploads': []}

    monkeypatch.setattr(workspace, 'native', native)
    monkeypatch.setattr(management.application, 'native', native)
    conversation, operations = records([{'platform': 'bilibili', 'id': 'old-account-task'}])
    result = asyncio.run(workspace.project(None, {'role': 'admin'}, conversation, operations))
    assert result['tasks'][0]['publications'][0]['status'] == 'UNAVAILABLE'


def test_nested_redaction_scans_environment_once_and_refreshes_next_call(monkeypatch):
    class Environment(dict):
        reads = 0

        def items(self):
            self.reads += 1
            return super().items()

    env = Environment(PROVIDER_API_KEY='old-configured-value', MODEL_NAME='visible-model')
    monkeypatch.setattr(safety.os, 'environ', env)
    rows = [{'text': 'old-configured-value visible-model', 'nested': ['Bearer abc.def', 42]}
            for _ in range(100)]
    result = safety.redact({'rows': rows, 'password': 'private', 'extra_headers': {'X-Key': 'private'}})
    assert env.reads == 1
    assert all(row == {'text': '[已隐藏] visible-model', 'nested': ['Bearer [已隐藏]', 42]}
               for row in result['rows'])
    assert result['password'] == '[已配置]' and result['extra_headers'] == {'X-Key': '[已配置]'}
    assert rows[0]['text'] == 'old-configured-value visible-model'

    env['PROVIDER_API_KEY'] = 'new-configured-value'
    assert safety.redact('new-configured-value old-configured-value') == '[已隐藏] old-configured-value'
    assert env.reads == 2


def test_matrix_workspace_does_not_build_unused_report_history(monkeypatch):
    import asyncio
    from web.services import assistant_workspace
    calls = []
    async def native(request, method, path, **kwargs):
        calls.append((path, kwargs))
        return 200, {'name': 'Example', 'members': [], 'render': {}}
    monkeypatch.setattr(assistant_workspace, 'native', native)
    result = asyncio.run(assistant_workspace.object_view({'kind': 'matrix', 'id': 'sample'}, object()))
    assert result['title'] == 'Example'
    assert calls == [('/api/matrices/sample', {'query': {'reports': False}})]


def test_native_response_projection_does_not_block_unrelated_async_requests(monkeypatch):
    import asyncio
    import threading
    from fastapi import FastAPI
    from starlette.requests import Request
    from web.services import assistant_application
    app = FastAPI()
    @app.get('/read')
    def read():
        return {'items': []}
    entered, release = threading.Event(), threading.Event()
    def slow_projection(data):
        entered.set()
        release.wait(timeout=0.4)
        return data
    monkeypatch.setattr(assistant_application, 'redact', slow_projection)
    request = Request({'type': 'http', 'method': 'GET', 'path': '/', 'headers': [], 'app': app})
    async def check():
        task = asyncio.create_task(assistant_application.native(request, 'GET', '/read'))
        try:
            for _ in range(100):
                if entered.is_set():
                    break
                await asyncio.sleep(0.005)
            assert entered.is_set()
            assert not task.done(), 'projection occupied the event loop until it finished'
        finally:
            release.set()
            await task
    asyncio.run(check())
