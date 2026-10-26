"""Keep credential values out of model context and durable chat records."""
from __future__ import annotations
import os
import re
from urllib.parse import urlsplit, urlunsplit


def secret_key(key: str) -> bool:
    name = key.lower()
    if name in ('cookie', 'cookies', 'credentials', 'set-cookie'):
        return True
    if name.endswith(('_tokens', '_token_minutes', '_token_days')) or name.startswith('tokens_') or name in ('tokens', 'token_id', 'token_count', 'token_name'):
        return False
    return (not name.endswith(('_env', '_envs')) and
            any(word in name for word in ('password', 'secret', 'api_key', 'token', 'authorization', 'sendkey', 'webhook_url')))


def safe_url(value: str) -> str:
    try:
        url = urlsplit(value)
        if url.scheme in ('http', 'https') and url.netloc:
            return urlunsplit((url.scheme, url.hostname + (f':{url.port}' if url.port else ''), url.path, '', ''))
    except ValueError:
        pass
    return value


def redact(value):
    # A platform overview can contain thousands of text fields. Snapshot the
    # configured values once per traversal; a later call still sees env edits.
    secrets = tuple(secret for key, secret in os.environ.items()
                    if secret_key(key) and len(secret) >= 6)
    return _redact(value, secrets)


def _redact(value, secrets: tuple[str, ...]):
    if isinstance(value, dict):
        return {k: ('[已配置]' if v else '[未配置]') if secret_key(k) else
                ({str(h): '[已配置]' for h in v} if k == 'extra_headers' and isinstance(v, dict) else _redact(v, secrets))
                for k, v in value.items()}
    if isinstance(value, list):
        return [_redact(v, secrets) for v in value]
    if isinstance(value, str):
        # Exact configured env values also cover a credential echoed inside a
        # provider error or a log line, rather than only a dict's key name.
        for secret in secrets:
            value = value.replace(secret, '[已隐藏]')
        value = re.sub(r'(?i)(bearer\s+)[A-Za-z0-9_.~-]+', r'\1[已隐藏]', value)
        value = re.sub(r'(?i)(\b(?:token|password|secret|api[_-]?key)\s+)[A-Za-z0-9_./+=~-]{16,}', r'\1[已隐藏]', value)
        value = re.sub(r'(?i)(api[_-]?key|password|secret|token)([\"\x27]?\s*[:=]\s*[\"\x27]?)[^\s,;\"\x27}]+', r'\1\2[已隐藏]', value)
        return value
    return value


def validate_patch(patch: dict):
    allowed = {'ai', 'search', 'qa', 'research', 'budget', 'notifications', 'extraction', 'defaults', 'logging', 'cleanup'}
    if not patch or set(patch) - allowed:
        raise ValueError('仅支持系统配置的已知业务分区')
    if any(not isinstance(section, dict) for section in patch.values()):
        raise ValueError('配置分区必须是对象；删除单个字段可将该字段设为 null')

    def walk(data):
        if isinstance(data, dict):
            for key, value in data.items():
                if not isinstance(key, str) or secret_key(key) or key == 'extra_headers':
                    raise ValueError('凭据与请求头请通过受保护输入或设置页修改')
                if key.endswith(('_env', '_envs')):
                    values = value.values() if isinstance(value, dict) else [value]
                    if any(v is not None and (not isinstance(v, str) or (v and not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', v))) for v in values):
                        raise ValueError('环境变量字段必须是环境变量名')
                walk(value)
        elif isinstance(data, list):
            for value in data:
                walk(value)
        elif isinstance(data, str) and data.startswith(('http://', 'https://')) and safe_url(data) != data:
            raise ValueError('URL 不能包含凭据、查询密钥或片段；请使用环境变量配置敏感地址')
    walk(patch)
