"""Bounded YAML parsing cache. Callers get independent values; edits take effect immediately."""

from __future__ import annotations

import copy
from functools import lru_cache
from typing import Any, TextIO

import yaml


@lru_cache(maxsize=128)
def _parse(text: str) -> Any:
    return yaml.safe_load(text)


def load_yaml(source: str | bytes | TextIO) -> Any:
    """Read fresh file text, cache only parsing, and never share mutable configuration."""
    text = source.read() if hasattr(source, "read") else source
    if not isinstance(text, str) or len(text) > 128_000:
        return yaml.safe_load(text)
    return copy.deepcopy(_parse(text))
