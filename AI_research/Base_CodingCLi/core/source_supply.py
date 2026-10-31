"""Bounded full-text and change collection for opted-in matrix research Jobs."""
from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import logging
from pathlib import Path
import re
import sqlite3
import time
from urllib.parse import urlsplit

from .errors import CancelledError, SourcesUnavailableError
from .provenance import extract_urls, normalize_url
from .search import SearchClient
from .source_supply_store import SupplyStore
from .yaml_cache import load_yaml

logger = logging.getLogger(__name__)


def profiles(config_dir: str) -> dict[str, dict]:
    result = {}
    for path in sorted((Path(config_dir) / "source_supply").glob("*.yaml")):
        with path.open() as handle:
            value = load_yaml(handle) or {}
        if value.get("enabled", True):
            result[path.stem] = {**value, "key": path.stem}
    return result


def for_job(config_dir: str, job: dict) -> dict | None:
    return next((p for p in profiles(config_dir).values() if job.get("_file") in p.get("jobs", {})), None)


def section_key(item) -> str:
    # normalize_url strips tracking but also fragments. Keep heading fragments so
    # a late release-note section cannot collapse into the first one on the page.
    fragment = urlsplit(item.url).fragment
    if not fragment and item.provider == "aa":
        fragment = hashlib.sha256(item.title.encode()).hexdigest()[:16]
    return normalize_url(item.url) + ("#" + fragment if fragment else "")


def fingerprint(item) -> str:
    text = item.title + "\n" + "\n".join(item.excerpts)
    return hashlib.sha256(re.sub(r"\s+", " ", text).strip().encode()).hexdigest()


def read_page(url: str, *, limit: int = 80, timeout: int = 15, search_client=None, browser: bool = True):
    """Reuse public-page readers and anonymous browser; never use a publishing login."""
    from .sources import _get_page
    from .source_pages import fetch_page
    try:
        result = fetch_page(url, limit, timeout, get_page=_get_page, search_client=search_client, section_chars=24000)
        if not result:
            raise ValueError("正文读取未取得段落")
        return result
    except CancelledError:
        raise
    except Exception:
        if not browser:
            raise
        from .video.screenshots import page_for_capture
        from .source_pages import parse_page
        with page_for_capture(url, timeout_seconds=timeout) as page:
            page.wait_for_function("(document.querySelector('article, main') || document.body).innerText.length > 120",
                                   timeout=timeout * 1000)
            return parse_page(page.content(), page.url, limit, section_chars=24000)


def choose_pool(pool: list[dict], job: str, count: int, now: float) -> list[dict]:
    available = [r for r in pool if job in r["jobs"] and r.get("next_at", {}).get(job, 0) <= now + 300]
    def score(row):
        stats = row.get("stats", {})
        return (stats.get("used", 0) * 4 + stats.get("changed", 0)) / max(1, stats.get("reports", 0))
    ranked = sorted(available, key=lambda r: (-score(r), r.get("last", {}).get("at", 0)))
    chosen = ranked[:max(0, count - 1)]
    remaining = [r for r in available if r not in chosen]
    if remaining and count:
        chosen.append(min(remaining, key=lambda r: r.get("last", {}).get("at", 0)))
    return chosen


def collect(engine, job: dict, profile: dict) -> tuple[list, dict, dict, dict]:
    """Scan before slicing, deepen selected links, and return uncommitted delivery."""
    from .sources import fetch_source, resolve
    from .source_pages import canonical_url
    root = Path(getattr(engine, 'state_dir', Path(engine.workspace_dir) / 'state'))
    storage = SupplyStore(root, profile["key"])
    canonical = job["_file"]
    now = time.time()
    from .research_window import select
    window = job.get('_collection_window')
    from .production_policy import load, manages, references
    policy = job.get('_production_policy') or load(engine.config_dir)
    archive = None
    archive_error = ''
    reference_specs = set()
    if job.get('_issue_id') or manages(policy, engine.config_dir, job=canonical):
        from .material_archive import MaterialArchive
        try:
            archive = MaterialArchive(root)
        except (OSError, sqlite3.Error) as exc:
            archive_error = str(exc)[:300]
        reference_specs = references(policy, engine.config_dir)
    old = storage.baseline(canonical)["items"]
    declared = resolve(list(job.get("sources") or []), engine.config_dir)
    automatic = choose_pool(storage.pool()["sources"], canonical, int(profile.get("pool_per_job", 4)), now)
    specs = list(dict.fromkeys(declared + [r["spec"] for r in automatic]))
    scan = int(profile.get("scan_limit", 80))
    per_source = int(job.get("sources_limit", 10))
    mode = profile["jobs"][canonical].get("mode", "context")
    client = engine._source_options(["page:https://example.org"])["search_client"]
    pending = {"profile": profile["key"], "job": canonical, "at": now, "delivered": {}, "sources": {},
               "initial_baseline": {}}
    items, stats, candidates, discovered_links = [], {}, [], []
    queues = {}
    budget = int(profile.get("material_chars", 180000))
    used_chars = 0
    detail_left = int(profile.get("detail_pages", 4))
    detailed = set()
    receipt = {"version": 1, "profile": profile["key"], "mode": mode, "first_collection": not bool(old),
               "scan_limit": scan, "details": [], "sources": pending["sources"]}
    if archive_error:
        receipt['archive_error'] = archive_error
    if window:
        receipt['collection_window'] = window
    for spec in specs:
        if engine._cancel.is_set():
            raise CancelledError("信息源采集已取消")
        row = {"scanned": 0, "changed": 0, "delivered": 0, "urls": [], "error": ""}
        pending["sources"][spec] = row
        try:
            try:
                if spec.startswith("page:"):
                    fetched = read_page(spec[5:], limit=scan, search_client=client)
                else:
                    options = {'collection_window': window} if window else {}
                    fetched = fetch_source(spec, limit=scan, timeout=15, search_client=client, **options)
                if archive:
                    try:
                        archive.record(spec, fetched)
                    except (OSError, sqlite3.Error) as exc:
                        row['archive_error'] = str(exc)[:300]
                        archive = None
                row['date_coverage'] = getattr(fetched, 'coverage', {})
            except CancelledError:
                raise
            except Exception as exc:
                if not archive:
                    raise
                try:
                    archive.record(spec, [], error=str(exc))
                except (OSError, sqlite3.Error) as cache_error:
                    row['archive_error'] = str(cache_error)[:300]
                    archive = None
                row['error'] = str(exc)[:300]
                fetched = []
            if archive:
                from .material_archive import merge
                from datetime import datetime
                cutoff = datetime.fromisoformat(window['end']).timestamp() if window else now
                try:
                    archived, row['archive_coverage'] = archive.read(spec, before=cutoff)
                except (OSError, sqlite3.Error) as exc:
                    archived = []
                    row['archive_error'] = str(exc)[:300]
                    archive = None
                row['archived_items'] = len(archived)
                fetched = merge(archived, fetched)
            if not fetched:
                raise ValueError("未取得正文条目")
            row["scanned"] = len(fetched)
            fetched = select(fetched, window, reference=spec in reference_specs)
            changes, unchanged = [], []
            for item in fetched:
                key = section_key(item)
                record = {"fingerprint": fingerprint(item), "text": "\n".join(item.excerpts)}
                if not old:
                    pending["initial_baseline"][key] = record
                prior = old.get(key)
                changed = prior is None or prior["fingerprint"] != record["fingerprint"]
                if changed:
                    row["changed"] += 1
                (changes if changed else unchanged).append((item, key, record, prior))
                discovered_links.extend(extract_urls(item.url + "\n" + "\n".join(item.excerpts)))
            # Changed entries are considered before old context, even at the end of a long page.
            queues[spec] = (changes + (unchanged if mode == "context" else []))[:per_source]
            stats[spec] = 0
        except CancelledError:
            raise
        except Exception as exc:
            row["error"] = str(exc)[:300]
            stats[spec] = "失败：" + row["error"]
    # Round-robin across sources keeps a long first page from consuming the
    # entire context before a small specialist feed gets its first item.
    initial_budget = int(budget * .75) if detail_left else budget
    for index in range(per_source):
        for spec, selected in queues.items():
            if index < len(selected):
                item, key, record, prior = selected[index]
                row = pending["sources"][spec]
                if key in pending["delivered"]:
                    continue
                text = "\n".join(item.excerpts)
                state = "首次采集背景" if not old else "新增材料" if prior is None else "页面内容修改" if prior["fingerprint"] != record["fingerprint"] else "当前背景"
                if prior and prior["fingerprint"] != record["fingerprint"]:
                    text += "\n上次已交付内容：" + prior["text"][:3000]
                rendered = replace(item, excerpts=[f"采集状态：{state}；页面变化不等于事件刚刚发生。\n" + text])
                if used_chars + len(rendered.to_markdown()) > initial_budget:
                    continue
                used_chars += len(rendered.to_markdown())
                items.append(rendered)
                pending["delivered"][key] = record
                row["delivered"] += 1
                row["urls"].append(item.url)
                # Feeds and directories often contain only a teaser. Follow the
                # selected article, not every link or navigation entry.
                if item.provider in {"rss", "catalog", "tldr", "hn", "gh_new", "github", "arxiv", "hf_papers"} and detail_left:
                    target = canonical_url(item.url)
                    if target not in detailed:
                        candidates.append((target, row))
                        detailed.add(target)
                stats[spec] = row["delivered"]
    for url, row in candidates:
        if detail_left <= 0:
            break
        if engine._cancel.is_set():
            raise CancelledError("正文补读已取消")
        detail_left -= 1
        detail = {"url": url, "status": "unavailable", "sections": 0}
        receipt["details"].append(detail)
        try:
            parts = read_page(url, limit=scan, search_client=client)
            from .ai_dossier_sources import detail_sections
            parts = detail_sections(parts, max_chars=min(48000, budget - used_chars))
            for part in parts:
                size = len(part.to_markdown())
                if used_chars + size > budget:
                    continue
                items.append(part)
                used_chars += size
                detail["sections"] += 1
                row["urls"].append(part.url)
            detail["status"] = "read" if detail["sections"] else "budget"
        except CancelledError:
            raise
        except Exception as exc:
            detail["error"] = str(exc)[:300]
    storage.remember_links(list(dict.fromkeys(canonical_url(u) for u in discovered_links)))
    receipt.update(chars=used_chars, items=len(items), automatic=[r["spec"] for r in automatic])
    receipt["quiet"] = mode == "changes" and not items and not any(r["changed"] for r in pending["sources"].values())
    receipt["pending_changes"] = sum(max(0, r["changed"] - r["delivered"]) for r in pending["sources"].values())
    storage.attempt({"type": "collection", "at": now, "job": canonical, **receipt})
    if not any(r["scanned"] for r in pending["sources"].values()):
        raise SourcesUnavailableError(stats)
    return items, stats, pending, receipt


def material_block(items: list, receipt: dict) -> str:
    from .research_window import instruction
    intro = ("首次采集用于建立背景；发布时间以材料实际提供的时间为准。" if receipt["first_collection"] else
             "优先提供新增或修改材料；修改页面不代表产品刚发布。")
    failures = [f"- {spec}: {row['error']}" for spec, row in receipt.get("sources", {}).items() if row.get("error")]
    missing = "\n\n本次未取得内容的来源：\n" + "\n".join(failures) if failures else ""
    coverage = []
    for spec, row in receipt.get('sources', {}).items():
        unavailable = [day for day, detail in row.get('date_coverage', {}).items()
                       if detail.get('status') in {'unavailable', 'empty'}]
        if unavailable:
            coverage.append(f"- {spec} 未读取到这些日期：{', '.join(unavailable)}")
        archive = row.get('archive_coverage')
        if archive and receipt.get('collection_window'):
            from datetime import datetime
            start = datetime.fromisoformat(receipt['collection_window']['start'])
            first = archive.get('first_observation')
            if first is None or first > start.timestamp():
                coverage.append(f'- {spec} 的日常归档尚未积满本期范围；较早材料依赖来源历史接口，未保证覆盖。')
    coverage_text = '\n\n采集范围说明：\n' + '\n'.join(coverage) if coverage else ''
    return ("## 矩阵信息采集\n\n" + instruction(receipt.get('collection_window')) + intro +
            "扫描受单源条数与正文预算约束，不代表覆盖整个网站。\n\n" +
            (SearchClient.format_results(items) if items else
             "已成功读取来源，本次没有新增或修改材料；不要将上次事件重新当作新变化。") + missing + coverage_text)


def save_receipt(path: str, receipt: dict) -> None:
    from .fileio import atomic_write
    atomic_write(path + ".source-supply.json", json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
