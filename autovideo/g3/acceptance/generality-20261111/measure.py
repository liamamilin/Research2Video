"""Evaluate the frozen cohort with output geometry, event coverage and typography measurements."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from PIL import ImageFont
ROOT=Path(__file__).resolve().parents[3];sys.path.insert(0,str(ROOT))
from g2 import shot_director,visual_language
from g2.process_diagram import wrap_label,FONT

HERE=Path(__file__).parent

def prepared(case):
    family=case['family'];length=case.get('duration_frames',480)
    if family=='flow':
        count=len(case['props']['nodes']);frames=[round(40+i*(length-100)/max(1,count-1)) for i in range(count)]
        scene={'id':case['id'],'component':'flow_diagram','start_frame':0,'end_frame':length,'props':case['props'],
            'beats':[{'id':f'b{i}','action':'reveal','target_id':f'node:{n["id"]}','frame':frames[i]} for i,n in enumerate(case['props']['nodes'])]}
        scene['props']={**scene['props'],'shot_plan':shot_director.camera_plan(scene,case['profile'])}
        return {'id':case['id'],'family':family,'scene':scene,'frames':frames}
    if family=='motion':
        frames=[round((45+i*case['gap'])/case['speed']) for i in range(case['count'])]
        scene={'id':case['id'],'start_frame':0,'end_frame':length,'props':{'shot_plan':{'version':1,'profile':case['profile'],
            'cues':[{'beat_id':f'b{i}','targets':[f'node:n{i}']} for i in range(case['count'])]}},
            'beats':[{'id':f'b{i}','frame':f,'target_id':f'node:n{i}'} for i,f in enumerate(frames)]}
        return {'id':case['id'],'family':family,'scene':scene,'frames':frames}
    if family=='material':
        image={'focus':{'width':case['width'],'height':case['height'],'regions':[case['region']]}}
        scene={'id':case['id'],'start_frame':0,'end_frame':length,'props':{'shot_plan':{'version':1,'profile':'balanced',
            'cues':[{'beat_id':'focus','targets':['region:0:0']}]}},'beats':[{'id':'focus','frame':90}]}
        return {'id':case['id'],'family':family,'scene':scene,'image':image,'frames':[90,120],
            'view':{'width':800 if case['pair'] else 1600,'height':500}}

def inside(rect,pose,width,height):
    z,x,y=pose['zoom'],pose['x'],pose['y'];e=.01
    return rect['x']*z+x>=-e and rect['y']*z+y>=-e and (rect['x']+rect['width'])*z+x<=width+e and (rect['y']+rect['height'])*z+y<=height+e

def run(split,output,bundle,current=False):
    raw=(HERE/f'{split}.json').read_bytes();manifest=json.loads((HERE/(f'{split}-manifest.json' if (HERE/f'{split}-manifest.json').exists() else 'manifest.json')).read_text());assert hashlib.sha256(raw).hexdigest()==manifest['files'][f'{split}.json']['sha256']
    cases=json.loads(raw);output.mkdir(parents=True,exist_ok=False)
    inputs=[prepared(c) for c in cases if c['family'] in {'flow','motion','material'}]
    if current:
        for entry in inputs:
            if entry['family']=='material':
                original=next(c for c in cases if c['id']==entry['id'])
                entry['view']={'width':1031 if original['pair'] else 1618,'height':400 if original['pair'] else 488}
        from types import SimpleNamespace
        from g2.flow_layouts import attach
        pipeline=SimpleNamespace(ARTIFACTS=output,PROJECT=ROOT,NODE='node',
            write_json=lambda path,data:path.write_text(json.dumps(data,ensure_ascii=False)),
            _run_fixed_command=lambda name,args,cwd,timeout:subprocess.check_output([str(a) for a in args],cwd=cwd,timeout=timeout,env={**os.environ,'NODE_PATH':str(ROOT/'g0/vendor/remotion-audiogram/node_modules')}))
        attach({'scenes':[p['scene'] for p in inputs if p['family']=='flow']},pipeline)
        for c in cases:
            if c['family']=='comparison':
                chars=set(''.join(c['props'][side]['label']+''.join(c['props'][side]['items']) for side in ['left','right']))
                font=ImageFont.truetype(FONT,100)
                inputs.append({**c,'widths':{char:font.getlength(char)/100 for char in chars}})
    (output/'input.json').write_text(json.dumps(inputs,ensure_ascii=False))
    results=json.loads(subprocess.check_output(['node',str(bundle),str(output/'input.json')]))
    results={r['id']:r for r in results};reports=[]
    for c in cases:
        family=c['family'];report={'id':c['id'],'family':family,'issues':[]}
        if family=='flow':
            result=results[c['id']];nodes={f'node:{n["id"]}':n for n in c['props']['layout']['nodes']};sizes=[]
            for camera in result['cameras']:
                # Required targets come from the spoken shot; other branches are contextual.
                targets=camera['shot']['targets'];pose=camera['pose'];view=camera.get('view',{'width':1560,'height':500})
                layout=camera.get('layout',c['props']['layout']);nodes={f'node:{n["id"]}':n for n in layout['nodes']}
                sizes.append(layout['font_size']*pose['zoom'])
                if any(not inside(nodes[t],pose,view['width'],view['height']) for t in targets):report['issues'].append('focus_clipped')
            report['minimum_focus_font']=min(sizes)
            report['readable_anchors']=sum(size>=30-.01 for size in sizes)
            report['total_anchors']=len(sizes)
            if current:
                graph=c['props'];ids=[n['id'] for n in graph['nodes']]
                for index,node in enumerate(ids):
                    incoming={e['from'] for e in graph['edges'] if e['to']==node and e['from'] in ids[:index]}
                    outgoing={e['to'] for e in graph['edges'] if e['from']==node and e['to'] not in ids[:index]}
                    required={f'node:{n}' for n in {node}|(incoming or outgoing)}
                    cam=result['cameras'][index]
                    visible={f'node:{n["id"]}' for n in cam['layout']['nodes']}
                    if not required<=visible:report['issues'].append('required_relationship_missing')
            if min(sizes)<30-.01:report['issues'].append('focus_text_below_30px')
            report['node_count']=len(c['props']['nodes'])
            report['detail_views']=sum(cam.get('detail',False) for cam in result['cameras'])
        elif family=='material':
            result=results[c['id']];view=next(p['view'] for p in inputs if p['id']==c['id'])
            if any(not inside(result['rects']['region:0:0'],r['pose'],view['width'],view['height']) for r in result['cameras']):report['issues'].append('region_clipped')
        elif family=='motion':
            result=results[c['id']];seen={t for shot in result['shots'] for t in shot['targets']};expected={f'node:n{i}' for i in range(c['count'])}
            report['missing_targets']=sorted(expected-seen)
            if expected-seen and len(result['shots'])>1:report['issues'].append('spoken_target_dropped')
            report['overview_only']=len(result['shots'])==1
        elif family=='timing':
            texts=c['segments'];rows={f'n{i}':{'id':f'n{i}','text':t,'start_ms':round(i*5000/c['speed']),'end_ms':round((i+1)*5000/c['speed'])} for i,t in enumerate(texts)}
            scene={'id':c['id'],'narration_segment_ids':list(rows),'props':{'visuals':[{'target':'line:0','reveal_phrase':c['introduction'],'phrase':c['emphasis']}]}}
            timing=[];beats=visual_language.action_beats(scene,rows,timing)
            if not beats or beats[0]['trigger']['segment_id']!='n1' or timing[0]['character_index']!=texts[1].index(c['emphasis']):report['issues'].append('repeated_emphasis_lost_or_early')
        elif current:
            fit=results[c['id']]['fit'];report['measured_height']=max(fit['sides'][side]['height'] for side in ['left','right']);report['body_font']=fit['body']
            if not fit['fits']:report['issues'].append('comparison_text_exceeds_panel')
        else:
            # Existing renderer's layout; use actual system font metrics, not a character-count proxy.
            heights=[]
            for side in ['left','right']:
                data=c['props'][side];size=30 if len(''.join(data['items']))>130 else 34;labelsize=34 if len(data['label'])>20 else 42
                label=len(wrap_label(data['label'],ImageFont.truetype(FONT,labelsize),610))*labelsize*1.4
                body=sum(len(wrap_label(t,ImageFont.truetype(FONT,size),676))*size*1.5 for t in data['items'])
                heights.append(68+label+30+body+20*(len(data['items'])-1))
            report['estimated_height']=max(heights)
            if max(heights)>500:report['issues'].append('comparison_text_exceeds_panel')
        report['issues']=sorted(set(report['issues']));reports.append(report)
    summary={f:{'cases':sum(r['family']==f for r in reports),'with_issues':sum(r['family']==f and bool(r['issues']) for r in reports)} for f in sorted({r['family'] for r in reports})}
    (output/'results.json').write_text(json.dumps({'split':split,'corpus_sha256':hashlib.sha256(raw).hexdigest(),'summary':summary,'cases':reports},ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(summary,ensure_ascii=False))

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--split',default='development');parser.add_argument('--output',type=Path,required=True);parser.add_argument('--bundle',type=Path,required=True)
    parser.add_argument('--current',action='store_true')
    args=parser.parse_args();run(args.split,args.output.resolve(),args.bundle.resolve(),args.current)
