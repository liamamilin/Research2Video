"""Keep validation samples out of publishing and avoid retelling a matrix's recent stories.

Identity comes from cited article URLs, not the generated headline, date code or production recipe.
All automatic configurations share the source matrix's 30-day memory, including the same report.
Published versions (including published validation samples) remain evidence even after local archival.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from functools import lru_cache
import json
import re
from pathlib import Path
import sqlite3
from urllib.parse import urlsplit

from core import events, matrices
from core.provenance import extract_urls, normalize_url
from core.video import store

SAMPLE_CREATORS = ("chain-validation", "prompt-quality-validation", "多矩阵质量验收")
MEMORY_DAYS = 30
MAIN_STORY_SHARE = 1.0
# All platform candidate queries use the same policy, including archived historical renders.
PUBLISHABLE_SQL = ("s.created_by NOT IN ('chain-validation','prompt-quality-validation','多矩阵质量验收') "
                   "AND COALESCE(json_extract(s.topic,'$.test_sample'),0)=0 "
                   "AND json_extract(s.topic,'$.content_archive') IS NULL")


def is_sample(script: dict) -> bool:
    return script.get("created_by") in SAMPLE_CREATORS or bool((script.get("topic") or {}).get("test_sample"))


def publishing_block(script: dict) -> str:
    if is_sample(script):
        return "这是验收样片，已与正式投稿隔离。"
    archived = (script.get("topic") or {}).get("content_archive")
    return f"这篇文稿已因内容重复归档：{archived.get('reason', '')}" if archived else ""


def with_policy(script: dict, jobs_dir: str = "") -> dict:
    """Infer policy in memory for older automatic drafts, including dated flash topic IDs."""
    topic = script.get("topic") or {}
    if script.get("created_by") != "autopilot" or any((topic.get("matrix_dedup") or {}).get(k) for k in ("job", "matrix")):
        return script
    job = (script.get("content_context") or {}).get("job", "")
    with store.connect() as conn:
        row = conn.execute("SELECT job FROM research_reports WHERE id=? OR report_path=? ORDER BY job DESC LIMIT 1",
                           (topic.get("report_id") or script.get("report_id"), topic.get("from_report", ""))).fetchone()
        source = conn.execute("SELECT source FROM topics WHERE id=?", (topic.get("id", ""),)).fetchone()
    job = job or (row["job"] if row else "")
    if not job and source and jobs_dir:
        from core.video import topic_store

        found = topic_store.resolve(jobs_dir, source["source"])
        job = found.job if found else ""
    return {**script, "topic": policy_topic(topic, job)} if job else script


def policy_topic(topic: dict, job: str) -> dict:
    return {**topic, "matrix_dedup": {"job": job, "policy": "matrix-source-v2"}} if job else topic


def new_topic(topic: dict, context: dict, config_dir: str, jobs_dir: str) -> dict:
    """New matrix videos share selection across entry points; rewrites keep their event."""
    if topic.get("matrix_dedup") or topic.get("article") or topic.get("local_video") or topic.get("test_sample"):
        return topic
    candidates = [context.get("job", "")]
    with store.connect() as conn:
        row = conn.execute("SELECT job FROM research_reports WHERE id=? OR report_path=? ORDER BY job DESC LIMIT 1",
                           (topic.get("report_id", ""), topic.get("from_report", ""))).fetchone()
        source = conn.execute("SELECT source FROM topics WHERE id=?", (topic.get("id", ""),)).fetchone()
        parents = conn.execute("SELECT topic,content_context FROM scripts WHERE "
            "(?!='' AND id=?) OR (?!='' AND report_id=?) OR (?!='' AND report_path=?) ORDER BY created_at LIMIT 20",
            (topic.get("parent_script", ""), topic.get("parent_script", ""), topic.get("report_id", ""),
             topic.get("report_id", ""), topic.get("from_report", ""), topic.get("from_report", ""))).fetchall()
    candidates.append(row["job"] if row else "")
    if source and jobs_dir:
        from core.video import topic_store

        found = topic_store.resolve(jobs_dir, source["source"])
        candidates.append(found.job if found else "")
    for parent in parents:
        candidates += [json.loads(parent["content_context"] or "{}").get("job", ""),
                       (json.loads(parent["topic"]).get("matrix_dedup") or {}).get("job", "")]
    members = {job for matrix in matrices.load_all(config_dir).values() for job in matrix.jobs}
    job = next((value for value in candidates if value in members), "")
    if not job:
        return topic
    from core.video import story_pool

    return {**topic, "matrix_dedup": {"job": job, "policy": story_pool.POLICY}}


def published(script_id: str) -> bool:
    with store.connect() as conn:
        return bool(conn.execute("SELECT 1 FROM publications WHERE script_id=? LIMIT 1", (script_id,)).fetchone())


def upload_protected(state: Path) -> set[str]:
    """Include pending/uncertain platform receipts before the publication ledger catches up."""
    protected = set()
    files = [(state / "platforms.db", "uploads"), (state / "douyin.db", "jobs")]
    files += [(p, "jobs") for p in (state / "platforms").glob("*/queue.db")]
    for path, table in files:
        if not path.is_file():
            continue
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as conn:
            conn.row_factory = sqlite3.Row
            if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                continue  # a worker may have created its DB before initializing the queue
            for raw in conn.execute(f"SELECT * FROM {table}"):
                row = dict(raw)
                if table == "jobs" and row.get("kind") != "UPLOAD":
                    continue
                if row.get("script_id") and (row["status"] not in ("FAILED", "CANCELLED")
                                              or row.get("work_id") or row.get("bvid")):
                    protected.add(row["script_id"])
    return protected


def protected(script_id: str) -> bool:
    return published(script_id) or script_id in upload_protected(Path(store.db_path()).parent)


def source_key(url: str) -> str:
    """Article identity; live rankings, feeds and general documentation are reusable references."""
    try:
        value = normalize_url(url)
        parts = urlsplit(value)
        path = parts.path.lower().rstrip("/")
        if (not parts.netloc or not path or "/leaderboards" in path or "/changelog" in path
                or path.endswith((".rss", ".xml", "/feed", "/feeds", "/rss", "/atom", "/pricing"))
                or path.startswith(("/rss/", "/feed/", "/feeds/"))
                or (parts.netloc.startswith(("docs.", "developers.", "help."))
                    and not path.startswith(("/blog/", "/posts/", "/updates/", "/news/", "/releases/")))):
            return ""
        if parts.netloc == "arxiv.org":
            match = re.search(r"/(?:abs|html|pdf)/(\d{4}\.\d+)", path)
            if match:
                return "arxiv:" + match[1]
        return value.removeprefix("http://").removeprefix("https://")
    except (TypeError, ValueError):
        return ""


def sources(text: str) -> set[str]:
    return {key for url in extract_urls(text) if (key := source_key(url))}


@lru_cache(maxsize=4096)
def _signature(key: str) -> tuple[str, frozenset]:
    url = "https://" + key
    return urlsplit(url).netloc, frozenset(events.slug_title(url).lower().split())


def overlap(left: set[str], right: set[str]) -> set[str]:
    """Reuse the report memory's conservative cross-outlet story matcher, without touching its database."""
    hits = left & right
    for key in left - hits:
        host, tokens = _signature(key)
        if any(events.same_story(tokens, _signature(other)[1], same_host=host == _signature(other)[0])
               for other in right):
            hits.add(key)
    return hits


def cited_sources(sections: list[dict], *, lead: bool = False) -> set[str]:
    return {key for section in (sections[:1] if lead else sections) for p in section.get("paragraphs", [])
            for url in p.get("citations", []) if (key := source_key(url))}


def scope(job: str, config_dir: str) -> str:
    matrix = matrices.of_job(job, config_dir) if job else None
    return f"matrix:{matrix.key}" if matrix else f"job:{job}" if job else ""


def topic_scope(topic: dict, config_dir: str) -> str:
    policy = topic.get("matrix_dedup") or {}
    return f"matrix:{policy['matrix']}" if policy.get("matrix") else scope(policy.get("job", ""), config_dir)


def known_duplicate_ids(candidates: list[dict], config_dir: str) -> set[str]:
    """Remember explicit duplicate skips while their original content still covers them.

    Match the frozen item, not just its date/code: corrected reports may reuse an
    ID with new content. Ordinary provider failures and exhausted pools without
    a covering peer remain eligible for retry.
    """
    ids = [t["id"] for t in candidates if t.get("id")]
    if not ids:
        return set()
    with store.connect() as conn:
        skipped = [json.loads(r["topic"]) for r in conn.execute(
            "SELECT topic FROM scripts WHERE archived=1 AND status='FAILED' AND current_version=0"
            " AND json_extract(topic,'$.content_archive') IS NOT NULL"
            " AND json_extract(topic,'$.id') IN (SELECT value FROM json_each(?))",
            (json.dumps(ids),))]
        peers = {peer for t in skipped for peer in t["content_archive"].get("duplicate_of", [])}
        cutoff = (datetime.now().astimezone() - timedelta(days=MEMORY_DAYS)).isoformat()[:10]
        covered = {r["id"] for r in conn.execute(
            "SELECT s.id FROM scripts s WHERE s.id IN (SELECT value FROM json_each(?)) AND ("
            "EXISTS (SELECT 1 FROM script_versions v JOIN publications p ON p.script_id=v.script_id"
            " AND (p.version=v.version OR p.version IS NULL) WHERE v.script_id=s.id AND v.created_at>=?)"
            " OR (s.archived=0 AND s.status!='FAILED' AND s.created_by NOT IN"
            " ('chain-validation','prompt-quality-validation','多矩阵质量验收')"
            " AND COALESCE(json_extract(s.topic,'$.test_sample'),0)=0 AND ("
            "EXISTS (SELECT 1 FROM script_versions v WHERE v.script_id=s.id AND v.version=s.current_version"
            " AND v.created_at>=?) OR EXISTS (SELECT 1 FROM video_story_claims c WHERE c.script_id=s.id"
            " AND c.state='ACTIVE' AND s.status IN ('RESEARCHING','WRITING')))))",
            (json.dumps(sorted(peers)), cutoff, cutoff))}

    scopes: dict[str, str] = {}

    def identity(topic: dict) -> tuple[str, str]:
        policy = topic.get("matrix_dedup") or {}
        key = policy.get("matrix") or policy.get("job", "")
        if key not in scopes:
            scopes[key] = topic_scope(topic, config_dir)
        return scopes[key], json.dumps({
            "id": topic.get("id"),
            "title": topic.get("from_item_title") or topic.get("title"),
            "fields": {k: v for k, v in (topic.get("fields") or {}).items() if k != "目标受众"},
        }, ensure_ascii=False, sort_keys=True)

    remembered = {identity(t) for t in skipped if covered.intersection(t["content_archive"].get("duplicate_of", []))}
    return {t["id"] for t in candidates if t.get("id") and identity(t) in remembered}


def history(config_dir: str, jobs_dir: str) -> list[dict]:
    """A batch read, with the same ownership rules as the board; unpublished test drafts reserve nothing."""
    from core.video import outputs

    scripts = store.list_scripts(limit=100000) + store.list_scripts(limit=100000, archived=True)
    owners, _ = outputs.script_owners(config_dir, jobs_dir, scripts)
    matrix_scopes = {job: f"matrix:{matrix.key}" for matrix in matrices.load_all(config_dir).values()
                     for job in matrix.jobs}
    by_id = {s["id"]: s for s in scripts}
    cutoff = (datetime.now().astimezone() - timedelta(days=MEMORY_DAYS)).isoformat()[:10]
    with store.connect() as conn:
        rows = conn.execute("SELECT v.script_id,v.version,v.title,v.sections,v.meta,v.created_at,s.rowid AS sequence,"
                            "EXISTS(SELECT 1 FROM publications p WHERE p.script_id=v.script_id "
                            "AND (p.version=v.version OR p.version IS NULL)) AS published "
                            "FROM script_versions v JOIN scripts s ON s.id=v.script_id "
                            "WHERE v.created_at>=? AND (v.version=s.current_version OR EXISTS "
                            "(SELECT 1 FROM publications p WHERE p.script_id=v.script_id "
                            "AND (p.version=v.version OR p.version IS NULL)))", (cutoff,)).fetchall()
    result = []
    for row in rows:
        script = by_id[row["script_id"]]
        if not row["published"] and (script.get("archived") or is_sample(script) or script["status"] == "FAILED"):
            continue
        owner = owners[script["id"]]
        source_matrix = owner.get("source_matrix", owner["matrix"])
        story = json.loads(row["meta"]).get("story") or {}
        key = story.get("scope") or (f"matrix:{source_matrix}" if source_matrix else f"job:{owner['job']}" if owner["job"] else "")
        if not source_matrix and not story.get("scope"):
            key = matrix_scopes.get((script["topic"].get("matrix_dedup") or {}).get("job"), key)
        sections = json.loads(row["sections"])
        fields = script["topic"].get("fields") or {}
        event_date = str(fields.get("发布日期") or script["topic"].get("event_date") or "")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", event_date):
            event_date = ""
        result.append({"script_id": script["id"], "title": row["title"], "published": bool(row["published"]),
                       "version": row["version"],
                       "story": story,
                       "event_date": story.get("event_date") or event_date,
                       "summary": story.get("summary") or " ".join(p.get("text", "") for section in sections[:2]
                                                                     for p in section.get("paragraphs", []))[:1200],
                       "created_at": script["created_at"],
                       "sequence": row["sequence"],
                       "scope": key, "report": script.get("report_path") or script["topic"].get("from_report", ""),
                       "sources": {k for u in story["primary_sources"] if (k := source_key(u))}
                                  if story else cited_sources(sections)})
    return result


def coverage(topic: dict, config_dir: str, jobs_dir: str, *, script_id: str = "") -> list[dict]:
    key = topic_scope(topic, config_dir)
    if not key:
        return []
    with store.connect() as conn:
        current = conn.execute("SELECT rowid AS sequence FROM scripts WHERE id=?", (script_id,)).fetchone() if script_id else None
    return [h for h in history(config_dir, jobs_dir) if h["scope"] == key and h["script_id"] != script_id
            and (not current or h["published"] or h["sequence"] < current["sequence"])]


def writing_peer(topic: dict, config_dir: str, jobs_dir: str, *, script_id: str = "") -> bool:
    """Reserve a matrix until its earlier first draft has usable sources; no same-report exemption."""
    from core.video import outputs, story_pool

    key = topic_scope(topic, config_dir)
    if not key:
        return False
    with store.connect() as conn:
        order = {r["id"]: r["sequence"] for r in conn.execute("SELECT id,rowid AS sequence FROM scripts")}
        assigned = {r["script_id"] for r in conn.execute("SELECT script_id FROM video_story_claims WHERE state IN ('ACTIVE','COVERED')")}
        cancelled = {r["script_id"] for r in conn.execute("SELECT script_id FROM autopilot_items WHERE state IN ('CANCELLED','FAILED')")}
    scripts = [s for s in store.list_scripts(limit=100000) if s["id"] != script_id and not s["current_version"]
               and s["status"] in ("RESEARCHING", "WRITING") and not is_sample(s)
               and s["id"] not in cancelled and not (story_pool.enabled(topic) and s["id"] in assigned)]
    owners, _ = outputs.script_owners(config_dir, jobs_dir, scripts)
    return any((f"matrix:{owners[s['id']]['source_matrix']}" if owners[s["id"]].get("source_matrix")
                else scope(owners[s["id"]]["job"], config_dir)) == key
               and (not script_id or order[s["id"]] < order.get(script_id, float("inf"))) for s in scripts)


def filter_material(text: str, blocked: set[str]) -> str:
    """Remove covered source blocks and bibliography lines; keep the original saved material intact."""
    if not blocked:
        return text
    parts = re.split(r"\n\s*---\s*\n|(?m:^(?=### ))", text)
    kept = []
    for part in parts:
        if overlap(sources(part), blocked):
            # A bibliography is a list, not one story. Preserve fresh references line by line.
            if all(not line.strip() or line.startswith(("- ", "* ", "#")) or re.match(r"\d+\. ", line)
                   for line in part.splitlines()):
                part = "\n".join(line for line in part.splitlines() if not overlap(sources(line), blocked))
            else:
                continue
        if part.strip():
            kept.append(part.strip())
    return "\n\n---\n\n".join(kept)


class DuplicateContent(RuntimeError):
    def __init__(self, peers: list[dict], reason: str = "同矩阵已讲过该选题"):
        super().__init__(reason + "：" + "、".join(dict.fromkeys(p["title"] for p in peers))[:240])
        self.peers = peers


def topic_sources(topic: dict) -> set[str]:
    """Core event evidence takes precedence over auxiliary links in discovery fields."""
    fields = topic.get("fields") or {}
    for name in ("主事件来源", "证据来源", "来源网址"):
        primary = sources(json.dumps(fields.get(name) or "", ensure_ascii=False))
        if primary:
            return primary
    return sources(json.dumps(fields, ensure_ascii=False))


def duplicate_peers(used: set[str], peers: list[dict]) -> list[dict]:
    covered = set().union(*(p["sources"] for p in peers)) if peers else set()
    if used and len(overlap(used, covered)) / len(used) >= MAIN_STORY_SHARE:
        return [p for p in peers if overlap(used, p["sources"])]
    return []


def check_topic(topic: dict, config_dir: str, jobs_dir: str) -> None:
    from core.video import story_pool

    if story_pool.enabled(topic):
        return  # The event pool, with history and explicit new developments, selects before writing.
    peers = duplicate_peers(topic_sources(topic), coverage(topic, config_dir, jobs_dir))
    if peers:
        raise DuplicateContent(peers)


def prepare(script: dict, report: str, material: str, config_dir: str, jobs_dir: str) -> tuple[str, str, dict]:
    """Recheck at writing time, after earlier matrix drafts have finished; no LLM or network needed."""
    if protected(script["id"]):
        return report, material, {}
    peers = coverage(script["topic"], config_dir, jobs_dir, script_id=script["id"])
    blocked = set().union(*(p["sources"] for p in peers)) if peers else set()
    input_sources = sources(material or report)
    hits = overlap(input_sources, blocked)
    if hits and not input_sources - hits:
        raise DuplicateContent([p for p in peers if overlap(hits, p["sources"])], "报告中的新闻均已讲过，跳过自动写稿")
    evidence = {"scope": topic_scope(script["topic"], config_dir),
                "excluded_sources": sorted(hits),
                "peers": [{"script_id": p["script_id"], "title": p["title"]} for p in peers if overlap(hits, p["sources"])]}
    fresh_report, fresh_material = filter_material(report, blocked), filter_material(material, blocked)
    if hits and not sources(fresh_material or fresh_report):
        raise DuplicateContent([p for p in peers if overlap(hits, p["sources"])], "排重后没有可独立讲述的新条目")
    return fresh_report, fresh_material, evidence


def check(script: dict, sections: list[dict], config_dir: str, jobs_dir: str) -> None:
    """A second gate before saving/rendering: rephrasing a headline cannot bypass its article identity."""
    if protected(script["id"]):
        return
    peers = coverage(script["topic"], config_dir, jobs_dir, script_id=script["id"])
    story = script["topic"].get("story")
    if story:
        from core.video import story_pool

        duplicates = story_pool.duplicate(story, peers + story_pool.reservations(story["scope"], script["id"]))
    else:
        used = cited_sources(sections, lead=True) or cited_sources(sections)
        duplicates = duplicate_peers(used, peers)
    if duplicates:
        raise DuplicateContent(duplicates)


def archive_duplicate(script_id: str, reason: str, duplicate_of: list[str]) -> bool:
    """Reversible archival with an explanation. Never update a script with any publication record."""
    if script_id in upload_protected(Path(store.db_path()).parent):
        return False
    with store.connect(write=True) as conn:
        row = conn.execute("SELECT topic FROM scripts WHERE id=? AND NOT EXISTS "
                           "(SELECT 1 FROM publications WHERE script_id=?) AND NOT EXISTS "
                           "(SELECT 1 FROM renders WHERE script_id=? AND status IN "
                           "('SUBMITTING','QUEUED','RUNNING','DOWNLOADING'))", (script_id, script_id, script_id)).fetchone()
        if row is None:
            return False
        topic = json.loads(row["topic"])
        topic["content_archive"] = {"reason": reason, "duplicate_of": duplicate_of,
                                    "at": datetime.now().astimezone().isoformat()}
        conn.execute("UPDATE scripts SET archived=1,topic=?,error=?,updated_at=? WHERE id=?",
                     (json.dumps(topic, ensure_ascii=False), reason, store._now(), script_id))
    return True
