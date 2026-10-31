import copy
import json
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from g1.tests.test_video_ir_v0_2_runtime import base_ir, make_root
from g1.video_ir import ValidationFault, validate_video_ir
from g2 import explanation, shot_director
from g2 import information_motion as motion
from g2.tests.test_explanation import chart

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize('saved,profile', [
    ({}, 'balanced'), ({'production_recipe': 'ai_news'}, 'brief'),
    ({'production_recipe': 'how_to'}, 'explain'), ({'production_recipe': 'science_explainer'}, 'explain'),
    ({'production_context': {'content_recipe': {'id': 'matrix_flash'}}}, 'brief'),
    ({'production_context': {'content_recipe': {'id': 'matrix_deep_dive'}}}, 'explain'),
    ({'production_recipe': 'ai_news', '_shot_profile': 'explain'}, 'explain'),
])
def test_recipe_and_matrix_profiles(saved, profile):
    assert shot_director.profile_for(saved) == profile


def test_mixed_pipeline_attaches_camera_to_existing_cues_without_new_model_calls(tmp_path):
    pipeline = SimpleNamespace(ARTIFACTS=tmp_path,
        write_json=lambda path, data: path.write_text(json.dumps(data)))
    props = chart()
    groups = [{'id': 'card_1', 'title': '趋势', 'text': '一月12，二月18，三月16。',
               'segment_ids': ['n1'], 'image_indices': []}]
    plan = {'groups': groups, 'pages': [{'id': 'card_1', 'component': 'data_chart', 'props': props}]}
    speech = {'rows': [{'id': 'n1', 'text': groups[0]['text'], 'start_ms': 0, 'end_ms': 12000}]}
    board = explanation.compile_storyboard(pipeline, plan, speech, {'production_recipe': 'science_explainer'}, [])
    scene = board['scenes'][0]
    camera = scene['props']['shot_plan']
    assert camera['profile'] == 'explain'
    assert camera['cues'][2]['targets'] == ['category:1', 'category:2']
    assert {c['beat_id'] for c in camera['cues']} <= {b['id'] for b in scene['beats']}
    assert scene['props']['series'] == props['series']
    assert scene['narration_segment_ids'] == ['n1']
    assert json.loads((tmp_path/'shot-director.json').read_text())['extra_model_calls'] == 0


def test_flow_focus_uses_graph_relationship_instead_of_neighboring_array_item():
    nodes = [{'id': key} for key in ('entry', 'left', 'right')]
    scene = {'component': 'flow_diagram', 'props': {'nodes': nodes, 'edges': [
        {'from': 'entry', 'to': 'left'}, {'from': 'entry', 'to': 'right'}]},
        'beats': [{'id': f'b_{n["id"]}', 'target_id': f'node:{n["id"]}', 'action': 'reveal'} for n in nodes]}
    plan = shot_director.camera_plan(scene, 'balanced')
    assert plan['cues'][-1]['targets'] == ['node:entry', 'node:right']


def test_scatter_camera_keeps_spoken_comparison_and_summary():
    scene = {'component': 'scatter_chart', 'props': {'points': [{}, {}, {}],
        'focus_steps': [{'indices': [0, 2]}], 'takeaway': '按需求选择'},
        'beats': [{'id': f'b{i}', 'target_id': f'line:{i}', 'action': 'reveal'} for i in range(5)]}
    plan = shot_director.camera_plan(scene, 'explain')
    assert plan['cues'][-1] == {'beat_id': 'b3', 'targets': ['point:0', 'point:2']}
    assert plan['summary_beat_id'] == 'b4'


def test_two_object_comparison_keeps_both_positions_while_the_native_chart_changes_emphasis():
    scene = {'component': 'scatter_chart', 'props': {'points': [{}, {}],
        'focus_steps': [{'indices': [0]}, {'indices': [1]}]},
        'beats': [{'id': f'b{i}', 'target_id': f'line:{i}', 'action': 'reveal'} for i in range(4)]}
    plan = shot_director.camera_plan(scene, 'balanced')
    assert all(cue['targets'] == ['point:0', 'point:1'] for cue in plan['cues'])


def test_context_points_and_later_reveals_do_not_replace_an_already_spoken_comparison():
    scene = {'component': 'scatter_chart', 'narration_segment_ids': ['n1'],
        'props': {'points': [{'name': '方案A'}, {'name': '方案B'}, {'name': '方案C'}],
                  'focus_steps': [{'indices': [0, 2], 'phrase': '先比较方案A和方案C'}]},
        'beats': [{'id': f'b{i}', 'target_id': f'line:{i}', 'action': 'reveal',
                   'trigger': {'segment_id': 'n1', 'offset_ms': at}}
                  for i, at in enumerate([1000, 2000, 3000, 500])]}
    plan = shot_director.camera_plan(scene, 'balanced', '先比较方案A和方案C，方案A的成本更低。')
    assert plan['cues'] == [{'beat_id': 'b3', 'targets': ['point:0', 'point:2']}]


def test_grouped_chart_uses_spoken_series_names_when_plan_supplies_monthly_phrases():
    p = {'title': '成本比较', 'kind': 'grouped', 'unit': '元', 'categories': ['一月', '二月', '三月'],
         'series': [{'name': '本地方案', 'values': [20, 25, 22]}, {'name': '云端方案', 'values': [30, 28, 32]}],
         'reveal_phrases': ['一月', '二月', '三月'], 'takeaway': '并排看差异'}
    text = '先看两个方案。一月本地方案20元，云端方案30元。二月25和28元。三月22和32元。并排看差异。'
    scene = {'component': 'data_chart', 'props': p, 'narration_segment_ids': ['n1']}
    cues = motion.automatic_cues(scene, {'n1': {'id': 'n1', 'text': text, 'start_ms': 0, 'end_ms': 15000}})
    assert [c['character_index'] for c in cues[1:]] == [text.index('本地方案'), text.index('云端方案'), text.index('并排看差异')]


def test_runtime_camera_references_follow_compiled_beats_after_timing_changes(tmp_path):
    root = make_root(tmp_path)
    ir = base_ir(root)
    scene = ir['scenes'][0]
    scene['props']['shot_plan'] = {'version': 1, 'profile': 'brief', 'cues': [
        {'beat_id': 'reveal_sender', 'targets': ['node:sender']}]}
    compiled = validate_video_ir(ir, root)
    assert compiled['scenes'][0]['props']['shot_plan'] == scene['props']['shot_plan']
    scene['beats'][0]['trigger']['offset_ms'] = 300
    revised = validate_video_ir(ir, root)
    assert revised['scenes'][0]['beats'][0]['frame'] > compiled['scenes'][0]['beats'][0]['frame']
    assert revised['scenes'][0]['props']['shot_plan'] == compiled['scenes'][0]['props']['shot_plan']
    broken = copy.deepcopy(ir)
    broken['scenes'][0]['props']['shot_plan']['cues'][0]['targets'] = ['node:missing']
    with pytest.raises(ValidationFault, match='SHOT_REFERENCE'):
        validate_video_ir(broken, root)
    broken['scenes'][0]['props']['shot_plan']['cues'][0] = {'beat_id': 'missing', 'targets': ['node:sender']}
    with pytest.raises(ValidationFault, match='SHOT_REFERENCE'):
        validate_video_ir(broken, root)


@pytest.fixture(scope='module')
def camera_runtime(tmp_path_factory):
    folder = tmp_path_factory.mktemp('shot-runtime')
    source = folder/'camera.ts'
    source.write_text('''
import {cameraAt,shotsFor,materialAt,transitionAt,pacingFor,comparisonAt} from CAMERA;
import {imageRects} from MATERIALS;
import {scatterState} from SCATTER;
const input=JSON.parse(process.argv[2]);
const scene=input.scene;
console.log(JSON.stringify({shots:shotsFor(scene),
  pacing:pacingFor(scene),
  transitions:input.frames.map(frame=>transitionAt(scene,frame)),
  material:scene.props.images?input.frames.map(frame=>materialAt(scene,frame)):null,
  scatter:scene.component==='scatter_chart'?input.frames.map(frame=>scatterState(scene,frame)):null,
  comparison:scene.component==='comparison'?input.frames.map(frame=>comparisonAt(scene,frame)):null,
  image:input.image?imageRects(input.image,0,1000,500):null,
  cameras:input.frames.map(frame=>cameraAt(scene,frame,input.rects,input.view))}));
'''.replace('CAMERA', json.dumps(str(ROOT/'g1/renderer/src/ShotDirector.tsx')))
   .replace('MATERIALS', json.dumps(str(ROOT/'g1/renderer/src/DirectedMaterials.tsx')))
   .replace('SCATTER', json.dumps(str(ROOT/'g1/renderer/src/ScatterChart.tsx'))))
    target = folder/'camera.cjs'
    env = {**os.environ, 'NODE_PATH': str(ROOT/'g0/vendor/remotion-audiogram/node_modules')}
    subprocess.run([str(ROOT/'g0/vendor/remotion-audiogram/node_modules/.bin/esbuild'), str(source),
        '--bundle', '--platform=node', '--format=cjs', '--jsx=automatic', f'--outfile={target}'],
        env=env, check=True, capture_output=True, text=True)

    def run(scene, frames, rects=None, view=None, image=None):
        payload = {'scene': scene, 'frames': frames, 'rects': rects or {},
                   'view': view or {'x': 20, 'y': 70, 'width': 1000, 'height': 400}, 'image': image}
        result = subprocess.run([shutil.which('node'), str(target), json.dumps(payload)],
                                check=True, capture_output=True, text=True)
        return json.loads(result.stdout)
    return run


def camera_scene(profile='balanced', length=600):
    return {'start_frame': 100, 'end_frame': 100+length, 'props': {'shot_plan': {
        'version': 1, 'profile': profile, 'summary_beat_id': 'summary', 'cues': [
            {'beat_id': 'first', 'targets': ['node:a']}, {'beat_id': 'second', 'targets': ['node:b']},
            {'beat_id': 'third', 'targets': ['node:c']}]}},
        'beats': [{'id': 'first', 'frame': 110}, {'id': 'second', 'frame': 130},
                  {'id': 'third', 'frame': 310}, {'id': 'summary', 'frame': 580}]}


def test_dense_cues_get_a_sustained_view_and_summary_returns_to_overview(camera_runtime):
    scene = camera_scene()
    result = camera_runtime(scene, [100, 160, 340, 610], {
        'node:a': {'x': 25, 'y': 100, 'width': 100, 'height': 80},
        'node:b': {'x': 180, 'y': 100, 'width': 100, 'height': 80},
        'node:c': {'x': 800, 'y': 100, 'width': 100, 'height': 80}})
    assert result['shots'][1]['targets'] == ['node:a', 'node:b']
    assert [s['kind'] for s in result['shots']] == ['overview', 'focus', 'focus', 'summary']
    assert result['cameras'][1]['pose']['zoom'] > 1
    assert result['cameras'][-1]['pose'] == {'zoom': 1, 'x': 0, 'y': 0}


def test_short_scenes_hold_overview_and_missing_legacy_plan_is_identity(camera_runtime):
    scene = camera_scene(length=120)
    assert len(camera_runtime(scene, [160])['shots']) == 1
    del scene['props']['shot_plan']
    assert camera_runtime(scene, [110])['cameras'][0]['pose'] == {'zoom': 1, 'x': 0, 'y': 0}


def test_camera_stays_inside_canvas_at_edges_and_is_seek_deterministic(camera_runtime):
    scene = camera_scene()
    frames = [340, 312, 341, 340]
    result = camera_runtime(scene, frames, {
        'node:a': {'x': 0, 'y': 70, 'width': 10, 'height': 10},
        'node:c': {'x': 990, 'y': 440, 'width': 10, 'height': 10}})
    assert result['cameras'][0] == result['cameras'][-1]
    for camera in result['cameras']:
        pose = camera['pose']
        assert 1 <= pose['zoom'] <= 1.8
        assert 1020*(1-pose['zoom'])-1e-6 <= pose['x'] <= 20*(1-pose['zoom'])+1e-6
        assert 470*(1-pose['zoom'])-1e-6 <= pose['y'] <= 70*(1-pose['zoom'])+1e-6


def test_spoken_object_takes_priority_over_profile_establishing_time(camera_runtime):
    for profile in ('brief', 'balanced', 'explain'):
        scene = camera_scene(profile)
        result = camera_runtime(scene, [])
        assert result['shots'][1]['frame'] <= scene['beats'][0]['frame']


def test_same_page_carries_settled_pose_after_beat_retiming(camera_runtime):
    previous = camera_scene()
    previous.update(component='object_showcase')
    previous['props'].update(images=[{'image_path': 'page.png'}], focus_indices=[0])
    previous['props']['shot_plan'].update(closing='hold', cues=[{'beat_id': 'third', 'targets': ['region:0:0']}])
    scene = copy.deepcopy(previous)
    scene.update(start_frame=700, end_frame=1300, previous_scene=previous)
    scene['props']['scene_flow'] = {'entry': 'carry', 'profile': 'balanced'}
    scene['props']['shot_plan'].pop('closing')
    scene['beats'] = [{'id': 'third', 'frame': 880}, {'id': 'summary', 'frame': 1240}]
    scene['props']['shot_plan']['cues'] = [{'beat_id': 'third', 'targets': ['region:0:1']}]
    rects = {'region:0:0': {'x': 25, 'y': 100, 'width': 150, 'height': 80},
             'region:0:1': {'x': 700, 'y': 100, 'width': 150, 'height': 80}}
    end = camera_runtime(previous, [699], rects)
    start = camera_runtime(scene, [700, 860, 880, 1299], rects)
    assert end['shots'][-1]['kind'] == 'focus'
    assert start['shots'][0]['kind'] == 'carry'
    assert end['cameras'][0]['pose'] == start['cameras'][0]['pose']
    assert start['cameras'][0]['pose'] == start['cameras'][1]['pose']
    assert start['cameras'][2]['pose'] != start['cameras'][0]['pose']
    assert start['cameras'][-1]['pose']['zoom'] == 1
    assert start['transitions'][0]['entry'] == 'carry'
    scene['start_frame'], scene['end_frame'] = 710, 770
    scene['previous_scene']['end_frame'] = 710
    assert camera_runtime(scene, [710], rects)['cameras'][0]['pose'] == end['cameras'][0]['pose']
    scene['end_frame'] = 890
    scene['beats'] = [{'id': 'third', 'frame': 800}, {'id': 'summary', 'frame': 870}]
    short = camera_runtime(scene, [710, 889], rects)
    assert [s['kind'] for s in short['shots']] == ['carry', 'focus', 'summary']
    assert short['cameras'][-1]['pose'] == {'zoom': 1, 'x': 0, 'y': 0}


def test_late_spoken_focus_is_not_dropped_to_make_room_for_a_cosmetic_summary(camera_runtime):
    scene = camera_scene()
    scene['props']['shot_plan'].pop('summary_beat_id')
    scene['beats'][2]['frame'] = 680
    result = camera_runtime(scene, [680])
    assert result['cameras'][0]['shot']['targets'] == ['node:c']
    assert result['shots'][-1]['kind'] == 'focus'


def scatter_scene():
    return {'component': 'scatter_chart', 'start_frame': 0, 'end_frame': 600,
        'props': {'points': [{'name': 'max', 'x': 6, 'y': 700}, {'name': 'xhigh', 'x': 3, 'y': 100}],
                  'focus_steps': [{'indices': [0], 'phrase': 'max', 'caption': 'max读数'}]},
        'beats': [{'id': 'p0', 'target_id': 'line:0', 'frame': 60},
                  {'id': 'p1', 'target_id': 'line:1', 'frame': 300},
                  {'id': 'f0', 'target_id': 'line:2', 'frame': 60}]}


def test_new_scatter_object_replaces_an_old_focus_step_and_is_ready_at_its_spoken_beat(camera_runtime):
    scene = scatter_scene()
    result = camera_runtime(scene, [299, 300, 450, 300])['scatter']
    assert all(state['selected'] == [1] for state in result)
    assert result[1]['since'] + 15 == 300
    assert result[1].get('caption') is None
    assert result[1] == result[-1]


def test_background_reveals_preserve_a_spoken_comparison_group(camera_runtime):
    scene = scatter_scene()
    scene['props']['focus_steps'][0]['indices'] = [0, 1]
    scene['props']['shot_plan'] = {'version': 1, 'profile': 'balanced',
        'cues': [{'beat_id': 'f0', 'targets': ['point:0', 'point:1']}]}
    states = camera_runtime(scene, [60, 300, 450])['scatter']
    assert all(state['selected'] == [0, 1] for state in states)
    assert all(state['caption'] == 'max读数' for state in states)


def test_conclusion_reveals_the_comparison_even_when_planned_reveals_come_later(camera_runtime):
    scene = scatter_scene()
    scene['props'].update(takeaway='把两种选择一起看', focus_indices=[0, 1])
    scene['beats'].append({'id': 'end', 'target_id': 'line:3', 'frame': 200})
    state = camera_runtime(scene, [200])['scatter'][0]
    assert state['shown'] == state['selected'] == [0, 1]
    assert state['caption'] == '把两种选择一起看'
    later = camera_runtime(scene, [300])['scatter'][0]
    assert later['selected'] == [1]
    assert later.get('caption') is None


def test_conclusion_first_does_not_suppress_later_explanation_camera_cues(camera_runtime):
    scene = camera_scene()
    scene['beats'][-1]['frame'] = 105
    result = camera_runtime(scene, [340])
    assert result['cameras'][0]['shot']['targets'] == ['node:c']
    assert next(shot['frame'] for shot in result['shots'] if shot['kind'] == 'summary') < result['shots'][-1]['frame']


def test_same_scatter_canvas_keeps_objects_visible_and_focus_between_speech_units(camera_runtime):
    previous = scatter_scene()
    scene = copy.deepcopy(previous)
    scene.update(start_frame=600, end_frame=1200, previous_scene=previous)
    scene['props']['scene_flow'] = {'entry': 'carry', 'profile': 'balanced'}
    scene['beats'] = [{'id': 'p0', 'target_id': 'line:0', 'frame': 900},
                      {'id': 'p1', 'target_id': 'line:1', 'frame': 1050},
                      {'id': 'f0', 'target_id': 'line:2', 'frame': 900}]
    state = camera_runtime(scene, [600, 870, 900])
    assert state['transitions'][0]['entry'] == 'carry'
    assert state['scatter'][0]['shown'] == [0, 1]
    assert state['scatter'][0]['selected'] == state['scatter'][1]['selected'] == [1]
    assert state['scatter'][0]['times'] == [585, 585]
    assert state['scatter'][2]['selected'] == [0]
    scene['props']['points'][0]['y'] = 800
    assert camera_runtime(scene, [600])['transitions'][0]['entry'] == 'dissolve'


def test_comparison_context_is_ready_during_summary_instead_of_waiting_for_last_sentence(camera_runtime):
    scene = {'component': 'comparison', 'start_frame': 0, 'end_frame': 600,
             'props': {'left': {'label': '模型调用'}, 'right': {'label': '单次授权'}},
             'beats': [{'id': 'left', 'target_id': 'side:left', 'frame': 450},
                       {'id': 'right', 'target_id': 'side:right', 'frame': 550}]}
    states = camera_runtime(scene, [30, 200, 450, 550])['comparison']
    assert all(state['opacity'] == 1 for state in states)
    assert states[0].get('target') is None
    assert states[2]['target'] == 'side:left'
    assert states[3]['target'] == 'side:right'
    next_scene = copy.deepcopy(scene)
    next_scene.update(start_frame=600, end_frame=1200, previous_scene=scene, beats=[])
    next_scene['props']['scene_flow'] = {'entry': 'carry', 'profile': 'balanced'}
    assert camera_runtime(next_scene, [600])['comparison'][0] == {'opacity': 1, 'target': 'side:right'}


def test_pair_keeps_emphasis_at_boundary_and_changed_picture_falls_back_to_dissolve(camera_runtime):
    previous = camera_scene()
    previous.update(component='material_collage')
    previous['props'].update(images=[{'image_path': 'a.png'}, {'image_path': 'b.png'}], focus_indices=[0, 1])
    previous['props']['shot_plan'].update(closing='hold', cues=[{'beat_id': 'third', 'targets': ['image:1']}])
    scene = copy.deepcopy(previous)
    scene.update(start_frame=700, end_frame=1300, previous_scene=previous, beats=[])
    scene['props']['scene_flow'] = {'entry': 'carry', 'profile': 'balanced'}
    scene['props']['shot_plan']['cues'] = []
    assert camera_runtime(previous, [699])['material'][0]['weights'] == camera_runtime(scene, [700])['material'][0]['weights']
    scene['props']['images'][0]['image_path'] = 'changed.png'
    result = camera_runtime(scene, [700, 706.5, 713])
    assert result['shots'][0]['kind'] == 'overview'
    assert [t['progress'] for t in result['transitions']] == [0, .5, 1]
    assert [t['active'] for t in result['transitions']] == [True, True, False]
