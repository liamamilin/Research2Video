"""Extra angles on a very hot item: the same event told again as 算账值不值 and 怎么上手 (11-10, 选题热度 step 2).

Hits on B站 tell one big event several ways (release, hands-on, cost); our dedupe allowed one video per event. A
production with ``selection.angle_min`` gives the hottest item of a batch at or above that heat extra items, one per
angle its material supports. The program decides: an angle needs its markers in the item's own text (prices for
算账, entry points and steps for 上手), so a variant is never written from material that cannot carry it.

A variant carries ``angle``; its event claim is ``<event_key>#<angle>``, and dedupe compares a story only with
earlier stories of the same angle (core/video/story_pool.py), so siblings never block each other while a second
算账 video on an event already told that way is still refused.
"""
from __future__ import annotations

import copy
import re

ANGLES = {
    "money": {
        "label": "算账值不值",
        "minutes": 5,  # the account's 7-minute hit was a 算账 video; a cost comparison needs room (11-10)
        "markers": re.compile(r"[¥$￥]|\d\s*(?:元|美元|刀)|额度|套餐|价格|定价|每月|月费|年费|每百万|免费|降价|涨价|订阅"),
        "prompt": ("本条讲述角度：算账值不值。同一事件已有一条视频讲发布了什么，这一条只从观众的钱和选择讲："
                   "价格、额度、套餐怎么算，和观众手上现有的方案比多花还是省，什么人值得换、什么人不必。"
                   "标题和开场都从这个角度出发，和讲发布的那条不要重复；材料没给的价格不要编。"),
    },
    "howto": {
        "label": "怎么上手",
        "minutes": 4,
        "markers": re.compile(r"入口|打开|设置|命令|步骤|开启|安装|下载|登录|点击|输入|配置|启用|切换|在[^，。]{1,12}里"),
        "prompt": ("本条讲述角度：怎么上手。同一事件已有一条视频讲发布了什么，这一条只讲观众今天怎么用上它："
                   "从哪里进入、按什么顺序操作、用一个具体任务走一遍、会遇到什么限制。"
                   "标题和开场都从这个角度出发，和讲发布的那条不要重复；材料没写的操作步骤不要编。"),
    },
}


def supported(text: str) -> list[str]:
    """The angles whose markers the item's own text contains, in ``ANGLES`` order."""
    return [key for key, angle in ANGLES.items() if angle["markers"].search(text or "")]


def variants(topic: dict, text: str) -> list[dict]:
    """Copies of ``topic`` for every angle its text supports: own id, ``angle``, ``base_id`` and a field naming it."""
    out = []
    for key in supported(text):
        variant = copy.deepcopy({k: v for k, v in topic.items() if k != "_story_fallbacks"})
        variant.update(id=f"{topic['id']}~{key}", base_id=topic["id"], angle=key,
                       fields={**(topic.get("fields") or {}), "讲述角度": ANGLES[key]["label"]})
        out.append(variant)
    return out


def minutes(angle: str) -> float:
    """The angle's own length, longer than a flash item's; 0 for no angle."""
    return float(ANGLES[angle]["minutes"]) if angle in ANGLES else 0.0


def prompt(angle: str) -> str:
    return ANGLES[angle]["prompt"] if angle in ANGLES else ""
