"""The same report must support an episode and bounded raw-material recovery.

Run real picking, episode writing, coverage, pool claims and autopilot transitions
against an isolated DB; only provider responses are synthetic. Never render/publish.
"""
import json
from pathlib import Path

import pytest

from core import report_meta, report_material
from core.video import audience_strategy as strategy, store, story_pool, content_guard
from core.video import autopilot_store as ap
from tests.test_audience_rollout import chosen
from tests.test_video_scripts import video_env, llm, script_json  # noqa: F401
from tests.test_video_story_pool import event
from web.services import video_autopilot as auto, video_workflow as workflow


@pytest.mark.parametrize('raw_available', [True, False])
def test_episode_then_singles_recover_unused_material_without_repeating_episode(video_env, llm, monkeypatch, raw_available):
    monkeypatch.chdir(video_env)
    urls = [f'https://example.com/release-{i}' for i in range(4)]
    material = '\n\n'.join(f'### [发布{i}]({url})\n独立产品{i}公开了新版本和使用流程。' for i, url in enumerate(urls[:4 if raw_available else 3]))
    selection = strategy.Selection(candidates=[chosen().candidates[0].model_copy(update={
        'title':f'产品{i}的新发布', 'source_ids':[f'S{i+1}'], 'source_roles':{},
        'material':f'产品{i}的新变化及操作结果'}) for i in range(3)], tradeoffs='')
    text = strategy.render_report(selection, strategy.packets(material), 'flash', original_report='# 2026-11-16 AI快讯')
    path = video_env/'output/flash/2026-11-16_ai_flash_discovery.md'
    path.parent.mkdir(parents=True); path.write_text(text)
    relative = path.relative_to(video_env).as_posix()
    ref = report_material.save(str(path), text, [material])
    report_meta.append_record(relative, 'monitoring/ai_flash_discovery', extra={
        'material':ref, 'audience_strategy':{'strategy':strategy.MARKER, 'channel':'flash'}})
    spec = {'job':'monitoring/ai_flash_discovery', 'report':relative, 'picks':[
        {'column':'flash-nr', 'production':'flash_daily', 'whole':True, 'content_mode':'episode'},
        {'column':'flash-nr', 'production':'flash_each', 'source':'flash', 'count':3}]}
    picks = auto.pick_topics(spec)
    assert len(picks) == 4 and len(picks[0]['topic']['episode']['items']) == 3
    run = ap.create_run(spec, picks)
    # An unrelated running task is never cancelled, edited or adopted.
    other = store.create_script('flash-nr', {'title':'其他正在运行的任务','id':'unrelated-running'}, production='other')
    store.update_script(other['id'], status='WRITING')
    before = store.get_script(other['id'])
    episode_item = run['items'][0]
    episode = store.create_script('flash-nr', episode_item['topic'], production='flash_daily')
    llm.replies = [json.dumps({'title':'今日快讯', 'sections':[
        {'title':'本期导读','episode_role':'opening','paragraphs':[{'text':'今天介绍三项变化。','citations':[]}]},
        *[{'title':f'产品{i}的新发布','episode_role':'item',
           'paragraphs':[{'text':f'产品{i}公开了新版本和使用流程。','citations':[urls[i]]}]} for i in range(3)],
        {'title':'结尾','episode_role':'closing','paragraphs':[{'text':'这些就是本期更新。','citations':[]}]}]})]
    assert workflow.run(episode['id'], llm_config=object()) == 'DRAFT'
    store.update_script(episode['id'], status='APPROVED')
    ap.update_item(episode_item['id'], script_id=episode['id'], state='DONE')
    assert len(store.get_version(episode['id'], 1)['meta']['episode']['items']) == 3
    for i, item in enumerate(run['items'][1:]):
        single = store.create_script('flash-nr', item['topic'], production='flash_each')
        ap.update_item(item['id'], script_id=single['id'], state='WORKING')
        llm.replies = [json.dumps({'events':[event(title=f'产品{i}的新发布', subject=f'产品{i}',
                                                    action='release', edition='v2', primary=['S1'])]})]
        assert workflow.run(single['id'], llm_config=object()) == 'FAILED'
        decisions = story_pool.selection(single['id'])
        assert decisions['skipped'] == 1
        assert any(d.get('peer_id') == episode['id'] for d in decisions['decisions'])
        auto.advance(ap.get_run(run['id'])['items'][i+1], can_start_script=True, can_start_render=False)
    singles = ap.get_run(run['id'])['items'][1:]
    retry = singles[0]
    assert retry['state'] == 'PENDING' and retry['topic']['_material_reselection']
    assert all(item['state'] == 'CANCELLED' for item in singles[1:])  # existing bounded second-pass policy
    assert not retry['topic'].get('from_item')
    fresh = store.create_script('flash-nr', retry['topic'], production='flash_each')
    ap.update_item(retry['id'], script_id=fresh['id'], state='WORKING')
    monkeypatch.setattr(auto, '_heat_rank', lambda pick, column, rows, taken: (rows, ''))
    llm.replies = ([json.dumps({'events':[event(title='产品3的新发布', subject='产品3', action='release',
                                               edition='v2', primary=['S4'])]}),
                    script_json(300, citations={(0,0):[urls[3]]})] if raw_available else [])
    outcome = workflow.run(fresh['id'], llm_config=object())
    assert outcome == ('DRAFT' if raw_available else 'FAILED')
    if raw_available:
        version = store.get_version(fresh['id'], 1)
        assert version['meta']['story']['primary_sources'] == [urls[3]]
        request = llm.calls[-1][1]['content']
        from core.video import writer
        assert writer.SCOPE_CONCRETE_RULE in llm.calls[-1][0]['content']
        assert urls[3] in request and all(url not in request for url in urls[:3])
        store.update_script(fresh['id'], status='APPROVED')
        assert not content_guard.sources(request) & set(urls[:3])
    else:
        auto.advance(ap.get_run(run['id'])['items'][1], can_start_script=True, can_start_render=False)
        assert ap.get_run(run['id'])['items'][1]['state'] == 'CANCELLED'
    assert store.get_script(other['id']) == before
    assert store.get_script(episode['id'])['status'] == 'APPROVED'


def test_all_marked_candidates_filtered_before_start_still_get_raw_fallback(video_env, monkeypatch):
    from tests.test_audience_rollout import report, MATERIAL
    monkeypatch.chdir(video_env)
    path=video_env/'output/flash/2026-11-16_ai_flash_discovery.md'
    path.parent.mkdir(parents=True); path.write_text(report())
    relative=path.relative_to(video_env).as_posix()
    report_meta.append_record(relative, 'monitoring/ai_flash_discovery', extra={
        'audience_strategy':{'strategy':strategy.MARKER,'channel':'flash'}})
    monkeypatch.setattr(content_guard, 'known_duplicate_ids', lambda candidates, config: {c['id'] for c in candidates})
    picks = auto.pick_topics({'report':relative, 'picks':[
        {'column':'flash-nr','production':'flash_each','source':'flash','count':3}]})
    assert len(picks) == 1 and picks[0]['topic']['_material_reselection']
