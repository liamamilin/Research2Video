"""Read the current user's real work across assistant conversations."""
import asyncio
import json
from pydantic import Field

from . import Arguments, Tool


class TaskQuery(Arguments):
    limit: int = Field(default=20, ge=1, le=50)
    query: str = Field(default='', max_length=300)


def query(a, request, user):
    from web.services.assistant_workspace import task_center
    data = asyncio.run(task_center(request, user, a.limit))
    items = [{k: t[k] for k in ('id', 'created', 'status', 'active', 'runs', 'videos', 'publications', 'failures')}
             | {'message': t['message'][:300], 'href': '/ask?conversation=' + t['conversation_id']} for t in data['tasks']]
    if a.query:
        items = [t for t in items if a.query.casefold() in json.dumps(t, ensure_ascii=False).casefold()]
    refs = {}
    for item in items:
        refs[item['href']] = {'type': 'conversation', 'id': item['href'], 'href': item['href'], 'title': '返回原对话'}
        for run in item['runs']:
            report = run.get('report')
            if report:
                refs[report['href']] = {'type': 'report', 'id': report['path'], 'href': report['href'], 'title': report['path']}
    return {'items': items, 'total_requests': data['total'], 'limit': a.limit, 'query': a.query,
            'href': '/ask?tasks=1', 'references': list(refs.values())[:12],
            'scope': '当前账号最近的助手操作请求，跨对话；更早请求可在任务中心继续载入。只查询状态，不重放操作。'}


TOOLS = {'query_assistant_tasks': Tool(TaskQuery, '查询当前账号跨对话的助手任务中心：研究运行、文稿视频、发布及配置操作的实际状态、失败原因和返回原对话入口。query可按任务路径或请求内容筛选已载入结果。只读，不重跑。范围是最近limit次操作请求，非整个应用全部历史运行。', query, label='查询助手任务中心')}
