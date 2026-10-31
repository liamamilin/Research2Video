"""Native automatic writing and rendering share one allowance across matrices."""

import copy
import time

import yaml

from core.video import autopilot_store as batches
from core.video import store
from core.video.production_admission import WAIT_NOTE, Ledger
from tests.test_video_scripts import video_env  # noqa: F401
from web.services import video_autopilot, video_executor, video_render


def test_native_automatic_entries_share_budget_and_keep_deferred_work(
    video_env, monkeypatch
):
    root = video_env
    (root / "config/matrices").mkdir(exist_ok=True)
    for name in ("first", "second"):
        (root / f"config/matrices/{name}.yaml").write_text(
            f"name: {name}\njobs: [{name}/radar]\n"
        )
    (root / "config/production_policy.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 1,
                "matrices": ["first", "second"],
                "window": {"mode": "previous_slot"},
                "automatic": {"daily_limit": 2},
            }
        )
    )
    ledger = Ledger(str(root / "state"), now=time.time() - 5)
    created, rendered = [], []

    def create(column, topic, started_by):
        script = store.create_script(column, topic, created_by=started_by)
        created.append(script["id"])
        return script

    monkeypatch.setattr(video_executor, "create", create)
    monkeypatch.setattr(
        video_render,
        "create",
        lambda script: rendered.append(script["id"]) or {"id": "isolated-render"},
    )
    monkeypatch.setattr(video_render, "start", lambda _: None)

    def topic(owner, number):
        return {
            "title": f"Programme {number}",
            "id": f"item-{number}",
            "content_mode": "episode",
            "fields": {},
            "from_report": f"output/{number}.md",
            "episode": {
                "job": f"{owner}/radar",
                "report_path": f"output/{number}.md",
                "report_identity": f"revision-{number}",
            },
        }

    first = batches.create_run(
        {},
        [{"column_key": "flash", "topic": topic("first", 1)}],
        started_by="autopilot",
    )
    assert video_autopilot.advance(
        first["items"][0], can_start_script=True, can_start_render=True
    )["script"]
    assert len(created) == 1

    # A different automatic producer reaches the common executor with an already approved script.
    second = store.create_script("flash", topic("second", 2), created_by="autopilot")
    store.update_script(second["id"], status="APPROVED", approved_version=1)
    second = store.get_script(second["id"])
    out = {"took": {"script": False, "render": False}, "item": {}, "hold": None}
    video_executor._render_auto(second, True, out)
    assert out["took"]["render"] and rendered == [second["id"]]

    deferred = batches.create_run(
        {},
        [{"column_key": "flash", "topic": topic("first", 3)}],
        started_by="autopilot",
    )
    original = deferred["items"][0]
    original_topic = copy.deepcopy(original["topic"])
    assert not any(
        video_autopilot.advance(
            original, can_start_script=True, can_start_render=True
        ).values()
    )
    saved = batches.get_run(deferred["id"])["items"][0]
    assert saved["state"] == "PENDING" and saved["script_id"] is None
    assert saved["topic"] == original_topic and saved["note"] == WAIT_NOTE
    assert len(created) == 1

    # An explicit user batch remains available after the automatic allowance is exhausted.
    manual = batches.create_run(
        {}, [{"column_key": "flash", "topic": topic("second", 4)}], started_by="admin"
    )
    assert video_autopilot.advance(
        manual["items"][0], can_start_script=True, can_start_render=True
    )["script"]
    with ledger.connect() as db:
        assert (
            db.execute(
                "SELECT COUNT(*) FROM admissions WHERE mode='automatic'"
            ).fetchone()[0]
            == 2
        )
        assert (
            db.execute(
                "SELECT COUNT(*) FROM admissions WHERE mode='manual'"
            ).fetchone()[0]
            == 1
        )
