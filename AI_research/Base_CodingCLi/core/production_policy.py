"""Versioned production policy; recurrence remains owned by managed schedules."""

from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from core import cron, matrices
from core.yaml_cache import load_yaml

FILE = "production_policy.yaml"


def load(config_dir: str) -> dict:
    path = Path(config_dir) / FILE
    if not path.exists():
        return {}
    raw = path.read_bytes()
    value = load_yaml(raw.decode())
    validate(value)
    return {**value, "revision": hashlib.sha256(raw).hexdigest()}


def validate(value: dict) -> None:
    if not isinstance(value, dict) or set(value) - {
        "schema_version",
        "matrices",
        "window",
        "collection",
        "automatic",
        "jobs",
        "sources",
    }:
        raise ValueError("生产策略字段无效")
    if value.get("schema_version") != 1:
        raise ValueError("不支持的生产策略版本")
    owners = value.get("matrices", [])
    if not isinstance(owners, list) or not all(
        isinstance(x, str) and x for x in owners
    ):
        raise ValueError("matrices 应为矩阵标识列表")

    def window(item):
        if (
            not isinstance(item, dict)
            or set(item) - {"mode", "days", "timezone"}
            or item.get("mode") not in {"previous_slot", "rolling"}
        ):
            raise ValueError("研究窗口支持 previous_slot 或 rolling")
        ZoneInfo(item.get("timezone", "Asia/Shanghai"))
        if item.get("mode") == "rolling" and (
            type(item.get("days")) is not int or not 1 <= item["days"] <= 366
        ):
            raise ValueError("滚动窗口天数应为 1–366")

    window(value.get("window", {}))
    overrides = value.get("jobs", {})
    if not isinstance(overrides, dict):
        raise ValueError("jobs 应为任务策略映射")
    for item in overrides.values():
        window(item)
    roles = value.get("sources", {})
    if (
        not isinstance(roles, dict)
        or set(roles) - {"reference"}
        or not isinstance(roles.get("reference", []), list)
    ):
        raise ValueError("sources.reference 应为背景信息源列表")
    if not all(isinstance(s, str) and s.strip() for s in roles.get("reference", [])):
        raise ValueError("背景信息源标识无效")
    collection = value.get("collection", {})
    if not isinstance(collection, dict) or set(collection) - {
        "enabled",
        "schedule",
        "timezone",
        "retention_days",
        "limit_per_source",
        "max_sources",
        "timeout_seconds",
    }:
        raise ValueError("素材积累配置无效")
    if type(collection.get("enabled", False)) is not bool:
        raise ValueError("采集开关应为布尔值")
    if collection.get("enabled"):
        cron.parse_schedule(collection["schedule"])
        ZoneInfo(collection.get("timezone", "Asia/Shanghai"))
    for k, default, maximum in [
        ("retention_days", 60, 730),
        ("limit_per_source", 80, 200),
        ("max_sources", 100, 500),
        ("timeout_seconds", 15, 120),
    ]:
        n = collection.get(k, default)
        if type(n) is not int or not 1 <= n <= maximum:
            raise ValueError(f"{k} 超出范围")
    automatic = value.get("automatic", {})
    if not isinstance(automatic, dict) or set(automatic) - {
        "daily_limit",
        "timezone",
        "recommendations",
    }:
        raise ValueError("自动生产配置无效")
    n = automatic.get("daily_limit", 4)
    if type(n) is not int or not 1 <= n <= 100:
        raise ValueError("每日自动生产数量应为 1–100")
    ZoneInfo(automatic.get("timezone", "Asia/Shanghai"))
    if type(automatic.get("recommendations", False)) is not bool:
        raise ValueError("推荐自动接续应为布尔值")


def manages(policy: dict, config_dir: str, job: str = "", matrix: str = "") -> bool:
    if not policy:
        return False
    owner = matrices.of_job(job, config_dir) if job else None
    return (matrix or (owner.key if owner else "")) in policy.get("matrices", [])


def plan_for(state_dir: str, job: str) -> dict | None:
    path = Path(state_dir) / "schedules.db"
    if not path.exists():
        return None
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        row = db.execute(
            "SELECT * FROM schedules WHERE job_name=? AND deleted=0", (job,)
        ).fetchone()
    return dict(row) if row else None


def issue(
    config_dir: str,
    state_dir: str,
    job: dict,
    now: datetime,
    context: dict | None = None,
) -> dict:
    """One immutable issue: same cutoff across queue delay, retries and policy edits."""
    if context and context.get("prepared"):
        return context
    policy = load(config_dir)
    name = job.get("_file", "")
    if not manages(policy, config_dir, name):
        return context or {}
    rule = policy.get("jobs", {}).get(name, policy["window"])
    from core.schedule_store import slot
    from core.schedule_store import zone as schedule_zone

    plan = context or plan_for(state_dir, name)
    timezone = (plan or {}).get("timezone") or rule.get("timezone", "Asia/Shanghai")
    zone = schedule_zone(timezone)
    current = now.replace(tzinfo=zone) if now.tzinfo is None else now.astimezone(zone)
    expression = (plan or {}).get("expression") or (plan or {}).get("schedule")
    due = (context or {}).get("due_at")
    if due is None:
        due = (
            slot(expression, current.timestamp(), timezone, backwards=True)
            if expression and rule["mode"] == "previous_slot"
            else current.timestamp()
        )
    end = datetime.fromtimestamp(due, zone)
    if rule["mode"] == "previous_slot" and expression:
        start = datetime.fromtimestamp(
            slot(expression, due - 60, timezone, backwards=True), zone
        )
    else:
        from datetime import timedelta

        start = end - timedelta(days=rule.get("days", job.get("collection_days", 7)))
    window = {
        "mode": rule["mode"],
        "days": max(1, int((end - start).total_seconds() + 86399) // 86400),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "timezone": timezone,
    }
    identity = {
        "job": name,
        "schedule_id": (plan or {}).get("schedule_id") or (plan or {}).get("id"),
        "schedule_version": (plan or {}).get("version"),
        "start": window["start"],
        "end": window["end"],
    }
    return {
        **(context or {}),
        "prepared": True,
        "policy_revision": policy["revision"],
        "policy": copy.deepcopy(policy),
        "issue_id": hashlib.sha256(
            json.dumps(identity, sort_keys=True).encode()
        ).hexdigest(),
        "research_window": window,
        "job": copy.deepcopy(job),
    }


def prepare(runtime, job_name: str, context: dict) -> dict:
    from core.config import load_job

    job = load_job(runtime.jobs_dir, job_name)
    if not job:
        return context
    if job.get("source_collection"):
        policy = load(runtime.config_dir)
        if policy:
            frozen = {**copy.deepcopy(job), "_production_policy": policy}
            identity = json.dumps([job_name, context.get("due_at"), policy["revision"]])
            return {
                **context,
                "prepared": True,
                "job": frozen,
                "policy_revision": policy["revision"],
                "issue_id": hashlib.sha256(identity.encode()).hexdigest(),
            }
    return issue(
        runtime.config_dir,
        runtime.state_dir,
        matrices.for_run(job, runtime.config_dir),
        datetime.now(),
        context,
    )


def references(policy: dict, config_dir: str) -> set[str]:
    from core.sources import resolve

    return set(resolve(policy.get("sources", {}).get("reference", []), config_dir))
