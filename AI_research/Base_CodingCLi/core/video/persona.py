"""The account's narrator: a hook, a natural introduction tied to this topic, then the story.

Identity is authored with the surrounding narration, never spliced into a sentence
by position. Without config/persona.yaml there is no narrator instruction.
"""
from __future__ import annotations

import os
import re
from typing import Optional

import yaml


def _config_dir() -> str:
    try:
        from web.settings import get_settings
        return get_settings().paths.config_dir
    except Exception:  # noqa: BLE001 - outside the web app: the workspace's own config
        return "config"


def load(config_dir: Optional[str] = None) -> dict:
    """``{name, introduce, farewell}``, or ``{}`` when there is no persona (no file, no name)."""
    path = os.path.join(config_dir or _config_dir(), "persona.yaml")
    try:
        with open(path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except (OSError, yaml.YAMLError):
        return {}
    name = str(data.get("name") or "").strip() if isinstance(data, dict) else ""
    if not name:
        return {}
    greeting = str(data.get("opening_greeting") or "").strip()
    return {"name": name, "introduce": str(data.get("introduce") or f"我是{name}。").strip(),
            "farewell": str(data.get("farewell") or f"我是{name}，我们下期见。").strip(),
            **({"opening_greeting": greeting} if greeting else {})}


def prompt(persona: dict, *, opening_only: bool = False) -> str:
    if not persona:
        return ""
    name = persona["name"]
    introduction = str(persona.get("introduce") or f"我是{name}。").rstrip("。.!！ ")
    greeting = persona.get("opening_greeting", "大家好")
    exact_greeting = (f"没有固定栏目问候时，用「{greeting}，{introduction}」接在钩子后。"
                      if persona.get("opening_greeting") else "")
    ending = "。" if opening_only else f"；最后道别说「{persona['farewell']}」。"
    return (f"\n你是这个账号固定的讲述者「{name}」，全程用第一人称“我”对观众讲：我看了、我算了一下、我的判断是、我建议。"
            f"开头先讲完整的钩子，再自然问候、自我介绍并接上本期具体要聊的问题，然后承接正文。"
            f"例如句式‘{greeting}，{introduction}，今天我们聊聊……’，省略处必须写成这份材料的具体问题，不能照抄省略号。"
            f"{exact_greeting}"
            "问候、名字和本期话题写成同一段连贯口播，不把名字单独写成一句或一段，不把自我介绍插进尚未讲完的事件。"
            "介绍要短，说明为什么接下来要讲这件事，不列节目流程，不重复钩子；语气贴合内容，严肃事件不强行热闹。"
            f"若节目已经有固定问候，不重复打招呼，仍自然衔接名字和本期问题{ending}"
            "来源只用半句话带过（“官方公告写的是……”“测出来的结果是……”），不用“据某某报道”“某某表示”起句。"
            "没有亲手做过的事不说成自己做的：材料里的测试、跑分、实验是别人做的，不说“我实测”“我试了”。")


def opening_needs_introduction(paragraphs: list, persona: dict, *, fixed_greeting: str = "") -> bool:
    """Recognize missing or bare identity lines, not judge the quality of an opening.

    A complete introduction with a topic is left to the author. This only catches
    the mechanical name-only sentence previously inserted by this module.
    """
    if not persona or not paragraphs:
        return False
    identity = str(persona.get("introduce") or f"我是{persona['name']}。").rstrip("。.!！ ")
    opening = "\n".join(p["text"] if isinstance(p, dict) else p.text for p in paragraphs)
    if greeting := (persona.get("opening_greeting") if not fixed_greeting else ""):
        introductions = list(re.finditer(re.escape(greeting) + r"[，, ]+" + re.escape(identity), opening))
        if len(introductions) != 1 or opening.count(identity) != 1:
            return True
        intro = introductions[0]
        hook = opening[:intro.start()].rstrip()
        continuation = opening[intro.end():].partition("\n")[0].lstrip("，,。.!！ ")
        return not (re.search(r"\w", hook) and hook.endswith(("。", "？", "！", "?", "!", "”", "」"))
                    and re.search(r"\w", continuation))
    if identity not in opening:
        return True
    bare = re.compile(r"(?:^|[。！？!?\n])\s*(?:(?P<greeting>大家好|各位好)[，,。！! ]*)?"
                      + re.escape(identity) + r"\s*(?:[。！!](?![”’\"'])|$)")
    for match in bare.finditer(opening):
        # A greeting after the hook can end a sentence. The same paragraph
        # still carries the topic, so the spoken introduction is not isolated.
        continuation = opening[match.end():].partition("\n")[0].strip()
        if not (match["greeting"] and opening[:match.start()].strip() and continuation):
            return True
    return False
