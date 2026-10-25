"""Start or reuse the local AutoVideo service; no models run at launch."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime
import fcntl
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, build_opener


class LaunchError(RuntimeError):
    pass


def read_json(opener, url: str) -> dict:
    with opener.open(url, timeout=1.5) as response:
        value = json.load(response)
    if not isinstance(value, dict):
        raise LaunchError("本地服务返回了无法识别的状态。")
    return value


def service_ready(opener, base_url: str) -> bool:
    try:
        health = read_json(opener, base_url + '/api/health')
        schema = read_json(opener, base_url + '/openapi.json')
        return (health.get('status') == 'ok' and health.get('worker_primary') is True
                and schema.get('info', {}).get('title') == 'AutoVideo Local')
    except (OSError, ValueError, LaunchError):
        return False


def port_in_use(port: int) -> bool:
    with socket.socket() as connection:
        connection.settimeout(.4)
        return connection.connect_ex(('127.0.0.1', port)) == 0


@contextmanager
def startup_lock(path: Path):
    with path.open('a') as lock:
        deadline = time.monotonic() + 45
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() > deadline:
                    raise LaunchError('另一次启动仍未结束，请稍后再次点击。')
                time.sleep(.15)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def start(config: dict) -> dict:
    project = Path(config['project_dir']).expanduser().resolve()
    python = Path(config['python']).expanduser()
    node = Path(config['node']).expanduser()
    base = config.get('base_url', 'http://127.0.0.1:8011').rstrip('/')
    parsed = urlsplit(base)
    if parsed.scheme != 'http' or parsed.hostname != '127.0.0.1' or parsed.path or not parsed.port:
        raise LaunchError('启动器仅支持本机 127.0.0.1 HTTP 服务。')
    if not (project / 'g3/app.py').is_file():
        raise LaunchError('找不到 AutoVideo 项目，请在项目的新位置重新构建启动器。')
    log_dir = project / 'g3/data/launcher'
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / 'server.log'
    opener = build_opener(ProxyHandler({}))
    with startup_lock(log_dir / 'start.lock'):
        if service_ready(opener, base):
            result = {'status': 'ready', 'action': 'reused', 'url': base + '/#new'}
        elif port_in_use(parsed.port):
            # An initializing server may own the socket briefly. Never kill it or start a competitor.
            for _ in range(5):
                time.sleep(.3)
                if service_ready(opener, base):
                    break
            else:
                raise LaunchError(f'{parsed.port} 端口已被占用，或制作服务尚未就绪。请检查已有服务后再启动。')
            result = {'status': 'ready', 'action': 'reused', 'url': base + '/#new'}
        else:
            if not python.is_file() or not node.is_file():
                raise LaunchError('Python 或 Node 环境已移动，请重新构建启动器。')
            env = os.environ.copy()
            env['PATH'] = os.pathsep.join([str(node.parent), str(python.parent), env.get('PATH', '/usr/bin:/bin:/usr/sbin:/sbin')])
            env['AUTOVIDEO_DATA_DIR'] = str(project / 'g3/data')
            # Development shell variables must not alter a Dock-launched production task.
            for key in list(env):
                if key.startswith('AUTOVIDEO_G2_'):
                    env.pop(key)
            with log_path.open('ab') as stream:
                process = subprocess.Popen(
                    [str(python), '-m', 'uvicorn', 'g3.app:app', '--host', '127.0.0.1', '--port', str(parsed.port)],
                    cwd=project, env=env, stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT,
                    start_new_session=True, close_fds=True,
                )
            os.chmod(log_path, 0o600)
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                if service_ready(opener, base):
                    record = {'pid': process.pid, 'started_at': datetime.now().astimezone().isoformat(), 'base_url': base}
                    (log_dir / 'server.json').write_text(json.dumps(record, indent=2) + '\n')
                    result = {'status': 'ready', 'action': 'started', 'pid': process.pid, 'url': base + '/#new'}
                    break
                if process.poll() is not None:
                    raise LaunchError('本地服务未能启动，请查看启动日志。')
                time.sleep(.25)
            else:
                raise LaunchError('本地服务启动等待超时，请查看日志。再次点击会先检查已有服务。')
        result['log_path'] = str(log_path)
        with (log_dir / 'launcher.log').open('a') as stream:
            stream.write(json.dumps({'time': datetime.now().astimezone().isoformat(), **result}, ensure_ascii=False) + '\n')
        return result


def main() -> int:
    parser = argparse.ArgumentParser(description='AutoVideo local app bootstrap')
    parser.add_argument('--config', type=Path, default=Path(__file__).with_name('launcher.json'))
    args = parser.parse_args()
    try:
        config = json.loads(args.config.read_text())
        result = start(config)
    except Exception as exc:
        print(json.dumps({'status': 'error', 'message': str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
