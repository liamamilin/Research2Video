"""Freeze only this integration's files against the pre-edit source snapshot."""
from pathlib import Path
import difflib
import hashlib
import json
import shutil

SOURCE=Path(__file__).resolve().parents[3]
DATA=SOURCE/'state/validation/chain-production-integration-20261118'
rows=json.loads((DATA/'source-before.json').read_text())
known={r['path'] for r in rows}
for rel in ['core/config_transaction.py','scripts/manage_chain_rollout.py','scripts/backup_chain_state.py','tests/test_chain_config_transactions.py','tests/test_chain_management.py','tests/test_chain_production_runner.py','web/services/chain_access.py','web/services/chain_history.py','web/services/chain_reports.py']:
    if rel not in known: rows.append({'path':rel,'sha256':None})
manifest=[];patch=[]
for row in rows:
    rel=row['path']; before=DATA/'source-before'/rel; now=SOURCE/rel
    raw=before.read_bytes() if row['sha256'] else b''
    assert not row['sha256'] or hashlib.sha256(raw).hexdigest()==row['sha256'], rel
    after=now.read_bytes() if now.exists() else b''
    if raw==after:continue
    target=DATA/'source-candidate'/rel;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(after)
    manifest.append({**row,'after_sha256':hashlib.sha256(after).hexdigest() if now.exists() else None})
    patch.extend(difflib.unified_diff(raw.decode().splitlines(True),after.decode().splitlines(True),fromfile='a/'+rel,tofile='b/'+rel))
(DATA/'source-release.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
(DATA/'implementation.patch').write_text(''.join(patch))
print('Frozen',len(manifest),'changed source files; unrelated work excluded')
