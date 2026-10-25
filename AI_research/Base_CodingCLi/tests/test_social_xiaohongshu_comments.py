"""Xiaohongshu first comments: no live providers, credentials or platform writes."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.social.adapter import AdapterError
from core.social.comments import clean_first_comment
from core.social.xiaohongshu_comments import (XiaohongshuComments, ADD, API, SELF, FEED,
    allowed_login, allowed_read, comment_receipt, exact_comment_request, matching_comments, web_profile)
from scripts import social_comment_worker as worker
from tests.test_social_publishing import social_env, manager, PAYLOAD
from tests.test_platforms import render
from tests.test_video_scripts import _login, _csrf
from scripts.douyin_worker import private_json

OWNER='657b2244000000002002c62b'
WORK='690000000000000000000001'
COMMENT='690000000000000000000002'
OTHER='690000000000000000000003'
OPTION={'enabled':True,'text':'补充资料\n链接和说明','pin':False}


@pytest.fixture
def xhs_env(social_env):
    root=manager.directory('xiaohongshu')
    private_json(root/'account.json',{'uid':OWNER,'name':'测试小红书','verified_at':time.time()})
    private_json(manager.credential(root,OWNER),{'cookies':[],'origins':[]})
    return social_env


def row(**changes):
    return {'id':COMMENT,'note_id':WORK,'user_info':{'user_id':OWNER},'content':OPTION['text'],**changes}


def receipt(value=None, **changes):
    return {'code':0,'success':True,'data':{'comment':value if value is not None else row()},**changes}


def listing(rows=None):
    return {'code':0,'success':True,'data':{'comments':[row()] if rows is None else rows}}


def enqueue(option=OPTION):
    return manager.enqueue('xiaohongshu',render()['id'],{**PAYLOAD,'account_uid':OWNER,'first_comment':option})


def prepared():
    parent=enqueue();db=manager.store('xiaohongshu')
    db.update(parent['id'],status='PUBLISHED',work_id=WORK)
    manager.schedule_comments('xiaohongshu',db)
    child=db.comments(OWNER)[parent['id']]
    db.update(child['id'],status='RUNNING')
    return db,db.get(child['id']),parent


@pytest.mark.parametrize('option',[{'pin':True},{'enabled':1},{'enabled':True,'text':''},
    {'enabled':True,'text':'😀'*126},{'text':'\ud800'},{'text':'\x00'},{'enabled':True,'text':'x','target_comment_id':OTHER}])
def test_config_rejects_pin_invalid_text_and_extra_fields(option):
    with pytest.raises(AdapterError):clean_first_comment('xiaohongshu',option)


@pytest.mark.parametrize('value',[{}, {'code':True,'success':True,'data':{'user_id':OWNER}},
    {'code':0,'success':False,'data':{'user_id':OWNER}}, {'code':-101,'success':False,'data':{}},
    {'code':0,'success':True,'data':{'user_id':123}}])
def test_web_identity_requires_verified_uid(value):
    with pytest.raises(AdapterError):web_profile(value)


@pytest.mark.parametrize('guest',[True,None,0,1,'false'])
def test_guest_identity_never_counts_as_a_comment_login(guest):
    with pytest.raises(AdapterError):web_profile({'code':0,'success':True,'data':{'user_id':OWNER,'guest':guest}})


def test_receipt_uses_exact_ids_and_never_exposes_provider_tokens():
    assert web_profile({'code':0,'success':True,'data':{'user_id':OWNER,'guest':False}})==OWNER
    assert comment_receipt(receipt(),OWNER,WORK,OPTION['text'])=={'accepted':True,'comment_id':COMMENT}
    assert comment_receipt({'code':-100,'success':False,'msg':'private-token'},OWNER,WORK,OPTION['text'])=={'accepted':False,'code':-100}


@pytest.mark.parametrize('changes',[{'id':123},{'note_id':OTHER},{'content':'changed'},
    {'user_info':{'user_id':OTHER}},{'target_comment':{'id':OTHER}},{'note_id':None}])
def test_receipt_does_not_accept_wrong_note_author_content_or_reply(changes):
    with pytest.raises(AdapterError):comment_receipt(receipt(row(**changes)),OWNER,WORK,OPTION['text'])


@pytest.mark.parametrize('value',[{},receipt(code=True),receipt(success=False),receipt(code=7),
    {'code':0,'success':True,'data':{'comment_id':COMMENT}}])
def test_ambiguous_envelopes_do_not_infer_success(value):
    with pytest.raises(AdapterError):comment_receipt(value,OWNER,WORK,OPTION['text'])


def test_list_readback_is_scoped_to_exact_root_comment():
    assert matching_comments(listing(),OWNER,WORK,OPTION['text'],COMMENT)==[{'comment_id':COMMENT,'read_back':True}]
    assert matching_comments(listing(),OWNER,WORK,OPTION['text'],OTHER)==[]
    assert matching_comments(listing([row(note_id=OTHER)]),OWNER,WORK,OPTION['text'])==[]
    assert matching_comments(listing([row(target_comment={'id':OTHER})]),OWNER,WORK,OPTION['text'])==[]
    assert matching_comments(listing([row(user_info={'user_id':OTHER})]),OWNER,WORK,OPTION['text'])==[]
    assert matching_comments(listing([row(note_id=None)]),OWNER,WORK,OPTION['text'])
    assert matching_comments(listing([]),OWNER,WORK,OPTION['text'])==[]


def test_request_guards_block_replies_mutations_wrong_hosts_and_pin():
    assert exact_comment_request({'note_id':WORK,'content':OPTION['text'],'at_users':[]},WORK,OPTION['text'])
    for changes in ({'note_id':OTHER},{'content':'other'},{'target_comment_id':OTHER},{'at_users':[OWNER]},{'image_ids':['img']}):
        assert not exact_comment_request({'note_id':WORK,'content':OPTION['text'],**changes},WORK,OPTION['text'])


def test_comment_login_allows_only_official_qr_confirmation_lookup():
    url=f'https://{API}/api/qrcode/userinfo';body={'qrId':'qr-session','code':'qr-code'}
    assert allowed_login('POST',url,body)
    assert not allowed_read('POST',url,body)
    for invalid in ({}, {'qrId':'x'}, {**body,'note_id':WORK}, {'qrId':123,'code':'x'}):
        assert not allowed_login('POST',url,invalid)
    assert not allowed_login('POST',url.replace(API,'edith.xiaohongshu.com.evil.test'),body)
    assert allowed_read('GET',f'https://{API}/api/sns/web/v2/comment/page?note_id={WORK}')
    assert allowed_read('POST',f'https://{API}{FEED}',{'source_note_id':WORK},WORK)
    assert not allowed_read('POST',f'https://{API}{FEED}',{'source_note_id':OTHER},WORK)
    for path in (ADD,'/api/sns/web/v1/comment/like','/api/sns/web/v1/comment/delete','/api/sns/web/v1/comment/top','/web_api/sns/v2/note'):
        assert not allowed_read('POST',f'https://{API}{path}',{},WORK)
        assert not allowed_login('POST',f'https://{API}{path}',{})
    assert allowed_login('POST',f'https://{API}/api/sns/web/v1/login/qrcode/create',{})
    assert not allowed_login('POST','https://evil.example/api/sns/web/v1/login/qrcode/create',{})
    assert not allowed_read('POST',f'https://user:secret@{API}{FEED}',{'source_note_id':WORK},WORK)


@pytest.mark.parametrize('mode',['ok','timeout','bad_receipt','http','mismatch','wrong_account'])
def test_actual_guard_posts_at_most_once_and_persists_before_network(mode):
    session=XiaohongshuComments(None,None,OWNER,WORK)
    session.armed=True;session.home_matches=True;session.web_uid=OTHER if mode=='wrong_account' else OWNER
    session.text=OPTION['text'];events=[]
    session.before_send=lambda:events.append('saved-start')
    session.save_receipt=lambda value:events.append('saved-receipt')
    class Route:
        request=SimpleNamespace(method='POST',url=f'https://{API}{ADD}',post_data_json={'note_id':OTHER if mode=='mismatch' else WORK,'content':OPTION['text']})
        async def fetch(self,**kwargs):
            assert events==['saved-start'];events.append('post')
            assert kwargs['max_retries']==0 and kwargs['max_redirects']==0
            if mode=='timeout':raise TimeoutError()
            return SimpleNamespace(status=500 if mode=='http' else 200,json=AsyncMock(return_value={} if mode=='bad_receipt' else receipt()))
        async def fulfill(self,**kwargs):events.append('ack')
        async def abort(self):events.append('abort')
    async def run():
        session.result=asyncio.get_running_loop().create_future()
        await session.guard(Route());await session.guard(Route())
        if session.result.done():
            try:session.result.result()
            except Exception:pass
    asyncio.run(run())
    if mode in ('mismatch','wrong_account'):
        assert events==['abort','abort']
    else:
        assert events.count('post')==1 and events.index('saved-start')<events.index('post')
        assert events[-1]=='abort'
        if mode=='ok':assert events.index('saved-receipt')<events.index('ack')


def test_templates_are_account_and_platform_scoped_frozen_snapshots(xhs_env):
    parent=enqueue({'enabled':False,'text':'','pin':False});db=manager.store('xiaohongshu')
    manager.save_first_comment_template('xiaohongshu',OPTION)
    manager.schedule_comments('xiaohongshu',db)
    assert not db.comments(OWNER)
    second=manager.enqueue('xiaohongshu',render()['id'],{**PAYLOAD,'account_uid':OWNER})
    assert second['payload']['first_comment']==OPTION
    manager.save_first_comment_template('xiaohongshu',{**OPTION,'text':'new template'})
    assert db.get(second['id'])['payload']['first_comment']==OPTION
    assert db.comment_settings(OTHER)['enabled'] is False
    assert manager.store('kuaishou').comment_settings(OWNER)['enabled'] is False
    assert db.get(parent['id'])['payload']['first_comment']['enabled'] is False


def test_concurrent_add_and_same_work_reservation_are_unique(xhs_env):
    first=enqueue({'enabled':False,'text':'','pin':False});db=manager.store('xiaohongshu')
    db.update(first['id'],status='PUBLISHED',work_id=WORK)
    with ThreadPoolExecutor(max_workers=6) as pool:
        results=list(pool.map(lambda _:manager.add_first_comment('xiaohongshu',first['id'],OPTION),range(12)))
    assert len({r['first_comment']['id'] for r in results})==1
    second=enqueue();db.update(second['id'],status='PUBLISHED',work_id=WORK)
    manager.schedule_comments('xiaohongshu',db)
    assert db.comments(OWNER)[second['id']]['result']['duplicate_work'] is True
    with pytest.raises(AdapterError):manager.add_first_comment('xiaohongshu',first['id'],{**OPTION,'text':'replacement'})


@pytest.mark.parametrize('outcome',['ok','timeout','accepted_then_crash','private','wrong_evidence','wrong_account','login_missing'])
def test_worker_handles_failures_without_resending_or_changing_publication(xhs_env,monkeypatch,outcome):
    db,child,parent=prepared();count=[]
    class Session:
        async def open(self):
            if outcome=='login_missing':raise AdapterError('评论未登录')
        async def close(self):pass
        async def load(self):pass
        def matches(self,text,target=''):return [{'comment_id':COMMENT,'read_back':True}] if target else []
        async def send(self,text,before,save):
            before();count.append('post')
            if outcome=='timeout':raise TimeoutError()
            value={'accepted':True,'comment_id':COMMENT};save(value)
            if outcome=='accepted_then_crash':raise TimeoutError()
            return value
    monkeypatch.setattr(worker,'comment_session',lambda *args:Session())
    proof={'public':outcome!='private','owned':True,'uid':OTHER if outcome=='wrong_account' else OWNER,'work_id':WORK,
           'evidence':'kuaishou_creator_public' if outcome=='wrong_evidence' else 'xiaohongshu_creator_public'}
    provider=SimpleNamespace(inspect_work=AsyncMock(return_value=proof))
    asyncio.run(worker.send_first_comment(db,child,provider,None,None))
    current=db.get(child['id'])
    expected='FAILED' if outcome in ('private','wrong_evidence','wrong_account','login_missing') else 'UNKNOWN' if outcome=='timeout' else 'SENT'
    assert current['status']==expected
    assert db.get(parent['id'])['status']=='PUBLISHED'
    if count:
        db.update(child['id'],status='RUNNING')
        asyncio.run(worker.send_first_comment(db,db.get(child['id']),provider,None,None))
        assert count==['post']


def test_automatic_checks_only_read_and_back_off(xhs_env,monkeypatch):
    db,child,parent=prepared()
    db.update(child['id'],status='UNKNOWN',result={'submission_started':True,'next_check_at':0})
    for _ in range(3):manager.schedule_comments('xiaohongshu',db)
    check=db.list(kind='COMMENT_CHECK')[0]
    assert len(db.list(kind='COMMENT_CHECK'))==1
    class Session:
        async def open(self):pass
        async def close(self):pass
        def matches(self,*args):return []
        async def send(self,*args):pytest.fail('never send from a check')
    monkeypatch.setattr(worker,'comment_session',lambda *args:Session())
    asyncio.run(worker.check_first_comment(db,check,None,None))
    current=db.get(child['id'])
    assert current['status']=='UNKNOWN' and current['result']['next_check_at']>time.time()
    with pytest.raises(AdapterError):manager.first_comment_action('xiaohongshu',parent['id'],'retry')
    manager.schedule_comments('xiaohongshu',db)
    assert len(db.list(kind='COMMENT_CHECK'))==1 and len(db.list(kind='UPLOAD'))==1


def test_login_supplements_original_account_and_never_overwrites_wrong_uid(xhs_env):
    db=manager.store('xiaohongshu');old=manager.credential(manager.directory('xiaohongshu'),OWNER).read_text()
    login=manager.start_comment_login('xiaohongshu');job=db.get(login['id'])
    folder=manager.directory('xiaohongshu')/'jobs'/job['id']
    private_json(folder/'storage.json',{'cookies':[],'origins':[],'new-comment-session':True})
    db.update(job['id'],status='DONE',result={'uid':OTHER,'name':'wrong','comments_verified':True})
    manager.settle('xiaohongshu',db)
    assert db.get(job['id'])['status']=='FAILED'
    assert manager.credential(manager.directory('xiaohongshu'),OWNER).read_text()==old
    assert manager.account('xiaohongshu')['uid']==OWNER


def test_successful_login_and_verify_preserve_comment_connection(xhs_env):
    db=manager.store('xiaohongshu');login=manager.start_comment_login('xiaohongshu')
    folder=manager.directory('xiaohongshu')/'jobs'/login['id']
    private_json(folder/'storage.json',{'cookies':[],'origins':[],'new-comment-session':True})
    db.update(login['id'],status='DONE',result={'uid':OWNER,'name':'test','comments_verified':True})
    manager.settle('xiaohongshu',db)
    profile=manager.account('xiaohongshu')
    assert profile['comments_verified_at']>0
    assert json.loads(manager.credential(manager.directory('xiaohongshu'),OWNER).read_text())['new-comment-session'] is True
    verify=db.create('VERIFY',OWNER);db.update(verify['id'],status='DONE',result={'uid':OWNER,'name':'test'})
    manager.settle('xiaohongshu',db)
    assert manager.account('xiaohongshu')['comments_verified_at']==profile['comments_verified_at']


def test_api_permissions_comment_login_and_pin_rejection(xhs_env,client):
    parent=enqueue({'enabled':False,'text':'','pin':False});db=manager.store('xiaohongshu')
    db.update(parent['id'],status='SUBMITTED',work_id=WORK)
    path=f"/api/platforms/social/xiaohongshu/uploads/{parent['id']}/first-comment"
    _login(client,'viewer','viewer-pass-123')
    assert client.post(path,json=OPTION,headers=_csrf(client)).status_code==403
    _login(client,'editor')
    assert client.post('/api/platforms/social/xiaohongshu/comments/login',headers=_csrf(client)).status_code==403
    assert client.post(path,json=OPTION).status_code==403
    assert client.post(path,json={**OPTION,'pin':True},headers=_csrf(client)).status_code==409
    result=client.post(path,json=OPTION,headers=_csrf(client))
    assert result.status_code==202 and result.json()['first_comment']['status']=='WAITING'
    assert len(db.list(kind='UPLOAD'))==1


@pytest.mark.parametrize('mode',['ok','slow_page','wrong_uid','cancel','verification_cancel'])
def test_login_worker_saves_only_matching_sessions_and_closes_browser(xhs_env,monkeypatch,mode):
    from core.social import xiaohongshu_comments as implementation
    from scripts import douyin_worker
    db=manager.store('xiaohongshu');job=db.create('LOGIN',OWNER,{'purpose':'comments'})
    db.update(job['id'],status='RUNNING');job=db.get(job['id'])
    folder=manager.directory('xiaohongshu')/'jobs'/job['id'];folder.mkdir(parents=True)
    storage=manager.credential(manager.directory('xiaohongshu'),OWNER)
    original=storage.read_text();callbacks={};sleep=asyncio.sleep
    class Page:
        def on(self,name,callback):callbacks[name]=callback
        async def goto(self,*args,**kwargs):
            if mode=='cancel':db.update(job['id'],status='CANCELLED')
            response=SimpleNamespace(status=200,url=f'https://{API}{SELF}',json=AsyncMock(return_value={
                'code':0,'success':True,'data':{'user_id':OTHER if mode=='wrong_uid' else OWNER,'guest':mode=='verification_cancel'}}))
            callbacks['response'](response)
            await sleep(0)
            if mode=='slow_page':raise TimeoutError()
        def locator(self,*args):return SimpleNamespace(count=AsyncMock(return_value=0))
        def get_by_text(self,*args,**kwargs):return SimpleNamespace(is_visible=AsyncMock(return_value=mode=='verification_cancel'))
    async def save(**kwargs):private_json(__import__('pathlib').Path(kwargs['path']),{'cookies':[],'origins':[]})
    context=SimpleNamespace(new_page=AsyncMock(return_value=Page()),route=AsyncMock(),storage_state=save)
    browser=SimpleNamespace(new_context=AsyncMock(return_value=context),close=AsyncMock())
    monkeypatch.setattr(douyin_worker,'launch',AsyncMock(return_value=browser))
    monkeypatch.setattr(implementation.XiaohongshuAdapter,'identity',AsyncMock(return_value={'uid':OWNER,'name':'test'}))
    if mode=='verification_cancel':
        async def cancel_after_prompt(_):
            assert '本人短信验证' in db.get(job['id'])['phase']
            db.update(job['id'],status='CANCELLED')
            await sleep(0)
        monkeypatch.setattr(implementation.asyncio,'sleep',cancel_after_prompt)
    if mode=='wrong_uid':
        with pytest.raises(AdapterError):asyncio.run(implementation.login_comments(db,job,None,folder,storage))
    else:asyncio.run(implementation.login_comments(db,job,None,folder,storage))
    assert browser.close.await_count==1
    assert storage.read_text()==original  # Only settle may replace the stable credential.
    if mode in ('ok','slow_page'):
        assert db.get(job['id'])['status']=='DONE' and db.get(job['id'])['result']['comments_verified'] is True
        assert (folder/'storage.json').stat().st_mode & 0o777==0o600
    else:assert not (folder/'storage.json').exists()
