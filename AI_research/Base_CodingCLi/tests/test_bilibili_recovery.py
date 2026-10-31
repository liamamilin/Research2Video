"""Native failure evidence and read-only receipt recovery use isolated accounts only."""
import json
import subprocess
import time

import httpx
import pytest

from core.video import store as video_store
from tests.test_platforms import platform_env, render, native_credentials, PAYLOAD, BV  # noqa: F401
from tests.test_video_scripts import _csrf, _login
from web.services import bilibili, platform_manager as manager
from web.services.bilibili_recovery import failure_evidence, matching_archives


UPOS = "https://upos-cs-upcdnbda2.bilivideo.com/ugcever/file.mp4"


def terminal(url, reason="connection reset"):
    return f'Error: Unknown Error\nreqwest::Error {{ kind: Request, url: "{url}", source: {reason} }}'


def receipt():
    return f'ResponseData {{ code: 0, data: Some(Object {{"bvid": String("{BV}")}}) }}'.encode()


def unknown():
    row = manager.enqueue(render()["id"], PAYLOAD)
    manager.store().update_upload(row["id"], status="UNKNOWN", error="连接中断")
    return manager.store().get_upload(row["id"])


def archive(task, **patch):
    return {"bvid": BV, "title": PAYLOAD["title"], "desc": PAYLOAD["description"], "tid": PAYLOAD["tid"],
            "ctime": int(task["created_at"])+10, "state": 0, **patch}


def listing(monkeypatch, rows):
    monkeypatch.setattr(bilibili.BilibiliClient, "archives", lambda self, **kwargs: {"archives": rows, "pages": 1, "total": len(rows), "complete": True})


@pytest.mark.parametrize("url,phase", [
    ("https://member.bilibili.com/preupload?access_key=test-secret", "preupload"),
    ("https://member.bilibili.com/x/vu/web/cover/up?csrf=test-secret", "cover"),
    (UPOS+"?uploads&output=json", "upload_init"),
    (UPOS+"?partNumber=1&uploadId=test-secret", "transfer"),
    (UPOS+"?uploadId=test-secret&biz_id=123", "merge"),
])
def test_terminal_before_submission_error_is_safe_and_only_retains_allowlisted_evidence(url, phase):
    result = failure_evidence(terminal(url))
    assert result["phase"] == phase and result["confirmed_not_submitted"] and result["retryable"]
    assert "secret" not in json.dumps(result) and "?" not in json.dumps(result)


@pytest.mark.parametrize("output", [
    "error sending request",  # generic old diagnostics cannot prove a stage
    terminal("https://member.bilibili.com/x/vu/web/add/v3?csrf=test-secret"),
    terminal("https://unrecognized.example/upload?token=test-secret"),
    terminal(UPOS)+"\nError: final submit interrupted",  # earlier error is not the terminal cause
    terminal(UPOS)+"\n"+terminal("https://member.bilibili.com/x/vu/web/add/v3"),
])
def test_ambiguous_or_final_submit_failure_never_allows_whole_task_retry(output):
    result = failure_evidence(output)
    assert not result["confirmed_not_submitted"] and not result["retryable"]


def test_storage_auth_rejection_is_not_a_transient_retry():
    result = failure_evidence(terminal(UPOS, "status: 403"))
    assert result["confirmed_not_submitted"] and result["http_status"] == 403 and not result["retryable"]


@pytest.mark.parametrize("cause", ["TimedOut", "connection reset", "IncompleteMessage", "Status(503)"])
def test_native_rust_error_debug_forms_retain_retryable_transport_errors(cause):
    evidence = failure_evidence(terminal(UPOS, cause))
    assert evidence["confirmed_not_submitted"] and evidence["retryable"]


def test_native_transfer_retry_is_bounded_and_preserves_sanitized_attempt_history(platform_env, monkeypatch):
    manager.connect(native_credentials())
    task, calls = manager.enqueue(render()["id"], PAYLOAD), []
    monkeypatch.setattr(bilibili.time, "sleep", lambda _: None)

    def run(args, **kwargs):
        command = args[3]
        calls.append(command)
        fail = command == "upload" and calls.count("upload") < 3
        return subprocess.CompletedProcess(args, 1 if fail else 0, b'' if command == "list" or fail else receipt(),
                                           terminal(UPOS+"?partNumber=1&uploadId=test-secret").encode() if fail else b'')

    monkeypatch.setattr(subprocess, "run", run)
    manager.tick()
    saved = manager.store().get_upload(task["id"])
    assert calls == ["list", "upload", "upload", "upload"]
    assert saved["status"] == "SUBMITTED" and saved["bvid"] == BV and saved["recovery"]["attempt"] == 3
    evidence = manager.directory()/"uploads"/task["id"]
    history = [json.loads(p.read_text()) for p in evidence.glob("diagnostic*.json")]
    assert any(item.get("phase") == "transfer" for item in history)
    assert "test-secret" not in json.dumps(history)


def test_exhausted_transfer_retry_stays_retryable_not_unknown(platform_env, monkeypatch):
    manager.connect(native_credentials())
    task, calls = manager.enqueue(render()["id"], PAYLOAD), []
    monkeypatch.setattr(bilibili.time, "sleep", lambda _: None)

    def run(args, **kwargs):
        calls.append(args[3])
        return subprocess.CompletedProcess(args, 0 if args[3] == "list" else 1, b'', terminal(UPOS).encode())

    monkeypatch.setattr(subprocess, "run", run)
    manager.tick()
    manager.tick()
    saved = manager.store().get_upload(task["id"])
    assert calls == ["list", "upload", "upload", "upload"]
    assert saved["status"] == "FAILED" and saved["recovery"]["confirmed_not_submitted"]


def test_final_submit_network_failure_is_never_reuploaded(platform_env, monkeypatch):
    manager.connect(native_credentials())
    task, calls = manager.enqueue(render()["id"], PAYLOAD), []

    def run(args, **kwargs):
        calls.append(args[3])
        return subprocess.CompletedProcess(args, 0 if args[3] == "list" else 1, b'',
                                           terminal("https://member.bilibili.com/x/vu/web/add/v3?csrf=test-secret").encode())

    monkeypatch.setattr(subprocess, "run", run)
    manager.tick()
    manager.tick()
    saved = manager.store().get_upload(task["id"])
    assert calls == ["list", "upload"] and saved["status"] == "UNKNOWN"
    assert saved["recovery"]["phase"] == "submit" and "test-secret" not in json.dumps(saved)


def test_positive_receipt_is_retained_even_after_abnormal_process_exit(platform_env, monkeypatch):
    manager.connect(native_credentials())
    task = manager.enqueue(render()["id"], PAYLOAD)
    monkeypatch.setattr(subprocess, "run", lambda args, **kwargs: subprocess.CompletedProcess(args, 1 if args[3] == "upload" else 0, receipt(), b''))
    manager.tick()
    saved = manager.store().get_upload(task["id"])
    assert saved["status"] == "SUBMITTED" and saved["bvid"] == BV


def test_creator_lookup_uses_all_states_and_preserves_pagination_without_leaking_private_fields(monkeypatch):
    original, calls = httpx.Client, []

    def respond(req):
        calls.append(req)
        assert req.method == "GET" and req.url.host == "member.bilibili.com"
        assert req.url.params["status"] == "is_pubing,pubed,not_pubed"
        return httpx.Response(200, json={"code": 0, "data": {"page": {"ps": 1, "count": 2}, "arc_audits": [
            {"Archive": {"bvid": BV, "title": "视频", "private": "test-secret"}, "private": "test-secret"}]}})

    monkeypatch.setattr(httpx, "Client", lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs))
    result = bilibili.BilibiliClient().archives()
    assert len(calls) == 2 and result["complete"] and result["pages"] == 2
    assert "secret" not in json.dumps(result)


def test_creator_keyword_lookup_accepts_real_empty_response_but_not_malformed_nonempty_page(monkeypatch):
    original = httpx.Client
    replies = [0, 3]

    def respond(req):
        assert req.url.params["keyword"] == PAYLOAD["title"]
        return httpx.Response(200, json={"code": 0, "data": {"page": {"ps": 20, "count": replies.pop(0)}, "arc_audits": None}})

    monkeypatch.setattr(httpx, "Client", lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs))
    assert bilibili.BilibiliClient().archives(keyword=PAYLOAD["title"])["archives"] == []
    with pytest.raises(bilibili.BilibiliError, match="格式异常"):
        bilibili.BilibiliClient().archives(keyword=PAYLOAD["title"])


@pytest.mark.parametrize("patch", [
    {"desc": "另一份简介"}, {"tid": 1}, {"title": "差不多的标题"}, {"ctime": 0}, {"ctime": None}, {"bvid": "invalid"},
])
def test_title_alone_is_insufficient_to_attach_a_receipt(platform_env, patch):
    task = unknown()
    assert not matching_archives(task, [archive(task, **patch)])


def test_unknown_lookup_matches_publication_without_new_upload_and_is_idempotent(platform_env, monkeypatch):
    task = unknown()
    listing(monkeypatch, [archive(task)])
    monkeypatch.setattr(bilibili, "upload", lambda *args: pytest.fail("lookup must never upload"))
    monkeypatch.setattr(bilibili.BilibiliClient, "owned_video", lambda *args: {"title": PAYLOAD["title"], "state": 0, "pubdate": int(task["created_at"])+10})
    assert manager.lookup_upload(task["id"])["status"] == "PUBLISHED"
    assert manager.lookup_upload(task["id"])["bvid"] == BV
    assert len(video_store.list_publications(task["script_id"])) == 1
    assert manager.store().get_upload(task["id"])["payload"] == task["payload"]


@pytest.mark.parametrize("ambiguous", [False, True])
def test_absence_or_ambiguity_keeps_original_unknown_task_with_durable_backoff(platform_env, monkeypatch, ambiguous):
    task = unknown()
    listing(monkeypatch, [archive(task), archive(task, bvid="BV1yy411c7mD")] if ambiguous else [])
    monkeypatch.setattr(bilibili, "upload", lambda *args: pytest.fail("absence is not permission to reupload"))
    saved = manager.lookup_upload(task["id"])
    assert saved["status"] == "UNKNOWN" and not saved["bvid"]
    assert saved["created_at"] == task["created_at"] and saved["updated_at"] == task["updated_at"]
    assert saved["recovery"]["lookup_count"] == 1 and saved["recovery"]["next_lookup_at"] > time.time()
    assert not video_store.list_publications(task["script_id"])


def test_automatic_lookup_recovers_one_old_task_per_pass_and_honors_restart_backoff(platform_env, monkeypatch):
    one, two = unknown(), unknown()
    with manager.store().connect(write=True) as c:
        c.execute("UPDATE uploads SET updated_at=? WHERE status='UNKNOWN'", (time.time()-120,))
    listing(monkeypatch, [])
    manager.tick()
    manager.tick()
    for row in (one, two):
        saved = manager.store().get_upload(row["id"])
        assert saved["recovery"]["lookup_count"] == 1
    manager.tick()
    assert all(manager.store().get_upload(row["id"])["recovery"]["lookup_count"] == 1 for row in (one, two))


def test_lookup_error_retains_original_receipt_and_schedules_retry(platform_env, monkeypatch):
    task = unknown()
    monkeypatch.setattr(bilibili.BilibiliClient, "archives", lambda *args, **kwargs: (_ for _ in ()).throw(bilibili.BilibiliError("B 站连接失败")))
    saved = manager.lookup_upload(task["id"])
    assert saved["status"] == "UNKNOWN" and "连接失败" in saved["recovery"]["note"]
    assert saved["recovery"]["lookup_count"] == 1


def test_pending_recovered_receipt_is_retained_until_public(platform_env, monkeypatch):
    task = unknown()
    listing(monkeypatch, [archive(task, state=-4)])
    monkeypatch.setattr(bilibili.BilibiliClient, "owned_video", lambda *args: {"title": PAYLOAD["title"], "state": -4})
    saved = manager.lookup_upload(task["id"])
    assert saved["status"] == "SUBMITTED" and saved["bvid"] == BV
    assert not video_store.list_publications(task["script_id"])


def test_receipt_binding_is_atomic_across_competing_tasks(platform_env):
    from concurrent.futures import ThreadPoolExecutor

    one, two = unknown(), unknown()
    db = manager.store()
    with ThreadPoolExecutor(max_workers=2) as executor:
        result = list(executor.map(lambda row: db.attach_upload_receipt(row["id"], "123", BV), (one, two)))
    assert sum(result) == 1
    assert {db.get_upload(row["id"])["status"] for row in (one, two)} == {"SUBMITTED", "UNKNOWN"}


def test_existing_receipt_is_verified_without_searching_or_replacing_it(platform_env, monkeypatch):
    task = unknown()
    manager.store().update_upload(task["id"], status="UNKNOWN", bvid=BV)
    monkeypatch.setattr(bilibili.BilibiliClient, "archives", lambda *args: pytest.fail("known receipt should be verified directly"))
    monkeypatch.setattr(bilibili.BilibiliClient, "owned_video", lambda *args: {"title": PAYLOAD["title"], "state": 0})
    assert manager.lookup_upload(task["id"])["status"] == "PUBLISHED"


def test_lookup_does_not_steal_another_tasks_receipt(platform_env, monkeypatch):
    one, two = unknown(), unknown()
    assert manager.store().attach_upload_receipt(one["id"], "123", BV)
    listing(monkeypatch, [archive(two)])
    saved = manager.lookup_upload(two["id"])
    assert saved["status"] == "UNKNOWN" and not saved["bvid"]
    assert manager.store().get_upload(one["id"])["bvid"] == BV


def test_lookup_requires_account_ownership_role_and_csrf(platform_env, client, monkeypatch):
    task = unknown()
    listing(monkeypatch, [])
    _login(client, "viewer", "viewer-pass-123")
    assert client.post(f"/api/platforms/bilibili/uploads/{task['id']}/lookup", headers=_csrf(client)).status_code == 403
    _login(client)
    assert client.post(f"/api/platforms/bilibili/uploads/{task['id']}/lookup").status_code == 403
    assert client.post(f"/api/platforms/bilibili/uploads/{task['id']}/lookup", headers=_csrf(client)).status_code == 200
    assert client.post('/api/platforms/bilibili/uploads/foreign/lookup', headers=_csrf(client)).status_code == 409
