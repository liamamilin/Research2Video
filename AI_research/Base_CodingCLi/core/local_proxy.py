"""Ensure outbound fetches can use a local Clash/mihomo HTTP proxy.

Goal: read overseas sources even when the Clash Verge GUI is closed.

Order:
1. Reuse an already-working proxy env, or Clash Verge's mixed-port if up.
2. Otherwise start a headless ``verge-mihomo`` with a patched copy of the
   local Clash Verge config (TUN off, dedicated sidecar port), then inject
   ``HTTP_PROXY`` for this process. Does not open the Clash Verge app.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import logging
import os
import socket
import subprocess
import time
from typing import Any, Callable, Mapping, MutableMapping, Optional
from urllib.parse import urlparse

import yaml

logger = logging.getLogger(__name__)

_PROXY_ENV_KEYS = (
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY",
    "http_proxy", "https_proxy", "all_proxy",
)
_DEFAULT_NO_PROXY = "localhost,127.0.0.1,::1"
_PROBE_TIMEOUT = 0.4
_DEFAULT_CORE = Path("/Applications/Clash Verge.app/Contents/MacOS/verge-mihomo")
_DEFAULT_SUPPORT = Path.home() / "Library/Application Support/io.github.clash-verge-rev.clash-verge-rev"
_DEFAULT_CONFIG = _DEFAULT_SUPPORT / "clash-verge.yaml"
_DEFAULT_SIDECAR_PORT = 17897
# Keep sidecar log handles alive so the child process is not left writing to a
# closed fd after this function returns.
_OPEN_LOGS: list[Any] = []


@dataclass(frozen=True)
class ProxyStatus:
    """Outcome of ``ensure_local_proxy`` for logging and callers."""

    ready: bool
    url: str
    source: str  # disabled | env | listening | sidecar | started | missing
    message: str
    launched: bool = False


class ProxyUnavailableError(RuntimeError):
    """Raised when ``required`` is true and no local proxy became ready."""


def proxy_config(system_cfg: Mapping[str, Any] | None) -> dict[str, Any]:
    """Normalize ``network.proxy`` from system.yaml with defaults."""
    section = ((system_cfg or {}).get("network") or {}).get("proxy") or {}
    port = _as_int(section.get("port"), 7897)
    sidecar_port = _as_int(section.get("sidecar_port"), _DEFAULT_SIDECAR_PORT)
    timeout = section.get("startup_timeout_seconds", 45)
    try:
        timeout = float(timeout)
    except (TypeError, ValueError):
        timeout = 45.0
    # auto_start defaults true; explicit false disables headless core launch.
    if "auto_start" in section:
        auto_start = bool(section.get("auto_start"))
    else:
        auto_start = True
    core = str(section.get("core_path") or "").strip() or str(_DEFAULT_CORE)
    config = str(section.get("config_path") or "").strip() or str(_DEFAULT_CONFIG)
    data_dir = str(section.get("data_dir") or "").strip() or str(_DEFAULT_SUPPORT)
    work_dir = str(section.get("work_dir") or "state/local_proxy").strip() or "state/local_proxy"
    return {
        "enabled": bool(section.get("enabled", True)),
        "host": str(section.get("host") or "127.0.0.1").strip() or "127.0.0.1",
        "port": port,
        "sidecar_port": sidecar_port,
        "auto_start": auto_start,
        "core_path": core,
        "config_path": config,
        "data_dir": data_dir,
        "work_dir": work_dir,
        "startup_timeout_seconds": max(0.0, timeout),
        "required": bool(section.get("required", True)),
        "no_proxy": str(section.get("no_proxy") or _DEFAULT_NO_PROXY).strip() or _DEFAULT_NO_PROXY,
    }


def _as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def proxy_url(host: str, port: int) -> str:
    return f"http://{host}:{port}"


def probe(host: str, port: int, *, timeout: float = _PROBE_TIMEOUT) -> bool:
    """True when TCP connect to the mixed-port succeeds."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _env_proxy_url(environ: Mapping[str, str] | None = None) -> str:
    env = os.environ if environ is None else environ
    for key in _PROXY_ENV_KEYS:
        value = (env.get(key) or "").strip()
        if value:
            return value
    return ""


def _host_port_from_proxy_url(url: str) -> tuple[str, int] | None:
    raw = url.strip()
    if not raw:
        return None
    if "://" not in raw:
        raw = "http://" + raw
    parsed = urlparse(raw)
    host = parsed.hostname
    if not host:
        return None
    port = parsed.port
    if port is None:
        port = 443 if parsed.scheme == "https" else 80
    return host, port


def env_proxy_ready(environ: Mapping[str, str] | None = None,
                    *, probe_fn=probe) -> tuple[bool, str]:
    """Reuse an existing proxy env when its host:port accepts connections."""
    url = _env_proxy_url(environ)
    if not url:
        return False, ""
    parsed = _host_port_from_proxy_url(url)
    if parsed is None:
        return False, url
    host, port = parsed
    if probe_fn(host, port):
        return True, url
    return False, url


def apply_env(url: str, no_proxy: str = _DEFAULT_NO_PROXY,
              environ: MutableMapping[str, str] | None = None) -> None:
    """Point urllib/httpx at ``url`` for this process."""
    env = os.environ if environ is None else environ
    for key in _PROXY_ENV_KEYS:
        env[key] = url
    env["NO_PROXY"] = no_proxy
    env["no_proxy"] = no_proxy


def write_sidecar_config(source: Path, dest: Path, *, mixed_port: int) -> Path:
    """Copy Clash Verge runtime YAML into a headless-safe sidecar config.

    Disables TUN (needs the Verge service / root) and points mixed-port at the
    dedicated sidecar port so we do not fight a later Clash Verge launch on 7897.
    """
    data = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ProxyUnavailableError(f"Clash 配置不是对象：{source}")
    data["mixed-port"] = int(mixed_port)
    data["allow-lan"] = False
    tun = data.get("tun")
    if isinstance(tun, dict):
        tun = dict(tun)
        tun["enable"] = False
        data["tun"] = tun
    else:
        data["tun"] = {"enable": False}
    # fake-ip is for TUN; plain HTTP proxy needs real hostnames or dial fails DNS.
    dns = data.get("dns")
    if isinstance(dns, dict):
        dns = dict(dns)
        dns["enhanced-mode"] = "redir-host"
        data["dns"] = dns
    data.pop("external-controller-unix", None)
    data["external-controller"] = ""
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return dest


def start_sidecar_core(cfg: Mapping[str, Any], *,
                       popen: Callable[..., Any] = subprocess.Popen) -> subprocess.Popen:
    """Start verge-mihomo with the patched sidecar config; leave it running."""
    core = Path(cfg["core_path"])
    source = Path(cfg["config_path"]).expanduser()
    data_dir = Path(cfg["data_dir"]).expanduser()
    work = Path(cfg["work_dir"]).expanduser()
    if not core.is_file():
        raise ProxyUnavailableError(f"找不到 mihomo 内核：{core}")
    if not source.is_file():
        raise ProxyUnavailableError(f"找不到 Clash Verge 配置：{source}")
    if not data_dir.is_dir():
        raise ProxyUnavailableError(f"找不到 Clash Verge 数据目录：{data_dir}")

    sidecar_yaml = write_sidecar_config(
        source, work / "mihomo.yaml", mixed_port=int(cfg["sidecar_port"]))
    work.mkdir(parents=True, exist_ok=True)
    log_path = work / "mihomo.log"
    pid_path = work / "mihomo.pid"
    log_fh = open(log_path, "ab", buffering=0)
    _OPEN_LOGS.append(log_fh)
    try:
        proc = popen(
            [str(core), "-d", str(data_dir), "-f", str(sidecar_yaml)],
            stdin=subprocess.DEVNULL,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    except OSError as exc:
        _OPEN_LOGS.remove(log_fh)
        log_fh.close()
        raise ProxyUnavailableError(f"启动 mihomo 失败：{exc}") from exc
    try:
        pid_path.write_text(str(proc.pid), encoding="utf-8")
    except OSError:
        pass
    return proc


def wait_until_listening(host: str, port: int, timeout_seconds: float,
                         *, probe_fn=probe, sleep=time.sleep,
                         clock=time.monotonic) -> bool:
    deadline = clock() + max(0.0, timeout_seconds)
    while True:
        if probe_fn(host, port):
            return True
        if clock() >= deadline:
            return False
        sleep(0.5)


def ensure_local_proxy(system_cfg: Mapping[str, Any] | None = None, *,
                       required: Optional[bool] = None,
                       environ: MutableMapping[str, str] | None = None,
                       probe_fn=probe,
                       start_fn: Optional[Callable[[Mapping[str, Any]], Any]] = None,
                       wait_fn=wait_until_listening,
                       apply_fn=apply_env) -> ProxyStatus:
    """Make sure this process can use a local Clash/mihomo proxy.

    ``required`` overrides ``network.proxy.required``. When the proxy cannot
    be made ready and required is true, raises ``ProxyUnavailableError``.
    """
    cfg = proxy_config(system_cfg)
    must = cfg["required"] if required is None else bool(required)
    host = cfg["host"]
    primary = proxy_url(host, cfg["port"])
    sidecar = proxy_url(host, cfg["sidecar_port"])
    starter = start_fn or start_sidecar_core

    if not cfg["enabled"]:
        status = ProxyStatus(True, "", "disabled", "network.proxy.enabled is false")
        logger.info("local proxy skipped: %s", status.message)
        return status

    env = os.environ if environ is None else environ
    ready, existing = env_proxy_ready(env, probe_fn=probe_fn)
    if ready:
        if not (env.get("NO_PROXY") or env.get("no_proxy")):
            env["NO_PROXY"] = cfg["no_proxy"]
            env["no_proxy"] = cfg["no_proxy"]
        status = ProxyStatus(True, existing, "env",
                             f"using existing proxy env ({existing})")
        logger.info("local proxy ready: %s", status.message)
        return status

    if probe_fn(host, cfg["port"]):
        apply_fn(primary, cfg["no_proxy"], env)
        status = ProxyStatus(True, primary, "listening",
                             f"Clash Verge proxy already listening at {primary}")
        logger.info("local proxy ready: %s", status.message)
        return status

    if probe_fn(host, cfg["sidecar_port"]):
        apply_fn(sidecar, cfg["no_proxy"], env)
        status = ProxyStatus(True, sidecar, "sidecar",
                             f"headless mihomo already listening at {sidecar}")
        logger.info("local proxy ready: %s", status.message)
        return status

    if not cfg["auto_start"]:
        message = (f"proxy not listening at {primary} or {sidecar} "
                   f"and network.proxy.auto_start is false")
        if must:
            raise ProxyUnavailableError(message)
        status = ProxyStatus(False, primary, "missing", message)
        logger.warning("local proxy unavailable: %s", message)
        return status

    try:
        starter(cfg)
    except ProxyUnavailableError:
        if must:
            raise
        status = ProxyStatus(False, sidecar, "missing",
                             "failed to start headless mihomo from Clash Verge config")
        logger.warning("local proxy unavailable: %s", status.message)
        return status

    logger.info("local proxy: started headless mihomo, waiting up to %.0fs for %s",
                cfg["startup_timeout_seconds"], sidecar)
    if wait_fn(host, cfg["sidecar_port"], cfg["startup_timeout_seconds"],
               probe_fn=probe_fn):
        apply_fn(sidecar, cfg["no_proxy"], env)
        status = ProxyStatus(True, sidecar, "started",
                             f"started headless mihomo at {sidecar}",
                             launched=True)
        logger.info("local proxy ready: %s", status.message)
        return status

    message = (f"started headless mihomo but {sidecar} did not become ready "
               f"within {cfg['startup_timeout_seconds']:.0f}s; see "
               f"{cfg['work_dir']}/mihomo.log")
    if must:
        raise ProxyUnavailableError(message)
    status = ProxyStatus(False, sidecar, "missing", message, launched=True)
    logger.warning("local proxy unavailable: %s", message)
    return status
