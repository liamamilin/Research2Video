"""Retry an isolated failed sample through the normal API; preserve the first attempt."""
import argparse,json,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[3];sys.path.insert(0,str(ROOT))
from fastapi.testclient import TestClient
from g3.app import create_app
from g3.config import Settings
parser=argparse.ArgumentParser();parser.add_argument('--source',type=Path,required=True);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
source,out=args.source.resolve(),args.output.resolve();out.mkdir(parents=True,exist_ok=False)
location=json.loads((source/'location.json').read_text());data=Path(location['engine_data']);job_id=location['job_id']
(out/'location.json').write_text(json.dumps(location,indent=2))
with TestClient(create_app(Settings(data_dir=data,runner_timeout_seconds=1200),start_worker=True)) as engine:
 response=engine.post(f'/api/jobs/{job_id}/retry');assert response.status_code==202,response.text
 print(response.text,flush=True)
 deadline=time.monotonic()+1200;previous=None
 while time.monotonic()<deadline:
  job=engine.get(f'/api/jobs/{job_id}').json();state=(job['status'],job.get('active_stage'))
  if state!=previous:print(state,flush=True);previous=state
  if job['status'] in {'COMPLETED','FAILED','CANCELLED','NEEDS_INPUT'}:break
  time.sleep(1)
 (out/'job.json').write_text(json.dumps(job,ensure_ascii=False,indent=2))
 assert job['status']=='COMPLETED',job
 for name in ['video.mp4','content.mp4','video-ir.json','qa.json','package-qa.json','execution-log.txt']:
  response=engine.get(f'/api/jobs/{job_id}/artifacts/{name}');assert response.status_code==200,name;(out/name).write_bytes(response.content)
 attempt=max((data/'jobs'/job_id/'attempts').iterdir(),key=lambda p:int(p.name))/'g2-run'
 direction=json.loads((attempt/'artifacts/story-direction.json').read_text())
 (out/'story-direction.json').write_text(json.dumps(direction,ensure_ascii=False,indent=2))
 assert any(s.get('recap') for s in direction['scenes'])
 print(json.dumps({'job_id':job_id,'attempt':job['attempt_count'],'components':[s['component'] for s in direction['scenes']]},ensure_ascii=False),flush=True)
