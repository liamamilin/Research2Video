"""Video columns (栏目), configured in ``config/video_columns.yaml``."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

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
    #: The non-content parts of a video (intro, outro, accent colour), see ``package`` in spec/video-short-forms.md.
    #: Empty means the video is the content alone.
    package: dict = field(default_factory=dict)
    #: End the video on a source card listing what the script cites (AutoVideo ``sources``).
    source_card: bool = False

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
            "package": self.package,
            "source_card": self.source_card,
        }


#: part -> template names AutoVideo knows for it
PACKAGE_TEMPLATES = {"intro": ("stinger",), "outro": ("end_card",)}
PACKAGE_SECONDS = (0.5, 5.0)
_PART_KEYS = {"template", "title", "subtitle", "lines", "seconds", "sound"}
_SOUND_ID = re.compile(r"[a-z][a-z0-9_]{0,31}")
_ACCENT = re.compile(r"#[0-9a-fA-F]{6}")


def _text(key: str, part: str, name: str, value, limit: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
        raise ColumnError(f"栏目 {key} 的 package.{part}.{name} 应为 1–{limit} 个字的文字")
    return value.strip()


def _package(key: str, raw) -> dict:
    """The validated ``package`` block: {"intro": {...}, "outro": {...}, "accent": "#rrggbb", "badge": "快讯"}, each optional.

    The badge is the column's mark in the corner of the content picture.

    A subtitle may contain ``{date}``, filled with the date of the topic when the render is made."""
    if raw is None:
        return {}
    if not isinstance(raw, dict) or set(raw) - {"intro", "outro", "accent", "badge"}:
        raise ColumnError(f"栏目 {key} 的 package 只能有 intro、outro、accent、badge")
    out: dict = {}
    if "accent" in raw:
        if not isinstance(raw["accent"], str) or not _ACCENT.fullmatch(raw["accent"]):
            raise ColumnError(f"栏目 {key} 的 package.accent 应为 #rrggbb")
        out["accent"] = raw["accent"]
    if "badge" in raw:
        out["badge"] = _text(key, "badge", "badge", raw["badge"], 12)
    for part, templates in PACKAGE_TEMPLATES.items():
        if part not in raw:
            continue
        spec = raw[part]
        if not isinstance(spec, dict) or set(spec) - _PART_KEYS:
            raise ColumnError(f"栏目 {key} 的 package.{part} 只能有 {', '.join(sorted(_PART_KEYS))}")
        if spec.get("template") not in templates:
            raise ColumnError(f"栏目 {key} 的 package.{part}.template 应为 {', '.join(templates)} 之一")
        item: dict = {"template": spec["template"], "title": _text(key, part, "title", spec.get("title"), 12)}
        if "subtitle" in spec:
            item["subtitle"] = _text(key, part, "subtitle", spec["subtitle"], 24)
        if "lines" in spec:
            lines = spec["lines"]
            if not isinstance(lines, list) or not 1 <= len(lines) <= 3:
                raise ColumnError(f"栏目 {key} 的 package.{part}.lines 应为 1–3 行文字")
            item["lines"] = [_text(key, part, "lines", line, 30) for line in lines]
        try:
            seconds = float(spec.get("seconds", 2))
        except (TypeError, ValueError) as exc:
            raise ColumnError(f"栏目 {key} 的 package.{part}.seconds 不是数字") from exc
        if not PACKAGE_SECONDS[0] <= seconds <= PACKAGE_SECONDS[1]:
            raise ColumnError(f"栏目 {key} 的 package.{part}.seconds 应在 {PACKAGE_SECONDS[0]}–{PACKAGE_SECONDS[1]} 之间")
        item["seconds"] = int(seconds) if seconds == int(seconds) else seconds
        if "sound" in spec:
            if not isinstance(spec["sound"], str) or not _SOUND_ID.fullmatch(spec["sound"]):
                raise ColumnError(f"栏目 {key} 的 package.{part}.sound 应为小写字母、数字、下划线组成的名字")
            item["sound"] = spec["sound"]
        out[part] = item
    return out


def _flag(key: str, name: str, raw) -> bool:
    if raw is None:
        return False
    if not isinstance(raw, bool):
        raise ColumnError(f"栏目 {key} 的 {name} 应为 true 或 false")
    return raw


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


def build(key: str, raw: dict) -> Column:
    """One column from its flat settings, as written in ``video_columns.yaml`` or composed from a matrix cell."""
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
    return Column(
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
        package=_package(key, raw.get("package")),
        source_card=_flag(key, "source_card", raw.get("source_card")),
    )


def _flat(config_dir: str) -> dict[str, Column]:
    path = os.path.join(config_dir, COLUMNS_FILE)
    if not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return {key: build(key, raw) for key, raw in data.items()}


def load(config_dir: str) -> dict[str, Column]:
    """All configured columns: the flat ones in ``video_columns.yaml`` and the cells of ``video_matrix.yaml``.

    A malformed entry raises, naming the column. A cell is listed under its column key (the old name when it has
    one); ``get`` also takes the cell's own id."""
    from . import matrix

    columns = _flat(config_dir)
    for cell in matrix.load(config_dir).values():
        if cell.status == "blocked":
            continue
        if cell.column.key in columns:
            raise ColumnError(f"栏目 {cell.column.key} 同时出现在 {COLUMNS_FILE} 和矩阵里")
        columns[cell.column.key] = cell.column
    return columns


def get(config_dir: str, key: str) -> Column:
    from . import matrix

    columns = load(config_dir)
    if key not in columns:
        cell = matrix.load(config_dir).get(key)
        if cell is not None and cell.status == "blocked":
            raise ColumnError(f"格子 {key} 暂不能用：{cell.reason}")
        if cell is None or cell.column.key not in columns:
            raise ColumnError(f"没有这个栏目：{key}")
        return columns[cell.column.key]
    return columns[key]


def provenance(config_dir: str, key: str):
    """The matrix cell and definition versions a column key resolved to (recorded in a script version's meta)."""
    from . import matrix

    return matrix.provenance(config_dir, key)
