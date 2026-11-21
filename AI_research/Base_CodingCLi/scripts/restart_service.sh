#!/usr/bin/env bash
# Idle-safe restart for Research console app (gateway keeps 8775; app is 8766).
# Usage:
#   bash AI_research/Base_CodingCLi/scripts/restart_service.sh
#   bash AI_research/Base_CodingCLi/scripts/restart_service.sh --force
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
STATE="$ROOT/state"
RUNTIME="$STATE/app_runtime.json"
VIDEO_DB="$STATE/video.db"
PUBLIC_URL="${AI_RESEARCH_PUBLIC_URL:-http://127.0.0.1:8775}"
APP_PORT_DEFAULT=8766
LOG_DIR="$STATE/maintenance"
mkdir -p "$LOG_DIR" "$ROOT/logs"

PY="$ROOT/../.AI_research/bin/python"
if [[ ! -x "$PY" ]]; then
  PY="$(command -v python3)"
fi

echo "== Research restart =="
echo "root=$ROOT"
echo "python=$PY"

if [[ "$FORCE" -ne 1 ]]; then
  export RS_VIDEO_DB="$VIDEO_DB"
  "$PY" - <<'PY'
import os, sqlite3, sys
from pathlib import Path
db = Path(os.environ["RS_VIDEO_DB"])
if not db.is_file():
    print("warn: video.db missing; skip render idle check")
    raise SystemExit(0)
con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
n = con.execute(
    "select count(*) from renders where status in ('RUNNING','QUEUED','PENDING')"
).fetchone()[0]
con.close()
print("active renders", n)
if n:
    raise SystemExit("ABORT: research has active renders; pass --force to override")
PY
fi

APP_PORT="$APP_PORT_DEFAULT"
OLD_PID=""
if [[ -f "$RUNTIME" ]]; then
  eval "$(
    "$PY" - "$RUNTIME" <<'PY'
import json, shlex, sys
rt = json.loads(open(sys.argv[1], encoding="utf-8").read())
print(f"OLD_PID={shlex.quote(str(rt.get('pid') or ''))}")
print(f"APP_PORT={shlex.quote(str(rt.get('port') or 8766))}")
if rt.get("python"):
    print(f"PY={shlex.quote(rt['python'])}")
PY
  )"
fi
if [[ -z "${OLD_PID}" ]]; then
  OLD_PID="$(lsof -nP -iTCP:${APP_PORT} -sTCP:LISTEN -t 2>/dev/null | head -1 || true)"
fi

if [[ -n "${OLD_PID}" ]]; then
  echo "stopping app pid=${OLD_PID} port=${APP_PORT}"
  if ! kill -TERM "${OLD_PID}" 2>/tmp/rs-restart-kill.err; then
    echo "ABORT: cannot signal pid ${OLD_PID}: $(cat /tmp/rs-restart-kill.err 2>/dev/null)" >&2
    echo "hint: run this script in your own Terminal (outside Cursor sandbox)" >&2
    exit 1
  fi
  for _ in $(seq 1 120); do
    if ! kill -0 "${OLD_PID}" 2>/dev/null \
      && ! lsof -nP -iTCP:${APP_PORT} -sTCP:LISTEN >/dev/null 2>&1; then
      echo "stopped"
      break
    fi
    sleep 0.5
  done
  if lsof -nP -iTCP:${APP_PORT} -sTCP:LISTEN >/dev/null 2>&1; then
    echo "ABORT: app port ${APP_PORT} still busy after SIGTERM" >&2
    exit 1
  fi
else
  echo "no app listener on ${APP_PORT}"
fi

# Prefer letting the gateway respawn the app so ports/env stay consistent.
GATEWAY_UP=0
if curl -sf -o /dev/null --max-time 2 "${PUBLIC_URL}/__gateway/status" 2>/dev/null; then
  GATEWAY_UP=1
fi

if [[ "$GATEWAY_UP" -eq 1 ]]; then
  echo "waking gateway ${PUBLIC_URL}"
  # Any proxied request makes the gateway spawn a fresh app process.
  curl -sS -o /dev/null --max-time 5 "${PUBLIC_URL}/api/live" || true
  curl -sS -o /dev/null --max-time 5 "${PUBLIC_URL}/" || true
else
  echo "gateway not answering; starting app directly on ${APP_PORT}"
  export AI_RESEARCH_WEB_CONFIG="${AI_RESEARCH_WEB_CONFIG:-$ROOT/config/web.yaml}"
  export AI_RESEARCH_APP_PORT="$APP_PORT"
  nohup "$PY" "$ROOT/run_web.py" serve --host 127.0.0.1 --port "$APP_PORT" \
    >>"$LOG_DIR/manual-restart.log" 2>&1 < /dev/null &
  echo "spawned $!"
  disown 2>/dev/null || true
fi

export RS_PUBLIC="$PUBLIC_URL" RS_APP_PORT="$APP_PORT" RS_RUNTIME="$RUNTIME"
"$PY" - <<'PY'
from urllib.request import urlopen
from pathlib import Path
import json, os, time
public = os.environ["RS_PUBLIC"].rstrip("/")
app_port = os.environ["RS_APP_PORT"]
runtime_path = Path(os.environ["RS_RUNTIME"])
deadline = time.monotonic() + 180
last = None
old_instance = None
if runtime_path.is_file():
    try:
        old_instance = json.loads(runtime_path.read_text(encoding="utf-8")).get("instance_id")
    except Exception:
        old_instance = None
while time.monotonic() < deadline:
    for url in (f"{public}/api/live", f"http://127.0.0.1:{app_port}/api/live",
                f"http://127.0.0.1:{app_port}/api/health"):
        try:
            body = json.load(urlopen(url, timeout=2))
            if body.get("status") != "ok":
                last = body
                continue
            # Prefer evidence the process actually flipped.
            if runtime_path.is_file():
                current = json.loads(runtime_path.read_text(encoding="utf-8"))
                new_id = current.get("instance_id")
                if old_instance and new_id == old_instance:
                    last = {"waiting_for_new_instance": new_id}
                    continue
                print("ready", url, {"pid": current.get("pid"), "instance_id": new_id, "port": current.get("port")})
            else:
                print("ready", url, body)
            print("OK Research restarted")
            raise SystemExit(0)
        except SystemExit:
            raise
        except Exception as exc:
            last = str(exc)
    time.sleep(1)
raise SystemExit(f"Research not ready: {last}")
PY
