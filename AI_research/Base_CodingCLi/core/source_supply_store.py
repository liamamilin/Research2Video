"""Automatic sources and delivery baselines, separate from hand-maintained YAML."""
from __future__ import annotations

import hashlib
import json
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
            return dict(row)
        return self.update(merge)

    def remember_links(self, links: list[str]) -> None:
        if links:
            self.update(lambda value: value.update(links=list(dict.fromkeys(value.get("links", []) + links))[-200:]))

    def baseline(self, job: str) -> dict:
        path = self.root / "baselines" / (identity(job) + ".json")
        return json.loads(path.read_text()) if path.exists() else {"items": {}}

    def attempt(self, receipt: dict) -> None:
        append_line(str(self.root / "attempts.jsonl"), json.dumps(receipt, ensure_ascii=False))

    def commit(self, pending: dict, report: str, report_path: str) -> None:
        """Only successfully saved reports advance delivered fingerprints and contribution."""
        from .provenance import extract_urls, normalize_url
        cited = {normalize_url(u) for u in extract_urls(report)}
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
                for name, amount in {"reports": 1, "changed": seen["changed"], "used": used,
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
