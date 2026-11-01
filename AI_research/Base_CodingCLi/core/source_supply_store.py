"""Automatic sources and delivery baselines, separate from hand-maintained YAML."""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path

from .fileio import atomic_write, append_line, file_lock


def identity(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:20]


class SupplyStore:
    """Merge concurrent discovery and research receipts without replacing user config."""

    def __init__(self, state_dir: str | Path, profile: str):
        self.root = Path(state_dir) / "source_supply" / profile
        self.path = self.root / "pool.json"

    def pool(self) -> dict:
        return json.loads(self.path.read_text()) if self.path.exists() else {"sources": [], "links": []}

    def update(self, operation):
        with file_lock(str(self.path)):
            value = self.pool()
            result = operation(value)
            atomic_write(str(self.path), json.dumps(value, ensure_ascii=False, indent=2) + "\n")
            return result

    def add(self, entry: dict) -> dict:
        def merge(value):
            row = next((r for r in value["sources"] if r["spec"] == entry["spec"]), None)
            if row is None:
                row = {**entry, "id": "auto_" + identity(entry["spec"]), "stats": {}, "next_at": {},
                       "quiet": {}, "discovered_at": time.time()}
                value["sources"].append(row)
            else:
                row["jobs"] = list(dict.fromkeys(row["jobs"] + entry["jobs"]))
                row['coverage'] = list(dict.fromkeys(row.get('coverage', []) + entry.get('coverage', [])))
                if entry.get('role'):
                    row['role'] = entry['role']
                for key in ('sample', 'last_verified_at'):
                    if key in entry:
                        row[key] = entry[key]
            return dict(row)
        return self.update(merge)

    def remember_links(self, links: list[str]) -> None:
        if links:
            self.update(lambda value: value.update(links=list(dict.fromkeys(value.get("links", []) + links))[-200:]))

    def baseline(self, job: str) -> dict:
        path = self.root / "baselines" / (identity(job) + ".json")
        return json.loads(path.read_text()) if path.exists() else {"items": {}}

    def attempt(self, receipt: dict) -> None:
        append_line(str(self.root / "attempts.jsonl"), json.dumps(receipt, ensure_ascii=False) + '\n')

    def _material_path(self, job: str) -> Path:
        return self.root / 'candidates' / (identity(job) + '.json')

    def _materials(self, job: str) -> dict:
        path = self._material_path(job)
        value = json.loads(path.read_text()) if path.exists() else {}
        if value.get('schema') == 2:
            return value
        # Flat legacy records conflated adoption with the last delivery. Preserve
        # the old receipt, but do not invent a report-adoption relationship.
        rows = {}
        for key, row in value.items():
            uncertain = row.get('status') in {'reported', 'background'}
            rows[key] = {**row, 'history': [], 'legacy': dict(row), 'lifecycle': 'current',
                         **({'status': 'unknown', 'report': '', 'history_incomplete': True} if uncertain else {})}
        return {'schema': 2, 'current': {}, 'candidates': rows}

    def candidates(self, job: str) -> dict:
        return self._materials(job)['candidates']

    @staticmethod
    def _version_key(source: str, key: str) -> str:
        return identity(source + '\n' + key)

    def observe(self, job: str, observed: list[dict], *, at: float, complete_sources: set[str] | None = None) -> None:
        """Record live versions independently of whether a report gets saved."""
        path = self._material_path(job)
        with file_lock(str(path)):
            data = self._materials(job)
            seen = {self._version_key(row['source'], row['key']) for row in observed}
            for key, prior in list(data['current'].items()):
                if prior['source'] in (complete_sources or set()) and key not in seen and prior.get('at', 0) <= at:
                    data['current'][key] = {**prior, 'fingerprint': None, 'at': at}
            for row in observed:
                key = self._version_key(row['source'], row['key'])
                if data['current'].get(key, {}).get('at', 0) <= at:
                    data['current'][key] = {**row, 'at': at}
            for row in data['candidates'].values():
                latest = data['current'].get(self._version_key(row['source'], row['key']))
                if latest:
                    row.update(lifecycle='current' if latest['fingerprint'] == row['fingerprint'] else 'superseded',
                               superseded_by=None if latest['fingerprint'] == row['fingerprint'] else latest['fingerprint'])
                elif row['source'] in (complete_sources or set()) and row.get('last_offered', 0) <= at:
                    row.update(lifecycle='superseded', superseded_by=None)
            atomic_write(str(path), json.dumps(data, ensure_ascii=False))

    def retry_candidates(self, job: str, *, now: float, limit: int, exclude: set[str] | None = None) -> list[dict]:
        rows = [r for r in self.candidates(job).values() if r['status'] in {'offered', 'deferred'}
                and r.get('lifecycle', 'current') != 'superseded' and r['id'] not in (exclude or set())
                and now - r['last_offered'] >= 86400 and now - r['first_seen'] <= 14 * 86400
                and r.get('offers', 0) < 3]
        return sorted(rows, key=lambda r: r['last_offered'])[:limit]

    def _commit_candidates(self, pending: dict, report: str, report_path: str) -> dict:
        path = self.root / 'candidates' / (identity(pending['job']) + '.json')
        usage = {}
        for match in re.finditer(r'<!--\s*material-use:\s*(\{.*?\})\s*-->', report, re.S):
            try:
                value = json.loads(match.group(1))
                for status, key in [('reported', 'selected'), ('background', 'background'), ('deferred', 'deferred')]:
                    for item in value.get(key, []) if isinstance(value.get(key), list) else []:
                        if isinstance(item, str):
                            usage[item] = status
            except (ValueError, TypeError):
                continue
        with file_lock(str(path)):
            state = self._materials(pending['job'])
            data = state['candidates']
            for row in pending.get('candidates', []):
                old = data.get(row['id'], {})
                event = {'report': report_path, 'at': pending['at'], 'status': usage.get(row['id'], 'offered')}
                history = list(old.get('history', []))
                if not any(e['report'] == report_path and e['at'] == pending['at'] for e in history):
                    history.append(event)
                selected = [e for e in history if e['status'] == 'reported']
                background = [e for e in history if e['status'] == 'background']
                adoption = (selected or background or [event])[-1]
                latest = state['current'].get(self._version_key(row['source'], row['key']))
                superseded = bool(latest and latest['fingerprint'] != row['fingerprint'])
                data[row['id']] = {**old, **row, 'first_seen': old.get('first_seen', pending['at']),
                                   'last_offered': pending['at'], 'offers': len(history), 'history': history,
                                   'status': adoption['status'], 'report': adoption['report'],
                                   'last_report': report_path, 'lifecycle': 'superseded' if superseded else 'current'}
            # The ledger is an operational queue; the full material remains in the report/archive.
            # Keep adoption history even when the unadopted working queue is pruned.
            adopted = {key: row for key, row in data.items() if any(e['status'] in {'reported', 'background'} for e in row['history'])}
            recent = dict(sorted(data.items(), key=lambda item: item[1]['last_offered'], reverse=True)[:2000])
            state['candidates'] = {**adopted, **recent}
            atomic_write(str(path), json.dumps(state, ensure_ascii=False))
        return {key: value for key, value in usage.items() if key in {r['id'] for r in pending.get('candidates', [])}}

    def commit(self, pending: dict, report: str, report_path: str) -> None:
        """Only successfully saved reports advance delivered fingerprints and contribution."""
        from .provenance import extract_urls, normalize_url
        cited = {normalize_url(u) for u in extract_urls(report)}
        adoption = self._commit_candidates(pending, report, report_path)
        path = self.root / "baselines" / (identity(pending["job"]) + ".json")
        with file_lock(str(path)):
            current = self.baseline(pending["job"])
            # Initial scan is background, not a backlog of newly published news.
            # Subsequent runs only advance delivered changes; overflow remains retryable.
            current["items"].update(pending.get("initial_baseline", {}))
            current["items"].update(pending["delivered"])
            current.update(report=report_path, at=pending["at"])
            atomic_write(str(path), json.dumps(current, ensure_ascii=False) + "\n")

        def merge(value):
            for row in value["sources"]:
                seen = pending["sources"].get(row["spec"])
                if seen is None:
                    continue
                stats = row.setdefault("stats", {})
                used = len({normalize_url(u) for u in seen["urls"]} & cited)
                reported = sum(adoption.get(r['id']) == 'reported' for r in pending.get('candidates', []) if r['source'] == row['spec'])
                for name, amount in {"reports": 1, "changed": seen["changed"], "used": used, 'reported': reported,
                                     "errors": int(bool(seen.get("error")))}.items():
                    stats[name] = stats.get(name, 0) + amount
                job = pending["job"]
                quiet = 0 if seen["changed"] or used else row.setdefault("quiet", {}).get(job, 0) + 1
                row["quiet"][job] = quiet
                # Automatic sources only. Preserve declared-source cadence; cap silence at a week.
                hours = min(168, (12 if seen.get("error") else 6) * 2 ** min(quiet, 5))
                row.setdefault("next_at", {})[job] = pending["at"] + hours * 3600
                row["last"] = {"at": pending["at"], "job": job, "items": seen["scanned"],
                               "changed": seen["changed"], "used": used, "error": seen.get("error", "")}
        self.update(merge)
        self.attempt({"type": "report", "job": pending["job"], "at": pending["at"],
                      "report": report_path, "sources": pending["sources"]})
