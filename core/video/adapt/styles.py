"""Script styles: a depth (how much to explain) and a tone (how to say it).

Configured in ``config/video_styles.yaml``. Each preset carries a version; a
script version records the presets it was written with (``explainer@1``), so
editing a preset never rewrites what an old version claims.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import yaml

STYLES_FILE = "video_styles.yaml"


class StyleError(ValueError):
    pass


@dataclass(frozen=True)
class Preset:
    key: str
    name: str
    version: int
    guidance: str
    example: str = ""
    audience: str = ""
    #: Depth only: new terms one section may introduce; 0 means no limit.
    max_new_terms_per_section: int = 0

    @property
    def ref(self) -> str:
        return f"{self.key}@{self.version}"


@dataclass(frozen=True)
class Style:
    depth: Preset
    tone: Preset

    @property
    def label(self) -> str:
        return f"{self.depth.name} · {self.tone.name}"

    def as_dict(self) -> dict:
        return {"depth": self.depth.key, "tone": self.tone.key, "label": self.label,
                "presets": [self.depth.ref, self.tone.ref]}


def _presets(raw: dict, kind: str) -> dict[str, Preset]:
    out: dict[str, Preset] = {}
    for key, item in (raw or {}).items():
        if not isinstance(item, dict) or not str(item.get("guidance") or "").strip():
            raise StyleError(f"{kind}.{key} 缺少 guidance")
        try:
            version = int(item.get("version", 1))
            max_terms = int(item.get("max_new_terms_per_section", 0) or 0)
        except (TypeError, ValueError) as exc:
            raise StyleError(f"{kind}.{key} 的 version 或 max_new_terms_per_section 不是整数") from exc
        out[key] = Preset(
            key=key,
            name=str(item.get("name") or key),
            version=version,
            guidance=str(item["guidance"]).strip(),
            example=str(item.get("example") or "").strip(),
            audience=str(item.get("audience") or "").strip(),
            max_new_terms_per_section=max_terms,
        )
    if not out:
        raise StyleError(f"{STYLES_FILE} 里没有 {kind}")
    return out


def load(config_dir: str) -> tuple[dict[str, Preset], dict[str, Preset]]:
    """(depths, tones)."""
    path = os.path.join(config_dir, STYLES_FILE)
    if not os.path.isfile(path):
        raise StyleError(f"缺少 {STYLES_FILE}")
    with open(path, encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    return _presets(data.get("depths"), "depths"), _presets(data.get("tones"), "tones")


def resolve(config_dir: str, column, depth: str | None = None, tone: str | None = None) -> Style:
    """The style for a script of ``column``; the column's default where not given."""
    depths, tones = load(config_dir)
    depth = depth or column.default_depth
    tone = tone or column.default_tone
    if depth not in depths:
        raise StyleError(f"没有这个深浅档位：{depth}")
    if tone not in tones:
        raise StyleError(f"没有这个语气：{tone}")
    if column.allowed_tones and tone not in column.allowed_tones:
        raise StyleError(f"栏目「{column.name}」不用「{tones[tone].name}」语气")
    return Style(depths[depth], tones[tone])


def catalog(config_dir: str) -> dict:
    """What the review page offers."""
    depths, tones = load(config_dir)
    return {
        "depths": [{"key": p.key, "name": p.name, "version": p.version, "audience": p.audience}
                   for p in depths.values()],
        "tones": [{"key": p.key, "name": p.name, "version": p.version, "guidance": p.guidance}
                  for p in tones.values()],
    }
