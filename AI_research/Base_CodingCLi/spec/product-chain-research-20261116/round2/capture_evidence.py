"""Capture bounded source excerpts and read-only production rows for the audit."""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).parent

# Each ID is referenced by REVIEW.txt. Full-file hashes make working-tree drift visible.
EXCERPTS = {
    "E01": ("core/source_supply.py", [(117, 169), (207, 244)]),
    "E02": ("web/services/source_registry.py", [(45, 100)]),
    "E03": ("web/services/source_catalog.py", [(22, 87)]),
    "E04": ("ui/src/pages/Sources.tsx", [(44, 67), (108, 130)]),
    "E05": ("core/source_supply.py", [(22, 44)]),
    "E06": ("core/video/audience_strategy.py", [(125, 150)]),
    "E07": ("core/ai_dossier_sources.py", [(45, 70)]),
    "E08": ("core/video/question_material.py", [(55, 80)]),
    "E09": ("core/video/manuscript_workflow.py", [(51, 66), (532, 580)]),
    "E10": ("web/routes/matrices.py", [(275, 341), (537, 595)]),
    "E11": ("ui/src/pages/Matrices.tsx", [(98, 109), (370, 410), (537, 553), (573, 607), (632, 640)]),
    "E12": ("ui/src/components/ProductionConfigs.tsx", [(146, 193), (244, 295)]),
    "E13": ("ui/src/components/ContentColumns.tsx", [(100, 160), (218, 233)]),
    "E14": ("core/video/auto.py", [(38, 110)]),
    "E15": ("web/services/video_autopilot.py", [(127, 175), (270, 282), (1049, 1072)]),
    "E16": ("core/video/content_columns.py", [(161, 177), (216, 245)]),
    "E17": ("web/services/video_render.py", [(222, 258), (307, 374), (418, 432), (457, 465)]),
    "E18": ("core/video/store.py", [(327, 354), (632, 644), (802, 841), (881, 899)]),
    "E19": ("web/routes/production.py", [(50, 111), (113, 134)]),
    "E20": ("web/services/topic_progress.py", [(35, 70)]),
    "E21": ("web/routes/video.py", [(298, 322)]),
    "E22": ("core/video/joint_planning.py", [(230, 341)]),
    "E23": ("config/joint_planning.yaml", [(1, 65)]),
    "E24": ("core/production_policy.py", [(156, 265)]),
    "E25": ("core/material_collection.py", [(19, 56), (106, 123)]),
    "E26": ("config/production_policy.yaml", [(1, 50)]),
    "E27": ("core/engine.py", [(129, 165), (195, 210), (237, 280), (324, 370)]),
    "E28": ("ui/src/components/MatrixRuns.tsx", [(38, 52), (113, 139)]),
    "E29": ("core/video/production.py", [(264, 291), (303, 316)]),
    "E30": ("config/production/flash_each.yaml", [(1, 30)]),
    "E31": ("tests/test_production_policy.py", [(80, 112), (237, 275)]),
    "E32": ("tests/test_content_columns.py", [(70, 110), (146, 167)]),
    "E33": ("tests/test_joint_planning_review.py", [(93, 118), (137, 148)]),
    "E34": ("tests/test_matrices.py", [(188, 213), (244, 255)]),
    "E35": ("web/services/production_policy.py", [(15, 74)]),
    "E36": ("ui/src/components/ArticleCreate.tsx", [(50, 76)]),
}


def rows(dbfile, sql, args=()):
    with sqlite3.connect(dbfile.resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        return [dict(row) for row in db.execute(sql, args)]


def main():
    capture = {"captured_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
               "head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
               "note": "Working-tree excerpts, not a clean commit; SQLite reads use mode=ro.", "sources": []}
    text = []
    for key, (rel, ranges) in EXCERPTS.items():
        raw = (ROOT / rel).read_bytes()
        lines = raw.decode().splitlines()
        sha = hashlib.sha256(raw).hexdigest()
        capture["sources"].append({"id": key, "path": rel, "sha256": sha, "ranges": ranges})
        text.append(f"{key} | {rel} | SHA256 {sha}")
        for start, end in ranges:
            text.extend(f"{i}: {lines[i-1]}" for i in range(start, min(end, len(lines)) + 1))
            text.append("")
    (OUT / "SOURCE_EVIDENCE.txt").write_text("\n".join(text) + "\n")
    (OUT / "manifest.json").write_text(json.dumps(capture, ensure_ascii=False, indent=2) + "\n")

    plan = "4ba5d2951ed11d67b8c6c01398b35d24b92ea5e57895b1c86a584c89ee0b3757"
    runtime = {"captured_at": capture["captured_at"]}
    runtime["joint_outputs"] = rows(ROOT / "state/video.db", """
        SELECT s.id,s.column_key,s.production,s.status, s.topic, r.id render_id,
               r.title,r.status render_status,r.completed_at
        FROM scripts s LEFT JOIN renders r ON r.script_id=s.id
        WHERE json_extract(s.topic,'$.joint_plan.id')=? ORDER BY r.completed_at
    """, (plan,))
    for row in runtime["joint_outputs"]:
        topic = json.loads(row.pop("topic"))
        row["topic"] = {k: topic[k] for k in ["id", "from_item", "from_report", "episode", "joint_plan"] if k in topic}
    runtime["schedules"] = rows(ROOT / "state/schedules.db", """
        SELECT id,job_name,schedule,enabled,deleted FROM schedules
        WHERE job_name IN ('tech/tech_daily','tech/tech_weekly','source_discovery/ai_media',
                          'system/material_collection','monitoring/ai_flash_discovery')
    """)
    target = "output/flash/2026-11-16_ai_flash_discovery_1.md"
    runtime["report"] = {"path": target, "sha256": hashlib.sha256((ROOT / target).read_bytes()).hexdigest()}
    for line in reversed((ROOT / "state/report_meta.jsonl").read_text().splitlines()):
        r = json.loads(line)
        if r.get("path") == target:
            runtime["report"]["metadata"] = {k: r[k] for k in ["path", "job", "ts", "duration_seconds", "searches", "audience_strategy", "production_issue"] if k in r}
            receipt_path = Path(r["audience_strategy"]["receipt"])
            receipt = json.loads(receipt_path.read_text())
            question = receipt.get("question_material", {})
            runtime["report"]["question_material_receipt"] = {
                "path": str(receipt_path), "sha256": hashlib.sha256(receipt_path.read_bytes()).hexdigest(),
                **{k: question[k] for k in ["status", "usage", "searches", "enriched"] if k in question},
                "topic_count": len(question.get("topics", [])),
            }
            break
    hist = ROOT / "state/history/monitoring_ai_flash_discovery.jsonl"
    if hist.exists():
        for line in reversed(hist.read_text().splitlines()):
            r = json.loads(line)
            if r.get("output_path") == target:
                usage = r.get("usage", {})
                receipt = usage.get("source_supply", {})
                runtime["report"]["history"] = {
                    "ts": r.get("ts"), "status": r.get("status"),
                    "source_count": len(receipt.get("sources", {})),
                    "question_material": usage.get("question_material"),
                    "audience_strategy": usage.get("audience_strategy"),
                }
                break
    (OUT / "runtime-evidence.json").write_text(json.dumps(runtime, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"excerpts": len(EXCERPTS), "outputs": len(runtime["joint_outputs"]),
                      "report_hash": runtime["report"]["sha256"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
