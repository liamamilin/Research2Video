"""Run one isolated writer replay, preserving all model calls and revisions."""
from __future__ import annotations

import argparse
from dataclasses import asdict, replace
import hashlib
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[4]
BASE = ROOT / "state/acceptance-content-value-20261115/natural-writing-20261116"


def save(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def run(variant: str, name: str, attempt: str = "", round_name: str = "round1") -> None:
    workspace_round = "1" if variant == "control" else round_name.removeprefix("round")
    work = Path("/tmp/natural-writing-20261116-r" + workspace_round) / variant
    out = BASE / round_name / "results" / name / (variant + ("-" + attempt if attempt else ""))
    if out.exists():
        raise FileExistsError(f"Preserve attempts: {out}")
    out.mkdir(parents=True)
    sys.path.insert(0, str(work))
    os.chdir(work)
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=False)
    from web.settings import get_settings
    settings = get_settings()
    for folder in ("config", "jobs", "state", "logs"):
        setattr(settings.paths, folder + "_dir", str(work / folder))
    from core import activity, llm, telemetry
    activity.use_state_dir(str(work / "state"))
    telemetry.use_state_dir(str(work / "state"))
    import yaml
    system_config = yaml.safe_load((work / "config/system.yaml").read_text())
    cfg = llm.LLMConfig.from_system(system_config, "ai")
    save(out / "configuration.json", {"model": cfg.model, "base_url": cfg.base_url,
         "max_tokens": cfg.max_tokens, "timeout": cfg.timeout, "request_timeout": cfg.request_timeout,
         "fallback_models": cfg.fallback_models, "fallback_providers": cfg.fallback_providers,
         "variant": variant, "evidence_level": "expression experiment"})

    class RecordedClient(llm.LLMClient):
        def chat(self, messages, **kwargs):
            stamp = str(time.time_ns())
            save(out / "calls" / f"{stamp}-request.json", {"messages": messages, "options": kwargs, "model": self.config.model})
            before = time.monotonic()
            try:
                response = super().chat(messages, **kwargs)
            except Exception as exc:
                save(out / "calls" / f"{stamp}-error.json", {"type": type(exc).__name__, "seconds": time.monotonic()-before})
                raise
            save(out / "calls" / f"{stamp}-response.json", {"content": response.content,
                 "model": getattr(response, "model", ""), "usage": dict(self.usage),
                 "seconds": time.monotonic()-before, "model_switches": list(getattr(self, "model_switches", []))})
            return response

    llm.LLMClient = RecordedClient
    from core.video import columns, content_columns, manuscript_workflow, polish, story_pool
    data = json.loads((BASE / "round1/inputs" / f"{name}.json").read_text())
    script = data["script"]
    if data.get("context_source") == "current_production":
        script = {**script, "content_context": content_columns.snapshot(str(work / "config"), script["production"], script["content_context"]["job"])}
    topic = script["topic"]
    context = script.get("content_context") or {}
    profile = manuscript_workflow.profile_for(script, str(work / "config"), str(work / "jobs"), system_config.get("video") or {})
    if profile and profile.draft and context.get("column"):
        context = {**context, "notes": profile.draft}
    column = columns.get(str(work / "config"), script["column_key"])
    column = content_columns.apply_script_context(column, context)
    if profile and profile.draft and not context.get("column"):
        column = replace(column, script_style=column.script_style + "\n" + profile.draft)
    if data["allocation_direction"]:
        column = replace(column, script_style=column.script_style + "\n" + data["allocation_direction"])
    column = replace(column, script_style=column.script_style + "\n" + story_pool.brief(topic["story"]))
    inputs = {"column": column, "content_context": script.get("content_context") or {},
              "report": data["report"], "citation_check": None, "topic": topic, "llm_config": cfg}
    if data["original_material"] is not None:
        inputs["original_material"] = data["original_material"]
    save(out / "input-receipt.json", {"source_script": script["id"], "topic": topic,
         "profile": profile.version if profile else None, "column_style": column.script_style,
         "report_sha256": data["report_sha256"], "material_sha256": data["material_sha256"],
         "input_kind": "report_and_material" if data["original_material"] is not None else "selected_original_material"})
    started = time.monotonic()
    save(out / "status.json", {"status": "running", "pid": os.getpid(), "started_at": time.time()})
    try:
        if profile:
            inputs["on_stage"] = lambda stage, value: save(out / "stages" / f"{stage}.json", value)
        result = manuscript_workflow.write(mode=(system_config.get("video") or {}).get("manuscript_workflow"), profile=profile, **inputs)
        save(out / "before-polish.json", asdict(result))
        polish.apply(result, cfg, voice=column.voice_id or None)
        save(out / "script.json", asdict(result))
        body = result.title + "\n\n" + "\n\n".join(s["title"] + "\n" + "\n".join(p["text"] for p in s["paragraphs"]) for s in result.sections)
        (out / "script.txt").write_text(body + "\n")
        save(out / "status.json", {"status": "complete", "pid": os.getpid(), "seconds": time.monotonic()-started,
             "title": result.title, "chars": len(body)})
        print(json.dumps({"case": name, "variant": variant, "status": "complete", "title": result.title}, ensure_ascii=False), flush=True)
    except Exception as exc:
        save(out / "status.json", {"status": "failed", "pid": os.getpid(), "type": type(exc).__name__, "seconds": time.monotonic()-started})
        print(json.dumps({"case": name, "variant": variant, "status": "failed", "type": type(exc).__name__}), flush=True)
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("variant", choices=["control", "candidate"])
    parser.add_argument("case", choices=["flash-svg", "tutorial-share", "flash-code", "flash-lattice", "t05-voice"])
    parser.add_argument("--attempt", default="", choices=["", "attempt2"])
    parser.add_argument("--round", dest="round_name", default="round1", choices=["round1", "round2", "round3", "round4", "round5", "round6", "round7", "round8"])
    args = parser.parse_args()
    run(args.variant, args.case, args.attempt, args.round_name)
