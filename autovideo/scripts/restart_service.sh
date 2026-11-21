#!/usr/bin/env bash
# Idle-safe restart for local AutoVideo (127.0.0.1:8011).
# Usage:
#   bash autovideo/scripts/restart_service.sh
#   bash autovideo/scripts/restart_service.sh --force   # skip idle check
set -euo pipefail

FORCE=0
for arg in "$@"; do
  case "$arg" in
    --force|-f) FORCE=1 ;;
    -h|--help)
      sed -n '2,5p' "$0"
      exit 0
      ;;
    *)
      echo "unknown arg: $arg" >&2
      exit 2
      ;;
  esac
done

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PORT=8011
BASE="http://127.0.0.1:${PORT}"
DATA_DIR="${AUTOVIDEO_DATA_DIR:-$ROOT/g3/data}"
LOG_DIR="$DATA_DIR/launcher"
LOG_FILE="$LOG_DIR/server.log"
META_FILE="$LOG_DIR/server.json"
LAUNCHER_JSON="$LOG_DIR/build/AutoVideo.app/Contents/Resources/launcher.json"

mkdir -p "$LOG_DIR"

PY="${AUTOVIDEO_PYTHON:-}"
NODE="${AUTOVIDEO_NODE:-}"
if [[ -z "$PY" || -z "$NODE" ]]; then
  if [[ -f "$LAUNCHER_JSON" ]]; then
    eval "$(
      /usr/bin/python3 - "$LAUNCHER_JSON" <<'PY'
import json, shlex, sys
cfg = json.loads(open(sys.argv[1], encoding="utf-8").read())
print(f"PY={shlex.quote(cfg['python'])}")
print(f"NODE={shlex.quote(cfg['node'])}")
PY
    )"
  fi
fi
PY="${PY:-/Users/milin/.pyenv/versions/3.10.13/bin/python3}"
NODE="${NODE:-/Users/milin/.nvm/versions/node/v22.21.1/bin/node}"
if [[ ! -x "$PY" ]]; then
  echo "python not executable: $PY" >&2
  exit 1
fi
if [[ ! -x "$NODE" ]]; then
  echo "node not executable: $NODE" >&2
  exit 1
fi

echo "== AutoVideo restart =="
echo "root=$ROOT"
echo "python=$PY"

if [[ "$FORCE" -ne 1 ]]; then
  "$PY" - <<PY
from urllib.request import build_opener, ProxyHandler
import json, sys
opener = build_opener(ProxyHandler({}))
try:
    summary = json.load(opener.open("${BASE}/api/jobs?limit=1", timeout=5))["summary"]
except Exception as exc:
    print("warn: cannot read job summary (%s); continuing because listener may be down" % exc)
    raise SystemExit(0)
print("summary", summary)
if int(summary.get("running") or 0) != 0:
    raise SystemExit("ABORT: AutoVideo has running jobs; pass --force to override")
PY
fi

OLD_PID="$(lsof -nP -iTCP:${PORT} -sTCP:LISTEN -t 2>/dev/null | head -1 || true)"
if [[ -n "${OLD_PID}" ]]; then
  echo "stopping pid=${OLD_PID}"
  if ! kill -TERM "${OLD_PID}" 2>/tmp/av-restart-kill.err; then
    echo "ABORT: cannot signal pid ${OLD_PID}: $(cat /tmp/av-restart-kill.err 2>/dev/null)" >&2
    echo "hint: run this script in your own Terminal (outside Cursor sandbox)" >&2
    exit 1
  fi
  for _ in $(seq 1 90); do
    if ! kill -0 "${OLD_PID}" 2>/dev/null \
      && ! lsof -nP -iTCP:${PORT} -sTCP:LISTEN >/dev/null 2>&1; then
      echo "stopped"
      break
    fi
    sleep 0.5
  done
  if lsof -nP -iTCP:${PORT} -sTCP:LISTEN >/dev/null 2>&1; then
    echo "ABORT: port ${PORT} still busy after SIGTERM" >&2
    exit 1
  fi
else
  echo "no listener on ${PORT}"
fi

export PATH="$(dirname "$NODE"):$(dirname "$PY"):${PATH:-/usr/bin:/bin}"
export AUTOVIDEO_DATA_DIR="$DATA_DIR"
while IFS='=' read -r key _; do
  case "$key" in AUTOVIDEO_G2_*) unset "$key" ;; esac
done < <(env)

cd "$ROOT"
# Detach into a new session so Cursor/agent shell cleanup cannot reap the server
# with the parent process group. Same idea as g3/launcher/launch.py.
export AV_ROOT="$ROOT" AV_LOG_FILE="$LOG_FILE" AV_PORT="$PORT"
SPAWNED="$("$PY" - <<'PY'
import os, subprocess, sys
from pathlib import Path
log = Path(os.environ["AV_LOG_FILE"])
log.parent.mkdir(parents=True, exist_ok=True)
with log.open("ab") as stream:
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "g3.app:app",
         "--host", "127.0.0.1", "--port", os.environ["AV_PORT"]],
        cwd=os.environ["AV_ROOT"],
        env=os.environ.copy(),
        stdin=subprocess.DEVNULL,
        stdout=stream,
        stderr=subprocess.STDOUT,
        start_new_session=True,
        close_fds=True,
    )
print(proc.pid)
PY
)"
echo "spawned ${SPAWNED}"

export AV_BASE="$BASE" AV_PORT="$PORT" AV_META="$META_FILE"
"$PY" - <<'PY'
from urllib.request import build_opener, ProxyHandler
from datetime import datetime
from pathlib import Path
import json, os, time, subprocess
base = os.environ["AV_BASE"]
port = os.environ["AV_PORT"]
meta = Path(os.environ["AV_META"])
opener = build_opener(ProxyHandler({}))
deadline = time.monotonic() + 60
last = None
while time.monotonic() < deadline:
    try:
        health = json.load(opener.open(f"{base}/api/health", timeout=2))
        schema = json.load(opener.open(f"{base}/openapi.json", timeout=2))
        ok = (health.get("status") == "ok"
              and health.get("worker_primary") is True
              and schema.get("info", {}).get("title") == "AutoVideo Local")
        if ok:
            summary = json.load(opener.open(f"{base}/api/jobs?limit=1", timeout=5))["summary"]
            status = json.load(opener.open(f"{base}/api/config/status", timeout=5))
            print("ready", health)
            print("summary", summary)
            print("config_ready", status.get("ready"), "blockers", status.get("blockers"))
            break
        last = health
    except Exception as exc:
        last = str(exc)
    time.sleep(0.35)
else:
    raise SystemExit(f"AutoVideo not ready: {last}")
out = subprocess.check_output(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"], text=True)
pid = int(out.splitlines()[1].split()[1])
meta.write_text(
    json.dumps({
        "pid": pid,
        "started_at": datetime.now().astimezone().isoformat(),
        "base_url": base,
    }, indent=2) + "\n",
    encoding="utf-8",
)
print("server.json pid", pid)
print("OK AutoVideo restarted")
PY
