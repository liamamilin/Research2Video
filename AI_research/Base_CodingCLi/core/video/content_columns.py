"""Business columns, independent of the legacy script-recipe Column runtime.

A column owns production references; reading or editing it never rewrites those
files (their mtime is the auto-production activation boundary).
"""
from __future__ import annotations

import copy
import hashlib
from dataclasses import dataclass, field, replace
from pathlib import Path

import yaml

from core import matrices
from core.fileio import atomic_write, file_lock
from core.yaml_cache import load_yaml
from .production_settings import inherit, validate as validate_render
from .recipes import KEY

TYPE_KEYS = ("news", "explainer", "comparison", "tutorial", "deep_research", "series")
DIR = "content_columns"
FIELDS = {"schema_version", "name", "matrix", "type", "audience", "promise", "production_ids",
          "render_inheritance", "render", "presentation", "status", "notes"}


@dataclass(frozen=True)
class ContentColumn:
    key: str
    name: str
    matrix: str = ""
    type: str = ""
    audience: str = ""
    promise: str = ""
    production_ids: tuple[str, ...] = ()
    render_inheritance: str = "source_compatible"
    render: dict = field(default_factory=dict)
    presentation: dict = field(default_factory=dict)
    status: str = "configured"
    notes: str = ""
    revision: str = ""
    errors: tuple[str, ...] = ()

    @property
    def usable(self) -> bool:
        return not self.errors and self.status != "planned"

    def as_dict(self) -> dict:
        return {**self.data(), "key": self.key, "revision": self.revision,
                "errors": list(self.errors), "usable": self.usable}

    def data(self) -> dict:
        return {"schema_version": 1, "name": self.name, "matrix": self.matrix, "type": self.type,
                "audience": self.audience, "promise": self.promise, "production_ids": list(self.production_ids),
                "render_inheritance": self.render_inheritance, "render": copy.deepcopy(self.render),
                "presentation": copy.deepcopy(self.presentation), "status": self.status, "notes": self.notes}


def types(config_dir: str) -> dict[str, dict]:
    path = Path(config_dir) / "column_types.yaml"
    if not path.exists():
        return {}
    try:
        with path.open(encoding="utf-8") as stream:
            value = load_yaml(stream) or {}
        raw = value.get("types", {})
        if not isinstance(raw, dict):
            return {}
        return {key: {"key": key, **item} for key, item in raw.items()
                if key in TYPE_KEYS and isinstance(item, dict) and isinstance(item.get("name"), str)}
    except (OSError, yaml.YAMLError, AttributeError):
        return {}


def _build(key: str, data: dict, matrix_map: dict, type_map: dict, revision: str = "") -> ContentColumn:
    if not isinstance(data, dict) or set(data) - FIELDS:
        raise ValueError("栏目字段无效，请使用名称、所属领域、类型、受众、交付目标、生产绑定和制作设置")
    if data.get("schema_version", 1) != 1:
        raise ValueError("不支持的栏目配置版本")
    for name in ("name", "matrix", "type", "audience", "promise", "notes"):
        value = data.get(name, "")
        if not isinstance(value, str) or len(value) > 2000:
            raise ValueError(f"{name} 应为文字，最多 2000 字")
    if not data.get("name", "").strip() or not data.get("promise", "").strip():
        raise ValueError("栏目需要名称和交付目标")
    matrix = matrix_map.get(data.get("matrix"))
    if matrix is None:
        raise ValueError("所属领域矩阵不存在")
    if matrix.archived or matrix.content.get("role", "domain") != "domain":
        raise ValueError("栏目需要属于未归档的领域矩阵")
    if data.get("type") not in type_map:
        raise ValueError("请选择已登记的共享栏目类型")
    ids = data.get("production_ids", [])
    if not isinstance(ids, list) or not all(isinstance(item, str) and KEY.fullmatch(item) for item in ids):
        raise ValueError("production_ids 应为生产配置标识列表")
    if len(set(ids)) != len(ids):
        raise ValueError("生产绑定不能重复")
    mode = data.get("render_inheritance", "source_compatible")
    if mode not in {"source_compatible", "content_matrix"}:
        raise ValueError("制作继承方式应为 source_compatible 或 content_matrix")
    status = data.get("status", "configured")
    if status not in {"configured", "planned"}:
        raise ValueError("栏目状态应为 configured 或 planned")
    presentation = data.get("presentation", {})
    if not isinstance(presentation, dict) or set(presentation) - {"strategy", "preferred_visuals"}:
        raise ValueError("画面偏好支持 strategy 和 preferred_visuals")
    if not isinstance(presentation.get("strategy", ""), str):
        raise ValueError("画面策略应为文字")
    visuals = presentation.get("preferred_visuals", [])
    if not isinstance(visuals, list) or not all(isinstance(v, str) and v.strip() for v in visuals):
        raise ValueError("推荐画面应为列表")
    return ContentColumn(key=key, name=data["name"].strip(), matrix=matrix.key, type=data["type"],
                         audience=data.get("audience", "").strip(), promise=data["promise"].strip(),
                         production_ids=tuple(ids), render_inheritance=mode, render=validate_render(data.get("render")),
                         presentation=copy.deepcopy(presentation), status=status, notes=data.get("notes", ""), revision=revision)


def load_all(config_dir: str, *, configs: dict | None = None, matrix_map: dict | None = None) -> dict[str, ContentColumn]:
    matrix_map = matrices.load_all(config_dir) if matrix_map is None else matrix_map
    type_map = types(config_dir)
    result = {}
    for path in sorted((Path(config_dir) / DIR).glob("*.yaml")):
        if not KEY.fullmatch(path.stem):
            continue
        try:
            raw = path.read_bytes()
            revision = hashlib.sha256(raw).hexdigest()
            with path.open(encoding="utf-8") as stream:
                data = load_yaml(stream)
            result[path.stem] = _build(path.stem, data, matrix_map, type_map, revision)
        except (OSError, ValueError, TypeError, yaml.YAMLError) as exc:
            result[path.stem] = ContentColumn(key=path.stem, name=path.stem, errors=(str(exc),))
    references: dict[str, list[str]] = {}
    for column in result.values():
        for pid in column.production_ids:
            references.setdefault(pid, []).append(column.key)
    for key, column in list(result.items()):
        errors = list(column.errors)
        for pid in column.production_ids:
            if len(references[pid]) > 1:
                errors.append(f"生产配置 {pid} 被多个栏目引用：{'、'.join(references[pid])}")
            if configs is not None and pid not in configs:
                errors.append(f"生产配置不存在：{pid}")
        if errors:
            result[key] = replace(column, errors=tuple(errors))
    return result


def for_production(config_dir: str, production_id: str, *, configs: dict | None = None) -> ContentColumn | None:
    return next((c for c in load_all(config_dir, configs=configs).values()
                 if c.usable and production_id in c.production_ids), None)


def snapshot(config_dir: str, production_id: str, job: str) -> dict:
    column = for_production(config_dir, production_id)
    if column is None:
        return {}
    source = matrices.of_job(job, config_dir)
    return {"column": column.key, "column_name": column.name, "type": column.type, "matrix": column.matrix,
            "source_matrix": source.key if source else "", "job": job, "revision": column.revision,
            "audience": column.audience, "promise": column.promise, "notes": column.notes,
            "type_definition": copy.deepcopy(types(config_dir).get(column.type, {})),
            "render_inheritance": column.render_inheritance, "render": copy.deepcopy(column.render),
            "presentation": copy.deepcopy(column.presentation)}


def apply_script_context(recipe, context: dict):
    """Reuse the writer, adding a shared editorial goal without changing its report input or topic kind."""
    if not context.get("column"):
        return recipe
    definition = context.get("type_definition") or {}
    structure = " → ".join(definition.get("structure") or [])
    direction = f"栏目：{context.get('column_name', '')}。持续交付：{context.get('promise', '')}。"
    if structure:
        direction += f"\n本期可围绕 {structure} 组织讲述，按素材自然展开。"
    if context.get("type") == "comparison":
        direction += "\n把材料中的对象放到相同维度比较，讲清关键差异和适用情形；对象数量沿用材料，不限定为两个。"
    elif context.get("type") == "tutorial":
        direction += "\n围绕一个明确任务，按操作顺序讲清步骤和可看到的结果。"
    notes = (context.get("notes") or "").strip()
    if notes:
        direction += f"\n栏目创作方向：{notes}"
    # A concrete business-column brief owns the creative goal. The reused
    # recipe continues to own its input mode, writer and output contract.
    return replace(recipe, script_style=direction if notes else recipe.script_style + "\n" + direction,
                   audience=context.get("audience") or recipe.audience)


def render_direction(context: dict) -> str:
    if not context.get("column"):
        return ""
    visuals = (context.get("presentation") or {}).get("preferred_visuals", [])
    strategy = ((context.get("presentation") or {}).get("strategy") or "").strip()
    return (f"栏目：{context.get('column_name', '')}。讲解目标：{context.get('promise', '')}。"
            + (f"\n优先按段落组合{'、'.join(visuals)}，普通说明保持简洁，重点随旁白展开。" if visuals else "")
            + (f"\n画面讲述方向：{strategy}" if strategy else ""))


def render_settings(config_dir: str, source_matrix, context: dict, overrides: dict) -> dict:
    result = copy.deepcopy(source_matrix.render if source_matrix else {})
    if context.get("render_inheritance") == "content_matrix":
        content = matrices.load_all(config_dir).get(context.get("matrix", ""))
        if content and (not source_matrix or content.key != source_matrix.key):
            result = inherit(result, content.render)
    return inherit(inherit(result, context.get("render", {})), overrides)


def save(config_dir: str, key: str, data: dict, *, expected_revision: str | None = None) -> ContentColumn:
    if not KEY.fullmatch(key):
        raise ValueError("栏目标识应为小写字母、数字、下划线或连字符")
    folder = Path(config_dir) / DIR
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{key}.yaml"
    # One catalog lock also prevents two concurrent edits claiming the same production binding.
    with file_lock(str(folder / ".catalog")):
        if expected_revision is not None:
            current = hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else ""
            if current != expected_revision:
                raise FileExistsError("栏目已变化，请刷新后保存")
        column = _build(key, data, matrices.load_all(config_dir), types(config_dir))
        for other in load_all(config_dir).values():
            if other.key != key and set(other.production_ids) & set(column.production_ids):
                raise ValueError(f"生产绑定已属于栏目：{other.name}")
        for pid in column.production_ids:
            if not (Path(config_dir) / "production" / f"{pid}.yaml").is_file():
                raise ValueError(f"生产配置不存在：{pid}")
        atomic_write(str(path), yaml.safe_dump(column.data(), allow_unicode=True, sort_keys=False))
    return load_all(config_dir)[key]
