from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import hashlib,json,subprocess,wave

import numpy as np
import pytest
from fastapi.testclient import TestClient
from g1.video_ir import validate_video_ir
from g2.media_settings import retime_ir,tempo_filter
from g2.scene_fields import apply_changes,editable_fields
from g3.app import create_app
from g3.config import Settings

ROOT=Path(__file__).resolve().parents[2]
SOURCE='会议记录要说明已经确定的事项，写清负责人和下一步动作。未达成共识的信息标为待确认，不猜测补齐，未确定的日期保留为待定。'
READY=lambda _:{'ready':True,'checks':{'diagram_layout':True},'blockers':[]}


def ir_for(root):
    (root/'assets').mkdir(parents=True)
    raw=(np.sin(np.arange(120000)*2*np.pi*400/24000)*10000).astype('<i2').tobytes()
    path=root/'assets/communications_noise.wav'
    with wave.open(str(path),'wb') as f:f.setnchannels(1);f.setsampwidth(2);f.setframerate(24000);f.writeframes(raw)
    ir={'schema_version':'0.2','video':{'title':'测试场景','width':1920,'height':1080,'fps':30,'duration_ms':5000},'source':{'language':'zh-CN','original_text':SOURCE,'text_sha256':hashlib.sha256(SOURCE.encode()).hexdigest()},'style':{'preset':'minimal_light','seed':1},'presentation':{'preset':'information_motion'},'assets':[{'id':'voice','type':'audio','path':'assets/communications_noise.wav','origin':'test tone','sha256':hashlib.sha256(path.read_bytes()).hexdigest()}],'narration':{'text':'测试旁白。','audio_asset_id':'voice','segments':[{'id':'n1','text':'测试旁白。','start_ms':200,'end_ms':4500}],'alignment':{'method':'provider','granularity':'phrase'}},'audio_mix':{'narration_gain_db':0},'scenes':[{'id':'s1','start_ms':0,'duration_ms':5000,'component':'title_card','props':{'title':'测试标题','subtitle':'测试副标题'},'purpose':'测试场景','visual_intent':{'viewer_understands':'测试','before':'未出现','after':'已出现'},'narration_segment_ids':['n1'],'beats':[{'id':'b1','action':'reveal','target_id':'title','trigger':{'segment_id':'n1','boundary':'start','offset_ms':0}},{'id':'b2','action':'reveal','target_id':'subtitle','trigger':{'segment_id':'n1','boundary':'start','offset_ms':300}}]}]}
    return ir


def pipe(root):
    artifacts=root/'artifacts';artifacts.mkdir()
    def run(label,args,cwd):return subprocess.check_output(list(map(str,args)),cwd=cwd,text=True)
    return SimpleNamespace(FIXTURE_ROOT=root,ARTIFACTS=artifacts,FFMPEG=ROOT/'g0/darwin_arm64/ffmpeg',PROJECT=ROOT,SAMPLE_RATE=24000,FPS=30,_run_fixed_command=run,digest=lambda p:hashlib.sha256(p.read_bytes()).hexdigest(),write_json=lambda path,v:path.write_text(json.dumps(v)))


@pytest.mark.parametrize('speed',[.5,1,1.25,1.5,2])
def test_audio_speed_preserves_pitch_and_sync_and_can_restore(tmp_path,speed):
    ir=ir_for(tmp_path);p=pipe(tmp_path);original=deepcopy(ir)
    original_audio=(tmp_path/'assets/communications_noise.wav').read_bytes()
    changed=retime_ir(p,ir,speed)
    assert ir==original
    compiled=validate_video_ir(changed,tmp_path)
    assert abs(changed['video']['duration_ms']-5000/speed)<120
    assert abs(changed['narration']['segments'][0]['start_ms']-200/speed)<10
    assert compiled['total_frames']>0
    with wave.open(str(tmp_path/'assets/communications_noise.wav')) as f:
        audio=np.frombuffer(f.readframes(f.getnframes()),dtype='<i2')
    spectrum=np.abs(np.fft.rfft(audio));peak=np.argmax(spectrum)*24000/len(audio)
    assert abs(peak-400)<2
    if speed!=1:
        assert (tmp_path/'assets/voice-normal-speed.wav').is_file()
        restored=retime_ir(p,changed,1)
        assert abs(restored['video']['duration_ms']-5000)<70
        validate_video_ir(restored,tmp_path)
        assert (tmp_path/'assets/communications_noise.wav').read_bytes()==original_audio


def test_safe_screen_fields_and_parent_immutability():
    scene={'component':'concept_reveal','props':{'title':'三个重点','lines':['决定：记录结论','行动：明确责任','待确认：保留未知']}}
    updated=apply_changes(scene,{'title':'会议记录重点','lines.0':'决定：已经确认的结论'})
    assert scene['props']['title']=='三个重点' and updated['props']['title']=='会议记录重点'
    with pytest.raises(ValueError):apply_changes(scene,{'layout.x':'10'})
    with pytest.raises(ValueError):apply_changes(scene,{'__proto__':'bad'})
    with pytest.raises(ValueError):apply_changes(scene,{'lines.8':'bad'})


def complete_parent(client):
    response=client.post('/api/jobs',json={'source_text':SOURCE,'duration_mode':'content','video_form':'information_motion','voice_id':'zm_yunxi','video_speed':1.5})
    assert response.status_code==202
    jid=response.json()['job_id'];store=client.app.state.store;claim=store.claim_next()
    run=store.data_dir/claim['workspace_rel_path']/'g2-run';fixture=run/'fixtures';artifacts=run/'artifacts';artifacts.mkdir(parents=True)
    ir=ir_for(fixture);(fixture/'video-ir-v0.2.json').write_text(json.dumps(ir));(artifacts/'final-narration-plan.json').write_text(json.dumps({'segments':[{'id':'n1','text':'测试旁白。'}]}));(artifacts/'video.mp4').write_bytes(b'fake video used only for offline API tests')
    files=[]
    for path in [*(fixture.rglob('*')),*(artifacts.rglob('*'))]:
        if path.is_file():files.append({'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
    (artifacts/'manifest.json').write_text(json.dumps({'files':files}))
    records=[]
    for name,path in [('video-ir.json',fixture/'video-ir-v0.2.json'),('video.mp4',artifacts/'video.mp4'),('narration.json',artifacts/'final-narration-plan.json'),('manifest.json',artifacts/'manifest.json')]:
        records.append({'name':name,'relative_path':str(path.relative_to(run.parent)),'content_type':'application/json','sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'bytes':path.stat().st_size})
    store.mark_completed(jid,1,records);return jid


def test_versioned_scene_and_media_requests_preserve_parent_and_options(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path/'data'),start_worker=False,preflight=READY)) as c:
        jid=complete_parent(c);parent=c.get('/api/jobs/'+jid).json()
        scene=c.get('/api/jobs/'+jid+'/scenes').json()['items'][0]
        assert scene['fields'][0]['value']=='测试标题'
        child=c.post('/api/jobs/'+jid+'/visual-revisions',json={'scene_id':'s1','changes':{'title':'修订标题'}})
        assert child.status_code==202
        v=c.get('/api/jobs/'+child.json()['job_id']).json()
        assert v['voice_id']=='zm_yunxi' and v['video_speed']==1.5 and v['parent_job_id']==jid
        media=c.post('/api/jobs/'+jid+'/media-revisions',json={'video_speed':2})
        assert media.status_code==202
        assert c.get('/api/jobs/'+media.json()['job_id']).json()['video_speed']==2
        assert c.get('/api/jobs/'+jid).json()==parent
        assert len(c.get('/api/jobs/'+jid+'/revisions').json()['items'])==2


def test_quarter_speed_is_supported_for_new_jobs_and_media_revisions(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path/'data'),start_worker=False,preflight=READY)) as c:
        assert 1.25 in c.get('/api/voices').json()['speeds']
        parent=complete_parent(c)
        revised=c.post('/api/jobs/'+parent+'/media-revisions',json={'video_speed':1.25})
        assert revised.status_code==202,revised.text
        assert c.get('/api/jobs/'+revised.json()['job_id']).json()['video_speed']==1.25
        assert c.get('/api/jobs/'+parent).json()['video_speed']==1.5
        created=c.post('/api/jobs',json={'source_text':SOURCE,'video_speed':1.25})
        assert created.status_code==202,created.text
        assert c.get('/api/jobs/'+created.json()['job_id']).json()['video_speed']==1.25


def test_invalid_scope_rate_voice_and_corrupt_parent_rejected(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path/'data'),start_worker=False,preflight=READY)) as c:
        jid=complete_parent(c)
        for request in [{'scene_id':'missing','changes':{'title':'x'}},{'scene_id':'s1','changes':{'audio.path':'bad'}}]:
            assert c.post('/api/jobs/'+jid+'/visual-revisions',json=request).status_code==409
        for request in [{'video_speed':3},{'voice_id':'untrusted'},{}, {'video_speed':0}]:
            assert c.post('/api/jobs/'+jid+'/media-revisions',json=request).status_code==422
        assert c.post('/api/jobs/'+jid+'/visual-revisions',json={'scene_id':'s1','changes':{'title':'x'},'instruction':'also change'}).status_code==422
        path=next((tmp_path/'data/jobs'/jid/'attempts/1/g2-run/fixtures/assets').glob('*.wav'));path.write_bytes(b'changed')
        assert c.get('/api/jobs/'+jid+'/scenes').status_code==409


def test_settings_validation_defaults_and_choices(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path/'data'),start_worker=False,preflight=READY)) as c:
        r=c.post('/api/jobs',json={'source_text':SOURCE});assert r.status_code==202
        job=c.get('/api/jobs/'+r.json()['job_id']).json();assert job['voice_id']=='zf_xiaoxiao' and job['video_speed']==1
        assert c.post('/api/jobs',json={'source_text':SOURCE,'video_speed':3}).status_code==422
        assert c.post('/api/jobs',json={'source_text':SOURCE,'voice_id':'fake'}).status_code==422
        assert len(c.get('/api/voices').json()['items'])==14


def test_flow_text_revision_retains_graph_identity_and_connections():
    scene={'component':'flow_diagram','props':{'title':'借用流程','takeaway':'缺货则等待','nodes':[{'id':'apply','label':'申请'},{'id':'wait','label':'等待'}],'edges':[{'id':'e1','from':'apply','to':'wait','label':'缺货','relation':'flow'}],'paths':[]}}
    updated=apply_changes(scene,{'nodes.wait.label':'进入候补名单','edges.e1.label':'无设备','title':'设备借用'})
    assert updated['props']['nodes'][1]['label']=='进入候补名单'
    assert scene['props']['nodes'][1]['label']=='等待'
    assert updated['props']['edges'][0]['from']=='apply' and updated['props']['edges'][0]['to']=='wait'
    with pytest.raises(ValueError):apply_changes(scene,{'edges.e1.to':'outside'})


def test_playback_extension_validates_speed_duration_relation(tmp_path):
    ir=ir_for(tmp_path)
    ir['playback']={'speed':2,'source_duration_ms':5000,'method':'pitch-preserving audio tempo; synchronized captions and animation'}
    from g1.video_ir import ValidationFault
    with pytest.raises(ValidationFault,match='PLAYBACK_DURATION_MISMATCH'):
        validate_video_ir(ir,tmp_path)
