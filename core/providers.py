"""LLM provider presets and env-name validation for the config form.

A *provider* is the bundle of three values that have to agree for a hosted
endpoint to answer: the ``base_url``, the env var that holds the key, and the
headers the gateway expects. The form offered them as three independent text
boxes, so pointing the page at Open Design while the key in ``.env`` belonged
to opencode produced an ``invalid_api_key`` 401 whose real cause -- the endpoint
and the credential are from different vendors -- was nowhere in the message.

The presets below are suggestions, not a whitelist: the form still accepts any
OpenAI-compatible URL, and ``is_valid_env_name``/``looks_like_secret`` exist so
that a key pasted into the *env var name* field is caught as the misconfiguration
it is instead of silently sending an unauthenticated request.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

# A shell environment-variable name: a letter or underscore, then word chars.
# Deliberately permissive about case because real configs use both LLM_API_KEY
# and lowercase names, but a pasted key ("sk-amr-...", "sk_...") always has a
# character this rejects.
_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Vendor key prefixes. Used together with the env-name check so a value that
# happens to be identifier-shaped but is obviously a key is still caught.
_SECRET_PREFIXES = ("sk-", "sk_", "gg-", "tvly-", "xai-", "gsk_", "AIza")


def is_valid_env_name(value: str) -> bool:
    """True when ``value`` looks like an environment-variable *name*."""
    return bool(_ENV_NAME_RE.match((value or "").strip()))


def looks_like_secret(value: str) -> bool:
    """True when a value was almost certainly pasted from a key field.

    An env-name field holds a name such as ``LLM_API_KEY``; anything that is not
    a valid name, or that carries a known vendor prefix, is a key that landed in
    the wrong box.
    """
    text = (value or "").strip()
    if not text:
        return False
    if not is_valid_env_name(text):
        return True
    return text.lower().startswith(_SECRET_PREFIXES)


@dataclass(frozen=True)
class ProviderPreset:
    """One selectable endpoint in the LLM section of the config form."""

    id: str
    label: str
    base_url: str
    # Env var this provider's key should live in. Empty for keyless endpoints.
    # Distinct per vendor (not a shared "LLM_API_KEY") so switching providers
    # never overwrites a key you may want to switch back to later: both keys
    # sit in .env at once, and only the active one is read.
    api_key_env: str = "LLM_API_KEY"
    # Headers the gateway requires. ``{session_id}`` is filled per conversation.
    extra_headers: dict = field(default_factory=dict)
    # Model ids we know the endpoint serves. A *hint* for the model field, not a
    # validation list: any OpenAI-compatible id the provider accepts will work.
    models: list = field(default_factory=list)
    # Which of those ids cost nothing (a curated hint, not fetched live --
    # the endpoints have no pricing API). Put the free ones in ai.model /
    # review_ai.model and the rest in fallback_models so a quota-exhausted
    # free model fails over to a paid one instead of failing the job.
    free_models: list = field(default_factory=list)
    note: str = ""

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "base_url": self.base_url,
            "api_key_env": self.api_key_env,
            "extra_headers": dict(self.extra_headers),
            "models": list(self.models),
            "free_models": list(self.free_models),
            "note": self.note,
        }


# Ordered as they appear in the dropdown. The first entries are the ones this
# project is actually deployed against.
PROVIDERS: tuple[ProviderPreset, ...] = (
    ProviderPreset(
        id="open-design",
        label="Open Design（amr-link）",
        base_url="https://amr-link.open-design.ai/v1",
        api_key_env="OPENDESIGN_API_KEY",
        models=["deepseek-v4.1-flash", "deepseek-v4-flash", "deepseek-v4-pro", "glm-5.3-flash", "claude-haiku-5.5"],
        note="OpenAI 兼容。密钥以 sk-amr- 开头，填在下方密钥卡片，不要填进“密钥环境变量”。",
    ),
    ProviderPreset(
        id="opencode-go",
        label="opencode Go（订阅）",
        base_url="https://opencode.ai/zen/go/v1",
        api_key_env="OPENCODE_API_KEY",
        extra_headers={
            "x-opencode-session": "ses_{session_id}",
            "User-Agent": "BaseCodingCLi-ResearchAgent/2.0",
        },
        models=["minimax-m3.1-flash-free", "beam-free", "minimax-m3", "deepseek-v4.1-flash",
                "deepseek-v4-pro", "glm-5.3-flash", "claude-haiku-5-5"],
        free_models=["minimax-m3.1-flash-free", "beam-free"],
        note="需要有效的 OpenCode Go 订阅；否则返回 403。免费模型名带 -free 后缀，会不定期变化。",
    ),
    ProviderPreset(
        id="opencode-zen",
        label="opencode zen（按量计费）",
        base_url="https://opencode.ai/zen/v1",
        api_key_env="OPENCODE_API_KEY",
        extra_headers={"User-Agent": "BaseCodingCLi-ResearchAgent/2.0"},
        models=["deepseek-v4.1-flash-free", "minimax-m3.1-flash-free", "glm-5.3-flash-free", "mimo-v2.6-flash-free",
                "deepseek-v4.1-flash", "minimax-m3", "gpt-5.5"],
        free_models=["deepseek-v4.1-flash-free", "minimax-m3.1-flash-free", "glm-5.3-flash-free", "mimo-v2.6-flash-free"],
        note="按量付费，需要账户余额。",
    ),
    ProviderPreset(
        id="deepseek",
        label="DeepSeek 官方",
        base_url="https://api.deepseek.com/v1",
        api_key_env="DEEPSEEK_API_KEY",
        models=["deepseek-chat", "deepseek-reasoner"],
        note="DeepSeek 官方 OpenAI 兼容接口。",
    ),
    ProviderPreset(
        id="openai",
        label="OpenAI",
        base_url="https://api.openai.com/v1",
        api_key_env="OPENAI_API_KEY",
        models=["gpt-5.4"],
        note="官方 OpenAI 接口。",
    ),
    ProviderPreset(
        id="ollama",
        label="本地 Ollama（无需密钥）",
        base_url="http://localhost:11434/v1",
        api_key_env="",
        models=["qwen3.8:27b-mlx", "bge-m3:latest"],
        free_models=["qwen3.8:27b-mlx", "bge-m3:latest"],
        note="本地端点，密钥环境变量留空。",
    ),
)

_BY_ID = {p.id: p for p in PROVIDERS}


def list_providers() -> list[dict]:
    """All presets as plain dicts, in dropdown order."""
    return [p.as_dict() for p in PROVIDERS]


def get_provider(provider_id: str) -> Optional[ProviderPreset]:
    return _BY_ID.get((provider_id or "").strip())


def find_provider_by_base_url(base_url: str) -> Optional[ProviderPreset]:
    """Match a configured ``base_url`` back to a preset, if any.

    Trailing slashes and case are ignored: the stored value is trimmed by
    ``LLMConfig.from_system`` and an admin may have typed a different case.
    """
    normalized = (base_url or "").strip().rstrip("/").lower()
    if not normalized:
        return None
    for preset in PROVIDERS:
        if preset.base_url.rstrip("/").lower() == normalized:
            return preset
    return None
