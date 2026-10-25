"""Author-comment contracts. Sending and video publication have independent state."""
from __future__ import annotations

import re
import unicodedata

from core.social.adapter import AdapterError

COMMENT_LIMIT = 250  # Creator UI uses a 254 UTF-16-unit ceiling; leave a small margin.
COMMENT_PLATFORMS = {'kuaishou': '快手', 'xiaohongshu': '小红书'}


def clean_first_comment(platform: str, value: object) -> dict:
    if not isinstance(value, dict) or set(value) - {'enabled', 'text', 'pin'}:
        raise AdapterError('首条评论配置格式不正确。')
    enabled, text, pin = value.get('enabled', False), value.get('text', ''), value.get('pin', False)
    if type(enabled) is not bool or type(pin) is not bool or not isinstance(text, str):
        raise AdapterError('首条评论配置格式不正确。')
    if (enabled or pin or text) and platform not in COMMENT_PLATFORMS:
        raise AdapterError('这个平台尚未接入自动首评。')
    if pin:
        raise AdapterError(f'{COMMENT_PLATFORMS.get(platform,"本平台")}本期不支持自动置顶，请关闭置顶选项。')
    if (len(text.encode('utf-16-le', errors='surrogatepass')) // 2 > COMMENT_LIMIT
            or any(0xD800 <= ord(c) <= 0xDFFF or ord(c) < 32 and c not in '\n\r\t' for c in text)):
        raise AdapterError(f'{COMMENT_PLATFORMS.get(platform,"本平台")}首条评论最多 {COMMENT_LIMIT} 字，不能包含控制字符。')
    if enabled and not text.strip():
        raise AdapterError('请填写首条评论内容。')
    return {'enabled': enabled, 'text': text.strip(), 'pin': False}


def comment_id(value: object) -> str:
    # Python JSON preserves integer precision; never accept rounded floats or bools.
    if type(value) is int and value > 0:
        value = str(value)
    return value if isinstance(value, str) and re.fullmatch(r'[1-9][0-9]{0,39}', value) else ''


def comment_receipt(value: object) -> dict:
    if (not isinstance(value, dict) or type(value.get('result')) is not int
            or ('success' in value and value['success'] is not True)):
        raise AdapterError('快手评论回执无法确认，请到作品下核对；不会自动重发。')
    if value['result'] != 1:
        # A platform rejection is not proof that a timed-out earlier attempt was absent.
        return {'accepted': False, 'code': value['result']}
    nested = value.get('data')
    target = comment_id(nested.get('commentId')) if isinstance(nested, dict) else ''
    if not target:
        raise AdapterError('快手没有返回可靠评论 ID，请到作品下核对；不会自动重发。')
    return {'accepted': True, 'comment_id': target}


def matching_comments(value: object, owner: str, work: str, text: str, target: str = '') -> list[dict]:
    """Only an exact author, work, root-comment and content match counts as readback."""
    if (not isinstance(value, dict) or type(value.get('result')) is not int or value['result'] != 1
            or not isinstance(value.get('data'), dict) or not isinstance(value['data'].get('list'), list)):
        raise AdapterError('快手评论列表暂未取得，未重新发送评论。')
    def normalize(item):
        return unicodedata.normalize('NFC', item).replace('\r\n', '\n').strip() if isinstance(item, str) else None
    found = {}
    for row in value['data']['list']:
        if not isinstance(row, dict):
            continue
        key = comment_id(row.get('commentId'))
        if (key and (not target or key == target) and str(row.get('photoId')) == work
                and comment_id(row.get('authorId')) == owner
                and type(row.get('replyToCommentId')) is int and row['replyToCommentId'] == 0
                and normalize(row.get('content')) == normalize(text)):
            found[key] = {'comment_id': key, 'read_back': True}
    return list(found.values())
