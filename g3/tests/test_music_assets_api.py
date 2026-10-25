import copy
import io
import json
import shutil
import sys
import threading
from dataclasses import replace

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

from g2.music_library import frozen_input
from g2.production_plan import verify
from g3.app import create_app
from g3.config import Settings
from g3.tests.test_g3_package import READY, body, saved_input
from g3.tests.test_scene_media import complete_parent
from g3.worker import G2ProcessRunner


def audio_bytes(silent=False):
    buf = io.BytesIO()
    sf.write(buf, np.zeros(48000) if silent else .15*np.sin(np.arange(48000)*2*np.pi*220/24000), 24000, format='WAV')
    return buf.getvalue()


def test_import_preview_rejection_and_freeze(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path), start_worker=False, preflight=READY)) as c:
        for name, raw in [('list.m3u', b'http://example.com/a.wav'), ('fake.wav', b'http://example.com/a.wav'), ('silent.wav', audio_bytes(True))]:
            assert c.post('/api/music-assets', files={'file': (name, raw)}).status_code == 422
        asset = c.post('/api/music-assets', files={'file': ('我的音乐.wav', audio_bytes())})
        assert asset.status_code == 201, asset.text
        asset = asset.json()
        assert asset['sample_rate'] == 48000 and asset['channels'] == 2
        assert c.get(asset['preview_url']).content[:4] == b'RIFF'
        assert c.get(asset['preview_url'], headers={'Range':'bytes=0-31'}).status_code == 206
        assert any(a['id'] == asset['id'] for a in c.get('/api/music-assets').json()['items'])
        r = c.post('/api/jobs', json=body(production_recipe='ai_news', background_music={'enabled':True, 'asset_id':asset['id']}))
        assert r.status_code == 202, r.text
        jid = r.json()['job_id']; saved = saved_input(c, jid); store = c.app.state.store
        resolved, path = frozen_input(store.source_path(jid).parent, saved)
        assert resolved['asset']['sha256'] == asset['sha256'] and path.is_file()
        assert saved['production_recipe_resolved']['music']['mode'] == 'background'
        assert saved['production_plan']['music_record_sha256'] == resolved['sha256']
        verify({**saved, 'source_text': store.source_path(jid).read_text()})
        store.claim_next(); store.mark_failed(jid, 1, 'RENDER', 'TEMP', 'isolated', True)
        frozen = path.read_bytes()
        shutil.rmtree(tmp_path/'music-assets'/asset['id'])
        assert c.post(f'/api/jobs/{jid}/retry').status_code == 202
        assert path.read_bytes() == frozen


def test_default_music_is_off_and_explicit_disable_wins(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path), start_worker=False, preflight=READY)) as c:
        for extra in [{}, {'production_recipe':'ai_news'}, {'production_recipe':'how_to', 'background_music':{'enabled':False}}]:
            r=c.post('/api/jobs', json=body(**extra)); assert r.status_code == 202, r.text
            saved=saved_input(c,r.json()['job_id'])
            assert not (saved.get('background_music') or {}).get('enabled')
        for value in [{'enabled':True}, {'asset_id':'../audio'}, {'level_db':-1}, {'url':'http://example.com/audio'}]:
            assert c.post('/api/jobs', json=body(background_music=value)).status_code == 422


def test_music_revisions_reuse_frozen_parent_without_provider_or_cache(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path), start_worker=False, preflight=READY)) as c:
        jid = complete_parent(c); before = copy.deepcopy(c.get('/api/jobs/'+jid).json())
        asset = c.post('/api/music-assets', files={'file':('music.wav', audio_bytes())}).json()
        c.app.state.preflight = lambda _: {'ready':False}  # Audio-only changes do not require a model.
        child = c.post(f'/api/jobs/{jid}/music-revisions', json={'background_music':{'enabled':True,'asset_id':asset['id']}})
        assert child.status_code == 202, child.text
        child_id=child.json()['job_id']; saved=saved_input(c,child_id)
        assert saved['operation_type']=='music_revision' and saved['voice_id']=='zm_yunxi' and saved['video_speed']==1.5
        store=c.app.state.store
        # Freeze a derived version using the saved parent's asset even after global cache disappears.
        shutil.rmtree(tmp_path/'music-assets'/asset['id'])
        from g3.music_assets import freeze
        target=tmp_path/'derived'; target.mkdir()
        derived=freeze(tmp_path,target,{**saved,'parent_job_id':child_id,'background_music':{**saved['background_music'],'level_db':-24}})
        assert frozen_input(target,derived)[1].is_file()
        assert c.get('/api/jobs/'+jid).json()==before
        assert c.post(f'/api/jobs/{jid}/music-revisions',json={'background_music':{'enabled':False}}).status_code==202


def test_worker_never_publishes_music_without_passing_mix_qa(tmp_path, monkeypatch):
    from g3.tests.test_g3_package import FAKE_G2
    fake=tmp_path/'fake.py'; fake.write_text(FAKE_G2)
    settings=replace(Settings(data_dir=tmp_path/'data'),g2_python=sys.executable,g2_script=fake)
    monkeypatch.setenv('AUTOVIDEO_TEST_MODE','ok')
    with TestClient(create_app(settings,start_worker=False,preflight=READY)) as c:
        asset=c.post('/api/music-assets',files={'file':('track.wav',audio_bytes())}).json()
        r=c.post('/api/jobs',json=body(background_music={'enabled':True,'asset_id':asset['id']}))
        assert r.status_code==202,r.text
        store=c.app.state.store;G2ProcessRunner(settings,store).execute(store.claim_next(),threading.Event())
        result=store.get_job(r.json()['job_id'])
        assert result['status']=='FAILED' and result['error_code']=='MIX_UNVERIFIED'
        assert result['active_stage']=='AUDIO_MIX' and not store.get_artifacts(result['job_id'])


def test_fast_and_standard_share_music_plan_and_frozen_identity(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path),start_worker=False,preflight=READY)) as c:
        asset=c.post('/api/music-assets',files={'file':('track.wav',audio_bytes())}).json()
        for form,mode,style in [('kinetic_typography','standard','legacy'),('information_cards','fast','editorial_light')]:
            r=c.post('/api/jobs',json=body(video_form=form,production_mode=mode,visual_style=style,
                background_music={'enabled':True,'asset_id':asset['id']}))
            assert r.status_code==202,r.text
            saved=saved_input(c,r.json()['job_id'])
            verify(saved)
            assert saved['production_plan']['music']['mode']=='background'
            assert saved['production_plan']['settings']['music'] is None  # Existing v1 binding remains valid.
            altered=copy.deepcopy(saved);altered['background_music']['enabled']=False
            with pytest.raises(ValueError):verify(altered)


@pytest.mark.parametrize('mode',['source','narration'])
def test_content_revisions_inherit_music_after_cache_disappears(tmp_path,mode):
    with TestClient(create_app(Settings(data_dir=tmp_path),start_worker=False,preflight=READY)) as c:
        asset=c.post('/api/music-assets',files={'file':('track.wav',audio_bytes())}).json()
        class ParentClient:
            app=c.app
            def post(self,path,*,json):
                return c.post(path,json={**json,'background_music':{'enabled':True,'asset_id':asset['id'],'level_db':-23}})
        parent=complete_parent(ParentClient())
        shutil.rmtree(tmp_path/'music-assets'/asset['id'])
        text='这是修订后的完整素材，保留原文中的条件和例外，并按顺序解释已经确定的事项以及仍然待确认的事项，保持原有画面表达和背景音乐。' if mode=='source' else '这是修订后的逐字讲稿。'
        response=c.post(f'/api/jobs/{parent}/revisions',json={'mode':mode,'text':text})
        assert response.status_code==202,response.text
        jid=response.json()['job_id'];saved=saved_input(c,jid)
        assert saved['background_music']['asset_id']==asset['id'] and saved['background_music']['level_db']==-23
        assert frozen_input(c.app.state.store.source_path(jid).parent,saved)[1].is_file()
