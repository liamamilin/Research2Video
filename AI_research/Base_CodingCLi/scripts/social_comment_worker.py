"""Independent first-comment execution; never uploads or requeues a video."""
from __future__ import annotations

from contextlib import suppress
import json
from pathlib import Path
import time

from core.social.adapter import AdapterError
from core.social.comments import clean_first_comment
from core.social.kuaishou_comments import KuaishouComments
from core.social.verification import MAX_COMMENT_CHECKS, delay, EVIDENCE


def comment_session(platform, pw, storage, owner, work):
    if platform == 'kuaishou':
        return KuaishouComments(pw, storage, owner, work)
    if platform == 'xiaohongshu':
        from core.social.xiaohongshu_comments import XiaohongshuComments
        return XiaohongshuComments(pw, storage, owner, work)
    raise AdapterError('本平台尚未接入首评。')


def parent_for(db, child):
    parent = db.get(child['parent_id'])
    if (not parent or parent['kind'] != 'UPLOAD' or parent['status'] != 'PUBLISHED'
            or parent['uid'] != child['uid'] or not child['work_id'] or parent['work_id'] != child['work_id']):
        raise AdapterError('原投稿的账号、作品或公开状态未确认，首评未发送。')
    # Unlike the account check in the service, this also runs in the detached worker.
    profile = Path(db.path).parent / 'account.json'
    if not profile.is_file() or json.loads(profile.read_text()).get('uid') != child['uid']:
        raise AdapterError('请连接原投稿账号，首评未发送。')
    return parent


async def send_first_comment(db, child, provider, pw, storage):
    session = None
    try:
        parent_for(db, child)
        current = db.get(child['id'])
        if current['result'].get('submission_started') or current['result'].get('comment_id'):
            db.recover_comment(current)
            return
        text = clean_first_comment(db.platform, {'enabled':True,'text':child['payload']['text']})['text']
        proof = await provider.inspect_work(pw, storage, child['uid'], child['work_id'])
        if not (proof.get('public') is True and proof.get('owned') is True and proof.get('uid') == child['uid']
                and proof.get('work_id') == child['work_id'] and proof.get('evidence') == EVIDENCE.get(db.platform)):
            raise AdapterError('这条视频尚未确认公开，首评未发送，可稍后重新检查。')
        session = comment_session(db.platform, pw, storage, child['uid'], child['work_id'])
        await session.open()
        matches = session.matches(text)
        if len(matches) == 1:
            db.update(child['id'], status='SENT', phase='已找到相同作者评论，未重复发送', result=matches[0], error='')
            return
        if matches:
            raise AdapterError('作品下已有多条相同作者评论，请人工核对，不会继续发送。')

        def before_send():
            parent_for(db, child)
            current = db.get(child['id'])
            if current['status'] != 'RUNNING' or current['result'].get('submission_started'):
                raise AdapterError('评论任务已变化，停止重复发送。')
            if not db.update(child['id'], expected='RUNNING', phase='正在发送首条评论',
                             result={'submission_started':True,'submission_started_at':time.time()}):
                raise AdapterError('评论任务已变化，停止发送。')

        def save_receipt(receipt):
            db.update(child['id'], status='SENT' if receipt['accepted'] else 'FAILED',
                      phase='首评已取得平台回执' if receipt['accepted'] else '平台拒绝首评',
                      result={**db.get(child['id'])['result'],**receipt,'confirmed_not_submitted':False},
                      error='' if receipt['accepted'] else f"平台拒绝评论（错误码 {receipt['code']}），未自动重发。")

        receipt = await session.send(text, before_send, save_receipt)
        if receipt['accepted']:
            with suppress(Exception):
                await session.load()
                matches = session.matches(text, receipt['comment_id'])
                if len(matches) == 1:
                    db.update(child['id'], phase='首评已发送并回读', result={**db.get(child['id'])['result'],**matches[0]})
                else:
                    db.update(child['id'], phase='已取得发送回执，等待平台评论列表同步')
    except Exception as exc:
        current = db.get(child['id'])
        if current['result'].get('comment_id'):
            return
        started = current['result'].get('submission_started') is True
        db.update(child['id'], status='UNKNOWN' if started else 'FAILED',
                  phase='首评结果待核对' if started else '首评发送前检查未通过',
                  result={**current['result'],'confirmed_not_submitted':not started},
                  error=str(exc) if isinstance(exc, AdapterError) else '首评未完成，请核对登录和作品评论；结果不明不会重发。')
    finally:
        if session:
            await session.close()


async def check_first_comment(db, check, pw, storage):
    child = db.get(check['parent_id'])
    if not child or child['kind'] != 'FIRST_COMMENT' or child['uid'] != check['uid']:
        raise AdapterError('首评核对任务与账号不一致。')
    parent_for(db, child)
    session = comment_session(db.platform, pw, storage, child['uid'], child['work_id'])
    try:
        await session.open()
        matches = session.matches(child['payload']['text'], child['result'].get('comment_id',''))
        if len(matches) == 1:
            db.update(child['id'], status='SENT', phase='已回读首条评论，未重发', error='',
                      result={**child['result'],**matches[0]})
        db.update(check['id'], status='RECORDED', phase='评论只读核对完成', result={'matches':len(matches)})
    finally:
        current = db.get(child['id'])
        attempts = int(current['result'].get('check_attempts', 0)) + 1
        checked_at = time.time()
        db.update(child['id'], result={**current['result'],'checked_at':checked_at,'check_attempts':attempts,
                                      'next_check_at':checked_at+delay(attempts)},
                  phase='首评已发送并回读' if current['result'].get('read_back') else
                        '评论已发送，平台评论列表仍未显示；自动核对已停止，可手动核对，不重发' if attempts >= MAX_COMMENT_CHECKS
                        else '评论暂未回读，继续自动只读核对，不重发')
        await session.close()
