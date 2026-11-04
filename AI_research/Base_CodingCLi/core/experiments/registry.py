"""The switch registry (spec §6): the only settings an experiment's B arm may change.

Each switch says where it is written in a script's frozen ``content_context``, which files read it, when it is read
(before writing, so failures before writing count as missing; or from writing on), which scripts it can reach, how the
module tells from existing artifacts whether it was applied, and its default (today's behaviour).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Timing = Literal["before_writing", "from_writing"]
Reach = Literal["all", "with_column"]
Status = Literal["existing", "planned", "unconfirmed"]
#: How B is looked at before it goes live (§9a step ③): a real render, the two covers, or nothing (writing changes).
Preview = Literal["render", "cover", "none"]


@dataclass(frozen=True)
class Switch:
    path: str
    label: str
    readers: tuple[str, ...]
    timing: Timing
    reaches: Reach
    applied_by: str
    default: object
    status: Status
    #: A prefix switch accepts any sub-path, e.g. ``render`` covers ``render.packaging``.
    prefix: bool = False
    values: tuple = field(default=())
    note: str = ""
    preview: Preview = "none"


SWITCHES: tuple[Switch, ...] = (
    Switch("greeting", "问候语", ("core/video/episodes.py",), "from_writing", "with_column",
           "稿件开头是否是这句问候", "", "existing",
           note="episodes.greeting 没有 column 就返回空"),
    Switch("notes", "创作方向", ("core/video/content_columns.py", "core/video/research_dossier.py"),
           "before_writing", "with_column", "无法判断", "", "existing",
           note="研究和选题也读，写稿前的失败照算缺失"),
    Switch("presentation.strategy", "画面讲述方向", ("core/video/content_columns.py", "core/video/visual_materials.py"),
           "from_writing", "all", "无法判断", "", "existing", preview="render"),
    Switch("render", "出片参数", ("web/services/video_render.py", "core/video/content_columns.py"),
           "from_writing", "all", "读实际出片参数", None, "existing", prefix=True, preview="render",
           note="生产配置 config.render 优先级更高；开始前校验范围内没有生产配置设同一项"),
    Switch("greeting_position", "问候语位置", ("web/services/video_workflow.py",), "from_writing", "with_column",
           "稿件开头是否先讲要点", "before", "planned", values=("before", "after")),
    Switch("presentation.opening_card", "开头要点卡", ("core/video/visual_materials.py",), "from_writing", "all",
           "出片参数里有没有这张卡", False, "planned", values=(False, True), preview="render"),
    Switch("cover_style", "封面样式", ("web/services/publish_cover.py",), "from_writing", "all",
           "封面文字缓存里 copy 是否为空", "number", "planned", values=("number", "title"), preview="cover"),
    Switch("render.packaging", "片头片尾", ("web/services/video_render.py",), "from_writing", "all",
           "读实际出片参数", None, "unconfirmed", note="待确认现有配置能否单独关掉片头", preview="render"),
)

_BY_PATH = {s.path: s for s in SWITCHES}


def overlaps(a: str, b: str) -> bool:
    """Two write paths overlap when they are equal or one is a parent of the other."""
    return a == b or a.startswith(b + ".") or b.startswith(a + ".")


def find(path: str) -> Switch | None:
    """The registered switch that owns ``path``: an exact entry first, else the nearest prefix switch."""
    if path in _BY_PATH:
        return _BY_PATH[path]
    parts = path.split(".")
    for end in range(len(parts) - 1, 0, -1):
        owner = _BY_PATH.get(".".join(parts[:end]))
        if owner and owner.prefix:
            return owner
    return None


def as_rows() -> list[dict]:
    """The registry for the tab, in the spec's order."""
    return [{"path": s.path + (".*" if s.prefix else ""), "label": s.label, "readers": list(s.readers),
             "timing": s.timing, "reaches": s.reaches, "applied_by": s.applied_by, "status": s.status,
             "values": list(s.values), "note": s.note, "preview": s.preview} for s in SWITCHES]
