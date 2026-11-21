"""Four-stage OpenCode research pipeline: Discover -> Soft Filter -> Per-Topic Gather -> Assemble.

Opt-in per Job via ``research_mode: opencode_topic_v1`` (or globally via
``gather.opencode_cli.pipeline_mode: opencode_topic_v1``).

Pipeline stages:
1. Topic Discovery (OpenCode): Job config + window + reported_events + lightweight
   declared-source index -> structured candidate topics with ``seed_urls`` and
   ``research_questions``.
2. Soft Filter (no quantity cap): deduplicate, merge micro-topics sharing
   ``cluster_key``, enforce verifiability and channel relevance.
3. Per-Topic Deep Gather (OpenCode, bounded concurrency + per-topic cache):
   each surviving topic runs an independent focused research pass around its
   ``seed_urls`` and ``research_questions``.
4. Report Assembly: renders the standard Markdown report format (``### 1.N`` +
   ``### 来源清单``) so downstream manuscript/video/citation modules require zero
   changes.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
import logging
import re
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from . import opencode_cli
from .errors import CancelledError
from .fileio import atomic_write
from .provenance import extract_urls, normalize_url

logger = logging.getLogger(__name__)

MODE_NAME = "opencode_topic_v1"
SINGLE_MODE_NAME = "opencode_single_topic"
VERSION = 1


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

class TopicCandidate(BaseModel):
    title: str = Field(min_length=2)
    why_now: str = ""
    audience: str = ""
    promise: str = ""
    seed_urls: list[str] = Field(default_factory=list)
    research_questions: list[str] = Field(default_factory=list)
    cluster_key: str = ""
    is_update: bool = False
    update_note: str = ""
    series: str = ""
    topic_kind: str = ""


class TopicDiscoveryResult(BaseModel):
    candidates: list[TopicCandidate] = Field(default_factory=list)
    notes: str = ""


class MergeGroup(BaseModel):
    indices: list[int] = Field(min_length=2)
    merged_title: str = ""
    reason: str = ""


class DroppedTopic(BaseModel):
    index: int
    reason: str = ""


class SoftFilterDecision(BaseModel):
    keep_indices: list[int] = Field(default_factory=list)
    merge_groups: list[MergeGroup] = Field(default_factory=list)
    dropped: list[DroppedTopic] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

DISCOVERY_PROMPT = """你是一个负责上游选题发现的研究助手。当前阶段只做【快速广度选题生成 + 种子链接定位】，不要展开写长报告，不要写入任何本地文件。

工作方式：
1. 先阅读下方提供的「栏目配置」「时间范围」「近期已报来源」和「声明源轻量索引」。
2. **速度与工具约束**：优先直接从「声明源轻量索引」中提炼选题；仅当声明源为空或明显缺少关键线索时，最多执行 2–3 次 `websearch` 补线索。**严禁在选题阶段调用 `webfetch` 逐篇读全文**（正文留到下一阶段单题深挖再读），也**严禁调用文件写入工具创建任何本地文件**。
3. **不要为了凑数或限流而人为砍题**：只要在栏目范围内且有实质信息价值，就列出来；没有合适新题时返回空数组。
4. 同一产品、同一事件的零散小改动请给相同的 `cluster_key`（例如 `claude-code-cli`、`openai-pricing`），便于下一步合并。
5. 每条选题必须给出 2–3 个具体的 `research_questions`，告诉下一阶段的深挖助手“必须查清哪些事实、对照、步骤、数据或背景”。
6. `seed_urls` 只填写真实存在的 `https://` 或 `http://` 链接（优先来自声明源或你检索到的官方/一手页面），禁止编造 URL。

最后必须输出一个严格的 JSON 代码块（不要在 JSON 内写注释）：
```json
{
  "candidates": [
    {
      "title": "具体选题标题（明确主体与问题/变化）",
      "why_now": "为什么此刻值得写，或时间窗口内的具体新变化",
      "audience": "适合哪类观众及适用范围",
      "promise": "一句话说明读者看完能弄清或掌握什么",
      "seed_urls": ["https://..."],
      "research_questions": ["具体需要深挖查清的问题1", "问题2"],
      "cluster_key": "同类微选题归并键（可选，无则留空）",
      "is_update": false,
      "update_note": "若是旧题新进展，写清只需深挖的新增部分",
      "series": "产品或主题系列（可选）",
      "topic_kind": "题材分类（可选）"
    }
  ],
  "notes": "本轮检索范围与主要观察（可选）"
}
```

## 本轮选题任务
{objective}
"""

SOFT_FILTER_PROMPT = """你负责上游选题的【软筛选】，目的是挡住重复、跑偏和不可验证的噪声，把真正值得深挖的选题放行。

核心原则：
- **禁止设数量上限**：不要因为选题多就硬砍！只要符合栏目定位、有实质增量、非重复，全部保留。
- **跨轮去重**：若选题与「近期已报来源」高度重复且无实质新进展，放入 `dropped` 并注明原因。
- **同类合并**：若多个候选本质上是同一产品/事件的零散小更新，放入 `merge_groups` 合并为一个综合选题。
- **可验证底线**：纯情绪吐槽、空泛公关、无实质事实支撑的放入 `dropped`。
- 每个候选编号（0-based index）必须且只能归属到 `keep_indices`、`merge_groups` 或 `dropped` 之一。

只输出 JSON：
{"keep_indices":[0,2],"merge_groups":[{"indices":[1,3],"merged_title":"合并后的标题","reason":"合并理由"}],"dropped":[{"index":4,"reason":"剔除理由"}]}
"""

PER_TOPIC_GATHER_PROMPT = """你正在执行单个选题的【定向深挖调研】，为下游报告整理结构化证据包。不要写成口播稿。

工作方式：
1. 先用 `webfetch` 读透下方提供的 `seed_urls`，不要只看标题猜内容。
2. 逐条回答 `research_questions`：如果种子链接缺少关键数字、对照基准、操作步骤、原始档案、适用边界或社区实测反馈，主动用 `websearch` + `webfetch` 定向补齐一手资料；围绕这几个核心问题查清即可，控制在 5–8 次核心工具调用内，不要无限展开无关链接导致超时。
3. 只写实际读到或检索到的内容；查不到的缺口如实写进「口径与信息缺口」，绝对不要编造数字、案例或 URL。
4. 关键事实、数字、步骤和案例旁边直接附上对应原始链接。
5. **严禁调用文件写入工具创建或修改任何本地文件（不要保存到 `output/` 或任何 `.md` 文件）！所有调研正文必须直接输出在你的最终回复文本中。**
6. **语言规范**：正文统一使用自然、流畅的中文表述；关键专有名词、配置键、命令或重要原话可保留英文，但必须附带准确中文释义，避免生硬的中英夹杂。

输出使用 Markdown，固定包含以下 4 个二级标题（不要使用一级标题 `# `，正文内小节只用 `#### `，**材料条目下的每条来源必须严格使用 `### [条目标题](https://完整链接)`**）：

## 核心结论与背景
用 2–4 段写清核心判断、背景与适用范围。

## 关键证据与展开
围绕研究问题逐项展开（可用 `#### 问题标题` 分节），保留具体数字、对照、操作输入输出、过程与成立条件，重要事实旁贴原始链接。

## 材料条目
### [条目标题](https://example.com/article)
- 来源角色与发布时间（如：官方公告 / 文档 / 论文 / 实测 / 档案）
- 核心要点与可引用原句

## 口径与信息缺口
- 一手/官方已确认：...
- 第三方实测/社区反馈：...
- 尚未核实或缺失项：...

## 本次单题深挖任务
{task}
"""


# ---------------------------------------------------------------------------
# Mode & settings helpers
# ---------------------------------------------------------------------------

def single_topic_enabled(job: dict, config: dict | None = None) -> bool:
    """Whether this Job should run single-topic OpenCode deep research directly."""
    if job.get("opencode_gather") is False:
        return False
    mode = str(job.get("research_mode") or "").strip().lower()
    if mode != SINGLE_MODE_NAME:
        return False
    cfg = config or {}
    return bool(cfg.get("enabled", True))


def enabled(job: dict, config: dict | None = None) -> bool:
    """Whether this Job should run the 4-stage OpenCode topic pipeline."""
    if job.get("opencode_gather") is False:
        return False
    mode = str(job.get("research_mode") or "").strip().lower()
    if mode in {"legacy", "default", "off", "false", SINGLE_MODE_NAME}:
        return False
    if mode == MODE_NAME:
        return True
    # Synthesis stages consuming local {upstream_reports} and offline knowledge Jobs
    # stay on their dedicated pipelines unless explicitly opted in via research_mode.
    if "{upstream_reports}" in str(job.get("prompt") or ""):
        return False
    from . import knowledge_job
    if knowledge_job.enabled(job):
        return False
    cfg = config or {}
    if not cfg.get("enabled", True):
        return False
    return str(cfg.get("pipeline_mode") or "").strip().lower() == MODE_NAME


def _check_cancel(cancel_token) -> None:
    if cancel_token is not None and cancel_token.is_set():
        raise CancelledError("OpenCode 四段式管线已取消")


def _clean_url(url: str) -> str:
    return str(url or "").strip().rstrip(".,;)]}'\"")


def _valid_urls(urls: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in urls or []:
        url = _clean_url(raw)
        if not url.startswith(("http://", "https://")):
            continue
        key = normalize_url(url) or url
        if key in seen:
            continue
        seen.add(key)
        out.append(url)
    return out


# ---------------------------------------------------------------------------
# Stage 0: Lightweight source index
# ---------------------------------------------------------------------------

def build_source_index(source_block: str, *, max_items: int = 60, excerpt_chars: int = 450) -> list[dict]:
    """Convert declared-source markdown block into compact title/url/excerpt entries."""
    if not (source_block or "").strip():
        return []
    from .video.audience_strategy import packets

    items = packets(source_block)
    index: list[dict] = []
    for item in items[:max_items]:
        url = _clean_url(item.get("url") or "")
        body = re.sub(r"^### \[.*?\]\(.*?\)\s*", "", str(item.get("text") or ""), count=1).strip()
        body = re.sub(r"\s+", " ", body)
        index.append({
            "id": item.get("id") or f"S{len(index) + 1}",
            "title": str(item.get("title") or "").strip()[:180],
            "url": url,
            "excerpt": body[:excerpt_chars],
        })
    return index


# ---------------------------------------------------------------------------
# Stage 1: Topic Discovery
# ---------------------------------------------------------------------------

def _window_line(window: dict | None) -> str:
    if not isinstance(window, dict) or not window.get("start") or not window.get("end"):
        return ""
    tz = window.get("timezone") or "Asia/Shanghai"
    period = f"{window['start']} 至 {window['end']}（{tz}）"
    if window.get("mode") != "since_last_collection" and window.get("days"):
        period = f"近 {window['days']} 天：{period}"
    return f"时间范围：{period}（优先发掘该窗口内的新进展、更新或变化；更早内容仅作必要对照背景）"


def _discovery_objective(job: dict, source_index: list[dict], *, reported_events: str = "",
                         today: str = "", knowledge: bool = False, raw_knowledge_block: str = "") -> str:
    from .prompt_scope import editorial_brief

    name = str(job.get("name") or job.get("_file") or "研究")
    domain = str(job.get("domain") or "").strip()
    audience = str(job.get("audience_channel") or "").strip()
    kw = job.get("keywords")
    keywords = "、".join(str(x).strip() for x in kw if str(x).strip()) if isinstance(kw, list) else str(kw or "").strip()
    run_date = (today or "").strip() or time.strftime("%Y-%m-%d")
    criteria = str(job.get("topic_criteria") or "").strip()
    brief = editorial_brief(str(job.get("prompt") or ""))[:12000]

    lines = [
        f"名称：{name}",
        f"当前日期：{run_date}",
        f"领域：{domain or '（未声明）'}",
        f"关键词：{keywords or '（请从栏目要求提炼）'}",
    ]
    if audience:
        lines.append(f"受众/频道：{audience}")
    if knowledge:
        lines.append("任务类型：常青知识/专题（无需强行绑定近几天新闻，优先选值得深讲且未重复的主题）")
    else:
        wline = _window_line(job.get("_collection_window"))
        if wline:
            lines.append(wline)
    if criteria:
        lines.append("选题标准（topic_criteria）：")
        lines.append(criteria[:8000])
    if brief:
        lines.append("栏目研究范围（参考）：")
        lines.append(brief[:8000])
    if (reported_events or "").strip():
        lines.append("近期已报来源（若无实质新进展请勿重复；有实质更新请标记 is_update=true）：")
        lines.append(reported_events.strip()[:6000])
    if knowledge and raw_knowledge_block.strip():
        lines.append("历史已写与灵感线索：")
        lines.append(raw_knowledge_block.strip()[:12000])
    elif source_index:
        lines.append(f"声明源轻量索引（共 {len(source_index)} 条，可直接使用其中 URL 作为 seed_urls，也可补充公开一手链接）：")
        lines.append(json.dumps(source_index, ensure_ascii=False))
    else:
        lines.append("声明源轻量索引：（本轮无预设声明源，请直接通过 websearch 发现候选与一手种子链接）")
    return "\n".join(lines)


def _extract_json_payload(text: str) -> Any:
    raw = (text or "").strip()
    if not raw:
        raise ValueError("empty discovery output")
    fenced = re.findall(r"```(?:json)?\s*([\s\S]*?)\s*```", raw, flags=re.I)
    candidates = list(reversed(fenced)) + [raw]
    for blob in candidates:
        s = blob.strip()
        if not s:
            continue
        try:
            return json.loads(s)
        except json.JSONDecodeError:
            # Try outermost JSON object or array
            start_obj, end_obj = s.find("{"), s.rfind("}")
            if start_obj != -1 and end_obj > start_obj:
                try:
                    return json.loads(s[start_obj:end_obj + 1])
                except json.JSONDecodeError:
                    pass
            start_arr, end_arr = s.find("["), s.rfind("]")
            if start_arr != -1 and end_arr > start_arr:
                try:
                    return json.loads(s[start_arr:end_arr + 1])
                except json.JSONDecodeError:
                    pass
    raise ValueError("could not parse JSON from OpenCode discovery output")


def parse_discovery_output(text: str) -> TopicDiscoveryResult:
    data = _extract_json_payload(text)
    if isinstance(data, list):
        data = {"candidates": data}
    if not isinstance(data, dict):
        raise ValueError("discovery JSON root must be object or list")
    raw_list = data.get("candidates")
    if not isinstance(raw_list, list):
        raw_list = []
    cleaned: list[TopicCandidate] = []
    for row in raw_list:
        if not isinstance(row, dict):
            continue
        title = str(row.get("title") or "").strip()
        if len(title) < 2:
            continue
        seed_urls = _valid_urls(row.get("seed_urls") if isinstance(row.get("seed_urls"), list) else [])
        rqs = [str(q).strip() for q in (row.get("research_questions") or []) if str(q).strip()][:5]
        cleaned.append(TopicCandidate(
            title=title,
            why_now=str(row.get("why_now") or "").strip(),
            audience=str(row.get("audience") or "").strip(),
            promise=str(row.get("promise") or "").strip(),
            seed_urls=seed_urls,
            research_questions=rqs,
            cluster_key=str(row.get("cluster_key") or "").strip().lower(),
            is_update=bool(row.get("is_update")),
            update_note=str(row.get("update_note") or "").strip(),
            series=str(row.get("series") or "").strip(),
            topic_kind=str(row.get("topic_kind") or "").strip(),
        ))
    return TopicDiscoveryResult(candidates=cleaned, notes=str(data.get("notes") or "").strip())


def discover_topics(engine, job: dict, source_index: list[dict], *, config: dict,
                    reported_events: str = "", today: str = "", knowledge: bool = False,
                    raw_knowledge_block: str = "", cancel_token=None) -> tuple[TopicDiscoveryResult, dict]:
    _check_cancel(cancel_token)
    objective = _discovery_objective(
        job, source_index, reported_events=reported_events, today=today,
        knowledge=knowledge, raw_knowledge_block=raw_knowledge_block,
    )
    prompt = DISCOVERY_PROMPT.replace("{objective}", objective)
    timeout = int(config.get("discovery_timeout_seconds") or config.get("gather_timeout_seconds") or 240)
    started = time.time()
    text, meta = opencode_cli.run(prompt, config=config, timeout=timeout, cancel_token=cancel_token)
    receipt: dict[str, Any] = {
        "seconds": round(time.time() - started, 1),
        "raw_chars": len(text),
        **meta,
    }
    try:
        result = parse_discovery_output(text)
    except Exception as parse_exc:
        logger.warning("OpenCode discovery JSON parse failed (%s); falling back to LLM extractor", parse_exc)
        from .llm import LLMClient, LLMConfig
        from .video.adapt.common import call_json
        client = LLMClient(LLMConfig.from_system(engine.sys, "ai"), cancel_token=cancel_token)
        try:
            result = call_json(
                client,
                "把下面选题发现草稿提取为标准 TopicDiscoveryResult JSON，只保留原文中出现的真实标题与 URL，不得编造。",
                text[:60000],
                TopicDiscoveryResult,
                temperature=0.1,
            )
            for item in result.candidates:
                item.seed_urls = _valid_urls(item.seed_urls)
            receipt["parse_fallback"] = "llm_json"
            receipt["fallback_usage"] = dict(client.usage)
        finally:
            client.close()
    receipt["candidate_count"] = len(result.candidates)
    return result, receipt


# ---------------------------------------------------------------------------
# Stage 2: Soft Filter (no quantity cap)
# ---------------------------------------------------------------------------

def _merge_candidates(group: list[TopicCandidate], merged_title: str = "") -> TopicCandidate:
    primary = group[0]
    title = merged_title.strip() or primary.title
    urls = _valid_urls([u for c in group for u in c.seed_urls])
    rqs: list[str] = []
    for c in group:
        for q in c.research_questions:
            if q not in rqs:
                rqs.append(q)
    why_parts = list(dict.fromkeys(c.why_now for c in group if c.why_now))
    promise_parts = list(dict.fromkeys(c.promise for c in group if c.promise))
    return TopicCandidate(
        title=title,
        why_now="；".join(why_parts)[:600],
        audience=primary.audience or next((c.audience for c in group if c.audience), ""),
        promise=primary.promise or ("；".join(promise_parts)[:300]),
        seed_urls=urls,
        research_questions=rqs[:5],
        cluster_key=primary.cluster_key,
        is_update=any(c.is_update for c in group),
        update_note="；".join(dict.fromkeys(c.update_note for c in group if c.update_note)),
        series=primary.series or next((c.series for c in group if c.series), ""),
        topic_kind=primary.topic_kind or next((c.topic_kind for c in group if c.topic_kind), ""),
    )


def soft_filter(candidates: list[TopicCandidate], *, job: dict, reported_events: str = "",
                llm_config=None, use_llm: bool = True, knowledge: bool = False,
                cancel_token=None) -> tuple[list[TopicCandidate], dict]:
    """Filter duplicates/noise and merge micro-topics without imposing any count cap."""
    _check_cancel(cancel_token)
    receipt: dict[str, Any] = {
        "input_count": len(candidates),
        "merged": [],
        "dropped": [],
        "llm_used": False,
    }
    if not candidates:
        receipt["output_count"] = 0
        return [], receipt

    # Step 2a: deterministic verifiability check + cluster_key merge + exact URL dedup
    pre_valid: list[TopicCandidate] = []
    by_cluster: dict[str, list[TopicCandidate]] = {}
    seen_title_keys: set[str] = set()
    for c in candidates:
        tkey = re.sub(r"\s+", "", c.title.lower())
        if tkey in seen_title_keys:
            receipt["dropped"].append({"title": c.title, "reason": "duplicate_title"})
            continue
        seen_title_keys.add(tkey)
        if not knowledge and not c.seed_urls:
            receipt["dropped"].append({"title": c.title, "reason": "missing_seed_urls"})
            continue
        if c.cluster_key:
            by_cluster.setdefault(c.cluster_key, []).append(c)
        else:
            pre_valid.append(c)

    clustered: list[TopicCandidate] = list(pre_valid)
    for ckey, group in by_cluster.items():
        if len(group) == 1:
            clustered.append(group[0])
        else:
            merged = _merge_candidates(group)
            clustered.append(merged)
            receipt["merged"].append({
                "cluster_key": ckey,
                "titles": [g.title for g in group],
                "merged_title": merged.title,
                "reason": "shared_cluster_key",
            })

    if not clustered or not use_llm or llm_config is None:
        receipt["output_count"] = len(clustered)
        return clustered, receipt

    # Step 2b: lightweight LLM soft-filter (no quantity cap; graceful fallback on failure)
    from .llm import LLMClient
    from .prompt_scope import editorial_brief
    from .video.adapt.common import call_json

    criteria = str(job.get("topic_criteria") or "").strip() or editorial_brief(str(job.get("prompt") or ""))[:4000]
    payload = {
        "job_name": job.get("name") or job.get("_file") or "",
        "domain": job.get("domain") or "",
        "criteria": criteria,
        "reported_events": (reported_events or "")[:4000],
        "candidates": [
            {"index": i, **c.model_dump(exclude_defaults=False)}
            for i, c in enumerate(clustered)
        ],
    }
    client = LLMClient(llm_config, cancel_token=cancel_token)
    try:
        decision = call_json(
            client,
            SOFT_FILTER_PROMPT,
            json.dumps(payload, ensure_ascii=False),
            SoftFilterDecision,
            temperature=0.1,
        )
        receipt["llm_used"] = True
        receipt["usage"] = dict(client.usage)
    except CancelledError:
        raise
    except Exception as exc:
        logger.warning("Soft filter LLM step skipped (%s); keeping deterministic list", exc)
        receipt["llm_error"] = str(exc)[:200]
        receipt["output_count"] = len(clustered)
        return clustered, receipt
    finally:
        client.close()

    n = len(clustered)
    used_indices: set[int] = set()
    final_topics: list[TopicCandidate] = []

    for grp in decision.merge_groups:
        idxs = [i for i in grp.indices if 0 <= i < n and i not in used_indices]
        if len(idxs) >= 2:
            used_indices.update(idxs)
            merged = _merge_candidates([clustered[i] for i in idxs], grp.merged_title)
            final_topics.append(merged)
            receipt["merged"].append({
                "titles": [clustered[i].title for i in idxs],
                "merged_title": merged.title,
                "reason": grp.reason or "llm_merge",
            })

    for idx in decision.keep_indices:
        if 0 <= idx < n and idx not in used_indices:
            used_indices.add(idx)
            final_topics.append(clustered[idx])

    dropped_set: set[int] = set()
    for d in decision.dropped:
        if 0 <= d.index < n and d.index not in used_indices:
            dropped_set.add(d.index)
            receipt["dropped"].append({
                "title": clustered[d.index].title,
                "reason": d.reason or "llm_dropped",
            })

    # Safety net: any index omitted by the model is kept rather than silently lost.
    for i in range(n):
        if i not in used_indices and i not in dropped_set:
            final_topics.append(clustered[i])

    receipt["output_count"] = len(final_topics)
    return final_topics, receipt


# ---------------------------------------------------------------------------
# Stage 3: Per-Topic Deep Gather (concurrent + cached)
# ---------------------------------------------------------------------------

def _topic_hash(job_key: str, run_date: str, topic: TopicCandidate, *, extra: str = "") -> str:
    payload: dict[str, Any] = {
        "job": job_key,
        "date": run_date,
        "title": topic.title,
        "urls": sorted(topic.seed_urls),
    }
    if extra:
        payload["extra"] = hashlib.sha256(extra.encode("utf-8")).hexdigest()[:16]
    seed = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


def _per_topic_task(job: dict, topic: TopicCandidate, *, today: str) -> str:
    from .prompt_scope import editorial_brief

    reqs = str(job.get("gather_requirements") or "").strip()
    if not reqs:
        reqs = editorial_brief(str(job.get("prompt") or ""))[:6000]
    lines = [
        f"所属栏目：{job.get('name') or job.get('_file') or ''}（领域：{job.get('domain') or '未声明'}）",
        f"当前日期：{today}",
        f"选题标题：{topic.title}",
    ]
    if topic.why_now:
        lines.append(f"选题缘由/变化：{topic.why_now}")
    if topic.audience:
        lines.append(f"目标受众：{topic.audience}")
    if topic.promise:
        lines.append(f"核心看点：{topic.promise}")
    if topic.is_update and topic.update_note:
        lines.append(f"增量深挖要求：{topic.update_note}")
    if topic.seed_urls:
        lines.append("种子链接（请优先用 webfetch 读透）：")
        lines.extend(f"- {u}" for u in topic.seed_urls)
    if topic.research_questions:
        lines.append("必须查清的研究问题（请逐条找一手证据回答）：")
        lines.extend(f"{i}. {q}" for i, q in enumerate(topic.research_questions, 1))
    if reqs:
        lines.append("栏目取材要求：")
        lines.append(reqs)
    return "\n".join(lines)


def _strip_fence(text: str) -> str:
    text = (text or "").strip()
    match = re.match(r"^```(?:markdown|md)?\s*([\s\S]*?)\s*```$", text)
    return match.group(1).strip() if match else text


_SAVED_FILE_RE = re.compile(r"(?:保存在|保存至|已写入|已保存至|saved to|written to)\s*[`\"']?([^`\"'\n\r，。；;]+?\.md)[`\"']?", re.I)


def _recover_saved_markdown(body: str, workspace_dir: Path) -> str:
    """If OpenCode still wrote the dossier to a local .md file and returned a short stub, read and inline it."""
    if "## 核心结论与背景" in body and "## 关键证据与展开" in body:
        return body
    match = _SAVED_FILE_RE.search(body)
    if not match:
        return body
    raw_rel = match.group(1).strip()
    candidate = Path(raw_rel)
    if not candidate.is_absolute():
        candidate = (workspace_dir / candidate).resolve()
    else:
        candidate = candidate.resolve()
    ws = workspace_dir.resolve()
    if candidate != ws and not str(candidate).startswith(str(ws) + "/"):
        return body
    if not candidate.is_file():
        return body
    try:
        content = candidate.read_text(encoding="utf-8").strip()
    except OSError:
        return body
    if len(content) > len(body):
        logger.info("Recovered OpenCode dossier written to file: %s (%d chars)", candidate, len(content))
        try:
            candidate.unlink()
        except OSError:
            pass
        return _strip_fence(content)
    return body


def _is_file_stub(body: str) -> bool:
    return bool(_SAVED_FILE_RE.search(body)) and "## 核心结论与背景" not in body


def gather_single_topic(job: dict, topic: TopicCandidate, *, config: dict, cache_dir: Path,
                        today: str, workspace_dir: str = "", cancel_token=None) -> dict:
    _check_cancel(cancel_token)
    job_key = str(job.get("_file") or job.get("name") or "job")
    theme_meta = job.get("theme_topic") if isinstance(job.get("theme_topic"), dict) else {}
    extra_seed = "\n".join(str(x) for x in (theme_meta.get("angle") or "", theme_meta.get("notes") or "") if x)
    thash = _topic_hash(job_key, today, topic, extra=extra_seed)
    cache_path = cache_dir / f"{thash}.json"
    ws_path = Path(workspace_dir) if workspace_dir else cache_dir.parents[2]
    if cache_path.is_file():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            if isinstance(cached, dict) and cached.get("markdown") and not _is_file_stub(cached["markdown"]):
                return {**cached, "cached": True}
        except (OSError, ValueError):
            pass

    prompt = PER_TOPIC_GATHER_PROMPT.replace("{task}", _per_topic_task(job, topic, today=today))
    timeout = int(config.get("per_topic_timeout_seconds") or 300)
    started = time.time()
    text, meta = opencode_cli.run(prompt, config=config, timeout=timeout, cancel_token=cancel_token)
    body = _recover_saved_markdown(_strip_fence(text), ws_path)
    urls = _valid_urls(list(topic.seed_urls) + list(extract_urls(body)))
    result = {
        "topic": topic.model_dump(),
        "hash": thash,
        "markdown": body,
        "urls": urls,
        "seconds": round(time.time() - started, 1),
        "chars": len(body),
        "cached": False,
        "meta": meta,
    }
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        atomic_write(str(cache_path), json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    except OSError as exc:
        logger.debug("Topic cache write skipped for %s: %s", thash, exc)
    return result


def gather_topics(job: dict, topics: list[TopicCandidate], *, config: dict, workspace_dir: str,
                  today: str, progress_cb=None, cancel_token=None) -> tuple[list[dict], dict]:
    _check_cancel(cancel_token)
    if not topics:
        return [], {"total": 0, "succeeded": 0, "failed": 0, "urls": []}

    cache_dir = Path(workspace_dir) / "output" / "_opencode_gather" / "topic_cache"
    max_workers = max(1, min(len(topics), int(config.get("max_concurrency") or 3)))
    results: list[dict | None] = [None] * len(topics)
    errors: list[dict] = []
    started = time.time()
    completed_count = 0

    pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="opencode-topic")
    future_map = {
        pool.submit(
            gather_single_topic, job, topic, config=config,
            cache_dir=cache_dir, today=today, workspace_dir=workspace_dir, cancel_token=cancel_token,
        ): idx
        for idx, topic in enumerate(topics)
    }
    cancelled = False
    try:
        for fut in as_completed(future_map):
            _check_cancel(cancel_token)
            idx = future_map[fut]
            topic = topics[idx]
            completed_count += 1
            try:
                res = fut.result()
                results[idx] = res
                if progress_cb:
                    progress_cb({
                        "type": "phase",
                        "phase": "gathering_topics",
                        "detail": topic.title,
                        "topic": completed_count,
                        "topics": len(topics),
                    })
            except CancelledError:
                cancelled = True
                for other in future_map:
                    other.cancel()
                raise
            except Exception as exc:
                logger.warning("Per-topic gather failed for '%s': %s", topic.title, exc)
                errors.append({"title": topic.title, "error": str(exc)[:300]})
    except CancelledError:
        cancelled = True
        for other in future_map:
            other.cancel()
        raise
    finally:
        pool.shutdown(wait=not cancelled, cancel_futures=cancelled)

    dossiers = [r for r in results if r is not None]
    all_urls = _valid_urls([u for d in dossiers for u in (d.get("urls") or [])])
    stage_usage: dict[str, Any] = {"opencode_calls": sum(1 for d in dossiers if not d.get("cached"))}
    for d in dossiers:
        if d.get("cached"):
            continue
        for k, v in ((d.get("meta") or {}).get("usage") or {}).items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                stage_usage[k] = round(stage_usage.get(k, 0) + v, 6) if isinstance(v, float) else stage_usage.get(k, 0) + v
    receipt = {
        "total": len(topics),
        "succeeded": len(dossiers),
        "failed": len(errors),
        "cached": sum(1 for d in dossiers if d.get("cached")),
        "max_concurrency": max_workers,
        "seconds": round(time.time() - started, 1),
        "urls": all_urls,
        "errors": errors,
        "usage": stage_usage,
    }
    return dossiers, receipt


# ---------------------------------------------------------------------------
# Stage 4: Report Assembly (downstream-compatible Markdown)
# ---------------------------------------------------------------------------

def _demote_dossier_headings(markdown: str) -> tuple[str, list[tuple[str, str]]]:
    """Keep dossier prose clean under ``### 1.N`` without creating stray top-level items."""
    from .opencode_gather import normalize_packets

    # Split out the "## 材料条目" section so its packets go into raw material & source list
    parts = re.split(r"(?m)^##\s+材料条目\s*$", markdown, maxsplit=1)
    main_text = parts[0].strip()
    source_entries: list[tuple[str, str]] = []
    if len(parts) > 1:
        rest = parts[1]
        # Keep "## 口径与信息缺口" if it appears after "## 材料条目"
        tail_split = re.split(r"(?m)^##\s+口径与信息缺口\s*$", rest, maxsplit=1)
        items_block = tail_split[0]
        for match in re.finditer(r"(?m)^### \[(.*?)\]\((https?://[^\s)]+)\)", items_block):
            source_entries.append((match.group(1).strip(), _clean_url(match.group(2))))
        if len(tail_split) > 1 and tail_split[1].strip():
            main_text = (main_text + "\n\n#### 口径与信息缺口\n" + tail_split[1].strip()).strip()
    else:
        _, urls = normalize_packets(markdown)
        for u in urls:
            source_entries.append((u, u))

    # Demote any remaining #, ##, ### headings in main_text to #### so report parsers never split on them
    safe_body = re.sub(r"(?m)^#{1,3}\s+", "#### ", main_text).strip()
    return safe_body, source_entries


def assemble_report(job: dict, dossiers: list[dict], *, channel: str = "", strategy: str = MODE_NAME) -> tuple[str, str]:
    """Render downstream-compatible Markdown report and raw material snapshot block."""
    flat = lambda v: " ".join(str(v or "").splitlines()).replace("**", "").strip()
    name = str(job.get("name") or job.get("_file") or "本期研究报告").strip()
    heading = flat((dossiers[0].get("topic") or {}).get("title") or name) if len(dossiers) == 1 else name
    lines = [f"# {heading}", ""]
    all_sources: list[tuple[str, str]] = []
    seen_source_urls: set[str] = set()
    material_parts: list[str] = []

    for index, dossier in enumerate(dossiers, 1):
        topic = dossier.get("topic") or {}
        title = flat(topic.get("title") or f"选题 {index}")
        promise = flat(topic.get("promise") or topic.get("why_now") or title)
        audience = flat(topic.get("audience") or "")
        urls = _valid_urls(dossier.get("urls") or topic.get("seed_urls") or [])
        body, entries = _demote_dossier_headings(str(dossier.get("markdown") or ""))

        lines.append(f"### 1.{index} {title}")
        fields: list[tuple[str, str]] = [("一句话", promise)]
        if audience:
            fields.append(("目标受众", audience))
        fields.append(("选题策略", strategy))
        if channel:
            fields.append(("选题入口", channel))
        if urls:
            fields.append(("证据来源", " ".join(urls)))
        if topic.get("why_now") and flat(topic.get("why_now")) != promise:
            fields.append(("为什么此刻值得看", flat(topic.get("why_now"))))
        if topic.get("is_update") and topic.get("update_note"):
            fields.append(("增量说明", flat(topic.get("update_note"))))
        if topic.get("series"):
            fields.append(("系列", flat(topic.get("series"))))
        if topic.get("topic_kind"):
            fields.append(("题材分类", flat(topic.get("topic_kind"))))

        lines.extend(f"- **{k}**：{v}" for k, v in fields if v)
        if body:
            lines.extend(["", body, ""])
        else:
            lines.append("")

        for label, url in entries:
            key = normalize_url(url) or url
            if key not in seen_source_urls:
                seen_source_urls.add(key)
                all_sources.append((label or url, url))
        for url in urls:
            key = normalize_url(url) or url
            if key not in seen_source_urls:
                seen_source_urls.add(key)
                all_sources.append((title, url))

        if dossier.get("markdown"):
            material_parts.append(f"## 选题：{title}\n\n{dossier['markdown']}")

    if not dossiers:
        lines.append("本次暂无新题材。")
        lines.append("")

    lines.append("### 来源清单")
    for label, url in all_sources:
        lines.append(f"- {flat(label)} {url}")

    report_md = "\n".join(lines).rstrip() + "\n"
    raw_material = "\n\n---\n\n".join(material_parts)
    return report_md, raw_material


# ---------------------------------------------------------------------------
# End-to-end entry point for ResearchEngine
# ---------------------------------------------------------------------------

def run(engine, job: dict, source_block: str = "", *, knowledge: bool = False) -> tuple[str, str, dict]:
    """Execute the full 4-stage pipeline and return ``(report_md, raw_material, receipt)``."""
    cancel = getattr(engine, "_cancel", None)
    progress = getattr(engine, "_progress", None)
    config = opencode_cli.gather_settings(engine.config_dir)
    if not opencode_cli.available({**config, "enabled": True}):
        raise RuntimeError("OpenCode CLI 不可用，无法执行 opencode_topic_v1 管线")

    today = engine._now().strftime("%Y-%m-%d") if hasattr(engine, "_now") else time.strftime("%Y-%m-%d")
    reported = engine._reported_events() if hasattr(engine, "_reported_events") else ""
    channel = str(job.get("audience_channel") or "").strip()

    # Stage 0: Lightweight source index
    source_index = [] if knowledge else build_source_index(source_block)

    # Stage 1: Topic Discovery
    if progress:
        progress({"type": "phase", "phase": "discovering_topics"})
    discovery, disc_receipt = discover_topics(
        engine, job, source_index, config=config,
        reported_events=reported, today=today,
        knowledge=knowledge, raw_knowledge_block=source_block if knowledge else "",
        cancel_token=cancel,
    )

    # Stage 2: Soft Filter (no quantity cap)
    if progress:
        progress({"type": "phase", "phase": "filtering_topics", "candidates": len(discovery.candidates)})
    from .llm import LLMConfig
    use_llm_filter = bool(config.get("soft_filter_llm", True))
    llm_cfg = LLMConfig.from_system(engine.sys, "ai") if use_llm_filter else None
    selected, filter_receipt = soft_filter(
        discovery.candidates, job=job, reported_events=reported,
        llm_config=llm_cfg, use_llm=use_llm_filter, knowledge=knowledge,
        cancel_token=cancel,
    )

    # Stage 3: Per-Topic Deep Gather
    if progress:
        progress({"type": "phase", "phase": "gathering_topics", "topics": len(selected)})
    dossiers, gather_receipt = gather_topics(
        job, selected, config=config, workspace_dir=engine.workspace_dir,
        today=today, progress_cb=progress, cancel_token=cancel,
    )

    # Stage 4: Report Assembly
    if progress:
        progress({"type": "phase", "phase": "assembling_report", "topics": len(dossiers)})
    report_md, raw_material = assemble_report(job, dossiers, channel=channel)

    all_urls = _valid_urls(
        [u for c in selected for u in c.seed_urls]
        + list(gather_receipt.get("urls") or [])
    )
    opencode_usage: dict[str, Any] = {"opencode_calls": 1}
    for src_usage in (disc_receipt.get("usage") or {}, gather_receipt.get("usage") or {}):
        for k, v in src_usage.items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                opencode_usage[k] = round(opencode_usage.get(k, 0) + v, 6) if isinstance(v, float) else opencode_usage.get(k, 0) + v
    receipt = {
        "mode": MODE_NAME,
        "version": VERSION,
        "source_index_count": len(source_index),
        "discovery": disc_receipt,
        "soft_filter": filter_receipt,
        "gather": gather_receipt,
        "topic_count": len(dossiers),
        "usage": opencode_usage,
        "urls": all_urls,
    }

    # Save audit artifacts for easy review of discovered / filtered / gathered topics
    try:
        folder = Path(engine.workspace_dir) / "output" / "_opencode_gather"
        folder.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        safe_name = str(job.get("_file") or job.get("name") or "job").replace("/", "_")
        artifact_path = folder / f"{stamp}_{safe_name}_pipeline.json"
        atomic_write(str(artifact_path), json.dumps({
            "receipt": receipt,
            "discovered": [c.model_dump() for c in discovery.candidates],
            "selected": [c.model_dump() for c in selected],
        }, ensure_ascii=False, indent=2) + "\n")
        receipt["artifact_path"] = str(artifact_path.relative_to(engine.workspace_dir))
    except OSError as exc:
        logger.debug("Pipeline audit artifact skipped: %s", exc)

    return report_md, raw_material, receipt


def run_single_topic(engine, job: dict, source_block: str = "") -> tuple[str, str, dict]:
    """Run focused single-topic OpenCode deep research for a pre-selected/custom theme.

    Skips Stage 1 (multi-topic discovery) and Stage 2 (soft filter) and feeds the
    selected theme directly into ``gather_single_topic`` + ``assemble_report``.
    """
    cancel = getattr(engine, "_cancel", None)
    progress = getattr(engine, "_progress", None)
    config = opencode_cli.gather_settings(engine.config_dir)
    if not opencode_cli.available({**config, "enabled": True}):
        raise RuntimeError("OpenCode CLI 不可用，无法执行单题深挖管线")

    today = engine._now().strftime("%Y-%m-%d") if hasattr(engine, "_now") else time.strftime("%Y-%m-%d")
    meta = job.get("theme_topic") if isinstance(job.get("theme_topic"), dict) else {}
    title = str(meta.get("title") or job.get("name") or "主题研究").strip()
    angle = str(meta.get("angle") or "").strip()
    audience = str(meta.get("audience") or job.get("audience_channel") or "").strip()
    promise = str(meta.get("promise") or angle or title).strip()
    reason = str(meta.get("reason") or "").strip()
    notes = str(meta.get("notes") or "").strip()
    domain = str(meta.get("domain") or job.get("domain") or "").strip()

    # Only keep URLs explicitly belonging to this theme (discovery signals or user notes).
    # Broad column subscription feeds must never leak unrelated news into single-topic research.
    seed_urls = _valid_urls(
        list(meta.get("seed_urls") or [])
        + list(extract_urls(notes))
    )[:12]

    questions = [str(q).strip() for q in (meta.get("research_questions") or []) if str(q).strip()][:5]
    if not questions:
        questions = [
            f"针对「{title}」，有哪些可验证的一手事实、官方规则、关键数字或具体案例？",
            f"目标观众（{audience or '普通观众'}）最关心的实际操作、选择依据、对比差异与适用边界是什么？",
        ]
        if angle:
            questions.append(f"围绕角度「{angle}」，需要补齐哪些具象演示、输入输出过程或反例？")

    topic = TopicCandidate(
        title=title,
        why_now=reason or angle,
        audience=audience,
        promise=promise,
        seed_urls=seed_urls,
        research_questions=questions,
        topic_kind=str(meta.get("category_name") or "").strip(),
    )

    extra_reqs: list[str] = []
    if str(job.get("gather_requirements") or "").strip():
        extra_reqs.append(str(job["gather_requirements"]).strip())
    elif str(job.get("prompt") or "").strip():
        extra_reqs.append(str(job["prompt"]).strip()[:4000])
    if notes:
        extra_reqs.append("用户补充材料与限定背景（务必纳入正文并与查证结果结合）：\n" + notes[:6000])

    single_job = {
        **job,
        "domain": domain or job.get("domain") or "",
        "gather_requirements": "\n\n".join(extra_reqs),
    }

    if progress:
        progress({"type": "phase", "phase": "gathering_topics", "detail": title, "topic": 1, "topics": 1})
    cache_dir = Path(engine.workspace_dir) / "output" / "_opencode_gather" / "topic_cache"
    dossier = gather_single_topic(
        single_job, topic, config=config, cache_dir=cache_dir,
        today=today, workspace_dir=engine.workspace_dir, cancel_token=cancel,
    )
    if progress:
        progress({"type": "phase", "phase": "assembling_report", "topics": 1})
    report_md, raw_material = assemble_report(single_job, [dossier], channel=str(job.get("audience_channel") or "").strip(), strategy=SINGLE_MODE_NAME)
    all_urls = _valid_urls(list(seed_urls) + list(dossier.get("urls") or []))
    opencode_usage: dict[str, Any] = {"opencode_calls": 0 if dossier.get("cached") else 1}
    if not dossier.get("cached"):
        for k, v in ((dossier.get("meta") or {}).get("usage") or {}).items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                opencode_usage[k] = round(opencode_usage.get(k, 0) + v, 6) if isinstance(v, float) else opencode_usage.get(k, 0) + v
    receipt = {
        "mode": SINGLE_MODE_NAME,
        "version": VERSION,
        "source_index_count": 0,
        "topic_count": 1,
        "cached": bool(dossier.get("cached")),
        "seconds": dossier.get("seconds"),
        "usage": opencode_usage,
        "urls": all_urls,
    }
    return report_md, raw_material, receipt
