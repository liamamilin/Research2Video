"""Production configs: how a matrix Job's reports become videos (spec/production.md, spec/product-redesign.md §2.2).

One file per config, ``config/production/<key>.yaml``::

    name: 快讯 · 开发者        # optional; the recipe's label when left out
    job: monitoring/ai_flash_discovery
    recipe: flash              # how it is written and rendered: a recipe under config/video_recipes/

A config adds no settings of its own yet. Who it is for, how long it runs, whether a picked item is researched again
and how the report is split all come from the recipe, because the recipes' prompts say them in their own words
(a developer recipe's style says 开发者 and 60–90 秒; overriding the number would make it contradict itself). So a
config resolves to exactly its recipe's Column, and what the AI configs write is what the recipes wrote before.
What a config is for: tying a recipe to the Job whose reports it writes from, so a matrix shows what each Job's
reports turn into, and every script records which config made it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

import yaml

from core import matrices
from core.config import load_job
from core.fileio import atomic_write

from . import recipes, topic_store

DIR = "production"
KEY = recipes.KEY
#: how a recipe's topic kind splits a report (the prototype's 拆不拆)
SPLITS = {"single": ("each", "每条一期"), "roundup": ("some", "挑几条合一期")}


@dataclass(frozen=True)
class Config:
    key: str
    name: str
    job: str
    recipe: str
    errors: tuple = ()
    #: filled when the recipe builds: what it derives from the recipe
    source: str = ""         # the topic source the Job feeds (its alias), where items are picked from
    split: str = ""
    split_name: str = ""
    reverify: bool = False   # a picked item is researched again before writing (the recipe's research.mode: job)
    audience: str = ""
    target_minutes: float = 0.0
    label: str = ""          # the recipe's label

    @property
    def usable(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict:
        return {"key": self.key, "name": self.name, "job": self.job, "recipe": self.recipe, "errors": list(self.errors),
                "source": self.source, "split": self.split, "split_name": self.split_name, "reverify": self.reverify,
                "audience": self.audience, "target_minutes": self.target_minutes, "label": self.label}


def directory(config_dir: str) -> str:
    return os.path.join(config_dir, DIR)


def _check(key: str, data: dict, config_dir: str, jobs_dir: str, every_recipe: dict) -> Config:
    job, recipe_key = str(data.get("job") or "").strip(), str(data.get("recipe") or "").strip()
    errors: list[str] = []
    unknown = set(data) - {"name", "job", "recipe"}
    if unknown:
        errors.append(f"不认识的字段：{', '.join(sorted(unknown))}")
    recipe = every_recipe.get(recipe_key)
    if recipe is None:
        errors.append(f"没有这个配方：{recipe_key or '（没写 recipe）'}")
    elif not recipe.usable:
        errors.append(f"配方 {recipe_key} {'已停用' if recipe.column else '有错'}，不能用来写稿")
    source = None
    if not job:
        errors.append("没写 job")
    elif load_job(jobs_dir, job) is None:
        errors.append(f"没有这个 Job：{job}")
    else:
        if matrices.of_job(job, config_dir) is None:
            errors.append(f"{job} 不在任何矩阵里：先把它放进一个矩阵")
        source = topic_store.resolve(jobs_dir, job)
        if source is None:
            errors.append(f"{job} 的报告不是条目列表（没有 topics:），整份写成视频请在报告页点「写成视频」")
        elif source.error:
            errors.append(f"{job} 的选题来源有错：{source.error}")
    column = recipe.column if recipe is not None and recipe.usable else None
    if column is not None and column.topic_kind not in SPLITS:
        errors.append(f"配方 {recipe_key} 要手填两个对象（对比），不从 Job 的报告里挑条目")
    elif column is not None and source is not None and not source.error and source.name not in column.topic_sources:
        # the recipe's prompts are written for its own sources (AI's say 开发者 and 每百万 token): a medical Job's items
        # would be researched and written with them. Listing the source in the recipe is the deliberate way to allow it.
        errors.append(f"配方 {recipe_key} 是给 {'、'.join(column.topic_sources) or '手选'} 的条目写的，不读 {source.name}："
                      f"要用它，先在配方的 topic.sources 里加上 {source.name}")
    split, split_name = SPLITS.get(column.topic_kind, ("", "")) if column is not None else ("", "")
    label = recipe.label if recipe is not None else ""
    return Config(key=key, name=str(data.get("name") or "").strip() or label or key, job=job, recipe=recipe_key,
                  errors=tuple(errors), source=source.name if source else "", split=split, split_name=split_name,
                  reverify=bool(column is not None and column.research_mode == "job"),
                  audience=column.audience if column is not None else "",
                  target_minutes=column.target_minutes if column is not None else 0.0, label=label)


def load_all(config_dir: str, jobs_dir: str) -> dict[str, Config]:
    """Every config by key, in file-name order; a broken one is listed with its errors, never left out."""
    folder = directory(config_dir)
    try:
        names = sorted(f for f in os.listdir(folder) if f.endswith(".yaml") and not f.startswith((".", "_")))
    except OSError:
        return {}
    every_recipe = recipes.load(config_dir)
    out: dict[str, Config] = {}
    for filename in names:
        key = filename[:-len(".yaml")]
        if not KEY.fullmatch(key):
            continue
        try:
            with open(os.path.join(folder, filename), encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
        except (OSError, yaml.YAMLError) as exc:
            out[key] = Config(key=key, name=key, job="", recipe="", errors=(f"读不了：{str(exc)[:120]}",))
            continue
        if not isinstance(data, dict):
            out[key] = Config(key=key, name=key, job="", recipe="", errors=("应为映射（name、job、recipe）",))
            continue
        out[key] = _check(key, data, config_dir, jobs_dir, every_recipe)
    return out


def get(config_dir: str, jobs_dir: str, key: str) -> Optional[Config]:
    return load_all(config_dir, jobs_dir).get(key)


def save(config_dir: str, key: str, *, job: str, recipe: str, name: str = "") -> None:
    data = {"name": name, "job": job, "recipe": recipe} if name else {"job": job, "recipe": recipe}
    text = ("# 生产配置：这个 Job 的报告怎么写成视频（spec/production.md）。给谁看、多长、拆不拆、要不要再核实都由配方定。\n"
            + yaml.safe_dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False))
    os.makedirs(directory(config_dir), exist_ok=True)
    atomic_write(os.path.join(directory(config_dir), f"{key}.yaml"), text)


def remove(config_dir: str, key: str) -> bool:
    try:
        os.remove(os.path.join(directory(config_dir), f"{key}.yaml"))
        return True
    except FileNotFoundError:
        return False
