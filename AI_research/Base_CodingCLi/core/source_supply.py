"""Bounded full-text and change collection for opted-in matrix research Jobs."""
from __future__ import annotations

from dataclasses import replace, asdict
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
from . import source_coverage
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


_MONTH = (r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|"
          r"Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)(?![a-z])")
_ANNOUNCED = re.compile(
    rf"\b(?:starting|beginning|effective|as of)\s+(?:(?:in|on|from)\s+)?(?:{_MONTH}|\d{{4}}-\d{{1,2}}-\d{{1,2}})"
    r"|(?:自|从)?\s*(?:\d{4}\s*年\s*)?\d{1,2}\s*月(?:\s*\d{1,2}\s*日)?\s*(?:起|开始)",
    re.I)


def announcements(text: str) -> list[str]:
    """Sentences on a price/plan page that state when a change takes effect, e.g. "Starting in December, ..."."""
    sentences = re.split(r"(?<=[.!?])\s+|(?<=[。！？])|\n+", text)
    return list(dict.fromkeys(s.strip() for s in sentences if _ANNOUNCED.search(s)))


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


def read_page(url: str, *, limit: int = 80, timeout: int = 15, search_client=None, browser: bool = True,
              catalog: bool = False):
    """Reuse public-page readers and anonymous browser; never use a publishing login."""
    from .sources import _get_page
    from .source_pages import fetch_page
    try:
        result = fetch_page(url, limit, timeout, get_page=_get_page, search_client=search_client,
                            section_chars=24000, catalog=catalog)
        if not result:
            raise ValueError("正文读取未取得段落")
        return result
    except CancelledError:
        raise
    except Exception:
        if not browser:
            raise
        from .video.screenshots import page_for_capture
        from .source_pages import parse_page, parse_catalog
        with page_for_capture(url, timeout_seconds=timeout) as page:
            page.wait_for_function("(document.querySelector('article, main') || document.body).innerText.length > 120",
                                   timeout=timeout * 1000)
            return (parse_catalog(page.content(), page.url, limit) if catalog else
                    parse_page(page.content(), page.url, limit, section_chars=24000))


def choose_pool(pool: list[dict], job: str, count: int, now: float) -> list[dict]:
    available = [r for r in pool if job in r["jobs"] and r.get("next_at", {}).get(job, 0) <= now + 300]
    def score(row):
        stats = row.get("stats", {})
        return (stats.get("reported", stats.get("used", 0)) * 4 + min(5, stats.get("changed", 0))) / max(1, stats.get("reports", 0))
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
    coverage = source_coverage.targets(profile, canonical, storage.pool()['sources'])
    metadata = {r['spec']: r for r in automatic}
    metadata.update({r['spec']: r for r in coverage})
    reference_specs.update(r['spec'] for r in coverage if r['role'] in {'state', 'snapshot'})
    specs = list(dict.fromkeys([r['spec'] for r in coverage] + declared + [r["spec"] for r in automatic]))
    # Snapshot volume is useful context but cannot displace product changes and articles.
    specs.sort(key=lambda spec: metadata.get(spec, {}).get('role') == 'snapshot')
    scan = int(profile.get("scan_limit", 80))
    per_source = int(job.get("sources_limit", 10))
    mode = profile["jobs"][canonical].get("mode", "context")
    client = engine._source_options(["page:https://example.org"])["search_client"]
    pending = {"profile": profile["key"], "job": canonical, "at": now, "delivered": {}, "sources": {},
               "initial_baseline": {}, "candidates": []}
    items, stats, candidates, discovered_links = [], {}, [], []
    observed, complete_sources = [], set()
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
        role = metadata.get(spec, {}).get('role', 'editorial')
        row = {"scanned": 0, "changed": 0, "delivered": 0, "urls": [], "error": "", 'role': role,
               'coverage': metadata.get(spec, {}).get('cell', ''), 'live_items': 0, 'cache_items': 0,
               'live_status': 'pending'}
        live_keys = set()
        pending["sources"][spec] = row
        try:
            try:
                if spec.startswith(("page:", "catalog:")):
                    kind, url = spec.split(":", 1)
                    fetched = read_page(url, limit=scan, search_client=client, catalog=kind == "catalog")
                else:
                    options = {'collection_window': window} if window else {}
                    fetched = fetch_source(spec, limit=scan, timeout=15, search_client=client, **options)
                row.update(live_items=len(fetched), live_status='read' if fetched else 'empty')
                if not fetched:
                    row['error'] = '本次实时读取未取得正文条目'
                live_keys = {section_key(item) for item in fetched}
                if spec not in metadata and fetched and all(item.provider in {'aa', 'hf_models', 'wb'} for item in fetched):
                    role = row['role'] = 'snapshot'
                if role in {'state', 'snapshot'} and fetched and len(fetched) < scan:
                    complete_sources.add(spec)
                if archive:
                    try:
                        archive.record(spec, fetched, error=row['error'])
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
                row['live_status'] = 'unavailable'
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
                row['cache_items'] = len([item for item in archived if section_key(item) not in live_keys]) if not fetched or role not in {'state', 'snapshot'} else 0
                if not fetched or role not in {'state', 'snapshot'}:
                    fetched = merge(archived, fetched)
            if not fetched:
                raise ValueError("未取得正文条目")
            row["scanned"] = len(fetched)
            if spec not in metadata and all(item.provider in {'aa', 'hf_models', 'wb'} for item in fetched):
                role = row['role'] = 'snapshot'
            fetched = select(fetched, window, reference=spec in reference_specs or role in {'state', 'snapshot'})
            changes, unchanged = [], []
            for item in fetched:
                key = section_key(item)
                record = {"fingerprint": fingerprint(item), "text": "\n".join(item.excerpts)}
                if key in live_keys:
                    observed.append({'source': spec, 'key': key, 'fingerprint': record['fingerprint']})
                if not old:
                    pending["initial_baseline"][key] = record
                prior = old.get(key)
                changed = prior is None or prior["fingerprint"] != record["fingerprint"]
                if changed:
                    row["changed"] += 1
                (changes if changed else unchanged).append((item, key, record, prior))
                discovered_links.extend(extract_urls(item.url + "\n" + "\n".join(item.excerpts)))
            # Changed entries are considered before old context, even at the end of a long page.
            limit = min(per_source, int(profile.get('snapshot_items', 2))) if role == 'snapshot' else per_source
            queues[spec] = (changes + (unchanged if mode == "context" or role == 'state' else []))[:limit]
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
                state = "首次观察材料" if not old else "新增材料" if prior is None else "页面内容修改" if prior["fingerprint"] != record["fingerprint"] else "当前背景"
                if row['role'] == 'state' and prior is None:
                    state = '首次观察当前状态，尚无历史对照，不能据此宣称降价或涨价'
                # A dated announcement new on a reference page is the period's change, unlike the page around it.
                announced = [a for a in announcements(text) if not prior or a not in prior["text"]] \
                    if old and row['role'] in {'state', 'snapshot'} else []
                if announced:
                    state = f"页面新增带生效时间的公告：{' '.join(announced)[:600]}——这条公告可作为本期变化，时间以页面原文为准；页面其余内容仍是当前背景"
                if prior and prior["fingerprint"] != record["fingerprint"]:
                    text += "\n上次已交付内容：" + prior["text"][:3000]
                material_id = 'M' + hashlib.sha256((canonical + key + record['fingerprint']).encode()).hexdigest()[:16]
                rendered = replace(item, excerpts=[f"材料编号：{material_id}；来源角色：{row['role']}。\n采集状态：{state}{'' if announced else '；页面变化不等于事件刚刚发生'}。\n" + text])
                if used_chars + len(rendered.to_markdown()) > initial_budget:
                    continue
                used_chars += len(rendered.to_markdown())
                items.append(rendered)
                pending["delivered"][key] = record
                pending['candidates'].append({'id': material_id, 'source': spec, 'key': key, 'fingerprint': record['fingerprint'],
                                              'item': asdict(item), 'role': row['role']})
                row["delivered"] += 1
                row["urls"].append(item.url)
                # Feeds and directories often contain only a teaser. Follow the
                # selected article, not every link or navigation entry.
                if item.provider in {"rss", "catalog", "tldr", "hn", "gh_new", "github", "arxiv", "hf_papers"} and detail_left:
                    target = canonical_url(item.url)
                    if target not in detailed:
                        candidates.append((target, row, material_id))
                        detailed.add(target)
                stats[spec] = row["delivered"]
    attempts = 0
    max_attempts = int(profile.get('detail_attempts', max(4, detail_left * 3)))
    for url, row, material_id in candidates:
        if detail_left <= 0 or attempts >= max_attempts or used_chars >= budget:
            break
        if engine._cancel.is_set():
            raise CancelledError("正文补读已取消")
        attempts += 1
        detail = {"url": url, "status": "unavailable", "sections": 0}
        receipt["details"].append(detail)
        try:
            parts = read_page(url, limit=scan, search_client=client)
            from .ai_dossier_sources import detail_sections
            parts = detail_sections(parts, max_chars=min(48000, budget - used_chars))
            for part in parts:
                part = replace(part, excerpts=[*part.excerpts, f'材料编号：{material_id}（正文补读）'])
                size = len(part.to_markdown())
                if used_chars + size > budget:
                    continue
                items.append(part)
                used_chars += size
                detail["sections"] += 1
                row["urls"].append(part.url)
            detail["status"] = "read" if detail["sections"] else "budget"
            if detail['sections']:
                detail_left -= 1
        except CancelledError:
            raise
        except Exception as exc:
            detail["error"] = str(exc)[:300]
    # A delivered change is not automatically an adopted story. Re-offer a bounded
    # number on a later day; baseline comparison and editorial adoption stay separate.
    from .search import SearchResult
    storage.observe(canonical, observed, at=now, complete_sources=complete_sources)
    retry_items = 0
    for candidate in storage.retry_candidates(canonical, now=now, limit=int(profile.get('retry_items', 0)),
                                               exclude={r['id'] for r in pending['candidates']}):
        item = SearchResult(**candidate['item'])
        rendered = replace(item, excerpts=[f"材料编号：{candidate['id']}；此前已交付，未记录为已采用，供本次重新选题。", *item.excerpts])
        size = len(rendered.to_markdown())
        if used_chars + size <= budget:
            items.append(rendered)
            used_chars += size
            pending['candidates'].append({k: candidate[k] for k in ('id', 'source', 'key', 'fingerprint', 'item', 'role')})
            retry_items += 1
    source_coverage.CoverageStore(root, profile['key']).record(pending['sources'], at=now)
    receipt['coverage'] = source_coverage.CoverageStore(root, profile['key']).status(profile, now=now)
    receipt['detail_budget'] = {'success_limit': int(profile.get('detail_pages', 4)), 'attempt_limit': max_attempts,
                                'attempts': attempts, 'successful': sum(d['status'] == 'read' for d in receipt['details'])}
    storage.remember_links(list(dict.fromkeys(canonical_url(u) for u in discovered_links)))
    receipt.update(chars=used_chars, items=len(items), retry_items=retry_items, automatic=[r["spec"] for r in automatic])
    receipt["quiet"] = mode == "changes" and not retry_items and not any(r["changed"] for r in pending["sources"].values())
    receipt["pending_changes"] = sum(max(0, r["changed"] - r["delivered"]) for r in pending["sources"].values())
    storage.attempt({"type": "collection", "at": now, "job": canonical, **receipt})
    if not items and not any(r["scanned"] for r in pending["sources"].values()):
        raise SourcesUnavailableError(stats)
    return items, stats, pending, receipt


def material_block(items: list, receipt: dict) -> str:
    from .research_window import instruction
    intro = ("首次采集建立观察基线；有明确发布时间且落在本期范围内的公告、更新和案例仍可作为本期选题。"
             "日期不明的材料作为背景；发布时间以材料实际提供的时间为准。" if receipt["first_collection"] else
             "优先提供新增或修改材料；修改页面不代表产品刚发布。")
    if receipt.get('retry_items'):
        intro += f"另有 {receipt['retry_items']} 条此前未采用的材料重新供选题；可按原发布日期补充报道，不因来源本次无变化而忽略，也不要称为今日新发布。"
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
            '\n角色 state 是当前价格/额度/能力说明，release 是更新公告，snapshot 是榜单/数据库快照，editorial 是文章与案例。'
            '\n报告末尾附一条不可见素材回执：<!-- material-use: {"selected":["实际用于选题或结论的材料编号"],"background":["只作解释背景的编号"],"deferred":["有价值但本次未用的编号"]} -->。只填提供的编号，未采用的不要伪称采用。\n' +
            "扫描受单源条数与正文预算约束，不代表覆盖整个网站。\n\n" +
            (SearchClient.format_results(items) if items else
             "已成功读取来源，本次没有新增或修改材料；不要将上次事件重新当作新变化。") + missing + coverage_text)


def save_receipt(path: str, receipt: dict) -> None:
    from .fileio import atomic_write
    atomic_write(path + ".source-supply.json", json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
