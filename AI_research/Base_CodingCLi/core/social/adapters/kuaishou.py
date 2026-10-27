"""Kuaishou account checks and a guarded, single-submit upstream integration.

Sources (pinned; no provider credentials are imported here):
https://github.com/dreammis/social-auto-upload/blob/3847fff01f5434a8a32829833163cd6723cabc59/uploader/ks_uploader/main.py
https://github.com/yikart/AiToEarn/blob/cb894a43b2d23cc1ad67d9e7729b1d86cf9360bd/project/aitoearn-electron/electron/plat/Kwai/index.ts
https://github.com/yikart/AiToEarn/blob/cb894a43b2d23cc1ad67d9e7729b1d86cf9360bd/project/aitoearn-electron/electron/plat/Kwai/kwai.type.ts

The creator submit response can acknowledge acceptance without a public work ID.
Keep it SUBMITTED awaiting the work link, never infer publication or retry it.
The OAuth photo/info endpoint needs separately authorized OAuth credentials;
browser cookies are not a substitute for those credentials.
"""
from __future__ import annotations

import asyncio
from contextlib import suppress
import hashlib
import importlib.util
from pathlib import Path
import re
import sys
from types import ModuleType
from typing import Any
from urllib.parse import urlsplit

from core.social.adapter import AdapterError
from core.social.verification import asset_fingerprints, protect_context, receipt, select_work, timestamp
from core.social.verification import lookup_diagnostics


SOURCE_SHA256 = "fc97d1f83aa0d531284365d2e54a6e043bef420f8b8088b3e90b9ed785326c66"
SUBMIT_PATH = "/rest/cp/works/v2/video/pc/submit"
# Observed after the creator's single 发布 click during the guarded 2026-10-25
# preparation check. The diagnostic denied it before any network transmission.
ISOLATED_SUBMIT_PATH = "/rest/cp/works/v2/video/pc/isolation/submit"
WORKS_PATH = "/rest/cp/works/v2/video/pc/photo/list"
CREATOR_PROFILE_PATH = "/rest/cp/creator/pc/home/infoV2"
CURRENT_ACCOUNT_PATH = "/rest/v2/creator/pc/authority/account/current"
IDENTITY_QUERY = {
    "operationName": "userInfoQuery",
    "variables": {},
    "query": "query userInfoQuery { userInfo { id name userId __typename } }",
}
PUBLIC_CHECK_LIMITATION = "快手浏览器接口的审核与可见性枚举尚未完成核验，请在创作者中心确认公开状态。"
UNKNOWN_RECEIPT = "快手已返回投稿响应，但没有可靠的作品 ID；请到创作者中心核对，系统不会自动重投。"


def _provider_url(url: str, hostname: str, path: str) -> bool:
    try:
        parts = urlsplit(url)
        return (parts.scheme == "https" and parts.hostname == hostname and
                parts.port in (None, 443) and parts.username is None and parts.password is None and
                not parts.fragment and parts.path == path)
    except (ValueError, TypeError):
        return False


def _uid(value: Any) -> str:
    # Python JSON integers are exact; floats/JS-rounded numeric values never qualify.
    if type(value) is int and value > 0:
        value = str(value)
    if not isinstance(value, str) or not re.fullmatch(r"[1-9][0-9]{0,39}", value):
        raise AdapterError("快手没有返回可验证的用户 ID，请重新连接账号。")
    return value


def _work_id(value: Any) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{6,80}", value):
        raise AdapterError("请填写准确的快手作品 ID。")
    return value


def _caption(title: str, description: str) -> str:
    from core.social.publish_text import combined_text
    return combined_text("kuaishou", title, description, [])


def _profile(data: Any) -> dict:
    if not isinstance(data, dict) or data.get("errors"):
        raise AdapterError("快手账号尚未通过验证，请完成扫码或浏览器中的身份验证。")
    outer = data.get("data")
    user = outer.get("userInfo") if isinstance(outer, dict) else None
    if not isinstance(user, dict):
        raise AdapterError("快手账号尚未通过验证，请完成扫码或浏览器中的身份验证。")
    uid = _uid(user.get("userId"))
    name = user.get("name")
    if not isinstance(name, str) or not name.strip() or len(name) > 200:
        raise AdapterError("快手没有返回可验证的账号昵称，请重新连接账号。")
    return {"uid": uid, "name": name.strip()}


def _creator_profile(data: Any) -> dict:
    """Use the creator account response when its login lacks the public-site session."""
    if (not isinstance(data, dict) or type(data.get('result')) is not int
            or data['result'] != 1 or not isinstance(data.get('data'), dict)):
        raise AdapterError('快手创作中心尚未返回已核实的账号。')
    user = data['data']
    uid = _uid(user.get('userId'))
    name = user.get('userName')
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 200:
        raise AdapterError('快手创作中心没有返回可核实的昵称。')
    return {'uid': uid, 'name': name.strip()}


def _current_creator_profile(data: Any) -> dict:
    """Current creator-page response observed during account-only diagnosis."""
    if (not isinstance(data, dict) or type(data.get('result')) is not int or data['result'] != 1
            or not isinstance(data.get('data'), dict) or data['data'].get('logined') is not True):
        raise AdapterError('快手创作中心尚未返回已登录的当前账号。')
    return _creator_profile(data)


def _owned_work(data: Any, owner_id: str, target: str) -> bool:
    if not isinstance(data, dict) or type(data.get("result")) is not int or data["result"] != 1:
        return False
    nested = data.get("data")
    rows = nested.get("list") if isinstance(nested, dict) else None
    if not isinstance(rows, list):
        return False
    for row in rows:
        if not isinstance(row, dict) or row.get("workId") != target:
            continue
        # Do not confuse a pending publishId, a caption match or the current
        # account session with an exact work/author association.
        actual = _uid(row.get('userId') if 'userId' in row else row.get('userIdStr'))
        if actual != owner_id:
            raise AdapterError("这条快手作品不属于当前连接账号，不能回写发布记录。")
        return True
    return False


def work_records(data: Any, owner_id: str, target: str = '') -> list[dict]:
    """Enums verified in the official articleManage.f297ace2.js and live own-work list.

    publishStatus: 1 pending, 2 transcoding, 3 failed, 4 succeeded,
    5 deleted, 6 transcode failed, 8 composing, 9 composing failed,
    10 reviewing, 11 review failed, 15 upload forbidden.
    photoStatus 1 is private, 3 friends-only; only observed normal 0 qualifies.
    """
    if (not isinstance(data, dict) or type(data.get('result')) is not int or data['result'] != 1
            or not isinstance(data.get('data'), dict) or not isinstance(data['data'].get('list'), list)):
        raise AdapterError('快手作品列表暂不可用，将稍后自动核对；不会重新投稿。')
    records = []
    for row in data['data']['list']:
        if not isinstance(row, dict):
            continue
        try:
            work_id = _work_id(row.get('workId'))
            owner = _uid(row.get('userId') if 'userId' in row else row.get('userIdStr'))
        except AdapterError:
            continue
        if owner != owner_id or row.get('photoOwner') is False:
            if target == work_id:
                raise AdapterError('这条快手作品不属于原投稿账号，已停止回写。')
            continue
        status, visibility = row.get('publishStatus'), row.get('photoStatus')
        state = 'unknown'
        if type(status) is int:
            if status in (3, 6, 9, 11, 15):
                state = 'rejected'
            elif status == 10:
                state = 'reviewing'
            elif status in (2, 8):
                state = 'processing'
            elif status == 1:
                state = 'scheduled' if type(row.get('publishType')) is int and row['publishType'] == 2 else 'reviewing'
            elif status == 4 and type(visibility) is int:
                if visibility in (1, 3):
                    state = 'private'
                elif (visibility == 0 and type(row.get('judgementStatus')) is int and row['judgementStatus'] == 1
                      and row.get('photoStatusTags') == []):
                    state = 'published'
        records.append({'work_id': work_id, 'title': str(row.get('title') or '').split('\n')[0],
                        'published_at': timestamp(row.get('uploadTime')), 'duration_seconds': row.get('durationSecond'),
                        'correlations': [*asset_fingerprints(row.get('unPublishCoverKey')), *asset_fingerprints(row.get('publishCoverUrl'))], 'state': state})
    return records


async def _read_more_works(page: Any, records: list[dict], errors: list[Exception]) -> bool:
    """The creator list uses infinite scroll; observe its next signed list request."""
    count = len(records)
    try:
        await page.evaluate("""() => {
          const nodes = [document.scrollingElement, ...document.querySelectorAll('*')];
          for (const node of nodes) {
            if (node && node.scrollHeight > node.clientHeight + 100 &&
                (node === document.scrollingElement || /auto|scroll/.test(getComputedStyle(node).overflowY))) {
              node.scrollTop = node.scrollHeight;
            }
          }
        }""")
        for _ in range(12):
            await page.wait_for_timeout(250)
            if len(records) > count or errors:
                break
    except Exception:
        return False
    return len(records) > count


def _isolated_upstream() -> ModuleType:
    """Load the verified source into private globals; never mutate the shared uploader."""
    try:
        spec = importlib.util.find_spec("uploader.ks_uploader.main")
        source_path = Path(spec.origin)
        source = source_path.read_bytes()
    except (OSError, TypeError, AttributeError, ImportError, ValueError):
        raise AdapterError("快手上传组件缺失，请重新验证固定版本运行环境。") from None
    if hashlib.sha256(source).hexdigest() != SOURCE_SHA256:
        raise AdapterError("快手上传组件与已核验版本不一致，已停止投稿。")
    module = ModuleType("_arec_single_submit_kuaishou")
    module.__file__ = str(source_path)
    try:
        exec(compile(source, str(source_path), "exec"), module.__dict__)
    except Exception:
        raise AdapterError("快手上传组件未能加载，请重新验证固定版本运行环境。") from None
    module.KUAISHOU_PUBLISH_ATTEMPTS = 1

    async def no_page_dump(page: Any, tag: str) -> str:
        # The upstream fallback writes authenticated HTML to the working folder.
        # Preserve a safe error message without retaining account pages.
        return "未保存账号页面；请在创作者中心核对界面"

    module._dump_page_debug = no_page_dump
    return module


async def _select_video_file(page: Any, path: str) -> None:
    """Select a video input by its file type, with a unique semantic-button fallback."""
    inputs = page.locator('input[type="file"]')
    with suppress(Exception):
        # The creator SPA mounts its uploader after account/config requests.
        await inputs.first.wait_for(state='attached', timeout=25000)
    videos = []
    generic = []
    for index in range(await inputs.count()):
        item = inputs.nth(index)
        accept = (await item.get_attribute('accept') or '').lower()
        if 'video' in accept or any(ext in accept for ext in ('.mp4', '.mov', '.webm', '.mkv', '.flv', '.avi')):
            videos.append(item)
        elif not accept:
            generic.append(item)
    if len(videos) == 1:
        await videos[0].set_input_files(path)
        return
    if not videos and len(generic) == 1 and await inputs.count() == 1:
        await generic[0].set_input_files(path)
        return
    if len(videos) > 1:
        raise AdapterError('快手页面存在多个视频上传入口，已停止选择，避免误传。')
    button = page.get_by_role('button', name=re.compile(r'^(?:上传视频|选择视频|选择文件|点击上传|上传文件|本地上传)$'))
    if await button.count() != 1:
        raise AdapterError('未找到唯一的快手视频上传入口，请核对创作中心页面。')
    async with page.expect_file_chooser() as captured:
        await button.click(timeout=10000)
    await (await captured.value).set_files(path)


async def _upload_video(app: Any, module: ModuleType, playwright: Any) -> None:
    """Use the reviewed helper methods while keeping one final publish click under our control."""
    await app.validate_upload_args()
    browser = await playwright.chromium.launch(headless=app.headless)
    context = None
    try:
        context = await browser.new_context(storage_state=app.account_file)
        context = await module.set_init_script(context)
        page = await context.new_page()
        await page.goto(module.KUAISHOU_UPLOAD_URL, wait_until='domcontentloaded', timeout=45000)
        await page.wait_for_url(module.KUAISHOU_UPLOAD_URL_PATTERN, timeout=15000)
        await _select_video_file(page, app.file_path)
        await app.close_guide_overlay(page)
        await module._focus_desc_editor(page)
        await page.keyboard.press('Meta+A' if sys.platform == 'darwin' else 'Control+A')
        await page.keyboard.press('Backspace')
        await page.keyboard.type(app.desc or app.title)
        await page.keyboard.press('Enter')
        for tag in app.tags:
            await page.keyboard.type(f'#{tag} ')
            await page.wait_for_timeout(2000)
        deadline = asyncio.get_running_loop().time() + module.KUAISHOU_UPLOAD_TIMEOUT_SECONDS
        while await page.get_by_text('上传中', exact=False).count():
            if await page.get_by_text('上传失败', exact=False).count():
                raise AdapterError('快手视频素材上传失败，未点击发布；请核对网络和文件格式。')
            if asyncio.get_running_loop().time() > deadline:
                raise AdapterError('快手视频素材上传超时，未点击发布。')
            await page.wait_for_timeout(1000)
        if await page.get_by_text('上传失败', exact=False).count():
            raise AdapterError('快手视频素材上传失败，未点击发布；请核对网络和文件格式。')
        # The first-run guide may mount only after the asset finishes uploading.
        await app.close_guide_overlay(page)
        await app.set_thumbnail(page)
        await app.apply_collection(page)
        await _publish_once(page, module)
        await page.wait_for_url(module.KUAISHOU_MANAGE_URL_PATTERN, timeout=15000)
    finally:
        if context is not None:
            with suppress(Exception):
                await context.close()
        with suppress(Exception):
            await browser.close()


async def _publish_once(page: Any, module: ModuleType) -> None:
    """Click one unambiguous publish control, including the creator's custom control."""
    if await module._click_visible_publish_confirm(page):
        return
    button = page.get_by_role('button', name='发布', exact=True)
    count = await button.count()
    if count > 1:
        raise AdapterError('未找到唯一的快手发布按钮，已停止，不会重复点击。')
    if count == 0:
        # The current creator page uses a custom element for 发布. This exact
        # text selector is also used by the pinned uploader; disambiguate it
        # by visibility rather than ever clicking the first match or retrying.
        matches = page.get_by_text('发布', exact=True)
        visible = [matches.nth(index) for index in range(await matches.count())
                   if await matches.nth(index).is_visible()]
        if len(visible) != 1:
            raise AdapterError('未找到唯一的快手发布按钮，已停止，不会重复点击。')
        button = visible[0]
    await button.click(timeout=15000)
    await page.wait_for_timeout(1000)
    await module._click_visible_publish_confirm(page)


class KuaishouAdapter:
    """Browser account connection; one submission attempt under the worker's guard."""

    key = "kuaishou"
    label = "快手"
    creator_url = "https://cp.kuaishou.com/article/manage/video"
    login_url = (
        "https://passport.kuaishou.com/pc/account/login/?sid=kuaishou.web.cp.api&"
        "callback=https%3A%2F%2Fcp.kuaishou.com%2Frest%2Finfra%2Fsts%3FfollowUrl%3D"
        "https%253A%252F%252Fcp.kuaishou.com%252Farticle%252Fpublish%252Fvideo%26setRootDomain%3Dtrue"
    )
    # Conservative editor limits for this integration; not a claim about every
    # Kuaishou app/client. The full caption (title/description/topics) is checked.
    title_min = 1
    title_limit = 80
    description_limit = 500
    combined_limit = 500
    tag_limit = 3
    supports_upload = True
    supports_public_check = True
    public_check_gap = '发布后自动核对作品归属、审核和公开状态；无法唯一关联时可补充作品链接。'
    upload_note = '确认后在本机投稿，随后自动核对平台状态；确认公开后自动回写文稿发布记录。'

    async def identity(self, context: Any) -> dict:
        """Verify a stable numeric UID and nickname with the provider's user query."""
        try:
            response = await context.request.post(
                "https://www.kuaishou.com/graphql", data=IDENTITY_QUERY,
                headers={"Referer": "https://www.kuaishou.com/", "Content-Type": "application/json"},
                timeout=20000, max_redirects=0, max_retries=0,
            )
            if response.status == 200:
                return _profile(await response.json())
        except Exception:
            pass
        creator_pages = getattr(context, 'pages', [])
        if creator_pages and not any(any(_provider_url(getattr(page, 'url', ''), 'cp.kuaishou.com', path)
                       for path in ('/', '/profile', '/article/manage/video', '/article/publish/video'))
                   for page in creator_pages):
            raise AdapterError('请先在手机确认快手登录，等待进入创作中心。')
        # Passport's cp.api login can be valid in the creator center without
        # establishing www.kuaishou.com's separate GraphQL login. Observe the
        # real signed request made by the creator page instead of copying
        # tokens between domains or using a cookie as identity proof.
        page = None
        try:
            page = await context.new_page()
            async with page.expect_response(
                lambda r: r.status == 200 and any(_provider_url(r.url, 'cp.kuaishou.com', path)
                    for path in (CREATOR_PROFILE_PATH, CURRENT_ACCOUNT_PATH)),
                timeout=15000,
            ) as captured:
                await page.goto('https://cp.kuaishou.com/profile', wait_until='domcontentloaded', timeout=15000)
            response = await captured.value
            data = await response.json()
            return (_current_creator_profile(data) if _provider_url(response.url, 'cp.kuaishou.com', CURRENT_ACCOUNT_PATH)
                    else _creator_profile(data))
        except Exception:
            raise AdapterError('尚未核实快手创作中心账号，请完成扫码后的平台确认。') from None
        finally:
            if page is not None:
                with suppress(Exception):
                    await asyncio.wait_for(page.close(), 5)

    async def prepare_login(self, context: Any) -> Any:
        """Open the actual passport page; the worker owns login and identity polling."""
        page = await context.new_page()
        await page.goto(self.login_url, wait_until="domcontentloaded", timeout=45000)
        form = page.locator("main#login-form").first
        qr = form.locator('div.qr-login img[alt="qrcode"]').first
        try:
            # The passport shell reaches DOMContentLoaded before its login
            # form mounts. A one-shot is_visible() misses the switch entirely.
            await form.wait_for(state="visible", timeout=30000)
            if not await qr.is_visible():
                switch = form.locator("div.platform-switch").first
                await switch.wait_for(state="visible", timeout=15000)
                await switch.click(timeout=5000)
                await qr.wait_for(state="visible", timeout=15000)
        except Exception:
            # CAPTCHA, alternate login layouts and connection completion remain
            # visible in the owned browser. Never treat them as logged in.
            pass
        return page

    async def capture_qr(self, page: Any, destination: Path) -> bool:
        """Screenshot the known login QR element only, never an authenticated page."""
        if not (_provider_url(page.url, "passport.kuaishou.com", "/pc/account/login/") or
                _provider_url(page.url, "passport.kuaishou.com", "/pc/account/login")):
            return False
        qr = page.locator('main#login-form div.qr-login img[alt="qrcode"]').first
        try:
            if not await qr.is_visible():
                return False
            await qr.screenshot(path=str(destination), timeout=5000)
            return True
        except Exception:
            return False

    def is_submit(self, url: str) -> bool:
        """Match only the final creator mutation, excluding file uploads and login."""
        return any(_provider_url(url, "cp.kuaishou.com", path)
                   for path in (SUBMIT_PATH, ISOLATED_SUBMIT_PATH))

    def submission_receipt(self, data: Any) -> dict:
        """Acknowledge verified acceptance, keeping public work IDs separate from asset IDs."""
        code = data.get("result") if isinstance(data, dict) else None
        if type(code) is not int:
            raise AdapterError(UNKNOWN_RECEIPT)
        if code != 1:
            return {"accepted": False, "confirmed_not_submitted": True, "code": code}
        if 'success' in data and data['success'] is not True:
            raise AdapterError(UNKNOWN_RECEIPT)
        # The pinned creator DTO only promises {result, message}. publishId from
        # the pending list and photoIdStr from upload/finish are NOT work IDs.
        # On 2026-10-25 the real creator response was {result: 1, data: {}};
        # the user independently confirmed the work in Kuaishou. This proves
        # submission acceptance, not its public ID or review/visibility state.
        # Do not borrow the OAuth openapi video_info schema for this endpoint.
        return {'accepted': True, 'confirmed_not_submitted': False, 'code': 1,
                'awaiting_work_id': True, 'public': False}

    def validate_payload(self, payload: dict) -> None:
        """Validate the publishing text without reading files or initializing a provider."""
        if not isinstance(payload, dict):
            raise AdapterError("快手发布包格式不正确。")
        title, description, tags = payload.get("title"), payload.get("description", ""), payload.get("tags", [])
        if not isinstance(title, str) or not title.strip() or not self.title_min <= len(title) <= self.title_limit:
            raise AdapterError(f"快手标题需要 {self.title_min}–{self.title_limit} 字。")
        if not isinstance(description, str) or not isinstance(tags, list) or len(tags) > self.tag_limit:
            raise AdapterError("快手简介或话题格式不正确，最多支持 3 个话题。")
        if any(not isinstance(tag, str) or not tag.strip() or len(tag) > 30 for tag in tags):
            raise AdapterError("快手话题需要 1–30 字。")
        caption = _caption(title, description)
        from core.social.publish_text import combined_text
        full_caption = combined_text("kuaishou", title, description, tags)
        if len(full_caption) > self.combined_limit:
            raise AdapterError(f"快手标题、简介与话题合计不能超过 {self.combined_limit} 字。")

    async def upload(self, playwright: Any, payload: dict, storage: Path) -> None:
        """Use the injected guarded browser with exactly one upstream publish attempt."""
        self.validate_payload(payload)
        title, description, tags = payload["title"], payload.get("description", ""), payload.get("tags", [])
        caption = _caption(title, description)
        if not isinstance(payload.get("video_path"), str) or not payload["video_path"]:
            raise AdapterError("没有找到可投稿的视频文件。")
        module = _isolated_upstream()

        class SingleSubmitVideo(module.KSVideo):
            async def validate_base_args(self) -> None:
                # cookie_auth starts an unguarded browser through a global
                # async_playwright. Identity was verified by the owning worker;
                # skip that helper while retaining upstream local-file checks.
                if not Path(self.account_file).is_file():
                    raise AdapterError("快手登录记录缺失，请重新连接账号。")
                self.publish_strategy = module.KUAISHOU_PUBLISH_STRATEGY_IMMEDIATE
                self.publish_date = 0

        app = SingleSubmitVideo(
            title=title, desc=caption, tags=tags, file_path=payload["video_path"],
            publish_date=0, account_file=str(storage),
            thumbnail_path=payload.get("cover") or None,
            publish_strategy=module.KUAISHOU_PUBLISH_STRATEGY_IMMEDIATE,
            debug=False, headless=True,
        )
        app.local_executable_path = ""
        try:
            await _upload_video(app, module, playwright)
        except AdapterError:
            raise
        except Exception:
            # Provider/page exception strings can include authenticated URLs.
            raise AdapterError("快手上传流程未完成，请查看投稿回执；如果已发出请求，系统不会自动重投。") from None

    async def inspect_work(self, playwright: Any, storage: Path, owner_id: str, work_id: str) -> dict:
        return await self.inspect_submission(playwright, storage, owner_id, {'work_id': _work_id(work_id)})

    async def inspect_submission(self, playwright: Any, storage: Path, owner_id: str, lookup: dict) -> dict:
        """Observe the creator's own signed list with every content mutation denied."""
        owner_id = _uid(owner_id)
        target = _work_id(lookup['work_id']) if lookup.get('work_id') else ''
        browser = await playwright.chromium.launch(headless=True)
        tasks: list[asyncio.Task] = []
        records: list[dict] = []
        errors: list[Exception] = []
        loaded = False
        try:
            context = await browser.new_context(storage_state=str(storage), service_workers='block')
            await protect_context(context)
            actual = await self.identity(context)
            if actual["uid"] != owner_id:
                raise AdapterError("当前快手登录账号与投稿账号不一致，请重新连接原账号。")
            page = await context.new_page()

            async def read_response(response: Any) -> None:
                nonlocal loaded
                if response.status == 200 and _provider_url(response.url, "cp.kuaishou.com", WORKS_PATH):
                    try:
                        rows = work_records(await response.json(), owner_id, target)
                        records.extend(rows); loaded = True
                    except Exception as exc:
                        errors.append(exc)

            def observe(response: Any) -> None:
                if _provider_url(response.url, "cp.kuaishou.com", WORKS_PATH):
                    tasks.append(asyncio.create_task(read_response(response)))

            page.on("response", observe)
            await page.goto(self.creator_url, wait_until="domcontentloaded", timeout=30000)
            for _ in range(30):
                if loaded or errors:
                    break
                await page.wait_for_timeout(500)
            if tasks:
                await asyncio.gather(*tasks)
            if errors:
                raise AdapterError('快手作品列表或账号归属未通过核对，将稍后重查；不会重新投稿。')
            if not loaded:
                raise AdapterError('暂未取得快手作品列表，请核对平台登录；系统会自动重查。')
            selected, reason = select_work(records, lookup)
            for _ in range(8):
                if selected or reason == 'ambiguous' or not await _read_more_works(page, records, errors):
                    break
                if tasks:
                    await asyncio.gather(*tasks)
                if errors:
                    raise AdapterError('快手后续作品列表未能读取，原投稿保留，将稍后核对。')
                selected, reason = select_work(records, lookup)
            return {**receipt(self.key, owner_id, selected, reason), 'lookup_diagnostics': lookup_diagnostics(records, lookup)}
        except AdapterError:
            raise
        except Exception:
            raise AdapterError("快手作品核对暂未完成，请到创作者中心查看；不会自动重投。") from None
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            await browser.close()
