"""Unit tests for core.local_proxy — no real Clash Verge required."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from core.local_proxy import (
    ProxyUnavailableError,
    apply_env,
    ensure_local_proxy,
    env_proxy_ready,
    proxy_config,
    proxy_url,
    write_sidecar_config,
)


def test_proxy_config_defaults():
    cfg = proxy_config({})
    assert cfg["enabled"] is True
    assert cfg["host"] == "127.0.0.1"
    assert cfg["port"] == 7897
    assert cfg["sidecar_port"] == 17897
    assert cfg["auto_start"] is True
    assert cfg["required"] is True
    assert "verge-mihomo" in cfg["core_path"]


def test_proxy_config_disabled():
    cfg = proxy_config({"network": {"proxy": {"enabled": False, "port": "7900"}}})
    assert cfg["enabled"] is False
    assert cfg["port"] == 7900


def test_apply_env_sets_both_cases():
    env = {}
    apply_env("http://127.0.0.1:7897", "localhost", env)
    assert env["HTTP_PROXY"] == "http://127.0.0.1:7897"
    assert env["https_proxy"] == "http://127.0.0.1:7897"
    assert env["ALL_PROXY"] == "http://127.0.0.1:7897"
    assert env["NO_PROXY"] == "localhost"
    assert env["no_proxy"] == "localhost"


def test_env_proxy_ready_when_listening():
    env = {"HTTPS_PROXY": "http://127.0.0.1:7897"}
    ready, url = env_proxy_ready(env, probe_fn=lambda host, port: host == "127.0.0.1" and port == 7897)
    assert ready is True
    assert url == "http://127.0.0.1:7897"


def test_env_proxy_ready_false_when_dead():
    env = {"HTTP_PROXY": "http://127.0.0.1:1"}
    ready, url = env_proxy_ready(env, probe_fn=lambda *_: False)
    assert ready is False
    assert "127.0.0.1" in url


def test_write_sidecar_config_disables_tun_and_rewrites_port(tmp_path: Path):
    source = tmp_path / "clash-verge.yaml"
    source.write_text(yaml.safe_dump({
        "mixed-port": 7897,
        "tun": {"enable": True, "stack": "gvisor"},
        "dns": {"enable": True, "enhanced-mode": "fake-ip"},
        "external-controller-unix": "/tmp/verge.sock",
        "proxies": [{"name": "x", "type": "ss", "server": "1.1.1.1", "port": 443}],
    }), encoding="utf-8")
    dest = tmp_path / "out" / "mihomo.yaml"
    write_sidecar_config(source, dest, mixed_port=17897)
    data = yaml.safe_load(dest.read_text(encoding="utf-8"))
    assert data["mixed-port"] == 17897
    assert data["tun"]["enable"] is False
    assert data["dns"]["enhanced-mode"] == "redir-host"
    assert "external-controller-unix" not in data
    assert data["external-controller"] == ""
    assert len(data["proxies"]) == 1


def test_ensure_disabled_skips():
    status = ensure_local_proxy({"network": {"proxy": {"enabled": False}}})
    assert status.ready is True
    assert status.source == "disabled"
    assert status.launched is False


def test_ensure_reuses_existing_env_without_start():
    env = {"HTTP_PROXY": "http://127.0.0.1:7897", "HTTPS_PROXY": "http://127.0.0.1:7897"}
    started = []

    status = ensure_local_proxy(
        {"network": {"proxy": {"enabled": True, "port": 7897}}},
        environ=env,
        probe_fn=lambda host, port: True,
        start_fn=lambda cfg: started.append(cfg),
    )
    assert status.ready is True
    assert status.source == "env"
    assert started == []


def test_ensure_listening_injects_env_without_start():
    env = {}
    started = []

    def probe(host, port):
        return host == "127.0.0.1" and port == 7897

    status = ensure_local_proxy(
        {"network": {"proxy": {"enabled": True}}},
        environ=env,
        probe_fn=probe,
        start_fn=lambda cfg: started.append(cfg),
    )
    assert status.ready is True
    assert status.source == "listening"
    assert status.url == proxy_url("127.0.0.1", 7897)
    assert env["http_proxy"] == status.url
    assert started == []


def test_ensure_reuses_existing_sidecar_port():
    env = {}

    def probe(host, port):
        return port == 17897

    status = ensure_local_proxy(
        {"network": {"proxy": {"enabled": True}}},
        environ=env,
        probe_fn=probe,
        start_fn=lambda cfg: (_ for _ in ()).throw(AssertionError("should not start")),
    )
    assert status.ready is True
    assert status.source == "sidecar"
    assert status.url == "http://127.0.0.1:17897"
    assert env["HTTPS_PROXY"] == status.url


def test_ensure_starts_sidecar_when_ports_down():
    env = {}
    state = {"up": False}
    started = []

    def probe(host, port):
        return state["up"] and port == 17897

    def start(cfg):
        started.append(cfg["sidecar_port"])
        state["up"] = True

    def wait(host, port, timeout, probe_fn=None):
        return (probe_fn or probe)(host, port)

    status = ensure_local_proxy(
        {"network": {"proxy": {"enabled": True, "auto_start": True, "required": True}}},
        environ=env,
        probe_fn=probe,
        start_fn=start,
        wait_fn=wait,
    )
    assert status.ready is True
    assert status.source == "started"
    assert status.launched is True
    assert started == [17897]
    assert env["HTTPS_PROXY"] == "http://127.0.0.1:17897"


def test_ensure_timeout_raises_when_required():
    with pytest.raises(ProxyUnavailableError, match="did not become ready"):
        ensure_local_proxy(
            {"network": {"proxy": {
                "enabled": True, "auto_start": True, "required": True,
                "startup_timeout_seconds": 1,
            }}},
            environ={},
            probe_fn=lambda *_: False,
            start_fn=lambda cfg: None,
            wait_fn=lambda *_args, **_kwargs: False,
        )


def test_ensure_timeout_warns_when_not_required():
    status = ensure_local_proxy(
        {"network": {"proxy": {"enabled": True, "auto_start": True, "required": True}}},
        required=False,
        environ={},
        probe_fn=lambda *_: False,
        start_fn=lambda cfg: None,
        wait_fn=lambda *_args, **_kwargs: False,
    )
    assert status.ready is False
    assert status.source == "missing"
    assert status.launched is True


def test_ensure_auto_start_false_missing():
    with pytest.raises(ProxyUnavailableError, match="auto_start is false"):
        ensure_local_proxy(
            {"network": {"proxy": {
                "enabled": True, "auto_start": False, "required": True,
            }}},
            environ={},
            probe_fn=lambda *_: False,
            start_fn=lambda cfg: None,
        )
