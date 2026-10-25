"""Which model service AutoVideo's planning calls go to.

DeepSeek is the default and behaves exactly as before. Others are chosen in ``.env`` (or the process environment):

    AUTOVIDEO_LLM_PROVIDER=opencode        # deepseek (default) | opencode | opendesign | custom
    AUTOVIDEO_LLM_ENV_FILE=/path/.env      # optional: a second file to read the key from, so it is never copied

and any single setting of the chosen provider can be overridden: AUTOVIDEO_LLM_BASE_URL, _MODEL, _API_KEY_ENV,
_MAX_TOKENS, _INPUT_PER_M, _OUTPUT_PER_M (USD per million tokens), _THINKING (0/1: DeepSeek's thinking request
fields) and _JSON_MODE (0/1: ``response_format: json_object``). ``custom`` has no defaults: base URL, model and
key variable are all required. Every service must speak the OpenAI chat-completions protocol.

A settings file (``AUTOVIDEO_LLM_SETTINGS_FILE``, written by ``PUT /api/config/llm``, which AREC's settings page calls)
wins over all of that: it holds ``base_url``, ``model``, ``key_env`` (empty = a keyless local endpoint), ``env_file`` and
optionally the other settings. The service is recognised by its address (DeepSeek, Open Design, opencode) and gets that
preset's headers and request style; any other address is a plain OpenAI-compatible service.

Error codes keep the ``DEEPSEEK_`` prefix for DeepSeek (old logs and the worker know them) and use ``LLM_`` for the rest.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Mapping, Optional
from urllib.parse import urlparse

FAULTS = ("API_KEY_MISSING", "AUTH_FAILED", "RATE_LIMIT", "HTTP_ERROR", "UNAVAILABLE", "TIMEOUT", "INVALID_RESPONSE")


@dataclass(frozen=True)
class Provider:
    name: str
    label: str
    base_url: str
    model: str
    key_env: str
    thinking: bool = False
    json_mode: bool = True
    max_tokens: int = 32_768
    input_per_m: float = 0.0
    output_per_m: float = 0.0
    code_prefix: str = "LLM"
    headers: Mapping[str, str] = field(default_factory=dict)
    pricing_source: str = ""
    error: str = ""  # set when the settings cannot be used; the first call fails with it instead of the import
    env_file: str = ""  # a second file to read the key from (never copied)
    fallback_models: tuple = ()  # tried in order, same service and key, when the model answers 402 (out of balance/quota)
    source: str = "默认"  # where the choice came from: 默认 | .env | 设置页

    @property
    def needs_key(self) -> bool:
        return bool(self.key_env)

    def request_headers(self, session_id: str) -> dict[str, str]:
        """The extra headers, with ``{session_id}`` filled in (one run is one session)."""
        return {k: v.replace("{session_id}", session_id) for k, v in self.headers.items()}

    @property
    def url(self) -> str:
        return self.base_url.rstrip("/") + "/chat/completions"

    def code(self, fault: str) -> str:
        return f"{self.code_prefix}_{fault}"

    @property
    def fault_codes(self) -> set[str]:
        return {self.code(f) for f in FAULTS}


PRESETS: dict[str, Provider] = {
    "deepseek": Provider(
        name="deepseek", label="DeepSeek API", base_url="https://api.deepseek.com", model="deepseek-flash",
        key_env="DEEPSEEK_API_KEY", thinking=True, json_mode=True, max_tokens=100_000,
        input_per_m=0.30, output_per_m=1.20, code_prefix="DEEPSEEK",
        pricing_source="https://api-docs.deepseek.com/quick_start/pricing/"),
    "opencode": Provider(
        name="opencode", label="opencode Go", base_url="https://opencode.ai/zen/go/v1", model="minimax-m3.1-flash-free",
        key_env="OPENCODE_API_KEY", thinking=False, json_mode=True, max_tokens=32_768,
        headers={"User-Agent": "AutoVideo/1.0", "x-opencode-session": "ses_{session_id}"}),  # opencode refuses requests without a session
    # Billed to the Open Design account, so the prices are DeepSeek's: a conservative cap for the per-video budget check.
    "opendesign": Provider(
        name="opendesign", label="Open Design（amr-link）", base_url="https://amr-link.open-design.ai/v1",
        model="deepseek-v4-flash", key_env="OPENDESIGN_API_KEY", thinking=True, json_mode=True, max_tokens=100_000,
        input_per_m=0.30, output_per_m=1.20, headers={"User-Agent": "AutoVideo/1.0"}),
}
#: Every provider's codes, for the places that must tell a service failure from a bad plan.
ALL_FAULT_CODES = {c for p in (*PRESETS.values(), Provider("x", "x", "", "", "")) for c in p.fault_codes}


def read_env_file(path: Optional[Path]) -> dict[str, str]:
    values: dict[str, str] = {}
    if path is not None and path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            name, sep, raw = line.partition("=")
            if sep and name.strip() and not name.strip().startswith("#"):
                values[name.strip()] = raw.strip().strip("\"'")
    return values


def _flag(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _host(url: str) -> str:
    return urlparse(url if "//" in url else "//" + url).hostname or ""


def _models(value) -> tuple:
    """A list (or comma-separated text) of model ids, without blanks or repeats."""
    items = value.split(",") if isinstance(value, str) else (value or [])
    return tuple(dict.fromkeys(str(m).strip() for m in items if str(m).strip()))


def preset_for(base_url: str) -> Provider:
    """The preset that serves ``base_url``, or a plain OpenAI-compatible service (conservative price cap, no thinking)."""
    host = _host(base_url)
    for preset in PRESETS.values():
        if _host(preset.base_url) == host:
            return preset
    return Provider(name="custom", label=host or "自定义服务", base_url=base_url, model="", key_env="",
                    input_per_m=0.30, output_per_m=1.20)


def read_settings(path: Optional[str]) -> dict:
    if not path:
        return {}
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def load(env_path: Optional[Path] = None, environ: Optional[Mapping[str, str]] = None) -> Provider:
    """The chosen provider with its overrides applied. Never raises: bad settings come back in ``error``."""
    environ = os.environ if environ is None else environ
    dotenv = read_env_file(env_path)
    settings = {**dotenv, **{k: v for k, v in environ.items() if k.startswith("AUTOVIDEO_LLM_")}}
    saved = read_settings(environ.get("AUTOVIDEO_LLM_SETTINGS_FILE") or dotenv.get("AUTOVIDEO_LLM_SETTINGS_FILE"))
    source = "默认"
    if saved.get("base_url"):
        base = preset_for(str(saved["base_url"]))
        provider = replace(base, base_url=str(saved["base_url"]).strip())
        source = "设置页"
    else:
        name = (settings.get("AUTOVIDEO_LLM_PROVIDER") or "deepseek").strip().lower()
        if name == "custom":
            base = PRESETS["opencode"]
            provider = Provider(name="custom", label="自定义 OpenAI 兼容服务", base_url="", model="", key_env="",
                                thinking=False, json_mode=base.json_mode, max_tokens=base.max_tokens)
        elif name in PRESETS:
            provider = PRESETS[name]
        else:
            return replace(PRESETS["deepseek"], error=f"AUTOVIDEO_LLM_PROVIDER={name} 不认识；可选 {', '.join([*PRESETS, 'custom'])}")
        if any(k.startswith("AUTOVIDEO_LLM_") and k != "AUTOVIDEO_LLM_SETTINGS_FILE" for k in settings):
            source = ".env"
    changes: dict = {}
    try:
        for var, attr, cast in (("BASE_URL", "base_url", str), ("MODEL", "model", str), ("API_KEY_ENV", "key_env", str),
                                ("MAX_TOKENS", "max_tokens", int), ("INPUT_PER_M", "input_per_m", float),
                                ("OUTPUT_PER_M", "output_per_m", float), ("THINKING", "thinking", _flag),
                                ("FALLBACK_MODELS", "fallback_models", _models),
                                ("JSON_MODE", "json_mode", _flag)):
            raw = (settings.get(f"AUTOVIDEO_LLM_{var}") or "").strip()
            if raw:
                changes[attr] = cast(raw)
        for attr, cast in (("model", str), ("key_env", str), ("fallback_models", _models), ("max_tokens", int), ("input_per_m", float),
                           ("output_per_m", float), ("thinking", bool), ("json_mode", bool)):
            if attr in saved and (attr in ("key_env", "fallback_models") or saved[attr] not in (None, "")):
                changes[attr] = cast(saved[attr])
    except (ValueError, TypeError) as exc:
        return replace(provider, error=f"LLM 设置里的数值写错了：{exc}")
    env_file = str(saved.get("env_file") or settings.get("AUTOVIDEO_LLM_ENV_FILE") or "").strip()
    provider = replace(provider, source=source, env_file=env_file, **changes)
    provider = replace(provider, fallback_models=tuple(m for m in provider.fallback_models if m != provider.model))
    missing = [label for label, value in (("AUTOVIDEO_LLM_BASE_URL", provider.base_url), ("AUTOVIDEO_LLM_MODEL", provider.model))
               if not value]
    if provider.name == "custom" and not saved.get("base_url") and not provider.key_env:
        missing.append("AUTOVIDEO_LLM_API_KEY_ENV")
    if missing:
        provider = replace(provider, error="自定义服务还缺：" + "、".join(missing))
    return provider


def api_key(provider: Provider, env_path: Optional[Path] = None, environ: Optional[Mapping[str, str]] = None) -> str:
    """The provider's key from the environment, then ``.env``, then the provider's second env file.

    Empty when there is none (or the provider needs no key). The value is never logged or written anywhere."""
    if not provider.key_env:
        return ""
    environ = os.environ if environ is None else environ
    value = (environ.get(provider.key_env) or "").strip()
    if value:
        return value
    dotenv = read_env_file(env_path)
    value = dotenv.get(provider.key_env, "")
    other = provider.env_file or environ.get("AUTOVIDEO_LLM_ENV_FILE") or dotenv.get("AUTOVIDEO_LLM_ENV_FILE") or ""
    if not value and other.strip():
        value = read_env_file(Path(other.strip()).expanduser()).get(provider.key_env, "")
    return value


def ping(provider: Provider, key: str, timeout: float = 30.0) -> dict:
    """One tiny request to see whether the service, the model and the key work: ``{"ok", "status", "message"}``.

    The message is the service's own error text (never the key or the request)."""
    if provider.error:
        return {"ok": False, "status": None, "message": provider.error}
    if provider.needs_key and not key:
        return {"ok": False, "status": None, "message": f"没有找到密钥 {provider.key_env}"}
    body = {"model": provider.model, "messages": [{"role": "user", "content": "只回复 OK"}], "max_tokens": 30, "stream": False}
    headers = {"Content-Type": "application/json", **provider.request_headers("autovideo-ping")}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    request = urllib.request.Request(provider.url, data=json.dumps(body).encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout):
            return {"ok": True, "status": 200, "message": "可用"}
    except urllib.error.HTTPError as exc:
        try:
            text = str((json.loads(exc.read()).get("error") or {}).get("message") or "")
        except (ValueError, AttributeError):
            text = ""
        return {"ok": False, "status": exc.code, "message": text[:200] or f"HTTP {exc.code}"}
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {"ok": False, "status": None, "message": f"连不上：{type(exc).__name__}"}


def validate_settings(raw: dict) -> dict:
    """The settings ``PUT /api/config/llm`` may save; raises ``ValueError`` with a sentence for the user."""
    base_url = str(raw.get("base_url") or "").strip()
    if not base_url.lower().startswith(("http://", "https://")):
        raise ValueError("接口地址要以 http:// 或 https:// 开头")
    model = str(raw.get("model") or "").strip()
    if not model:
        raise ValueError("模型 ID 不能为空")
    key_env = str(raw.get("key_env") or "").strip()
    if key_env and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key_env):
        raise ValueError("密钥环境变量只填变量名，不要填密钥本身")
    env_file = str(raw.get("env_file") or "").strip()
    if env_file and not Path(env_file).expanduser().is_file():
        raise ValueError("找不到存放密钥的文件")
    fallbacks = [m for m in _models(raw.get("fallback_models")) if m != model]
    out = {"base_url": base_url, "model": model, "key_env": key_env, "env_file": env_file, "fallback_models": fallbacks}
    for attr, cast in (("max_tokens", int), ("input_per_m", float), ("output_per_m", float)):
        if raw.get(attr) not in (None, ""):
            try:
                out[attr] = cast(raw[attr])
            except (TypeError, ValueError):
                raise ValueError(f"{attr} 要填数字") from None
    for attr in ("thinking", "json_mode"):
        if raw.get(attr) is not None:
            out[attr] = bool(raw[attr])
    return out
