"""Regression coverage for the stalled original uploads; providers are never contacted."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.douyin import DouyinLookupError
from core.douyin_store import DouyinStore
from core.social import media, reconciliation
from core.social.store import SocialStore
from core.social.verification import check_progress, lookup_revision, MAX_UNRESOLVED_CHECKS
from scripts import douyin_worker
from web.services import douyin, publication_state
from core.maintenance_work import busy_work, restart_gate
from core.maintenance_work import MaintenanceSuperseded


def test_duration_uses_bundled_binary_with_empty_path(tmp_path, monkeypatch):
    movie = tmp_path / 'original movie.mp4'; movie.write_bytes(b'fixture')
    monkeypatch.setattr(media.shutil, 'which', lambda _: None)
    monkeypatch.setattr(media.os, 'access', lambda *_: True)
    calls = []
    def run(args, **options):
        calls.append(args)
        assert options['timeout'] == 15 and options['check']
        return SimpleNamespace(stdout='{"format":{"duration":"86.033333"}}')
    monkeypatch.setattr(media.subprocess, 'run', run)
    assert media.probe_duration(movie) == 86.033333
    assert 'autovideo/g0' in calls[0][0] and calls[0][-1] == str(movie)


@pytest.mark.parametrize('value', ['NaN', 'Infinity', '-2', '0'])
def test_invalid_probe_never_creates_match_metadata(tmp_path, monkeypatch, value):
    movie = tmp_path / 'movie.mp4'; movie.write_bytes(b'fixture')
    monkeypatch.setattr(media.os, 'access', lambda *_: True)
    monkeypatch.setattr(media.shutil, 'which', lambda _: None)
    monkeypatch.setattr(media.subprocess, 'run', lambda *a, **kw: SimpleNamespace(stdout=json.dumps({'format': {'duration': value}})))
    assert media.probe_duration(movie) is None


def test_repair_original_pending_upload_keeps_guard_response_and_payload(tmp_path, monkeypatch):
    db = SocialStore(str(tmp_path), 'kuaishou')
    row = db.create('UPLOAD', '123', {'title': '原题', 'video_path': '/existing.mp4'},
                    render={'id': 'render-1', 'script_id': 'script-1', 'version': 2})
    original = {'accepted': True, 'submission_started': True, 'submission_started_at': 1792918129,
                'submission_response': {'http_status': 200, 'body': {'result': 1, 'data': {}}},
                'verification': {'attempts': 46, 'exhausted': True, 'next_check_at': None}}
    db.update(row['id'], status='SUBMITTED', result=original)
    before = db.get(row['id'])
    monkeypatch.setattr(reconciliation, 'probe_duration', lambda _: 90.5)
    lookup = reconciliation.prepare_lookup(db, before)
    after = db.get(row['id'])
    assert lookup['duration_seconds'] == 90.5 and lookup['submitted_at'] == original['submission_started_at']
    for key in ('id', 'status', 'payload', 'created_at', 'render_id', 'script_id', 'version'):
        assert after[key] == before[key]
    assert after['result']['submission_response'] == original['submission_response']
    assert after['result']['submission_started'] is True and after['result']['accepted'] is True
    assert after['result']['verification']['attempts'] == 46 and not after['result']['verification']['exhausted']
    assert len(db.list(kind='UPLOAD')) == 1
    assert not db.record_duration(row['id'], 123)
    db.update(row['id'], status='PUBLISHED')
    assert not db.record_duration(row['id'], 20)


def test_unchanged_missing_lookup_stops_and_changed_metadata_gets_new_cycle():
    progress = {}; revision = lookup_revision({'work_id': '', 'duration_seconds': None})
    for i in range(MAX_UNRESOLVED_CHECKS):
        progress = check_progress(progress, 'missing', 1000 + i, revision)
    assert progress['exhausted'] and progress['next_check_at'] is None
    fresh = check_progress(progress, 'missing', 2000, lookup_revision({'work_id': '', 'duration_seconds': 90}))
    assert fresh['attempts'] == MAX_UNRESOLVED_CHECKS + 1 and fresh['attempts_for_input'] == 1
    assert not fresh['exhausted'] and fresh['next_check_at'] > 2000
    assert not check_progress(progress, 'reviewing', 2000, revision)['exhausted']


@pytest.mark.parametrize('response,code', [
    (SimpleNamespace(status=403), 'list_http_error'),
    (SimpleNamespace(status=200, json=AsyncMock(return_value={'status_code': 8})), 'list_api_error'),
    (SimpleNamespace(status=200, json=AsyncMock(return_value={'status_code': 0})), 'list_schema_error'),
    (SimpleNamespace(status=200, json=AsyncMock(side_effect=ValueError('invalid'))), 'list_invalid_json'),
])
def test_douyin_list_errors_do_not_mean_work_is_missing(response, code):
    context = SimpleNamespace(request=SimpleNamespace(get=AsyncMock(return_value=response)))
    with pytest.raises(DouyinLookupError) as error:
        asyncio.run(douyin_worker.owned_record(context, '7700000000000000001'))
    assert error.value.code == code and error.value.state == 'unavailable'


def test_douyin_advances_server_cursor_and_distinguishes_completed_empty_list():
    data = [{'status_code': 0, 'item_info_list': [], 'has_more': True, 'cursor': 'next'},
            {'status_code': 0, 'item_info_list': [], 'has_more': False}]
    request = AsyncMock(side_effect=[SimpleNamespace(status=200, json=AsyncMock(return_value=d)) for d in data])
    assert asyncio.run(douyin_worker.owned_record(SimpleNamespace(request=SimpleNamespace(get=request)), '7700000000000000001')) is None
    assert request.call_args_list[1].kwargs['params']['cursor'] == 'next'


def test_douyin_legacy_failure_history_is_retained_and_no_longer_requeued(tmp_path):
    db = DouyinStore(str(tmp_path)); parent = db.create('UPLOAD', '123', {'title': '原题'})
    receipt = {'accepted': True, 'submission_response': {'unchanged': True}}
    db.update(parent['id'], status='SUBMITTED', work_id='7700000000000000001', result=receipt)
    for _ in range(MAX_UNRESOLVED_CHECKS):
        child = db.create('RECONCILE', '123', {'work_id': '7700000000000000001'}, parent_id=parent['id'])
        db.update(child['id'], status='FAILED', error='legacy lookup error')
    douyin.settle_verification(db)
    current = db.get(parent['id'])
    assert current['status'] == 'SUBMITTED' and current['result']['accepted']
    assert current['result']['submission_response'] == receipt['submission_response']
    assert current['result']['verification']['exhausted']
    assert len(db.list(kind='RECONCILE', statuses=('FAILED',))) == MAX_UNRESOLVED_CHECKS
    assert len(db.list(kind='UPLOAD')) == 1
    before = current['result']['verification'].copy()
    douyin.settle_verification(db)
    assert db.get(parent['id'])['result']['verification'] == before


def test_background_timer_is_waiting_and_exhaustion_needs_attention():
    row = {'status': 'SUBMITTED', 'verification': {'automatic': True, 'running': False, 'next_check_at': 2000}}
    assert not publication_state.describe('kuaishou', 'job', row)['active']
    assert publication_state.describe('kuaishou', 'job', row)['state'] == 'waiting'
    row['verification']['running'] = True
    assert publication_state.describe('kuaishou', 'job', row)['active']
    row['verification'].update(running=False, automatic=False, exhausted=True)
    result = publication_state.describe('kuaishou', 'job', row)
    assert result['needs_attention'] and not result['active'] and result['recovery']['resumable']


def test_maintenance_keeps_receipts_and_waiting_comments_but_blocks_real_workers(tmp_path):
    import fcntl
    db = SocialStore(str(tmp_path), 'kuaishou')
    parent = db.create('UPLOAD', '123', {'title': '原题'})
    db.update(parent['id'], status='SUBMITTED', result={'accepted': True})
    child = db.create('FIRST_COMMENT', '123', {'text': '原评论'}, parent_id=parent['id'])
    lock = tmp_path / 'platforms/kuaishou/worker.lock'; lock.touch()
    before = db.list()
    assert not busy_work(tmp_path)
    with restart_gate(tmp_path, timeout=1):
        with lock.open('a') as probe:
            with pytest.raises(BlockingIOError):
                fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
    assert db.list() == before and db.get(child['id'])['status'] == 'WAITING'
    queued = db.create('RECONCILE', '123', parent_id=parent['id'])
    assert busy_work(tmp_path)[0]['count'] == 1
    with pytest.raises(RuntimeError, match='未发送重启信号'):
        with restart_gate(tmp_path, timeout=0.01):
            pytest.fail('must not enter maintenance with queued work')
    assert db.get(queued['id'])['status'] == 'QUEUED'


def test_old_web_receipt_replacement_can_be_repaired_without_overwriting_public_result(tmp_path):
    from scripts.repair_publishing_lookups import retain_douyin_receipts
    folder = tmp_path / 'platforms/douyin'; folder.mkdir(parents=True)
    db = DouyinStore(str(tmp_path)); original = db.create('UPLOAD', '123', {'title': '原题'})
    db.update(original['id'], status='SUBMITTED', work_id='7700000000000000001',
              result={'accepted': True, 'public': False, 'work_id': '7700000000000000001'})
    original = db.get(original['id'])
    db.update(original['id'], status='PUBLISHED', result={'public': True, 'evidence': 'anonymous_video_detail'})
    changes = retain_douyin_receipts(tmp_path, {'douyin': [original]}, apply=True)
    after = db.get(original['id'])
    assert changes[0]['fields_retained'] == ['accepted', 'work_id']
    assert after['status'] == 'PUBLISHED' and after['result']['public'] is True
    assert after['result']['accepted'] is True and after['result']['evidence'] == 'anonymous_video_detail'
    assert not retain_douyin_receipts(tmp_path, {'douyin': [original]}, apply=True)
    assert len(db.list(kind='UPLOAD')) == 1


def test_kuaishou_reads_old_works_beyond_first_thirty_without_any_submit():
    from core.social.adapters.kuaishou import _read_more_works
    records = [{'work_id': str(i)} for i in range(30)]
    page = SimpleNamespace(evaluate=AsyncMock(), wait_for_timeout=AsyncMock())
    async def scroll(script):
        assert 'scrollTop' in script and 'fetch' not in script and 'click' not in script
        records.append({'work_id': 'older_original_work'})
    page.evaluate.side_effect = scroll
    assert asyncio.run(_read_more_works(page, records, []))
    assert records[-1]['work_id'] == 'older_original_work'
    page.evaluate.side_effect = None
    assert not asyncio.run(_read_more_works(page, records, []))


def test_new_instance_ends_obsolete_maintenance_even_while_upload_is_queued(tmp_path):
    db = SocialStore(str(tmp_path), 'kuaishou'); queued = db.create('UPLOAD', '123')
    (tmp_path / 'app_runtime.json').write_text(json.dumps({'pid': 22, 'instance_id': 'new'}))
    with pytest.raises(MaintenanceSuperseded) as caught:
        with restart_gate(tmp_path, expected_runtime={'pid': 11, 'instance_id': 'old'}):
            pytest.fail('must not restart an already replaced instance')
    assert caught.value.runtime['pid'] == 22 and db.get(queued['id'])['status'] == 'QUEUED'
