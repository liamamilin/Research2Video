"""Submit and observe chain work through the existing production workers."""

from __future__ import annotations
import json, logging, threading, time, uuid, sqlite3
from pathlib import Path
from core.video import autopilot_store as ap, store, production
from web.runner.registry import TaskRegistry
from web.runner.executor import start_job
from web.services import chains, video_autopilot

logger = logging.getLogger(__name__)
STOP = threading.Event()
_last_heartbeat = 0.0


def owned_items(row, batch):
    outputs = set(row.get("outputs", []))
    return [
        item
        for item in (batch or {}).get("items", [])
        if item.get("production") in outputs
    ]


def get(rid: str) -> dict:
    with chains.db() as d:
        r = d.execute("SELECT data FROM runs WHERE id=?", (rid,)).fetchone()
    if r:
        row = json.loads(r["data"])
    elif rid.startswith(("history-", "schedule-")):
        from web.services.chain_history import resolve

        row = resolve(rid, chains.all_chains() + chains.all_chains(True))
        if not row:
            chains.error("运行记录不存在", 404)
    else:
        chains.error("运行记录不存在", 404)
    if row.get("report"):
        from web.services.chain_reports import relative_path

        row["report_view"] = relative_path(row["report"])
    if row.get("batch_id"):
        row["batch"] = ap.get_run(row["batch_id"])
        if row["batch"]:
            mine = owned_items(row, row["batch"])
            row["shared_batch"] = len(mine) != len(row["batch"]["items"])
            row["batch"]["items"] = mine
            if row.get("historical"):
                from web.services.chain_history import outcome

                row["status"] = outcome(
                    row["batch"]["status"], {i["state"] for i in mine}
                )
        for item in (row["batch"] or {}).get("items", []):
            render = (
                store.get_render(item["render_id"]) if item.get("render_id") else None
            )
            if render:
                from web.services import video_render

                timings = video_render.render_timings(render)
                diag = timings.get("diagnostics") or {}
                item["render_progress"] = {
                    **{
                        k: render.get(k)
                        for k in ("status", "active_stage", "error_message", "created_at")
                    },
                    **(
                        {"elapsed_seconds": timings["elapsed_seconds"]}
                        if timings.get("elapsed_seconds") is not None
                        else {}
                    ),
                    **({"diagnostics": diag} if diag else {}),
                }
    if row.get("task_id"):
        from web.runner.log_store import read_events

        _, events = read_events(
            chains.paths().logs_dir, row["job"], row["task_id"], limit=8
        )
        phases = {
            "building_prompt": "准备研究材料",
            "organizing_topics": "整理选题",
            "supplementing_materials": "补充选题材料",
            "saving_output": "保存研究报告",
            "discovering_topics": "发现候选选题",
            "filtering_topics": "软筛选与归并选题",
            "gathering_topics": "逐题深挖材料",
            "assembling_report": "组装研究报告",
        }
        for event in events:
            payload = event["event"]
            if payload.get("phase"):
                label = phases.get(payload["phase"], payload["phase"])
                if payload.get("topic") and payload.get("topics"):
                    label = f"{label}（{payload['topic']}/{payload['topics']}）"
                elif payload.get("topics") is not None and payload["phase"] in (
                    "gathering_topics",
                    "assembling_report",
                ):
                    label = f"{label}（共 {payload['topics']} 题）"
                elif payload.get("candidates") is not None and payload["phase"] == "filtering_topics":
                    label = f"{label}（{payload['candidates']} 条候选）"
                payload["message"] = label + (
                    "：" + str(payload["detail"]) if payload.get("detail") else ""
                )
        row["progress"] = events
    return row


def put(row: dict) -> None:
    row = dict(row)
    row.pop("batch", None)
    row.pop("progress", None)
    chains.save_run(row)


def start(key: str, body: dict, user: dict) -> dict:
    with chains.LOCK:
        d = chains.detail(key)
        if d.get("archived"):
            chains.error("已删除链路不能运行")
        if chains.active_for_job(d["job"]):
            chains.error("共享研究已有运行中的批次，请进入该批次查看")
        if TaskRegistry().is_running(d["job"]):
            chains.error("研究任务正在其他页面运行，请等待完成")
        chosen = body.get(
            "outputs", [x["key"] for x in d["outputs_detail"] if x["auto"]]
        )
        outputs = [x for x in d["outputs_detail"] if x["key"] in chosen]
        if len(outputs) != len(chosen) or any(x["errors"] for x in outputs):
            chains.error("本次输出不可用，请检查链路设置", 422)
        limit = int(body.get("limit", 1))
        if not 0 <= limit <= 10:
            chains.error("单条数量为 0（全部）或 1–10", 422)
        row = {
            "id": uuid.uuid4().hex,
            "chain_id": key,
            "job": d["job"],
            "outputs": chosen,
            "limit": limit,
            "status": "RUNNING",
            "stage": "RESEARCH",
            "started_at": time.time(),
            "started_by": user["username"],
            "snapshot": {
                **{k: v for k, v in d.items() if k not in ("scripts", "runs")},
                **(
                    {"research_context": body["run_context"]}
                    if body.get("run_context")
                    else {}
                ),
            },
            "retry_of": body.get("retry_of"),
            "note": "正在获取信息源并生成研究报告",
            "attempt": 1,
            "events": [],
            "schedule_run_id": body.get("schedule_run_id"),
        }
        put(row)
        try:
            task = start_job(
                d["job"],
                user["username"],
                chains.paths().config_dir,
                chains.paths().jobs_dir,
                run_context={
                    **(body.get("run_context") or {}),
                    "chain_run_id": row["id"],
                },
            )
            row["task_id"] = task.task_id
            put(row)
        except Exception as exc:
            row.update(status="FAILED", note=str(exc))
            put(row)
        return get(row["id"])


def cancel(rid: str) -> dict:
    with chains.LOCK:
        row = get(rid)
        if row["status"] not in ("RUNNING", "CANCELLING"):
            return row
        if row.get("native_schedule"):
            chains.schedule_store().cancel(row["schedule_run_id"])
            return get(rid)
        if row["stage"] == "RESEARCH":
            task = TaskRegistry().get(row["job"])
            if task and task.task_id == row.get("task_id"):
                TaskRegistry().cancel(row["job"])
        if row.get("batch_id"):
            batch = ap.get_run(row["batch_id"])
            for item in owned_items(row, batch):
                if item["state"] not in ("DONE", "FAILED", "HELD", "CANCELLED"):
                    ap.update_item(
                        item["id"], state="CANCELLED", note="用户停止链路接续"
                    )
            if len(owned_items(row, batch)) == len(batch["items"]):
                ap.update_run(batch["id"], status="CANCELLED")
        _cancel_renders(row)
        row.update(
            status="CANCELLING",
            note="已停止接续，并请求取消排队/运行中的渲染；等待当前工作退出，完成产物保留",
        )
        put(row)
        return get(rid)


def retry(rid: str, user: dict) -> dict:
    with chains.LOCK:
        row = get(rid)
        if row["status"] not in ("FAILED", "HELD", "CANCELLED", "INTERRUPTED") and not (
            row["stage"] == "VIDEO"
            and row["status"] == "RUNNING"
            and any(
                i["state"] == "HELD" for i in (row.get("batch") or {}).get("items", [])
            )
        ):
            chains.error("当前运行不需要重试")
        if any(r["id"] != rid for r in chains.active_for_job(row["job"])):
            chains.error("共享研究还有任务运行中")
        if row.get("native_schedule") and not row.get("batch_id"):
            context = row.get("snapshot") or {}
            if not context.get("job"):
                chains.error(
                    "原周期缺少冻结的研究快照，请从链路页按当前设置重新运行研究"
                )
            current = chains.detail(row["chain_id"])
            return start(
                row["chain_id"],
                {
                    "outputs": [
                        o["key"] for o in current["outputs_detail"] if o["auto"]
                    ],
                    "limit": 0,
                    "run_context": context,
                    "retry_of": rid,
                },
                user,
            )
        if row.get("batch_id"):
            batch = ap.get_run(row["batch_id"])
            current = chains.detail(row["chain_id"])
            row.setdefault("retry_history", []).append(
                {
                    "at": time.time(),
                    "by": user["username"],
                    "outputs": [
                        {k: o[k] for k in ("key", "name", "effective_render")}
                        for o in current["outputs_detail"]
                        if o["key"] in row["outputs"]
                    ],
                }
            )
            for item in owned_items(row, batch):
                if item["state"] in ("FAILED", "HELD", "CANCELLED"):
                    history = (
                        store.list_renders(item["script_id"])
                        if item.get("script_id")
                        else []
                    )
                    latest = history[0] if history else {}
                    if latest.get("status") == "FAILED" and latest.get(
                        "error_code"
                    ) in ("DOWNLOAD_FAILED", "SUBMIT_UNCONFIRMED"):
                        from web.services import video_render

                        try:
                            if latest["error_code"] == "DOWNLOAD_FAILED":
                                resumed = store.resume_render_download(latest["id"])
                            else:
                                # SUBMIT_UNCONFIRMED: human confirmed (or library has no match).
                                # Atomically clear submit_started_at so we create again; same
                                # idempotency_key keeps AutoVideo from duplicating if the first create landed.
                                resumed = store.resume_unconfirmed_render(latest["id"])
                        except store.ActiveRenderError as exc:
                            chains.error(str(exc), 409)
                        if resumed:
                            video_render.start(latest["id"])
                    cutoff = (
                        max(
                            (
                                r["created_at"]
                                for r in store.list_renders(item["script_id"])
                                if r["status"] in ("FAILED", "CANCELLED")
                            ),
                            default="",
                        )
                        if item.get("script_id")
                        else ""
                    )
                    ap.update_item(
                        item["id"],
                        state="WORKING" if item.get("script_id") else "PENDING",
                        note="用户从失败步骤继续",
                        retries={"renders_forgiven_before": cutoff},
                    )
            ap.update_run(batch["id"], status="RUNNING")
            row.update(
                status="RUNNING",
                stage="VIDEO",
                note="复用已有报告与讲稿，采用当前出片设置继续未完成视频",
                attempt=row.get("attempt", 1) + 1,
            )
            put(row)
            return get(rid)
        return start(
            row["chain_id"], {"outputs": row["outputs"], "limit": row["limit"]}, user
        )


def _cancel_renders(row):
    from web.services import video_render

    sent = set(row.get("cancelled_render_requests", []))
    for item in (
        owned_items(row, ap.get_run(row["batch_id"])) if row.get("batch_id") else []
    ):
        if not item.get("script_id"):
            continue
        for render in store.list_renders(item["script_id"]):
            if (
                render["id"] in sent
                or render["status"] not in ("QUEUED", "RUNNING")
                or not render.get("autovideo_job_id")
            ):
                continue
            try:
                video_render.get_client()._request(
                    "POST", "/api/jobs/" + render["autovideo_job_id"] + "/cancel"
                )
                sent.add(render["id"])
            except Exception as exc:
                logger.warning("render cancel deferred: %s", exc)
    row["cancelled_render_requests"] = list(sent)


def _still_working(row):
    task = TaskRegistry().get(row["job"])
    if (
        task
        and task.task_id == row.get("task_id")
        and task.future
        and not task.future.done()
    ):
        return True
    if row.get("batch_id"):
        for i in owned_items(row, ap.get_run(row["batch_id"])):
            if i.get("script_id"):
                s = store.get_script(i["script_id"]) or {}
                if s.get("status") in ("RESEARCHING", "WRITING", "AUDITING"):
                    return True
                if any(
                    r["status"] in ("SUBMITTING", "QUEUED", "RUNNING", "DOWNLOADING")
                    for r in store.list_renders(i["script_id"])
                ):
                    return True
    return False


def advance(row: dict) -> None:
    if row["status"] == "CANCELLING":
        _cancel_renders(row)
        put(row)
        if not _still_working(row):
            row.update(
                status="CANCELLED",
                note="已停止，完成的产物与运行记录保留",
                finished_at=time.time(),
            )
            put(row)
        return
    if row["stage"] == "RESEARCH":
        task = TaskRegistry().get(row["job"])
        if task and task.task_id != row.get("task_id"):
            task = None
        if task and task.future and not task.future.done():
            return
        report = getattr(task, "output_path", None) if task else None
        if not report:
            from core import report_meta

            receipts = [
                m
                for m in report_meta.load_all().values()
                if m.get("chain_run_id") == row["id"]
            ]
            if receipts:
                report = receipts[-1]["path"]
        if not report and row.get("task_id"):
            from web.runner.log_store import read_events

            _, events = read_events(
                chains.paths().logs_dir, row["job"], row["task_id"], limit=5000
            )
            report = next(
                (
                    event["event"]["output_path"]
                    for event in reversed(events)
                    if event["event"].get("status") == "success"
                    and event["event"].get("output_path")
                ),
                None,
            )
        if not report:
            row.update(
                status="FAILED" if task else "INTERRUPTED",
                note="研究未完成，请查看日志后重试"
                if task
                else "研究执行中断，保留启动记录，可重试研究",
            )
            put(row)
            return
        row["report"] = report
        if not row["outputs"]:
            row.update(
                status="DONE",
                stage="RESEARCH",
                note="研究报告完成；本次没有制作视频",
                finished_at=time.time(),
            )
            put(row)
            return
        ps = production.load_all(chains.paths().config_dir, chains.paths().jobs_dir)
        picks = []
        for pid in row["outputs"]:
            p = ps[pid]
            pick = {
                "column": p.recipe,
                "production": pid,
                "count": row["limit"] or 1,
                "all": row["limit"] == 0,
            }
            if p.content_mode == "episode":
                pick["content_mode"] = "episode"
            if p.split == "whole":
                pick["whole"] = True
            else:
                pick["source"] = p.source
            picks.append(pick)
        from core.report_identity import describe

        root = str(Path(chains.paths().config_dir).parent)
        batch = video_autopilot.start_run(
            {
                "picks": picks,
                "refresh": False,
                "job": row["job"],
                "report": report,
                "chain_run_id": row["id"],
                "report_identity": describe(report, root)["id"],
            },
            started_by=row["started_by"],
        )
        row.update(
            stage="VIDEO",
            batch_id=batch["id"],
            note="报告完成，正在选题、写稿与生成视频",
        )
        put(row)
    if row["stage"] == "VIDEO":
        batch = ap.get_run(row["batch_id"])
        mine = owned_items(row, batch)
        settled = bool(mine) and all(
            i["state"] in ("DONE", "FAILED", "HELD", "CANCELLED") for i in mine
        )
        if batch["status"] != "RUNNING" or settled:
            states = {i["state"] for i in mine}
            if states & {"FAILED"}:
                status = "FAILED"
            elif states & {"HELD"}:
                status = "HELD"
            elif states == {"CANCELLED"} or batch["status"] == "CANCELLED":
                status = "CANCELLED"
            elif not mine:
                status = "NO_CONTENT"
            else:
                status = "DONE"
            note = (
                "本链路视频已完成，其他输出保留原批次状态"
                if status == "DONE" and len(mine) != len(batch["items"])
                else batch.get("note", "")
            )
            row.update(status=status, note=note, finished_at=time.time())
            put(row)


def advance_safely(row: dict) -> None:
    try:
        advance(row)
    except Exception as exc:
        if isinstance(exc, sqlite3.OperationalError) and any(
            word in str(exc).lower() for word in ("locked", "busy")
        ):
            logger.warning("chain run %s waiting for database: %s", row["id"], exc)
            return
        logger.exception("chain run %s failed", row["id"])
        row.update(status="FAILED", note=f"{type(exc).__name__}: {exc}")
        put(row)


def tick() -> None:
    """Observe durable intents; production autopilot alone advances video batches."""
    from core.fileio import file_lock

    with (
        chains.LOCK,
        file_lock(str(Path(chains.paths().state_dir) / "chain-observer"), timeout=0),
    ):
        with chains.db() as d:
            rows = [json.loads(x["data"]) for x in d.execute("SELECT data FROM runs")]
        for row in rows:
            if row["status"] in ("RUNNING", "CANCELLING"):
                advance_safely(row)
