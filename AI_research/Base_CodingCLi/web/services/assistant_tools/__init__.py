"""Typed application tools, with permissions enforced before every dispatch."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Callable
from pydantic import BaseModel, ConfigDict
from web.deps import require_admin, require_editor, require_viewer


class Arguments(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, str_strip_whitespace=True)


@dataclass(frozen=True)
class Tool:
    arguments: type[BaseModel]
    description: str
    handler: Callable
    role: str = 'viewer'
    write: bool = False
    label: str = ''


def registry() -> dict[str, Tool]:
    from . import work, reports, intelligence, administration, video, application, management, activity, operations
    return {**work.TOOLS, **reports.TOOLS, **intelligence.TOOLS, **administration.TOOLS, **video.TOOLS, **application.TOOLS, **management.TOOLS, **activity.TOOLS, **operations.TOOLS}


def definitions(role: str) -> list[dict]:
    ranks = {'viewer': 0, 'editor': 1, 'admin': 2}
    return [{'type': 'function', 'function': {'name': name, 'description': spec.description,
             'parameters': spec.arguments.model_json_schema()}}
            for name, spec in registry().items() if ranks[role] >= ranks[spec.role]]


def execute(name: str, arguments: dict, request, user) -> dict:
    spec = registry().get(name)
    if not spec:
        raise ValueError(f'未知工具：{name}')
    {'viewer': require_viewer, 'editor': require_editor, 'admin': require_admin}[spec.role](request, user=user)
    return spec.handler(spec.arguments.model_validate(arguments), request, user)
