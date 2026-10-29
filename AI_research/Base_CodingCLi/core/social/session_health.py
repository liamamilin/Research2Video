"""Derive whether a platform login still works from what its own jobs observed.

A login is called expired only after repeated identity failures with no verified
success since, never from a single timeout. No browser or network access here.
"""
from __future__ import annotations

IDENTITY_FAILURE = '尚未核实'
EXPIRED_AFTER = 2
VERIFY_INTERVAL = 3600
RECHECK_INTERVAL = 1800


def _success(row: dict) -> bool:
    if row['kind'] == 'LOGIN':
        return row['status'] == 'DONE'
    if row['kind'] == 'RECONCILE' and row['result'].get('platform_state') == 'metadata_missing':
        return False
    return row['kind'] in ('VERIFY', 'RECONCILE', 'COMMENT_CHECK') and row['status'] in ('DONE', 'RECORDED') and not row['error']


def assess(jobs: list[dict]) -> dict:
    """`jobs` are this account's queue rows in any order."""
    events = []
    for row in jobs:
        if str(row['error']).startswith(IDENTITY_FAILURE):
            events.append((row['updated_at'], False))
        elif _success(row):
            events.append((row['updated_at'], True))
    if not events:
        return {'state': 'unknown', 'failures': 0, 'checked_at': None, 'last_ok_at': None, 'since': None}
    events.sort()
    last_ok = max((when for when, ok in events if ok), default=None)
    failed = [when for when, ok in events if not ok and (last_ok is None or when > last_ok)]
    expired = len(failed) >= EXPIRED_AFTER
    return {'state': 'expired' if expired else 'valid', 'failures': len(failed), 'checked_at': events[-1][0],
            'last_ok_at': last_ok, 'since': failed[0] if expired else None}


def verify_due(session: dict, now: float) -> bool:
    """Whether an idle account is due for a read-only login check."""
    if session['checked_at'] is None:
        return False
    interval = RECHECK_INTERVAL if session['state'] == 'expired' else VERIFY_INTERVAL
    return now - session['checked_at'] >= interval
