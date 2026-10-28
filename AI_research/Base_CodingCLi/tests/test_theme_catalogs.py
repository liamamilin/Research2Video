"""Domain question catalogs, retained legacy records and reference-first defaults."""
from pathlib import Path
from types import SimpleNamespace
import json
import threading

import pytest
import yaml

from core import lock, matrices, state, theme_context, theme_discovery, theme_ideas
from core.llm import LLMConfig
from core.theme_library import ThemeLibrary
from core.theme_store import ThemeStore
from core.video import content_columns, production
from tests.test_theme_workbench import theme_env  # noqa: F401
from tests.test_video_scripts import video_env  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
EXPANDED = ('ai_media', 'company', 'consumer', 'culture', 'economy', 'education',
            'geography', 'history', 'industry', 'science')


@pytest.mark.parametrize('matrix', EXPANDED)
def test_domain_catalog_has_distinct_questions_and_real_reusable_references(tmp_path, matrix):
    config = str(ROOT / 'config')
    categories = theme_context.categories(config, matrix)
    assert 8 <= len(categories) <= 9
    assert len({c['key'] for c in categories}) == len(categories)
    assert len({c['promise'] for c in categories}) == len(categories)
    assert all('每日' not in c['name'] and '每周' not in c['name'] for c in categories)
    bindings = production.load_all(config, str(ROOT / 'jobs'))
    library = ThemeLibrary(str(tmp_path))
    legacy = {'category': 'old_category', 'title': '旧的参考问题仍然保留', 'url': 'https://example.org/legacy'}
    library.ingest(matrix, [legacy])
    library.seed(config, matrix)
    first = library.snapshot(matrix)
    library.seed(config, matrix)
    assert library.snapshot(matrix) == first
    assert any(e['url'] == legacy['url'] for e in first['examples'])
    for category in categories:
        examples = {e['id']: e for e in first['examples'] if e['category'] == category['key']}
        patterns = [p for p in first['patterns'] if p['category'] == category['key']]
        assert examples and len(patterns) == 3
        assert len({p['title_pattern'] for p in patterns}) == 3
        assert all(p['example_ids'] and set(p['example_ids']) <= examples.keys() for p in patterns)
        assert all(3 <= len(p['outline']) <= 6 for p in patterns)
        assert all(e['url'].startswith('https://') and 'search.bilibili.com' not in e['url'] for e in examples.values())
        for key in category['production_ids']:
            assert key in bindings and not bindings[key].errors
            assert content_columns.for_production(config, key).matrix == matrix


def test_every_active_matrix_defaults_to_reference_generation_and_keeps_platforms():
    config = str(ROOT / 'config')
    active = [m for m in matrices.load_all(config).values() if not m.archived and m.content.get('role', 'domain') == 'domain']
    assert len(active) == 11
    for matrix in active:
        cfg = theme_discovery.configuration(config, matrix.key)
        assert cfg['mode'] == 'ideas'
        assert cfg['platforms'] and cfg['time'] == '09:00'
    assert theme_discovery.configuration('/nonexistent-config', 'future_domain')['mode'] == 'ideas'


@pytest.mark.parametrize('matrix', EXPANDED)
def test_real_catalog_daily_generation_is_balanced_and_does_not_search(tmp_path, monkeypatch, matrix):
    config = str(ROOT / 'config')
    monkeypatch.setattr(lock, '_LOCK_DIR', str(tmp_path / 'locks'))
    monkeypatch.setattr(state, '_STATE_DIR', str(tmp_path / 'state'))
    monkeypatch.setattr(state, '_HISTORY_DIR', str(tmp_path / 'state/history'))
    seen = []
    class Model:
        usage = {'requests': 1, 'total_tokens': 123}
        def __init__(self, *args, **kwargs): pass
        def close(self): pass
        def chat(self, messages, **kwargs):
            payload = json.loads(messages[1]['content'])
            seen.append(payload)
            rows = []
            for c in payload['categories']:
                example = next(e for e in payload['examples'] if e['category'] == c['key'])
                pattern = next(p for p in payload['patterns'] if p['category'] == c['key'])
                rows.append({'category': c['key'], 'title': c['promise'],
                    'question': c['promise'], 'topic_key': 'test_' + c['key'], 'reason': '把领域问题放到一个具体对象中解释',
                    'example_ids': [example['id']], 'pattern_id': pattern['id']})
            return SimpleNamespace(content=json.dumps({'themes': rows}, ensure_ascii=False))
    monkeypatch.setattr(theme_ideas, 'LLMClient', Model)
    monkeypatch.setattr(theme_ideas.LLMConfig, 'from_system', lambda *a: LLMConfig(model='test'))
    monkeypatch.setattr(theme_discovery.SearchClient, 'search', lambda *a, **k: pytest.fail('reference generation must not search'))
    engine = SimpleNamespace(config_dir=config, workspace_dir=str(tmp_path), jobs_dir=str(ROOT/'jobs'),
                             sys={}, user='test', _cancel=threading.Event(), _progress=lambda *a: None)
    job = {'topic_discovery': matrix, '_file': 'topic_discovery/' + matrix}
    cfg = theme_discovery.configuration(config, matrix)
    output = theme_discovery.run(engine, job)
    assert output and (tmp_path/output).is_file()
    assert theme_discovery.run(engine, job) == output and len(seen) == 1
    rows = ThemeStore(str(tmp_path/'state')).list(matrix)
    assert len(rows) == len(theme_context.categories(config, matrix))
    assert len({row['category'] for row in rows}) == len(rows)
    assert all(row['inspiration']['basis'] == 'reference_library' for row in rows)
    assert state.StateManager.get(job['_file'])['last_usage']['searches'] == 0


def install_catalog(root):
    folder = root/'config/theme_categories'
    folder.mkdir(exist_ok=True)
    rows = [{'key': 'demo_tasks', 'name': '具体任务', 'promise': '完成一个具体任务',
             'production_ids': ['demo_value'], 'type': 'tutorial', 'notes': '沿一次具体输入和操作展示结果',
             'keywords': ['办公任务 文件处理']}]
    (folder/'demo.yaml').write_text(yaml.safe_dump({'categories': rows}, allow_unicode=True))


def test_old_themes_and_formats_remain_accessible_without_entering_default_categories(theme_env):
    from web.services import theme_workbench
    old = theme_workbench.create('demo', {'category': 'demo_value', 'title': '这是一个旧分类下已经做过的具体问题'})
    store = theme_workbench.storage()
    store.mark_used(old['id'])
    library = theme_workbench.reference_library('demo')
    library.ingest('demo', [{'category': 'demo_value', 'title': '旧例题', 'url': 'https://example.org/old'}])
    pattern = library.add_pattern('demo', 'demo_value', name='旧题型', title_pattern='{任务}值得升级吗？',
                                  outline=['任务', '对照', '选择'], example_ids=[])
    install_catalog(theme_env)
    result = theme_workbench.snapshot('demo')
    assert [c['key'] for c in result['categories']] == ['demo_tasks']
    assert result['legacy_categories'][0]['key'] == 'demo_value'
    assert result['legacy_categories'][0]['legacy']
    assert theme_workbench.category('demo', 'demo_value')
    assert theme_workbench.history('demo')['themes'][0]['id'] == old['id']
    assert result['library']['patterns'][0]['id'] == pattern
    assert result['used_count'] == 1 and store.feedback_profile('demo')['topics'] == 1


def test_new_theme_inherits_render_binding_but_uses_its_own_category_brief(theme_env, monkeypatch):
    from web.services import theme_workbench, video_workflow
    from core.video import store as videos
    install_catalog(theme_env)
    monkeypatch.setattr(video_workflow, 'spawn', lambda *a: None)
    row = theme_workbench.create('demo', {'category': 'demo_tasks', 'title': '怎样把一份会议记录整理成具体的待办清单？'})
    result = theme_workbench.start('demo', row['id'], 'editor', auto_video=False)
    script = videos.get_script(result['script_id'])
    assert script['production'] == 'demo_value'
    context = script['content_context']
    assert context['column'] == 'demo_tasks' and context['type'] == 'tutorial'
    assert context['promise'] == '完成一个具体任务'
    assert context['matrix'] == 'demo' and context['render_inheritance']
    assert '沿一次具体输入' in context['notes']
    assert 'source:official' in script['topic']['theme_research']['sources']


def test_category_search_uses_the_subject_instead_of_the_old_report_keywords():
    category = {'key': 'work', 'keywords': ['具体工作场景'], 'production_ids': ['not_needed']}
    assert theme_discovery.search_terms(category, None, None, {}) == ['具体工作场景']
    assert theme_discovery.search_terms(category, None, None, {'queries': {'work': ['用户自己的搜索词']}}) == ['用户自己的搜索词']


def test_supplementary_discovery_does_not_change_the_daily_reference_mode(theme_env, monkeypatch):
    from core.config import load_job
    from web.services import theme_workbench
    from web.runner import executor
    from web.runner.registry import TaskRegistry
    (theme_env/'config/topic_discovery/demo.yaml').write_text('mode: ideas\nplatforms: [bilibili]\n')
    monkeypatch.setattr(TaskRegistry(), 'is_running', lambda *a: False)
    monkeypatch.setattr(executor, 'start_job', lambda *a, **k: SimpleNamespace(task_id='platform-task'))
    result = theme_workbench.discover('demo', 'editor')
    assert result['job'] == 'topic_platform/demo'
    spec = load_job(str(theme_env/'jobs'), result['job'])
    assert spec['platform_discovery_only'] and spec['topic_discovery'] == 'demo'
    assert theme_discovery.configuration(str(theme_env/'config'), 'demo')['mode'] == 'ideas'


def test_an_unstarted_legacy_theme_can_still_enter_the_existing_production_flow(theme_env, monkeypatch):
    from web.services import theme_workbench, video_workflow
    from core.video import store as videos
    old = theme_workbench.create('demo', {'category':'demo_value', 'title':'一个旧分类下还没有开始制作的具体主题'})
    install_catalog(theme_env)
    monkeypatch.setattr(video_workflow, 'spawn', lambda *a: None)
    result = theme_workbench.start('demo', old['id'], 'editor', auto_video=False)
    assert videos.get_script(result['script_id'])['production'] == 'demo_value'
    assert theme_workbench.storage().get(old['id'])['used_at']


def test_provider_cutoff_keeps_one_failed_attempt_and_usage_without_partial_topics(theme_env, monkeypatch):
    monkeypatch.setattr(lock, '_LOCK_DIR', str(theme_env/'state/locks'))
    calls = []
    class Model:
        usage = {'requests': 1, 'completion_tokens': 1024, 'total_tokens': 1100}
        def __init__(self, config, **kwargs):
            assert config.max_tokens <= 1024  # Respect the operator's configured limit.
        def close(self): pass
        def chat(self, *args, **kwargs):
            calls.append(True)
            return SimpleNamespace(content='{"themes":[]}', finish_reason='length')
    monkeypatch.setattr(theme_ideas, 'LLMClient', Model)
    monkeypatch.setattr(theme_ideas.LLMConfig, 'from_system', lambda *a: LLMConfig(model='test', max_tokens=1024))
    library = ThemeLibrary(str(theme_env/'state'))
    request = library.queue('demo', '')
    engine = SimpleNamespace(config_dir=str(theme_env/'config'), workspace_dir=str(theme_env),
                             sys={}, user='test', _cancel=threading.Event(), _progress=lambda *a: None)
    assert theme_ideas.run(engine, {'topic_ideas':'demo','idea_request':request}) is None
    saved = library.run(request)
    assert saved['status'] == 'failed' and '输出上限' in saved['error']
    assert saved['result']['usage']['requests'] == 1 and len(calls) == 1
    assert not ThemeStore(str(theme_env/'state')).list('demo')
    response = next((theme_env/'output/topic_ideas/demo').glob('*.response.json'))
    assert json.loads(response.read_text())['finish_reason'] == 'length'
