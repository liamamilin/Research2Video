"""Business-level matrix and publication operations."""
from typing import Literal

from pydantic import Field

from . import Arguments, Tool
from web.services import assistant_management as service
from web.services.local_video import LocalVideoRequest


class MatrixQuery(Arguments):
    key: str = ''


class MatrixFields(Arguments):
    name: str | None = None
    domain: str | None = None
    keywords: list[str] | None = None
    jobs: list[str] | None = None
    monthly_searches: int | None = Field(default=None, ge=0)
    render: dict | None = None


class MatrixChange(Arguments):
    action: Literal['create', 'update', 'add_jobs', 'remove_jobs']
    key: str = Field(pattern=r'^[a-z][a-z0-9_]{0,23}$')
    fields: MatrixFields = Field(default_factory=MatrixFields)
    expected_revision: str = ''


class PublishingQuery(Arguments):
    action: Literal['catalog', 'overview', 'candidates', 'prepare', 'task'] = 'catalog'
    platform: str = 'bilibili'
    matrix: str = ''
    text: str = ''
    current_only: bool = True
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=20, ge=1, le=50)
    render_ids: list[str] = Field(default_factory=list, max_length=20)
    task_id: str = ''


class ProductionChange(Arguments):
    config_key: str = Field(default='', description='现有视频生产配置的key；新增关联时留空。不是矩阵key。')
    job: str = ''
    recipe: str = ''
    auto: int | None = Field(default=None, ge=0, le=10)
    render: dict | None = None
    inherit: list[str] = Field(default_factory=list)
    expected_revision: str = ''


class PublicationItem(Arguments):
    render_id: str = Field(min_length=1, max_length=64)
    fields: dict = Field(default_factory=dict)


class PublishingChange(Arguments):
    action: Literal['submit', 'retry', 'cancel', 'reconcile', 'resume']
    platform: str
    account_uid: str = Field(min_length=1, max_length=300)
    items: list[PublicationItem] = Field(default_factory=list, max_length=20)
    task_id: str = ''
    video: str = ''


class PublishingDraft(Arguments):
    platform: str
    account_uid: str = Field(min_length=1, max_length=300)
    render_id: str = Field(min_length=1, max_length=64)
    fields: dict


TOOLS = {
    'publish_local_video': Tool(LocalVideoRequest, '按用户提供的本机视频绝对路径准备或提交到指定平台。支持MP4/MOV/M4V；path是运行本应用电脑上的文件路径，platform用catalog返回的id。action=prepare导入/复用视频并返回发布信息、平台限制、当前账号，不上传；action=submit直接接入原生投稿队列，无需生成文稿或重生成视频。account_uid可选，默认该平台当前已连接账号；fields可填title/description/tags（B站可tid）和first_comment。未给标题或简介时提示补充缺项，沿用已有草稿信息，不自行把文件名当发布标题或编写简介；返回needs_input时按missing_fields询问，补充后用原路径继续，保留其他字段。按实际平台限制整理发布信息；用户明确要求投稿时可准备后继续submit，信息齐全不再询问一次确认。重复路径/相同文件复用，已有投稿继续原任务。登录或文件不可用时返回具体入口/原因；平台后台跟踪结果，不循环查进度。',
        lambda a, r, u: service._native(r, 'POST', '/api/assistant/local-video', body=a.model_dump(exclude_none=True)),
        'editor', True, '发布本地视频'),
    'save_publishing_draft': Tool(PublishingDraft, '保存指定平台、账号和准确成片的发布草稿，不上传。先query_publishing prepare读取发布包，fields仅填写要修改的title/description/tags/first_comment（B站还可tid），保留其他字段。保存后返回原生平台适配和校验结果；需要继续修正时按实际问题修改草稿，不能把未保存的建议称为准备完成。', service.save_publishing_draft, 'editor', True, '保存平台发布草稿'),
    'query_matrices': Tool(MatrixQuery, '查询矩阵。key为空列出矩阵和未归属任务；填key读取成员、周期、每个任务可用的production_options（同原生创建校验，不写文件）、已关联生产配方、矩阵共用render、任务overrides与effective_render及修改revision。', service.matrix_query, label='查询矩阵'),
    'manage_matrix': Tool(MatrixChange, '创建或调整矩阵。create填写key与fields.name；update只填需要改的字段；add_jobs/remove_jobs只填fields.jobs。修改前query_matrices取expected_revision。render按字段合并，保留其他共用设置和任务覆盖。新研究任务用create_job，视频配置用configure_production，周期用manage_schedule，随后关联矩阵。', service.matrix_change, 'editor', True, '管理矩阵'),
    'configure_production': Tool(ProductionChange, '为矩阵内研究任务关联视频配方或调整任务配置。新增：job+recipe，先query_matrices看production_options，auto默认关闭，render仅设置此任务个性字段。修改：config_key+expected_revision（矩阵查询production返回），auto修改自动生产数量（0关闭），render填需要改的字段，inherit填恢复矩阵继承的字段名（如video_speed）；其他设置保留，不重复创建关联。', service.configure_production, 'editor', True, '设置任务视频配置'),
    'query_publishing': Tool(PublishingQuery, '统一发布工作台：catalog列出可用平台与账号；candidates按平台、可选matrix和text查未投稿成片，默认仅当前定稿，offset分页；prepare用render_ids准备准确版本的标题/简介/标签与警告，不上传；task读取一次投稿；overview查看平台任务。平台未登录时给href。', service.publishing_query, label='查询与准备发布'),
    'manage_publishing': Tool(PublishingChange, '执行投稿：submit指定items、platform和已核对account_uid；retry/cancel/reconcile使用原task_id，reconcile可填作品链接video。处理登录或失败后用resume：先读原任务，已投稿仅核对，确认未提交才重试，已公开直接返回结果，不新建上传。query_publishing task返回label、recovery和准确版本入口；恢复按recovery操作。由工作区跟踪，不循环查进度；批量保留成功项。登录/验证码在平台页处理。', service.publishing_change, 'editor', True, '管理发布'),
}
