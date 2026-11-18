"""Preflight or restore this rollout's source/UI after a managed shutdown; never rewind data."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import sys

SOURCE=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(SOURCE))
DATA=SOURCE/'state/validation/chain-production-integration-20261118'
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--apply',action='store_true')
args=parser.parse_args()
manifest=json.loads((DATA/'source-release.json').read_text())
conflicts=[]
for row in manifest:
    path=SOURCE/row['path']
    digest=hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None
    if digest not in (row['sha256'],row['after_sha256']):conflicts.append(row['path'])
    if row['sha256']:
        backup=DATA/'source-before'/row['path']
        assert hashlib.sha256(backup.read_bytes()).hexdigest()==row['sha256'],row['path']
if conflicts:raise SystemExit('Preserved later changes; cannot overwrite: '+', '.join(conflicts))
print('Source preflight passed:',len(manifest),'managed files; historical data excluded',flush=True)
if not args.apply:raise SystemExit(0)
from core.processes import process_exited
from core.maintenance_work import busy_work
from core.fileio import atomic_write
runtime=json.loads((SOURCE/'state/app_runtime.json').read_text())
assert process_exited(runtime['pid']),'Use managed shutdown and wait for completion before restoring source'
marker=json.loads((SOURCE/'state/assistant_maintenance.json').read_text())
assert marker.get('action')=='shutdown' and marker.get('status')=='completed' and marker.get('pid')==runtime['pid'],'Require the completed managed shutdown receipt'
assert not busy_work(SOURCE/'state',include_waiting_video=False),'Wait for executing work; prepared queues are retained'
ui_before=DATA/'formal-ui-before/index.html'
assert ui_before.exists(),'No completed UI activation to reverse'
current_ui=SOURCE/'ui/dist/index.html'
activated=json.loads((DATA/'formal-activation.json').read_text())
assert hashlib.sha256(current_ui.read_bytes()).hexdigest()==activated['ui_sha256'],'Later UI build preserved'
access=SOURCE/'config/chain_access.yaml'
expected='mode: enabled\nallow_create: true\nwritable_chains: ["*"]\n'
assert access.read_text()==expected,'Operator changed entry rollout settings; preserve and review them'
atomic_write(str(access),'mode: read_only\nallow_create: false\nwritable_chains: []\n')
for row in manifest:
    path=SOURCE/row['path']
    if row['sha256'] is None:path.unlink(missing_ok=True)
    else:atomic_write(str(path),(DATA/'source-before'/row['path']).read_text())
atomic_write(str(current_ui),ui_before.read_text())
print('Source and entry HTML restored. All configuration, plans, new reports, videos and publication records retained.')
print('Start with the existing run_web.py entry; reload the existing supervised dispatcher if needed.')
