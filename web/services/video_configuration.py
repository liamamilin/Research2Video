"""Resolve the configuration editors relevant to a script, without changing it."""
from urllib.parse import quote
from pathlib import Path
import yaml

from core import matrices
from core.video import production, recipes
from core.yaml_cache import load_yaml


def context(script: dict, version: dict | None, config_dir: str, jobs_dir: str) -> dict:
    recipe = recipes.find(config_dir, script["column_key"])
    links = []
    changed = None
    if recipe:
        base = recipe.base or recipe.key
        links.append({"label": "文稿配方与提示词", "name": recipe.label or base,
                      "href": f"/video-recipes/{quote(base, safe='')}",
                      "scope": "此配方的后续写稿", "next": "保存后重新生成文稿"})
        saved = ((version or {}).get("meta") or {}).get("cell") or {}
        if saved.get("resolved_sha256") and recipe.resolved_sha256:
            changed = saved["resolved_sha256"] != recipe.resolved_sha256
        labels = {"audience": "受众与表达深度", "research": "调研模板", "writing": "共用写稿提示词",
                  "output": "出片形式与配音", "packaging": "片头片尾与包装"}
        for kind, label in labels.items():
            use = recipe.uses.get(kind)
            if not use:
                continue
            links.append({"label": label, "name": use["name"],
                          "href": f"/video-recipes/parts/{kind}/{quote(use['name'], safe='')}",
                          "scope": "所有引用它的配方",
                          "next": "保存后重新生成视频" if kind in ("output", "packaging") else
                                  "保存后重新生成文稿" if kind != "research" else "仅重新写稿会沿用旧报告；新调研使用新模板"})
    key = script.get("production") or ""
    if key and production.KEY.fullmatch(key):
        path = Path(config_dir) / production.DIR / f"{key}.yaml"
        try:
            config = load_yaml(path.read_text()) if path.is_file() else None
        except (OSError, yaml.YAMLError):
            config = None  # An invalid configuration must not hide the saved manuscript.
        if isinstance(config, dict) and config.get("job"):
            job = str(config["job"])
            for matrix in matrices.load_all(config_dir).values():
                if job in matrix.jobs:
                    links.append({"label": "矩阵的共用视频设置", "name": matrix.name,
                                  "href": f"/matrices/{quote(matrix.key, safe='')}?tab=settings",
                                  "scope": "本矩阵所有未单独覆盖的任务", "next": "调整默认输出倍速后重新生成视频"})
                    links.append({"label": "任务的视频生成设置", "name": config.get("name") or key,
                                  "href": f"/matrices/{quote(matrix.key, safe='')}/video/{quote(job, safe='')}",
                                  "scope": "该研究任务的后续视频", "next": "在此调整制作配方、制作档位等设置"})
                    break
            links.append({"label": "原研究任务与提示词", "name": job,
                          "href": f"/jobs/{quote(job, safe='')}", "scope": "该任务后续研究报告",
                          "next": "不会改写已有报告"})
    return {"links": links, "changed_since_generation": changed, "recipe_available": recipe is not None}
