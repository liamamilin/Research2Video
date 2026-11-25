"""Capture current configuration-composed author instructions without generation.

Dynamic topic, history, materials, joint allocation and on-demand repairs are
explicitly absent. These artifacts prove composition, not production execution.
"""
from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path

INPUT_ROOT = Path(__file__).resolve().parents[4]
ROOT = INPUT_ROOT
sys.path.insert(0, str(ROOT))

from core.config import load_system_config
from core.video import content_columns, episodes, manuscript_workflow, persona, production, recipes, writer

HERE = Path(__file__).resolve().parent
BASE = INPUT_ROOT / "state/acceptance-content-value-20261115/natural-writing-20261116/baseline"


def main(label: str = 'baseline') -> None:
    destination = BASE if label == 'baseline' else BASE.parent / label
    index = HERE / ('route-captures.json' if label == 'baseline' else label + '-route-captures.json')
    if (destination / 'routes').exists() or index.exists():
        raise FileExistsError('Preserve previous captures')
    inventory = json.loads((HERE / "inventory.json").read_text())
    config_dir, jobs_dir = str(ROOT / "config"), str(ROOT / "jobs")
    configs = production.load_all(config_dir, jobs_dir)
    video_config = load_system_config(config_dir).get("video", {})
    every = recipes.expanded(config_dir)
    narrator = persona.load(config_dir)
    summary = []
    for entry in inventory["jobs"]:
        for pid, binding in configs.items():
            if binding.job != entry["job"]:
                continue
            key = binding.recipe
            recipe = recipes.find(config_dir, key, every=every)
            if not binding.usable or recipe is None or recipe.column is None:
                summary.append({"job": entry["job"], "production": pid,
                                "error": list(binding.errors) or "unresolved recipe"})
                continue
            context = content_columns.snapshot(config_dir, pid, binding.job)
            profile = manuscript_workflow.profile_for({"production": pid, "content_context": context},
                                                     config_dir, jobs_dir, video_config)
            original_context = context
            if profile and profile.draft and context.get("column"):
                context = {**context, "notes": profile.draft}
            column = content_columns.apply_script_context(recipe.column, context)
            if profile and profile.draft and not context.get("column"):
                column = replace(column, script_style=column.script_style + "\n" + profile.draft)
            # Current business columns default to episode. Unbound recipes keep
            # their own individual mode; actual topic setup is checked later.
            topic = {"title": "<实际选题>", "content_mode": context.get("content_mode", "individual"),
                     "fields": {"目标受众": context.get("audience") or column.audience}}
            if topic["content_mode"] == "episode":
                topic["episode"] = {"items": []}
            tutorial = writer.tutorial_context(original_context)
            greeting = episodes.greeting(topic, original_context) or None
            system = writer.author_system_prompt(topic, narrator, greeting=greeting, tutorial=tutorial,
                                                  episode_prompt=profile.episode_prompt if profile else None,
                                                  episode_lead=profile.episode_lead if profile else None)
            prompt = writer._request("<实际研究底稿：本捕获不提供>", topic,
                                     column.script_style + episodes.brief(topic, tutorial=tutorial),
                                     column.target_minutes, 0, topic_fields=column.writer_topic_fields(),
                                     original_material="<实际自动材料：本捕获不提供>")
            output = {"job": entry["job"], "production": pid, "recipe": key,
                      "capture_kind": "configuration_composition_only",
                      "missing_runtime_layers": ["actual topic and audience flags", "joint allocation direction",
                                                 "event/history selection", "original material and report",
                                                 "introduction/episode opening repair requests", "polish triggers"],
                      "context": original_context, "profile": profile.version if profile else None,
                      "system": system, "user_shape": prompt,
                      "reader": profile.read if profile else None,
                      "revision": profile.revise if profile else None,
                      "research_recipe_prompt": column.research_prompt,
                      "recipe_sources": {"path": recipe.path, "uses": recipe.uses}}
            target = destination / "routes" / f"{pid}.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
            summary.append({"job": entry["job"], "production": pid, "recipe": key,
                            "profile": output["profile"], "mode": topic["content_mode"],
                            "path": str(target.relative_to(INPUT_ROOT))})
    index.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"bindings": len(summary), "errors": [r for r in summary if "error" in r]}, ensure_ascii=False))


if __name__ == '__main__':
    main('soft-final')
