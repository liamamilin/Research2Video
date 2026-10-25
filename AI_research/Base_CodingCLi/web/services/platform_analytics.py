"""Read-only Bilibili measurements, shared by the Web API and independent scheduler.

The scheduler never uploads, comments, or calls an LLM. UI reads only local caches.
"""
from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import sqlite3
import threading
import time
from zoneinfo import ZoneInfo

from core.fileio import file_lock
from core.platform_analytics_store import AnalyticsStore, INTERNAL_JOB, SCHEDULE_ID
from core.schedule_store import ScheduleStore
from web.services.bilibili import BilibiliClient, BilibiliError, BilibiliRejected, BV


def integer(value, default: int = 0) -> int:
    return value if type(value) is int else default


def catalog_video(item: dict, uid: str) -> dict:
    archive = item.get("Archive") or {}
    if str(archive.get("mid")) != uid:
        raise BilibiliError("视频目录账号不一致，采集已停止。")
    bvid = str(archive.get("bvid") or "")
    if not BV.fullmatch(bvid):
        raise BilibiliError("视频目录返回了无效的 BV 号。")
    return {"bvid": bvid, "aid": integer(archive.get("aid")), "title": str(archive.get("title") or ""),
            "pubdate": integer(archive.get("ptime")) or integer(archive.get("online_time")),
            "duration": integer(archive.get("duration")), "tid": integer(archive.get("tid")),
            "tname": " / ".join(str(item[k]) for k in ("parent_tname", "typename") if item.get(k)),
            "state": archive.get("state") if type(archive.get("state")) is int else None,
            "state_desc": str(archive.get("state_desc") or ""), "stat": item.get("stat") or {}}


def public_video(data: dict) -> dict:
    return {"bvid": data["bvid"], "aid": integer(data.get("aid")), "title": str(data.get("title") or ""),
            "pubdate": integer(data.get("pubdate")), "duration": integer(data.get("duration")),
            "tid": integer(data.get("tid")), "tname": str(data.get("tname") or ""),
            "state": data.get("state") if type(data.get("state")) is int else None,
            "state_desc": "已公开" if data.get("state") == 0 else str(data.get("state_desc") or "状态待核对"),
            "stat": data.get("stat") or {}}


def account_data(summary: dict, graph: dict) -> tuple[dict, list, str]:
    stamp = str(summary.get("log_date") or "")
    try:
        cutoff = datetime.strptime(stamp, "%Y%m%d").date().isoformat()
    except ValueError as exc:
        raise BilibiliError("账号统计缺少有效的统计截止日期。") from exc
    values = {key: summary.get(source) if type(summary.get(source)) is int else None
              for key, source in {"view": "play", "like": "like", "favorite": "fav", "fan": "fan"}.items()}
    rows = graph.get("tendency")
    if not isinstance(rows, list) or not rows:
        raise BilibiliError("账号播放趋势暂时不可用。")
    points = []
    for row in rows:
        if not isinstance(row, dict) or type(row.get("date_key")) is not int or type(row.get("total_inc")) is not int:
            raise BilibiliError("账号播放趋势格式发生变化，保留上次数据。")
        date = datetime.fromtimestamp(row["date_key"], ZoneInfo("Asia/Shanghai")).date().isoformat()
        points.append({"date": date, "value": row["total_inc"]})
    return values, sorted(points, key=lambda p: p["date"]), cutoff


def paused_error(exc: BilibiliError) -> bool:
    return getattr(exc, "code", None) in (-101, -111, -509, -352) or getattr(exc, "http_status", None) in (401, 403, 412, 429)


class Collector:
    def __init__(self, state_dir: str):
        self.state_dir = str(Path(state_dir).resolve())
        self.folder = Path(self.state_dir) / "platforms" / "bilibili"
        self.storage = AnalyticsStore(self.state_dir)

    def identity(self) -> tuple[str, BilibiliClient] | None:
        try:
            profile = json.loads((self.folder / "account.json").read_text())
            credentials = json.loads((self.folder / "credentials.json").read_text())
            client = BilibiliClient(credentials)
            uid = str(profile.get("uid") or "")
            if not uid.isdigit() or client.cookies.get("DedeUserID") != uid:
                return None
            return uid, client
        except (OSError, ValueError, TypeError, KeyError):
            return None

    def step(self, *, manual_only: bool = False, now: float | None = None, cancel: threading.Event | None = None) -> str:
        identity = self.identity()
        if not identity:
            return "disconnected"
        uid, client = identity
        now = time.time() if now is None else now
        try:
            with file_lock(str(self.folder / f"analytics-{uid}.lock"), timeout=0):
                config = self.storage.account(uid)
                manual = bool(config["requested_at"])
                plan = ScheduleStore(self.state_dir).get(SCHEDULE_ID)
                automatic = config["enabled"] and plan and plan["enabled"]
                if (manual_only and not manual) or (not manual and not automatic) or (cancel and cancel.is_set()):
                    return "idle"
                if config["blocked_until"] > now:
                    return "backoff"
                self.storage.update_account(uid, worker_at=now, last_attempt_at=now)
                try:
                    return self.collect(uid, client, config, now, manual, cancel)
                except BilibiliError as exc:
                    self.storage.failure(uid, str(exc), now=now, paused=paused_error(exc))
                    return "error"
                except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
                    self.storage.failure(uid, "B 站数据格式发生变化，保留上次数据，稍后重试。", now=now)
                    return "error"
        except TimeoutError:
            return "busy"

    def collect(self, uid: str, client: BilibiliClient, config: dict, now: float,
                manual: bool, cancel: threading.Event | None) -> str:
        # Verify the authenticated owner once before every full catalog pass.
        catalog_due = manual or bool(config["run_id"]) or config["next_catalog_at"] <= now
        if catalog_due and not config["run_id"]:
            if client.verify()["uid"] != uid:
                raise BilibiliError("登录账号已变化，采集已停止，请重新连接账号。")
            config = self.storage.begin_catalog(uid, now)
        if config["next_account_at"] <= now or (manual and config["page"] == 1):
            # Preserve video collection if only the account overview endpoint is unavailable.
            try:
                for period, remote_period in ((7, 0), (30, 1)):
                    if cancel and cancel.is_set():
                        return "cancelled"
                    summary, _ = client.request("/x/web/data/v2/overview/stat/num", member=True,
                                                period=remote_period, s_locale="zh_CN", tab=0)
                    graph, _ = client.request("/x/web/data/v2/overview/stat/graph", member=True,
                                              period=remote_period, s_locale="zh_CN", type="play")
                    values, points, date = account_data(summary, graph)
                    self.storage.save_overview(uid, period, values, points, date, now)
                self.storage.update_account(uid, next_account_at=now + 6 * 3600)
                with self.storage.connect(write=True) as db:
                    db.execute("DELETE FROM analytics_overview_errors WHERE uid=?", (uid,))
            except BilibiliError as exc:
                if paused_error(exc):
                    raise
                self.storage.update_account(uid, next_account_at=now + 1800)
                # An explicit error accompanies stale account data, independently of the video collector.
                self.overview_error(uid, str(exc), now)
        if catalog_due:
            if cancel and cancel.is_set():
                return "cancelled"
            data, _ = client.request("/x/web/archives", member=True, pn=config["page"], ps=50,
                                     status="pubed,not_pubed,is_pubing")
            rows, page = data.get("arc_audits"), data.get("page") or {}
            total = page.get("count")
            if not isinstance(rows, list) or type(total) is not int or not 0 <= total <= 100000:
                raise BilibiliError("视频目录分页格式发生变化，保留上次数据。")
            if not rows and config["page"] * 50 <= total:
                raise BilibiliError("视频目录提前返回空页，稍后继续核对。")
            # Validate the whole page before persisting any of it.
            parsed = [catalog_video(row, uid) for row in rows]
            for row in parsed:
                self.storage.ingest(uid, row, now=now, run_id=config["run_id"], force=manual)
            if config["page"] * 50 >= total:
                self.storage.finish_catalog(uid, config["run_id"], total, now)
                return "catalog_complete"
            self.storage.update_account(uid, page=config["page"] + 1, remote_total=total)
            return "catalog_page"
        self.discover_uploads(uid, client, now, cancel)
        count = 0
        for row in self.storage.due(uid, now, 5):
            if cancel and cancel.is_set():
                return "cancelled"
            try:
                detail = client.owned_video(row["bvid"], uid)
                self.storage.ingest(uid, public_video(detail), now=now, source="video")
                count += 1
            except BilibiliRejected as exc:
                if paused_error(exc):
                    raise
                self.storage.video_failure(uid, row["bvid"], str(exc), now, missing=getattr(exc, "code", None) in (-404, 62002))
            except BilibiliError as exc:
                if paused_error(exc) or not isinstance(exc, BilibiliRejected):
                    raise
                self.storage.video_failure(uid, row["bvid"], str(exc), now)
        self.storage.update_account(uid, status="IDLE", error="", failures=0, blocked_until=0,
                                    last_success_at=now)
        return f"videos:{count}"

    def overview_error(self, uid: str, error: str, now: float) -> None:
        # Dedicated cache metadata: never replace good measurements with an error payload.
        with self.storage.connect(write=True) as db:
            db.execute("INSERT INTO analytics_overview_errors VALUES (?,?,?) ON CONFLICT(uid) DO UPDATE SET error=excluded.error,at=excluded.at",
                       (uid, error, now))

    def discover_uploads(self, uid: str, client: BilibiliClient, now: float, cancel: threading.Event | None) -> None:
        path = Path(self.state_dir) / "platforms.db"
        if not path.is_file():
            return
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2) as db:
            rows = db.execute("SELECT DISTINCT bvid FROM uploads WHERE uid=? AND status='PUBLISHED' AND bvid<>''", (uid,)).fetchall()
        attempted = 0
        for (bvid,) in rows:
            if cancel and cancel.is_set():
                return
            if BV.fullmatch(bvid) and not self.storage.get_video(uid, bvid):
                attempted += 1
                try:
                    self.storage.ingest(uid, public_video(client.owned_video(bvid, uid)), now=now, source="video")
                except BilibiliRejected as exc:
                    if paused_error(exc):
                        raise
                if attempted >= 2:
                    break


def run_scheduled(state_dir: str, cancel: threading.Event) -> tuple[str, str, str | None]:
    collector = Collector(state_dir)
    deadline = time.monotonic() + 240
    result = "idle"
    while not cancel.is_set() and time.monotonic() < deadline:
        result = collector.step(cancel=cancel)
        if result != "catalog_page":
            break
        cancel.wait(0.3)
    if cancel.is_set():
        return "cancelled", "采集已取消，已保存分页进度。", None
    return ("failed", "采集暂时失败，详情见平台管理的视频数据页。", None) if result == "error" else ("success", "", None)


def current() -> tuple[Collector, str]:
    from web.settings import get_settings
    collector = Collector(get_settings().paths.state_dir)
    identity = collector.identity()
    if not identity:
        raise BilibiliError("请先连接哔哩哔哩账号。")
    return collector, identity[0]


def workflow_links() -> dict:
    from core.video import outputs, store as video_store
    from web.settings import get_settings
    path = Path(video_store.db_path())
    if not path.is_file():
        return {}
    result = {}
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2) as db:
        db.row_factory = sqlite3.Row
        rows = db.execute("SELECT script_id,version,render_id,url,published_on FROM publications WHERE platform='bilibili' ORDER BY created_at").fetchall()
    for row in rows:
        match = BV.search(row["url"] or "")
        if match:
            result[match.group()] = {key: row[key] for key in ("script_id", "version", "render_id", "published_on")}
    if result:
        settings = get_settings()
        # Use the board's real origin chain, including parent scripts and archived matrices.
        # A recipe tag or a similar title is insufficient evidence of matrix ownership.
        scripts = video_store.list_scripts(limit=100000, archived=False) + video_store.list_scripts(limit=100000, archived=True)
        owners, _ = outputs.script_owners(settings.paths.config_dir, settings.paths.jobs_dir, scripts)
        for publication in result.values():
            owner = owners.get(publication["script_id"])
            if owner is None:
                publication["matrix_unavailable"] = True
            else:
                publication.update({key: owner[key] for key in ("matrix", "matrix_name", "matrix_archived", "job", "job_name", "production_name")})
    return result


def cached(*, period: int = 7, query: str = "", scope: str = "all", matrix: str = "", sort: str = "date", page: int = 1, size: int = 25) -> dict:
    from web.services import managed_scheduler
    collector, uid = current()
    storage = collector.storage
    plan = ScheduleStore(collector.state_dir).get(SCHEDULE_ID)
    with storage.connect() as db:
        error = db.execute("SELECT error,at FROM analytics_overview_errors WHERE uid=?", (uid,)).fetchone()
        total = db.execute("SELECT count(*) FROM analytics_videos WHERE uid=?", (uid,)).fetchone()[0]
        due = db.execute("SELECT min(next_due) FROM analytics_videos WHERE uid=? AND missing=0", (uid,)).fetchone()[0]
    state = storage.account(uid)
    links = workflow_links()
    return {"uid": uid, "collector": state,
            "schedule": {"enabled": bool(plan and plan["enabled"]), "next_run_at": plan["next_run_at"] if plan else None,
                         "online": managed_scheduler.store().health().get("online", False)},
            "overview": storage.overview(uid, period), "overview_error": dict(error) if error else None,
            "catalog_total": total, "next_video_at": due,
            "matrices": storage.matrix_groups(uid, links),
            "videos": storage.videos(uid, query=query.strip(), workflow=links, scope=scope, matrix=matrix, sort=sort, page=page, size=size)}


def detail(bvid: str, days: int = 30) -> dict:
    collector, uid = current()
    video = collector.storage.get_video(uid, bvid)
    if not video:
        raise BilibiliError("当前账号缓存中没有这条视频，请先同步视频目录。")
    comments = None
    path = Path(collector.state_dir) / "platforms.db"
    if path.is_file():
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2) as db:
            sync = db.execute("SELECT synced_at FROM videos WHERE uid=? AND bvid=?", (uid, bvid)).fetchone()
            pending = db.execute("SELECT count(*) FROM comments WHERE uid=? AND bvid=? AND author_mid<>? AND status='NEW'", (uid, bvid, uid)).fetchone()[0]
        comments = {"pending": pending, "synced_at": sync[0] if sync else None}
    return {"video": {**video, "publication": workflow_links().get(bvid)},
            "history": collector.storage.history(uid, bvid, since=time.time() - days * 86400), "days": days, "comments": comments}


def request_sync() -> dict:
    collector, uid = current()
    try:
        with file_lock(str(collector.folder / f"analytics-{uid}.lock"), timeout=0):
            return collector.storage.request(uid)
    except TimeoutError as exc:
        raise BilibiliError("该账号正在采集，请稍后查看同步进度。") from exc
    except ValueError as exc:
        raise BilibiliError(str(exc)) from exc


def set_enabled(enabled: bool) -> dict:
    from web.services import managed_scheduler
    collector, uid = current()
    if enabled:
        managed_scheduler.ensure_online()
        schedules = ScheduleStore(collector.state_dir)
        plan = schedules.get(SCHEDULE_ID)
        if not plan:
            schedules.save(INTERNAL_JOB, "*/5 * * * *", schedule_id=SCHEDULE_ID,
                           timezone="Asia/Shanghai", max_retries=0)
        elif plan["job_name"] != INTERNAL_JOB:
            raise BilibiliError("视频数据调度标识被其他计划占用。")
        else:
            schedules.toggle(SCHEDULE_ID, True)
    result = collector.storage.update_account(uid, enabled=int(enabled))
    if enabled and not result["last_success_at"] and not result["blocked_until"]:
        try:
            result = collector.storage.request(uid)
        except ValueError:
            pass  # A recent manual request is cooling down; the independent plan is already enabled.
    return result


def manual_tick() -> str:
    from web.settings import get_settings
    return Collector(get_settings().paths.state_dir).step(manual_only=True)


def reconnected(state_dir: str, uid: str) -> None:
    """Refreshing the same account's credentials releases its authentication backoff."""
    if not (Path(state_dir) / "platform_analytics.db").is_file():
        return
    storage = AnalyticsStore(state_dir)
    storage.update_account(uid, blocked_until=0, failures=0, error="", status="IDLE")
