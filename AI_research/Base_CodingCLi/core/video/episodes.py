"""One report, one authored programme; individual production remains a separate route."""
from __future__ import annotations

import copy
from datetime import date
from pathlib import PurePath
import re

MODE = "episode"


def enabled(topic: dict) -> bool:
    return topic.get("content_mode") == MODE


def topic(report: dict, workspace: str, *, job: str, source=None, production: str = "") -> dict:
    from . import reports, topic_store
    from core import report_identity
    import os
    identity = report_identity.describe(os.path.join(workspace, report["report_path"]), workspace)["id"]
    from core import report_meta
    issue = (report_meta.get(report['report_path']) or {}).get('production_issue') or {}
    value = reports.topic_for(report, workspace)
    items = []
    if source is not None:
        _, candidates = topic_store.read_report(source, report["report_path"], workspace)
        items = [{"id": t.id, "title": t.title} for t in candidates]
    return {**value, "content_mode": MODE, "from_report": report["report_path"],
            "report_id": report["id"], "episode": {"job": job, "production": production,
            "items": items, "report_path": report["report_path"], "report_identity": identity,
            **({'issue':copy.deepcopy(issue)} if issue else {})}}


def snapshot_context(context: dict, topic: dict) -> dict:
    # An explicitly submitted individual topic stays individual even when the
    # owning column now defaults to programmes. Historical scripts stay frozen.
    return {**context, "content_mode": MODE if enabled(topic) else "individual"}


def publication_topic(topic: dict, context: dict) -> dict:
    """Freeze the programme's public name at creation, without retitling old work."""
    if not enabled(topic) or not context.get("column"):
        return topic
    episode = topic.get("episode") or {}
    if episode.get("publication"):
        return topic
    label = ((context.get("render") or {}).get("packaging") or {}).get("label") or context.get("column_name", "")
    label = re.sub(r"^T\d+\s*[·.、：:]\s*", "", label).strip()
    if context["column"] == "ai_flash":
        label = "今日快讯"
    elif context.get("matrix") == "chatgpt_acquisition" and not label.startswith("ChatGPT"):
        label = "ChatGPT " + label
    if not label:
        return topic
    # Use this report's issue date, never the later render/retry date or an
    # individual event's release date. Undated reports keep a date-free label.
    issue = ""
    for value in (((episode.get('issue') or {}).get('research_window') or {}).get('end', ''),
                  PurePath(topic.get("from_report") or episode.get("report_path") or "").name, topic.get("round", "")):
        if match := re.search(r"\d{4}-\d{2}-\d{2}", str(value)):
            try:
                issue = date.fromisoformat(match.group()).isoformat()
            except ValueError:
                continue
            break
    display_date = f"{int(issue[:4])}年{int(issue[5:7]):02d}月{int(issue[8:]):02d}日" if issue else ""
    heading = label + ("｜" + display_date if display_date else "")
    publication = {"scheme": "column-date/v1", "title": heading, "label": label, "issue_date": issue,
                   "subject_subtitle": context.get("type") == "deep_research"}
    return {**topic, "title": heading, **({"round": issue} if issue else {}),
            "episode": {**episode, "publication": publication}}


def publication_title(topic: dict, authored_title: str) -> str:
    publication = (topic.get("episode") or {}).get("publication") if enabled(topic) else None
    if not publication:
        return authored_title
    heading = publication["title"]
    subject = authored_title.strip()
    if publication.get("subject_subtitle") and subject:
        return subject if subject.startswith(heading) else heading + "｜" + subject
    return heading


def brief(topic: dict) -> str:
    if not enabled(topic):
        return ""
    items = (topic.get("episode") or {}).get("items") or []
    chosen = "\n".join(f"{i+1}. {t['title']}" for i, t in enumerate(items))
    publication = (topic.get("episode") or {}).get("publication") or {}
    naming = (f"\n整期总标题使用「{publication['title']}」，不要把第一条或最显眼的事件当作整期总标题。"
              + ("本期单主题深度解读的具体疑问可保留为副标题。" if publication.get("subject_subtitle") else "具体事件使用各段的内容标题。")
              + "日期是展示信息，不必写进口播，不要求讲述者念日期。" if publication else "")
    return ("\n\n## 内容组织方式：整期节目\n"
            "本份报告只制作一篇总稿、一条视频。把本期有价值的多个内容按合理顺序充分展开。"
            "整期共享开场和收尾，各条之间自然转场。每条内容用一个 section，title 为适合导航的短标题，"
            "该 section 的 paragraphs 按需要展开背景、具体例子、操作过程、机制、结果与影响。"
            "同一内容可以有很多段旁白；不要把每个解释步骤拆成新的内容条目。"
            "开场和收尾可各占一个 section。每个 section 加 episode_role：开场 opening、内容 item、收尾 closing。"
            "内容标题最多 24 字，不使用编号，视频自动显示序号。"
            "时长由内容决定，配方中单条视频的秒数、分钟数或字数只作历史参考，不作为整期篇幅上限。"
            "栏目方向中只选一个事件的要求用于独立单条，本期按整期节目覆盖多个内容。"
            "保留原始材料的具体细节和研究底稿的解释深度。上游结论直接用于讲述。"
            "本期素材若围绕同一问题，则按解释层次组织章节，不强行制造无关话题。"
            + naming + ("\n报告中的内容线索：\n" + chosen if chosen else ""))


def manifest(topic: dict, sections: list[dict]) -> dict:
    return {"mode": MODE, "report_path": topic.get("from_report", ""),
            **({"publication": copy.deepcopy(topic["episode"]["publication"])}
               if (topic.get("episode") or {}).get("publication") else {}),
            "source_items": copy.deepcopy((topic.get("episode") or {}).get("items") or []),
            "items": [{"index": i, "title": s["title"], "chapter_index": i,
                       "paragraphs": len(s["paragraphs"])} for i, s in enumerate(sections) if s.get("episode_role", "item") == "item"]}


def scripts_for(report_path: str, recipe: str, production_id: str, column_id: str, revision: str = "") -> list[dict]:
    """One programme per frozen business column and report, across recipe bindings."""
    from . import store
    owner = "content_column_id = ?" if column_id else "production = ?"
    version_clause = " AND json_extract(topic,'$.episode.report_identity') = ?" if revision else ""
    with store.connect() as db:
        rows = db.execute(f"""SELECT id,column_key,status,topic,created_at FROM scripts
            WHERE archived=0 AND status!='FAILED' AND {owner}
            AND json_extract(topic,'$.content_mode')='episode'
            AND (report_path=? OR json_extract(topic,'$.from_report')=?)
             {version_clause} ORDER BY created_at""", (column_id or production_id, report_path, report_path, *((revision,) if revision else ()))).fetchall()
    import json
    return [{"id": x["id"], "column_key": x["column_key"], "status": x["status"],
             "title": json.loads(x["topic"])["title"], "created_at": x["created_at"], "archived": False} for x in rows]
