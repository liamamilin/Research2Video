"""Per-job source versions: advance the comparison baseline only after a report is saved."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path

from .fileio import atomic_write
from .search import SearchResult


def prepare(root: Path, job: str, items: list[SearchResult]) -> tuple[str, dict]:
    folder = root / hashlib.sha256(job.encode()).hexdigest()[:20]
    path = folder / "latest.json"
    previous = json.loads(path.read_text()) if path.exists() else {"items": {}}
    current = dict(previous["items"])
    changed = []
    counts = {"新增": 0, "修改": 0, "未变化": 0}
    for item in items:
        record = asdict(item)
        old = current.get(item.url)
        state = "新增" if old is None else "未变化" if old == record else "修改"
        counts[state] += 1
        current[item.url] = record
        if state != "未变化":
            before = ""
            if state == "修改":
                before = "\n上次内容：" + " ".join(old.get("excerpts") or [])[:3000]
            changed.append(replace(item, excerpts=[f"变化状态：{state}\n" + "\n".join(item.excerpts) + before]))
    from .search import SearchClient
    summary = "；".join(f"{key} {value} 条" for key, value in counts.items())
    note = ("首次采集用于建立基线，不能据此声称刚刚发布。" if not path.exists() else
            "以下仅为相对本任务上次成功报告新增或修改的材料；页面修改不等于产品刚刚发布。")
    block = f"## 信息源变化\n\n{summary}。{note}\n\n"
    block += SearchClient.format_results(changed) if changed else "本次没有新增或修改材料。请说明暂无新题材，不要重复生成旧题。"
    return block, {"folder": str(folder), "job": job, "at": datetime.now(timezone.utc).isoformat(), "items": current}


def commit(pending: dict) -> None:
    folder = Path(pending["folder"])
    value = {key: val for key, val in pending.items() if key != "folder"}
    text = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
    version = hashlib.sha256(text.encode()).hexdigest()[:20]
    atomic_write(str(folder / f"{version}.json"), text)
    atomic_write(str(folder / "latest.json"), text)
