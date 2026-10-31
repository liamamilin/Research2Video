"""First-comment behavior without platform writes, real accounts or persistent files."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.social.adapter import AdapterError
from core.social.comments import clean_first_comment, comment_receipt, matching_comments
from core.social.kuaishou_comments import ADD, KuaishouComments, exact_comment_request, allowed_read
from core.social.store import SocialStore
from scripts import social_comment_worker as worker
from tests.test_social_publishing import social_env, enqueue, manager, OWNER, WORK, PAYLOAD
from tests.test_platforms import render
from tests.test_video_scripts import _login, _csrf

OPTION = {'enabled':True,'text':'补充资料\n欢迎交流','pin':False}


@pytest.mark.parametrize('option', [None, [], {'enabled':1}, {'pin':True}, {'enabled':True},
    {'text':'x'*251}, {'text':'😀'*126}, {'text':'\u0000'}, {'text':'\ud800'}, {'unknown':True}])
def test_bad_config(option):
    with pytest.raises(AdapterError):
        clean_first_comment('kuaishou',option)


def test_config_and_other_platform():
    assert clean_first_comment('kuaishou', OPTION) == OPTION
    assert clean_first_comment('kuaishou', {}) == {'enabled':False,'text':'','pin':False}
    assert clean_first_comment('xiaohongshu', OPTION) == OPTION
    with pytest.raises(AdapterError):
        clean_first_comment('weixin_channels', OPTION)


@pytest.mark.parametrize('value', [{}, {'result':True}, {'result':1,'data':{}}, {'result':1,'data':{'commentId':1.5}}, {'result':1,'success':False,'data':{'commentId':123}}])
def test_unknown_receipt_is_not_success(value):
    with pytest.raises(AdapterError):
        comment_receipt(value)


def test_receipts_keep_integer_precision_and_no_raw_secrets():
    assert comment_receipt({'result':1,'data':{'commentId':90071992547409931234},'token':'secret'}) == {'accepted':True,'comment_id':'90071992547409931234'}
    assert comment_receipt({'result':9,'message':'private-token'}) == {'accepted':False,'code':9}


def listed(**changes):
    return {'result':1,'data':{'list':[{'commentId':123456789,'authorId':OWNER['uid'],'photoId':WORK,
           'replyToCommentId':0,'content':OPTION['text'],**changes}]}}


@pytest.mark.parametrize('changes',[{'photoId':'another-work'},{'authorId':'1234'},{'replyToCommentId':123},
    {'replyToCommentId':False},{'content':'other'},{'commentId':1.2}])
def test_readback_does_not_match_wrong_work_author_reply_content(changes):
    assert matching_comments(listed(**changes),OWNER['uid'],WORK,OPTION['text']) == []


def test_readback_matches_exact_author_root_comment():
    assert matching_comments(listed(),OWNER['uid'],WORK,OPTION['text']) == [{'comment_id':'123456789','read_back':True}]
    assert matching_comments(listed(),OWNER['uid'],WORK,OPTION['text'],'987654321') == []


def test_template_snapshot_not_applied_to_old_uploads(social_env):
    parent=enqueue('kuaishou',render()); db=manager.store('kuaishou')
    manager.save_first_comment_template('kuaishou',OPTION)
    assert db.comments(OWNER['uid']) == {}
    manager.schedule_comments('kuaishou',db)
    assert db.comments(OWNER['uid']) == {}
    parent2=enqueue('kuaishou',render())
    child=db.comments(OWNER['uid'])[parent2['id']]
    assert child['payload']=={'text':OPTION['text']} and child['status']=='WAITING'
    manager.save_first_comment_template('kuaishou',{**OPTION,'text':'changed'})
    assert db.get(parent2['id'])['payload']['first_comment']==OPTION
    assert db.comment_settings('another')['enabled'] is False
    assert db.get(parent['id'])['payload']['first_comment']['enabled'] is False


@pytest.mark.parametrize('options,enabled', [({'text': '补充资料'}, True),
                                           ({'text': '补充资料', 'enabled': False}, False)])
def test_text_only_upload_options_create_comment_task_unless_explicitly_disabled(social_env, options, enabled):
    parent = enqueue('kuaishou', render(), first_comment=options)
    db = manager.store('kuaishou')
    assert db.get(parent['id'])['payload']['first_comment']['enabled'] is enabled
    assert bool(db.comments(OWNER['uid']).get(parent['id'])) is enabled


def test_add_existing_comment_api_does_not_insert_an_unrequested_disabled_flag(social_env, client):
    parent = enqueue('kuaishou', render())
    db = manager.store('kuaishou')
    db.update(parent['id'], status='PUBLISHED', work_id=WORK)
    _login(client)
    response = client.post('/api/platforms/social/kuaishou/uploads/' + parent['id'] + '/first-comment',
                           json={'text': '补充资料'}, headers=_csrf(client))
    assert response.status_code == 202, response.text
    assert response.json()['first_comment']['text'] == '补充资料'


def test_publication_gate_cancel_and_unique_target(social_env):
    parent=enqueue('kuaishou',render(),first_comment=OPTION); db=manager.store('kuaishou')
    child=db.comments(OWNER['uid'])[parent['id']]
    for status in ('UNKNOWN','SUBMITTED'):
        db.update(parent['id'],status=status,work_id=WORK)
        manager.schedule_comments('kuaishou',db)
        assert db.get(child['id'])['status']=='WAITING'
    db.update(parent['id'],status='PUBLISHED')
    manager.schedule_comments('kuaishou',db)
    assert db.get(child['id'])['status']=='QUEUED' and db.get(child['id'])['work_id']==WORK
    manager.first_comment_action('kuaishou',parent['id'],'cancel')
    manager.schedule_comments('kuaishou',db)
    assert db.get(child['id'])['status']=='CANCELLED'
    manager.first_comment_action('kuaishou',parent['id'],'retry')
    manager.schedule_comments('kuaishou',db)
    assert db.get(child['id'])['status']=='QUEUED'
    second=enqueue('kuaishou',render(),first_comment=OPTION)
    db.update(second['id'],status='PUBLISHED',work_id=WORK)
    manager.schedule_comments('kuaishou',db)
    duplicate=db.comments(OWNER['uid'])[second['id']]
    assert duplicate['status']=='FAILED' and not duplicate['result']['confirmed_not_submitted']
    assert db.get(parent['id'])['status']=='PUBLISHED'


def test_concurrent_add_is_once_and_unknown_never_retried(social_env):
    parent=enqueue('kuaishou',render()); db=manager.store('kuaishou')
    db.update(parent['id'],status='PUBLISHED',work_id=WORK)
    with ThreadPoolExecutor(max_workers=6) as pool:
        rows=list(pool.map(lambda _:manager.add_first_comment('kuaishou',parent['id'],OPTION),range(10)))
    assert len({row['first_comment']['id'] for row in rows})==1
    child=db.comments(OWNER['uid'])[parent['id']]
    with pytest.raises(AdapterError):
        manager.add_first_comment('kuaishou',parent['id'],{**OPTION,'text':'changed'})
    db.update(child['id'],status='RUNNING',heartbeat=time.time()-200,result={'submission_started':True})
    db.recover()
    assert db.get(child['id'])['status']=='UNKNOWN'
    with pytest.raises(AdapterError):
        manager.first_comment_action('kuaishou',parent['id'],'retry')
    for _ in range(3):
        manager.first_comment_action('kuaishou',parent['id'],'check')
    assert len(db.list(kind='COMMENT_CHECK'))==1
    assert len(db.list(kind='UPLOAD'))==1


def prepared(social_env):
    parent=enqueue('kuaishou',render(),first_comment=OPTION); db=manager.store('kuaishou')
    db.update(parent['id'],status='PUBLISHED',work_id=WORK)
    manager.schedule_comments('kuaishou',db)
    child=db.comments(OWNER['uid'])[parent['id']]
    db.update(child['id'],status='RUNNING')
    return db,db.get(child['id']),parent


class FakeSession:
    outcome='ok'
    sent=0
    def __init__(self,*args): pass
    async def open(self): pass
    async def close(self): pass
    async def load(self): pass
    def matches(self,text,target=''):
        return [{'comment_id':'123456789','read_back':True}] if target else []
    async def send(self,text,before,save):
        before(); FakeSession.sent+=1
        if self.outcome=='timeout':raise TimeoutError()
        receipt={'accepted':True,'comment_id':'123456789'}
        save(receipt)
        if self.outcome=='after-receipt':raise TimeoutError()
        return receipt


@pytest.mark.parametrize('outcome',['ok','timeout','after-receipt'])
def test_send_crash_receipt_and_no_replay(social_env,monkeypatch,outcome):
    db,child,parent=prepared(social_env)
    FakeSession.outcome=outcome;FakeSession.sent=0
    monkeypatch.setattr(worker,'KuaishouComments',FakeSession)
    provider=SimpleNamespace(inspect_work=AsyncMock(return_value={'public':True,'owned':True,'uid':OWNER['uid'],'work_id':WORK,'evidence':'kuaishou_creator_public'}))
    asyncio.run(worker.send_first_comment(db,child,provider,None,None))
    current=db.get(child['id'])
    assert current['status']==('UNKNOWN' if outcome=='timeout' else 'SENT')
    assert not current['result'].get('confirmed_not_submitted')
    assert db.get(parent['id'])['status']=='PUBLISHED'
    assert FakeSession.sent==1
    db.update(child['id'],status='RUNNING')
    asyncio.run(worker.send_first_comment(db,db.get(child['id']),provider,None,None))
    assert FakeSession.sent==1


@pytest.mark.parametrize('reason',['private','account','parent'])
def test_preflight_denies_without_sending(social_env,monkeypatch,reason):
    db,child,parent=prepared(social_env)
    monkeypatch.setattr(worker,'KuaishouComments',lambda *args:pytest.fail('must not open composer'))
    proof={'public':False,'owned':True,'uid':OWNER['uid'],'work_id':WORK,'evidence':'kuaishou_creator_public'}
    if reason=='account':
        (manager.directory('kuaishou')/'account.json').unlink()
    if reason=='parent':db.update(parent['id'],status='SUBMITTED')
    provider=SimpleNamespace(inspect_work=AsyncMock(return_value=proof))
    asyncio.run(worker.send_first_comment(db,child,provider,None,None))
    assert db.get(child['id'])['status']=='FAILED'
    assert db.get(child['id'])['result']['confirmed_not_submitted'] is True


def test_guard_saves_started_and_receipt_before_ui_and_blocks_second_post():
    session=KuaishouComments(None,None,OWNER['uid'],WORK)
    session.armed=True;session.home_matches=True;session.text=OPTION['text']
    events=[]
    session.before_send=lambda:events.append('durable-start')
    session.save_receipt=lambda receipt:events.append('durable-receipt')
    class Route:
        request=SimpleNamespace(method='POST',url='https://cp.kuaishou.com'+ADD,post_data_json={'photoId':WORK,'content':OPTION['text']})
        async def fetch(self,**kw):
            assert events==['durable-start']; assert kw['max_retries']==0 and kw['max_redirects']==0
            return SimpleNamespace(status=200,json=AsyncMock(return_value={'result':1,'data':{'commentId':123456789}}))
        async def fulfill(self,**kw):
            assert events==['durable-start','durable-receipt'];events.append('ui-ack')
        async def abort(self):events.append('abort')
    async def run():
        session.result=asyncio.get_running_loop().create_future()
        await session.guard(Route());await session.guard(Route())
    asyncio.run(run())
    assert events==['durable-start','durable-receipt','ui-ack','abort']


def test_comment_context_allows_reads_only_and_exact_root_body():
    assert allowed_read('POST','https://cp.kuaishou.com/rest/cp/creator/comment/commentList',{})
    for path in (ADD,'/rest/cp/creator/comment/delete','/rest/cp/creator/comment/setTopComment','/rest/cp/works/v2/video/pc/submit'):
        assert not allowed_read('POST','https://cp.kuaishou.com'+path,{})
    for body in ({'photoId':'other','content':OPTION['text']},{'photoId':WORK,'content':'changed'}, {'photoId':WORK,'content':OPTION['text'],'replyToCommentId':123}):
        assert not exact_comment_request(body,WORK,OPTION['text'])


def test_automatic_comment_checks_back_off_without_resending(social_env,monkeypatch):
    db,child,parent=prepared(social_env)
    db.update(child['id'],status='SENT',result={'comment_id':'123456789','submission_started':True,'next_check_at':0})
    for _ in range(3):manager.schedule_comments('kuaishou',db)
    checks=db.list(kind='COMMENT_CHECK')
    assert len(checks)==1 and len(db.list(kind='UPLOAD'))==1
    class ReadOnlySession(FakeSession):
        def matches(self,*args):return []
        async def send(self,*args):pytest.fail('read-only checks must never send')
    monkeypatch.setattr(worker,'KuaishouComments',ReadOnlySession)
    asyncio.run(worker.check_first_comment(db,checks[0],None,None))
    current=db.get(child['id'])
    assert current['status']=='SENT' and current['result']['comment_id']=='123456789'
    assert current['result']['check_attempts']==1 and current['result']['next_check_at']>time.time()
    manager.schedule_comments('kuaishou',db)
    assert len(db.list(kind='COMMENT_CHECK'))==1
    assert db.get(parent['id'])['status']=='PUBLISHED'


def test_api_roles_csrf_and_opt_in(social_env,client):
    parent=enqueue('kuaishou',render()); db=manager.store('kuaishou')
    db.update(parent['id'],status='PUBLISHED',work_id=WORK)
    path=f"/api/platforms/social/kuaishou/uploads/{parent['id']}/first-comment"
    _login(client,'viewer','viewer-pass-123')
    assert client.post(path,json=OPTION,headers=_csrf(client)).status_code==403
    _login(client,'editor')
    assert client.post(path,json=OPTION).status_code==403
    assert client.put('/api/platforms/social/kuaishou/first-comment-template',json=OPTION,headers=_csrf(client)).status_code==403
    result=client.post(path,json=OPTION,headers=_csrf(client))
    assert result.status_code==202 and result.json()['first_comment']['status']=='WAITING'
    assert client.post(path,json={**OPTION,'pin':True},headers=_csrf(client)).status_code==409
    assert len(db.list(kind='UPLOAD'))==1
