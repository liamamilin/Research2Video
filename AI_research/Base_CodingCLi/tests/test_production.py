"""Production configs: a recipe tied to a matrix Job, writing from that Job's reports (spec/production.md)."""

from __future__ import annotations

import dataclasses
import os
import shutil

import pytest
import yaml

from core.video import length, production, recipes, store
from core.video import columns as video_columns
from tests.test_video_flash import DISCOVERY
from tests.test_video_scripts import (GOOD, ROOT, FakeLLM, _csrf, _login, _wait, autovideo, llm,  # noqa: F401
                                      research, script_json, video_env)


SHIPPED_CONFIGS = {"ai-developer-roundup-long", "ai-non_tech-deep_dive-long", "ai-non_tech-one_point-short",
                   "ai-non_tech-quick_take-short", "deep_dive", "flash", "point_short"}


def test_each_recipe_but_comparison_is_one_config_on_the_job_it_picks_from():
    config, jobs = str(ROOT / "config"), str(ROOT / "jobs")
    # the configs the migration made, one per recipe; configs added since (ai_media_*, pai_*, ...) are not checked here
    every = {k: c for k, c in production.load_all(config, jobs).items() if k in SHIPPED_CONFIGS}
    shipped = {k: r for k, r in recipes.expanded(config).items() if r.column and r.column.topic_kind != "comparison"}
    shipped.pop("ai-non_tech-roundup-long")  # an audience added after the migration: no config on it yet
    for whole in ("report_digest", "top_finding", "action_list"):  # whole-report recipes: a config on a Job with no items
        shipped.pop(whole), shipped.pop({"report_digest": "ai-non_tech-digest-long", "top_finding": "ai-non_tech-headline-short",
                                         "action_list": "ai-non_tech-action_list-short"}[whole])
    shipped.pop("topic_digest")  # the other domains' whole-report recipe: configs added since
    shipped.pop("chatgpt_acquisition")  # the acquisition matrix was added after the migration
    shipped.pop("theme_research")  # generic manual themes do not have a feed production binding
    assert sorted(c.recipe for c in every.values()) == sorted(shipped)  # one each, so old scripts map 1:1 by recipe
    for c in every.values():
        column = shipped[c.recipe].column
        assert c.usable, (c.key, c.errors)
        assert (c.audience, c.target_minutes, c.reverify) == (column.audience, column.target_minutes,
                                                              column.research_mode == "job")
        assert c.source in column.topic_sources  # the Job it is on is the one its recipe already picked from
    assert every["flash"].job == "monitoring/ai_flash_discovery" and every["flash"].split == "each"
    assert every["ai-developer-roundup-long"].split == "some" and not every["ai-developer-roundup-long"].reverify
    assert every["deep_dive"].job == "practical_ai_intelligence/07_product_content_opportunities"


@pytest.fixture
def prod_env(video_env):
    (video_env / "config" / "matrices").mkdir(exist_ok=True)
    (video_env / "config" / "matrices" / "ai.yaml").write_text(
        "name: AI 矩阵\njobs: [monitoring/ai_flash_discovery, practical_ai_intelligence/07_product_content_opportunities,"
        " research/loose]\n", encoding="utf-8")
    shutil.copytree(ROOT / "config" / "production", video_env / "config" / "production")
    (video_env / "config" / "matrices" / "medical.yaml").write_text("name: 医疗\njobs: [medical/flash_discovery]\n",
                                                                     encoding="utf-8")
    (video_env / "jobs" / "medical").mkdir(exist_ok=True)
    (video_env / "jobs" / "medical" / "flash_discovery.yaml").write_text(
        "name: 医疗快讯\nprompt: y\noutput: output/medical/{date}.md\n"
        "topics:\n  alias: medical_flash\n  format: flash\n  prefix: MF\n", encoding="utf-8")
    (video_env / "jobs" / "research").mkdir(exist_ok=True)
    (video_env / "jobs" / "research" / "loose.yaml").write_text("name: x\nprompt: y\noutput: output/x/{date}.md\n",
                                                                 encoding="utf-8")
    (video_env / "jobs" / "research" / "outside.yaml").write_text(
        "name: x\nprompt: y\noutput: output/o/{date}.md\ntopics:\n  format: items\n  prefix: O\n", encoding="utf-8")
    base = video_env / "output" / "flash"
    base.mkdir(parents=True, exist_ok=True)
    (base / "2026-10-19_ai_flash_discovery.md").write_text(DISCOVERY, encoding="utf-8")
    return video_env


@pytest.mark.parametrize("data, message", [
    ({"job": "research/outside", "recipe": "flash"}, "不在任何矩阵里"),
    ({"job": "research/loose", "recipe": "flash"}, "不是条目列表"),
    ({"job": "monitoring/ai_flash_discovery", "recipe": "ai-developer-comparison-long"}, "对比"),
    ({"job": "monitoring/ai_flash_discovery", "recipe": "nope"}, "没有这个配方"),
    ({"job": "research/nope", "recipe": "flash"}, "没有这个 Job"),
    ({"job": "monitoring/ai_flash_discovery", "recipe": "flash", "speed": 3}, "不认识的字段"),
    ({"job": "monitoring/ai_flash_discovery", "recipe": "flash", "minutes": 3}, "时长不能改"),  # its text says 60–90 秒
    ({"job": "practical_ai_intelligence/07_product_content_opportunities", "recipe": "deep_dive", "minutes": 9},
     "只能在 4–8 分钟之间"),
    ({"job": "practical_ai_intelligence/07_product_content_opportunities", "recipe": "deep_dive", "minutes": 6.3},
     "半分钟一档"),
    ({"job": "practical_ai_intelligence/07_product_content_opportunities", "recipe": "deep_dive", "minutes": "八"},
     "分钟数"),
    ({"job": "monitoring/ai_flash_discovery", "recipe": "flash", "audience": "儿童"}, "flash 没有 儿童 这个受众（有：developer、non_tech）"),
    ({"job": "monitoring/ai_flash_discovery", "recipe": "ai-non_tech-quick_take-short", "audience": "non_tech"}, "没有这个配方"),  # a column, not a file
    ({"job": "medical/flash_discovery", "recipe": "flash"}, "先在配方的 topic.sources 里加上 medical_flash"),  # an AI recipe
])
def test_a_config_that_cannot_write_is_listed_with_why(prod_env, data, message):
    (prod_env / "config" / "production" / "bad.yaml").write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    bad = production.load_all(str(prod_env / "config"), str(prod_env / "jobs"))["bad"]
    assert not bad.usable and any(message in e for e in bad.errors), bad.errors


def _organize_event(monkeypatch):
    from core.video import story_pool

    def organize(report, packets, previous, config):
        return story_pool.validate([{"title": packets[0]["title"], "subject": packets[0]["title"],
            "action": "event", "summary": packets[0]["text"], "primary": [packets[0]["id"]]}],
            packets, previous), {"requests": 1}

    monkeypatch.setattr(story_pool, "organize", organize)


def test_api_config_and_direct_entries_share_event_memory(client, prod_env, research, llm, autovideo, monkeypatch):
    _organize_event(monkeypatch)
    budget = length.budget_for(video_columns.get(str(ROOT / "config"), "flash").target_minutes, "voxcpm_anchor")
    llm.replies = [script_json(budget - 5, citations={(0, 0): [GOOD]}) for _ in range(2)]
    _login(client)
    listed = client.get("/api/production", params={"job": "monitoring/ai_flash_discovery"}).json()
    assert {c["key"] for c in listed} >= {"flash", "point_short", "ai-developer-roundup-long"}
    assert all(c["job"] == "monitoring/ai_flash_discovery" for c in listed)

    direct = client.post("/api/video/scripts", json={"column": "flash", "topic_id": "2026-10-19/F1"}, headers=_csrf(client))
    first = _wait(client, direct.json()["id"], {"DRAFT", "FAILED"})
    made = client.post("/api/production/flash/write", json={"topic_ids": ["2026-10-19/F1"]}, headers=_csrf(client))
    assert made.status_code == 202, made.text
    second = _wait(client, made.json()[0]["id"], {"DRAFT", "FAILED"})
    assert first["status"] == "DRAFT" and second["status"] == "FAILED"
    assert first["id"] in second["topic"]["content_archive"]["duplicate_of"]
    assert second["current_version"] == 0

    # Both entries use the same research recipe; the repeated event never calls the body writer again.
    def prompt(job: str) -> str:
        return yaml.safe_load((prod_env / "jobs" / f"{job}.yaml").read_text(encoding="utf-8"))["prompt"]
    assert len(research.jobs) == 2 and prompt(research.jobs[0]) == prompt(research.jobs[1])
    assert len(FakeLLM.calls) == 1 and first["version_shown"]["meta"]["cell"]
    assert store.get_script(second["id"])["production"] == "flash" and store.get_script(first["id"])["production"] == ""

    pending = client.get("/api/production/flash/pending").json()
    assert pending["report_date"] == "2026-10-19"
    assert [i["written"] for i in pending["items"]][:2] == [True, False]  # F1 written with this recipe, F2 not yet
    assert {c["key"]: c["scripts"] for c in client.get("/api/production").json()}["flash"] == 1


def test_api_a_roundup_config_writes_the_picked_items_as_one_script_without_research(client, prod_env, research, llm, autovideo):
    budget = length.budget_for(video_columns.get(str(ROOT / "config"), "ai-developer-roundup-long").target_minutes, "voxcpm_anchor")
    llm.replies = [script_json(budget - 5, citations={(0, 0): [GOOD]})]
    _login(client)
    made = client.post("/api/production/ai-developer-roundup-long/write",
                       json={"topic_ids": ["2026-10-19/F1", "2026-10-19/F2"]}, headers=_csrf(client))
    assert made.status_code == 202, made.text
    assert len(made.json()) == 1 and research.jobs == []
    assert sorted(store.get_script(made.json()[0]["id"])["topic"]["items"]) == ["2026-10-19/F1", "2026-10-19/F2"]


def test_api_adding_and_removing_a_config(client, prod_env):
    _login(client)
    bad = client.post("/api/production", json={"job": "research/loose", "recipe": "flash"}, headers=_csrf(client))
    assert bad.status_code == 422 and "不是条目列表" in bad.json()["error"]["message"]
    assert not (prod_env / "config" / "production" / "flash-2.yaml").exists()  # nothing left behind
    added = client.post("/api/production", json={"job": "monitoring/ai_flash_discovery", "recipe": "flash",
                                                 "name": "快讯 · 第二套"}, headers=_csrf(client))
    assert added.status_code == 201 and added.json()["key"] == "flash-2" and added.json()["name"] == "快讯 · 第二套"
    wrong = client.post("/api/production/flash-2/write", json={"topic_ids": ["manual/1"]}, headers=_csrf(client))
    assert wrong.status_code == 422 and wrong.json()["error"]["code"] == "not_from_this_job"
    assert client.delete("/api/production/flash-2", headers=_csrf(client)).status_code == 200
    assert not os.path.exists(prod_env / "config" / "production" / "flash-2.yaml")


def test_a_config_picks_the_audience_and_is_the_column_that_audience_makes(prod_env):
    config, jobs = str(prod_env / "config"), str(prod_env / "jobs")
    (prod_env / "config" / "production" / "kids.yaml").write_text(
        "job: monitoring/ai_flash_discovery\nrecipe: flash\naudience: non_tech\n", encoding="utf-8")
    (prod_env / "config" / "production" / "devs.yaml").write_text(
        "job: monitoring/ai_flash_discovery\nrecipe: flash\naudience: developer\n", encoding="utf-8")
    every = production.load_all(config, jobs)
    assert (every["kids"].recipe, every["kids"].base_recipe, every["kids"].audience_key, every["kids"].audience) == (
        "ai-non_tech-quick_take-short", "flash", "non_tech", "普通用户")
    assert (every["devs"].recipe, every["devs"].audience) == ("flash", "开发者")  # the recipe's first audience is its own key
    assert every["kids"].usable and every["devs"].usable
    # the old way of naming the column directly still works, and says the same
    old = every["ai-non_tech-quick_take-short"]
    assert (old.recipe, old.base_recipe, old.audience_key) == (every["kids"].recipe, "flash", "non_tech")


def test_a_config_asks_for_another_length_within_what_its_recipe_allows(prod_env):
    config, jobs = str(prod_env / "config"), str(prod_env / "jobs")
    job = "practical_ai_intelligence/07_product_content_opportunities"
    (prod_env / "config" / "production" / "long.yaml").write_text(
        f"job: {job}\nrecipe: deep_dive\nminutes: 7.5\n", encoding="utf-8")
    (prod_env / "config" / "production" / "long-kids.yaml").write_text(
        f"job: {job}\nrecipe: deep_dive\naudience: non_tech\nminutes: 4\n", encoding="utf-8")
    every = production.load_all(config, jobs)
    long, kids = every["long"], every["long-kids"]
    assert long.usable and (long.recipe, long.base_recipe, long.target_minutes, long.minutes, long.minutes_range) == (
        "deep_dive-m7p5", "deep_dive", 7.5, 7.5, (4.0, 8.0))
    assert kids.usable and (kids.recipe, kids.audience_key, kids.target_minutes) == (
        "ai-non_tech-deep_dive-long-m4", "non_tech", 4.0)
    assert every["deep_dive"].minutes == 0 and every["deep_dive"].target_minutes == 6  # the recipe's own length
    assert video_columns.get(config, "deep_dive-m7p5").target_minutes == 7.5
    assert video_columns.get(config, "deep_dive").target_minutes == 6
    # only the length changes: the same prompts and settings as the recipe's own
    import dataclasses
    own, longer = dataclasses.asdict(video_columns.get(config, "deep_dive")), dataclasses.asdict(video_columns.get(config, "deep_dive-m7p5"))
    assert {k for k in own if own[k] != longer[k]} == {"key", "target_minutes"}


def test_api_adding_a_config_with_a_length_and_writing_from_it(client, prod_env, monkeypatch):
    monkeypatch.setattr("web.services.video_workflow.spawn", lambda *a, **k: None)  # make the script, do not run it
    _login(client)
    job = "practical_ai_intelligence/07_product_content_opportunities"
    refused = client.post("/api/production", json={"job": "monitoring/ai_flash_discovery", "recipe": "flash", "minutes": 3},
                          headers=_csrf(client))
    assert refused.status_code == 422 and "时长不能改" in refused.json()["error"]["message"]
    added = client.post("/api/production", json={"job": job, "recipe": "deep_dive", "minutes": 8}, headers=_csrf(client))
    assert added.status_code == 201, added.text
    made = added.json()
    assert (made["key"], made["recipe"], made["minutes"], made["target_minutes"]) == ("deep_dive-m8", "deep_dive-m8", 8, 8)
    assert yaml.safe_load((prod_env / "config" / "production" / "deep_dive-m8.yaml").read_text(encoding="utf-8")) == {
        "job": job, "recipe": "deep_dive", "minutes": 8}
    listed = {c["key"]: c for c in client.get("/api/production").json()}
    assert listed["deep_dive-m8"]["minutes_range"] == [4, 8] and listed["flash"]["minutes_range"] == []
    # a script made at that length is a column the app can read back: it appears on the board and is not "unknown"
    made_script = client.post("/api/video/scripts", headers=_csrf(client), json={
        "column": "deep_dive-m8", "topic": {"title": "八分钟的选题", "angle": "x"}})
    assert made_script.status_code == 202, made_script.text
    detail = client.get(f"/api/video/scripts/{made_script.json()['id']}").json()
    assert detail["column_key"] == "deep_dive-m8" and detail["column"]["key"] == "deep_dive-m8"
    assert detail["column"]["target_minutes"] == 8
    from web.routes import video  # the lookups that read a script's column back (approve, render, board) find it too
    known = video._known_columns()
    assert known.get("deep_dive-m8").target_minutes == 8 and known.get("deep_dive-m9") is None and "deep_dive-m8" not in known
    assert client.post("/api/video/scripts", headers=_csrf(client), json={
        "column": "deep_dive-m9", "topic": {"title": "太长", "angle": "x"}}).status_code == 422
    assert client.post("/api/video/scripts", headers=_csrf(client), json={
        "column": "flash-m2", "topic": {"title": "不让改", "angle": "x"}}).status_code == 422


def test_api_adding_a_config_for_an_audience(client, prod_env):
    _login(client)
    body = {"job": "monitoring/ai_flash_discovery", "recipe": "flash", "audience": "non_tech"}
    added = client.post("/api/production", json=body, headers=_csrf(client))
    assert added.status_code == 201, added.text
    made = added.json()
    assert made["key"] == "ai-non_tech-quick_take-short-2"  # named after the column it writes with; the shipped one has it
    assert (made["recipe"], made["base_recipe"], made["audience_key"], made["audience"]) == (
        "ai-non_tech-quick_take-short", "flash", "non_tech", "普通用户")
    assert yaml.safe_load((prod_env / "config" / "production" / "ai-non_tech-quick_take-short-2.yaml").read_text(
        encoding="utf-8")) == body
    bad = client.post("/api/production", json={**body, "audience": "ghosts"}, headers=_csrf(client))
    assert bad.status_code == 422 and "没有 ghosts 这个受众" in bad.json()["error"]["message"]
    assert not list((prod_env / "config" / "production").glob("*ghosts*")) and not (
        prod_env / "config" / "production" / "flash-2.yaml").exists()


def test_api_only_editors_change_or_write(client, prod_env):
    _login(client, "viewer", "viewer-pass-123")
    assert client.get("/api/production").status_code == 200
    assert client.post("/api/production/flash/write", json={"topic_ids": ["2026-10-19/F1"]},
                       headers=_csrf(client)).status_code == 403


def test_a_config_can_turn_the_research_off_only_where_its_recipe_says_so(prod_env):
    config, jobs = str(prod_env / "config"), str(prod_env / "jobs")
    job = "monitoring/ai_flash_discovery"
    for name, body in {"bare": f"job: {job}\nrecipe: flash\nreverify: false\n",
                       "bare-kids": f"job: {job}\nrecipe: flash\naudience: non_tech\nreverify: false\n",
                       "keeps": f"job: {job}\nrecipe: flash\nreverify: true\n",
                       "no-point": f"job: {job}\nrecipe: point_short\nreverify: false\n",
                       "roundup": f"job: {job}\nrecipe: ai-developer-roundup-long\nreverify: false\n",
                       "silly": f"job: {job}\nrecipe: flash\nreverify: 0\n"}.items():
        (prod_env / "config" / "production" / f"{name}.yaml").write_text(body, encoding="utf-8")
    every = production.load_all(config, jobs)
    bare = every["bare"]
    assert bare.usable and (bare.recipe, bare.base_recipe, bare.reverify, bare.split) == ("flash-nr", "flash", False, "each")
    assert (every["bare-kids"].recipe, every["bare-kids"].audience_key) == ("ai-non_tech-quick_take-short-nr", "non_tech")
    assert every["keeps"].recipe == "flash" and every["keeps"].reverify  # asking for the research is no change
    assert "调研不能关" in "".join(every["no-point"].errors)  # explaining a knowledge point takes research
    assert every["roundup"].recipe == "ai-developer-roundup-long" and not every["roundup"].reverify  # none to turn off
    assert "reverify 应为" in "".join(every["silly"].errors)
    # only what it is written from changes
    own, bare_col = (dataclasses.asdict(video_columns.get(config, k)) for k in ("flash", "flash-nr"))
    assert {k for k in own if own[k] != bare_col[k]} == {"key", "research_mode", "research_prompt", "research_timeout_seconds",
                                                          "research_language"} - {k for k in own if own[k] == bare_col[k]}
    assert bare_col["research_mode"] == "none" and bare_col["script_style"] == own["script_style"]


def test_api_writing_without_research_gives_the_writer_only_that_items_part_of_the_report(client, prod_env, research, llm, autovideo, monkeypatch):
    _organize_event(monkeypatch)
    budget = length.budget_for(video_columns.get(str(ROOT / "config"), "flash").target_minutes, "voxcpm_anchor")
    llm.replies = [script_json(budget - 5, citations={(0, 0): ["https://example.com/pricing"]})]
    report = prod_env / "output" / "flash" / "2026-10-19_ai_flash_discovery.md"
    report.write_text(report.read_text(encoding="utf-8") + "3. 无关来源 | 二手 | 2026-10-01 | https://example.com/unrelated\n",
                      encoding="utf-8")
    _login(client)
    added = client.post("/api/production", json={"job": "monitoring/ai_flash_discovery", "recipe": "flash", "reverify": False},
                        headers=_csrf(client))
    assert added.status_code == 201, added.text
    assert (added.json()["key"], added.json()["recipe"], added.json()["reverify"]) == ("flash-nr", "flash-nr", False)
    made = client.post("/api/production/flash-nr/write", json={"topic_ids": ["2026-10-19/F1"]}, headers=_csrf(client))
    assert made.status_code == 202, made.text
    script = _wait(client, made.json()[0]["id"], {"DRAFT", "FAILED"})
    assert script["status"] == "DRAFT", script.get("error")
    assert research.jobs == []  # nothing was researched
    stored = store.get_script(script["id"])
    assert stored["column_key"] == "flash-nr" and stored["production"] == "flash-nr"
    assert stored["topic"]["from_item"] == "2026-10-19/F1" and stored["topic"]["from_report"].endswith("_ai_flash_discovery.md")
    sent = "\n".join(m["content"] for m in FakeLLM.calls[0])
    assert "Opus 5.5 输入价降到 4 美元" in sent and "https://example.com/pricing" in sent  # the item and its source
    assert "https://example.com/releases/2.0" in sent  # F1 cites source 2 as well as 1
    assert "某框架 2.0" not in sent and "https://example.com/unrelated" not in sent  # not the other item, nor a source F1 never cites
    assert "未收录但值得留意" not in sent
    again = client.get("/api/production/flash-nr/pending").json()
    assert [i["written"] for i in again["items"]][:2] == [True, False]
