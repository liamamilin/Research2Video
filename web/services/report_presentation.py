"""Readable titles for legacy reports whose index has no title; no index writes."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import re


@lru_cache(maxsize=512)
def _title(path: str, mtime_ns: int, size: int) -> str:
    # Auxiliary model text can precede the real heading. Bound the read and
    # cache by file version so normal list refreshes do not re-read documents.
    text = Path(path).read_text(encoding="utf-8", errors="replace") if size <= 65536 else ""
    if size > 65536:
        with open(path, encoding="utf-8", errors="replace") as handle:
            text = handle.read(65536)
    text = re.sub(r"^\s*<think>.*?</think>\s*", "", text, count=1, flags=re.S | re.I)
    fenced = False
    for line in text.splitlines():
        line = line.strip()
        if line.startswith(("```", "~~~")):
            fenced = not fenced
        elif not fenced and line.startswith("# "):
            return line[2:].strip()
    return ""


def fallback_title(path: str) -> str:
    """Read a fallback from an already-authorized local report path."""
    try:
        stat = Path(path).stat()
        return _title(path, stat.st_mtime_ns, stat.st_size)
    except OSError:
        return ""
