"""Pick an OpenCode CLI model: free first, then cheap paid fallbacks.

OpenCode Go free model ids change over time (``*-free`` / ``*:free``). This
module lists what ``opencode models`` currently advertises, probes them with a
tiny ``run``, and falls back to configured paid models when every free probe
fails.
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
import time
from pathlib import Path

from .errors import CancelledError

logger = logging.getLogger(__name__)

_UNUSABLE = re.compile(
    r"(model.?not.?found|unknown model|deprecated|HTTP\s*410|HTTP\s*404|"
    r"insufficient|quota|余额|额度|NO_MORE|not available|unavailable|"
    r"permission denied|403 Forbidden|does not exist)",
    re.I,
)

# Default paid ladder on OpenCode Go when free models are all down (cheap → stronger).
DEFAULT_PAID = (
    "opencode-go/deepseek-v4.1-flash",
    "opencode-go/glm-5.3-flash",
    "opencode-go/minimax-m3",
)


def looks_free(model_id: str) -> bool:
    name = (model_id or "").strip().lower()
    leaf = name.rsplit("/", 1)[-1]
    return leaf.endswith("-free") or leaf.endswith(":free") or "-free-" in leaf or ":free" in leaf


def list_models(binary: str) -> list[str]:
    """Return ``provider/model`` ids from ``opencode models``."""
    from .opencode_cli import subprocess_env

    resolved = binary
    completed = subprocess.run(
        [resolved, "models"],
        capture_output=True,
        text=True,
        timeout=60,
        env=subprocess_env({"allowed_mcps": []}),
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or completed.stdout or "opencode models failed")[:300])
    return [line.strip() for line in (completed.stdout or "").splitlines() if "/" in line.strip()]


def free_models_for_provider(models: list[str], provider: str) -> list[str]:
    prefix = (provider or "opencode-go").strip().rstrip("/") + "/"
    found = [m for m in models if m.startswith(prefix) and looks_free(m)]
    # Also accept zen free models when Go list is empty.
    if not found and provider.startswith("opencode"):
        found = [m for m in models if m.startswith("opencode/") and looks_free(m)]
    return found


def _parse_probe_text(stdout: str) -> str:
    texts = []
    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "text":
            part = event.get("part") or {}
            if isinstance(part.get("text"), str) and part["text"].strip():
                texts.append(part["text"].strip())
        err = event.get("error")
        if err:
            raise RuntimeError(str(err)[:300])
    return texts[-1] if texts else ""


def probe_model(binary: str, model: str, *, timeout: int = 45, cancel_token=None) -> bool:
    """True when a one-token style reply succeeds for ``model``."""
    if cancel_token is not None and cancel_token.is_set():
        raise CancelledError("OpenCode 模型探测已取消")
    command = [
        binary, "run", "--auto", "--format", "json", "--standalone",
        "--model", model,
        "Reply with exactly OK",
    ]
    from .opencode_cli import subprocess_env

    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, timeout=max(20, timeout),
            env=subprocess_env({"allowed_mcps": []}), check=False,
        )
    except subprocess.TimeoutExpired:
        logger.info("OpenCode model probe timeout: %s", model)
        return False
    blob = (completed.stdout or "") + "\n" + (completed.stderr or "")
    if _UNUSABLE.search(blob):
        logger.info("OpenCode model unusable: %s (%s)", model, _UNUSABLE.search(blob).group(0))
        return False
    try:
        text = _parse_probe_text(completed.stdout or "")
    except RuntimeError as exc:
        logger.info("OpenCode model probe error: %s (%s)", model, exc)
        return False
    ok = bool(text) and "ok" in text.lower()[:20]
    if not ok:
        logger.info("OpenCode model probe no OK: %s (%r)", model, text[:80])
    return ok


def candidate_ladder(config: dict, *, binary: str) -> list[str]:
    """Free candidates then paid fallbacks, de-duplicated."""
    provider = str(config.get("model_provider") or "opencode-go")
    configured_free = [str(m) for m in (config.get("free_models") or []) if str(m).strip()]
    paid = [str(m) for m in (config.get("paid_models") or list(DEFAULT_PAID)) if str(m).strip()]
    if configured_free:
        free = configured_free
    else:
        try:
            free = free_models_for_provider(list_models(binary), provider)
        except Exception as exc:
            logger.warning("OpenCode model list failed, using paid ladder only: %s", exc)
            free = []
    out: list[str] = []
    for model in [*free, *paid]:
        if model not in out:
            out.append(model)
    return out


def select_model(config: dict, *, binary: str, cancel_token=None,
                 cache: dict | None = None) -> tuple[str, dict]:
    """Probe free models then paid; return ``(model_id, receipt)``.

    ``cache`` may hold ``{"model", "at", "ttl"}`` from a previous successful pick
    in this process so Jobs in one batch do not re-probe every time.
    """
    ttl = int(config.get("model_cache_seconds") or 3600)
    now = time.time()
    if cache and cache.get("model") and now - float(cache.get("at") or 0) < ttl:
        return str(cache["model"]), {"model": cache["model"], "cached": True, "tried": []}

    ladder = candidate_ladder(config, binary=binary)
    if not ladder:
        raise RuntimeError("没有可用的 OpenCode 模型候选（免费与付费列表皆空）")
    probe_timeout = int(config.get("probe_timeout_seconds") or 45)
    tried = []
    for model in ladder:
        if cancel_token is not None and cancel_token.is_set():
            raise CancelledError("OpenCode 模型探测已取消")
        kind = "free" if looks_free(model) else "paid"
        ok = probe_model(binary, model, timeout=probe_timeout, cancel_token=cancel_token)
        tried.append({"model": model, "kind": kind, "ok": ok})
        if ok:
            if cache is not None:
                cache.clear()
                cache.update({"model": model, "at": now, "ttl": ttl})
            return model, {"model": model, "cached": False, "kind": kind, "tried": tried}
    detail = "; ".join(f"{r['model']}:{'ok' if r['ok'] else 'fail'}" for r in tried)
    raise RuntimeError(f"OpenCode 免费与付费候选均不可用：{detail}")
