"""Xiaohongshu author comments through the platform's signed browser UI.

Sources: xpzouying/xiaohongshu-mcp/xiaohongshu/comment_feed.go;
AiToEarn's Xiaohongshu commentPost/list DTOs; official pc-web
index.a9f61821.js and Login.e4afc82b.js inspected 2026-10-25.
No signing service, copied cross-site tokens or upstream retries are used.
"""
from __future__ import annotations

import asyncio
from contextlib import suppress
from pathlib import Path
import re
from urllib.parse import parse_qs, urlsplit

from core.social.adapter import AdapterError
from core.social.adapters.xiaohongshu import IDENTIFIER, XiaohongshuAdapter, _is_endpoint
from core.social.verification import readonly_request

WEB = 'https://www.xiaohongshu.com'
API = 'edith.xiaohongshu.com'
SELF = '/api/sns/web/v2/user/me'
ADD = '/api/sns/web/v1/comment/post'
FEED = '/api/sns/web/v1/feed'
LISTS = ('/api/sns/web/v2/comment/page', '/api/sns/web/v2/comment/pag')


def success(data) -> bool:
    return (isinstance(data, dict) and type(data.get('code')) is int and data['code'] == 0
            and data.get('success') is True and isinstance(data.get('data'), dict))


def web_profile(data) -> str:
    value = data.get('data', {}).get('user_id') if success(data) else None
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value) or data['data'].get('guest') is not False:
        raise AdapterError('小红书主站评论登录尚未连接或已过期，请先连接评论登录。')
    return value


def normalized(value):
    return value.replace('\r\n', '\n').strip() if isinstance(value, str) else None


def root_matches(row, owner: str, work: str, text: str, *, scoped: bool = False) -> bool:
    if not isinstance(row, dict) or not isinstance(row.get('id'), str) or not IDENTIFIER.fullmatch(row['id']):
        return False
    author = row.get('user_info')
    return (isinstance(author, dict) and author.get('user_id') == owner
            and (row.get('note_id') == work or scoped and row.get('note_id') is None)
            and row.get('target_comment') in (None, {})
            and normalized(row.get('content')) == normalized(text))


def comment_receipt(data, owner: str, work: str, text: str) -> dict:
    if (isinstance(data, dict) and type(data.get('code')) is int and data['code'] != 0
            and data.get('success') is False):
        return {'accepted':False,'code':data['code']}
    row = data.get('data', {}).get('comment') if success(data) else None
    if not root_matches(row, owner, work, text):
        raise AdapterError('小红书评论回执无法确认账号、作品和原文，结果待核对，不会重发。')
    return {'accepted':True,'comment_id':row['id']}


def matching_comments(data, owner: str, work: str, text: str, target: str = '') -> list[dict]:
    # The caller binds this response to a GET with the exact note_id, and
    # verifies the detail's author. Some list versions omit per-row note_id.
    if not success(data) or not isinstance(data['data'].get('comments'), list):
        raise AdapterError('小红书评论列表暂不可用，将只读重查，不会重发。')
    values = {row['id']:{'comment_id':row['id'],'read_back':True}
              for row in data['data']['comments'] if root_matches(row,owner,work,text,scoped=True)
              and (not target or row['id']==target)}
    return list(values.values())


def exact_comment_request(body, work: str, text: str) -> bool:
    return (isinstance(body, dict) and body.get('note_id') == work and body.get('content') == text
            and body.get('target_comment_id') in (None, '') and body.get('at_users', []) == []
            and body.get('image_ids', []) == [])


def allowed_read(method: str, url: str, body=None, work: str = '') -> bool:
    if readonly_request(method, url, body):
        return True
    # Official pc-web getConfig POST returns configMap (no user mutation).
    if method == 'POST' and _is_endpoint(url,API,'/api/sns/web/v1/config'):
        return True
    return (method == 'POST' and _is_endpoint(url, API, FEED) and isinstance(body, dict)
            and bool(work) and body.get('source_note_id') == work)


def allowed_login(method: str, url: str, body=None) -> bool:
    """Login may create/confirm its QR session, never publish or comment."""
    if readonly_request(method,url,body):
        return True
    if method=='POST' and _is_endpoint(url,API,'/api/sns/web/v1/config'):
        return True
    # Current official pc-web WEB_LOGIN_QRCODE_USERINFO polls this separate
    # endpoint with qrId/code. It only reads the QR session's confirmation.
    if method=='POST' and _is_endpoint(url,API,'/api/qrcode/userinfo'):
        return (isinstance(body,dict) and set(body)=={'qrId','code'}
                and all(isinstance(body[k],str) and body[k] for k in ('qrId','code')))
    try:
        path=urlsplit(url).path
    except (ValueError,TypeError):
        return False
    return (method=='POST' and _is_endpoint(url,API,path)
            and re.fullmatch(r'/api/sns/web/v[12]/login/(?:qrcode/(?:create|status|userinfo|activate)|activate)',path) is not None)


class XiaohongshuComments:
    def __init__(self, pw, storage: Path, owner: str, work: str):
        if not isinstance(owner,str) or not IDENTIFIER.fullmatch(owner) or not isinstance(work,str) or not IDENTIFIER.fullmatch(work):
            raise AdapterError('小红书首评账号或作品 ID 无效。')
        self.pw, self.storage, self.owner, self.work = pw, storage, owner, work
        self.browser = self.page = self.result = None
        self.tasks = []; self.listings = []
        self.web_uid = ''; self.home_matches = False
        self.armed = self.attempted = False
        self.text = ''; self.before_send = self.save_receipt = None
        self.detail_url = ''

    async def open(self):
        self.browser = await self.pw.chromium.launch(headless=True)
        self.context = await self.browser.new_context(storage_state=str(self.storage), service_workers='block')
        await self.context.route('**/*', self.guard)
        if (await XiaohongshuAdapter().identity(self.context))['uid'] != self.owner:
            raise AdapterError('小红书创作中心账号与原投稿账号不一致，首评未发送。')
        self.page = await self.context.new_page()
        self.page.on('response',lambda response:self.tasks.append(asyncio.create_task(self.observe(response))))
        # Own profile provides the platform-generated detail link. Its access
        # token stays in this browser/session and is never returned or saved.
        await self.page.goto(f'{WEB}/user/profile/{self.owner}',wait_until='domcontentloaded',timeout=30000)
        for _ in range(30):
            if self.web_uid:
                break
            await self.page.wait_for_timeout(500)
        if self.web_uid != self.owner:
            raise AdapterError('小红书评论登录未连接或账号不一致，请连接原账号的评论登录。')
        links = await self.page.locator(f'a[href*="/explore/{self.work}"]').evaluate_all('(nodes) => nodes.map(node => node.href)')
        for link in links:
            if _is_endpoint(link,'www.xiaohongshu.com',f'/explore/{self.work}'):
                self.detail_url = link; break
        self.detail_url = self.detail_url or f'{WEB}/explore/{self.work}'
        await self.load()

    async def load(self):
        self.listings = []; self.home_matches = False
        await self.page.goto(self.detail_url,wait_until='domcontentloaded',timeout=30000)
        for _ in range(30):
            if not self.home_matches:
                # SSR notes need not emit a feed XHR. Read only the exact note
                # and author from the same state used by the source frontend.
                with suppress(Exception):
                    proof=await self.page.evaluate('(id) => { const n=window.__INITIAL_STATE__?.note?.noteDetailMap?.[id]?.note; return n ? {id:n.noteId, uid:n.user?.userId} : null; }',self.work)
                    self.home_matches=isinstance(proof,dict) and proof.get('id')==self.work and proof.get('uid')==self.owner
            if self.home_matches and self.listings:
                return
            await self.page.wait_for_timeout(500)
        raise AdapterError('小红书尚未返回准确作品及评论列表，请检查评论登录或作品访问权限；没有重发。')

    async def observe(self, response):
        if response.status != 200:
            return
        if _is_endpoint(response.url,API,SELF):
            with suppress(AdapterError,ValueError):
                self.web_uid=web_profile(await response.json())
        elif response.request.method=='POST' and _is_endpoint(response.url,API,FEED):
            with suppress(Exception):
                if response.request.post_data_json.get('source_note_id') != self.work:
                    return
                data=await response.json()
                if success(data):
                    for item in data['data'].get('items',[]):
                        note=item.get('note_card',{})
                        if note.get('note_id')==self.work and note.get('user',{}).get('user_id')==self.owner:
                            self.home_matches=True
        elif response.request.method=='GET' and any(_is_endpoint(response.url,API,path) for path in LISTS):
            with suppress(Exception):
                if parse_qs(urlsplit(response.url).query).get('note_id') != [self.work]:
                    return
                data=await response.json();matching_comments(data,self.owner,self.work,'')
                self.listings.append(data)

    def matches(self, text: str, target: str = '') -> list[dict]:
        found={row['comment_id']:row for data in self.listings
               for row in matching_comments(data,self.owner,self.work,text,target)}
        return list(found.values())

    async def guard(self, route):
        request=route.request
        try:body=request.post_data_json if request.method=='POST' else None
        except Exception:body=None
        if request.method=='POST' and _is_endpoint(request.url,API,ADD):
            if not self.armed or self.attempted or not self.home_matches or self.web_uid!=self.owner or not exact_comment_request(body,self.work,self.text):
                return await route.abort()
            self.attempted=True;response=None
            try:
                self.before_send()
                response=await route.fetch(timeout=30000,max_redirects=0,max_retries=0)
                if response.status!=200:
                    raise AdapterError(f'小红书评论返回 HTTP {response.status}，结果待核对，不重发。')
                receipt=comment_receipt(await response.json(),self.owner,self.work,self.text)
                self.save_receipt(receipt)
                if not self.result.done():self.result.set_result(receipt)
            except Exception as exc:
                if not self.result.done():self.result.set_exception(exc)
            finally:
                with suppress(Exception):
                    if response is not None:await route.fulfill(response=response)
                    else:await route.abort()
            return
        if allowed_read(request.method,request.url,body,self.work):
            return await route.continue_()
        await route.abort()

    async def send(self, text: str, before_send, save_receipt):
        self.text,self.before_send,self.save_receipt=text,before_send,save_receipt
        editor=self.page.locator('div.input-box div.content-edit p.content-input')
        if await editor.count()==0:
            trigger=self.page.locator('div.input-box div.content-edit span')
            if await trigger.count()!=1:
                raise AdapterError('小红书作者评论输入框尚未就绪，首评未发送。')
            await trigger.click(timeout=10000)
        button=self.page.locator('div.bottom button.submit')
        if await editor.count()!=1 or await button.count()!=1:
            raise AdapterError('小红书评论入口已变化，首评未发送。')
        await editor.wait_for(state='visible',timeout=10000)
        await editor.fill(text,timeout=10000)
        self.result=asyncio.get_running_loop().create_future();self.armed=True
        try:
            await button.click(timeout=10000)
            return await asyncio.wait_for(asyncio.shield(self.result),40)
        finally:
            self.armed=False
            if self.result.done() and not self.result.cancelled():self.result.exception()

    async def close(self):
        if self.result is not None:
            if not self.result.done():self.result.cancel()
            elif not self.result.cancelled():self.result.exception()
        for task in self.tasks:
            if not task.done():task.cancel()
        if self.tasks:await asyncio.gather(*self.tasks,return_exceptions=True)
        if self.browser:await self.browser.close()


async def login_comments(db, job, pw, folder: Path, storage: Path):
    """Extend the original browser session only after both provider UIDs match."""
    from scripts.douyin_worker import launch
    browser=await launch(pw,headless=False)
    tasks=[];observed={}
    try:
        context=await browser.new_context(storage_state=str(storage),service_workers='block')
        async def guard(route):
            request=route.request
            try:body=request.post_data_json if request.method=='POST' else None
            except Exception:body=None
            if allowed_login(request.method,request.url,body):await route.continue_()
            else:await route.abort()
        await context.route('**/*',guard)
        page=await context.new_page()
        async def read(response):
            if response.status==200 and _is_endpoint(response.url,API,SELF):
                with suppress(Exception):observed['uid']=web_profile(await response.json())
            elif response.status==200 and _is_endpoint(response.url,API,'/api/qrcode/userinfo'):
                with suppress(Exception):
                    data=await response.json()
                    if type(data.get('codeStatus')) is int and data['codeStatus'] in (1,2):
                        db.update(job['id'],phase='手机已确认，正在核对主站账号' if data['codeStatus']==2 else '已扫码，请在手机确认登录')
        page.on('response',lambda r:tasks.append(asyncio.create_task(read(r))))
        db.update(job['id'],phase='正在准备小红书主站评论登录二维码')
        try:
            await page.goto(f'{WEB}/explore',wait_until='domcontentloaded',timeout=30000)
        except Exception as exc:
            # A slow SPA can already render login while its load event stalls.
            # Do not navigate again: that would invalidate the displayed QR.
            if type(exc).__name__ != 'TimeoutError':
                raise
        deadline=asyncio.get_running_loop().time()+330;login_opened=False
        verification_seen=False;qr_presented=False
        while asyncio.get_running_loop().time()<deadline:
            if db.get(job['id'])['status']!='RUNNING':return
            with suppress(Exception):
                if await page.get_by_text('短信验证码验证',exact=True).is_visible():
                    if not verification_seen:
                        verification_seen=True
                        deadline=max(deadline,asyncio.get_running_loop().time()+900)
                    db.update(job['id'],phase='请在小红书独立窗口完成本人短信验证')
            qr=page.locator('img.qrcode-img')
            with suppress(Exception):
                if not verification_seen and await qr.count()==1 and await qr.is_visible():
                    await qr.screenshot(path=str(folder/'qrcode.png'),timeout=3000)
                    (folder/'qrcode.png').chmod(0o600)
                    db.update(job['id'],phase='请用小红书原账号扫码，连接评论登录')
                    if not qr_presented:
                        qr_presented=True
                        # The official page suspends its QR polling while
                        # document.hidden; foreground once, never every poll.
                        await page.bring_to_front()
                elif not verification_seen and not login_opened:
                    # Official pc-web sidebar renders #login-btn for guests;
                    # recent versions no longer always auto-open the dialog.
                    trigger=page.locator('#login-btn')
                    if await trigger.count()==1 and await trigger.is_visible():
                        await trigger.click(timeout=5000)
                        login_opened=True
            if observed.get('uid'):
                if observed['uid']!=job['uid']:
                    raise AdapterError('扫码账号与原投稿账号不一致，未保存；请使用原账号重新连接评论登录。')
                owner=await XiaohongshuAdapter().identity(context)
                if owner['uid']!=job['uid']:raise AdapterError('创作中心账号已变化，未覆盖原登录态。')
                path=folder/'storage.json'
                await context.storage_state(path=str(path));path.chmod(0o600)
                if not db.update(job['id'],expected='RUNNING',status='DONE',phase='评论登录账号已核对',result={**owner,'comments_verified':True},error=''):
                    path.unlink(missing_ok=True)
                return
            await asyncio.sleep(2)
        raise AdapterError('小红书评论登录等待本人验证超时，未更换原投稿账号。' if verification_seen
                           else '小红书评论登录二维码已过期，请重新获取；未更换原投稿账号。')
    finally:
        for task in tasks:
            if not task.done():task.cancel()
        if tasks:await asyncio.gather(*tasks,return_exceptions=True)
        await browser.close()
