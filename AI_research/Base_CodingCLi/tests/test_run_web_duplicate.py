"""A second launch on a taken port exits before uvicorn, so it never runs the lifespan (render resume, autopilot)."""
from __future__ import annotations

import argparse
import socket

import pytest

import run_web


def test_a_taken_port_exits_before_uvicorn_starts(web_env, monkeypatch):
    tmp_path, _users = web_env
    owner = socket.socket()
    owner.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    owner.bind(("127.0.0.1", 0))
    owner.listen()
    port = owner.getsockname()[1]
    monkeypatch.setenv("AI_RESEARCH_APP_PORT", str(port))
    import uvicorn
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: pytest.fail("uvicorn started on a taken port"))
    try:
        with pytest.raises(SystemExit) as exit_info:
            run_web.cmd_serve(argparse.Namespace(port=None, host=None, reload=False, log_level="warning"))
    finally:
        owner.close()
    assert exit_info.value.code == 1
    assert not list((tmp_path / "state").glob("app_runtime-*.json"))


def test_a_free_port_reaches_uvicorn(web_env, monkeypatch):
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    monkeypatch.setenv("AI_RESEARCH_APP_PORT", str(port))
    started = []
    import uvicorn
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: started.append(k["port"]))
    run_web.cmd_serve(argparse.Namespace(port=None, host=None, reload=False, log_level="warning"))
    assert started == [port]
