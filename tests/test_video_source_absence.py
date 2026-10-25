"""An omission in a research summary cannot certify an omission in its source."""
from copy import deepcopy

import pytest

from core.video.audience_copy import submission_problem, unverified_source_absences
from core.video import credibility
from tests.test_video_scripts import GOOD, _approved_script, video_env  # noqa: F401
from web.services import video_executor, video_render, video_workflow


def version(text):
    return {"sections":[{"title":"本期重点","paragraphs":[{"text":text,"citations":[GOOD],"evidence":"CITED",
             "fact_verification":{"status":"verified","reason":"摘要支持","sources":[GOOD]}}]}],
            "meta":{}}


@pytest.mark.parametrize('text',[
    '报道没有给出具体容量。','许可证没写，成片效果也没有公开说明。',
    '报道没有附上原始研究的出处。','报告里没有记录第三方独立复测。','论文未提供逐项分数。','具体容量没有披露。','仓库没有说明如何修改画面。'])
def test_unsupported_source_omissions_require_original_page_review(text):
    v=version(text)
    assert unverified_source_absences(v)
    assert not submission_problem(v)
    assert credibility.label(v)['tier']=='spoken'


@pytest.mark.parametrize('text',[
    '官方目前仅支持这两种硬件。','这仍是研究阶段，尚未商业化。',
    '在模拟重力条件下，13名受试者没有出现重大问题。','这个传说尚无定论。',
    '论文作者报告了提升，但不能推断任意部署都有同样收益。'])
def test_genuine_scope_and_uncertainty_are_not_source_omission_claims(text):
    assert not unverified_source_absences(version(text))
    assert not submission_problem(version(text))


def test_recorded_source_review_and_matching_paragraph_proof_can_support_a_real_omission():
    v=version('论文未提供单独的消融结果。')
    v['meta']['source_review']={'packet_sha256':'checked-packet','checked_source_urls':[GOOD]}
    assert not unverified_source_absences(v)
    assert not submission_problem(v)
    v['sections'][0]['paragraphs'][0]['fact_verification']['sources']=['https://unchecked.example/']
    assert unverified_source_absences(v)


def test_old_positive_audit_cannot_bypass_render_or_approval(video_env):
    from core.video import store
    script = _approved_script()
    old = store.get_version(script["id"], 1)
    old["sections"][0]["paragraphs"][0]["text"] = "许可证没写。"
    store.update_version_audit(script["id"], 1, sections=old["sections"], meta=old["meta"])
    video_executor.approve(script["id"], 1, "reviewer")
    render = video_render.create(script)
    assert "许可证没写。" in render["source_text"]


def test_new_source_omissions_enter_existing_repair_targets():
    from core.video.adapt import review
    v=version('报道没有给出具体容量。')
    checks=review.assemble([],{}, {'issues':[]},None,v['sections'])
    refreshed=video_workflow._current_copy_checks(checks,v['sections'],v['meta'])
    assert not refreshed['summary']['passed']
    assert 'S1P1' in review.targets(v['sections'],refreshed)


def test_adaptation_audit_routes_omissions_to_repair_even_if_model_misses_them():
    from core.video.adapt import review
    v = version('报道没有附上原始研究的出处。')
    checks = review.assemble([], {}, {'issues': []}, None, v['sections'])
    assert not checks['summary']['passed']
    assert 'S1P1' in review.targets(v['sections'], checks)
