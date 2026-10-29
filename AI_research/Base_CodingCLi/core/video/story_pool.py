"""Shared report candidates and durable main-event reservations for automatic videos.

The model organizes supplied material; it never creates event identifiers or URLs.
Only selected video events reserve work. Reading a research report reserves nothing.
"""
from __future__ import annotations

from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import re
import unicodedata
import uuid

from core.fileio import file_lock
from core.video import store

PROMPT_VERSION = 3
POLICY = "matrix-story-v3"
ACTIONS = {"release", "pricing", "results", "policy", "financing", "earnings", "discovery", "event", "analysis"}
SCHEMA = """
CREATE TABLE IF NOT EXISTS video_story_pools (
    id TEXT PRIMARY KEY, scope TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS video_story_claims (
    id TEXT PRIMARY KEY, scope TEXT NOT NULL, event_key TEXT NOT NULL,
    script_id TEXT NOT NULL UNIQUE, pool_id TEXT NOT NULL, story TEXT NOT NULL,
    state TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_video_story_occupied
    ON video_story_claims(scope,event_key) WHERE state IN ('ACTIVE','COVERED');
CREATE TABLE IF NOT EXISTS video_story_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, script_id TEXT NOT NULL, pool_id TEXT NOT NULL,
    decision TEXT NOT NULL, title TEXT NOT NULL, peer_id TEXT NOT NULL DEFAULT '',
    reason TEXT NOT NULL, created_at TEXT NOT NULL
);
"""


class SelectionError(RuntimeError):
    pass


class NoCandidate(SelectionError):
    def __init__(self, decisions: list[dict], pool_id: str):
        super().__init__("候选主事件均已占用或覆盖，排重后没有新的可写选题")
        self.decisions, self.pool_id = decisions, pool_id


def enabled(topic: dict) -> bool:
    return (topic.get("matrix_dedup") or {}).get("policy") == POLICY


def init(conn) -> None:
    conn.executescript(SCHEMA)


def _normal(value: str) -> str:
    return re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", value).casefold())


def _value(value: str) -> str:
    # Dots and decimal punctuation distinguish versions/prices (1.0 != 10).
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value).casefold())


def _edition(value: str) -> str:
    return re.sub(r"^(?:version|v|版本)[:：]?(?=\d)", "", _value(value))


def _hash(*values) -> str:
    return hashlib.sha256(json.dumps(values, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def packets(report: str, material: str = "") -> list[dict]:
    from core.provenance import extract_urls
    from core.video import content_guard as guard

    text = material or report
    bibliography = {}
    for line in text.splitlines():
        match = re.match(r"\s*(?:\[(\d+)\]|(\d+)[.、)])\s*", line)
        if match and (links := extract_urls(line)):
            bibliography[match.group(1) or match.group(2)] = links
    result = []
    for part in re.split(r"\n\s*---\s*\n|(?m:^(?=### ))", text):
        part = part.strip()
        if not part or re.match(r"^#{1,6}\s*(?:\d+[.、 ]*)?(?:来源清单|参考文献|Fetched sources)", part, re.I):
            continue
        references = [u for n in re.findall(r"\[(\d+)\]", part) for u in bibliography.get(n, [])]
        links = list(dict.fromkeys(u for u in extract_urls(part)+references if guard.source_key(u)))
        if not links:
            continue
        heading = part.splitlines()[0].lstrip("# ")
        if references:
            part += "\n\n本条引用：\n" + "\n".join(dict.fromkeys(references))
        result.append({"id": f"S{len(result)+1}", "title": heading, "text": part, "links": links})
    if not result and report.strip():
        result = [{"id": "S1", "title": report.splitlines()[0].lstrip("# "), "text": report, "links": []}]
    return result


def changes(value: list[dict]) -> list[dict]:
    """An explicit changed result/edition can be new; a headline or popularity counter cannot."""
    ignored = {"title", "headline", "wording", "url", "source", "reportdate", "likes", "stars", "downloads",
               "标题", "措辞", "链接", "来源", "报告日期", "热度", "点赞", "下载量"}
    counters = ("stars", "starcount", "likes", "likecount", "downloads", "downloadcount", "trendingrank",
                "trending score", "点赞数", "收藏数", "下载量", "下载次数", "趋势排名", "热度分数")
    result = []
    for row in value:
        if not isinstance(row, dict):
            raise SelectionError("新进展应为字段、原值与新值")
        item = {k: str(row[k]).strip() if row.get(k) is not None else "" for k in ("dimension", "before", "after")}
        dimension = _normal(item["dimension"])
        if (all(item.values()) and dimension not in ignored and not dimension.endswith(tuple(_normal(c) for c in counters))
                and _value(item["before"]) != _value(item["after"])):
            result.append(item)
    return result


def context(peers: list[dict]) -> list[dict]:
    """Keep legacy frozen event summaries as evidence without backfilling their versions."""
    peers = list({(p["script_id"], (p.get("story") or {}).get("event_key") or p.get("version") or p["title"]): p
                  for p in peers}.values())
    return [{"ref": f"H{i+1}", "script_id": p["script_id"], "title": p["title"],
             "version": p.get("version"),
             "summary": p.get("summary", ""), "story": p.get("story") or {},
             "sources": sorted(p["sources"])} for i, p in enumerate(peers)]


def organize(report: str, source_packets: list[dict], previous: list[dict], llm_config) -> tuple[list[dict], dict]:
    """One cached organization call over existing text, not a research or fact-check step."""
    from core.llm import LLMClient

    system = """把给定素材整理成视频选题事件池，只使用输入，不检索、不重新核算。
把同一主事件的不同报道合成一条；一个主体的不同版本、不同结果、不同政策变化可以分别成题。
subject 使用具体产品/事项名称，action 从 release/pricing/results/policy/financing/earnings/discovery/event/analysis 选择。
edition 填材料明确给出的版本/财报期/轮次。event_date 只填材料明确给出的单日事件日期，格式 YYYY-MM-DD，未知留空；时间段、日期范围与历史时期放在 edition 或 summary 中，event_date 留空。报告日期不等于事件日期。
primary 填核心证据的 S 编号，background 填辅助背景 S 编号。新增参考、热度、下载量或换标题不构成新事件。
same_as 填已经覆盖该主事件的 H 编号；有实际新进展仍关联旧事件，并在 changes 中写明发生变化的维度及前后值。
不确定有新进展时不得编造 changes。保持各候选与实际材料一致，优先排列适合讲述的具体事件。
JSON: {"events":[{"title":"选题标题","subject":"具体对象","action":"release","edition":"","event_date":"",
"summary":"事件与本次看点","primary":["S1"],"background":[],"same_as":[],
"changes":[{"dimension":"明确变化的维度","before":"原值","after":"新值"}]}]}"""
    request = {"report": report,
               "materials": [{**p, "text": p["text"][:2400]} for p in source_packets],
               "covered": [{k: v for k, v in p.items() if k != "script_id"} for p in previous]}
    client = LLMClient(llm_config)
    reply = client.chat([{"role": "system", "content": system},
                         {"role": "user", "content": json.dumps(request, ensure_ascii=False)}], temperature=0)
    try:
        raw = reply.content.strip()
        start, end = raw.find("{"), raw.rfind("}")
        items = json.loads(raw[start:end+1])["events"]
        return validate(items, source_packets, previous), dict(client.usage)
    except (ValueError, KeyError, TypeError) as exc:
        raise SelectionError("候选事件返回格式无效") from exc


def validate(items, source_packets: list[dict], previous: list[dict]) -> list[dict]:
    if not isinstance(items, list):
        raise SelectionError("候选事件应为列表")
    by_source, by_peer = {p["id"]: p for p in source_packets}, {p["ref"]: p for p in previous}
    output = {}
    for raw in items:
        if not isinstance(raw, dict):
            raise SelectionError("候选事件格式无效")
        event = {key: str(raw[key]).strip() if raw.get(key) is not None else "" for key in
                 ("title", "subject", "action", "edition", "event_date", "summary")}
        if not all(event[k] for k in ("title", "subject", "summary")) or event["action"] not in ACTIONS:
            raise SelectionError("候选缺少主事件信息")
        primary, background, same_as = (raw.get(k) or [] for k in ("primary", "background", "same_as"))
        if (not isinstance(primary, list) or not primary or not isinstance(background, list)
                or not isinstance(same_as, list) or not all(isinstance(k, str) for k in primary+background+same_as)
                or set(primary+background)-by_source.keys() or set(same_as)-by_peer.keys()):
            raise SelectionError("候选必须引用已有素材和历史编号")
        if event["event_date"]:
            try:
                event["event_date"] = datetime.strptime(event["event_date"], "%Y-%m-%d").date().isoformat()
            except ValueError as exc:
                raise SelectionError("事件日期应为 YYYY-MM-DD 或空") from exc
        delta = raw.get("changes") or []
        if not isinstance(delta, list):
            raise SelectionError("新进展应为列表")
        event["changes"] = changes(delta)
        # Explicit edition metadata already carries a real release change. Do
        # not require the organizer to repeat it in a free-form changes field.
        if event["action"] == "release" and event["edition"] and not event["changes"]:
            previous_editions = [(by_peer[ref].get("story") or {}).get("edition", "") for ref in same_as]
            previous_editions = [v for v in previous_editions if v]
            if previous_editions and _edition(event["edition"]) not in {_edition(v) for v in previous_editions}:
                event["changes"] = [{"dimension": "edition", "before": previous_editions[0], "after": event["edition"]}]
        event["primary"], event["background"] = list(dict.fromkeys(primary)), list(dict.fromkeys(background))
        event["previous"] = list(dict.fromkeys(by_peer[k]["script_id"] for k in same_as))
        event["previous_refs"] = [{"script_id": by_peer[k]["script_id"], "version": by_peer[k].get("version"),
                                   "event_key": (by_peer[k].get("story") or {}).get("event_key", "")} for k in same_as]
        event["family_key"] = _hash(_normal(event["subject"]), event["action"])
        if same_as and not event["changes"]:
            peer = by_peer[same_as[0]]
            event["event_key"] = (peer.get("story") or {}).get("event_key") or _hash(
                "legacy", peer["script_id"], peer.get("version") or peer["title"])
        elif event["action"] == "release" and event["edition"] and event["event_date"]:
            # Different descriptions of one dated version release still reserve
            # one event. A later release phase keeps its own actual event date.
            event["event_key"] = _hash(event["family_key"], _edition(event["edition"]), event["event_date"])
        elif event["changes"]:
            event["event_key"] = _hash(event["family_key"], _edition(event["edition"]),
                                        sorted((_normal(v["dimension"]), _value(v["after"])) for v in event["changes"]))
        else:
            # An entity alone is not an event. With no edition/date, retain the
            # concrete evidence; semantic aliases are linked through same_as.
            evidence = sorted(by_source[k]["links"][0] if by_source[k]["links"] else by_source[k]["text"]
                              for k in primary) if not event["edition"] and not event["event_date"] else []
            event["event_key"] = _hash(event["family_key"], _edition(event["edition"]), event["event_date"], evidence)
        event["primary_sources"] = list(dict.fromkeys(u for k in primary for u in by_source[k]["links"]))
        key = event["event_key"]
        if key in output:
            for field in ("primary", "background", "previous", "previous_refs", "primary_sources"):
                output[key][field] = list({_hash(v): v for v in output[key][field]+event[field]}.values())
        else:
            output[key] = event
    return list(output.values())


def load_or_create(scope: str, report: str, material: str, peers: list[dict], llm_config) -> dict:
    pool_id = _hash(PROMPT_VERSION, scope, report, material)
    lock = Path(store.db_path()).parent / ("story-pool-" + pool_id)
    with file_lock(str(lock), timeout=60):
        with store.connect() as conn:
            row = conn.execute("SELECT payload FROM video_story_pools WHERE id=?", (pool_id,)).fetchone()
        if row:
            return json.loads(row["payload"])
        source_packets = packets(report, material)
        events, usage = organize(report, source_packets, context(peers), llm_config)
        pool = {"id": pool_id, "scope": scope, "packets": source_packets, "events": events, "usage": usage,
                "prompt_version": PROMPT_VERSION}
        with store.connect(write=True) as conn:
            conn.execute("INSERT INTO video_story_pools VALUES(?,?,?,?)",
                         (pool_id, scope, json.dumps(pool, ensure_ascii=False), store._now()))
        return pool


def reconcile() -> None:
    """Recover reservations from authoritative script/queue state, without editing any script."""
    cutoff = (datetime.now().astimezone()-timedelta(days=30)).isoformat()[:10]
    from core.video import content_guard as guard

    with store.connect() as conn:
        if not conn.execute("SELECT 1 FROM video_story_claims WHERE state IN ('ACTIVE','COVERED') LIMIT 1").fetchone():
            return
    receipts = guard.upload_protected(Path(store.db_path()).parent)
    with store.connect(write=True) as conn:
        for row in conn.execute("SELECT c.script_id,c.created_at,s.archived,s.status,s.current_version,"
                                "EXISTS(SELECT 1 FROM publications p WHERE p.script_id=c.script_id) AS published,"
                                "EXISTS(SELECT 1 FROM autopilot_items i WHERE i.script_id=c.script_id "
                                "AND i.state IN ('CANCELLED','FAILED')) AS cancelled "
                                "FROM video_story_claims c LEFT JOIN scripts s ON s.id=c.script_id "
                                "WHERE c.state IN ('ACTIVE','COVERED')").fetchall():
            protected = row["published"] or row["script_id"] in receipts
            abandoned = row["status"] is None or row["archived"] or row["status"] == "FAILED" or (
                row["cancelled"] and not row["current_version"])
            if row["created_at"] < cutoff or (abandoned and not protected):
                conn.execute("UPDATE video_story_claims SET state='RELEASED',updated_at=? WHERE script_id=?",
                             (store._now(), row["script_id"]))
        conn.execute("UPDATE video_story_claims SET state='COVERED',updated_at=? WHERE state='ACTIVE' "
                     "AND EXISTS(SELECT 1 FROM scripts s WHERE s.id=video_story_claims.script_id AND s.current_version>0)", (store._now(),))


def reservations(scope: str, script_id: str = "") -> list[dict]:
    from core.video import content_guard as guard

    with store.connect() as conn:
        rows = conn.execute("SELECT script_id,story FROM video_story_claims WHERE scope=? AND script_id!=? "
                            "AND state IN ('ACTIVE','COVERED')", (scope, script_id)).fetchall()
    return [{"script_id": r["script_id"], "title": (s := json.loads(r["story"]))["title"],
             "summary": s["summary"], "story": s,
             "sources": {k for u in s["primary_sources"] if (k := guard.source_key(u))}} for r in rows]


def _same_event(left: dict, right: dict) -> bool:
    if left["event_key"] == right.get("event_key"):
        return True
    if left.get("changes"):
        return False
    if left["family_key"] != right.get("family_key"):
        return False
    if left["edition"] and right.get("edition"):
        return _edition(left["edition"]) == _edition(right["edition"])
    return bool(left["event_date"] and left["event_date"] == right.get("event_date"))


def duplicate(event: dict, peers: list[dict]) -> list[dict]:
    from core.video import content_guard as guard

    def alias(peer):
        refs = event.get("previous_refs")
        if refs is None:  # pools written before version-specific aliases
            return peer["script_id"] in event["previous"]
        for ref in refs:
            if ref["script_id"] != peer["script_id"]:
                continue
            if ref["event_key"]:
                if ref["event_key"] == (peer.get("story") or {}).get("event_key"):
                    return True
            elif ref["version"] is None or ref["version"] == peer.get("version"):
                return True
        return False

    result = [p for p in peers if _same_event(event, p.get("story") or {}) or (not event["changes"] and alias(p))]
    if not event["changes"]:
        result += guard.duplicate_peers({guard.source_key(u) for u in event["primary_sources"] if guard.source_key(u)}, peers)
    by_id = {}
    for peer in result:
        by_id.setdefault(peer["script_id"], peer)
    return list(by_id.values())


def take(script_id: str, pool: dict, peers: list[dict]) -> tuple[dict, list[dict]]:
    """Reserve one remaining event and freeze it on the unstarted script in the same transaction."""
    from core.video import content_guard as guard

    if guard.protected(script_id):
        raise SelectionError("已发布或有投稿回执的文稿不重新分配选题")
    reconcile()
    decisions, selected = [], None
    with store.connect(write=True) as conn:
        row = conn.execute("SELECT topic,current_version,archived FROM scripts s WHERE id=? AND NOT EXISTS "
                           "(SELECT 1 FROM publications p WHERE p.script_id=s.id) AND NOT EXISTS "
                           "(SELECT 1 FROM autopilot_items i WHERE i.script_id=s.id AND i.state IN ('CANCELLED','FAILED'))",
                           (script_id,)).fetchone()
        if not row or row["current_version"] or row["archived"]:
            raise SelectionError("文稿已有版本或已归档，不重新分配主事件")
        existing = conn.execute("SELECT story FROM video_story_claims WHERE script_id=? AND state='ACTIVE'", (script_id,)).fetchone()
        if existing:
            return json.loads(existing["story"]), []
        occupied = [{"script_id": r["script_id"], "title": (s := json.loads(r["story"]))["title"],
                     "story": s, "sources": {k for u in s["primary_sources"] if (k := guard.source_key(u))}}
                    for r in conn.execute("SELECT * FROM video_story_claims WHERE scope=? AND script_id!=? "
                                          "AND state IN ('ACTIVE','COVERED')", (pool["scope"], script_id))]
        for event in pool["events"]:
            hits = duplicate(event, peers+occupied)
            if hits:
                decisions.append({"decision": "skip", "title": event["title"], "peer_id": hits[0]["script_id"],
                                  "reason": "主事件已覆盖或正在写稿："+hits[0]["title"]})
                continue
            selected = {**event, "pool_id": pool["id"], "scope": pool["scope"]}
            topic = json.loads(row["topic"])
            topic.update(story=selected, title=selected["title"])
            conn.execute("INSERT INTO video_story_claims VALUES(?,?,?,?,?,?,'ACTIVE',?,?) "
                         "ON CONFLICT(script_id) DO UPDATE SET scope=excluded.scope,event_key=excluded.event_key,"
                         "pool_id=excluded.pool_id,story=excluded.story,state='ACTIVE',created_at=excluded.created_at,updated_at=excluded.updated_at",
                         (uuid.uuid4().hex, pool["scope"], selected["event_key"], script_id, pool["id"],
                          json.dumps(selected, ensure_ascii=False), store._now(), store._now()))
            conn.execute("UPDATE scripts SET topic=?,updated_at=? WHERE id=?",
                         (json.dumps(topic, ensure_ascii=False), store._now(), script_id))
            decisions.append({"decision": "select", "title": selected["title"], "peer_id": "", "reason": "已占用主事件，开始写稿"})
            break
        if selected is None:
            decisions.append({"decision": "exhausted", "title": "", "peer_id": "", "reason": "排重后没有新的可写候选，少做一条"})
        for value in decisions:
            conn.execute("INSERT INTO video_story_decisions(script_id,pool_id,decision,title,peer_id,reason,created_at) VALUES(?,?,?,?,?,?,?)",
                         (script_id, pool["id"], value["decision"], value["title"], value["peer_id"], value["reason"], store._now()))
    if selected is None:
        raise NoCandidate(decisions, pool["id"])
    return selected, decisions


def material_for(pool: dict, story: dict) -> str:
    by_id = {p["id"]: p for p in pool["packets"]}
    primary = "\n\n---\n\n".join(by_id[key]["text"] for key in story["primary"])
    background = "\n\n---\n\n".join(by_id[key]["text"] for key in story["background"] if key not in story["primary"])
    return "## 主事件材料\n\n" + primary + ("\n\n## 辅助背景（不另作主事件）\n\n" + background if background else "")


def get(pool_id: str) -> dict:
    with store.connect() as conn:
        row = conn.execute("SELECT payload FROM video_story_pools WHERE id=?", (pool_id,)).fetchone()
    if not row:
        raise SelectionError("已选事件的材料池不存在")
    return json.loads(row["payload"])


def brief(story: dict) -> str:
    return ("本期只围绕已选主事件写稿，不从背景材料另选主事件。\n" +
            json.dumps({k: story[k] for k in ("title", "subject", "action", "edition", "event_date", "summary",
                                             "primary_sources", "changes")}, ensure_ascii=False))


def decisions(script_id: str) -> list[dict]:
    with store.connect() as conn:
        return [dict(row) for row in conn.execute("SELECT decision,title,peer_id,reason FROM video_story_decisions WHERE script_id=? ORDER BY id",
                                                 (script_id,))]


def selection(script_id: str) -> dict:
    with store.connect() as conn:
        claim = conn.execute("SELECT pool_id,story FROM video_story_claims WHERE script_id=? AND state IN ('ACTIVE','COVERED')",
                             (script_id,)).fetchone()
        last = conn.execute("SELECT pool_id FROM video_story_decisions WHERE script_id=? ORDER BY id DESC LIMIT 1",
                            (script_id,)).fetchone()
        pool_id = claim["pool_id"] if claim else last["pool_id"] if last else ""
        row = conn.execute("SELECT payload FROM video_story_pools WHERE id=?", (pool_id,)).fetchone()
    values = decisions(script_id)
    if not values:
        return {}
    return {"pool_id": pool_id, "candidates": len(json.loads(row["payload"])["events"]) if row else 0,
            "selected": json.loads(claim["story"])["title"] if claim else "", "decisions": values,
            "skipped": sum(v["decision"] == "skip" for v in values)}
