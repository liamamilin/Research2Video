"""Stable project paths for task directories relocated by the storage setting."""
from __future__ import annotations

from pathlib import Path


def project_path(project: Path, path: Path) -> Path:
    """Return the project alias, allowing only configured top-level data links.

    Workers pass physical paths, while manifests use project-relative names.
    Resolve before matching so a link inside a task cannot escape its data root.
    """
    project = project.resolve()
    resolved = path.expanduser().resolve()
    if resolved.is_relative_to(project):
        return resolved
    data = project / "g3/data"
    if data.is_dir():
        for alias in sorted(data.iterdir()):
            if alias.is_symlink() and alias.is_dir():
                target = alias.resolve()
                if resolved.is_relative_to(target):
                    return alias / resolved.relative_to(target)
    raise ValueError("Path must remain inside the project or its configured video storage")
