"""OpenCode CLI as a gather-and-organize worker for research Jobs.

The worker only collects and structures material. The report-writing agent still
owns the final Markdown report. Inputs to that agent become:

  declared sources (+ optional supply) + OpenCode gather pack + Exa last-resort reads
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

GATHER_PROMPT = """你是信息搜集与整理员，不是成稿作者。

任务：根据「研究题」和「已取得的声明源材料」，把材料补厚、理清，产出一份可直接喂给下游成稿 Agent 的材料包。
目标：让成稿时有更多可引用事实、数字、对比与可跟读链接，而不是只剩几句摘要。

规则：
1. 优先吃透已提供的声明源正文与链接；摘要、列表、RSS/catalog 片段不够时，用 webfetch 打开具体 URL 补全文。
2. 材料仍偏薄时，可用有限次数 websearch 补缺口（标明「搜索补充」），再 webfetch 打开检索到的可靠链接；不要无搜不读。
3. 不要写最终研究报告，不要写口播稿，不要编造未取得的事实；拿不到就写进「未覆盖」。
4. 每条材料尽量写「可引用」细节：具体数字、时间点、主体名称、前后对比、原文短引（一两句即可）。
5. 输出必须是 Markdown，使用下列结构（可增减条目编号，但保留这些一级/二级标题）：

## 整理结论
（3–5 句：本期覆盖了什么、最值得成稿展开的 2–3 个点、主要缺口）

## 关键数字与对比
- 用条目列出本期最有用的数字、涨跌、前后对比或排名（没有就写「无」）

## 材料条目
### 1. 标题
- **链接**：URL 或「无」
- **来源类型**：声明源 / webfetch / 搜索补充
- **时间**：若有
- **要点**：3–6 条可引用事实（含数字/主体/因果，勿空话）
- **可引用原句**：若有，一两句原文；没有写「无」

## 成稿可用角度
- 2–4 条：下游成稿可以怎么讲（角度名 + 对应哪几条材料），不要写成完整段落

## 未覆盖
- 题目中仍缺证据的部分（若无写「无」）

研究题：
{objective}

已取得的声明源材料：
{sources}
"""


def wanted(job: dict, config: dict) -> bool:
    """Whether this Job should run the OpenCode gather worker."""
    flag = job.get("opencode_gather")
    if flag is False:
        return False
    if not config.get("enabled"):
        return False
    if flag is True:
        return True
    apply = str(config.get("apply_to") or "all")
    if apply == "all":
        return True
    if apply == "flagged":
        return False
    # fetch_only: Jobs that disable web search rely on declared material.
    return job.get("search") is False


def _objective(job: dict) -> str:
    name = str(job.get("name") or job.get("_file") or "研究")
    domain = str(job.get("domain") or "")
    prompt = str(job.get("prompt") or "")
    # Keep the gather prompt bounded; full job prompt can be huge.
    head = prompt.strip().splitlines()
    brief = "\n".join(head[:40])[:4000]
    return f"名称：{name}\n领域：{domain}\n要求摘要：\n{brief}"


def _strip_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("## ") or text.startswith("# "):
        return text
    match = re.match(r"^```(?:markdown|md)?\s*([\s\S]*?)\s*```$", text)
    return match.group(1).strip() if match else text


def gather(engine, job: dict, source_block: str) -> tuple[str, dict]:
    """Return ``(markdown_block, receipt)``. Empty block when skipped or soft-failed."""
    config = opencode_cli.gather_settings(engine.config_dir)
    if not wanted(job, config):
        return "", {"skipped": True, "reason": "not_enabled_for_job"}
    if not opencode_cli.available({**config, "enabled": True}):
        return "", {"skipped": True, "reason": "cli_unavailable"}

    prompt = GATHER_PROMPT.format(
        objective=_objective(job),
        sources=(source_block or "（本轮没有声明源正文，请仅在规则允许时极少次搜索，否则写明材料不足）")[:120000],
    )
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

    block = "## OpenCode 搜集整理\n\n" + body
    receipt = {
        "version": 1,
        "seconds": round(time.time() - started, 1),
        "chars": len(body),
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
