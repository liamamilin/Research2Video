#!/usr/bin/env python3
"""Permanent front door for the console.

Why this exists
---------------
The 30-minute idle shutdown used to be the end of the story: once the launcher
applet quit, the only thing that could answer ``http://127.0.0.1:8765`` was the
app itself, so a bookmark died with it and a phone could never reach the
console at all.

This process owns the public port and treats the app as a child it starts on
demand:

    browser/phone ──▶ gateway :8765 ──▶ app :8766 (loopback only)
                          │
                          └── app down? spawn it, show "starting", retry

Design constraints that shaped it:

* **Stdlib only.** This is the process that has to work when everything else is
  broken, so it must not depend on the project's venv being healthy.
* **Spawn only on a real request.** A background poller would resurrect the app
  the moment the idle timer fired and quietly defeat the 30-minute rule. The
  gateway never polls; it only reacts to traffic, and the applet's own watchdog
  is traffic too, which is exactly the self-healing we want.
* **Never let a boot loop fork servers.** One spawn at a time, with a cooldown.
* **Stream, don't buffer.** The console pushes live job logs over SSE; buffering
  would turn a progress log into one lump at the end.

Usage:
    python scripts/gateway.py            # run in the foreground
    python scripts/gateway.py --status   # report state, do not serve
"""

from __future__ import annotations

import argparse
import errno
import http.client
import json
import os
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils import net as _net  # noqa: E402 - needs ROOT on sys.path first

# Hop-by-hop headers must not be forwarded in either direction (RFC 9110).
HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade",
}
# Endpoints the gateway answers itself; everything else belongs to the app.
GATEWAY_PREFIX = "/__gateway"

_STARTING_PAGE = """<!doctype html>
<html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="2">
<title>研映 · Research2Video · 正在启动</title>
<style>
  :root {{ color-scheme: dark; }}
  body {{ margin:0; min-height:100vh; display:flex; align-items:center;
         justify-content:center; background:#0d1117; color:#e6edf3;
         font:15px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }}
  .box {{ text-align:center; padding:2rem; max-width:30rem; }}
  h1 {{ font-size:1.05rem; font-weight:600; margin:0 0 .35rem; }}
  p {{ color:#8b949e; font-size:.85rem; margin:.2rem 0; }}
  .spin {{ width:26px; height:26px; margin:0 auto 1.1rem; border-radius:50%;
           border:2px solid #21262d; border-top-color:#58a6ff; animation:s .8s linear infinite; }}
  @keyframes s {{ to {{ transform:rotate(360deg); }} }}
  code {{ background:#161b22; padding:.1rem .35rem; border-radius:4px; font-size:.8rem; }}
  .note {{ margin-top:1.4rem; font-size:.75rem; color:#6e7681; }}
  .clock {{ font-variant-numeric:tabular-nums; color:#58a6ff; }}
</style></head>
<body><div class="box">
  <div class="spin"></div>
  <h1>正在启动 研映 · Research2Video</h1>
  <p>检测到后台服务未运行，已自动拉起。启动需要十几秒（要建立报告索引）。</p>
  <p>已等待 <span class="clock" id="t">0</span> 秒，页面每 2 秒自动刷新，就绪后直接进入。</p>
  <p class="note">若超过一分钟仍在等待，请查看项目日志
     <code>logs/app_launcher.log</code> 与 <code>logs/gateway.err.log</code>。</p>
</div>
<script>
  var waited = 0;
  setInterval(function () { waited += 2; document.getElementById('t').textContent = waited; }, 2000);
</script>
</body></html>
"""


_OPERATOR_OWNED = frozenset({"AI_RESEARCH_SECRET_KEY", "WEB_SECRET_KEY"})


def _env_file_keys(project: Path) -> frozenset:
    """Names defined in the project's .env, parsed without dotenv.

    The gateway is the process that must survive everything else being broken,
    so it stays stdlib-only and does not import the venv's dotenv. Only the key
    names matter here, and ``KEY=value`` lines are trivial to read.
    """
    path = project / ".env"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return frozenset()
    keys = set()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key = line.split("=", 1)[0].strip()
        # Tolerate `export KEY=value`, which some people write.
        if key.startswith("export "):
            key = key[len("export "):].strip()
        if key:
            keys.add(key)
    return frozenset(keys)


def lan_address() -> str:
    """This machine's LAN IP, or an empty string when there isn't one.

    Delegates to utils.net so the URL the UI shows and the address the listener
    advertises can never disagree, and so a VPN/TUN address is filtered out
    rather than handed to a phone that cannot reach it.
    """
    return _net.lan_address()


class Supervisor:
    """Keeps at most one app alive, and never spawns two at once."""

    def __init__(self, app_port: int, project: Path, cooldown: float = 10.0,
                 health_timeout: float = 2.0, python: str | None = None, state_dir: Path | None = None,
                 logs_dir: Path | None = None):
        self.app_port = app_port
        self.project = project
        self.cooldown = max(0.0, cooldown)
        self.health_timeout = health_timeout
        self.python = python or sys.executable
        self.state_dir = state_dir or project / 'state'
        self.logs_dir = logs_dir or project / 'logs'
        self._lock = threading.Lock()
        self._last_attempt = 0.0
        self._started_at: float | None = None
        self.spawns = 0
        self.last_error = ""
        self._child: subprocess.Popen | None = None

    def healthy(self) -> bool:
        conn = http.client.HTTPConnection("127.0.0.1", self.app_port, timeout=self.health_timeout)
        try:
            # Dependency diagnostics write probes/read databases and belong to
            # /api/health. A config error must not prevent opening Settings to fix it.
            conn.request("GET", "/api/live")
            response = conn.getresponse()
            response.read()
            if response.status == 404:  # rolling upgrade from an older app
                conn.close()
                conn = http.client.HTTPConnection("127.0.0.1", self.app_port, timeout=self.health_timeout)
                conn.request("GET", "/api/health")
                response = conn.getresponse()
                response.read()
            return response.status == 200
        except (OSError, http.client.HTTPException):
            return False
        finally:
            conn.close()

    def claimed(self) -> bool:
        """Whether anything already holds the app port.

        A listener may be warming up or belong to another launcher. It must
        not get a competing server just because its health probe timed out.
        Our own child is tracked separately while it has not bound yet.
        """
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.settimeout(min(1.0, self.health_timeout))
        try:
            return probe.connect_ex(("127.0.0.1", self.app_port)) == 0
        except OSError:
            return False
        finally:
            probe.close()

    def ensure(self, *, allow_shutdown_wake: bool = True) -> tuple[bool, str]:
        """Return (healthy, note). Starts the app at most once per cooldown."""
        if self.healthy():
            return True, "running"
        with self._lock:
            # Re-check inside the lock: several browser requests can arrive
            # while the app is booting, and each must not fork a server.
            if self.healthy():
                return True, "running"
            # Uvicorn completes startup/indexing before binding its listener.
            # A live child still owns this boot even after the retry cooldown.
            if self._child is not None and self._child.poll() is None:
                return False, "starting"
            if self.claimed():
                # Something owns the port and is not answering yet, so it is
                # still coming up rather than crashed.
                return False, "starting"
            try:
                marker = json.loads((self.state_dir / 'assistant_maintenance.json').read_text())
                if not allow_shutdown_wake and marker.get('action') == 'shutdown' and marker.get('status') in ('applying', 'completed'):
                    runtime = json.loads((self.state_dir / 'app_runtime.json').read_text())
                    if runtime.get('pid') == marker.get('pid'):
                        return False, 'stopped'
                if marker.get('action') == 'restart' and marker.get('status') in ('applying', 'starting') and \
                        time.time() - marker.get('updated', marker.get('created', 0)) < 300:
                    return False, 'maintenance'
            except (OSError, ValueError):
                pass
            now = time.monotonic()
            if self._last_attempt and (now - self._last_attempt) < self.cooldown:
                return False, "starting"
            self._last_attempt = now
            self.spawns += 1
            self._started_at = time.time()
            try:
                self._spawn()
                self.last_error = ""
            except OSError as exc:
                self.last_error = str(exc)
                return False, f"spawn_failed: {exc}"
        return False, "starting"

    def _spawn(self) -> None:
        log = self.logs_dir / "gateway_app.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        pid_file = self.state_dir / "server.pid"
        pid_file.parent.mkdir(parents=True, exist_ok=True)
        # A stale pid file from a crashed run would make the applet think a
        # server is still around, so clear it before handing over.
        pid_file.unlink(missing_ok=True)
        # Do NOT forward the .env values this process loaded at its own startup.
        # The gateway lives for weeks, so its copy of a key is stale the moment
        # the console saves a new one; passing that snapshot on made the app
        # serve yesterday's key. The app re-reads .env itself, so drop the keys
        # the file defines and let it fill them in fresh. This is why the
        # gateway does not simply pass {**os.environ}.
        #
        # The JWT secret is kept: the README tells operators to set it in the
        # environment, so an explicit value must reach the app -- and load_env
        # would otherwise see the stripped copy and fall back to the file.
        env = {k: v for k, v in os.environ.items()
               if k not in _env_file_keys(self.project) or k in _OPERATOR_OWNED}
        env["AI_RESEARCH_APP_PORT"] = str(self.app_port)
        with open(log, "ab") as handle:
            child = subprocess.Popen(
                [self.python, str(self.project / "run_web.py"), "serve"],
                cwd=str(self.project), stdout=handle, stderr=handle,
                stdin=subprocess.DEVNULL, start_new_session=True, env=env,
            )
        self._child = child
        threading.Thread(target=child.wait, daemon=True, name='reap-app').start()

    def status(self) -> dict:
        return {
            "healthy": self.healthy(),
            "app_port": self.app_port,
            "spawns": self.spawns,
            "started_at": self._started_at,
            "last_error": self.last_error,
        }


class GatewayHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "AREC-Gateway"

    # Injected by serve().
    supervisor: Supervisor
    public_port: int
    lan_access: bool
    lan_host: str

    # --- plumbing ---------------------------------------------------------
    def log_message(self, fmt, *args):  # noqa: A003 - stdlib signature
        if os.environ.get("AREC_GATEWAY_VERBOSE"):
            sys.stderr.write("[gateway] " + (fmt % args) + "\n")

    def _send(self, status: int, body: bytes, content_type: str,
              extra: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _starting(self) -> None:
        self._send(503, _STARTING_PAGE.encode("utf-8"), "text/html; charset=utf-8",
                   {"Retry-After": "2"})

    def _gateway_status(self) -> None:
        payload = {
            "ok": True,
            "app": self.supervisor.status(),
            "lan_access": self.lan_access,
            "lan_url": f"http://{self.lan_host}:{self.public_port}/" if self.lan_access else "",
            "public_url": f"http://{'127.0.0.1' if not self.lan_access else self.lan_host}:{self.public_port}/",
        }
        self._send(200, json.dumps(payload).encode("utf-8"),
                   "application/json; charset=utf-8")

    # --- proxy ------------------------------------------------------------
    def _proxy(self) -> None:
        path = self.path
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None

        headers = {}
        for key, value in self.headers.items():
            if key.lower() in HOP_BY_HOP or key.lower() == "host":
                continue
            headers[key] = value
        host, _, port = self.headers.get("Host", "").partition(":")
        headers["Host"] = f"127.0.0.1:{self.supervisor.app_port}"
        headers["X-Forwarded-Host"] = self.headers.get("Host", "")
        headers["X-Forwarded-Proto"] = "http"
        headers["X-Forwarded-For"] = self.client_address[0]

        conn = http.client.HTTPConnection("127.0.0.1", self.supervisor.app_port, timeout=600)
        try:
            conn.request(self.command, path, body=body, headers=headers)
            upstream = conn.getresponse()
            self.send_response(upstream.status)
            # Frame the body ourselves. http.client has already decoded any
            # upstream chunking, so the only question left is whether the length
            # is known. SSE never carries one -- getting this wrong forwards an
            # empty body and silently turns a live log into nothing at all.
            declared = upstream.getheader("Content-Length")
            for key, value in upstream.getheaders():
                if key.lower() in HOP_BY_HOP or key.lower() == "content-length":
                    continue
                self.send_header(key, value)
            if self.command == "HEAD":
                self.send_header("Content-Length", declared or "0")
                self.end_headers()
            elif declared is not None:
                self.send_header("Content-Length", declared)
                self.end_headers()
                self._pump_plain(upstream, int(declared))
            else:
                self.send_header("Transfer-Encoding", "chunked")
                self._pump_chunked(upstream)
            self.close_connection = False
        except (OSError, http.client.HTTPException) as exc:
            # The app can die between the health check and this request; say so
            # instead of hanging the browser on a half-open socket.
            if not self.wfile.closed:
                try:
                    self._send(502, json.dumps({
                        "error": {"code": "app_unreachable", "message": str(exc)}
                    }).encode("utf-8"), "application/json; charset=utf-8")
                except OSError:
                    pass
        finally:
            conn.close()

    def _pump_plain(self, upstream, length: int) -> None:
        remaining = length
        while remaining > 0:
            chunk = upstream.read(min(65536, remaining))
            if not chunk:
                break
            self.wfile.write(chunk)
            self.wfile.flush()
            remaining -= len(chunk)

    def _pump_chunked(self, upstream) -> None:
        self.end_headers()
        while True:
            try:
                # read1, not read: read(n) blocks until it holds n bytes, which
                # on a live log means waiting for the buffer to fill, so the
                # client sees nothing until the stream is already over.
                chunk = upstream.read1(4096)
            except (OSError, http.client.HTTPException):
                break
            if not chunk:
                break
            self.wfile.write(b"%x\r\n" % len(chunk) + chunk + b"\r\n")
            self.wfile.flush()
        try:
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except OSError:
            pass

    # --- dispatch ---------------------------------------------------------
    def _handle(self) -> None:
        if self.path.split("?", 1)[0] == f"{GATEWAY_PREFIX}/status":
            self._gateway_status()
            return
        document = self.command in ('GET', 'HEAD') and (
            self.path.split('?', 1)[0] in ('/', '/index.html') or 'text/html' in (self.headers.get('Accept') or ''))
        healthy, _note = self.supervisor.ensure(allow_shutdown_wake=document)
        if not healthy:
            # A real request is the trigger, so a bookmark revives the app.
            # Only a document request gets the friendly page; an API caller
            # should see a status code it can reason about.
            if self.path.split("?", 1)[0] in ("/", "/index.html") or \
                    "text/html" in (self.headers.get("Accept") or ""):
                self._starting()
            else:
                self._send(503, json.dumps({
                    "error": {"code": "app_stopped" if _note == 'stopped' else "app_starting",
                              "message": "服务已关停；重新打开应用页面后可恢复访问并核验维护回执" if _note == 'stopped' else "服务正在启动，请稍候重试"}
                }).encode("utf-8"), "application/json; charset=utf-8",
                    {"Retry-After": "2"})
            return
        self._proxy()

    do_GET = do_HEAD = do_POST = do_PUT = do_PATCH = do_DELETE = _handle

    def do_OPTIONS(self) -> None:
        healthy, _ = self.supervisor.ensure(allow_shutdown_wake=False)
        if not healthy:
            self._starting()
            return
        self._proxy()


def _load_settings():
    from web.settings import get_settings
    return get_settings()


def _serve_once(rebinding: str | None = None, fallback: dict | None = None) -> tuple[int, str | None]:
    settings = _load_settings()
    if fallback:
        # Restore the last actual listener without rewriting the desired
        # config. The UI can then truthfully show "saved, still pending".
        settings.server.host = fallback['bind_host']
        settings.server.port = fallback['port']
        settings.gateway.lan_access = fallback['lan_access']
        settings.gateway.app_port = fallback['app_port']
    gateway = settings.gateway
    if not gateway.enabled:
        print("gateway.enabled is false; run the app directly (python run_web.py serve)",
              file=sys.stderr)
        return 2, None

    public_port = settings.server.port
    host = lan_address() if gateway.lan_access else ""
    if gateway.lan_access and not host:
        print("LAN access requested but no LAN address was found; staying on loopback",
              file=sys.stderr)

    handler = type("BoundHandler", (GatewayHandler,), {
        "supervisor": Supervisor(
            app_port=gateway.app_port, project=ROOT,
            cooldown=gateway.respawn_cooldown, health_timeout=gateway.health_timeout,
            state_dir=Path(settings.paths.state_dir),
            logs_dir=Path(settings.paths.logs_dir),
        ),
        "public_port": public_port,
        "lan_access": gateway.lan_access,
        "lan_host": host or "127.0.0.1",
    })

    class Server(ThreadingHTTPServer):
        daemon_threads = True
        allow_reuse_address = True

        def handle_error(self, request, client_address):
            # A browser that navigates away or aborts a log stream resets the
            # socket. That is routine, not an error worth a stack trace in the
            # launchd error log on every page change.
            kind = sys.exc_info()[0]
            if kind and issubclass(kind, (ConnectionResetError, BrokenPipeError,
                                          ConnectionAbortedError)):
                return
            super().handle_error(request, client_address)

    try:
        httpd = Server((settings.bind_host(), public_port), handler)
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:
            print(f"port {public_port} is already in use — is the gateway already running?",
                  file=sys.stderr)
            return 1, None
        raise

    if gateway.lan_access and host:
        print(f"gateway listening on {settings.bind_host()}:{public_port}")
        print(f"  this machine : http://127.0.0.1:{public_port}/")
        print(f"  phone / LAN  : http://{host}:{public_port}/")
    else:
        print(f"gateway listening on {settings.bind_host()}:{public_port} (loopback only)")
    print(f"  app (internal): 127.0.0.1:{gateway.app_port}")

    from core.fileio import atomic_write
    state = Path(settings.paths.state_dir)
    state.mkdir(parents=True, exist_ok=True)
    stop = threading.Event()
    reload_id = []
    started = time.time()
    actual = {'pid': os.getpid(), 'started_at': started, 'bind_host': settings.bind_host(), 'port': public_port,
              'app_port': gateway.app_port, 'lan_access': gateway.lan_access, 'control_version': 1}

    def write(path, data):
        atomic_write(str(path), json.dumps(data, ensure_ascii=False))

    def finish_reload(key, status, **extra):
        path = state / 'maintenance' / (key + '.json')
        try:
            record = json.loads(path.read_text())
            record.update(status=status, actual=actual, updated=time.time(), **extra)
            write(path, record)
            write(state / 'assistant_maintenance.json', record)
        except (OSError, ValueError):
            pass

    write(state / 'gateway_runtime.json', {**actual, 'heartbeat': time.time()})
    if rebinding:
        finish_reload(rebinding, 'completed')

    def monitor():
        while not stop.wait(1):
            write(state / 'gateway_runtime.json', {**actual, 'heartbeat': time.time()})
            try:
                command = json.loads((state / 'gateway_control.json').read_text())
                key = command.get('id', '')
                if command.get('action') != 'reload' or command.get('created', 0) < started or \
                        len(key) != 32 or any(c not in '0123456789abcdef' for c in key):
                    continue
                record = json.loads((state / 'maintenance' / (key + '.json')).read_text())
                if record.get('action') != 'rebind_gateway' or record.get('status') != 'applying':
                    continue
                reload_id.append(key)
                httpd.shutdown()
                return
            except (OSError, ValueError):
                pass
    threading.Thread(target=monitor, daemon=True, name='gateway-control').start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\ngateway stopped")
    finally:
        stop.set()
        httpd.server_close()
    return 0, reload_id[0] if reload_id else None


def serve() -> int:
    """Rebind under the same supervisor process, with a durable receipt."""
    rebinding = None
    fallback = None
    previous = None
    state = None
    while True:
        from web.settings import reset_settings
        reset_settings()
        try:
            settings = _load_settings()
            state = Path(settings.paths.state_dir)
            if not rebinding:
                previous = None
                try:
                    previous = json.loads((state / 'gateway_runtime.json').read_text())
                except (OSError, ValueError):
                    pass
            code, next_reload = _serve_once(rebinding, fallback)
        except OSError:
            code, next_reload = 1, None
        if code and rebinding and previous and state:
            from core.fileio import atomic_write
            path = state / 'maintenance' / (rebinding + '.json')
            try:
                record = json.loads(path.read_text())
                record.update(status='failed', updated=time.time(),
                              error='新监听未能绑定，正在恢复先前监听；保存的配置仍待生效')
                atomic_write(str(path), json.dumps(record, ensure_ascii=False))
                atomic_write(str(state / 'assistant_maintenance.json'), json.dumps(record, ensure_ascii=False))
            except (OSError, ValueError):
                pass
            fallback, rebinding = previous, None
            continue
        if not next_reload:
            return code
        try:
            previous = json.loads((state / 'gateway_runtime.json').read_text())
        except (OSError, ValueError):
            previous = None
        rebinding = next_reload
        fallback = None


def status() -> int:
    settings = _load_settings()
    supervisor = Supervisor(app_port=settings.app_port(), project=ROOT,
                            health_timeout=settings.gateway.health_timeout)
    print(json.dumps({
        "gateway_enabled": settings.gateway.enabled,
        "app_healthy": supervisor.healthy(),
        "app_port": settings.app_port(),
        "public_port": settings.server.port,
        "bind_host": settings.bind_host(),
        "lan_url": (f"http://{lan_address()}:{settings.server.port}/"
                    if settings.gateway.lan_access else ""),
    }, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Permanent front door for the console")
    parser.add_argument("--status", action="store_true", help="report state and exit")
    args = parser.parse_args()
    return status() if args.status else serve()


if __name__ == "__main__":
    raise SystemExit(main())
