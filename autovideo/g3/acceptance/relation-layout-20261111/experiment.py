"""Bounded route A experiment: four predeclared wrapping widths, no production edits."""
import hashlib,json,os,subprocess,sys
from pathlib import Path
from PIL import ImageFont
ROOT=Path(__file__).resolve().parents[3];sys.path.insert(0,str(ROOT));HERE=Path(__file__).parent
from g2 import process_diagram
from g2.technical_text import display_width
out=ROOT/'g3/data/validation/relation-layout-20261111/width-experiment';out.mkdir(exist_ok=False)
cases=json.loads((HERE/'development.json').read_text());manifest=json.loads((HERE/'manifest.json').read_text());assert hashlib.sha256((HERE/'development.json').read_bytes()).hexdigest()==manifest['files']['development.json']['sha256']
requests=[];font=ImageFont.truetype(process_diagram.FONT,36)
for c in cases:
 for width in manifest['candidate_wrap_widths']:
  req=process_diagram.layout_request({'scenes':[{'id':c['id'],**c['props']} ]})['scenes'][0];req['id']=f'{c["id"]}-w{width}'
  icons={v['target'] for v in c['props']['visuals']}
  for node in req['nodes']:
   label=next(n['label'] for n in c['props']['nodes'] if n['id']==node['id']);lines=process_diagram.wrap_label(label,font,width)
   node.update(label_lines=lines,width=max(210,max(display_width(font,line) for line in lines)+60),height=max(160,len(lines)*46+110) if f'node:{node["id"]}' in icons else max(100,len(lines)*46+40))
  requests.append(req)
request=out/'requests.json';request.write_text(json.dumps({'scenes':requests},ensure_ascii=False));env={**os.environ,'NODE_PATH':str(ROOT/'g0/vendor/remotion-audiogram/node_modules')}
raw=json.loads(subprocess.check_output(['node',str(process_diagram.LAYOUT_SCRIPT),str(request)],env=env));(out/'layouts.json').write_text(json.dumps(raw,ensure_ascii=False))
records=[]
for c in cases:
 choices=[r for r in raw['scenes'] if r['id'].startswith(c['id']+'-w')];best=max(choices,key=lambda r:r['layout']['font_size']);layout=best['layout']
 # Compiler already fits all routed edges into the target canvas. Camera cannot
 # improve the limiting dimension beyond that canvas while retaining the graph.
 records.append({'id':c['id'],'family':c['family'],'best_width':best['id'].split('-w')[-1],'node_px':layout['font_size'],'edge_px':layout['edge_font_size'],'meets':layout['font_size']>=30 and layout['edge_font_size']>=24})
result={'route':'bounded whole-graph wrap search','candidates':manifest['candidate_wrap_widths'],'passed':sum(r['meets'] for r in records),'cases':len(records),'records':records,'decision_threshold':.95,'limitation':'Whole graph experiment, not a proof that no other graph layout could work.'};(out/'results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));print(json.dumps({k:v for k,v in result.items() if k!='records'}))
