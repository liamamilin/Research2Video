"""The content matrix: domain x audience x content type x format (spec/video-matrix.md).

``config/video_matrix.yaml`` holds one definition per value of each dimension and a list of cells. A cell picks one
value from each dimension and is composed into the flat settings a column has always had (``columns.build`` checks
and turns them into a ``Column``), so everything that consumes a column is unchanged.

Precedence when two definitions set the same field: the type's setting for the format, then the format, then the
type, then the audience, then the domain. The audience limits the tones: a cell's allowed tones are the type's
that the audience allows.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Optional

import yaml

from . import columns

MATRIX_FILE = "video_matrix.yaml"
STATUSES = ("enabled", "experimental", "blocked")
DIMENSIONS = ("domain", "audience", "type", "format")
_SLUG = re.compile(r"[a-z0-9][a-z0-9_-]*")
#: what a type may say about one format, on top of the format's own settings
_OVERRIDES = {"target_minutes", "video_form", "voice_id", "source_card", "package", "writer", "sections",
              "paragraph_units"}


@dataclass(frozen=True)
class Cell:
    id: str
    status: str
    parts: dict                      # dimension -> {"key": ..., "version": ...}
    reason: str = ""                 # why a blocked cell cannot be used
    column: Optional[columns.Column] = field(default=None, compare=False)
    raw: dict = field(default_factory=dict, compare=False)   # the composed flat settings


def _mapping(data: dict, name: str) -> dict:
    value = data.get(name) or {}
    if not isinstance(value, dict):
        raise columns.ColumnError(f"矩阵的 {name} 必须是映射")
    for key, item in value.items():
        if not isinstance(key, str) or not _SLUG.fullmatch(key):
            raise columns.ColumnError(f"矩阵 {name} 的键 {key!r} 应为小写字母、数字、下划线、连字符")
        if not isinstance(item, dict) or not isinstance(item.get("version"), int) or item["version"] < 1:
            raise columns.ColumnError(f"矩阵 {name}.{key} 必须是映射，并带整数 version（从 1 起）")
    return value


def compose(cell_id: str, domain: dict, audience: dict, type_: dict, format_: dict, format_key: str) -> dict:
    """The flat column settings of one cell."""
    if format_key not in (type_.get("formats") or {}):
        raise columns.ColumnError(f"格子 {cell_id}：类型 {type_.get('name')} 没有形态 {format_key} 的定义")
    override = type_["formats"][format_key] or {}
    unknown = set(override) - _OVERRIDES
    if unknown:
        raise columns.ColumnError(f"格子 {cell_id}：类型对形态的设置不认 {', '.join(sorted(unknown))}")

    def pick(name: str, default=None):
        for source in (override, format_, type_, audience, domain):
            if name in source:
                return source[name]
        return default

    allowed_by_type = list(type_.get("allowed_tones") or [])
    allowed_by_audience = list(audience.get("tones") or [])
    if allowed_by_type and allowed_by_audience:
        tones = [t for t in allowed_by_type if t in allowed_by_audience]
    else:
        tones = allowed_by_type or allowed_by_audience
    if allowed_by_type and allowed_by_audience and not tones:
        raise columns.ColumnError(f"格子 {cell_id}：受众允许的语气和类型允许的语气没有交集")
    structure = {"required_roles": list(type_.get("required_roles") or [])}
    for name in ("sections", "paragraph_units"):
        if pick(name) is not None:
            structure[name] = pick(name)
    raw = {
        "name": type_.get("name"),
        "description": type_.get("description", ""),
        "target_minutes": pick("target_minutes", 6),
        "video_form": pick("video_form", "auto"),
        "research_timeout_seconds": type_.get("research_timeout_seconds", 3600),
        "writer": pick("writer", "legacy"),
        "voice_id": pick("voice_id", ""),
        "default_style": {"depth": audience.get("depth", "intermediate"), "tone": type_.get("default_tone", "explainer")},
        "allowed_tones": tones,
        "structure": structure,
        "research_prompt": type_.get("research_prompt", ""),
        "script_style": type_.get("script_style", ""),
    }
    for name in ("package", "source_card"):
        if pick(name) is not None:
            raw[name] = pick(name)
    return raw


def load(config_dir: str) -> dict[str, Cell]:
    """Every cell of ``video_matrix.yaml`` by its id; a malformed definition raises, naming it."""
    path = os.path.join(config_dir, MATRIX_FILE)
    if not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    defs = {name: _mapping(data, plural) for name, plural in
            (("domain", "domains"), ("audience", "audiences"), ("type", "types"), ("format", "formats"))}
    cells = data.get("cells") or []
    if not isinstance(cells, list):
        raise columns.ColumnError("矩阵的 cells 必须是列表")
    out: dict[str, Cell] = {}
    for entry in cells:
        if not isinstance(entry, dict):
            raise columns.ColumnError("矩阵的每个格子必须是映射")
        chosen = {dim: entry.get(dim) for dim in DIMENSIONS}
        for dim, value in chosen.items():
            if value not in defs[dim]:
                raise columns.ColumnError(f"格子 {chosen}：没有这个{dim}：{value}")
        cell_id = "-".join(chosen[dim] for dim in DIMENSIONS)
        if cell_id in out:
            raise columns.ColumnError(f"格子 {cell_id} 重复")
        status = str(entry.get("status", "experimental"))
        if status not in STATUSES:
            raise columns.ColumnError(f"格子 {cell_id} 的 status 应为 {', '.join(STATUSES)} 之一")
        reason = str(entry.get("reason") or "")
        if status == "blocked" and not reason:
            raise columns.ColumnError(f"格子 {cell_id} 是 blocked，要写 reason")
        key = str(entry.get("key") or cell_id)
        if not _SLUG.fullmatch(key):
            raise columns.ColumnError(f"格子 {cell_id} 的 key 应为小写字母、数字、下划线、连字符")
        parts = {dim: {"key": chosen[dim], "version": defs[dim][chosen[dim]]["version"]} for dim in DIMENSIONS}
        column, raw = None, {}
        if status != "blocked":
            raw = compose(cell_id, defs["domain"][chosen["domain"]], defs["audience"][chosen["audience"]],
                          defs["type"][chosen["type"]], defs["format"][chosen["format"]], chosen["format"])
            try:
                column = columns.build(key, raw)
            except columns.ColumnError as exc:
                raise columns.ColumnError(f"格子 {cell_id}：{exc}") from exc
        out[cell_id] = Cell(id=cell_id, status=status, parts=parts, reason=reason, column=column, raw=raw)
    return out


def provenance(config_dir: str, key: str) -> Optional[dict]:
    """Which cell (and which versions of its definitions) a column key resolved to; None for a flat column."""
    for cell in load(config_dir).values():
        if cell.id == key or (cell.column is not None and cell.column.key == key):
            return {"cell": cell.id, "status": cell.status, "parts": cell.parts}
    return None
