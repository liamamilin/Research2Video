"""Tests for configurable Bilibili creation_statement and read-only verification."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from core.platform_store import PlatformStore
from web.services import bilibili


def test_clean_creation_statement_validates_known_ids():
    assert bilibili.clean_creation_statement(None) == 0
    assert bilibili.clean_creation_statement(0) == 0
    assert bilibili.clean_creation_statement(1) == 1
    assert bilibili.clean_creation_statement(3) == 3
    assert bilibili.clean_creation_statement({"id": 3}) == 3
    for bad in (2, -1, 99, True, "abc"):
        with pytest.raises(bilibili.BilibiliError):
            bilibili.clean_creation_statement(bad)


def test_verify_creation_statement_distinguishes_confirmed_mismatch_and_unverified(monkeypatch):
    def fake_client(archive):
        class _Client:
            def __init__(self, _cred):
                pass

            def archive_for_edit(self, _bvid):
                return {"archive": archive}

        return _Client

    monkeypatch.setattr(bilibili, "BilibiliClient", fake_client({"creation_statement": {"id": 3}}))
    assert bilibili.verify_creation_statement({}, "BV1abcdefgh1", 3) == {
        "requested": 3, "actual": 3, "status": "confirmed",
    }

    monkeypatch.setattr(bilibili, "BilibiliClient", fake_client({"creation_statement": {"id": -1}}))
    assert bilibili.verify_creation_statement({}, "BV1abcdefgh1", 3) == {
        "requested": 3, "actual": -1, "status": "mismatch",
    }

    monkeypatch.setattr(bilibili, "BilibiliClient", fake_client({"creation_statement_id": 1}))
    assert bilibili.verify_creation_statement({}, "BV1abcdefgh1", 1) == {
        "requested": 1, "actual": 1, "status": "confirmed",
    }

    class _FailClient:
        def __init__(self, _cred):
            pass

        def archive_for_edit(self, _bvid):
            raise bilibili.BilibiliError("network")

    monkeypatch.setattr(bilibili, "BilibiliClient", _FailClient)
    res = bilibili.verify_creation_statement({}, "BV1abcdefgh1", 3)
    assert res["status"] == "unverified" and res["actual"] is None


def test_platform_store_persists_creation_statement_setting(tmp_path):
    store = PlatformStore(str(tmp_path))
    assert store.settings()["creation_statement"] == 0
    saved = store.save_settings({"creation_statement": 3})
    assert saved["creation_statement"] == 3


def test_upload_passes_selected_creation_statement_extra_fields(tmp_path, monkeypatch):
    video = tmp_path / "video.mp4"
    cover = tmp_path / "cover.jpg"
    video.write_bytes(b"mp4")
    cover.write_bytes(b"jpg")
    work_dir = tmp_path / "work"
    work_dir.mkdir()

    seen_args: list[list[str]] = []

    def fake_run(args, **kwargs):
        seen_args.append(list(args))
        if "list" in args:
            return SimpleNamespace(returncode=0, stdout=b"ok", stderr=b"")
        return SimpleNamespace(
            returncode=0,
            stdout=b'ResponseData { code: 0, data: {"bvid": "BV1abcdefgh1"} }',
            stderr=b"",
        )

    monkeypatch.setattr(bilibili, "biliup_binary", lambda: "/usr/local/bin/biliup")
    monkeypatch.setattr(bilibili.subprocess, "run", fake_run)
    creds = {
        "token_info": {"mid": 12345, "access_token": "tok", "refresh_token": "ref", "expires_in": 999999},
        "cookie_info": {"cookies": [
            {"name": "SESSDATA", "value": "s"},
            {"name": "bili_jct", "value": "j"},
            {"name": "DedeUserID", "value": "12345"},
            {"name": "buvid3", "value": "b"},
            {"name": "DedeUserID__ckMd5", "value": "m"},
        ]},
    }
    bvid = bilibili.upload(
        creds,
        video,
        {
            "title": "标题", "description": "简介", "tags": ["科技"],
            "tid": 95, "cover": str(cover), "creation_statement": 3,
        },
        work_dir,
    )
    assert bvid == "BV1abcdefgh1"
    upload_cmd = seen_args[-1]
    idx = upload_cmd.index("--extra-fields")
    assert json.loads(upload_cmd[idx + 1]) == {"creation_statement": {"id": 3}}


def test_local_video_fields_accepts_creation_statement():
    from web.services.local_video import LocalVideoFields

    parsed = LocalVideoFields(title="标题", tid=231, creation_statement=3)
    assert parsed.creation_statement == 3


def test_submitted_reconcile_does_not_head_of_line_block_newer_videos(tmp_path, monkeypatch):
    import time as time_mod
    from web.services import comment_restrictions, platform_manager

    db = PlatformStore(str(tmp_path))
    uid = "12345"
    old_render = {"id": "r-old", "script_id": "s-old", "version": 1}
    new_render = {"id": "r-new", "script_id": "s-new", "version": 1}
    t_old = db.enqueue(uid, old_render, {"title": "旧视频", "tags": ["科技"], "tid": 231, "first_comment": {"enabled": True, "text": "首评", "pin": True}})
    t_new = db.enqueue(uid, new_render, {"title": "新视频", "tags": ["科技"], "tid": 231, "first_comment": {"enabled": True, "text": "首评", "pin": True}})
    db.update_upload(t_old["id"], status="SUBMITTED", bvid="BV1old0000001")
    db.update_upload(t_new["id"], status="SUBMITTED", bvid="BV1new0000002")
    # Age both tasks past the 60s poll threshold.
    past = time_mod.time() - 300
    with db.connect(write=True) as conn:
        conn.execute("UPDATE uploads SET updated_at=? WHERE id IN (?, ?)", (past, t_old["id"], t_new["id"]))

    reconciled: list[str] = []

    def fake_reconcile(task_id: str, bvid: str):
        reconciled.append(bvid)
        if bvid == "BV1old0000001":
            raise bilibili.BilibiliError("稿件不可见")
        db.update_upload(task_id, status="PUBLISHED", bvid=bvid)
        return db.get_upload(task_id)

    commented: list[str] = []
    monkeypatch.setattr(platform_manager, "store", lambda: db)
    monkeypatch.setattr(platform_manager, "directory", lambda: tmp_path)
    monkeypatch.setattr(platform_manager, "account", lambda: {"connected": True, "uid": uid})
    monkeypatch.setattr(platform_manager, "_recover_unknown_uploads", lambda _owner: None)
    monkeypatch.setattr(platform_manager, "reconcile", fake_reconcile)
    monkeypatch.setattr(platform_manager, "process_first_comment", lambda task: commented.append(task["bvid"]))
    monkeypatch.setattr(platform_manager, "process_collection", lambda _task: None)
    monkeypatch.setattr(comment_restrictions, "tick", lambda _owner: None)

    platform_manager.tick()

    assert "BV1old0000001" in reconciled
    assert "BV1new0000002" in reconciled
    assert db.get_upload(t_new["id"])["status"] == "PUBLISHED"
    assert db.get_upload(t_old["id"])["recovery"].get("last_checked_at", 0) > past
    assert commented == ["BV1new0000002"]
