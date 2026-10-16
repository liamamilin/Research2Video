"""Scoped evidence retrieval and report operations without a nested LLM."""
from typing import Literal
from urllib.parse import quote
from pydantic import Field
from . import Arguments, Tool
from web.routes import reports
from web.indexer import db, vectors


class ReportQuery(Arguments):
    action: Literal['list', 'search', 'read', 'stats', 'tags'] = 'list'
    query: str = Field(default='', max_length=200)
    path: str = ''
    paths: list[str] = Field(default_factory=list, max_length=100)
    since: str | None = None
    until: str | None = None
    job_name: str | None = None
    category: str | None = None
    round_date: str | None = None
    favorite: bool | None = None
    tag: str | None = None
    unread: bool | None = None
    page: int = Field(default=1, ge=1)
    limit: int = Field(default=10, ge=1, le=100)
    offset: int = Field(default=0, ge=0)
    characters: int = Field(default=12000, ge=100, le=24000)


def link(path):
    return '/reports/view?path=' + quote(path, safe='')


def freshness(path):
    parts = path.split('/')
    if len(parts) != 3 or parts[0] != 'practical_ai_intelligence':
        return {}
    from web.routes import pipeline
    from web.settings import get_settings
    from core.pipeline_docs import STAGE_FILE
    state = pipeline.pipeline.get_round_state(parts[1]) or pipeline._recorded_round(parts[1], get_settings()) or {}
    stages = state.get('stages') or []
    if isinstance(stages, dict):
        stages = list(stages.values())
    key = next((key for key, file in STAGE_FILE.items() if file == parts[2]), None)
    stage = next((stage for stage in stages if stage.get('key') == key), {})
    status = stage.get('status', '')
    stale = bool(status and status not in ('success', 'skipped', 'done'))
    return {'stage_status': status or None, 'stale': stale,
            'warning': '该阶段未完成本次运行，磁盘内容可能是先前产物，不能当作本次新结论' if stale else None}


def query(a, request, user):
    if a.action == 'stats':
        return reports.stats(user=user)
    if a.action == 'tags':
        return reports.list_tags(user=user)
    if a.action == 'read':
        import hashlib
        raw = reports.raw_report(a.path, user=user)
        content = raw.pop('content')
        end = a.offset + a.characters
        return {**raw, 'content': content[a.offset:end], 'offset': a.offset,
                'end_offset': min(end, len(content)),
                'document_version': hashlib.sha256(content.encode('utf-8')).hexdigest(),
                'next_offset': end if end < len(content) else None, 'complete': end >= len(content),
                'href': link(raw['path']), 'report_date': (raw.get('index') or {}).get('report_date'), **freshness(raw['path'])}
    since = reports._validate_date(a.since, 'since')
    until = reports._validate_date(a.until, 'until')
    if since and until and since > until:
        raise ValueError('开始日期必须不晚于结束日期')
    if a.round_date:
        reports._validate_date(a.round_date, 'round_date')
    filters = dict(category=a.category, job_name=a.job_name, favorite=a.favorite,
                   tag=a.tag, unread=a.unread, since=since, until=until, latest_round=False,
                   path_prefix=('practical_ai_intelligence/' + a.round_date + '/') if a.round_date else None,
                   paths=a.paths or None)
    # SQL determines indexed scope; filesystem checks concern returned items,
    # rather than every file in the library on every page.
    searching = a.action == 'search'
    scope = db.list_reports(page=a.page, per_page=1 if searching else a.limit,
                            scope_metadata=True, scope_paths=searching, **filters)
    total = scope['total']
    unavailable = []
    def valid(item):
        try:
            reports.resolve_output_file(item['path'], allowed_exts=('.md', '.mdx'))
        except Exception:
            unavailable.append(item['path'])
            return False
        return True
    if a.action == 'search':
        if not a.query:
            raise ValueError('搜索需要 query')
        from web.routes.qa import _embed_query, _retrieval_settings
        from core.config import load_system_config
        from web.settings import get_settings
        config = load_system_config(get_settings().paths.config_dir)
        vector = _embed_query(a.query, config)
        options = _retrieval_settings(config)
        options['round_scope'] = 0
        allowed = set(scope['scope_paths'])
        while True:
            hits = vectors.hybrid_search(a.query, vector or None, limit=a.limit, allowed_paths=allowed, **options)
            available = [hit for hit in hits if valid(hit)]
            rejected = {hit['path'] for hit in hits} - {hit['path'] for hit in available}
            if not rejected:
                break
            allowed -= rejected
        data = [{**h, 'href': link(h['path']), **freshness(h['path'])} for h in available]
    else:
        data = [{**i, 'href': link(i['path']), **freshness(i['path'])} for i in scope['items'] if valid(i)]
    return {'items': data, 'total_in_scope': total, 'page': a.page, 'limit': a.limit,
            'scope': {'since': since, 'until': until, 'job_name': a.job_name, 'round_date': a.round_date,
                      'paths': a.paths, 'latest_round': False},
            'coverage': '仅返回相关片段，未穷尽原文' if a.action == 'search' else '分页目录；需要读取原文后分析',
            'dates_available': scope['dates_available'], 'unavailable': sorted(set(unavailable)),
            'scope_basis': '数量和日期来自索引目录；仅当前返回文件已核验可读，索引条目不等于已读取或文件仍存在',
            'has_more': a.page * a.limit < total if not searching else None,
            'mode': ('hybrid' if vector else 'fts') if a.action == 'search' else 'list'}


class ReportChange(Arguments):
    path: str
    favorite: bool | None = None
    read: bool | None = None
    tags: list[str] | None = None
    rating: int | None = Field(default=None, ge=0, le=5)
    rating_note: str | None = None


def change(a, request, user):
    result = reports.update_meta(a.model_dump(exclude_none=True), request, user=user)
    return {**result, 'href': link(a.path)}


class Distribution(Arguments):
    action: Literal['export', 'share', 'shares', 'revoke', 'email']
    path: str = ''
    jti: str = ''
    ttl_hours: int = Field(default=168, ge=1, le=8760)
    recipients: list[str] = Field(default_factory=list)


def distribute(a, request, user):
    from web.deps import require_editor
    if a.action == 'export':
        reports.resolve_output_file(a.path, allowed_exts=('.md', '.mdx'))
        return {'path': a.path, 'download_url': '/api/reports/html?path=' + quote(a.path, safe=''), 'href': link(a.path)}
    require_editor(request, user=user)
    if a.action == 'shares':
        return reports.list_shares(include_expired=True, user=user)
    if a.action == 'revoke':
        return reports.revoke_share(a.jti, request, user=user)
    if a.action == 'share':
        return reports.create_share({'path': a.path, 'ttl_hours': a.ttl_hours}, request, user=user)
    if not a.recipients or any('@' not in address or '\n' in address or '\r' in address for address in a.recipients):
        raise ValueError('发送邮件需要明确且有效的收件地址')
    return reports.email_report({'path': a.path, 'to': a.recipients}, request, user=user)


TOOLS = {
    'query_reports': Tool(ReportQuery, '报告目录、按明确日期/Job/轮次/paths筛选、混合search、read原文分页、stats/tags。历史比较先确定范围，搜索片段不代表全部历史；read按next_offset展开。引用返回href和日期，不调用另一套问答模型。', query, label='查询报告'),
    'update_report': Tool(ReportChange, '按明确要求修改报告收藏/已读/标签/评分，仅修改提供字段。', change, 'editor', True, '更新报告'),
    'distribute_report': Tool(Distribution, '导出HTML下载、分享/查分享/撤销、向明确recipients发送指定报告。单纯分析不隐含外发授权；先明确内容与受众。', distribute, 'viewer', True, '报告分发'),
}
