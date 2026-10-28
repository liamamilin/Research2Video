"""Retained references, new questions from reusable formats, and permanent use history."""
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from core import theme_ideas
from core.engine import ResearchEngine
from core.llm import LLMConfig
from core.theme_library import ThemeLibrary
from core.theme_store import ThemeStore
from tests.test_theme_workbench import theme_env  # noqa: F401
from tests.test_video_scripts import ROOT, _csrf, _login, video_env  # noqa: F401


def proposal(title='免费用户整理表格时，怎样判断是否需要升级？', question='整理表格是否需要付费', key='spreadsheet_upgrade', pattern='same-format'):
    return {'category': 'demo_value', 'title': title, 'reason': '围绕具体任务作选择', 'angle': '比较任务是否能完成',
            'audience': '普通用户', 'signals': [], 'inspiration': {'question': question, 'topic_key': key,
            'pattern_id': pattern, 'pattern_name': '任务与升级判断', 'outline': ['用户任务', '比较差异', '给出条件']}}


def reference(library, matrix='demo', category='demo_value'):
    library.ingest(matrix, [{'category': category, 'title': '一个具体任务的免费与付费比较', 'url': 'https://example.org/free-paid', 'excerpt': '按任务比较体验'}])
    return library.snapshot(matrix)['examples'][0]


def test_references_and_formats_survive_rediscovery_and_do_not_cross_matrices(tmp_path):
    library = ThemeLibrary(str(tmp_path))
    row = reference(library)
    library.ingest('demo', [{'category': 'demo_value', 'title': '更新后的标题', 'url': row['url']+'#part', 'excerpt': '新的简介'}])
    pattern = library.add_pattern('demo', 'demo_value', name='任务决策', title_pattern='{任务}需要升级吗？',
                                  outline=['场景', '比较', '决策'], example_ids=[row['id'], 'fake'])
    kept = ThemeLibrary(str(tmp_path)).snapshot('demo')
    assert len(kept['examples']) == 1 and kept['examples'][0]['title'] == '更新后的标题'
    assert kept['patterns'][0]['id'] == pattern and kept['patterns'][0]['example_ids'] == [row['id']]
    assert library.snapshot('other')['examples'] == []


def test_shipped_reference_bank_covers_nine_categories_and_seeding_is_repeatable(tmp_path):
    library = ThemeLibrary(str(tmp_path))
    library.seed(str(ROOT/'config'), 'chatgpt_acquisition')
    original = library.snapshot('chatgpt_acquisition')
    library.seed(str(ROOT/'config'), 'chatgpt_acquisition')
    data = library.snapshot('chatgpt_acquisition')
    assert data == original
    assert len(data['patterns']) == 9 and len(data['examples']) == 9
    assert len({p['category'] for p in data['patterns']}) == 9
    assert all(p['example_ids'] and len(p['outline']) >= 3 for p in data['patterns'])


def test_used_questions_are_excluded_forever_while_the_same_format_can_be_reused(tmp_path):
    store = ThemeStore(str(tmp_path))
    first = store.publish_generated('demo', '2025-01-01', [proposal()])[0]
    store.mark_used(first['id'])
    with sqlite3.connect(store.path) as db:
        db.execute('UPDATE themes SET created_at=1,updated_at=1 WHERE id=?', (first['id'],))
    # Neither a rewritten title with the same identity nor the same question under a new identity is new.
    assert not store.publish_generated('demo', '2026-10-27', [proposal('怎样选择适合自己的表格工具？')])
    assert not store.publish_generated('demo', '2026-10-27', [proposal('完成办公任务的版本选择指南', key='a-new-identifier')])
    another = proposal('准备英语面试时，语音练习值得付费吗？', '语音面试练习的订阅选择', 'voice_interview_upgrade')
    assert len(store.publish_generated('demo', '2026-10-27', [another])) == 1
    assert store.get(first['id'])['used_at'] > 0
    assert store.history('demo')['themes'][0]['id'] == first['id']
    assert store.history('other')['themes'] == []


def test_concurrent_generations_do_not_add_the_same_question_twice(tmp_path):
    store = ThemeStore(str(tmp_path))
    with ThreadPoolExecutor(max_workers=2) as pool:
        made = list(pool.map(lambda _: store.publish_generated('demo', '2026-10-27', [proposal()]), range(2)))
    assert sum(map(len, made)) == 1


def test_old_used_topics_also_block_platform_rediscovery(tmp_path):
    store = ThemeStore(str(tmp_path))
    row = store.create('demo', 'demo_value', '免费用户整理表格时，怎样判断是否需要升级？')
    store.attach(row['id'], 'script-kept', False)
    with sqlite3.connect(store.path) as db:
        db.execute('UPDATE themes SET created_at=1 WHERE id=?', (row['id'],))
    assert not store.publish('demo', '2026-10-27', [proposal()])
    assert store.history('demo')['themes'][0]['script_id'] == 'script-kept'


def test_generation_runs_through_the_engine_once_without_search_and_keeps_the_brief(theme_env, monkeypatch):
    from core import lock
    from web.runner import executor
    from web.runner.registry import TaskRegistry
    from web.services import theme_workbench
    monkeypatch.setattr(lock, '_LOCK_DIR', str(theme_env/'state/locks'))
    monkeypatch.setattr(TaskRegistry(), 'is_running', lambda _: False)
    monkeypatch.setattr(executor, 'start_job', lambda *args, **kwargs: SimpleNamespace(task_id='ideas-task'))
    library = ThemeLibrary(str(theme_env/'state'))
    example = reference(library)
    calls = []
    class Model:
        usage = {'requests': 1, 'total_tokens': 123}
        def __init__(self, *args, **kwargs): pass
        def chat(self, messages, **kwargs):
            payload = json.loads(messages[1]['content']); calls.append(payload)
            assert payload['extra_direction'] == '面向职场新人'
            return SimpleNamespace(content=json.dumps({'themes': [{'category': 'demo_value', 'title': '准备英语面试时，语音练习值得付费吗？',
                'question': '语音面试练习的订阅选择', 'topic_key': 'voice_interview_upgrade', 'reason': '结合具体任务作选择',
                'angle': '从任务完成情况判断', 'example_ids': [example['id']],
                'format': {'name': '任务与升级判断', 'title_pattern': '{任务}值得升级吗？', 'outline': ['场景', '比较', '决策']}}]}))
        def close(self): pass
    monkeypatch.setattr(theme_ideas, 'LLMClient', Model)
    monkeypatch.setattr(theme_ideas.LLMConfig, 'from_system', lambda *args: LLMConfig(model='test'))
    started = theme_workbench.generate('demo', 'editor', brief='面向职场新人')
    engine = ResearchEngine(config_dir=str(theme_env/'config'), jobs_dir=str(theme_env/'jobs'), workspace_dir=str(theme_env))
    output = engine.run_job(started['job'])
    assert output and (theme_env/output).is_file()
    assert engine.run_job(started['job']) == output and len(calls) == 1
    theme = ThemeStore(str(theme_env/'state')).list('demo')[0]
    assert theme['origin'] == 'generated' and theme['notes'] == '面向职场新人'
    assert theme['signals'][0]['url'] == example['url'] and theme['signals'][0]['metrics'] == {}
    result = library.run(started['request_id'])
    assert result['status'] == 'success' and result['result']['usage']['searches'] == 0
    assert library.snapshot('demo')['patterns'][0]['outline'] == ['场景', '比较', '决策']
    # A future generation can reference this same format without finding web pages again.
    ThemeStore(str(theme_env/'state')).mark_used(theme['id'])
    new = theme_workbench.generate('demo', 'editor', pattern_id=theme['inspiration']['pattern_id'])
    assert library.run(new['request_id'])['category'] == 'demo_value'


def test_model_cannot_attach_foreign_examples_or_generate_every_topic_in_one_category(tmp_path):
    library = ThemeLibrary(str(tmp_path))
    own = reference(library)
    foreign = reference(library, category='other')
    rows = []
    for refs in ([foreign['id']], [own['id']], [own['id']]):
        rows.append({'category': 'demo_value', 'title': '如何完成这项具体的办公任务？', 'question': '具体办公任务', 'topic_key': 'office_task',
                     'reason': '面向普通用户', 'example_ids': refs, 'format': {'name': '任务教程', 'title_pattern': '怎样完成{任务}？', 'outline': ['准备', '操作', '结果']}})
    selected = theme_ideas.select(json.dumps({'themes': rows}), [{'key': 'demo_value', 'audience': '普通用户'}], library, 'demo', library.snapshot('demo'))
    assert len(selected) == 1 and selected[0]['signals'][0]['id'] == own['id']


def test_api_keeps_used_history_and_prevents_foreign_pattern_selection(client, theme_env, monkeypatch):
    from web.runner import executor
    from web.runner.registry import TaskRegistry
    from web.services import theme_workbench
    from core import lock
    monkeypatch.setattr(lock, '_LOCK_DIR', str(theme_env/'state/locks'))
    starts = []
    monkeypatch.setattr(TaskRegistry(), 'is_running', lambda _: bool(starts))
    monkeypatch.setattr(executor, 'start_job', lambda name, *args, **kwargs: starts.append(name) or SimpleNamespace(task_id='ideas-task'))
    _login(client)
    root = '/api/matrices/demo/themes'
    first = client.post(root+'/generate', json={'brief': '多讲具体工作任务'}, headers=_csrf(client))
    assert first.status_code == 202
    assert client.post(root+'/generate', json={}, headers=_csrf(client)).status_code == 202 and len(starts) == 1
    row = theme_workbench.create('demo', {'title': '这是我们已经在别处用过的一个题目', 'category': 'demo_value'})
    assert client.post(root+f'/{row["id"]}/used', headers=_csrf(client)).status_code == 200
    assert client.get(root+'/history').json()['themes'][0]['id'] == row['id']
    assert client.get('/api/matrices/other/themes/history').json()['themes'] == []
    assert client.patch(root+f'/{row["id"]}', json={'title': '不能覆盖已用记录'}, headers=_csrf(client)).status_code == 409
    assert client.post(root+f'/{row["id"]}/start', json={}, headers=_csrf(client)).status_code == 409
    library = ThemeLibrary(str(theme_env/'state'))
    foreign = library.add_pattern('other', 'general', name='其他矩阵', title_pattern='{其他领域}怎样展开？', outline=['问题', '过程', '结果'], example_ids=[])
    assert client.post(root+'/generate', json={'pattern_id': foreign}, headers=_csrf(client)).status_code == 409
    _login(client, 'viewer', 'viewer-pass-123')
    assert client.get(root+'/history').status_code == 200
    assert client.post(root+'/generate', json={}, headers=_csrf(client)).status_code == 403
    assert client.post(root+f'/{row["id"]}/used', headers=_csrf(client)).status_code == 403


def test_history_includes_existing_production_status_across_dates(theme_env):
    from core.video import store as videos
    from web.services import theme_workbench
    row = theme_workbench.create('demo', {'title': '过去已经做过的一个具体教程问题', 'category': 'demo_value'})
    script = videos.create_script('theme_research', {'title': row['title']})
    videos.update_script(script['id'], status='DRAFT', current_version=1)
    themes = ThemeStore(str(theme_env/'state'))
    themes.attach(row['id'], script['id'], False)
    with sqlite3.connect(themes.path) as db:
        db.execute('UPDATE themes SET created_at=1 WHERE id=?', (row['id'],))
    history = theme_workbench.history('demo')
    assert history['total'] == 1 and history['themes'][0]['script_status'] == 'DRAFT'
    assert history['themes'][0]['script_id'] == script['id']


def test_matrix_without_references_can_still_suggest_a_question(tmp_path):
    library = ThemeLibrary(str(tmp_path))
    reply = {'themes': [{'category': 'science', 'title': '为什么同样一杯热水，换个杯子凉得更快？', 'question': '杯子材料如何影响散热',
        'topic_key': 'cup_heat_transfer', 'reason': '从生活现象理解科学', 'example_ids': [],
        'format': {'name': '日常现象解释', 'title_pattern': '为什么{现象}会{变化}？', 'outline': ['描述现象', '拆解机制', '生活应用']}}]}
    made = theme_ideas.select(json.dumps(reply), [{'key': 'science', 'audience': '普通观众'}], library, 'demo', library.snapshot('demo'))
    assert made[0]['inspiration']['basis'] == 'matrix' and made[0]['signals'] == []
