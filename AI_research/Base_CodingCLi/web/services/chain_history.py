"""Project native batch history into chain views without rewriting old records."""

import json
from core.video import store, autopilot_store as ap


def outcome(native_status: str, states: set[str]) -> str:
    """A finished worker can retain failed/held items that still need attention."""
    if states and states <= {"DONE", "FAILED", "HELD", "CANCELLED"}:
        if "FAILED" in states:
            return "FAILED"
        if "HELD" in states:
            return "HELD"
        if states == {"CANCELLED"} or native_status == "CANCELLED":
            return "CANCELLED"
        return "DONE"
    return native_status


def rows(chain: dict) -> list[dict]:
    outputs = set(chain.get("outputs", []) + chain.get("retired_outputs", []))
    batches = []
    states = {}
    if outputs:
        sorted_outputs = sorted(outputs)
        like_clause = " OR ".join("spec LIKE ?" for _ in sorted_outputs)
        with store.connect() as db:
            batches = db.execute(
                f"SELECT id,spec,status,note,started_at,started_by FROM autopilot_runs WHERE {like_clause} ORDER BY started_at DESC",
                [f'%"{o}"%' for o in sorted_outputs],
            ).fetchall()
            for run_id, state in db.execute(
                "SELECT run_id,state FROM autopilot_items WHERE production IN ("
                + ",".join("?" for _ in sorted_outputs)
                + ")",
                sorted_outputs,
            ):
                states.setdefault(run_id, set()).add(state)
    result = []
    for batch in batches:
        spec = json.loads(batch["spec"])
        selected = {p.get("production") for p in spec.get("picks", [])}
        if not outputs.intersection(selected):
            continue
        result.append(
            {
                "id": "history-" + batch["id"],
                "chain_id": chain["key"],
                "job": chain["job"],
                "outputs": sorted(outputs.intersection(selected)),
                "limit": 0,
                "batch_id": batch["id"],
                "report": spec.get("report"),
                "historical": True,
                "status": outcome(batch["status"], states.get(batch["id"], set())),
                "stage": "VIDEO",
                "note": batch["note"],
                "started_at": batch["started_at"],
                "started_by": batch["started_by"],
            }
        )
        if len(result) >= 100:
            break
    from web.services import chains

    with chains.schedule_store().connect() as db:
        executions = db.execute(
            "SELECT * FROM schedule_runs WHERE job_name=? ORDER BY created_at DESC LIMIT 100",
            (chain["job"],),
        ).fetchall()
    from web.services.chain_reports import relative_path

    by_report = {}
    for batch in result:
        if batch.get("report"):
            try:
                by_report.setdefault(relative_path(batch["report"]), []).append(batch)
            except ValueError:
                pass
    for execution in executions:
        item = dict(execution)
        report = item.get("output_path")
        matched = []
        if report:
            try:
                matched = by_report.get(relative_path(report), [])
            except ValueError:
                pass
        if len(matched) == 1:
            matched[0]["schedule_run_id"] = item["id"]
            matched[0]["research_started_at"] = item["started_at"]
            continue
        code = item["status"]
        status = {
            "success": "DONE",
            "failed": "FAILED",
            "blocked": "HELD",
            "cancelled": "CANCELLED",
            "interrupted": "INTERRUPTED",
            "skipped": "NO_CONTENT",
        }.get(code, "RUNNING")
        result.append(
            {
                "id": "schedule-" + item["id"],
                "chain_id": chain["key"],
                "job": chain["job"],
                "outputs": [],
                "limit": 0,
                "historical": True,
                "native_schedule": True,
                "schedule_run_id": item["id"],
                "report": report,
                "status": status,
                "stage": "RESEARCH",
                "started_at": item["started_at"] or item["due_at"],
                "started_by": "scheduler",
                "note": "周期研究 · "
                + (item.get("error") or ("报告已生成" if code == "success" else code)),
                "snapshot": json.loads(item["context"])
                if item.get("context")
                else None,
            }
        )
    return result


def resolve(identifier: str, chains: list[dict]) -> dict | None:
    if identifier.startswith("schedule-"):
        from web.services import chains as service

        execution = service.schedule_store().run(identifier.removeprefix("schedule-"))
        if not execution:
            return None
        for chain in chains:
            if chain["job"] == execution["job_name"]:
                found = next(
                    (
                        row
                        for row in rows(chain)
                        if row["id"] == identifier
                        or row.get("schedule_run_id") == execution["id"]
                    ),
                    None,
                )
                return {**found, "id": identifier} if found else None
        return None
    target = identifier.removeprefix("history-")
    batch = ap.get_run(target)
    if not batch:
        return None
    selected = {p.get("production") for p in batch["spec"].get("picks", [])}
    for chain in chains:
        if selected.intersection(
            chain.get("outputs", []) + chain.get("retired_outputs", [])
        ):
            return next((row for row in rows(chain) if row["id"] == identifier), None)
    return None
