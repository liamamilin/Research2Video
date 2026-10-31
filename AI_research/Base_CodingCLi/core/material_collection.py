"""Recurring inexpensive collection: public source adapters, no research or writing model."""

from __future__ import annotations

import json
import time
from pathlib import Path

from core import matrices, production_policy, source_supply, sources
from core.config import load_job
from core.errors import CancelledError
from core.fileio import atomic_write
from core.lock import LockManager
from core.material_archive import MaterialArchive
from core.source_supply_store import SupplyStore
from core.state import StateManager


def collect(engine, policy: dict) -> dict:
    settings = policy.get("collection", {})
    root = Path(getattr(engine, "state_dir", Path(engine.workspace_dir) / "state"))
    archive = MaterialArchive(root)
    specs = []
    owners = matrices.load_all(engine.config_dir)
    active = set()
    for key in policy["matrices"]:
        owner = owners.get(key)
        if not owner or owner.paused or owner.archived:
            continue
        active.add(key)
        for name in owner.jobs:
            job = load_job(engine.jobs_dir, name)
            if job and job.get("enabled", True):
                specs.extend(
                    sources.resolve(job.get("sources") or [], engine.config_dir)
                )
    for profile in source_supply.profiles(engine.config_dir).values():
        if profile.get("matrix") in active:
            specs.extend(
                r["spec"] for r in SupplyStore(root, profile["key"]).pool()["sources"]
            )
    # Fetch source identities in a stable rotation when the configured bound is smaller than the pool.
    specs = sorted(set(specs))
    bound = settings.get("max_sources", 100)
    offset = (int(time.time() // 86400) * bound) % max(1, len(specs))
    selected = (specs[offset:] + specs[:offset])[:bound]
    receipt = {
        "policy_revision": policy["revision"],
        "sources_total": len(specs),
        "sources": {},
        "usage": {"total_tokens": 0, "llm_calls": 0, "searches": 0},
        "started_at": time.time(),
    }
    for spec in selected:
        if engine._cancel.is_set():
            raise CancelledError("素材积累已取消")
        if spec.startswith("search:"):
            receipt["sources"][spec] = {
                "status": "skipped",
                "reason": "搜索型来源留给研究任务，本采集不调用付费搜索",
            }
            continue
        try:
            items = sources.fetch_source(
                spec,
                limit=settings.get("limit_per_source", 80),
                timeout=settings.get("timeout_seconds", 15),
            )
            archive.record(spec, items)
            receipt["sources"][spec] = {
                "status": "read",
                "items": len(items),
                "coverage": getattr(items, "coverage", {}),
            }
        except CancelledError:
            raise
        except Exception as exc:
            try:
                archive.record(spec, [], error=str(exc))
            except Exception:
                # Preserve the source failure and earlier receipts even if the cache itself is unavailable.
                pass
            receipt["sources"][spec] = {
                "status": "unavailable",
                "error": str(exc)[:300],
            }
    archive.prune(settings.get("retention_days", 60))
    receipt["finished_at"] = time.time()
    return receipt


def run(engine, job: dict) -> str | None:
    started = time.time()
    try:
        with LockManager(job["_file"], stale_after=7200):
            policy = job.get("_production_policy") or production_policy.load(
                engine.config_dir
            )
            if not policy.get("collection", {}).get("enabled"):
                StateManager.update(job["_file"], "skipped", user=engine.user)
                return None
            receipt = collect(engine, policy)
            # JSON is not a research report and cannot wake report-to-video reconciliation.
            reference = f"output/source_collections/{time.time_ns()}.json"
            atomic_write(
                str(Path(engine.workspace_dir) / reference),
                json.dumps(receipt, ensure_ascii=False, indent=2),
            )
            read = any(r["status"] == "read" for r in receipt["sources"].values())
            StateManager.update(
                job["_file"],
                "success" if read else "failed",
                output_path=reference,
                error=None if read else "本次来源均未读取成功，已保留失败回执",
                usage=receipt["usage"],
                user=engine.user,
                duration_seconds=round(time.time() - started, 1),
            )
            return reference if read else None
    except Exception as exc:
        StateManager.update(
            job["_file"],
            "cancelled" if isinstance(exc, CancelledError) else "failed",
            error=str(exc)[:1000],
            user=engine.user,
            duration_seconds=round(time.time() - started, 1),
        )
        return None
