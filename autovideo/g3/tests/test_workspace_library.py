from uuid import uuid4

from fastapi.testclient import TestClient

from g3.app import create_app
from g3.tests.test_g3 import make_settings, request_payload


def test_library_pagination_search_and_summary_cover_all_records(tmp_path):
    settings = make_settings(tmp_path)
    with TestClient(create_app(settings, start_worker=False)) as client:
        store = client.app.state.store
        ids = []
        for index in range(15):
            job_id = str(uuid4())
            ids.append(job_id)
            title = "进度 100%_完成" if index == 0 else f"制作记录 {index}"
            store.create_job(job_id, request_payload(title=title))
        first = client.get('/api/jobs', params={'limit': 12}).json()
        second = client.get('/api/jobs', params={'limit': 12, 'offset': 12}).json()
        assert len(first['items']) == 12 and len(second['items']) == 3
        assert {v['job_id'] for v in first['items'] + second['items']} == set(ids)
        assert first['total'] == second['total'] == 15
        assert first['summary'] == {'total': 15, 'completed': 0, 'running': 15, 'needs_input': 0}
        literal = client.get('/api/jobs', params={'q': '%_'}).json()
        assert literal['total'] == 1 and literal['items'][0]['job_id'] == ids[0]
        assert literal['summary']['total'] == 15
        identifier = client.get('/api/jobs', params={'q': ids[-1][:8]}).json()
        assert identifier['total'] == 1 and identifier['items'][0]['job_id'] == ids[-1]
        assert client.get('/api/jobs', params={'q': '没有此作品'}).json()['items'] == []


def test_library_distinguishes_material_clarification_from_generation_failure(tmp_path):
    settings = make_settings(tmp_path)
    with TestClient(create_app(settings, start_worker=False)) as client:
        store = client.app.state.store
        for title in ['待确认材料', '模型连接失败', '等待制作']:
            store.create_job(str(uuid4()), request_payload(title=title))
        first = store.claim_next()
        store.mark_failed(first['job_id'], 1, 'PLAN', 'INPUT_REQUIRES_CLARIFICATION', '请确认活动日期', False)
        second = store.claim_next()
        store.mark_failed(second['job_id'], 1, 'PLAN', 'MODEL_TIMEOUT', '连接超时', True)
        material = client.get('/api/jobs', params={'status': 'NEEDS_INPUT'}).json()
        failure = client.get('/api/jobs', params={'status': 'FAILED'}).json()
        assert material['total'] == failure['total'] == 1
        assert material['items'][0]['job_id'] == first['job_id']
        assert failure['items'][0]['job_id'] == second['job_id']
        assert material['summary'] == {'total': 3, 'completed': 0, 'running': 1, 'needs_input': 1}
        assert client.get('/api/jobs', params={'status': 'INVALID'}).status_code == 422
