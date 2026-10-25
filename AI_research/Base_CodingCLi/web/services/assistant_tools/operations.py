"""Whole-application coverage, source management and durable batch operations."""
from typing import Literal

from pydantic import Field

from . import Arguments, Tool
from web.routes.assistant_batches import BatchSpec, BatchAction
from web.routes.sources import SourceFields
from web.services import assistant_application as app, assistant_batches
from web.services.assistant_batch_store import BatchStore, view


class Capabilities(Arguments):
    pass


class MatrixOperations(Arguments):
    key: str
    platforms: list[str] = Field(default_factory=list)
    limit: int = Field(default=5, ge=1, le=10)


def inspect_matrix(a, request, user):
    from web.services.assistant_matrix_ops import inspect
    return inspect(a, request, user)


CAPABILITIES = [
    {'key': 'matrices', 'title': '矩阵与任务', 'detail': '创建矩阵、管理成员、研究任务与配方关联、共用配置和任务覆盖。', 'example': '查看各矩阵的研究任务、配方和周期，找出没有配齐的部分。'},
    {'key': 'sources', 'title': '信息源', 'detail': '查看、登记、修改、删除共享来源，检查直接引用和标签引用；任务 sources 使用原任务编辑。', 'example': '查看最近抓取失败的信息源，以及哪些研究任务受影响。'},
    {'key': 'research', 'title': '研究与报告', 'detail': '启动或取消准确一次研究，检索、比较、读取和分发报告，跟踪情报轮次。', 'example': '查看今天哪些研究没有完成，先分析原因。'},
    {'key': 'production', 'title': '文稿与视频', 'detail': '调整配方、公共提示词、继承设置，编辑或重跑文稿、制作视频、比较版本。', 'example': '查看当前文稿与视频对应的版本，以及生成配置。'},
    {'key': 'batches', 'title': '跨阶段与批量操作', 'detail': '固定范围，批量启停周期、调整矩阵、研究后自动制作视频、逐条投稿；保存逐项结果。', 'example': '把 AI 矩阵的生产配置列出来，准备运行研究并制作视频，先不执行。'},
    {'key': 'platforms', 'title': '发布与运营', 'detail': '查询账号、准备发布包、投稿及恢复；B站数据同步、视频数据、评论读取与回复通过应用操作目录。', 'example': '查看 B 站发布情况和可读取的视频数据，只分析。'},
    {'key': 'administration', 'title': '系统管理', 'detail': '模型和配置、用量、诊断、用户、索引、通知与服务维护，按当前账号权限执行。', 'example': '检查当前模型、用量和服务状态，只查询。'},
]


def capabilities(request, user):
    counts = {}
    for _, route, _ in app.routes(request, user):
        area = app.domain(route.path)
        counts[area] = counts.get(area, 0) + 1
    return {'areas': CAPABILITIES, 'role': user['role'], 'native_actions': counts,
            'boundaries': ['平台登录、验证码和秘密输入使用页面或受保护卡片。',
                           '已有接口代表可以调用，未逐项实测的场景不宣称已经验收。',
                           '发布到外部平台按用户指定的内容、平台和账号执行。'],
            'href': '/ask?capabilities=1'}


class SourceQuery(Arguments):
    id: str = ''
    text: str = ''
    tag: str = ''
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=30, ge=1, le=100)


def source_query(a, request, user):
    from web.services.assistant_management import _native
    if a.id:
        from urllib.parse import quote
        return _native(request, 'GET', '/api/sources/' + quote(a.id, safe=''))
    data = _native(request, 'GET', '/api/sources')
    rows = [s for s in data['sources'] if (not a.tag or a.tag in s['tags']) and
            (not a.text or a.text.casefold() in str([s['id'], s['name'], s['spec'], s['note']]).casefold())]
    return {'revision': data['revision'], 'adapters': data['adapters'], 'sources': rows[a.offset:a.offset+a.limit],
            'total': len(rows), 'next_offset': a.offset+a.limit if len(rows) > a.offset+a.limit else None, 'problems': data['problems'], 'href': '/sources'}


class SourceChange(Arguments):
    action: Literal['create', 'update', 'delete']
    id: str
    expected_revision: str
    fields: SourceFields = Field(default_factory=SourceFields)


def source_change(a, request, user):
    action = {'create': ('POST /api/sources', {}), 'update': ('PATCH /api/sources/{key}', {'key': a.id}),
              'delete': ('DELETE /api/sources/{key}', {'key': a.id})}[a.action]
    body = {'expected_revision': a.expected_revision, 'fields': a.fields.model_dump(exclude_none=True)}
    if a.action == 'create':
        body['id'] = a.id
    values = dict(action_id=action[0], path=action[1], body=None if a.action == 'delete' else body,
                  query={'expected_revision': a.expected_revision} if a.action == 'delete' else {}, result_offset=0)
    from types import SimpleNamespace
    result = app.execute(SimpleNamespace(**values), request, user, write=True)
    result.update(href='/sources', message=(result.get('result') or {}).get('message') or '信息源变更已保存')
    return result


class PrepareBatch(Arguments):
    spec: BatchSpec


class ManageBatch(Arguments):
    id: str
    action: Literal['get', 'start', 'retry', 'cancel'] = 'get'


class SourceRegistration(SourceFields):
    id: str


class ConfigureTask(Arguments):
    matrix_key: str
    matrix_name: str | None = None
    domain: str | None = None
    job_name: str = Field(description='完整研究任务路径，例如 research/browser_python；不使用显示名称')
    title: str | None = None
    prompt: str | None = None
    output: str | None = None
    sources: list[str] | None = None
    keywords: list[str] | None = None
    registered_sources: list[SourceRegistration] = Field(default_factory=list)
    recipe: str
    render: dict | None = None


def get_batch(a, request, user):
    batch = view(BatchStore().get(a.id, user))
    recovery = ({'tool': 'manage_application_batch', 'arguments': {'id': batch['id'], 'action': 'retry'},
                 'scope': '仅继续失败或阻塞项；已完成研究和成功项不重跑。'}
                if batch['status'] in ('failed', 'waiting') and any(i['state'] in ('failed', 'blocked') for i in batch['items']) else None)
    return {'batch': batch, 'href': batch['href'], 'application_batch_id': a.id,
            'message': '只读取批次状态，不重新执行', 'recovery': recovery}


def configure_task(a, request, user):
    from web.services.assistant_setup import configure
    return configure(a, request, user)


TOOLS = {
    'inspect_matrix_operations': Tool(MatrixOperations, '一次检查指定矩阵的运营链路：任务状态、来源及抓取问题、生产配置及继承、周期、已登录平台账号、近期投稿和本矩阵可发布候选。key是矩阵key，platforms可限定平台，默认列全部平台但仅查询已登录账号的候选。候选total为总数，items最多limit条；近期投稿最多10条且可能属于其他矩阵。只读，不触发研究或投稿；运营检查优先使用本工具，细节缺失时再展开。', inspect_matrix, label='检查矩阵运营'),
    'configure_matrix_task': Tool(ConfigureTask, '一次配齐矩阵、共享信息源、研究任务和视频配方。已有对象只改指定字段，缺少时创建；job_name必须是完整路径，title是显示名称。registered_sources登记/修改共享来源，sources设置任务引用。recipe使用兼容配方；render是矩阵共用设置，任务继承且原覆盖保留。返回准确production_keys，可用于prepare_application_batch的scope.ids；也可用scope.jobs选择完整job_name。不会启动研究、周期或发布。失败保留并列出已完成步骤。',
        configure_task, 'editor', True, '配齐矩阵与研究任务'),
    'application_capabilities': Tool(Capabilities, '查看整个应用助手的能力范围、当前角色可发现的操作数量和边界，先整体后局部。', lambda a, r, u: capabilities(r, u), label='查看应用能力'),
    'query_sources': Tool(SourceQuery, 'id为空分页读取信息源、revision、adapter、引用任务与抓取状态，可用text/tag筛选；id非空读取准确来源及直接/标签引用。next_offset非空说明还有来源。', source_query, label='查询信息源'),
    'manage_source': Tool(SourceChange, '登记、修改或删除共享信息源。先query_sources读取expected_revision，只改fields指定字段；会造成直接引用或标签引用失效的删除将返回受影响任务，按用户要求调整引用后再删除。', source_change, 'editor', True, '管理信息源'),
    'prepare_application_batch': Tool(PrepareBatch, '准备完整范围的持久批次，不执行。kind：produce按生产配置研究后自动写稿出片（refresh=false用现有最新报告）；publish批量发布到指定platform和核对过的account_uid；schedules按enabled批量启停；matrix_render合并render且保留任务覆盖。scope指定ids（produce填production_keys，不是job名）、jobs（完整研究任务名）、matrices（矩阵key）或明确all=true。候选全部分页收集并冻结；返回批次id与逐项清单。用户明确要求执行时准备后直接start，无需额外确认；只要求查看或准备时不要start。',
        lambda a, r, u: assistant_batches.prepare(a.spec.model_dump(), r, u), 'editor', True, '准备操作批次'),
    'query_application_batch': Tool(ManageBatch, '读取一个批次的固定范围、逐项结果、真实后台状态及入口。只读，不循环等待。', get_batch, label='查询操作批次'),
    'manage_application_batch': Tool(ManageBatch, '对已准备批次执行start，或按用户要求retry未完成项/cancel停止后续派发。恢复原批次直接retry，后台会接续原报告和正确版本，已完成研究和成功项不重跑，无需另建批次或在外面手动派发文稿步骤。未知提交不自动重放；已启动的研究、视频和投稿仍由原生执行器管理。只查状态使用query_application_batch。',
        lambda a, r, u: assistant_batches.manage(a.id, a.action, u) if a.action != 'get' else get_batch(a, r, u), 'editor', True, '执行操作批次'),
}
