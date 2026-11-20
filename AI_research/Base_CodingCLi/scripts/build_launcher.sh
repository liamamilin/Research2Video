#!/usr/bin/env bash
# Compile the macOS launcher applet from scripts/launcher.applescript.in.
#
# Usage:
#   bash scripts/build_launcher.sh                 # build into the repo root
#   bash scripts/build_launcher.sh ~/Applications  # build and install there
#   bash scripts/build_launcher.sh <directory> <name.app>  # update an existing name
#
# The generated .app bakes in absolute paths (project dir, python, log file),
# so rebuild it if the repository or virtualenv moves.

set -euo pipefail

BASE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
TEMPLATE="$BASE_DIR/scripts/launcher.applescript.in"
RENDERED="$BASE_DIR/scripts/.launcher.rendered.applescript"
STARTER_TEMPLATE="$BASE_DIR/scripts/start_server_detached.sh"
APP_NAME="AREC.app"
DEST_DIR="${1:-$BASE_DIR}"
if [[ $# -ge 2 ]]; then
  APP_NAME="$2"
fi
if [[ "$APP_NAME" != *.app || "$APP_NAME" == */* ]]; then
  echo "error: app name must be a basename ending in .app" >&2
  exit 1
fi

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "error: the launcher is a macOS applet; nothing to do on $(uname -s)." >&2
  exit 1
fi

PYTHON_BIN="$BASE_DIR/../.AI_research/bin/python"
if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="$(command -v python3)"
fi

# Read the public listener from the same config as the app/gateway. A copied
# bundle can otherwise keep opening a port now used by an unrelated project.
BASE_URL="$("$PYTHON_BIN" - "$BASE_DIR/config/web.yaml" <<'PY'
import sys
from pathlib import Path
import yaml

config = yaml.safe_load(Path(sys.argv[1]).read_text(encoding="utf-8")) or {}
port = int((config.get("server") or {}).get("port", 8765))
if not 1 <= port <= 65535:
    raise ValueError("server.port must be between 1 and 65535")
print(f"http://127.0.0.1:{port}")
PY
)"

mkdir -p "$DEST_DIR" "$BASE_DIR/logs"

# The starter script needs the same interpreter path baked in.
STARTER="$BASE_DIR/scripts/.start_server.rendered.sh"
/usr/bin/sed -e "s|@PYTHON_BIN@|$PYTHON_BIN|g" "$STARTER_TEMPLATE" > "$STARTER"
chmod +x "$STARTER"

# @...@ placeholders are filled per machine; a stale absolute path is the main
# reason the launcher would silently fail after a clone or a move.
/usr/bin/sed \
  -e "s|@BASE_URL@|$BASE_URL|g" \
  -e "s|@PROJECT_DIR@|$BASE_DIR|g" \
  -e "s|@PYTHON_BIN@|$PYTHON_BIN|g" \
  -e "s|@LOG_FILE@|$BASE_DIR/logs/app_launcher.log|g" \
  -e "s|@START_SCRIPT@|$STARTER|g" \
  -e "s|@STATE_DIR@|$BASE_DIR/state|g" \
  "$TEMPLATE" > "$RENDERED"

TARGET="$DEST_DIR/$APP_NAME"
rm -rf "$TARGET"
# -s (stay-open) is required: without it the applet quits as soon as `run`
# returns, so `on idle` never fires and the 30-minute watchdog is dead code.
/usr/bin/osacompile -s -o "$TARGET" "$RENDERED"
rm -f "$RENDERED"

# Give it a real icon so it is not the default AppleScript droplet.
#
# Two files matter, and the second is the one that was missing: an applet ships
# both Contents/Resources/applet.icns and Contents/Resources/Assets.car, and
# Info.plist declares the icon through CFBundleIconFile *and* CFBundleIconName.
# On current macOS the compiled asset catalog wins, so copying only the .icns
# left the stock AppleScript ribbon visible on screen. Install both.
ICON="$BASE_DIR/scripts/launcher.icns"
ICON_DIR="$BASE_DIR/scripts/icon"
if [[ -f "$ICON" ]]; then
  /usr/bin/codesign --force --sign - "$TARGET" 2>/dev/null || true
  # Plain `cp`, not `/usr/bin/cp`: on macOS cp lives in /bin, so the absolute
  # path used here before did not exist and -- with `2>/dev/null || true` --
  # failed silently, which is why this applet kept the stock AppleScript icon
  # no matter how many times it was rebuilt.
  cp "$ICON" "$TARGET/Contents/Resources/applet.icns"
  # Replace the stock catalog built by osacompile; a stale one would win.
  if [[ -f "$ICON_DIR/Assets.car" ]]; then
    cp "$ICON_DIR/Assets.car" "$TARGET/Contents/Resources/Assets.car"
  fi
  # Declare the icon name the catalog was compiled with. osacompile already
  # writes CFBundleIconName=applet, so this is a no-op on a fresh build -- but a
  # bundle that predates it would otherwise ignore the catalog.
  /usr/libexec/PlistBuddy -c "Set :CFBundleIconName applet" \
    "$TARGET/Contents/Info.plist" 2>/dev/null \
    || /usr/libexec/PlistBuddy -c "Add :CFBundleIconName string applet" \
         "$TARGET/Contents/Info.plist" 2>/dev/null || true
  /usr/bin/codesign --force --sign - "$TARGET" 2>/dev/null || true
  # LaunchServices caches icons by bundle path + mtime; without this the Dock
  # keeps showing the old artwork for a while.
  touch "$TARGET"
fi

echo "built $TARGET"
echo "paths: project=$BASE_DIR"
echo "       url=$BASE_URL"
echo "       python=$PYTHON_BIN"
echo "       log=$BASE_DIR/logs/app_launcher.log"
