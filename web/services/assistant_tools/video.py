"""Video script tools: query the pipeline, start a script, and move one along (spec/console-redesign.md).

Nothing here posts to a video site: ``add_publication`` only records a link the user supplies."""
from typing import Literal
from pydantic import Field
from . import Arguments, Tool
from web.routes import video

#: What a started action costs, in the receipt, so the assistant can say it before and after.
COSTS = {
    'create': '研究约 3–10 分钟、消耗搜索额度，随后写稿；短稿的事实/风格审计会再占几分钟。完成后停在草稿，等你审校定稿。',
    'recheck': '发布前复核约 4 分钟，消耗搜索额度。',
    'render': '成片约 15–30 分钟。',
    'restyle': '换风格重写约 10 分钟，不重新调研。',
}


class VideoQuery(Arguments):
    action: Literal['board', 'list', 'find', 'get', 'topics', 'cells', 'quality', 'references'] = 'board'
    script_id: str = ''
    text: str = ''
    status: Literal['RESEARCHING', 'WRITING', 'DRAFT', 'APPROVED', 'FAILED'] | None = None
    source: Literal['flash', 'opportunities'] = 'flash'
    limit: int = Field(default=30, ge=1, le=100)


def _href(script_id: str) -> str:
    return '/video/' + script_id


def _brief(s: dict) -> dict:
    return {'script_id': s['id'], 'title': s.get('title'), 'column': s['column_key'], 'status': s['status'],
            'stage': s.get('stage'), 'published': s.get('published'), 'created_at': s['created_at'],
            'error': s.get('error'), 'href': _href(s['id'])}


def _detail(script_id: str, user) -> dict:
    d = video.get_script(script_id, user=user)
    shown = d.get('version_shown') or {}
    checks = shown.get('checks') or {}
    return {
        'script_id': script_id, 'title': shown.get('title') or d['topic'].get('title'), 'column': d['column_key'],
        'status': d['status'], 'stage': d.get('stage'), 'error': d.get('error'),
        'current_version': d['current_version'], 'approved_version': d.get('approved_version'),
        'length': shown.get('length_check'), 'check_summary': checks.get('summary'),
        'problems': (shown.get('meta') or {}).get('problems'),
        'renders': [{k: r[k] for k in ('id', 'version', 'status', 'active_stage', 'error_message')} for r in d['renders'][:5]],
        'publications': d['publications'],
        'rechecks': [{k: r.get(k) for k in ('id', 'version', 'status', 'verdict')} for r in d['rechecks']],
        'href': _href(script_id),
    }


def query(a, request, user):
    if a.action == 'board':
        return video.home_board(user=user)
    if a.action == 'cells':
        return video.list_cells(user=user)
    if a.action == 'quality':
        return video.draft_quality(user=user)
    if a.action == 'references':
        return video.reference_videos(q=a.text, limit=min(a.limit, 20), user=user)
    if a.action == 'topics':
        return video.list_topics(source=a.source, user=user)
    if a.action == 'get':
        return _detail(a.script_id, user)
    scripts = [_brief(s) for s in video.list_scripts(limit=200, user=user)]
    if a.status:
        scripts = [s for s in scripts if s['status'] == a.status]
    if a.action == 'find':
        needle = a.text.lower()
        scripts = [s for s in scripts if needle in f"{s['title']} {s['column']}".lower()]
    return {'scripts': scripts[:a.limit], 'total': len(scripts)}


class VideoCreate(Arguments):
    column: str
    topic_id: str | None = None
    title: str = ''
    angle: str = ''
    audience: str = ''
    subjects: list[str] = Field(default_factory=list, max_length=2)
    dimensions: str = ''
    topic_ids: list[str] = Field(default_factory=list)
    video_form: str | None = None
    from_script: str | None = None


def create(a, request, user):
    topic = None
    if a.title or a.angle or a.audience or a.subjects or a.dimensions:
        topic = video.TopicIn(title=a.title, angle=a.angle, audience=a.audience, subjects=a.subjects, dimensions=a.dimensions)
    body = video.ScriptCreateIn(column=a.column, topic_id=a.topic_id, topic=topic, topic_ids=a.topic_ids,
                                video_form=a.video_form, from_script=a.from_script)
    made = video.create_script(body, request, user=user)
    return {'script_id': made['id'], 'status': 'started', 'script_status': made['status'], 'cost': COSTS['create'],
            'href': _href(made['id'])}


class VideoChange(Arguments):
    action: Literal['approve', 'restyle', 'recheck', 'render', 'add_publication', 'delete_publication']
    script_id: str
    version: int | None = None
    depth: str | None = None
    tone: str | None = None
    voice_id: str | None = None
    url: str = ''
    published_on: str | None = None
    note: str = ''
    publication_id: str = ''


def change(a, request, user):
    sid = a.script_id
    if a.action == 'approve':
        video.approve_script(sid, video.ApproveIn(version=a.version), user=user)
        return {'script_id': sid, 'status': 'approved', 'href': _href(sid)}
    if a.action == 'restyle':
        made = video.restyle_script(sid, video.RestyleIn(depth=a.depth, tone=a.tone), user=user)
        return {'script_id': sid, 'status': 'started', 'script_status': made['status'], 'cost': COSTS['restyle'], 'href': _href(sid)}
    if a.action == 'recheck':
        made = video.recheck_script(sid, user=user)
        return {'script_id': sid, 'status': 'started', 'recheck': made.get('id'), 'cost': COSTS['recheck'], 'href': _href(sid)}
    if a.action == 'render':
        made = video.render_script(sid, video.RenderIn(voice_id=a.voice_id), user=user)
        return {'script_id': sid, 'status': 'started', 'render_id': made['id'], 'cost': COSTS['render'], 'href': _href(sid)}
    if a.action == 'add_publication':
        fields = {k: v for k, v in (('url', a.url), ('published_on', a.published_on), ('note', a.note), ('version', a.version)) if v}
        made = video.add_publication(sid, video.PublicationIn(**fields), user=user)
        return {'script_id': sid, 'status': 'recorded', 'publication_id': made['id'], 'href': _href(sid)}
    video.delete_publication(sid, a.publication_id, user=user)
    return {'script_id': sid, 'status': 'deleted', 'href': _href(sid)}


TOOLS = {
    'query_video': Tool(VideoQuery, '查视频流水线：board各阶段数量和待处理清单；list/find按状态或标题查文稿；get一篇文稿的状态、审校摘要、成片、发布记录、复核；topics快讯或内容机会候选（含是否已写稿/已发布）；cells可选的栏目格子及状态；quality初稿被改动的程度。references在B站和YouTube找别人做的参考视频（text是主题；只返回标题和链接，不下载；花一次搜索额度，开始前说一句）；其余都只查内部数据。', query, label='查询视频'),
    'create_video': Tool(VideoCreate, '用栏目+选题开始一篇文稿（盘点用topic_ids，对比用subjects+dimensions，快讯/深度用topic_id或title）。会消耗搜索额度和数分钟，开始前先说明；只写到草稿，不会定稿或出片。返回已启动回执，需再查实际状态。', create, 'editor', True, '创建视频文稿'),
    'manage_video': Tool(VideoChange, '对已有文稿：approve定稿（只在用户明确说定稿时）、restyle换风格重写、recheck发布前复核（快讯）、render生成成片、add_publication记录用户提供的B站链接和日期、delete_publication删除记录。不会向任何平台发布。render/recheck/restyle开始前说明耗时，返回只代表已启动。', change, 'editor', True, '管理视频文稿'),
}
