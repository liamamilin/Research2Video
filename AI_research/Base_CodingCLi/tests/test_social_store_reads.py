"""Reading a publisher's queue never waits on its write lock, and one login failure is counted once."""
from __future__ import annotations

import sqlite3
import time

from core.social.session_health import IDENTITY_FAILURE
from core.social.store import SocialStore
from web.services import social_publishing as manager


def test_opening_a_known_queue_does_not_wait_for_a_busy_writer(tmp_path):
    first = SocialStore(str(tmp_path), "kuaishou")
    first.create("UPLOAD", "42", {"title": "t"}, render={"id": "r", "script_id": "s", "version": 1})
    writer = sqlite3.connect(first.path, timeout=0)
    writer.execute("BEGIN IMMEDIATE")
    try:
        started = time.monotonic()
        again = SocialStore(str(tmp_path), "kuaishou")
        rows = again.list(uid="42")
        assert time.monotonic() - started < 1
    finally:
        writer.rollback(); writer.close()
    assert [row["kind"] for row in rows] == ["UPLOAD"]


def test_a_deleted_queue_file_gets_its_schema_again(tmp_path):
    first = SocialStore(str(tmp_path), "kuaishou")
    for suffix in ("", "-wal", "-shm"):
        (tmp_path / "platforms" / "kuaishou" / f"queue.db{suffix}").unlink(missing_ok=True)
    assert SocialStore(str(tmp_path), "kuaishou").list() == [] and first.path


def test_one_failed_login_of_the_owner_counts_once(tmp_path):
    db = SocialStore(str(tmp_path), "kuaishou")
    login = db.create("LOGIN", "42")
    db.update(login["id"], status="FAILED", error=IDENTITY_FAILURE + "：登录未完成")
    session = manager.session_of(db, "42")
    assert session["failures"] == 1 and session["state"] == "valid"
    assert manager.session_of(db, "42", jobs=db.list(uid="42")) == session


def test_a_login_queued_before_its_uid_was_known_still_counts(tmp_path):
    db = SocialStore(str(tmp_path), "kuaishou")
    login = db.create("LOGIN", "")
    db.update(login["id"], status="DONE", result={"uid": "42"})
    assert manager.session_of(db, "42")["state"] == "valid"
