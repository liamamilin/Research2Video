"""Quality-first repair across the writer and model audits."""

import json

from tests.test_video_claim_bounds import ORIGINAL, SHORTENED, SOURCE
from tests.test_video_scripts import (GOOD, REPORT, _login, _make_audited as _make, _roundup_budget, _write,
                                      llm, research, script_json, video_env)  # noqa: F401


def verified(reason="项目用途与来源一致"):
    return json.dumps({"issues": [], "verifications": [
        {"id": "S1P1", "status": "verified", "reason": reason, "sources": [GOOD]},
    ]})


def test_extra_verification_links_do_not_reject_the_paragraphs_actual_source():
    from core.video import audit

    sections = [{"paragraphs": [{"text": "运行会输出成本与耗时。", "citations": [GOOD],
                                  "evidence": "NEEDS_REVIEW", "review_reason": "旧标记"}]}]
    audit.reconcile_evidence(sections, [], [{"id": "S1P1", "status": "verified", "reason": "CLI 文档支持当前说法",
                                            "sources": [GOOD, "https://other.example/extra"]}])
    para = sections[0]["paragraphs"][0]
    assert para["evidence"] == "CITED" and "review_reason" not in para
    assert para["fact_verification"]["sources"] == [GOOD]
    assert para["citations"] == [GOOD]


def test_verification_without_any_paragraph_source_stays_pending():
    from core.video import audit

    sections = [{"paragraphs": [{"text": "运行会输出成本与耗时。", "citations": [GOOD], "evidence": "NEEDS_REVIEW"}]}]
    audit.reconcile_evidence(sections, [], [{"id": "S1P1", "status": "verified", "reason": "模型说通过",
                                            "sources": ["https://other.example/extra"]}])
    assert sections[0]["paragraphs"][0]["evidence"] == "NEEDS_REVIEW"
    assert sections[0]["paragraphs"][0]["fact_verification"]["sources"] == []


def question(text):
    return json.dumps({"paragraphs": [{"id": "S1P1", "questions": [text], "lost": []}]})


def fact_problem(text, *, with_proof=False):
    payload = json.loads(verified()) if with_proof else {"issues": []}
    payload["issues"] = [{"id": "S1P1", "rule": "D1", "severity": "严重", "problem": text}]
    return json.dumps(payload)


def rewrite(text):
    return json.dumps({"paragraphs": [{"id": "S1P1", "text": text}]})


def edited(text, title="工具先解决哪一步手工"):
    return json.dumps({"title": title, "sections": [{"title": "用途", "paragraphs": [
        {"text": text, "citations": [GOOD]}]}]}, ensure_ascii=False)


def test_repair_can_take_multiple_rounds_and_every_rewrite_is_audited(client, video_env, research, llm):
    key = "point_short"
    llm.replies = [script_json(_roundup_budget(key) - 5, citations={(0, 0): [GOOD]})]
    llm.reader_replies = [question("这个工具是什么？"), question("能拿它做什么？"), '{"paragraphs": []}']
    llm.revise_replies = [rewrite("这是一个做动效的开源工具。"), rewrite("这是一个做动效的开源工具，可以用来制作网页动画。")]
    llm.audit_replies = [fact_problem("用途与来源不符"), fact_problem("仍有无依据的用途", with_proof=True), verified()]
    _login(client)
    script = _make(client, key, "连续两轮修正")
    shown = script["version_shown"]
    assert script["status"] == "DRAFT" and shown["checks"]["rounds"] == 2
    assert len(llm.revise_calls) == 2 and len(llm.audit_calls) == 3
    assert len(llm.style_calls) == 3 and len(llm.reader_calls) == 3
    assert shown["sections"][0]["paragraphs"][0]["evidence"] == "CITED"
    assert shown["meta"]["audit_usage"]["requests"] == 11  # all three audit passes and both rewrites


def test_empty_post_rewrite_findings_leave_the_paragraph_pending(client, video_env, research, llm):
    key = "point_short"
    llm.replies = [script_json(_roundup_budget(key) - 5, citations={(0, 0): [GOOD]})]
    llm.reader_replies = [question("这个工具是什么？")]
    llm.audit_replies = [fact_problem("用途与来源不符"), '{"issues": []}']
    llm.revise_replies = [rewrite("这是一个做动效的开源工具。")]
    _login(client)
    script = _make(client, key, "复查不能靠空问题列表清除标记")
    para = script["version_shown"]["sections"][0]["paragraphs"][0]
    assert script["status"] == "DRAFT" and para["evidence"] == "NEEDS_REVIEW"
    assert para["review_reason"] == "用途与来源不符"  # the unresolved concrete reason survives an empty reply
    assert len(llm.revise_calls) == 2  # tries to repair the pending paragraph; no change stops the loop
    assert "【待核实】" in llm.revise_calls[1][-1]["content"]


def test_initial_explicit_proof_clears_a_stale_flag_without_an_unnecessary_rewrite(client, video_env, research, llm):
    key = "point_short"
    payload = json.loads(script_json(_roundup_budget(key) - 5, citations={(0, 0): [GOOD]}))
    payload["sections"][0]["paragraphs"][0].update(needs_review=True, review_reason="原审计标记尚未解决")
    llm.replies = [json.dumps(payload)]
    llm.audit_replies = [verified()]
    _login(client)
    script = _make(client, key, "有明确依据才解除核实标记")
    assert script["version_shown"]["sections"][0]["paragraphs"][0]["evidence"] == "CITED"
    assert llm.revise_calls == []


def test_repair_can_add_a_missing_report_citation_then_request_explicit_verification(client, video_env, research, llm):
    key = "point_short"
    payload = json.loads(script_json(_roundup_budget(key) - 5))
    para = payload["sections"][0]["paragraphs"][0]
    para.update(needs_review=True, review_reason="缺少出处")
    llm.replies = [json.dumps(payload)]
    llm.audit_replies = ['{"issues": []}', verified()]
    llm.revise_replies = [json.dumps({"paragraphs": [{"id": "S1P1", "text": para["text"], "citations": [GOOD]}]})]
    _login(client)
    script = _make(client, key, "补充可信引用后复审")
    current = script["version_shown"]["sections"][0]["paragraphs"][0]
    assert current["citations"] == [GOOD] and current["evidence"] == "CITED"
    assert script["version_shown"]["checks"]["rounds"] == 1


def test_automatic_repair_is_bounded_when_the_same_problem_persists(client, video_env, research, llm):
    key = "point_short"
    llm.replies = [script_json(_roundup_budget(key) - 5, citations={(0, 0): [GOOD]})]
    llm.reader_replies = [question("机制仍没讲清楚") for _ in range(5)]
    llm.revise_replies = [rewrite(f"这是第{i}次修改的工具说明。") for i in range(5)]
    llm.audit_replies = [fact_problem("仍有无依据的用途") for _ in range(6)]
    _login(client)
    script = _make(client, key, "修正不应无限循环")
    shown = script["version_shown"]
    assert shown["checks"]["rounds"] == 3 and len(llm.revise_calls) == 3
    assert any(i["rule"] == "D1" for i in shown["checks"]["issues"])


def test_reader_advice_does_not_trigger_rewriting_or_block_core_checks(client, video_env, research, llm):
    key = "point_short"
    llm.replies = [script_json(_roundup_budget(key) - 5, citations={(0, 0): [GOOD]})]
    llm.reader_replies = [question("配置的入口在哪里？")]
    _login(client)
    script = _make(client, key, "核心通过时保留一般建议")
    assert llm.revise_calls == []
    checks = script["version_shown"]["checks"]
    assert checks["summary"]["passed"] is True
    assert [i["severity"] for i in checks["issues"] if i["rule"] == "N1"] == ["warn"]


def test_concrete_listening_problem_gets_one_editorial_pass_and_fact_recheck(client, video_env, research, llm):
    key = "point_short"
    llm.replies = [script_json(_roundup_budget(key) - 5, citations={(0, 0): [GOOD]})]
    lost = json.dumps({"paragraphs": [{"id": "S1P1", "questions": [], "lost": ["开场先念内部核实过程，听不出为什么值得继续听"]}]})
    llm.reader_replies = [lost, '{"paragraphs": []}']
    llm.editorial_replies = [edited("先看它能省下哪一步手工：这是一个做动效的开源工具。")]
    llm.audit_replies = ['{"issues": []}', verified()]
    _login(client)
    made = _make(client, key, "编辑质量不能只显示建议")
    shown = made["version_shown"]
    assert len(llm.editorial_calls) == 1 and len(llm.audit_calls) == 2 and not llm.revise_calls
    assert "【编辑建议，按实际效果取舍】" in llm.brief_calls[0][-1]["content"]
    assert "开场先念内部核实过程" not in llm.editorial_calls[0][-1]["content"]
    assert "## 原稿" not in llm.editorial_calls[0][-1]["content"]
    assert shown["sections"][0]["paragraphs"][0]["text"].startswith("先看它能省下")
    assert shown["checks"]["summary"]["passed"] and shown["checks"]["editorial_attempted"]
    assert shown["checks"]["rounds"] == 1
    assert shown["title"] == "工具先解决哪一步手工" and len(shown["sections"]) == 1
    assert shown["meta"]["editorial_attempt"]["decision"]["choice"] == "B"
    assert shown["meta"]["editorial_attempt"]["editorial_brief"]["angle"]

    comparison = llm.compare_calls[0][-1]["content"]
    assert "evidence" not in comparison and "citations" not in comparison and "review_reason" not in comparison


def test_whole_script_editing_can_resolve_listening_and_source_problems_together(client, video_env, research, llm):
    llm.replies = [script_json(300, citations={(0, 0): [GOOD]})]
    llm.reader_replies = [json.dumps({"paragraphs": [{"id": "S1P1", "lost": ["开场在念资料目录"]}]})]
    llm.audit_replies = [fact_problem("原稿把项目自述扩大成普遍效果"), verified()]
    llm.editorial_replies = [edited("一轮评测跑完，成本与耗时会直接出现在输出里。")]
    _login(client)
    shown = _make(client, "point_short", "同时改善听感和实际说法")["version_shown"]
    assert shown["checks"]["summary"]["passed"]
    assert shown["meta"]["editorial_attempt"]["applied"] and not llm.revise_calls
    assert len(llm.audit_calls) == 2 and len(llm.compare_calls) == 1
    assert "项目自述扩大" in llm.brief_calls[0][-1]["content"]
    assert shown["sections"][0]["paragraphs"][0]["text"].startswith("一轮评测跑完")


def test_joint_editing_still_rejects_an_unverified_candidate(client, video_env, research, llm):
    original = json.loads(script_json(300, citations={(0, 0): [GOOD]}))
    llm.replies = [json.dumps(original)]
    llm.reader_replies = [json.dumps({"paragraphs": [{"id": "S1P1", "lost": ["开场在念资料目录"]}]})]
    llm.audit_replies = [fact_problem("原稿仍有事实疑点")] + [fact_problem("候选增加了无依据的效果")] * 4
    llm.editorial_replies = [edited("这个工具能让所有人效率翻倍。")]
    _login(client)
    shown = _make(client, "point_short", "编辑不能绕过事实复核")["version_shown"]
    assert not shown["checks"]["summary"]["passed"]
    assert not shown["meta"]["editorial_attempt"]["applied"] and not llm.compare_calls
    assert shown["sections"][0]["paragraphs"][0]["text"] == original["sections"][0]["paragraphs"][0]["text"]


def test_failed_joint_editing_can_still_repair_the_retained_original(client, video_env, research, llm):
    llm.replies = [script_json(300, citations={(0, 0): [GOOD]})]
    llm.reader_replies = [json.dumps({"paragraphs": [{"id": "S1P1", "lost": ["开场在念资料目录"]}]})]
    llm.audit_replies = [fact_problem("原稿用途写错"), fact_problem("候选有无依据的效果"), verified()]
    llm.editorial_replies = [edited("这个工具能让所有人效率翻倍。")]
    llm.revise_replies = ['{"paragraphs": []}', rewrite("每次运行的成本和耗时会出现在输出里。")]
    _login(client)
    shown = _make(client, "point_short", "候选失败仍修复保留稿")["version_shown"]
    assert not shown["meta"]["editorial_attempt"]["applied"] and not llm.compare_calls
    assert shown["checks"]["summary"]["passed"]
    assert shown["sections"][0]["paragraphs"][0]["text"] == "每次运行的成本和耗时会出现在输出里。"


def test_an_unverified_editorial_proposal_does_not_replace_a_checked_draft(client, video_env, research, llm):
    key = "point_short"
    original = json.loads(script_json(_roundup_budget(key) - 5, citations={(0, 0): [GOOD]}))
    llm.replies = [json.dumps(original)]
    llm.reader_replies = [json.dumps({"paragraphs": [{"id": "S1P1", "lost": ["开场太拖沓"]}]})]
    llm.editorial_replies = [edited("这个工具让所有人的效率翻倍。")]
    llm.audit_replies = ['{"issues": []}', fact_problem("把个别用法放大为所有人都翻倍")]
    _login(client)
    shown = _make(client, key, "更生动不等于允许误导")["version_shown"]
    assert shown["sections"][0]["paragraphs"][0]["text"] == original["sections"][0]["paragraphs"][0]["text"]
    assert shown["checks"]["summary"]["passed"]
    attempt = shown["meta"]["editorial_attempt"]
    assert not attempt["applied"] and attempt["sections"][0]["paragraphs"][0]["text"] == "这个工具让所有人的效率翻倍。"
    assert not attempt["checks"]["summary"]["passed"]
    assert not llm.compare_calls


def test_editorial_candidate_repairs_a_source_error_before_listening_comparison(client, video_env, research, llm):
    llm.replies = [script_json(300, citations={(0, 0): [GOOD]})]
    lost = json.dumps({"paragraphs": [{"id": "S1P1", "lost": ["开场念资料，缺少值得听的变化"]}]})
    llm.reader_replies = [lost, lost, '{"paragraphs": []}']
    llm.editorial_replies = [edited("跑一遍评测，成本和耗时都能少一半。")]
    llm.audit_replies = ['{"issues":[]}', fact_problem("无依据的减半结论"), verified("改为只说输出成本与耗时")]
    llm.revise_replies = [rewrite("跑一遍评测，花了多少、等了多久，现在可以直接比较。")]
    _login(client)
    shown = _make(client, "point_short", "编辑候选先修事实，再判断听感")["version_shown"]
    attempt = shown["meta"]["editorial_attempt"]
    assert attempt["applied"] and attempt["source_repair"]["checks"]["summary"]["passed"]
    assert len(llm.editorial_calls) == 1 and len(llm.revise_calls) == 1 and len(llm.audit_calls) == 3
    assert len(llm.compare_calls) == 1 and "少一半" not in llm.compare_calls[0][-1]["content"]
    assert shown["sections"][0]["paragraphs"][0]["text"].startswith("跑一遍评测，花了多少")
    assert shown["checks"]["rounds"] == 2  # one editorial proposal and one factual repair


def test_editorial_keeps_the_adopted_draft_when_later_attempts_are_worse(client, video_env, research, llm, monkeypatch):
    from core.video import editorial

    monkeypatch.setattr(editorial, "MAX_ROUNDS", 2)
    llm.replies = [script_json(300, citations={(0, 0): [GOOD]})]
    lost = json.dumps({"paragraphs": [{"id": "S1P1", "lost": ["转场仍有重复"]}]})
    llm.reader_replies = [lost, lost, lost]
    llm.editorial_replies = [edited("一轮评测的成本与耗时，可以直接比较。"), edited("评测有多种输出。")]
    llm.compare_replies = ['{"choice":"B","reason":"具体用途比资料目录更清楚"}',
                           '{"choice":"A","reason":"第二次改写反而丢失了具体变化"}']
    _login(client)
    shown = _make(client, "point_short", "继续打磨，但不覆盖更好的上一稿")["version_shown"]
    attempts = shown["meta"]["editorial_attempts"]
    assert [item["applied"] for item in attempts] == [True, False]
    assert "一轮评测的成本与耗时，可以直接比较" in llm.brief_calls[1][-1]["content"]
    assert len(llm.editorial_calls) == 2 and len(llm.compare_calls) == 2
    assert shown["sections"][0]["paragraphs"][0]["text"] == "一轮评测的成本与耗时，可以直接比较。"
    assert shown["meta"]["editorial_attempt"]["applied"] and shown["checks"]["editorial_attempted"]


def test_a_rejected_candidate_informs_another_attempt_without_replacing_the_original(client, video_env, research, llm):
    llm.replies = [script_json(300, citations={(0, 0): [GOOD]})]
    lost = json.dumps({"paragraphs": [{"id": "S1P1", "lost": ["开场还没讲到具体变化"]}]})
    llm.reader_replies = [lost, lost, '{"paragraphs": []}']
    llm.editorial_replies = [edited("这项更新值得大家关注。"), edited("一轮评测花了多少，现在能直接比较。")]
    llm.compare_replies = ['{"choice":"A","reason":"候选只有口号，失去了具体内容"}',
                           '{"choice":"B","reason":"从实际运行的花费展开，比资料清单更清楚"}']
    _login(client)
    shown = _make(client, "point_short", "首个候选失败后仍能自动找到更好的讲法")["version_shown"]
    assert [a["applied"] for a in shown["meta"]["editorial_attempts"]] == [False, True]
    assert "候选只有口号" in llm.brief_calls[1][-1]["content"]
    assert "候选只有口号" not in llm.editorial_calls[1][-1]["content"]
    assert shown["sections"][0]["paragraphs"][0]["text"] == "一轮评测花了多少，现在能直接比较。"


def test_repeated_editorial_feedback_has_a_resource_bound(client, video_env, research, llm, monkeypatch):
    from core.video import editorial

    monkeypatch.setattr(editorial, "MAX_ROUNDS", 2)
    llm.replies = [script_json(300, citations={(0, 0): [GOOD]})]
    lost = json.dumps({"paragraphs": [{"id": "S1P1", "lost": ["还可以把变化讲得更具体"]}]})
    llm.reader_replies = [lost] * 3
    llm.editorial_replies = [edited("先看一轮评测花了多少。"), edited("一轮评测花了多少，现在可以拿输出直接比较。")]
    _login(client)
    shown = _make(client, "point_short", "不把主编意见变成无限循环")["version_shown"]
    assert len(llm.editorial_calls) == 2 and len(shown["meta"]["editorial_attempts"]) == 2
    assert shown["checks"]["summary"]["passed"] and shown["checks"]["summary"]["lost"] == 1


def test_workflow_can_copyedit_a_sound_story_without_restarting_selection(client, video_env, research, llm):
    from core.video import editorial

    plan = {"angle": "运行的实际开销", "viewer_value": "比较成本与耗时", "throughline": "先看输出，再看用途", "facts": ["F1"]}
    llm.brief_replies = [json.dumps({**plan, "edit_mode": "copyedit", "edit_reason": "只需要接好开场两句话"})]
    llm.replies = [script_json(300, citations={(0, 0): [GOOD]})]
    llm.reader_replies = [json.dumps({"paragraphs": [{"id": "S1P1", "lost": ["开场两句话衔接不顺"]}]})]
    llm.editorial_replies = [edited("一轮评测跑完，成本和耗时都能直接比较。")]
    _login(client)
    shown = _make(client, "point_short", "局部修改不重启整篇发布介绍")["version_shown"]
    assert llm.editorial_calls[0][0]["content"] == editorial.COPYEDIT_PROMPT
    assert "## 当前口播" in llm.editorial_calls[0][-1]["content"]
    assert shown["meta"]["editorial_attempt"]["editorial_brief"]["edit_mode"] == "copyedit"
    assert shown["meta"]["editorial_attempt"]["applied"] and shown["checks"]["summary"]["passed"]


def test_a_fresh_editorial_proposal_needs_a_full_audit_without_artificial_pending_flags(client, video_env, research, llm):
    llm.replies = [script_json(300, citations={(0, 0): [GOOD]})]
    llm.reader_replies = [json.dumps({"paragraphs": [{"id": "S1P1", "lost": ["开场没有具体内容"]}]})]
    llm.editorial_replies = [edited("一次评测的成本与耗时，现在能在输出里看到。")]
    llm.audit_replies = ['{"issues":[]}', '{"issues":[]}']
    _login(client)
    shown = _make(client, "point_short", "编辑本身不构成事实疑点")["version_shown"]
    assert len(llm.audit_calls) == 2 and REPORT in llm.audit_calls[1][-1]["content"]
    assert shown["checks"]["summary"]["passed"] and shown["meta"]["editorial_attempt"]["applied"]


def test_editorial_authors_own_concrete_doubt_survives_an_empty_fact_audit(client, video_env, research, llm):
    original = json.loads(script_json(300, citations={(0, 0): [GOOD]}))
    llm.replies = [json.dumps(original)]
    llm.reader_replies = [json.dumps({"paragraphs": [{"id": "S1P1", "lost": ["主线不清"]}]})]
    candidate = json.loads(edited("一次评测的成本现在能输出。"))
    candidate["sections"][0]["paragraphs"][0].update(needs_review=True, review_reason="成本是否含全部请求尚未核对")
    llm.editorial_replies = [json.dumps(candidate)]
    llm.audit_replies = ['{"issues":[]}', '{"issues":[]}']
    _login(client)
    shown = _make(client, "point_short", "已有具体疑点不能用空结果消除")["version_shown"]
    assert shown["title"] == original["title"]
    assert not shown["meta"]["editorial_attempt"]["applied"]
    assert not llm.compare_calls


def test_editor_rejects_a_source_backed_but_worse_whole_script(client, video_env, research, llm):
    original = json.loads(script_json(300, citations={(0, 0): [GOOD]}))
    llm.replies = [json.dumps(original)]
    llm.style_replies = [json.dumps({"issues": [{"id": "S1P1", "rule": "S1", "severity": "一般",
                                              "problem": "开场信息堆砌", "editorial": True}]})]
    llm.editorial_replies = [edited("工具已经发布，值得关注。")]
    llm.audit_replies = ['{"issues":[]}', verified()]
    llm.compare_replies = ['{"choice":"A","reason":"B 变成空泛表态，丢失具体用途","remaining":["开场仍有堆砌"]}']
    _login(client)
    shown = _make(client, "point_short", "不能只靠事实通过选择新稿")["version_shown"]
    assert shown["title"] == original["title"] and len(shown["sections"]) == 4
    assert shown["checks"]["summary"]["passed"]
    assert not shown["meta"]["editorial_attempt"]["applied"]
    assert shown["meta"]["editorial_attempt"]["decision"]["choice"] == "A"


def test_automatic_material_selection_reaches_writer_without_manual_brief(llm):
    llm.replies = [script_json(300, citations={(0, 0): [GOOD]})]
    written = _write()
    assert len(llm.calls) == 1 and not llm.brief_calls and not llm.fact_calls
    assert REPORT in llm.calls[0][-1]["content"]
    assert written.meta["generation"] == "goal_first" and not written.meta.get("editorial_brief")


def test_editor_reorders_the_whole_script_and_verifies_new_positions(client, video_env, research, llm):
    llm.replies = [script_json(300, citations={(0, 0): [GOOD]})]
    llm.reader_replies = [json.dumps({"paragraphs": [{"id": "S3P2", "lost": ["前面履历太长，直到这里才知道变化"]}]})]
    llm.editorial_replies = [json.dumps({"title": "先看运行花了多少", "sections": [
        {"title": "变化", "paragraphs": [{"text": "现在一次评测花了多少、跑了多久，都能在输出里看到。", "citations": [GOOD]}]},
        {"title": "用途", "paragraphs": [{"text": "比较模型时，可以把这些运行信息放在一起看。", "citations": [GOOD]}]},
    ]})]
    proof = json.loads(verified())
    proof["verifications"].append({"id": "S2P1", "status": "verified", "reason": "报告说明这些运行指标可供比较", "sources": [GOOD]})
    llm.audit_replies = ['{"issues":[]}', json.dumps(proof)]
    _login(client)
    shown = _make(client, "point_short", "改变整个讲述顺序")["version_shown"]
    assert shown["title"] == "先看运行花了多少" and len(shown["sections"]) == 2
    assert shown["checks"]["summary"]["passed"]
    assert all(p["evidence"] == "CITED" for s in shown["sections"] for p in s["paragraphs"])
    assert len(llm.compare_calls) == 1 and len(llm.audit_calls) == 2
    assert shown["meta"]["editorial_attempt"]["mode"] == "whole_script"


def test_failed_editorial_comparison_keeps_original_and_attempt(client, video_env, research, llm):
    original = json.loads(script_json(300, citations={(0, 0): [GOOD]}))
    llm.replies = [json.dumps(original)]
    llm.reader_replies = [json.dumps({"paragraphs": [{"id": "S1P1", "lost": ["开场太绕"]}]})]
    llm.editorial_replies = [edited("先看一次评测输出的成本与耗时。")]
    llm.audit_replies = ['{"issues":[]}', verified()]
    llm.compare_replies = ["invalid"] * 3
    _login(client)
    shown = _make(client, "point_short", "比较失败不能默认采用新稿")["version_shown"]
    assert shown["title"] == original["title"] and len(shown["sections"]) == 4
    attempt = shown["meta"]["editorial_attempt"]
    assert not attempt["applied"] and attempt["sections"][0]["paragraphs"][0]["text"].startswith("先看一次")
    assert "编辑比较没有完成" in attempt["reason"]


def test_long_source_backed_claims_are_not_compressed_to_a_length_budget(llm):
    initial = json.loads(script_json(3000, citations={(0, 0): [GOOD]}))
    initial["sections"][0]["paragraphs"][0]["text"] = ORIGINAL + initial["sections"][0]["paragraphs"][0]["text"]
    shortened = json.loads(script_json(1548, citations={(0, 0): [GOOD]}))
    shortened["sections"][0]["paragraphs"][0]["text"] = SHORTENED
    llm.replies = [json.dumps(initial), json.dumps(shortened)]
    written = _write(report=REPORT + "\n" + ORIGINAL + "\n" + SOURCE)
    assert ORIGINAL in written.sections[0]["paragraphs"][0]["text"]
    assert written.length_check["status"] == "OVER"
    assert len(llm.calls) == 1
