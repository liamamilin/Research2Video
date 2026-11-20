"""Allocate one report between a programme and independent videos before writing.

The frozen allocation is shared by both outputs. Existing angle reservations
handle supported money/how-to follow-ups; unrelated history is still checked.
"""
from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
from pathlib import Path
import re

from core.fileio import atomic_write, file_lock
from core import report_material, report_meta, telemetry
from core.video import audience_strategy, store
from core.yaml_cache import load_yaml

VERSION = 3
logger = logging.getLogger(__name__)
SYSTEM = """给同一份研究报告一起规划一份快讯合集和若干独立单条。只组织输入材料，不搜索，不写成片文稿。
合集回答“发生了什么、对谁有什么影响”，保留有价值的变化，不因要做单条而删掉值得知道的新闻。
单条回答一个有足够材料展开的独立问题。可以与合集共用事件，但必须提供合集简报之外的实际收获，不能只换标题重讲发布。
同一事件同时出现时，单条选 money（费用、额度、选择）或 howto（入口、操作、产物）；这是现有作者和排重支持的独立角度。
只有参数、泛泛判断、功能名，缺少能展开的内容时，只放合集，不凑单条。其他独立问题可只做单条，此时 angle 留空。
不为凑数量制造题目，也不把所有题目机械地拆成两份。每个候选最多一份独立单条。
episode.summary 只交代核心变化是什么、对目标观众的直接影响是什么，保留必要的使用条件。不要把原材料缩写成参数清单、功能表或跑分表。
先决定单条独立回答什么，再把回答它所需的操作过程、完整比较和案例从 episode.summary 中拿出去，放进 single.material。共同背景可以简述，两份材料不能各自完整回答同一个问题。
若单条是 money，合集不提前展开任务分层与购买判断；若单条是 howto，合集不提前讲安装命令和操作步骤。
howto 必须有材料明确提供的操作入口/命令、关键动作和结果；发布说明只有权限名或功能名时，不能把它改成“怎么配置”的教程。可以留在合集解释变化。
single.material 是按该问题整理的已有证据与实例，从观众要解决的问题或关键变化写起，不要用发布日期当第一句；single.incremental_value 写观众听完能带走的收获，不要写合集与单条的制作分工。
保留选题指定的受众范围；职业案例不自动变成整篇的观看前提。目标受众写在 single.audience 中。
source_ids 只能选输入的 S 编号；各自选择真正相关的材料。既往内容只作避免重复的参考。
必须逐一返回所有候选的 candidate_id。每条可有 episode、single、两者都有或两者都为 null；都不选时 reason 说明原因。
single_allowed=false 的候选只能分配给合集或不选，不能为它创建单条。
raw_material=true 是报告尚未使用的原始资料，并不代表值得制作。旧事、背景、产品常驻页面、材料不足或与其他候选重复时都不选；有独立价值的新变化可以安排，episode.title 可填简短内容标题。
JSON: {"allocations":[{"candidate_id":"候选ID","reason":"分配原因",
"episode":{"summary":"合集材料摘要","source_ids":["S1"]},
"single":{"question":"独立问题标题","audience":"受众","angle":"howto",
"incremental_value":"观众能带走的收获","material":"展开该问题的材料与实例","source_ids":["S1"]}}]}"""


def _path(plan_id: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{64}", plan_id):
        raise ValueError("联合规划编号无效")
    return Path(store.db_path()).parent / "joint-plans" / f"{plan_id}.json"


def viewer_takeaway(value: str) -> str:
    """Keep the audience gain; drop planner wording that compares programme vs single."""
    text = (value or "").strip()
    if "合集" in text and "单条" in text:
        parts = re.split(r"单条(?:进一步)?", text, maxsplit=1)
        if len(parts) == 2:
            part = re.sub(r"^(?:给出|讲清|展开|把|说明)", "", parts[1].strip())
            part = part.strip(" ：:;；。")
            if part:
                return part
    return text


def writing_direction(single: dict | None = None, *, role: str = "single") -> str:
    """Author brief for one frozen assignment. Do not paste programme copy into the single."""
    if role == "episode":
        return "按各条摘要讲清变化和对观众的直接影响；完整操作步骤与费用比较不在这里展开。"
    if not single:
        raise ValueError("单条写作方向需要独立问题")
    takeaway = viewer_takeaway(single["incremental_value"])
    return f"本条只回答：{single['question']}。观众能带走：{takeaway}。"


def assignment(topic: dict) -> dict | None:
    ref = topic.get("joint_plan")
    if not ref:
        return None
    data = json.loads(_path(ref["id"]).read_text(encoding="utf-8"))
    if data.get("id") != ref["id"] or data.get("status") != "planned":
        raise ValueError("联合规划记录不完整")
    entry = dict(data["assignments"][ref["entry"]])
    if ref["entry"] == "episode":
        entry["direction"] = writing_direction(role="episode")
    else:
        entry["direction"] = writing_direction(entry["single"])
    return entry


def validate(value: dict, candidates: list[dict], packets: list[dict]) -> list[dict]:
    rows = value.get("allocations") if isinstance(value, dict) else None
    expected = {c["id"] for c in candidates}
    if not isinstance(rows, list) or len(rows) != len(expected):
        raise ValueError("联合规划必须覆盖全部候选")
    known = {p["id"] for p in packets}
    available = {c["id"] for c in candidates if c.get("single_allowed", True)}
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or row.get("candidate_id") not in expected - seen:
            raise ValueError("联合规划候选编号重复或不存在")
        seen.add(row["candidate_id"])
        if not isinstance(row.get("reason"), str) or not row["reason"].strip():
            raise ValueError("联合规划缺少分配理由")
        for role in ("episode", "single"):
            if role not in row:
                raise ValueError("联合规划必须明确分配或放弃合集和单条")
            part = row[role]
            if part is None:
                continue
            fields = ("summary",) if role == "episode" else ("question", "audience", "incremental_value", "material")
            if not isinstance(part, dict) or any(not isinstance(part.get(k), str) or not part[k].strip() for k in fields):
                raise ValueError("联合规划缺少具体问题或材料")
            if role == "episode" and "title" in part and (
                    not isinstance(part["title"], str) or not part["title"].strip()):
                raise ValueError("合集条目标题必须是非空文字")
            ids = part.get("source_ids")
            if not isinstance(ids, list) or not ids or any(not isinstance(i, str) or i not in known for i in ids):
                raise ValueError("联合规划引用了未知材料")
        single = row.get("single")
        if single:
            if row["candidate_id"] not in available:
                raise ValueError("联合规划不能重新加入已处理或未入队的单条")
            if single.get("angle", "") not in ("", "money", "howto"):
                raise ValueError("联合规划讲述角度无效")
            if row.get("episode") and not single.get("angle"):
                raise ValueError("合集与单条共用事件时需要明确的独立角度")
    return rows


def _organize(candidates: list[dict], packets: list[dict], previous: list[dict], config, *,
              channel: str = "flash", programme: str = "") -> tuple[list[dict], dict]:
    from core.llm import LLMClient
    from core.video.adapt.common import parse_json, AdaptError

    client = LLMClient(config)
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": json.dumps({
        "column": audience_strategy.CHANNELS[channel],
        "scope": ("本期是 AI 快讯，原始采集材料中的一般消费电子、航天和社会新闻，若没有具体 AI 能力、使用或成本变化则不选。"
                  if channel == "flash" else f"本期栏目：{programme}。严格围绕本栏目的研究对象，不扩成其他品牌或泛科技合集。"),
        "candidates": candidates,
        "materials": [{**p, "text": p["text"][:4000]} for p in packets],
        "previous": previous,
    }, ensure_ascii=False)}]
    try:
        for attempt in range(3):
            with telemetry.track("joint-planning"):
                reply = client.chat(messages, temperature=0)
            try:
                return validate(parse_json(reply.content), candidates, packets), dict(client.usage)
            except (AdaptError, ValueError, TypeError, KeyError) as exc:
                if attempt == 2:
                    raise
                messages.extend([{"role": "assistant", "content": reply.content},
                                 {"role": "user", "content": f"只修正格式与引用编号，不改材料：{exc}"}])
    finally:
        client.close()


def _compile(rows: list[dict], candidates: list[dict], packets: list[dict]) -> dict:
    by_id = {c["id"]: c for c in candidates}
    source = {p["id"]: p for p in packets}
    assignments = {}
    episode_parts, episode_items = [], []

    def links(ids):
        return "\n".join(dict.fromkeys(url for i in ids for url in source[i]["links"]))

    for row in rows:
        candidate = by_id[row["candidate_id"]]
        episode, single = row.get("episode"), row.get("single")
        if episode:
            title = episode.get("title") or candidate["title"]
            episode_items.append({"id": candidate["id"], "title": title})
            episode_parts.append(f"### {title}\n\n{episode['summary']}\n\n{links(episode['source_ids'])}")
        if single:
            assignments[candidate["id"]] = {
                "report": f"# {single['question']}\n\n{single['material']}\n\n{links(single['source_ids'])}",
                "material": "\n\n---\n\n".join(source[i]["text"] for i in dict.fromkeys(single["source_ids"])),
                "direction": writing_direction(single),
                "single": single,
            }
    if episode_items:
        assignments["episode"] = {
            "report": "# 本期快讯\n\n" + "\n\n".join(episode_parts), "material": "", "items": episode_items,
            "direction": writing_direction(role="episode"),
        }
    return assignments


def history_context(peers: list[dict]) -> list[dict]:
    """Keep every prior question visible without copying whole manuscripts.

    Actual event/angle protection still runs with complete history at writing.
    """
    from core.video import story_pool
    return [{"title": row["title"], "summary": row.get("summary", "")[:400],
             "angle": row.get("story", {}).get("angle", ""), "sources": row["sources"]}
            for row in story_pool.context(peers)]


def _episode_key(production: str, topic: dict) -> tuple | None:
    """Whole-report topics intentionally have id=None; never use that as coverage."""
    episode = topic.get("episode") or {}
    revision = episode.get("report_identity") or topic.get("report_identity")
    if revision:
        return production, "revision", revision
    if topic.get("report_id"):
        return production, "report", topic["report_id"]
    path = topic.get("from_report") or episode.get("report_path")
    if path:
        return production, "path", str(Path(path).resolve())
    return (production, "topic", topic["id"]) if topic.get("id") else None


def available_items(items: list[dict], connection=None) -> list[dict]:
    """Recheck the final angle IDs, including claims made while the model ran.

    With a caller's write transaction this check and queue insertion are atomic.
    Episodes reserve their production/report pair, not every use of that report.
    """
    from contextlib import nullcontext
    with nullcontext(connection) if connection is not None else store.connect() as db:
        taken, episodes = set(), set()
        for row in db.execute("SELECT topic,production,archived,status FROM scripts s WHERE status!='FAILED'"
                              " OR EXISTS(SELECT 1 FROM publications p WHERE p.script_id=s.id)"):
            topic = json.loads(row["topic"])
            taken.add(topic.get("id"))
            taken.update(topic.get("items") or [])
            if topic.get("content_mode") == "episode" and not row["archived"] and row["status"] != "FAILED":
                key = _episode_key(row["production"], topic)
                if key is not None:
                    episodes.add(key)
        for row in db.execute("SELECT topic,production FROM autopilot_items WHERE state NOT IN ('FAILED','CANCELLED')"):
            topic = json.loads(row["topic"])
            taken.add(topic.get("id"))
            if topic.get("content_mode") == "episode":
                key = _episode_key(row["production"], topic)
                if key is not None:
                    episodes.add(key)
        dismissed = {r[0] for r in db.execute("SELECT topic_id FROM dismissed_topics")}
    taken.discard(None)
    result = []
    for item in items:
        topic = item["topic"]
        identity = topic.get("id")
        episode = topic.get("content_mode") == "episode"
        key = _episode_key(item.get("production", ""), topic) if episode else None
        if identity in dismissed or (key in episodes if episode else identity in taken):
            continue
        result.append(item)
        if episode and key is not None:
            episodes.add(key)
        elif not episode and identity:
            taken.add(identity)
    return result


def enabled_for(spec: dict, config_dir: str) -> bool:
    """Whether this report batch can need a slow shared allocation."""
    if not spec.get("report") or spec.get("job") not in audience_strategy.JOBS:
        return False
    try:
        config = load_yaml((Path(config_dir) / "joint_planning.yaml").read_text()) or {}
        productions = {p.get("production") for p in spec.get("picks", [])}
        return config.get("enabled") is True and any(
            p.get("episode_production") in productions and p.get("single_production") in productions
            for p in config.get("pairs", [config]))
    except Exception:
        return False


def apply(spec: dict, items: list[dict], config_dir: str, jobs_dir: str, workspace: str,
          llm_config, notes: list | None = None, rank_raw=None) -> list[dict]:
    """Only new paired batches opt in. A failed planner returns the original queue."""
    config_path = Path(config_dir) / "joint_planning.yaml"
    if not config_path.is_file():
        return items
    try:
        config = load_yaml(config_path.read_text()) or {}
        if not isinstance(config, dict):
            raise ValueError("联合规划配置必须是映射")
        pairs = config.get("pairs", [config])
        if not isinstance(pairs, list) or any(not isinstance(p, dict) for p in pairs):
            raise ValueError("联合规划的 pairs 必须是配置列表")
    except Exception as exc:
        logger.warning("Joint planning configuration unavailable: %s", exc)
        if notes is not None:
            notes.append("联合规划配置不可读，保留原选题流程")
        return items
    if config.get("enabled") is not True or not spec.get("report"):
        return items
    episodes, singles = [], []
    for pair in pairs:
        episode_id, single_id = pair.get("episode_production"), pair.get("single_production")
        episodes = [i for i in items if i.get("production") == episode_id and i["topic"].get("content_mode") == "episode"]
        singles = [i for i in items if i.get("production") == single_id and i["topic"].get("from_item") and not i["topic"].get("angle")]
        if len(episodes) == 1 and singles:
            config = {"enabled": True, **pair}
            break
    if len(episodes) != 1 or not singles:
        return items
    channel = audience_strategy.JOBS.get(spec.get("job"))
    if channel not in audience_strategy.CHANNELS:
        return items
    try:
        episode = episodes[0]
        report_path = Path(workspace) / spec["report"]
        report = report_path.read_text()
        # Production reconciliation supplies an absolute path; metadata is keyed
        # by workspace-relative output paths, unlike isolated/manual batches.
        metadata = (report_meta.get(spec["report"])
                    or report_meta.get(os.path.relpath(report_path, workspace)) or {})
        material = report_material.read(metadata.get("material") or {}, str(report_path), report) or report
        packets = [{"id": p["id"], "text": p["text"], "links": [p["url"]] if p["url"] else []}
                   for p in audience_strategy.packets(material)]
        # A partial/manual queue must retain episode-only entries, not silently drop them.
        from core.video import excerpt, content_guard, story_pool
        consolidated = {}
        for packet in packets:
            key = story_pool._anchored(packet["links"][0]) if packet["links"] else packet["id"]
            if key not in consolidated or len(packet["text"]) > len(consolidated[key]["text"]):
                consolidated[key] = packet
        packets = list(consolidated.values())
        available = {i["topic"]["id"] for i in singles}
        candidates = [{"id": c["id"], "title": c["title"],
                       "material": excerpt.item_excerpt(report, c["title"]) or c["title"],
                       "single_allowed": c["id"] in available}
                      for c in episode["topic"]["episode"]["items"]]
        if channel == "flash" and any(i.get("pick", {}).get("all") for i in singles):
            # Bring the existing bounded raw-material recovery forward. This
            # prevents the joint brief from starving singles when no report
            # candidate supports an independent question.
            from core.video import autopilot_store as ap
            taken = store.topic_ids_in_use() | store.published_topic_ids() | ap.used_topic_ids() | store.dismissed_topic_ids()
            cited = set().union(*(story_pool.cited(c["material"]) for c in candidates))
            unused = {}
            for packet in packets:
                if not packet["links"] or story_pool.cited_packet(packet, cited):
                    continue
                anchor = story_pool._anchored(packet["links"][0])
                if anchor not in unused or len(packet["text"]) > len(unused[anchor]["text"]):
                    unused[anchor] = packet
            for packet in list(unused.values())[:40]:
                raw_id = 'flash-raw:' + hashlib.sha256(json.dumps([packet["links"][0], packet["text"]]).encode()).hexdigest()[:24]
                if raw_id in taken or any(f'{raw_id}~{angle}' in taken for angle in ('money', 'howto')):
                    continue
                title = packet["text"].splitlines()[0].lstrip('# ')
                candidates.append({"id": raw_id, "title": title, "material": packet["text"][:900],
                                   "source_id": packet["id"], "single_allowed": True, "raw_material": True})
                available.add(raw_id)
                singles.append({"column_key": singles[0]["column_key"], "production": single_id,
                                "pick": singles[0]["pick"], "topic": {
                                    "id": raw_id, "title": title, "from_report": spec["report"],
                                    "from_item": raw_id, "from_item_title": title, "fields": {},
                                    "audience_strategy": "flash", "matrix_dedup": singles[0]["topic"].get("matrix_dedup", {})}})
        cited = set().union(*(story_pool.cited(c["material"]) for c in candidates if not c.get("raw_material")))
        raw_sources = {c["source_id"] for c in candidates if c.get("raw_material")}
        selected_packets = [p for p in packets if p["id"] in raw_sources or story_pool.cited_packet(p, cited)]
        if selected_packets:
            packets = selected_packets
        if not candidates or not packets:
            return items
        fingerprint = json.dumps([VERSION, report, material, candidates, config], sort_keys=True, ensure_ascii=False)
        plan_id = hashlib.sha256(fingerprint.encode()).hexdigest()
        path = _path(plan_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with file_lock(str(path), timeout=60):
            if path.exists():
                plan = json.loads(path.read_text())
            else:
                previous = content_guard.coverage(singles[0]["topic"], config_dir, jobs_dir)
                from core.video import content_columns
                owner = content_columns.for_production(config_dir, episode_id)
                programme = f"{owner.name}；{owner.promise}" if owner else channel
                rows, usage = _organize(candidates, packets, history_context(previous),
                                        llm_config(singles[0]["column_key"]), channel=channel, programme=programme)
                if any(row.get("single") and row["candidate_id"] not in available for row in rows):
                    raise ValueError("联合规划不能重新加入已处理或未入队的单条")
                plan = {"id": plan_id, "version": VERSION, "status": "planned", "report": spec["report"],
                        "allocations": rows, "usage": usage, "assignments": _compile(rows, candidates, packets)}
                atomic_write(str(path), json.dumps(plan, ensure_ascii=False, indent=2))
        result = []
        assigned = plan["assignments"]
        if "episode" in assigned:
            item = copy.deepcopy(episode)
            item["topic"]["joint_plan"] = {"id": plan_id, "entry": "episode"}
            item["topic"]["episode"]["items"] = assigned["episode"]["items"]
            result.append(item)
        for original in singles:
            entry = original["topic"]["id"]
            if entry not in assigned:
                continue
            item = copy.deepcopy(original)
            part = assigned[entry]["single"]
            topic = item["topic"]
            topic.update(joint_plan={"id": plan_id, "entry": entry}, title=part["question"],
                         from_item_title=part["question"], preserve_audience_scope=True, _story_fallbacks=[])
            topic["fields"] = {**topic.get("fields", {}), "目标受众": part["audience"],
                               "核心角度": viewer_takeaway(part["incremental_value"])}
            if part.get("angle"):
                topic.update(id=f"{entry}~{part['angle']}", base_id=entry, angle=part["angle"])
            result.append(item)
        result += [i for i in items if i.get("production") not in (episode_id, single_id)]
        raw = [i for i in result if i.get("production") == single_id
               and i["topic"]["joint_plan"]["entry"].startswith("flash-raw:")]
        if raw and rank_raw is not None:
            ranked, note = rank_raw(raw)
            kept = {topic["id"]: topic for topic in ranked}
            result = [i for i in result if i not in raw or i["topic"]["id"] in kept]
            for item in raw:
                if item["topic"]["id"] in kept and kept[item["topic"]["id"]].get("heat"):
                    item["topic"]["heat"] = kept[item["topic"]["id"]]["heat"]
            if note and notes is not None:
                notes.append(note)
        available_result = available_items(result)
        if notes is not None and len(available_result) < len(result):
            notes.append(f"规划后的选题已有任务占用，跳过 {len(result) - len(available_result)} 项")
        result = available_result
        if notes is not None:
            notes.append(f"合集与单条已共同规划：合集 {len(assigned.get('episode', {}).get('items', []))} 项，单条 {sum(i.get('production') == single_id for i in result)} 条；规划 {plan_id[:12]}")
        return result
    except Exception as exc:
        logger.warning("Joint planning fell back to existing selection: %s", exc)
        if notes is not None:
            notes.append(f"联合规划未完成（{type(exc).__name__}），保留原选题流程")
        return items
