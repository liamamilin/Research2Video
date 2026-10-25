"""Use Kuaishou's own author-comment composer, with one durable guarded POST.

Source: official articleComment.5a0e74df.js and 6551.61ae1458.js, inspected
2026-10-25. Signatures stay in the original creator UI; no external signer.
"""
from __future__ import annotations

import asyncio
from contextlib import suppress
from pathlib import Path

from core.social.adapter import AdapterError
from core.social.adapters.kuaishou import KuaishouAdapter, _provider_url, _uid, _work_id
from core.social.comments import comment_receipt, matching_comments
from core.social.verification import readonly_request

HOME = '/rest/cp/creator/comment/home'
LIST = '/rest/cp/creator/comment/commentList'
ADD = '/rest/cp/creator/comment/add'
READS = (HOME, LIST, '/rest/cp/creator/comment/photoList', '/rest/cp/creator/comment/commentSwitch')


def allowed_read(method: str, url: str, body=None) -> bool:
    return readonly_request(method, url, body) or (method == 'POST' and any(
        _provider_url(url, 'cp.kuaishou.com', path) for path in READS))


def exact_comment_request(body: object, work: str, text: str) -> bool:
    return (isinstance(body, dict) and body.get('photoId') == work and body.get('content') == text
            and body.get('replyToCommentId') in (None, 0, '') and body.get('replyTo') in (None, 0, ''))


class KuaishouComments:
    """One short-lived creator session. Unrelated mutations are always denied."""

    def __init__(self, pw, storage: Path, owner: str, work: str):
        self.pw, self.storage = pw, storage
        self.owner, self.work = _uid(owner), _work_id(work)
        self.browser = None
        self.page = None
        self.tasks = []
        self.listing = None
        self.home_matches = False
        self.armed = False
        self.attempted = False
        self.before_send = None
        self.save_receipt = None
        self.text = ''
        self.result = None

    async def open(self):
        self.browser = await self.pw.chromium.launch(headless=True)
        self.context = await self.browser.new_context(storage_state=str(self.storage), service_workers='block')
        await self.context.route('**/*', self.guard)
        owner = await KuaishouAdapter().identity(self.context)
        if owner['uid'] != self.owner:
            raise AdapterError('快手登录账号与原投稿账号不一致，首评未发送。')
        self.page = await self.context.new_page()
        self.page.on('response', lambda response: self.tasks.append(asyncio.create_task(self.observe(response))))
        await self.load()

    async def load(self):
        self.listing = None; self.home_matches = False
        await self.page.goto(f'https://cp.kuaishou.com/article/comment?photoId={self.work}',
                             wait_until='domcontentloaded', timeout=30000)
        for _ in range(30):
            if self.listing is not None and self.home_matches:
                return
            await self.page.wait_for_timeout(500)
        raise AdapterError('快手评论页未确认当前作品，首评未重复发送。')

    async def observe(self, response):
        if response.status != 200:
            return
        if _provider_url(response.url, 'cp.kuaishou.com', HOME):
            with suppress(Exception):
                data = await response.json()
                self.home_matches = (type(data.get('result')) is int and data['result'] == 1
                                     and data['data']['photo']['photoId'] == self.work)
        elif _provider_url(response.url, 'cp.kuaishou.com', LIST):
            with suppress(Exception):
                if response.request.post_data_json.get('photoId') == self.work:
                    data = await response.json()
                    # Validate even an empty list, so auth failures cannot pass the send preflight.
                    matching_comments(data, self.owner, self.work, '')
                    self.listing = data

    def matches(self, text: str, target: str = '') -> list[dict]:
        return matching_comments(self.listing, self.owner, self.work, text, target)

    async def guard(self, route):
        request = route.request
        try:
            body = request.post_data_json if request.method == 'POST' else None
        except Exception:
            body = None
        if request.method == 'POST' and _provider_url(request.url, 'cp.kuaishou.com', ADD):
            if not self.armed or self.attempted or not self.home_matches or not exact_comment_request(body, self.work, self.text):
                return await route.abort()
            self.attempted = True
            response = None
            try:
                # Must persist before any network transmission, and save the ID before UI acknowledgement.
                self.before_send()
                response = await route.fetch(timeout=30000, max_redirects=0, max_retries=0)
                if response.status != 200:
                    raise AdapterError(f'快手评论返回 HTTP {response.status}，结果待核对，不自动重发。')
                receipt = comment_receipt(await response.json())
                self.save_receipt(receipt)
                if not self.result.done():
                    self.result.set_result(receipt)
            except Exception as exc:
                if not self.result.done():
                    self.result.set_exception(exc)
            finally:
                with suppress(Exception):
                    if response is not None:
                        await route.fulfill(response=response)
                    else:
                        await route.abort()
            return
        if allowed_read(request.method, request.url, body):
            return await route.continue_()
        await route.abort()

    async def send(self, text: str, before_send, save_receipt) -> dict:
        self.text, self.before_send, self.save_receipt = text, before_send, save_receipt
        # These selectors are from the reviewed official author composer, never a reply box.
        editor = self.page.locator('.author-comment-input__row__input[contenteditable="true"]')
        button = self.page.locator('.author-comment-input__row__btn')
        if await editor.count() != 1 or await button.count() != 1:
            raise AdapterError('快手作者评论入口已变化，尚未发送，请核对创作中心。')
        await editor.wait_for(state='visible', timeout=10000)
        # Kuaishou's plain-text serializer concatenates text nodes, ignoring
        # BR separators. Keep user line breaks in a text node, never HTML.
        await editor.evaluate("(node, value) => { node.textContent = value; node.dispatchEvent(new InputEvent('input', {bubbles: true, inputType: 'insertText', data: value})); }", text)
        self.result = asyncio.get_running_loop().create_future()
        self.armed = True
        try:
            await button.click(timeout=10000)
            return await asyncio.wait_for(asyncio.shield(self.result), timeout=40)
        finally:
            self.armed = False
            if self.result.done() and not self.result.cancelled():
                self.result.exception()

    async def close(self):
        if self.result is not None:
            if not self.result.done():
                self.result.cancel()
            elif not self.result.cancelled():
                self.result.exception()
        for task in self.tasks:
            if not task.done():
                task.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)
        if self.browser:
            await self.browser.close()
