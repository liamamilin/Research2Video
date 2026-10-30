"""Diagnose a Kuaishou upload preparation without allowing final publication.

Run inside the pinned social runtime, with its PYTHONPATH and browser path.
The original queue record is read only. Browser mutations are denied unless
they are reviewed file-upload or read-only lookup endpoints. No publishing
request can leave this browser, including an unrecognized replacement endpoint.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import suppress
import json
import os
from pathlib import Path
import re
import sys
from types import ModuleType, SimpleNamespace
from urllib.parse import urlsplit

if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    package = ModuleType('core')
    package.__path__ = [str(Path(__file__).resolve().parents[1] / 'core')]
    sys.modules['core'] = package

from core.social.adapter import AdapterError, clean_owner, upload_failure_details
from core.social.registry import adapter
from core.social.store import SocialStore
from scripts.social_publish_worker import credential, silence_upstream_logs, verify_upstream
from scripts.douyin_worker import launch


ALLOWED_PREPARATION = {
    ('cp.kuaishou.com', '/rest/cp/works/v2/video/pc/upload/pre'): 'upload_pre',
    ('cp.kuaishou.com', '/rest/cp/works/v2/video/pc/upload/finish'): 'upload_finish',
    ('cp.kuaishou.com', '/rest/cp/works/v2/video/pc/upload/cover/upload'): 'cover_upload',
    ('cp.kuaishou.com', '/rest/cp/works/v2/video/pc/upload/isolation/pre'): 'upload_pre',
    ('cp.kuaishou.com', '/rest/cp/works/v2/video/pc/upload/isolation/finish'): 'upload_finish',
    ('cp.kuaishou.com', '/rest/cp/works/v2/video/pc/upload/frameUpload'): 'file_fragment',
    ('cp.kuaishou.com', '/rest/cp/works/v4/video/pc/upload/material/specified'): 'asset_prepare',
    ('cp.kuaishou.com', '/rest/cp/works/v4/video/pc/cover/edit/recommend/submit'): 'cover_prepare',
    ('cp.kuaishou.com', '/rest/cp/works/v4/video/pc/cover/edit/cover/upload'): 'cover_upload',
    ('cp.kuaishou.com', '/rest/cp/works/v4/video/pc/cover/edit/recommend/query'): 'cover_prepare',
    ('cp.kuaishou.com', '/rest/cp/works/v4/video/pc/cover/edit/draft/get'): 'cover_prepare',
    ('cp.kuaishou.com', '/rest/cp/works/v4/video/pc/upload/cover/extract'): 'cover_prepare',
    ('cp.kuaishou.com', '/rest/cp/works/v2/video/pc/tag/search'): 'topic_lookup',
    ('cp.kuaishou.com', '/rest/cp/creator/pc/home/infoV2'): 'account_lookup',
    ('cp.kuaishou.com', '/rest/v2/creator/pc/authority/account/current'): 'account_lookup',
    ('cp.kuaishou.com', '/rest/cp/works/v2/video/pc/photo/list'): 'works_lookup',
    ('upload.kuaishouzt.com', '/api/upload/fragment'): 'file_fragment',
    ('upload.kuaishouzt.com', '/api/upload/complete'): 'file_complete',
}

# Read-only configuration/list operations observed on the authenticated creator
# upload page. The method/path allowlist remains exact; notification mutations,
# publish/submit and any unknown replacement endpoint are still denied.
for _path in (
    '/rest/v2/creator/pc/frontend/kswitch/config',
    '/rest/bamboo/pc/hotspot/show',
    '/rest/wd/pc/emotion/package/list',
    '/rest/wd/kconf/get',
    '/rest/cp/creator/comment/report/menu',
    '/rest/cp/works/v2/common/pc/fe/kconf',
    '/rest/cp/works/v2/video/pc/upload/tips/show',
    '/rest/v2/creator/pc/school/category/tree',
    '/rest/cp/works/v2/common/pc/current/user',
    '/rest/cp/works/v2/collection/tab',
    '/rest/creator/v2/inspiration/pc/home/material/list',
    '/rest/cp/creator/pc/home/userInfo',
    '/rest/cp/creator/analysis/export/task/list',
    '/rest/v2/creator/pc/popup/list',
    '/rest/cp/works/v2/video/pc/upload/config',
    '/rest/cp/creator/analysis/pc/home/photo/list',
    '/rest/cp/creator/analysis/pc/home/author/overview',
    '/rest/cp/works/v2/video/pc/home/photo/list',
    '/rest/v2/creator/publish/pc/isolation/config',
    '/rest/cp/works/atlas/pc/upload/config',
    '/rest/cp/works/v2/common/pc/w/info',
    '/rest/cp/works/v2/collection/visible',
    '/rest/cp/works/v2/video/pc/upload/domain/list',
    '/rest/cp/works/v2/video/pc/snapshot/info',
    '/rest/cp/works/v2/video/pc/upload/cover/profile',
    '/rest/cp/works/v2/collection/canAdd',
    '/rest/cp/works/v2/video/pc/upload/recTag',
):
    ALLOWED_PREPARATION[('cp.kuaishou.com', _path)] = 'creator_lookup'


def preparation_operation(method: str, url: str) -> str:
    """Return only reviewed preparation operations; final and unknown mutations are denied."""
    try:
        parts = urlsplit(url)
        if (parts.scheme != 'https' or parts.port not in (None, 443)
                or parts.username is not None or parts.password is not None):
            return ''
        if method in ('GET', 'HEAD', 'OPTIONS'):
            return 'read'
        if method == 'POST':
            return ALLOWED_PREPARATION.get((parts.hostname, parts.path), '')
    except (ValueError, TypeError):
        pass
    return ''


def endpoint_label(url: str) -> str:
    """Describe only a first-party API path; drop queries and dynamic path segments."""
    try:
        parts = urlsplit(url)
        if parts.hostname not in ('cp.kuaishou.com', 'www.kuaishou.com', 'upload.kuaishouzt.com'):
            return 'other-host'
        segments = [segment if re.fullmatch(r'[A-Za-z_-]{1,30}|v[0-9]{1,2}', segment) else ':id'
                    for segment in parts.path.split('/') if segment]
        return parts.hostname + '/' + '/'.join(segments[:16])
    except (TypeError, ValueError):
        return 'unknown-host'


def profile_evidence(data: object) -> dict:
    """Retain account-response structure and public UID/nickname only, never auth fields."""
    public_fields = {}

    def describe(value, path='', depth=0):
        if not isinstance(value, dict) or depth > 3:
            return type(value).__name__
        fields = {}
        for key, nested in list(value.items())[:80]:
            if not isinstance(key, str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,49}', key):
                continue
            current = f'{path}.{key}' if path else key
            if key in ('userId', 'userIdStr', 'uid') and (type(nested) is int or isinstance(nested, str)):
                if re.fullmatch(r'[1-9][0-9]{0,39}', str(nested)):
                    public_fields[current] = str(nested)
            if key in ('userName', 'nickName', 'nickname') and isinstance(nested, str) and len(nested) <= 200:
                public_fields[current] = nested
            fields[key] = describe(nested, current, depth+1)
        return fields

    schema = describe(data)
    return {'schema': schema, 'public_fields': public_fields,
            'result': data.get('result') if isinstance(data, dict) and type(data.get('result')) is int else None}


def preparation_status(data: object) -> dict:
    """Keep numeric status fields only, never asset credentials or provider messages."""
    if not isinstance(data, dict):
        return {}
    output = {key: value for key in ('code', 'result', 'error_code')
              if type(value := data.get(key)) is int and abs(value) <= 2**53}
    if type(data.get('success')) is bool:
        output['success'] = data['success']
    return output


def pending_locations(task: asyncio.Task) -> list[dict]:
    """Return static coroutine locations only, never arguments, locals, URLs or exception text."""
    current = task.get_coro()
    locations = []
    while current is not None and len(locations) < 20:
        frame = getattr(current, 'cr_frame', None) or getattr(current, 'gi_frame', None)
        if frame is not None:
            locations.append({'file': Path(frame.f_code.co_filename).name,
                              'function': frame.f_code.co_name, 'line': frame.f_lineno})
        current = getattr(current, 'cr_await', None) or getattr(current, 'gi_yieldfrom', None)
    return locations


def exception_summary(error: Exception) -> dict:
    """Classify known UI failures and retain static locations without the exception's raw text."""
    locations, seen = [], set()
    current = error
    kind = 'unknown'
    while id(current) not in seen:
        seen.add(id(current))
        message = str(current)
        if 'strict mode violation' in message:
            kind = 'ambiguous_control'
        elif '未找到快手发布按钮' in message:
            kind = 'missing_publish_control'
        elif 'Timeout' in message or 'timed out' in message:
            kind = 'control_timeout'
        trace = current.__traceback__
        while trace is not None and len(locations) < 30:
            locations.append({'file': Path(trace.tb_frame.f_code.co_filename).name,
                              'function': trace.tb_frame.f_code.co_name, 'line': trace.tb_lineno})
            trace = trace.tb_next
        nested = current.__cause__ or current.__context__
        if not isinstance(nested, Exception):
            break
        current = nested
    return {'kind': kind, 'locations': locations}


async def upload_surfaces(browsers: list) -> list[dict]:
    """Describe upload controls/frames and known blocking notices, excluding page bodies and credentials."""
    output = []
    phrases = ('上传视频', '选择视频', '点击上传', '登录已过期', '立即登录', '实名认证', '安全验证',
               '创作者服务协议', '同意协议', '暂不支持', '账号异常', '上传失败')
    for browser in browsers:
        for context in getattr(browser, 'contexts', []):
            for page in context.pages:
                for frame in page.frames:
                    record = {'page': endpoint_label(page.url), 'frame': endpoint_label(frame.url)}
                    with suppress(Exception):
                        files = frame.locator('input[type="file"]')
                        record['file_inputs'] = await files.count()
                        record['notices'] = [text for text in phrases if await frame.get_by_text(text, exact=False).count()]
                        labels = await frame.get_by_role('button').all_text_contents()
                        record['upload_buttons'] = [label.strip()[:100] for label in labels
                            if any(word in label for word in ('上传', '选择', '发布', '同意', '知道', '确认', '继续'))][:30]
                    output.append(record)
                    if len(output) >= 12:
                        return output
    return output


async def diagnose(state: Path, job_id: str, *, timeout: int = 180, hold_seconds: int = 0,
                   screenshot: Path | None = None) -> dict:
    """Prepare the exact stored payload once, stopping before publication and preserving the task."""
    from patchright.async_api import async_playwright

    silence_upstream_logs()
    db = SocialStore(str(state), 'kuaishou')
    job = db.get(job_id)
    if not job or job['kind'] != 'UPLOAD' or job['status'] not in ('UNKNOWN', 'FAILED'):
        raise AdapterError('仅诊断现有待核对或失败的快手任务。')
    provider = adapter('kuaishou')
    verify_upstream('kuaishou')
    provider.validate_payload(job['payload'])
    storage = credential(state / 'platforms/kuaishou', job['uid'])
    if not storage.is_file():
        raise AdapterError('平台登录记录缺失，无法诊断。')
    report = {'job_id': job_id, 'platform': 'kuaishou', 'publishing_protection_active': True,
              'final_publish_requests_intercepted': 0,
              'blocked_unknown_mutations': 0, 'blocked_endpoints': [], 'operations': {}, 'failure': None,
              'responses': {}, 'request_failures': {}, 'provider_results': {},
              'phase': '准备上传组件'}
    browsers, observations, task = [], [], None
    stopped = asyncio.Event()

    async def guard(route):
        request = route.request
        if provider.is_submit(request.url):
            report['final_publish_requests_intercepted'] += 1
            stopped.set()
            return await route.abort()
        operation = preparation_operation(request.method, request.url)
        if not operation:
            report['blocked_unknown_mutations'] += 1
            label = endpoint_label(request.url)
            if label not in report['blocked_endpoints'] and len(report['blocked_endpoints']) < 20:
                report['blocked_endpoints'].append(label)
            return await route.abort()
        if operation != 'read':
            report['operations'][operation] = report['operations'].get(operation, 0) + 1
        return await route.continue_()

    async with async_playwright() as pw:
        async def guarded_launch(**kwargs):
            report['phase'] = '启动独立浏览器'
            browser = await launch(pw, headless=False)
            browsers.append(browser)

            async def new_context(**options):
                options['storage_state'] = str(storage)
                options['service_workers'] = 'block'
                options.pop('permissions', None)
                context = await browser.new_context(**options)
                await context.route('**/*', guard)
                async def observe(response):
                    parts = urlsplit(response.url)
                    operation = preparation_operation(response.request.method, response.url)
                    if operation and operation != 'read':
                        counts = report['responses'].setdefault(operation, {})
                        code = str(response.status)
                        counts[code] = counts.get(code, 0) + 1
                        if operation in ('upload_pre', 'file_fragment', 'file_complete', 'upload_finish', 'cover_upload'):
                            with suppress(Exception):
                                status = preparation_status(await response.json())
                                if status:
                                    report['provider_results'][operation] = status
                    if (parts.hostname == 'cp.kuaishou.com'
                            and parts.path == '/rest/v2/creator/pc/authority/account/current'):
                        with suppress(Exception):
                            report['account_response'] = profile_evidence(await response.json())

                context.on('response', lambda response: observations.append(asyncio.create_task(observe(response))))
                def request_failed(request):
                    operation = preparation_operation(request.method, request.url)
                    if operation and operation != 'read':
                        report['request_failures'][operation] = report['request_failures'].get(operation, 0) + 1

                context.on('requestfailed', request_failed)
                class KeepDiagnosticPage:
                    def __init__(self, page):
                        self.page = page

                    def __getattr__(self, name):
                        return getattr(self.page, name)

                    async def close(self):
                        if not hold_seconds:
                            await self.page.close()

                class IdentityContext:
                    def __getattr__(self, name):
                        return getattr(context, name)

                    async def new_page(self):
                        return KeepDiagnosticPage(await context.new_page())

                report['phase'] = '核对当前账号'
                owner = clean_owner(await provider.identity(IdentityContext()))
                if owner['uid'] != job['uid']:
                    raise AdapterError('当前登录账号与原任务不一致，停止诊断。')
                report['phase'] = '打开上传页面'
                class DiagnosticContext:
                    def __getattr__(self, name):
                        return getattr(context, name)

                    async def close(self):
                        # The diagnostic owns final cleanup; keep its surface
                        # available just long enough to classify a failure.
                        pass

                return DiagnosticContext()

            async def close():
                pass

            return SimpleNamespace(new_context=new_context, close=close)

        stopped_task = asyncio.create_task(stopped.wait())
        try:
            task = asyncio.create_task(provider.upload(
                SimpleNamespace(chromium=SimpleNamespace(launch=guarded_launch)), job['payload'], storage,
            ))
            done, _ = await asyncio.wait((task, stopped_task), timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
            if stopped_task not in done:
                if task in done:
                    task.result()
                else:
                    report['pending_locations'] = pending_locations(task)
                    raise TimeoutError('Upload preparation timed out')
        except Exception as error:
            report['failure'] = upload_failure_details(error)
            report['exception_summary'] = exception_summary(error)
        finally:
            if observations:
                await asyncio.gather(*observations, return_exceptions=True)
            report['upload_surfaces'] = await upload_surfaces(browsers)
            if screenshot:
                for browser in browsers:
                    for context in browser.contexts:
                        for page in context.pages:
                            if endpoint_label(page.url) == 'cp.kuaishou.com/article/publish/video':
                                with suppress(Exception):
                                    await page.screenshot(path=str(screenshot), full_page=True, timeout=5000)
            # Print no provider messages, payloads, authenticated URLs or credentials.
            print(json.dumps(report, ensure_ascii=False), flush=True)
            if hold_seconds:
                await asyncio.sleep(min(hold_seconds, 60))
            if task:
                task.cancel()
            stopped_task.cancel()
            for browser in browsers:
                with suppress(Exception):
                    await asyncio.wait_for(browser.close(), timeout=10)
            if task:
                with suppress(asyncio.CancelledError, Exception):
                    await asyncio.wait_for(task, timeout=5)
            with suppress(asyncio.CancelledError):
                await stopped_task
    assert db.get(job_id) == job, 'The diagnostic must never change the original task'
    return report


def main() -> None:
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir', type=Path, required=True)
    parser.add_argument('--job', required=True)
    parser.add_argument('--hold-seconds', type=int, default=0)
    parser.add_argument('--timeout', type=int, default=180)
    parser.add_argument('--screenshot', type=Path)
    args = parser.parse_args()
    asyncio.run(diagnose(args.state_dir.resolve(), args.job, timeout=args.timeout,
                        hold_seconds=args.hold_seconds, screenshot=args.screenshot))


if __name__ == '__main__':
    main()
