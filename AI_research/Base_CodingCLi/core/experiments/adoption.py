"""The suggested change after「B 更好」(spec §9a step ⑥, §10). The module only suggests; the user saves it.

Each suggestion names a content column's config field, its current value and B's value, plus a link that opens the
column editor with the change filled in. Switches with no config field yet say so: the field is added when the
change is adopted, not before (§12 item 13).
"""
from __future__ import annotations

from typing import Optional

from core.experiments.definition import Experiment


def _field(path: str) -> Optional[str]:
    return path if path in ("notes", "presentation.strategy") or path.startswith("render.") else None


def _get(data: dict, path: str):
    node = data
    for part in path.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node


def suggestions(experiment: Experiment, columns: dict, used_columns: set[str]) -> list[dict]:
    """One entry per write path and content column the experiment reached.

    ``columns`` maps content column key → its config (``ContentColumn.as_dict()``-like with ``matrix``).
    """
    import json
    from urllib.parse import quote
    out = []
    targets = [c for c in (experiment.scope.columns or sorted(used_columns)) if c in columns]
    for path, value in experiment.arms["B"].overrides.items():
        field = _field(path)
        if field is None:
            out.append({"path": path, "to": value, "column": "", "editable": False,
                        "reason": "这个开关还没有对应的栏目配置字段；决定采用时需要先加上这个字段"})
            continue
        for key in targets:
            column = columns[key]
            prefill = quote(json.dumps({field: value}, ensure_ascii=False))
            out.append({"path": path, "column": key, "column_name": column.get("name") or key,
                        "from": _get(column, field), "to": value, "editable": True,
                        "link": f"/matrices/{column.get('matrix') or ''}?tab=columns&column={key}"
                                f"&prefill={prefill}&experiment={experiment.id}"})
        if not targets:
            out.append({"path": path, "to": value, "column": "", "editable": False,
                        "reason": "实验里的视频都没有内容栏目，没有可以预填的栏目配置"})
    return out
