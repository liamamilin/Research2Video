"""A renewed QR cannot adopt a cancelled login's account snapshot."""
import asyncio
from pathlib import Path
from types import SimpleNamespace

from core.social.store import SocialStore
from scripts import social_publish_worker as worker
from tests.test_social_publishing import social_env
from web.services import social_publishing as manager


def test_refresh_replaces_active_qr_without_touching_uploads(social_env):
    first=manager.start_login('kuaishou')
    assert manager.start_login('kuaishou')['id']==first['id']
    second=manager.start_login('kuaishou',True)
    assert second['id']!=first['id']
    assert manager.store('kuaishou').get(first['id'])['status']=='CANCELLED'
    assert manager.login_status('kuaishou',first['id'])['connected'] is False
    assert manager.store('kuaishou').list(kind='UPLOAD')==[]


def test_cancel_during_identity_does_not_save_an_old_login(tmp_path,monkeypatch):
    db=SocialStore(str(tmp_path),'kuaishou');job=db.create('LOGIN');job=db.claim()
    folder=tmp_path/'jobs'/job['id'];folder.mkdir(parents=True)
    calls={'saved':0,'closed':0}

    class Browser:
        async def new_context(self,**kwargs):
            assert kwargs['service_workers']=='block'
            return self
        async def storage_state(self,**kwargs): calls['saved']+=1
        async def close(self): calls['closed']+=1

    class Provider:
        async def prepare_login(self,context): return object()
        async def capture_qr(self,*args): return False
        async def identity(self,context):
            db.update(job['id'],status='CANCELLED')
            return {'uid':'123','name':'旧扫码账号'}

    async def launch(*args,**kwargs): return Browser()
    monkeypatch.setattr(worker,'launch',launch)
    asyncio.run(worker.login(db,job,Provider(),None,folder))
    assert db.get(job['id'])['status']=='CANCELLED'
    assert calls=={'saved':0,'closed':1}
    assert not (folder/'storage.json').exists()
