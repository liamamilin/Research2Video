"""Scoped SQL retrieval and useful, honest continuation of bounded turns."""
import json
from core.llm import ChatResponse, ToolCall
from tests.test_assistant_api import login, fake_llm, tool


def test_report_list_paginates_before_file_checks(web_env, monkeypatch):
    from web.indexer import db
    from web.services.assistant_tools import reports
    root, _ = web_env
    for index in range(40):
        path = f'practical_ai_intelligence/2026-09-01/{index:02d}.md'
        full = root / 'output' / path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text('# Memory report')
        db.upsert_report(path, index, title='Memory')
    checked = []
    original = reports.reports.resolve_output_file
    def resolve(path, **kwargs):
        checked.append(path)
        return original(path, **kwargs)
    monkeypatch.setattr(reports.reports, 'resolve_output_file', resolve)
    result = reports.query(reports.ReportQuery(action='list', round_date='2026-09-01', limit=3, page=2), None, {'role': 'viewer'})
    assert len(result['items']) == len(checked) == 3
    assert result['total_in_scope'] == 40 and result['has_more']
    assert '索引' in result['scope_basis']
    selected = reports.query(reports.ReportQuery(action='list', paths=[checked[0], 'elsewhere.md'], round_date='2026-09-01'), None, {'role': 'viewer'})
    assert selected['total_in_scope'] == 1


def test_scope_filters_fts_before_limit_and_skips_a_stale_high_ranked_file(web_env, monkeypatch):
    from web.indexer import db
    from web.routes import qa
    from web.services.assistant_tools import reports
    root, _ = web_env
    real = root / 'output/real.md'
    real.write_text('# AI memory')
    db.upsert_report('real.md', 1, title='AI memory')
    db.upsert_report('missing.md', 100, title='AI memory')
    db.upsert_report('outside.md', 200, title='AI memory')
    assert db.search_reports('AI', 1, allowed_paths={'real.md'})[0]['path'] == 'real.md'
    assert db.search_reports('memory', 1, allowed_paths={'real.md'})[0]['path'] == 'real.md'
    monkeypatch.setattr(qa, '_embed_query', lambda *a: [])
    result = reports.query(reports.ReportQuery(action='search', query='AI', limit=1, paths=['real.md', 'missing.md']), None, {'role': 'viewer'})
    assert [item['path'] for item in result['items']] == ['real.md']
    assert result['unavailable'] == ['missing.md']


def test_step_limit_saves_read_position_and_carries_it_into_explicit_continuation(client, web_env, monkeypatch):
    from web.services import assistant_runtime as runtime
    from web.indexer import scanner
    root, _ = web_env
    report = root / 'output/long.md'
    report.write_text('# Long report\n' + 'Evidence. ' * 100)
    scanner.full_scan(str(root / 'output'))
    monkeypatch.setattr(runtime, 'MAX_STEPS', 2)
    fake_llm(monkeypatch, [tool('query_reports', action='list'), tool('query_reports', action='read', path='long.md', characters=100)])
    first = client.post('/api/assistant', headers=login(client), json={'message': '读取并分析这些报告'}).json()
    progress = first['continuation']
    assert '尚未完成' in first['answer']
    assert progress['reports_complete'] == 0 and progress['remaining_report_count'] == 1
    read = progress['report_reads'][0]
    assert read['path'] == 'long.md' and read['next_offset'] == 100 and not read['complete']
    assert read['operation_ids'] and read['document_version']
    restored = client.get('/api/assistant/conversations/' + first['conversation_id']).json()
    assert restored['requests'][0]['result']['continuation'] == progress
    def finish(messages, kwargs):
        serialized = json.dumps(messages, ensure_ascii=False)
        assert 'next_offset' in serialized and 'long.md' in serialized
        return ChatResponse(content='已从保存位置继续。')
    fake_llm(monkeypatch, [finish])
    second = client.post('/api/assistant', headers=login(client), json={'message': '继续', 'conversation_id': first['conversation_id']}).json()
    assert second['answer'] == '已从保存位置继续。' and not second['actions']


def test_continuation_rehydrates_evidence_receipts_and_combines_only_the_same_document_version(client, web_env, monkeypatch):
    from web.services import assistant_runtime as runtime
    root, _ = web_env
    report = root / 'output/resume.md'
    report.write_text('A' * 200)
    monkeypatch.setattr(runtime, 'MAX_STEPS', 1)
    fake_llm(monkeypatch, [tool('query_reports', action='read', path='resume.md', characters=100)])
    first = client.post('/api/assistant', headers=login(client), json={'message': '读报告'}).json()
    operation = first['continuation']['report_reads'][0]['operation_ids'][0]
    calls = [ToolCall(id='old', name='query_operation', arguments={'operation_id': operation}),
             ToolCall(id='new', name='query_reports', arguments={'action': 'read', 'path': 'resume.md', 'offset': 100, 'characters': 100})]
    fake_llm(monkeypatch, [ChatResponse(tool_calls=calls)])
    second = client.post('/api/assistant', headers=login(client), json={'message': '继续', 'conversation_id': first['conversation_id']}).json()
    assert second['continuation']['reports_complete'] == 1
    report.write_text('B' * 200)
    fake_llm(monkeypatch, [ChatResponse(tool_calls=calls)])
    changed = client.post('/api/assistant', headers=login(client), json={'message': '继续核验变更后的文件', 'conversation_id': first['conversation_id']}).json()
    assert changed['continuation']['reports_complete'] == 0
    assert changed['continuation']['report_reads'][0]['next_offset'] == 0


def test_reading_a_tail_alone_does_not_claim_the_whole_report_was_read(client, web_env, monkeypatch):
    from web.services import assistant_runtime as runtime
    root, _ = web_env
    (root / 'output/gap.md').write_text('A' * 200)
    monkeypatch.setattr(runtime, 'MAX_STEPS', 1)
    fake_llm(monkeypatch, [tool('query_reports', action='read', path='gap.md', offset=100, characters=100)])
    result = client.post('/api/assistant', headers=login(client), json={'message': '读取末尾'}).json()
    assert result['continuation']['reports_complete'] == 0
    assert result['continuation']['report_reads'][0]['next_offset'] == 0


def test_tool_limit_does_not_execute_the_pending_write_or_replay_successes(client, web_env, monkeypatch):
    from web.services import assistant_runtime as runtime
    root, _ = web_env
    monkeypatch.setattr(runtime, 'MAX_TOOLS', 1)
    calls = [ToolCall(id=str(index), name='create_job', arguments={'name': name, 'prompt': 'Research with evidence'}) for index, name in enumerate(['first', 'second'])]
    fake_llm(monkeypatch, [ChatResponse(tool_calls=calls)])
    response = client.post('/api/assistant', headers=login(client), json={'message': '创建 first 和 second'}).json()
    assert len(response['actions']) == 1
    assert response['continuation']['pending_tools'] == ['create_job']
    assert (root / 'jobs/research/first.yaml').exists()
    assert not (root / 'jobs/research/second.yaml').exists()
