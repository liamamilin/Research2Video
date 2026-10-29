"""Run one Douyin job in the pinned, isolated upload environment. No secrets on argv/stdout."""

from __future__ import annotations

import argparse
import asyncio
from contextlib import suppress
import fcntl
import json
import os
from pathlib import Path
import re
import sys
import time
from types import ModuleType, SimpleNamespace
from urllib.parse import urlsplit, unquote

# core/__init__.py eagerly imports the research engine and its model dependencies.
# This subprocess only needs three stdlib-based modules. Keep it independent of the
# application environment without changing imports in the running Web application.
if __name__ == "__main__":
    package = ModuleType("core")
    package.__path__ = [str(Path(__file__).resolve().parents[1] / "core")]
    sys.modules["core"] = package

from core.douyin import CREATE_PATHS, DouyinError, accepted_receipt, profile, public_receipt, creator_diagnostics, comment_receipt, comment_text_matches
from core.douyin import DouyinLookupError
from core.douyin_store import DouyinStore


def private_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix(".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as stream:
        json.dump(value, stream, ensure_ascii=False)
    os.replace(temporary, path)
    path.chmod(0o600)


async def identity(context) -> dict:
    response = await context.request.get("https://creator.douyin.com/web/api/media/user/info/", timeout=20000)
    if response.status != 200:
        raise DouyinError("抖音账号验证未完成，请重新扫码或完成浏览器中的身份验证。")
    return profile(await response.json())


async def launch(playwright, **kwargs):
    # Each worker owns its own browser. Never connect to or close the user's ordinary Chrome session.
    return await asyncio.wait_for(playwright.chromium.launch(headless=kwargs.get("headless", True),
                                  args=["--no-first-run", "--no-default-browser-check"], timeout=45000), 50)


async def login(db: DouyinStore, job: dict, folder: Path) -> None:
    from uploader.douyin_uploader import main as upstream
    from patchright.async_api import async_playwright
    from loguru import logger

    logger.remove()  # Upstream file sinks include local variables on failures; never retain those.

    credential = folder / "storage.json"

    async def qrcode(value):
        image = value.get("image_path")
        if image and Path(image).is_file():
            raw = Path(image).read_bytes()
            if len(raw) <= 2_000_000:
                fd = os.open(folder / "qrcode.png", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "wb") as out:
                    out.write(raw)
                db.update(job["id"], phase="等待扫码，请在独立浏览器完成身份验证")

    db.update(job["id"], phase="正在打开抖音登录窗口")
    async with async_playwright() as pw:
        browser = await launch(pw, headless=False)
        upstream_qr = None
        try:
            db.update(job["id"], phase="正在创建独立抖音会话")
            context = await asyncio.wait_for(browser.new_context(), 30)
            context = await upstream.set_init_script(context)
            db.update(job["id"], phase="正在准备抖音登录页面")
            page = await asyncio.wait_for(context.new_page(), 30)
            db.update(job["id"], phase="正在加载抖音登录页")
            await page.goto("https://creator.douyin.com/", wait_until="domcontentloaded", timeout=45000)
            qr = await upstream._save_douyin_qrcode(page, str(credential), qrcode_callback=qrcode)
            upstream_qr = Path(qr["image_path"]) if qr.get("image_path") else None
            db.update(job["id"], phase="等待扫码，请在独立浏览器完成身份验证")
            result = await asyncio.wait_for(upstream._wait_for_douyin_login(
                page, str(credential), qr, qrcode_callback=qrcode, poll_interval=3, max_checks=100), 330)
            if not result.get("success"):
                raise DouyinError("扫码未完成或已过期，请重新连接；遇到身份验证时请在独立浏览器操作。")
            db.update(job["id"], phase="正在核对实际抖音账号")
            owner = await identity(context)
            await context.storage_state(path=str(credential))
            credential.chmod(0o600)
        finally:
            await browser.close()
            if upstream_qr:
                upstream_qr.unlink(missing_ok=True)
    db.update(job["id"], status="DONE", phase="账号已验证", result=owner)


async def upload(db: DouyinStore, job: dict, storage: Path) -> None:
    from patchright.async_api import async_playwright
    from uploader.douyin_uploader import main as upstream
    from loguru import logger

    logger.remove()

    payload = job["payload"]
    future = asyncio.get_running_loop().create_future()
    attempted = False
    browsers = []

    async def guarded_submit(route):
        nonlocal attempted
        request = route.request
        if request.method != "POST":
            return await route.continue_()
        if attempted:
            await route.abort()
            return
        attempted = True
        db.update(job["id"], phase="正在提交，等待平台回执", result={"submission_started": True})
        try:
            if urlsplit(request.url).path not in CREATE_PATHS:
                raise DouyinError("抖音发布接口已变化，已停止提交，请更新适配器。")
            response = await route.fetch(timeout=60000, max_redirects=0, max_retries=0)
            receipt = accepted_receipt(await response.json()) if response.status == 200 else None
            if not receipt:
                raise DouyinError("未取得明确的投稿回执，请到创作中心核对；系统不会自动重投。")
            if receipt["accepted"]:
                db.update(job["id"], work_id=receipt["work_id"], result={**receipt, "uid": job["uid"]},
                          phase="已取得作品 ID，等待核对公开状态")
            else:
                db.update(job["id"], result={"confirmed_not_submitted": True, "provider_code": receipt["code"]})
            with suppress(Exception):
                await route.fulfill(response=response)
            if not future.done():
                future.set_result(receipt)
        except Exception as exc:
            with suppress(Exception):
                await route.abort()
            if not future.done():
                future.set_exception(exc)

    async def guarded_launch(**kwargs):
        browser = await launch(pw, headless=False)
        browsers.append(browser)

        async def new_context(**options):
            # Force the captured account snapshot, rather than any global upstream cookie file.
            options["storage_state"] = str(storage)
            options.pop("permissions", None)
            context = await browser.new_context(**options)
            owner = await identity(context)
            if owner["uid"] != job["uid"]:
                raise DouyinError("登录账号已改变，已停止投稿，请重新连接。")
            await context.route(re.compile(r"^https://creator\.douyin\.com/web/api/media/aweme/create[^?]*(?:\?.*)?$"), guarded_submit)
            return context

        return SimpleNamespace(new_context=new_context, close=browser.close)

    async def no_shared_sms_file(_path):
        db.update(job["id"], phase="请在独立浏览器完成短信验证")
        return ""

    # Do not consume the upstream project's shared verify_code.txt or write OTPs to disk.
    upstream._read_verify_code = no_shared_sms_file
    video = upstream.DouYinVideo(
        title=payload["title"], file_path=payload["video_path"], tags=payload["tags"],
        publish_date=0, account_file=str(storage), desc=payload["description"],
        thumbnail_landscape_path=payload.get("cover") or None,
        declaration="内容由AI生成", headless=False, debug=False,
    )
    async with async_playwright() as pw:
        task = asyncio.create_task(video.upload(SimpleNamespace(chromium=SimpleNamespace(launch=guarded_launch))))
        try:
            done, _ = await asyncio.wait((task, future), timeout=1800, return_when=asyncio.FIRST_COMPLETED)
            if future in done:
                receipt = future.result()
                if not receipt["accepted"]:
                    db.update(job["id"], status="FAILED", phase="平台拒绝投稿",
                              error=f"抖音拒绝了本次投稿（错误码 {receipt['code']}），未自动重试。")
                    return
                db.update(job["id"], status="SUBMITTED", phase="已投稿，待核对平台审核与公开状态", error="")
            elif task in done:
                task.result()
                raise DouyinError("上传窗口已结束，但没有取得可靠作品 ID，请到创作中心核对。")
            else:
                raise DouyinError("上传等待超时，请到创作中心核对；系统不会自动重投。")
        except Exception as exc:
            row = db.get(job["id"])
            status = "SUBMITTED" if row["work_id"] else "UNKNOWN" if attempted else "FAILED"
            message = str(exc) if isinstance(exc, DouyinError) else "抖音上传未完成，请检查登录、素材或创作中心中的结果。"
            db.update(job["id"], status=status, error=message,
                      result=row["result"] if attempted else {"confirmed_not_submitted": True})
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            if not future.done():
                future.cancel()
            elif not future.cancelled():
                future.exception()  # Consume an error even if the upload task failed first.
            for browser in browsers:
                with suppress(Exception):
                    await browser.close()


async def owned_record(context, target: str) -> dict | None:
    cursor = "0"
    for _ in range(10):
        response = await context.request.get("https://creator.douyin.com/aweme/v1/creator/item/list/",
                                             params={"cursor": cursor, "count": "50"}, timeout=20000)
        if response.status != 200:
            raise DouyinLookupError(f'抖音作品列表返回 HTTP {response.status}，本次未能读取作品。', 'list_http_error')
        try:
            data = await response.json()
        except (ValueError, TypeError):
            raise DouyinLookupError('抖音作品列表未返回可解析数据，本次未能读取作品。', 'list_invalid_json') from None
        if not isinstance(data, dict) or type(data.get('status_code')) is not int or data['status_code'] != 0:
            raise DouyinLookupError('抖音作品列表接口未成功，请检查平台登录或验证提示。', 'list_api_error')
        rows = data.get('item_info_list')
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise DouyinLookupError('抖音作品列表格式已变化，本次未能完成作品查找。', 'list_schema_error')
        for row in rows:
            if str(row.get("item_id_plain") or row.get("aweme_id") or "") == target:
                return {"work_id": target, "owned": True, "provider_item_id": row.get("item_id", ""), "creator_state": creator_diagnostics(row)}
        if not data.get('has_more'):
            return None
        next_cursor = str(data.get('cursor') or data.get('max_cursor') or (rows[-1].get('cursor') if rows else '') or '')
        if not next_cursor or cursor == next_cursor:
            raise DouyinLookupError('抖音作品列表分页未推进，尚不能认定作品不存在。', 'list_cursor_error')
        cursor = next_cursor
    raise DouyinLookupError('已查到本次作品列表上限，尚未定位原作品。', 'list_limit_reached', state='unknown')



def matching_work_records(value, target: str) -> list[dict]:
    """Inspect JSON identities exactly; titles and list positions never bind a work."""
    found = []
    if isinstance(value, dict):
        if any(str(value.get(k) or '') == target for k in ('aweme_id', 'item_id_plain', 'item_id', 'awemeId', 'itemId')):
            found.append(value)
        for child in value.values():
            if isinstance(child, (dict, list)):
                found.extend(matching_work_records(child, target))
    elif isinstance(value, list):
        for child in value:
            found.extend(matching_work_records(child, target))
    return found


def rendered_work_records(scripts: list[str], target: str) -> list[dict]:
    """Decode JSON/React Flight data without executing any provider JavaScript."""
    records = []
    decoder = json.JSONDecoder()

    def collect(value, depth: int = 0):
        records.extend(matching_work_records(value, target))
        if depth >= 3:
            return
        if isinstance(value, list):
            for child in value:
                if isinstance(child, str) and target in child:
                    fragments(child, depth + 1)
        elif isinstance(value, dict):
            for child in value.values():
                if isinstance(child, str) and target in child:
                    fragments(child, depth + 1)

    def fragments(raw: str, depth: int):
        # Flight rows have numeric prefixes, e.g. 12:["$","div",...,{...}].
        # Only JSONDecoder consumes data; JavaScript names/calls are never evaluated.
        positions = [m.start() for m in re.finditer(r'[\[{]', raw)][:2000]
        for position in positions:
            with suppress(ValueError, RecursionError):
                value, _ = decoder.raw_decode(raw, position)
                collect(value, depth)
                if records:
                    return

    for raw in scripts[:500]:
        if not raw or len(raw) > 10_000_000 or target not in raw:
            continue
        raw = raw.strip()
        if raw.startswith('window._ROUTER_DATA'):
            raw = raw.partition('=')[2].strip().rstrip(';')
        elif raw.startswith('self.__pace_f.push('):
            raw = raw[len('self.__pace_f.push('):].strip().rstrip(';')
            if not raw.endswith(')'):
                continue
            raw = raw[:-1]
        else:
            raw = unquote(raw)
        if not raw.startswith(('{', '[')):
            continue
        with suppress(ValueError, RecursionError):
            collect(json.loads(raw))
    return records


async def inspect_work(pw, storage: Path, owner_id: str, target: str) -> dict:
    browser = await launch(pw)
    try:
        context = await browser.new_context(storage_state=str(storage))
        owner = await identity(context)
        if owner["uid"] != owner_id:
            raise DouyinError("核对账号与原投稿账号不一致，请重新连接原账号。")
        owned = await owned_record(context, target)
        if not owned:
            raise DouyinLookupError('已读取当前账号作品列表，未找到原作品 ID；保留投稿回执。', 'work_not_found', state='missing')
        await context.close()
        # An isolated anonymous context prevents a private, author-only video from appearing public.
        anonymous = await browser.new_context()
        page = await anonymous.new_page()
        found = asyncio.get_running_loop().create_future()
        details = []

        async def inspect(response):
            parts = urlsplit(response.url)
            if parts.hostname in ("www.douyin.com", "douyin.com") and parts.path == "/aweme/v1/web/aweme/detail/":
                try:
                    data = await response.json()
                    record = data.get('aweme_detail') if isinstance(data, dict) else None
                    if isinstance(record, dict) and str(record.get('aweme_id') or '') == target:
                        details.append({'states': creator_diagnostics(record), 'fields': sorted(record)[:60]})
                    receipt = public_receipt(data, target, owner)
                    if receipt and not found.done():
                        found.set_result(receipt)
                except DouyinError as exc:
                    if not found.done():
                        found.set_exception(exc)
                except Exception:
                    pass

        page.on("response", inspect)
        navigation = {}
        try:
            response = await page.goto(f"https://www.douyin.com/video/{target}", wait_until="domcontentloaded", timeout=30000)
            navigation['http_status'] = response.status if response else 0
        except Exception as exc:
            navigation['error_type'] = type(exc).__name__
        try:
            return await asyncio.wait_for(found, timeout=15)
        except asyncio.TimeoutError:
            # The detail can be server-rendered, so no XHR detail response is emitted.
            rendered = []
            with suppress(Exception):
                navigation['title'] = (await page.title())[:200]
                navigation['script_count'] = await page.locator('script').count()
                visible = (await page.locator('body').inner_text())[:5000]
                navigation['needs_verification'] = any(term in visible for term in ('请完成验证', '验证码验证', '安全验证', '拖动滑块', '验证后继续'))
            with suppress(Exception):
                scripts = await page.locator('script').all_text_contents()
                relevant = [raw for raw in scripts if target in raw]
                navigation['target_script_count'] = len(relevant)
                navigation['script_markers'] = [(re.match(r'[A-Za-z_$][A-Za-z0-9_$.]*', raw.strip()).group(0) if re.match(r'[A-Za-z_$][A-Za-z0-9_$.]*', raw.strip()) else 'json-or-encoded') for raw in relevant[:5]]
                for record in rendered_work_records(scripts, target):
                    rendered.append({'fields': sorted(record)[:60], 'states': creator_diagnostics(record)})
                    receipt = public_receipt({'status_code': 0, 'aweme_detail': record}, target, owner)
                    if receipt:
                        return receipt
            return {"work_id": target, "uid": owner_id, "owned": True, "public": False,
                    "url": f"https://www.douyin.com/video/{target}", "creator_state": owned.get("creator_state", {}),
                    "check_reason": "匿名页面未取得可确认公开的作品详情；账号归属已核对。", "rendered_observations": rendered[:3], "detail_observations": details[:3], "navigation": navigation}
    finally:
        await browser.close()



async def _existing_author_comment(context, provider_id: str, text: str) -> dict | None:
    listing = await context.request.get('https://creator.douyin.com/aweme/v1/creator/comment/list/',
        params={'item_id': provider_id, 'sort': 'TIME', 'cursor': '0', 'count': '50'}, timeout=20000,
        max_redirects=0, max_retries=0)
    data = await listing.json()
    if (listing.status != 200 or not isinstance(data, dict)
            or type(data.get('status_code')) is not int or data['status_code'] != 0):
        return None
    rows = data.get('comment_info_list') or data.get('comment_list') or []
    if not isinstance(rows, list):
        return None
    for row in rows:
        with suppress(DouyinError):
            receipt = comment_receipt({'status_code': 0, 'comment_info': row}, text)
            if receipt['accepted']:
                return receipt
    return None


async def send_first_comment(db: DouyinStore, job: dict, pw, storage: Path) -> None:
    """One creator-API POST after an exact owner/work preflight, with a durable at-most-once guard."""
    attempted = False
    browser = None
    try:
        parent = db.get(job['parent_id'])
        if not parent or parent['kind'] != 'UPLOAD' or parent['status'] != 'PUBLISHED' or parent['uid'] != job['uid'] or parent['work_id'] != job['work_id']:
            raise DouyinError('作品、账号或公开状态尚未确认，作者评论未提交。')
        if job['result'].get('submission_started') or job['result'].get('comment_id'):
            db.update(job['id'], status='SENT' if job['result'].get('comment_id') else 'UNKNOWN', phase='保留已有评论结果，不重复提交')
            return
        browser = await launch(pw)
        context = await browser.new_context(storage_state=str(storage))
        owner = await identity(context)
        if owner['uid'] != job['uid']:
            raise DouyinError('登录账号与原投稿账号不一致，作者评论未提交。')
        owned = await owned_record(context, job['work_id'])
        provider_id = (owned or {}).get('provider_item_id')
        if not isinstance(provider_id, str) or not provider_id:
            raise DouyinError('未取得这条作品的创作者标识，作者评论未提交。')
        # A comment may already have been added through the platform while the
        # application's task was missing. Reuse that exact author comment.
        existing = None
        with suppress(Exception):
            existing = await _existing_author_comment(context, provider_id, job['payload']['text'])
        if existing:
            db.update(job['id'], status='SENT', phase='已关联创作中心已有作者评论，未重复发送',
                      result={**existing, 'read_back': True, 'existing_comment': True,
                              'work_id': job['work_id']}, error='')
            return
        # Read the token only from Douyin's own authenticated response; never expose it to the app API or logs.
        proof = await context.request.head('https://creator.douyin.com/web/api/media/anchor/search',
            headers={'X-Secsdk-Csrf-Request': '1', 'X-Secsdk-Csrf-Version': '1.2.22',
                     'Referer': 'https://creator.douyin.com/creator-micro/home'},
            timeout=20000, max_redirects=0, max_retries=0)
        token = proof.headers.get('x-ware-csrf-token', '').split(',')[-1].strip()
        if proof.status != 200 or not token:
            raise DouyinError('评论登录验证未完成，请验证抖音登录后重试；尚未提交评论。')
        # Persist before sending. A timeout or process death after this point is never automatically retried.
        attempted = True
        db.update(job['id'], phase='正在发送作者评论', result={'submission_started': True})
        response = await context.request.post('https://creator.douyin.com/aweme/v1/creator/comment/reply/',
            data={'item_id': provider_id, 'comment_Id': '', 'text': job['payload']['text']},
            headers={'Content-Type': 'application/json', 'X-Secsdk-Csrf-Token': token,
                     'Referer': 'https://creator.douyin.com/creator-micro/interaction/comment'},
            timeout=30000, max_redirects=0, max_retries=0)
        if response.status != 200:
            raise DouyinError(f'评论请求返回 HTTP {response.status}，请到作品下核对；不会自动重发。')
        data = await response.json()
        observed = data.get('comment_info') if isinstance(data, dict) else None
        if isinstance(observed, dict):
            candidate = observed.get('comment_id')
            diagnostic = {'text_matches': observed.get('text') == job['payload']['text'],
                          'is_author': observed.get('is_author') is True,
                          'level': observed.get('level') if type(observed.get('level')) in (int, bool) else None}
            if isinstance(candidate, str) and re.fullmatch(r'[A-Za-z0-9@/+=_.-]{8,300}', candidate):
                diagnostic['observed_comment_id'] = candidate
            db.update(job['id'], result={'submission_started': True, 'receipt_check': diagnostic})
        receipt = comment_receipt(data, job['payload']['text'])
        if not receipt['accepted']:
            db.update(job['id'], status='FAILED', phase='评论被平台拒绝', result=receipt,
                      error=f"抖音拒绝本次评论（错误码 {receipt['code']}）；没有自动重试。")
            return
        receipt = {**receipt, 'submission_started': True, 'work_id': job['work_id']}
        db.update(job['id'], status='SENT', phase='评论已取得平台回执', result=receipt, error='')
        # The creator list read-back is additional evidence, not permission to submit again.
        with suppress(Exception):
            listing = await context.request.get('https://creator.douyin.com/aweme/v1/creator/comment/list/',
                params={'item_id': provider_id, 'sort': 'TIME', 'cursor': '0', 'count': '50'}, timeout=20000,
                max_redirects=0, max_retries=0)
            data = await listing.json()
            rows = data.get('comment_info_list') or data.get('comment_list') or []
            matched = any(isinstance(c, dict) and c.get('comment_id') == receipt['comment_id'] and
                          comment_text_matches(c.get('text'), job['payload']['text']) and c.get('is_author') is True for c in rows)
            if listing.status == 200 and data.get('status_code') == 0 and matched:
                db.update(job['id'], status='SENT', phase='已发送并在创作中心回读', result={**receipt, 'read_back': True})
    except Exception as exc:
        current = db.get(job['id'])
        if current['result'].get('comment_id'):
            return
        db.update(job['id'], status='UNKNOWN' if attempted else 'FAILED',
                  phase='评论结果待核对' if attempted else '评论提交前检查未通过',
                  error=str(exc) if isinstance(exc, DouyinError) else '评论检查或发送未完成，请核对登录与作品下评论。',
                  result={**current['result'], 'submission_started': attempted, 'confirmed_not_submitted': not attempted})
    finally:
        if browser:
            await browser.close()

async def check_first_comment(db: DouyinStore, job: dict, pw, storage: Path) -> None:
    """Read only: resolve an uncertain comment without ever issuing another POST."""
    child = db.get(job['parent_id'])
    parent = db.get(child['parent_id']) if child else None
    if not child or child['kind'] != 'FIRST_COMMENT' or child['uid'] != job['uid'] or not parent or parent['uid'] != job['uid'] or parent['work_id'] != child['work_id']:
        raise DouyinError('评论核对的账号或作品不一致。')
    browser = await launch(pw)
    try:
        context = await browser.new_context(storage_state=str(storage))
        owner = await identity(context)
        if owner['uid'] != job['uid']:
            raise DouyinError('请连接原投稿账号后核对评论。')
        owned = await owned_record(context, child['work_id'])
        provider_id = (owned or {}).get('provider_item_id')
        if not provider_id:
            raise DouyinError('未核对到原作品，评论未重发。')
        listing = await context.request.get('https://creator.douyin.com/aweme/v1/creator/comment/list/',
            params={'item_id': provider_id, 'sort': 'TIME', 'cursor': '0', 'count': '50'}, timeout=20000,
            max_redirects=0, max_retries=0)
        data = await listing.json()
        if listing.status != 200 or not isinstance(data, dict) or type(data.get('status_code')) is not int or data['status_code'] != 0:
            raise DouyinError('创作中心评论列表未返回可核对结果；评论未重发。')
        rows = data.get('comment_info_list') or data.get('comment_list') or []
        if not isinstance(rows, list):
            raise DouyinError('创作中心评论列表格式变化；评论未重发。')
        matches, observations = [], []
        target = child['result'].get('comment_id')
        for row in rows:
            if not isinstance(row, dict):
                continue
            text = row.get('text')
            same = comment_text_matches(text, child['payload']['text'])
            created = row.get('create_time')
            recent = str(created).isdigit() and child['created_at'] - 120 <= int(created) <= time.time() + 60
            observations.append({'text_matches': same, 'is_author': row.get('is_author') is True,
                                 'level': row.get('level') if type(row.get('level')) in (int, bool) else None,
                                 'recent': bool(recent), 'fields': sorted(row)[:25]})
            if same and (row.get('comment_id') == target if target else recent):
                with suppress(DouyinError):
                    receipt = comment_receipt({'status_code': 0, 'comment_info': row}, text)
                    matches.append({**receipt, 'read_back': True, 'submission_started': True, 'work_id': child['work_id'],
                                    'normalized_whitespace': text != child['payload']['text']})
        if len(matches) == 1:
            db.update(child['id'], status='SENT', phase='已在创作中心核对作者评论',
                      result={**child['result'], **matches[0]}, error='')
        else:
            db.update(child['id'], error='暂未在创作中心核对到唯一的本次作者评论；未重复发送。')
        db.update(job['id'], status='DONE', phase='已只读核对评论', result={'matched': len(matches) == 1, 'count': len(rows), 'observations': observations[:10]})
    finally:
        await browser.close()


async def run(state: Path, key: str) -> None:
    from patchright.async_api import async_playwright

    db = DouyinStore(str(state))
    job = db.get(key)
    root = state / "platforms/douyin"
    folder = root / "jobs" / key
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not job or job["status"] != "RUNNING":
        return
    stop = asyncio.Event()

    async def beat():
        while not stop.is_set():
            db.touch(key)
            try:
                await asyncio.wait_for(stop.wait(), timeout=10)
            except asyncio.TimeoutError:
                pass

    heartbeat = asyncio.create_task(beat())
    try:
        if job["kind"] == "LOGIN":
            await login(db, job, folder)
        else:
            storage = root / "accounts" / job["uid"] / "storage.json"
            if not storage.is_file():
                raise DouyinError("抖音登录凭证不可用，请重新连接账号。")
            if job["kind"] == "UPLOAD":
                await upload(db, job, storage)
            else:
                async with async_playwright() as pw:
                    if job["kind"] == "VERIFY":
                        browser = await launch(pw)
                        try:
                            context = await browser.new_context(storage_state=str(storage))
                            owner = await identity(context)
                            if owner["uid"] != job["uid"]:
                                raise DouyinError("抖音账号已改变，请重新连接。")
                            db.update(key, status="DONE", result=owner, phase="登录有效")
                        finally:
                            await browser.close()
                    elif job["kind"] == "FIRST_COMMENT":
                        await send_first_comment(db, job, pw, storage)
                    elif job["kind"] == "COMMENT_CHECK":
                        await check_first_comment(db, job, pw, storage)
                    elif job["kind"] == "RECONCILE":
                        receipt = await inspect_work(pw, storage, job["uid"], job["payload"]["work_id"])
                        db.update(key, status="DONE", result=receipt, phase="已核对作品")
    except Exception as exc:
        row = db.get(key)
        if job['kind'] == 'FIRST_COMMENT' and row['result'].get('comment_id'):
            return
        uncertain_comment = job['kind'] == 'FIRST_COMMENT' and row['result'].get('submission_started')
        status = "UNKNOWN" if uncertain_comment or (job["kind"] == "UPLOAD" and row["phase"] != "准备") else "FAILED"
        retained = row['result']
        if job['kind'] == 'RECONCILE':
            retained = {**retained, 'lookup_error': {'code': exc.code if isinstance(exc, DouyinLookupError) else type(exc).__name__,
                                                   'state': exc.state if isinstance(exc, DouyinLookupError) else 'unavailable'}}
        db.update(key, status=status, result={**retained, "confirmed_not_submitted": not uncertain_comment} if job["kind"] == "FIRST_COMMENT" else retained,
                  error=str(exc) if isinstance(exc, DouyinError) else
                  f"抖音连接或检查没有完成（{type(exc).__name__}），请重试；遇到身份验证时请在独立浏览器完成。")
    finally:
        stop.set()
        await heartbeat
        (folder / "qrcode.png").unlink(missing_ok=True)


if __name__ == "__main__":
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--job", required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[a-f0-9]{32}", args.job):
        raise SystemExit("Invalid job ID")
    state = Path(args.state_dir).resolve()
    lock_path = state / "platforms/douyin/worker.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with lock_path.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            asyncio.run(asyncio.wait_for(run(state, args.job), timeout=1950))
        except Exception as exc:
            # Import/bootstrap failures must also leave a durable, secret-free result.
            db = DouyinStore(str(state))
            row = db.get(args.job)
            if row and row["status"] == "RUNNING":
                uncertain = (row["kind"] == "UPLOAD" and row["phase"] != "准备") or (row["kind"] == "FIRST_COMMENT" and row["result"].get("submission_started"))
                status = ("SENT" if row["result"].get("comment_id") else "UNKNOWN" if uncertain else "FAILED") if row["kind"] == "FIRST_COMMENT" else "SUBMITTED" if row["work_id"] else "UNKNOWN" if uncertain else "FAILED"
                db.update(args.job, status=status, error=f"抖音组件未完成（{type(exc).__name__}），请重新检查连接。",
                          result=row["result"] if uncertain or row["work_id"] else {"confirmed_not_submitted": True})
