"""Relation fallback preserves content and semantics, including reordered and parallel relations."""
import copy,json,os,subprocess
from pathlib import Path
import pytest
from PIL import ImageFont
from g2.process_diagram import FONT
from g2.shot_director import camera_plan
ROOT=Path(__file__).resolve().parents[2]

@pytest.fixture(scope='module')
def runtime(tmp_path_factory):
 out=tmp_path_factory.mktemp('relation-layout');source=out/'probe.ts';bundle=out/'probe.cjs';deps=ROOT/'g0/vendor/remotion-audiogram/node_modules'
 source.write_text("import {relationLayout,flowPresentation} from "+json.dumps(str(ROOT/'g1/renderer/src/RelationLayout'))+";\nconst x=JSON.parse(process.argv[2]);const m=(s,n)=>Array.from(s).reduce((a,c)=>a+(x.widths[c]??1)*n,0);process.stdout.write(JSON.stringify({layout:relationLayout(x.scene.props,x.scale,m),presentation:flowPresentation(x.scene,x.scale,m)}));")
 subprocess.run([str(deps/'.bin/esbuild'),str(source),'--bundle','--platform=node',f'--outfile={bundle}'],env={**os.environ,'NODE_PATH':str(deps)},check=True,capture_output=True)
 def run(scene,scale=1):
  p=scene['props'];text=''.join(n['label'] for n in p['nodes'])+''.join(e.get('label','') for e in p['edges'])+p.get('takeaway','');font=ImageFont.truetype(FONT,100)
  return json.loads(subprocess.check_output(['node',str(bundle),json.dumps({'scene':scene,'scale':scale,'widths':{c:font.getlength(c)/100 for c in set(text)}})]))
 return run

def fixture():
 nodes=[{'id':f'n{i}','label':'保持不同来源的输入说明以及最终处理条件完整可读'} for i in range(8)]
 edges=[{'id':f'e{i}','from':'n0','to':f'n{i+1}','label':'保留条件','relation':'blocked' if i==2 else 'flow'} for i in range(7)]
 props={'nodes':nodes,'edges':edges,'takeaway':'完整保留方向和条件','layout':{'font_size':12,'edge_font_size':10,'scale':1,'nodes':[{'id':n['id'],'x':i*180,'y':150,'width':170,'height':200} for i,n in enumerate(nodes)],'edges':[]}}
 scene={'id':'s','component':'flow_diagram','start_frame':0,'end_frame':600,'props':props,'beats':[{'id':f'b{i}','action':'reveal','target_id':f'node:n{i}','frame':40+i*65} for i in range(8)]};props['shot_plan']=camera_plan(scene,'balanced');return scene

@pytest.mark.parametrize('scale',[1,.9])
def test_long_fork_keeps_all_objects_conditions_and_screen_font(runtime,scale):
 scene=fixture();result=runtime(scene,scale);p=result['presentation'];assert p['mode']=='index'
 layout=p['index'];assert layout['fits'] and layout['nodeFont']*scale>=30 and layout['edgeFont']*scale>=24
 assert {n['id']:''.join(n['lines']) for n in layout['nodes']}=={n['id']:n['label'] for n in scene['props']['nodes']}
 assert [(e['id'],e['from'],e['to'],e['relation'],''.join(e['lines'])) for e in layout['edges']]==[(e['id'],e['from'],e['to'],e['relation'],e['label']) for e in scene['props']['edges']]
 assert all(n['y']+n['height']<=590 for n in [*layout['nodes'],*layout['edges']])

def test_return_and_parallel_edges_are_context_even_when_array_order_changes():
 scene=fixture();scene['props']['edges']=[{'id':'a','from':'n0','to':'n1'},{'id':'b','from':'n1','to':'n0'},{'id':'c','from':'n1','to':'n0'}]
 plan=camera_plan(scene,'balanced');assert set(plan['cues'][0]['targets'])=={'node:n0','node:n1'}
 scene['props']['nodes'].reverse();other=camera_plan(scene,'balanced');assert {c['beat_id']:set(c['targets']) for c in plan['cues']}=={c['beat_id']:set(c['targets']) for c in other['cues']}

def test_identity_dictionary_is_stable_when_node_array_is_permuted(runtime):
 scene=fixture();first=runtime(scene);scene['props']['nodes'].reverse();second=runtime(scene)
 assert first['layout']==second['layout']

def test_no_timer_or_speed_changes_the_selected_expression(runtime):
 scene=fixture();first=runtime(scene)['presentation'];scene['end_frame']=300
 for beat in scene['beats']:beat['frame']=round(beat['frame']/2)
 second=runtime(scene)['presentation'];assert first['mode']==second['mode']=='index' and first['index']==second['index']

def test_extreme_embedding_reports_capacity_instead_of_shrinking_or_paginating(runtime):
 result=runtime(fixture(),.4)['presentation'];assert result['reason']=='capacity-unmet'
 assert result['index']['nodeFont']*.4>=30 and not result['index']['fits']

def test_readable_simple_graph_keeps_its_graph_expression(runtime):
 scene=fixture();scene['props']['nodes']=scene['props']['nodes'][:2];scene['props']['edges']=[{'id':'e0','from':'n0','to':'n1','label':'通过'}]
 scene['props']['layout']={'font_size':36,'edge_font_size':28,'scale':1,'nodes':[{'id':'n0','x':200,'y':200,'width':200,'height':100},{'id':'n1','x':800,'y':200,'width':200,'height':100}], 'edges':[{'id':'e0','points':[{'x':400,'y':250},{'x':800,'y':250}]}]}
 scene['beats']=scene['beats'][:2];scene['props']['shot_plan']=camera_plan(scene,'balanced')
 assert runtime(scene)['presentation']['mode']=='graph'


def test_renaming_ids_preserves_every_relation_and_full_text(runtime):
 scene=fixture();mapping={n['id']:f"object_{8-i}" for i,n in enumerate(scene['props']['nodes'])}
 for n in scene['props']['nodes']:n['id']=mapping[n['id']]
 for e in scene['props']['edges']:e['from']=mapping[e['from']];e['to']=mapping[e['to']]
 for b in scene['beats']:b['target_id']='node:'+mapping[b['target_id'][5:]]
 scene['props']['shot_plan']=camera_plan(scene,'balanced')
 layout=runtime(scene)['presentation']['index']
 assert {n['id']:''.join(n['lines']) for n in layout['nodes']}=={n['id']:n['label'] for n in scene['props']['nodes']}
 assert {(e['id'],e['from'],e['to']) for e in layout['edges']}=={(e['id'],e['from'],e['to']) for e in scene['props']['edges']}
