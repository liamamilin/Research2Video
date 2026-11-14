"""Independent semantics and geometry checks for frozen relation cohorts."""
import argparse,copy,hashlib,json,os,subprocess,sys
from pathlib import Path
from types import SimpleNamespace
from PIL import ImageFont
ROOT=Path(__file__).resolve().parents[3];sys.path.insert(0,str(ROOT));HERE=Path(__file__).parent
from g2 import flow_layouts,shot_director
from g2.process_diagram import FONT

def prepare(cases,out):
 inputs=[];font=ImageFont.truetype(FONT,100)
 for c in cases:
  props=copy.deepcopy(c['props']);scene={'id':c['id'],'component':'flow_diagram','props':props,'start_frame':0,'end_frame':c['duration_frames'],'beats':c['beats']}
  props['shot_plan']=shot_director.camera_plan(scene,c['profile'])
  texts=''.join(n['label'] for n in props['nodes'])+''.join(e.get('label','') for e in props['edges'])+props.get('takeaway','')
  inputs.append({'id':c['id'],'scene':scene,'frames':sorted({b['frame'] for b in c['beats'] if b['target_id'].startswith(('node:','edge:'))}), 'widths':{char:font.getlength(char)/100 for char in set(texts)}})
 env={**os.environ,'NODE_PATH':str(ROOT/'g0/vendor/remotion-audiogram/node_modules')}
 pipeline=SimpleNamespace(ARTIFACTS=out,PROJECT=ROOT,NODE='node',write_json=lambda p,x:p.write_text(json.dumps(x,ensure_ascii=False)),_run_fixed_command=lambda name,args,cwd,timeout:subprocess.check_output([str(a) for a in args],cwd=cwd,timeout=timeout,env=env))
 flow_layouts.attach({'scenes':[c['scene'] for c in inputs]},pipeline)
 return inputs

def required(c,frame):
 targets=[b['target_id'] for b in c['beats'] if b['frame']==frame and b['target_id'].startswith(('node:','edge:'))]
 nodes={t[5:] for t in targets if t.startswith('node:')};edges={t[5:] for t in targets if t.startswith('edge:')}
 edges|={e['id'] for e in c['props']['edges'] if e['from'] in nodes or e['to'] in nodes}
 nodes|={key for e in c['props']['edges'] if e['id'] in edges for key in [e['from'],e['to']]}
 return nodes,edges

def run(split,out,bundle,case_path=None):
 raw=(case_path or HERE/f'{split}.json').read_bytes()
 if case_path is None:
  manifest=json.loads((HERE/'manifest.json').read_text());assert hashlib.sha256(raw).hexdigest()==manifest['files'][f'{split}.json']['sha256']
 cases=json.loads(raw);out.mkdir(parents=True,exist_ok=False);inputs=prepare(cases,out);(out/'input.json').write_text(json.dumps(inputs,ensure_ascii=False));result=json.loads(subprocess.check_output(['node',str(bundle),str(out/'input.json')]))
 (out/'runtime.json').write_text(json.dumps(result,ensure_ascii=False));reports=[]
 for c,r in zip(cases,result):
  for view in r['scales']:
   scale=view['scale'];issues=set();sizes=[];mode=view['mode']
   if mode=='index':
    layout=view['index'];node_map={n['id']:n for n in layout['nodes']};edge_map={e['id']:e for e in layout['edges']}
    if set(node_map)!={n['id'] for n in c['props']['nodes']} or set(edge_map)!={e['id'] for e in c['props']['edges']}:issues.add('identity_missing')
    for n in c['props']['nodes']:
     if ''.join(node_map[n['id']]['lines'])!=n['label']:issues.add('node_text_changed')
    for e in c['props']['edges']:
     drawn=edge_map[e['id']]
     if any(drawn[k]!=e[k] for k in ['from','to']) or ''.join(drawn['lines'])!=e.get('label','') or drawn.get('relation','flow')!=e.get('relation','flow'):issues.add('relationship_changed')
    for box in [*layout['nodes'],*layout['edges']]:
     if box['x']<0 or box['y']<0 or box['x']+box['width']>1560+.01 or box['y']+box['height']>590+.01:issues.add('index_clipped')
    sizes=[layout['nodeFont']*scale];edge_size=layout['edgeFont']*scale
    if sizes[0]<29.99 or edge_size<23.99:issues.add('small_text')
    if not layout['fits']:issues.add('capacity_unmet')
   else:
    for sample in view['frames']:
     camera=sample['camera'];layout=camera['layout'];nodes,edges=required(c,sample['frame']);visible={n['id']:n for n in layout['nodes']};visible_edges={e['id'] for e in layout['edges']};pose=camera['pose'];z=pose['zoom']
     if not nodes<=set(visible) or not edges<=visible_edges:issues.add('relationship_missing')
     for nid in nodes & set(visible):
      n=visible[nid]
      if n['x']*z+pose['x']<-.01 or n['y']*z+pose['y']<-.01 or (n['x']+n['width'])*z+pose['x']>camera['view']['width']+.01 or (n['y']+n['height'])*z+pose['y']>500+.01:issues.add('graph_clipped')
     sizes.append(layout['font_size']*z*scale)
     labeled=any(e.get('label') and e['id'] in edges for e in c['props']['edges'])
     if sizes[-1]<29.99 or (labeled and layout['edge_font_size']*z*scale<23.99):issues.add('small_text')
   reports.append({'id':c['id'],'family':c['family'],'scale':scale,'mode':mode,'issues':sorted(issues),'minimum_node_px':min(sizes),'duration_frames':c['duration_frames']})
 summary={str(scale):{'cases':len(cases),'failed':sum(bool(r['issues']) for r in reports if r['scale']==scale),'index':sum(r['mode']=='index' for r in reports if r['scale']==scale)} for scale in [1,.9]}
 (out/'results.json').write_text(json.dumps({'split':split,'sha256':hashlib.sha256(raw).hexdigest(),'summary':summary,'cases':reports},ensure_ascii=False,indent=2)+'\n');print(json.dumps(summary))
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--split',default='development');p.add_argument('--output',type=Path,required=True);p.add_argument('--bundle',type=Path,required=True);p.add_argument('--cases',type=Path);a=p.parse_args();run(a.split,a.output.resolve(),a.bundle.resolve(),a.cases)
