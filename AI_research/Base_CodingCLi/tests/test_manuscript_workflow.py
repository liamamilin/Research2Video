"""The reader gets a fresh context and a failed revision preserves the first draft."""
from hashlib import sha256
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.video import manuscript_workflow as workflow, writer


@pytest.mark.parametrize("source", ["matrix", "knowledge", "report"])
def test_manual_reports_use_their_own_job_profile(monkeypatch, source):
    from core.video import reports

    job = "ai_media/ai_tool_tutorials"
    monkeypatch.setattr(reports, "get", lambda report_id: {"job": job} if report_id == "report-1" else None)
    topic = {"report_id": "report-1"}
    if source == "matrix":
        topic["matrix_dedup"] = {"job": job}
    if source == "knowledge":
        topic["knowledge_job"] = job
    profile = workflow.profile_for({"topic": topic}, "config", "jobs", {"manuscript_jobs": [job]})
    assert profile is workflow.JOB_PROMPTS[job]


def test_binding_specific_profile_matches_only_its_manual_individual_recipe(monkeypatch):
    from core.video import production

    job = "monitoring/ai_flash_discovery"
    monkeypatch.setattr(production, "get", lambda *_: SimpleNamespace(usable=True, recipe="flash", job=job))
    topic = {"from_report": "output/flash/report.md", "matrix_dedup": {"job": job}}
    config = {"manuscript_jobs": [job]}
    assert workflow.profile_for({"topic": topic, "column_key": "flash"}, "config", "jobs", config)
    assert not workflow.profile_for({"topic": topic, "column_key": "point_short"}, "config", "jobs", config)
    assert not workflow.profile_for({"topic": topic, "production": "flash_full"}, "config", "jobs", config)


def test_rollout_requires_the_actual_job_author_prompt_and_revision_evidence():
    acceptance = (Path(__file__).resolve().parents[1] / "spec/acceptance/workflow-polish-20261112"
                  / "check_tutorial_rollout.py")
    spec = importlib.util.spec_from_file_location("check_tutorial_rollout", acceptance)
    check = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(check)
    profile = next(profile for profile in workflow.JOB_PROMPTS.values() if profile.episode_prompt)
    topic = {"content_mode": "episode", "next_episode": "下一期第一条"}
    system = writer.author_system_prompt(topic, {}, episode_prompt=profile.episode_prompt,
                                         episode_lead=profile.episode_lead)
    other_system = writer.author_system_prompt(topic, {})
    assert system != other_system
    item = {
        "_topic": topic, "_context": {}, "_creative_direction": profile.draft,
        "author_system_prompt_sha256": sha256(other_system.encode()).hexdigest(),
        "reader_prompt_sha256": sha256(profile.read.encode()).hexdigest(),
        "revision_prompt_sha256": None, "revision": "draft", "matches_configured_profile": True,
        "has_independent_read": True,
    }
    check.verify_prompts(item, profile, {})
    assert not check.verified_first_draft(item)
    item["author_system_prompt_sha256"] = sha256(system.encode()).hexdigest()
    check.verify_prompts(item, profile, {})
    assert check.verified_first_draft(item)
    item["revision"] = "revised"
    check.verify_prompts(item, profile, {})
    assert not check.verified_first_draft(item)


def _draft(text):
    return writer.WrittenScript("标题", [{"title": "开场", "paragraphs": [{"text": text}]}], {}, {})


def test_independent_reader_and_blind_comparison(monkeypatch):
    first, revised = _draft("第一稿的观点。"), _draft("改稿的观点。")
    drafts = iter((first, revised))
    writer_inputs = []

    def write(**kwargs):
        writer_inputs.append(kwargs)
        return next(drafts)

    def chat(client, prompt, request, schema, *, temperature):
        if prompt == workflow.PLAN:
            return schema(considered=["方向一", "方向二"], angle="编辑选的方向",
                          viewer_value="观众价值", details=[], leave_out=[])
        if prompt == workflow.READ:
            assert "编辑选的方向" not in request and "第一稿的观点" in request
            return schema(takeaway="读者理解", keep_quote="第一稿的观点", revision="补充结论为什么成立")
        assert "读者理解" not in request and "改稿的观点" in request
        assert prompt.startswith(workflow.RANK)
        return schema(order=[["2"], ["1"]], readings={
            label: {"takeaway": "判断", "quote": "观点", "reason": "理由"} for label in ("1", "2")
        })

    monkeypatch.setattr(workflow, "LLMClient", lambda *a, **kw: object())
    monkeypatch.setattr(workflow, "call_json", chat)
    monkeypatch.setattr(writer, "write_for_column", write)
    result = workflow.author(report="材料", topic={}, llm_config=None,
                             column=SimpleNamespace(script_style="风格", target_minutes=3))
    assert result is revised
    assert "编辑选的方向" in writer_inputs[0]["editorial_context"]
    assert "补充结论为什么成立" in writer_inputs[1]["editorial_context"]
    assert result.meta["manuscript_workflow"]["before_revision"]["sections"] == first.sections


def test_unusable_revision_leaves_the_planned_draft(monkeypatch):
    first = _draft("可以交付的初稿。")
    written = iter((first,))

    def write(**kwargs):
        try:
            return next(written)
        except StopIteration:
            raise writer.ScriptError("修订返回不完整") from None

    def chat(client, prompt, request, schema, *, temperature):
        if prompt == workflow.PLAN:
            return schema(considered=[], angle="角度", viewer_value="价值", details=[], leave_out=[])
        assert prompt == workflow.READ
        return schema(takeaway="主线", keep_quote="初稿", revision="写清转折")

    monkeypatch.setattr(workflow, "LLMClient", lambda *a, **kw: object())
    monkeypatch.setattr(workflow, "call_json", chat)
    monkeypatch.setattr(writer, "write_for_column", write)
    result = workflow.author(report="材料", topic={}, llm_config=None,
                             column=SimpleNamespace(script_style="风格", target_minutes=3))
    assert result is first
    assert result.meta["manuscript_workflow"]["error"]["stage"] == "revision"


@pytest.mark.parametrize("reply", ["targeted_edit", "unrequested_edit", "no_edit"])
def test_focused_author_returns_one_manuscript_without_ranking(monkeypatch, reply):
    draft = _draft("需要改的开头。")
    body = {"title": "论证", "paragraphs": [{"text": "保留这个推论。", "citations": ["https://example.com"]}]}
    draft.sections.append(body)
    target = workflow.pid(0, 0)

    def chat(client, prompt, request, schema, *, temperature):
        if prompt == workflow.FOCUSED_READ:
            assert "原始素材" not in request and "保留这个推论" in request
            return schema(takeaway="判断", keep_quote="保留这个推论", revision="讲清开头",
                          targets=[] if reply == "no_edit" else [target])
        assert prompt == workflow.FOCUSED_WRITE
        assert reply != "no_edit"
        assert "原始素材" in request and "讲清开头" in request
        replacement = target if reply == "targeted_edit" else workflow.pid(1, 0)
        return schema(paragraphs=[{"id": replacement, "text": "观众现在知道了这期为什么值得听。"}])

    def rank(*args, **kwargs):
        pytest.fail("Ranking belongs in trial evaluation")

    monkeypatch.setattr(writer, "write_for_column", lambda **kwargs: draft)
    monkeypatch.setattr(workflow, "LLMClient", lambda *a, **kw: object())
    monkeypatch.setattr(workflow, "call_json", chat)
    monkeypatch.setattr(workflow, "rank", rank)
    result = workflow.focused_author(
        report="研究底稿", original_material="原始素材", topic={}, llm_config=None,
        column=SimpleNamespace(script_style="风格", target_minutes=3, voice_id=None),
    )
    assert result.title == draft.title and result.sections[1] == body
    assert draft.sections[0]["paragraphs"][0]["text"] == "需要改的开头。"
    if reply == "targeted_edit":
        assert result.sections[0]["paragraphs"][0]["text"] == "观众现在知道了这期为什么值得听。"
        assert result.meta["manuscript_workflow"]["result"] == "revised"
    else:
        assert result is draft and result.meta["manuscript_workflow"]["result"] == "draft"


def test_revision_keeps_a_connected_introduction_and_applies_the_body_edit(monkeypatch):
    draft = _draft("这份报告敢直接交吗？大家好，我是洛洛，今天就来看看怎么验。")
    draft.sections.append({"title": "例子", "paragraphs": [{"text": "先核对报告。"}]})
    monkeypatch.setattr(workflow.persona, "load", lambda: {"name": "洛洛", "introduce": "我是洛洛。"})
    monkeypatch.setattr(workflow, "LLMClient", lambda *args, **kwargs: object())

    def chat(client, prompt, request, schema, *, temperature):
        if prompt == workflow.FOCUSED_READ:
            return schema(takeaway="能核验再交付", keep_quote="这份报告敢直接交吗",
                          revision="收紧开头并讲清怎么核对", targets=["S1P1", "S2P1"])
        assert prompt == workflow.FOCUSED_WRITE
        return schema(paragraphs=[
            {"id": "S1P1", "text": "这份报告敢直接交吗？我是洛洛。"},
            {"id": "S2P1", "text": "先点开引用，再看它能不能支持那句话。"},
        ])

    monkeypatch.setattr(workflow, "call_json", chat)
    result = workflow.revise_passage(
        draft, report="研究底稿", topic={}, llm_config=None,
        column=SimpleNamespace(script_style="风格", target_minutes=3, voice_id=None),
    )

    assert result.sections[0] == draft.sections[0]
    assert result.sections[1]["paragraphs"][0]["text"] == "先点开引用，再看它能不能支持那句话。"
    receipt = result.meta["manuscript_workflow"]
    assert receipt["retained_opening"] == "revision_broke_introduction"
    assert [change["id"] for change in receipt["changes"]] == ["S2P1"]
