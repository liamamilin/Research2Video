"""Fixed, synthetic console workload. Only called after web_env redirects all storage.

No production config/data copies, credentials, providers, media generation or workers.
The tiny .mp4 placeholders exercise file availability checks, not media decoding.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import yaml

MANIFEST = Path(__file__).parent / "fixtures/console_workload.json"


def seed(root: Path) -> dict:
    from core import telemetry
    from core.schedule_store import ScheduleStore
    from core.video import store, autopilot_store
    from web.indexer import db as index_db
    from web.services.assistant_store import AssistantStore

    spec = json.loads(MANIFEST.read_text())
    config, jobs, output = (root / name for name in ("config", "jobs", "output"))
    stamp = spec["timestamp"]
    epoch = datetime.fromisoformat(stamp).timestamp()

    def write(path: Path, value: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(value, allow_unicode=True, sort_keys=False))

    write(config / "system.yaml", {"ai": {"model": "fixture-model"}, "search": {"provider": "disabled"}})
    write(config / "video_recipes/flash.yaml", {
        "name": "固定快讯", "status": "enabled", "audience": "开发者", "target_minutes": 1.25,
        "tags": {k: {"key": v, "name": v} for k, v in {
            "domain": "ai", "audience": "developer", "type": "quick_take", "format": "short"}.items()},
        "topic": {"kind": "single", "sources": ["fixture"], "whole_report": True},
        "research": {"mode": "none"}, "writing": {"pipeline": "single", "style": "讲清一个变化。"},
        "output": {"target": "autovideo", "video_form": "information_motion"},
    })
    write(config / "sources.yaml", {f"source_{i:03}": {
        "name": f"固定来源 {i}", "spec": f"rss:https://example.invalid/{i}.xml", "tags": ["fixture"],
    } for i in range(spec["sources"])})
    for i in range(spec["jobs"]):
        name = f"fixture/job_{i:03}"
        write(jobs / f"{name}.yaml", {
            "name": f"固定任务 {i}", "prompt": "固定研究输入。", "enabled": True,
            "output": f"output/fixture/job_{i:03}/{{date}}.md", "sources": [f"source:source_{i:03}"],
            **({"topics": {"alias": "fixture", "format": "flash", "prefix": "F"}} if i == 0 else {}),
        })
        # Large detailed usage is intentional: compact list regressions must be visible.
        data = {"job_name": name, "last_run_at": stamp, "last_status": "success", "last_error": None,
                "last_output": f"fixture/job_{i:03}/2026-01-15.md", "last_duration_seconds": 60,
                "last_usage": {"model": "fixture", "total_tokens": 12000, "searches": 4,
                               "sources": [{"url": f"https://example.invalid/{n}", "text": "固定素材内容。" * 120}
                                           for n in range(24)]}}
        (root / "state" / f"fixture_job_{i:03}.json").write_text(json.dumps(data, ensure_ascii=False))
        write(config / f"production/production_{i:03}.yaml", {
            "job": name, "recipe": "flash", "auto": 0,
        })
        write(config / f"chains/chain_{i:03}.yaml", {
            "key": f"chain_{i:03}", "name": f"固定链路 {i}", "job": name,
            "outputs": [f"production_{i:03}"], "imported": True,
        })
    for i in range(spec["matrices"]):
        write(config / f"matrices/matrix_{i:02}.yaml", {
            "name": f"固定矩阵 {i}", "jobs": [f"fixture/job_{j:03}" for j in range(i, spec["jobs"], spec["matrices"])],
        })
    with index_db.connect() as db:
        for i in range(spec["reports"]):
            job = f"fixture/job_{i % spec['jobs']:03}"
            path = f"fixture/report_{i:04}_2026-01-15.md"
            text = f"# 固定报告 {i}\n\n" + "具体资料及结论。" * 100
            (output / "fixture").mkdir(exist_ok=True)
            (output / path).write_text(text)
            db.execute("INSERT INTO reports(path,job_name,size_bytes,mtime,title,category,indexed_at,report_date)"
                       " VALUES(?,?,?,?,?,'fixture',?,'2026-01-15')",
                       (path, job, len(text.encode()), epoch, f"固定报告 {i}", epoch))
            rowid = db.execute("SELECT last_insert_rowid()").fetchone()[0]
            db.execute("INSERT INTO reports_fts(rowid,title,content) VALUES(?,?,?)", (rowid, f"固定报告 {i}", text))
    # Topic report parsing goes through the real source importer.
    topic_report = output / "fixture/job_000/2026-01-15.md"
    topic_report.parent.mkdir(parents=True, exist_ok=True)
    topic_report.write_text("# 固定选题\n\n" + "\n\n".join(
        f"### 1.{i} 固定变化 {i}\n- **一句话**：解释用法变化\n- **目标受众**：开发者\n- **评分**：8/10\n"
        for i in range(1, 25)))
    store.use_state_dir(str(root / "state"))
    store.init_db()
    with store.connect() as db:
        autopilot_store.init(db)
        for i in range(spec["scripts"]):
            sid = f"{i:032x}"
            job_index = i % spec["jobs"]
            archived = int(i < spec["archived_scripts"])
            status = "DRAFT" if i % 8 == 0 else "FAILED" if i % 8 == 1 else "APPROVED"
            topic = {"title": f"固定视频 {i}", "id": f"2026-01-15/F{i % 24 + 1}",
                     "theme_research": {"sources": ["固定详细原文。" * 200]}}
            db.execute("INSERT INTO scripts(id,column_key,topic,status,current_version,approved_version,created_at,updated_at,"
                       "archived,production) VALUES(?,?,?, ?,1,1,?,?,?,?)",
                       (sid, "flash", json.dumps(topic, ensure_ascii=False), status, stamp, stamp, archived, f"production_{job_index:03}"))
            sections = [{"title": f"第 {s} 节", "paragraphs": [
                {"text": f"固定段落 {s}-{p}：" + "说明具体变化与影响。" * 12, "evidence": "CITED", "citations": []}
                for p in range(4)]} for s in range(5)]
            db.execute("INSERT INTO script_versions(script_id,version,title,sections,length_check,edit_type,meta,created_at)"
                       " VALUES(?,1,?,?,?,'draft',?,?)", (sid, f"固定视频 {i}", json.dumps(sections, ensure_ascii=False),
                       json.dumps({"status": "OK", "estimated_seconds": 90}), '{"checks":{"mode":"on_request"}}', stamp))
            if status == "APPROVED":
                rid = f"render_{i:04}"
                folder = root / "state/video_renders" / rid
                folder.mkdir(parents=True, exist_ok=True)
                (folder / "video.mp4").write_bytes(b"fixture-existence-only")
                db.execute("INSERT INTO renders(id,script_id,version,status,title,source_text,source_sha256,artifacts,"
                           "created_at,updated_at,completed_at) VALUES(?,?,1,'COMPLETED',?,'fixture','fixture',?,?,?,?)",
                           (rid, sid, f"固定视频 {i}", '[{"name":"video.mp4"}]', stamp, stamp, stamp))
                if i % 3 == 0:
                    db.execute("INSERT INTO publications(id,script_id,version,render_id,url,published_on,created_at)"
                               " VALUES(?,?,1,?,?,?,?)", (f"pub_{i}", sid, rid, f"https://example.invalid/video/{i}", stamp[:10], stamp))
    plans = ScheduleStore(str(root / "state"), config_dir=str(config), jobs_dir=str(jobs))
    with plans.connect(True) as db:
        db.executemany("INSERT INTO schedules(id,job_name,schedule,timezone,missed_policy,max_retries,next_run_at,created_at,updated_at)"
                       " VALUES(?,?,'0 8 * * *','UTC','latest',2,?,?,?)",
                       [(f"schedule_{i:03}", f"fixture/job_{i:03}", epoch + 86400, epoch, epoch)
                        for i in range(spec["schedules"])])
    telemetry.use_state_dir(str(root / "state"))
    history = root / "state/history"
    history.mkdir(exist_ok=True)
    (history / "__usage__.jsonl").write_text("".join(json.dumps({
        "ts": stamp, "job_name": f"fixture/job_{i % spec['jobs']:03}", "user": "fixture", "status": "success",
        "usage": {"model": "fixture", "prompt_tokens": 1000, "completion_tokens": 100, "total_tokens": 1100},
    }) + "\n" for i in range(spec["history_runs"])))
    with telemetry.connect(True) as db:
        db.executemany("INSERT INTO calls(ts,kind,model,provider,stage,script_id,column_key,prompt_tokens,completion_tokens,duration_s)"
                       " VALUES(?,'llm','fixture','fixture','write',?,'flash',1000,100,2)",
                       [(epoch + i, f"{i % spec['scripts']:032x}") for i in range(spec["telemetry_calls"])])
    assistant = AssistantStore()
    with assistant.db() as db:
        db.execute("INSERT INTO conversations(id,user_id,updated) VALUES('fixture-conversation','1',?)", (epoch,))
        for i in range(spec["assistant_requests"]):
            db.execute("INSERT INTO requests VALUES(?, 'fixture-conversation','1',?,'completed','fixture',?,?,?,?)",
                       (f"req_{i:03}", f"固定对话 {i}", '{"answer":"固定回复","actions":[]}', epoch + i, epoch + i, '[]'))
    # Non-empty old rounds; no production dispatcher or background stages.
    from web.runner import pipeline
    for i in range(7):
        day = (datetime.fromisoformat(stamp) - timedelta(days=i)).date().isoformat()
        (output / pipeline.PIPELINE_DIR / day).mkdir(parents=True, exist_ok=True)
    return spec
