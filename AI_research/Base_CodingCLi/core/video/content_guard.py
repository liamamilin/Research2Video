"""Keep validation samples out of publishing and avoid retelling a matrix's recent stories.

Identity comes from cited article URLs, not the generated headline. Whole-report configurations on the same
report may still use different recipes; different daily/weekly reports share the matrix's 30-day memory.
Published versions (including published validation samples) remain evidence even after local archival.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from functools import lru_cache
import json
import re
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


def with_policy(script: dict) -> dict:
    """Include older automatic whole-report drafts when they next reach the render gate."""
    topic = script.get("topic") or {}
    if (script.get("created_by") != "autopilot" or topic.get("id") or not topic.get("from_report")
            or (topic.get("matrix_dedup") or {}).get("job")):
        return script
    with store.connect() as conn:
        row = conn.execute("SELECT job FROM research_reports WHERE id=? OR report_path=? ORDER BY job DESC LIMIT 1",
                           (topic.get("report_id") or script.get("report_id"), topic["from_report"])).fetchone()
    if row and row["job"]:
        topic = {**topic, "matrix_dedup": {"job": row["job"], "policy": "matrix-source-v1"}}
        return {**script, "topic": topic}
    return script


def source_key(url: str) -> str:
    """Article identity; live rankings, feeds and general documentation are reusable references."""
    try:
        value = normalize_url(url)
        parts = urlsplit(value)
        path = parts.path.lower().rstrip("/")
        if (not parts.netloc or not path or "/leaderboards" in path or "/changelog" in path
                or path.endswith((".rss", ".xml", "/feed", "/pricing"))
                or parts.netloc.startswith(("docs.", "developers.", "help."))):
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


def history(config_dir: str, jobs_dir: str) -> list[dict]:
    """A batch read, with the same ownership rules as the board; unpublished test drafts reserve nothing."""
    from core.video import outputs

    scripts = store.list_scripts(limit=100000) + store.list_scripts(limit=100000, archived=True)
    owners, _ = outputs.script_owners(config_dir, jobs_dir, scripts)
    by_id = {s["id"]: s for s in scripts}
    cutoff = (datetime.now().astimezone() - timedelta(days=MEMORY_DAYS)).isoformat()[:10]
    with store.connect() as conn:
        rows = conn.execute("SELECT v.script_id,v.version,v.title,v.sections,v.created_at,s.rowid AS sequence,"
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
        key = f"matrix:{owner['matrix']}" if owner["matrix"] else f"job:{owner['job']}" if owner["job"] else ""
        result.append({"script_id": script["id"], "title": row["title"], "published": bool(row["published"]),
                       "created_at": script["created_at"],
                       "sequence": row["sequence"],
                       "scope": key, "report": script.get("report_path") or script["topic"].get("from_report", ""),
                       "sources": cited_sources(json.loads(row["sections"]))})
    return result


def coverage(topic: dict, config_dir: str, jobs_dir: str, *, script_id: str = "") -> list[dict]:
    policy = topic.get("matrix_dedup") or {}
    key = scope(policy.get("job", ""), config_dir)
    if not key:
        return []
    with store.connect() as conn:
        current = conn.execute("SELECT rowid AS sequence FROM scripts WHERE id=?", (script_id,)).fetchone() if script_id else None
    return [h for h in history(config_dir, jobs_dir) if h["scope"] == key and h["script_id"] != script_id
            and h["report"] != topic.get("from_report")
            and (not current or h["published"] or h["sequence"] < current["sequence"])]


def writing_peer(topic: dict, config_dir: str, jobs_dir: str) -> bool:
    """Wait for an earlier report's first draft so two concurrent jobs cannot both pick its lead story."""
    from core.video import outputs

    key = scope((topic.get("matrix_dedup") or {}).get("job", ""), config_dir)
    if not key:
        return False
    scripts = [s for s in store.list_scripts(limit=100000) if not s["current_version"]
               and s["status"] in ("RESEARCHING", "WRITING") and not is_sample(s)]
    owners, _ = outputs.script_owners(config_dir, jobs_dir, scripts)
    return any(scope(owners[s["id"]]["job"], config_dir) == key
               and s["topic"].get("from_report") != topic.get("from_report") for s in scripts)


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
    def __init__(self, peers: list[dict], reason: str = "同矩阵其他报告已讲过该选题"):
        super().__init__(reason + "：" + "、".join(dict.fromkeys(p["title"] for p in peers))[:240])
        self.peers = peers


def prepare(script: dict, report: str, material: str, config_dir: str, jobs_dir: str) -> tuple[str, str, dict]:
    """Recheck at writing time, after earlier matrix drafts have finished; no LLM or network needed."""
    peers = coverage(script["topic"], config_dir, jobs_dir, script_id=script["id"])
    blocked = set().union(*(p["sources"] for p in peers)) if peers else set()
    input_sources = sources(material or report)
    hits = overlap(input_sources, blocked)
    if hits and not input_sources - hits:
        raise DuplicateContent([p for p in peers if overlap(hits, p["sources"])], "报告中的新闻均已讲过，跳过自动写稿")
    evidence = {"scope": scope((script["topic"].get("matrix_dedup") or {}).get("job", ""), config_dir),
                "excluded_sources": sorted(hits),
                "peers": [{"script_id": p["script_id"], "title": p["title"]} for p in peers if overlap(hits, p["sources"])]}
    fresh_report, fresh_material = filter_material(report, blocked), filter_material(material, blocked)
    if hits and not sources(fresh_material or fresh_report):
        raise DuplicateContent([p for p in peers if overlap(hits, p["sources"])], "排重后没有可独立讲述的新条目")
    return fresh_report, fresh_material, evidence


def check(script: dict, sections: list[dict], config_dir: str, jobs_dir: str) -> None:
    """A second gate before saving/rendering: rephrasing a headline cannot bypass its article identity."""
    peers = coverage(script["topic"], config_dir, jobs_dir, script_id=script["id"])
    used = cited_sources(sections, lead=True) or cited_sources(sections)
    covered = set().union(*(p["sources"] for p in peers)) if peers else set()
    duplicates = [p for p in peers if overlap(used, p["sources"])]
    if used and len(overlap(used, covered)) / len(used) >= MAIN_STORY_SHARE:
        raise DuplicateContent(duplicates)


def archive_duplicate(script_id: str, reason: str, duplicate_of: list[str]) -> bool:
    """Reversible archival with an explanation. Never update a script with any publication record."""
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
