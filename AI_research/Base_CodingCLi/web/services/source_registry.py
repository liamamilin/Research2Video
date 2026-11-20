"""Versioned source registry edits shared by the page and the assistant."""
from __future__ import annotations

import hashlib
import re
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import HTTPException

from core import sources
from core.config import list_jobs, load_job
from core.fileio import file_lock
from web.services import yaml_io
from web.settings import get_settings


def snapshot() -> dict:
    path = Path(get_settings().paths.config_dir) / sources.REGISTRY_FILE
    content = path.read_text(encoding='utf-8') if path.exists() else ''
    return {'revision': hashlib.sha256(content.encode()).hexdigest(),
            'sources': sources.load_registry(str(path.parent)), 'adapters': list(sources._ADAPTERS)}


def validate_entry(key: str, fields: dict) -> None:
    if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,99}', key):
        raise ValueError('信息源 ID 使用字母、数字、下划线、点或连字符')
    if set(fields) - {'name', 'spec', 'tags', 'note', 'enabled'}:
        raise ValueError('仅支持 name、spec、tags、note、enabled')
    spec = fields.get('spec', '')
    kind, _, target = spec.partition(':')
    if kind not in sources._ADAPTERS or not target.strip():
        raise ValueError('spec 需要支持的来源类型与地址，例如 rss:https://example.org/feed')
    if kind in ('rss', 'feed', 'page', 'catalog'):
        url = urlsplit(target)
        if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password:
            raise ValueError('来源地址需要不带账号密码的 HTTP 或 HTTPS URL')
    if kind == 'github' and not re.fullmatch(r'[\w.-]+/[\w.-]+', target):
        raise ValueError('GitHub 来源使用 github:owner/repo')
    if not isinstance(fields.get('tags', []), list) or any(not isinstance(t, str) or not t.strip() for t in fields.get('tags', [])):
        raise ValueError('tags 需要非空字符串列表')


def usages_map(registry: dict, keys: list[str] | None = None) -> dict[str, list[dict]]:
    """Compute source usage across all Jobs in a single scan (instead of O(sources * jobs))."""
    wanted = [k for k in (keys if keys is not None else list(registry)) if k in registry]
    result: dict[str, list[dict]] = {k: [] for k in wanted}
    if not wanted:
        return result
    paths = get_settings().paths
    from core import source_supply, source_coverage

    profiles = source_supply.profiles(paths.config_dir)
    profile_by_job = {
        job_name: prof
        for prof in profiles.values()
        for job_name in prof.get("jobs", {})
    }
    tag_to_keys: dict[str, list[str]] = {}
    spec_to_keys: dict[str, list[str]] = {}
    for k in wanted:
        entry = registry[k]
        for tag in entry.get("tags", []):
            tag_to_keys.setdefault(tag, []).append(k)
        if entry.get("spec"):
            spec_to_keys.setdefault(entry["spec"], []).append(k)

    for job in list_jobs(paths.jobs_dir):
        data = load_job(paths.jobs_dir, job) or {}
        job_sources = data.get("sources") or []
        per_key: dict[str, list[str]] = {}
        for s in job_sources:
            text = str(s)
            if text.startswith("source:"):
                k = text[7:]
                if k in result:
                    per_key.setdefault(k, []).append(text)
            elif text.startswith("tag:"):
                for k in tag_to_keys.get(text[4:], ()):
                    per_key.setdefault(k, []).append(text)
            for k in spec_to_keys.get(text, ()):
                per_key.setdefault(k, []).append(text)
        profile = profile_by_job.get(job) if data.get("source_supply_enabled", True) else None
        if profile:
            for target in source_coverage.targets(profile, job):
                for k in spec_to_keys.get(target.get("spec", ""), ()):
                    per_key.setdefault(k, []).append(target["spec"])
        job_name = data.get("name") or job
        for k, refs in per_key.items():
            result[k].append({"job": job, "name": job_name, "references": refs})
    return result


def usages(registry: dict, key: str) -> list[dict]:
    return usages_map(registry, [key]).get(key, [])


def change(action: str, key: str, fields: dict, revision: str) -> dict:
    path = Path(get_settings().paths.config_dir) / sources.REGISTRY_FILE
    with file_lock(str(path)):
        current = snapshot()
        if current['revision'] != revision:
            raise HTTPException(409, '信息源已被修改，请重新读取后保存；未覆盖其他修改')
        registry = current['sources']
        if action == 'create' and key in registry:
            raise HTTPException(409, '这个信息源 ID 已存在')
        if action != 'create' and key not in registry:
            raise HTTPException(404, '信息源不存在')
        used_by = usages(registry, key) if key in registry else []
        old = path.read_text(encoding='utf-8') if path.exists() else ''
        raw = yaml_io.parse_yaml(old) if old.strip() else {}
        if action == 'delete':
            candidate = {k: v for k, v in registry.items() if k != key}
            for use in used_by:
                try:
                    sources.resolve(use['references'], registry=candidate)
                except sources.RegistryError as exc:
                    raise HTTPException(409, {'message': '删除会使任务来源失效，请先调整引用：' + str(exc), 'used_by': used_by}) from exc
            del raw[key]
        else:
            entry = {**registry.get(key, {'name': key, 'tags': [], 'note': ''}), **fields}
            validate_entry(key, entry)
            candidate = {**registry, key: entry}
            # A tag edit must not silently invalidate an existing job.
            for use in used_by:
                try:
                    sources.resolve(use['references'], registry=candidate)
                except sources.RegistryError as exc:
                    raise HTTPException(409, {'message': str(exc), 'used_by': used_by}) from exc
            raw[key] = {**dict(raw.get(key) or {}), **entry}
        backup = None
        if old:
            # Unique names keep several changes within one second recoverable.
            backup = yaml_io.save_backup(str(path.with_name('sources_' + uuid.uuid4().hex[:8] + '.yaml')), old)
        path.parent.mkdir(parents=True, exist_ok=True)
        yaml_io.atomic_write(str(path), yaml_io.dump_yaml(raw))
    return {'id': key, 'revision': snapshot()['revision'], 'used_by': used_by, 'backup': backup,
            'message': {'create': '信息源已登记', 'update': '信息源已更新，后续研究使用新配置', 'delete': '信息源已删除'}[action],
            'href': '/sources'}
