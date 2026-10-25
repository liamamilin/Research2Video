"""Topics as records (spec/video-topics.md): any job that declares ``topics:`` feeds them, and the user can add some.

A job becomes a topic source by saying how to read its reports::

    topics:
      alias: flash          # optional short name recipes and batches use (the job path otherwise)
      format: flash         # flash (### 1.1 items -> F1, F2 ...) | opportunities (### C1 ...) | items (any ### heading)
      prefix: D             # the code letter(s) its topics get (D1, D2 ...), unique among sources; required for
                            # items, optional for flash (F) and opportunities (C) — a second source of the same format
                            # needs one, or both would number their topics F1, F2 … and overwrite each other's
      refresh: true         # the unattended pipeline may start this job when its newest report is not today's

Its reports are the files its ``output`` path names (``{date}`` in the path is the report date). They are read into
the ``topics`` table by a reconcile scan (``sync``): a report whose size or modification time changed is parsed
again, the others are left alone — so a report written by the scheduler, the command line, a round or the console
is picked up the same way, and the first scan imports every report there already is. A topic's id is
``<report date>/<code>``, as it always was, so the scripts, batches and "not writing" marks made before keep pointing
at the same topics. Topics added by hand have the source ``manual`` and the id ``manual/<n>``.

Ids are unique only while prefixes are: when two sources would give the same codes, the one that has already imported
reports keeps its prefix and the newcomer is listed with an error until it declares its own (a second flash source,
e.g. one per matrix). See spec/video-topics.md.
"""

from __future__ import annotations

import glob
import json
import os
import re
from dataclasses import dataclass
from typing import Optional

from . import flash as flash_format
from . import topics as opportunity_format
from .store import connect, _now
from .topics import Topic

FORMATS = ("flash", "opportunities", "items")
MANUAL = "manual"
#: the codes the two built-in formats give when no prefix is declared; an ``items`` source must pick another prefix
DEFAULT_PREFIXES = {"flash": "F", "opportunities": "C"}
_RESERVED_PREFIXES = set(DEFAULT_PREFIXES.values())
_PREFIX = re.compile(r"[A-Z]{1,3}")
_ALIAS = re.compile(r"[a-z][a-z0-9_-]{1,31}")
#: per-script research jobs are never topic sources
_SKIP = ("video/", "vod/")

SCHEMA = """
CREATE TABLE IF NOT EXISTS topics (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    report_date TEXT,
    report_path TEXT,
    code TEXT NOT NULL,
    position INTEGER NOT NULL DEFAULT 0,
    title TEXT NOT NULL,
    fields TEXT NOT NULL DEFAULT '{}',
    created_by TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_topics_source ON topics(source, report_date);
CREATE TABLE IF NOT EXISTS topic_imports (
    report_path TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    report_date TEXT,
    mtime REAL,
    size INTEGER,
    count INTEGER NOT NULL DEFAULT 0,
    error TEXT NOT NULL DEFAULT '',
    imported_at TEXT NOT NULL
);
"""


def init(conn) -> None:
    conn.executescript(SCHEMA)


@dataclass(frozen=True)
class Source:
    """A job whose reports hold topics, as its ``topics:`` block declares."""
    name: str            # what recipes and batches call it: the alias, else the job path
    job: str             # the job path (jobs/<job>.yaml)
    label: str
    format: str = "items"
    prefix: str = ""     # the code letters its topics get: declared, else the format's own (F, C)
    refresh: bool = False
    output: str = ""     # the job's output path, with {date}
    error: str = ""      # why it cannot be read; it is listed but not imported

    def as_dict(self) -> dict:
        return {"name": self.name, "job": self.job, "label": self.label, "format": self.format,
                "prefix": self.prefix, "refresh": self.refresh, "error": self.error}


def _declare(job_name: str, job: dict) -> Optional[Source]:
    block = job.get("topics")
    if block is None:
        return None
    label = str(job.get("name") or job_name)
    if not isinstance(block, dict):
        return Source(name=job_name, job=job_name, label=label, error="topics 应为映射（format、prefix 等）")
    alias = str(block.get("alias") or "").strip()
    name = alias or job_name
    fmt = str(block.get("format") or "items")
    prefix = str(block.get("prefix") or "").strip()
    output = str(job.get("output") or "")
    errors = []
    if alias and not _ALIAS.fullmatch(alias):
        errors.append(f"alias {alias!r} 只能用小写字母、数字、下划线、连字符")
    if alias == MANUAL:
        errors.append("alias 不能叫 manual（手动添加的选题用这个名字）")
    if fmt not in FORMATS:
        errors.append(f"format 应为 {', '.join(FORMATS)} 之一")
    if fmt == "items" and (not _PREFIX.fullmatch(prefix) or prefix in _RESERVED_PREFIXES):
        errors.append("format: items 要写 prefix（1–3 个大写字母，不能是 F 或 C），选题编号就是 prefix + 序号")
    elif fmt != "items" and prefix and not _PREFIX.fullmatch(prefix):
        errors.append("prefix 要写 1–3 个大写字母，选题编号就是 prefix + 序号")
    if "{date}" not in output:
        errors.append("这个 Job 的 output 路径里要有 {date}，才能知道每份报告是哪天的")
    unknown = set(block) - {"alias", "format", "prefix", "refresh"}
    if unknown:
        errors.append(f"topics 不认识这些字段：{', '.join(sorted(unknown))}")
    return Source(name=name, job=job_name, label=label, format=fmt, prefix=prefix or DEFAULT_PREFIXES.get(fmt, ""),
                  refresh=bool(block.get("refresh")), output=output, error="；".join(errors))


#: path → ((mtime_ns, size), job name, its Source or None). Parsing every job file took ~80 ms per call, and the
#: topics page asked for the sources once per recipe; an edited file has a new mtime or size and is parsed again.
_declared: dict[str, tuple[tuple[int, int], str, Optional[Source]]] = {}


def _declared_in(path: str, job_name: str) -> Optional[Source]:
    import yaml

    try:
        stat = os.stat(path)
    except OSError:
        return None
    key = (stat.st_mtime_ns, stat.st_size)
    cached = _declared.get(path)
    if cached is not None and cached[0] == key and cached[1] == job_name:
        return cached[2]
    try:
        with open(path, encoding="utf-8") as fh:
            job = yaml.safe_load(fh) or {}
    except (OSError, yaml.YAMLError):
        return None  # not cached: a file caught half-written is read again next time
    source = _declare(job_name, job) if isinstance(job, dict) else None
    _declared[path] = (key, job_name, source)
    return source


def sources(jobs_dir: str) -> dict[str, Source]:
    """Every job that declares ``topics:``, by name. Two sources with the same name or prefix are both errors."""
    from core.config import list_jobs

    found: list[Source] = []
    for job_name in list_jobs(jobs_dir):
        if job_name.startswith(_SKIP):
            continue
        path = next((p for p in (os.path.join(jobs_dir, job_name + ext) for ext in (".yaml", ".yml"))
                     if os.path.isfile(p)), None)
        if path is None:
            continue
        if (source := _declared_in(path, job_name)) is not None:
            found.append(source)
    imported = _imported_sources()
    out: dict[str, Source] = {}
    for source in found:
        clash = [s for s in found if s is not source and (s.name == source.name or (
            source.prefix and s.prefix == source.prefix))]
        # the source already feeding topics keeps them; only a newcomer is stopped (its ids would overwrite)
        if clash and not (source.name in imported and not any(s.name in imported for s in clash)):
            other = clash[0]
            why = (f"和 {other.job} 重名" if other.name == source.name else
                   f"编号前缀 {source.prefix} 已经被 {other.job} 用了：在这个 Job 的 topics 里加一个 prefix"
                   f"（1–3 个大写字母，比如 M{source.prefix}），选题编号就是 prefix + 序号")
            source = Source(**{**source.__dict__, "error": "；".join(filter(None, [source.error, why]))})
        out.setdefault(source.name, source)
    return out


def _imported_sources() -> set[str]:
    """Names of the sources that have imported a report: they keep their codes when a newcomer clashes."""
    try:
        with connect() as conn:
            return {r["source"] for r in conn.execute("SELECT DISTINCT source FROM topic_imports")}
    except Exception:  # noqa: BLE001 - no database yet: nobody has imported anything
        return set()


def whole_source(jobs_dir: str, job_name: str) -> Optional[Source]:
    """A Job whose reports are not item lists (no ``topics:``), read as a source of whole reports: where its reports are
    and which day each is from. None when the Job does not exist or does declare ``topics:`` (``resolve`` is for those)."""
    from core.config import load_job

    job = load_job(jobs_dir, job_name)
    if not isinstance(job, dict) or job.get("topics") is not None:
        return None
    output = str(job.get("output") or "")
    return Source(name=job_name, job=job_name, label=str(job.get("name") or job_name), output=output,
                  error="" if "{date}" in output else "这个 Job 的 output 路径里要有 {date}，才能知道每份报告是哪天的")


def resolve(jobs_dir: str, name: str) -> Optional[Source]:
    """A source by its name, alias or job path."""
    every = sources(jobs_dir)
    return every.get(name) or next((s for s in every.values() if s.job == name), None)


# --- reading reports ---------------------------------------------------------

def _pattern(source: Source, root: str) -> tuple[str, re.Pattern]:
    """The glob and regex for the source's reports. ``{name}`` is the job's name as the engine writes it; a second run
    on the same day gets ``_1``, ``_2`` … before the extension (core/engine.py ``_save_output``)."""
    path = os.path.join(root, source.output.replace("{name}", re.sub(r'[<>:"/\\|?*]', "_", source.label)))
    stem, ext = os.path.splitext(path)
    regex = re.escape(stem).replace(re.escape("{date}"), r"(\d{4}-\d{2}-\d{2})") + r"(?:_\d+)?" + re.escape(ext)
    return stem.replace("{date}", "*") + "*" + ext, re.compile(regex + r"\Z")


def report_files(source: Source, root: str) -> list[tuple[str, str]]:
    """``(date, path)`` of the source's reports, one per day (that day's last run), newest day first."""
    if source.error:
        return []
    pattern, regex = _pattern(source, root)
    newest: dict[str, str] = {}
    for path in glob.glob(pattern):
        match = regex.match(path)
        if match and os.path.isfile(path):
            date = match.group(1)
            if date not in newest or os.path.getmtime(path) > os.path.getmtime(newest[date]):
                newest[date] = path
    return sorted(newest.items(), reverse=True)


_ITEM_HEADING = re.compile(r"^###\s+(.+?)\s*$")
_NUMBERING = re.compile(r"^(?:[A-Z]{0,3}\d+(?:\.\d+)*)\s*[.．、:：]?\s+")
_ANY_HEADING = re.compile(r"^#{1,6}\s")
_BOLD_FIELD = re.compile(r"^\s*[-*]\s*\*\*(.+?)\*\*\s*[:：]\s*(.*)$")


def parse_items(text: str, report_date: str, prefix: str) -> list[Topic]:
    """``items``: every ``###`` heading followed by ``- **字段**：值`` lines is a topic, numbered <prefix>1, 2 ...

    A leading number on the heading (``1.``, ``1.1``, ``C3``) is dropped from the title; a heading without fields
    (an overview, a section title) is not a topic."""
    found: list[tuple[str, dict]] = []
    current: tuple[str, dict] | None = None
    for line in text.splitlines():
        heading = _ITEM_HEADING.match(line)
        if heading or _ANY_HEADING.match(line):
            if current and current[1]:
                found.append(current)
            current = (_NUMBERING.sub("", heading.group(1)).replace("**", "").strip(), {}) if heading else None
            continue
        field = _BOLD_FIELD.match(line)
        if current is not None and field:
            current[1][field.group(1).replace("**", "").strip()] = field.group(2).replace("**", "").strip()
    if current and current[1]:
        found.append(current)
    topics = []
    for n, (title, fields) in enumerate(found, 1):
        topic = Topic(id=f"{report_date}/{prefix}{n}", round=report_date, code=f"{prefix}{n}", title=title,
                      fields=fields)
        flash_format._complete(topic, {})  # 来源网址 from the URLs it cites inline
        topics.append(topic)
    return topics


def parse(source: Source, text: str, report_date: str) -> list[Topic]:
    if source.format == "items":
        return parse_items(text, report_date, source.prefix)
    if source.format == "opportunities":
        found = opportunity_format.parse(text, report_date)
    else:
        found = flash_format.parse(text, report_date)
    default = DEFAULT_PREFIXES[source.format]
    if source.prefix == default:
        return found
    for topic in found:  # a declared prefix replaces the format's own letter: F3 -> MF3
        topic.code = source.prefix + topic.code[len(default):]
        topic.id = f"{report_date}/{topic.code}"
    return found


def sync(jobs_dir: str, output_dir: str) -> list[dict]:
    """Import every report that is new or changed since it was last read. Returns what each source looks like."""
    root = os.path.dirname(os.path.abspath(output_dir))
    with connect() as conn:
        seen = {r["report_path"]: (r["mtime"], r["size"]) for r in conn.execute(
            "SELECT report_path, mtime, size FROM topic_imports")}
    status = []
    for source in sources(jobs_dir).values():
        files = report_files(source, root)
        for report_date, path in files:
            rel = os.path.relpath(path, root)
            stat = os.stat(path)
            if seen.get(rel) == (stat.st_mtime, stat.st_size):
                continue
            _import(source, rel, path, report_date, stat)
        status.append({**source.as_dict(), "reports": len(files), "latest": files[0][0] if files else None})
    return status


def _import(source: Source, rel: str, path: str, report_date: str, stat) -> None:
    try:
        with open(path, encoding="utf-8") as fh:
            found = parse(source, fh.read(), report_date)
        error = "" if found else "报告里没找到选题：标题和字段的格式不对？"
    except (OSError, UnicodeDecodeError) as exc:
        found, error = [], f"读不了：{exc}"
    now = _now()
    with connect(write=True) as conn:
        # the report was rewritten: its topics are replaced as a whole (scripts keep their own copy of the topic)
        conn.execute("DELETE FROM topics WHERE report_path = ? AND source = ?", (rel, source.name))
        for position, topic in enumerate(found):
            conn.execute(
                "INSERT OR REPLACE INTO topics (id, source, report_date, report_path, code, position, title, fields,"
                " created_by, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'import', ?, ?)",
                (topic.id, source.name, report_date, rel, topic.code, position, topic.title,
                 json.dumps(topic.fields, ensure_ascii=False), now, now))
        conn.execute(
            "INSERT OR REPLACE INTO topic_imports (report_path, source, report_date, mtime, size, count, error,"
            " imported_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (rel, source.name, report_date, stat.st_mtime, stat.st_size, len(found), error, now))


# --- reading topics ------------------------------------------------------------

def _topic(row) -> Topic:
    topic = Topic(id=row["id"], round=row["report_date"] or "", code=row["code"], title=row["title"],
                  fields=json.loads(row["fields"] or "{}"))
    topic.source = row["source"]  # not dataclass fields: a script's copy of its topic stays as it was
    topic.report_path = row["report_path"]
    return topic


def report_dates(source: str) -> list[str]:
    with connect() as conn:
        return [r["report_date"] for r in conn.execute(
            "SELECT DISTINCT report_date FROM topics WHERE source = ? AND report_date IS NOT NULL"
            " ORDER BY report_date DESC", (source,))]


def latest(source: str) -> Optional[str]:
    dates = report_dates(source)
    return dates[0] if dates else None


def load(source: str, report_date: Optional[str] = None) -> tuple[Optional[str], list[Topic]]:
    """A source's topics from one report (the newest when none is given); every topic added by hand for ``manual``."""
    with connect() as conn:
        if source == MANUAL:
            rows = conn.execute("SELECT * FROM topics WHERE source = ? ORDER BY rowid DESC", (MANUAL,)).fetchall()
            return None, [_topic(r) for r in rows]
        report_date = report_date or latest(source)
        if report_date is None:
            return None, []
        rows = conn.execute("SELECT * FROM topics WHERE source = ? AND report_date = ? ORDER BY position",
                            (source, report_date)).fetchall()
    return report_date, [_topic(r) for r in rows]


def get(topic_id: str) -> Optional[Topic]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM topics WHERE id = ?", (topic_id,)).fetchone()
    return _topic(row) if row else None


def import_errors() -> dict[str, list[dict]]:
    """Reports that were read but gave no topics, by source."""
    with connect() as conn:
        rows = conn.execute("SELECT * FROM topic_imports WHERE error != '' ORDER BY report_date DESC").fetchall()
    out: dict[str, list[dict]] = {}
    for r in rows:
        out.setdefault(r["source"], []).append({"report": r["report_path"], "date": r["report_date"], "error": r["error"]})
    return out


def add_manual(title: str, fields: dict, by: str = "") -> Topic:
    """A topic the user wrote: ``manual/<n>``, numbered in the order they were added."""
    title = title.strip()
    if len(title) < 4:
        raise ValueError("选题标题至少 4 个字")
    clean = {str(k).strip(): str(v).strip() for k, v in (fields or {}).items() if str(k).strip() and str(v or "").strip()}
    now = _now()
    with connect(write=True) as conn:
        number = 1 + (conn.execute("SELECT COUNT(*) FROM topics WHERE source = ?", (MANUAL,)).fetchone()[0])
        while conn.execute("SELECT 1 FROM topics WHERE id = ?", (f"{MANUAL}/{number}",)).fetchone():
            number += 1
        topic_id = f"{MANUAL}/{number}"
        conn.execute(
            "INSERT INTO topics (id, source, report_date, report_path, code, position, title, fields, created_by,"
            " created_at, updated_at) VALUES (?, ?, NULL, NULL, ?, 0, ?, ?, ?, ?, ?)",
            (topic_id, MANUAL, f"M{number}", title, json.dumps(clean, ensure_ascii=False), by, now, now))
    return get(topic_id)
