#!/usr/bin/env python3
"""AI Research Console - Web UI entry point.

Usage:
  python run_web.py serve                       Start the server
  python run_web.py stop                        Stop the running server
  python run_web.py restart                     Restart the server
  python run_web.py create-admin <user> <pwd>   Create the first admin
  python run_web.py create-user <user> <pwd> <role>  Create a user
  python run_web.py reindex                     Force re-scan output/
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
import time

# Absolute, so `stop` works no matter where the command was run from. A
# relative path made the launcher and the gateway (which spawn the app with its
# own cwd) look in different places, and a missing file reads as "not running".
PID_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "state", "server.pid")


def cmd_serve(args):
    import uvicorn
    from web.settings import get_settings

    settings = get_settings()
    # With the gateway in front, the app moves to the internal port so exactly
    # one process owns the URL a browser (or a phone) talks to. The env var lets
    # the gateway choose the port without editing config on the fly.
    env_port = os.environ.get("AI_RESEARCH_APP_PORT", "").strip()
    port = int(env_port) if env_port.isdigit() else settings.app_port()
    if not env_port and args.port:
        port = args.port
    # The app is loopback-only; which address the front door binds is the
    # gateway's decision, not this process's.
    host = "127.0.0.1" if settings.gateway.enabled else (args.host or settings.server.host)
    from pathlib import Path
    from web.services.yaml_io import atomic_write
    import json
    runtime_path = Path(settings.paths.state_dir) / f'app_runtime-{os.getpid()}.json'
    runtime_path.parent.mkdir(parents=True, exist_ok=True)
    from web.services.runtime_files import prune_dead
    prune_dead(runtime_path.parent)
    pid_file = str(Path(settings.paths.state_dir).resolve() / 'server.pid')
    atomic_write(str(runtime_path), json.dumps({'pid': os.getpid(), 'host': host, 'port': port,
                 'python': sys.executable, 'config_path': settings.source_path, 'started': time.time(), 'pid_file': pid_file}))
    # Publish the shared runtime/PID only after this instance receives HTTP.
    # A duplicate launch that fails to bind must not overwrite the live owner.

    print(f"Starting AI Research Console on http://{host}:{port}")
    print(f"(API docs: http://{host}:{port}/docs)")
    if settings.gateway.enabled:
        print(f"(behind the gateway -- users should visit {settings.server.base_url}/)")

    try:
        uvicorn.run(
            "web.server:app",
            host=host,
            port=port,
            reload=args.reload,
            log_level=args.log_level,
            # Nothing sits in front of this server that we do not control, and
            # uvicorn trusts X-Forwarded-* by default. Left on, any loopback
            # client (the Vite dev proxy, the gateway) could rewrite the peer
            # address, which is what the login rate limit keys on -- so a
            # brute-force attempt would get a fresh bucket per request.
            proxy_headers=False,
            forwarded_allow_ips="",
            server_header=False,
        )
    finally:
        try:
            runtime_path.unlink(missing_ok=True)
            if os.path.isfile(pid_file) and Path(pid_file).read_text().strip() == str(os.getpid()):
                os.remove(pid_file)
        except OSError:
            pass


def cmd_stop(args):
    """Stop the running server by reading PID file."""
    from web.settings import get_settings
    pid_file = os.path.join(os.path.abspath(get_settings().paths.state_dir), 'server.pid')
    if not os.path.isfile(pid_file):
        print("Server is not running (no PID file found).", file=sys.stderr)
        sys.exit(1)

    with open(pid_file) as f:
        pid = int(f.read().strip())

    try:
        os.kill(pid, signal.SIGTERM)
        print(f"Sent SIGTERM to PID {pid}...")

        for _ in range(10):
            time.sleep(0.5)
            try:
                os.kill(pid, 0)
            except OSError:
                print(f"Server (PID {pid}) stopped.")
                return
        print(f"Server (PID {pid}) did not stop, sending SIGKILL...")
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        print(f"PID {pid} not found (server already stopped).")
        try:
            os.remove(pid_file)
        except OSError:
            pass
        sys.exit(1)


def cmd_restart(args):
    cmd_stop(args)
    cmd_serve(args)


def cmd_create_admin(args):
    cmd_create_user(args, default_role="admin")


def cmd_create_user(args, default_role: str | None = None):
    from web.auth import db as user_db
    from web.auth.password import hash_password

    role = getattr(args, "role", None) or default_role or "viewer"
    if role not in ("admin", "editor", "viewer"):
        print(f"Error: role must be one of admin/editor/viewer (got {role})", file=sys.stderr)
        sys.exit(1)

    user_db.init_db()
    existing = user_db.get_user_by_username(args.username)
    if existing:
        print(f"Error: user '{args.username}' already exists", file=sys.stderr)
        sys.exit(1)

    pwd_hash = hash_password(args.password)
    uid = user_db.create_user(args.username, pwd_hash, role)
    print(f"Created user '{args.username}' (id={uid}, role={role})")


def cmd_reindex(args):
    from web.indexer import db as index_db
    from web.indexer.scanner import full_scan
    from web.settings import get_settings

    index_db.init_db()
    settings = get_settings()
    result = full_scan(settings.paths.output_dir)
    print(f"Re-indexed {result['indexed']} files in {result['elapsed']:.1f}s")
    if result.get("errors"):
        print(f"Errors: {result['errors']}")


def main():
    p = argparse.ArgumentParser(description="AI Research Console")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("serve", help="Start the web server")
    sp.add_argument("--host", help="Override bind host")
    sp.add_argument("--port", type=int, help="Override bind port")
    sp.add_argument("--reload", action="store_true", help="Auto-reload on code change (dev)")
    sp.add_argument("--log-level", default="info")
    sp.set_defaults(func=cmd_serve)

    sp = sub.add_parser("stop", help="Stop the running server")
    sp.set_defaults(func=cmd_stop)

    sp = sub.add_parser("restart", help="Restart the server")
    sp.set_defaults(func=cmd_restart)

    sp = sub.add_parser("create-admin", help="Create an admin user")
    sp.add_argument("username")
    sp.add_argument("password")
    sp.set_defaults(func=cmd_create_admin)

    sp = sub.add_parser("create-user", help="Create a non-admin user")
    sp.add_argument("username")
    sp.add_argument("password")
    sp.add_argument("--role", choices=["admin", "editor", "viewer"], default="viewer")
    sp.set_defaults(func=cmd_create_user)

    sp = sub.add_parser("reindex", help="Force re-scan of output/ directory")
    sp.set_defaults(func=cmd_reindex)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
