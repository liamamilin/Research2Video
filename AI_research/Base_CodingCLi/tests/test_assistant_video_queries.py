"""Native list responses remain readable through the assistant and its receipts."""
import json

import pytest

from core.llm import ChatResponse
from core.video import recipes
from tests.test_assistant_api import fake_llm, login, tool
from web.services.assistant_store import AssistantStore


@pytest.mark.parametrize('cells', [[], [{'key': 'flash', 'label': '快讯', 'status': 'usable'}]])
def test_cells_query_continues_with_catalog_and_saves_successful_receipt(client, web_env, monkeypatch, cells):
    monkeypatch.setattr(recipes, 'catalog', lambda _config: cells)
    headers = login(client)
    # Keep the frontend's native list response unchanged.
    assert client.get('/api/video/cells').json() == cells

    def continue_after_query(messages, _kwargs):
        result = json.loads(messages[-1]['content'])
        assert result == {'cells': cells}
        return ChatResponse(content='已读取视频配置，可以继续处理。')

    fake_llm(monkeypatch, [tool('query_video', action='cells', limit=100), continue_after_query])
    response = client.post('/api/assistant', headers=headers, json={'message': '查看可用的视频配置'})
    assert response.status_code == 200
    data = response.json()
    assert data['error'] is None and data['actions'][0]['ok'], data
    assert data['answer'] == '已读取视频配置，可以继续处理。'
    receipt = AssistantStore().receipt(data['actions'][0]['operation_id'], web_env[1]['admin'])
    assert receipt['status'] == 'completed' and receipt['result'] == {'cells': cells}
    workspace = client.get('/api/assistant/workspace', params={'conversation_id': data['conversation_id']})
    assert workspace.status_code == 200
    assert workspace.json()['operations'] == []
