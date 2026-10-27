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
    return lookup_for(row)
