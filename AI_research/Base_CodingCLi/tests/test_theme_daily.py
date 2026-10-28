"""Daily reference generation, durable preferences and provider-free usage reads."""
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
import yaml

from core import lock, theme_ideas
from core.engine import ResearchEngine
from core.llm import LLMConfig
from core.state import StateManager
from core.theme_library import ThemeLibrary
from core.theme_store import ThemeStore
from core.video import store as videos
from tests.test_theme_library import proposal, reference
from tests.test_theme_workbench import theme_env  # noqa: F401
from tests.test_video_scripts import _csrf, _login, video_env  # noqa: F401
from web.services import theme_workbench


def test_daily_claim_is_atomic_and_retries_keep_failed_attempts(tmp_path):
    library = ThemeLibrary(str(tmp_path))
    with ThreadPoolExecutor(max_workers=2) as pool:
        ids = list(pool.map(lambda _: library.daily_request('demo', '2026-10-27', '办公场景'), range(2)))
    assert ids[0] == ids[1]
    library.finish(ids[0], 'failed', {'usage': {'requests': 1}}, '暂时失败')
    retry = library.daily_request('demo', '2026-10-27', '办公场景')
    assert retry != ids[0] and library.run(ids[0])['status'] == 'failed'
    library.finish(retry, 'success', {'output': 'kept.md'})
    assert library.daily_request('demo', '2026-10-27', '修改方向') == retry
    assert library.daily_request('demo', '2026-10-28', '办公场景') != retry
    manual = library.queue('demo', '本次方向')
    assert library.run(manual)['status'] == 'queued'
    assert library.run(retry)['status'] == 'success'


def test_feedback_is_counted_once_and_restoration_keeps_events(tmp_path):
    store = ThemeStore(str(tmp_path))
    row = store.publish_generated('demo', '2026-10-27', [proposal()])[0]
    store.feedback(row['id'], 'interested')
    store.feedback(row['id'], 'interested')
    assert store.feedback_profile('demo')['groups'][0]['interested'] == 1
    store.feedback(row['id'], 'dismissed', 'hard_to_show', '希望有输入与输出')
    assert store.get(row['id'])['status'] == 'dismissed'
    group = store.feedback_profile('demo')['groups'][0]
    assert group['interested'] == 0 and group['reasons'] == {'hard_to_show': 1}
    store.feedback(row['id'], 'dismissed', 'not_now')
    assert store.feedback_profile('demo')['groups'][0]['reasons'] == {'not_now': 1}
    store.feedback(row['id'], 'clear')
    assert store.feedback_profile('demo')['topics'] == 0
    store.mark_used(row['id']); store.mark_used(row['id'])
    assert store.feedback_profile('demo')['groups'][0]['selected'] == 1
    with sqlite3.connect(store.path) as db:
        assert db.execute('SELECT count(*) FROM theme_feedback').fetchone()[0] == 5
    assert store.feedback_profile('other')['topics'] == 0
    with pytest.raises(ValueError):
        store.feedback(row['id'], 'interested')


def test_daily_engine_uses_direction_and_feedback_and_reuses_success(theme_env, monkeypatch):
    monkeypatch.setattr(lock, '_LOCK_DIR', str(theme_env/'state/locks'))
    theme_workbench.set_schedule('demo', enabled=True, at='09:00', platforms=[], mode='ideas', direction='具体办公任务')
    example = reference(ThemeLibrary(str(theme_env/'state')))
    old = theme_workbench.create('demo', {'category': 'demo_value', 'title': '这是一个过于抽象的选题吗？'})
    theme_workbench.storage().feedback(old['id'], 'dismissed', 'too_generic')
    calls = []
    class Model:
        usage = {'requests': 1, 'total_tokens': 321}
        def __init__(self, *args, **kwargs): pass
        def close(self): pass
        def chat(self, messages, **kwargs):
            payload = json.loads(messages[1]['content']); calls.append(payload)
            assert payload['extra_direction'] == '具体办公任务'
            assert payload['feedback']['groups'][0]['reasons'] == {'too_generic': 1}
            return SimpleNamespace(content=json.dumps({'themes': [{'category': 'demo_value', 'title': '面试准备时怎样选择适合自己的练习方式？',
                'question': '面试准备练习方式选择', 'topic_key': 'interview_practice', 'reason': '具体任务', 'example_ids': [example['id']],
                'format': {'name': '任务决策', 'title_pattern': '{任务}怎样选择？', 'outline': ['目标', '比较', '选择']}}]}))
    monkeypatch.setattr(theme_ideas, 'LLMClient', Model)
    monkeypatch.setattr(theme_ideas.LLMConfig, 'from_system', lambda *args: LLMConfig(model='test'))
    engine = ResearchEngine(config_dir=str(theme_env/'config'), jobs_dir=str(theme_env/'jobs'), workspace_dir=str(theme_env))
    library = ThemeLibrary(str(theme_env/'state'))
    from datetime import datetime
    from zoneinfo import ZoneInfo
    interrupted = library.daily_request('demo', datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat(), '具体办公任务')
    library.finish(interrupted, 'running', {'usage': {'requests': 1}})
    output = engine.run_job('topic_discovery/demo')
    assert output and engine.run_job('topic_discovery/demo') == output and len(calls) == 1
    state = StateManager.get('topic_discovery/demo')
    assert state['last_usage']['requests'] == 0 and state['last_usage']['reused']
    theme = next(t for t in theme_workbench.storage().list('demo') if t['origin'] == 'generated')
    assert theme['inspiration']['generation_id']
    assert library.for_theme('demo', theme['id'])['result']['usage']['requests'] == 1
    assert library.run(interrupted)['status'] == 'interrupted'
    assert library.run(interrupted)['result']['usage']['requests'] == 1
    assert theme['inspiration']['generation_id'] != interrupted


def test_lock_contender_cannot_fail_an_active_request(theme_env, monkeypatch):
    monkeypatch.setattr(lock, '_LOCK_DIR', str(theme_env/'state/locks'))
    library = ThemeLibrary(str(theme_env/'state'))
    request = library.queue('demo', '')
    library.finish(request, 'running', {})
    engine = SimpleNamespace(workspace_dir=str(theme_env), user='test')
    with lock.LockManager('theme-ideas/demo', owner_id='another-owner'):
        assert theme_ideas.run(engine, {'topic_ideas': 'demo', 'idea_request': request}) is None
    assert library.run(request)['status'] == 'running'


def test_preferred_new_format_is_not_hidden_by_the_prompt_limit():
    patterns = [{'id': str(i), 'category': 'one'} for i in range(4)] + [{'id': 'different', 'category': 'two'}]
    categories = [{'key': 'one'}, {'key': 'two'}]
    profile = {'groups': [{'pattern_id': '3', 'selected': 1, 'interested': 0, 'reasons': {}},
                          {'pattern_id': '0', 'selected': 0, 'interested': 0, 'reasons': {'not_now': 4}}]}
    chosen = theme_ideas.reference_patterns(patterns, categories, profile)
    assert [p['id'] for p in chosen] == ['3', '0', '1', 'different']
    assert {p['category'] for p in chosen} == {'one', 'two'}
    assert '2' in {p['id'] for p in theme_ideas.reference_patterns(patterns, categories, profile, '2')}


def test_daily_settings_preserve_plan_and_manual_discovery(client, theme_env, monkeypatch):
    from web.runner import executor
    from web.runner.registry import TaskRegistry
    from core.schedule_store import ScheduleStore
    _login(client)
    root = '/api/matrices/demo/themes'
    first = client.put(root+'/schedule', json={'enabled': True, 'time': '09:00'}, headers=_csrf(client)).json()
    settings = {'enabled': True, 'time': '10:00', 'platforms': [], 'mode': 'ideas', 'direction': '面向新手'}
    saved = client.put(root+'/schedule', json=settings, headers=_csrf(client)).json()
    assert saved['schedule']['id'] == first['schedule']['id']
    assert len(ScheduleStore(str(theme_env/'state')).list()) == 1
    starts = []
    monkeypatch.setattr(TaskRegistry(), 'is_running', lambda _: False)
    monkeypatch.setattr(executor, 'start_job', lambda name, *args, **kwargs: starts.append(name) or SimpleNamespace(task_id='manual'))
    # Empty platform selection is fine for ideas, but manual web discovery still has useful defaults.
    response = client.post(root+'/discover', headers=_csrf(client))
    assert response.status_code == 202 and starts == ['topic_platform/demo']
    job = yaml.safe_load((theme_env/'jobs/topic_platform/demo.yaml').read_text())
    assert job['platform_discovery_only'] and job['topic_discovery'] == 'demo'
    response = client.put(root+'/schedule', json={'enabled': False, 'time': '10:00'}, headers=_csrf(client)).json()
    assert response['configuration']['mode'] == 'ideas' and response['configuration']['direction'] == '面向新手'


def test_usage_reads_saved_versions_and_does_not_count_manual_edits(theme_env):
    from web.services.theme_usage import usage_for_theme
    theme = theme_workbench.create('demo', {'category': 'demo_value', 'title': '一个能实际演示的会议整理主题'})
    script = videos.create_script('theme_research', {'title': theme['title']})
    videos.add_version(script['id'], title=theme['title'], sections=[], length_check={}, created_by='test', edit_type='GENERATED',
                       meta={'usage': {'requests': 1, 'total_tokens': 123}, 'duration_seconds': 4.5})
    videos.add_version(script['id'], title=theme['title'], sections=[], length_check={}, created_by='test', edit_type='EDITED',
                       meta={'usage': {'requests': 1, 'total_tokens': 123}})
    theme = theme_workbench.storage().attach(theme['id'], script['id'], False)
    stages = {s['key']: s for s in usage_for_theme(theme)['stages']}
    assert stages['writing']['requests'] == 1 and stages['writing']['total_tokens'] == 123
    assert stages['writing']['duration_seconds'] == 4.5 and stages['writing']['cost_usd'] is None
    assert stages['video']['requests'] is None and stages['ideas']['shared']


def test_feedback_api_is_scoped_and_usage_is_viewable(client, theme_env):
    _login(client)
    row = theme_workbench.create('demo', {'category': 'demo_value', 'title': '反馈如何影响下次的具体选题？'})
    root = f'/api/matrices/demo/themes/{row["id"]}'
    assert client.post(root+'/feedback', json={'action': 'dismissed', 'reason': 'too_generic'}, headers=_csrf(client)).status_code == 200
    assert client.get('/api/matrices/demo/themes').json()['feedback']['topics'] == 1
    assert client.post(root.replace('/demo/', '/other/')+'/feedback', json={'action': 'clear'}, headers=_csrf(client)).status_code == 404
    _login(client, 'viewer', 'viewer-pass-123')
    assert client.get(root+'/usage').status_code == 200
    assert client.post(root+'/feedback', json={'action': 'clear'}, headers=_csrf(client)).status_code == 403


def test_video_usage_with_zero_configured_rates_is_unknown_not_free(theme_env, monkeypatch):
    from web.services import theme_usage, video_render
    theme = theme_workbench.create('demo', {'category': 'demo_value', 'title': '视频各阶段使用了多少模型额度？'})
    script = videos.create_script('theme_research', {'title': theme['title']})
    theme = theme_workbench.storage().attach(theme['id'], script['id'], True)
    artifact = theme_env/'cost.json'
    artifact.write_text(json.dumps({'calls': [{'prompt_tokens': 100, 'completion_tokens': 20,
        'cloud_api_cost_estimate_usd': 0, 'pricing_snapshot': {'input_uncached_peak_usd_per_million': 0, 'output_peak_usd_per_million': 0}}]}))
    monkeypatch.setattr(videos, 'list_renders', lambda _: [{'id': 'render', 'status': 'COMPLETED'}])
    monkeypatch.setattr(video_render, 'safe_file', lambda *_: artifact)
    monkeypatch.setattr(video_render, 'render_timings', lambda _: {'elapsed_seconds': 80, 'process_runs': 2})
    stage = theme_usage.usage_for_theme(theme)['stages'][-1]
    assert stage['requests'] == 1 and stage['total_tokens'] == 120 and stage['duration_seconds'] == 80
    assert stage['cost_usd'] is None and stage['partial']
