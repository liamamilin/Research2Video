"""Fresh production snapshot for native-worker rehearsal; never replace an existing run."""
from pathlib import Path
import json
import shutil
import sqlite3
import time
import yaml

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parents[2]
DATA = SOURCE / 'state/validation/chain-production-integration-20261118'
RUNTIME = DATA / 'native'
if (RUNTIME / 'prepared.json').exists():
    raise SystemExit('保留已有正式接入预演数据；源码可单独同步，数据库不重置')
RUNTIME.mkdir(exist_ok=True)
for name in ('core', 'utils', 'web', 'scripts', 'config', 'jobs'):
    shutil.copytree(SOURCE / name, RUNTIME / name, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc', 'backups'))
for name in ('state', 'output', 'logs', 'tmp', 'evidence'):
    (RUNTIME / name).mkdir(exist_ok=True)
for name in ('video.db', 'reports.db', 'schedules.db'):
    with sqlite3.connect((SOURCE / 'state' / name).resolve().as_uri() + '?mode=ro', uri=True) as src:
        with sqlite3.connect(RUNTIME / 'state' / name) as dst:
            src.backup(dst)
for path in (SOURCE / 'output').rglob('*.md'):
    target = RUNTIME / 'output' / path.relative_to(SOURCE / 'output')
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, target)
if (SOURCE / 'state/report_meta.jsonl').exists():
    shutil.copy2(SOURCE / 'state/report_meta.jsonl', RUNTIME / 'state/report_meta.jsonl')
# Copied history must never resume a production remote job or trigger a copied schedule.
with sqlite3.connect(RUNTIME / 'state/video.db') as db:
    db.execute("UPDATE renders SET status='CANCELLED' WHERE status IN ('SUBMITTING','QUEUED','RUNNING','DOWNLOADING')")
    db.execute("UPDATE autopilot_runs SET status='CANCELLED' WHERE status IN ('QUEUED','RUNNING')")
    db.execute("UPDATE autopilot_plans SET enabled=0")
with sqlite3.connect(RUNTIME / 'state/schedules.db') as db:
    db.execute('UPDATE schedules SET enabled=0')
    db.execute("UPDATE schedule_runs SET status='cancelled' WHERE status IN ('queued','retry','starting','running')")
for path in (RUNTIME / 'config/production').glob('*.yaml'):
    value = yaml.safe_load(path.read_text())
    value['auto'] = 0
    path.write_text(yaml.safe_dump(value, allow_unicode=True, sort_keys=False))
flash = RUNTIME / 'jobs/monitoring/ai_flash_discovery.yaml'
value = yaml.safe_load(flash.read_text())
value['enabled'] = False  # prevents the unrelated native hot-trigger loop starting copied Flash work
flash.write_text(yaml.safe_dump(value, allow_unicode=True, sort_keys=False))
web = yaml.safe_load((RUNTIME / 'config/web.yaml').read_text())
web['paths'] = {name + '_dir': str((RUNTIME / name).resolve()) for name in ('jobs', 'config', 'output', 'state', 'logs')}
web['server'] = {'host': '127.0.0.1', 'port': 9254}
web['gateway'] = {'enabled': False}
web.setdefault('auth', {})['secret_key'] = 'native-chain-rehearsal-local-secret-20261118'
(RUNTIME / 'config/web.yaml').write_text(yaml.safe_dump(web, allow_unicode=True))
system = yaml.safe_load((RUNTIME / 'config/system.yaml').read_text())
video = system.setdefault('video', {})
video.update(autovideo_base_url='http://127.0.0.1:9253', require_mounts=[])
video['storage'] = {'dir': str((RUNTIME / 'state').resolve())}
video.setdefault('archive', {})['enabled'] = False
system.setdefault('logging', {})['file'] = str((RUNTIME / 'logs/native.log').resolve())
(RUNTIME / 'config/system.yaml').write_text(yaml.safe_dump(system, allow_unicode=True))
(RUNTIME / 'config/chain_access.yaml').write_text('mode: enabled\nallow_create: true\nwritable_chains: ["*"]\n')
(RUNTIME / 'ui').mkdir(exist_ok=True)
shutil.copytree(DATA / 'candidate-ui-dist', RUNTIME / 'ui/dist', dirs_exist_ok=True)
(RUNTIME / 'prepared.json').write_text(json.dumps({'at': time.time(), 'source': str(SOURCE),
    'history_fenced': True, 'credentials_copied': False, 'platform_queues_copied': False}, indent=2))
print(RUNTIME.resolve())
