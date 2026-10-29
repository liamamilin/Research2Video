"""Small, conservative Douyin receipt contracts shared by the API and isolated worker."""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlsplit

WORK_ID = re.compile(r"^[0-9]{15,25}$")
UID = re.compile(r"^[A-Za-z0-9_.-]{1,160}$")
CREATE_PATHS = {"/web/api/media/aweme/create/", "/web/api/media/aweme/create_v2/"}


class DouyinError(RuntimeError):
    """A user-readable failure without provider credentials or raw responses."""


class DouyinLookupError(DouyinError):
    """Distinguish a list failure from an actually completed, empty lookup."""
    def __init__(self, message: str, code: str, *, state: str = 'unavailable'):
        super().__init__(message)
        self.code = code
        self.state = state


def work_id(value: str) -> str:
    value = value.strip()
    if WORK_ID.fullmatch(value):
        return value
    parts = urlsplit(value)
    if parts.scheme == "https" and parts.hostname in ("www.douyin.com", "douyin.com", "www.iesdouyin.com"):
        match = re.fullmatch(r"/(?:share/)?video/([0-9]{15,25})/?", parts.path)
        candidate = match[1] if match else parse_qs(parts.query).get("modal_id", [""])[0]
        if WORK_ID.fullmatch(candidate):
            return candidate
    raise DouyinError("请填写作品 ID 或完整的抖音视频链接；短链接请先在浏览器打开。")


def profile(data: dict) -> dict:
    if not isinstance(data, dict) or type(data.get("status_code")) is not int or data["status_code"] != 0:
        raise DouyinError("未能验证抖音登录，请在独立浏览器完成扫码或身份验证后重试。")
    nested = data.get("data") if isinstance(data.get("data"), dict) else {}
    user = data.get("user") or nested.get("user")
    if not isinstance(user, dict):
        raise DouyinError("抖音没有返回可验证的账号标识，请重新连接。")
    uid = str(user.get("uid") or user.get("sec_uid") or "")
    if not UID.fullmatch(uid):
        raise DouyinError("抖音没有返回可验证的账号标识，请重新连接。")
    return {"uid": uid, "name": str(user.get("nickname") or "抖音创作者")[:100],
            "sec_uid": str(user.get("sec_uid") or "")[:160]}


def accepted_receipt(data: dict) -> dict:
    """A management-page redirect and a zero exit code are not submission receipts."""
    code = data.get("status_code") if isinstance(data, dict) else None
    if not isinstance(code, int) or isinstance(code, bool):
        raise DouyinError("投稿响应格式无法识别，请到创作中心核对；系统不会自动重投。")
    if code != 0:
        return {"accepted": False, "code": code}
    aweme = data.get("aweme") if isinstance(data.get("aweme"), dict) else {}
    candidate = data.get("item_id") or aweme.get("aweme_id")
    if type(candidate) is int:
        candidate = str(candidate)  # Python JSON integers are exact; expose only strings to JavaScript.
    if not isinstance(candidate, str) or not WORK_ID.fullmatch(candidate):
        raise DouyinError("平台已返回响应，但未取得可靠的作品 ID，请到创作中心核对。")
    return {"accepted": True, "work_id": candidate,
            "url": f"https://www.douyin.com/video/{candidate}", "public": False}


def public_receipt(data: dict, expected_work: str, owner: dict) -> dict | None:
    """Only an anonymous detail response for the exact work and author confirms public availability."""
    if not isinstance(data, dict) or type(data.get("status_code")) is not int or data["status_code"] != 0:
        return None
    item = data.get("aweme_detail")
    if not isinstance(item, dict):
        return None
    if str(item.get("aweme_id") or item.get("awemeId") or "") != expected_work:
        return None
    author = item.get("author") if isinstance(item.get("author"), dict) else {}
    actual_uid, actual_sec = str(author.get("uid") or ""), str(author.get("sec_uid") or author.get("secUid") or "")
    owned = actual_uid == owner["uid"] or bool(owner.get("sec_uid") and actual_sec == owner["sec_uid"])
    if not owned:
        raise DouyinError("这条抖音视频不属于当前连接账号，不能回写发布记录。")
    state = item.get("status") if isinstance(item.get("status"), dict) else {}
    state = {**state, **{snake: state[camel] for snake, camel in (
        ('private_status', 'privateStatus'), ('is_delete', 'isDelete'), ('is_prohibited', 'isProhibited'))
        if snake not in state and camel in state}}
    # An anonymous exact-work response with explicit public visibility is proof of
    # availability. Current Douyin responses omit `reviewed`; in that schema require
    # a concrete Douyin video playback address instead of inventing an audit value.
    if (type(state.get("private_status")) is not int or state["private_status"] != 0 or
            state.get("is_delete") is not False or state.get("is_prohibited") is not False):
        return None
    if any(state.get(key) is True for key in ('is_private', 'isPrivate', 'in_reviewing', 'is_reviewing', 'isReviewing')):
        return None
    if 'reviewed' in state:
        if type(state['reviewed']) is not int or state['reviewed'] != 1:
            return None
    else:
        video = item.get('video') if isinstance(item.get('video'), dict) else {}
        address = video.get('play_addr', video.get('playAddr', {}))
        urls = address.get('url_list', address.get('urlList', [])) if isinstance(address, dict) else []
        if not isinstance(urls, list) or not any(
                isinstance(url, str) and urlsplit(url).scheme == 'https' and
                ((urlsplit(url).hostname or '') == 'www.douyin.com' or (urlsplit(url).hostname or '').endswith('.douyinvod.com'))
                for url in urls):
            return None
    created = item.get("create_time", item.get("createTime"))
    return {"work_id": expected_work, "uid": owner["uid"], "public": True,
            "url": f"https://www.douyin.com/video/{expected_work}",
            "published_at": created if isinstance(created, int) and not isinstance(created, bool) else 0,
            "title": str(item.get("desc") or "")[:100], "evidence": "anonymous_video_detail"}


COMMENT_LIMIT = 500


def clean_first_comment(value: dict) -> dict:
    """Capture an opt-in author comment; pinning is unavailable on Douyin."""
    if not isinstance(value, dict) or set(value) - {'enabled', 'text', 'pin'}:
        raise DouyinError('作者评论选项格式不正确。')
    if value.get('pin'):
        raise DouyinError('抖音当前不支持自动置顶评论，请关闭置顶选项。')
    if type(value.get('enabled', False)) is not bool or not isinstance(value.get('text', ''), str):
        raise DouyinError('作者评论选项格式不正确。')
    from core.social.publish_text import comment_options
    value = comment_options(value)
    text = value.get('text', '').strip()
    if len(text) > COMMENT_LIMIT or (value.get('enabled') and not text):
        raise DouyinError(f'启用作者评论时请填写 1–{COMMENT_LIMIT} 字的内容。')
    return {'enabled': value.get('enabled', False), 'text': text, 'pin': False}


def comment_text_matches(actual, expected: str) -> bool:
    """Douyin can collapse whitespace; never tolerate changed words, punctuation or URLs."""
    return isinstance(actual, str) and re.sub(r'\s+', ' ', actual).strip() == re.sub(r'\s+', ' ', expected).strip()


def comment_receipt(data: dict, text: str) -> dict:
    """Accept only an exact author/top-level comment receipt from the guarded creator request."""
    code = data.get('status_code') if isinstance(data, dict) else None
    if type(code) is not int:
        raise DouyinError('没有取得明确的评论回执，请核对视频下方评论；不会自动重发。')
    if code != 0:
        return {'accepted': False, 'code': code, 'confirmed_not_submitted': True}
    comment = data.get('comment_info')
    if not isinstance(comment, dict):
        raise DouyinError('评论响应缺少可靠记录，请核对视频下方评论；不会自动重发。')
    cid = comment.get('comment_id')
    if not isinstance(cid, str) or not re.fullmatch(r'[A-Za-z0-9@/+=_.-]{8,300}', cid):
        raise DouyinError('评论 ID 无法确认，请核对视频下方评论；不会自动重发。')
    if not comment_text_matches(comment.get('text'), text) or comment.get('is_author') is not True or type(comment.get('level')) is not int or comment['level'] != 1:
        raise DouyinError('评论内容、作者或层级与本次作者评论不一致，请人工核对。')
    return {'accepted': True, 'comment_id': cid, 'read_back': False, 'confirmed_not_submitted': False, 'normalized_whitespace': comment.get('text') != text}


def creator_diagnostics(row: dict) -> dict:
    """Keep only scalar visibility/audit signals, never provider tokens or opaque work identifiers."""
    names = ('status', 'item_status', 'video_status', 'review_status', 'audit_status', 'reviewed',
             'private_status', 'visibility_type', 'is_delete', 'is_prohibited', 'is_private', 'aweme_status', 'audit_status',
             'is_reviewing', 'is_public', 'item_state', 'visibility', 'permission', 'permissions',
             'privateStatus', 'isDelete', 'isProhibited', 'isReviewing', 'isPublic', 'reviewStatus', 'isPrivate')
    out = {}
    for name in names:
        value = row.get(name)
        if type(value) in (int, bool):
            out[name] = value
        elif name == 'status' and isinstance(value, dict):
            out[name] = {k: v for k, v in value.items() if k in names and type(v) in (int, bool)}
    return out
