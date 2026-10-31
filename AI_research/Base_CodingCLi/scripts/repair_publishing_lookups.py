#!/usr/bin/env python3
"""Repair local lookup metadata only; no browser, upload, replay, or task reset."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.social.media import probe_duration
from core.social.store import SocialStore
from core.social.verification import seconds
from core.douyin_store import DouyinStore


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def repair(state_dir: Path, *, apply: bool = False) -> dict:
    """Preserve every original upload and acknowledgement while filling missing duration."""
    rows = []
    for platform in ('kuaishou', 'xiaohongshu'):
        path = state_dir / 'platforms' / platform / 'queue.db'
        if not path.is_file():
            continue
        with sqlite3.connect(f'file:{path}?mode=ro', uri=True) as conn:
            conn.row_factory = sqlite3.Row
            pending = [SocialStore.decode(row) for row in conn.execute(
                "SELECT * FROM jobs WHERE kind='UPLOAD' AND status IN ('SUBMITTED','UNKNOWN')")]
        db = SocialStore(str(state_dir), platform) if apply else None
        for before in pending:
            if before['work_id'] or seconds(before['result'].get('duration_seconds')) is not None:
                continue
            duration = probe_duration(before['payload'].get('video_path', ''))
            changed = db.record_duration(before['id'], duration) if db and duration is not None else False
            after = db.get(before['id']) if db else before
            identity = ('id', 'kind', 'uid', 'parent_id', 'script_id', 'version', 'render_id', 'created_at')
            preserved = (all(after[key] == before[key] for key in identity)
                         and digest(after['payload']) == digest(before['payload'])
                         and digest(after['result'].get('submission_response')) == digest(before['result'].get('submission_response')))
            if not preserved:
                raise RuntimeError('原投稿身份或回执发生变化，请检查执行记录')
            rows.append({'platform': platform, 'id': before['id'], 'duration_seconds': duration,
                         'metadata_repaired': changed, 'original_identity_payload_response_preserved': preserved,
                         'before_status': before['status'], 'after_status': after['status']})
    return {'checked_at': time.time(), 'apply': apply, 'metadata_repaired': sum(r['metadata_repaired'] for r in rows),
            'unreadable_media': sum(r['duration_seconds'] is None for r in rows), 'items': rows,
            'uploads_submitted': 0, 'tasks_cleared': 0}


def retain_douyin_receipts(state_dir: Path, baseline: dict, *, apply: bool = False) -> list[dict]:
    """Restore fields an older web instance dropped, keeping every newer result."""
    if not (state_dir / 'douyin.db').is_file():
        return []
    db = DouyinStore(str(state_dir)); changes = []
    with (state_dir / 'platforms/douyin/dispatch.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        for original in baseline.get('douyin', []):
            if original['kind'] != 'UPLOAD':
                continue
            current = db.get(original['id'])
            if (not current or any(current[key] != original[key] for key in
                    ('kind', 'work_id', 'script_id', 'version', 'render_id', 'created_at'))):
                continue
            merged = {**original['result'], **current['result']}
            missing = sorted(set(original['result']) - set(current['result']))
            if missing:
                if apply:
                    db.update(current['id'], expected=current['status'], result=merged)
                changes.append({'id': current['id'], 'fields_retained': missing,
                                'status_preserved': current['status'], 'applied': apply})
    return changes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir', type=Path, required=True)
    parser.add_argument('--apply', action='store_true', help='Only add duration to existing pending uploads')
    parser.add_argument('--receipt', type=Path)
    parser.add_argument('--baseline', type=Path, help='Task-owned original receipt snapshot; restore missing fields only')
    args = parser.parse_args()
    receipt = repair(args.state_dir.resolve(), apply=args.apply)
    if args.baseline:
        receipt['retained_original_receipts'] = retain_douyin_receipts(
            args.state_dir.resolve(), json.loads(args.baseline.read_text()), apply=args.apply)
    if args.receipt:
        args.receipt.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({k: v for k, v in receipt.items() if k != 'items'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
