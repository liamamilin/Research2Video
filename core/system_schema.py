"""Validate changed application settings before publishing YAML.

Unchanged extension fields survive edits; new or changed unknown fields fail
instead of becoming settings that the application silently ignores.
"""
from __future__ import annotations

import math
import json

POSITIVE_INT = (int, 1, None)
NONNEGATIVE_INT = (int, 0, None)
POSITIVE_NUMBER = ((int, float), 0, None, True)
NONNEGATIVE_NUMBER = ((int, float), 0, None)
RATIO = ((int, float), 0, 1)
STRINGS = [str]
ENV_MAP = {'*': str}
HEADERS = {'*': str}
CHANNEL = {'enabled': bool, 'webhook_url': str, 'webhook_url_env': str}
SCHEMA = {
    'ai': {
        'model': str, 'base_url': str, 'api_key': str, 'api_key_env': str,
        'embedding_model': str, 'embedding_base_url': str, 'embedding_api_key_env': str,
        'timeout': POSITIVE_INT, 'request_timeout': POSITIVE_INT,
        'max_tokens': POSITIVE_INT, 'temperature': NONNEGATIVE_NUMBER,
        'max_retries': NONNEGATIVE_INT, 'stream': bool,
        'reasoning_content': ('enum', 'auto', 'always', 'never'),
        'extra_body': dict, 'extra_headers': HEADERS,
        'pricing': {'input_per_1m': NONNEGATIVE_NUMBER, 'output_per_1m': NONNEGATIVE_NUMBER},
    },
    'search': {
        'provider': str, 'api_key_env': str, 'api_key_envs': ENV_MAP,
        'mode': str, 'max_results': POSITIVE_INT, 'max_chars_per_result': POSITIVE_INT,
        'max_chars_total': POSITIVE_INT, 'after_date': str, 'after_days': NONNEGATIVE_INT,
        'timeout': POSITIVE_INT, 'fallback_provider': str, 'fallback_providers': STRINGS,
    },
    'qa': {'recency_weight': RATIO, 'recency_half_life_days': POSITIVE_NUMBER,
           'overfetch': POSITIVE_INT, 'round_scope': NONNEGATIVE_INT},
    'research': {
        'mode': ('enum', 'auto', 'agent', 'pipeline'), 'max_rounds': POSITIVE_INT,
        'max_searches': NONNEGATIVE_INT, 'max_chars_per_call': POSITIVE_INT,
        'max_file_chars': POSITIVE_INT, 'context_char_budget': POSITIVE_INT,
        'min_report_chars': NONNEGATIVE_INT,
        'event_memory': {'enabled': bool, 'days': POSITIVE_INT, 'limit': POSITIVE_INT},
        'citation_check': {'enabled': bool, 'min_coverage': RATIO, 'verify_unreachable': bool,
                           'max_checks': NONNEGATIVE_INT, 'timeout': POSITIVE_NUMBER},
    },
    'budget': {'monthly_usd_limit': NONNEGATIVE_NUMBER, 'warn_ratio': RATIO, 'block_pipeline': bool},
    'notifications': {
        **CHANNEL, 'notify_on': STRINGS, 'timeout': POSITIVE_INT,
        'digest': {'enabled': bool, 'max_actions': NONNEGATIVE_INT},
        'wecom': CHANNEL, 'feishu': {**CHANNEL, 'secret': str, 'secret_env': str},
        'pushplus': {'enabled': bool, 'token': str, 'token_env': str, 'topic': str},
        'serverchan': {'enabled': bool, 'sendkey': str, 'sendkey_env': str},
        'email': {'enabled': bool, 'smtp_host': str, 'smtp_port': (int, 1, 65535),
                  'smtp_user': str, 'smtp_password_env': str, 'from_addr': str,
                  'to_addrs': STRINGS, 'use_ssl': bool},
    },
    'extraction': {'enabled': bool, 'model': str, 'base_url': str, 'api_key_env': str},
    'defaults': {'output_dir': str},
    'logging': {'level': ('enum', 'DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL', 'NOTSET'), 'file': str},
    'cleanup': {},
}


def validate_system_change(before: dict, after: dict) -> None:
    """Reject invalid changed fields, without copying values into errors."""
    def walk(value, previous, rule, path):
        if isinstance(rule, dict):
            if not isinstance(value, dict):
                raise ValueError(f'{path or "配置"} 必须是对象')
            previous = previous if isinstance(previous, dict) else {}
            for key, child in value.items():
                name = f'{path}.{key}' if path else str(key)
                if key in previous:
                    try:
                        if json.dumps(child, sort_keys=True, default=str) == json.dumps(previous[key], sort_keys=True, default=str):
                            continue
                    except TypeError:
                        pass
                spec = rule.get(key, rule.get('*'))
                if spec is None:
                    raise ValueError(f'未知配置字段：{name}')
                walk(child, previous.get(key), spec, name)
        elif isinstance(rule, list):
            if not isinstance(value, list):
                raise ValueError(f'{path} 必须是数组')
            for item in value:
                walk(item, None, rule[0], path)
        elif isinstance(rule, tuple):
            if rule[0] == 'enum':
                if not isinstance(value, str) or value not in rule[1:]:
                    raise ValueError(f'{path} 不在支持的选项中')
                return
            kind, minimum, maximum, *exclusive = rule
            if type(value) is bool or not isinstance(value, kind) or not math.isfinite(value):
                raise ValueError(f'{path} 必须是有效数值')
            if minimum is not None and (value <= minimum if exclusive else value < minimum):
                raise ValueError(f'{path} 小于允许范围')
            if maximum is not None and value > maximum:
                raise ValueError(f'{path} 大于允许范围')
        elif not isinstance(value, rule) or (rule is int and type(value) is bool):
            raise ValueError(f'{path} 类型错误')
    walk(after, before, SCHEMA, '')


def validate_system_patch(patch: dict, before: dict) -> None:
    """Validate field names even when a patch deletes a nonexistent field."""
    def walk(changes, previous, schema, path=''):
        previous = previous if isinstance(previous, dict) else {}
        for key, value in changes.items():
            name = f'{path}.{key}' if path else str(key)
            rule = schema.get(key, schema.get('*'))
            if rule is None:
                if value is None and key in previous:
                    continue
                raise ValueError(f'未知配置字段：{name}')
            if isinstance(rule, dict) and isinstance(value, dict):
                walk(value, previous.get(key), rule, name)
    walk(patch, before, SCHEMA)
