import threading

import httpx

from core.video import store
from core.video.autovideo import AutoVideoClient
from tests.test_video_artifact_recovery import artifact
from tests.test_video_scripts import video_env, autovideo, _approved_script  # noqa: F401
from web.services import video_render


def test_render_http_session_uses_one_pool_and_closes_it_on_failure(monkeypatch):
    original = httpx.Client
    created = []
    def client(*args, **kwargs):
        value = original(*args, **kwargs)
        created.append(value)
        return value
    monkeypatch.setattr(httpx, "Client", client)
    service = AutoVideoClient("http://local.test", transport=httpx.MockTransport(lambda _: httpx.Response(200, json={"status": "ok"})))
    try:
        with service.session():
            service.health()
            with service.session():
                service.health()
            raise RuntimeError("interrupted")
    except RuntimeError:
        pass
    assert len(created) == 1 and created[0].is_closed
    assert service._session_client is None


def test_downloads_overlap_with_bounded_workers_and_keep_integrity(video_env, autovideo, monkeypatch):
    render = video_render.create(_approved_script())
    client = autovideo.client()
    original = client.download
    entered = threading.Barrier(2)
    def overlapping(item, dest):
        entered.wait(timeout=3)
        return original(item, dest)
    monkeypatch.setattr(client, "download", overlapping)
    assert video_render.process(render["id"], client=client) == "COMPLETED"
    timings = store.get_render(render["id"])["timings"]
    assert timings["download_workers"] == 4 and timings["download_attempts"] == 1
    assert len(timings["artifacts"]) == 2
    assert all(row["reused"] is False for row in timings["artifacts"])


def test_recovery_probes_recent_heartbeat_but_still_uses_the_process_lock(video_env, monkeypatch):
    render = video_render.create(_approved_script())
    store.touch_render(render["id"])
    starts = []
    monkeypatch.setattr(video_render, "start", starts.append)
    assert store.orphaned_render_ids() == []
    video_render.resume_active()
    assert starts == [render["id"]]


def test_recovery_does_not_report_a_local_follower_as_resumed(video_env, monkeypatch):
    render = video_render.create(_approved_script())
    monkeypatch.setattr(video_render, "_running", {render["id"]})
    monkeypatch.setattr(video_render, "start", lambda _: (_ for _ in ()).throw(AssertionError("already followed")))
    assert video_render.resume_active() == 0


def test_wall_time_includes_recovery_wait_and_keeps_phase_measurements():
    result = video_render.render_timings({"created_at": "2026-10-23T17:00:00+0800",
        "completed_at": "2026-10-23T17:02:01+0800", "timings": {"download_seconds": .1}})
    assert result == {"elapsed_seconds": 121, "download_seconds": .1}


def test_duplicate_artifact_names_fail_without_racing_writers(video_env, autovideo, monkeypatch):
    render = video_render.create(_approved_script())
    client = autovideo.client()
    row = artifact(b"video")
    monkeypatch.setattr(client, "list_artifacts", lambda _: [row, row])
    monkeypatch.setattr(client, "download", lambda *args: (_ for _ in ()).throw(AssertionError("must not copy")))
    assert video_render.process(render["id"], client=client) == "FAILED"
    assert "重复文件名" in store.get_render(render["id"])["error_message"]
