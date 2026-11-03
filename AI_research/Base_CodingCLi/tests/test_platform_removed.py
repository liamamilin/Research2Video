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


def archive_list(*bvids, complete=True, page=None):
    """The creator list searched by title, and the video page; ``page`` is the view API's answer code (None: visible)."""
    from web.services import bilibili

    class Client:
        searched = []

        def archives(self, max_pages=3, *, keyword=""):
            Client.searched.append(keyword)
            return {"archives": [{"bvid": b} for b in bvids], "complete": complete}

        def owned_video(self, bvid, uid):
            if page is not None:
                raise bilibili.BilibiliRejected("gone", code=page)
            return {"bvid": bvid}
    return lambda uid="": Client()


def test_a_deleted_submission_is_marked_removed_and_frees_its_slot(task, monkeypatch):
    db, task_id = task
    monkeypatch.setattr(pm, "client", archive_list("BV1other", page=-404))
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


@pytest.mark.parametrize("client_args,kwargs", [
    ((), {"page": None}),             # gone from the search but the video page still shows it (title edited)
    ((), {"page": -404, "complete": False}),  # too many same-title submissions to read them all
    ((), {"page": -352}),             # an unrelated rejection is not an answer that it is gone
])
def test_an_old_or_renamed_video_is_not_marked_without_proof(task, monkeypatch, client_args, kwargs):
    db, task_id = task
    monkeypatch.setattr(pm, "client", archive_list(*client_args, **kwargs))
    with pytest.raises(pm.Error):
        pm.mark_removed(task_id)
    assert db.get_upload(task_id)["status"] == "SUBMITTED"


def test_marking_searches_the_submission_by_its_title_not_only_recent_pages(task, monkeypatch):
    _, task_id = task
    client = archive_list(page=-404)
    monkeypatch.setattr(pm, "client", client)
    pm.mark_removed(task_id)
    assert client().searched == ["t"]


def test_a_removed_submission_can_be_uploaded_again_as_a_manual_upload(task, monkeypatch):
    db, task_id = task
    monkeypatch.setattr(pm, "client", archive_list(page=-404))
    pm.mark_removed(task_id)
    monkeypatch.setattr(pm, "eligible", lambda render_id: None)
    row = pm.reupload(task_id)
    assert row["status"] == "QUEUED" and row["bvid"] == "" and row["error"] == ""
    assert "dtime" not in row["payload"] and row["payload"]["automatic"] is False
    assert row["payload"]["removed_bvids"] == ["BV1gone"]
    with pytest.raises(pm.Error):
        pm.reupload(task_id)  # already back in the queue
