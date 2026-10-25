"""Extract the actual object, action and comparison frames from an isolated completed video."""
import argparse
import json
import subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parents[3]
parser=argparse.ArgumentParser()
parser.add_argument('--source',type=Path,required=True)
args=parser.parse_args()
source=args.source.resolve()
loc=json.loads((source/'location.json').read_text())
job=Path(loc['engine_data'])/'jobs'/loc['job_id']
attempt=max((job/'attempts').iterdir(),key=lambda p:int(p.name))/'g2-run'
ir=json.loads((attempt/'artifacts/compiled-timeline.json').read_text())
out=source/'frames';out.mkdir(exist_ok=False)
frames=[]
for scene in ir['scenes']:
    targets={'flow_diagram':['edge:e1','node:result'],'concept_reveal':['line:0','line:1','line:2'],
             'comparison':['side:right'],'price_change':['line:2']}.get(scene['component'],[])
    for target in targets:
        matches=[b for b in scene['beats'] if b['target_id']==target]
        if not matches:continue
        beat=next((b for b in matches if '_action_' in b['id']),matches[0])
        at=min(beat['frame']/30+1.1,(scene['end_frame']-2)/30)
        name=scene['component']+'-'+target.replace(':','-')+'.png'
        subprocess.run([str(ROOT/'g0/darwin_arm64/ffmpeg'),'-v','error','-ss',str(at),'-i',str(source/'content.mp4'),
                        '-frames:v','1','-update','1',str(out/name)],check=True)
        frames.append({'scene':scene['id'],'target':target,'seconds':at,'file':name})
(source/'frames.json').write_text(json.dumps(frames,ensure_ascii=False,indent=2))
print(json.dumps(frames,ensure_ascii=False),flush=True)
