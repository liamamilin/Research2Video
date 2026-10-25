"""Exercise local TTS, replayed planning, rendering and packaging on a saved script.

Network access is disabled. Model responses are read from the source attempt and
accepted only if the newly built request messages match exactly. Optional replay
delays simulate the recorded model latency, not a new live model measurement.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import time

PROJECT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-job", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--replay-latency", action="store_true")
    args = parser.parse_args()
    source, out = args.source_job.resolve(strict=True), args.output.resolve()
    if not source.is_relative_to(PROJECT) or not out.is_relative_to(PROJECT) or out.exists():
        raise ValueError("Use a preserved job and a new output directory inside the project")
    saved = json.loads((source / "input/input.json").read_text())
    if saved.get("input_mode") != "narration" or saved.get("duration_mode") != "content":
        raise ValueError("This acceptance run requires a final content-driven narration")
    inputs = out.with_name(out.name + "-input")
    inputs.mkdir(parents=True, exist_ok=False)
    for name in ("input.json", "source.txt"):
        shutil.copyfile(source / "input" / name, inputs / name)
    for key in list(os.environ):
        if key.startswith("AUTOVIDEO_G2_"):
            os.environ.pop(key)
    os.environ.update(AUTOVIDEO_G2_RUN_DIR=str(out), AUTOVIDEO_G2_RUN_ID=out.name,
               AUTOVIDEO_G2_SOURCE_FILE=str(inputs / "source.txt"), AUTOVIDEO_G2_INTENT_FILE=str(inputs / "input.json"),
               AUTOVIDEO_G2_LOCK_NARRATION="1", AUTOVIDEO_G2_DURATION_MODE="content",
               AUTOVIDEO_G2_TARGET_DURATION_SEC=str(saved["target_duration_sec"]),
               AUTOVIDEO_G2_VIDEO_FORM=saved["video_form"], AUTOVIDEO_G2_VOICE_ID=saved["voice_id"],
               AUTOVIDEO_G2_VIDEO_SPEED=str(saved.get("video_speed", 1)), AUTOVIDEO_G2_USE_DRAW_THINGS="0",
               HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    from g2 import run_g2 as pipeline
    original = source / "attempts/1/g2-run"
    records = {row["call_id"]: row for line in (original / "logs/llm_calls.jsonl").read_text().splitlines()
               if (row := json.loads(line))}

    def no_network(*args, **kwargs):
        raise RuntimeError("Network is disabled for this local acceptance replay")

    pipeline.urllib.request.urlopen = no_network

    def replay(stage, messages, schema, attempt):
        call_id = f"{stage}-attempt-{attempt}"
        request = original / "artifacts/llm_calls" / f"{call_id}.request.json"
        response = original / "artifacts/llm_calls" / f"{call_id}.response.json"
        body, reply = json.loads(request.read_text()), json.loads(response.read_text())
        if body["messages"] != messages:
            raise RuntimeError(f"Saved request differs: {call_id}; refusing replay or network fallback")
        value = pipeline.strip_schema_echo(json.loads(reply["choices"][0]["message"]["content"]), schema)
        errors = pipeline._schema_errors(schema, value)
        if errors:
            raise RuntimeError(f"Saved response failed current schema: {call_id}: {errors}")
        latency = records[call_id]["elapsed_seconds"]
        if args.replay_latency:
            time.sleep(latency)
        pipeline.write_json(pipeline.CALLS_DIR / request.name, body)
        pipeline.write_json(pipeline.CALLS_DIR / response.name, reply)
        record = {"call_id": call_id, "stage": stage, "provider": "saved_response_replay",
                  "request_sha256": pipeline.digest(request), "response_sha256": pipeline.digest(response),
                  "request_messages_verified_equal": True, "original_latency_seconds": latency,
                  "simulated_latency": args.replay_latency, "cloud_api_cost_estimate_usd": 0,
                  "peak_uncached_cost_upper_bound_usd": 0}
        pipeline.CALL_RECORDS.append(record)
        pipeline.append_log(pipeline.LOGS / "llm_calls.jsonl", record)
        return value

    pipeline.deepseek_json_call = replay
    started = time.monotonic()
    qa = pipeline.run_all()
    pipeline.run_step("package", pipeline.run_package_stage, pipeline.package_request_from_intent())
    total = time.monotonic() - started
    art = out / "artifacts"
    schedule = json.loads((art / "generation-schedule.json").read_text())
    model = json.loads((art / "model-cost.json").read_text())
    qa.update(real_llm=False, llm_call_count=0, saved_responses_replayed=len(model["calls"]),
              simulated_model_latency=args.replay_latency, scope="Local TTS and media, saved model response replay; no network")
    pipeline.write_json(art / "qa.json", qa)
    pipeline.write_json(art / "model-cost.json", {**model, "provider": "saved_response_replay", "new_llm_calls": 0})
    metrics = {"source_job": str(source.relative_to(PROJECT)), "total_seconds": round(total, 3),
               "schedule": schedule, "resources": json.loads((art / "render-resources.json").read_text()),
               "qa_status": qa["status"], "video_seconds": qa["video"]["duration_seconds"],
               "new_llm_calls": 0, "saved_responses_replayed": len(model["calls"]), "real_tts": qa["real_tts"],
               "narration_check": json.loads((art / "narration-check.json").read_text()),
               "simulated_model_latency": args.replay_latency,
               "interpretation": "Local TTS/render/package are real. Model responses and optional latency are replayed; no new live LLM test."}
    (art / "full-run-metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2) + "\n")
    pipeline.write_manifest(renderer_root=out / "renderer")
    print(json.dumps(metrics, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
