from fastapi.testclient import TestClient

from g3.app import create_app
from g3.config import Settings
from g3.tests.test_creative_forms_api import upload
from g3.tests.test_g3_package import READY, saved_input

CHAPTERS = [{'title': '第一章', 'paragraphs': ['第一章的讲稿。']}, {'title': '第二章', 'paragraphs': ['第二章的讲稿。']}]


def body(images, **extra):
    return {'input_mode': 'narration', 'chapters': CHAPTERS, 'duration_mode': 'content',
            'video_form': 'illustrated_explainer', 'slideshow_images': images, **extra}


def test_illustrated_job_freezes_one_image_per_chapter_and_keywords(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path), start_worker=False, preflight=READY)) as c:
        a, b = upload(c)['image_id'], upload(c)['image_id']
        response = c.post('/api/jobs', json=body([a, b], chapter_keywords=[['关键词'], [' 两个 ', '词']]))
        assert response.status_code == 202, response.text
        saved = saved_input(c, response.json()['job_id'])
        assert [r['image_id'] for r in saved['slideshow_images_resolved']] == [a, b]
        assert saved['chapter_keywords'] == [['关键词'], ['两个', '词']]
        assert saved['use_draw_things'] is False


def test_illustrated_job_rejects_mismatched_inputs(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path), start_worker=False, preflight=READY)) as c:
        a, b = upload(c)['image_id'], upload(c)['image_id']
        rejected = [
            body([a]),                                                     # fewer images than chapters
            body([], chapter_keywords=[['一'], ['二']]),                   # generated: keywords come from the storyboard
            body([a, b], chapter_keywords=[['一']]),                       # keyword rows != chapters
            body([a, b], chapter_keywords=[['一', '二', '三'], []]),       # more than two per chapter
            body([a, b], chapter_keywords=[['超过十二个字的一个很长关键词'], []]),
            {'source_text': '素材正文。' * 10, 'duration_mode': 'content', 'video_form': 'illustrated_explainer',
             'slideshow_images': [a, b]},                                  # needs authored chapters
            {'input_mode': 'narration', 'chapters': CHAPTERS, 'duration_mode': 'content',
             'video_form': 'kinetic_typography', 'chapter_keywords': [[], []]},  # keywords only for this form
        ]
        for payload in rejected:
            assert c.post('/api/jobs', json=payload).status_code == 422, payload


def test_no_pictures_means_local_generation_and_needs_draw_things(tmp_path):
    off = lambda _: {'ready': True, 'checks': {'diagram_layout': True, 'draw_things': False}, 'blockers': []}
    with TestClient(create_app(Settings(data_dir=tmp_path), start_worker=False, preflight=off)) as c:
        response = c.post('/api/jobs', json=body([]))
        assert response.status_code == 503 and 'DRAW_THINGS_NOT_READY' in response.text, response.text
    on = lambda _: {'ready': True, 'checks': {'diagram_layout': True, 'draw_things': True}, 'blockers': []}
    with TestClient(create_app(Settings(data_dir=tmp_path / 'on'), start_worker=False, preflight=on)) as c:
        response = c.post('/api/jobs', json=body([]))
        assert response.status_code == 202, response.text
        assert saved_input(c, response.json()['job_id'])['use_draw_things'] is True
