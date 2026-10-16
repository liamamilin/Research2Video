"""Reviewable service operations; target the current managed process only."""
from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from web.settings import get_settings
from web.services.assistant_store import identity
from web.services.yaml_io import atomic_write


def _read(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}


def _write(path, data):
    atomic_write(str(path), json.dumps(data, ensure_ascii=False))


def _state():
    return Path(get_settings().paths.state_dir).resolve()


def gateway_status():
    from web.routes.gateway import _describe
    desired = _describe()
    actual = _read(_state() / 'gateway_runtime.json')
    live = bool(actual and time.time() - actual.get('heartbeat', 0) < 5)
    applied = not desired['enabled'] or (live and actual.get('lan_access') == desired['lan_access'] and actual.get('port') == desired['public_port'])
    return {'saved': desired, 'actual': actual if live else None,
            'restart_required': not applied, 'control_available': live and actual.get('control_version') == 1,
            'message': '已核验实际监听' if applied else '配置已保存；尚未核验实际监听已匹配'}


def status():
    from web.runner.registry import TaskRegistry
    from web.runner import pipeline
    active = {name: run for name, run in TaskRegistry().all_tasks().items()
              if run.is_running or (run.future is not None and not run.future.done())}
    runtime = _read(_state() / 'app_runtime.json')
    return {'application': runtime, 'restart_supported': runtime.get('pid') == os.getpid(),
            'manual_runs': [{'job_name': run.job_name, 'run_id': run.task_id, 'status': run.status} for run in active.values()],
            'rounds': [r for r in pipeline.list_round_states() if r['status'] == 'running'],
            'gateway': gateway_status(),
            'scheduled_runs': '独立调度进程不随 Web 服务重启取消'}


def ensure_accepting_runs():
    marker = _read(_state() / 'assistant_maintenance.json')
    if marker.get('pid') == os.getpid() and marker.get('action') != 'rebind_gateway' and marker.get('status') in ('waiting', 'applying'):
        raise RuntimeError('服务维护正在进行，暂不接受新的手动运行')


@contextmanager
def run_admission():
    """Make maintenance admission and registration of a new run indivisible."""
    from core.fileio import file_lock
    with file_lock(str(_state() / 'assistant-maintenance-admission')):
        ensure_accepting_runs()
        yield


def _alive(pid):
    if type(pid) is not int or pid <= 1:
        return False
    from core.processes import process_exited
    return not process_exited(pid)


def _reconcile(data):
    if data.get('action') != 'rebind_gateway' or data.get('status') not in ('waiting', 'applying'):
        return data
    actual = _read(_state() / 'gateway_runtime.json')
    live = actual and time.time() - actual.get('heartbeat', 0) < 5
    replaced = (actual.get('pid') != data.get('gateway_pid') or actual.get('started_at', 0) > data.get('created', 0))
    target = data.get('gateway_target') or {}
    if live and replaced and target:
        matches = (actual.get('port') == target.get('public_port') and actual.get('app_port') == target.get('app_port')
                   and actual.get('lan_access') == target.get('lan_access') and actual.get('bind_host') == target.get('bind_host'))
        data.update(status='completed' if matches else 'interrupted', actual=actual, updated=time.time())
        if not matches:
            data['error'] = '网关实例已更换，原重绑定未完成；请核验已保存配置与实际监听后重新发起'
    elif not live and data.get('gateway_pid') and not _alive(data['gateway_pid']):
        data.update(status='interrupted', updated=time.time(), error='网关执行进程已退出，原操作中断；未自动重放')
    else:
        return data
    _write(_state() / 'maintenance' / (data['id'] + '.json'), data)
    if _read(_state() / 'assistant_maintenance.json').get('id') == data['id']:
        _write(_state() / 'assistant_maintenance.json', data)
    return data


def receipt(key, user):
    if len(key) != 32 or any(c not in '0123456789abcdef' for c in key):
        raise ValueError('维护标识无效')
    data = _read(_state() / 'maintenance' / (key + '.json'))
    if not data or data.get('user_id') != identity(user):
        raise ValueError('维护操作不存在或无权访问')
    return _reconcile(data)


def _spawn(spec_path, log_path):
    script = Path(__file__).resolve().parents[2] / 'scripts' / 'service_maintenance.py'
    # __file__ is web/services/...; parents[2] is the repository root.
    with open(log_path, 'ab') as log:
        subprocess.Popen([sys.executable, str(script), str(spec_path)],
                         stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)


def start(action, active_runs, request, user):
    from web import audit
    from web.runner.registry import TaskRegistry
    from web.runner import pipeline
    from core.fileio import file_lock
    root = _state()
    with file_lock(str(root / 'assistant-maintenance-admission')):
        info = status()
        if action != 'rebind_gateway' and not info['restart_supported']:
            raise ValueError('当前服务未由受支持的 run_web.py 入口启动，无法安全重启；请使用该入口启动后重试')
        if action == 'rebind_gateway' and not info['gateway']['control_available']:
            raise ValueError('当前网关尚不支持在线重绑定；升级后需从现有启动入口重启网关一次')
        running = (info['manual_runs'] or info['rounds']) if action != 'rebind_gateway' else False
        if running and active_runs == 'refuse':
            raise ValueError('有手动任务或轮次正在运行，请明确选择等待完成或取消这些运行')
        marker = _reconcile(_read(root / 'assistant_maintenance.json'))
        if marker.get('pid') == os.getpid() and marker.get('status') in ('waiting', 'applying'):
            raise ValueError('已有维护操作，请先查询其回执')
        key = uuid.uuid4().hex
        directory = root / 'maintenance'
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / (key + '.json')
        data = {'id': key, 'action': action, 'user_id': identity(user), 'status': 'waiting',
                'pid': os.getpid(), 'created': time.time(), 'runtime': info['application'],
                'state_dir': str(root), 'active_runs': active_runs}
        if action == 'rebind_gateway':
            data.update(gateway_pid=info['gateway']['actual']['pid'], gateway_target=info['gateway']['saved'])
        _write(path, data)
        _write(root / 'assistant_maintenance.json', data)
    audit.log('assistant_service_maintenance', user=user['username'], target=action,
              details={'id': key, 'active_runs': active_runs})
    def work():
        try:
            if running and active_runs == 'cancel':
                pipeline.cancel_round()
                for name in TaskRegistry().all_running():
                    TaskRegistry().cancel(name)
            # TaskRegistry.cancel marks status immediately; futures still need
            # time to release engines/locks before the process can be stopped.
            while action != 'rebind_gateway':
                tasks = list(TaskRegistry().all_tasks().values())
                executing = any(t.is_running or (t.future is not None and not t.future.done()) for t in tasks)
                rounds = any(r['status'] == 'running' for r in pipeline.list_round_states())
                if not executing and not rounds:
                    break
                time.sleep(0.5)
            data['status'] = 'applying'
            _write(path, data)
            _write(root / 'assistant_maintenance.json', data)
            if action == 'rebind_gateway':
                _write(root / 'gateway_control.json', {'id': key, 'action': 'reload', 'created': time.time()})
            else:
                _spawn(path, directory / (key + '.log'))
        except Exception as exc:
            data.update(status='failed', error=str(exc)[:300])
            _write(path, data)
            _write(root / 'assistant_maintenance.json', data)
    threading.Thread(target=work, daemon=True, name='service-maintenance').start()
    return {'maintenance_id': key, 'status': 'waiting', 'affected_runs': info['manual_runs'],
            'message': '操作已提交；恢复连接后查询维护回执核验结果'}
