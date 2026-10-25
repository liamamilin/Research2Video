#!/usr/bin/env bash
# Build the macOS app icon from scripts/icon/AppIcon.svg.
#
# Produces scripts/launcher.icns (what build_launcher.sh copies into the applet)
# and scripts/icon/AppIcon.icns + Assets.car (the modern, richer form).
#
# Why Assets.car matters: an applet carries BOTH Contents/Resources/applet.icns
# and Contents/Resources/Assets.car, and Info.plist names the icon via both
# CFBundleIconFile and CFBundleIconName. On current macOS the Assets.car stack
# wins, so copying only the .icns left the stock AppleScript ribbon on screen --
# which is exactly the bug this script fixes.
#
# Usage:
#   bash scripts/build_icons.sh          # regenerate all icon artifacts
#
# Requires: Google Chrome (SVG rasteriser), Pillow, iconutil, and actool
# (from Xcode command line tools). Everything here runs offline.

set -euo pipefail

BASE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ICON_DIR="$BASE_DIR/scripts/icon"
SVG="$ICON_DIR/AppIcon.svg"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/arec-icon.XXXXXX")"
trap 'rm -rf "$WORK"' EXIT

CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
PYTHON="$BASE_DIR/../.AI_research/bin/python"
[[ -x "$PYTHON" ]] || PYTHON="$(command -v python3)"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "error: the app icon is a macOS asset; nothing to do on $(uname -s)." >&2
  exit 1
fi
[[ -f "$SVG" ]] || { echo "error: missing $SVG" >&2; exit 1; }
[[ -x "$CHROME" ]] || { echo "error: Google Chrome not found at $CHROME" >&2; exit 1; }

# --- 1. rasterise the master -------------------------------------------------
# Chrome renders SVG with alpha at the requested window size. --default-
# background-color=00000000 keeps the corners transparent (an opaque square
# around the squircle would show as a white box in the Dock).
echo "rendering $SVG"
"$CHROME" --headless --disable-gpu --no-sandbox --hide-scrollbars \
  --force-device-scale-factor=1 --default-background-color=00000000 \
  --window-size=1024,1024 --screenshot="$WORK/master.png" "file://$SVG" \
  >/dev/null 2>&1

# --- 2. downscale to the iconset --------------------------------------------
# The appiconset doubles as an .iconset (same PNG names differ), so we write
# both from one set of pixels. iconutil wants icon_<size>x<size>[@2x].png.
APPSET="$WORK/Assets.xcassets/applet.appiconset"
ICONSET="$WORK/applet.iconset"
mkdir -p "$APPSET" "$ICONSET" "$WORK/out"

"$PYTHON" - "$WORK/master.png" "$APPSET" "$ICONSET" <<'PY'
import sys
from PIL import Image

master, appset, iconset = sys.argv[1], sys.argv[2], sys.argv[3]
im = Image.open(master).convert("RGBA")
if im.size != (1024, 1024):
    raise SystemExit(f"master is {im.size}, expected 1024x1024")

# 1x/2x pairs actool expects for a macOS app icon.
for px in (16, 32, 64, 128, 256, 512, 1024):
    im.resize((px, px), Image.LANCZOS).save(f"{appset}/icon_{px}.png")

# iconutil's naming: logical size + @2x for the retina half.
import os
for logical in (16, 32, 128, 256, 512):
    im.resize((logical, logical), Image.LANCZOS).save(
        os.path.join(iconset, f"icon_{logical}x{logical}.png"))
    im.resize((logical * 2, logical * 2), Image.LANCZOS).save(
        os.path.join(iconset, f"icon_{logical}x{logical}@2x.png"))
PY

cat > "$APPSET/Contents.json" <<'JSON'
{
  "images": [
    {"idiom": "mac", "size": "16x16",   "scale": "1x", "filename": "icon_16.png"},
    {"idiom": "mac", "size": "16x16",   "scale": "2x", "filename": "icon_32.png"},
    {"idiom": "mac", "size": "32x32",   "scale": "1x", "filename": "icon_32.png"},
    {"idiom": "mac", "size": "32x32",   "scale": "2x", "filename": "icon_64.png"},
    {"idiom": "mac", "size": "128x128", "scale": "1x", "filename": "icon_128.png"},
    {"idiom": "mac", "size": "128x128", "scale": "2x", "filename": "icon_256.png"},
    {"idiom": "mac", "size": "256x256", "scale": "1x", "filename": "icon_256.png"},
    {"idiom": "mac", "size": "256x256", "scale": "2x", "filename": "icon_512.png"},
    {"idiom": "mac", "size": "512x512", "scale": "1x", "filename": "icon_512.png"},
    {"idiom": "mac", "size": "512x512", "scale": "2x", "filename": "icon_1024.png"}
  ],
  "info": {"version": 1, "author": "arec"}
}
JSON
printf '{"info":{"version":1,"author":"arec"}}' > "$WORK/Assets.xcassets/Contents.json"

# --- 3. .icns via iconutil (works without Xcode) -----------------------------
/usr/bin/iconutil -c icns "$ICONSET" -o "$WORK/applet-from-iconset.icns"

# --- 4. Assets.car via actool ------------------------------------------------
# Optional: without Xcode's actool we still ship a correct .icns. The icon set
# is named "applet" so the generated partial plist matches the applet's existing
# CFBundleIconName and no Info.plist edit is needed.
HAVE_ACTOOL=0
if xcrun --find actool >/dev/null 2>&1; then
  if xcrun actool "$WORK/Assets.xcassets" --compile "$WORK/out" \
        --platform macosx --minimum-deployment-target 11.0 \
        --app-icon applet --output-partial-info-plist "$WORK/partial.plist" \
        >/dev/null 2>&1 && [[ -f "$WORK/out/Assets.car" ]]; then
    HAVE_ACTOOL=1
  fi
fi

# --- 5. install artifacts ----------------------------------------------------
cp "$WORK/applet-from-iconset.icns" "$BASE_DIR/scripts/launcher.icns"
echo "wrote scripts/launcher.icns"

if [[ "$HAVE_ACTOOL" == "1" ]]; then
  cp "$WORK/out/applet.icns" "$ICON_DIR/AppIcon.icns"
  cp "$WORK/out/Assets.car" "$ICON_DIR/Assets.car"
  echo "wrote scripts/icon/AppIcon.icns and scripts/icon/Assets.car"
else
  echo "note: actool unavailable; Assets.car not rebuilt (the .icns alone is enough"
  echo "      for Finder/Dock, but a stale Assets.car would still win -- delete it"
  echo "      from the app bundle or install Xcode command line tools)."
fi

echo "done. app icon rebuilt from scripts/icon/AppIcon.svg"
