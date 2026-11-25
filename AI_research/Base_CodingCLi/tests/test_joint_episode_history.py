"""Real report-backed programmes have no topic ID; history must use their report revision."""
import copy
import json

import pytest

from core.video import joint_planning as joint, store, autopilot_store as ap
from web.services import video_autopilot as auto
from tests.test_joint_planning import batch  # noqa: F401
from tests.test_video_scripts import video_env, llm  # noqa: F401


@pytest.mark.parametrize('history_kind', ['script', 'queue'])
def test_older_episode_with_null_topic_id_does_not_drop_todays_episode(batch, llm, history_kind):
    spec, plan, _ = batch
    llm.replies = [json.dumps(plan)]
    current = auto.pick_topics(spec)
    old = copy.deepcopy(current[0])
    assert old['topic']['id'] is None
    old['topic']['from_report'] = 'output/flash/2026-11-15_ai_flash_discovery.md'
    old['topic']['episode']['report_path'] = old['topic']['from_report']
    old['topic']['episode']['report_identity'] = 'previous-day-revision'
    old['topic'].pop('joint_plan')
    if history_kind == 'script':
        row = store.create_script(old['column_key'],old['topic'],production=old['production'])
        store.update_script(row['id'],status='APPROVED')
    else:
        ap.create_run({},[old])
    again = auto.pick_topics(spec)
    assert [i['production'] for i in again] == ['flash_daily','flash_each']
    run = ap.create_run(spec,[])
    assert auto._add_items(run['id'],again,recheck=True)
    assert len(ap.get_run(run['id'])['items']) == 2
    assert len(llm.calls) == 1


@pytest.mark.parametrize('change,allowed', [('same',False),('new_revision',True),('other_production',True)])
@pytest.mark.parametrize('history_kind', ['script','queue'])
def test_episode_dedup_uses_production_and_report_revision(batch,llm,change,allowed,history_kind):
    spec,plan,_=batch
    llm.replies=[json.dumps(plan)]
    episode=auto.pick_topics(spec)[0]
    other=copy.deepcopy(episode)
    if change=='new_revision':other['topic']['episode']['report_identity']='different-content-same-path'
    if change=='other_production':other['production']='another-programme'
    if history_kind=='script':store.create_script(other['column_key'],other['topic'],production=other['production'])
    else:ap.create_run({},[other])
    assert bool(joint.available_items([episode])) is allowed


def test_legacy_episode_without_revision_uses_report_record(batch,llm):
    spec,plan,_=batch
    llm.replies=[json.dumps(plan)]
    episode=auto.pick_topics(spec)[0]
    episode['topic']['episode'].pop('report_identity')
    old=copy.deepcopy(episode)
    old['topic']['report_id']='older-report-record'
    ap.create_run({},[old])
    assert joint.available_items([episode]) == [episode]
    ap.create_run({},[episode])
    assert joint.available_items([episode]) == []


def test_production_absolute_report_path_keeps_original_source_packets(batch,video_env,llm):
    spec,plan,_=batch
    spec['report']=str(video_env/spec['report'])
    llm.replies=[json.dumps(plan)]*3
    items=auto.pick_topics(spec)
    request=json.loads(llm.calls[0][1]['content'])
    assert len(request['materials']) == 3
    assert all('install product' in packet['text'] for packet in request['materials'])
    assert len(items)==2 and items[0]['topic'].get('joint_plan')
    material=joint.assignment(items[1]['topic'])['material']
    assert 'install product0' in material and 'install product1' not in material
