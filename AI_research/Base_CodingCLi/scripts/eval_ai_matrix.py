"""Produce the eight AI-column samples through the existing research/video chain.

Run reports, scripts, then media with --output state/validation/<name>.
Use ../.AI_research/bin/python, which includes the production screenshot runtime.
Ledgers, attempts and engine jobs are isolated; no publisher is started.
"""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import yaml

from scripts.eval_video_chain import attempt, record, report_case, save, validation_output

BINDINGS = {
    "flash": ("flash_daily", "monitoring/ai_flash_discovery", "flash"),
    "opensource": ("ai_media_opensource_digest", "ai_media/daily_opensource_radar", "radar"),
    "models": ("ai_media_arena_digest", "ai_media/weekly_model_arena", "arena"),
    "business": ("ai_media_business_digest", "ai_media/weekly_business_watch", "business"),
    "deep": ("ai_media_deep_dive", "ai_media/weekly_deep_research", "deep"),
}
CASES = {f"{kind}-{number}": (kind, number) for kind, count in
         (("flash", 2), ("opensource", 2), ("models", 2), ("business", 1), ("deep", 1))
         for number in range(1, count + 1)}


def isolate(out: Path) -> None:
    from core import events, report_meta, state
    from core.video import store
    from web.settings import get_settings, reset_settings

    isolated = out / "isolated-state"
    isolated.mkdir(parents=True, exist_ok=True)
    web_config = out / "web.yaml"
    if not web_config.exists():
        web_config.write_text(yaml.safe_dump({
            "auth": {"secret_key": uuid4().hex + uuid4().hex},
            "paths": {"config_dir": str(ROOT / "config"), "jobs_dir": str(ROOT / "jobs"),
                      "state_dir": str(isolated), "output_dir": str(out / "output"),
                      "logs_dir": str(out / "logs")}}, allow_unicode=True), encoding="utf-8")
        web_config.chmod(0o600)
    os.environ["AI_RESEARCH_WEB_CONFIG"] = str(web_config)
    os.environ["AI_RESEARCH_ENV"] = "development"
    os.environ["AREC_NO_AUTOPILOT_LOOP"] = "1"
    reset_settings()
    get_settings()
    state.use_state_dir(str(isolated))
    events.use_state_dir(str(isolated))
    report_meta.META_DIR, report_meta.META_FILE = str(isolated), str(isolated / "report_meta.jsonl")
    store.use_state_dir(str(isolated))
    store.init_db()


def script_case(out: Path, key: str, index: dict) -> dict:
    from core import report_material, report_meta
    from core.video import content_columns, content_guard, production, reports, store, topic_store
    from web.services import video_workflow

    kind, number = CASES[key]
    production_id, job, folder = BINDINGS[kind]
    report = index.get("reports", {}).get(job, {})
    if report.get("status") != "COMPLETED":
        raise ValueError(f"Generate the report first: {job}")
    path = Path(report["report"])
    # A second whole-report work uses the previous real research edition;
    # its focus is still chosen by the ordinary author, not a hand-written script.
    if number == 2 and kind != "flash":
        existing = sorted((ROOT / "output" / folder).glob("*.md"),
                          key=lambda p: p.stat().st_mtime, reverse=True)
        if not existing:
            raise ValueError(f"No previous edition for {kind}")
        path = existing[0]
    cfg = production.get(str(ROOT / "config"), str(ROOT / "jobs"), production_id)
    context = content_columns.snapshot(str(ROOT / "config"), production_id, job)
    work = attempt(out, "scripts", key)
    source_path = path
    if kind != "flash":
        # Samples stay out of production history. Reuse its existing material
        # selection here with this trial's earlier works as the local memory.
        earlier = set(list(CASES)[:list(CASES).index(key)])
        blocked = set().union(*(content_guard.cited_sources(item["version"]["sections"])
                                for other, item in index.get("scripts", {}).items()
                                if other in earlier and item.get("version")))
        body = path.read_text()
        reference = (report_meta.get(str(path)) or {}).get("material") or {}
        if not reference:
            for snapshot in path.parent.glob(path.stem + ".materials-*.json"):
                value = json.loads(snapshot.read_text())
                if value.get("report_sha256") == report_material.digest(body):
                    reference = {"path": str(snapshot), "sha256": report_material.digest(value["material"]),
                                 "report_sha256": value["report_sha256"]}
                    break
        material = report_material.read(reference, str(path), body)
        selected = content_guard.filter_material(material or body, blocked)
        path = work / "selected-material.md"
        path.write_text(selected, encoding="utf-8")
        save(work / "selection.json", {"source_report": str(source_path), "excluded_sources": sorted(blocked),
                                        "input_kind": "original_material" if material else "report"})
    recorded = reports.record_file(str(path), job=job, created_by="chain-validation")
    topic = {"title": reports.title_of(str(path)), "test_sample": True,
             "fields": {"目标受众": context["audience"]}, "from_report": str(path),
             "report_id": recorded["id"]}
    if kind == "flash":
        source = topic_store.resolve(str(ROOT / "jobs"), job)
        topics = topic_store.parse(source, path.read_text(), datetime.now().strftime("%Y-%m-%d"))
        if len(topics) < number:
            raise ValueError(f"Discovery supplied only {len(topics)} topics")
        chosen = topics[number - 1]
        topic.update(title=chosen.title, from_item=chosen.id, from_item_title=chosen.title)
        topic["fields"].update(chosen.fields)
        topic["fields"]["目标受众"] = context["audience"]
    script = store.create_script(cfg.recipe, topic, created_by="chain-validation",
                                 production=production_id, content_context=context)
    save(work / "input.json", {"script_id": script["id"], "production": production_id,
                               "report": str(path), "content_context": context})
    started = time.monotonic()
    status = video_workflow.run(script["id"], started_by="chain-validation")
    script = store.get_script(script["id"])
    version = store.get_version(script["id"], script["current_version"]) if script["current_version"] else None
    result = {"status": status, "work": str(work), "script": script, "version": version,
              "seconds": round(time.monotonic() - started, 2), "report": str(path), "source_report": str(source_path)}
    save(work / "workflow.json", result)
    if version:
        body = "\n\n".join(p["text"] for s in version["sections"] for p in s["paragraphs"])
        (work / "script.md").write_text("# " + version["title"] + "\n\n" + body, encoding="utf-8")
        result.update(title=version["title"], characters=len(body), script_file=str(work / "script.md"))
    return result


def media_case(out: Path, key: str, index: dict) -> dict:
    import importlib.util

    if importlib.util.find_spec("playwright") is None:
        raise RuntimeError("Use ../.AI_research/bin/python for media: the sample needs the production screenshot runtime")
    from core.video import store
    from core.video.autovideo import AutoVideoClient
    from web.services import video_executor, video_render

    previous = index.get("scripts", {}).get(key, {})
    if previous.get("status") != "DRAFT":
        raise ValueError(f"Generate the script first: {key}")
    script = store.get_script(previous["script"]["id"])
    video_executor.advance_auto(script, {}, can_start_script=True, can_start_render=False)
    script = store.get_script(script["id"])
    if script["status"] != "APPROVED":
        raise ValueError(f"Automatic workflow did not approve sample: {script['status']}")
    work = attempt(out, "media", key)
    auto_root = ROOT.parents[1] / "autovideo"
    sys.path.insert(0, str(auto_root))
    import httpx
    from fastapi.testclient import TestClient
    from g3.app import create_app
    from g3.config import Settings

    engine_root = (auto_root / "g3/data/ai-matrix-productization-20261027").resolve()
    failed = index.get("media", {}).get(key, {})
    retrying = failed.get("status") == "FAILED" and bool(failed.get("engine_job_id"))
    if retrying:
        data = Path(failed["engine_data"]).resolve()
        render = store.get_render(failed["render_id"])
        if (data.parent != engine_root or not Path(failed["work"]).resolve().is_relative_to(out.resolve())
                or not render or render["script_id"] != script["id"] or render["status"] != "FAILED"
                or render["autovideo_job_id"] != failed["engine_job_id"]):
            raise ValueError("Retry must belong to this isolated sample and its failed engine job")
        save(work / "retry-of.json", failed)
        save(work / "previous-render.json", render)
    else:
        data = engine_root / uuid4().hex
        data.mkdir(parents=True)
        shutil.copyfile(auto_root / "g3/data/llm-settings.json", data / "llm-settings.json")
        (data / "llm-settings.json").chmod(0o600)
    settings = Settings(data_dir=data, runner_timeout_seconds=3600)
    started = time.monotonic()
    with TestClient(create_app(settings, start_worker=True)) as engine:
        def transport(request):
            response = engine.request(request.method, request.url.raw_path.decode(),
                                      content=request.content, headers=dict(request.headers))
            return httpx.Response(response.status_code, content=response.content, headers=response.headers)
        client = AutoVideoClient("http://127.0.0.1:8011", transport=httpx.MockTransport(transport))
        video_render.get_client = lambda: client
        if retrying:
            # The existing engine retry retains attempts and can reuse accepted
            # chapter plans with identical inputs. Only this trial's ledger is
            # reopened so its normal poll/download path follows that attempt.
            response = engine.post(f"/api/jobs/{failed['engine_job_id']}/retry")
            response.raise_for_status()
            store.update_render(render["id"], status="QUEUED", error_code=None,
                                error_message=None, completed_at=None, active_stage=None)
            render = store.get_render(render["id"])
        else:
            render = video_render.create(script)
        save(work / "request.json", render["request"])
        record(out / "validation-index.json", "media", key, {
            "status": "RUNNING", "work": str(work), "render_id": render["id"],
            "engine_data": str(data), "script_id": script["id"]})
        print(json.dumps({"stage": "media", "key": key, "render_id": render["id"],
                          "engine_data": str(data)}, ensure_ascii=False), flush=True)
        status = video_render.process(render["id"], client=client)
        finished = store.get_render(render["id"])
        save(work / "render.json", finished)
        job_id = finished.get("autovideo_job_id")
        if job_id:
            job = engine.get(f"/api/jobs/{job_id}").json()
            save(work / "job.json", job)
            for name in ("video-ir.json", "qa.json", "package-qa.json"):
                response = engine.get(f"/api/jobs/{job_id}/artifacts/{name}")
                if response.status_code == 200:
                    (work / name).write_bytes(response.content)
            for name in ("production-execution.json", "explanation-plan.json", "hybrid-card-plan.json",
                         "scene-assembly.json", "story-direction.json"):
                attempts = sorted((data / "jobs" / job_id / "attempts").glob("*/g2-run/artifacts/" + name))
                if attempts:
                    shutil.copyfile(attempts[-1], work / name)
    return {"status": status, "work": str(work), "render_id": render["id"],
            "engine_job_id": job_id, "engine_data": str(data), "script_id": script["id"],
            "seconds": round(time.monotonic() - started, 2), "error": finished.get("error_message")}


def review_case(out: Path, key: str, index: dict) -> dict:
    from PIL import Image, ImageDraw
    from web.services.video_render import files_dir

    media = index.get("media", {}).get(key, {})
    if media.get("status") != "COMPLETED":
        raise ValueError(f"Finish media first: {key}")
    work = Path(media["work"])
    source = files_dir(media["render_id"])
    auto_root = ROOT.parents[1] / "autovideo"
    ffmpeg, ffprobe = (str(auto_root / "g0/darwin_arm64" / name) for name in ("ffmpeg", "ffprobe"))
    for name in ("video.mp4", "content.mp4"):
        shutil.copyfile(source / name, work / name)
        with (work / (name + ".decode.log")).open("w") as log:
            subprocess.run([ffmpeg, "-nostdin", "-v", "error", "-i", str(work / name), "-f", "null", "-"],
                           stdout=log, stderr=log, check=True, timeout=600)
    probe = json.loads(subprocess.check_output([ffprobe, "-v", "error", "-show_streams", "-show_format",
                                               "-of", "json", str(work / "content.mp4")]))
    save(work / "probe.json", probe)
    ir = json.loads((work / "video-ir.json").read_text())
    duration = float(probe["format"]["duration"])
    scale = duration / (ir["video"]["duration_ms"] / 1000)
    targets = {}
    for scene in ir["scenes"]:
        fractions = (.35, .8) if scene["component"] in {
            "object_showcase", "data_chart", "price_change", "flow_diagram", "comparison"} else (.7,)
        for fraction in fractions:
            seconds = (scene["start_ms"] + scene["duration_ms"] * fraction) / 1000 * scale
            frame = min(round(seconds * 30), int(duration * 30) - 2)
            targets[frame] = {"scene": scene["id"], "component": scene["component"],
                              "state": fraction, "seconds": round(seconds, 3)}
    review = attempt(out, "review", key)
    frames = review / "frames"
    frames.mkdir()
    expression = "+".join(f"eq(n\\,{n})" for n in sorted(targets))
    subprocess.run([ffmpeg, "-nostdin", "-v", "error", "-i", str(work / "content.mp4"),
                    "-vf", f"select={expression},scale=1280:-1", "-vsync", "0", str(frames / "frame-%03d.png")],
                   check=True, timeout=600)
    images = sorted(frames.glob("*.png"))
    if len(images) != len(targets):
        raise RuntimeError("Frame extraction count differs from selected picture states")
    rows = [{"file": str(path), "frame": frame, **metadata}
            for path, (frame, metadata) in zip(images, sorted(targets.items()))]
    contacts = []
    for start in range(0, len(images), 12):
        group = rows[start:start + 12]
        sheet = Image.new("RGB", (1920, ((len(group) + 3) // 4) * 294), "#edf1ec")
        draw = ImageDraw.Draw(sheet)
        for number, item in enumerate(group):
            picture = Image.open(item["file"]).convert("RGB").resize((480, 270))
            x, y = number % 4 * 480, number // 4 * 294
            sheet.paste(picture, (x, y))
            draw.text((x + 8, y + 273), f"{item['scene']} | {item['component']} | {item['seconds']}s", fill="#182720")
        path = review / f"contact-{start // 12 + 1}.png"
        sheet.save(path)
        contacts.append(str(path))
    save(review / "frames.json", rows)
    result = {"status": "COMPLETED", "work": str(review), "video": str(work / "video.mp4"),
              "content": str(work / "content.mp4"), "seconds": duration, "frames": len(rows),
              "contacts": contacts, "components": [s["component"] for s in ir["scenes"]],
              "visual_review": "pending", "render_id": media["render_id"]}
    save(review / "result.json", result)
    return result


def catalog(out: Path, index: dict) -> dict:
    labels = {"flash": "AI 快讯", "opensource": "AI 开源发现", "models": "AI 模型观察",
              "business": "AI 产业观察", "deep": "AI 深度解读"}
    completed = sum(v.get("status") == "COMPLETED" for v in index.get("media", {}).values())
    viewed = sum(v.get("visual_review") == "reviewed" for v in index.get("review", {}).values())
    lines = ["# AI 矩阵产品化 · 首轮代表作品", "", f"制作完成 {completed}/8；实际抽帧审看 {viewed}/8。", "",
             "任务使用隔离状态，样片不进入发布队列。每条作品的研究、写稿与制作尝试独立保留。", "",
             "| 栏目 | 本期标题 | 制作状态 | 内容片时长 |", "| --- | --- | --- | --- |"]
    for key, (kind, _) in CASES.items():
        script = index.get("scripts", {}).get(key, {})
        media = index.get("media", {}).get(key, {})
        review = index.get("review", {}).get(key, {})
        status = {"COMPLETED": "已完成", "RUNNING": "制作中", "FAILED": "本次失败"}.get(media.get("status"), "排队中")
        seconds = f"{review['seconds']:.1f} 秒" if review.get("seconds") else "—"
        title = script.get("title", key).replace("|", " / ")
        lines.append(f"| {labels[kind]} | {title} | {status} | {seconds} |")
    for key, (kind, _) in CASES.items():
        script = index.get("scripts", {}).get(key, {})
        review = index.get("review", {}).get(key, {})
        lines += ["", f"## {labels[kind]} · {key}", "", script.get("title", key), ""]
        if script.get("script_file"):
            lines.append(f"[完整文稿](<{script['script_file']}>)")
        if review.get("video"):
            lines.append(f"[包装成片](<{review['video']}>) · [内容片](<{review['content']}>)")
            lines += [f"[实际画面总览 {n}](<{path}>)" for n, path in enumerate(review.get("contacts", []), 1)]
        if review.get("observations"):
            lines += ["", *[f"- {note}" for note in review["observations"]]]
    path = out / "作品目录.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"catalog": str(path), "completed": completed, "visually_reviewed": viewed}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("reports", "scripts", "media", "review", "catalog"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--only", default="")
    parser.add_argument("--skip-completed", action="store_true")
    parser.add_argument("--watch", action="store_true", help="For review, collect completed media until the finite batch ends")
    args = parser.parse_args()
    out = validation_output(args.output)
    out.mkdir(parents=True, exist_ok=True)
    isolate(out)
    index_path = out / "validation-index.json"
    index = json.loads(index_path.read_text()) if index_path.exists() else {}
    if args.stage == "catalog":
        print(json.dumps(catalog(out, index), ensure_ascii=False))
        return 0
    if args.watch:
        if args.stage != "review" or args.only:
            parser.error("--watch is only for the whole review batch")
        while True:
            index = json.loads(index_path.read_text())
            for key in CASES:
                media = index.get("media", {}).get(key, {})
                reviewed = index.get("review", {}).get(key, {})
                if media.get("status") == "COMPLETED" and reviewed.get("render_id") != media.get("render_id"):
                    try:
                        result = review_case(out, key, index)
                    except Exception as exc:
                        logging.exception("Review collection failed: %s", key)
                        result = {"status": "FAILED", "error": f"{type(exc).__name__}: {exc}",
                                  "render_id": media["render_id"]}
                    index = record(index_path, "review", key, result)
                    print(json.dumps({"stage": "review", "key": key, "status": result["status"]}), flush=True)
            if all(index.get("media", {}).get(key, {}).get("status") in {"COMPLETED", "FAILED"} for key in CASES):
                return int(any(index.get("review", {}).get(key, {}).get("status") != "COMPLETED" for key in CASES))
            time.sleep(15)
    keys = [item[1] for item in BINDINGS.values()] if args.stage == "reports" else list(CASES)
    if args.only:
        if args.only not in keys:
            parser.error("Unknown job or sample")
        keys = [args.only]
    errors = 0
    for key in keys:
        if args.skip_completed and index.get(args.stage, {}).get(key, {}).get("status") in ("COMPLETED", "DRAFT"):
            continue
        print(json.dumps({"stage": args.stage, "key": key, "status": "STARTING"}), flush=True)
        try:
            if args.stage == "reports":
                result = report_case(out, key)
            elif args.stage == "scripts":
                result = script_case(out, key, index)
            elif args.stage == "media":
                result = media_case(out, key, index)
            else:
                result = review_case(out, key, index)
        except Exception as exc:
            logging.exception("Sample failed: %s", key)
            prior = index.get(args.stage, {}).get(key, {})
            retained = prior if args.stage == "media" and prior.get("status") == "FAILED" else {}
            result = {**retained, "status": "FAILED", "error": f"{type(exc).__name__}: {exc}"}
        index = record(index_path, args.stage, key, result)
        errors += result["status"] not in ("COMPLETED", "DRAFT")
        print(json.dumps({"stage": args.stage, "key": key, "status": result["status"],
                          "seconds": result.get("seconds"), "error": result.get("error")}, ensure_ascii=False), flush=True)
    return int(bool(errors))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    raise SystemExit(main())
