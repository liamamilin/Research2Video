"""Persistent chain orchestration over the existing research/video business objects."""

from __future__ import annotations
from core.config_transaction import remove as remove_config_file
import copy
import hashlib
import json
import re
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
import yaml
from fastapi import HTTPException
from core.config import load_job
from core import matrices, sources
from core.schedule_store import ScheduleStore
from core.video import (
    production,
    content_columns,
    store,
    recipes,
    autopilot_store as ap,
)
from web.settings import get_settings
from web.services import yaml_io, chain_access

LOCK = threading.RLock()


def paths():
    return get_settings().paths


def cfg() -> Path:
    return Path(paths().config_dir)


def read(path: str | Path) -> dict:
    return yaml.safe_load(Path(path).read_text()) if Path(path).exists() else {}


def write(path: str | Path, value: dict) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    yaml_io.atomic_write(
        str(path), yaml.safe_dump(value, allow_unicode=True, sort_keys=False)
    )


def error(message, code=409):
    raise HTTPException(code, message)


def key_ok(key: str) -> str:
    if not re.fullmatch(r"[a-z][a-z0-9_-]{1,59}", key):
        error("标识使用 2–60 位小写英文、数字、短横线或下划线", 422)
    return key


def schedule_store() -> ScheduleStore:
    return ScheduleStore(
        paths().state_dir, config_dir=paths().config_dir, jobs_dir=paths().jobs_dir
    )


def metadata() -> dict:
    return {f.stem: read(f) for f in (cfg() / "chains").glob("*.yaml")}


_YAML_DIGEST_CACHE: dict[tuple[str, str], tuple[tuple, bytes]] = {}
_YAML_DIGEST_LOCK = threading.Lock()


def _yaml_tree_digest(config_dir: str, jobs_dir: str) -> bytes:
    """Hash every YAML file under config/ and jobs/, cached by (path, mtime_ns, size)."""
    key = (config_dir, jobs_dir)
    files = sorted([*Path(config_dir).rglob("*.yaml"), *Path(jobs_dir).rglob("*.yaml")])
    sig = []
    for f in files:
        try:
            st = f.stat()
            sig.append((str(f), st.st_mtime_ns, st.st_size))
        except OSError:
            continue
    sig_tuple = tuple(sig)
    with _YAML_DIGEST_LOCK:
        cached = _YAML_DIGEST_CACHE.get(key)
        if cached is not None and cached[0] == sig_tuple:
            return cached[1]
    h = hashlib.sha256()
    for path_str, _, _ in sig_tuple:
        try:
            h.update(path_str.encode())
            h.update(Path(path_str).read_bytes())
        except OSError:
            continue
    value = h.digest()
    with _YAML_DIGEST_LOCK:
        _YAML_DIGEST_CACHE[key] = (sig_tuple, value)
    return value


def _all_chains_grouped(configs: dict | None = None) -> tuple[list[dict], list[dict]]:
    if configs is None:
        configs = production.load_all(paths().config_dir, paths().jobs_dir)
    meta = metadata()
    for member in meta.values():
        if member.get("archived"):
            continue
        removed = [pid for pid in member.get("outputs", []) if pid not in configs]
        member["outputs"] = [pid for pid in member.get("outputs", []) if pid in configs]
        if removed:
            member["retired_outputs"] = list(
                dict.fromkeys(member.get("retired_outputs", []) + removed)
            )
    owned = {p for m in meta.values() for p in m.get("outputs", [])}
    grouped = {}
    for p in configs.values():
        if p.key not in owned:
            grouped.setdefault(p.job, []).append(p.key)
    for job, ids in grouped.items():
        candidates = [
            m for m in meta.values() if m["job"] == job and not m.get("archived")
        ]
        imported = next((m for m in candidates if m.get("imported")), None)
        target = imported or (candidates[0] if len(candidates) == 1 else None)
        if target:
            target["outputs"] = list(dict.fromkeys(target.get("outputs", []) + ids))
            continue
        base = "job-" + hashlib.sha1(job.encode()).hexdigest()[:12]
        key, suffix = base, 2
        while key in meta:
            key, suffix = f"{base}-{suffix}", suffix + 1
        j = load_job(paths().jobs_dir, job) or {}
        meta[key] = {
            "key": key,
            "name": j.get("name", job),
            "job": job,
            "outputs": ids,
            "imported": True,
            "schedule_armed": False,
        }
    active = [m for m in meta.values() if not m.get("archived")]
    archived_list = [m for m in meta.values() if bool(m.get("archived"))]
    return active, archived_list


def all_chains(archived: bool = False, *, configs: dict | None = None) -> list[dict]:
    active, archived_list = _all_chains_grouped(configs=configs)
    return archived_list if archived else active


def find(key: str, include_archived: bool = False, *, configs: dict | None = None) -> dict:
    active, archived_list = _all_chains_grouped(configs=configs)
    pool = active + (archived_list if include_archived else [])
    found = next((m for m in pool if m["key"] == key), None)
    if found is None:
        error("链路不存在", 404)
    return found


def digest(meta: dict, *, all_plans: list[dict] | None = None) -> str:
    # A legacy-tab edit must invalidate a chain form too.
    h = hashlib.sha256(json.dumps(meta, sort_keys=True, ensure_ascii=False).encode())
    h.update(_yaml_tree_digest(paths().config_dir, paths().jobs_dir))
    raw_plans = schedule_store().list() if all_plans is None else all_plans
    plans = [
        {k: v for k, v in p.items() if k not in ("next_run_at", "updated_at")}
        for p in raw_plans
    ]
    h.update(json.dumps(plans, sort_keys=True).encode())
    return h.hexdigest()


def assert_revision(meta, revision):
    if not revision or revision != digest(meta):
        error("链路或关联页面已修改，请重新载入后保存；本次没有覆盖其他修改")


def remember(meta: dict) -> None:
    write(cfg() / "chains" / f"{meta['key']}.yaml", meta)


@contextmanager
def db():
    from core import sqlite_util

    with sqlite_util.connect(
        str(Path(paths().state_dir) / "chains.db"), timeout=30
    ) as d:
        d.execute(
            "CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, chain_id TEXT, data TEXT NOT NULL)"
        )
        yield d


def save_run(row: dict) -> None:
    with db() as d:
        d.execute(
            "INSERT OR REPLACE INTO runs VALUES(?,?,?)",
            (row["id"], row["chain_id"], json.dumps(row, ensure_ascii=False)),
        )


def runs(key: str, *, meta: dict | None = None) -> list[dict]:
    with db() as d:
        current = [
            json.loads(r["data"])
            for r in d.execute(
                "SELECT data FROM runs WHERE chain_id=? ORDER BY rowid DESC", (key,)
            )
        ]
    from web.services.chain_history import rows

    covered = {r.get("batch_id") for r in current}
    target = meta if meta is not None else find(key, True)
    historical = [
        row for row in rows(target) if row.get("batch_id") not in covered
    ]
    return sorted(current + historical, key=lambda r: r["started_at"], reverse=True)


def active_for_job(job):
    with db() as d:
        rows = [json.loads(r["data"]) for r in d.execute("SELECT data FROM runs")]
    return [
        r for r in rows if r["job"] == job and r["status"] in ("RUNNING", "CANCELLING")
    ]


def running_dependencies():
    """Include work submitted outside the chain tab, not just its own ledger."""
    rows = []
    for batch in ap.list_runs(limit=None, status="RUNNING"):
        spec = batch.get("spec") or {}
        outputs = [
            p["production"] for p in spec.get("picks", []) if p.get("production")
        ]
        if spec.get("job"):
            rows.append(
                {"id": "batch:" + batch["id"], "job": spec["job"], "outputs": outputs}
            )
    from web.runner.registry import TaskRegistry

    for task in TaskRegistry().all_tasks().values():
        if task.is_running or (task.future and not task.future.done()):
            rows.append(
                {"id": "task:" + task.task_id, "job": task.job_name, "outputs": []}
            )
    from core.lock import LockManager

    jobs = list(Path(paths().jobs_dir).rglob("*.yaml"))
    by_safe = {}
    for path in jobs:
        job = str(path.relative_to(paths().jobs_dir).with_suffix(""))
        by_safe.setdefault(job.replace("/", "_"), []).append(job)
    for path in (Path(paths().state_dir) / "locks").glob("*.lock"):
        try:
            payload = json.loads(path.read_text())
            if not isinstance(payload, dict):
                continue
            for job in by_safe.get(path.stem, []):
                if not LockManager(job)._is_stale(payload):
                    rows.append({"id": "lock:" + job, "job": job, "outputs": []})
        except (OSError, ValueError, KeyError):
            continue
    return rows


def assert_idle(job):
    if active_for_job(job) or any(r["job"] == job for r in running_dependencies()):
        error("该研究或视频正在运行，请等待完成或从原运行入口停止后再修改")


@contextmanager
def transaction():
    from core.config_transaction import transaction as configuration_transaction

    with (
        LOCK,
        configuration_transaction(
            paths().state_dir, [cfg(), Path(paths().jobs_dir)], schedule_store()
        ),
    ):
        yield


def detail(key: str) -> dict:
    configs = production.load_all(paths().config_dir, paths().jobs_dir)
    active_chains, archived_chains = _all_chains_grouped(configs=configs)
    meta = next((m for m in active_chains + archived_chains if m["key"] == key), None)
    if meta is None:
        error("链路不存在", 404)
    job = load_job(paths().jobs_dir, meta["job"]) or {}
    owner = matrices.of_job(meta["job"], paths().config_dir)
    registry = sources.load_registry(paths().config_dir)
    from web.services import source_registry
    from core import source_supply, source_coverage

    profile = next(
        (
            p
            for p in source_supply.profiles(paths().config_dir).values()
            if meta["job"] in p.get("jobs", {})
        ),
        None,
    )
    supply = None
    if profile:
        supply = {
            "key": profile["key"],
            "enabled": job.get("source_supply_enabled", True),
            "pool_per_job": profile.get("pool_per_job", 4),
            "targets": source_coverage.targets(profile, meta["job"]),
        }
    source_keys = [
        ref[7:]
        for ref in job.get("sources", [])
        if str(ref).startswith("source:") and ref[7:] in registry
    ]
    used_by_map = source_registry.usages_map(registry, source_keys) if source_keys else {}
    refs = []
    for ref in job.get("sources", []):
        k = ref[7:] if str(ref).startswith("source:") else None
        e = registry.get(k, {})
        refs.append(
            {
                "ref": ref,
                "key": k,
                "name": e.get("name", ref),
                "enabled": e.get("enabled", True),
                "spec": e.get("spec", ref),
                "members": [
                    {
                        "key": rk,
                        "name": rv.get("name", rk),
                        "spec": rv["spec"],
                        "enabled": rv.get("enabled", True),
                    }
                    for rk, rv in registry.items()
                    if str(ref).startswith("tag:") and ref[4:] in rv.get("tags", [])
                ],
                "used_by": used_by_map.get(k, []) if k in registry else [],
            }
        )
    every_recipe = recipes.expanded(paths().config_dir)
    outputs = []
    for pid in meta.get("outputs", []):
        p = configs.get(pid)
        if p:
            context = content_columns.snapshot(paths().config_dir, pid, meta["job"])
            recipe = recipes.find(paths().config_dir, p.recipe, every=every_recipe)
            outputs.append(
                {
                    **p.as_dict(),
                    "audiences": list(recipe.audiences) if recipe else [],
                    "effective_render": content_columns.render_settings(
                        paths().config_dir, owner, context, p.render
                    ),
                }
            )
    ids = meta.get("outputs", []) + meta.get("retired_outputs", [])
    scripts = []
    script_count = 0
    if ids:
        placeholders = ",".join("?" for _ in ids)
        with store.connect() as connection:
            script_count = connection.execute(
                "SELECT COUNT(*) FROM scripts WHERE production IN ("
                + placeholders
                + ")",
                ids,
            ).fetchone()[0]
            scripts = [
                dict(row)
                for row in connection.execute(
                    "SELECT s.id,s.status,s.current_version,s.archived,v.title FROM scripts s LEFT JOIN script_versions v ON v.script_id=s.id AND v.version=s.current_version WHERE s.production IN ("
                    + placeholders
                    + ") ORDER BY s.created_at DESC LIMIT 100",
                    ids,
                )
            ]
    from core import production_policy

    policy = production_policy.load(paths().config_dir)
    window = (
        policy.get("jobs", {}).get(meta["job"])
        or policy.get("window")
        or {"mode": "rolling", "days": 7}
    )
    all_plans = schedule_store().list()
    plans = [p for p in all_plans if p["job_name"] == meta["job"]]
    result = {
        **meta,
        "access": chain_access.snapshot(key),
        "revision": digest(meta, all_plans=all_plans),
        "research": {
            "name": job.get("name", ""),
            "prompt": job.get("prompt", ""),
            "sources": job.get("sources", []),
            "topics": job.get("topics", {}),
            "enabled": job.get("enabled", True),
            "search": job.get("search", False),
        },
        "source_supply": supply,
        "window_days": window.get("days", 7),
        "window_mode": window.get("mode", "rolling"),
        "sources": refs,
        "matrix": {"key": owner.key, "name": owner.name} if owner else None,
        "outputs_detail": outputs,
        "schedules": plans,
        "shared_chains": [
            {"key": m["key"], "name": m["name"]}
            for m in active_chains
            if m["key"] != key and m["job"] == meta["job"]
        ],
        "policy_managed": production_policy.manages(
            policy,
            paths().config_dir,
            job=meta["job"],
        ),
        "scripts": scripts[:100],
        "script_count": script_count,
        "runs": runs(key, meta=meta),
    }
    if meta.get("archived") and meta.get("snapshot"):
        for field in (
            "research",
            "sources",
            "matrix",
            "outputs_detail",
            "source_supply",
            "window_days",
            "window_mode",
        ):
            result[field] = meta["snapshot"].get(field, result.get(field))
    return result


def summary() -> list[dict]:
    configs = production.load_all(paths().config_dir, paths().jobs_dir)
    plans = {p["job_name"]: p for p in schedule_store().list()}
    owners = {
        job: m.key
        for m in matrices.load_all(paths().config_dir).values()
        for job in m.jobs
    }
    latest = {}
    with db() as connection:
        for row in connection.execute(
            "SELECT chain_id,data FROM runs ORDER BY rowid DESC"
        ):
            latest.setdefault(row["chain_id"], json.loads(row["data"]))
    out = []
    for m in all_chains():
        ps = [configs[p] for p in m.get("outputs", []) if p in configs]
        out.append(
            {
                **m,
                "matrix_key": owners.get(m["job"], ""),
                "output_count": len(ps),
                "automatic": sum(bool(p.auto) for p in ps),
                "errors": [e for p in ps for e in p.errors],
                "schedule": plans.get(m["job"]),
                "latest_run": latest.get(m["key"]),
            }
        )
    return out


def create(body: dict, user: dict) -> dict:
    key = key_ok(body["key"])
    name = str(body.get("name", "")).strip()
    if not name:
        error("请填写链路名称", 422)
    from core.video.length import MIN_MINUTES, MAX_MINUTES

    minutes = float(body.get("minutes", 1.25))
    if not MIN_MINUTES <= minutes <= min(MAX_MINUTES, 10):
        error(f"目标时长应在 {MIN_MINUTES:g}–10 分钟之间", 422)
    kinds = list(dict.fromkeys(body.get("output_modes", [])))
    if not kinds or set(kinds) - {"individual", "episode"}:
        error("至少选择一种视频输出方式", 422)
    schedule = str(body.get("schedule", "")).strip()
    if schedule and user["role"] != "admin":
        error("当前账号可以保存手动链路；设置周期需要管理员", 403)
    with transaction():
        baseline_errors = {
            (p.key, e)
            for p in production.load_all(paths().config_dir, paths().jobs_dir).values()
            for e in p.errors
        }
        if key in metadata() or (cfg() / "matrices" / f"{key}.yaml").exists():
            error("这个标识已被使用")
        if any(
            (folder / name).exists()
            for folder, name in [
                (Path(paths().jobs_dir), f"chains/{key}.yaml"),
                (cfg() / "video_recipes", f"chain_{key}.yaml"),
                (cfg() / "content_columns", f"{key}_episode.yaml"),
                *(
                    (cfg() / "production", f"{key}_{mode}.yaml")
                    for mode in ("each", "daily")
                ),
            ]
        ):
            error("新链路标识会覆盖已有配置，请更换标识")
        if not body.get("existing_job"):
            from core.video import topic_store

            if key in topic_store.sources(paths().jobs_dir):
                error("研究选题别名已被使用，请更换标识")
        job_id = body.get("existing_job") or f"chains/{key}"
        owned = []
        if body.get("existing_job"):
            job = load_job(paths().jobs_dir, job_id)
            if not job or not job.get("topics"):
                error("共享研究必须已经提供条目型选题", 422)
            owner = matrices.of_job(job_id, paths().config_dir)
            if not owner:
                error("共享研究尚未归属矩阵，请先设置", 422)
            matrix_key = owner.key
            if schedule:
                error("共享研究沿用已有周期，请创建后从研究设置修改", 422)
        else:
            refs = list(dict.fromkeys(body.get("sources", [])))
            if not refs:
                error("至少选择一个信息源", 422)
            resolved = sources.resolve(refs, paths().config_dir)
            if not resolved:
                error("所选信息源均已停用，请选择可用来源", 422)
            matrix_key = key
            from core.video import topic_store

            taken = {
                s.prefix for s in topic_store.sources(paths().jobs_dir).values()
            } | set(topic_store.DEFAULT_PREFIXES.values())
            prefix = next(
                (
                    a + b + c
                    for a in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
                    for b in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
                    for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
                    if a + b + c not in taken
                ),
                None,
            )
            prompt = str(body.get("prompt", "")).strip()
            if not prompt:
                error("请说明这条链路研究什么、面向谁", 422)
            job = {
                "name": name,
                "enabled": True,
                "language": "zh",
                "keywords": body.get("keywords", []),
                "search": False,
                "sources": refs,
                "sources_limit": 10,
                "runtime": {"timeout_seconds": 3600, "skip_if_running": True},
                "prompt": prompt
                + "\n\n日期：{date}。根据取得的材料形成可供视频作者使用的研究底稿。保留具体变化、例子、解释及原始链接。使用条目格式：\\n### 1.1 选题标题\\n- **一句话**：内容\\n- **发布日期**：日期\\n- **证据来源**：链接\\n正文充分展开，末尾列来源清单。没有可用题材时写“本次暂无新题材”。",
                "output": f"output/chains/{key}/{{date}}.md",
                "topics": {
                    "alias": key,
                    "format": "flash",
                    "prefix": prefix,
                    "refresh": True,
                },
            }
            job_path = Path(paths().jobs_dir) / (job_id + ".yaml")
            write(job_path, job)
            owned.append(str(job_path.relative_to(Path(paths().jobs_dir).parent)))
            write(
                cfg() / "matrices" / f"{key}.yaml",
                {
                    "name": name,
                    "domain": name,
                    "keywords": body.get("keywords", []),
                    "jobs": [job_id],
                    "render": {"video_speed": body.get("video_speed", 1.25)},
                    "content": {"name": name, "role": "domain", "status": "configured"},
                },
            )
            owned.append(f"config/matrices/{key}.yaml")
            policy = read(cfg() / "production_policy.yaml")
            policy.setdefault("matrices", []).append(matrix_key)
            policy.setdefault("jobs", {})[job_id] = {
                "mode": "rolling",
                "days": body.get("window_days", 7),
                "timezone": "Asia/Shanghai",
            }
            write(cfg() / "production_policy.yaml", policy)
        recipe = read(cfg() / "video_recipes/flash.yaml")
        recipe.update(name=name + "讲稿", target_minutes=body.get("minutes", 1.25))
        recipe["topic"]["sources"] = [job["topics"]["alias"]]
        recipe["tags"]["domain"] = {"key": "chain_" + key, "name": name}
        audience = body.get("audience", "non_tech")
        if audience not in ("developer", "non_tech"):
            error("请选择开发者或普通观众", 422)
        recipe["audiences"] = [audience] + [
            a for a in ("non_tech", "developer") if a != audience
        ]
        recipe["minutes_range"] = [1, 10]
        # Keep the actual established writer and rendering path.
        recipe_key = "chain_" + key
        write(cfg() / "video_recipes" / f"{recipe_key}.yaml", recipe)
        owned.append(f"config/video_recipes/{recipe_key}.yaml")
        ids = []
        for mode in kinds:
            pid = f"{key}_{'each' if mode == 'individual' else 'daily'}"
            ids.append(pid)
            production.save(
                paths().config_dir,
                pid,
                job=job_id,
                recipe=recipe_key,
                name=name + (" · 单条" if mode == "individual" else " · 合集"),
                reverify=False,
                auto=1,
                render={
                    "video_speed": body.get("video_speed", 1.25),
                    "voice_id": body.get("voice_id") or "voxcpm_anchor",
                },
            )
            if mode == "episode":
                col = key + "_episode"
                write(
                    cfg() / "content_columns" / f"{col}.yaml",
                    {
                        "name": name + "合集",
                        "matrix": matrix_key,
                        "type": "news",
                        "promise": str(body.get("prompt") or name),
                        "production_ids": [pid],
                        "content_mode": "episode",
                    },
                )
                owned.append(f"config/content_columns/{col}.yaml")
        if len(ids) == 2:
            jp = read(cfg() / "joint_planning.yaml")
            jp.setdefault("pairs", []).append(
                {
                    "episode_production": key + "_daily",
                    "single_production": key + "_each",
                }
            )
            write(cfg() / "joint_planning.yaml", jp)
        made = production.load_all(paths().config_dir, paths().jobs_dir)
        new_errors = {
            (p.key, e) for p in made.values() for e in p.errors
        } - baseline_errors
        problems = [f"{p}：{e}" for p, e in new_errors]
        if problems:
            error("；".join(problems), 422)
        if schedule:
            from web.services import managed_scheduler

            managed_scheduler.ensure_online()
            schedule_store().save(
                job_id, schedule, timezone="Asia/Shanghai", missed_policy="skip"
            )
        meta = {
            "key": key,
            "name": name,
            "job": job_id,
            "outputs": ids,
            "owned": owned,
            "imported": False,
            "schedule_armed": bool(schedule),
            "created_at": time.time(),
        }
        remember(meta)
    return detail(key)


def update(key: str, body: dict, user: dict) -> dict:
    with transaction():
        m = find(key)
        assert_revision(m, body.get("revision"))
        if set(body) - {"revision", "name", "schedule", "apply_shared"}:
            assert_idle(m["job"])
        if body.get("name") is not None:
            if not body["name"].strip():
                error("名称不能为空", 422)
            m["name"] = body["name"].strip()
        jpath = Path(paths().jobs_dir) / (m["job"] + ".yaml")
        job = read(jpath)
        if any(
            k in body
            for k in (
                "sources",
                "prompt",
                "schedule",
                "source_supply_enabled",
                "window_days",
                "window_mode",
            )
        ):
            shared = [
                x for x in all_chains() if x["job"] == m["job"] and x["key"] != key
            ]
            if shared and not body.get("apply_shared"):
                error("这项研究被其他链路共用，请查看影响范围后选择应用到共享研究")
        if "sources" in body:
            if not body["sources"]:
                error("研究至少需要一个来源", 422)
            sources.resolve(body["sources"], paths().config_dir)
            job["sources"] = body["sources"]
        if "prompt" in body:
            if not body["prompt"].strip():
                error("研究目标不能为空", 422)
            job["prompt"] = body["prompt"]
        if "source_supply_enabled" in body:
            job["source_supply_enabled"] = bool(body["source_supply_enabled"])
        if "window_days" in body or "window_mode" in body:
            days = int(body.get("window_days", 7))
            if not 1 <= days <= 90:
                error("研究窗口需要为 1–90 天", 422)
            policy = read(cfg() / "production_policy.yaml")
            mode = body.get("window_mode", "rolling")
            if mode not in ("rolling", "previous_slot"):
                error("请选择滚动窗口或相邻周期", 422)
            policy.setdefault("jobs", {})[m["job"]] = {
                "mode": mode,
                "timezone": "Asia/Shanghai",
                **({"days": days} if mode == "rolling" else {}),
            }
            write(cfg() / "production_policy.yaml", policy)
        if any(k in body for k in ("sources", "prompt", "source_supply_enabled")):
            write(jpath, job)
        for o in body.get("outputs", []):
            if o["key"] not in m["outputs"]:
                error("输出不属于该链路", 422)
            path = cfg() / "production" / f"{o['key']}.yaml"
            data = read(path)
            if "auto" in o:
                data["auto"] = int(bool(o["auto"]))
            for setting in ("minutes", "audience"):
                if setting in o:
                    data[setting] = o[setting]
            if "render" in o:
                from core.video.production_settings import validate

                data["render"] = validate(o["render"])
            production._write(paths().config_dir, o["key"], data)
            changed = production.get(paths().config_dir, paths().jobs_dir, o["key"])
            if changed.errors:
                error("；".join(changed.errors), 422)
        if "schedule" in body:
            if user["role"] != "admin":
                error("设置周期需要管理员", 403)
            plans = [p for p in schedule_store().list() if p["job_name"] == m["job"]]
            expression = body["schedule"].strip()
            if expression:
                p = plans[0] if plans else None
                from web.services import managed_scheduler

                managed_scheduler.ensure_online()
                schedule_store().save(
                    m["job"],
                    expression,
                    timezone=p["timezone"] if p else "Asia/Shanghai",
                    missed_policy=p["missed_policy"] if p else "skip",
                    max_retries=p["max_retries"] if p else 2,
                    schedule_id=p["id"] if p else None,
                    editing=bool(p),
                    expected_version=p["version"] if p else None,
                )
            else:
                for p in plans:
                    schedule_store().delete(p["id"])
            m["schedule_armed"] = bool(expression)
        for pid in body.get("remove_outputs", []):
            if pid not in m["outputs"]:
                error("输出不属于此链路", 422)
            m["output_template"] = read(cfg() / "production" / f"{pid}.yaml")
            remove_output(pid)
            m["outputs"].remove(pid)
            m.setdefault("retired_outputs", []).append(pid)
        if body.get("add_output"):
            spec = body["add_output"]
            mode = spec.get("mode")
            alias = (job.get("topics") or {}).get("alias")
            if mode not in (("individual", "episode") if alias else ("whole",)):
                error("条目型研究请选择单条或合集，整份报告请选择整份报告视频", 422)
            current_outputs = production.load_all(paths().config_dir, paths().jobs_dir)
            template = next(
                (current_outputs[k] for k in m["outputs"] if k in current_outputs), None
            )
            inherited = (
                read(cfg() / "production" / f"{template.key}.yaml")
                if template
                else m.get("output_template", {})
            )
            if mode == "whole":
                recipe_key = inherited.get("recipe")
                if not recipe_key:
                    error("当前研究没有可沿用的整份报告配方，请先在产出页配置", 422)
            else:
                recipe_key = "chain_" + key
                recipe_path = cfg() / "video_recipes" / f"{recipe_key}.yaml"
                if not recipe_path.exists():
                    recipe = read(cfg() / "video_recipes/flash.yaml")
                    recipe["name"] = m["name"] + "讲稿"
                    recipe["topic"]["sources"] = [alias]
                    recipe["tags"]["domain"] = {
                        "key": "chain_" + key,
                        "name": m["name"],
                    }
                    recipe["audiences"] = ["non_tech"]
                    write(recipe_path, recipe)
                    m.setdefault("owned", []).append(
                        f"config/video_recipes/{recipe_key}.yaml"
                    )
            pid = key + "_" + uuid.uuid4().hex[:6]
            production.save(
                paths().config_dir,
                pid,
                job=m["job"],
                recipe=recipe_key,
                name=spec.get("name") or m["name"],
                audience=inherited.get("audience", "") if mode == "whole" else "",
                minutes=inherited.get("minutes") if mode == "whole" else None,
                reverify=inherited.get("reverify") if mode == "whole" else False,
                auto=inherited.get("auto", 1),
                render=copy.deepcopy(
                    inherited.get("render", {"voice_id": "voxcpm_anchor"})
                ),
            )
            if mode == "episode":
                owner = matrices.of_job(m["job"], paths().config_dir)
                if not owner:
                    error("请先把研究加入矩阵", 422)
                write(
                    cfg() / "content_columns" / f"{pid}.yaml",
                    {
                        "name": spec.get("name") or m["name"] + "合集",
                        "matrix": owner.key,
                        "type": "news",
                        "promise": m["name"],
                        "production_ids": [pid],
                        "content_mode": "episode",
                    },
                )
            made = production.get(paths().config_dir, paths().jobs_dir, pid)
            if made.errors:
                error("；".join(made.errors), 422)
            m["outputs"].append(pid)
            configs = production.load_all(paths().config_dir, paths().jobs_dir)
            peer = next(
                (
                    p
                    for p in configs.values()
                    if p.key in m["outputs"] and p.key != pid and p.content_mode != mode
                ),
                None,
            )
            if peer and mode != "whole":
                jp = read(cfg() / "joint_planning.yaml")
                jp.setdefault("pairs", []).append(
                    {
                        "episode_production": pid if mode == "episode" else peer.key,
                        "single_production": peer.key if mode == "episode" else pid,
                    }
                )
                write(cfg() / "joint_planning.yaml", jp)
        remember(m)
    return detail(key)


def attach_output(key: str, pid: str) -> None:
    """A legacy page opened from a chain keeps new outputs in that exact chain."""
    member = find(key)
    output = production.get(paths().config_dir, paths().jobs_dir, pid)
    if not output or output.job != member["job"]:
        error("新增输出的研究任务与当前链路不一致", 422)
    if any(
        pid in m.get("outputs", []) and m["key"] != key for m in metadata().values()
    ):
        error("该输出已经属于另一条链路", 409)
    member["outputs"] = list(dict.fromkeys(member.get("outputs", []) + [pid]))
    remember(member)


def toggle(key: str, body: dict, user: dict) -> dict:
    with transaction():
        m = find(key)
        assert_revision(m, body.get("revision"))
        scope = body.get("scope", "outputs")
        enabled = bool(body["enabled"])
        if scope == "research":
            if user["role"] != "admin":
                error("控制研究周期需要管理员", 403)
            plans = [p for p in schedule_store().list() if p["job_name"] == m["job"]]
            if not plans:
                error("尚未设置周期，请先保存运行安排", 422)
            for p in plans:
                schedule_store().toggle(p["id"], enabled)
            m["schedule_armed"] = enabled
        elif scope == "outputs":
            current = production.load_all(paths().config_dir, paths().jobs_dir)
            if not enabled:
                m["resume_outputs"] = {
                    pid: current[pid].auto for pid in m["outputs"] if pid in current
                }
            for pid in m["outputs"]:
                if (cfg() / "production" / f"{pid}.yaml").exists():
                    production.set_auto(
                        paths().config_dir,
                        pid,
                        m.get("resume_outputs", {}).get(pid, 1)
                        if enabled and current.get(pid) and current[pid].split != "some"
                        else 0,
                    )
        else:
            error("未知暂停范围", 422)
        remember(m)
    return detail(key)


def remove_output(pid: str) -> None:
    if active_output(pid):
        error("该输出仍有运行中的批次，请先取消或等待完成")
    for member in all_chains():
        if pid not in member.get("outputs", []):
            continue
        member["output_template"] = read(cfg() / "production" / f"{pid}.yaml")
        member["outputs"] = [other for other in member["outputs"] if other != pid]
        member["retired_outputs"] = list(
            dict.fromkeys(member.get("retired_outputs", []) + [pid])
        )
        remember(member)
    for f in (cfg() / "content_columns").glob("*.yaml"):
        c = read(f)
        if pid in c.get("production_ids", []):
            c["production_ids"] = [x for x in c["production_ids"] if x != pid]
            if c["production_ids"]:
                write(f, c)
            else:
                remove_config_file(f)
    f = cfg() / "joint_planning.yaml"
    jp = read(f)
    prior_pairs = jp.get("pairs", [])
    jp["pairs"] = [
        p
        for p in prior_pairs
        if pid not in (p.get("episode_production"), p.get("single_production"))
    ]
    if jp["pairs"] != prior_pairs:
        write(f, jp)
    production.remove(paths().config_dir, pid)
    for key, m in metadata().items():
        if pid in m.get("outputs", []) and not m.get("archived"):
            m["outputs"].remove(pid)
            m.setdefault("retired_outputs", []).append(pid)
            remember(m)


def active_output(pid):
    with db() as d:
        rows = [json.loads(x["data"]) for x in d.execute("SELECT data FROM runs")]
    return any(
        r["status"] in ("RUNNING", "CANCELLING") and pid in r.get("outputs", [])
        for r in rows
    )


def delete_preview(key: str) -> dict:
    m = find(key)
    configs = production.load_all(paths().config_dir, paths().jobs_dir)
    remaining = [
        p.key
        for p in configs.values()
        if p.job == m["job"] and p.key not in m["outputs"]
    ]
    shared = [
        {"key": other["key"], "name": other["name"]}
        for other in all_chains()
        if other["key"] != key and other["job"] == m["job"]
    ]
    return {
        "archive_only": bool(m.get("imported")),
        "outputs": [] if m.get("imported") else m["outputs"],
        "job": m["job"],
        "remaining_outputs": remaining,
        "shared_chains": shared,
        "keep_job": bool(remaining or shared),
        "keep_sources": True,
        "keep_history": True,
        "running": bool(
            active_for_job(m["job"])
            or any(r["job"] == m["job"] for r in running_dependencies())
        ),
        "revision": digest(m),
    }


def delete(key: str, revision: str) -> dict:
    with transaction():
        m = find(key)
        assert_revision(m, revision)
        assert_idle(m["job"])
        preview = delete_preview(key)
        snapshot = detail(key)
        # Archive first to keep history ownership while individual output references are removed.
        m["archived"] = True
        m["archived_at"] = time.time()
        m["snapshot"] = snapshot
        remember(m)
        if m.get("imported"):
            return {
                "ok": True,
                "archived": key,
                "note": "现有链路入口已归档；原研究、周期、输出和历史继续保留",
            }
        for pid in list(m["outputs"]):
            remove_output(pid)
        if not preview["keep_job"]:
            schedule_store().delete_for_job(m["job"])
            jp = Path(paths().jobs_dir) / (m["job"] + ".yaml")
            if jp.exists():
                remove_config_file(jp)
            for f in (cfg() / "matrices").glob("*.yaml"):
                v = read(f)
                prior_jobs = v.get("jobs", [])
                remaining = [
                    j
                    for j in prior_jobs
                    if (j if isinstance(j, str) else j.get("job")) != m["job"]
                ]
                if remaining != prior_jobs:
                    v["jobs"] = remaining
                    write(f, v)
            pol = read(cfg() / "production_policy.yaml")
            if m["job"] in pol.get("jobs", {}):
                pol["jobs"].pop(m["job"])
                write(cfg() / "production_policy.yaml", pol)
        cleanup_owned(m["job"])
        return {
            "ok": True,
            "archived": key,
            "note": "链路已删除；共享来源和历史产出仍保留",
        }


def cleanup_owned(job):
    """Retain shared/history dependencies; remove only objects created for this research."""
    members = [
        m for m in metadata().values() if m["job"] == job and not m.get("imported")
    ]
    owned = {name for m in members for name in m.get("owned", [])}
    configs = production.load_all(paths().config_dir, paths().jobs_dir)
    with store.connect() as connection:
        columns = [
            r[0] for r in connection.execute("SELECT DISTINCT column_key FROM scripts")
        ]
    historical_bases = {
        recipe.base
        for name in columns
        if (recipe := recipes.find(paths().config_dir, name)) is not None
    }
    ids = {
        pid
        for member in members
        for pid in member.get("outputs", []) + member.get("retired_outputs", [])
    }
    with store.connect() as connection:
        has_history = (
            bool(
                connection.execute(
                    "SELECT 1 FROM scripts WHERE production IN ("
                    + ",".join("?" for _ in ids)
                    + ") LIMIT 1",
                    sorted(ids),
                ).fetchone()
            )
            if ids
            else False
        )
    for name in sorted(owned):
        path = (cfg().parent / name).resolve()
        if not path.is_relative_to(cfg().resolve()) or not path.is_file():
            continue
        if path.parent == (cfg() / "video_recipes").resolve():
            if path.stem not in historical_bases and not any(
                p.base_recipe == path.stem for p in configs.values()
            ):
                remove_config_file(path)
        elif path.parent == (cfg() / "matrices").resolve():
            value = read(path)
            if value.get("jobs"):
                continue
            if has_history:
                value["archived"] = True
                write(path, value)
            else:
                remove_config_file(path)
            policy = read(cfg() / "production_policy.yaml")
            if path.stem in policy.get("matrices", []):
                policy["matrices"].remove(path.stem)
                write(cfg() / "production_policy.yaml", policy)


def mutation(fn):
    """Apply the same transaction and relationship checks to legacy management tabs."""
    from functools import wraps

    @wraps(fn)
    def wrapped(*args, **kwargs):
        import inspect

        bound = inspect.signature(fn).bind(*args, **kwargs)
        job_id = bound.arguments.get("job_id")
        if job_id:
            from web.routes.scheduler import _is_system_job

            if _is_system_job(job_id):
                return fn(*args, **kwargs)
        with transaction():
            configs = production.load_all(paths().config_dir, paths().jobs_dir)
            before = {(p.key, e) for p in configs.values() for e in p.errors}
            # Configuration edits during a run are explicit, never silently applied mid-script.
            with db() as d:
                active = [
                    json.loads(x["data"]) for x in d.execute("SELECT data FROM runs")
                ]
            active = [
                x for x in active if x["status"] in ("RUNNING", "CANCELLING")
            ] + running_dependencies()

            def dependencies(row):
                job = load_job(paths().jobs_dir, row["job"]) or {}
                ps = production.load_all(paths().config_dir, paths().jobs_dir)
                owner = matrices.of_job(row["job"], paths().config_dir)
                selected = [ps[x] for x in row.get("outputs", []) if x in ps]
                registry = sources.load_registry(paths().config_dir)
                refs = job.get("sources", [])
                used = {
                    k: v
                    for k, v in registry.items()
                    if "source:" + k in refs
                    or v["spec"] in refs
                    or any("tag:" + t in refs for t in v.get("tags", []))
                }
                return {
                    "job": job,
                    "sources": used,
                    "matrix": read(cfg() / "matrices" / f"{owner.key}.yaml")
                    if owner
                    else None,
                    "outputs": [
                        {k: v for k, v in p.as_dict().items() if k != "auto"}
                        for p in selected
                    ],
                    "recipes": [
                        read(cfg() / "video_recipes" / f"{p.recipe}.yaml")
                        for p in selected
                    ],
                    "columns": [
                        content_columns.snapshot(paths().config_dir, p.key, row["job"])
                        for p in selected
                    ],
                }

            frozen = {r["id"]: dependencies(r) for r in active}
            plans_before = {p["id"]: p for p in schedule_store().list()}
            result = fn(*args, **kwargs)
            if any(dependencies(r) != frozen[r["id"]] for r in active):
                error(
                    "此修改会影响正在运行的链路，请等待该批次结束；其他链路可继续管理"
                )
            for plan in schedule_store().list():
                prior = plans_before.get(plan["id"])
                if not prior or any(
                    plan[k] != prior[k] for k in ("version", "enabled")
                ):
                    for chain in all_chains():
                        if chain["job"] == plan["job_name"]:
                            chain["schedule_armed"] = bool(plan["enabled"])
                            remember(chain)
            after = {
                (p.key, e)
                for p in production.load_all(
                    paths().config_dir, paths().jobs_dir
                ).values()
                for e in p.errors
            }
            introduced = after - before
            if introduced:
                error(
                    "这项修改会使关联视频输出失效："
                    + "；".join(f"{p}：{e}" for p, e in sorted(introduced))
                )
            return result

    return wrapped


def scope(key: str) -> dict:
    m = find(key, True)
    ids = set(m.get("outputs", []) + m.get("retired_outputs", []))
    with store.connect() as d:
        script_ids = (
            [
                row["id"]
                for row in d.execute(
                    "SELECT id FROM scripts WHERE production IN ("
                    + ",".join("?" for _ in ids)
                    + ")",
                    sorted(ids),
                )
            ]
            if ids
            else []
        )
    owner = matrices.of_job(m["job"], paths().config_dir)
    configs = production.load_all(paths().config_dir, paths().jobs_dir)
    return {
        "key": key,
        "name": m["name"],
        "job": m["job"],
        "outputs": sorted(ids),
        "script_ids": script_ids,
        "matrix_key": owner.key if owner else None,
        "recipes": sorted({configs[k].base_recipe for k in ids if k in configs}),
    }
