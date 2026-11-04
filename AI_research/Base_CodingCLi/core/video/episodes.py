"""One report, one authored programme; individual production remains a separate route."""
from __future__ import annotations

import copy
from datetime import date
from pathlib import PurePath
import re

MODE = "episode"


def enabled(topic: dict) -> bool:
    return topic.get("content_mode") == MODE


def material_topic(topic: dict, context: dict) -> dict:
    """Supplied material follows its column's mode unless the words are final narration."""
    supplied = topic.get('user_materials') or {}
    if (not supplied or supplied.get('mode') == 'narration' or 'content_mode' in topic
            or not context.get('column')):
        return topic
    return {**topic, 'content_mode': context.get('content_mode', 'individual')}


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


def programme_label(context: dict) -> str:
    """The column's public programme name, as viewers see it in the title and hear it in the greeting."""
    if not context.get("column"):
        return ""
    label = ((context.get("render") or {}).get("packaging") or {}).get("label") or context.get("column_name", "")
    label = re.sub(r"^T\d+\s*[·.、：:]\s*", "", label).strip()
    if context["column"] == "ai_flash":
        label = "今日快讯"
    elif context.get("matrix") == "chatgpt_acquisition" and label and not label.startswith("ChatGPT"):
        label = "ChatGPT " + label
    return label


def greeting(topic: dict, context: dict) -> str:
    """The fixed line the narrator opens with: the column's own ``greeting``, else one built from its name.

    It is the same for every issue of a column and says no date. A script without a column has none.
    """
    if not context.get("column"):
        return ""
    if (context.get("greeting") or "").strip():
        return context["greeting"].strip()
    label = ((topic.get("episode") or {}).get("publication") or {}).get("label") or programme_label(context)
    return f"大家好，欢迎收看{label}。" if label else ""


def publication_topic(topic: dict, context: dict) -> dict:
    """Freeze the programme's public name at creation, without retitling old work."""
    if not enabled(topic) or not context.get("column") or topic.get('user_materials'):
        return topic
    episode = topic.get("episode") or {}
    if episode.get("publication"):
        return topic
    label = programme_label(context)
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
    # "栏目｜日期" titles got 35–98 plays against hundreds for named, concrete ones (weekly review, 10-19 to 11-01):
    # the programme keeps its name for the greeting, and the public title is the one written from this issue's
    # strongest items. Until it is written the column name stands in.
    publication = {"scheme": SCHEME, "title": label, "label": label, "issue_date": issue}
    return {**topic, "title": label, **({"round": issue} if issue else {}),
            "episode": {**episode, "publication": publication}}


#: Programmes created before 2026-11-02 keep the dated title they were given ("column-date/v1").
SCHEME = "lead-subject/v2"


def publication_title(topic: dict, authored_title: str) -> str:
    publication = (topic.get("episode") or {}).get("publication") if enabled(topic) else None
    if not publication:
        return authored_title
    subject = authored_title.strip()
    if publication.get("scheme") == SCHEME:
        return subject or publication["title"]
    heading = publication["title"]
    if publication.get("subject_subtitle") and subject:
        return subject if subject.startswith(heading) else heading + "｜" + subject
    return heading


def brief(topic: dict, *, tutorial: bool = False) -> str:
    if not enabled(topic):
        return ""
    items = (topic.get("episode") or {}).get("items") or []
    chosen = "\n".join(f"{i+1}. {t['title']}" for i, t in enumerate(items))
    publication = (topic.get("episode") or {}).get("publication") or {}
    if publication.get("scheme") == SCHEME:
        naming = ("\n整期总标题由你来写：从本期最值得看的一两条内容里取具体产品和数字，"
                  f"不写日期，也不写栏目名「{publication['label']}」。日期不写进口播。")
    elif publication:
        naming = (f"\n整期总标题使用「{publication['title']}」，不要把第一条或最显眼的事件当作整期总标题。"
                  + ("本期单主题深度解读的具体疑问可保留为副标题。" if publication.get("subject_subtitle") else "具体事件使用各段的内容标题。")
                  + "日期是展示信息，不必写进口播，不要求讲述者念日期。")
    else:
        naming = ""
    if tutorial:
        return ("\n\n## 内容组织方式：完整教程\n"
                "本份报告制作一条视频，按实际任务的准备、操作、结果等阶段分章，每个阶段一个 section。"
                "章节就是步骤导航，title 为不带编号的短标题；各步骤承接同一份输入与输出。"
                "开场与收尾各一个 section，episode_role 使用 opening、item、closing。"
                "按实际内容展开，沿用上游给出的命令和结果。" + naming)
    return ("\n\n## 内容组织方式：整期节目\n"
            "本份报告只制作一篇总稿、一条视频。把本期有价值的多个内容按合理顺序充分展开。"
            "整期共享开场和收尾，各条之间按需要衔接。每条内容用一个 section，title 为适合导航的短标题，"
            "该 section 的 paragraphs 按需要展开背景、具体例子、操作过程、机制、结果与影响。"
            "同一内容可以有很多段旁白；不要把每个解释步骤拆成新的内容条目。"
            "开场和收尾各占一个 section。每个 section 加 episode_role：开场 opening、内容 item、收尾 closing。"
            "内容标题最多 24 字，不使用编号，视频自动显示序号。"
            "时长由内容决定，配方中单条视频的秒数、分钟数或字数只作历史参考，不作为整期篇幅上限。"
            "保留原始材料的具体细节和研究底稿的解释深度。上游结论直接用于讲述。"
            "本期素材若围绕同一问题，则按解释层次组织章节，不强行制造无关话题。"
            + naming + ("\n报告中的内容线索：\n" + chosen if chosen else ""))


def manifest(topic: dict, sections: list[dict]) -> dict:
    from .screenshots import spoken_sections

    return {"mode": MODE, "report_path": topic.get("from_report", ""),
            **({"publication": copy.deepcopy(topic["episode"]["publication"])}
               if (topic.get("episode") or {}).get("publication") else {}),
            "source_items": copy.deepcopy((topic.get("episode") or {}).get("items") or []),
            "items": [{"index": i, "title": s["title"], "chapter_index": i,
                       "paragraphs": len(s["paragraphs"])} for i, s in enumerate(spoken_sections({'sections': sections}))
                      if s.get("episode_role", "item") == "item"]}


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


_FIGURE = re.compile(r"(?:US\$|\$|¥|￥)\s?(\d[\d,]*(?:\.\d+)?)|(\d[\d,]*(?:\.\d+)?)\s?(?:美元|美金|元|USD|%|％)")
_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _number(value: str) -> str:
    value = value.replace(",", "")
    return value.rstrip("0").rstrip(".") if "." in value else value


# Worked examples and production notes compute their own illustrative figures; only the facts count.
_ILLUSTRATIVE = ("使用场景", "用户最终得到什么", "画面建议", "操作与演示", "输入与结果", "获客作用", "注意", "待实测")


def _figures(text: str) -> set[str]:
    segments = re.split(r"\*\*([^*\n]{1,16})\*\*", text)
    facts = segments[0] + "".join(body for label, body in zip(segments[1::2], segments[2::2])
                                  if not label.strip("：: ").startswith(_ILLUSTRATIVE))
    facts = re.sub(r"https?://\S+", " ", facts)  # "GPT-5.6%20..." in a link is not 5.6%
    return {_number(a or b) for a, b in _FIGURE.findall(facts)}


def _pages(text: str) -> set[tuple[str, str]]:
    """Cited entries: one what's-new or pricing page holds many, told apart by their anchors."""
    from core.provenance import extract_urls
    from core.video.story_pool import _anchored
    return {_anchored(u) for u in extract_urls(text)}


def _key(value: str) -> str:
    return re.sub(r"[\W_]+", "", value).casefold()


def item_parts(report: str, items: list[dict]) -> dict[str, str]:
    """Each picked item's own section of the report, found by its heading."""
    parts = {}
    for part in re.split(r"(?m)^(?=### )", report):
        heading = _key(part.splitlines()[0]) if part.startswith("### ") else ""
        for item in items:
            if heading and _key(item["title"]) and _key(item["title"]) in heading:
                parts.setdefault(item["title"], part)
    return parts


def covered(report: str, items: list[dict], peers: list[dict], read=None) -> list[dict]:
    """Items an earlier video already told in full: its report cited the same entry and the video said
    every price and figure the item gives. An item with a figure the earlier video never said is an update and stays;
    an item without figures is never dropped on a guess."""
    read = read or (lambda path: open(path, encoding="utf-8").read())
    pages = {}
    result = []
    for title, part in item_parts(report, items).items():
        figures, cited = _figures(part), _pages(part)
        if not figures or not cited:
            continue
        for peer in peers:
            told = {_number(n) for n in _NUMBER.findall(peer.get("text") or "")}
            if not figures <= told:
                continue
            if peer.get("report") not in pages:
                try:
                    pages[peer.get("report")] = _pages(read(peer["report"])) if peer.get("report") else set()
                except OSError:
                    pages[peer.get("report")] = set()
            shared = cited & pages[peer.get("report")]
            # A whole help article backs many different tips; one figure on it is not the same story.
            if any(anchor for _, anchor in shared) or (shared and len(figures) >= 2):
                result.append({"title": title, "peer_id": peer["script_id"], "peer_title": peer["title"]})
                break
    return result


def without(report: str, items: list[dict], titles: set[str]) -> str:
    parts = item_parts(report, [i for i in items if i["title"] in titles])
    for part in parts.values():
        report = report.replace(part, "")
    return report
