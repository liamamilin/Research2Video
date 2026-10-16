"""Build a small native macOS launcher for this local checkout."""
from __future__ import annotations

import argparse
from datetime import datetime
import getpass
import json
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[2]
SOURCE = Path(__file__).resolve().parent
BUNDLE_ID = 'local.autovideo.launcher'


def command(args, **kwargs):
    subprocess.run([str(a) for a in args], check=True, **kwargs)


def pin_to_dock(app: Path, state: Path) -> bool:
    current = subprocess.check_output(['/usr/bin/defaults', 'export', 'com.apple.dock', '-'])
    preferences = plistlib.loads(current)
    for tile in preferences.get('persistent-apps', []):
        raw = tile.get('tile-data', {}).get('file-data', {}).get('_CFURLString', '')
        if raw and Path(unquote(urlsplit(raw).path)).resolve() == app.resolve():
            return False
    backup = state / ('dock-before-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '.plist')
    backup.write_bytes(current)
    tile = {'tile-data': {'file-data': {'_CFURLString': app.as_uri() + '/', '_CFURLStringType': 15},
                          'file-label': 'AutoVideo', 'bundle-identifier': BUNDLE_ID}, 'tile-type': 'file-tile'}
    xml = plistlib.dumps(tile, fmt=plistlib.FMT_XML).decode()
    command(['/usr/bin/defaults', 'write', 'com.apple.dock', 'persistent-apps', '-array-add', xml])
    command(['/usr/bin/killall', '-u', getpass.getuser(), 'Dock'])
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install', action='store_true', help='Install in the current user Applications folder')
    parser.add_argument('--pin-dock', action='store_true', help='Pin the installed app to the Dock')
    parser.add_argument('--python', default=sys.executable, help='Existing Python with the G3 dependencies')
    args = parser.parse_args()
    if args.pin_dock and not args.install:
        parser.error('--pin-dock requires --install')
    python = Path(args.python).absolute()
    node = shutil.which('node')
    if not node:
        raise SystemExit('Node is not available; run this builder from the configured development environment.')
    command([python, '-c', 'import fastapi,uvicorn,pypdf,docx,multipart'])
    state = ROOT / 'g3/data/launcher'
    build = state / 'build'
    build.mkdir(parents=True, exist_ok=True)
    bundle = build / 'AutoVideo.app'
    if bundle.exists():
        shutil.rmtree(bundle)
    contents = bundle / 'Contents'
    resources = contents / 'Resources'
    executables = contents / 'MacOS'
    resources.mkdir(parents=True)
    executables.mkdir()
    config = {'project_dir': str(ROOT), 'python': str(python), 'node': str(Path(node).absolute()),
              'base_url': 'http://127.0.0.1:8011'}
    (resources / 'launcher.json').write_text(json.dumps(config, ensure_ascii=False, indent=2) + '\n')
    shutil.copy2(SOURCE / 'launch.py', resources / 'launch.py')
    plist = {'CFBundleIdentifier': BUNDLE_ID, 'CFBundleName': 'AutoVideo', 'CFBundleDisplayName': 'AutoVideo',
             'CFBundleExecutable': 'AutoVideo', 'CFBundleIconFile': 'AutoVideo.icns',
             'CFBundlePackageType': 'APPL', 'CFBundleVersion': '1', 'CFBundleShortVersionString': '0.1.0',
             'NSHighResolutionCapable': True, 'NSPrincipalClass': 'NSApplication',
             'LSMultipleInstancesProhibited': True, 'LSApplicationCategoryType': 'public.app-category.video'}
    (contents / 'Info.plist').write_bytes(plistlib.dumps(plist))
    cache = build / 'module-cache'
    cache.mkdir(exist_ok=True)
    print('Compiling the native launcher…', flush=True)
    command(['/usr/bin/xcrun', 'swiftc', '-O', '-module-cache-path', cache, '-framework', 'AppKit',
             SOURCE / 'AppMain.swift', '-o', executables / 'AutoVideo'])
    print('Rendering the app icon…', flush=True)
    icon_maker = build / 'IconMaker'
    command(['/usr/bin/xcrun', 'swiftc', '-O', '-module-cache-path', cache, '-framework', 'AppKit',
             SOURCE / 'Icon.swift', '-o', icon_maker])
    iconset = build / 'AutoVideo.iconset'
    command([icon_maker, iconset])
    command(['/usr/bin/iconutil', '-c', 'icns', iconset, '-o', resources / 'AutoVideo.icns'])
    command(['/usr/bin/codesign', '--force', '--sign', '-', bundle])
    command(['/usr/bin/codesign', '--verify', '--deep', '--strict', bundle])
    destination = bundle
    if args.install:
        destination = Path.home() / 'Applications/AutoVideo.app'
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            existing = plistlib.loads((destination / 'Contents/Info.plist').read_bytes())
            if existing.get('CFBundleIdentifier') != BUNDLE_ID:
                raise SystemExit('A different AutoVideo.app already exists; it was left unchanged.')
            backup = state / ('AutoVideo-before-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '.app')
            shutil.move(str(destination), backup)
        shutil.copytree(bundle, destination)
        subprocess.run(['/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister', '-f', str(destination)], check=False)
    pinned = pin_to_dock(destination, state) if args.pin_dock else False
    print(json.dumps({'app': str(destination), 'dock_added': pinned, 'bundle_bytes': sum(p.stat().st_size for p in destination.rglob('*') if p.is_file())}, ensure_ascii=False))


if __name__ == '__main__':
    main()
