import hashlib
from io import BytesIO
import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from g3.app import create_app
from g3.config import Settings
from g3.tests.test_g3_package import READY, saved_input


def image_bytes():
    output = BytesIO(); Image.new('RGB', (640, 480), 'orange').save(output, 'PNG'); return output.getvalue()


def upload(client):
    response = client.post('/api/creative-images', files={'file': ('illustration.png', image_bytes(), 'image/png')})
    assert response.status_code == 201, response.text
    return response.json()


@pytest.mark.parametrize('recipe,form', [('ai_news','kinetic_typography'),('how_to','image_slideshow'),('science_explainer','chat_dialogue')])
def test_recipe_and_manual_form_support_material_authored_and_revisions(tmp_path, recipe, form):
    with TestClient(create_app(Settings(data_dir=tmp_path), start_worker=False, preflight=READY)) as c:
        extra = {'slideshow_images': [upload(c)['image_id']]} if form == 'image_slideshow' else {}
        for source in [{'source_text': '完整的事实材料，保留条件限制和例外。' * 5},
                       {'input_mode':'narration','chapters':[{'title':'讲稿章节','paragraphs':['这段讲稿不应被改写。']}]}]:
            response=c.post('/api/jobs',json={**source,**extra,'production_recipe':recipe,'video_form':form,'duration_mode':'content'})
            assert response.status_code==202,response.text
            jid=response.json()['job_id']; store=c.app.state.store; saved=saved_input(c,jid)
            assert saved['production_recipe_resolved']['selection']['video_form']==form
            assert saved['production_recipe_resolved']['recipe']['id']==recipe
            assert saved['packaging_resolved']
            claim=store.claim_next(); store.mark_failed(jid,1,'PLAN','TEST','failure test',True)
            assert c.post(f'/api/jobs/{jid}/retry').status_code==202
            store.claim_next(); store.mark_failed(jid,2,'PLAN','TEST','failure test',True)
            child=c.post(f'/api/jobs/{jid}/revisions',json={'mode':'source','text':'新的完整素材，所有事实都需要保留。'*5})
            assert child.status_code==202,child.text
            inherited=saved_input(c,child.json()['job_id'])
            assert inherited['video_form']==form
            assert inherited['production_recipe_resolved']['recipe']==saved['production_recipe_resolved']['recipe']
            assert inherited.get('slideshow_images_resolved')==saved.get('slideshow_images_resolved')
            # Finish the child so the next iteration's claim remains unambiguous.
            store.claim_next(); store.mark_failed(child.json()['job_id'],1,'PLAN','TEST','test',True)


def test_upload_validation_order_freezing_and_unavailable_generation(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path), start_worker=False, preflight=READY)) as c:
        assert c.post('/api/creative-images',files={'file':('unsafe.svg',b'<svg/>','image/svg+xml')}).status_code==422
        a,b=upload(c),upload(c)
        body={'source_text':'轮播的完整素材内容。'*8,'video_form':'image_slideshow','duration_mode':'content'}
        assert c.post('/api/jobs',json=body).status_code==503 # no local generator and no uploaded images
        assert c.post('/api/jobs',json={**body,'slideshow_images':['../../bad']}).status_code==422
        assert c.post('/api/jobs',json={**body,'slideshow_images':[str(uuid4())]}).status_code==422
        assert c.post('/api/jobs',json={**body,'slideshow_images':[a['image_id'],a['image_id']]}).status_code==422
        response=c.post('/api/jobs',json={**body,'slideshow_images':[b['image_id'],a['image_id']]})
        assert response.status_code==202,response.text
        jid=response.json()['job_id'];store=c.app.state.store;saved=saved_input(c,jid)
        assert saved['use_draw_things'] is False
        assert [r['image_id'] for r in saved['slideshow_images_resolved']]==[b['image_id'],a['image_id']]
        frozen=store.source_path(jid).parent/'images'/f"{a['image_id']}.png"
        expected=hashlib.sha256(frozen.read_bytes()).hexdigest()
        original=tmp_path/'creative-images'/a['image_id']/'image.png'; original.write_bytes(b'changed')
        assert c.get(a['preview_url']).status_code==404
        assert hashlib.sha256(frozen.read_bytes()).hexdigest()==expected
        assert c.post('/api/jobs',json={**body,'slideshow_images':[a['image_id']]}).status_code==422


def test_revision_uses_frozen_parent_images_even_if_import_is_unavailable(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path), start_worker=False, preflight=READY)) as c:
        image=upload(c)
        body={'source_text':'保存给修订使用的完整图片轮播素材。'*5,'video_form':'image_slideshow','duration_mode':'content','slideshow_images':[image['image_id']]}
        response=c.post('/api/jobs',json=body);jid=response.json()['job_id'];store=c.app.state.store
        store.claim_next();store.mark_failed(jid,1,'PLAN','TEST','test',True)
        (tmp_path/'creative-images'/image['image_id']/'image.png').write_bytes(b'changed import')
        child=c.post(f'/api/jobs/{jid}/revisions',json={'mode':'source','text':'修订后的完整素材。'*10})
        assert child.status_code==202,child.text
        parent_file=store.source_path(jid).parent/'images'/f"{image['image_id']}.png"
        child_file=store.source_path(child.json()['job_id']).parent/'images'/parent_file.name
        assert child_file.read_bytes()==parent_file.read_bytes()
        store.claim_next();store.mark_failed(child.json()['job_id'],1,'PLAN','TEST','test',True)
        parent_file.write_bytes(b'corrupt parent')
        assert c.post(f'/api/jobs/{jid}/revisions',json={'mode':'source','text':'另一份修订后的完整素材。'*10}).status_code==409
