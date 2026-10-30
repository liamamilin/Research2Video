"""Recommended themes reserve normal queue items, with quotas independent of report auto counts."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from zoneinfo import ZoneInfo
from types import SimpleNamespace

import pytest
import yaml

from core import theme_production as policy
from core.theme_store import ThemeStore
from core.video import autopilot_store as ap, content_guard, store
from tests.test_theme_workbench import theme_env  # noqa: F401
from tests.test_video_scripts import _csrf, _login, video_env  # noqa: F401
from web.services import theme_autopilot, theme_workbench, video_autopilot, video_workflow

NOW = datetime(2026, 10, 30, 10, tzinfo=ZoneInfo('Asia/Shanghai')).timestamp()


@pytest.fixture
def automatic(theme_env, monkeypatch):
    folder = theme_env / 'config/theme_production'
    folder.mkdir()
    (folder / 'demo.yaml').write_text('enabled: true\ndaily_limit: 1\nminutes: 4\n')
    monkeypatch.setattr(ap, 'time', SimpleNamespace(time=lambda: NOW))
    themes = ThemeStore(str(theme_env / 'state'))
    made = [themes.create('demo', 'demo_value', title, origin='generated', day='2026-10-30') for title in (
        '免费版用了一个月，应该升级 Plus 吗？', '如何让团队报告保留每一项原始链接？')]
    spawned = []
    monkeypatch.setattr(video_workflow, 'spawn', lambda sid, started_by='': spawned.append(sid))
    return theme_env, themes, made, spawned


def test_repeated_or_concurrent_claims_reserve_one_slot_and_no_writer_runs_yet(automatic):
    root, themes, made, spawned = automatic
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(lambda _: theme_autopilot.fire(NOW), range(2)))
    assert sum(results) == 1 and len(ap.list_runs()) == 1 and spawned == []
    assert theme_autopilot.fire(NOW) == 0
    run = ap.list_runs()[0]
    assert run['stage'] == 'ITEMS' and run['items'][0]['state'] == 'PENDING'
    chosen = run['items'][0]['topic']['theme_task_id']
    assert themes.get(chosen)['status'] == 'queued'
    states = policy.plan(str(root / 'config'), str(root / 'state'), 'demo', NOW)
    assert states['daily_used'] == 1
    assert states['states'][chosen]['state'] == 'queued'
    assert any(row['state'] == 'quota' for row in states['states'].values())


def test_queue_handover_recovers_without_creating_a_second_script(automatic):
    root, themes, made, spawned = automatic
    assert theme_autopilot.fire(NOW) == 1
    item = ap.list_runs()[0]['items'][0]
    first = theme_workbench.create_queued(item)
    second = theme_workbench.create_queued(item)
    assert first['id'] == second['id'] and spawned == [first['id']]
    assert first['content_matrix_id'] == first['source_matrix_id'] == 'demo'
    assert content_guard.topic_scope(first['topic'], str(root / 'config')) == 'matrix:demo'
    # The legacy theme pass adopts this reservation even in the cross-database handover gap.
    theme_workbench.tick()
    assert len(ap.list_runs()) == 1 and ap.item_for_theme(item['topic']['theme_task_id'])['script_id'] == first['id']
    with pytest.raises(ValueError, match='制作队列|进入制作'):
        themes.edit(made[0]['id'], title='不能在领取后覆盖原题', angle='', audience='', notes='')


def test_duplicate_skips_free_quota_but_real_failures_hold_the_original_slot(automatic):
    root, themes, made, spawned = automatic
    assert theme_autopilot.fire(NOW) == 1
    item = ap.list_runs()[0]['items'][0]
    ap.update_item(item['id'], state='HELD', note='模型超时，等待重试')
    assert theme_autopilot.fire(NOW) == 0
    ap.update_item(item['id'], state='CANCELLED', note='重复选题，已跳过')
    assert theme_autopilot.fire(NOW) == 1
    assert len(ap.list_runs()) == 2 and spawned == []


def test_quotas_use_shanghai_monday_boundaries_and_ignore_old_or_dismissed_themes(automatic):
    root, themes, made, spawned = automatic
    theme_autopilot.fire(NOW)
    # A new day gets a matrix slot but does not reset a weekly column quota.
    themes.create('demo', 'demo_value', '下周应该怎样比较两种不同的协作方法？', origin='generated', day='2026-10-31')
    tomorrow = policy.plan(str(root / 'config'), str(root / 'state'), 'demo', NOW + 86400)
    assert tomorrow['daily_used'] == 0 and tomorrow['selected'] == []
    themes.create('demo', 'demo_value', '如何把一周的材料整理为可执行学习路径？', origin='generated', day='2026-11-02')
    monday = policy.plan(str(root / 'config'), str(root / 'state'), 'demo', NOW + 3 * 86400)
    assert len(monday['selected']) == 1
    assert policy.window('week', NOW) == datetime(2026, 10, 26, tzinfo=ZoneInfo('Asia/Shanghai')).timestamp()


def test_manual_selection_and_automatic_selection_do_not_start_the_same_theme_twice(client, automatic):
    root, themes, made, spawned = automatic
    theme_autopilot.fire(NOW)
    chosen = ap.list_runs()[0]['items'][0]['topic']['theme_task_id']
    _login(client)
    response = client.post(f'/api/matrices/demo/themes/{chosen}/start', json={'auto_video': True}, headers=_csrf(client))
    assert response.status_code == 409 and spawned == []
    video_autopilot.tick()
    assert len(spawned) == 1
    assert theme_autopilot.fire(NOW) == 0


def test_production_settings_are_scoped_role_checked_and_do_not_change_report_config(client, theme_env):
    original = (theme_env / 'config/production/demo_value.yaml').read_bytes()
    url = '/api/matrices/demo/themes/production'
    body = {'enabled': True, 'daily_limit': 2, 'minutes': 3.5,
            'columns': {'demo_value': {'period': 'week', 'count': 1}}}
    _login(client, 'viewer', 'viewer-pass-123')
    assert client.put(url, json=body, headers=_csrf(client)).status_code == 403
    _login(client)
    response = client.put(url, json=body, headers=_csrf(client))
    assert response.status_code == 200 and response.json()['daily_limit'] == 2
    assert not policy.configuration(str(theme_env / 'config'), 'other')['enabled']
    assert (theme_env / 'config/production/demo_value.yaml').read_bytes() == original
    bad = {**body, 'columns': {'missing': {'period': 'day', 'count': 1}}}
    assert client.put(url, json=bad, headers=_csrf(client)).status_code == 409


def test_a_question_already_in_report_production_is_not_researched_again(automatic):
    root, themes, made, spawned = automatic
    existing = store.create_script('theme_research', {'title': made[0]['title']}, 'autopilot',
                                   content_context={'matrix': 'demo', 'source_matrix': 'demo'})
    snapshot = policy.plan(str(root / 'config'), str(root / 'state'), 'demo', NOW)
    assert snapshot['states'][made[0]['id']]['state'] == 'skipped'
    assert snapshot['states'][made[0]['id']]['script_id'] == existing['id']
    assert made[1]['id'] in snapshot['selected']
    assert spawned == []


def test_a_recommendation_keeps_its_matrix_when_reusing_another_matrix_binding(automatic):
    root, themes, made, spawned = automatic
    recipe, topic = theme_workbench.prepare('demo', made[0], 4)
    topic['matrix_dedup']['job'] = 'foreign/daily'
    assert content_guard.topic_scope(topic, str(root / 'config')) == 'matrix:demo'
    row = store.create_script(recipe, topic, 'autopilot', content_context=topic['_content_context'])
    assert content_guard.with_policy(row, str(root / 'jobs'))['topic']['matrix_dedup']['matrix'] == 'demo'
