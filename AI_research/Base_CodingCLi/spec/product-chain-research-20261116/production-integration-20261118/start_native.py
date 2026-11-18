"""Launch native services against the named rehearsal workspace, without OS installation."""
from pathlib import Path
import json
import os
import socket
import subprocess
import sys
from dotenv import dotenv_values

SOURCE = Path(__file__).resolve().parents[3]
DATA = SOURCE / 'state/validation/chain-production-integration-20261118'
ROOT = DATA / 'native'
assert (ROOT / 'prepared.json').exists(), 'Finish the snapshot before starting'
environment = dict(os.environ)
environment.update({k: v for k, v in dotenv_values(SOURCE / '.env').items() if v is not None})
environment.pop('AREC_NO_AUTOPILOT_LOOP', None)
environment.update(AI_RESEARCH_WEB_CONFIG=str(ROOT / 'config/web.yaml'),
                   AI_RESEARCH_SECRET_KEY='native-chain-rehearsal-local-secret-20261118',
                   AI_RESEARCH_LOG_FILE=str(ROOT / 'logs/native.log'),
                   TMPDIR=str(ROOT / 'tmp'), PYTHONDONTWRITEBYTECODE='1')

def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False

services = {
    'web': [sys.executable, '-m', 'uvicorn', 'web.server:create_app', '--factory', '--host', '127.0.0.1', '--port', '9254'],
    'dispatcher': [sys.executable, str(ROOT / 'scripts/dispatch_schedules.py'), '--workspace', str(ROOT),
                   '--state-dir', str(ROOT / 'state'), '--config-dir', str(ROOT / 'config'),
                   '--jobs-dir', str(ROOT / 'jobs'), '--logs-dir', str(ROOT / 'logs')],
}
for name, arguments in services.items():
    pid_file = ROOT / (name + '.pid')
    pid = int(pid_file.read_text()) if pid_file.exists() else 0
    if pid and alive(pid):
        print(name, 'already started', pid)
        continue
    with (ROOT / 'logs' / (name + '-process.log')).open('a') as stream:
        process = subprocess.Popen(arguments, cwd=ROOT, env=environment, stdin=subprocess.DEVNULL,
                                   stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
    pid_file.write_text(str(process.pid))
    print(name, 'started', process.pid)
print('http://chain-rehearsal.localhost:9254/chains')
