"""Golden snapshot of what the three shipped video columns turn into (spec/video-matrix.md, phase 0).

They are recipe files now (config/video_recipes/, spec/video-recipes.md). A refactor must not change what a shipped column resolves to: its fields, the research prompt a topic
fills, and the source request now used by the automatic material-selection step.
The draft's selected packet is dynamic and covered by workflow integration tests.
``snapshot()`` computes them from the shipped
``config/``; ``tests/golden/video_cells.json`` is the committed result. Regenerate it only for a deliberate change:

    python -m tests.golden_video_cells --write

(adapt's prompts are built from the same Column fields, which the snapshot holds in full.)
"""

from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "tests" / "golden" / "video_cells.json"

#: the cells that have been rendered and checked; cells added later are experimental and not frozen here
VALIDATED = ("deep_dive", "flash", "point_short")
TOPIC = {
    "id": "2026-10-19/F1", "round": "2026-10-19", "title": "示例选题：某模型的限时价格",
    "fields": {"核心角度": "输入价从 2 美元涨到 4 美元", "目标受众": "开发者", "为什么有人在乎": "影响 API 成本",
               "建议大纲": "变化、影响、行动", "变化": "输入 2 美元 -> 4 美元", "发布日期": "2026-10-13",
               "来源网址": "https://example.com/pricing"},
}
REPORT = "### 1. 直接回答\n示例报告：输入价 2 美元，12 月 1 日起 4 美元。\n\n### 6. 来源清单\n1. 定价页 | 一手 | 2026-10-13 | https://example.com/pricing\n"


def install_recipes(config_dir: Path | str, replace: tuple[tuple[str, str], ...] = ()) -> Path:
    """Copy the shipped recipe files into ``config_dir``; each (old, new) in ``replace`` is applied to every file."""
    import shutil

    target = Path(config_dir) / "video_recipes"
    shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True)
    for source in sorted((ROOT / "config" / "video_recipes").glob("*.yaml")):
        text = source.read_text(encoding="utf-8")
        for old, new in replace:
            text = text.replace(old, new)
        (target / source.name).write_text(text, encoding="utf-8")
    install_parts(config_dir)
    return target


def install_parts(config_dir: Path | str) -> None:
    """Copy the shared parts the recipes use: output parts into ``config_dir``, research templates into the jobs
    folder next to it (where ``recipes.jobs_dir_for`` looks)."""
    import shutil

    from core.video import recipes

    config_dir = Path(config_dir)
    shutil.copytree(ROOT / "config" / recipes.PARTS_DIR, config_dir / recipes.PARTS_DIR, dirs_exist_ok=True)
    jobs = Path(recipes.jobs_dir_for(str(config_dir)))
    shutil.copytree(ROOT / "jobs" / recipes.RESEARCH_TEMPLATES, jobs / recipes.RESEARCH_TEMPLATES, dirs_exist_ok=True)


#: flat column field -> (recipe section or None for the top level, field there)
_FLAT = {
    "name": (None, "name"), "description": (None, "description"), "audience": (None, "audience"),
    "target_minutes": (None, "target_minutes"), "status": (None, "status"),
    "topic_kind": ("topic", "kind"), "topic_sources": ("topic", "sources"), "topic_fields": ("topic", "fields"),
    "research_mode": ("research", "mode"), "research_timeout_seconds": ("research", "timeout_seconds"),
    "research_language": ("research", "language"), "research_prompt": ("research", "prompt"),
    "script_style": ("writing", "style"), "default_style": ("writing", "default_style"),
    "allowed_tones": ("writing", "allowed_tones"), "revise_rounds": ("writing", "revise_rounds"),
    "audit": ("writing", "audit"),
    "voice_id": ("output", "voice_id"), "video_form": ("output", "video_form"),
    "allowed_forms": ("output", "allowed_forms"), "creative_intent": ("output", "creative_intent"),
    "source_card": ("output", "source_card"), "package": ("output", "package"), "packaging": ("output", "packaging"), "recheck": ("output", "recheck"),
}


def write_flat(config_dir: Path | str, text: str) -> None:
    """Test shorthand: a mapping of key -> flat column settings, written as one recipe file per key."""
    import yaml

    from core.video import recipes

    folder = Path(config_dir) / "video_recipes"
    folder.mkdir(parents=True, exist_ok=True)
    for key, flat in (yaml.safe_load(text) or {}).items():
        recipe: dict = {"status": "enabled"}
        for name, value in (flat or {}).items():
            if name == "writer":
                recipe.setdefault("writing", {})["pipeline"] = "single" if value == "legacy" else value
            elif name == "structure":
                recipe.setdefault("writing", {}).update(value or {})
            else:
                section, field = _FLAT[name]
                (recipe if section is None else recipe.setdefault(section, {}))[field] = value
        (folder / f"{key}.yaml").write_text(recipes.dump(recipe), encoding="utf-8")


def snapshot() -> dict:
    from core.video import columns as video_columns
    from core.video import length, writer
    from web.services import video_render, video_workflow

    out: dict = {}
    for key, column in sorted(video_columns.load(str(ROOT / "config")).items()):
        if key not in VALIDATED:
            continue
        fields = dataclasses.asdict(column)
        budget = length.budget_for(column.target_minutes, column.voice_id or None)
        out[key] = {
            "column": fields,
            "research_job": video_workflow.job_spec(column, TOPIC, "00000000aaaa"),
            "writer_system": writer.SYSTEM_PROMPT,
            "writer_request": writer._request(REPORT, TOPIC, column.script_style, column.target_minutes, budget),
            "writer_request_used": writer._request(REPORT, TOPIC, column.script_style, column.target_minutes, budget,
                                                   tuple(column.paragraph_units), column.writer_topic_fields()),
            "budget": budget,
            "package_request": video_render.package_request(column, TOPIC),
            "packaging_request": video_render.packaging_request(column, TOPIC),
        }
    return json.loads(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    if "--write" in sys.argv:
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_text(json.dumps(snapshot(), ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        print("wrote", GOLDEN)
