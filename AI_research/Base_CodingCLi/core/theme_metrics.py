"""Read public video counters with the existing Bilibili connection; cache observations across matrices."""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from . import sqlite_util
from .fileio import atomic_write, file_lock

METRICS = {"view", "like", "favorite", "coin", "reply", "share", "danmaku"}
BV = re.compile(r"/video/(BV[0-9A-Za-z]{10})(?:[/ ?#]|$)")


def video_id(url: str) -> str | None:
    parts = urlsplit(url)
    if parts.scheme not in {"https", "http"} or not (parts.hostname == "bilibili.com" or (parts.hostname or "").endswith(".bilibili.com")):
        return None
    match = BV.search(parts.path + "/")
    return match[1] if match else None


class MetricStore:
    def __init__(self, state_dir: str):
        self.root = Path(state_dir)
        self.path = str(self.root / "theme_metrics.db")
        with sqlite_util.connect(self.path) as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS videos (
                    bvid TEXT PRIMARY KEY, observed_at REAL NOT NULL, attempted_at REAL NOT NULL,
                    data TEXT NOT NULL, error TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS observations (
                    bvid TEXT NOT NULL, observed_at REAL NOT NULL, metrics TEXT NOT NULL,
                    PRIMARY KEY (bvid,observed_at)
                );
                CREATE INDEX IF NOT EXISTS observations_video ON observations(bvid,observed_at);
            ''')

    def get(self, bvid: str) -> dict | None:
        with sqlite_util.connect(self.path) as db:
            row = db.execute("SELECT * FROM videos WHERE bvid=?", (bvid,)).fetchone()
        return {**dict(row), "data": json.loads(row["data"])} if row else None

    def save(self, bvid: str, data: dict, now: float) -> dict:
        metrics = data["metrics"]
        with sqlite_util.connect(self.path, write=True) as db:
            previous = db.execute("SELECT observed_at,metrics FROM observations WHERE bvid=? AND observed_at<=? "
                                  "ORDER BY observed_at DESC LIMIT 1", (bvid, now - 3600)).fetchone()
            if previous:
                old = json.loads(previous["metrics"])
                elapsed = now - previous["observed_at"]
                if "view" in old and "view" in metrics and metrics["view"] >= old["view"]:
                    data["growth"] = {"views": metrics["view"] - old["view"], "hours": round(elapsed / 3600, 3),
                                      "views_per_day": round((metrics["view"] - old["view"]) * 86400 / elapsed, 2)}
            db.execute("INSERT OR REPLACE INTO observations VALUES (?,?,?)", (bvid, now, json.dumps(metrics)))
            db.execute("INSERT OR REPLACE INTO videos VALUES (?,?,?,?,'')", (bvid, now, now, json.dumps(data, ensure_ascii=False)))
        return data

    def failure(self, bvid: str, now: float, error: str) -> None:
        with sqlite_util.connect(self.path, write=True) as db:
            db.execute("INSERT INTO videos VALUES (?,0,?,'{}',?) ON CONFLICT(bvid) DO UPDATE "
                       "SET attempted_at=excluded.attempted_at,error=excluded.error", (bvid, now, error))


class BilibiliMetrics:
    def __init__(self, state_dir: str, *, client=None, clock=time.time, sleeper=time.sleep,
                 cache_seconds: float = 21600, spacing: float = 0.8):
        self.store = MetricStore(state_dir)
        self.clock, self.sleep = clock, sleeper
        self.cache_seconds, self.spacing = cache_seconds, spacing
        self.requests = 0
        self.hits = 0
        self.blocked = ""
        credentials_path = Path(state_dir) / "platforms/bilibili/credentials.json"
        self.connection_version = credentials_path.stat().st_mtime if credentials_path.exists() else 0
        if client is not None:
            self.client = client
        else:
            from web.services.bilibili import BilibiliClient
            try:
                credentials = json.loads(credentials_path.read_text())
            except (OSError, ValueError):
                credentials = None
            self.client = BilibiliClient(credentials)
        self.authenticated = bool(getattr(self.client, "cookies", {}).get("SESSDATA"))

    @staticmethod
    def envelope(data: dict, observed: float, *, status: str, error: str = "") -> dict:
        return {**data, "metrics": data.get("metrics", {}), "metrics_status": status,
                "metrics_observed_at": observed or None, "metrics_error": error,
                "metrics_source": "bilibili_view" if observed else "", "metrics_cached": status == "cached"}

    def collect(self, url: str) -> dict:
        try:
            return self._collect(url)
        except TimeoutError:
            cached = self.store.get(video_id(url) or "")
            return self.envelope(cached["data"] if cached else {}, cached["observed_at"] if cached else 0,
                                 status="stale" if cached and cached["observed_at"] else "unavailable",
                                 error="其他矩阵正在采集，稍后刷新")

    def _collect(self, url: str) -> dict:
        bvid = video_id(url)
        if not bvid:
            return self.envelope({}, 0, status="unsupported")
        # One bounded request lane across all matrix jobs and web processes.
        with file_lock(str(self.store.root / "theme-bilibili-request"), timeout=45):
            now = self.clock()
            cached = self.store.get(bvid)
            if cached and cached["observed_at"] and now - cached["observed_at"] < self.cache_seconds:
                self.hits += 1
                return self.envelope(cached["data"], cached["observed_at"], status="cached")
            def unavailable(message):
                return self.envelope(cached["data"] if cached else {}, cached["observed_at"] if cached else 0,
                                     status="stale" if cached and cached["observed_at"] else "unavailable", error=message)
            if cached and cached["error"] and now - cached["attempted_at"] < 900 and cached["attempted_at"] >= self.connection_version:
                return unavailable(cached["error"])
            lane = self.store.root / "theme-bilibili-request.json"
            try:
                control = json.loads(lane.read_text())
            except (OSError, ValueError):
                control = {}
            if control.get("blocked_until", 0) > now and control.get("connection_version", 0) == self.connection_version:
                self.blocked = control.get("error") or "B站暂不可用，稍后重试"
                return unavailable(control.get("error") or "B站暂不可用，稍后重试")
            wait = self.spacing - (now - control.get("last_request", 0))
            if wait > 0:
                self.sleep(wait)
            now = self.clock()
            atomic_write(str(lane), json.dumps({"last_request": now}))
            try:
                self.requests += 1
                data, _ = self.client.request("/x/web-interface/view", bvid=bvid)
                if data.get("bvid") != bvid:
                    raise ValueError("B站未返回对应视频的数据")
                metrics = {k: v for k, v in (data.get("stat") or {}).items() if k in METRICS and type(v) is int and v >= 0}
                if "view" not in metrics:
                    raise ValueError("B站未返回播放计数")
                value = {"bvid": bvid, "metrics": metrics, "video_title": str(data.get("title") or ""),
                         "video_published_at": data.get("pubdate") if type(data.get("pubdate")) is int else None,
                         "video_author": str((data.get("owner") or {}).get("name") or "")}
                if value["video_published_at"]:
                    value["video_published_date"] = datetime.fromtimestamp(value["video_published_at"], timezone.utc).date().isoformat()
                value = self.store.save(bvid, value, now)
                return self.envelope(value, now, status="fresh")
            except Exception as exc:
                code, http = getattr(exc, "code", None), getattr(exc, "http_status", None)
                if http in (401, 403, 412, 429) or code in (-101, -111, -352, -509):
                    message = "B站连接暂不可用，请在平台管理检查登录连接后重试" if self.authenticated else "请在平台管理连接B站账号后获取真实指标"
                    self.blocked = message
                    atomic_write(str(lane), json.dumps({"last_request": now, "blocked_until": now + 900, "error": message, "connection_version": self.connection_version}))
                else:
                    message = "该视频计数暂不可用，保留上次数据"
                self.store.failure(bvid, now, message)
                return unavailable(message)
