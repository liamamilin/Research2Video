"""Analytics are read-only, account-scoped, durable, and independent of Web uptime."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import threading
import time

import httpx
import pytest

from core.platform_analytics_store import AnalyticsStore, INTERNAL_JOB, SCHEDULE_ID, interval
from core.schedule_runner import Runtime, canonical_job, execute_job
from core.schedule_store import ScheduleStore
from core.video import store as video_store
from tests.test_platforms import platform_env  # noqa: F401
from tests.test_video_scripts import _csrf
from web.server import create_app
from web.services import bilibili, managed_scheduler, platform_analytics as analytics

NOW = 1792836000
BV = "BV1xx411c7mD"


def item(index=0, **patch):
    archive = {"mid": 123, "bvid": f"BV{index:010d}", "aid": index + 1, "title": f"科学视频{index}",
               "ptime": NOW - 9 * 86400, "duration": 90, "tid": 201, "state": 0, **patch}
    return {"Archive": archive, "stat": {"view": 100, "like": 2, "favorite": 3, "coin": 0, "reply": 1}, "typename": "科学科普"}


def provider(monkeypatch, count=113):
    calls = []
    monkeypatch.setattr(bilibili.BilibiliClient, "verify", lambda self: {"uid": "123"})

    def request(self, path, **params):
        calls.append((path, params))
        assert "data" not in params  # No POST, reply, upload or other side effects.
        if path == "/x/web/archives":
            start = (params["pn"] - 1) * 50
            return {"arc_audits": [item(i) for i in range(start, min(start + 50, count))], "page": {"count": count}}, None
        if path.endswith("/num"):
            return {"play": 9, "like": 0, "fav": 1, "fan": -6, "log_date": 20261023}, None
        if path.endswith("/graph"):
            return {"tendency": [{"date_key": NOW - 86400, "total_inc": 9}]}, None
        raise AssertionError(path)
    monkeypatch.setattr(bilibili.BilibiliClient, "request", request)
    return calls


def collector(platform_env):
    return analytics.Collector(str(platform_env / "state"))


def test_catalog_resumes_all_pages_and_manual_baselines(platform_env, monkeypatch):
    calls = provider(monkeypatch)
    worker = collector(platform_env)
    worker.storage.request("123", NOW)
    assert worker.step(now=NOW) == "catalog_page"
    assert worker.storage.account("123")["page"] == 2
    # Fresh process/collector resumes durable cursor, without re-reading overview.
    worker = collector(platform_env)
    assert worker.step(now=NOW + 15, manual_only=True) == "catalog_page"
    assert worker.step(now=NOW + 30, manual_only=True) == "catalog_complete"
    result = worker.storage.videos("123", size=200)
    assert result["total"] == 113
    assert worker.storage.overview("123", 7)["summary"]["fan"] == -6
    assert worker.storage.overview("123", 7)["summary"]["like"] == 0
    assert len(calls) == 7
    assert worker.storage.history("123", "BV0000000000", since=0)["delta"]["view"] is None
    assert worker.step(now=NOW + 60, manual_only=True) == "idle"
    with worker.storage.connect() as db:
        assert db.execute("SELECT count(*) FROM analytics_snapshots").fetchone()[0] == 113


@pytest.mark.parametrize("age,expected", [(60, 1800), (86401, 7200), (8 * 86400, 86400)])
def test_cadence(age, expected):
    assert interval(NOW - age, NOW) == expected


def test_snapshots_preserve_missing_and_negative_corrections(tmp_path):
    store = AnalyticsStore(str(tmp_path))
    row = analytics.catalog_video(item(0), "123")
    store.ingest("123", row, now=NOW)
    store.ingest("123", {**row, "stat": {"view": 999}}, now=NOW + 1)
    assert store.get_video("123", row["bvid"])["metrics"]["view"] == 100
    store.ingest("123", {**row, "stat": {"view": 90}}, now=NOW + 86400)
    history = store.history("123", row["bvid"], since=0)
    assert history["delta"]["view"] == -10
    assert history["delta"]["like"] is None
    store.video_failure("123", row["bvid"], "已删除", NOW + 86401, missing=True)
    assert not store.get_video("123", row["bvid"])["available"]
    assert store.get_video("123", row["bvid"])["metrics"]["view"] == 90
    assert len(store.history("123", row["bvid"], since=0)["points"]) == 2
    assert store.videos("456")["total"] == 0


def test_nonpublic_missing_fields_and_literal_search(tmp_path):
    store = AnalyticsStore(str(tmp_path))
    row = analytics.catalog_video(item(0, title="100%_资料", state=-30), "123")
    store.ingest("123", row, now=NOW)
    assert store.get_video("123", row["bvid"])["metrics"] == {}
    assert store.videos("123", query="%_")["total"] == 1
    assert store.videos("123", query="不存在")["total"] == 0
    assert store.videos("123", scope="workflow", workflow={BV: {}})["total"] == 0
    assert store.history("123", row["bvid"], since=0)["baseline_at"] is None
    with pytest.raises(bilibili.BilibiliError, match="账号"):
        analytics.catalog_video(item(1, mid=456), "123")


def test_matrix_groups_use_all_account_rows_and_paired_weighted_samples(tmp_path):
    store = AnalyticsStore(str(tmp_path))
    workflow = {}
    science = {"matrix": "science", "matrix_name": "科学", "script_id": "s1"}
    samples = [(100, 10, 0), (900, 0, 0), (None, 3, 0), (50, 50, -30), (70, 3, 0)]
    for i, (view, favorite, state) in enumerate(samples):
        row = analytics.catalog_video(item(i, state=state), "123")
        row["stat"] = {"view": view, "favorite": favorite, "like": 1}
        store.ingest("123", row, now=NOW)
        workflow[row["bvid"]] = science
    store.video_failure("123", "BV0000000004", "视频已删除", NOW + 1, missing=True)
    zero = analytics.catalog_video(item(5), "123")
    store.ingest("123", {**zero, "stat": {"view": 0, "favorite": 0}}, now=NOW)
    workflow[zero["bvid"]] = {"matrix": "tech", "matrix_name": "科技", "matrix_archived": True}
    store.ingest("123", analytics.catalog_video(item(6), "123"), now=NOW)
    store.ingest("456", analytics.catalog_video(item(99, mid=456), "456"), now=NOW)
    groups = {g["key"]: g for g in store.matrix_groups("123", workflow)}
    group = groups["matrix:science"]
    assert group["videos"] == 5 and group["public_videos"] == 3
    assert group["view"] == 1000 and group["median_view"] == 500
    assert group["view_samples"] == 2
    assert group["favorite_rate"] == .01 and group["rate_samples"] == 2  # Not mean(10%, 0%).
    assert groups["matrix:tech"]["favorite_rate"] is None
    assert groups["matrix:tech"]["view"] == 0
    assert groups["matrix:tech"]["name"] == "科技（已归档）"
    assert groups["unlinked"]["videos"] == 1
    # Summary is complete even if the corresponding video table is paginated.
    assert store.videos("123", matrix="matrix:science", workflow=workflow, size=1)["total"] == 5
    assert store.videos("123", matrix="matrix:tech", workflow=workflow)["total"] == 1
    assert store.videos("123", matrix="unlinked", workflow=workflow)["total"] == 1
    assert store.videos("123", matrix="unlinked", scope="workflow", workflow=workflow)["total"] == 0
    assert store.videos("123", matrix="matrix:science", query="科学视频1", workflow=workflow)["total"] == 1
    assert store.videos("123", matrix="' OR 1=1 --", workflow=workflow)["total"] == 0


def test_matrix_unknown_and_independent_are_distinct_from_unlinked(tmp_path):
    store = AnalyticsStore(str(tmp_path))
    for i in range(3):
        row = analytics.catalog_video(item(i), "123")
        store.ingest("123", {**row, "stat": {}}, now=NOW)
    workflow = {"BV0000000000": {}, "BV0000000001": {"matrix_unavailable": True}}
    groups = {g["key"]: g for g in store.matrix_groups("123", workflow)}
    assert set(groups) == {"independent", "unknown", "unlinked"}
    assert all(g["view"] is None and g["median_view"] is None and g["view_samples"] == 0 for g in groups.values())
    assert store.videos("123", workflow=workflow, matrix="unknown")["items"][0]["bvid"] == "BV0000000001"
    assert store.videos("123", matrix="unlinked")["total"] == 3
    assert store.videos("123", matrix="independent")["total"] == 0


def test_workflow_matrix_uses_board_ownership_with_active_and_archived_ancestors(platform_env, monkeypatch):
    from core.video import outputs
    parent = video_store.create_script("flash", {"title": "同名视频"})
    child = video_store.create_script("flash", {"parent_script": parent["id"], "title": "同名视频"})
    archived = video_store.create_script("flash", {"title": "历史父文稿"})
    video_store.update_script(archived["id"], archived=1)
    video_store.add_publication(child["id"], url="https://www.bilibili.com/video/" + BV, published_on="2026-10-24", version=1)
    def owners(config_dir, jobs_dir, scripts):
        assert {parent["id"], child["id"], archived["id"]} <= {s["id"] for s in scripts}
        return {child["id"]: {"matrix": "science", "matrix_name": "科学", "matrix_archived": False,
                              "job": "science/daily", "job_name": "科学日报", "production_name": "科学速览"}}, []
    monkeypatch.setattr(outputs, "script_owners", owners)
    links = analytics.workflow_links()
    assert links[BV]["matrix"] == "science"
    assert links[BV]["production_name"] == "科学速览"
    assert len(links) == 1


def test_catalog_mismatch_never_removes_good_cache(tmp_path):
    store = AnalyticsStore(str(tmp_path))
    row = analytics.catalog_video(item(0), "123")
    store.ingest("123", row, now=NOW, run_id="old")
    assert not store.finish_catalog("123", "new", 2, NOW + 1)
    assert store.get_video("123", row["bvid"])["available"]
    assert store.finish_catalog("123", "new", 0, NOW + 2)
    assert not store.get_video("123", row["bvid"])["available"]


def test_manual_request_idempotent_cooldown_and_concurrency(tmp_path):
    store = AnalyticsStore(str(tmp_path))
    with ThreadPoolExecutor(4) as pool:
        results = list(pool.map(lambda _: store.request("123", NOW), range(4)))
    assert all(r["requested_at"] == NOW for r in results)
    store.update_account("123", status="IDLE", requested_at=0)
    with pytest.raises(ValueError, match="60"):
        store.request("123", NOW + 10)
    store.request("123", NOW + 61)


def test_auth_backoff_and_cached_data_survive(platform_env, monkeypatch):
    calls = provider(monkeypatch, 1)
    worker = collector(platform_env)
    worker.storage.request("123", NOW)
    assert worker.step(now=NOW) == "catalog_complete"
    worker.storage.request("123", NOW + 61)
    def rejected(self):
        raise bilibili.BilibiliRejected("登录失效", code=-101)
    monkeypatch.setattr(bilibili.BilibiliClient, "verify", rejected)
    assert worker.step(now=NOW + 61) == "error"
    assert worker.step(now=NOW + 62) == "backoff"
    assert worker.storage.account("123")["status"] == "PAUSED"
    assert worker.storage.videos("123")["total"] == 1
    assert len(calls) == 5


def test_automatic_video_refresh_and_no_automatic_comment_watch(platform_env, monkeypatch):
    provider(monkeypatch, 1)
    worker = collector(platform_env)
    worker.storage.request("123", NOW)
    worker.step(now=NOW)
    worker.storage.update_account("123", enabled=1, next_catalog_at=NOW + 100000, next_account_at=NOW + 100000)
    ScheduleStore(str(platform_env / "state")).save(INTERNAL_JOB, "*/5 * * * *", schedule_id=SCHEDULE_ID, now=NOW)
    calls = []
    def owned(self, bvid, uid):
        calls.append((bvid, uid))
        return {**item(0)["Archive"], "pubdate": NOW - 9 * 86400, "stat": {"view": 120}}
    monkeypatch.setattr(bilibili.BilibiliClient, "owned_video", owned)
    assert worker.step(now=NOW + 86400) == "videos:1"
    assert calls == [("BV0000000000", "123")]
    assert worker.storage.history("123", "BV0000000000", since=0)["delta"]["view"] == 20
    from core.platform_store import PlatformStore
    with PlatformStore(str(platform_env / "state")).connect() as db:
        assert db.execute("SELECT count(*) FROM videos").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM comments").fetchone()[0] == 0
    ScheduleStore(str(platform_env / "state")).toggle(SCHEDULE_ID, False)
    assert worker.step(now=NOW + 86401) == "idle"


def test_single_video_404_retains_history_and_rate_limit_stops_tick(platform_env, monkeypatch):
    provider(monkeypatch, 1)
    worker = collector(platform_env)
    worker.storage.request("123", NOW)
    worker.step(now=NOW)
    worker.storage.update_account("123", enabled=1, next_catalog_at=NOW + 200000, next_account_at=NOW + 200000)
    ScheduleStore(str(platform_env / "state")).save(INTERNAL_JOB, "*/5 * * * *", schedule_id=SCHEDULE_ID, now=NOW)
    def missing(*args):
        raise bilibili.BilibiliRejected("视频不存在", code=-404)
    monkeypatch.setattr(bilibili.BilibiliClient, "owned_video", missing)
    assert worker.step(now=NOW + 86400) == "videos:0"
    old = worker.storage.get_video("123", "BV0000000000")
    assert old["missing"] and old["metrics"]["view"] == 100
    worker.storage.ingest("123", analytics.catalog_video(item(0), "123"), now=NOW + 86401, force=True)
    def rate_limit(*args):
        raise bilibili.BilibiliRejected("访问频繁", code=-509)
    monkeypatch.setattr(bilibili.BilibiliClient, "owned_video", rate_limit)
    assert worker.step(now=NOW + 172802) == "error"
    assert worker.step(now=NOW + 172803) == "backoff"
    assert len(worker.storage.history("123", "BV0000000000", since=0)["points"]) == 2


def test_collector_lock_and_switching_account_do_not_mix_data(platform_env, monkeypatch):
    calls = provider(monkeypatch, 1)
    worker = collector(platform_env)
    worker.storage.request("123", NOW)
    from core.fileio import file_lock
    with file_lock(str(worker.folder / "analytics-123.lock"), timeout=0):
        assert worker.step(now=NOW) == "busy"
    assert not calls
    worker.step(now=NOW)
    bilibili.private_json(worker.folder / "account.json", {"uid": "456"})
    assert worker.step(now=NOW + 100) == "disconnected"  # Profile/cookie mismatch never makes a request.
    bilibili.private_json(worker.folder / "credentials.json", bilibili.normalize_credentials({"sessdata": "fake", "bili_jct": "fake", "dedeuserid": "456"}))
    assert worker.step(now=NOW + 100) == "idle"  # New owner defaults to disabled.
    assert worker.storage.videos("456")["total"] == 0
    assert worker.storage.videos("123")["total"] == 1


def test_bad_account_graph_is_explicit_and_preserves_last_success(platform_env, monkeypatch):
    calls = provider(monkeypatch, 1)
    worker = collector(platform_env)
    worker.storage.request("123", NOW)
    worker.step(now=NOW)
    original = bilibili.BilibiliClient.request
    def request(self, path, **params):
        if path.endswith("/graph"):
            raise bilibili.BilibiliError("账号统计暂不可用")
        return original(self, path, **params)
    monkeypatch.setattr(bilibili.BilibiliClient, "request", request)
    worker.storage.request("123", NOW + 61)
    assert worker.step(now=NOW + 61) == "catalog_complete"
    assert worker.storage.overview("123", 7)["synced_at"] == NOW
    with worker.storage.connect() as db:
        assert db.execute("SELECT error FROM analytics_overview_errors WHERE uid='123'").fetchone()[0] == "账号统计暂不可用"
    assert len(calls) == 7


def test_settings_register_one_schedule_and_builtin_no_research(platform_env, monkeypatch):
    installed = []
    monkeypatch.setattr(managed_scheduler, "ensure_online", lambda: installed.append(True))
    analytics.set_enabled(True)
    analytics.set_enabled(True)
    plans = ScheduleStore(str(platform_env / "state")).list()
    assert len(plans) == 1 and plans[0]["job_name"] == INTERNAL_JOB
    assert plans[0]["schedule"] == "*/5 * * * *"
    analytics.set_enabled(False)
    assert not collector(platform_env).storage.account("123")["enabled"]
    runtime = Runtime(str(platform_env), str(platform_env / "state"), str(platform_env / "jobs"), str(platform_env / "config"), str(platform_env / "logs"))
    assert canonical_job(runtime, INTERNAL_JOB)[1]["runtime"]["timeout_seconds"] == 300
    from core import budget
    monkeypatch.setattr(budget, "pipeline_allowed", lambda *a: pytest.fail("analytics must never consume research budget"))
    monkeypatch.setattr(analytics, "run_scheduled", lambda state_dir, cancel: ("success", "", None))
    assert execute_job(runtime, INTERNAL_JOB, threading.Event()) == ("success", "", None)


@pytest.mark.asyncio
async def test_routes_cache_only_rbac_and_exact_workflow_links(platform_env, monkeypatch):
    calls = provider(monkeypatch, 1)
    worker = collector(platform_env)
    worker.storage.request("123", time.time())
    worker.step()
    before = len(calls)
    script = video_store.create_script("flash", {"title": "本地文稿"})
    video_store.add_publication(script["id"], platform="bilibili", url="https://www.bilibili.com/video/BV0000000000", published_on="2026-10-24", version=1)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app()), base_url="http://test") as client:
        assert (await client.post("/api/auth/login", json={"username": "viewer", "password": "viewer-pass-123"})).status_code == 200
        response = await client.get("/api/platforms/bilibili/analytics?scope=workflow")
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["videos"]["total"] == 1
        assert body["videos"]["items"][0]["publication"]["script_id"] == script["id"]
        assert body["matrices"][0]["key"] == "independent"
        assert (await client.get("/api/platforms/bilibili/analytics?matrix=unlinked")).json()["videos"]["total"] == 0
        filtered = (await client.get("/api/platforms/bilibili/analytics?matrix=independent")).json()
        assert filtered["videos"]["total"] == 1
        assert filtered["overview"] == body["overview"]
        assert "secret" not in json.dumps(body)
        assert len(calls) == before
        assert (await client.get("/api/platforms/bilibili/analytics?period=8")).status_code == 422
        assert (await client.post("/api/platforms/bilibili/analytics/sync", headers=_csrf(client))).status_code == 403
        assert (await client.get("/api/platforms/bilibili/analytics/videos/BV0000000000")).status_code == 200
        assert (await client.get("/api/platforms/bilibili/analytics/videos/BV0000000001")).status_code == 409
        assert (await client.post("/api/auth/login", json={"username": "editor", "password": "editor-pass-123"})).status_code == 200
        assert (await client.put("/api/platforms/bilibili/analytics/settings", json={"enabled": True}, headers=_csrf(client))).status_code == 403
        # Starting a manual sync twice is idempotent; it queues local work, never blocks on Bilibili.
        worker.storage.update_account("123", last_requested_at=0)
        assert (await client.post("/api/platforms/bilibili/analytics/sync", headers=_csrf(client))).status_code == 202
        assert len(calls) == before
        assert (await client.post("/api/auth/login", json={"username": "admin", "password": "admin-pass-123"})).status_code == 200
        monkeypatch.setattr(managed_scheduler, "ensure_online", lambda: {"online": True})
        response = await client.put("/api/platforms/bilibili/analytics/settings", json={"enabled": True}, headers=_csrf(client))
        assert response.status_code == 200, response.text
        assert response.json()["enabled"] == 1
