"""OpenCode CLI helpers: page webfetch and shared ``opencode run`` invocation."""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import time
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
    # "" (opt-in per Job via research_mode: opencode_topic_v1) | "opencode_topic_v1"
    "pipeline_mode": "",
    "discovery_timeout_seconds": 240,
    "per_topic_timeout_seconds": 300,
    "max_concurrency": 6,
    "soft_filter_llm": True,
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
    """Resolve the OpenCode CLI path.

    Launchd / gateway processes often have a minimal ``PATH`` (``/usr/bin:/bin``),
    while interactive shells include ``~/.opencode/bin``. Accept an absolute or
    ``~``-prefixed config path, then fall back to the standard install location.
    """
    binary = str(cfg.get("binary") or "opencode").strip() or "opencode"
    which = shutil.which(binary)
    if which:
        return which
    candidates: list[str] = []
    expanded = Path(binary).expanduser()
    if expanded.is_file():
        candidates.append(str(expanded.resolve()))
    # Bare name under a starved PATH: default user install from `opencode` installer.
    if not expanded.is_absolute() or Path(binary).name == "opencode":
        home_bin = Path.home() / ".opencode" / "bin" / "opencode"
        if home_bin.is_file():
            candidates.append(str(home_bin.resolve()))
    seen: set[str] = set()
    for path in candidates:
        if path in seen:
            continue
        seen.add(path)
        target = Path(path)
        if target.is_file() and os.access(target, os.X_OK):
            return str(target)
    raise RuntimeError(f"未找到 OpenCode CLI：{binary}")


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


def parse_usage(stdout: str) -> dict:
    """Aggregate token/cost usage from ``--format json`` JSONL events when present."""
    totals = {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "reasoning_tokens": 0,
        "cache_read_tokens": 0,
        "total_tokens": 0,
        "cost_usd": 0.0,
        "opencode_steps": 0,
    }
    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        part = event.get("part") or {}
        if not isinstance(part, dict):
            continue
        tokens = part.get("tokens")
        if isinstance(tokens, dict):
            totals["opencode_steps"] += 1
            inp = int(tokens.get("input") or 0)
            out = int(tokens.get("output") or 0)
            reas = int(tokens.get("reasoning") or 0)
            cache = tokens.get("cache") if isinstance(tokens.get("cache"), dict) else {}
            cread = int(cache.get("read") or 0)
            totals["prompt_tokens"] += inp
            totals["completion_tokens"] += out
            totals["reasoning_tokens"] += reas
            totals["cache_read_tokens"] += cread
            totals["total_tokens"] += int(tokens.get("total") or (inp + out + reas + cread))
        cost = part.get("cost")
        if isinstance(cost, (int, float)) and not isinstance(cost, bool):
            totals["cost_usd"] += float(cost)
    totals["cost_usd"] = round(totals["cost_usd"], 6)
    return {k: v for k, v in totals.items() if v}


def _run_subprocess(command: list[str], *, limit: int, workdir: str | None, env: dict,
                    chosen: str, cancel_token=None) -> tuple[int, str, str]:
    """Run command; poll ``cancel_token`` via Popen so cancellation terminates active child processes."""
    if cancel_token is None:
        try:
            completed = subprocess.run(
                command, capture_output=True, text=True, timeout=limit,
                cwd=workdir, env=env, check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"OpenCode CLI 超时（{limit}s，model={chosen}）") from exc
        return completed.returncode, completed.stdout or "", completed.stderr or ""

    proc = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, cwd=workdir, env=env,
    )
    deadline = time.monotonic() + limit
    try:
        while True:
            if cancel_token.is_set():
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=2)
                raise CancelledError("OpenCode 运行已取消")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=2)
                raise RuntimeError(f"OpenCode CLI 超时（{limit}s，model={chosen}）")
            try:
                stdout, stderr = proc.communicate(timeout=min(0.5, remaining))
                return proc.returncode, stdout or "", stderr or ""
            except subprocess.TimeoutExpired:
                continue
    except BaseException:
        if proc.poll() is None:
            proc.kill()
            try:
                proc.wait(timeout=2)
            except Exception:
                pass
        raise


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
    returncode, stdout, stderr = _run_subprocess(
        command, limit=limit, workdir=workdir, env={**os.environ, "NO_COLOR": "1"},
        chosen=chosen, cancel_token=cancel_token,
    )
    if cancel_token is not None and cancel_token.is_set():
        raise CancelledError("OpenCode 运行已取消")
    if returncode != 0 and not stdout.strip():
        err = (stderr or stdout or "opencode run failed")[:300]
        raise RuntimeError(err)
    text = parse_final_text(stdout)
    usage = parse_usage(stdout)
    meta = {"model": chosen, "model_select": model_receipt, "returncode": returncode}
    if usage:
        meta["usage"] = usage
    return text, meta


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
