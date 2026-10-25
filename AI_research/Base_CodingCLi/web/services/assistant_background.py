"""Small durable status records for assistant maintenance/index tasks."""
from __future__ import annotations
import json
from pathlib import Path
import threading
import time
import uuid
from web.services.assistant_store import OWNER, identity
from web.services.assistant_safety import redact
from web.services.yaml_io import atomic_write
from web.settings import get_settings


def folder() -> Path:
    path = Path(get_settings().paths.state_dir) / 'assistant-tasks'
    path.mkdir(parents=True, exist_ok=True)
    return path


def read(key: str, user: dict) -> dict:
    if not key or len(key) != 32 or any(c not in '0123456789abcdef' for c in key):
        raise ValueError('后台操作标识无效')
    try:
        data = json.loads((folder() / (key + '.json')).read_text())
    except (OSError, ValueError) as exc:
        raise ValueError('后台操作不存在') from exc
    if data['user_id'] != identity(user):
        raise ValueError('无权访问该操作')
    if data['status'] in ('queued', 'running') and data.get('owner') != OWNER and not data.get('detached'):
        data.update(status='interrupted', error='服务重启，操作中断；请核验业务状态后重新发起')
        atomic_write(str(folder() / (key + '.json')), json.dumps(data, ensure_ascii=False))
    return data


def start(kind: str, user: dict, operation) -> dict:
    key = uuid.uuid4().hex
    path = folder() / (key + '.json')
    data = {'id': key, 'kind': kind, 'user_id': identity(user), 'owner': OWNER,
            'status': 'queued', 'created': time.time(), 'progress': {}, 'error': None}
    def save(**updates):
        data.update(updates, updated=time.time())
        atomic_write(str(path), json.dumps(redact(data), ensure_ascii=False, default=str))
    save()
    def work():
        from core.fileio import file_lock
        try:
            with file_lock(str(folder() / (kind + '.lock')), timeout=0):
                save(status='running')
                result = operation(lambda **progress: save(progress=progress))
                failed = result.get('failed') or result.get('errors') or []
                count = len(failed) if isinstance(failed, list) else int(failed)
                save(status='partial' if count else 'completed', result=result,
                     error=f'{count} 项未完成，请查询操作结果中的失败项' if count else None)
        except Exception as exc:
            save(status='failed', error=str(exc)[:500])
    threading.Thread(target=work, daemon=True, name='assistant-' + kind).start()
    return {'task_id': key, 'status': 'queued', 'background': True}
