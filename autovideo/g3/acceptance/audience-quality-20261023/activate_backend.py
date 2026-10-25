"""Activate backend-only changes after verifying the shared service is idle."""
import json
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
with sqlite3.connect(f"file:{ROOT / 'state/video.db'}?mode=ro", uri=True) as db:
    assert not db.execute("SELECT id FROM scripts WHERE status IN ('WRITING','RESEARCHING') OR (stage IS NOT NULL AND status != 'FAILED')").fetchall()
    assert not db.execute("SELECT id FROM renders WHERE status NOT IN ('COMPLETED','FAILED','CANCELLED')").fetchall()
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
    raise RuntimeError("Existing backend did not exit; no forced kill or duplicate launch")
with (ROOT / "state/audience-quality-backend.log").open("ab") as log:
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
    raise RuntimeError("Backend launch did not become live; inspect saved log")
result = {"old_pid": pid, "new_pid": proc.pid, "port": runtime["port"], "idle_verified": True, "live": True}
(OUT / "backend-activation.json").write_text(json.dumps(result, indent=2))
print(json.dumps(result))
