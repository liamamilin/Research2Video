"""Matrices: a set of research Jobs sharing a domain and keywords, listed, edited and copied (spec/matrices.md).

A matrix only groups Jobs. Running, pausing and scheduling a member stay on the Jobs and schedule pages; this page
shows them. Copying a matrix with new keywords makes new Job files from each member's ``copy_as`` template: they
start manual, so a copy never runs or spends anything until someone starts it.
"""

from __future__ import annotations

import os
import re
import string

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from core import matrices
from core.config import list_jobs, load_job
from web import audit
from web.deps import require_editor, require_viewer
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
            out[plan["job_name"]] = {"schedule": plan["schedule"], "timezone": plan["timezone"],
                                     "enabled": shown["enabled"], "next_runs": shown["next_runs"][:1]}
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
        "schedule": plans.get(member.job),
    }


def _shown(matrix: matrices.Matrix, jobs_dir: str, plans: dict) -> dict:
    return {**matrix.as_dict(), "members": [_member(jobs_dir, m, plans) for m in matrix.members]}


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
    return _shown(_get(key), _dirs()[1], _plans())


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
                  keywords=_keywords(payload.keywords), members=members)
    audit.log("matrix_update", user=user["username"], target=key)
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
