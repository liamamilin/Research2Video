"""Video columns (栏目), configured in ``config/video_columns.yaml``."""

from __future__ import annotations

import os
from dataclasses import dataclass

import yaml

from .length import MAX_MINUTES, MIN_MINUTES

COLUMNS_FILE = "video_columns.yaml"
VIDEO_FORMS = ("auto", "information_motion", "process_diagram")
#: legacy: one model call writes the script (core/video/writer.py).
#: adapt: the five-step adaptation (core/video/adapt/, spec/video-adaptation.md).
WRITERS = ("legacy", "adapt")
ROLES = ("钩子", "背景", "概念", "机制", "证据", "反方", "行动", "收束")


class ColumnError(ValueError):
    pass


@dataclass(frozen=True)
class Column:
    key: str
    name: str
    description: str
    target_minutes: float
    video_form: str
    research_prompt: str
    script_style: str
    research_timeout_seconds: int = 3600
    writer: str = "legacy"
    #: AutoVideo voice the column is rendered with, and the speech rate the length estimate assumes; empty means
    #: AutoVideo's own default.
    voice_id: str = ""
    default_depth: str = "intermediate"
    default_tone: str = "explainer"
    #: Tones a script of this column may be restyled to; empty means any.
    allowed_tones: tuple[str, ...] = ()
    required_roles: tuple[str, ...] = ()
    sections: tuple[int, int] = (4, 6)
    paragraph_units: tuple[int, int] = (60, 140)

    def as_dict(self) -> dict:
        return {
            "key": self.key,
            "name": self.name,
            "description": self.description,
            "target_minutes": self.target_minutes,
            "video_form": self.video_form,
            "writer": self.writer,
            "voice_id": self.voice_id,
            "default_style": {"depth": self.default_depth, "tone": self.default_tone},
            "allowed_tones": list(self.allowed_tones),
        }


def _pair(key: str, field: str, raw, default: tuple[int, int]) -> tuple[int, int]:
    if raw is None:
        return default
    try:
        low, high = (int(v) for v in raw)
    except (TypeError, ValueError) as exc:
        raise ColumnError(f"栏目 {key} 的 structure.{field} 应为 [最小, 最大]") from exc
    if not 0 < low <= high:
        raise ColumnError(f"栏目 {key} 的 structure.{field} 应满足 0 < 最小 ≤ 最大")
    return low, high


def load(config_dir: str) -> dict[str, Column]:
    """All configured columns. A malformed entry raises, naming the column."""
    path = os.path.join(config_dir, COLUMNS_FILE)
    if not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    columns: dict[str, Column] = {}
    for key, raw in data.items():
        if not isinstance(raw, dict):
            raise ColumnError(f"栏目 {key} 的配置必须是映射")
        try:
            minutes = float(raw.get("target_minutes", 6))
        except (TypeError, ValueError) as exc:
            raise ColumnError(f"栏目 {key} 的 target_minutes 不是数字") from exc
        minutes = int(minutes) if minutes == int(minutes) else minutes
        if not MIN_MINUTES <= minutes <= MAX_MINUTES:
            raise ColumnError(f"栏目 {key} 的 target_minutes 应在 {MIN_MINUTES}–{MAX_MINUTES} 之间")
        form = str(raw.get("video_form", "auto"))
        if form not in VIDEO_FORMS:
            raise ColumnError(f"栏目 {key} 的 video_form 应为 {', '.join(VIDEO_FORMS)} 之一")
        prompt = str(raw.get("research_prompt") or "").strip()
        if not prompt:
            raise ColumnError(f"栏目 {key} 缺少 research_prompt")
        writer = str(raw.get("writer", "legacy"))
        if writer not in WRITERS:
            raise ColumnError(f"栏目 {key} 的 writer 应为 {', '.join(WRITERS)} 之一")
        style = raw.get("default_style") or {}
        structure = raw.get("structure") or {}
        if not isinstance(style, dict) or not isinstance(structure, dict):
            raise ColumnError(f"栏目 {key} 的 default_style 和 structure 必须是映射")
        roles = tuple(str(r) for r in structure.get("required_roles") or ())
        unknown = [r for r in roles if r not in ROLES]
        if unknown:
            raise ColumnError(f"栏目 {key} 的 structure.required_roles 有未知角色：{', '.join(unknown)}")
        columns[key] = Column(
            key=key,
            name=str(raw.get("name") or key),
            description=str(raw.get("description") or ""),
            target_minutes=minutes,
            video_form=form,
            research_prompt=prompt,
            script_style=str(raw.get("script_style") or "").strip(),
            research_timeout_seconds=int(raw.get("research_timeout_seconds", 3600)),
            writer=writer,
            voice_id=str(raw.get("voice_id") or ""),
            default_depth=str(style.get("depth") or "intermediate"),
            default_tone=str(style.get("tone") or "explainer"),
            allowed_tones=tuple(str(t) for t in raw.get("allowed_tones") or ()),
            required_roles=roles,
            sections=_pair(key, "sections", structure.get("sections"), (4, 6)),
            paragraph_units=_pair(key, "paragraph_units", structure.get("paragraph_units"), (60, 140)),
        )
    return columns


def get(config_dir: str, key: str) -> Column:
    columns = load(config_dir)
    if key not in columns:
        raise ColumnError(f"没有这个栏目：{key}")
    return columns[key]
