import hashlib
import json
from pathlib import Path
import sys
import threading
import time

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

from g2.music_engine import GenerationError
from g3.app import create_app
from g3.config import Settings
from g3.music_generation import MusicGeneration
from g3.tests.test_g3_package import READY, body, saved_input


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr('g3.app.music_readiness',lambda:{'ready':True,'message':'isolated model availability'})
    with TestClient(create_app(Settings(data_dir=tmp_path),start_worker=False,preflight=READY)) as c:
        yield c


@pytest.mark.parametrize('extra',[{'duration_seconds':9},{'duration_seconds':31},{'duration_seconds':True},
    {'bpm':300},{'seed':-1},{'style':'unknown'},{'lyrics':'words'},{'path':'../music.wav'},
    {'name':'x'},{'description':'x'*221},{'name':'a\x00b'}])
def test_invalid_music_requests_create_nothing(client,extra):
    assert client.post('/api/music-jobs',json={'name':'测试音乐',**extra}).status_code==422
    assert client.get('/api/music-jobs').json()['items']==[]


def test_idempotency_cancel_retry_and_unavailable_engine(client,monkeypatch):
    payload={'name':'重试音乐','idempotency_key':'same-request'}
    first=client.post('/api/music-jobs',json=payload);assert first.status_code==202
    job=first.json();again=client.post('/api/music-jobs',json=payload).json()
    assert again['id']==job['id'] and again['settings']['seed']==job['settings']['seed']
    assert client.post('/api/music-jobs',json={**payload,'style':'piano'}).status_code==409
    assert client.post(f"/api/music-jobs/{job['id']}/cancel").json()['status']=='CANCELLED'
    retried=client.post(f"/api/music-jobs/{job['id']}/retry").json()
    assert retried['id']!=job['id'] and retried['parent_id']==job['id']
    assert retried['settings']['seed']==job['settings']['seed']
    assert client.post(f"/api/music-jobs/{retried['id']}/retry").status_code==409
    assert client.get('/api/music-jobs/not-a-job').status_code==404
    monkeypatch.setattr('g3.app.music_readiness',lambda:{'ready':False,'message':'模型未连接'})
    assert client.post('/api/music-jobs',json={'name':'缺模型'}).status_code==503
    assert client.post('/api/music-assets',files={'file':('bad.mp3',b'invalid')}).status_code==422


def result_file(service,job,duration=24):
    work=service.jobs.work(job['id']);(work/'output').mkdir(parents=True)
    target=work/'output/generated.wav';t=np.arange(duration*48000)/48000
    sf.write(target,np.repeat((.1*np.sin(t*2*np.pi*220))[:,None],2,axis=1),48000,subtype='FLOAT')
    report={'status':'PASS','file':'output/generated.wav','sha256':hashlib.sha256(target.read_bytes()).hexdigest(),
            'engine':'isolated test fixture; not AI','seed':job['settings']['seed'],'generation_calls':0}
    (work/'result.json').write_text(json.dumps(report));return work


def test_generated_audio_enters_same_library_and_recipe_freeze(client):
    service=client.app.state.music_generation
    response=client.post('/api/music-jobs',json={'name':'测试生成音乐','style':'piano','seed':10})
    job=service.jobs.claim();work=result_file(service,job)
    service.publish(job,work)
    saved=client.get('/api/music-jobs/'+job['id']).json()
    assert saved['status']=='COMPLETED' and saved['asset_id']==job['id']
    asset=next(a for a in client.get('/api/music-assets').json()['items'] if a['id']==job['id'])
    assert asset['kind']=='generated' and asset['generation']['request']['seed']==10
    assert client.get(asset['preview_url'],headers={'Range':'bytes=0-31'}).status_code==206
    child=client.post('/api/jobs',json=body(production_recipe='ai_news',background_music={'enabled':True,'asset_id':job['id']}))
    assert child.status_code==202,child.text
    snapshot=saved_input(client,child.json()['job_id'])
    assert snapshot['background_music_resolved']['asset']['kind']=='generated'
    assert snapshot['production_recipe_resolved']['music']['mode']=='background'
    assert snapshot['production_recipe_resolved']['music']['background_music_generated'] is True
    assert snapshot['production_plan']['music']['background_music_generated'] is True
    assert snapshot['background_music_resolved']['music_generated'] is True
    assert snapshot['background_music_resolved']['asset']['generation']['generation_calls']==0


@pytest.mark.parametrize('bad',['duration','hash','path','silence'])
def test_invalid_generated_outputs_are_never_listed(client,bad):
    service=client.app.state.music_generation
    job=client.post('/api/music-jobs',json={'name':'拒绝错误输出'}).json()
    work=result_file(service,job,duration=10 if bad=='duration' else 24)
    path=work/'result.json';report=json.loads(path.read_text())
    if bad=='hash':report['sha256']='0'*64
    if bad=='path':report['file']='../outside.wav'
    if bad=='silence':
        target=work/'output/generated.wav';sf.write(target,np.zeros((24*48000,2)),48000,subtype='FLOAT');report['sha256']=hashlib.sha256(target.read_bytes()).hexdigest()
    path.write_text(json.dumps(report))
    with pytest.raises((ValueError,OSError)):
        service.publish(job,work)
    assert not any(a['id']==job['id'] for a in client.get('/api/music-assets').json()['items'])


def test_restart_recovers_published_asset_without_repeating_generation(client):
    service=client.app.state.music_generation
    job=client.post('/api/music-jobs',json={'name':'中断恢复'}).json();service.jobs.claim()
    service.publish(job,result_file(service,job))
    service.jobs.update(job['id'],status='RUNNING',stage='CHECKING')
    service.recover()
    assert service.jobs.get(job['id'])['status']=='COMPLETED'
    second=client.post('/api/music-jobs',json={'name':'未完成中断'}).json();service.jobs.claim();service.recover()
    assert service.jobs.get(second['id'])['error_code']=='MUSIC_INTERRUPTED'


def test_cancel_before_publication_keeps_output_but_does_not_register_asset(client):
    service=client.app.state.music_generation
    job=client.post('/api/music-jobs',json={'name':'发布前取消'}).json();service.jobs.claim()
    work=result_file(service,job)
    assert client.post('/api/music-jobs/'+job['id']+'/cancel').status_code==200
    service.publish(job,work)
    assert service.jobs.get(job['id'])['status']=='CANCELLED'
    assert (work/'result.json').is_file()
    assert not any(a['id']==job['id'] for a in client.get('/api/music-assets').json()['items'])


def test_final_asset_check_cannot_race_with_cancel(client):
    service=client.app.state.music_generation
    job=client.post('/api/music-jobs',json={'name':'正在保存'}).json();service.jobs.claim()
    assert service.jobs.start_publish(job['id'])
    response=client.post('/api/music-jobs/'+job['id']+'/cancel')
    assert response.status_code==409 and service.jobs.get(job['id'])['cancel_requested'] is False


def test_running_cancel_stops_subprocess_and_preserves_log(client,tmp_path,monkeypatch):
    runtime=tmp_path/'fake-runtime';(runtime/'venv/bin').mkdir(parents=True)
    (runtime/'venv/bin/python').symlink_to(sys.executable)
    script=tmp_path/'fake_engine.py';script.write_text("import time\nprint('MUSIC_STAGE GENERATING',flush=True)\ntime.sleep(60)\n")
    monkeypatch.setattr('g3.music_generation.SCRIPT',script)
    monkeypatch.setattr('g3.music_generation.model_root',lambda:runtime)
    monkeypatch.setattr('g3.music_generation.readiness',lambda:{'ready':True})
    service=client.app.state.music_generation;job=client.post('/api/music-jobs',json={'name':'取消运行'}).json()
    thread=threading.Thread(target=service.run_one,args=(threading.Event(),));thread.start()
    for _ in range(100):
        if service.jobs.get(job['id'])['stage']=='GENERATING':break
        time.sleep(.02)
    else:pytest.fail('isolated child did not start')
    assert client.post('/api/music-jobs/'+job['id']+'/cancel').status_code==200
    thread.join(timeout=5);assert not thread.is_alive()
    assert service.jobs.get(job['id'])['status']=='CANCELLED'
    assert (service.jobs.work(job['id'])/'generation.log').exists()


def test_music_stem_download_uses_registered_file_and_range(client):
    response=client.post('/api/jobs',json=body());store=client.app.state.store
    job=store.claim_next();attempt=store.data_dir/job['workspace_rel_path'];attempt.mkdir(parents=True)
    path=attempt/'music-bed.wav';sf.write(path,np.ones((4800,2))*.01,48000)
    raw=path.read_bytes();store.mark_completed(job['job_id'],job['attempt_no'],[{'name':'music-bed.wav',
        'relative_path':'music-bed.wav','content_type':'audio/wav','sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw)}])
    url=f"/api/jobs/{job['job_id']}/artifacts/music-bed.wav"
    result=client.get(url,headers={'Range':'bytes=0-31'})
    assert result.status_code==206 and result.content==raw[:32] and result.headers['content-type']=='audio/wav'
    path.write_bytes(b'corrupted')
    assert client.get(url).status_code==409
