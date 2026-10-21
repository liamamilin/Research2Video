"""Video script recipes: one YAML file per recipe, ``config/video_recipes/<key>.yaml`` (spec/video-recipes.md).

A recipe says how one kind of script is made, stage by stage: how it is researched, how it is
written and checked, what it is rendered into, and which topic sources the autopilot picks from for it (a
script made by hand may take any topic). Like a job file, what the file says is what runs:
there is no inheritance, and a new recipe starts as a copy of an existing one.

``load`` reads every file; a file that does not build is reported on its own entry (``error``) and left out of the
usable columns, so one bad file cannot stop the others. ``to_flat`` turns a recipe into the flat settings
``columns.build`` checks and turns into a ``Column``.

A recipe may take two of its sections from shared files instead of writing them out (``resolve``):

* ``research: {template: <name>}`` — a research template, ``jobs/_templates/video_research/<name>.yaml``: a job file
  (``language``, ``runtime.timeout_seconds``, ``prompt``) whose prompt has topic placeholders; each script's research
  job is made from it. A research method is written once and any recipe can use it.
* ``output: {use: <name>}`` / ``writing: {use: <name>}`` — a part, ``config/video_parts/<section>/<name>.yaml``, with
  that section's fields.

What the recipe itself writes in that section wins over the shared file (one level deep). A script version records
which files were used and the hash of the settings they came to (``provenance``).

A recipe may also leave out who it is for and offer several audiences (``audiences: [developer, non_tech]``): each
is a profile, ``config/video_parts/audience/<key>.yaml``, and the recipe's text says where the profile's words go
(``{{audience}}`` is the profile's name, ``{{audience.depth}}`` its depth, ``{{audience.<snippet>}}`` a snippet from
its ``text:``). Every audience is its own column: the first is the recipe's own key, the others are named by the
cell their tags spell with that audience (``ai-non_tech-quick_take-short``), so everything that looks a column up
by key keeps working (``expanded``).

A recipe whose text does not name a length may declare ``minutes_range: [min, max]``: a production config can then
ask for another length in half-minute steps within it. That too is a column of its own, named by the column it
changes and the length (``deep_dive-m8``, ``deep_dive-m7p5``); ``find`` builds it on demand, since there is no
list of every length (``minutes_key``).

A recipe whose item is already verified by the report it comes from may say ``research: {optional: true}``: a config
can then turn the research off, and the script is written from that item's part of the report alone
(``excerpt.py``). That is another column too, with ``-nr`` after its name (``flash-nr``, ``flash-m2-nr``); every
such column is the original but for what it is written from, and its length if one was asked for.
"""

from __future__ import annotations

import contextlib
import contextvars
import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from typing import Optional

import yaml

from . import columns

RECIPES_DIR = "video_recipes"
STATUSES = ("enabled", "experimental", "blocked")
#: the tags that place a recipe in the topic page's grid (type x audience); none of them changes what runs
DIMENSIONS = ("domain", "audience", "type", "format")
PIPELINES = {"single": "legacy", "adapt": "adapt"}
OUTPUT_TARGETS = ("autovideo",)
KEY = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")

_TOP = {"name", "description", "status", "reason", "label", "tags", "audience", "audiences", "target_minutes", "minutes_range",
        "topic", "research", "writing", "output"}
_SECTIONS = {
    "topic": {"kind", "sources", "fields", "whole_report"},
    "research": {"mode", "timeout_seconds", "language", "prompt", "optional"},
    "writing": {"pipeline", "style", "sections", "paragraph_units", "required_roles", "default_style",
                "allowed_tones", "revise_rounds", "pause_after_plan", "audit"},
    "output": {"target", "voice_id", "video_form", "allowed_forms", "creative_intent", "source_card", "package",
               "recheck"},
}
#: sections that may name a shared part (``use``), and where research templates live under the jobs folder
PART_SECTIONS = ("writing", "output")
PARTS_DIR = "video_parts"
AUDIENCE_DIR = "audience"
_AUDIENCE_FIELDS = {"name", "status", "depth", "creative_intent", "text"}
_STRAY = re.compile(r"\{\{\s*audien")  # anything that looks like a token and was not filled
_TOKEN = re.compile(r"\{\{\s*audience(?:\.([a-z][a-z0-9_]*))?\s*\}\}")
RESEARCH_TEMPLATES = os.path.join("_templates", "video_research")


@dataclass(frozen=True)
class Recipe:
    key: str
    path: str
    sha256: str
    status: str = "experimental"
    reason: str = ""
    label: str = ""
    tags: dict = field(default_factory=dict)      # dimension -> {"key": ..., "name": ...}
    column: Optional[columns.Column] = None       # None when the file does not build
    error: str = ""                               # why the file does not build
    data: dict = field(default_factory=dict, compare=False)
    #: the shared files it took sections from: section -> {"kind", "name", "path", "sha256"}
    uses: dict = field(default_factory=dict, compare=False)
    #: hash of the settings after the shared files were put in (what actually runs)
    resolved_sha256: str = ""
    #: the audience profile it was made for ("" when the recipe names none), every audience its file offers (the
    #: first is the recipe's own), and the key of the file it comes from
    audience: str = ""
    audiences: tuple = ()
    base: str = ""
    #: the lengths (minutes) a config may ask for instead of the recipe's own; empty: the length is not for sale
    minutes_range: tuple = ()
    #: set on a column made for another length than its recipe's
    minutes: float = 0.0
    #: a config may turn the research off (``research.optional``); set on the column made that way: ``no_research``
    research_optional: bool = False
    no_research: bool = False
    #: ``topic.whole_report``: it may be written from a whole report as it is (a Job whose reports are no item lists)
    whole_report: bool = False

    @property
    def cell_id(self) -> str:
        """The four tags joined (the id the content matrix gave this cell), or the key when a tag is missing."""
        if all(dim in self.tags for dim in DIMENSIONS):
            return "-".join(self.tags[dim]["key"] for dim in DIMENSIONS)
        return self.key

    @property
    def usable(self) -> bool:
        """New scripts can be made with it: it builds and is not blocked."""
        return self.column is not None and self.status != "blocked"


def recipes_dir(config_dir: str) -> str:
    return os.path.join(config_dir, RECIPES_DIR)


def path_for(config_dir: str, key: str) -> str:
    if not KEY.fullmatch(key or ""):
        raise columns.ColumnError(f"配方名 {key!r} 只能用小写字母、数字、下划线、连字符（最多 64 个字符）")
    return os.path.join(recipes_dir(config_dir), f"{key}.yaml")


def _mapping(key: str, name: str, value) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise columns.ColumnError(f"配方 {key} 的 {name} 必须是映射")
    return value


def _tags(key: str, raw) -> dict:
    tags = {}
    for dim, value in _mapping(key, "tags", raw).items():
        if dim not in DIMENSIONS:
            raise columns.ColumnError(f"配方 {key} 的 tags 只能有 {', '.join(DIMENSIONS)}")
        if isinstance(value, str):
            value = {"key": value}
        if not isinstance(value, dict) or not isinstance(value.get("key"), str) or not KEY.fullmatch(value["key"]):
            raise columns.ColumnError(f"配方 {key} 的 tags.{dim} 应为 {{key: 小写标识, name: 显示名}}")
        tags[dim] = {"key": value["key"], "name": str(value.get("name") or value["key"])}
    return tags


def jobs_dir_for(config_dir: str) -> str:
    """The jobs folder next to the config folder (where research templates live)."""
    return os.path.join(os.path.dirname(os.path.abspath(config_dir)), "jobs")


def research_template_path(config_dir: str, name: str) -> str:
    if not KEY.fullmatch(name or ""):
        raise columns.ColumnError(f"调研模板名 {name!r} 只能用小写字母、数字、下划线、连字符")
    return os.path.join(jobs_dir_for(config_dir), RESEARCH_TEMPLATES, f"{name}.yaml")


def part_path(config_dir: str, section: str, name: str) -> str:
    if section not in PART_SECTIONS:
        raise columns.ColumnError(f"只有 {', '.join(PART_SECTIONS)} 能引用部件")
    if not KEY.fullmatch(name or ""):
        raise columns.ColumnError(f"部件名 {name!r} 只能用小写字母、数字、下划线、连字符")
    return os.path.join(config_dir, PARTS_DIR, section, f"{name}.yaml")


def audience_path(config_dir: str, name: str) -> str:
    if not KEY.fullmatch(name or ""):
        raise columns.ColumnError(f"受众名 {name!r} 只能用小写字母、数字、下划线、连字符")
    return os.path.join(config_dir, PARTS_DIR, AUDIENCE_DIR, f"{name}.yaml")


#: file text to use instead of what is on disk, while checking an edit to a shared part (``trying``)
_TRYING: contextvars.ContextVar[dict] = contextvars.ContextVar("recipe_parts_trying", default={})


@contextlib.contextmanager
def trying(path: str, text: str):
    """Within the block, the part at ``path`` reads as ``text``: to see what an edit would do to the recipes using it."""
    token = _TRYING.set({**_TRYING.get(), os.path.abspath(path): text})
    try:
        yield
    finally:
        _TRYING.reset(token)


def _read_mapping(path: str, what: str) -> tuple[dict, str]:
    text = _TRYING.get().get(os.path.abspath(path))
    if text is None:
        try:
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
        except OSError:
            raise columns.ColumnError(f"找不到{what}：{path}") from None
    data = yaml.safe_load(text) or {}
    if not isinstance(data, dict):
        raise columns.ColumnError(f"{what}的根必须是映射：{path}")
    return data, hashlib.sha256(text.encode("utf-8")).hexdigest()


def research_from_template(config_dir: str, name: str) -> tuple[dict, dict]:
    """A research template's settings in the recipe's ``research`` shape, and where they came from."""
    path = research_template_path(config_dir, name)
    data, sha = _read_mapping(path, f"调研模板 {name}")
    runtime = data.get("runtime") or {}
    research = {"mode": "job", "timeout_seconds": runtime.get("timeout_seconds"), "language": data.get("language"),
                "prompt": data.get("prompt")}
    if not str(research["prompt"] or "").strip():
        raise columns.ColumnError(f"调研模板 {name} 没有 prompt")
    return ({k: v for k, v in research.items() if v is not None},
            {"kind": "research_template", "name": name, "path": path, "sha256": sha})


def audience_profile(config_dir: str, name: str) -> tuple[dict, dict]:
    """An audience profile (``config/video_parts/audience/<name>.yaml``) and where it came from."""
    path = audience_path(config_dir, name)
    data, sha = _read_mapping(path, f"受众 {name}")
    extra = set(data) - _AUDIENCE_FIELDS
    if extra:
        raise columns.ColumnError(f"受众 {name} 不认识这些字段：{', '.join(sorted(extra))}")
    if not str(data.get("name") or "").strip():
        raise columns.ColumnError(f"受众 {name} 要写 name（显示名）")
    if data.get("status", "enabled") not in ("enabled", "experimental"):
        raise columns.ColumnError(f"受众 {name} 的 status 应为 enabled、experimental 之一")
    if data.get("creative_intent") is not None and not isinstance(data["creative_intent"], dict):
        raise columns.ColumnError(f"受众 {name} 的 creative_intent 必须是映射")
    text = data.get("text")
    if text is not None and (not isinstance(text, dict) or not all(
            isinstance(k, str) and re.fullmatch(r"[a-z][a-z0-9_]*", k) and isinstance(v, str) for k, v in text.items())):
        raise columns.ColumnError(f"受众 {name} 的 text 应是 {{小写名: 文字}}")
    return data, {"kind": "audience", "name": name, "path": path, "sha256": sha}


def _fill(text: str, values: dict, where: str) -> str:
    """Put the audience's words in. A line that holds only a token takes the value's lines, and goes away when the
    value is empty (an audience with nothing to add leaves no blank line); a token inside a line is replaced."""
    def word(match) -> str:
        field = match.group(1)
        if field is None:
            return values["name"]
        if field not in values:
            raise columns.ColumnError(f"{where} 用了 {{{{audience.{field}}}}}，这个受众没写 {field}")
        return str(values[field])

    out = []
    for line in text.splitlines(keepends=True):
        whole = _TOKEN.fullmatch(line.strip())
        if whole and whole.group(1) is not None:
            value = word(whole)
            if value.strip():
                out.append(value.rstrip("\n") + ("\n" if line.endswith("\n") else ""))
            continue
        out.append(_TOKEN.sub(word, line))
    return "".join(out)


def _fill_tree(node, values: dict, where: str):
    if isinstance(node, str):
        return _fill(node, values, where)
    if isinstance(node, dict):
        return {k: _fill_tree(v, values, where) for k, v in node.items()}
    if isinstance(node, list):
        return [_fill_tree(v, values, where) for v in node]
    return node


def _strings(node):
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for v in node.values():
            yield from _strings(v)
    elif isinstance(node, list):
        for v in node:
            yield from _strings(v)


def resolve(key: str, data: dict, config_dir: Optional[str], audience: Optional[str] = None) -> tuple[dict, dict]:
    """The recipe with its shared sections put in: research templates and parts, the recipe's own fields winning;
    with ``audience``, that profile's words put into the text."""
    if not isinstance(data, dict):
        raise columns.ColumnError(f"配方 {key} 的根必须是映射")
    out = dict(data)
    uses: dict = {}
    profile: dict = {}
    if audience:
        if config_dir is None:
            raise columns.ColumnError(f"配方 {key} 有受众，要知道 config 目录才能读")
        profile, uses["audience"] = audience_profile(config_dir, audience)
        if profile.get("creative_intent"):  # the audience's intent stands in for the part's; the recipe's own wins
            out["output"] = {"creative_intent": profile["creative_intent"], **(out.get("output") or {})}
    research = data.get("research")
    if isinstance(research, dict) and "template" in research:
        if config_dir is None:
            raise columns.ColumnError(f"配方 {key} 引用了调研模板，要知道 config 目录才能读")
        base, uses["research"] = research_from_template(config_dir, str(research["template"]))
        out["research"] = {**base, **{k: v for k, v in research.items() if k != "template"}}
    for section in PART_SECTIONS:
        own = out.get(section)
        if isinstance(own, dict) and "use" in own:
            if config_dir is None:
                raise columns.ColumnError(f"配方 {key} 引用了部件，要知道 config 目录才能读")
            path = part_path(config_dir, section, str(own["use"]))
            base, sha = _read_mapping(path, f"部件 {section}/{own['use']}")
            extra = set(base) - _SECTIONS[section]
            if extra:
                raise columns.ColumnError(f"部件 {section}/{own['use']} 不认识这些字段：{', '.join(sorted(extra))}")
            uses[section] = {"kind": "part", "name": str(own["use"]), "path": path, "sha256": sha}
            out[section] = {**base, **{k: v for k, v in own.items() if k != "use"}}
    if audience:
        values = {**(profile.get("text") or {}), "name": profile["name"], "key": audience}
        if profile.get("depth") is not None:
            values["depth"] = str(profile["depth"])
        out = _fill_tree(out, values, f"配方 {key}")
        out["audience"] = profile["name"]
        out["tags"] = {**(out.get("tags") or {}), "audience": {"key": audience, "name": profile["name"]}}
        if profile.get("status") == "experimental" and out.get("status", "experimental") == "enabled":
            out["status"] = "experimental"  # a recipe is no more settled than the audience it is written for
    if any(_STRAY.search(text) for text in _strings(out)):
        raise columns.ColumnError(
            f"配方 {key} 里有没填的 {{{{audience}}}}：" + ("写了 audiences 才会填。" if not audience else "写错了名字？"))
    return out, uses


def to_flat(key: str, data: dict) -> dict:
    """A recipe's settings in the flat shape ``columns.build`` takes. Unknown fields raise (a typo must not be lost)."""
    if not isinstance(data, dict):
        raise columns.ColumnError(f"配方 {key} 的根必须是映射")
    unknown = set(data) - _TOP
    if unknown:
        raise columns.ColumnError(f"配方 {key} 不认识这些字段：{', '.join(sorted(unknown))}")
    parts = {name: _mapping(key, name, data.get(name)) for name in _SECTIONS}
    for name, allowed in _SECTIONS.items():
        extra = set(parts[name]) - allowed
        if extra:
            raise columns.ColumnError(f"配方 {key} 的 {name} 不认识这些字段：{', '.join(sorted(extra))}")
    topic, research, writing, output = parts["topic"], parts["research"], parts["writing"], parts["output"]

    pipeline = str(writing.get("pipeline") or "single")
    if pipeline not in PIPELINES:
        raise columns.ColumnError(f"配方 {key} 的 writing.pipeline 应为 {', '.join(PIPELINES)} 之一")
    target = str(output.get("target") or "autovideo")
    if target not in OUTPUT_TARGETS:
        raise columns.ColumnError(f"配方 {key} 的 output.target 现在只能是 {', '.join(OUTPUT_TARGETS)}")

    flat: dict = {"writer": PIPELINES[pipeline]}
    plain = {  # flat name -> value in the recipe
        "name": data.get("name"),
        "description": data.get("description"),
        "audience": data.get("audience"),
        "target_minutes": data.get("target_minutes"),
        "topic_kind": topic.get("kind"),
        "topic_sources": topic.get("sources"),
        "topic_fields": topic.get("fields"),
        "research_mode": research.get("mode"),
        "research_timeout_seconds": research.get("timeout_seconds"),
        "research_language": research.get("language"),
        "research_prompt": research.get("prompt"),
        "script_style": writing.get("style"),
        "default_style": writing.get("default_style"),
        "allowed_tones": writing.get("allowed_tones"),
        "revise_rounds": writing.get("revise_rounds"),
        "pause_after_plan": writing.get("pause_after_plan"),
        "audit": writing.get("audit"),
        "voice_id": output.get("voice_id"),
        "video_form": output.get("video_form"),
        "allowed_forms": output.get("allowed_forms"),
        "creative_intent": output.get("creative_intent"),
        "source_card": output.get("source_card"),
        "package": output.get("package"),
        "recheck": output.get("recheck"),
    }
    flat.update({k: v for k, v in plain.items() if v is not None})
    structure = {k: writing[k] for k in ("sections", "paragraph_units", "required_roles") if writing.get(k) is not None}
    if structure:
        flat["structure"] = structure
    return flat


def _offered(data) -> tuple[str, ...]:
    """The audiences a recipe's file offers (the first is the recipe's own); empty when it names none."""
    raw = data.get("audiences") if isinstance(data, dict) else None
    if raw is None:
        return ()
    if not isinstance(raw, list) or not raw or not all(isinstance(a, str) and KEY.fullmatch(a) for a in raw) \
            or len(set(raw)) != len(raw):
        raise columns.ColumnError("audiences 应是受众名的列表，不能重复，例如 [developer, non_tech]")
    return tuple(raw)


def _range(key: str, data: dict) -> tuple:
    raw = data.get("minutes_range")
    if raw is None:
        return ()
    ok = (isinstance(raw, list) and len(raw) == 2 and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in raw)
          and all(v * 2 == int(v * 2) for v in raw) and raw[0] <= raw[1])
    if not ok:
        raise columns.ColumnError(f"配方 {key} 的 minutes_range 应是 [最短, 最长]，分钟数，半分钟一档，例如 [4, 8]")
    return (float(raw[0]), float(raw[1]))


def parse(key: str, text: str, path: str = "", config_dir: Optional[str] = None, audience: Optional[str] = None,
          base: str = "", minutes: Optional[float] = None, no_research: bool = False) -> Recipe:
    """One recipe from its file's text. Never raises for bad content: the problem goes in ``error``.

    ``config_dir`` is where its shared parts are looked up; without it a recipe that uses one does not build.
    ``audience`` picks which of the audiences the file offers it is built for (the first when left out); ``minutes``
    builds it for another length, which the recipe has to allow (``minutes_range``), and ``no_research`` without the
    research, which it has to allow too (``research.optional``)."""
    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    uses: dict = {}
    offered: tuple[str, ...] = ()
    try:
        data = yaml.safe_load(text) or {}
        if not isinstance(data, dict):
            raise columns.ColumnError(f"配方 {key} 的根必须是映射")
        written = data
        offered = _offered(written)
        if offered and "audience" in written:
            raise columns.ColumnError(f"配方 {key} 写了 audiences，就不要再写 audience：给谁看由受众定")
        chosen = ""
        if offered:
            chosen = audience or offered[0]
            if chosen not in offered:
                raise columns.ColumnError(f"配方 {key} 没有 {chosen} 这个受众")
        elif audience:
            raise columns.ColumnError(f"配方 {key} 没有写 audiences")
        data, uses = resolve(key, written, config_dir, chosen or None)
        status = str(data.get("status") or "experimental")
        if status not in STATUSES:
            raise columns.ColumnError(f"配方 {key} 的 status 应为 {', '.join(STATUSES)} 之一")
        reason = str(data.get("reason") or "")
        if status == "blocked" and not reason:
            raise columns.ColumnError(f"配方 {key} 是 blocked，要写 reason")
        span = _range(key, data)
        if span and not span[0] <= float(data.get("target_minutes", 0) or 0) <= span[1]:
            raise columns.ColumnError(f"配方 {key} 自己的 target_minutes 要在 minutes_range {list(span)} 里")
        if minutes is not None:
            if not span:
                raise columns.ColumnError(f"配方 {key} 的时长不能改（没写 minutes_range）：文字里写着它多长，或者它不是为改时长写的")
            if not span[0] <= minutes <= span[1] or minutes * 2 != int(minutes * 2):
                raise columns.ColumnError(f"配方 {key} 的时长只能在 {span[0]:g}–{span[1]:g} 分钟之间，半分钟一档")
            data = {**data, "target_minutes": minutes}
        optional = (data.get("research") or {}).get("optional", False) if isinstance(data.get("research"), dict) else False
        if not isinstance(optional, bool):
            raise columns.ColumnError(f"配方 {key} 的 research.optional 应为 true 或 false")
        whole = (data.get("topic") or {}).get("whole_report", False) if isinstance(data.get("topic"), dict) else False
        if not isinstance(whole, bool):
            raise columns.ColumnError(f"配方 {key} 的 topic.whole_report 应为 true 或 false")
        if no_research:
            if not optional:
                raise columns.ColumnError(f"配方 {key} 的调研不能关（没写 research.optional: true）：它靠调研才讲得对，或者本来就不调研")
            data = {**data, "research": {"mode": "none"}}
        tags = _tags(key, data.get("tags"))
        if len(offered) > 1 and not all(dim in tags for dim in DIMENSIONS):
            raise columns.ColumnError(f"配方 {key} 有多个受众，要写全 tags 的 domain、type、format（栏目名由它们拼出）")
        flat = to_flat(key, data)
        column = columns.build(key, flat)
    except Exception as exc:  # noqa: BLE001 - one hand-edited file must never break loading the others
        if not isinstance(exc, (yaml.YAMLError, columns.ColumnError)):
            exc = columns.ColumnError(f"配方 {key} 有个值的类型不对：{type(exc).__name__}: {exc}")
        return Recipe(key=key, path=path, sha256=sha, status="blocked", reason=str(exc), error=str(exc),
                      label=key, data={}, uses=uses, audience=audience or "", audiences=offered, base=base or key)
    label = str(data.get("label") or "") or " · ".join(
        tags[dim]["name"] for dim in ("type", "audience", "format") if dim in tags) or column.name
    # a blocked recipe keeps its column: the scripts already made with it stay readable and renderable
    resolved = hashlib.sha256(json.dumps(flat, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    return Recipe(key=key, path=path, sha256=sha, status=status, reason=reason, label=label, tags=tags,
                  column=column, data=data, uses=uses, resolved_sha256=resolved, audience=chosen, audiences=offered,
                  base=base or key, minutes_range=span, minutes=minutes or 0.0,
                  research_optional=optional and not no_research, no_research=no_research, whole_report=whole)


def validate(key: str, text: str, config_dir: Optional[str] = None) -> Recipe:
    """The recipe ``text`` would be; raises ``ColumnError`` when it does not build (for saving)."""
    recipe = parse(key, text, config_dir=config_dir)
    if recipe.error:
        raise columns.ColumnError(recipe.error)
    for end in recipe.minutes_range:  # a recipe offering other lengths must build at both ends of them
        ends = parse(key, text, config_dir=config_dir, minutes=end)
        if ends.error:
            raise columns.ColumnError(f"时长 {end:g} 分钟这一版有问题：{ends.error}")
    if recipe.research_optional:  # and without the research, if it offers that
        bare = parse(key, text, config_dir=config_dir, no_research=True)
        if bare.error:
            raise columns.ColumnError(f"不调研的这一版有问题：{bare.error}")
    for other in recipe.audiences[1:]:  # each audience is a column of its own: all of them must build
        variant = parse(key, text, config_dir=config_dir, audience=other)
        if variant.error:
            raise columns.ColumnError(f"给 {other} 看的这一版有问题：{variant.error}")
    return recipe


def load(config_dir: str) -> dict[str, Recipe]:
    """Every recipe file by key, broken ones included (their ``error`` says why): enabled ones first."""
    folder = recipes_dir(config_dir)
    if not os.path.isdir(folder):
        return {}
    out: dict[str, Recipe] = {}
    for name in sorted(os.listdir(folder)):
        stem, ext = os.path.splitext(name)
        if ext not in (".yaml", ".yml") or stem.startswith(("_", ".")) or not KEY.fullmatch(stem):
            continue
        path = os.path.join(folder, name)
        with open(path, encoding="utf-8") as fh:
            out[stem] = parse(stem, fh.read(), path, config_dir)
    order = {status: i for i, status in enumerate(STATUSES)}
    return dict(sorted(out.items(), key=lambda kv: order[kv[1].status]))  # enabled first, then by file name


def variant_key(base: Recipe, audience: str) -> str:
    """The column key of a recipe's version for another audience: the cell its tags spell with that audience."""
    return "-".join(audience if dim == "audience" else base.tags[dim]["key"] for dim in DIMENSIONS)


def expanded(config_dir: str) -> dict[str, Recipe]:
    """Every column there is: each recipe file's own (its first audience) and one more for each other audience it
    offers. A key a file already has is not taken by an audience's version."""
    every = load(config_dir)
    out = dict(every)
    for key, base in every.items():
        if len(base.audiences) < 2 or base.error:
            continue
        with open(base.path, encoding="utf-8") as fh:
            text = fh.read()
        for audience in base.audiences[1:]:
            name = variant_key(base, audience)
            if name not in out:
                out[name] = parse(name, text, base.path, config_dir, audience=audience, base=key)
    order = {status: i for i, status in enumerate(STATUSES)}
    return dict(sorted(out.items(), key=lambda kv: order[kv[1].status]))


_VARIANT_KEY = re.compile(r"(.+?)(?:-m(\d{1,2})(p5)?)?(-nr)?")


def minutes_key(column_key: str, minutes: float) -> str:
    """The column key for a column at another length: ``deep_dive`` at 7.5 minutes is ``deep_dive-m7p5``."""
    return f"{column_key}-m{int(minutes)}" + ("p5" if minutes != int(minutes) else "")


def variant_column_key(column_key: str, minutes: Optional[float] = None, no_research: bool = False) -> str:
    """The key of a column at another length and/or without research: ``deep_dive-m8``, ``flash-nr``, ``flash-m2-nr``."""
    key = minutes_key(column_key, minutes) if minutes is not None else column_key
    return key + ("-nr" if no_research else "")


def split_variant_key(key: str) -> Optional[tuple[str, Optional[float], bool]]:
    """``(column key, minutes or None, no research)`` when ``key`` names a column changed that way, else None."""
    found = _VARIANT_KEY.fullmatch(key or "")
    if not found or (found.group(2) is None and not found.group(4)):
        return None
    minutes = None if found.group(2) is None else int(found.group(2)) + (0.5 if found.group(3) else 0.0)
    return found.group(1), minutes, bool(found.group(4))


def find(config_dir: str, key: str) -> Optional[Recipe]:
    """A recipe by its key, or by the cell id its tags spell, or built for another length than a recipe's own."""
    every = expanded(config_dir)
    if key in every:
        return every[key]
    cell = next((r for r in every.values() if r.cell_id == key), None)
    if cell is not None:
        return cell
    split = split_variant_key(key)
    parent = every.get(split[0]) if split else None
    if parent is None or parent.error:
        return None
    with open(parent.path, encoding="utf-8") as fh:
        return parse(key, fh.read(), parent.path, config_dir, audience=parent.audience or None, base=parent.base,
                     minutes=split[1], no_research=split[2])


def provenance(config_dir: str, key: str) -> Optional[dict]:
    """What a script version records about the recipe it was made with: key, file hash, cell, status, tags."""
    recipe = find(config_dir, key)
    if recipe is None:
        return None
    return {"recipe": recipe.key, "sha256": recipe.sha256, "cell": recipe.cell_id, "status": recipe.status,
            "parts": {dim: {"key": tag["key"]} for dim, tag in recipe.tags.items()} or None,
            "resolved_sha256": recipe.resolved_sha256 or None,
            "uses": {section: {"kind": u["kind"], "name": u["name"], "sha256": u["sha256"]}
                     for section, u in recipe.uses.items()} or None}


PART_KINDS = {"research": "调研模板", "output": "出片部件", "writing": "写稿部件", "audience": "给谁看"}


def part_file(config_dir: str, kind: str, name: str) -> str:
    """Where a shared part lives: ``research`` templates under the jobs folder, the others under config."""
    if kind not in PART_KINDS:
        raise columns.ColumnError(f"部件类型应为 {', '.join(PART_KINDS)} 之一")
    if kind == "audience":
        return audience_path(config_dir, name)
    return research_template_path(config_dir, name) if kind == "research" else part_path(config_dir, kind, name)


def parts(config_dir: str) -> list[dict]:
    """Every shared part there is, with the recipe files that use it."""
    every = expanded(config_dir)
    folders = {"research": os.path.join(jobs_dir_for(config_dir), RESEARCH_TEMPLATES)}
    folders.update({kind: os.path.join(config_dir, PARTS_DIR, kind) for kind in (*PART_SECTIONS, AUDIENCE_DIR)})
    out = []
    for kind, folder in folders.items():
        if not os.path.isdir(folder):
            continue
        for name in sorted(os.listdir(folder)):
            stem, ext = os.path.splitext(name)
            if ext not in (".yaml", ".yml") or not KEY.fullmatch(stem):
                continue
            path = os.path.join(folder, name)
            section = "research" if kind == "research" else kind
            used_by = sorted({r.base or r.key for r in every.values() if (r.uses.get(section) or {}).get("path") == path})
            try:
                data = yaml.safe_load(open(path, encoding="utf-8").read()) or {}
            except (OSError, yaml.YAMLError):
                data = {}
            label = str(data.get("name") or stem) if isinstance(data, dict) else stem
            out.append({"kind": kind, "kind_label": PART_KINDS[kind], "name": stem, "label": label,
                        "path": os.path.relpath(path, os.path.dirname(os.path.abspath(config_dir))),
                        "used_by": used_by})
    return out


def topic_sources(config_dir: str, key: str) -> list[str]:
    recipe = find(config_dir, key)
    return list(recipe.column.topic_sources) if recipe and recipe.usable else []


def catalog(config_dir: str) -> list[dict]:
    """Every recipe for a picker: what it is, whether it can be used and why not, and its column's settings.

    Enabled first, then experimental, then blocked or broken ones."""
    rows = []
    for recipe in expanded(config_dir).values():
        rows.append({
            "id": recipe.cell_id,
            "key": recipe.key if recipe.usable else None,
            "recipe": recipe.key,
            "base": recipe.base or recipe.key,  # the recipe file it comes from
            "minutes_range": list(recipe.minutes_range),  # the lengths a config may ask for; empty: none
            "research_optional": recipe.research_optional,  # a config may turn the research off
            "whole_report": recipe.whole_report,  # a config may write it from a whole report (no items to pick)
            "audience": recipe.audience,  # the audience profile it is for ("" when the recipe names none)
            "status": recipe.status,
            "reason": recipe.reason,
            "error": recipe.error,
            "usable": recipe.usable,
            "label": recipe.label,
            # the topic page's grid places a recipe by all four tags; one with fewer is listed but not placed
            "parts": ({dim: dict(recipe.tags[dim]) for dim in DIMENSIONS}
                      if all(dim in recipe.tags for dim in DIMENSIONS) else None),
            "column": recipe.column.as_dict() if recipe.usable else None,
            "topic_sources": list(recipe.column.topic_sources) if recipe.usable else [],
        })
    order = {status: i for i, status in enumerate(STATUSES)}
    return sorted(rows, key=lambda r: order[r["status"]])


class _Dumper(yaml.SafeDumper):
    pass


_Dumper.add_representer(str, lambda d, v: d.represent_scalar(
    "tag:yaml.org,2002:str", v, style="|" if "\n" in v else None))
_Dumper.add_representer(list, lambda d, v: d.represent_sequence(
    "tag:yaml.org,2002:seq", v, flow_style=all(not isinstance(i, (dict, list)) for i in v)))


def dump(data: dict) -> str:
    """A recipe as YAML text: multi-line text as literal blocks, short lists inline, keys in the given order."""
    return yaml.dump(data, Dumper=_Dumper, allow_unicode=True, sort_keys=False, width=10000)
