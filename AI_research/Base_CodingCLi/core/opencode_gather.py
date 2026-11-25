"""OpenCode CLI as a Job-driven research harness (gather worker).

OpenCode is a tool harness, not a dumb webfetch pipe. This worker receives the
Job's keywords and requirements, runs a bounded research task (search / fetch /
cross-check), and returns a structured material pack. The report-writing agent
still owns the final Markdown.

Page-level ``webfetch_markdown`` in ``opencode_cli`` remains a thin reader for the
HTTP→Chrome→OpenCode→Exa fallback chain only.

Material entries are normalized to ``### [title](url)`` so
``audience_strategy.packets()`` can index them the same way as
``SearchClient.format_results``.
"""
from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path

from . import opencode_cli
from .errors import CancelledError
from .fileio import atomic_write

logger = logging.getLogger(__name__)

GATHER_PROMPT = """按下面的调研任务做一轮调研，产出给下游成稿用的材料包。
不要写最终研究报告或口播稿。

工作方式：
- 以关键词与调研要求为主，自行检索、读页、交叉比对；与声明源抓取并行，不要等声明源。
- 声明源材料会另路交给成稿，不必复述或等待；你这边补声明源可能漏掉的公开材料与可引用细节。
- 需要时用 websearch / webfetch 等工具；查询可自拟，中英均可。
- 只写实际读到或检索到的内容；缺证据就标明，不要编造。
- 材料尽量带可引用细节（数字、时间、主体、对比、关键原句），空话少写。

输出用 Markdown。概览段落用普通标题；**每一条可引用材料必须用下面这种标题**（与系统声明源格式一致，缺一不可）：

### [条目标题](https://完整链接)

要点与可引用原句写在该标题下面。

可参考整体结构（按实际材料增减，不必凑条数或凑字数）：

## 调研结论
这轮覆盖了什么、值得展开的点、主要缺口

## 关键数字与对比
有用的数字、涨跌、对比或排名（没有可省略）

## 材料条目
### [条目标题](https://example.com/article)
- 来源说明、时间（若有）
- 要点与可引用原句

## 成稿可用角度
下游可以怎么讲（点到即可）

## 未覆盖
仍缺证据的部分（没有可省略）

## 调研任务
{objective}
"""

_PACKET_HEAD = re.compile(r"^### \[(.*?)\]\((https?://[^\s]+?)\)(.*)$")
_MD_LINK = re.compile(r"\[([^\]]*)\]\((https?://[^\s)]+)\)")
_BARE_URL = re.compile(r"(https?://[^\s\]\)>'\"<>]+)")
_SKIP_TITLES = frozenset({
    "调研结论", "关键数字与对比", "材料条目", "成稿可用角度", "未覆盖", "调研任务",
})


def wanted(job: dict, config: dict) -> bool:
    """Whether this Job should run the OpenCode gather worker.

    OpenCode gather is the primary research gatherer alongside fixed sources:
    ``apply_to: all`` enables it for every non-knowledge Job (including
    ``search: false``, which only disables paid in-agent search loops). Pure
    knowledge Jobs opt in explicitly with ``opencode_gather: true``; any Job
    can opt out with ``opencode_gather: false``.
    """
    flag = job.get("opencode_gather")
    if flag is False:
        return False
    if not config.get("enabled"):
        return False
    if flag is True:
        return True
    from . import knowledge_job
    if knowledge_job.enabled(job):
        return False
    apply = str(config.get("apply_to") or "all")
    fetch_only = job.get("search") is False
    if apply == "flagged":
        return False
    if apply == "fetch_only":
        return fetch_only
    return True


def _keywords(job: dict) -> str:
    raw = job.get("keywords")
    if isinstance(raw, list):
        return "、".join(str(x).strip() for x in raw if str(x).strip())
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return ""


def _window_line(window: dict | None) -> str:
    if not isinstance(window, dict) or not window.get("start") or not window.get("end"):
        return ""
    tz = window.get("timezone") or "Asia/Shanghai"
    period = f"{window['start']} 至 {window['end']}（{tz}）"
    if window.get("mode") != "since_last_collection" and window.get("days"):
        period = f"近 {window['days']} 天：{period}"
    return f"采集时间范围：{period}（优先检索此窗口内的新发布、更新与案例；更早资料仅作必要背景，勿当作本期新动态）"


def _objective(job: dict, *, reported_events: str = "", today: str = "") -> str:
    """Build the research brief from Job fields (date + window + keywords + editorial scope)."""
    from .prompt_scope import editorial_brief

    name = str(job.get("name") or job.get("_file") or "研究")
    domain = str(job.get("domain") or "").strip()
    audience = str(job.get("audience_channel") or "").strip()
    keywords = _keywords(job)
    run_date = (today or "").strip() or time.strftime("%Y-%m-%d")
    window_line = _window_line(job.get("_collection_window"))
    # Optional Job override for a dedicated OpenCode research brief.
    custom = str(job.get("opencode_task") or job.get("opencode_brief") or "").strip()
    brief = editorial_brief(str(job.get("prompt") or ""))[:50000]
    lines = [
        f"名称：{name}",
        f"当前日期：{run_date}",
        f"领域：{domain or '（未声明）'}",
        f"关键词：{keywords or '（未声明——请从要求摘要自行提炼检索词）'}",
    ]
    if window_line:
        lines.append(window_line)
    if audience:
        lines.append(f"受众/频道：{audience}")
    if custom:
        lines.append("专项调研要求：")
        lines.append(custom[:30000])
    lines.append("栏目研究范围（供把握选题与检索方向）：")
    lines.append(brief or "（无）")
    if (reported_events or "").strip():
        lines.append("近期已报来源（优先寻找尚未覆盖的新进展；若确有实质更新再补充）：")
        lines.append(reported_events.strip())
    return "\n".join(lines)


def _strip_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("## ") or text.startswith("# "):
        return text
    match = re.match(r"^```(?:markdown|md)?\s*([\s\S]*?)\s*```$", text)
    return match.group(1).strip() if match else text


def _clean_url(url: str) -> str:
    return url.rstrip(".,;)]}'\"")


def _first_url(text: str) -> tuple[str, str]:
    """Return ``(title_hint, url)`` from the first markdown or bare URL in text."""
    md = _MD_LINK.search(text)
    if md:
        return (md.group(1) or "").strip(), _clean_url(md.group(2))
    bare = _BARE_URL.search(text)
    if bare:
        return "", _clean_url(bare.group(1))
    return "", ""


def normalize_packets(body: str) -> tuple[str, list[str]]:
    """Turn gather markdown into ``### [title](url)`` packets + preface.

    Returns ``(block, urls)``. ``block`` starts with ``## OpenCode 调研材料`` and
    uses the same ``---`` separators as ``SearchClient.format_results``, so
    ``audience_strategy.packets()`` indexes OpenCode items as their own Sn.
    """
    parts = re.split(r"(?m)(?=^### )", body or "")
    preface: list[str] = []
    packets: list[str] = []
    urls: list[str] = []
    seen: set[str] = set()

    for part in parts:
        raw = part.strip()
        if not raw:
            continue
        if not raw.startswith("### "):
            preface.append(raw)
            continue
        first, _, rest = raw.partition("\n")
        linked = _PACKET_HEAD.match(first.strip())
        if linked:
            title, url, tail = linked.group(1).strip(), _clean_url(linked.group(2)), (linked.group(3) or "").strip()
            content = "\n".join(x for x in (tail, rest.strip()) if x).strip()
            if url not in seen:
                seen.add(url)
                urls.append(url)
            packets.append(f"### [{title or url}]({url})" + (f"\n\n{content}" if content else ""))
            continue

        title = first[4:].strip()
        if title in _SKIP_TITLES or title.startswith("材料条目"):
            preface.append(raw)
            continue
        hint, url = _first_url(raw)
        if not url:
            preface.append(raw)
            continue
        label = title or hint or url
        content = rest.strip()
        if url not in seen:
            seen.add(url)
            urls.append(url)
        packets.append(f"### [{label}]({url})" + (f"\n\n{content}" if content else ""))

    # Start with ### [title](url) so audience_strategy.packets() does not glue this
    # block onto the previous declared-source packet. Overview text rides on the
    # last OpenCode packet (or stands alone when there are no URLs).
    if packets:
        head, _, rest = packets[0].partition("\n")
        packets[0] = head + "\n\n（OpenCode 调研材料）" + (("\n" + rest) if rest else "")
        joined = "\n\n---\n\n".join(packets)
        if preface:
            joined = joined + "\n\n" + "\n\n".join(preface)
        return joined.strip() + "\n", urls
    if preface:
        return "## OpenCode 调研材料\n\n" + "\n\n".join(preface).strip() + "\n", urls
    return "", urls


def gather(engine, job: dict, source_block: str = "") -> tuple[str, dict]:
    """Return ``(markdown_block, receipt)``. Empty block when skipped or soft-failed.

    ``source_block`` is ignored: gather runs in parallel with declared-source
    fetch and works from Job keywords / requirements only.
    """
    config = opencode_cli.gather_settings(engine.config_dir)
    if not wanted(job, config):
        return "", {"skipped": True, "reason": "not_enabled_for_job"}
    if not opencode_cli.available({**config, "enabled": True}):
        return "", {"skipped": True, "reason": "cli_unavailable"}

    reported = ""
    if hasattr(engine, "_reported_events"):
        try:
            reported = str(engine._reported_events() or "").strip()
        except Exception as exc:  # noqa: BLE001 - event memory is best-effort
            logger.debug("OpenCode gather reported_events skipped: %s", exc)
    today = ""
    if hasattr(engine, "_now"):
        try:
            today = engine._now().strftime("%Y-%m-%d")
        except Exception:  # noqa: BLE001 - fallback to local date
            today = ""
    prompt = GATHER_PROMPT.format(objective=_objective(job, reported_events=reported, today=today))
    started = time.time()
    try:
        text, meta = opencode_cli.run(
            prompt,
            config=config,
            timeout=int(config.get("gather_timeout_seconds") or 300),
            cancel_token=getattr(engine, "_cancel", None),
        )
    except CancelledError:
        raise
    except Exception as exc:
        logger.warning("OpenCode gather failed: %s", exc)
        return "", {"skipped": False, "error": str(exc)[:500], "seconds": round(time.time() - started, 1)}

    body = _strip_fence(text)
    if len(body) < 40:
        return "", {"skipped": False, "error": "gather_too_short", "seconds": round(time.time() - started, 1), **meta}

    block, urls = normalize_packets(body)
    receipt = {
        "version": 3,
        "seconds": round(time.time() - started, 1),
        "chars": len(block),
        "keywords": _keywords(job),
        "urls": urls,
        "packet_count": len(urls),
        **meta,
    }
    try:
        folder = Path(engine.workspace_dir) / "output" / "_opencode_gather"
        folder.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        name = str(job.get("_file") or job.get("name") or "job").replace("/", "_")
        path = folder / f"{stamp}_{name}.md"
        atomic_write(str(path), block + "\n")
        atomic_write(str(path.with_suffix(".json")), json.dumps(receipt, ensure_ascii=False, indent=2))
        receipt["path"] = str(path.relative_to(engine.workspace_dir))
    except Exception as exc:
        logger.warning("OpenCode gather artifact not saved: %s", exc)
    return block, receipt
