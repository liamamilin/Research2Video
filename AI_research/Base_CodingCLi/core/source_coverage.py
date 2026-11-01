"""Configured coverage goals and observations, independent of source popularity."""
from __future__ import annotations

import json
from pathlib import Path
import time

from .fileio import atomic_write, file_lock

ROLES = {'state', 'release', 'snapshot', 'editorial'}


def targets(profile: dict, job: str = '', pool: list[dict] | None = None) -> list[dict]:
    """Expand data-defined subject/topic goals into concrete, typed source slots."""
    result = []
    for cell in profile.get('coverage', []):
        if job and job not in cell.get('jobs', profile.get('jobs', {})):
            continue
        sources = cell.get('sources', []) + [{'spec': row['spec'], 'role': row.get('role', 'editorial')}
                   for row in pool or [] if cell['key'] in row.get('coverage', [])
                   and set(row.get('jobs', [])) & set(cell.get('jobs', profile.get('jobs', {})))
                   and (not job or job in row.get('jobs', []))]
        for source in sources:
            if source.get('role') not in ROLES or not source.get('spec'):
                raise ValueError('覆盖来源需要 spec 和有效 role')
            result.append({**source, 'cell': cell['key'], 'subject': cell.get('subject', cell['key']),
                           'topics': cell.get('topics', []), 'queries': cell.get('queries', []),
                           'max_age_hours': cell.get('max_age_hours', 48),
                           'jobs': cell.get('jobs', list(profile.get('jobs', {})))})
    return result


class CoverageStore:
    def __init__(self, state_dir, profile: str):
        self.path = Path(state_dir) / 'source_supply' / profile / 'coverage.json'

    def observations(self) -> dict:
        return json.loads(self.path.read_text()) if self.path.exists() else {}

    def record(self, sources: dict, *, at: float | None = None) -> None:
        now = time.time() if at is None else at
        with file_lock(str(self.path)):
            data = self.observations()
            for spec, row in sources.items():
                old = data.get(spec, {})
                count = row.get('live_items', row.get('scanned', row.get('items', 0)))
                live_status = row.get('live_status', 'read' if count and not row.get('error') else 'unavailable')
                success = bool(count) and live_status == 'read' and not row.get('error')
                if old.get('attempted_at', 0) > now:
                    if success and now > old.get('read_at', 0):
                        data[spec] = {**old, 'read_at': now}
                    continue
                data[spec] = {**old, 'attempted_at': now, 'error': row.get('error', ''), 'items': count,
                              'cache_items': row.get('cache_items', 0), 'live_status': live_status,
                              **({'read_at': now} if success else {})}
            atomic_write(str(self.path), json.dumps(data, ensure_ascii=False))

    def status(self, profile: dict, *, now: float | None = None) -> list[dict]:
        now = time.time() if now is None else now
        data = self.observations()
        from .source_supply_store import SupplyStore
        pool = SupplyStore(self.path.parent.parent.parent, profile['key']).pool()['sources']
        return [{**row, **data.get(row['spec'], {}),
                 'status': 'cached' if data.get(row['spec'], {}).get('live_status') in {'empty', 'unavailable'} and data.get(row['spec'], {}).get('cache_items') else
                           'unavailable' if data.get(row['spec'], {}).get('error') or data.get(row['spec'], {}).get('live_status') in {'empty', 'unavailable'} else
                           'missing' if not data.get(row['spec'], {}).get('read_at') else
                           'stale' if now - data[row['spec']]['read_at'] > row['max_age_hours'] * 3600 else 'current'}
                for row in targets(profile, pool=pool)]


def discovery_queries(profile: dict, state_dir) -> list[str]:
    """Spend discovery on uncovered or failed goals before general exploration."""
    rows = CoverageStore(state_dir, profile['key']).status(profile)
    missing = {row['cell'] for row in rows if row['status'] != 'current'}
    # Cells without a source are themselves explicit discovery gaps.
    observed_cells = {row['cell'] for row in rows}
    missing.update(cell['key'] for cell in profile.get('coverage', []) if cell['key'] not in observed_cells)
    cells = [cell for cell in profile.get('coverage', []) if cell['key'] in missing]
    if cells:
        offset = int(time.time() // 604800) % len(cells)
        cells = cells[offset:] + cells[:offset]
    return list(dict.fromkeys(q for cell in cells for q in cell.get('queries', [])))


def overview(config_dir: str, state_dir: str) -> dict:
    from .source_supply import profiles
    from .source_supply_store import SupplyStore
    result = []
    for profile in profiles(config_dir).values():
        rows = CoverageStore(state_dir, profile['key']).status(profile)
        cells = []
        for cell in profile.get('coverage', []):
            sources = [r for r in rows if r['cell'] == cell['key']]
            cells.append({**cell, 'sources': sources, 'status': 'missing' if not sources else
                          'current' if all(r['status'] == 'current' for r in sources) else 'gap'})
        storage = SupplyStore(state_dir, profile['key'])
        counts = {}
        for job in profile.get('jobs', {}):
            for row in storage.candidates(job).values():
                status = row['status']
                counts[status] = counts.get(status, 0) + 1
        result.append({'key': profile['key'], 'name': profile.get('name', profile['key']),
                       'cells': cells, 'candidates': counts})
    return {'profiles': result}


def material_records(config_dir: str, state_dir: str, *, profile: str = '', job: str = '',
                     text: str = '', status: str = '', offset: int = 0, limit: int = 30) -> dict:
    """Let the assistant distinguish a collected topic from a report that adopted it."""
    from .source_supply import profiles
    from .source_supply_store import SupplyStore
    rows = []
    for configured in profiles(config_dir).values():
        if profile and configured['key'] != profile:
            continue
        storage = SupplyStore(state_dir, configured['key'])
        for name in configured.get('jobs', {}):
            if job and name != job:
                continue
            for row in storage.candidates(name).values():
                item = row['item']
                if status and row['status'] != status:
                    continue
                if text and text.casefold() not in str([item['title'], item['url'], row['source'], item.get('excerpts', [])]).casefold():
                    continue
                rows.append({**{k: row.get(k) for k in ('id', 'source', 'role', 'status', 'first_seen', 'last_offered', 'offers', 'report', 'last_report', 'history', 'lifecycle', 'history_incomplete')},
                             'profile': configured['key'], 'job': name, 'title': item['title'], 'url': item['url']})
    rows.sort(key=lambda row: row['last_offered'], reverse=True)
    return {'items': rows[offset:offset + limit], 'total': len(rows),
            'next_offset': offset + limit if offset + limit < len(rows) else None,
            'note': 'reported 表示有报告采用回执；offered 表示仅已交付。未出现记录不能推断所有历史报告均未涉及。'}
