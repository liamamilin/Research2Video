"""Recurring plans, source-specific runs, templates and job cloning."""
from pathlib import Path
from datetime import datetime
from typing import Literal
from urllib.parse import quote
from pydantic import Field
from . import Arguments, Tool
from web.routes import jobs, scheduler
from web.services import managed_scheduler as managed
from web.settings import get_settings


class ScheduleQuery(Arguments):
    action: Literal['list', 'get', 'preview', 'runs'] = 'list'
    schedule_id: str = ''
    schedule: str = ''
    timezone: str = 'local'


class ScheduleChange(Arguments):
    expected_version: int | None = None
    action: Literal['create', 'update', 'pause', 'resume', 'delete', 'restore', 'start_dispatcher']
    schedule_id: str = ''
    job_name: str = ''
    schedule: str = ''
    timezone: str | None = None
    missed_policy: Literal['latest', 'skip'] | None = None
    max_retries: int | None = Field(default=None, ge=0, le=5)


def query(a, request, user):
    if a.action == 'preview':
        return scheduler.preview_schedule(a.schedule, a.timezone, user=user)
    if a.action == 'runs':
        return scheduler.execution_history(a.schedule_id, user=user)
    data = scheduler.list_scheduled(user=user)
    if a.action == 'list':
        return data
    selected = next((j for j in data['jobs'] if j['id'] == a.schedule_id), None)
    if not selected:
        raise ValueError('计划不存在，请先查询计划列表')
    return {**selected, 'dispatcher': data.get('dispatcher'),
            'health': next((item for item in data.get('health', {}).get('jobs', []) if item['id'] == a.schedule_id), None)}


def change(a, request, user):
    if a.action == 'start_dispatcher':
        return scheduler.start_dispatcher(user=user)
    if a.action == 'create':
        from web.services.jobs_assistant import _detail
        _detail(a.job_name, user)
        result = scheduler.add_scheduled(scheduler.ScheduleCreate(
            id=a.schedule_id, job_name=a.job_name, schedule=a.schedule,
            timezone=a.timezone or 'local', missed_policy=a.missed_policy or 'latest',
            max_retries=2 if a.max_retries is None else a.max_retries), user=user)
    elif a.action == 'update':
        current = query(ScheduleQuery(action='get', schedule_id=a.schedule_id), request, user)
        # Keep an existing legacy command; the model can never supply shell code.
        if current.get('backend') != 'managed' and a.timezone is not None:
            raise ValueError('旧系统计划使用系统时区，不能单独改变时区')
        if current.get('backend') != 'managed' and (a.missed_policy is not None or a.max_retries is not None):
            raise ValueError('旧系统计划不支持托管补跑或重试策略，请只修改执行时间')
        result = scheduler.edit_scheduled(a.schedule_id, scheduler.ScheduleEdit(
            preserve_command=current.get('backend') != 'managed',
            expected_version=a.expected_version if a.expected_version is not None else current.get('version'),
            schedule=a.schedule or current['schedule'], command=current.get('command', ''),
            timezone=a.timezone or current.get('timezone', 'local'),
            missed_policy=a.missed_policy or current.get('missed_policy', 'latest'),
            max_retries=current.get('max_retries', 2) if a.max_retries is None else a.max_retries), user=user)
    elif a.action in ('pause', 'resume'):
        result = scheduler.toggle_scheduled(a.schedule_id, scheduler.ScheduleToggle(enabled=a.action == 'resume'), user=user)
    elif a.action == 'restore':
        result = scheduler.restore_scheduled(a.schedule_id, user=user)
    else:
        result = scheduler.remove_scheduled(a.schedule_id, user=user)
    if a.action not in ('delete', 'restore'):
        result = {**result, 'plan': query(ScheduleQuery(action='get', schedule_id=result.get('id', a.schedule_id)), request, user)}
    result['server_now'] = datetime.now().astimezone().isoformat(timespec='seconds')
    if a.action == 'resume' and result.get('plan', {}).get('backend') == 'managed':
        result['effect'] = '已恢复，从 server_now 之后的下一次计划时间开始；暂停期间不补跑。latest 仅适用于启用期间离线错过的运行。'
    elif a.action == 'pause':
        result['effect'] = '已暂停后续周期；正在执行的一次运行保持原状，需单独取消。'
    schedule_id = result.get('id', a.schedule_id)
    return {**result, 'schedule_id': schedule_id, 'href': '/scheduler?plan=' + quote(schedule_id, safe='')}


class RunQuery(Arguments):
    source: Literal['manual', 'scheduled']
    job_name: str = ''
    run_id: str = ''
    limit: int = Field(default=100, ge=1, le=500)


def run_query(a, request, user):
    from web.services.assistant_artifacts import report_reference
    if a.source == 'manual':
        from web.services.jobs_assistant import _detail
        _detail(a.job_name, user)
        data = jobs.get_job_logs(a.job_name, request, run_id=a.run_id or None, limit=a.limit, user=user)
        task = jobs._registry.get(a.job_name)
        return {'source': 'manual', 'job_name': a.job_name, **data,
                'report': report_reference((data.get('run') or {}).get('output_path')),
                'current': {'id': task.task_id, 'status': task.status} if task else None,
                'href': '/jobs/' + quote(a.job_name, safe='')}
    from web.deps import require_admin
    require_admin(request, user=user)
    run = managed.store().run(a.run_id)
    if not run:
        raise ValueError('周期运行不存在')
    path = Path(get_settings().paths.logs_dir) / 'schedules' / f'{run["id"]}.log'
    # Resolve the ID through the DB before reading any file; only a bounded tail.
    text = ''
    if path.is_file():
        with path.open('rb') as stream:
            stream.seek(max(0, path.stat().st_size - 48000))
            text = stream.read().decode('utf-8', errors='replace')
    return {'source': 'scheduled', 'run': run, 'report': report_reference(run.get('output_path')),
            'logs': '\n'.join(text.splitlines()[-a.limit:]), 'href': '/scheduler?plan=' + quote(run['schedule_id'], safe='')}


def cancel_run(a, request, user):
    if not a.run_id:
        raise ValueError('取消运行需要具体 run_id；请先读取运行状态')
    if a.source == 'scheduled':
        from web.deps import require_admin
        require_admin(request, user=user)
        return scheduler.cancel_execution(a.run_id, user=user)
    task = jobs._registry.get(a.job_name)
    if not task or task.task_id != a.run_id:
        raise ValueError('所选运行已结束或已被新运行替换，请重新查询')
    return jobs.cancel_job(a.job_name, request, user=user)


class TemplateQuery(Arguments):
    key: str = ''


class OperationQuery(Arguments):
    operation_id: str


class BackgroundQuery(Arguments):
    task_id: str


def background(a, request, user):
    from web.services.assistant_background import read
    return read(a.task_id, user)


def operation(a, request, user):
    from web.services.assistant_store import AssistantStore
    return AssistantStore().receipt(a.operation_id, user)


class TemplateChange(Arguments):
    action: Literal['create', 'update', 'delete']
    key: str
    fields: dict = Field(default_factory=dict)


def templates(a, request, user):
    data = jobs.list_templates(user=user)
    if not a.key:
        return data
    jobs._require_key(a.key, allow_builtin=True)
    return jobs._read_template(str(Path(get_settings().paths.jobs_dir) / '_templates'), a.key)


def template_change(a, request, user):
    if a.action == 'delete':
        return jobs.delete_template(a.key, user=user)
    payload = {**a.fields, 'key': a.key}
    return (jobs.create_template(payload, user=user) if a.action == 'create'
            else jobs.update_template(a.key, payload, user=user))


class Clone(Arguments):
    job_name: str
    name: str
    category: str = 'research'


def clone(a, request, user):
    from web.services.jobs_assistant import _detail
    from web.services import yaml_io
    original = _detail(a.job_name, user)
    data = yaml_io.parse_yaml(original.yaml_content)
    # Publish a fully validated copy; use the shared template create path, then
    # restore advanced fields through the versioned Job update path.
    payload = {k: data[k] for k in ('prompt', 'description', 'keywords', 'language') if k in data}
    created = jobs.create_job({**payload, 'name': a.name, 'category': a.category}, user=user)
    from web.models import JobYamlUpdate
    detail = jobs.get_one(created.name, user=user)
    copy = {**data, 'name': a.name, 'output': f'output/{created.name.replace("/", "_")}_{{date}}.md'}
    update = JobYamlUpdate(yaml_content=yaml_io.dump_yaml(copy), expected_mtime=detail.yaml_mtime)
    try:
        checked = jobs.validate_yaml(created.name, update, user=user)
        if not checked['ok']:
            raise ValueError('高级字段验证失败：' + str(checked['errors']))
        jobs.update_yaml(created.name, update, user=user)
    except Exception as exc:
        from web.services.jobs_assistant import _error_message
        return {'ok': False, 'partial': True, 'job_name': created.name,
                'message': f'已创建基础任务 {created.name}，复制高级字段未完成；请先核验当前任务。' + _error_message(exc),
                'href': '/jobs/' + quote(created.name, safe='')}
    return {'job_name': created.name, 'source_job': a.job_name}


TOOLS = {
    'query_background': Tool(BackgroundQuery, '查询本人后台索引操作的进度和失败项，使用原task_id；服务中断的操作不会自动重放。', background, label='查询后台操作'),
    'query_operation': Tool(OperationQuery, '查询本人的某次操作回执。started 表示结果待核验，必须重新查询实际业务状态，不能自动重放。', operation, label='核验操作回执'),
    'query_schedules': Tool(ScheduleQuery, '查询计划列表/get详情/preview五字段cron时区及未来三次时间/runs执行历史。使用真实schedule_id；当前仅管理员可用。', query, 'admin', label='查询周期计划'),
    'manage_schedule': Tool(ScheduleChange, '按明确请求创建/改期/暂停/恢复/删除计划，或恢复系统计划、启动执行器。先查询真实ID；不接受shell命令。自然语言时间先转换五字段cron并preview，明确时区；不能无损表达的规则先说明。暂停不取消正在运行的一次任务。恢复只从下次未来时间开始，不补跑暂停期间。', change, 'admin', True, '管理周期计划'),
    'query_run': Tool(RunQuery, '读取一次运行的状态、日志和产物。manual使用job_name+可选run_id；scheduled必须具体run_id且需管理员。', run_query, label='查询运行'),
    'cancel_run': Tool(RunQuery, '取消具体一次运行，必须source+run_id；不暂停未来周期。周期运行取消不会自动重试。', cancel_run, 'editor', True, '取消一次运行'),
    'query_templates': Tool(TemplateQuery, '列出Job模板，key非空时读取模板详情。', templates, label='读取任务模板'),
    'manage_template': Tool(TemplateChange, '创建/修改/删除自定义任务模板；fields使用现有模板结构，修改前读取详情和版本。', template_change, 'editor', True, '管理任务模板'),
    'clone_job': Tool(Clone, '复制一个真实Job为新任务，保留研究配置，生成独立输出路径，不复制周期计划。', clone, 'editor', True, '复制 Job'),
}
