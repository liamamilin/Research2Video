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
    assert report["topic_id"] == "2026-10-19/F1" and report["research"] == "quick_take"

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


WEEKLY_REPORT = """# 医疗健康一周快讯（2026-10-14 至 2026-10-20）

## 1. 条目

### 1.1 某新药获批上市
- **一句话**：示例
"""


def _topic_rows() -> int:
    with store.connect() as conn:
        return conn.execute("SELECT count(*) FROM topics").fetchone()[0]


def test_api_any_engine_report_is_written_into_a_video_without_a_topic(client, video_env, research, llm, autovideo):
    """Product redesign phase 1: a report from any Job becomes a video directly; nothing goes into the topic pool."""
    base = video_env / "output" / "monitoring"
    base.mkdir(parents=True, exist_ok=True)
    (base / "2026-10-20_medical.md").write_text(WEEKLY_REPORT, encoding="utf-8")
    budget = length.budget_for(video_columns.get(str(ROOT / "config"), "point_short").target_minutes, "voxcpm_anchor")
    llm.replies = [script_json(budget - 5, citations={(0, 0): [GOOD]}) for _ in range(2)]
    _login(client)

    recorded = client.post("/api/video/reports", json={"path": "monitoring/2026-10-20_medical.md"}, headers=_csrf(client))
    assert recorded.status_code == 200, recorded.text
    report = recorded.json()
    assert report["report_path"] == "output/monitoring/2026-10-20_medical.md" and report["kind"] == "other"
    assert report["title"] == "医疗健康一周快讯（2026-10-14 至 2026-10-20）" and report["topic_id"] == ""
    again = client.post("/api/video/reports", json={"path": "monitoring/2026-10-20_medical.md"}, headers=_csrf(client))
    assert again.json()["id"] == report["id"]  # recorded once

    pool = _topic_rows()
    made = client.post("/api/video/scripts", headers=_csrf(client), json={"column": "point_short", "report_id": report["id"]})
    assert made.status_code == 202, made.text
    script = _wait(client, made.json()["id"], {"DRAFT", "FAILED"})
    assert script["status"] == "DRAFT", script.get("error")
    assert script["topic"]["id"] is None and script["topic"]["title"] == report["title"]
    assert script["report_id"] == report["id"] and script["report_path"] == report["report_path"]

    typed = client.post("/api/video/scripts", headers=_csrf(client), json={
        "column": "point_short", "report_id": report["id"], "topic": {"title": "新药审批这周变了什么", "audience": "普通读者"}})
    assert typed.status_code == 202, typed.text
    second = _wait(client, typed.json()["id"], {"DRAFT", "FAILED"})
    assert second["topic"]["title"] == "新药审批这周变了什么" and second["topic"]["fields"] == {"目标受众": "普通读者"}
    assert research.jobs == []  # written from the report: no research, no searches
    assert _topic_rows() == pool  # and no topic was made for either
    assert [s["id"] for s in client.get(f"/api/video/reports/{report['id']}").json()["scripts"]] == [script["id"], second["id"]]

    short = client.post("/api/video/scripts", headers=_csrf(client),
                        json={"column": "point_short", "report_id": report["id"], "topic": {"title": "短"}})
    assert short.status_code == 422


def test_api_recording_a_report_only_takes_markdown_inside_output(client, video_env):
    (video_env / "output").mkdir(exist_ok=True)
    (video_env / "output" / "notes.json").write_text("{}", encoding="utf-8")
    _login(client)
    post = lambda path: client.post("/api/video/reports", json={"path": path}, headers=_csrf(client)).status_code  # noqa: E731
    assert post("../config/system.yaml") == 400
    assert post("notes.json") == 400
    assert post("missing.md") == 404
    client.post("/api/auth/logout", headers=_csrf(client))
    assert post("missing.md") in (401, 403)
