"""Platform-neutral account/queue orchestration. Provider details live in independent adapters."""
from __future__ import annotations

import base64
from contextlib import contextmanager
from datetime import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time

from core.social.adapter import AdapterError, accepted_without_work_id, clean_owner, clean_work
from core.social.registry import PLATFORMS, DEFERRED_PLATFORMS, adapter, definition
from core.social.store import SocialStore, ACTIVE
from core.social.comments import clean_first_comment, COMMENT_PLATFORMS
from core.social.verification import EVIDENCE, STATE_LABELS, delay, timestamp
from core.social.verification import MAX_COMMENT_CHECKS, check_progress, lookup_revision
from core.social.reconciliation import lookup_for
from core.social.session_health import assess, verify_due
from core.video import store as video_store
from core.video.publication_identity import identify
from scripts.douyin_worker import private_json
from web.services import douyin, platform_manager, video_render
from web.settings import get_settings

ROOT = Path(__file__).resolve().parents[2]
runtime = douyin.runtime


def directory(platform: str) -> Path:
    if platform not in PLATFORMS:
        raise AdapterError('这个平台尚未接入。')
    return Path(get_settings().paths.state_dir).resolve() / 'platforms' / platform


def store(platform: str) -> SocialStore:
    return SocialStore(get_settings().paths.state_dir, platform)


def credential(root: Path, uid: str) -> Path:
    return root / 'accounts' / hashlib.sha256(uid.encode()).hexdigest() / 'storage.json'


@contextmanager
def dispatch_lock(platform: str):
    root = directory(platform); root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (root / 'dispatch.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def account(platform: str) -> dict:
    path = directory(platform) / 'account.json'
    if not path.is_file():
        return {'connected': False}
    try:
        value = json.loads(path.read_text())
        return {'connected': True, **clean_owner(value), 'verified_at': value.get('verified_at'),
                **({'comments_verified_at':value['comments_verified_at']} if value.get('comments_verified_at') else {})}
    except (ValueError, AdapterError):
        return {'connected': False}


def uid(platform: str) -> str:
    profile = account(platform)
    if not profile['connected']:
        raise AdapterError('请先连接这个平台的账号。')
    return profile['uid']


def public_job(platform: str, row: dict, *, comments: dict | None = None, checking: set[str] | None = None) -> dict:
    verification = row['result'].get('verification', {})
    if row['kind'] == 'UPLOAD' and checking is None:
        checking = {check['parent_id'] for check in store(platform).list(uid=row['uid'], kind='RECONCILE', statuses=ACTIVE)}
    value = {**{k: row[k] for k in ('id','kind','status','phase','error','work_id','script_id','version','render_id','created_at','updated_at')},
            'platform': platform, 'account_uid': row['uid'], 'payload': {k: row['payload'][k] for k in ('title','description','tags','first_comment') if k in row['payload']},
            'url': row['result'].get('url', ''),
            'verification': {'automatic': platform in EVIDENCE and row['kind'] == 'UPLOAD' and row['status'] in ('UNKNOWN','SUBMITTED') and not verification.get('exhausted'),
                             'running': row['id'] in (checking or set()),
                             **{k: verification[k] for k in ('attempts','checked_at','next_check_at','state','reason','exhausted') if k in verification}},
            'can_confirm_absent': row['status'] == 'UNKNOWN' and not row['work_id'] and row['result'].get('accepted') is not True,
            'can_retry': row['status'] in ('FAILED','CANCELLED') and bool(row['result'].get('confirmed_not_submitted'))}
    if row['kind'] == 'LOGIN' and row['payload'].get('purpose') == 'comments':
        value['login_purpose'] = 'comments'
    if row['kind'] == 'UPLOAD':
        from web.services.publication_state import describe
        value['progress'] = describe(platform, row['id'], value)
    if row['kind'] == 'UPLOAD' and platform in COMMENT_PLATFORMS:
        child = (comments if comments is not None else store(platform).comments(row['uid'])).get(row['id'])
        value['first_comment'] = None if not child else {
            **{k: child[k] for k in ('id','status','phase','error')}, 'text': child['payload']['text'],
            'comment_id': child['result'].get('comment_id',''), 'read_back': bool(child['result'].get('read_back')),
            'can_retry': child['status'] in ('FAILED','CANCELLED') and child['result'].get('confirmed_not_submitted') is True}
    return value


def save_first_comment_template(platform: str, value: dict) -> dict:
    clean = clean_first_comment(platform, value)
    if not definition(platform)['capabilities']['first_comment']:
        raise AdapterError('这个平台尚未接入自动首评。')
    with dispatch_lock(platform):
        return store(platform).save_comment_settings(uid(platform), clean)


def ensure_comment(db: SocialStore, parent: dict, option: dict | None = None) -> dict | None:
    option = option if option is not None else parent['payload'].get('first_comment', {})
    if not option.get('enabled'):
        return None
    clean = clean_first_comment(db.platform, option)
    return db.create('FIRST_COMMENT', parent['uid'], {'text':clean['text']}, parent_id=parent['id'],
                     render={'id':parent['render_id'],'script_id':parent['script_id'],'version':parent['version']})


def add_first_comment(platform: str, key: str, value: dict) -> dict:
    clean = clean_first_comment(platform, value)
    if not clean['enabled']:
        raise AdapterError('请启用并填写首条评论。')
    with dispatch_lock(platform):
        parent = upload_task(platform, key)
        if parent['status'] not in ('SUBMITTED','PUBLISHED') or not parent['work_id']:
            raise AdapterError('取得可靠作品 ID 后才能安排首条评论。')
        child = ensure_comment(store(platform), parent, clean)
        if child['payload']['text'] != clean['text']:
            raise AdapterError('这条作品已有另一份首评记录，不能替换或重复发送。')
    tick(platform)
    return public_job(platform, store(platform).get(key))


def first_comment_action(platform: str, key: str, action_name: str) -> dict:
    with dispatch_lock(platform):
        parent = upload_task(platform, key); db = store(platform)
        child = db.comments(parent['uid']).get(key)
        if not child:
            raise AdapterError('没有这条首评任务。')
        if action_name == 'check' and child['status'] in ('UNKNOWN','SENT'):
            if not any(r['parent_id'] == child['id'] for r in db.list(kind='COMMENT_CHECK', statuses=ACTIVE)):
                db.create('COMMENT_CHECK', parent['uid'], parent_id=child['id'])
        elif action_name == 'cancel' and child['status'] in ('WAITING','QUEUED'):
            db.update(child['id'], expected=child['status'], status='CANCELLED', phase='已取消首评', result={'confirmed_not_submitted':True})
        elif action_name == 'retry' and child['status'] in ('FAILED','CANCELLED') and child['result'].get('confirmed_not_submitted') is True:
            db.update(child['id'], expected=child['status'], status='WAITING', phase='等待重新检查', error='', result={})
        else:
            raise AdapterError('只能重试确定未发送的评论；结果不明时仅能只读核对，不会重发。')
    tick(platform)
    return public_job(platform, db.get(key))


def upload_task(platform: str, key: str) -> dict:
    row = store(platform).get(key)
    if not row or row['kind'] != 'UPLOAD' or row['uid'] != uid(platform):
        raise AdapterError('找不到当前平台账号的这条投稿任务。')
    return row


def start_login(platform: str, renew: bool = False) -> dict:
    if not runtime()['ready']:
        raise AdapterError('本机上传运行环境尚未准备好。')
    with dispatch_lock(platform):
        db = store(platform); active = db.list(statuses=ACTIVE)
        login = next((r for r in active if r['kind'] == 'LOGIN'), None)
        if login and not renew:
            return public_job(platform, login)
        if any(r['kind']!='LOGIN' for r in active):
            raise AdapterError('这个平台已有任务运行，请完成后再连接账号。')
        if login:
            db.update(login['id'],expected=login['status'],status='CANCELLED',phase='已请求重新获取二维码')
        row = db.create('LOGIN')
        private_json(directory(platform) / 'login.json', {'id': row['id']})
    tick(platform)
    return public_job(platform, store(platform).get(row['id']))


def start_comment_login(platform: str, renew: bool = False) -> dict:
    """Supplement the existing creator session; never change the publishing account."""
    if platform != 'xiaohongshu' or not runtime()['ready']:
        raise AdapterError('本平台评论登录入口尚未准备好。')
    with dispatch_lock(platform):
        owner = uid(platform); db = store(platform)
        active = db.list(statuses=ACTIVE)
        login = next((row for row in active if row['kind']=='LOGIN' and row['payload'].get('purpose')=='comments'),None)
        if login and not renew:
            return public_job(platform, login)
        if any(row['id'] != (login or {}).get('id') for row in active):
            raise AdapterError('这个平台有任务运行，请稍后连接评论登录。')
        if login:
            db.update(login['id'], expected=login['status'], status='CANCELLED', phase='已请求刷新评论登录二维码')
        row = db.create('LOGIN', owner, {'purpose':'comments'})
        private_json(directory(platform)/'login.json', {'id':row['id']})
    tick(platform)
    return public_job(platform, store(platform).get(row['id']))


def login_status(platform: str, key: str) -> dict:
    if not re.fullmatch(r'[a-f0-9]{32}', key):
        raise AdapterError('没有这次账号连接记录。')
    row = store(platform).get(key)
    if not row or row['kind'] != 'LOGIN':
        raise AdapterError('没有这次账号连接记录。')
    value = public_job(platform, row)
    image = directory(platform) / 'jobs' / key / 'qrcode.png'
    if row['status'] == 'RUNNING' and image.is_file() and image.stat().st_size <= 2_000_000:
        value['image'] = 'data:image/png;base64,' + base64.b64encode(image.read_bytes()).decode()
    value['connected'] = row['status'] == 'DONE' and row['result'].get('uid') == account(platform).get('uid')
    return value


def verify(platform: str) -> dict:
    with dispatch_lock(platform):
        owner = uid(platform); db = store(platform)
        if db.list(statuses=ACTIVE):
            raise AdapterError('这个平台已有任务运行，请稍后验证。')
        row = db.create('VERIFY', owner)
    tick(platform)
    return public_job(platform, store(platform).get(row['id']))


def disconnect(platform: str) -> dict:
    with dispatch_lock(platform):
        if store(platform).list(statuses=ACTIVE):
            raise AdapterError('这个平台已有任务运行，请完成后再断开账号。')
        owner = uid(platform)
        credential(directory(platform), owner).unlink(missing_ok=True)
        (directory(platform) / 'account.json').unlink(missing_ok=True)
    return {'connected': False}


def candidates(platform: str) -> list[dict]:
    from core.video import content_guard

    owner = uid(platform)
    targets = {(r['script_id'],r['version']) for r in store(platform).list(uid=owner,kind='UPLOAD')}
    with video_store.connect() as conn:
        rows = conn.execute("SELECT r.id,r.script_id,r.version,r.title,r.completed_at,r.artifacts,"
                            "s.archived,s.status AS script_status,s.current_version,s.approved_version "
                            "FROM renders r JOIN scripts s ON s.id=r.script_id "
                            f"WHERE r.status='COMPLETED' AND {content_guard.PUBLISHABLE_SQL} AND NOT EXISTS "
                            "(SELECT 1 FROM publications p WHERE p.script_id=r.script_id AND p.version=r.version "
                            "AND p.platform=? AND (p.account_uid=? OR p.account_uid='')) "
                            "ORDER BY r.completed_at DESC,r.created_at DESC,r.id DESC", (platform, owner)).fetchall()
    seen, values = set(), []
    for raw in rows:
        row = dict(raw); target = (row['script_id'],row['version'])
        if target in targets or target in seen:
            continue
        render = {'id':row['id'],'artifacts':json.loads(row['artifacts'])}
        if video_render.safe_file(render,'video.mp4') is None:
            continue
        seen.add(target)
        values.append({**{k:row[k] for k in ('script_id','version','title','completed_at','archived','script_status','current_version','approved_version')},'render_id':row['id']})
    return values


def preview(platform: str, render_id: str) -> dict:
    from web.services.distributions import preview_draft
    render, script, version = platform_manager.eligible(render_id)
    pack = video_render.publish_pack(script,version,platform_manager.column_for(script),render_id=render_id)
    from core.video.platform_publish import adapt
    from core.social.publish_text import default_comment
    adaptation = adapt(pack, platform)
    draft = preview_draft(render,platform)
    if draft.get('draft'):
        adaptation = adapt(pack, platform, draft['draft']['payload'])
    spec = definition(platform)
    warnings = list(pack.get('warnings',[])) + list(pack.get('freshness',{}).get('warnings',[]))
    tags = list(pack['tags'])
    if len(tags) > spec['tag_limit']:
        tags = tags[:spec['tag_limit']]
        if not draft.get('draft'):
            warnings.append(f"{spec['name']}发布包最多 {spec['tag_limit']} 个话题，已默认保留通用发布包的前 {spec['tag_limit']} 个，可自行调整。")
    return {**draft, **adaptation['payload'], 'adaptation':adaptation,
            'render_id':render_id,'script_id':script['id'],'version':render['version'],
            'warnings':warnings, 'definition':spec,
            'first_comment':default_comment(store(platform).comment_settings(uid(platform))) if spec['capabilities']['first_comment'] else None}


def clean_payload(platform: str, payload: dict) -> dict:
    spec = definition(platform)
    if set(payload) - {'title','description','tags','account_uid','first_comment'}:
        raise AdapterError('这个平台的发布包字段不正确。')
    title, description, tags = payload.get('title',''),payload.get('description',''),payload.get('tags',[])
    if not isinstance(title,str) or not 1 <= len(title.strip()) <= spec['title_limit'] or not isinstance(description,str) or len(description) > spec['description_limit']:
        raise AdapterError(f"标题需为 1–{spec['title_limit']} 字，简介最多 {spec['description_limit']} 字。")
    if not isinstance(tags,list) or len(tags) > spec['tag_limit'] or any(not isinstance(t,str) or not t.strip() or len(t) > 100 or any(c in t for c in '\n\r') for t in tags):
        raise AdapterError(f"话题格式不正确，最多 {spec['tag_limit']} 个。")
    clean={'title':title.strip(),'description':description,'tags':[t.strip().lstrip('#') for t in tags]}
    from core.video.platform_publish import inspect
    check = inspect(platform, clean)
    if not check['valid']:
        raise AdapterError('；'.join(check['issues']))
    if 'first_comment' in payload:
        clean['first_comment'] = clean_first_comment(platform, payload['first_comment'])
    adapter(platform).validate_payload(clean)
    return clean


def enqueue(platform: str, render_id: str, payload: dict) -> dict:
    owner = uid(platform); spec = definition(platform)
    if payload.get('account_uid') != owner:
        raise AdapterError('平台账号已改变，请刷新后重试。')
    if not runtime()['ready'] or not spec['capabilities']['upload']:
        raise AdapterError('这个平台的上传路径尚未准备好。')
    render, _, _ = platform_manager.eligible(render_id)
    clean = clean_payload(platform,payload)
    with dispatch_lock(platform):
        if uid(platform) != owner or any(r['kind']=='LOGIN' for r in store(platform).list(statuses=ACTIVE)):
            raise AdapterError('平台账号正在更换，请稍后重试。')
        if spec['capabilities']['first_comment'] and 'first_comment' not in clean:
            clean['first_comment'] = clean_first_comment(platform, store(platform).comment_settings(owner))
        if video_store.has_publication(render['script_id'],render['version'],platform,owner):
            raise AdapterError('这个版本在本平台已有发布记录。')
        video = video_render.safe_file(render,'video.mp4')
        if video is None:
            raise AdapterError('成片文件不可用。')
        cover = next((p for name in ('cover.png','cover.jpg') if (p:=video_render.safe_file(render,name))),None)
        row = store(platform).create('UPLOAD',owner,{**clean,'video_path':str(video.resolve()),'cover':str(cover.resolve()) if cover else ''},render=render)
        ensure_comment(store(platform), row)
    tick(platform)
    return public_job(platform,store(platform).get(row['id']))


def confirm_absent(platform: str, key: str, owner: str, updated_at: float, actor: str) -> dict:
    """Accept an explicit account-scoped human decision; retry remains a separate action."""
    with dispatch_lock(platform):
        row = upload_task(platform, key); db = store(platform)
        if owner != row['uid']:
            raise AdapterError('平台账号已改变，请刷新后重新核对。')
        if db.list(statuses=ACTIVE):
            raise AdapterError('这个平台有任务运行，请等待结束后再确认。')
        if video_store.has_publication(row['script_id'], row['version'], platform, row['uid']):
            raise AdapterError('本平台已有发布记录，不能确认未发布。')
        saved = row['result'].get('submission_response', {})
        if isinstance(saved, dict) and saved.get('http_status') == 200:
            try:
                receipt = adapter(platform).submission_receipt(saved.get('body'))
            except AdapterError:
                receipt = {}
            if receipt.get('accepted') is True:
                raise AdapterError('已有平台接受投稿的回执，请继续核对作品，不能恢复重投。')
        if not db.confirm_absent(key, owner, actor, updated_at):
            raise AdapterError('任务状态已改变或已有作品回执，请刷新后重新核对。')
    return public_job(platform, db.get(key))


def action(platform: str, key: str, kind: str) -> dict:
    with dispatch_lock(platform):
        row = upload_task(platform,key); db = store(platform)
        if kind == 'cancel' and row['status']=='QUEUED':
            db.update(key,expected='QUEUED',status='CANCELLED',result={'confirmed_not_submitted':True})
        elif kind == 'retry' and row['status'] in ('FAILED','CANCELLED') and row['result'].get('confirmed_not_submitted'):
            if video_store.has_publication(row['script_id'],row['version'],platform,row['uid']):
                raise AdapterError('这个版本已记录发布，不能重复投稿。')
            adapter(platform).validate_payload(row['payload'])
            render,_,_ = platform_manager.eligible(row['render_id'])
            video = video_render.safe_file(render,'video.mp4')
            if video is None:
                raise AdapterError('成片文件不可用。')
            if not db.requeue_upload(key, row['uid'], {**row['payload'],'video_path':str(video.resolve())}):
                raise AdapterError('任务状态已改变，请刷新后再重试。')
        else:
            raise AdapterError('结果待核对时不能重投；仅能重试已确认未提交的任务。')
    tick(platform)
    return public_job(platform,store(platform).get(key))


def reconcile(platform: str, key: str, value: str = '') -> dict:
    spec=definition(platform)
    if not spec['capabilities']['public_check'] and not spec['ownership_check']:
        raise AdapterError('本平台自动作品核对尚未接入，请到创作中心确认后在文稿页手动补录。')
    with dispatch_lock(platform):
        parent = upload_task(platform,key); target = value.strip() or parent['work_id']
        if target.startswith(('http://','https://')):
            try:
                target,_ = identify(platform,target,strict=True)
            except ValueError as exc:
                raise AdapterError(str(exc)) from exc
        target = clean_work(target) if target else ''
        if not target and not spec['capabilities']['public_check']:
            raise AdapterError('请填写准确的作品 ID 或完整链接。')
        if parent['status'] not in ('UNKNOWN','SUBMITTED') or (parent['work_id'] and parent['work_id'] != target):
            raise AdapterError('当前任务无需核对，或已有作品 ID 与填写内容不一致。')
        db=store(platform)
        existing=next((r for r in db.list(kind='RECONCILE',statuses=ACTIVE) if r['parent_id']==key),None)
        if existing and existing['payload'].get('work_id')!=target:
            raise AdapterError('正在核对另一作品 ID，请等待该次检查结束后更正。')
        payload = {'work_id': target} if target else {'work_id': '', 'lookup': check_lookup(parent)}
        row=existing or db.create('RECONCILE',parent['uid'],payload,parent_id=key)
    tick(platform)
    return public_job(platform,store(platform).get(row['id']))


def overview(platform: str) -> dict:
    profile = account(platform); owner = profile.get('uid','')
    jobs=store(platform).list(uid=owner) if owner else []
    comments={r['parent_id']:r for r in jobs if r['kind']=='FIRST_COMMENT'}
    checking = {r['parent_id'] for r in jobs if r['kind'] == 'RECONCILE' and r['status'] in ACTIVE}
    path=directory(platform)/'login.json'
    if owner:
        profile = {**profile, 'session': session_of(store(platform), owner)}
    return {'definition':definition(platform),'account':profile,'runtime_ready':runtime()['ready'],
            'login_id':json.loads(path.read_text()).get('id') if path.is_file() else None,
            'uploads':[public_job(platform,r,comments=comments,checking=checking) for r in jobs if r['kind']=='UPLOAD'],
            'settings':{'first_comment':store(platform).comment_settings(owner)} if owner else {},
            'checks':[public_job(platform,r) for r in jobs if r['kind'] in ('VERIFY','RECONCILE')][:10],
            'candidates':candidates(platform) if owner else []}


def settle(platform: str, db: SocialStore) -> None:
    recover_saved_receipts(platform, db)
    path=directory(platform)/'login.json'
    active=json.loads(path.read_text()).get('id') if path.is_file() else ''
    for row in db.list(statuses=('DONE',)):
        if row['kind']=='LOGIN' and row['id']==active:
            owner=clean_owner(row['result']); temporary=directory(platform)/'jobs'/row['id']/'storage.json'
            target=credential(directory(platform),owner['uid'])
            if not temporary.is_file():
                db.update(row['id'],status='FAILED',error='账号凭证未保存，请重新连接。');continue
            if row['payload'].get('purpose') == 'comments' and (row['uid'] != owner['uid'] or row['uid'] != account(platform).get('uid') or row['result'].get('comments_verified') is not True):
                db.update(row['id'],status='FAILED',error='评论登录账号与原投稿账号不一致，未覆盖原登录态。')
                temporary.unlink(missing_ok=True);path.unlink(missing_ok=True);continue
            profile={**owner,'verified_at':time.time()}
            if row['payload'].get('purpose') == 'comments':
                profile['comments_verified_at']=time.time()
            private_json(target,json.loads(temporary.read_text()));private_json(directory(platform)/'account.json',profile)
            temporary.unlink();path.unlink(missing_ok=True)
        elif row['kind']=='VERIFY':
            owner=clean_owner(row['result'])
            if owner['uid']==row['uid'] and account(platform).get('uid')==row['uid']:
                previous=account(platform)
                private_json(directory(platform)/'account.json',{**owner,'verified_at':row['updated_at'],
                    **({'comments_verified_at':previous['comments_verified_at']} if previous.get('comments_verified_at') else {})})
            db.update(row['id'],status='RECORDED')
        elif row['kind']=='RECONCILE':
            apply_check(platform, db, row)
            db.update(row['id'],status='RECORDED')
    for row in db.list(kind='RECONCILE', statuses=('FAILED',)):
        apply_check(platform, db, row)
        db.update(row['id'], expected='FAILED', status='RECORDED')
    if not definition(platform)['capabilities']['public_check']:
        return
    for row in db.list(kind='UPLOAD',statuses=('SUBMITTED',)):
        receipt=row['result']
        if row['uid'] != account(platform).get('uid'):
            continue
        if not (receipt.get('public') is True and receipt.get('owned') is True and receipt.get('uid')==row['uid'] and receipt.get('work_id')==row['work_id']
                and receipt.get('evidence') == EVIDENCE.get(platform) and receipt.get('platform_state') == 'published'):
            continue
        url_id,url=identify(platform,receipt.get('url',''),strict=True)
        if url_id!=row['work_id']:
            continue
        when = timestamp(receipt.get('published_at'))
        when = when if when and when <= time.time() + 86400 else time.time()
        video_store.add_publication(row['script_id'],version=row['version'],render_id=row['render_id'],platform=platform,url=url,
                                    published_on=datetime.fromtimestamp(when).astimezone().date().isoformat(),title=row['payload']['title'],
                                    note=('平台作品列表按发布时间与时长关联到原投稿，回写发布记录；标题未能读回，建议抽查。'
                                          if receipt.get('match_basis') == 'time_duration'
                                          else '平台自动核对作品归属和公开状态，回写发布记录。'),
                                    recorded_by='platform-manager',account_uid=row['uid'],account_name=account(platform).get('name',''))
        db.update(row['id'],expected='SUBMITTED',status='PUBLISHED',phase='已公开并回写文稿',error='')
    schedule_comments(platform, db)


def schedule_comments(platform: str, db: SocialStore) -> None:
    """Release opted-in children only; never apply a new template to old uploads."""
    if not definition(platform)['capabilities']['first_comment']:
        return
    owner = account(platform).get('uid')
    if not owner:
        return
    existing = db.comments(owner)
    for parent in db.list(uid=owner, kind='UPLOAD', statuses=('SUBMITTED','PUBLISHED','UNKNOWN')):
        if parent['id'] not in existing:
            ensure_comment(db, parent)
    for child in reversed(db.list(uid=owner, kind='FIRST_COMMENT', statuses=('WAITING',))):
        parent = db.get(child['parent_id'])
        if not parent or parent['uid'] != owner or parent['kind'] != 'UPLOAD' or parent['status'] != 'PUBLISHED' or not parent['work_id']:
            continue
        if not db.activate_comment(child['id'], owner, parent['work_id']):
            db.update(child['id'], expected='WAITING', status='FAILED', error='同一账号的这条作品已有首评任务，已阻止重复发送。',
                      result={'duplicate_work':True,'confirmed_not_submitted':False})
    active_checks = {row['parent_id'] for row in db.list(kind='COMMENT_CHECK', statuses=ACTIVE)}
    due = [row for row in db.list(uid=owner, kind='FIRST_COMMENT', statuses=('SENT','UNKNOWN'))
           if not row['result'].get('read_back') and row['id'] not in active_checks
           and int(row['result'].get('check_attempts', 0)) < MAX_COMMENT_CHECKS
           and row['result'].get('next_check_at', row['updated_at'] + 15) <= time.time()]
    if due and not active_checks and session_of(db, owner)['state'] != 'expired':
        child = min(due, key=lambda row:row['result'].get('next_check_at',row['updated_at'] + 15))
        db.create('COMMENT_CHECK', owner, parent_id=child['id'])


def apply_check(platform: str, db: SocialStore, check: dict) -> None:
    """Merge a read-only result without losing the original single-submit guard."""
    parent = db.get(check['parent_id'])
    if (not parent or parent['kind'] != 'UPLOAD' or parent['uid'] != check['uid']
            or parent['uid'] != account(platform).get('uid') or parent['status'] not in ('UNKNOWN','SUBMITTED')):
        return
    receipt = check['result']; previous = parent['result'].get('verification', {})
    state = receipt.get('platform_state', 'unknown') if check['status'] == 'DONE' else 'unavailable'
    if state not in STATE_LABELS:
        state = 'unknown'
    if check['status'] == 'DONE' and (receipt.get('uid') != parent['uid'] or (receipt.get('owned') is not True and state not in ('missing','ambiguous','metadata_missing'))):
        state = 'unknown'
    tracking = check_progress(previous, state, check['updated_at'], lookup_revision(check_lookup(parent)))
    if receipt.get('status_details'):
        tracking['status_details'] = receipt['status_details']
    result = {**parent['result'], 'verification': tracking}
    fields = {'result': result}
    if (check['status'] == 'DONE' and receipt.get('uid') == parent['uid'] and receipt.get('owned') is True):
        try:
            target, url = identify(platform, receipt.get('url', ''), strict=True)
            if not target or target != receipt.get('work_id') or (parent['work_id'] and parent['work_id'] != target):
                raise ValueError('inconsistent work identity')
        except ValueError:
            tracking.update(state='unknown', reason=STATE_LABELS['unknown'])
        else:
            public = (receipt.get('public') is True and state == 'published' and receipt.get('evidence') == EVIDENCE.get(platform))
            if state == 'published' and not public:
                state = 'unknown'; tracking.update(state=state, reason=STATE_LABELS[state])
            result.update({k: receipt[k] for k in ('published_at', 'match_basis') if k in receipt})
            result.update(accepted=True, confirmed_not_submitted=False, awaiting_work_id=False,
                          work_id=target, url=url, uid=parent['uid'], owned=True, public=public,
                          evidence=EVIDENCE[platform] if public else '', platform_state=state)
            fields.update(status='FAILED' if state == 'rejected' else 'SUBMITTED', work_id=target,
                          phase=STATE_LABELS[state], error=STATE_LABELS[state] if state == 'rejected' else '')
    db.update(parent['id'], expected=parent['status'], **fields)


def schedule_checks(platform: str, db: SocialStore) -> None:
    """Queue one due read-only check. This never creates or requeues an UPLOAD."""
    if not definition(platform)['capabilities']['public_check']:
        return
    current = account(platform).get('uid')
    if not current or any(row['kind'] in ('LOGIN','RECONCILE') for row in db.list(statuses=ACTIVE)):
        return
    if session_of(db, current)['state'] == 'expired':
        return
    now = time.time()
    pending = db.list(uid=current, kind='UPLOAD', statuses=('SUBMITTED','UNKNOWN'))
    due = [row for row in pending if not row['result'].get('verification', {}).get('exhausted')
           and (row['result'].get('verification', {}).get('next_check_at') or row['updated_at'] + 15) <= now]
    due.sort(key=lambda row: row['result'].get('verification', {}).get('next_check_at', row['updated_at'] + 15))
    if not due:
        return
    row = due[0]
    db.create('RECONCILE', current, {'work_id': row['work_id'], 'lookup': check_lookup(row), 'automatic': True}, parent_id=row['id'])


def session_of(db: SocialStore, owner: str) -> dict:
    """Login health from this account's jobs; a login attempt is queued before its UID is known."""
    logins = [row for row in db.list(kind='LOGIN') if row['uid'] == owner or row['result'].get('uid') == owner]
    return assess([*db.list(uid=owner), *logins])


def schedule_verify(platform: str, db: SocialStore) -> None:
    """Queue a read-only login check for an idle account; this never touches a work."""
    current = account(platform).get('uid')
    if not current or db.list(statuses=ACTIVE):
        return
    if verify_due(session_of(db, current), time.time()):
        db.create('VERIFY', current)


def check_lookup(row: dict) -> dict:
    return lookup_for(row)


def recover_saved_receipts(platform: str, db: SocialStore) -> None:
    """Re-parse a stored final response after an adapter fix; never send another POST."""
    owner = account(platform).get('uid')
    if not owner:
        return
    for row in db.list(uid=owner, kind='UPLOAD', statuses=('UNKNOWN',)):
        evidence = row['result'].get('submission_response')
        if (not isinstance(evidence, dict) or evidence.get('http_status') != 200
                or not isinstance(evidence.get('body'), dict)):
            continue
        try:
            receipt = adapter(platform).submission_receipt(evidence['body'])
            if receipt.get('accepted') is True:
                if accepted_without_work_id(platform, receipt) and not row['work_id']:
                    result = {**row['result'], 'accepted': True, 'awaiting_work_id': True,
                              'work_id': '', 'url': '', 'uid': row['uid'], 'public': False,
                              'confirmed_not_submitted': False}
                    db.update(row['id'], expected='UNKNOWN', status='SUBMITTED', result=result,
                              phase='已投稿，待补作品链接', error='')
                    continue
                target = clean_work(receipt.get('work_id'))
                work_id, url = identify(platform, receipt.get('url', ''), strict=True)
                if work_id != target or (row['work_id'] and row['work_id'] != target):
                    continue
                result = {**row['result'], 'accepted': True, 'work_id': target,
                          'url': url, 'uid': row['uid'], 'public': False,
                          'confirmed_not_submitted': False}
                db.update(row['id'], expected='UNKNOWN', status='SUBMITTED', work_id=target,
                          result=result, phase='已恢复保存的投稿回执，未重新投稿', error='')
            elif receipt.get('accepted') is False and receipt.get('confirmed_not_submitted') is True and not row['work_id']:
                result = {**row['result'], 'accepted': False, 'confirmed_not_submitted': True,
                          'code': receipt.get('code', '')}
                db.update(row['id'], expected='UNKNOWN', status='FAILED', result=result,
                          phase='已恢复平台拒绝回执，未重新投稿',
                          error=f"平台拒绝投稿（错误码 {receipt.get('code', '')}），未自动重试。")
        except (AdapterError, ValueError):
            continue


def tick(platform: str) -> None:
    if platform in DEFERRED_PLATFORMS:
        return
    with dispatch_lock(platform):
        db=store(platform);settle(platform,db)
        with (directory(platform)/'worker.lock').open('a') as lock:
            try:
                fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:
                return
            db.recover()
        if not runtime()['ready']:
            return
        schedule_verify(platform, db)
        schedule_checks(platform, db)
        row=db.claim(prefer_checks=True)
        if not row:
            return
        if row['kind']!='LOGIN' and row['uid']!=account(platform).get('uid'):
            db.update(row['id'],status='FAILED',error='原平台账号已断开，请连接原账号。',result={'confirmed_not_submitted':True});return
        if row['kind']=='UPLOAD':
            try:
                adapter(platform).validate_payload(row['payload'])
                render,_,_=platform_manager.eligible(row['render_id'])
                if video_store.has_publication(row['script_id'],row['version'],platform,row['uid']):
                    raise AdapterError('本平台已有发布记录，已停止重复投稿。')
                video=video_render.safe_file(render,'video.mp4')
                if video is None:
                    raise AdapterError('成片文件不可用。')
                db.update(row['id'],payload={**row['payload'],'video_path':str(video.resolve())})
            except (AdapterError,platform_manager.Error) as exc:
                db.update(row['id'],status='FAILED',error=str(exc),result={'confirmed_not_submitted':True});return
        rt=runtime();env={k:v for k,v in os.environ.items() if k in ('PATH','HOME','TMPDIR','LANG','LC_ALL','DISPLAY')}
        env.update(PYTHONPATH=os.pathsep.join((str(rt['source']),str(ROOT))),PLAYWRIGHT_BROWSERS_PATH=str(rt['browsers']))
        try:
            subprocess.Popen([str(rt['python']),str(ROOT/'scripts/social_publish_worker.py'),'--state-dir',str(Path(get_settings().paths.state_dir).resolve()),'--platform',platform,'--job',row['id']],
                             cwd=rt['source'],env=env,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
        except OSError:
            db.update(row['id'],status='FAILED',error='无法启动本机平台组件。',result={'confirmed_not_submitted':True})
