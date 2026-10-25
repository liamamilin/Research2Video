"""Common copy quality survives historical audits and every submission mode."""
from copy import deepcopy
import importlib.util

import pytest

from core.video import columns, credibility, production, store
from core.video.adapt import review
from core.video.audience_copy import findings
from tests.test_video_scripts import (ROOT, _approved_script, _csrf, _login, video_env)  # noqa: F401
from web.services import video_board, video_executor, video_render, video_workflow

BAD_COPY = "材料没有给出量化续航、具体售价，所以不能直接比较。"
GOOD_COPY = "官方标称在实验室条件下续航最高72小时，日常使用结果可能不同。"


def historical_version(script, text=BAD_COPY):
    version = store.get_version(script["id"], 1)
    version["sections"][0]["paragraphs"][0]["text"] = text
    # Simulate a saved pre-guard success; today's assemble must reject bad copy.
    checks = review.assemble([], {}, {"issues": []}, None,
                             [{"paragraphs": [{"text": GOOD_COPY, "evidence": "CITED"}]}])
    assert checks["summary"]["passed"]
    version["meta"] = {"checks": checks}
    store.update_version_audit(script["id"], 1, sections=version["sections"], meta=version["meta"])
    return version


@pytest.mark.parametrize("key", tuple(columns.load(str(ROOT / "config"))))
def test_every_resolved_recipe_refreshes_copy_checks_without_mutating_history(key):
    column = columns.get(str(ROOT / "config"), key)
    sections = [{"title": "本期重点", "paragraphs": [{"text": BAD_COPY, "evidence": "CITED"}]}]
    version = {"sections": sections, "meta": {"checks": review.assemble([], {}, {"issues": []}, None, sections)}}
    saved = deepcopy(version)
    checks = video_workflow.rule_checks(version, column)
    assert checks["model_ran"] and not checks["summary"]["passed"]
    assert [i["source"] for i in checks["issues"]] == ["audience_copy"]
    assert video_workflow.rule_checks({**version, "meta": {"checks": checks}}, column) == checks
    assert version == saved
    assert credibility.label(version)["tier"] == "spoken"


@pytest.mark.parametrize("key", tuple(production.load_all(str(ROOT / "config"), str(ROOT / "jobs"))))
def test_every_matrix_production_binding_uses_the_common_copy_checks(key):
    binding = production.get(str(ROOT / "config"), str(ROOT / "jobs"), key)
    if not binding.usable:
        pytest.skip("unusable production config: " + "; ".join(binding.errors))
    column = columns.get(str(ROOT / "config"), binding.recipe)
    sections = [{"title": "本期重点", "paragraphs": [{"text": BAD_COPY}]}]
    checks = video_workflow.rule_checks({"sections": sections}, column)
    assert any(i.get("source") == "audience_copy" for i in checks["issues"])


def test_manual_approval_returns_location_and_preserves_the_draft(client, video_env):
    _login(client)
    script = _approved_script()
    original = historical_version(script)
    store.update_script(script["id"], status="DRAFT", approved_version=None)
    response = client.post(f"/api/video/scripts/{script['id']}/approve", json={}, headers=_csrf(client))
    assert response.status_code == 200
    assert store.get_script(script["id"])["status"] == "APPROVED"
    assert store.get_version(script["id"], 1)["sections"] == original["sections"]


def test_the_board_does_not_recommend_rendering_copy_the_handoff_would_reject(video_env):
    script = _approved_script()
    version = historical_version(script)
    label = credibility.label(version)
    action = video_board._next_action(script, "approved", label, [], [])
    assert label["tier"] == "spoken" and action["action"] == "review"
    assert action["action_label"] == "修改口播"


@pytest.mark.parametrize("mode", [None, "standard", "fast"])
def test_new_renders_of_old_approved_versions_cannot_bypass_local_checks(video_env, mode):
    script = _approved_script()
    historical_version(script)
    render = video_render.create(script, production_mode=mode)
    assert BAD_COPY in render["source_text"]
    assert len(store.list_renders(script["id"])) == 1


def test_manual_render_route_and_rerun_use_the_same_guard(client, video_env, monkeypatch):
    monkeypatch.setattr(video_render, "start", lambda *_: None)
    _login(client)
    script = _approved_script()
    historical_version(script)
    response = client.post(f"/api/video/scripts/{script['id']}/render", json={}, headers=_csrf(client))
    assert response.status_code == 202, response.text
    assert BAD_COPY in store.list_renders(script["id"])[0]["source_text"]


def test_factual_limits_and_manual_pending_review_are_preserved(video_env):
    script = _approved_script()
    version = historical_version(script, GOOD_COPY)
    version["sections"][0]["paragraphs"][0].update(evidence="NEEDS_REVIEW", review_reason="待核验具体测试版本")
    store.update_version_audit(script["id"], 1, sections=version["sections"], meta=version["meta"])
    video_executor.approve(script["id"], 1, "reviewer")
    for mode in ("standard", "fast"):
        render = video_render.create(store.get_script(script["id"]), production_mode=mode)
        assert render["request"]["chapters"][0]["paragraphs"][0] == GOOD_COPY
        assert render["request"]["content_notices"][0]["message"] == "本章节有内容待核实"
        store.update_render(render["id"], status="COMPLETED")


def test_resuming_an_existing_download_is_not_a_new_generation(video_env):
    script = _approved_script()
    render = video_render.create(script)
    request = deepcopy(render["request"])
    version = historical_version(script)
    request["chapters"] = video_render.chapters(version)
    text, sha = video_render.narration_source(request["chapters"])
    # Simulate an old submission with the same approved text and saved request.
    store.update_render(render["id"], status="FAILED", error_code="DOWNLOAD_FAILED", autovideo_job_id="already-generated",
                        request=request, source_text=text, source_sha256=sha)
    resumed = video_render.create(script, recover_download=True)
    assert resumed["id"] == render["id"] and resumed["status"] == "DOWNLOADING"
    assert resumed["request"] == request and len(store.list_renders(script["id"])) == 1


def test_research_and_independent_autovideo_handoff_have_the_same_copy_contract():
    path = ROOT.parents[1] / "autovideo/g2/audience_copy.py"
    if not path.is_file():
        pytest.skip("independent AutoVideo checkout is not installed beside Research")
    spec = importlib.util.spec_from_file_location("autovideo_copy_contract", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    from core.video.audience_copy import GUIDANCE, _INTERNAL_GAP
    assert module.GUIDANCE == GUIDANCE and module.INTERNAL_GAP.pattern == _INTERNAL_GAP.pattern
    for text in (BAD_COPY, GOOD_COPY, "材料没有毒性。", "摘要未列出售价。", "所给资料并没有提供数据。"):
        assert not module.problem(text)
        assert not __import__("core.video.audience_copy", fromlist=["submission_problem"]).submission_problem({"sections": [{"paragraphs": [{"text": text}]}]})
