"""Production configs: how a matrix Job's reports become videos (spec/production.md, spec/product-redesign.md §2.2).

One file per config, ``config/production/<key>.yaml``::

    name: 快讯 · 开发者        # optional; the recipe's label when left out
    job: monitoring/ai_flash_discovery
    recipe: flash              # how it is written and rendered: a recipe under config/video_recipes/
    audience: non_tech         # optional: which of the audiences the recipe offers (its first when left out)
    minutes: 8                 # optional: another length than the recipe's, within the range it declares
    reverify: false            # optional: do not research a picked item again; write from its part of the report
    auto: 3                    # optional: when the Job's report comes out, write up to this many of its items, unattended

Who it is for is the config's choice among the audiences the recipe offers (``recipes.py``: each audience is a column
of its own, so the config resolves to that column and nothing downstream changes). How long it runs, whether a picked
item is researched again and how the report is split still come from the recipe, because the recipes' prompts say them
in their own words (a style that says 60–90 秒 would contradict an overridden number). So a config resolves to exactly
one Column, and what the AI configs write is what the recipes wrote before. Length is the config's choice only for a
recipe that declares a ``minutes_range``, and whether a picked item is researched again only for a recipe that says
its research is ``optional``.
``auto`` is the one setting that is no part of the column: it says the unattended pipeline (``video_autopilot``) writes
this config's recipe from each new report of the Job, up to that many items, and takes each to a finished video. It is
off unless asked for, only for a recipe that writes one video per item, and only reports that come out after it was
turned on are written (``core/video/auto.py``).
A Job whose reports are no item lists (no ``topics:``) takes a config too: its recipe has to say ``topic.whole_report``,
and each new report is one video written from the report as it is (split ``whole``, never researched again).
What a config is for: tying a recipe to the Job whose reports it writes from, so a matrix shows what each Job's
reports turn into, and every script records which config made it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

import yaml

from core import matrices
from core.config import load_job
from core.fileio import atomic_write
from core.yaml_cache import load_yaml

from . import recipes, topic_store

DIR = "production"
KEY = recipes.KEY
#: how a recipe's topic kind splits a report (the prototype's 拆不拆)
SPLITS = {"single": ("each", "每条一期"), "roundup": ("some", "挑几条合一期")}
#: a Job whose reports are no item lists: each new report is one video, written from the report as it is
WHOLE = ("whole", "整份报告一期")
#: most items one report is written into without being asked (one batch of the unattended pipeline)
MAX_AUTO = 10


@dataclass(frozen=True)
class Config:
    key: str
    name: str
    job: str
    recipe: str
    errors: tuple = ()
    #: the recipe file and audience profile the column is made of ("" for a recipe that names no audiences)
    base_recipe: str = ""
    audience_key: str = ""
    #: the length the config asks for instead of the recipe's (0: the recipe's own) and the lengths its recipe allows
    minutes: float = 0.0
    minutes_range: tuple = ()
    #: filled when the recipe builds: what it derives from the recipe
    source: str = ""         # the topic source the Job feeds (its alias), where items are picked from
    split: str = ""
    split_name: str = ""
    reverify: bool = False   # a picked item is researched again before writing (the recipe's research.mode: job)
    auto: int = 0            # items written per new report without being asked (0: only when the user writes)
    audience: str = ""
    target_minutes: float = 0.0
    label: str = ""          # the recipe's label
    render: dict = field(default_factory=dict)

    @property
    def usable(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict:
        return {"key": self.key, "name": self.name, "job": self.job, "recipe": self.recipe, "errors": list(self.errors),
                "base_recipe": self.base_recipe, "audience_key": self.audience_key, "minutes": self.minutes,
                "minutes_range": list(self.minutes_range),
                "source": self.source, "split": self.split, "split_name": self.split_name, "reverify": self.reverify,
                "auto": self.auto,
                **({"render": self.render} if self.render else {}),
                "audience": self.audience, "target_minutes": self.target_minutes, "label": self.label}


def directory(config_dir: str) -> str:
    return os.path.join(config_dir, DIR)


def column_key(every_recipe: dict, recipe: str, audience: str = "", minutes=None, reverify=None) -> tuple[str, str]:
    """The column a recipe file, audience, length and research choice make (the recipe's own key when none is asked
    for), or why not. Whether the recipe allows the length or the missing research is for ``recipes.find`` to say."""
    key = recipe
    if audience:
        base = every_recipe.get(recipe)
        if base is None or base.base != base.key:
            return recipe, f"没有这个配方：{recipe or '（没写 recipe）'}"
        if base.error:
            return recipe, f"配方 {recipe} 有错：{base.error}"
        if audience not in base.audiences:
            offered = "、".join(base.audiences)
            return recipe, f"配方 {recipe} 没有 {audience} 这个受众" + (f"（有：{offered}）" if offered else "（它没写 audiences）")
        key = recipe if audience == base.audiences[0] else recipes.variant_key(base, audience)
    if minutes is not None:
        if isinstance(minutes, bool) or not isinstance(minutes, (int, float)) or minutes * 2 != int(minutes * 2) or minutes <= 0:
            return key, "minutes 应是分钟数，半分钟一档，例如 8 或 7.5"
        own = every_recipe.get(key)
        if own is None or own.column is None or own.column.target_minutes != minutes:  # asking for its own length is no change
            key = recipes.minutes_key(key, minutes)
        else:
            minutes = None
    if reverify is not None and not isinstance(reverify, bool):
        return key, "reverify 应为 true 或 false"
    if reverify is False:
        own = every_recipe.get(recipes.split_variant_key(key)[0] if recipes.split_variant_key(key) else key)
        if own is None or own.column is None or own.column.research_mode != "none":  # it has no research to turn off
            key += "-nr"
    return key, ""


def _check(key: str, data: dict, config_dir: str, jobs_dir: str, every_recipe: dict,
           sources: dict, matrix_jobs: set[str]) -> Config:
    job, recipe_key = str(data.get("job") or "").strip(), str(data.get("recipe") or "").strip()
    audience = str(data.get("audience") or "").strip()
    errors: list[str] = []
    unknown = set(data) - {"name", "job", "recipe", "audience", "minutes", "reverify", "auto", "render"}
    if unknown:
        errors.append(f"不认识的字段：{', '.join(sorted(unknown))}")
    from .production_settings import validate as validate_render
    try:
        render_settings = validate_render(data.get("render"))
    except ValueError as exc:
        errors.append(str(exc))
        render_settings = {}
    minutes = data.get("minutes")
    reverify = data.get("reverify")
    recipe_key, why = column_key(every_recipe, recipe_key, audience, minutes, reverify)
    if why:
        errors.append(why)
    recipe = every_recipe.get(recipe_key)
    if recipe is None and not why and recipes.split_variant_key(recipe_key):
        recipe = recipes.find(config_dir, recipe_key, every=every_recipe)
    if recipe is None:
        if not why:
            errors.append(f"没有这个配方：{recipe_key or '（没写 recipe）'}")
    elif recipe.error and recipes.split_variant_key(recipe_key):
        errors.append(recipe.error)
    elif not recipe.usable:
        errors.append(f"配方 {recipe_key} {'已停用' if recipe.column else '有错'}，不能用来写稿")
    source = None
    whole = None  # the Job's reports as whole files, when they are no item lists
    if not job:
        errors.append("没写 job")
    elif load_job(jobs_dir, job) is None:
        errors.append(f"没有这个 Job：{job}")
    else:
        if job not in matrix_jobs:
            errors.append(f"{job} 不在任何矩阵里：先把它放进一个矩阵")
        source = sources.get(job)
        if source is None:
            whole = topic_store.whole_source(jobs_dir, job)
            if whole is not None and whole.error:
                errors.append(f"{job}：{whole.error}")
        elif source.error:
            errors.append(f"{job} 的选题来源有错：{source.error}")
    column = recipe.column if recipe is not None and recipe.usable else None
    if column is not None and whole is not None:
        if column.topic_kind != "single" or not recipe.whole_report:
            errors.append(f"{job} 的报告不是条目列表，而是整份的：配方 {recipe_key} 要从条目里挑，不能用；"
                          f"整份报告要用写了 topic.whole_report 的配方（比如深度解读）")
        elif reverify is not None:
            errors.append("整份报告写成视频本来就不再调研，不用写 reverify")
    if column is not None and column.topic_kind not in SPLITS:
        errors.append(f"配方 {recipe_key} 要手填两个对象（对比），不从 Job 的报告里挑条目")
    elif column is not None and source is not None and not source.error and source.name not in column.topic_sources:
        # the recipe's prompts are written for its own sources (AI's say 开发者 and 每百万 token): a medical Job's items
        # would be researched and written with them. Listing the source in the recipe is the deliberate way to allow it.
        errors.append(f"配方 {recipe_key} 是给 {'、'.join(column.topic_sources) or '手选'} 的条目写的，不读 {source.name}："
                      f"要用它，先在配方的 topic.sources 里加上 {source.name}")
    split, split_name = SPLITS.get(column.topic_kind, ("", "")) if column is not None else ("", "")
    if whole is not None and column is not None and column.topic_kind == "single" and recipe.whole_report:
        split, split_name = WHOLE
    auto = data.get("auto")
    if auto is not None and (isinstance(auto, bool) or not isinstance(auto, int) or not 0 <= auto <= MAX_AUTO):
        errors.append(f"auto 应是每次写几条，1 到 {MAX_AUTO} 的整数（0 是不自动写）")
        auto = None
    elif auto is not None and split == "some":
        errors.append("盘点一期要挑几条合成，不能自动写：自动只用于每条一期的配方")
        auto = None
    elif auto is not None and split == "whole" and auto > 1:
        errors.append("整份报告每份写一期，auto 只能是 1（开）或 0（关）")
        auto = None
    label = recipe.label if recipe is not None else ""
    return Config(key=key, name=str(data.get("name") or "").strip() or label or key, job=job, recipe=recipe_key,
                  errors=tuple(errors), base_recipe=recipe.base if recipe is not None else "",
                  audience_key=recipe.audience if recipe is not None else "",
                  minutes=recipe.minutes if recipe is not None else 0.0,
                  minutes_range=recipe.minutes_range if recipe is not None else (),
                  source=source.name if source else "", split=split, split_name=split_name,
                  reverify=bool(column is not None and column.research_mode == "job" and split != "whole"), auto=auto or 0,
                  audience=column.audience if column is not None else "",
                  target_minutes=column.target_minutes if column is not None else 0.0, label=label, render=render_settings)


def load_all(config_dir: str, jobs_dir: str) -> dict[str, Config]:
    """Every config by key, in file-name order; a broken one is listed with its errors, never left out."""
    folder = directory(config_dir)
    try:
        names = sorted(f for f in os.listdir(folder) if f.endswith(".yaml") and not f.startswith((".", "_")))
    except OSError:
        return {}
    every_recipe = recipes.expanded(config_dir)
    # These are one request's snapshots, not a persistent cache. Loading them
    # inside _check rescanned every Job and reopened the topics DB per config.
    sources = topic_store.sources(jobs_dir)
    sources = {**{s.job: s for s in sources.values()}, **sources}
    matrix_jobs = {job for matrix in matrices.load_all(config_dir).values() for job in matrix.jobs}
    out: dict[str, Config] = {}
    for filename in names:
        key = filename[:-len(".yaml")]
        if not KEY.fullmatch(key):
            continue
        try:
            with open(os.path.join(folder, filename), encoding="utf-8") as fh:
                data = load_yaml(fh) or {}
        except (OSError, yaml.YAMLError) as exc:
            out[key] = Config(key=key, name=key, job="", recipe="", errors=(f"读不了：{str(exc)[:120]}",))
            continue
        if not isinstance(data, dict):
            out[key] = Config(key=key, name=key, job="", recipe="", errors=("应为映射（name、job、recipe）",))
            continue
        try:
            out[key] = _check(key, data, config_dir, jobs_dir, every_recipe, sources, matrix_jobs)
        except (OSError, ValueError, TypeError, AttributeError, KeyError, yaml.YAMLError) as exc:
            # A referenced job/part may be malformed or replaced during an edit.
            # Keep the broken config visible without breaking every list page.
            out[key] = Config(key=key, name=str(data.get("name") or key), job=str(data.get("job") or ""),
                              recipe=str(data.get("recipe") or ""),
                              errors=(f"配置加载失败：{type(exc).__name__}: {str(exc)[:160]}",))
    return out


def get(config_dir: str, jobs_dir: str, key: str) -> Optional[Config]:
    return load_all(config_dir, jobs_dir).get(key)


HEADER = ("# 生产配置：这个 Job 的报告怎么写成视频（spec/production.md）。拆不拆、要不要再核实由配方定，给谁看在 audience 里选，"
          "多长在配方允许的范围里用 minutes 改，要不要重新调研用 reverify 改，报告出来后自动写几条用 auto 改。\n")


def _write(config_dir: str, key: str, data: dict) -> None:
    data = {k: v for k, v in data.items() if v not in ("", None)}
    os.makedirs(directory(config_dir), exist_ok=True)
    atomic_write(os.path.join(directory(config_dir), f"{key}.yaml"),
                 HEADER + yaml.safe_dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False))


def save(config_dir: str, key: str, *, job: str, recipe: str, name: str = "", audience: str = "",
         minutes=None, reverify=None, auto=None, render=None) -> None:
    if render is None:
        path = os.path.join(directory(config_dir), f"{key}.yaml")
        if os.path.isfile(path):
            with open(path, encoding="utf-8") as stream:
                render = (yaml.safe_load(stream) or {}).get("render")
    from .production_settings import validate as validate_render
    render = validate_render(render)
    _write(config_dir, key, {"name": name, "job": job, "recipe": recipe, "audience": audience, "minutes": minutes,
                             "reverify": reverify, "auto": auto, **({"render": render} if render else {})})


def set_auto(config_dir: str, key: str, count: Optional[int]) -> bool:
    """Turn the unattended writing of a config on (``count`` items per report) or off (None), keeping the rest of its
    file. False when there is no such config file."""
    path = os.path.join(directory(config_dir), f"{key}.yaml")
    try:
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except (OSError, yaml.YAMLError):
        return False
    if not isinstance(data, dict):
        return False
    data["auto"] = count
    _write(config_dir, key, data)  # the file's time is when auto was turned on: older reports are not written
    return True


def remove(config_dir: str, key: str) -> bool:
    try:
        os.remove(os.path.join(directory(config_dir), f"{key}.yaml"))
        return True
    except FileNotFoundError:
        return False
