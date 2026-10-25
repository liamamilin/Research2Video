from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from threading import Thread

import pytest

from g3.launcher.launch import LaunchError, start


@contextmanager
def service(title='AutoVideo Local'):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            value = {'status': 'ok', 'worker_primary': True} if self.path == '/api/health' else {'info': {'title': title}}
            body = json.dumps(value).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}'
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def config(tmp_path, base):
    project = tmp_path / '中文项目 with spaces'
    (project / 'g3').mkdir(parents=True)
    (project / 'g3/app.py').write_text('# fixture')
    return {'project_dir': str(project), 'python': '/missing/python', 'node': '/missing/node', 'base_url': base}


def test_launcher_reuses_verified_service_without_starting_another_process(tmp_path, monkeypatch):
    def unexpected_process(*args, **kwargs):
        raise AssertionError('A healthy existing service must be reused')
    monkeypatch.setattr('g3.launcher.launch.subprocess.Popen', unexpected_process)
    with service() as base:
        result = start(config(tmp_path, base))
        assert result['action'] == 'reused'
        assert result['url'] == base + '/#new'
        assert Path(result['log_path']).parent.is_dir()
        assert not (Path(result['log_path']).parent / 'server.json').exists()


def test_launcher_preserves_unrelated_service_on_occupied_port(tmp_path, monkeypatch):
    def unexpected_process(*args, **kwargs):
        raise AssertionError('An occupied port must not launch a competing service')
    monkeypatch.setattr('g3.launcher.launch.subprocess.Popen', unexpected_process)
    with service('Unrelated Local App') as base:
        with pytest.raises(LaunchError, match='端口已被占用'):
            start(config(tmp_path, base))
