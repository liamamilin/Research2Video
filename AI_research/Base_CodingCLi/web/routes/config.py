"""System config routes: read/write system.yaml (admin only, secrets masked)."""

from __future__ import annotations

import os
import copy
import hashlib
import logging
import math
import tempfile
import time
from io import StringIO
from pathlib import Path
from typing import Optional
from functools import lru_cache

from fastapi import APIRouter, Depends, HTTPException, Request, status

from core.config import load_system_config
from core.llm import LLMClient, LLMConfig
from core.providers import get_provider, list_providers, looks_like_secret
from core.search import SearchClient, SearchConfig
from web import audit
from web.deps import require_admin
from web.models import ApiError
from web.services.yaml_io import parse_yaml, atomic_write, save_backup

router = APIRouter(prefix="/api/config", tags=["config"])
logger = logging.getLogger(__name__)


@router.get('/production-policy')
def get_production_policy(user=Depends(require_admin)):
    from web.settings import get_settings
    from web.services.production_policy import describe
    return describe(get_settings().paths)


@router.put('/production-policy')
def update_production_policy(payload: dict, user=Depends(require_admin)):
    from web.settings import get_settings
    from web.services.production_policy import save
    from core.schedule_store import ScheduleConflict
    try:
        return save(get_settings().paths, payload['policy'], payload['expected_revision'])
    except ScheduleConflict as exc:
        raise HTTPException(409, detail=str(exc)) from exc
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(422, detail=str(exc)) from exc


@lru_cache(maxsize=8)
def _parsed_system_text(content: str) -> dict:
    return parse_yaml(content)


def system_config_lock():
    """One lock for settings-page edits, assistant saves and direct rollback."""
    from core.fileio import file_lock
    from web.settings import get_settings
    return file_lock(os.path.realpath(os.path.join(get_settings().paths.config_dir, 'system.yaml')))


def content_version(content: str) -> str:
    return hashlib.sha256(content.encode('utf-8')).hexdigest()

_ENV_PATH_OVERRIDE: Optional[str] = None


def _env_path() -> str:
    if _ENV_PATH_OVERRIDE:
        return _ENV_PATH_OVERRIDE
    return str(Path(__file__).resolve().parents[2] / ".env")


def set_env_path(path: Optional[str]) -> None:
    global _ENV_PATH_OVERRIDE
    _ENV_PATH_OVERRIDE = path


def _load_system() -> dict:
    from web.settings import get_settings
    return load_system_config(get_settings().paths.config_dir)


def _merged_system(overrides: Optional[dict] = None) -> dict:
    """Load system.yaml and deep-merge unsaved form overrides."""
    sys_config = _load_system()
    if overrides:
        _apply_patch(sys_config, overrides)
    return sys_config


def _secret_env_map() -> dict[str, str]:
    """Env var name → human label.

    Always includes a slot for every known LLM provider preset's key
    (core.providers.PROVIDERS), not only the ones ai/review_ai currently
    reference. Found live: switching ai from provider A to provider B made
    A's key slot disappear from the settings page the moment B became
    active, which read as "the key got lost" even though .env still held it
    untouched -- A's slot is just as real when it is not the active one, so
    it stays listed, and a key for a provider you have not switched to yet
    can be saved here in advance. An active ai/review_ai section's label
    wins when its var is also a preset's.
    """
    sys_config = _load_system()
    ai = sys_config.get("ai") or {}
    search = sys_config.get("search") or {}
    extraction = sys_config.get("extraction") or {}

    review_ai = sys_config.get("review_ai") or {}

    mapping: dict[str, str] = {}
    roles: dict[str, list[str]] = {}  # who uses a var right now (several features may share one provider's key)
    for section, role in ((ai, "AI 模型（LLM）"), (review_ai, "审校模型（review_ai）"), (extraction, "报告清理")):
        if section.get("api_key_env"):
            roles.setdefault(str(section["api_key_env"]), []).append(role)
    if ai.get("api_key_env"):
        mapping[str(ai["api_key_env"])] = "AI 模型（LLM）"
    if review_ai.get("api_key_env"):
        mapping.setdefault(str(review_ai["api_key_env"]), "审校模型（review_ai）")
    if ai.get("embedding_api_key_env"):
        mapping[str(ai["embedding_api_key_env"])] = "Embedding 模型"
    if search.get("api_key_env"):
        mapping.setdefault(str(search["api_key_env"]), "搜索服务")
    for provider, env_name in (search.get("api_key_envs") or {}).items():
        if env_name:
            mapping[str(env_name)] = f"搜索 · {provider}"
    if extraction.get("api_key_env"):
        mapping.setdefault(str(extraction["api_key_env"]), "报告清理（可选）")

    by_env: dict[str, list[str]] = {}
    for preset in list_providers():
        if preset["api_key_env"]:
            by_env.setdefault(preset["api_key_env"], []).append(preset["label"])
    for env_name, labels in by_env.items():
        provider = "服务商 · " + "、".join(dict.fromkeys(labels))
        role = "、".join(r for r in dict.fromkeys(roles.get(env_name, [])))
        # The slot is named after whose key it is, never after the role using it right now: labelled "AI 模型（LLM）"
        # it read as the AI's key whichever provider was active, and a key for another provider got pasted into it
        # (then switching provider found the new one's slot empty and the old one's slot holding the wrong key).
        mapping[env_name] = f"{provider} · 当前用于 {role}" if role else provider
    return mapping


def _mask(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 4:
        return "****"
    return value[:4] + "****" + value[-2:]


def _write_env_value(env_name: str, value: Optional[str]) -> None:
    """Set or remove one key in the .env file (atomic, 0600)."""
    path = _env_path()
    lines: list[str] = []
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()

    out: list[str] = []
    replaced = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            out.append(line)
            continue
        key = stripped.split("=", 1)[0].strip() if "=" in stripped else ""
        if key == env_name:
            replaced = True
            if value is not None:
                out.append(f'{env_name}="{value}"')
            continue
        out.append(line)

    if not replaced and value is not None:
        out.append(f'{env_name}="{value}"')

    content = "\n".join(out).rstrip() + "\n"
    # A private temp name, not `path + ".tmp"`: two concurrent saves shared one
    # fixed name, so one truncated the other's write and the first rename left
    # the second failing on a path the caller never named. Same reasoning as
    # `yaml_io.atomic_write`.
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".",
                               prefix=os.path.basename(path) + ".tmp.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except OSError:
        try:
            if os.path.isfile(tmp):
                os.remove(tmp)
        except OSError:
            pass
        raise

    if value is not None:
        os.environ[env_name] = value
    else:
        os.environ.pop(env_name, None)


def _reject_secret_in_env_field(patch: dict) -> None:
    """Refuse a patch that puts an API key into an ``*_api_key_env`` field.

    Such a value is silently useless -- ``os.environ.get(key)`` returns "" and
    the request goes out unauthenticated -- so it must not reach disk where it
    is mistaken for a configured key.
    """
    def walk(node: dict, path: str) -> None:
        for key, value in node.items():
            if not isinstance(value, dict):
                is_env_field = key == "api_key_env" or key.endswith("_api_key_env")
                if is_env_field and isinstance(value, str) and looks_like_secret(value):
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                        detail=ApiError.make(
                            "secret_in_env_field",
                            f"{path}{key} 看起来填的是 API Key，而不是环境变量名。"
                            f"请在“API 密钥”卡片填写密钥，这里只填变量名"
                            f"（如 LLM_API_KEY）。",
                        ),
                    )
            else:
                walk(value, f"{path}{key}.")

    walk(patch, "")


def _apply_patch(target: dict, patch: dict) -> None:
    """Deep-merge a patch into a ruamel document; None deletes a key."""
    from ruamel.yaml.comments import CommentedMap

    for key, value in patch.items():
        if value is None:
            if key in target:
                del target[key]
        elif isinstance(value, dict):
            node = target.get(key)
            if not isinstance(node, dict):
                node = CommentedMap()
                target[key] = node
            _apply_patch(node, value)
        else:
            target[key] = value


def _merge_patch(path: str, patch: dict) -> str:
    """Merge a field patch into system.yaml, preserving comments/formatting."""
    from ruamel.yaml import YAML
    from ruamel.yaml.comments import CommentedMap

    yaml = YAML()
    yaml.preserve_quotes = True
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.load(f) or CommentedMap()
    else:
        data = CommentedMap()
    _apply_patch(data, patch)
    buf = StringIO()
    yaml.dump(data, buf)
    return buf.getvalue()


def _mask_secrets(data: dict, parent_key: str = "") -> dict:
    """Recursively mask secret values in a config dict."""
    masked: dict = {}
    for key, value in data.items():
        full_key = f"{parent_key}.{key}" if parent_key else key
        # Check if this key or any parent key contains secret field names
        is_secret = any(s in full_key.lower() for s in ["secret", "api_key", "password", "token"])
        if is_secret and isinstance(value, str) and len(value) > 4:
            masked[key] = value[:4] + "****"
        elif isinstance(value, dict):
            masked[key] = _mask_secrets(value, full_key)
        elif isinstance(value, list):
            masked[key] = [
                _mask_secrets(v, full_key) if isinstance(v, dict) else v
                for v in value
            ]
        else:
            masked[key] = value
    return masked


@router.get("/providers")
def get_providers(user=Depends(require_admin)):
    """LLM endpoint presets for the config form's provider dropdown.

    Returns suggestions only; the form still accepts any OpenAI-compatible
    ``base_url``. Lets the UI fill base_url / api_key_env / required headers
    together so the three cannot be set from different vendors by accident.
    """
    return {"providers": list_providers()}


@router.get("/providers/{provider_id}/models")
def get_provider_models(provider_id: str, refresh: bool = False, user=Depends(require_admin)):
    """The model ids a provider preset serves right now (its ``/models`` list), so the form can warn about an id the
    provider does not have instead of the first request failing with 404.

    Only a preset's own address receives its saved key. Lists and public official prices are cached for one hour;
    ``refresh`` bypasses the cache. Upstream failure returns an error and any previous successful snapshot as stale.
    """
    from web.services.provider_catalog import get_catalog
    from web.settings import get_settings

    preset = get_provider(provider_id)
    if preset is None:
        raise HTTPException(status_code=404, detail=ApiError.make("not_found", "没有这个服务商"))
    return get_catalog(preset, get_settings().paths.state_dir, refresh=refresh)


@router.get("/system")
def get_system_config(user=Depends(require_admin)):
    """Read system.yaml, with secrets masked in the structured view.

    ``data`` is the parsed config with secret-looking values replaced, for the
    form. ``content`` is the raw file text, unmasked, because the UI has a YAML
    editor and round-trips what it was given; the PUT accepts the same text
    back. So the masking is there to keep the *form* readable, not to keep
    secrets off the wire -- ``content`` is the admin's own editable copy of a
    file they can already read, and this endpoint is admin-only.
    """
    with system_config_lock():
        return _get_system_config_locked()


def _get_system_config_locked():
    """Read content and version from the same locked snapshot."""
    from web.settings import get_settings
    settings = get_settings()
    path = os.path.join(settings.paths.config_dir, "system.yaml")

    if not os.path.isfile(path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=ApiError.make("not_found", "system.yaml 不存在"),
        )

    with open(path, "r", encoding="utf-8") as f:
        raw = f.read()
    mtime = os.path.getmtime(path)

    parsed = copy.deepcopy(_parsed_system_text(raw)) if len(raw) <= 128_000 else parse_yaml(raw)
    masked = _mask_secrets(parsed)

    return {
        "version": content_version(raw),
        "content": raw,
        "parsed": parsed,
        "masked": masked,
        "mtime": mtime,
        "path": path,
    }


@router.put("/system")
def update_system_config(
    payload: dict,
    request: Request,
    user=Depends(require_admin),
):
    """Write system.yaml under the shared compare-and-publish lock."""
    with system_config_lock():
        return _update_system_config_locked(payload, request, user)


def _update_system_config_locked(payload: dict, request: Request, user, *, trusted_restore: bool = False):
    """Write with system_config_lock already held by the caller."""
    from web.settings import get_settings
    settings = get_settings()
    path = os.path.join(settings.paths.config_dir, "system.yaml")

    old_content = Path(path).read_text(encoding='utf-8') if os.path.isfile(path) else ''
    expected_version = payload.get('expected_version')
    if expected_version is not None and expected_version != content_version(old_content):
        raise HTTPException(409, detail=ApiError.make('conflict', 'system.yaml 已被修改，请重新读取后保存'))
    patch = payload.get("patch")
    if patch is not None:
        if not isinstance(patch, dict):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=ApiError.make("invalid_patch", "patch 必须为对象"),
            )
        _reject_secret_in_env_field(patch)
        try:
            from core.system_schema import validate_system_patch
            validate_system_patch(patch, parse_yaml(old_content) if old_content.strip() else {})
            new_content = _merge_patch(path, patch)
        except Exception as e:  # noqa: BLE001 - reported to the admin
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=ApiError.make("merge_failed", f"合并配置失败: {e}"),
            )
    else:
        new_content = payload.get("content", "")

    if not new_content.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=ApiError.make("empty_content", "内容不能为空"),
        )

    # Conflict detection: refuse to overwrite edits made elsewhere.
    #
    # A client that omits expected_mtime is choosing not to check, and that is
    # allowed. A client that *sends* one and gets it wrong is not: `float()`
    # raised, the bare `except` swallowed it, and the check was skipped while
    # the write went ahead anyway -- so a typo in a client turned the guard off
    # silently and the concurrent edit it exists to prevent was lost. An
    # unparseable value is now an error rather than a disabled check.
    expected_mtime = payload.get("expected_mtime")
    if expected_mtime is not None and os.path.isfile(path):
        try:
            expected = float(expected_mtime)
            if not math.isfinite(expected):
                raise ValueError('non-finite version')
        except (TypeError, ValueError):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=ApiError.make(
                    "invalid_mtime",
                    f"expected_mtime 不是有效时间戳: {expected_mtime!r}",
                ),
            ) from None
        current_mtime = os.path.getmtime(path)
        if current_mtime != expected:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=ApiError.make(
                    "conflict",
                    "system.yaml 已被外部修改，刷新会丢弃未保存的修改，请先复制再刷新重试",
                    details={"current_mtime": current_mtime},
                ),
            )

    # Validate YAML
    try:
        parsed = parse_yaml(new_content)
        from core.system_schema import validate_system_change
        if not trusted_restore:
            validate_system_change(parse_yaml(old_content) if old_content.strip() else {}, parsed)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=ApiError.make("yaml_invalid", str(e)),
        )

    # Read old for backup
    if os.path.isfile(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                old_content = f.read()
            if old_content != new_content:
                save_backup(path, old_content)
        except OSError:
            pass

    atomic_write(path, new_content)
    warnings = []
    try:
        from core import search_health
        from web.indexer import vector_sync
        vector_sync.reset_client()
        search_health.clear()  # a changed provider or plan deserves a fresh try, not the old cooldown
        audit.log('system_config_update', user=user['username'], result='success',
                  ip=request.client.host if request.client else None)
    except Exception:
        logger.exception('Configuration saved; post-save bookkeeping failed')
        warnings.append('配置已保存，缓存或审计收尾未完成，请核验后续请求状态')
    return {"ok": True, "mtime": os.path.getmtime(path), 'version': content_version(new_content), 'warnings': warnings}


@router.get("/secrets")
def get_secrets(user=Depends(require_admin)):
    """List the API-key env vars referenced by the config (values masked)."""
    items = []
    for env_name, label in _secret_env_map().items():
        value = os.environ.get(env_name, "")
        items.append({
            "env": env_name,
            "label": label,
            "configured": bool(value),
            "masked": _mask(value),
        })
    return {"env_file": _env_path(), "secrets": items}


@router.put("/secrets")
def update_secrets(payload: dict, request: Request, user=Depends(require_admin)):
    """Set or clear API keys in .env. Keys are restricted to configured env names."""
    values = payload.get("values")
    if not isinstance(values, dict):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=ApiError.make("invalid_values", "values 必须为对象"),
        )

    allowed = _secret_env_map()
    applied: dict[str, bool] = {}
    for env_name, value in values.items():
        if env_name not in allowed:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=ApiError.make("unknown_secret", f"未在配置中引用的环境变量: {env_name}"),
            )
        if value is not None and not isinstance(value, str):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=ApiError.make("invalid_value", f"{env_name} 必须为字符串或 null"),
            )
        if isinstance(value, str) and ("\n" in value or "\r" in value):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=ApiError.make("invalid_value", "密钥不能包含换行"),
            )
        _write_env_value(env_name, value or None)
        applied[env_name] = value is not None

    from core import search_health
    search_health.clear()  # a new or topped-up key is tried at once rather than after its cooldown
    audit.log(
        "config_secrets_update",
        user=user["username"],
        result="success",
        details={"keys": sorted(applied), "cleared": [k for k, v in applied.items() if not v]},
        ip=request.client.host if request.client else None,
    )

    items = []
    for env_name, label in allowed.items():
        value = os.environ.get(env_name, "")
        items.append({
            "env": env_name,
            "label": label,
            "configured": bool(value),
            "masked": _mask(value),
        })
    return {"ok": True, "secrets": items}


# ---------------------------------------------------------------------------
# Connectivity tests (each tests ONE thing, against unsaved form values)
# ---------------------------------------------------------------------------


@router.post("/test-llm")
def test_llm(payload: dict, user=Depends(require_admin)):
    """Send a tiny chat completion with the (possibly unsaved) LLM config.

    ``section`` picks ``ai`` (default) or ``review_ai``, so the audit model
    can be tested the same way as the main one.
    """
    section = (payload or {}).get("section") or "ai"
    if section not in ("ai", "review_ai"):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=ApiError.make("invalid_section", f"未知配置分区：{section}"))
    overrides = (payload or {}).get("config") or {}
    api_key = (payload or {}).get("api_key")
    sys_config = _merged_system(overrides)
    llm_cfg = LLMConfig.from_system(sys_config, section)
    if api_key:
        llm_cfg.api_key = api_key
    llm_cfg.max_tokens = 16
    llm_cfg.timeout = min(llm_cfg.timeout, 60)
    llm_cfg.request_timeout = 60
    llm_cfg.max_retries = 0
    # the configured model alone: with fallbacks a dead primary (402 "Endpoint is unavailable") is answered by the next
    # one and the test reads "可用" while every real request pays for a failed first try
    llm_cfg.fallback_models = []

    started = time.time()
    try:
        client = LLMClient(llm_cfg)
        try:
            resp = client.chat(
                [{"role": "user", "content": "Reply with: ok"}],
                max_tokens=16,
                temperature=0.0,
            )
        finally:
            client.close()
        return {
            "ok": True,
            "model": llm_cfg.model,
            "base_url": llm_cfg.base_url,
            "reply": (resp.content or "")[:80],
            "latency_ms": int((time.time() - started) * 1000),
        }
    except Exception as exc:  # noqa: BLE001 - reported to the admin
        return {
            "ok": False,
            "model": llm_cfg.model,
            "base_url": llm_cfg.base_url,
            "error": str(exc)[:300],
            "latency_ms": int((time.time() - started) * 1000),
        }


@router.post("/test-search")
def test_search(payload: dict, user=Depends(require_admin)):
    """Run one small search with the (possibly unsaved) search config."""
    overrides = (payload or {}).get("config") or {}
    api_key = (payload or {}).get("api_key")
    sys_config = _merged_system(overrides)
    search_cfg = SearchConfig.from_system(sys_config)
    if api_key:
        search_cfg.api_key = api_key
        search_cfg.api_keys[search_cfg.provider] = api_key
    search_cfg.max_results = 1
    search_cfg.max_chars_per_result = 200
    search_cfg.timeout = min(search_cfg.timeout, 30)
    # the chosen provider alone: with a fallback a provider out of quota is answered by DuckDuckGo and the test reads
    # "ok" while every real search pays for the failed first try
    search_cfg.fallback_provider = ""
    search_cfg.fallback_providers = []

    started = time.time()
    try:
        client = SearchClient(search_cfg)
        hits = client.search(["connectivity test"], objective="connectivity check")
        result = {
            "ok": bool(hits),
            "provider": search_cfg.provider,
            "results": len(hits),
            "latency_ms": int((time.time() - started) * 1000),
        }
        if hits:
            result["sample_url"] = hits[0].url
        if not hits:
            result["error"] = getattr(client, "errors", {}).get(search_cfg.provider) or "无结果返回（检查服务商与密钥）"
        return result
    except Exception as exc:  # noqa: BLE001 - reported to the admin
        reasons = getattr(exc, "reasons", None)
        return {
            "ok": False,
            "provider": search_cfg.provider,
            "error": (reasons or {}).get(search_cfg.provider) or str(exc)[:300],
            "latency_ms": int((time.time() - started) * 1000),
        }


@router.post("/test-embedding")
def test_embedding(payload: dict, user=Depends(require_admin)):
    """Embed a probe string with the (possibly unsaved) embedding config."""
    overrides = (payload or {}).get("config") or {}
    api_key = (payload or {}).get("api_key")
    sys_config = _merged_system(overrides)

    started = time.time()
    try:
        from core.embeddings import EmbeddingClient

        client = EmbeddingClient.from_system(sys_config)
        if api_key:
            client.api_key = api_key
        vector = client.embed(["ping"])[0]
        return {
            "ok": True,
            "model": client.model,
            "base_url": client.base_url,
            "dims": len(vector),
            "latency_ms": int((time.time() - started) * 1000),
        }
    except Exception as exc:  # noqa: BLE001 - reported to the admin
        return {
            "ok": False,
            "error": str(exc)[:300],
            "latency_ms": int((time.time() - started) * 1000),
        }


@router.post("/test-connection")
def test_connection(user=Depends(require_admin)):
    """Probe the configured LLM endpoint and search provider.

    Makes one tiny LLM call and one small search request. Admin only.
    """
    from core.config import load_system_config
    from core.llm import LLMClient, LLMConfig
    from core.search import SearchClient, SearchConfig
    from web.settings import get_settings

    settings = get_settings()
    sys_cfg = load_system_config(settings.paths.config_dir)
    result: dict = {}

    # --- LLM ---
    started = time.time()
    llm_cfg = LLMConfig.from_system(sys_cfg, "ai")
    try:
        llm_cfg.max_tokens = 16
        llm_cfg.timeout = 60
        llm_cfg.request_timeout = 60
        llm_cfg.max_retries = 0
        client = LLMClient(llm_cfg)
        try:
            resp = client.chat(
                [{"role": "user", "content": "Reply with: ok"}],
                max_tokens=16,
                temperature=0.0,
            )
        finally:
            client.close()
        result["llm"] = {
            "ok": True,
            "model": llm_cfg.model,
            "base_url": llm_cfg.base_url,
            "reply": (resp.content or "")[:60],
            "latency_ms": int((time.time() - started) * 1000),
        }
    except Exception as exc:  # noqa: BLE001 - reported to the admin
        result["llm"] = {
            "ok": False,
            "model": llm_cfg.model,
            "base_url": llm_cfg.base_url,
            "error": str(exc)[:300],
            "latency_ms": int((time.time() - started) * 1000),
        }

    # --- Search ---
    started = time.time()
    search_cfg = SearchConfig.from_system(sys_cfg)
    search_cfg.max_results = 1
    search_cfg.max_chars_per_result = 200
    search_cfg.timeout = 30
    try:
        client = SearchClient(search_cfg)
        hits = client.search(
            ["connectivity test"], objective="connectivity check"
        )
        result["search"] = {
            "ok": bool(hits),
            "provider": search_cfg.provider,
            "results": len(hits),
            "latency_ms": int((time.time() - started) * 1000),
        }
        if hits:
            result["search"]["sample_url"] = hits[0].url
    except Exception as exc:  # noqa: BLE001 - reported to the admin
        result["search"] = {
            "ok": False,
            "provider": search_cfg.provider,
            "error": str(exc)[:300],
            "latency_ms": int((time.time() - started) * 1000),
        }

    return result
