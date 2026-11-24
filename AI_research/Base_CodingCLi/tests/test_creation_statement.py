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
