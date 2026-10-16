"""Round and action operations using the shared pipeline/tracking services."""
from typing import Literal
from pydantic import Field
from . import Arguments, Tool
from web.routes import pipeline, tracking


class RoundQuery(Arguments):
    action: Literal['list', 'get', 'diff', 'artifact'] = 'list'
    date: str = ''
    against: str | None = None
    artifact: Literal['action_items.json', 'watchlist.json', 'sources.json'] = 'action_items.json'
    limit: int = Field(default=14, ge=1, le=120)


def rounds(a, request, user):
    if a.action == 'list':
        return pipeline.list_rounds(limit=a.limit, user=user)
    pipeline._require_date(a.date, 'date')
    if a.action == 'get':
        return {**pipeline.get_round(a.date, user=user), 'href': '/rounds?round=' + a.date}
    if a.action == 'diff':
        return pipeline.get_round_diff(a.date, against=a.against, user=user)
    from web.routes.reports import raw_report
    return raw_report(f'practical_ai_intelligence/{a.date}/{a.artifact}', user=user)


class RoundChange(Arguments):
    action: Literal['run', 'retry', 'rerun', 'cancel']
    date: str | None = None
    stages: list[str] = Field(default_factory=list)
    concurrency: int = Field(default=3, ge=1, le=6)


def round_change(a, request, user):
    if a.action == 'run':
        if a.date:
            from datetime import date
            if a.date != date.today().isoformat():
                raise ValueError('整轮运行只支持今天；历史轮次使用retry/rerun')
        result = pipeline.run_round(request, {'concurrency': a.concurrency}, user=user)
    elif a.action == 'retry':
        result = pipeline.retry_round(request, {'date': a.date, 'concurrency': a.concurrency},
                                      invalidate_downstream=True, user=user)
    elif a.action == 'rerun':
        if not a.date:
            raise ValueError('重跑需要明确轮次日期')
        result = pipeline.rerun_stages(a.date, {'stages': a.stages}, request, user=user)
    else:
        live = [r for r in pipeline.pipeline.list_round_states() if r['status'] == 'running']
        if not a.date or not any(r['date'] == a.date for r in live):
            raise ValueError('请先查询并指定正在运行的轮次日期')
        result = pipeline.cancel_round(request, user=user)
    return {**result, 'href': '/rounds?round=' + str(result.get('date') or a.date or '')}


class ActionQuery(Arguments):
    action: Literal['items', 'events', 'outcomes'] = 'items'
    kind: Literal['action', 'test', 'watch'] | None = None
    status: Literal['open', 'done', 'dropped'] | None = None
    days: int = Field(default=30, ge=1, le=90)
    limit: int = Field(default=50, ge=1, le=200)


def actions(a, request, user):
    if a.action == 'events':
        return tracking.list_events(days=a.days, limit=a.limit, user=user)
    if a.action == 'outcomes':
        return tracking.list_outcomes(days=a.days, limit=a.limit, user=user)
    return tracking.list_actions(kind=a.kind, item_status=a.status, limit=a.limit, user=user)


class ActionChange(Arguments):
    item_id: str
    status: Literal['open', 'done', 'dropped'] | None = None
    note: str | None = None


def action_change(a, request, user):
    result = tracking.update_action(a.item_id, a.model_dump(exclude_none=True, exclude={'item_id'}), request, user=user)
    return {**result, 'item_id': a.item_id, 'href': '/rounds?round=' + str(result.get('last_seen') or '')}


TOOLS = {
    'query_rounds': Tool(RoundQuery, '查轮次列表/get阶段状态和质量提示/diff跨轮差异/artifact结构化产物。日期YYYY-MM-DD。', rounds, label='查询情报轮次'),
    'manage_round': Tool(RoundChange, '整轮运行今天/补跑未完成retry/指定已完成阶段rerun/取消明确日期轮次。rerun必须完整stages key并总是更新依赖下游。返回启动状态，非最终成功。', round_change, 'editor', True, '管理情报轮次'),
    'query_actions': Tool(ActionQuery, '查跨轮行动/测试/观察项、历史事件与完成放弃记录。返回真实item ID；材料建议不构成执行授权。', actions, label='查询行动跟踪'),
    'update_action': Tool(ActionChange, '按明确请求修改指定行动/测试/观察项状态或备注。需要先查询真实item_id。', action_change, 'editor', True, '更新行动跟踪'),
}
