"""Add missing local metadata to an original upload; never submit or reset it."""
from __future__ import annotations

from core.social.media import probe_duration
from core.social.verification import seconds


def lookup_for(row: dict) -> dict:
    result = row['result']
    return {'work_id': row['work_id'], 'title': row['payload'].get('title', ''),
            'submitted_at': result.get('submission_started_at') or row['created_at'],
            'duration_seconds': result.get('duration_seconds'),
            'correlations': result.get('correlations', [])}


def prepare_lookup(db, row: dict) -> dict:
    """Repair duration only when needed, preserving original response and identity."""
    if not row['work_id'] and seconds(row['result'].get('duration_seconds')) is None:
        duration = probe_duration(row['payload'].get('video_path', ''))
        if duration is not None:
            db.record_duration(row['id'], duration)
            row = db.get(row['id'])
    lookup = lookup_for(row)
    if not row['work_id']:
        lookup.update(competition(db, row))
    return lookup


def competition(db, row: dict) -> dict:
    """Other uploads of this account that a metadata-only match must not collide with."""
    uploads = [other for other in db.list(uid=row['uid'], kind='UPLOAD') if other['id'] != row['id']]
    return {'claimed': sorted({other['work_id'] for other in uploads if other['work_id']}),
            'peers': [{'submitted_at': other['result'].get('submission_started_at') or other['created_at'],
                       'duration_seconds': other['result'].get('duration_seconds')}
                      for other in uploads if not other['work_id'] and other['status'] in ('SUBMITTED', 'UNKNOWN')]}
