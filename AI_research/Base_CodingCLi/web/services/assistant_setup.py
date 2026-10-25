"""One business operation to connect matrix, sources, job and recipe."""
from __future__ import annotations

from web.routes import sources as source_routes
from web.services import assistant_management as management, jobs_assistant
from web.services.assistant_tools.management import MatrixChange, ProductionChange
from web.services.source_registry import snapshot
from web.settings import get_settings
from core import matrices
from core.config import load_job


def configure(a, request, user):
    paths = get_settings().paths
    receipts = []
    owner = matrices.of_job(a.job_name, paths.config_dir)
    if owner and owner.key != a.matrix_key:
        raise ValueError('该研究任务属于其他矩阵，请明确移动或创建新任务：' + owner.key)
    def record(label, result):
        receipts.append({'label': label, **{k: result[k] for k in ('message', 'href', 'job_name', 'id', 'revision') if k in result}})
        return result
    try:
        for entry in a.registered_sources:
            data = snapshot()
            fields = entry.model_dump(exclude={'id'}, exclude_none=True)
            if entry.id in data['sources']:
                fields = {k: v for k, v in fields.items() if data['sources'][entry.id].get(k) != v}
                if not fields:
                    continue
                body = source_routes.SourceChange(expected_revision=data['revision'], fields=fields)
                record('更新信息源', source_routes.update_source(entry.id, body, user=user))
            else:
                body = source_routes.SourceCreate(id=entry.id, expected_revision=data['revision'], fields=fields)
                record('登记信息源', source_routes.create_source(body, user=user))
        every = matrices.load_all(paths.config_dir)
        if a.matrix_key not in every:
            if not a.matrix_name:
                raise ValueError('新矩阵需要 matrix_name')
            record('创建矩阵', management.matrix_change(MatrixChange(action='create', key=a.matrix_key,
                fields={'name': a.matrix_name, 'domain': a.domain or '', 'render': a.render or {}}), request, user))
        elif a.render is not None or a.matrix_name is not None or a.domain is not None:
            fields = {k: v for k, v in {'name': a.matrix_name, 'domain': a.domain, 'render': a.render}.items() if v is not None}
            record('调整矩阵', management.matrix_change(MatrixChange(action='update', key=a.matrix_key,
                expected_revision=management.matrix_revision(a.matrix_key), fields=fields), request, user))
        existing = load_job(paths.jobs_dir, a.job_name)
        if existing is None:
            category, _, stem = a.job_name.rpartition('/')
            fields = {k: v for k, v in {'name': stem or a.job_name, 'category': category or 'research', 'prompt': a.prompt,
                'display_name': a.title or stem or a.job_name, 'output': a.output or '', 'sources': a.sources, 'keywords': a.keywords or []}.items() if v is not None}
            if not a.prompt:
                raise ValueError('创建研究任务需要 prompt')
            created = record('创建研究任务', jobs_assistant.execute_tool('create_job', fields, request, user))
            if created['job_name'] != a.job_name:
                raise ValueError('任务名被规范化为 ' + created['job_name'] + '，请使用此名称继续')
        else:
            changes = {k: v for k, v in {'name': a.title, 'prompt': a.prompt, 'output': a.output, 'sources': a.sources, 'keywords': a.keywords}.items() if v is not None}
            if changes:
                detail = jobs_assistant._detail(a.job_name, user)
                record('调整研究任务', jobs_assistant.execute_tool('update_job', {'job_name': a.job_name,
                    'expected_mtime': detail.yaml_mtime, 'changes': changes}, request, user))
        current = management.matrix_detail(a.matrix_key, user)
        if a.job_name not in [m['job'] for m in current['members']]:
            record('加入矩阵', management.matrix_change(MatrixChange(action='add_jobs', key=a.matrix_key,
                expected_revision=current['revision'], fields={'jobs': [a.job_name]}), request, user))
        current = management.matrix_detail(a.matrix_key, user)
        matches = [p for p in current['production'] if p['job'] == a.job_name and p['recipe'] == a.recipe]
        if not matches:
            record('关联配方', management.configure_production(ProductionChange(job=a.job_name, recipe=a.recipe, auto=0), request, user))
            current = management.matrix_detail(a.matrix_key, user)
            matches = [p for p in current['production'] if p['job'] == a.job_name and p['recipe'] == a.recipe]
        return {'ok': True, 'message': '矩阵、信息源、研究任务和配方已配置；尚未运行研究或发布',
                'matrix': a.matrix_key, 'job_name': a.job_name, 'production_keys': [p['key'] for p in matches],
                'receipts': receipts, 'href': current['href'], 'references': current['references']}
    except Exception as exc:
        return {'ok': False, 'message': '配置尚未全部完成；已保存的步骤保留', 'error': jobs_assistant._error_message(exc),
                'receipts': receipts, 'href': '/matrices/' + a.matrix_key}
