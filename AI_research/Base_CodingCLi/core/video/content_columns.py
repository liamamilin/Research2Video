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
          "render_inheritance", "render", "presentation", "status", "notes", "content_mode", "greeting"}


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
    content_mode: str = "episode"
    greeting: str = ""
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
                "presentation": copy.deepcopy(self.presentation), "status": self.status, "notes": self.notes,
                "content_mode": self.content_mode, **({"greeting": self.greeting} if self.greeting else {})}


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
    for name in ("name", "matrix", "type", "audience", "promise", "notes", "greeting"):
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
    content_mode = data.get("content_mode", "episode")
    if content_mode not in {"episode", "individual"}:
        raise ValueError("内容组织方式应为 episode（整期节目）或 individual（独立单条）")
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
                         presentation=copy.deepcopy(presentation), status=status, notes=data.get("notes", ""), content_mode=content_mode,
                         greeting=data.get("greeting", "").strip(), revision=revision)


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
    owner = matrices.load_all(config_dir).get(column.matrix) if column.matrix else None
    premise = str(((owner.content if owner else {}) or {}).get("premise") or "").strip()
    return {**({"premise": premise} if premise else {}), "column": column.key, "column_name": column.name, "type": column.type, "matrix": column.matrix,
            "source_matrix": source.key if source else "", "job": job, "revision": column.revision,
            "audience": column.audience, "promise": column.promise, "notes": column.notes,
            "content_mode": column.content_mode, "greeting": column.greeting,
            "type_definition": copy.deepcopy(types(config_dir).get(column.type, {})),
            "render_inheritance": column.render_inheritance, "render": copy.deepcopy(column.render),
            "presentation": copy.deepcopy(column.presentation)}


def apply_script_context(recipe, context: dict):
    """Reuse the writer, adding a shared editorial goal without changing its report input or topic kind."""
    if not context.get("column"):
        return recipe
    definition = context.get("type_definition") or {}
    structure = " → ".join(definition.get("structure") or [])
    programme = context.get("content_mode") == "episode"
    scope = "每项独立内容" if programme else "本期"
    direction = f"栏目：{context.get('column_name', '')}。持续交付：{context.get('promise', '')}。"
    if structure:
        direction += f"\n{scope}可围绕 {structure} 组织讲述，按素材自然展开。"
    if context.get("type") == "comparison":
        direction += (
            "\n不要写一碗水端平的优缺点列表，也不要按对象逐个报菜名式念参数。"
            "按「核心分水岭 → 什么人直接选各自选项（用具体任务、用量或门槛画人群，不用新手/专业人士等空标签）"
            " → 各自最隐蔽的坑（只挖材料里已有、第一眼看不出的隐藏成本、限额、静默限制或翻车条件，不强求对称、不编造）」组织讲述；"
            "若核心分水岭本身就是人群准入边界，可直接合并为一节；前面已说清的准入条件或数字后面直接用结论，严禁在分水岭、谁选谁、拍板三处把同一组条件换句式重复念两三遍；"
            "对象数量沿用材料（都要给清定位，别漏掉日常主力或入门项）；口播只说人耳朵一听就懂的话，不念长串价格数列或路径；小节标题贴合本期具体问题来起，不要照抄框架模板词。"
        )
    elif context.get("type") == "news":
        direction += (
            "\n不要写成通讯社通稿或新闻联播式播报，也不要在开头念“这条讲三件事”的提纲。"
            f"{scope}第一句直接亮出最反直觉的变化、冲突、现场切口或后果；讲清真正发生了什么、关键边界或转折是什么（材料有基线缺失或前提限制就顺手点破），"
            "以及对观众现实判断、当下用法或理解框架的具体影响（若是时效快讯，进一步点清咬到谁的利益，并给出具体可查动作或接下来盯紧的唯一关键信号）。"
            "口播只说人耳朵一听就懂的话，严禁念具体 IP 地址、邮箱、Git PR/分支路径、长串隐藏文件名（如 .npmrc/.netrc）或大写环境变量全名，用一句大白话概括机制，具体文件名和命令留给画面；"
            "引用材料数字时保留原小数位，不自行四舍五入抹掉小数；同一事实或数字全篇只在一个位置讲透，前后段落绝不车轱辘重复。"
            + ("\n整期合集三条铁律：标题里写的最重磅新闻必须排在正文第一个 item；"
               "opening 只用 1 到 2 句点出共同张力或直接切入，绝不提前把第一节的数字和结论复述一遍；"
               "严格控制单条篇幅——主打的第一条控制在 3 段以内，其余每条控制在 2 段左右，严禁把合集扩写成近万字的长篇报告。" if programme else "")
            + "小节标题贴合具体内容来起，不要照抄框架模板词。"
        )
    elif context.get("type") == "explainer":
        direction += (
            "\n不要写成教科书词条或百科定义汇编，不要在开头念提纲，也不要在同一段里连续堆砌五六个底层专业名词。"
            f"{scope}从一个具体反常识现象、看得见的账单/动作变化或真实工作卡点直接切入；"
            "每节只推进一步核心因果（看见的具体现象或动作 → 背后真正起作用的一层机制、激励或约束 → 带来的实际后果），"
            "抽象术语必须在它解释眼前具体例子的那一刻才出场，并用日常场景或具体对象锚定。"
            "口播不替画面逐行背诵冗长算账表格、公式或代码路径，也不要直接念带下划线或驼峰的英文配置键名/API参数名（如 completion_window、attributionProject、billing_group_tag、max_tokens），改用中文自然说法（如“后台完成窗口参数”、“项目归属标签”、“最大输出上限”），把英文参数名留给画面；"
            "引用材料数字时保留原小数位、不自行四舍五入抹掉小数；同一事实、数字或机制解释全篇只在一个位置讲透，前后绝不车轱辘重复。"
            + ("\n整期合集四条铁律：若包含多个独立议题或案例，标题里最重磅的主菜必须排在正文第一个 item；"
               "opening 只用 1 到 2 句抛出最反直觉的反差或追问，绝不提前把第一节的数字、拆解过程和结论剧透复述一遍；"
               "每个 item 小节控制在 3 到 5 段以内（若主打案例同时包含主机制和另一条独立支线，拆成两个有独立小标题的 item，不要把八九段塞进同一小节）；"
               "不同案例之间没有真实共同机制时各自利落讲透，严禁用“这标志着行业进入新阶段”之类的空洞宏大叙事强行串场。" if programme else "")
            + "收尾落回开头的问题或情境，留给观众一个下次碰到同类现象能直接套用的判断标尺，或手头能立刻借用的一步动作；小节标题贴合具体内容来起，不要照抄框架模板词。"
        )
    elif context.get("type") == "tutorial":
        direction += (
            f"\n{scope}围绕一个明确任务或高频症状，按一条干净的主操作路径顺着走到底（准备条件 → 关键动作 → 每一步做完后屏幕上能看到的验证信号）。"
            "不要把官方帮助文档里的边缘例外条款、各国本地支付枚举或企业后台细则全堆进主线，只留真正会让普通用户踩雷、重复扣费或失败的 1 到 2 个核心避坑点。"
            "口播用自然中文说清点哪儿、看什么信号、改完变成什么样，严禁像机器人一样连续念四层英文菜单路径、长串快捷键组合或四五个英文状态词枚举（精准英文按钮名、路径和命令留给画面）；"
            "排障和订阅类内容必须按代价从小到大分层，每层只放 1 到 2 个动作并给出清晰的红绿灯分岔（看到什么信号就停/等，看到什么再进下一步），绝不把六七个步骤塞进同一段一口气念完。"
            "opening 只用 1 到 2 句点出做完能拿到什么或先避开什么冤枉路，绝不提前把全部步骤复述一遍；同一入口、前提或范围边界全篇只说一次，前后绝不车轱辘重复；引用数字保留原小数位；单节控制在 3 到 5 段以内；收尾留一句可立刻照做的动作口诀。"
        )
    premise = (context.get("premise") or "").strip()
    if premise:  # what the matrix's viewers live with (matrix content.premise), before any column direction
        direction += f"\n观众前提：{premise}"
    notes = (context.get("notes") or "").strip()
    if notes:
        if programme:
            direction += "\n以下是可参考的创作方向。各项独立内容可采用适合自己的例子与讲法；同一问题的章节以连贯、好理解为目标，安排由作者结合材料决定。"
        direction += f"\n{'单项内容创作方向' if programme else '栏目创作方向'}：{notes}"
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
