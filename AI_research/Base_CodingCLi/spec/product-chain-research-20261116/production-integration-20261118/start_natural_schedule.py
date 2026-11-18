"""Create a research-only natural-clock rehearsal through the real management API."""
from pathlib import Path
import json
import httpx

ROOT=Path(__file__).resolve().parents[3]/'state/validation/chain-production-integration-20261118/native'
with httpx.Client(base_url='http://127.0.0.1:9254',timeout=180,trust_env=False) as client:
    client.post('/api/auth/login',json={'username':'native-admin','password':'native-chain-admin-2026'}).raise_for_status()
    client.headers['x-csrf-token']=client.cookies.get('ai_research_csrf','')
    body={'key':'native_schedule','name':'自然周期 · 正式接入预演',
          'prompt':'面向普通观众整理 OpenAI 最近一周用 AI 推进科学的具体进展、应用方法与案例。',
          'sources':['source:cg_openai_news'],'output_modes':['individual'],
          'schedule':'*/5 * * * *','window_days':7,'voice_id':'zf_xiaoxiao'}
    response=client.post('/api/chains',json=body)
    response.raise_for_status()
    chain=response.json()
    response=client.post('/api/chains/native_schedule/toggle',json={
        'revision':chain['revision'],'scope':'outputs','enabled':False})
    response.raise_for_status()
    chain=response.json()
    assert chain['schedules'][0]['enabled'] and not any(o['auto'] for o in chain['outputs_detail'])
    (ROOT/'evidence/natural-schedule-created.json').write_text(json.dumps(chain,ensure_ascii=False,indent=2))
    print('Real native dispatcher plan created, research only:',chain['schedules'][0]['id'],chain['schedules'][0]['schedule'],flush=True)
