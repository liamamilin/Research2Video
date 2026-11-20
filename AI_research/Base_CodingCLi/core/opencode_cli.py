"""OpenCode CLI helpers: page webfetch and shared ``opencode run`` invocation."""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path

from .errors import CancelledError

logger = logging.getLogger(__name__)

# Legacy page-reader defaults (still under page_readers.opencode_cli).
DEFAULTS = {
    "enabled": True,
    "binary": "opencode",
    "timeout_seconds": 90,
    "workdir": "",
}

_GATHER_DEFAULTS = {
    "enabled": True,
    "binary": "opencode",
    "workdir": "",
    "model_provider": "opencode-go",
    "free_models": [],
    "paid_models": [
        "opencode-go/deepseek-v4.1-flash",
        "opencode-go/glm-5.3-flash",
        "opencode-go/minimax-m3",
    ],
    "probe_timeout_seconds": 45,
    "gather_timeout_seconds": 300,
    "model_cache_seconds": 3600,
    # all | fetch_only | flagged — default: every Job gets the gather pack
    "apply_to": "all",
}

_FAIL = re.compile(r"(请求失败|HTTP\s*403|HTTP\s*401|HTTP\s*429|未能获取|access denied|verify you are human)", re.I)
_MODEL_CACHE: dict = {}


def _load_system(config_dir: str | None = None) -> dict:
    from .yaml_cache import load_yaml

    roots = []
    if config_dir:
        roots.append(Path(config_dir) / "system.yaml")
    roots.append(Path("config/system.yaml"))
    roots.append(Path(__file__).resolve().parents[1] / "config" / "system.yaml")
    for path in roots:
        if path.is_file():
            with path.open() as handle:
                return load_yaml(handle) or {}
    return {}


def settings(config_dir: str | None = None) -> dict:
    """Merge ``page_readers.opencode_cli`` from system.yaml with defaults."""
    value = dict(DEFAULTS)
    section = ((_load_system(config_dir).get("page_readers") or {}).get("opencode_cli") or {})
    if isinstance(section, dict):
        value.update({k: section[k] for k in DEFAULTS if k in section})
    return value


def gather_settings(config_dir: str | None = None) -> dict:
    """Merge ``gather.opencode_cli`` for the gather-and-organize worker."""
    value = dict(_GATHER_DEFAULTS)
    section = ((_load_system(config_dir).get("gather") or {}).get("opencode_cli") or {})
    if isinstance(section, dict):
        for key, default in _GATHER_DEFAULTS.items():
            if key not in section:
                continue
            if isinstance(default, list):
                value[key] = [str(x) for x in section[key]] if isinstance(section[key], list) else list(default)
            else:
                value[key] = section[key]
    # Reuse binary/workdir from page_readers when gather omits them.
    page = settings(config_dir)
    if not value.get("binary"):
        value["binary"] = page.get("binary") or "opencode"
    if not value.get("workdir") and page.get("workdir"):
        value["workdir"] = page["workdir"]
    return value


def resolve_binary(cfg: dict) -> str:
    binary = str(cfg.get("binary") or "opencode")
    resolved = shutil.which(binary) or str(Path(binary).expanduser())
    if not (shutil.which(binary) or Path(resolved).is_file()):
        raise RuntimeError(f"未找到 OpenCode CLI：{binary}")
    return shutil.which(binary) or resolved


def available(config: dict | None = None) -> bool:
    cfg = config or settings()
    if not cfg.get("enabled", True):
        return False
    try:
        resolve_binary(cfg)
        return True
    except RuntimeError:
        return False


def _parse_run_jsonl(stdout: str) -> str:
    """Prefer completed ``webfetch`` tool output; fall back to the last text part."""
    tool_text = ""
    final_text = ""
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        part = event.get("part") or {}
        if event.get("type") == "tool_use" and part.get("tool") == "webfetch":
            state = part.get("state") or {}
            if state.get("status") == "completed" and isinstance(state.get("output"), str):
                tool_text = state["output"]
            elif state.get("status") == "error":
                err = state.get("error") or state.get("output") or "webfetch failed"
                raise RuntimeError(str(err)[:300])
        if event.get("type") == "text" and isinstance(part.get("text"), str) and part["text"].strip():
            final_text = part["text"]
    body = (tool_text or final_text).strip()
    if not body:
        raise RuntimeError("OpenCode CLI 未返回网页正文")
    if _FAIL.search(body) and len(body) < 800:
        raise RuntimeError(body[:300])
    return body


def parse_final_text(stdout: str) -> str:
    """Last assistant text part from ``--format json`` output."""
    final = ""
    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        part = event.get("part") or {}
        if event.get("type") == "text" and isinstance(part.get("text"), str) and part["text"].strip():
            final = part["text"].strip()
        if event.get("error"):
            raise RuntimeError(str(event["error"])[:300])
    if not final:
        raise RuntimeError("OpenCode CLI 未返回文本")
    return final


def run(prompt: str, *, config: dict | None = None, model: str | None = None,
        timeout: int | None = None, cancel_token=None) -> tuple[str, dict]:
    """Run ``opencode run`` and return ``(final_text, meta)``."""
    cfg = config or gather_settings()
    if cancel_token is not None and cancel_token.is_set():
        raise CancelledError("OpenCode 运行已取消")
    binary = resolve_binary(cfg)
    chosen = model
    model_receipt: dict = {}
    if not chosen:
        from .opencode_models import select_model
        chosen, model_receipt = select_model(cfg, binary=binary, cancel_token=cancel_token, cache=_MODEL_CACHE)
    limit = max(30, int(timeout or cfg.get("gather_timeout_seconds") or cfg.get("timeout_seconds") or 300))
    command = [
        binary, "run", "--auto", "--format", "json", "--standalone",
        "--model", chosen,
        prompt,
    ]
    workdir = str(cfg.get("workdir") or "").strip() or None
    logger.info("OpenCode CLI run model=%s timeout=%s", chosen, limit)
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=limit,
            cwd=workdir,
            env={**os.environ, "NO_COLOR": "1"},
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"OpenCode CLI 超时（{limit}s，model={chosen}）") from exc
    if cancel_token is not None and cancel_token.is_set():
        raise CancelledError("OpenCode 运行已取消")
    if completed.returncode != 0 and not (completed.stdout or "").strip():
        err = (completed.stderr or completed.stdout or "opencode run failed")[:300]
        raise RuntimeError(err)
    text = parse_final_text(completed.stdout or "")
    return text, {"model": chosen, "model_select": model_receipt, "returncode": completed.returncode}


def webfetch_markdown(url: str, *, config: dict | None = None, cancel_token=None) -> str:
    """Run ``opencode run`` once and return markdown/text from ``webfetch``."""
    cfg = config or settings()
    if not cfg.get("enabled", True):
        raise RuntimeError("OpenCode CLI 读页已关闭")
    if cancel_token is not None and cancel_token.is_set():
        raise CancelledError("OpenCode 读页已取消")
    from .opencode_models import select_model

    gather_cfg = {**gather_settings(), **{k: cfg[k] for k in ("binary", "workdir") if cfg.get(k)}}
    binary = resolve_binary(gather_cfg)
    model, _ = select_model(gather_cfg, binary=binary, cancel_token=cancel_token, cache=_MODEL_CACHE)
    timeout = max(15, int(cfg.get("timeout_seconds") or DEFAULTS["timeout_seconds"]))
    # Thin reader for the page-fallback chain only. Job research tasks use
    # ``opencode_gather`` (keywords + requirements + free tool use).
    prompt = (
        "这是读页回退任务：用 webfetch 读取下面 URL，format=markdown，"
        "返回页面正文即可（可保留原标题）。不要搜索，不要写成研究报告。"
        f"\nURL: {url}"
    )
    command = [
        binary, "run", "--auto", "--format", "json", "--standalone",
        "--model", model, prompt,
    ]
    workdir = str(cfg.get("workdir") or "").strip() or None
    logger.info("OpenCode CLI webfetch: %s model=%s", url, model)
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, timeout=timeout, cwd=workdir,
            env={**os.environ, "NO_COLOR": "1"}, check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"OpenCode CLI 读页超时（{timeout}s）") from exc
    if cancel_token is not None and cancel_token.is_set():
        raise CancelledError("OpenCode 读页已取消")
    if completed.returncode != 0 and not (completed.stdout or "").strip():
        raise RuntimeError((completed.stderr or completed.stdout or "opencode run failed")[:300])
    return _parse_run_jsonl(completed.stdout or "")
