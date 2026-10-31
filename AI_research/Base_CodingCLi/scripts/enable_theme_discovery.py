#!/usr/bin/env python3
"""Register each active content matrix's daily topic discovery using the managed scheduler."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> None:
    from utils.env_file import load_env
    load_env(str(ROOT / ".env"))
    from core import matrices
    from web.services import theme_workbench
    from web.settings import get_settings
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all", action="store_true", help="Enable for all active content domains")
    parser.add_argument("--matrix", help="Enable for one matrix")
    parser.add_argument("--time", default="09:00")
    args = parser.parse_args()
    settings = get_settings()
    keys = [m.key for m in matrices.load_all(settings.paths.config_dir).values()
            if not m.archived and m.content.get("role", "domain") == "domain"] if args.all else [args.matrix]
    if not all(keys):
        parser.error("Use --all or --matrix")
    for key in keys:
        current = theme_workbench._schedule(key)
        if current:
            print(key, "kept", current["id"])
            continue
        saved = theme_workbench.set_schedule(key, enabled=True, at=args.time, platforms=["bilibili", "zhihu", "youtube"])
        print(key, saved["schedule"]["next_run"], flush=True)


if __name__ == "__main__":
    main()
