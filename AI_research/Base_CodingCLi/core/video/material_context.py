"""Versioned creative intent shared by research, authoring and production."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json


class ContextConflict(ValueError):
    """A running task or a newer revision owns the requested update."""


def get(script: dict, version: dict | None = None) -> dict:
    info = ((version or {}).get('meta') or {}).get('user_materials')
    info = deepcopy(info if info is not None else script.get('topic', {}).get('user_materials') or {})
    if not info:
        return {}
    info.setdefault('revision', 1)
    info.setdefault('title', script.get('topic', {}).get('title', ''))
    info.setdefault('audience', script.get('topic', {}).get('fields', {}).get('目标受众', '普通观众'))
    info.setdefault('style', {})
    return info


def revise(info: dict, **changes) -> dict:
    next_info = deepcopy(info)
    next_info.update({key: value for key, value in changes.items() if value is not None})
    if next_info != info:
        next_info['revision'] = info.get('revision', 1) + 1
    return next_info


def brief(info: dict) -> str:
    return ('## 本次创作要求\n选题：' + info.get('title', '') + '\n目标观众：' + info.get('audience', '普通观众')
            + '\n用户要求：' + info.get('goal', '') + '\n')


def research_key(info: dict) -> str:
    # A wording style change reuses research; changed materials or scope do not.
    value = {key: info.get(key) for key in ('ids', 'goal', 'title', 'audience')}
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]
