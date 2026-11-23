"""Local Unix-socket daemon keeping pinned MLX voice & caption-aligner models warm across jobs.

Eliminates ~107 s VoxCPM2 cold load + compile and ~48 s Qwen3 Forced Aligner cold load per job
without changing audio bytes, captions, workspace isolation, or fixed-command allowlists.
Unloads models and exits automatically after IDLE_SECONDS of inactivity to free unified memory.
"""
from __future__ import annotations

import hashlib
import json
import os
import select
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TOKENIZERS_PARALLELISM"] = "false"

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SOCKET_NAME = "mlx-worker.sock"
DEFAULT_IDLE_SECONDS = 1200  # 20 minutes
VERSION_FILES = (
    ROOT / "g2/mlx_daemon.py",
    ROOT / "g2/voxcpm_voice.py",
    ROOT / "g2/align_captions.py",
    ROOT / "g2/tts_text.py",
    ROOT / "g2/caption_alignment.py",
    ROOT / "g2/voice_performance.py",
)


def socket_path(root: Path = ROOT) -> Path:
    # macOS AF_UNIX sun_path limit is 104 bytes; keep the socket path short and scoped per workspace root.
    tag = hashlib.sha256(str(root.resolve()).encode("utf-8")).hexdigest()[:12]
    return Path("/tmp") / f"autovideo-mlx-{tag}.sock"


def code_version() -> str:
    h = hashlib.sha256()
    for path in VERSION_FILES:
        if path.is_file():
            h.update(path.read_bytes())
    return h.hexdigest()[:16]


def daemon_enabled(environ: dict[str, str] | None = None) -> bool:
    raw = (environ if environ is not None else os.environ).get("AUTOVIDEO_G2_MLX_DAEMON", "1").strip().lower()
    return raw not in {"0", "false", "off", "no"}


def _connect(sock_path: Path, timeout: float = 5.0) -> socket.socket:
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    sock.connect(str(sock_path))
    return sock


def _ensure_daemon(sock_path: Path, version: str) -> bool:
    if sock_path.exists():
        try:
            with _connect(sock_path, timeout=3.0) as sock:
                sock.sendall((json.dumps({"op": "ping"}) + "\n").encode("utf-8"))
                line = sock.makefile("r", encoding="utf-8").readline()
                info = json.loads(line) if line else {}
                if info.get("ok") and info.get("version") == version:
                    return True
                # Code changed on disk: ask stale daemon to shut down cleanly.
                with _connect(sock_path, timeout=2.0) as shutdown_sock:
                    shutdown_sock.sendall((json.dumps({"op": "shutdown"}) + "\n").encode("utf-8"))
                time.sleep(0.3)
        except TimeoutError:
            # Daemon is alive and busy serving a request; do not unlink its active socket.
            return False
        except OSError:
            try:
                sock_path.unlink(missing_ok=True)
            except OSError:
                pass
    sock_path.parent.mkdir(parents=True, exist_ok=True)
    log_dir = ROOT / "g3/data/launcher"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "mlx-worker.log"
    with open(log_path, "ab") as log_file:
        subprocess.Popen(
            [sys.executable, str(ROOT / "g2/mlx_daemon.py"), "--socket", str(sock_path)],
            cwd=str(ROOT),
            stdin=subprocess.DEVNULL,
            stdout=log_file,
            stderr=log_file,
            start_new_session=True,
        )
    deadline = time.monotonic() + 8.0
    while time.monotonic() < deadline:
        if sock_path.exists():
            try:
                with _connect(sock_path, timeout=2.0) as sock:
                    sock.sendall((json.dumps({"op": "ping"}) + "\n").encode("utf-8"))
                    line = sock.makefile("r", encoding="utf-8").readline()
                    if line and json.loads(line).get("ok"):
                        return True
            except OSError:
                pass
        time.sleep(0.15)
    return False


def call_daemon(payload: dict, *, root: Path = ROOT, on_log=None) -> bool:
    """Try executing ``payload`` on the local warm daemon. Returns True if handled, False to fallback."""
    if not daemon_enabled():
        return False
    sock_path = socket_path(root)
    version = code_version()
    if not _ensure_daemon(sock_path, version):
        return False
    try:
        sock = _connect(sock_path, timeout=None)
    except OSError:
        return False
    with sock:
        try:
            sock.sendall((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
            reader = sock.makefile("r", encoding="utf-8")
            for raw in reader:
                if not raw:
                    break
                msg = json.loads(raw)
                kind = msg.get("type")
                if kind == "log":
                    if on_log:
                        on_log(msg.get("line", ""))
                    else:
                        print(msg.get("line", ""), flush=True)
                elif kind == "done":
                    if msg.get("ok"):
                        return True
                    raise RuntimeError(msg.get("error") or "mlx daemon request failed")
        except (OSError, json.JSONDecodeError):
            return False
    return False


class _ModelCache:
    """In-memory warm cache for pinned MLX models and hashed metadata."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.voxcpm = None  # (key, model, anchor, anchor_rate, ref_cache)
        self.aligner = None  # (model_dir_str, model)
        self.sha_cache: dict[tuple[str, int, int], str] = {}

    def get_file_sha256(self, path: Path) -> str:
        st = path.stat()
        key = (str(path.resolve()), st.st_mtime_ns, st.st_size)
        cached = self.sha_cache.get(key)
        if cached is not None:
            return cached
        h = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(4 * 1024 * 1024), b""):
                h.update(chunk)
        digest = h.hexdigest()
        self.sha_cache[key] = digest
        return digest

    def get_voxcpm(self, model_dir: Path, anchor_path: Path, compile_flag: bool, ref_cache_flag: bool):
        import mlx.core as mx
        import soundfile as sf
        from mlx_audio.tts.utils import load_model
        from g2.voice_performance import ReferenceEncoderCache
        from g2.voices import VOXCPM_ANCHOR_TEXT
        from g2.voxcpm_voice import active_seconds, units

        st_a = anchor_path.stat()
        key = (str(model_dir.resolve()), str(anchor_path.resolve()), st_a.st_mtime_ns, compile_flag, ref_cache_flag)
        if self.voxcpm is not None and self.voxcpm[0] == key:
            return self.voxcpm[1], self.voxcpm[2], self.voxcpm[3], self.voxcpm[4], 0.0
        t0 = time.monotonic()
        self.voxcpm = None
        mx.clear_cache()
        model = load_model(str(model_dir))
        if compile_flag:
            model.compile_model()
        anchor, sample_rate = sf.read(str(anchor_path), dtype="float32")
        if sample_rate != model.sample_rate:
            raise ValueError("Anchor clip must be recorded at the model sample rate")
        ref_cache = ReferenceEncoderCache(model, anchor, mx.eval) if ref_cache_flag else None
        anchor_rate = units(VOXCPM_ANCHOR_TEXT) / active_seconds(anchor, model.sample_rate)
        load_seconds = time.monotonic() - t0
        self.voxcpm = (key, model, anchor, anchor_rate, ref_cache)
        return model, anchor, anchor_rate, ref_cache, load_seconds

    def get_aligner(self, model_dir: Path):
        import mlx.core as mx
        from mlx_audio.stt.utils import load_model

        key = str(model_dir.resolve())
        if self.aligner is not None and self.aligner[0] == key:
            return self.aligner[1], 0.0
        t0 = time.monotonic()
        self.aligner = None
        mx.clear_cache()
        model = load_model(str(model_dir))
        load_seconds = time.monotonic() - t0
        self.aligner = (key, model)
        return model, load_seconds

    def clear(self) -> None:
        self.voxcpm = None
        self.aligner = None
        try:
            import mlx.core as mx
            mx.clear_cache()
        except Exception:
            pass


def serve(sock_path: Path, *, idle_seconds: float | None = None) -> None:
    if idle_seconds is None:
        idle_seconds = float(os.environ.get("AUTOVIDEO_G2_MLX_DAEMON_IDLE_SECONDS", str(DEFAULT_IDLE_SECONDS)))
    sock_path.parent.mkdir(parents=True, exist_ok=True)
    sock_path.unlink(missing_ok=True)
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(sock_path))
    os.chmod(sock_path, 0o600)
    server.listen(8)
    version = code_version()
    cache = _ModelCache()
    last_activity = time.monotonic()
    should_stop = False

    def send(conn: socket.socket, obj: dict) -> None:
        conn.sendall((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))

    try:
        while not should_stop:
            remaining = max(0.5, idle_seconds - (time.monotonic() - last_activity))
            ready, _, _ = select.select([server], [], [], min(5.0, remaining))
            if not ready:
                if time.monotonic() - last_activity >= idle_seconds:
                    break
                continue
            conn, _ = server.accept()
            last_activity = time.monotonic()
            with conn:
                try:
                    raw = conn.makefile("r", encoding="utf-8").readline()
                    if not raw:
                        continue
                    req = json.loads(raw)
                    op = req.get("op")
                    if op == "ping":
                        send(conn, {"ok": True, "version": version, "pid": os.getpid()})
                        continue
                    if op == "shutdown":
                        send(conn, {"ok": True})
                        should_stop = True
                        continue
                    emit = lambda line: send(conn, {"type": "log", "line": line})
                    with cache.lock:
                        if op == "voxcpm":
                            from g2.voxcpm_voice import generate_in_process
                            generate_in_process(
                                Path(req["plan_path"]),
                                Path(req["output_dir"]),
                                req["voice"],
                                cache=cache,
                                emit=emit,
                            )
                        elif op == "align":
                            from g2.align_captions import align_in_process
                            align_in_process(
                                Path(req["ir_path"]),
                                Path(req["output"]),
                                cache=cache,
                                emit=emit,
                            )
                        else:
                            raise ValueError(f"Unsupported daemon op: {op}")
                    last_activity = time.monotonic()
                    send(conn, {"type": "done", "ok": True})
                except Exception as exc:
                    last_activity = time.monotonic()
                    try:
                        send(conn, {"type": "done", "ok": False, "error": f"{type(exc).__name__}: {exc}"})
                    except OSError:
                        pass
    finally:
        cache.clear()
        server.close()
        sock_path.unlink(missing_ok=True)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--socket", type=Path, default=socket_path())
    args = parser.parse_args()
    serve(args.socket)
