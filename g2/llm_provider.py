"""Which model service AutoVideo's planning calls go to.

DeepSeek is the default and behaves exactly as before. Others are chosen in ``.env`` (or the process environment):

    AUTOVIDEO_LLM_PROVIDER=opencode        # deepseek (default) | opencode | opendesign | custom
    AUTOVIDEO_LLM_ENV_FILE=/path/.env      # optional: a second file to read the key from, so it is never copied

and any single setting of the chosen provider can be overridden: AUTOVIDEO_LLM_BASE_URL, _MODEL, _API_KEY_ENV,
_MAX_TOKENS, _INPUT_PER_M, _OUTPUT_PER_M (USD per million tokens), _THINKING (0/1: DeepSeek's thinking request
fields) and _JSON_MODE (0/1: ``response_format: json_object``). ``custom`` has no defaults: base URL, model and
key variable are all required. Every service must speak the OpenAI chat-completions protocol.

Error codes keep the ``DEEPSEEK_`` prefix for DeepSeek (old logs and the worker know them) and use ``LLM_`` for the rest.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Mapping, Optional

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


def load(env_path: Optional[Path] = None, environ: Optional[Mapping[str, str]] = None) -> Provider:
    """The chosen provider with its overrides applied. Never raises: bad settings come back in ``error``."""
    environ = os.environ if environ is None else environ
    settings = {**read_env_file(env_path), **{k: v for k, v in environ.items() if k.startswith("AUTOVIDEO_LLM_")}}
    name = (settings.get("AUTOVIDEO_LLM_PROVIDER") or "deepseek").strip().lower()
    if name == "custom":
        base = PRESETS["opencode"]
        provider = Provider(name="custom", label="自定义 OpenAI 兼容服务", base_url="", model="", key_env="",
                            thinking=False, json_mode=base.json_mode, max_tokens=base.max_tokens)
    elif name in PRESETS:
        provider = PRESETS[name]
    else:
        return replace(PRESETS["deepseek"], error=f"AUTOVIDEO_LLM_PROVIDER={name} 不认识；可选 {', '.join([*PRESETS, 'custom'])}")
    changes: dict = {}
    try:
        for var, attr, cast in (("BASE_URL", "base_url", str), ("MODEL", "model", str), ("API_KEY_ENV", "key_env", str),
                                ("MAX_TOKENS", "max_tokens", int), ("INPUT_PER_M", "input_per_m", float),
                                ("OUTPUT_PER_M", "output_per_m", float), ("THINKING", "thinking", _flag),
                                ("JSON_MODE", "json_mode", _flag)):
            raw = (settings.get(f"AUTOVIDEO_LLM_{var}") or "").strip()
            if raw:
                changes[attr] = cast(raw)
    except ValueError as exc:
        return replace(provider, error=f"AUTOVIDEO_LLM_* 的数值写错了：{exc}")
    provider = replace(provider, **changes)
    missing = [label for label, value in (("AUTOVIDEO_LLM_BASE_URL", provider.base_url), ("AUTOVIDEO_LLM_MODEL", provider.model),
                                          ("AUTOVIDEO_LLM_API_KEY_ENV", provider.key_env)) if not value]
    if missing:
        provider = replace(provider, error="自定义服务还缺：" + "、".join(missing))
    return provider


def api_key(provider: Provider, env_path: Optional[Path] = None, environ: Optional[Mapping[str, str]] = None) -> str:
    """The provider's key from the environment, then ``.env``, then the file named by AUTOVIDEO_LLM_ENV_FILE.

    Empty when there is none. The value is never logged or written anywhere."""
    environ = os.environ if environ is None else environ
    value = (environ.get(provider.key_env) or "").strip()
    if value:
        return value
    values = read_env_file(env_path)
    value = values.get(provider.key_env, "")
    if not value:
        extra = (environ.get("AUTOVIDEO_LLM_ENV_FILE") or values.get("AUTOVIDEO_LLM_ENV_FILE") or "").strip()
        if extra:
            value = read_env_file(Path(extra).expanduser()).get(provider.key_env, "")
    return value
