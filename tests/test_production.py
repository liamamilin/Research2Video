"""Production configs: a recipe tied to a matrix Job, writing from that Job's reports (spec/production.md)."""

from __future__ import annotations

import os
import shutil

import pytest
import yaml

from core.video import length, production, recipes, store
from core.video import columns as video_columns
from tests.test_video_flash import DISCOVERY
from tests.test_video_scripts import (GOOD, ROOT, FakeLLM, _csrf, _login, _wait, autovideo, llm,  # noqa: F401
                                      research, script_json, video_env)


def test_each_recipe_but_comparison_is_one_config_on_the_job_it_picks_from():
    config, jobs = str(ROOT / "config"), str(ROOT / "jobs")
    every = production.load_all(config, jobs)
    shipped = {k: r for k, r in recipes.expanded(config).items() if r.column and r.column.topic_kind != "comparison"}
    shipped.pop("ai-non_tech-roundup-long")  # an audience added after the migration: no config on it yet
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
    ({"job": "monitoring/ai_flash_discovery", "recipe": "flash", "audience": "儿童"}, "不认识的字段"),
    ({"job": "medical/flash_discovery", "recipe": "flash"}, "先在配方的 topic.sources 里加上 medical_flash"),  # an AI recipe
])
def test_a_config_that_cannot_write_is_listed_with_why(prod_env, data, message):
    (prod_env / "config" / "production" / "bad.yaml").write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    bad = production.load_all(str(prod_env / "config"), str(prod_env / "jobs"))["bad"]
    assert not bad.usable and any(message in e for e in bad.errors), bad.errors


def test_api_writing_from_a_config_is_writing_with_its_recipe_and_records_the_config(client, prod_env, research, llm, autovideo):
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
    assert first["status"] == second["status"] == "DRAFT"

    # the same recipe, the same topic: the research Job's prompt and the writer's request are the same, byte for byte
    def prompt(job: str) -> str:
        return yaml.safe_load((prod_env / "jobs" / f"{job}.yaml").read_text(encoding="utf-8"))["prompt"]
    assert len(research.jobs) == 2 and prompt(research.jobs[0]) == prompt(research.jobs[1])
    assert len(FakeLLM.calls) == 2 and FakeLLM.calls[0] == FakeLLM.calls[1]
    assert first["version_shown"]["meta"]["cell"] == second["version_shown"]["meta"]["cell"]
    assert store.get_script(second["id"])["production"] == "flash" and store.get_script(first["id"])["production"] == ""

    pending = client.get("/api/production/flash/pending").json()
    assert pending["report_date"] == "2026-10-19"
    assert [i["written"] for i in pending["items"]][:2] == [True, False]  # F1 written with this recipe, F2 not yet
    # both count: the direct one is flash on this config's Job, written before it was recorded (spec/outputs.md)
    assert {c["key"]: c["scripts"] for c in client.get("/api/production").json()}["flash"] == 2


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


def test_api_only_editors_change_or_write(client, prod_env):
    _login(client, "viewer", "viewer-pass-123")
    assert client.get("/api/production").status_code == 200
    assert client.post("/api/production/flash/write", json={"topic_ids": ["2026-10-19/F1"]},
                       headers=_csrf(client)).status_code == 403
