import copy
import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from g2.creative_recommendations import recommend
from g2.production_plan import verify
from g3.app import create_app
from g3.config import Settings
from g3.tests.test_creative_forms_api import upload
from g3.tests.test_g3_package import READY, saved_input


@pytest.mark.parametrize('form', ['object_showcase', 'dynamic_collage'])
@pytest.mark.parametrize('authored', [False, True])
def test_materials_freeze_plan_and_revision_without_global_import(tmp_path, form, authored):
    with TestClient(create_app(Settings(data_dir=tmp_path), start_worker=False, preflight=READY)) as c:
        images = [upload(c), upload(c)]
        materials = [{'image_id':i['image_id'], 'caption':caption, 'role':role} for i,caption,role in
                     zip(images, ['整体示意','细节示意'], ['overall','detail'])]
        source = ({'input_mode':'narration','chapters':[{'title':'观察对象','paragraphs':['这是整体与细节的原创示意图，说明以已有文稿为准。']}]} if authored
                  else {'source_text':'这是整体与细节的原创示意图。图片按顺序展示，说明以已有文稿为准。'*3})
        request = {**source,'video_form':form,'duration_mode':'content','image_materials':materials,
                   'production_recipe':'science_explainer','idempotency_key':'materials-test'}
        r = c.post('/api/jobs', json=request)
        assert r.status_code == 202, r.text
        jid = r.json()['job_id']; saved = saved_input(c,jid); store=c.app.state.store
        assert c.post('/api/jobs',json=request).json()['job_id']==jid
        assert saved['image_materials'] == materials and not saved['use_draw_things']
        assert saved['slideshow_images'] == [i['image_id'] for i in images]
        verify(saved)
        changed=copy.deepcopy(saved); changed['image_materials'][0]['caption']='被修改'
        with pytest.raises(ValueError): verify(changed)
        store.claim_next();store.mark_failed(jid,1,'PLAN','TEST','test',True)
        for image in images:
            (tmp_path/'creative-images'/image['image_id']/'image.png').unlink()
        child=c.post(f'/api/jobs/{jid}/revisions',json={'mode':'source','text':'修订文稿后仍使用任务保存的原图片，不依赖全局导入缓存。'*3})
        assert child.status_code==202,child.text
        inherited=saved_input(c,child.json()['job_id'])
        assert inherited['image_materials']==materials
        assert inherited['slideshow_images_resolved']==saved['slideshow_images_resolved']
        verify(inherited)


@pytest.mark.parametrize('form',['object_showcase','dynamic_collage'])
def test_missing_invalid_or_conflicting_images_rejected_before_queue(tmp_path,form):
    with TestClient(create_app(Settings(data_dir=tmp_path),start_worker=False,preflight=READY)) as c:
        base={'source_text':'对象展示和动态拼贴必须使用现有图片，不自动调用图像服务。'*3,'video_form':form}
        for extra in [{},{'slideshow_images':[str(uuid4())]}, {'image_materials':[{'image_id':'../bad'}]}]:
            assert c.post('/api/jobs',json={**base,**extra}).status_code==422
        image=upload(c)['image_id']
        material={'image_id':image,'role':'overall'}
        for extra in [{'image_materials':[material,material]}, {'image_materials':[{**material,'role':'invented'}]},
                      {'image_materials':[{**material,'caption':'a'*81}]},
                      {'image_materials':[{**material,'caption':'坏\x00说明'}]},
                      {'image_materials':[material],'slideshow_images':[str(uuid4())]},
                      {'image_materials':[material],'use_draw_things':True},
                      {'image_materials':[material],'production_mode':'fast'}]:
            assert c.post('/api/jobs',json={**base,**extra}).status_code==422
        assert c.get('/api/jobs').json()['total']==0


def test_recommendations_follow_available_materials_without_jobs_or_setting_changes(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path),start_worker=False,preflight=READY)) as c:
        pure=c.post('/api/creative-recommendations',json={'production_recipe':'how_to'}).json()
        assert pure['forms'][0]['id']=='process_diagram' and pure['automatic_changes'] is False
        image=upload(c)['image_id']
        generic=c.post('/api/creative-recommendations',json={'image_materials':[{'image_id':image}]}).json()
        assert generic['forms'][0]['id']=='dynamic_collage'
        annotated=c.post('/api/creative-recommendations',json={'image_materials':[{'image_id':image,'role':'detail'}]}).json()
        assert annotated['forms'][0]['id']=='object_showcase'
        assert c.post('/api/creative-recommendations',json={'production_recipe':'unknown'}).status_code==422
        assert c.post('/api/creative-recommendations',json={'image_materials':[{'image_id':str(uuid4())}]}).status_code==422
        assert c.get('/api/jobs').json()['total']==0


def test_music_recommendations_reuse_only_available_instrumental_candidates():
    def asset(identity, **kwargs):
        return {'id':identity,'name':identity,'preview_url':'/api/music-assets/'+identity+'/preview',
                'available':True,'kind':'uploaded',**kwargs}
    assets=[asset('vocal-import'),asset('missing',kind='builtin',available=False),
            asset('ambient_pad',kind='builtin'),asset('generated',kind='generated',topics=['原理科普'],
            generation={'request':{'instrumental':True,'style':'ambient'}}),
            asset('vocal-generated',kind='generated',generation={'request':{'instrumental':False}})]
    result=recommend('原理科普','science_explainer',[],assets)
    assert [item['id'] for item in result['music']]==['generated','ambient_pad']
    assert result['automatic_changes'] is False
