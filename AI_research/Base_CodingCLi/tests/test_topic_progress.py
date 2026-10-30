"""Topic cards distinguish current-version videos, older output and failed work."""
import pytest

from core.video import store, topic_store
from tests.test_video_scripts import _login, video_env  # noqa: F401
from web.services import topic_progress, video_render


def script_for(topic, column="flash"):
    script = store.create_script(column, {"id": topic.id, "title": topic.title})
    add_version(script)
    return script


def add_version(script):
    return store.add_version(script["id"], title="当前稿", sections=[], length_check={}, edit_type="EDITED")


def render_for(script, version=1, status="COMPLETED", file=True):
    render = store.create_render(script["id"], version, title="片", source_text="不应读取的冻结讲稿",
                                 source_sha256="0" * 64, request={"private": "不要进入选题接口"})
    store.update_render(render["id"], status=status, artifacts=[{"name": "video.mp4"}])
    if file:
        folder = video_render.files_dir(render["id"])
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "video.mp4").write_bytes(b"local-video")
    return render


def test_topic_api_uses_current_version_and_keeps_old_video_separate(client, video_env):
    topic = topic_store.add_manual("当前版本和旧版分开", {})
    script = script_for(topic)
    old = render_for(script)
    add_version(script)
    store.update_script(script["id"], status="DRAFT")
    _login(client)
    body = client.get("/api/video/topics?source=manual").json()["topics"][0]
    row = body["scripts"][0]
    assert row["version"] == 2 and row["status"] == "DRAFT"
    assert row["render_state"] == "none" and row["video_url"] is None
    assert row["previous_video"] == {"version": 1, "url": f"/api/video/renders/{old['id']}/files/video.mp4"}
    assert "source_text" not in str(body) and "private" not in str(body)
    current = render_for(script, 2)
    render_for(script, 2, "FAILED", file=False)
    row = topic_progress.scripts_by_topic([topic.id])[topic.id][0]
    assert row["render_state"] == "done"  # A failed retry does not erase a good current video.
    assert current["id"] in row["video_url"]
    assert client.get(row["video_url"]).content == b"local-video"
    render_for(script, 2, "RUNNING", file=False)
    row = topic_progress.scripts_by_topic([topic.id])[topic.id][0]
    assert row["render_state"] == "done" and row["render"]["status"] == "RUNNING"
    assert current["id"] in row["video_url"]


@pytest.mark.parametrize("status,expected", [("QUEUED", "working"), ("RUNNING", "working"),
                                            ("DOWNLOADING", "working"), ("FAILED", "failed"),
                                            ("CANCELLED", "failed"), ("COMPLETED", "failed")])
def test_no_missing_video_is_offered_as_a_completed_video(video_env, status, expected):
    topic = topic_store.add_manual("等待或者失败的制作", {})
    script = script_for(topic)
    render_for(script, status=status, file=False)
    row = topic_progress.scripts_by_topic([topic.id])[topic.id][0]
    assert row["render_state"] == expected and row["video_url"] is None


def test_failed_drafts_roundups_columns_and_archive_remain_visible_without_changing_selection(video_env):
    one = topic_store.add_manual("盘点里面的第一条", {})
    two = topic_store.add_manual("盘点里面的第二条", {})
    unrelated = topic_store.add_manual("其他无关的选题内容", {})
    failed = script_for(one)
    store.update_script(failed["id"], status="FAILED")
    roundup = store.create_script("roundup", {"items": [one.id, two.id], "title": "两条合成一期"})
    add_version(roundup)
    store.update_script(roundup["id"], archived=1)
    render_for(roundup)
    script_for(unrelated)
    covered = topic_progress.scripts_by_topic([one.id, two.id])
    assert {s["id"] for s in covered[one.id]} == {failed["id"], roundup["id"]}
    assert [s["id"] for s in covered[two.id]] == [roundup["id"]]
    # A writing failure never submitted for rendering is still "not rendered".
    assert next(s for s in covered[one.id] if s["id"] == failed["id"])["render_state"] == "none"
    assert covered[two.id][0]["archived"] == 1 and covered[two.id][0]["render_state"] == "done"
    assert failed["id"] not in {s["id"] for s in store.scripts_by_topic()[one.id]}
    assert topic_progress.scripts_by_topic([]) == {}


def test_no_research_and_duration_variants_still_belong_to_their_original_cell(video_env):
    topic = topic_store.add_manual("直接使用已有报告写稿", {})
    script = script_for(topic, "flash-m2-nr")
    render_for(script)
    row = topic_progress.scripts_by_topic([topic.id])[topic.id][0]
    assert row["column_key"] == "flash-m2-nr" and row["cell_key"] == "flash"
    assert row["render_state"] == "done"


def test_duplicate_archive_links_original_current_video_without_mutating_records(client, video_env):
    from core.video import content_guard

    original_topic = topic_store.add_manual("先前已完成的主事件", {})
    original = script_for(original_topic)
    video = render_for(original)
    new_topic = topic_store.add_manual("同一主事件的新标题", {})
    duplicate = store.create_script("flash-nr", {"id": new_topic.id, "title": new_topic.title})
    assert content_guard.archive_duplicate(duplicate["id"], "主事件已经讲过", [original["id"]])
    store.update_script(duplicate["id"], status="FAILED")
    before = store.get_script(duplicate["id"])
    row = topic_progress.scripts_by_topic([new_topic.id])[new_topic.id][0]
    assert row["status"] == "FAILED" and row["render_state"] == "none"
    skip = row["selection_skip"]
    assert skip["kind"] == "duplicate" and skip["reason"] == "主事件已经讲过"
    assert skip["originals"][0]["id"] == original["id"]
    assert skip["originals"][0]["title"] == "当前稿"
    assert skip["originals"][0]["video_url"] == f"/api/video/renders/{video['id']}/files/video.mp4"
    assert "source_text" not in str(skip) and "private" not in str(skip)
    _login(client)
    assert client.get(f"/api/video/scripts/{duplicate['id']}").json()["selection_skip"] == skip
    assert store.get_script(duplicate["id"]) == before
    # File loss never offers a broken playback button, and manual restore removes the skip state.
    (video_render.files_dir(video['id']) / 'video.mp4').unlink()
    assert topic_progress.scripts_by_topic([new_topic.id])[new_topic.id][0]["selection_skip"]["originals"][0]["video_url"] is None
    store.update_script(duplicate["id"], archived=0)
    assert "selection_skip" not in topic_progress.scripts_by_topic([new_topic.id])[new_topic.id][0]


def test_exhausted_or_missing_originals_are_not_presented_as_render_failures(video_env):
    from core.video import content_guard

    topic = topic_store.add_manual("候选不足", {})
    row = store.create_script("flash", {"id": topic.id, "title": topic.title})
    assert content_guard.archive_duplicate(row["id"], "素材不足", [])
    store.update_script(row["id"], status="FAILED")
    assert topic_progress.scripts_by_topic([topic.id])[topic.id][0]["selection_skip"] == {
        "kind": "exhausted", "reason": "素材不足", "originals": []}
    assert content_guard.archive_duplicate(row["id"], "先前已讲过", ["missing"])
    skip = topic_progress.scripts_by_topic([topic.id])[topic.id][0]["selection_skip"]
    assert skip["kind"] == "duplicate" and skip["originals"] == []


def test_manual_topic_can_start_a_different_video_type_without_touching_existing_work(client, video_env, monkeypatch):
    from web.services import video_workflow
    from tests.test_video_scripts import _csrf

    topic = topic_store.add_manual("同一题目用于另一个视频类型", {})
    original = script_for(topic, "flash")
    store.update_script(original["id"], status="WRITING")
    before = store.get_script(original["id"])
    spawned = []
    monkeypatch.setattr(video_workflow, "spawn", lambda script_id, started_by="": spawned.append(script_id))
    _login(client)
    response = client.post("/api/video/scripts", headers=_csrf(client), json={"column": "point_short", "topic_id": topic.id})
    assert response.status_code == 202, response.text
    made = response.json()
    assert made["column_key"] == "point_short" and made["topic"]["id"] == topic.id
    assert spawned == [made["id"]]
    assert store.get_script(original["id"]) == before
