"""Reload the shared Research service only after its work is idle."""
import ast
import hashlib
import json
import shlex
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import time
import urllib.request

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[3] / "AI_research/Base_CodingCLi"
runtime = json.loads((ROOT / "state/app_runtime.json").read_text())
assert Path(runtime["config_path"]).resolve() == ROOT / "config/web.yaml"
independent = None
with sqlite3.connect(f"file:{ROOT / 'state/video.db'}?mode=ro", uri=True) as db:
    db.row_factory = sqlite3.Row
    busy = [dict(r) for r in db.execute("SELECT id,status,stage,current_version,heartbeat_at,error FROM scripts WHERE status IN ('WRITING','RESEARCHING') OR (stage IS NOT NULL AND status != 'FAILED')")]
    if busy:
        # This one live owner was inspected: it runs run_rewrite directly, not
        # through the web backend. Startup recovery leaves fresh heartbeats alone.
        owner_pid = 51844
        owner_file = Path('/tmp/arec-minimax-editorial-verify.py')
        assert len(busy) == 1 and busy[0]['id'] == 'bb6a3d490aed48dbb82eb0b8fbb9b9ba'
        assert time.time() - busy[0]['heartbeat_at'] < 45
        owner_cmd = shlex.split(subprocess.check_output(['ps','-p',str(owner_pid),'-o','command='],text=True))
        assert str(owner_file) in owner_cmd and owner_pid != int(runtime['pid'])
        owner_source = owner_file.read_text()
        tree = ast.parse(owner_source)
        assert busy[0]['id'] in owner_source
        assert any(isinstance(n,ast.Call) and ast.unparse(n.func)=='video_workflow.run_rewrite' for n in ast.walk(tree))
        assert '127.0.0.1:876' not in owner_source and 'localhost:876' not in owner_source
        independent = {'owner_pid':owner_pid,'owner_script':str(owner_file),'owner_script_sha256':hashlib.sha256(owner_file.read_bytes()).hexdigest(),'before':busy[0]}

    assert not db.execute("SELECT id FROM renders WHERE status NOT IN ('COMPLETED','FAILED','CANCELLED')").fetchall(), "busy render: leave service running"
for path in (ROOT / "state").glob("*.json"):
    try:
        item = json.loads(path.read_text())
    except (OSError, ValueError):
        continue
    assert not isinstance(item, dict) or str(item.get("last_status", "")).upper() not in ("RUNNING", "RESEARCHING", "WRITING"), path.name
pid = int(runtime["pid"])
command = subprocess.check_output(["ps", "-p", str(pid), "-o", "command="], text=True)
assert "run_web.py" in command and "serve" in command
os.kill(pid, signal.SIGTERM)
for _ in range(100):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        break
    time.sleep(.1)
else:
    raise RuntimeError("Service did not exit gracefully; no forced kill or duplicate launch")
with (OUT / "backend.log").open("ab") as log:
    proc = subprocess.Popen([runtime["python"], str(ROOT / "run_web.py"), "serve", "--port", str(runtime["port"])],
                            cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
for _ in range(100):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{runtime['port']}/api/live", timeout=1) as response:
            if response.status == 200:
                break
    except OSError:
        time.sleep(.2)
else:
    raise RuntimeError("Backend did not become live; inspect saved log")
if independent:
    with sqlite3.connect(f"file:{ROOT / 'state/video.db'}?mode=ro", uri=True) as db:
        db.row_factory=sqlite3.Row
        row=dict(db.execute('SELECT id,status,stage,current_version,heartbeat_at,error FROM scripts WHERE id=?',(independent['before']['id'],)).fetchone())
    if row['status'] in ('WRITING','RESEARCHING') or row.get('stage'):
        os.kill(independent['owner_pid'],0)
        assert time.time()-row['heartbeat_at'] < 45
    assert '服务重启' not in (row.get('error') or '')
    independent['after']=row
result = {"old_pid": pid, "new_pid": proc.pid, "port": runtime["port"], "idle_verified": True, "live": True,
          "web_owned_work_idle":True,"independent_work_preserved":independent}
(OUT / "backend-activation.json").write_text(json.dumps(result, indent=2))
print(json.dumps(result))
