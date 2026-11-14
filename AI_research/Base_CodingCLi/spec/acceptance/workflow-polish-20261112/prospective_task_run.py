"""Compare authors on a previously untested single from the real candidate queue."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import shutil

import yaml

from manuscript_trial import TASK_COMPARISON, blind
from opening_run import ROOT, save, setup


def copy_inputs(out: Path, report: str) -> None:
    dest = out / report
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / report, dest)
    meta = out / "state/report_meta.jsonl"
    entries = [json.loads(line) for line in meta.read_text().splitlines()]
    for row in entries:
        material = row.get("material") or {}
        path = Path(material.get("path", ""))
        if path.is_file() and path.name.startswith(dest.stem + ".materials-"):
            destination = dest.parent / path.name
            shutil.copy2(path, destination)
            row["material"] = {**material, "path": str(destination)}
    meta.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in entries))


def main(out: Path, candidates: Path, report: str, codes: list[str]) -> None:
    if out.exists():
        raise FileExistsError(f"Use a fresh isolated directory: {out}")
    queue = next(row for row in json.loads(candidates.read_text()) if row["report"] == report)
    item = queue["items"][0]
    original = item["topic"]
    choices = {t["code"]: t for t in (original, *original.get("_story_fallbacks", []))}
    selected = [copy.deepcopy(choices[code]) for code in codes]
    if any(topic.get("from_report") != report or not topic.get("from_item") for topic in selected):
        raise ValueError("The retained candidates must be single items from the specified report")
    setup(out)
    copy_inputs(out, report)
    config_path = out / "config/system.yaml"
    config = yaml.safe_load(config_path.read_text())
    config["video"].pop("manuscript_workflow", None)
    config["video"]["manuscript_jobs"] = []
    config_path.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False))

    from core.video import store, writer
    from web.services import video_workflow

    write = writer.write_for_column
    receipts = []

    def capture(**inputs):
        audience = inputs["topic"]["fields"].get("目标受众") or "普通观众"
        receipt = {"audience": audience, "model": inputs["llm_config"].model,
                   "story": (inputs["topic"].get("story") or {}).get("event_key"),
                   "topic_id": inputs["topic"]["id"]}
        for field in ("report", "original_material", "column_style"):
            receipt[field + "_sha256"] = hashlib.sha256((inputs.get(field) or "").encode()).hexdigest()
        receipts.append(receipt)
        save(out / "input-receipts.json", receipts)
        return write(**inputs)

    writer.write_for_column = capture
    video_workflow.spawn = lambda script_id, started_by: video_workflow.run(script_id, started_by=started_by)
    attempts = []
    for topic in selected:
        topic.pop("_story_fallbacks", None)
        if item.get("production"):
            topic["_production"] = item["production"]
        started = video_workflow.start(item["column_key"], topic, "autopilot")
        current = store.get_script(started["id"])
        attempts.append({key: current[key] for key in ("id", "status", "error", "current_version")})
        attempts[-1]["candidate"] = topic["code"]
        save(out / "attempts.json", attempts)
        if current["status"] == "DRAFT":
            break
        if not (current.get("error") or "").startswith("候选主事件均已占用或覆盖"):
            raise RuntimeError(current.get("error") or current["status"])
    else:
        return
    script_id = current["id"]
    first = store.get_version(script_id, current["current_version"])
    save(out / "default-version.json", first)
    fields = ("title", "sections", "length_check", "meta")
    save(out / "same-input.json", {key: first[key] for key in fields})
    writer.SYSTEM_PROMPT = writer.SYSTEM_PROMPT.replace(writer.CONCRETE_RULE, TASK_COMPARISON)
    save(out / "author-task.json", {"instruction": TASK_COMPARISON})
    status = video_workflow.run_rewrite(script_id, started_by="autopilot")
    current = store.get_script(script_id)
    if status != "DRAFT" or current["current_version"] <= first["version"]:
        raise RuntimeError(current.get("error") or status)
    second = store.get_version(script_id, current["current_version"])
    save(out / "saved-version.json", second)
    if len(receipts) != 2 or receipts[0] != receipts[1]:
        raise RuntimeError("The default author and rewrite received different inputs")
    save(out / "input-receipt.json", receipts[0])
    save(out / "generated.json", {key: second[key] for key in fields})
    blind(out, script_id[:8])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("candidates", type=Path)
    parser.add_argument("report")
    parser.add_argument("codes", nargs="+")
    args = parser.parse_args()
    main(args.output.resolve(), args.candidates, args.report, args.codes)
