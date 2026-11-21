"""The account narrator's identity, without a mandatory opening sequence.

Without config/persona.yaml there is no narrator instruction.
"""
from __future__ import annotations

import os
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
    ending = "" if opening_only else f"最后道别说「{persona['farewell']}」。"
    return f"\n你是这个账号固定的讲述者「{name}」，用自己的口吻对观众讲述。{ending}"


def opening_needs_introduction(paragraphs: list, persona: dict, *, fixed_greeting: str = "") -> bool:
    """Only repeated identity needs repair; missing or standalone introductions are valid."""
    if not persona or not paragraphs:
        return False
    identity = str(persona.get("introduce") or f"我是{persona['name']}。").rstrip("。.!！ ")
    opening = "\n".join(p["text"] if isinstance(p, dict) else p.text for p in paragraphs)
    return opening.count(identity) > 1
