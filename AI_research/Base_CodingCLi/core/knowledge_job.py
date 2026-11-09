"""Knowledge Jobs: write from the model's own knowledge, by explicit declaration.

A Job with ``search: false`` normally reads only its declared sources, and the run stops when none answer, so
the model is never asked to write from memory by accident. A knowledge Job opts in to exactly that with
``knowledge: true``. Besides the prompt it sees the questions its earlier reports already covered, so it picks a new
one, and, when it declares ``sources``, a list of questions from those sources used only as topic ideas: the content
is still written from the model's knowledge, and a source that fails never stops the run. Design:
spec/illustrated-explainer.md.
"""
from __future__ import annotations

import glob
import os
import re

MAX_TITLES = 200
MAX_IDEAS = 40


def enabled(job: dict) -> bool:
    return job.get("knowledge") is True


def problem(job: dict) -> str | None:
    """Why a knowledge declaration is invalid, or None."""
    if "knowledge" not in job:
        return None
    if job["knowledge"] is not True:
        return "'knowledge' 只能写 true（或不写）"
    if job.get("search") is not False:
        return "knowledge: true 的 Job 须同时写 search: false"
    if "sources" in job and (not isinstance(job["sources"], list) or not all(isinstance(s, str) for s in job["sources"])):
        return "sources 须是信息源列表"
    if "{date}" not in str(job.get("output", "")):
        return "knowledge: true 的 Job 的 output 须含 {date}，往期底稿才能按日期找到"
    return None


def past_titles(job: dict, workspace_dir: str) -> list[str]:
    """Titles (first ``# `` line) of this Job's earlier reports, newest first."""
    pattern = os.path.join(workspace_dir, str(job["output"]).replace("{date}", "*"))
    titles = []
    for path in sorted(glob.glob(pattern), reverse=True):
        if not path.endswith(".md"):
            continue
        try:
            with open(path, encoding="utf-8") as handle:
                for line in handle:
                    match = re.match(r"#\s+(.+)", line.strip())
                    if match:
                        titles.append(match.group(1).strip())
                        break
        except OSError:
            continue
        if len(titles) >= MAX_TITLES:
            break
    return titles


def block(titles: list[str]) -> str:
    if not titles:
        return "## 往期已讲过的问题\n\n（还没有往期底稿）"
    return "## 往期已讲过的问题\n\n" + "\n".join(f"- {t}" for t in titles) + "\n\n不要再选上面这些问题或换个说法的同一问题。"


def topic_block(items: list) -> str:
    """Questions from the declared sources, as topic ideas only (title, a short gist, link)."""
    if not items:
        return "## 选题参考\n\n（本次信息源没有取到内容，按题目清单选题）"
    lines = []
    for item in items[:MAX_IDEAS]:
        gist = re.sub(r"\s+", " ", " ".join(item.excerpts or []))[:140].strip()
        lines.append(f"- {item.title.strip()}" + (f" —— {gist}" if gist else "") + f"（{item.url}）")
    return ("## 选题参考（只用来选题，内容仍凭已有知识写）\n\n" + "\n".join(lines)
            + "\n\n这些多是外文栏目里观众真实提出的问题。可以挑一个生活里看得见、适合中国观众的，换成中国观众熟悉的情境来讲。")
