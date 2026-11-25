"""Replay the existing fetch-only researcher and normal writer on frozen source material."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3

import yaml

from opening_run import ROOT, save, setup


CASE_BOUNDARIES = (
    "同一来源有多次演示或测试时，在「具体材料」里逐次说明谁做的、测了哪个对象、"
    "做了什么、观察到什么。换了型号、任务或测试条件，重新写明对象，别用「两者」承接前一次。"
    "写得足够具体，后续作者才可以比较两个结果，解释它们如何改变观众的判断。"
)


def main(out: Path, prefix: str, separate_tests: bool) -> None:
    if out.exists():
        raise FileExistsError(f"Use a fresh isolated directory: {out}")
    setup(out)

    from core import report_material, report_meta
    from core.report_material import digest
    from core.config import load_job
    from core.engine import ResearchEngine
    from core.extractor import extract_report
    from core.llm import LLMConfig
    from core.research import ResearchAgent, ResearchConfig, report_problem
    from core.search import SearchConfig
    from core.video import store, topic_store, writer
    from web.services import video_workflow

    with sqlite3.connect(f"file:{ROOT / 'state/video.db'}?mode=ro", uri=True) as db:
        (script_id,), = db.execute("SELECT id FROM scripts WHERE id LIKE ?", (prefix + "%",))
    original = store.get_script(script_id)
    report = original["report_path"]
    record = report_meta.get(report)
    source_path = ROOT / report
    source_text = source_path.read_text()
    raw = report_material.read(record["material"], str(source_path), source_text)
    if not raw:
        raise RuntimeError("The original report has no intact automatic source material")
    job_key = original["content_context"]["job"]
    config_path = out / "config/system.yaml"
    config = yaml.safe_load(config_path.read_text())
    config["video"].pop("manuscript_workflow", None)
    config["video"]["manuscript_jobs"] = []
    config_path.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False))
    day = source_path.name[:10]
    engine = ResearchEngine(str(out / "config"), str(out / "jobs"), workspace_dir=str(out), date_override=day)
    job = load_job(str(out / "jobs"), job_key)
    base_prompt = engine._build_prompt(job)
    prompt = base_prompt + ("\n\n" + CASE_BOUNDARIES if separate_tests else "")
    save(out / "research-input.json", {"original_report": report, "job": job_key, "source_sha256": digest(raw),
         "base_prompt_sha256": digest(base_prompt), "research_prompt_sha256": digest(prompt),
         "addition": CASE_BOUNDARIES if separate_tests else "", "source_chars": len(raw)})
    agent = ResearchAgent(LLMConfig.from_system(config, "ai"), SearchConfig.from_system(config),
                          research_config=ResearchConfig.from_system(config), workspace_dir=str(out))
    try:
        result = agent.run_fetch_only(prompt, sources=raw)
        cleaned = extract_report(result, config)
        if problem := report_problem(cleaned):
            raise RuntimeError(problem)
        citation = engine._check_citations(cleaned, agent)
        usage = agent.get_usage()
    finally:
        agent.close()
    dest = out / report
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(cleaned)
    material = report_material.save(str(dest), cleaned, [raw])
    report_meta.append_record(report, job_key, usage=usage, extra={"citation_check": citation, "material": material})
    candidates = topic_store.parse_items(cleaned, day, job["topics"]["prefix"])
    if not candidates:
        raise RuntimeError("The replay produced no programme candidates")
    save(out / "research-result.json", {"report_sha256": digest(cleaned), "usage": usage,
         "candidate_titles": [item.title for item in candidates]})

    topic = dict(original["topic"])
    topic["episode"] = {**topic["episode"], "items": [{"id": item.id, "title": item.title} for item in candidates]}
    fresh = store.create_script(original["column_key"], topic, created_by="autopilot",
                                production=original["production"], content_context=original["content_context"])
    author = writer.write_for_column

    def capture(**inputs):
        save(out / "author-input.json", {"report_sha256": digest(inputs["report"]),
             "original_material_sha256": digest(inputs.get("original_material") or ""),
             "column_style_sha256": digest(inputs["column_style"]), "model": inputs["llm_config"].model})
        return author(**inputs)

    writer.write_for_column = capture
    status = video_workflow.run(fresh["id"], started_by="autopilot")
    current = store.get_script(fresh["id"])
    save(out / "outcome.json", {k: current[k] for k in ("id", "status", "current_version", "error")})
    if status != "DRAFT" or current["current_version"] != 1:
        raise RuntimeError(current["error"] or status)
    save(out / "saved-version.json", store.get_version(fresh["id"], 1))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("script_prefix")
    parser.add_argument("--separate-tests", action="store_true")
    args = parser.parse_args()
    main(args.output.resolve(), args.script_prefix, args.separate_tests)
