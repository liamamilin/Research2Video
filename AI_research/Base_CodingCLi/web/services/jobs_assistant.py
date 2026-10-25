"""Small tool harness over the console's existing Jobs operations."""

from __future__ import annotations

import json
import logging
from typing import Literal
from urllib.parse import quote

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from core.config import list_jobs, load_job
from web.deps import require_editor
from web.models import JobYamlUpdate
from web.routes import jobs
from web.services import yaml_io
from web.settings import get_settings

logger = logging.getLogger(__name__)

class Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)


class ListJobs(Arguments):
    query: str = ""


class JobName(Arguments):
    job_name: str = Field(min_length=1)


class Recurrence(Arguments):
    schedule: str
    timezone: str = "local"
    missed_policy: str = "latest"
    max_retries: int = Field(default=2, ge=0, le=5)


class SourceReference(Arguments):
    type: Literal['report', 'action', 'round']
    id: str = Field(min_length=1)


class CreateJob(Arguments):
    name: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    category: str = "research"
    description: str = ""
    keywords: list[str] = Field(default_factory=list)
    language: str = "zh"
    output: str = ""
    template: str = ""
    display_name: str = ''
    sources: list[str] | None = None
    recurrence: Recurrence | None = None
    source_references: list[SourceReference] = Field(default_factory=list)


class JobChanges(Arguments):
    name: str | None = Field(default=None, min_length=1)
    description: str | None = None
    prompt: str | None = Field(default=None, min_length=1)
    keywords: list[str] | None = None
    language: str | None = Field(default=None, min_length=1)
    enabled: bool | None = None
    output: str | None = None
    sources: list[str | dict] | dict | None = None
    timeout_seconds: int | None = Field(default=None, ge=1)


class UpdateJob(JobName):
    expected_mtime: float
    changes: JobChanges


TOOLS = {
    "list_jobs": (ListJobs, "查询 Jobs（含完整 job_name、名称、运行状态），query 可按名称或描述筛选。"),
    "get_job": (JobName, "读取一个 Job 的配置和 expected_mtime。修改前必须读取。"),
    "create_job": (CreateJob, "按研究目标创建 Job，可指定 template；明确要求周期时使用 recurrence 一起创建（需管理员）。不会自动运行。"),
    "update_job": (UpdateJob, "修改 Job 的指定字段；expected_mtime 使用 get_job 返回值。name 是显示名称，不移动文件。"),
    "delete_job": (JobName, "按用户明确要求删除 Job 及其关联周期计划，保留历史报告；运行中的任务不能删除。"),
    "run_job": (JobName, "启动 Job，在后台执行；返回 task_id，不等待研究完成。"),
    "cancel_job": (JobName, "取消正在运行的 Job。"),
    "job_history": (JobName, "查询 Job 最近的执行记录。"),
}
READ_TOOLS = {"list_jobs", "get_job", "job_history"}
LABELS = {
    "list_jobs": "查询 Jobs", "get_job": "读取 Job", "create_job": "创建 Job",
    "update_job": "修改 Job", "delete_job": "删除 Job", "run_job": "运行 Job",
    "cancel_job": "取消 Job", "job_history": "查看运行历史",
}


def tool_definitions(role: str) -> list[dict]:
    """Expose only operations permitted for this user."""
    return [
        {"type": "function", "function": {
            "name": name, "description": description,
            "parameters": model.model_json_schema(),
        }}
        for name, (model, description) in TOOLS.items()
        if role in ("editor", "admin") or name in READ_TOOLS
    ]


def _detail(name: str, user: dict):
    # Core's legacy basename lookup picks the first match. Require canonical IDs
    # here so an ambiguous short name never mutates an unrelated job.
    if name not in list_jobs(get_settings().paths.jobs_dir, enabled_only=False):
        raise ValueError("未找到完整 job_name，请先用 list_jobs 查询，勿使用简称")
    return jobs.get_one(name, user=user)


def execute_tool(name: str, arguments: dict, request: Request, user: dict) -> dict:
    """Validate a tool call and reuse the same Jobs handlers as the normal UI."""
    if name not in TOOLS:
        raise ValueError(f"未知工具：{name}")
    if name not in READ_TOOLS:
        # Direct handler calls do not run FastAPI dependencies. Enforce the
        # existing RBAC + CSRF check explicitly, even for a forged model call.
        require_editor(request, user=user)
    args = TOOLS[name][0].model_validate(arguments)
    from web.services.assistant_artifacts import report_reference
    if name == "list_jobs":
        items = []
        for item in jobs.list_all(user=user):
            data = item.model_dump(mode="json")
            config = load_job(get_settings().paths.jobs_dir, item.name) or {}
            data["title"] = config.get("name") or item.name
            if not args.query or args.query.casefold() in json.dumps(data, ensure_ascii=False).casefold():
                items.append(data)
        return {"items": items, "total": len(items)}
    if name == "create_job":
        payload = args.model_dump(exclude_none=True)
        if 'keywords' not in args.model_fields_set:
            payload.pop('keywords', None)
        result = jobs.create_job(payload, user=user)
        if isinstance(result, BaseModel):
            response = {"job_name": result.name, "job": result.model_dump(mode="json"), 'href': '/jobs/' + quote(result.name, safe='')}
            if args.recurrence:
                from web.services import managed_scheduler as managed
                plan = next(p for p in managed.store().list() if p['job_name'] == result.name)
                response.update(schedule_id=plan['id'], plan=managed.describe(plan),
                                schedule_href='/scheduler?plan=' + quote(plan['id'], safe=''))
                response['plan']['dispatcher'] = managed.store().health()
            return response
        return result

    detail = _detail(args.job_name, user)
    canonical = detail.name
    if name == "get_job":
        data = yaml_io.parse_yaml(detail.yaml_content)
        return {"job_name": canonical, "href": '/jobs/' + quote(canonical, safe=''), "expected_mtime": detail.yaml_mtime,
                "config": {k: data.get(k) for k in (
                    "name", "description", "prompt", "keywords", "language", "enabled", "output", "sources", "source_references")},
                "timeout_seconds": detail.timeout_seconds,
                "is_running": detail.is_running,
                "report": report_reference(detail.state.last_output) if detail.state else None,
                "state": detail.state.model_dump(mode="json") if detail.state else None}
    if name == "update_job":
        changes = args.changes.model_dump(exclude_none=True)
        if not changes:
            raise ValueError("changes 至少包含一个要修改的字段")
        data = yaml_io.parse_yaml(detail.yaml_content)
        timeout = changes.pop("timeout_seconds", None)
        data.update(changes)
        if timeout is not None:
            data["runtime"] = {**(data.get("runtime") or {}), "timeout_seconds": timeout}
        payload = JobYamlUpdate(yaml_content=yaml_io.dump_yaml(data), expected_mtime=args.expected_mtime)
        validation = jobs.validate_yaml(canonical, payload, user=user)
        if not validation["ok"]:
            raise ValueError("；".join(validation["errors"]))
        result = jobs.update_yaml(canonical, payload, user=user)
    elif name == "delete_job":
        known = [record.get('output_path') for record in jobs.get_history(canonical, limit=10, user=user)]
        if detail.state:
            known.append(detail.state.last_output)
        preserved = [report_reference(path) for path in dict.fromkeys(known) if path]
        result = jobs.delete_job(canonical, request, user=user)
        result.update(reports_preserved=True, verified_reports=[report for report in preserved if report],
                      report_scope='仅列出执行记录中可核验的文件；删除不清理任何历史报告文件')
    elif name == "run_job":
        result = jobs.run_job(canonical, request, user=user)
    elif name == "cancel_job":
        result = jobs.cancel_job(canonical, request, user=user)
    else:
        result = {"history": jobs.get_history(canonical, limit=10, user=user)}
    return {"job_name": canonical, **result}


def _error_message(exc: Exception) -> str:
    if isinstance(exc, HTTPException):
        detail = exc.detail
        if isinstance(detail, dict):
            return str(detail.get("error", {}).get("message") or detail)
        return str(detail)
    if isinstance(exc, ValidationError):
        return "工具参数无效：" + "; ".join(
            f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
    if isinstance(exc, ValueError):
        return str(exc)
    logger.exception("Assistant tool failed")
    return "操作失败，请查看服务日志；请先查询实际业务状态再重试"


def run(message: str, history: list[dict], llm, request: Request, user: dict,
        on_event=None, cancel_token=None, **kwargs) -> dict:
    """Compatibility entry point for the application-wide runtime."""
    from web.services.assistant_runtime import run as application_run
    return application_run(message, history, llm, request, user,
                           on_event=on_event, cancel_token=cancel_token, **kwargs)
