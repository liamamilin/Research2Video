"""List payload size reductions retain the state used by page controls."""
from copy import deepcopy
from types import SimpleNamespace

from web.models import JobState
from web.services.video_list_payload import job_state


def test_job_state_projection_preserves_status_and_usage_totals():
    state = {'job_name': 'daily', 'last_status': 'failed', 'last_error': 'error',
             'last_usage': {'total_tokens': 123, 'searches': 7, 'model': 'test',
                            'sources': ['large source body' * 1000]}}
    original = deepcopy(state)
    small = job_state(state)
    assert small == {**state, 'last_usage': {'total_tokens': 123, 'searches': 7, 'model': 'test'}}
    assert state == original
    assert job_state(None) is None


def test_compact_jobs_is_opt_in_and_does_not_shrink_detail_state(monkeypatch):
    from web.routes import jobs
    state = JobState(job_name='daily', last_status='success', last_usage={'total_tokens': 123, 'sources': ['body']})
    monkeypatch.setattr(jobs, 'get_settings', lambda: SimpleNamespace(paths=SimpleNamespace(jobs_dir='unused')))
    monkeypatch.setattr(jobs, 'list_jobs', lambda *a, **kw: ['daily'])
    monkeypatch.setattr(jobs, 'load_job', lambda *a: {'_file': 'daily'})
    monkeypatch.setattr(jobs, '_state_for', lambda *a: state.model_copy(deep=True))
    full = jobs.list_all(user={})
    small = jobs.list_all(user={}, compact=True)
    assert full[0].state.last_usage['sources'] == ['body']
    assert small[0].state.last_usage == {'total_tokens': 123}
    assert state.last_usage['sources'] == ['body']


def test_matrix_schedule_projection_avoids_scheduler_history_and_previews(monkeypatch):
    from web.routes.matrices import _plans
    from web.services import managed_scheduler
    plans = [{'id': 'on', 'job_name': 'daily', 'schedule': '0 8 * * *', 'timezone': 'Asia/Shanghai', 'enabled': 1},
             {'id': 'off', 'job_name': 'weekly', 'schedule': '0 8 * * 1', 'timezone': 'local', 'enabled': 0}]
    monkeypatch.setattr(managed_scheduler, 'store', lambda: SimpleNamespace(list=lambda: plans))
    monkeypatch.setattr(managed_scheduler, 'describe_all', lambda *a: (_ for _ in ()).throw(AssertionError('full scheduler read')))
    rows = _plans()
    assert rows['daily']['enabled'] is True and len(rows['daily']['next_runs']) == 1
    assert rows['daily']['period']['kind'] == 'daily'
    assert rows['weekly']['enabled'] is False and rows['weekly']['next_runs'] == []
