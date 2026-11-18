"""Read-only product audit; mutations are limited to temporary fixtures and evidence output.

Run from Base_CodingCLi. No model, network, scheduler, production DB writes or rendering.
These probes document current behavior rather than assert desired product behavior.
"""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from core import ai_dossier_sources, source_supply, sources
from core.video import audience_strategy, auto, content_columns, question_material


def run() -> list[dict]:
    config = str(ROOT / "config")
    job = yaml.safe_load((ROOT / "jobs/monitoring/ai_flash_discovery.yaml").read_text())
    job["_file"] = "monitoring/ai_flash_discovery"
    system = {"question_material": {"enabled": True}}
    results = []

    def behavior(value):
        return {
            "selection_channel": audience_strategy.job_channel(value),
            "source_supply_profile": (source_supply.for_job(config, value) or {}).get("key"),
            "question_enrichment": question_material.enabled(value, system),
            "original_reading": ai_dossier_sources.wanted(value),
        }

    clone = {**copy.deepcopy(job), "_file": "new_user/my_news"}
    results.append({"case": "identical_job_different_identifier", "original": behavior(job),
                    "clone": behavior(clone), "scope": "Only _file changed in memory; not the UI copy operation."})

    profile = source_supply.for_job(config, job)
    target = "rss:https://openai.com/news/rss.xml"
    refs = [ref for ref in job["sources"] if ref != "source:cg_openai_news"]
    declared = sources.resolve(refs, config)
    coverage = {s["spec"] for row in profile["coverage"] if job["_file"] in row.get("jobs", [])
                for s in row.get("sources", [])}
    results.append({"case": "remove_declared_openai_source", "still_declared": target in declared,
                    "still_in_coverage": target in coverage,
                    "scope": "Simulated removal of one Job reference only; source_supply.collect unions both sets."})

    original_context = {"render": {"voice_id": "old_voice"}, "render_inheritance": "source_compatible"}
    original_matrix = SimpleNamespace(key="demo", render={"video_speed": 1.0})
    changed_matrix = SimpleNamespace(key="demo", render={"video_speed": 1.5})
    before = content_columns.render_settings(config, original_matrix, original_context, {"production_mode": "standard"})
    after = content_columns.render_settings(config, changed_matrix, original_context, {"production_mode": "fast"})
    results.append({"case": "old_column_snapshot_with_current_overrides", "before": before, "after": after,
                    "scope": "Resolver boundary; video_render.create supplies live matrix/production and saved content_context."})

    inherited = content_columns.render_settings(config, original_matrix,
        {"render": {"video_speed": 1.5}}, {"video_speed": 1.25})
    results.append({"case": "column_change_masked_by_production_override", "matrix": 1.0,
                    "column": 1.5, "production": 1.25, "effective": inherited["video_speed"]})

    with tempfile.TemporaryDirectory(prefix="product-chain-probe-") as tmp:
        root = Path(tmp)
        report = root / "output/demo.md"
        report.parent.mkdir()
        report.write_text("# Saved report\n\nA complete research result.")
        os.utime(report, (200, 200))
        binding = SimpleNamespace(key="demo", job="demo/news", auto=1, usable=True,
            split="whole", auto_enabled_at=100, content_mode="individual", recipe="demo", source="",
            content_column_id="")
        # No schedule is installed or enabled. Represent an already-produced report after research stopped.
        with patch.object(auto.production, "load_all", return_value={"demo": binding}), \
             patch.object(auto.topic_store, "resolve", return_value=None), \
             patch.object(auto.topic_store, "whole_source", return_value=object()), \
             patch.object(auto.topic_store, "all_report_files", return_value=[("1970-01-01", str(report))]), \
             patch.object(auto.topic_store, "report_has_content", return_value=True), \
             patch.object(auto.report_identity, "describe", return_value={"id": "report-v1"}):
            pending = auto.pending(str(root / "config"), str(root / "jobs"), str(root / "state"), str(root), 400)
        results.append({"case": "downstream_pending_independent_of_research_schedule",
                        "pending_count": len(pending), "picks": pending[0]["picks"] if pending else [],
                        "scope": "Function-boundary fixture, not a production pause experiment or full dispatch test."})
    return results


if __name__ == "__main__":
    result = run()
    (Path(__file__).parent / "semantics-probes.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))
