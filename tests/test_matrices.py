"""Matrices: a set of Jobs sharing a domain and keywords, applied when a member runs (spec/matrices.md)."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime

import pytest

from core import events, matrices
from core.config import load_job
from core.engine import ResearchEngine
from tests.test_events import REPORT, events_db  # noqa: F401

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _engine(config_dir: str) -> ResearchEngine:
    engine = ResearchEngine.__new__(ResearchEngine)
    engine.config_dir = config_dir
    engine.prompt_vars = {}
    engine._now = lambda: datetime(2026, 10, 20, 8, 0, 0)
    engine._reported_events = lambda: "<<events>>"
    engine._outcomes_block = lambda: "<<outcomes>>"
    return engine


AI_MEDIA = {"monitoring/ai_flash_discovery", "ai_media/daily_opensource_radar", "ai_media/weekly_model_arena",
            "ai_media/weekly_business_watch", "ai_media/weekly_deep_research"}


def test_every_existing_job_renders_the_prompt_it_rendered_before_matrices():
    """The hashes were taken from the engine before matrices existed (tests/fixtures/job_prompt_hashes.json,
    scripts/snapshot_job_prompts.py)."""
    with open(os.path.join(ROOT, "tests/fixtures/job_prompt_hashes.json"), encoding="utf-8") as fh:
        before = json.load(fh)
    config, jobs = os.path.join(ROOT, "config"), os.path.join(ROOT, "jobs")
    engine, checked = _engine(config), 0
    for name, digest in before.items():
        job = load_job(jobs, name)
        if job is None:  # a Job some checkout does not have
            continue
        run = matrices.for_run(job, config)
        prompt = engine._build_prompt(run)
        text = prompt + "\n--fallback--\n" + json.dumps(run.get("keywords", []), ensure_ascii=False)
        assert hashlib.sha256(text.encode()).hexdigest() == digest, (
            f"{name} renders a different prompt than before matrices. If its prompt was edited on purpose, take the "
            f"snapshot again: python scripts/snapshot_job_prompts.py")
        # its event memory is unchanged too, except that since 2026-10-22 the two AI matrices (the 26 older Jobs and
        # the 5 media Jobs) leave each other's events out; no other matrix exists
        assert matrices.jobs_of_other_matrices(name, config) <= AI_MEDIA | set(matrices.load_all(config)["ai"].jobs), name
        checked += 1
    assert checked >= 24


def test_the_ai_matrices_hold_the_old_jobs_and_the_media_jobs_and_only_the_flash_job_can_be_copied():
    loaded = matrices.load_all(os.path.join(ROOT, "config"))
    ai, media = loaded["ai"], loaded["ai_media"]
    assert not ai.errors and not media.errors and ai.keywords == () and ai.domain == media.domain == "AI 与软件开发"
    assert set(media.jobs) == AI_MEDIA and not AI_MEDIA & set(ai.jobs) and "research/独立游戏" not in ai.jobs
    assert [m.job for m in media.members if m.copy_as] == ["monitoring/ai_flash_discovery"]
    assert not [m for m in ai.members if m.copy_as]
    assert os.path.isfile(os.path.join(ROOT, "jobs/_templates/_flash_discovery.yaml"))


def write(config, key: str, text: str) -> None:
    folder = config / "matrices"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{key}.yaml").write_text(text, encoding="utf-8")


def test_a_member_runs_with_the_matrix_keywords_first_and_its_domain(tmp_path):
    write(tmp_path, "medical", "name: 医疗矩阵\ndomain: 医疗健康\nkeywords: [新药审批, 医保政策]\njobs: [medical/flash]\n")
    job = {"_file": "medical/flash", "name": "医疗快讯", "keywords": ["医保政策", "医学家"],
           "prompt": "领域：{domain}。关键词：{keywords}。{name}"}
    run = matrices.for_run(job, str(tmp_path))
    assert run["keywords"] == ["新药审批", "医保政策", "医学家"] and job["keywords"] == ["医保政策", "医学家"]
    assert _engine(str(tmp_path))._build_prompt(run) == "领域：医疗健康。关键词：新药审批, 医保政策, 医学家。医疗快讯"
    outside = {"_file": "research/x", "name": "独立游戏", "prompt": "{domain}"}
    assert matrices.for_run(outside, str(tmp_path)) is outside
    assert _engine(str(tmp_path))._build_prompt(outside) == "独立游戏"  # no matrix: {domain} is the Job's name


def test_a_job_is_in_one_matrix_and_generated_jobs_in_none(tmp_path):
    write(tmp_path, "a", "name: A\njobs: [x/one, video/flash_1]\n")
    write(tmp_path, "b", "name: B\njobs: [x/one, x/two]\nschedule: daily\n")
    every = matrices.load_all(str(tmp_path))
    assert every["a"].jobs == ["x/one"] and "视频流水线" in every["a"].errors[0]
    assert every["b"].jobs == ["x/two"]
    assert any("已经在「A」里" in e for e in every["b"].errors) and any("schedule" in e for e in every["b"].errors)


def test_a_matrix_survives_being_saved_and_read_back(tmp_path):
    members = (matrices.Member("x/flash", copy_as="_flash_discovery"), matrices.Member("x/deep"))
    matrices.save(str(tmp_path), "m", name="M", domain="医疗", keywords=["a", "a", " b "], members=members)
    back = matrices.load_all(str(tmp_path))["m"]
    assert (back.keywords, back.members, back.errors) == (("a", "b"), members, ())


def test_another_matrix_s_sources_are_left_out_of_a_job_s_event_memory(tmp_path, events_db):  # noqa: F811
    write(tmp_path, "ai", "name: AI\njobs: [ai/daily]\n")
    write(tmp_path, "medical", "name: 医疗\njobs: [medical/flash]\n")
    events.register_report("medical/flash", "2026-09-28", REPORT, round_date="2026-09-28")
    assert matrices.jobs_of_other_matrices("ai/daily", str(tmp_path)) == {"medical/flash"}
    assert matrices.jobs_of_other_matrices("research/x", str(tmp_path)) == set()  # in no matrix: reads it all
    assert events.reported_block(exclude_jobs={"medical/flash"}) == ""
    events.register_report("ai/daily", "2026-09-29", REPORT, round_date="2026-09-29")  # AI reported it too: it stays
    assert "ai.google.dev" in events.reported_block(exclude_jobs={"medical/flash"})
    assert events.reported_block() == events.reported_block(exclude_jobs=set())


@pytest.mark.parametrize("bad", ["name: [", "- a list\n"])
def test_a_broken_matrix_file_is_listed_with_why_and_a_run_goes_on(tmp_path, bad):
    write(tmp_path, "bad", bad)
    assert matrices.load_all(str(tmp_path))["bad"].errors
    job = {"_file": "x/one", "keywords": ["k"]}
    assert matrices.for_run(job, str(tmp_path)) is job


# --- API -------------------------------------------------------------------------------------------------------------

from tests.test_video_scripts import _csrf, _login, video_env  # noqa: E402,F401

AI_MATRIX = """name: AI 矩阵
domain: AI 与软件开发
keywords: []
jobs:
- job: monitoring/ai_flash_discovery
  copy_as: _flash_discovery
- research/deep
"""


@pytest.fixture
def matrix_env(video_env):
    write(video_env / "config", "ai", AI_MATRIX)
    (video_env / "jobs/_templates").mkdir(parents=True, exist_ok=True)
    shipped = os.path.join(ROOT, "jobs/_templates/_flash_discovery.yaml")
    (video_env / "jobs/_templates/_flash_discovery.yaml").write_text(open(shipped, encoding="utf-8").read(), encoding="utf-8")
    (video_env / "jobs/research").mkdir(parents=True, exist_ok=True)
    (video_env / "jobs/research/deep.yaml").write_text("name: AI 深度\nprompt: 讲讲 AI\noutput: output/d/{date}.md\n", encoding="utf-8")
    (video_env / "jobs/research/games.yaml").write_text("name: 独立游戏\nprompt: x\noutput: output/g/{date}.md\n", encoding="utf-8")
    return video_env


MEDICAL = {"key": "medical", "name": "医疗矩阵", "domain": "医疗健康", "keywords": ["新药审批", "医保政策"],
           "jobs": ["monitoring/ai_flash_discovery"]}


def test_api_lists_matrices_with_their_jobs_and_the_jobs_in_none(client, matrix_env):
    _login(client)
    body = client.get("/api/matrices").json()
    ai = body["matrices"][0]
    assert ai["key"] == "ai" and [m["job"] for m in ai["members"]] == ["monitoring/ai_flash_discovery", "research/deep"]
    flash, deep = ai["members"]
    assert flash["copyable"] and flash["topics"] == "flash" and flash["schedule"] is None and not deep["copyable"]
    assert {"job": "research/games", "name": "独立游戏"} in body["ungrouped"]


def test_api_copying_a_matrix_makes_manual_jobs_with_their_own_topic_source(client, matrix_env):
    from core.video import topic_store

    _login(client)
    made = client.post("/api/matrices/ai/copy", json=MEDICAL, headers=_csrf(client))
    assert made.status_code == 201, made.text
    assert made.json()["keywords"] == ["新药审批", "医保政策"] and made.json()["members"][0]["job"] == "medical/flash_discovery"
    job = load_job(str(matrix_env / "jobs"), "medical/flash_discovery")
    assert job["enabled"] and job["schedule"] == {"type": "manual"} and job["keywords"] == []
    assert job["output"] == "output/medical/{date}_flash_discovery.md" and job["name"] == "医疗健康 快讯发现"
    assert job["topics"] == {"alias": "medical_flash_discovery", "format": "flash", "prefix": "MF", "refresh": False}
    sources = topic_store.sources(str(matrix_env / "jobs"))
    assert not sources["medical_flash_discovery"].error and not sources["flash"].error
    config = str(matrix_env / "config")
    prompt = _engine(config)._build_prompt(matrices.for_run(job, config))
    assert "领域：医疗健康。关键词：新药审批, 医保政策。" in prompt and "AI" not in prompt
    again = client.post("/api/matrices/medical/copy", json={**MEDICAL, "key": "finance", "domain": "金融",
                                                             "jobs": ["medical/flash_discovery"]}, headers=_csrf(client))
    assert again.status_code == 201  # a copy can be copied: it keeps its template
    assert load_job(str(matrix_env / "jobs"), "finance/flash_discovery")["topics"]["prefix"] == "FF"


@pytest.mark.parametrize("change, code, kind", [
    ({"jobs": ["research/deep"]}, 422, "not_copyable"),       # its prompt says AI
    ({"key": "ai"}, 409, "matrix_exists"),
    ({"key": "research"}, 409, "jobs_dir_exists"),
    ({"key": "Medical"}, 422, "invalid_key"),
    ({"keywords": []}, 422, "missing_fields"),
])
def test_api_a_copy_that_cannot_be_made_says_why_and_writes_nothing(client, matrix_env, change, code, kind):
    _login(client)
    response = client.post("/api/matrices/ai/copy", json={**MEDICAL, **change}, headers=_csrf(client))
    assert response.status_code == code and response.json()["error"]["code"] == kind
    assert not (matrix_env / "jobs/medical").exists() and not (matrix_env / "config/matrices/medical.yaml").exists()


def test_api_editing_members_keeps_a_job_in_one_matrix_and_deleting_keeps_the_jobs(client, matrix_env):
    _login(client)
    made = client.post("/api/matrices", json={"key": "games", "name": "游戏矩阵", "domain": "独立游戏",
                                              "keywords": ["Steam"]}, headers=_csrf(client))
    assert made.status_code == 201 and made.json()["members"] == []
    put = lambda jobs: client.put("/api/matrices/games", json={"name": "游戏矩阵", "domain": "独立游戏",  # noqa: E731
                                                               "keywords": ["Steam", "独立"], "jobs": jobs},
                                  headers=_csrf(client))
    assert put(["research/deep"]).json()["error"]["code"] == "job_in_other_matrix"
    assert put(["video/flash_1"]).json()["error"]["code"] == "generated_job"
    assert put(["research/nope"]).json()["error"]["code"] == "job_not_found"
    done = put(["research/games"]).json()
    assert done["keywords"] == ["Steam", "独立"] and done["members"][0]["name"] == "独立游戏"
    ai = client.put("/api/matrices/ai", json={"name": "AI 矩阵", "domain": "AI 与软件开发", "keywords": []},
                    headers=_csrf(client)).json()
    assert ai["members"][0]["copy_as"] == "_flash_discovery"  # no jobs sent: members and their templates stay
    assert client.delete("/api/matrices/games", headers=_csrf(client)).status_code == 200
    assert (matrix_env / "jobs/research/games.yaml").exists()


def test_api_archiving_a_matrix_hides_nothing_but_the_flag_and_survives_a_rename(client, matrix_env):
    _login(client)
    done = client.post("/api/matrices/ai/archive", json={"archived": True}, headers=_csrf(client)).json()
    assert done["archived"] is True and [m["job"] for m in done["members"]] == ["monitoring/ai_flash_discovery", "research/deep"]
    renamed = client.put("/api/matrices/ai", json={"name": "AI 矩阵 2", "domain": "AI 与软件开发", "keywords": []},
                         headers=_csrf(client)).json()
    assert renamed["archived"] is True  # a rename does not bring it back
    assert [m for m in client.get("/api/matrices").json()["matrices"] if m["key"] == "ai"][0]["archived"] is True
    back = client.post("/api/matrices/ai/archive", json={"archived": False}, headers=_csrf(client)).json()
    assert back["archived"] is False


def test_api_only_editors_change_matrices(client, matrix_env):
    _login(client, "viewer", "viewer-pass-123")
    assert client.get("/api/matrices").status_code == 200
    assert client.post("/api/matrices/ai/copy", json=MEDICAL, headers=_csrf(client)).status_code == 403
