"""Re-render the automatically planned sample with current visuals and unchanged audio."""
import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
from fastapi.testclient import TestClient
from g3.app import create_app
from g3.config import Settings

parser=argparse.ArgumentParser()
parser.add_argument('--source',type=Path,required=True)
parser.add_argument('--output',type=Path,required=True)
args=parser.parse_args()
source=args.source.resolve();out=args.output.resolve();out.mkdir(parents=True,exist_ok=False)
location=json.loads((source/'location.json').read_text());data=Path(location['engine_data']);parent=location['job_id']
with TestClient(create_app(Settings(data_dir=data,runner_timeout_seconds=900),start_worker=True)) as engine:
    response=engine.post(f'/api/jobs/{parent}/media-revisions',json={'video_speed':1.0})
    assert response.status_code==202,response.text
    jid=response.json()['job_id'];print(json.dumps({'job_id':jid,'parent_job_id':parent}),flush=True)
    (out/'location.json').write_text(json.dumps({'job_id':jid,'engine_data':str(data),'parent_job_id':parent},indent=2))
    deadline=time.monotonic()+900;previous=None
    while time.monotonic()<deadline:
        job=engine.get(f'/api/jobs/{jid}').json();current=(job['status'],job.get('active_stage'))
        if current!=previous:print(json.dumps({'status':current[0],'stage':current[1]}),flush=True);previous=current
        if job['status'] in {'COMPLETED','FAILED','CANCELLED','NEEDS_INPUT'}:break
        time.sleep(1)
    (out/'job.json').write_text(json.dumps(job,ensure_ascii=False,indent=2))
    if job['status']!='COMPLETED':raise RuntimeError({k:job.get(k) for k in ('status','error_code','error_message')})
    for name in ('video.mp4','content.mp4','video-ir.json','qa.json','package-qa.json','execution-log.txt'):
        result=engine.get(f'/api/jobs/{jid}/artifacts/{name}');assert result.status_code==200,name
        (out/name).write_bytes(result.content)
    before=json.loads((source/'video-ir.json').read_text());after=json.loads((out/'video-ir.json').read_text())
    voice=lambda ir:next(a['sha256'] for a in ir['assets'] if a['id']==ir['narration']['audio_asset_id'])
    assert voice(before)==voice(after)
    assert [s['props'] for s in before['scenes']]==[s['props'] for s in after['scenes']]
    result={'job_id':jid,'parent_job_id':parent,'engine_data':str(data),'voice_unchanged':True,'screen_data_unchanged':True,
            'original_real_model_plan':str(source),'components':[s['component'] for s in after['scenes']],
            'media':json.loads((out/'qa.json').read_text())['video']}
    (out/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps(result,ensure_ascii=False),flush=True)
