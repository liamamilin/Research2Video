"""Copy code/configuration and SQLite snapshots; never start production workers."""
from pathlib import Path
import hashlib
import json
import shutil
import sqlite3
import secrets
import yaml

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parents[2]
TARGET = HERE / 'runtime'
if TARGET.exists():
    raise SystemExit('Existing snapshot retained; do not overwrite it.')
TARGET.mkdir()
manifest = {'source': str(SOURCE), 'target': str(TARGET), 'files': {}, 'databases': {}}
for folder in ('core', 'utils', 'web', 'scripts', 'config', 'jobs'):
    for src in (SOURCE / folder).rglob('*'):
        if src.is_symlink() or not src.is_file() or '__pycache__' in src.parts or src.suffix == '.pyc':
            continue
        rel = src.relative_to(SOURCE)
        dst = TARGET / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        manifest['files'][str(rel)] = hashlib.sha256(src.read_bytes()).hexdigest()
shutil.copytree(SOURCE / 'ui' / 'dist', TARGET / 'ui' / 'dist')
for name in ('output', 'logs', 'state'):
    (TARGET / name).mkdir()
shutil.copytree(SOURCE / 'output', TARGET / 'output', dirs_exist_ok=True, symlinks=True)
for name in ('video.db', 'schedules.db', 'reports.db', 'materials.db', 'production_admission.db'):
    src = SOURCE / 'state' / name
    with sqlite3.connect(f'file:{src}?mode=ro', uri=True) as reader:
        with sqlite3.connect(TARGET / 'state' / name) as writer:
            reader.backup(writer)
    manifest['databases'][name] = {'bytes': (TARGET / 'state' / name).stat().st_size, 'method': 'SQLite online backup, source mode=ro'}
for pattern in ('ai_flash_discovery.json', 'history/ai_flash_discovery.jsonl', 'monitoring_ai_flash_discovery.json'):
    src = SOURCE / 'state' / pattern
    if src.is_file():
        dst = TARGET / 'state' / pattern
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
web = {'server': {'host': '127.0.0.1', 'port': 9251}, 'auth': {'secret_key': secrets.token_urlsafe(48)},
       'gateway': {'enabled': False}, 'cors': {'enabled': False},
       'rate_limit': {'login_per_minute': 1000, 'job_run_per_minute': 1000},
       'paths': {name+'_dir': str(TARGET/name) for name in ('jobs','config','output','state','logs')}}
(TARGET/'config'/'web.yaml').write_text(yaml.safe_dump(web))
manifest['excluded'] = ['production .env and users/accounts', 'media files and renderer storage', 'live LaunchAgents/cron', 'default application lifespan']
(HERE/'snapshot.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
print(json.dumps({'copied_files':len(manifest['files']), 'databases':list(manifest['databases']), 'runtime':str(TARGET)},ensure_ascii=False))
