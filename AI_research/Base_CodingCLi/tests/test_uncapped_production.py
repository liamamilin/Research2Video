"""Production regressions: no capped leftovers, immutable inputs and durable handover."""
from __future__ import annotations

import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from core import report_identity
from core.video import auto, autopilot_store as ap, production, store, topic_store
from tests.test_auto_write import pilot_env, _auto_off, _fresh, _pending, _report  # noqa: F401
from tests.test_production import prod_env  # noqa: F401
from tests.test_video_scripts import video_env  # noqa: F401
from tests.test_theme_workbench import theme_env  # noqa: F401
from web.services import video_autopilot as pilot, theme_workbench


def report_items(n):
    return '\n'.join(f'### {i}. 独立事件 {i}\n- **一句话**：不同变化 {i}\n' for i in range(1,n+1))


def test_all_twelve_candidates_enter_even_with_legacy_auto_one(pilot_env):
    env, _, _ = pilot_env
    _report(env).write_text(report_items(12))
    now = _fresh(env, count=1)
    assert pilot.fire_auto(now) == 1
    [run] = ap.list_runs()
    picked = pilot.pick_topics(run['spec'])
    assert len(picked) == 12
    assert len({i['topic']['id'] for i in picked}) == 12


def test_all_same_day_reports_are_discovered_and_exact_inputs_survive(pilot_env):
    env, _, _ = pilot_env
    now = _fresh(env)
    first = _report(env)
    first.write_text(report_items(2))
    second = first.with_name(first.stem+'_1.md')
    second.write_text('### 1. 下午的新事件\n- **一句话**：独立新变化\n')
    for index,path in enumerate((first,second)):
        report_identity.publish(str(path),str(env))
        os.utime(path,(now-120+index,now-120+index))
    assert len(_pending(env,now)) == 2
    assert pilot.fire_auto(now) == 2
    assert pilot.fire_auto(now) == 0
    sets=[pilot.pick_topics(r['spec']) for r in ap.list_runs()]
    assert sorted(len(items) for items in sets) == [1,2]
    assert len({i['topic']['id'] for items in sets for i in items}) == 3
    pilot.sync_topics()
    assert len(topic_store.load('flash')[1]) == 1  # browser shows latest run only
    assert store.get_script('does-not-exist') is None


def test_overwritten_legacy_code_does_not_attach_a_different_frozen_event(pilot_env):
    env, _, _ = pilot_env
    old=store.create_script('flash',{'id':'2026-10-19/F1','title':'旧版本的另一个事件','fields':{'一句话':'旧事件内容'}})
    before=store.get_script(old['id'])
    _report(env).write_text(report_items(1))
    pilot.sync_topics()
    [current]=topic_store.load('flash')[1]
    assert current.id != '2026-10-19/F1'
    assert current.title == '独立事件 1'
    assert store.get_script(old['id']) == before


def test_failed_batch_handover_is_retried_not_marked_as_finished(pilot_env,monkeypatch):
    env, _, _ = pilot_env
    now=_fresh(env)
    original=pilot.start_run
    monkeypatch.setattr(pilot,'start_run',lambda *a,**k: (_ for _ in ()).throw(OSError('temporary')))
    assert pilot.fire_auto(now) == 0
    assert ap.list_runs() == []
    monkeypatch.setattr(pilot,'start_run',original)
    assert pilot.fire_auto(now) == 1


def test_editing_enabled_config_does_not_move_activation_past_waiting_report(pilot_env):
    env, _, _ = pilot_env
    now=_fresh(env)
    before=production.get(str(env/'config'),str(env/'jobs'),'flash').auto_enabled_at
    production.save(str(env/'config'),'flash',job='monitoring/ai_flash_discovery',recipe='flash',auto=1,name='更名')
    assert production.get(str(env/'config'),str(env/'jobs'),'flash').auto_enabled_at == before
    assert len(_pending(env,now)) == 1


def test_recommendations_in_two_matrices_queue_all_once_without_spawning(theme_env,monkeypatch):
    from core.theme_store import ThemeStore
    env=theme_env
    (env/'jobs/demo/other.yaml').write_text('name: Other\nprompt: research\noutput: output/other/{date}.md\n')
    (env/'config/matrices/other.yaml').write_text('name: Other\njobs: [demo/other]\n')
    (env/'config/production/other.yaml').write_text('job: demo/other\nrecipe: theme_research\nauto: 1\n')
    production.set_auto(str(env/'config'),'demo_value',1)
    themes=ThemeStore(str(env/'state'))
    today=datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat()
    ids=[]
    for matrix,category,count in (('demo','demo_value',5),('other','general',3)):
        for n in range(count):
            row=themes.create(matrix,category,f'{matrix} 独立问题 {n}',origin='generated',day=today)
            themes.mark(row['id'],'recommended')
            ids.append(row['id'])
    assert theme_workbench.enqueue_recommendations() == 8
    assert theme_workbench.enqueue_recommendations() == 0
    runs=ap.list_runs(limit=None)
    assert len(runs) == 8 and all(r['stage']=='ITEMS' for r in runs)
    assert all(not item['script_id'] for r in runs for item in r['items'])
    assert {r['spec']['theme_id'] for r in runs} == set(ids)
    assert all(themes.get(key)['status']=='queued' for key in ids)
    assert auto.recommendations_pending(str(env/'config'),str(env/'jobs'),str(env/'state')) == []


def test_completed_input_is_not_requeued_under_a_new_report_id(pilot_env):
    from core.video import content_guard
    env, _, _ = pilot_env
    job='monitoring/ai_flash_discovery'
    original=content_guard.policy_topic({'id':'old/F1','title':'作者改过的标题',
        'from_item_title':'同一条目','fields':{'一句话':'同一份内容','目标受众':'旧受众'}},job)
    script=store.create_script('flash',original)
    before=store.get_script(script['id'])
    new=content_guard.policy_topic({'id':'new/F1@r123','title':'同一条目',
        'fields':{'一句话':'同一份内容','目标受众':'新受众'}},job)
    assert content_guard.used_candidate_ids([new],str(env/'config')) == {new['id']}
    changed={**new,'fields':{'一句话':'发生了新的进展'}}
    assert not content_guard.used_candidate_ids([changed],str(env/'config'))
    assert store.get_script(script['id']) == before


def test_existing_suffix_keeps_its_video_association_only_for_identical_input(pilot_env):
    env, _, _ = pilot_env
    path=_report(env).with_name('2026-10-19_ai_flash_discovery_2.md')
    path.write_text(report_items(1))
    source=topic_store.resolve(str(env/'jobs'),'flash')
    _,[original]=topic_store.read_report(source,str(path),str(env))
    from core.video import columns,excerpt
    frozen=excerpt.topic_for(columns.get(str(env/'config'),'flash-nr'),original)
    script=store.create_script('flash-nr',frozen)
    before=store.get_script(script['id'])
    report_identity.publish(str(path),str(env))
    _,[same]=topic_store.read_report(source,str(path),str(env))
    assert same.id == original.id == '2026-10-19/F1@2'
    path.write_text('### 1. 现在是另一件事\n- **一句话**：不同输入\n')
    _,[different]=topic_store.read_report(source,str(path),str(env))
    assert different.id != original.id and different.title == '现在是另一件事'
    assert store.get_script(script['id']) == before

    # An old buggy suffix file must not reuse the base file's table key.
    third=path.with_name('2026-10-19_ai_flash_discovery_3.md')
    third.write_text(report_items(1))
    store.create_script('flash-nr',{**frozen,'id':'2026-10-19/F1',
        'from_report':str(third.relative_to(env))})
    _,[independent]=topic_store.read_report(source,str(third),str(env))
    assert independent.id == '2026-10-19/F1@3'


def test_resumed_theme_settles_from_attention_to_completed_without_rewriting(theme_env,monkeypatch):
    from core.theme_store import ThemeStore
    from web.services import video_executor
    themes=ThemeStore(str(theme_env/'state'))
    row=themes.create('demo','demo_value','恢复原有主题的成片')
    topic={'title':row['title'],'theme_task_id':row['id']}
    script=store.create_script('theme_research',topic)
    store.update_script(script['id'],status='APPROVED')
    themes.attach(row['id'],script['id'],True)
    themes.mark(row['id'],'attention')
    before=store.get_script(script['id'])
    run=ap.create_run({},[{'column_key':'theme_research','topic':topic}])
    item=run['items'][0]
    ap.update_item(item['id'],script_id=script['id'],state='WORKING')
    monkeypatch.setattr(video_executor,'advance_auto',lambda *a,**k:{
        'took':{'script':False,'render':False},'item':{'state':'DONE'},'bump':None,'hold':None})
    pilot.advance(ap.get_run(run['id'])['items'][0],can_start_script=False,can_start_render=False)
    assert themes.get(row['id'])['status']=='completed'
    assert store.get_script(script['id'])==before
