"""产出: each script traced to matrix › Job › the run's report › production config (spec/outputs.md)."""

from __future__ import annotations

from core.video import outputs, reports, store, topic_store
from tests.test_production import prod_env  # noqa: F401
from tests.test_video_scripts import _login, video_env  # noqa: F401

FLASH = "monitoring/ai_flash_discovery"
REPORT = "output/flash/2026-10-19_ai_flash_discovery.md"


def _collect(env, archived=False):
    return outputs.collect(str(env / "config"), str(env / "jobs"), archived=archived)


def _by_id(data):
    return {r["id"]: r for r in data["scripts"]}


def test_each_kind_of_script_is_traced_to_the_report_it_came_from(prod_env):
    topic_store.sync(str(prod_env / "jobs"), str(prod_env / "output"))
    one = store.create_script("flash", {"id": "2026-10-19/F1", "title": "x"})
    roundup = store.create_script("ai-developer-roundup-long", {"title": "盘点", "items": ["2026-10-19/F1", "2026-10-19/F2"]})
    recorded = store.create_script("point_short", {"id": "2026-10-19/F2", "title": "x"})
    store.update_script(recorded["id"], production="point_short")
    cut = store.create_script("point_short", {"title": "切一条", "parent_script": one["id"]})
    manual = topic_store.add_manual("自己写的一个选题", {})
    typed = store.create_script("flash", {"id": manual.id, "title": manual.title})
    with store.connect(write=True) as conn:
        report_id = reports._record(conn, "output/x/2026-10-01.md", job="research/loose")
    whole = store.create_script("deep_dive", {"title": "整份", "report_id": report_id})

    rows = _by_id(_collect(prod_env))
    for script in (one, roundup, recorded, cut):
        row = rows[script["id"]]
        assert (row["matrix"], row["job"], row["report_path"], row["report_date"]) == ("ai", FLASH, REPORT, "2026-10-19")
    # made before configs: the config named after its recipe on the same Job; a recorded one is not a guess
    assert (rows[one["id"]]["production"], rows[one["id"]]["production_guessed"]) == ("flash", True)
    assert (rows[recorded["id"]]["production"], rows[recorded["id"]]["production_guessed"]) == ("point_short", False)
    assert rows[roundup["id"]]["production"] == "ai-developer-roundup-long"
    assert rows[typed["id"]]["manual"] and rows[typed["id"]]["job"] == "" and rows[typed["id"]]["production"] == ""
    # a whole report written from the reports page: its Job, and no config (deep_dive's config is on another Job)
    assert (rows[whole["id"]]["job"], rows[whole["id"]]["matrix"], rows[whole["id"]]["production"]) == (
        "research/loose", "ai", "")


def test_a_report_no_script_came_from_is_counted_per_job_archived_scripts_included(prod_env):
    (prod_env / "output" / "flash" / "2026-10-20_ai_flash_discovery.md").write_text(
        (prod_env / "output" / "flash" / "2026-10-19_ai_flash_discovery.md").read_text(encoding="utf-8"), encoding="utf-8")
    topic_store.sync(str(prod_env / "jobs"), str(prod_env / "output"))
    flash = lambda data: next(j for j in data["jobs"] if j["job"] == FLASH)  # noqa: E731
    assert (flash(_collect(prod_env))["reports"], flash(_collect(prod_env))["unwritten"]) == (2, 2)
    script = store.create_script("flash", {"id": "2026-10-19/F1", "title": "x"})
    store.update_script(script["id"], archived=1)
    assert _collect(prod_env)["scripts"] == [] and [r["id"] for r in _collect(prod_env, True)["scripts"]] == [script["id"]]
    assert flash(_collect(prod_env))["unwritten"] == 1  # the 10-19 run was made into a video, even if archived since


def test_api_lists_outputs_for_viewers(client, prod_env):
    _login(client, "viewer", "viewer-pass-123")
    body = client.get("/api/outputs").json()
    assert body["scripts"] == [] and {"key": "ai", "name": "AI 矩阵"} in body["matrices"]


def test_config_counts_leave_out_failed_scripts_and_count_older_ones(prod_env):
    topic_store.sync(str(prod_env / "jobs"), str(prod_env / "output"))
    for status in ("DRAFT", "FAILED"):
        script = store.create_script("flash", {"id": "2026-10-19/F1", "title": "x"})
        store.update_script(script["id"], status=status)
    assert outputs.count_by_config(str(prod_env / "config"), str(prod_env / "jobs")) == {"flash": 1}
