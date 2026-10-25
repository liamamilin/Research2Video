"""Keep selected run identity and validated on-disk output locations aligned."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.test_application_assistant import command
from tests.test_assistant_api import login, write_job
from web.runner import log_store


def test_selected_manual_run_has_its_own_status_report_and_reference(client, web_env, monkeypatch):
    from web.routes import jobs
    root, _ = web_env
    write_job(web_env)
    old = root / 'output/old.md'
    old.write_text('# Earlier report')
    for rid, state, output in [('old', 'success', str(old)), ('new', 'failed', None)]:
        log_store.append_event(str(root / 'logs'), 'research/memory', rid,
                               {'type': 'status', 'status': state, 'output_path': output})
    monkeypatch.setattr(jobs._registry, 'get', lambda _: SimpleNamespace(task_id='new', status='running', is_running=True))
    result = command(client, monkeypatch, 'query_run', {'source': 'manual', 'job_name': 'research/memory', 'run_id': 'old'})
    refs = result['actions'][0]['references']
    assert any(r['type'] == 'run' and r['id'] == 'old' for r in refs)
    assert not any(r['type'] == 'run' and r['id'] == 'new' for r in refs)
    assert any(r['type'] == 'report' and r['id'] == 'old.md' for r in refs)
    response = client.get('/api/jobs/research/memory/logs?run_id=old').json()
    assert response['run']['status'] == 'success' and not response['live']
    assert response['run']['output_path'] == str(old)
    assert {e['run_id'] for e in response['events']} == {'old'}
    missing = client.get('/api/jobs/research/memory/logs?run_id=missing')
    assert missing.status_code == 404


@pytest.mark.parametrize('custom_root', [False, True])
def test_validated_output_is_written_under_its_root_with_a_different_cwd(tmp_path, monkeypatch, custom_root):
    from core.engine import ResearchEngine
    workspace = tmp_path / 'workspace'
    process_dir = tmp_path / 'process'
    workspace.mkdir()
    process_dir.mkdir()
    monkeypatch.chdir(process_dir)
    engine = ResearchEngine.__new__(ResearchEngine)
    engine.workspace_dir = str(workspace)
    engine.sys = {}
    engine._date_override = None
    output_root = workspace / ('custom' if custom_root else 'output')
    job = {'name': 'test', 'output': 'result.md' if custom_root else 'output/result.md'}
    if custom_root:
        job['runtime'] = {'output_root': str(output_root)}
    path = engine._resolve_output_path(job)
    assert Path(path).is_absolute()
    saved = engine._save_output('# Fresh report', path)
    assert Path(saved) == output_root.resolve() / 'result.md'
    assert Path(saved).read_text() == '# Fresh report'
    assert not list(process_dir.rglob('*.md'))
    assert engine._output_reference(saved) == ('custom/result.md' if custom_root else 'output/result.md')


@pytest.mark.parametrize('fail', [False, True])
def test_engine_replaces_or_restores_the_previous_report_in_the_validated_workspace(web_env, tmp_path, monkeypatch, fail):
    from core import engine as engine_module
    from core.engine import ResearchEngine
    from contextlib import nullcontext
    root, _ = web_env
    job_path = write_job(web_env)
    import yaml
    data = yaml.safe_load(job_path.read_text())
    data['output'] = 'output/kept.md'
    job_path.write_text(yaml.safe_dump(data))
    output = root / 'output/kept.md'
    output.write_text('# Previous report')
    process_dir = tmp_path / 'other-cwd'
    process_dir.mkdir()
    monkeypatch.chdir(process_dir)
    class Agent:
        def __init__(self, **kwargs):
            self.materials = ['### Original source\n\nhttps://source.example/news\n\nConcrete detail.']
        def run(self, *args, **kwargs):
            if fail:
                raise RuntimeError('injected research failure')
            return '# Fresh report\n\nUseful evidence and findings.'
        def get_usage(self): return {}
        def close(self): pass
    monkeypatch.setattr(engine_module, 'ResearchAgent', Agent)
    monkeypatch.setattr(engine_module, 'LockManager', lambda *a, **kw: nullcontext())
    monkeypatch.setattr(engine_module, 'extract_report', lambda text, *a, **kw: text)
    monkeypatch.setattr(ResearchEngine, '_notify', lambda *a, **kw: None)
    monkeypatch.setattr(ResearchEngine, '_check_citations', lambda *a: None)
    monkeypatch.setattr(ResearchEngine, '_register_events', lambda *a: None)
    from core import report_meta
    recorded = []
    monkeypatch.setattr(report_meta, 'append_record', lambda *a, **kw: recorded.append(kw))
    engine = ResearchEngine(config_dir=str(root / 'config'), jobs_dir=str(root / 'jobs'), workspace_dir=str(root))
    result = engine.run_job('research/memory')
    assert result == (None if fail else 'output/kept.md')
    assert output.read_text().startswith('# Previous' if fail else '# Fresh')
    assert not output.with_suffix('.md.prev').exists()
    assert not list(process_dir.rglob('*.md'))
    if not fail:
        from core import report_material
        material = recorded[0]['extra']['material']
        assert Path(material['path']).parent == output.parent
        assert 'Concrete detail.' in report_material.read(material, str(output), output.read_text())
