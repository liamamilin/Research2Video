"""Execute one account-scoped social publishing job in the fixed isolated runtime."""
from __future__ import annotations

import argparse
import asyncio
from contextlib import suppress
import fcntl
import hashlib
import importlib.util
import os
from pathlib import Path
import re
import sys
import time
import signal
import json
import shutil
import subprocess
from types import ModuleType, SimpleNamespace

if __name__ == '__main__':
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
    package=ModuleType('core');package.__path__=[str(Path(__file__).resolve().parents[1]/'core')];sys.modules['core']=package

from core.social.adapter import AdapterError, accepted_without_work_id, clean_owner, clean_work, submission_evidence, upload_failure_details
from core.social.registry import PLATFORMS, adapter
from core.social.store import SocialStore
from core.social.verification import submission_correlations
from scripts.douyin_worker import launch, private_json


def credential(root: Path, uid: str) -> Path:
    return root/'accounts'/hashlib.sha256(uid.encode()).hexdigest()/'storage.json'


UPSTREAM = {
    'kuaishou': ('uploader.ks_uploader.main','fc97d1f83aa0d531284365d2e54a6e043bef420f8b8088b3e90b9ed785326c66'),
    'xiaohongshu': ('uploader.xiaohongshu_uploader.main','f4885e1d483a61221bde43abaa3aa6ccf1251aa7c6f953098324a03a919f693c'),
    'weixin_channels': ('uploader.tencent_uploader.main','7869fed61138ea3d39b7efdb63b64059373ffbeaec811b59a3a9f3ad25a88b9a'),
}


def verify_upstream(platform: str) -> None:
    module, expected = UPSTREAM[platform]
    try:
        source = Path(importlib.util.find_spec(module).origin)
        actual = hashlib.sha256(source.read_bytes()).hexdigest()
    except (ImportError,AttributeError,OSError,ValueError,TypeError):
        raise AdapterError('固定版本上传组件缺失，请重新验证运行环境。') from None
    if actual != expected:
        raise AdapterError('上传组件与已审查版本不一致，已停止本次投稿。')


def silence_upstream_logs() -> None:
    # This process serves exactly one provider job. Prevent upstream imports from
    # installing diagnose=True file sinks containing authenticated locals.
    from loguru import logger
    logger.remove()
    logger.add=lambda *args, **kwargs: 0


async def login(db, job, provider, pw, folder):
    browser=await launch(pw,headless=False)
    try:
        context=await browser.new_context(service_workers='block')
        page=await provider.prepare_login(context)
        db.update(job['id'],phase='请使用平台 App 扫码，并在手机确认')
        deadline=asyncio.get_running_loop().time()+330
        while asyncio.get_running_loop().time()<deadline:
            if db.get(job['id'])['status']!='RUNNING':
                return
            with suppress(Exception):
                await provider.capture_qr(page,folder/'qrcode.png')
                if (folder/'qrcode.png').is_file():
                    (folder/'qrcode.png').chmod(0o600)
            try:
                owner=clean_owner(await provider.identity(context))
            except AdapterError:
                await asyncio.sleep(3)
                continue
            if db.get(job['id'])['status']!='RUNNING':
                return
            path=folder/'storage.json'
            await context.storage_state(path=str(path));path.chmod(0o600)
            if not db.update(job['id'],expected='RUNNING',status='DONE',phase='账号身份已核对',result=owner,error=''):
                path.unlink(missing_ok=True)
            return
        raise AdapterError('二维码已过期或身份验证未完成，请重新连接；需要本人验证时在独立窗口完成。')
    finally:
        await browser.close()


async def upload(db, job, provider, pw, storage):
    """One final provider POST, durable receipt before UI acknowledgement, no automatic resubmit."""
    current=db.get(job['id'])
    if current['work_id']:
        db.update(job['id'],status='SUBMITTED',phase='保留已有作品回执，不再提交');return
    if accepted_without_work_id(db.platform, current['result']):
        db.update(job['id'],status='SUBMITTED',phase='已投稿，待补作品链接',error='');return
    if (current['result'].get('invocation_started') or current['result'].get('submission_started') or current['result'].get('accepted')) and not current['result'].get('confirmed_not_submitted'):
        db.update(job['id'],status='UNKNOWN',phase='保留已有投稿记录，请核对结果');return
    attempted=False;invoked=False;browsers=[]
    future=asyncio.get_running_loop().create_future()

    async def guard(route):
        nonlocal attempted
        request=route.request
        if request.method!='POST' or not provider.is_submit(request.url):
            return await route.continue_()
        if attempted:
            return await route.abort()
        attempted=True
        result = db.get(job['id'])['result']
        try:
            body = request.post_data_json
            correlations = submission_correlations(body)
        except Exception:
            correlations = []
        db.update(job['id'],phase='已提交请求，等待平台回执',result={**result,'invocation_started':True,'submission_started':True,
                  'submission_started_at':time.time(),'correlations':[value for value in correlations if value]})
        response=None
        try:
            response=await route.fetch(timeout=60000,max_redirects=0,max_retries=0)
            result=db.get(job['id'])['result']
            db.update(job['id'],result={**result,'submission_response':submission_evidence(db.platform,response.status,None)})
            if response.status!=200:
                raise AdapterError(f'投稿返回 HTTP {response.status}，请到创作中心核对；不会自动重投。')
            body=await response.json()
            result=db.get(job['id'])['result']
            db.update(job['id'],result={**result,'submission_response':submission_evidence(db.platform,response.status,body)})
            receipt=provider.submission_receipt(body)
            if receipt.get('accepted') is True:
                if accepted_without_work_id(db.platform, receipt):
                    receipt={'accepted':True,'awaiting_work_id':True,'work_id':'','url':'','uid':job['uid'],'public':False}
                    db.update(job['id'],status='SUBMITTED',result={**db.get(job['id'])['result'],**receipt},phase='已投稿，待补作品链接')
                else:
                    target=clean_work(receipt.get('work_id'))
                    receipt={'accepted':True,'work_id':target,'url':receipt.get('url',''),'uid':job['uid'],'public':False}
                    db.update(job['id'],work_id=target,result={**db.get(job['id'])['result'],**receipt},phase='已取得作品 ID，待核对公开状态')
            elif receipt.get('accepted') is False and receipt.get('confirmed_not_submitted') is True:
                receipt={'accepted':False,'confirmed_not_submitted':True,'code':receipt.get('code','')}
                db.update(job['id'],result={**db.get(job['id'])['result'],**receipt})
            else:
                raise AdapterError('投稿响应无法确认，请到创作中心核对；不会自动重投。')
            with suppress(Exception):
                await route.fulfill(response=response)
            if not future.done():
                future.set_result(receipt)
        except Exception as exc:
            with suppress(Exception):
                # The platform may already have created the work. Let its
                # original response reach the page even when our parser fails.
                # Never replay the POST to obtain a different response.
                if response is not None:
                    await route.fulfill(response=response)
                else:
                    await route.abort()
            if not future.done():
                future.set_exception(exc)

    async def guarded_launch(**kwargs):
        browser=await launch(pw,headless=False);browsers.append(browser)

        async def new_context(**options):
            options['storage_state']=str(storage)
            options['service_workers']='block'
            options.pop('permissions',None)
            context=await browser.new_context(**options)
            owner=clean_owner(await provider.identity(context))
            if owner['uid']!=job['uid']:
                raise AdapterError('登录账号与原投稿账号不一致，已停止操作。')
            await context.route('**/*',guard)
            return context

        return SimpleNamespace(new_context=new_context,close=browser.close)

    task=None
    try:
        invoked=True
        duration = None
        binary = shutil.which('ffprobe')
        if binary:
            try:
                info = subprocess.run([binary,'-v','error','-show_entries','format=duration','-of','json',job['payload']['video_path']],
                                      capture_output=True, text=True, timeout=10, check=True)
                duration = float(json.loads(info.stdout)['format']['duration'])
            except (OSError, ValueError, KeyError, subprocess.SubprocessError):
                pass
        db.update(job['id'],phase='正在准备平台上传页面',result={'invocation_started':True,'duration_seconds':duration})
        task=asyncio.create_task(provider.upload(SimpleNamespace(chromium=SimpleNamespace(launch=guarded_launch)),job['payload'],storage))
        done,_=await asyncio.wait((task,future),timeout=1800,return_when=asyncio.FIRST_COMPLETED)
        if future in done:
            receipt=future.result()
            if receipt['accepted']:
                db.update(job['id'],status='SUBMITTED',phase='已投稿，待补作品链接' if receipt.get('awaiting_work_id') else '已投稿，待核对平台公开状态',error='')
            else:
                db.update(job['id'],status='FAILED',phase='平台明确拒绝投稿',error=f"平台拒绝投稿（错误码 {receipt['code']}），未自动重试。")
        elif task in done:
            task.result()
            raise AdapterError('上传窗口已结束，尚未取得可靠回执。请到创作中心核对；不会自动重投。')
        else:
            raise AdapterError('上传等待超时，请到创作中心核对；不会自动重投。')
    except Exception as exc:
        row=db.get(job['id'])
        details=upload_failure_details(exc)
        error=str(exc) if isinstance(exc,AdapterError) else '平台上传未完成，结果待核对；系统不会自动重投。'
        db.update(job['id'],status='SUBMITTED' if row['work_id'] or accepted_without_work_id(db.platform,row['result']) else 'UNKNOWN' if invoked or attempted else 'FAILED',
                  phase='投稿结果待核对',result={**(row['result'] if invoked or attempted else {'confirmed_not_submitted':True}),'failure':details},
                  error=f"{error}（阶段：{details['stage']}；异常：{details['error_type']}）")
    finally:
        if task:
            task.cancel()
        if not future.done():
            future.cancel()
        elif not future.cancelled():
            future.exception()
        for browser in browsers:
            with suppress(Exception):
                await asyncio.wait_for(browser.close(),timeout=10)
        if task:
            done,_=await asyncio.wait({task},timeout=5)
            if task in done and not task.cancelled():
                task.exception()


async def run(state: Path, platform: str, key: str):
    from patchright.async_api import async_playwright
    silence_upstream_logs()
    db=SocialStore(str(state),platform);job=db.get(key)
    if not job or job['status']!='RUNNING':
        return
    root=state/'platforms'/platform;folder=root/'jobs'/key;folder.mkdir(parents=True,exist_ok=True,mode=0o700)
    provider=adapter(platform);stop=asyncio.Event()

    async def beat():
        while not stop.is_set():
            db.update(key,heartbeat=time.time())
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(stop.wait(),timeout=10)

    heartbeat=asyncio.create_task(beat())
    try:
        async with async_playwright() as pw:
            if job['kind']=='LOGIN':
                if job['payload'].get('purpose') == 'comments':
                    from core.social.xiaohongshu_comments import login_comments
                    await login_comments(db,job,pw,folder,credential(root,job['uid']))
                else:
                    await login(db,job,provider,pw,folder)
            else:
                storage=credential(root,job['uid'])
                if not storage.is_file():
                    raise AdapterError('平台登录凭证不可用，请重新连接。')
                if job['kind']=='UPLOAD':
                    verify_upstream(platform)
                    provider.validate_payload(job['payload'])
                    await upload(db,job,provider,pw,storage)
                elif job['kind']=='VERIFY':
                    browser=await launch(pw)
                    try:
                        context=await browser.new_context(storage_state=str(storage),service_workers='block');owner=clean_owner(await provider.identity(context))
                        if owner['uid']!=job['uid']:
                            raise AdapterError('实际登录账号已改变，请重新连接原账号。')
                        db.update(key,status='DONE',phase='登录有效',result=owner,error='')
                    finally:
                        await browser.close()
                elif job['kind']=='RECONCILE':
                    if 'lookup' in job['payload']:
                        receipt=await asyncio.wait_for(provider.inspect_submission(pw,storage,job['uid'],job['payload']['lookup']), timeout=120)
                    else:
                        receipt=await asyncio.wait_for(provider.inspect_work(pw,storage,job['uid'],job['payload']['work_id']), timeout=120)
                    db.update(key,status='DONE',phase='作品核对完成',result=receipt,error='')
                elif job['kind'] in ('FIRST_COMMENT','COMMENT_CHECK'):
                    if platform not in ('kuaishou','xiaohongshu'):
                        raise AdapterError('本平台尚未接入首条评论。')
                    from scripts.social_comment_worker import send_first_comment, check_first_comment
                    operation = (send_first_comment(db,job,provider,pw,storage) if job['kind']=='FIRST_COMMENT'
                                 else check_first_comment(db,job,pw,storage))
                    await asyncio.wait_for(operation, timeout=180)
    except Exception as exc:
        row=db.get(key);started=row['result'].get('invocation_started') or row['result'].get('submission_started')
        if row['kind']=='FIRST_COMMENT':
            db.recover_comment(row)
            return
        status=('SUBMITTED' if row['work_id'] or accepted_without_work_id(db.platform,row['result']) else 'UNKNOWN' if started else 'FAILED') if row['kind']=='UPLOAD' else 'FAILED'
        db.update(key,status=status,result={**row['result'],'confirmed_not_submitted':status=='FAILED' and not started and not row['work_id']},
                  error=str(exc) if isinstance(exc,AdapterError) else f'平台操作未完成（{type(exc).__name__}），请核对登录与创作中心。')
    finally:
        stop.set();await heartbeat
        (folder/'qrcode.png').unlink(missing_ok=True)


def main():
    os.umask(0o077)
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir',required=True);parser.add_argument('--platform',required=True,choices=PLATFORMS);parser.add_argument('--job',required=True)
    args=parser.parse_args()
    if not re.fullmatch(r'[a-f0-9]{32}',args.job):
        raise SystemExit('Invalid job ID')
    state=Path(args.state_dir).resolve();root=state/'platforms'/args.platform;root.mkdir(parents=True,exist_ok=True,mode=0o700)
    with (root/'worker.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        signal.alarm(1980)
        try:
            asyncio.run(asyncio.wait_for(run(state,args.platform,args.job),timeout=1950))
        except Exception as exc:
            db=SocialStore(str(state),args.platform);row=db.get(args.job)
            if row and row['status']=='RUNNING':
                if row['kind']=='FIRST_COMMENT':
                    db.recover_comment(row)
                    return
                started=row['result'].get('invocation_started') or row['result'].get('submission_started')
                status=('SUBMITTED' if row['work_id'] or accepted_without_work_id(db.platform,row['result']) else 'UNKNOWN' if started else 'FAILED') if row['kind']=='UPLOAD' else 'FAILED'
                db.update(row['id'],status=status,result={**row['result'],'confirmed_not_submitted':status=='FAILED' and not started and not row['work_id']},error=f'平台组件执行未完成（{type(exc).__name__}），已保留任务结果。')


if __name__=='__main__':
    main()
