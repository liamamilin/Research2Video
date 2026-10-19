"""Golden snapshot of what the three shipped video columns turn into (spec/video-matrix.md, phase 0).

The matrix refactor must not change what a shipped column resolves to: its fields, the research prompt a topic
fills, and the request the one-call writer sends the model. ``snapshot()`` computes them from the shipped
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

TOPIC = {
    "id": "2026-10-19/F1", "round": "2026-10-19", "title": "示例选题：某模型的限时价格",
    "fields": {"核心角度": "输入价从 2 美元涨到 4 美元", "目标受众": "开发者", "为什么有人在乎": "影响 API 成本",
               "建议大纲": "变化、影响、行动", "变化": "输入 2 美元 -> 4 美元", "发布日期": "2026-10-13",
               "来源网址": "https://example.com/pricing"},
}
REPORT = "### 1. 直接回答\n示例报告：输入价 2 美元，12 月 1 日起 4 美元。\n\n### 6. 来源清单\n1. 定价页 | 一手 | 2026-10-13 | https://example.com/pricing\n"


def shipped_flat_yaml(config_dir: Path | str = ROOT / "config") -> str:
    """The shipped cells as a flat ``video_columns.yaml``, for tests that write their own config and edit its text."""
    import yaml

    from core.video import matrix

    class Dumper(yaml.SafeDumper):
        pass

    Dumper.add_representer(str, lambda d, v: d.represent_scalar("tag:yaml.org,2002:str", v, style="|" if "\n" in v else None))
    Dumper.add_representer(list, lambda d, v: d.represent_sequence(
        "tag:yaml.org,2002:seq", v, flow_style=all(not isinstance(i, (dict, list)) for i in v)))
    flat = {cell.column.key: cell.raw for cell in matrix.load(str(config_dir)).values() if cell.column is not None}
    return yaml.dump(flat, Dumper=Dumper, allow_unicode=True, sort_keys=False, width=10000)


def snapshot() -> dict:
    from core.video import columns as video_columns
    from core.video import length, writer
    from web.services import video_render, video_workflow

    out: dict = {}
    for key, column in sorted(video_columns.load(str(ROOT / "config")).items()):
        fields = dataclasses.asdict(column)
        budget = length.budget_for(column.target_minutes, column.voice_id or None)
        out[key] = {
            "column": fields,
            "research_job": video_workflow.job_spec(column, TOPIC, "00000000aaaa"),
            "writer_system": writer.SYSTEM_PROMPT,
            "writer_request": writer._request(REPORT, TOPIC, column.script_style, column.target_minutes, budget),
            "budget": budget,
            "package_request": video_render.package_request(column, TOPIC),
        }
    return json.loads(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    if "--write" in sys.argv:
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_text(json.dumps(snapshot(), ensure_ascii=False, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        print("wrote", GOLDEN)
