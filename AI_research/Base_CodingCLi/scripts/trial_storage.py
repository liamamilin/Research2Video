"""Create an external work directory for a video trial or acceptance run."""
from __future__ import annotations

import argparse
import os
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[3]


def mounted_directory(path: Path) -> Path:
    """Resolve a directory and require a real mounted volume under /Volumes."""
    target = path.resolve()
    parts = target.parts
    if len(parts) < 4 or parts[:2] != ("/", "Volumes"):
        raise ValueError(f"试验目录必须位于外接盘：{path}")
    mount = Path(*parts[:3])
    if not os.path.ismount(mount) or not target.is_dir() or target.stat().st_dev != mount.stat().st_dev:
        raise ValueError(f"试验外接盘未挂载或目录不可用：{target}")
    return target


def storage_root(kind: str, workspace: Path = WORKSPACE) -> Path:
    """Return a stable project path backed by external storage."""
    data = workspace / "autovideo/g3/data"
    aliases = {
        "research": workspace / "AI_research/Base_CodingCLi/state/validation",
        "autovideo": data / "trials",
    }
    alias = aliases[kind]
    if not alias.is_symlink():
        if alias.exists():
            raise ValueError(f"试验目录仍在本机，请先迁移：{alias}")
        external_data = mounted_directory(data / "jobs").parent
        archive = external_data / "工作流试验"
        archive.mkdir(exist_ok=True)
        runs = mounted_directory(archive) / "新试验"
        runs.mkdir(exist_ok=True)
        target = mounted_directory(runs) / kind
        target.mkdir(exist_ok=True)
        mounted_directory(target)
        try:
            alias.symlink_to(target, target_is_directory=True)
        except FileExistsError:
            if not alias.is_symlink() or alias.resolve() != target:
                raise
    mounted_directory(alias)
    return alias


def trial_directory(kind: str, name: str, workspace: Path = WORKSPACE) -> Path:
    """Create or reopen a named external workspace."""
    if not name or name in {".", ".."} or "/" in name:
        raise ValueError("试验名称必须是单个目录名")
    base = storage_root(kind, workspace)
    path = base / name
    if path.is_symlink():
        raise ValueError(f"试验名称已被符号链接占用：{path}")
    path.mkdir(exist_ok=True)
    mounted_directory(path)
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("kind", choices=("research", "autovideo"))
    parser.add_argument("name")
    args = parser.parse_args()
    try:
        print(trial_directory(args.kind, args.name))
    except (OSError, ValueError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
