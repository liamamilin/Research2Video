"""A submission the creator deleted on B站 stops waiting and can never be re-uploaded by a retry."""
import pytest

from web.services import platform_manager as pm, publication_state


@pytest.fixture
def task(tmp_path, monkeypatch):
    from core.platform_store import PlatformStore
    db = PlatformStore(str(tmp_path / "platforms.db"))
    monkeypatch.setattr(pm, "store", lambda: db)
    monkeypatch.setattr(pm, "uid", lambda: "u1")
    render = {"id": "r1", "script_id": "s1", "version": 1}
    row = db.enqueue("u1", render, {"title": "t", "tags": ["a"], "description": "", "tid": 1, "dtime": 1793688000})
    db.update_upload(row["id"], status="SUBMITTED", bvid="BV1gone", expected="QUEUED")
    return db, row["id"]


def archive_list(*bvids):
    class Client:
        def archives(self, max_pages=3):
            return {"archives": [{"Archive": {"bvid": b}} for b in bvids]}
    return lambda uid="": Client()


def test_a_deleted_submission_is_marked_removed_and_frees_its_slot(task, monkeypatch):
    db, task_id = task
    monkeypatch.setattr(pm, "client", archive_list("BV1other"))
    row = pm.mark_removed(task_id)
    assert row["status"] == "REMOVED" and row["bvid"] == "BV1gone"
    assert db.pending_uploads("u1") == []  # no longer holds a publish slot or waits for its first comment
    assert publication_state.resume_action("bilibili", row) is None  # no retry that would upload it again
    assert publication_state.describe("bilibili", task_id, row)["label"] == "已在平台删除"


def test_a_submission_still_on_bilibili_is_not_marked(task, monkeypatch):
    db, task_id = task
    monkeypatch.setattr(pm, "client", archive_list("BV1gone"))
    with pytest.raises(pm.Error):
        pm.mark_removed(task_id)
    assert db.get_upload(task_id)["status"] == "SUBMITTED"
