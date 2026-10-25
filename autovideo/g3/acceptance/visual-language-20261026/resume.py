"""Retry the isolated failed render using the original request, model plan and voice cache."""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from fastapi.testclient import TestClient
from g3.app import create_app
from g3.config import Settings

parser = argparse.ArgumentParser()
parser.add_argument('--source', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
source, out = args.source.resolve(), args.output.resolve()
out.mkdir(parents=True, exist_ok=False)
location = json.loads((source/'location.json').read_text())
data, job_id = Path(location['engine_data']), location['job_id']
assert data.is_relative_to(ROOT/'g3/data/acceptance-visual-language')
with TestClient(create_app(Settings(data_dir=data, runner_timeout_seconds=900), start_worker=True)) as engine:
    response = engine.post(f'/api/jobs/{job_id}/retry')
    assert response.status_code == 202, response.text
    print(response.json(), flush=True)
    deadline, previous = time.monotonic()+900, None
    while time.monotonic()<deadline:
        job = engine.get(f'/api/jobs/{job_id}').json()
        current = (job['status'], job.get('active_stage'))
        if current != previous:
            print(json.dumps({'status': current[0], 'stage': current[1]}, ensure_ascii=False), flush=True)
            previous = current
        if job['status'] in {'COMPLETED','FAILED','CANCELLED','NEEDS_INPUT'}:
            break
        time.sleep(1)
    (out/'job.json').write_text(json.dumps(job,ensure_ascii=False,indent=2))
    (out/'location.json').write_text(json.dumps(location,ensure_ascii=False,indent=2))
    if job['status'] != 'COMPLETED':
        raise RuntimeError(str({k:job.get(k) for k in ('status','error_code','error_message')}))
    for name in ('video.mp4','content.mp4','video-ir.json','qa.json','package-qa.json','execution-log.txt'):
        result=engine.get(f'/api/jobs/{job_id}/artifacts/{name}')
        assert result.status_code==200,(name,result.text[:100])
        (out/name).write_bytes(result.content)
    ir=json.loads((out/'video-ir.json').read_text())
    result={'job_id':job_id,'attempt':job.get('current_attempt'), 'engine_data':str(data),
            'components':[s['component'] for s in ir['scenes']],
            'visuals':[{'scene_id':s['id'],'component':s['component'],'objects':s['props'].get('visuals',[])} for s in ir['scenes']],
            'media':json.loads((out/'qa.json').read_text())['video']}
    (out/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps(result,ensure_ascii=False),flush=True)
