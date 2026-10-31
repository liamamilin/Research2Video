"""Exact render identity and retained-history contracts; isolated databases only."""
import json
from tests.test_video_scripts import video_env, _approved_script, _login
from core.video import store
from web.services import video_render


def completed(script, number, start):
    row = store.create_render(script['id'], number, title='具体成片', source_text='x', source_sha256='0'*64, request={})
    folder = video_render.files_dir(row['id']); folder.mkdir(parents=True)
    (folder/'video.mp4').write_bytes(b'preserved-video')
    (folder/'chapters.json').write_text(json.dumps({'timeline':'packaged','chapters':[{'title':'开场','start_ms':0},{'title':'展开','start_ms':start},{'title':'结论','start_ms':start+20000}]}))
    store.update_render(row['id'], status='COMPLETED', artifacts=[{'name':'video.mp4'},{'name':'chapters.json'}])
    return row


def test_exact_render_chapters_reject_cross_script_version_and_unavailable(client, video_env):
    s = _approved_script('具体成片'); a = completed(s,1,30000); b = completed(s,1,50000)
    other = _approved_script('其他文稿'); c = completed(other,1,70000)
    _login(client)
    url = f"/api/video/scripts/{s['id']}/publish"
    one = client.get(url,params={'version':1,'render_id':a['id']}); assert one.status_code == 200, one.text
    assert one.json()['render_id'] == a['id'] and '00:30 展开' in one.json()['description']
    assert '00:50 展开' in client.get(url,params={'version':1,'render_id':b['id']}).json()['description']
    assert client.get(url,params={'version':2,'render_id':a['id']}).status_code == 422
    assert client.get(url,params={'render_id':c['id']}).status_code == 404
    assert client.get(url,params={'render_id':'missing'}).status_code == 404
    (video_render.files_dir(a['id'])/'video.mp4').unlink()
    assert client.get(url,params={'render_id':a['id']}).status_code == 404


def test_history_keeps_two_versions_after_edit_without_altering_inflight(client, video_env):
    s = _approved_script('保留旧片'); a = completed(s,1,30000)
    store.add_version(s['id'],title='第二版',sections=[],length_check={},edit_type='MANUAL',meta={})
    b = completed(s,2,50000); store.update_script(s['id'],status='DRAFT')
    active = store.create_render(s['id'],2,title='在途',source_text='x',source_sha256='0'*64,request={})
    store.update_render(active['id'],status='RUNNING')
    _login(client)
    assert not client.get('/api/video/shipments').json()
    rows = client.get('/api/video/shipments',params={'include_history':'true','compact':'true'}).json()
    assert len(rows)==2 and {r['render']['version'] for r in rows}=={1,2}
    assert all(r['historical'] and r['current_version']==2 and r['script_status']=='DRAFT' for r in rows)
    assert store.get_render(active['id'])['status']=='RUNNING'
    assert (video_render.files_dir(a['id'])/'video.mp4').read_bytes()==b'preserved-video'
    assert (video_render.files_dir(b['id'])/'video.mp4').read_bytes()==b'preserved-video'
