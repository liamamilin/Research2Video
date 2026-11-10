"""The account's fixed narrator (config/persona.yaml): one name, first person, introduced right after the hook.

B站 hits have a narrator viewers remember ("这里是瑞亚", "我是赎罪利亚"); ours was a nameless voice reading other
people's news ("据某某报道"). Every script is now told by one narrator in the first person. What the narrator did is
what the pipeline did (read, compared, calculated); a test the material reports is someone else's, so the narrator
never says they ran it. The program makes sure the introduction is said: when the author left it out, it goes right
after the first sentence. Without the file there is no persona and nothing changes.
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
    return {"name": name, "introduce": str(data.get("introduce") or f"我是{name}。").strip(),
            "farewell": str(data.get("farewell") or f"我是{name}，我们下期见。").strip()}


def prompt(persona: dict) -> str:
    if not persona:
        return ""
    name = persona["name"]
    return (f"\n你是这个账号固定的讲述者「{name}」，全程用第一人称“我”对观众讲：我看了、我算了一下、我的判断是、我建议。"
            f"第一句仍是钩子，紧接着第二句以「我是{name}，」开头，用半句话说这期帮观众解决什么"
            f"（比如“我是{name}，这期帮你把这笔账算清楚。”）；最后道别说「{persona['farewell']}」。"
            "来源只用半句话带过（“官方公告写的是……”“测出来的结果是……”），不用“据某某报道”“某某表示”起句。"
            "没有亲手做过的事不说成自己做的：材料里的测试、跑分、实验是别人做的，不说“我实测”“我试了”。")


_SENTENCE = re.compile(r"^(.+?[。！？!?])")


def ensure_introduced(paragraphs: list, persona: dict) -> bool:
    """Put ``introduce`` after the first sentence unless the opening already names the narrator. True when added.

    ``paragraphs`` are the first section's paragraph models (with ``.text``); only the first two are looked at."""
    if not persona or not paragraphs:
        return False
    if any(f"我是{persona['name']}" in p.text for p in paragraphs[:2]):
        return False
    first = paragraphs[0].text.strip()
    head = _SENTENCE.match(first)
    if head and head.group(1) != first:
        paragraphs[0].text = head.group(1) + persona["introduce"] + first[len(head.group(1)):].lstrip()
    else:
        paragraphs[0].text = (first + persona["introduce"]) if first else persona["introduce"]
    return True
