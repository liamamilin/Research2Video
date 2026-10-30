from __future__ import annotations

import time

from core.social.session_health import IDENTITY_FAILURE, RECHECK_INTERVAL, VERIFY_INTERVAL, assess, verify_due
from core.social.store import SocialStore
from tests.test_social_publishing import OWNER, social_env  # noqa: F401
from web.services import social_publishing as manager

NOW = 1_800_000_000
UNVERIFIED = IDENTITY_FAILURE + '快手创作中心账号，请完成扫码后的平台确认。'


def job(kind, status, at, error='', **result):
    return {'kind': kind, 'status': status, 'updated_at': at, 'error': error, 'result': result}


def test_two_identity_failures_without_a_success_in_between_mean_expired():
    rows = [job('LOGIN', 'DONE', NOW), job('COMMENT_CHECK', 'FAILED', NOW + 100, UNVERIFIED),
            job('RECONCILE', 'RECORDED', NOW + 200, UNVERIFIED)]
    session = assess(rows)
    assert (session['state'], session['failures'], session['since']) == ('expired', 2, NOW + 100)
    assert assess(rows[:2])['state'] == 'valid'
    assert assess([])['state'] == 'unknown'


def test_a_verified_success_after_failures_restores_the_login():
    rows = [job('COMMENT_CHECK', 'FAILED', NOW, UNVERIFIED), job('COMMENT_CHECK', 'FAILED', NOW + 10, UNVERIFIED),
            job('VERIFY', 'RECORDED', NOW + 20)]
    assert assess(rows)['state'] == 'valid' and assess(rows)['last_ok_at'] == NOW + 20


def test_checks_that_never_opened_the_platform_do_not_prove_the_login():
    rows = [job('LOGIN', 'DONE', NOW), job('COMMENT_CHECK', 'FAILED', NOW + 1, UNVERIFIED),
            job('COMMENT_CHECK', 'FAILED', NOW + 2, UNVERIFIED),
            job('RECONCILE', 'RECORDED', NOW + 3, platform_state='metadata_missing'),
            job('RECONCILE', 'RECORDED', NOW + 4, '平台暂不可用，请稍后')]
    session = assess(rows)
    assert session['state'] == 'expired' and session['last_ok_at'] == NOW


def test_verify_is_due_sooner_for_an_expired_login_and_never_without_history():
    valid = {'state': 'valid', 'checked_at': NOW}
    expired = {'state': 'expired', 'checked_at': NOW}
    assert not verify_due(valid, NOW + VERIFY_INTERVAL - 1) and verify_due(valid, NOW + VERIFY_INTERVAL)
    assert not verify_due(expired, NOW + RECHECK_INTERVAL - 1) and verify_due(expired, NOW + RECHECK_INTERVAL)
    assert not verify_due({'state': 'unknown', 'checked_at': None}, NOW)


def test_idle_account_gets_one_read_only_verify_and_an_expired_one_pauses_other_checks(social_env):
    db = SocialStore(str(social_env / 'state'), 'kuaishou')
    now = time.time()
    old = db.create('LOGIN', OWNER['uid'])
    db.update(old['id'], status='DONE')
    with db.connect(write=True) as conn:
        conn.execute('UPDATE jobs SET updated_at=? WHERE id=?', (now - VERIFY_INTERVAL - 5, old['id']))
    manager.schedule_verify('kuaishou', db)
    manager.schedule_verify('kuaishou', db)
    assert [r['kind'] for r in db.list(kind='VERIFY')] == ['VERIFY']

    upload = db.create('UPLOAD', OWNER['uid'], {'title': 't'}, render={'id': 'r', 'script_id': 's', 'version': 1})
    db.update(upload['id'], status='SUBMITTED', result={'duration_seconds': 90, 'submission_started_at': now - 600})
    for index in range(2):
        failed = db.create('COMMENT_CHECK', OWNER['uid'])
        db.update(failed['id'], status='FAILED', error=UNVERIFIED)
    for row in db.list(kind='VERIFY'):
        db.update(row['id'], status='FAILED', error=UNVERIFIED)
    overview = manager.overview('kuaishou')
    assert overview['account']['session']['state'] == 'expired'
    shown = next(r for r in overview['uploads'] if r['id'] == upload['id'])
    assert shown['status'] == 'SUBMITTED' and shown['verification']['automatic'] is False
    assert shown['verification']['paused'] is True
    assert shown['progress']['label'] == '已投稿 · 自动核对暂停'
    assert shown['progress']['active'] is False and shown['progress']['needs_attention'] is True
    assert shown['progress']['recovery']['kind'] == 'connection'
    assert manager.public_job('kuaishou', db.get(upload['id']))['verification']['paused'] is True
    original_upload = db.get(upload['id'])
    manager.schedule_checks('kuaishou', db)
    assert not db.list(kind='RECONCILE')
    assert db.get(upload['id']) == original_upload

    verified = db.create('VERIFY', OWNER['uid'])
    db.update(verified['id'], status='RECORDED')
    resumed = manager.public_job('kuaishou', db.get(upload['id']))
    assert resumed['verification']['automatic'] is True
    assert not resumed['verification'].get('paused')
    assert db.get(upload['id']) == original_upload
