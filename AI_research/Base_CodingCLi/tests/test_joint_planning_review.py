"""Failure/race regressions for shared episode/individual planning. Isolated state only."""
import copy
import json
import sqlite3

import pytest

from core.video import joint_planning as joint, store, autopilot_store as ap
from web.services import video_autopilot as auto
from tests.test_joint_planning import batch  # noqa: F401
from tests.test_video_scripts import video_env, llm  # noqa: F401


def test_cached_plan_does_not_requeue_an_already_reserved_angle(batch, llm):
    spec, plan, _ = batch
    llm.replies = [json.dumps(plan)]
    episode, single = auto.pick_topics(spec)
    existing = ap.create_run(spec, [single])
    again = auto.pick_topics(spec)
    assert [i['production'] for i in again] == ['flash_daily']
    assert len(llm.calls) == 1
    assert ap.get_run(existing['id'])['items'][0]['topic'] == single['topic']


def test_late_background_result_does_not_claim_another_batchs_items(batch, llm, monkeypatch):
    spec, plan, _ = batch
    llm.replies = [json.dumps(plan)]
    items = auto.pick_topics(spec)
    waiting = ap.create_run(spec, [])
    owner = ap.create_run(spec, items)
    monkeypatch.setattr(auto, '_background_topics', lambda _: (items, []))
    monkeypatch.setattr(auto, 'advance', lambda *_ , **kw: pytest.fail('must not start duplicate work'))
    auto._tick_run(waiting)
    assert not ap.get_run(waiting['id'])['items']
    assert ap.get_run(waiting['id'])['status'] == 'DONE'
    assert len(ap.get_run(owner['id'])['items']) == 2


def test_cancel_between_poll_and_admission_never_starts_work(batch, llm, monkeypatch):
    spec, plan, _ = batch
    llm.replies = [json.dumps(plan)]
    items = auto.pick_topics(spec)
    run = ap.create_run(spec, [])
    def completed(_):
        ap.update_run(run['id'], status='CANCELLED')
        return items, []
    monkeypatch.setattr(auto, '_background_topics', completed)
    started = []
    monkeypatch.setattr(auto, 'advance', lambda item, **kw: started.append(item) or {'script':0, 'render':0})
    auto._tick_run(run)
    assert not ap.get_run(run['id'])['items']
    assert not started
    assert ap.get_run(run['id'])['status'] == 'CANCELLED'


def test_failed_topics_transition_rolls_back_item_insertion(batch, llm, monkeypatch):
    spec, plan, _ = batch
    llm.replies = [json.dumps(plan)]
    items = auto.pick_topics(spec)
    run = ap.create_run(spec, [])
    monkeypatch.setattr(auto, '_background_topics', lambda _: (items, []))
    with store.connect(write=True) as db:
        db.execute("CREATE TRIGGER fail_stage BEFORE UPDATE OF stage ON autopilot_runs BEGIN SELECT RAISE(ABORT, 'simulated transition failure'); END")
    with pytest.raises(sqlite3.IntegrityError):
        auto._tick_run(run)
    assert not ap.get_run(run['id'])['items']
    assert ap.get_run(run['id'])['stage'] == 'TOPICS'
    with store.connect(write=True) as db:
        db.execute('DROP TRIGGER fail_stage')
    monkeypatch.setattr(auto, 'advance', lambda *_ , **kw: {'script':0, 'render':0})
    auto._tick_run(ap.get_run(run['id']))
    assert len(ap.get_run(run['id'])['items']) == 2


@pytest.mark.parametrize('bad', ['missing_roles', 'invalid_episode_title'])
def test_invalid_allocation_cannot_silently_drop_or_break_programme(batch, llm, bad):
    spec, plan, _ = batch
    broken = copy.deepcopy(plan)
    if bad == 'missing_roles':
        for row in broken['allocations']:
            row.pop('episode'); row.pop('single')
    else:
        broken['allocations'][0]['episode']['title'] = ['not', 'a', 'title']
    llm.replies = [json.dumps(broken)] * 3
    notes = []
    items = auto.pick_topics(spec, notes)
    assert len(llm.calls) == 3
    assert len(items) == 4 and all(not i['topic'].get('joint_plan') for i in items)
    assert '保留原选题流程' in notes[-1]


@pytest.mark.parametrize('role', ['episode', 'single'])
def test_frozen_assignment_survives_original_report_moving(batch, video_env, llm, role):
    from web.services import video_workflow as workflow
    from tests.test_video_scripts import script_json
    from tests.test_video_story_pool import event
    spec, plan, urls = batch
    llm.replies = [json.dumps(plan)]
    items = auto.pick_topics(spec)
    item = items[0 if role == 'episode' else 1]
    script = store.create_script('flash-nr', item['topic'], production=item['production'])
    (video_env/spec['report']).rename(video_env/'archived-report.md')
    if role == 'episode':
        reply = json.dumps({'title':'合集', 'sections':[
            {'title':'开场','episode_role':'opening','paragraphs':[{'text':'今天有三个变化。','citations':[]}]},
            *[{'title':f'产品{i}的新发布','episode_role':'item','paragraphs':[{'text':f'产品{i}支持导出文件。','citations':[url]}]} for i,url in enumerate(urls)],
            {'title':'结尾','episode_role':'closing','paragraphs':[{'text':'本期介绍到这里。','citations':[]}]}]})
        llm.replies = [reply]
    else:
        reply = script_json(350, citations={(0,0):[urls[0]]})
        llm.replies = [json.dumps({'events':[event(title='表格导出', subject='产品0', action='release', edition='v2', primary=['S1'])]}), reply]
    assert workflow.run(script['id'],llm_config=object()) == 'DRAFT'
    first = store.get_version(script['id'],1)
    llm.replies = [reply]
    assert workflow.run_rewrite(script['id'],llm_config=object()) == 'DRAFT'
    assert store.get_script(script['id'])['current_version'] == 2
    assert first['meta']['joint_plan'] == store.get_version(script['id'],2)['meta']['joint_plan']


def test_two_completed_planners_atomically_claim_only_one_queue(batch, llm):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    spec, plan, _ = batch
    llm.replies = [json.dumps(plan)]
    items = auto.pick_topics(spec)
    runs = [ap.create_run(spec, []) for _ in range(2)]
    barrier = threading.Barrier(2)
    def admit(run):
        barrier.wait(timeout=5)
        return auto._add_items(run['id'], items, recheck=True)
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(admit, runs))
    assert sorted(results) == [False, True]
    assert sum(len(ap.get_run(r['id'])['items']) for r in runs) == 2


def test_resume_old_interrupted_transition_uses_existing_items_without_replanning(batch, llm, monkeypatch):
    spec, plan, _ = batch
    llm.replies = [json.dumps(plan)]
    items = auto.pick_topics(spec)
    run = ap.create_run(spec, items)
    ap.update_run(run['id'], stage='TOPICS')
    monkeypatch.setattr(auto, '_background_topics', lambda *_: pytest.fail('existing items must resume directly'))
    monkeypatch.setattr(auto, 'advance', lambda *_ , **kw: {'script':0,'render':0})
    auto._tick_run(ap.get_run(run['id']))
    after = ap.get_run(run['id'])
    assert after['stage'] == 'ITEMS'
    assert [i['id'] for i in after['items']] == [i['id'] for i in run['items']]


def test_stale_cancelled_run_does_not_start_a_new_planner(batch, monkeypatch):
    spec, _, _ = batch
    run = ap.create_run(spec, [])
    ap.update_run(run['id'], status='CANCELLED')
    monkeypatch.setattr(auto, '_background_topics', lambda *_: pytest.fail('cancelled run must not plan'))
    assert auto._tick_run(run) == 0


def test_single_can_finish_before_its_joint_episode(batch, llm):
    from web.services import video_workflow as workflow
    from tests.test_video_scripts import script_json
    from tests.test_video_story_pool import event
    spec, plan, urls = batch
    llm.replies = [json.dumps(plan)]
    episode, single = auto.pick_topics(spec)
    row = store.create_script('flash-nr', single['topic'], production=single['production'])
    llm.replies = [json.dumps({'events':[event(title='表格导出', subject='产品0', action='release', edition='v2', primary=['S1'])]}),
                   script_json(350, citations={(0,0):[urls[0]]})]
    assert workflow.run(row['id'],llm_config=object()) == 'DRAFT'
    store.update_script(row['id'],status='APPROVED')
    row = store.create_script('flash-nr', episode['topic'], production=episode['production'])
    llm.replies = [json.dumps({'title':'合集', 'sections':[
        {'title':'开场','episode_role':'opening','paragraphs':[{'text':'今天有三个变化。','citations':[]}]},
        *[{'title':f'产品{i}的新发布','episode_role':'item','paragraphs':[{'text':f'产品{i}支持导出文件。','citations':[url]}]} for i,url in enumerate(urls)],
        {'title':'结尾','episode_role':'closing','paragraphs':[{'text':'本期介绍到这里。','citations':[]}]}]})]
    assert workflow.run(row['id'],llm_config=object()) == 'DRAFT'
    assert len(store.get_version(row['id'],1)['meta']['episode']['items']) == 3
    assert not store.list_renders(row['id'])
