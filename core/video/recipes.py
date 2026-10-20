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

_TOP = {"name", "description", "status", "reason", "label", "tags", "audience", "target_minutes",
        "topic", "research", "writing", "output"}
_SECTIONS = {
    "topic": {"kind", "sources", "fields"},
    "research": {"mode", "timeout_seconds", "language", "prompt"},
    "writing": {"pipeline", "style", "sections", "paragraph_units", "required_roles", "default_style",
                "allowed_tones", "revise_rounds", "pause_after_plan", "audit"},
    "output": {"target", "voice_id", "video_form", "allowed_forms", "creative_intent", "source_card", "package",
               "recheck"},
}
#: sections that may name a shared part (``use``), and where research templates live under the jobs folder
PART_SECTIONS = ("writing", "output")
PARTS_DIR = "video_parts"
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


def resolve(key: str, data: dict, config_dir: Optional[str]) -> tuple[dict, dict]:
    """The recipe with its shared sections put in: research templates and parts, the recipe's own fields winning."""
    if not isinstance(data, dict):
        raise columns.ColumnError(f"配方 {key} 的根必须是映射")
    out = dict(data)
    uses: dict = {}
    research = data.get("research")
    if isinstance(research, dict) and "template" in research:
        if config_dir is None:
            raise columns.ColumnError(f"配方 {key} 引用了调研模板，要知道 config 目录才能读")
        base, uses["research"] = research_from_template(config_dir, str(research["template"]))
        out["research"] = {**base, **{k: v for k, v in research.items() if k != "template"}}
    for section in PART_SECTIONS:
        own = data.get(section)
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


def parse(key: str, text: str, path: str = "", config_dir: Optional[str] = None) -> Recipe:
    """One recipe from its file's text. Never raises for bad content: the problem goes in ``error``.

    ``config_dir`` is where its shared parts are looked up; without it a recipe that uses one does not build."""
    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    uses: dict = {}
    try:
        data = yaml.safe_load(text) or {}
        if not isinstance(data, dict):
            raise columns.ColumnError(f"配方 {key} 的根必须是映射")
        written = data
        data, uses = resolve(key, written, config_dir)
        status = str(data.get("status") or "experimental")
        if status not in STATUSES:
            raise columns.ColumnError(f"配方 {key} 的 status 应为 {', '.join(STATUSES)} 之一")
        reason = str(data.get("reason") or "")
        if status == "blocked" and not reason:
            raise columns.ColumnError(f"配方 {key} 是 blocked，要写 reason")
        tags = _tags(key, data.get("tags"))
        flat = to_flat(key, data)
        column = columns.build(key, flat)
    except Exception as exc:  # noqa: BLE001 - one hand-edited file must never break loading the others
        if not isinstance(exc, (yaml.YAMLError, columns.ColumnError)):
            exc = columns.ColumnError(f"配方 {key} 有个值的类型不对：{type(exc).__name__}: {exc}")
        return Recipe(key=key, path=path, sha256=sha, status="blocked", reason=str(exc), error=str(exc),
                      label=key, data={}, uses=uses)
    label = str(data.get("label") or "") or " · ".join(
        tags[dim]["name"] for dim in ("type", "audience", "format") if dim in tags) or column.name
    # a blocked recipe keeps its column: the scripts already made with it stay readable and renderable
    resolved = hashlib.sha256(json.dumps(flat, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
    return Recipe(key=key, path=path, sha256=sha, status=status, reason=reason, label=label, tags=tags,
                  column=column, data=data, uses=uses, resolved_sha256=resolved)


def validate(key: str, text: str, config_dir: Optional[str] = None) -> Recipe:
    """The recipe ``text`` would be; raises ``ColumnError`` when it does not build (for saving)."""
    recipe = parse(key, text, config_dir=config_dir)
    if recipe.error:
        raise columns.ColumnError(recipe.error)
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


def find(config_dir: str, key: str) -> Optional[Recipe]:
    """A recipe by its key, or by the cell id its tags spell."""
    every = load(config_dir)
    if key in every:
        return every[key]
    return next((r for r in every.values() if r.cell_id == key), None)


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


PART_KINDS = {"research": "调研模板", "output": "出片部件", "writing": "写稿部件"}


def part_file(config_dir: str, kind: str, name: str) -> str:
    """Where a shared part lives: ``research`` templates under the jobs folder, the others under config."""
    if kind not in PART_KINDS:
        raise columns.ColumnError(f"部件类型应为 {', '.join(PART_KINDS)} 之一")
    return research_template_path(config_dir, name) if kind == "research" else part_path(config_dir, kind, name)


def parts(config_dir: str) -> list[dict]:
    """Every shared part there is, with the recipes that use it."""
    every = load(config_dir)
    folders = {"research": os.path.join(jobs_dir_for(config_dir), RESEARCH_TEMPLATES)}
    folders.update({kind: os.path.join(config_dir, PARTS_DIR, kind) for kind in PART_SECTIONS})
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
            used_by = [r.key for r in every.values() if (r.uses.get(section) or {}).get("path") == path]
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
    for recipe in load(config_dir).values():
        rows.append({
            "id": recipe.cell_id,
            "key": recipe.key if recipe.usable else None,
            "recipe": recipe.key,
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
