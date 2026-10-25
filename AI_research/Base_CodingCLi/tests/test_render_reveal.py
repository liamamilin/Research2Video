"""Showing a finished video's folder, so it can be uploaded by hand."""

from __future__ import annotations

import subprocess
import sys

import pytest

from tests.test_video_scripts import _csrf, _login, video_env  # noqa: F401
from web.services import video_render


@pytest.fixture
def render(tmp_path, monkeypatch):
    monkeypatch.setattr(video_render, "files_dir", lambda render_id: tmp_path / render_id)
    (tmp_path / "r1").mkdir()
    (tmp_path / "r1" / "video.mp4").write_bytes(b"x")
    return {"id": "r1", "artifacts": [{"name": "video.mp4"}, {"name": "cover.png"}]}


def test_the_video_is_shown_in_finder_on_a_mac_and_only_its_path_elsewhere(render, monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: calls.append(cmd) or subprocess.CompletedProcess(cmd, 0))
    monkeypatch.setattr(sys, "platform", "darwin")
    shown = video_render.reveal(render)
    assert calls == [["open", "-R", str(tmp_path / "r1" / "video.mp4")]]
    assert shown == {"path": str(tmp_path / "r1" / "video.mp4"), "folder": str(tmp_path / "r1"), "opened": True}
    monkeypatch.setattr(sys, "platform", "linux")
    assert video_render.reveal(render)["opened"] is False and len(calls) == 1


def test_a_failed_open_still_gives_the_path(render, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 1))
    assert video_render.reveal(render)["opened"] is False
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: (_ for _ in ()).throw(OSError("no open")))
    assert video_render.reveal(render)["opened"] is False


@pytest.mark.parametrize("name", ["../video.mp4", "other.mp4", "cover.png"])  # not recorded, outside, recorded but not downloaded
def test_only_a_recorded_downloaded_file_can_be_shown(render, name):
    with pytest.raises(FileNotFoundError):
        video_render.reveal(render, name)


def test_api_needs_a_known_render_and_an_editor(client, video_env):  # noqa: F811
    _login(client)
    assert client.post("/api/video/renders/nope/reveal", headers=_csrf(client)).status_code == 404
    client.cookies.clear()
    _login(client, "viewer", "viewer-pass-123")
    assert client.post("/api/video/renders/nope/reveal", headers=_csrf(client)).status_code == 403
