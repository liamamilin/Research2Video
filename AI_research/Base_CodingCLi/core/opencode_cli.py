"""OpenCode CLI helpers: page webfetch and shared ``opencode run`` invocation."""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import signal
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
        for key in ("disabled_mcps", "allowed_mcps"):
            if isinstance(section.get(key), list):
                value[key] = [str(x) for x in section[key]]
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
        for key in ("disabled_mcps", "allowed_mcps"):
            if isinstance(section.get(key), list):
                value[key] = [str(x) for x in section[key]]
    # Reuse binary/workdir from page_readers when gather omits them.
    page = settings(config_dir)
    if not value.get("binary"):
        value["binary"] = page.get("binary") or "opencode"
    if not value.get("workdir") and page.get("workdir"):
        value["workdir"] = page["workdir"]
    for key in ("disabled_mcps", "allowed_mcps"):
        if key not in value and key in page:
            value[key] = list(page[key])
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
    tool_calls: dict[str, int] = {}
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
        if event.get("type") == "tool_use" and isinstance(part.get("tool"), str):
            tool_calls[part["tool"]] = tool_calls.get(part["tool"], 0) + 1
    totals["cost_usd"] = round(totals["cost_usd"], 6)
    usage = {k: v for k, v in totals.items() if v}
    if tool_calls:
        usage["tool_calls"] = tool_calls
    return usage


def _terminate_process_group(proc: subprocess.Popen, *, pgid: int | None = None,
                             grace_seconds: float = 2.0) -> None:
    """Terminate ``proc`` and any descendants in its process group (TERM → KILL).

    OpenCode runs with ``start_new_session=True`` so spawned MCP servers
    (e.g. ``browse``) share ``pgid`` and are reaped together on timeout/cancel.
    """
    target_pgid = pgid if pgid is not None else proc.pid
    use_pg = hasattr(os, "killpg") and target_pgid is not None and target_pgid > 0

    def _send(sig: int) -> None:
        if use_pg:
            try:
                os.killpg(target_pgid, sig)
                return
            except ProcessLookupError:
                return
            except PermissionError:
                pass
        if proc.poll() is None:
            try:
                proc.send_signal(sig)
            except ProcessLookupError:
                pass

    _send(signal.SIGTERM)
    if proc.poll() is None:
        try:
            proc.wait(timeout=max(0.1, grace_seconds))
        except subprocess.TimeoutExpired:
            _send(signal.SIGKILL)
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
    if use_pg:
        # Even after parent exits, grandchildren in the process group may linger.
        deadline = time.monotonic() + min(0.5, max(0.0, grace_seconds))
        while time.monotonic() < deadline:
            try:
                os.killpg(target_pgid, 0)
            except ProcessLookupError:
                return
            except PermissionError:
                return
            time.sleep(0.05)
        _send(signal.SIGKILL)


def _run_subprocess(command: list[str], *, limit: int, workdir: str | None, env: dict,
                    chosen: str, cancel_token=None) -> tuple[int, str, str]:
    """Run command in its own process group so timeout/cancel reaps child processes too."""
    popen_kwargs: dict = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "cwd": workdir,
        "env": env,
    }
    if os.name != "nt":
        popen_kwargs["start_new_session"] = True
    proc = subprocess.Popen(command, **popen_kwargs)
    pgid = proc.pid
    deadline = time.monotonic() + limit
    try:
        if cancel_token is None:
            try:
                stdout, stderr = proc.communicate(timeout=limit)
                return proc.returncode, stdout or "", stderr or ""
            except subprocess.TimeoutExpired as exc:
                _terminate_process_group(proc, pgid=pgid)
                raise RuntimeError(f"OpenCode CLI 超时（{limit}s，model={chosen}）") from exc

        while True:
            if cancel_token.is_set():
                _terminate_process_group(proc, pgid=pgid)
                raise CancelledError("OpenCode 运行已取消")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _terminate_process_group(proc, pgid=pgid)
                raise RuntimeError(f"OpenCode CLI 超时（{limit}s，model={chosen}）")
            try:
                stdout, stderr = proc.communicate(timeout=min(0.5, remaining))
                return proc.returncode, stdout or "", stderr or ""
            except subprocess.TimeoutExpired:
                continue
    except BaseException:
        if proc.poll() is None:
            _terminate_process_group(proc, pgid=pgid, grace_seconds=0.5)
        raise


# User-global MCP servers that stay available to research runs. ``browse`` is kept
# for pages webfetch cannot read and for screenshots / page visuals; information
# search itself goes through websearch (see TOOL_POLICY). Every other user MCP
# (blender, godot-ai, ...) is disabled: unrelated to research and slow to connect.
_DEFAULT_ALLOWED_MCPS = ("browse",)
_JSONC_LINE_COMMENT = re.compile(r'(?m)(^|\s)//[^\n]*')


def _user_opencode_config_dir(env: dict[str, str], *, use_home_fallback: bool) -> Path | None:
    xdg = (env.get("XDG_CONFIG_HOME") or "").strip()
    if xdg:
        return Path(xdg) / "opencode"
    home = (env.get("HOME") or "").strip()
    if home:
        return Path(home) / ".config" / "opencode"
    if use_home_fallback:
        return Path.home() / ".config" / "opencode"
    return None


def _discover_user_mcp_names(env: dict[str, str], *, use_home_fallback: bool) -> list[str]:
    """Read user-global OpenCode config files and return configured MCP server names.

    Names not in the allowed list (``blender``, ``godot-ai``, ...) get disabled for
    research runs, avoiding 40-55s MCP connect timeouts on unrelated desktop tools.
    """
    cfg_dir = _user_opencode_config_dir(env, use_home_fallback=use_home_fallback)
    if cfg_dir is None:
        return []
    names: list[str] = []
    for filename in ("opencode.json", "opencode.jsonc"):
        path = cfg_dir / filename
        try:
            if not path.is_file():
                continue
            raw = path.read_text(encoding="utf-8")
        except OSError:
            continue
        parsed = None
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            if filename.endswith(".jsonc"):
                try:
                    parsed = json.loads(_JSONC_LINE_COMMENT.sub(r"\1", raw))
                except json.JSONDecodeError:
                    parsed = None
        if not isinstance(parsed, dict):
            continue
        mcp = parsed.get("mcp")
        if not isinstance(mcp, dict):
            continue
        servers = mcp.get("servers")
        if isinstance(servers, dict):
            for name in servers:
                if isinstance(name, str) and name.strip() and name.strip() not in names:
                    names.append(name.strip())
        for name, val in mcp.items():
            if name == "servers" or not isinstance(val, dict):
                continue
            if isinstance(name, str) and name.strip() and name.strip() not in names:
                names.append(name.strip())
    return names


def _disabled_mcp_entry(existing: object) -> dict:
    """Return an OpenCode v2 schema-valid disabled ``mcp.servers.<name>`` entry.

    OpenCode v2 validates each config document before merging and skips entries
    missing ``type``/``command`` (e.g. a bare ``{"disabled": True}``), which
    would silently leave global MCP servers enabled.
    """
    entry = dict(existing) if isinstance(existing, dict) else {}
    entry["disabled"] = True
    if not entry.get("type"):
        entry["type"] = "local"
    if entry.get("type") == "local" and not entry.get("command"):
        entry["command"] = ["true"]
    elif entry.get("type") == "remote" and not entry.get("url"):
        entry["url"] = "http://127.0.0.1:0"
    return entry


def subprocess_env(cfg: dict | None = None, *, base_env: dict[str, str] | None = None) -> dict[str, str]:
    """Build subprocess env for unattended OpenCode CLI runs.

    Disables user-global MCP servers outside ``allowed_mcps`` (default: only
    ``browse`` stays) via ``OPENCODE_CONFIG_CONTENT`` using schema-valid
    OpenCode v2 entries. ``disabled_mcps`` (explicit list) overrides discovery.
    """
    env = dict(base_env if base_env is not None else os.environ)
    env["NO_COLOR"] = "1"
    raw = (cfg or {}).get("disabled_mcps")
    explicit = isinstance(raw, (list, tuple))
    if explicit:
        disabled = [str(x).strip() for x in raw if str(x).strip()]
    else:
        allowed_raw = (cfg or {}).get("allowed_mcps")
        allowed = ({str(x).strip() for x in allowed_raw} if isinstance(allowed_raw, (list, tuple))
                   else set(_DEFAULT_ALLOWED_MCPS))
        disabled = [name for name in _discover_user_mcp_names(env, use_home_fallback=(base_env is None))
                    if name not in allowed]
    if not disabled:
        return env
    override: dict = {}
    existing = env.get("OPENCODE_CONFIG_CONTENT", "").strip()
    if existing:
        try:
            parsed = json.loads(existing)
            if isinstance(parsed, dict):
                override = parsed
        except json.JSONDecodeError:
            override = {}
    mcp = override.get("mcp")
    if not isinstance(mcp, dict):
        mcp = {}
        override["mcp"] = mcp
    servers = mcp.get("servers")
    if not isinstance(servers, dict):
        servers = {}
        mcp["servers"] = servers
    for name in disabled:
        entry = servers.get(name)
        # Keep an explicit caller opt-in (disabled: False) for auto-discovered MCPs.
        if not explicit and isinstance(entry, dict) and entry.get("disabled") is False:
            continue
        servers[name] = _disabled_mcp_entry(entry)
    env["OPENCODE_CONFIG_CONTENT"] = json.dumps(override, ensure_ascii=False)
    return env


# Appended to every research ``run`` prompt: search through search tools, browser
# only for what search/webfetch cannot do.
TOOL_POLICY = (
    "\n\n## 工具使用规则\n"
    "- 查找信息（找新闻、资料、链接、数字、说法出处）一律用 websearch；读取已知网址用 webfetch。"
    "不要用浏览器打开搜索引擎、站内搜索页或信息流去搜。\n"
    "- 浏览器（browse 工具）只用于：webfetch 读不到正文、需要执行脚本才显示内容的已知网址；"
    "或任务明确需要网页截图、查看页面画面与布局。\n"
    "- 使用浏览器时只操作自己新开的标签页，用完关闭；不要动已有标签页，不要登录、下单或提交表单。\n"
)


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
        prompt + TOOL_POLICY,
    ]
    workdir = str(cfg.get("workdir") or "").strip() or None
    logger.info("OpenCode CLI run model=%s timeout=%s", chosen, limit)
    returncode, stdout, stderr = _run_subprocess(
        command, limit=limit, workdir=workdir, env=subprocess_env(cfg),
        chosen=chosen, cancel_token=cancel_token,
    )
    if cancel_token is not None and cancel_token.is_set():
        raise CancelledError("OpenCode 运行已取消")
    if returncode != 0 and not stdout.strip():
        err = (stderr or stdout or "opencode run failed")[:300]
        raise RuntimeError(err)
    text = parse_final_text(stdout)
    usage = parse_usage(stdout)
    logger.info(
        "OpenCode CLI done model=%s rc=%s steps=%s tools=%s",
        chosen, returncode, usage.get("opencode_steps", 0), usage.get("tool_calls") or {},
    )
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
            # Thin webfetch reader: no MCP needed, skip connecting any.
            env=subprocess_env({**cfg, "allowed_mcps": []}), check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"OpenCode CLI 读页超时（{timeout}s）") from exc
    if cancel_token is not None and cancel_token.is_set():
        raise CancelledError("OpenCode 读页已取消")
    if completed.returncode != 0 and not (completed.stdout or "").strip():
        raise RuntimeError((completed.stderr or completed.stdout or "opencode run failed")[:300])
    return _parse_run_jsonl(completed.stdout or "")
