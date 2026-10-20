"""Research reports as records: one row per report file, any number of scripts written from it (spec/video-reports.md)."""

from __future__ import annotations

import json

from core.video import length, reports, store
from core.video import columns as video_columns
from tests.test_video_scripts import (FLASH_REPORT, GOOD, ROOT, _csrf, _login, _wait, autovideo, llm,  # noqa: F401
                                      research, script_json, video_env)


def _flash(video_env):
    base = video_env / "output" / "flash"
    base.mkdir(parents=True, exist_ok=True)
    (base / "2026-10-19_ai_flash_discovery.md").write_text(FLASH_REPORT, encoding="utf-8")


def test_api_one_research_three_scripts_and_the_report_lists_them(client, video_env, research, llm, autovideo):
    _flash(video_env)
    budgets = [length.budget_for(video_columns.get(str(ROOT / "config"), key).target_minutes, "voxcpm_anchor")
               for key in ("flash", "point_short", "point_short")]
    llm.replies = [script_json(budget - 5, citations={(0, 0): [GOOD]}) for budget in budgets]
    _login(client)

    made = client.post("/api/video/scripts", json={"column": "flash", "topic_id": "2026-10-19/F1"}, headers=_csrf(client))
    first = _wait(client, made.json()["id"], {"DRAFT", "FAILED"})
    assert first["status"] == "DRAFT", first.get("error")
    assert first["report_id"]
    report = client.get(f"/api/video/reports/{first['report_id']}").json()
    assert report["kind"] == "video_research" and report["job"] == research.jobs[0]
    assert report["topic_id"] == "2026-10-19/F1" and report["research"] == "quick_take_developer"

    # another recipe from the same research, the old way (from_script) and the new way (report_id, topic defaulted)
    cut = client.post("/api/video/scripts", headers=_csrf(client), json={
        "column": "point_short", "from_script": first["id"], "topic": {"title": "每次运行成本怎么看", "angle": "x"}})
    second = _wait(client, cut.json()["id"], {"DRAFT", "FAILED"})
    again = client.post("/api/video/scripts", headers=_csrf(client),
                        json={"column": "point_short", "report_id": first["report_id"]})
    assert again.status_code == 202, again.text
    third = _wait(client, again.json()["id"], {"DRAFT", "FAILED"})
    assert third["status"] == "DRAFT", third.get("error")
    assert len(research.jobs) == 1  # one research for all three
    assert first["report_id"] == second["report_id"] == third["report_id"]
    assert third["topic"]["id"] == "2026-10-19/F1" and third["report_path"] == first["report_path"]

    listed = client.get("/api/video/reports").json()
    assert len(listed) == 1 and listed[0]["on_disk"] is True
    assert [s["id"] for s in listed[0]["scripts"]] == [first["id"], second["id"], third["id"]]

    assert client.post("/api/video/scripts", headers=_csrf(client),
                       json={"column": "point_short", "report_id": "nope"}).status_code == 422
    assert client.post("/api/video/scripts", headers=_csrf(client), json={
        "column": "point_short", "report_id": first["report_id"], "from_script": first["id"]}).status_code == 422
    assert client.get("/api/video/reports/nope").status_code == 404


def test_scripts_from_before_are_recorded_once_per_report_file(video_env):
    """The backfill: what real data looked like — roundups sharing a discovery report, a point cut from a deep dive."""
    from web.services import video_autopilot as pilot

    _flash(video_env)
    pilot.sync_topics()  # the discovery report is a topic source's
    deep = store.create_script("deep_dive", {"id": "2026-09-29/C1", "title": "长上下文"})
    store.update_script(deep["id"], report_path="output/video/x_deep_dive.md", research_job="video/deep_dive_abc")
    cut = store.create_script("point_short", {"title": "一点", "from_report": "output/video/x_deep_dive.md"})
    store.update_script(cut["id"], report_path="output/video/x_deep_dive.md")
    rounds = []
    for n in range(3):
        r = store.create_script("ai-developer-roundup-long", {"title": f"盘点{n}",
                                                              "from_report": "output/flash/2026-10-19_ai_flash_discovery.md"})
        store.update_script(r["id"], report_path="output/flash/2026-10-19_ai_flash_discovery.md")
        rounds.append(r["id"])
    store.create_script("flash", {"title": "还没调研"})
    with store.connect(write=True) as conn:
        conn.execute("UPDATE scripts SET report_id = NULL")
        conn.execute("DELETE FROM research_reports")
    store.init_db()
    store.init_db()  # twice: nothing is recorded twice

    rows = {r["report_path"]: r for r in reports.list_reports()}
    assert set(rows) == {"output/video/x_deep_dive.md", "output/flash/2026-10-19_ai_flash_discovery.md"}
    research_row, source_row = rows["output/video/x_deep_dive.md"], rows["output/flash/2026-10-19_ai_flash_discovery.md"]
    assert research_row["kind"] == "video_research" and research_row["job"] == "video/deep_dive_abc"
    assert research_row["topic_title"] == "长上下文"  # the deep dive's topic, not the cut's
    assert [s["id"] for s in research_row["scripts"]] == [deep["id"], cut["id"]]
    assert source_row["kind"] == "topic_source" and sorted(s["id"] for s in source_row["scripts"]) == sorted(rounds)
    with store.connect() as conn:
        assert conn.execute("SELECT count(*) FROM scripts WHERE report_path = '' AND report_id IS NOT NULL").fetchone()[0] == 0
    json.dumps(reports.list_reports())  # serialisable as the API returns it
