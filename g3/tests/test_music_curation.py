import copy
import hashlib
import json

import pytest
from fastapi.testclient import TestClient

from g2.creative_recommendations import recommend
from g3 import music_curation as study
from g3 import music_assets
from g3.app import create_app
from g3.config import Settings
from g3.tests.test_g3_package import READY
from g3.tests.test_music_assets_api import audio_bytes


def uploaded(c):
    r=c.post('/api/music-assets',files={'file':('test-instrumental.wav',audio_bytes())})
    assert r.status_code==201,r.text
    return r.json()


def test_full_audio_analysis_loop_preview_and_explicit_listening_record(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path),start_worker=False,preflight=READY)) as c:
        asset=uploaded(c);identity=asset['id'];path=tmp_path/'music-assets'/identity/'asset.wav'
        original=path.read_bytes()
        r=c.post(f'/api/music-assets/{identity}/analyze');assert r.status_code==200,r.text
        report=r.json();assert report['status']=='passed' and report['full_decode'] and report['loop_sample_continuity']
        assert report['vocal_absence']=='requires_listening' and report['musical_loop_naturalness']=='requires_listening'
        item=next(a for a in c.get('/api/music-assets').json()['items'] if a['id']==identity)
        assert item['curation']['review']['status']=='pending'
        preview=c.get(item['curation']['loop_preview_url'],headers={'Range':'bytes=0-63'})
        assert preview.status_code==206 and preview.content[:4]==b'RIFF'
        target=tmp_path/'music-curation'/identity
        (target/'narration-preview.wav').write_bytes(original)
        study.write(target/'narration-preview.json',{'asset_sha256':asset['sha256'],
                    'preview_sha256':hashlib.sha256(original).hexdigest(),'video_job_id':'isolated-fixture'})
        response=c.get(f'/api/music-assets/{identity}/narration-preview?sha256={asset["sha256"]}',headers={'Range':'bytes=0-63'})
        assert response.status_code==206
        (target/'narration-preview.wav').write_bytes(b'corrupt preview')
        assert c.get(f'/api/music-assets/{identity}/narration-preview?sha256={asset["sha256"]}').status_code==422
        review={'asset_sha256':asset['sha256'],'listened':True,'no_unexpected_vocals':True,'natural_loop':True,'narration_friendly':True,'note':'isolated fixture record, not an actual musical review'}
        assert c.post(f'/api/music-assets/{identity}/review',json={**review,'listened':False}).status_code==422
        assert c.post(f'/api/music-assets/{identity}/review',json={**review,'asset_sha256':'0'*64}).status_code==422
        assert c.post(f'/api/music-assets/{identity}/review',json=review).json()['status']=='passed'
        assert c.post(f'/api/music-assets/{identity}/review',json={**review,'natural_loop':False}).json()['status']=='needs_work'
        assert len(list((tmp_path/'music-curation'/identity).glob('review-*.json')))==1
        assert path.read_bytes()==original


def test_preview_tamper_missing_analysis_and_old_reviews_are_not_trusted(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path),start_worker=False,preflight=READY)) as c:
        asset=uploaded(c);identity=asset['id']
        review={'asset_sha256':asset['sha256'],'listened':True,'no_unexpected_vocals':True,'natural_loop':True,'narration_friendly':True}
        assert c.post(f'/api/music-assets/{identity}/review',json=review).status_code==422
        c.post(f'/api/music-assets/{identity}/analyze')
        folder=tmp_path/'music-curation'/identity
        (folder/'loop-preview.wav').write_bytes(b'changed')
        assert c.get(f'/api/music-assets/{identity}/loop-preview?sha256={asset["sha256"]}').status_code==422
        assert c.post(f'/api/music-assets/{identity}/analyze').json()['status']=='passed'
        study.write(folder/'review.json',{'asset_sha256':'0'*64,'status':'passed'})
        assert study.describe(tmp_path,asset)['review']['status']=='pending'
        (folder/'review.json').unlink();(folder/'review.json').symlink_to(tmp_path/'outside.json')
        with pytest.raises(ValueError):study.describe(tmp_path,asset)


def test_common_candidates_bind_to_audio_content_and_do_not_change_snapshots(tmp_path,monkeypatch):
    with TestClient(create_app(Settings(data_dir=tmp_path),start_worker=False,preflight=READY)) as c:
        asset=uploaded(c);identity=asset['id'];before=copy.deepcopy(music_assets.load(tmp_path,identity)[0])
        config=tmp_path/'common.json';config.write_text(json.dumps({'assets':[{'id':identity,'asset_sha256':asset['sha256'],
              'display_name':'教程常用','recipe_ids':['how_to'],'rhythm':'轻','purpose':'验证用途'}]}));monkeypatch.setattr(study,'CATALOG',config)
        described=study.describe(tmp_path,asset)
        assert described['common'] and described['review']['status']=='pending' and described['rhythm']=='轻'
        assert music_assets.load(tmp_path,identity)[0]==before
        different={**asset,'sha256':'f'*64};assert not study.describe(tmp_path,different)['common']
        # A never-normalized builtin may be listed by its pinned source identity.
        data=json.loads(config.read_text());data['assets'][0]['source_sha256']='a'*64;config.write_text(json.dumps(data))
        unnormalized={'id':identity,'kind':'builtin','source_sha256':'a'*64}
        assert study.describe(tmp_path,unnormalized)['common']
        assert not study.describe(tmp_path,{**unnormalized,'source_sha256':'b'*64})['common']


def test_recommendation_uses_common_choices_and_excludes_failed_reviews():
    def asset(identity,**curation):
        return {'id':identity,'kind':'builtin','name':identity,'available':True,'preview_url':'/preview',
                'curation':{'common':True,'recipe_ids':['how_to'],**curation}}
    result=recommend('教程','how_to',[],[asset('curated'),asset('bad',review={'status':'needs_work'}),asset('broken',technical={'status':'needs_work'})])
    assert [a['id'] for a in result['music']]==['curated']
    assert result['music'][0]['review_status']=='pending' and result['automatic_changes'] is False
