"""Video columns (栏目): the settings one script is made with.

Each column is a recipe file, ``config/video_recipes/<key>.yaml`` (core/video/recipes.py, spec/video-recipes.md);
``build`` turns a recipe's flat settings into a ``Column``, which everything downstream consumes."""

from __future__ import annotations

import re
from dataclasses import dataclass, field


from .length import MAX_MINUTES, MIN_MINUTES

#: The five-step adaptation needs room for 3–5 comprehension goals and a causal chain (spec/video-adaptation.md).
ADAPT_MIN_MINUTES = 3

VIDEO_FORMS = ("auto", "information_motion", "process_diagram")
#: What a script of the column is made from: one topic (single), several items of a discovery report cut into a
#: list video (roundup), or two subjects set against each other (comparison); see spec/video-matrix.md.
TOPIC_KINDS = ("single", "roundup", "comparison")
#: legacy: one model call writes the script (core/video/writer.py).
#: adapt: the five-step adaptation (core/video/adapt/, spec/video-adaptation.md).
WRITERS = ("legacy", "adapt")
#: job: each script runs its own research job; none: the topic brings the report it is written from (a roundup's
#: discovery report, or the research of the script it is cut from).
RESEARCH_MODES = ("job", "none")
#: Which model runs the fact audit: ``review`` uses ``review_ai`` in system.yaml when it is set, ``writer`` the
#: writing model.
AUDIT_MODELS = ("review", "writer")
SEVERITIES = ("must", "warn")
#: The topic fields each writer shows the model when the recipe names none (what they always showed).
DEFAULT_TOPIC_FIELDS = {
    "legacy": ("核心角度", "目标受众", "条目数", "条目", "对比对象", "对比维度"),
    "adapt": ("核心角度", "目标受众", "为什么有人在乎"),
}
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
    #: Who the column is for, as a short label for on-screen and prompt text; empty for a column with no audience.
    audience: str = ""
    #: The video forms a script of this column may use (``video_form`` is the default); see spec/video-matrix.md.
    allowed_forms: tuple[str, ...] = ()
    #: What AutoVideo is told about the video (``creative_intent``: purpose, audience, tone, focus); a script's
    #: own focus is added when it is rendered.
    creative_intent: dict = field(default_factory=dict)
    topic_kind: str = "single"
    #: A flash-news script is checked again just before it is published (a search for changes since it was written).
    recheck: bool = False
    research_mode: str = "job"
    research_language: str = "zh"
    #: Where topics for this column come from (``flash``, ``opportunities``); empty: picked by hand only.
    topic_sources: tuple[str, ...] = ()
    #: The topic fields the writer is shown, in order; empty: the writer's default (``DEFAULT_TOPIC_FIELDS``).
    topic_fields: tuple[str, ...] = ()
    audit_model: str = "review"
    #: How a style finding of the one-call writer's audit counts: ``must`` (fix before approving) or ``warn``.
    audit_style_severity: str = "must"
    #: Time limit of the one-call writer's audit call; 0 means the default (240 s, twice that from 3 minutes on).
    audit_timeout_seconds: int = 0
    #: Fix rounds of the five-step adaptation.
    revise_rounds: int = 2
    #: Stop the five-step adaptation once the plan is made, until the user confirms it (spec/video-steps.md).
    #: Never for a batch's scripts: a batch does not wait for the user.
    pause_after_plan: bool = False

    def writer_topic_fields(self) -> tuple[str, ...]:
        return self.topic_fields or DEFAULT_TOPIC_FIELDS[self.writer]

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
            "audience": self.audience,
            "allowed_forms": list(self.allowed_forms) or [self.video_form],
            "topic_kind": self.topic_kind,
            "recheck": self.recheck,
            "research_mode": self.research_mode,
            "topic_sources": list(self.topic_sources),
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


_INTENT_KEYS = ("purpose", "audience", "tone", "focus")


def _intent(key: str, raw) -> dict:
    if raw is None:
        return {}
    if not isinstance(raw, dict) or set(raw) - set(_INTENT_KEYS) or not all(isinstance(v, str) and v.strip() for v in raw.values()):
        raise ColumnError(f"栏目 {key} 的 creative_intent 只能有 {', '.join(_INTENT_KEYS)}，值是文字")
    return {k: v.strip() for k, v in raw.items()}


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


#: a topic source is any job that declares ``topics:`` (by its alias or path), or ``manual``; whether one exists is
#: checked against the jobs folder where it is used (core/video/topic_store.py), not here
_SOURCE_NAME = re.compile(r"[^\s{}\[\],]+")


def _names(key: str, name: str, raw, allowed: tuple[str, ...] = ()) -> tuple[str, ...]:
    """A list of names; a bare string is refused (``flash`` would otherwise become f, l, a, s, h)."""
    if raw is None:
        return ()
    if not isinstance(raw, (list, tuple)) or not all(isinstance(x, str) and x.strip() for x in raw):
        raise ColumnError(f"栏目 {key} 的 {name} 应写成列表，如 [a, b]")
    unknown = [x for x in raw if allowed and x not in allowed]
    if unknown:
        raise ColumnError(f"栏目 {key} 的 {name} 只能取 {', '.join(allowed)}，不认识：{', '.join(unknown)}")
    return tuple(x.strip() for x in raw)


def _source_names(key: str, raw) -> tuple[str, ...]:
    names = _names(key, "topic.sources", raw)
    bad = [n for n in names if not _SOURCE_NAME.fullmatch(n)]
    if bad:
        raise ColumnError(f"栏目 {key} 的 topic.sources 写的应是 Job 的 topics.alias 或 Job 路径，不认识：{', '.join(bad)}")
    return names


def build(key: str, raw: dict) -> Column:
    """One column from its flat settings (``recipes.to_flat`` of a recipe file)."""
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
    topic_kind = str(raw.get("topic_kind") or "single")
    if topic_kind not in TOPIC_KINDS:
        raise ColumnError(f"栏目 {key} 的 topic_kind 应为 {', '.join(TOPIC_KINDS)} 之一")
    prompt = str(raw.get("research_prompt") or "").strip()
    research_mode = str(raw.get("research_mode") or ("none" if topic_kind == "roundup" else "job"))
    if research_mode not in RESEARCH_MODES:
        raise ColumnError(f"栏目 {key} 的 research.mode 应为 {', '.join(RESEARCH_MODES)} 之一")
    if not prompt and research_mode == "job":
        raise ColumnError(f"栏目 {key} 缺少 research_prompt（research.mode 是 job 时要写调研提示词）")
    writer = str(raw.get("writer", "legacy"))
    if writer not in WRITERS:
        raise ColumnError(f"栏目 {key} 的 writer 应为 {', '.join(WRITERS)} 之一")
    style = raw.get("default_style") or {}
    structure = raw.get("structure") or {}
    if not isinstance(style, dict) or not isinstance(structure, dict):
        raise ColumnError(f"栏目 {key} 的 default_style 和 structure 必须是映射")
    roles = _names(key, "required_roles", structure.get("required_roles"))
    unknown = [r for r in roles if r not in ROLES]
    if unknown:
        raise ColumnError(f"栏目 {key} 的 structure.required_roles 有未知角色：{', '.join(unknown)}")
    audit = raw.get("audit") or {}
    if not isinstance(audit, dict) or set(audit) - {"model", "style_severity", "timeout_seconds"}:
        raise ColumnError(f"栏目 {key} 的 audit 只能有 model、style_severity、timeout_seconds")
    audit_model = str(audit.get("model") or "review")
    if audit_model not in AUDIT_MODELS:
        raise ColumnError(f"栏目 {key} 的 audit.model 应为 {', '.join(AUDIT_MODELS)} 之一")
    severity = str(audit.get("style_severity") or "must")
    if severity not in SEVERITIES:
        raise ColumnError(f"栏目 {key} 的 audit.style_severity 应为 {', '.join(SEVERITIES)} 之一")
    try:
        audit_timeout = int(audit.get("timeout_seconds") or 0)
        rounds = int(raw.get("revise_rounds", 2))
    except (TypeError, ValueError) as exc:
        raise ColumnError(f"栏目 {key} 的 audit.timeout_seconds 和 revise_rounds 应为整数") from exc
    if audit_timeout < 0 or not 0 <= rounds <= 5:
        raise ColumnError(f"栏目 {key}：audit.timeout_seconds 不能为负，revise_rounds 应在 0–5 之间")
    pause = _flag(key, "pause_after_plan", raw.get("pause_after_plan"))
    if pause and writer != "adapt":
        raise ColumnError(f"栏目 {key}：pause_after_plan 只用于五步改编（writing.pipeline: adapt），一次写成没有方案这一步")
    if writer == "adapt" and minutes < ADAPT_MIN_MINUTES:
        raise ColumnError(f"栏目 {key}：五步改编（adapt）是给 {ADAPT_MIN_MINUTES} 分钟以上的稿子设计的，"
                          f"{minutes} 分钟的稿子用 single")
    topic_fields = raw.get("topic_fields") or ()
    if not isinstance(topic_fields, (list, tuple)) or not all(isinstance(f, str) and f.strip() for f in topic_fields):
        raise ColumnError(f"栏目 {key} 的 topic_fields 应为字段名列表")
    forms = _names(key, "allowed_forms", raw.get("allowed_forms")) or (form,)
    if form not in forms or set(forms) - set(VIDEO_FORMS):
        raise ColumnError(f"栏目 {key} 的 allowed_forms 应是 {', '.join(VIDEO_FORMS)} 里的，并包含默认的 {form}")
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
        allowed_tones=_names(key, "allowed_tones", raw.get("allowed_tones")),
        required_roles=roles,
        sections=_pair(key, "sections", structure.get("sections"), (4, 6)),
        paragraph_units=_pair(key, "paragraph_units", structure.get("paragraph_units"), (60, 140)),
        package=_package(key, raw.get("package")),
        source_card=_flag(key, "source_card", raw.get("source_card")),
        audience=str(raw.get("audience") or ""),
        allowed_forms=forms,
        creative_intent=_intent(key, raw.get("creative_intent")),
        topic_kind=topic_kind,
        recheck=_flag(key, "recheck", raw.get("recheck")),
        research_mode=research_mode,
        research_language=str(raw.get("research_language") or "zh"),
        topic_sources=_source_names(key, raw.get("topic_sources")),
        topic_fields=tuple(f.strip() for f in topic_fields),
        audit_model=audit_model,
        audit_style_severity=severity,
        audit_timeout_seconds=audit_timeout,
        revise_rounds=rounds,
        pause_after_plan=pause,
    )


def load(config_dir: str) -> dict[str, Column]:
    """Every column new scripts can be made with, by key: one per recipe file and audience that builds and is not
    blocked.

    A recipe that does not build is left out (``recipes.load`` reports it), so one bad file cannot stop the rest."""
    from . import recipes

    return {key: r.column for key, r in recipes.expanded(config_dir).items() if r.usable}


def get(config_dir: str, key: str) -> Column:
    """A column by its key, or by the cell id its tags spell (``ai-developer-quick_take-short`` is ``flash``).

    A blocked recipe still resolves, so the scripts already made with it can be read, approved and rendered;
    ``for_new_script`` is the check for starting one."""
    from . import recipes

    recipe = recipes.find(config_dir, key)
    if recipe is None:
        raise ColumnError(f"没有这个栏目：{key}")
    if recipe.column is None:
        raise ColumnError(f"配方 {recipe.key} 有错误：{recipe.error}")
    return recipe.column


def for_new_script(config_dir: str, key: str) -> Column:
    """The column a new script is made with; a blocked recipe makes none."""
    from . import recipes

    column = get(config_dir, key)
    recipe = recipes.find(config_dir, key)
    if recipe is not None and recipe.status == "blocked":
        raise ColumnError(f"配方 {recipe.key} 已停用：{recipe.reason}")
    return column


def provenance(config_dir: str, key: str):
    """Which recipe (and which content of it) a column key resolved to, recorded in a script version's meta."""
    from . import recipes

    return recipes.provenance(config_dir, key)
