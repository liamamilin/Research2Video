"""Matrices: a set of research Jobs sharing a domain and keywords, listed, edited and copied (spec/matrices.md).

A matrix only groups Jobs. Running a member and changing its period (manual, daily, weekdays, some weekdays,
monthly) can be done from the matrix page; pausing, cancelling and the schedule's finer settings stay on the Jobs and
schedule pages. Copying a matrix with new keywords makes new Job files from each member's ``copy_as`` template: they
start manual, so a copy never runs or spends anything until someone starts it.
"""

from __future__ import annotations

import os
import re
import string

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from datetime import datetime

from core import cron, matrices, periods
from core.config import list_jobs, load_job
from web import audit
from web.deps import require_admin, require_editor, require_viewer
from web.models import ApiError
from web.services import yaml_io
from web.settings import get_settings

router = APIRouter(prefix="/api/matrices", tags=["matrices"])


class MatrixIn(BaseModel):
    key: str = ""
    name: str
    domain: str = ""
    keywords: list[str] = Field(default_factory=list)
    jobs: list[str] | None = None  # editing: the members, in order; creating: none yet
    monthly_searches: int | None = Field(default=None, ge=0, le=1_000_000)  # editing: none keeps it, 0 lifts it


class CopyIn(BaseModel):
    key: str
    name: str
    domain: str
    keywords: list[str]
    jobs: list[str]  # which of the source's copyable members to bring along


def _error(code: int, kind: str, message: str) -> HTTPException:
    return HTTPException(status_code=code, detail=ApiError.make(kind, message))


def _dirs() -> tuple[str, str]:
    paths = get_settings().paths
    return paths.config_dir, paths.jobs_dir


def _keywords(values: list[str]) -> list[str]:
    out = [k.strip() for k in values if k and k.strip()]
    return list(dict.fromkeys(out))[:20]


def _plans() -> dict[str, dict]:
    """Each Job's schedule plan, if it has one (the schedule page owns them; read here only)."""
    try:
        from web.services import managed_scheduler as managed

        out = {}
        for plan in managed.store().list():
            shown = managed.describe(plan)
            out[plan["job_name"]] = {"id": plan["id"], "schedule": plan["schedule"], "timezone": plan["timezone"],
                                     "enabled": shown["enabled"], "next_runs": shown["next_runs"][:1],
                                     "period": periods.from_cron(plan["schedule"]),
                                     "runs_per_month": periods.runs_per_month(plan["schedule"], datetime.now().astimezone())}
        return out
    except Exception:  # noqa: BLE001 - no scheduler state: every member reads as manual
        return {}


def _member(jobs_dir: str, member: matrices.Member, plans: dict) -> dict:
    from web.routes.jobs import _registry, _state_for

    job = load_job(jobs_dir, member.job)
    if job is None:
        return {"job": member.job, "exists": False, "name": member.job, "copy_as": member.copy_as,
                "copyable": False, "schedule": None}
    state = _state_for(member.job)
    topics = job.get("topics") if isinstance(job.get("topics"), dict) else {}
    return {
        "job": member.job, "exists": True, "name": job.get("name") or member.job,
        "description": job.get("description", ""), "enabled": bool(job.get("enabled", True)),
        "keywords": list(job.get("keywords") or []), "copy_as": member.copy_as, "copyable": bool(member.copy_as),
        "topics": topics.get("alias") or (member.job if topics else ""),
        "state": state.model_dump() if state else None, "is_running": _registry.is_running(member.job),
        "searches_per_run": int(((state.last_usage if state else None) or {}).get("searches") or 0),
        "schedule": plans.get(member.job),
    }


def _scripts_by_report():
    """``(job, report path)`` -> how many scripts were written from that report (failed ones left out)."""
    from collections import Counter

    from core.video import outputs

    config_dir, jobs_dir = _dirs()
    made = Counter()
    for s in outputs.collect(config_dir, jobs_dir)["scripts"]:
        if s["status"] != "FAILED":
            made[(s["job"], s["report_path"])] += 1
    return made


def _items_by_report() -> dict[str, int]:
    """Report path -> how many items its Job's topic list took from it."""
    from core.video import store

    with store.connect() as conn:
        return {r[0]: r[1] for r in conn.execute("SELECT report_path, COUNT(*) FROM topics WHERE report_path != '' GROUP BY report_path")}


def _latest_reports(matrix: matrices.Matrix) -> dict[str, dict]:
    """Each member's newest finished report and how many videos were written from it (Jobs that never succeeded: none)."""
    from core.state import StateManager

    latest = {}
    for job in matrix.jobs:
        last = next((h for h in StateManager.get_history(job, limit=200) if h.get("status") == "success" and h.get("output_path")), None)
        if last:
            latest[job] = {"path": last["output_path"], "ts": last.get("ts", "")}
    if not latest:
        return {}
    made = _scripts_by_report()
    return {job: {**info, "scripts": made[(job, info["path"])]} for job, info in latest.items()}


def _shown(matrix: matrices.Matrix, jobs_dir: str, plans: dict, reports: bool = False) -> dict:
    latest = _latest_reports(matrix) if reports else {}
    members = [{**_member(jobs_dir, m, plans), **({"latest_report": latest[m.job]} if m.job in latest else {})} for m in matrix.members]
    spent = {"searches_this_month": matrices.month_searches(matrix)} if reports and matrix.monthly_searches else {}
    return {**matrix.as_dict(), "members": members, **spent}


@router.get("")
def list_matrices(user=Depends(require_viewer)):
    config_dir, jobs_dir = _dirs()
    every = matrices.load_all(config_dir)
    plans = _plans()
    grouped = {j for m in every.values() for j in m.jobs}
    ungrouped = []
    for name in list_jobs(jobs_dir):
        if name in grouped or name.startswith(("video/", "vod/")):
            continue
        job = load_job(jobs_dir, name) or {}
        ungrouped.append({"job": name, "name": job.get("name") or name})
    return {"matrices": [_shown(m, jobs_dir, plans) for m in every.values()], "ungrouped": ungrouped}


def _get(key: str) -> matrices.Matrix:
    matrix = matrices.load_all(_dirs()[0]).get(key)
    if matrix is None:
        raise _error(404, "matrix_not_found", f"没有这个矩阵：{key}")
    return matrix


@router.get("/{key}")
def get_matrix(key: str, user=Depends(require_viewer)):
    return _shown(_get(key), _dirs()[1], _plans(), reports=True)


def _new_key(key: str) -> str:
    key = key.strip()
    if not matrices.KEY.fullmatch(key):
        raise _error(422, "invalid_key", "矩阵的英文名要用小写字母开头，只含小写字母、数字和下划线，最多 24 个字符")
    config_dir, jobs_dir = _dirs()
    if key in matrices.load_all(config_dir) or os.path.exists(os.path.join(matrices.directory(config_dir), f"{key}.yaml")):
        raise _error(409, "matrix_exists", f"矩阵 {key} 已经有了")
    return key


def _members(jobs: list[str], keep: matrices.Matrix | None) -> list[matrices.Member]:
    """Validated members: real Jobs, not generated, not in another matrix. A member that stays keeps its copy_as."""
    config_dir, jobs_dir = _dirs()
    before = {m.job: m for m in keep.members} if keep else {}
    owner = {j: m for m in matrices.load_all(config_dir).values() if not keep or m.key != keep.key for j in m.jobs}
    out: list[matrices.Member] = []
    for job in dict.fromkeys(j.strip() for j in jobs if j.strip()):
        if job.startswith(("video/", "vod/")):
            raise _error(422, "generated_job", f"{job} 是视频流水线生成的 Job，不能放进矩阵")
        if job in owner:
            raise _error(409, "job_in_other_matrix", f"{job} 已经在「{owner[job].name}」里：一个 Job 只能属于一个矩阵")
        if job not in before and load_job(jobs_dir, job) is None:
            raise _error(422, "job_not_found", f"没有这个 Job：{job}")
        out.append(before.get(job) or matrices.Member(job))
    return out


@router.post("", status_code=201)
def create_matrix(payload: MatrixIn, user=Depends(require_editor)):
    key = _new_key(payload.key)
    if not payload.name.strip():
        raise _error(422, "missing_name", "矩阵要有名字")
    members = _members(payload.jobs or [], None)
    matrices.save(_dirs()[0], key, name=payload.name.strip(), domain=payload.domain.strip(),
                  keywords=_keywords(payload.keywords), members=members)
    audit.log("matrix_create", user=user["username"], target=key)
    return get_matrix(key, user)


@router.put("/{key}")
def update_matrix(key: str, payload: MatrixIn, user=Depends(require_editor)):
    matrix = _get(key)
    if not payload.name.strip():
        raise _error(422, "missing_name", "矩阵要有名字")
    members = _members(payload.jobs, matrix) if payload.jobs is not None else list(matrix.members)
    matrices.save(_dirs()[0], key, name=payload.name.strip(), domain=payload.domain.strip(),
                  keywords=_keywords(payload.keywords), members=members, monthly_searches=payload.monthly_searches)
    audit.log("matrix_update", user=user["username"], target=key,
              details={"monthly_searches": payload.monthly_searches} if payload.monthly_searches is not None else None)
    return get_matrix(key, user)


@router.delete("/{key}")
def delete_matrix(key: str, user=Depends(require_editor)):
    _get(key)
    matrices.remove(_dirs()[0], key)
    audit.log("matrix_delete", user=user["username"], target=key)
    return {"ok": True, "note": "只删了分组，里面的 Job 都还在"}


def _prefix(base: str, key: str, taken: set[str]) -> str:
    """Topic code letters for a copied source: the matrix's initial plus the source's own (medical + F → MF)."""
    tail = base[-1:] or "T"
    for lead in [key[0].upper(), key[:2].upper()] + list(string.ascii_uppercase):
        candidate = lead + tail
        if re.fullmatch(r"[A-Z]{1,3}", candidate) and candidate not in taken:
            return candidate
    raise _error(409, "no_prefix", "选题编号前缀都被占了")


@router.post("/{key}/copy", status_code=201)
def copy_matrix(key: str, payload: CopyIn, user=Depends(require_editor)):
    """A new matrix with new keywords: each picked member is made again from its template, manual, in jobs/<new key>/."""
    from core.video import topic_store

    source = _get(key)
    new_key = _new_key(payload.key)
    name, domain, keywords = payload.name.strip(), payload.domain.strip(), _keywords(payload.keywords)
    if not name or not domain or not keywords:
        raise _error(422, "missing_fields", "新矩阵要写名字、领域和至少一个关键词")
    config_dir, jobs_dir = _dirs()
    if os.path.exists(os.path.join(jobs_dir, new_key)):
        raise _error(409, "jobs_dir_exists", f"jobs/{new_key}/ 已经有了：换一个英文名")
    by_job = {m.job: m for m in source.members}
    picked = [by_job.get(j) for j in dict.fromkeys(payload.jobs)]
    if not picked or any(m is None for m in picked):
        raise _error(422, "unknown_member", "只能复制这个矩阵里的 Job")
    stuck = [m.job for m in picked if not m.copy_as]
    if stuck:
        raise _error(422, "not_copyable", f"这些 Job 的提示词写死了原来的领域，暂不能复制：{'、'.join(stuck)}")

    tmpl_dir = os.path.join(jobs_dir, "_templates")
    taken = {s.prefix for s in topic_store.sources(jobs_dir).values()} | set(topic_store.DEFAULT_PREFIXES.values())
    files: list[tuple[str, str]] = []
    members: list[matrices.Member] = []
    for member in picked:
        path = os.path.join(tmpl_dir, f"{member.copy_as}.yaml")
        try:
            data = yaml_io.parse_yaml(open(path, encoding="utf-8").read())
        except (OSError, ValueError) as exc:
            raise _error(422, "template_missing", f"{member.job} 要用的模板 {member.copy_as} 读不了：{exc}") from exc
        slug = member.copy_as.lstrip("_")
        label = str(data.pop("label", "") or slug)
        data.pop("category", None)
        data.update({"name": f"{domain} {label}", "enabled": True, "keywords": [],
                     "output": f"output/{new_key}/{{date}}_{slug}.md", "schedule": {"type": "manual"}})
        original = load_job(jobs_dir, member.job) or {}
        topics = original.get("topics") if isinstance(original.get("topics"), dict) else None
        if topics:
            fmt = str(topics.get("format") or "flash")
            base = str(topics.get("prefix") or topic_store.DEFAULT_PREFIXES.get(fmt, "T"))
            prefix = _prefix(base, new_key, taken)
            taken.add(prefix)
            # refresh off: the autopilot never starts a copy on its own, so it spends nothing until someone runs it
            data["topics"] = {"alias": f"{new_key}_{slug}", "format": fmt, "prefix": prefix, "refresh": False}
        errors = yaml_io.validate_output_path(data, output_root=get_settings().paths.output_dir)
        if errors:
            raise _error(422, "invalid_output", "；".join(errors))
        job = f"{new_key}/{slug}"
        files.append((os.path.join(jobs_dir, f"{job}.yaml"), yaml_io.dump_yaml(data)))
        members.append(matrices.Member(job, copy_as=member.copy_as))

    os.makedirs(os.path.join(jobs_dir, new_key), exist_ok=True)
    written: list[str] = []
    try:
        for path, content in files:
            yaml_io.atomic_create(path, content)
            written.append(path)
        matrices.save(config_dir, new_key, name=name, domain=domain, keywords=keywords, members=members)
    except Exception:
        for path in written:  # all or nothing: a half-made matrix would leave Jobs nobody groups
            try:
                os.remove(path)
            except OSError:
                pass
        raise
    audit.log("matrix_copy", user=user["username"], target=new_key, details={"from": key, "jobs": len(members)})
    return get_matrix(new_key, user)


# --- A member's period -----------------------------------------------------------


class PeriodIn(BaseModel):
    kind: str
    time: str = "05:00"
    days: list[int] = Field(default_factory=list)
    day: int = 1
    confirm: bool = False  # turning a timed run on spends money on every run: the page asks first


def _member_of(key: str, job: str) -> None:
    if job not in _get(key).jobs:
        raise _error(404, "not_a_member", f"{job} 不在矩阵 {key} 里")


def _plan_of(job: str) -> dict | None:
    from web.services import managed_scheduler as managed

    return next((p for p in managed.store().list() if p["job_name"] == job), None)


def _cron_of(body: PeriodIn) -> str:
    try:
        return periods.to_cron(body.kind, body.time, body.days, body.day)
    except ValueError as exc:
        raise _error(422, "invalid_period", str(exc)) from exc


def _cost(expression: str, job: str) -> dict:
    from web.routes.jobs import _state_for

    state = _state_for(job)
    searches = int(((state.last_usage if state else None) or {}).get("searches") or 0)
    runs = periods.runs_per_month(expression, datetime.now().astimezone()) if expression else 0
    return {"runs_per_month": runs, "searches_per_run": searches, "searches_per_month": runs * searches}


@router.get("/{key}/jobs/{job:path}/period")
def preview_period(key: str, job: str, kind: str, time: str = "05:00", days: str = "", day: int = 1,
                   user=Depends(require_viewer)):
    """What a period would be: its cron, the next three runs and the monthly cost, without saving anything."""
    _member_of(key, job)
    try:
        listed = [int(d) for d in days.split(",") if d.strip()]
    except ValueError as exc:
        raise _error(422, "invalid_period", "每周几天要用数字，用逗号分开") from exc
    expression = _cron_of(PeriodIn(kind=kind, time=time, days=listed, day=day))
    if not expression:
        return {"schedule": "", "next_runs": [], **_cost("", job)}
    entry = cron.parse_schedule(expression)
    upcoming = cron.fire_times(entry, datetime.now().astimezone(), count=3)
    return {"schedule": expression, "next_runs": [t.isoformat(timespec="seconds") for t in upcoming], **_cost(expression, job)}


@router.put("/{key}/jobs/{job:path}/period")
def set_period(key: str, job: str, body: PeriodIn, user=Depends(require_admin)):
    """Change a member's period. ``manual`` removes its plan; a timed one saves it (and turns a paused one back on)."""
    from web.routes.scheduler import _mutate
    from web.services import managed_scheduler as managed

    _member_of(key, job)
    expression = _cron_of(body)
    plan = _plan_of(job)
    if not expression:
        if plan:
            _mutate(managed.store().delete, plan["id"])
        audit.log("matrix_period", user=user["username"], target=job, details={"kind": "manual"})
        return get_matrix(key, user)
    if not (plan and plan["enabled"]) and not body.confirm:
        cost = _cost(expression, job)
        raise _error(409, "confirm_needed", (
            f"打开定时后每次都会自己运行并花搜索额度：每月约 {cost['runs_per_month']} 次"
            + (f"，上次每次用了 {cost['searches_per_run']} 次搜索，约 {cost['searches_per_month']} 次" if cost["searches_per_run"] else "")
            + "。确认后再保存"))
    if plan:
        _mutate(lambda: managed.save(job, expression, schedule_id=plan["id"], editing=True, timezone=plan["timezone"],
                                     missed_policy=plan["missed_policy"], max_retries=plan["max_retries"],
                                     expected_version=plan["version"]))
        if not plan["enabled"]:
            _mutate(managed.ensure_online)
            _mutate(managed.store().toggle, plan["id"], True)
    else:
        _mutate(lambda: managed.save(job, expression))
    audit.log("matrix_period", user=user["username"], target=job, details={"kind": body.kind, "schedule": expression})
    return get_matrix(key, user)


# --- Runs of a matrix ------------------------------------------------------------

#: history ``user`` values of runs nobody started by hand
_BY_ITSELF = ("scheduler", "autopilot")
_STATUSES = ("success", "failed", "cancelled", "skipped")


@router.get("/{key}/runs")
def matrix_runs(key: str, job: str = "", status: str = "", days: int = 0, order: str = "desc", limit: int = 30,
                offset: int = 0, user=Depends(require_viewer)):
    """Every run of every member, newest first (``order=asc`` for oldest first): when, which Job, scheduled or by hand,
    how it ended, what it cost in searches, the report it wrote, how many items that report has (when it is an item list) and how many videos came from it."""
    from datetime import timedelta

    from core.state import StateManager

    matrix = _get(key)
    if status and status not in _STATUSES:
        raise _error(422, "bad_status", f"状态只能是 {'、'.join(_STATUSES)} 之一")
    if order not in ("asc", "desc") or not 0 <= days <= 3650 or not 1 <= limit <= 200 or offset < 0:
        raise _error(422, "bad_query", "order 是 asc 或 desc，days 在 0–3650，limit 在 1–200，offset 不小于 0")
    if job and job not in matrix.jobs:
        raise _error(404, "not_a_member", f"{job} 不在矩阵 {key} 里")
    config_dir, jobs_dir = _dirs()
    since = (datetime.now().astimezone() - timedelta(days=days)).isoformat(timespec="seconds") if days else ""
    rows = []
    for name in ([job] if job else matrix.jobs):
        loaded = load_job(jobs_dir, name)
        for entry in StateManager.get_history(name, limit=2000):
            if (status and entry.get("status") != status) or (since and entry.get("ts", "") < since):
                continue
            who = entry.get("user") or ""
            usage = entry.get("usage") or {}
            rows.append({
                "ts": entry.get("ts", ""), "job": name, "name": (loaded or {}).get("name") or name,
                "trigger": "scheduled" if who in _BY_ITSELF else "manual" if who else "", "user": who,
                "status": entry.get("status") or "", "duration_seconds": entry.get("duration_seconds"),
                "searches": int(usage.get("searches") or 0), "report_path": entry.get("output_path") or "",
                "error": entry.get("error") or "",
            })
    rows.sort(key=lambda r: r["ts"], reverse=order == "desc")
    page = rows[offset:offset + limit]
    made = _scripts_by_report() if any(r["report_path"] for r in page) else {}
    items = _items_by_report() if any(r["report_path"] for r in page) else {}
    for r in page:
        r["scripts"] = made.get((r["job"], r["report_path"]), 0) if r["report_path"] else 0
        r["items"] = items.get(r["report_path"], 0)  # 0: the report is no item list (or was never read into the topic pool)
    return {"runs": page, "total": len(rows), "more": offset + limit < len(rows)}


# --- The matrix's week --------------------------------------------------------------

#: the scheduler runs this many Jobs at once; more due in the same minute wait in a queue
CONCURRENT_RUNS = 2


@router.get("/{key}/calendar")
def matrix_calendar(key: str, user=Depends(require_viewer)):
    """The next 7 days of the matrix's timed runs: each member's runs, a list of all of them in time order, how many
    searches each day is expected to take, and the minutes when more Jobs are due than can run at once."""
    from collections import Counter, defaultdict
    from datetime import timedelta

    from web.routes.jobs import _state_for

    matrix = _get(key)
    _, jobs_dir = _dirs()
    plans = _plans()
    now = datetime.now().astimezone()
    end = now + timedelta(days=7)
    runs, per_job = [], {}
    for member in matrix.members:
        plan = plans.get(member.job)
        job = load_job(jobs_dir, member.job)
        if plan is None or job is None:
            continue
        name = job.get("name") or member.job
        state = _state_for(member.job)
        searches = int(((state.last_usage if state else None) or {}).get("searches") or 0)
        due = []
        if plan["enabled"]:
            entry = cron.parse_schedule(plan["schedule"])
            due = [t for t in cron.fire_times(entry, now, count=200, days=7) if t <= end]
        per_job[member.job] = {"name": name, "enabled": plan["enabled"], "schedule": plan["schedule"], "period": plan["period"],
                               "searches_per_run": searches,
                               "runs": [t.isoformat(timespec="minutes") for t in due]}
        runs += [{"ts": t.isoformat(timespec="minutes"), "job": member.job, "name": name, "searches": searches} for t in due]
    runs.sort(key=lambda r: r["ts"])
    days: dict[str, int] = defaultdict(int)
    for r in runs:
        days[r["ts"][:10]] += r["searches"]
    crowd = Counter(r["ts"] for r in runs)
    clashes = [{"ts": ts, "jobs": [r["name"] for r in runs if r["ts"] == ts]} for ts, n in sorted(crowd.items()) if n > CONCURRENT_RUNS]
    return {"now": now.isoformat(timespec="minutes"), "jobs": per_job, "runs": runs,
            "searches_by_day": dict(sorted(days.items())), "clashes": clashes, "concurrent_runs": CONCURRENT_RUNS}


# --- Pausing the whole matrix ---------------------------------------------------------


class ResumeIn(BaseModel):
    confirm: bool = False  # turning timed runs back on spends money again, so the page asks first


def _save_paused(matrix: matrices.Matrix, paused: list[str]) -> None:
    matrices.save(_dirs()[0], matrix.key, name=matrix.name, domain=matrix.domain, keywords=matrix.keywords,
                  members=matrix.members, paused=paused)


@router.post("/{key}/pause")
def pause_matrix(key: str, user=Depends(require_admin)):
    """Stop every member's timed runs. Each Job keeps its own period (resuming turns the same ones back on) and can
    still be run by hand. Runs missed while paused are not made up."""
    from web.routes.scheduler import _mutate
    from web.services import managed_scheduler as managed

    matrix = _get(key)
    stopped = list(matrix.paused)
    for member in matrix.members:
        plan = _plan_of(member.job)
        if plan and plan["enabled"]:
            _mutate(managed.store().toggle, plan["id"], False)
            if member.job not in stopped:
                stopped.append(member.job)
    _save_paused(matrix, stopped)
    audit.log("matrix_pause", user=user["username"], target=key, details={"jobs": stopped})
    return get_matrix(key, user)


@router.post("/{key}/resume")
def resume_matrix(key: str, body: ResumeIn, user=Depends(require_admin)):
    """Turn back on the timed runs that pausing stopped (the ones switched off by hand meanwhile stay as they are)."""
    from web.routes.scheduler import _mutate
    from web.services import managed_scheduler as managed

    matrix = _get(key)
    due = [(j, p) for j in matrix.paused for p in [_plan_of(j)] if p and not p["enabled"]]
    if due and not body.confirm:
        now = datetime.now().astimezone()
        runs = sum(periods.runs_per_month(p["schedule"], now) for _, p in due)
        searches = sum(periods.runs_per_month(p["schedule"], now) * _cost(p["schedule"], j)["searches_per_run"] for j, p in due)
        raise _error(409, "confirm_needed", (
            f"恢复后 {len(due)} 个 Job 会按各自的周期自己运行：每月约 {runs} 次运行"
            + (f"，按上次每次的搜索数约 {searches} 次搜索" if searches else "") + "。暂停期间错过的不补跑。确认后再恢复"))
    if due:
        _mutate(managed.ensure_online)
    for _, plan in due:
        _mutate(managed.store().toggle, plan["id"], True)
    _save_paused(matrix, [])
    audit.log("matrix_resume", user=user["username"], target=key, details={"jobs": [j for j, _ in due]})
    return get_matrix(key, user)
