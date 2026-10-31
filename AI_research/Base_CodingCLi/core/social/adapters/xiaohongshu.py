"""Small, fail-closed Xiaohongshu wrapper around the reviewed SAU video uploader.

The uploader remains in the isolated, pinned runtime and receives the worker's
guarded Playwright instance. Neither login URLs nor a successful uploader exit
are publication receipts. Sources used to verify provider contracts:

* dreammis/social-auto-upload, 3847fff01f5434a8a32829833163cd6723cabc59
  (MIT), uploader/xiaohongshu_uploader/main.py and sau_commands/xiaohongshu.py
* yikart/AiToEarn, cb894a43b2d23cc1ad67d9e7729b1d86cf9360bd,
  project/aitoearn-electron/electron/plat/xiaohongshu/index.ts
* ZJU-REAL/Easel, a097ddfbb704c9d1eeb1db49a056fdbca9c2351a,
  skills/shared/scripts/xhs_publish.py (_creator_account)
* 027xiguapi/code-box, 7b5b52d5853853d568afe60628af64dd78fa1aca,
  sync/adapters/platforms/xiaohongshu.ts (XhsUserInfo)

No external signing service, copied credentials, or upstream login heuristics
are used. Review/visibility enums and the current own-note endpoint were
verified in the official creator frontend (4108.10b0aee5.js module 23522,
98.6a90c3f6.js and index.a3508693.js) on 2026-10-25. Unknown enums remain unconfirmed.
"""
from __future__ import annotations

from contextlib import suppress
import asyncio
from datetime import datetime
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from core.social.adapter import AdapterError
from core.social.verification import protect_context, receipt, select_work, timestamp
from core.social.verification import lookup_diagnostics


CREATOR_URL = "https://creator.xiaohongshu.com/new/home"
LOGIN_URL = "https://creator.xiaohongshu.com/login"
PROFILE_URL = "https://creator.xiaohongshu.com/api/galaxy/user/info"
SUBMIT_HOST = "edith.xiaohongshu.com"
SUBMIT_PATH = "/web_api/sns/v2/note"
IDENTIFIER = re.compile(r"[0-9a-f]{24}")
PUBLIC_CHECK_GAP = "尚未核实小红书审核和可见性状态的可靠回执，请在创作服务平台确认公开状态。"
MANAGER_URL = 'https://creator.xiaohongshu.com/new/note-manager'
WORKS_PATH = '/api/galaxy/v2/creator/note/user/posted'
LEGACY_WORKS_PATH = '/web_api/sns/v5/creator/note/user/posted'


def _note_time(value: Any) -> float | None:
    when = timestamp(value)
    if when or not isinstance(value, str):
        return when
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return timestamp(parsed.replace(tzinfo=ZoneInfo('Asia/Shanghai')).timestamp() if parsed.tzinfo is None else parsed.timestamp())
    except ValueError:
        return None


def work_records(data: Any, owner_id: str, target: str = '') -> list[dict]:
    """Creator enums verified in official 4108.10b0aee5.js module 23522.

    tab_status 1 published, 2 auditing, 3 not passed, 4 scheduled.
    permission_code 0 public, 1 private, 2/3 restricted, 4 friends-only.
    The UI also prevents opening a work when high_self or schedule_post_time is set.
    high_self is an optional restriction flag: the official note-card gate tests
    its truthiness, so absent/null is not a restriction. Published tab and public
    permission still have to be explicit.
    """
    if (not isinstance(data, dict) or any(type(data[k]) is not int or data[k] != 0 for k in ('code','result') if k in data)
            or ('success' in data and data['success'] is not True)
            or not ((data.get('success') is True and type(data.get('code')) is int and data['code'] == 0)
                    or (type(data.get('result')) is int and data['result'] == 0))
            or not isinstance(data.get('data'), dict) or not isinstance(data['data'].get('notes'), list)):
        raise AdapterError('小红书作品列表暂不可用，请核对登录或平台验证；系统会自动重查，不会重新投稿。')
    output = []
    for row in data['data']['notes']:
        if not isinstance(row, dict) or not isinstance(row.get('id'), str) or not IDENTIFIER.fullmatch(row['id']):
            continue
        actual = row.get('user_id') or (row.get('user', {}).get('user_id') if isinstance(row.get('user'), dict) else None)
        if (actual is not None and actual != owner_id) or (row.get('cocreate') is True and row.get('author') is not True):
            if row['id'] == target:
                raise AdapterError('这条小红书作品不属于原投稿账号，已停止回写。')
            continue
        tab, permission = row.get('tab_status'), row.get('permission_code')
        state = 'unknown'
        if type(tab) is int:
            if tab == 2:
                state = 'reviewing'
            elif tab == 3:
                state = 'rejected'
            elif tab == 4:
                state = 'scheduled'
            elif tab == 1 and type(permission) is int:
                if permission in (1,2,3,4) or row.get('high_self') is True or type(row.get('high_self')) is int and row['high_self'] != 0:
                    state = 'private'
                elif (permission == 0 and (row.get('high_self') is None or row.get('high_self') is False or type(row.get('high_self')) is int and row['high_self'] == 0)
                      and type(row.get('schedule_post_time')) is int and row['schedule_post_time'] == 0):
                    state = 'published'
        video = row.get('video_info') if isinstance(row.get('video_info'), dict) else {}
        output.append({'work_id': row['id'], 'title': row.get('display_title',''),
                       'published_at': _note_time(row.get('time')), 'duration_seconds': video.get('duration'),
                       'state': state, 'status_details': {
                           key: row.get(key) if type(row.get(key)) in (int, bool) else None
                           for key in ('tab_status', 'permission_code', 'high_self', 'schedule_post_time')}})
    return output


def _is_endpoint(url: str, hostname: str, path: str) -> bool:
    """Match only the reviewed HTTPS endpoint, including when a query is present."""
    if not isinstance(url, str):
        return False
    try:
        parsed = urlsplit(url)
        return (parsed.scheme == "https" and parsed.hostname == hostname
                and parsed.port in (None, 443) and parsed.username is None
                and parsed.password is None and parsed.path == path)
    except ValueError:
        return False


def _profile(data: Any) -> dict:
    """Extract the creator's stable userId, never the editable redId/小红书号."""
    if (not isinstance(data, dict) or data.get("success") is not True
            or type(data.get("code")) is not int or data["code"] != 0
            or not isinstance(data.get("data"), dict)):
        raise AdapterError("未取得可核实的小红书账号，请完成扫码连接后重试。")
    account = data["data"]
    uid = account.get("userId")
    name = account.get("userName") or account.get("nickName")
    if (not isinstance(uid, str) or not IDENTIFIER.fullmatch(uid)
            or not isinstance(name, str) or not name.strip()
            or len(name.strip()) > 200 or account.get("guest") is True):
        raise AdapterError("小红书账号信息缺少稳定 UID 或昵称，暂不能确认连接成功。")
    return {"uid": uid, "name": name.strip()}


class XiaohongshuAdapter:
    """Xiaohongshu video login, guarded upload, and strict submission receipt."""

    key = "xiaohongshu"
    label = "小红书"
    creator_url = CREATOR_URL
    login_url = LOGIN_URL
    title_min = 1
    title_limit = 20
    description_limit = 1000
    tag_limit = 10
    # No additional combined title/description limit has been source-verified.
    combined_limit = None
    supports_upload = True
    supports_public_check = True
    public_check_gap = '发布后自动核对审核与公开权限；登录失效或遇到平台验证时会提示并稍后重查。'
    upload_note = '确认后在本机投稿，随后自动核对平台状态；确认公开后自动回写文稿发布记录。'

    async def identity(self, context: Any) -> dict:
        """Read a real account response; signed browser navigation is the fallback."""
        try:
            response = await context.request.get(
                PROFILE_URL, headers={"Referer": CREATOR_URL}, timeout=5000,
                max_redirects=0, max_retries=0,
            )
            if response.status == 200 and _is_endpoint(
                response.url, "creator.xiaohongshu.com", "/api/galaxy/user/info",
            ):
                return _profile(await response.json())
        except Exception:
            # Fixed safe failure below; raw provider responses can contain secrets.
            pass

        page = None
        try:
            page = await context.new_page()

            def account_response(response: Any) -> bool:
                return (response.request.method == "GET" and response.status == 200
                        and _is_endpoint(response.url, "creator.xiaohongshu.com",
                                         "/api/galaxy/user/info"))

            async with page.expect_response(account_response, timeout=10000) as pending:
                await page.goto(CREATOR_URL, wait_until="domcontentloaded", timeout=15000)
            response = await pending.value
            return _profile(await response.json())
        except Exception:
            raise AdapterError("尚未核实小红书账号，请完成扫码；若平台要求验证，请在连接窗口操作。") from None
        finally:
            if page is not None:
                with suppress(Exception):
                    await page.close()

    async def prepare_login(self, context: Any) -> Any:
        """Open the actual creator QR login, without entering credentials."""
        page = await context.new_page()
        try:
            await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=30000)
            if not _is_endpoint(page.url, "creator.xiaohongshu.com", "/login"):
                raise AdapterError("扫码页已离开核实过的小红书登录地址。")
            box = page.locator("div[class*='login-box']").first
            await box.wait_for(state="visible", timeout=15000)
            scan = box.locator("div:has-text('扫一扫')").first
            if not await scan.count() or not await scan.is_visible():
                switch = box.locator("img.css-wemwzq").first
                await switch.wait_for(state="visible", timeout=5000)
                await switch.click()
                await scan.wait_for(state="visible", timeout=10000)
            return page
        except Exception:
            with suppress(Exception):
                await page.close()
            raise AdapterError("小红书扫码页未就绪，请重新生成二维码或在连接窗口完成平台验证。") from None

    async def capture_qr(self, page: Any, destination: Path) -> bool:
        """Save just the QR image below APP扫一扫登录, never a full account screenshot."""
        try:
            if not _is_endpoint(page.url, "creator.xiaohongshu.com", "/login"):
                return False
            qr = (page.locator(".login-box-container")
                  .get_by_text("APP扫一扫登录", exact=True)
                  .locator("xpath=..//following-sibling::div//img").first)
            await qr.wait_for(state="visible", timeout=5000)
            box = await qr.bounding_box()
            if (not box or box["width"] < 80 or box["height"] < 80
                    or not 0.7 <= box["width"] / box["height"] <= 1.3):
                return False
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.touch(mode=0o600, exist_ok=True)
            destination.chmod(0o600)
            await qr.screenshot(path=str(destination), timeout=5000)
            destination.chmod(0o600)
            return True
        except Exception:
            return False

    def is_submit(self, url: str) -> bool:
        """Select only final note creation, excluding asset uploads and login APIs."""
        return _is_endpoint(url, SUBMIT_HOST, SUBMIT_PATH)

    def submission_receipt(self, data: Any) -> dict:
        """Only an explicit accepted response with a string note ID is a receipt."""
        if not isinstance(data, dict):
            raise AdapterError("未取得明确的小红书投稿回执，请到创作服务平台核对，系统不会自动重投。")
        # This creator mutation also returns {result: 0, data: {id: ...}}.
        # The user-profile API's code/success envelope is a separate contract.
        # Reject malformed or contradictory status fields even when another
        # envelope happens to look successful. No ID alone implies acceptance.
        for key in ('code', 'result'):
            if key in data and type(data[key]) is not int:
                raise AdapterError("小红书投稿回执的状态格式不正确，请核对结果；系统不会自动重投。")
        if 'success' in data and type(data['success']) is not bool:
            raise AdapterError("小红书投稿回执的状态格式不正确，请核对结果；系统不会自动重投。")
        body = data.get("data")
        note_id = body.get("id") if isinstance(body, dict) else None
        legacy_ok = data.get('code') == 0 and data.get('success') is True
        creator_ok = type(data.get('result')) is int and data['result'] == 0
        rejected = any(data.get(key, 0) != 0 for key in ('code', 'result')) or data.get('success') is False
        if rejected:
            codes = [data[key] for key in ('code', 'result') if key in data]
            explicit_refusal = data.get('success') is False or ('result' in data and data['result'] != 0)
            if (codes and all(code != 0 for code in codes) and explicit_refusal
                    and data.get('success') is not True and note_id in (None, '')):
                return {"accepted": False, "confirmed_not_submitted": True, "code": codes[0]}
            raise AdapterError("小红书返回结果状态不一致，请核对投稿结果；系统不会自动重投。")
        if (not (legacy_ok or creator_ok) or not isinstance(note_id, str)
                or not IDENTIFIER.fullmatch(note_id)):
            raise AdapterError("小红书返回结果缺少可靠作品 ID 或状态不一致，请核对投稿结果；系统不会自动重投。")
        return {"accepted": True, "confirmed_not_submitted": False, "code": 0,
                "work_id": note_id, "url": f"https://www.xiaohongshu.com/explore/{note_id}",
                "public": False}

    def validate_payload(self, payload: dict) -> None:
        """Check editable fields before queueing; never import a runtime or read files."""
        if not isinstance(payload, dict):
            raise AdapterError("小红书发布包必须包含可编辑的标题、简介和标签。")
        title = payload.get("title")
        description = payload.get("description", "")
        tags = payload.get("tags", [])
        if (not isinstance(title, str) or len(title.strip()) < self.title_min
                or len(title) > self.title_limit
                or not isinstance(description, str) or len(description) > self.description_limit
                or not isinstance(tags, list) or len(tags) > self.tag_limit
                or any(not isinstance(tag, str) or not tag.strip() for tag in tags)):
            raise AdapterError("小红书发布包不符合标题、简介或标签限制，请先修改发布包。")

    async def upload(self, playwright: Any, payload: dict, storage: Path) -> None:
        """Run only the pinned uploader's injected upload(), with no private launch."""
        self.validate_payload(payload)
        title = payload["title"]
        description = payload.get("description", "")
        tags = payload.get("tags", [])
        # The runtime's importer and version verification are owned by the worker.
        from uploader.xiaohongshu_uploader.main import XiaoHongShuVideo

        class SingleSubmitVideo(XiaoHongShuVideo):
            """Keep upstream browser ownership but remove its final-click retry loop."""

            async def upload_video_content(self, page: Any) -> None:
                # All selectors below are reviewed in the pinned SAU implementation.
                # A changed mutation endpoint must lead to UNKNOWN, never another click.
                await page.goto(
                    "https://creator.xiaohongshu.com/publish/publish?from=homepage&target=video",
                    wait_until="domcontentloaded", timeout=30000,
                )
                await page.locator(
                    "div[class^='upload-content'] input[class='upload-input']",
                ).set_input_files(self.file_path)
                await page.locator('input[placeholder*="填写标题"]').wait_for(
                    state="visible", timeout=600000,
                )
                await self.fill_meta(page)
                await self.set_thumbnail(page, self.thumbnail_path)
                # No automatic source/repost declaration: no verified source is supplied.
                await page.get_by_role("button", name="发布", exact=True).click(timeout=600000)
                await page.wait_for_url("**/publish/success?**", timeout=60000)

        video = SingleSubmitVideo(
            title=title, file_path=payload["video_path"], desc=description, tags=list(tags),
            publish_date=0, account_file=str(storage), thumbnail_path=payload.get("cover") or None,
            publish_strategy="immediate", debug=False, headless=False,
        )
        await video.upload(playwright)

    async def inspect_work(self, playwright: Any, storage: Path, owner_id: str, work_id: str) -> dict:
        if (not isinstance(work_id, str) or not IDENTIFIER.fullmatch(work_id)
                or not isinstance(owner_id, str) or not IDENTIFIER.fullmatch(owner_id)):
            raise AdapterError("小红书作品 ID 或账号 UID 无效。")
        return await self.inspect_submission(playwright, storage, owner_id, {'work_id': work_id})

    async def inspect_submission(self, playwright: Any, storage: Path, owner_id: str, lookup: dict) -> dict:
        """Use the creator's signed own-note request, without any publishing mutation."""
        if not isinstance(owner_id, str) or not IDENTIFIER.fullmatch(owner_id):
            raise AdapterError('小红书账号 UID 无效。')
        target = lookup.get('work_id','')
        if target and (not isinstance(target, str) or not IDENTIFIER.fullmatch(target)):
            raise AdapterError('小红书作品 ID 无效。')
        browser = await playwright.chromium.launch(headless=True)
        tasks = []; records = []; errors = []; loaded = False
        try:
            context = await browser.new_context(storage_state=str(storage), service_workers='block')
            await protect_context(context)
            if (await self.identity(context))['uid'] != owner_id:
                raise AdapterError('当前小红书账号与原投稿账号不一致，请连接原账号。')
            page = await context.new_page()

            def matches(response):
                return response.request.method == 'GET' and (
                    _is_endpoint(response.url, 'creator.xiaohongshu.com', WORKS_PATH)
                    or _is_endpoint(response.url, 'edith.xiaohongshu.com', LEGACY_WORKS_PATH))

            async def read(response):
                nonlocal loaded
                if not matches(response):
                    return
                try:
                    if response.status != 200:
                        raise AdapterError('小红书作品查询需要有效登录或平台验证，将稍后自动重查。')
                    records.extend(work_records(await response.json(), owner_id, target)); loaded = True
                except Exception as exc:
                    errors.append(exc)

            page.on('response', lambda response: tasks.append(asyncio.create_task(read(response))) if matches(response) else None)
            await page.goto(MANAGER_URL, wait_until='domcontentloaded', timeout=30000)
            # Signing/bootstrap loads asynchronously. Observe its real request;
            # do not copy a token into a synthetic unsigned API call.
            for _ in range(10):
                await page.wait_for_timeout(1500)
                if loaded or errors:
                    break
            if tasks:
                await asyncio.gather(*tasks)
            if errors or not loaded:
                raise AdapterError('暂未取得小红书作品列表，请核对平台登录或验证提示；系统会自动重查，不会重新投稿。')
            selected, reason = select_work(records, lookup)
            return {**receipt(self.key, owner_id, selected, reason), 'lookup_diagnostics': lookup_diagnostics(records, lookup)}
        except AdapterError:
            raise
        except Exception:
            raise AdapterError('小红书作品核对暂未完成，将稍后自动重查；不会重新投稿。') from None
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            await browser.close()
