"""A render waits for the hand-opened Draw Things app instead of failing (web/services/video_render._submit)."""
import pytest

from core.video.autovideo import AutoVideoError
from web.services import video_render, video_storage, video_visuals


class Client:
    def __init__(self, refusals):
        self.refusals, self.calls = refusals, 0

    def create_job(self, payload):
        self.calls += 1
        if self.calls <= self.refusals:
            raise AutoVideoError(503, "DRAW_THINGS_NOT_READY", "Draw Things 本地 HTTP API 尚未连接")
        return {"job_id": "job-1", "status": "QUEUED"}


@pytest.fixture
def harness(monkeypatch):
    updates, failures, sleeps = [], [], []
    monkeypatch.setattr(video_storage, "unavailable_reason", lambda: "")
    monkeypatch.setattr(video_render, "offline_mounts", lambda: [])
    monkeypatch.setattr(video_visuals, "submission", lambda render: {"input_mode": "narration", "chapters": []})
    monkeypatch.setattr(video_render.store, "update_render", lambda rid, **kw: updates.append(kw))
    monkeypatch.setattr(video_render, "_fail", lambda rid, code, msg: failures.append((code, msg)) or "FAILED")
    monkeypatch.setattr(video_render, "_timing", lambda rid, **kw: None)
    monkeypatch.setattr(video_render, "_sleep", lambda s: sleeps.append(s))
    return updates, failures, sleeps


RENDER = {"id": "r1", "source_text": "", "request": {}}


def test_waits_while_draw_things_is_closed_then_submits(harness):
    updates, failures, sleeps = harness
    client = Client(refusals=3)
    assert video_render._submit(RENDER, client) == "job-1"
    assert client.calls == 4 and sleeps == [video_render.DRAW_THINGS_RETRY_SECONDS] * 3
    assert {"active_stage": video_render.WAITING_DRAW_THINGS} in updates
    assert updates[-1]["autovideo_job_id"] == "job-1" and not failures


def test_gives_up_with_a_reason_after_the_wait_limit(harness, monkeypatch):
    updates, failures, sleeps = harness
    monkeypatch.setattr(video_render, "DRAW_THINGS_WAIT_SECONDS", 900)
    assert video_render._submit(RENDER, Client(refusals=99)) is None
    assert len(sleeps) == 3 and failures[0][0] == "DRAW_THINGS_NOT_READY" and "Draw Things" in failures[0][1]


def test_other_refusals_still_fail_at_once(harness):
    updates, failures, sleeps = harness

    class Refuses(Client):
        def create_job(self, payload):
            raise AutoVideoError(422, "INVALID_REQUEST", "bad")

    assert video_render._submit(RENDER, Refuses(0)) is None
    assert failures == [("INVALID_REQUEST", "bad")] and not sleeps
