"""Isolated real API/worker/TTS/renderer acceptance; never submits an external model call."""
from __future__ import annotations
import json
from pathlib import Path
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from fastapi.testclient import TestClient
from g3.app import create_app
from g3.config import Settings
from g3.worker import G2ProcessRunner

out = ROOT / 'g3/data/production-plan-fast-cards-20261023'
out.mkdir(exist_ok=True)
settings = Settings(data_dir=out)
ready = lambda _: {'ready': True, 'checks': {'diagram_layout': True, 'draw_things': False}}
with TestClient(create_app(settings, start_worker=False, preflight=ready)) as client:
    short = {'title': '缓存的用途与限制', 'input_mode': 'narration', 'duration_mode': 'content',
             'chapters': [{'title': '用途与限制', 'paragraphs': ['缓存会暂时保存已经读取的数据，再次访问时可以减少重复读取。缓存可能过期，使用时仍应检查原始数据的更新条件。']}],
             'production_recipe': 'science_explainer', 'production_mode': 'fast', 'voice_id': 'zf_xiaoxiao',
             'packaging': {'label': '知识说明', 'sound': {'enabled': False}, 'intro': {'seconds': .5}, 'outro': {'seconds': .5}},
             'idempotency_key': 'fast-integration-short-v2'}
    old = json.loads((ROOT / 'g3/data/jobs/875a6e0c-3121-478a-89d1-27fa836324fe/input/input.json').read_text())
    full = {k: old[k] for k in ('title', 'input_mode', 'duration_mode', 'chapters', 'voice_id', 'sources', 'packaging') if k in old}
    if 'packaging' not in full and old.get('packaging_resolved'):
        full['packaging'] = old['packaging_resolved']['selection']
    full.update(production_mode='fast', idempotency_key='fast-integration-topic-digest-v2',
                content_notices=[{'chapter_index': 2, 'message': '本章节有内容待核实'}],
                production_context={'content_recipe': {'id': 'research:topic_digest', 'settings': {}},
                                    'matrix': {'id': 'acceptance:technology', 'settings': {'packaging': {'label': '专题速览'}}}})
    records = []
    for name, body in [('short-fresh-tts', short), ('topic-digest-fresh-tts', full)]:
        start = time.monotonic()
        submitted = client.post('/api/jobs', json=body)
        assert submitted.status_code == 202, submitted.text
        jid = submitted.json()['job_id']
        assert client.post('/api/jobs', json=body).json()['job_id'] == jid
        store = client.app.state.store
        job = store.get_job(jid)
        if job['status'] == 'QUEUED':
            claim = store.claim_next()
            assert claim['job_id'] == jid
            print(f'START {name} {jid}', flush=True)
            G2ProcessRunner(settings, store).execute(claim, threading.Event())
        job = store.get_job(jid)
        record = {'name': name, 'job_id': jid, 'wall_seconds': round(time.monotonic() - start, 3), 'job': job}
        records.append(record)
        (out / 'acceptance.json').write_text(json.dumps(records, ensure_ascii=False, indent=2))
        print(f'END {name}: {job["status"]} {job.get("error_code")} {job.get("error_message")}', flush=True)
        assert job['status'] == 'COMPLETED', {'job_id': jid, 'code': job.get('error_code'), 'message': job.get('error_message')}
        artifacts = client.get(f'/api/jobs/{jid}/artifacts').json()['items']
        assert {'video.mp4','qa.json','production-plan.json','production-execution.json','fast-card-plan.json'} <= {a['name'] for a in artifacts}
        record['artifacts'] = artifacts
        (out / 'acceptance.json').write_text(json.dumps(records, ensure_ascii=False, indent=2))
