"""Contract for local browser-based publishers. Importing this module never starts a browser."""
from __future__ import annotations

from pathlib import Path
import re
from typing import Any, Protocol


class AdapterError(RuntimeError):
    """Public-safe provider failure; never include credentials or raw provider responses."""


class UploadPreparationError(AdapterError):
    """The adapter stopped in upload preparation, before any publish action."""


def accepted_without_work_id(platform: str, receipt: dict) -> bool:
    """Kuaishou's verified acceptance envelope may omit its public work ID."""
    return (platform == 'kuaishou' and receipt.get('accepted') is True
            and receipt.get('awaiting_work_id') is True and receipt.get('work_id') in (None, ''))


def submission_evidence(platform: str, http_status: int, data: Any) -> dict:
    """Keep a bounded receipt that can be re-parsed without sending another POST."""
    evidence: dict = {'http_status': http_status, 'body_type': type(data).__name__, 'body': None}
    if not isinstance(data, dict):
        return evidence
    body: dict = {}
    for key in ('code', 'result'):
        if key in data:
            value = data[key]
            body[key] = value if type(value) is int and -(2**53) < value < 2**53 else None
    if 'success' in data:
        body['success'] = data['success'] if type(data['success']) is bool else None
    if 'data' in data:
        nested = data['data']
        body['data'] = None
        if isinstance(nested, dict):
            body['data'] = {}
            keys = ('id', 'note_id') if platform == 'xiaohongshu' else ('workId', 'photoIdStr', 'publishId')
            pattern = r'[0-9a-f]{24}' if platform == 'xiaohongshu' else r'[A-Za-z0-9_-]{6,80}'
            for key in keys:
                if key in nested:
                    value = nested[key]
                    body['data'][key] = value if isinstance(value, str) and re.fullmatch(pattern, value) else None
    # Never retain provider messages, request URLs, headers or signing tokens.
    evidence['body'] = body
    return evidence


def upload_failure_details(error: Exception) -> dict[str, str]:
    """Extract only static operation names and exception types, with no locals or error text."""
    stages = {
        'submission_receipt': '解析发布回执',
        'identity': '核对账号',
        'validate_upload_args': '检查本地视频和封面',
        'validate_base_args': '检查上传配置',
        '_focus_desc_editor': '定位作品描述输入框',
        '_select_video_file': '选择视频上传入口',
        '_wait_for_video_upload': '等待视频素材上传',
        '_publish_once': '定位并单次点击发布',
        'fill_meta': '填写标题、简介和话题',
        'set_thumbnail': '设置视频封面',
        'apply_collection': '设置作品合集',
        '_click_visible_publish_confirm': '确认发布弹窗',
        'set_schedule_time': '设置发布时间',
    }
    stage, current, seen = '平台上传流程', error, set()
    while id(current) not in seen:
        seen.add(id(current))
        trace = current.__traceback__
        while trace is not None:
            name = trace.tb_frame.f_code.co_name
            if name in stages:
                stage = stages[name]
            trace = trace.tb_next
        nested = current.__cause__ or current.__context__
        if not isinstance(nested, Exception):
            break
        current = nested
    return {'stage': stage, 'error_type': type(current).__name__[:80]}


class SocialAdapter(Protocol):
    key: str
    label: str
    creator_url: str
    login_url: str
    title_limit: int
    description_limit: int
    tag_limit: int
    # This must describe implemented, source-verified paths, not inferred upstream capabilities.
    supports_upload: bool
    supports_public_check: bool

    async def identity(self, context: Any) -> dict:
        """Return exact stable {'uid': str, 'name': str}; raise AdapterError if not verified."""
        ...

    async def prepare_login(self, context: Any) -> Any:
        """Open the platform login and return its page. No credentials are entered automatically."""
        ...

    async def capture_qr(self, page: Any, destination: Path) -> bool:
        """Capture only an actual login QR element, never the whole account page."""
        ...

    def is_submit(self, url: str) -> bool:
        """Exact provider hostname and publishing mutation path. Never match file uploads or login."""
        ...

    def submission_receipt(self, data: Any) -> dict:
        """Return a strict receipt; verified Kuaishou acceptance may explicitly await its work ID."""
        ...

    def validate_payload(self, payload: dict) -> None:
        """Validate editable content without file/network/provider side effects."""
        ...

    async def upload(self, playwright: Any, payload: dict, storage: Path) -> None:
        """Use injected guarded Playwright and the pinned upstream uploader. No retry/worker ownership here."""
        ...

    async def inspect_work(self, playwright: Any, storage: Path, owner_id: str, work_id: str) -> dict:
        """Read-only exact identity/ownership/public checks; public needs explicit evidence, else stay unconfirmed."""
        ...

    async def inspect_submission(self, playwright: Any, storage: Path, owner_id: str, lookup: dict) -> dict:
        """Discover a unique submitted work and read its review/visibility state, without a publish action."""
        ...


def clean_owner(value: dict) -> dict:
    """Keep stable identity only; file locations use a hash rather than provider identifiers."""
    uid = value.get('uid') if isinstance(value, dict) else None
    name = value.get('name') if isinstance(value, dict) else None
    if not isinstance(uid, str) or not 1 <= len(uid) <= 300 or any(ord(c) < 33 for c in uid):
        raise AdapterError('平台未返回可验证的稳定账号标识，请重新连接。')
    if not isinstance(name, str) or not name.strip():
        raise AdapterError('平台未返回可验证的账号名称，请重新连接。')
    return {'uid': uid, 'name': name[:100]}


def clean_work(value) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 300 or any(ord(c) < 33 for c in value):
        raise AdapterError('请填写完整且准确的平台作品 ID。')
    return value
