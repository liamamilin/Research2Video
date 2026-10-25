"""One allowlist of integrated publishers; adding an adapter never executes a provider action."""
from __future__ import annotations

from importlib import import_module

from core.social.adapter import AdapterError
from core.social.comments import COMMENT_PLATFORMS, COMMENT_LIMIT

PLATFORMS = {
    'kuaishou': ('快手', 'kuaishou', 'KuaishouAdapter'),
    'xiaohongshu': ('小红书', 'xiaohongshu', 'XiaohongshuAdapter'),
    'weixin_channels': ('微信视频号', 'weixin_channels', 'WeixinChannelsAdapter'),
}

# User requested hiding/deferment on 2026-10-25. Preserve the adapter and data;
# resume only after the login loop is fixed and account acceptance is complete.
DEFERRED_PLATFORMS = frozenset({'weixin_channels'})


def active_platforms() -> tuple[str, ...]:
    return tuple(key for key in PLATFORMS if key not in DEFERRED_PLATFORMS)


def adapter(platform: str):
    if platform not in PLATFORMS:
        raise AdapterError('这个平台尚未接入。')
    _, module, name = PLATFORMS[platform]
    return getattr(import_module('core.social.adapters.' + module), name)()


def definition(platform: str) -> dict:
    item = adapter(platform)
    return {'id': platform, 'name': item.label, 'creator_url': item.creator_url,
            'title_min': getattr(item,'title_min',1), 'combined_limit': getattr(item,'combined_limit',None), 'title_limit': item.title_limit, 'description_limit': item.description_limit, 'tag_limit': item.tag_limit, 'tag_length_limit': 30 if platform=='kuaishou' else 100,
            'capabilities': {'connect': True, 'upload': item.supports_upload,
                             'public_check': item.supports_public_check, 'first_comment': platform in COMMENT_PLATFORMS, 'pin_comment': False, 'analytics': False},
            'first_comment_limit': COMMENT_LIMIT,
            'public_check_gap': getattr(item, 'public_check_gap', getattr(item,'public_check_note','公开状态须在创作中心核对。')),
            'ownership_check': platform=='kuaishou',
            'upload_note': getattr(item,'upload_note','本平台投稿与公开确认仍待完整验收；无可靠回执时不会自动重投。'),
            'acceptance': 'awaiting_account'}
