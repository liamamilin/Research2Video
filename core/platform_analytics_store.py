"""Account-isolated, append-only video measurements; no credentials or raw API bodies."""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from statistics import median

from core import sqlite_util

METRICS = ("view", "like", "coin", "favorite", "reply", "danmaku", "share")
INTERNAL_JOB = "__bilibili_video_data"
SCHEDULE_ID = "bilibili-video-data"


def interval(pubdate: int, now: float) -> int:
    age = now - pubdate if pubdate > 0 else 8 * 86400
    return 1800 if age < 86400 else 7200 if age < 7 * 86400 else 86400


def counters(value: dict) -> dict:
    return {key: value.get(key) if type(value.get(key)) is int and value[key] >= 0 else None for key in METRICS}


def matrix_identity(publication: dict | None) -> tuple[str, str]:
    if publication is None:
        return "unlinked", "未关联矩阵"
    if publication.get("matrix_unavailable"):
        return "unknown", "矩阵归属暂不可用"
    if not publication.get("matrix"):
        return "independent", "独立视频"
    suffix = "（已归档）" if publication.get("matrix_archived") else ""
    return "matrix:" + publication["matrix"], (publication.get("matrix_name") or publication["matrix"]) + suffix


class AnalyticsStore:
    def __init__(self, state_dir: str):
        self.path = str(Path(state_dir) / "platform_analytics.db")
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS analytics_accounts (
                    uid TEXT PRIMARY KEY, enabled INTEGER NOT NULL DEFAULT 0,
                    requested_at REAL NOT NULL DEFAULT 0, last_requested_at REAL NOT NULL DEFAULT 0,
                    run_id TEXT NOT NULL DEFAULT '', page INTEGER NOT NULL DEFAULT 1,
                    remote_total INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'IDLE',
                    last_success_at REAL NOT NULL DEFAULT 0, last_attempt_at REAL NOT NULL DEFAULT 0,
                    next_catalog_at REAL NOT NULL DEFAULT 0, next_account_at REAL NOT NULL DEFAULT 0,
                    blocked_until REAL NOT NULL DEFAULT 0, failures INTEGER NOT NULL DEFAULT 0,
                    error TEXT NOT NULL DEFAULT '', worker_at REAL NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS analytics_videos (
                    uid TEXT NOT NULL, bvid TEXT NOT NULL, aid INTEGER NOT NULL,
                    title TEXT NOT NULL, pubdate INTEGER NOT NULL, duration INTEGER NOT NULL,
                    tid INTEGER NOT NULL, tname TEXT NOT NULL, state INTEGER,
                    state_desc TEXT NOT NULL DEFAULT '', missing INTEGER NOT NULL DEFAULT 0,
                    metrics TEXT NOT NULL DEFAULT '{}', stats_at REAL NOT NULL DEFAULT 0,
                    next_due REAL NOT NULL DEFAULT 0, last_seen REAL NOT NULL,
                    seen_run TEXT NOT NULL DEFAULT '', error TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY(uid,bvid)
                );
                CREATE INDEX IF NOT EXISTS analytics_due ON analytics_videos(uid,next_due);
                CREATE TABLE IF NOT EXISTS analytics_snapshots (
                    uid TEXT NOT NULL,bvid TEXT NOT NULL,observed_at REAL NOT NULL,metrics TEXT NOT NULL,
                    source TEXT NOT NULL, PRIMARY KEY(uid,bvid,observed_at)
                );
                CREATE TABLE IF NOT EXISTS analytics_overviews (
                    uid TEXT NOT NULL,period INTEGER NOT NULL,summary TEXT NOT NULL,
                    points TEXT NOT NULL,platform_date TEXT NOT NULL,synced_at REAL NOT NULL,
                    PRIMARY KEY(uid,period)
                );
                CREATE TABLE IF NOT EXISTS analytics_overview_errors (
                    uid TEXT PRIMARY KEY,error TEXT NOT NULL,at REAL NOT NULL
                );
            """)

    def connect(self, write: bool = False):
        return sqlite_util.connect(self.path, write=write)

    def account(self, uid: str) -> dict:
        with self.connect(write=True) as db:
            db.execute("INSERT OR IGNORE INTO analytics_accounts(uid) VALUES (?)", (uid,))
            return dict(db.execute("SELECT * FROM analytics_accounts WHERE uid=?", (uid,)).fetchone())

    def update_account(self, uid: str, **patch) -> dict:
        allowed = set(self.account(uid)) - {"uid"}
        if not patch.keys() <= allowed:
            raise ValueError("unknown analytics account field")
        with self.connect(write=True) as db:
            db.execute("UPDATE analytics_accounts SET " + ",".join(f"{key}=?" for key in patch) + " WHERE uid=?",
                       (*patch.values(), uid))
        return self.account(uid)

    def request(self, uid: str, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        self.account(uid)
        with self.connect(write=True) as db:
            row = dict(db.execute("SELECT * FROM analytics_accounts WHERE uid=?", (uid,)).fetchone())
            if row["requested_at"] and row["status"] in ("QUEUED", "RUNNING"):
                return row
            if row["blocked_until"] > now:
                raise ValueError(row["error"] or "请等待采集冷却后重试。")
            if row["last_requested_at"] and row["last_requested_at"] > now - 60:
                raise ValueError("手动同步至少间隔60秒，页面刷新可直接查看已缓存数据。")
            db.execute("UPDATE analytics_accounts SET requested_at=?,last_requested_at=?,status='QUEUED',"
                       "run_id='',page=1,error='' WHERE uid=?", (now, now, uid))
        return self.account(uid)

    def begin_catalog(self, uid: str, now: float) -> dict:
        return self.update_account(uid, run_id=uuid.uuid4().hex, page=1, remote_total=0,
                                   status="RUNNING", last_attempt_at=now, worker_at=now)

    def ingest(self, uid: str, row: dict, *, now: float, run_id: str = "", force: bool = False, source: str = "catalog") -> None:
        """Metadata can refresh without manufacturing an extra measurement before it is due."""
        with self.connect(write=True) as db:
            db.execute("INSERT INTO analytics_videos(uid,bvid,aid,title,pubdate,duration,tid,tname,state,state_desc,last_seen,seen_run) "
                       "VALUES (?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(uid,bvid) DO UPDATE SET aid=excluded.aid,"
                       "title=excluded.title,pubdate=excluded.pubdate,duration=excluded.duration,tid=excluded.tid,"
                       "tname=excluded.tname,state=excluded.state,state_desc=excluded.state_desc,last_seen=excluded.last_seen,"
                       "seen_run=CASE WHEN excluded.seen_run<>'' THEN excluded.seen_run ELSE analytics_videos.seen_run END,missing=0",
                       (uid, row["bvid"], row["aid"], row["title"], row["pubdate"], row["duration"], row["tid"], row["tname"],
                        row["state"], row.get("state_desc", ""), now, run_id))
            old = db.execute("SELECT * FROM analytics_videos WHERE uid=? AND bvid=?", (uid, row["bvid"])).fetchone()
            stats = counters(row.get("stat") or {})
            if row["state"] == 0 and (force or old["next_due"] <= now) and any(v is not None for v in stats.values()):
                encoded = json.dumps(stats)
                db.execute("INSERT OR IGNORE INTO analytics_snapshots VALUES (?,?,?,?,?)", (uid, row["bvid"], now, encoded, source))
                db.execute("UPDATE analytics_videos SET metrics=?,stats_at=?,next_due=?,error='' WHERE uid=? AND bvid=?",
                           (encoded, now, now + interval(row["pubdate"], now), uid, row["bvid"]))
            elif row["state"] != 0:
                db.execute("UPDATE analytics_videos SET next_due=?,error='' WHERE uid=? AND bvid=?",
                           (now + 1800, uid, row["bvid"]))
            elif old["next_due"] <= now or force:
                db.execute("UPDATE analytics_videos SET next_due=?,error=? WHERE uid=? AND bvid=?",
                           (now + 300, "平台暂未返回有效统计，保留已有数据。", uid, row["bvid"]))

    def finish_catalog(self, uid: str, run_id: str, total: int, now: float) -> bool:
        with self.connect(write=True) as db:
            seen = db.execute("SELECT count(*) FROM analytics_videos WHERE uid=? AND seen_run=?", (uid, run_id)).fetchone()[0]
            complete = seen == total
            if complete:
                db.execute("UPDATE analytics_videos SET missing=1,error=? WHERE uid=? AND seen_run<>?",
                           ("本次完整目录未返回该视频，保留历史数据。", uid, run_id))
            db.execute("UPDATE analytics_accounts SET run_id='',page=1,requested_at=0,status='IDLE',"
                       "next_catalog_at=?,last_success_at=?,error=?,failures=0 WHERE uid=?",
                       (now + (3600 if complete else 300), now,
                        "" if complete else "目录在分页期间发生变化，已保留数据，稍后重新核对。", uid))
        return complete

    def failure(self, uid: str, error: str, *, now: float, paused: bool = False) -> None:
        old = self.account(uid)
        delay = 3600 if paused else min(3600, 60 * 2 ** min(old["failures"], 6))
        self.update_account(uid, status="PAUSED" if paused else "ERROR", error=error, blocked_until=now + delay,
                            failures=old["failures"] + 1, last_attempt_at=now, worker_at=now)

    def video_failure(self, uid: str, bvid: str, error: str, now: float, *, missing: bool = False) -> None:
        with self.connect(write=True) as db:
            db.execute("UPDATE analytics_videos SET error=?,next_due=?,missing=CASE WHEN ? THEN 1 ELSE missing END "
                       "WHERE uid=? AND bvid=?", (error, now + (86400 if missing else 300), missing, uid, bvid))

    def due(self, uid: str, now: float, limit: int = 5) -> list[dict]:
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM analytics_videos WHERE uid=? AND missing=0 "
                                                   "AND next_due<=? ORDER BY CASE WHEN pubdate>? THEN 0 WHEN pubdate>? THEN 1 ELSE 2 END,next_due LIMIT ?",
                                                   (uid, now, now - 86400, now - 7 * 86400, limit))]

    def save_overview(self, uid: str, period: int, summary: dict, points: list, platform_date: str, now: float) -> None:
        with self.connect(write=True) as db:
            db.execute("INSERT INTO analytics_overviews VALUES (?,?,?,?,?,?) ON CONFLICT(uid,period) DO UPDATE SET "
                       "summary=excluded.summary,points=excluded.points,platform_date=excluded.platform_date,synced_at=excluded.synced_at",
                       (uid, period, json.dumps(summary), json.dumps(points), platform_date, now))

    def overview(self, uid: str, period: int) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM analytics_overviews WHERE uid=? AND period=?", (uid, period)).fetchone()
        return {**dict(row), "summary": json.loads(row["summary"]), "points": json.loads(row["points"])} if row else None

    @staticmethod
    def video(row) -> dict:
        data = dict(row)
        data["metrics"] = json.loads(data["metrics"])
        data["available"] = data["state"] == 0 and not data["missing"]
        return data

    def get_video(self, uid: str, bvid: str) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM analytics_videos WHERE uid=? AND bvid=?", (uid, bvid)).fetchone()
        return self.video(row) if row else None

    def videos(self, uid: str, *, query: str = "", workflow: dict | None = None, scope: str = "all", matrix: str = "", sort: str = "date", page: int = 1, size: int = 25) -> dict:
        where, args = ["uid=?"], [uid]
        if query:
            # Literal substring search, including %/_ supplied by the user.
            where.append("(instr(lower(title),lower(?))>0 OR instr(lower(bvid),lower(?))>0)")
            args.extend([query, query])
        if scope == "workflow":
            ids = list(workflow or {})
            where.append("bvid IN (" + ",".join("?" for _ in ids) + ")" if ids else "0")
            args.extend(ids)
        if matrix:
            ids = list(workflow or {}) if matrix == "unlinked" else [key for key, value in (workflow or {}).items() if matrix_identity(value)[0] == matrix]
            if ids:
                where.append("bvid " + ("NOT IN" if matrix == "unlinked" else "IN") + " (" + ",".join("?" for _ in ids) + ")")
                args.extend(ids)
            else:
                where.append("1" if matrix == "unlinked" else "0")
        orders = {"date": "pubdate DESC,bvid", "view": "CASE WHEN state=0 AND missing=0 THEN json_extract(metrics,'$.view') END DESC,bvid",
                  "favorite_rate": "CASE WHEN state=0 AND missing=0 AND json_extract(metrics,'$.view')>0 THEN "
                  "1.0*json_extract(metrics,'$.favorite')/json_extract(metrics,'$.view') END DESC,bvid"}
        condition = " AND ".join(where)
        with self.connect() as db:
            total = db.execute("SELECT count(*) FROM analytics_videos WHERE " + condition, args).fetchone()[0]
            rows = db.execute("SELECT * FROM analytics_videos WHERE " + condition + " ORDER BY " + orders.get(sort, orders["date"]) + " LIMIT ? OFFSET ?",
                              (*args, size, (page - 1) * size)).fetchall()
        return {"items": [{**self.video(row), "publication": (workflow or {}).get(row["bvid"])} for row in rows], "total": total, "page": page, "size": size}

    def matrix_groups(self, uid: str, workflow: dict) -> list[dict]:
        """Group every cached BV once; missing metrics stay unknown, ratios use paired samples."""
        groups = {}
        with self.connect() as db:
            rows = db.execute("SELECT bvid,state,missing,metrics,error FROM analytics_videos WHERE uid=?", (uid,)).fetchall()
        for row in rows:
            key, name = matrix_identity(workflow.get(row["bvid"]))
            group = groups.setdefault(key, {"key": key, "name": name, "videos": 0, "public_videos": 0,
                                           "view_samples": 0, "views": [], "stale_samples": 0,
                                           "rate_samples": 0, "rate_views": 0, "rate_favorites": 0})
            group["videos"] += 1
            if row["state"] != 0 or row["missing"]:
                continue
            group["public_videos"] += 1
            stats = json.loads(row["metrics"])
            view, favorite = stats.get("view"), stats.get("favorite")
            if type(view) is int:
                group["views"].append(view)
                group["view_samples"] += 1
                group["stale_samples"] += bool(row["error"])
                if type(favorite) is int:
                    group["rate_samples"] += 1
                    group["rate_views"] += view
                    group["rate_favorites"] += favorite
        result = []
        for group in groups.values():
            values = group.pop("views")
            result.append({**group, "view": sum(values) if values else None,
                           "median_view": median(values) if values else None,
                           "favorite_rate": group["rate_favorites"] / group["rate_views"] if group["rate_views"] else None})
        return sorted(result, key=lambda row: (not row["key"].startswith("matrix:"), -(row["view"] or 0), row["key"]))

    def history(self, uid: str, bvid: str, *, since: float) -> dict:
        with self.connect() as db:
            baseline = db.execute("SELECT min(observed_at) FROM analytics_snapshots WHERE uid=? AND bvid=?", (uid, bvid)).fetchone()[0]
            rows = db.execute("SELECT observed_at,metrics FROM analytics_snapshots WHERE uid=? AND bvid=? AND observed_at>=? "
                              "ORDER BY observed_at DESC LIMIT 5000", (uid, bvid, since)).fetchall()
        points = [{"at": row["observed_at"], "metrics": json.loads(row["metrics"])} for row in reversed(rows)]
        delta = {key: points[-1]["metrics"][key] - points[0]["metrics"][key]
                 if len(points) >= 2 and points[-1]["metrics"].get(key) is not None and points[0]["metrics"].get(key) is not None else None for key in METRICS}
        return {"baseline_at": baseline, "points": points, "delta": delta}
