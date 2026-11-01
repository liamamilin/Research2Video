"""Unattended source pictures share the normal render path and never weaken its submit ledger."""
import copy
import hashlib
from io import BytesIO
from uuid import uuid4
from datetime import datetime, timezone

import httpx
import pytest
from PIL import Image

from core.video import screenshots, store, visual_materials as visuals
from tests.test_video_scripts import video_env, FakeAutoVideo  # noqa: F401
from web.services import video_render, video_visuals

URL = "https://example.com/docs/screenshots"


def png():
    buf = BytesIO()
    Image.new("RGB", (800, 500), "navy").save(buf, "PNG")
    return buf.getvalue()


def script():
    row = store.create_script("deep_dive", {"title": "Acme 截图功能", "fields": {}})
    sections = [{"title": "了解功能", "paragraphs": [
        {"text": "先了解适用范围。", "citations": [], "evidence": "UNCITED"},
        {"text": "Acme 支持网页截图功能。", "citations": [URL], "evidence": "CITED"},
        {"text": "最后归纳本期重点。", "citations": [], "evidence": "UNCITED"}]}]
    store.add_version(row["id"], title="Acme 截图功能", sections=sections, length_check={}, edit_type="EDITED")
    store.update_script(row["id"], status="APPROVED", approved_version=1)
    return store.get_script(row["id"])


class Engine(FakeAutoVideo):
    def __init__(self):
        super().__init__()
        self.uploads = 0

    def handle(self, request):
        if request.url.path == "/api/creative-images":
            self.uploads += 1
            return httpx.Response(201, json={"image_id": str(uuid4()), "sha256": hashlib.sha256(png()).hexdigest()})
        return super().handle(request)


@pytest.fixture
def enabled(video_env, monkeypatch):
    engine = Engine()
    monkeypatch.setattr(video_render, "get_client", engine.client)
    monkeypatch.setattr(video_render, "_video_config", lambda: {"visual_materials": {"enabled": True, "version": 1}})
    acquired = []

    def capture(need, url, policy, **kwargs):
        acquired.append(url)
        return png(), {"url": url, "final_url": url, "title": "Acme 功能说明", "captured_at": datetime.now(timezone.utc).isoformat(),
                       "mode": "viewport", "selector": "", "clip": None}

    monkeypatch.setattr(video_visuals, "acquire", capture)
    return engine, acquired


def test_normal_render_automatically_captures_registers_maps_and_freezes_before_submit(enabled):
    engine, acquired = enabled
    row = script()
    render = video_render.create(row)
    assert acquired == [] and render["visuals"]["state"] == "pending"
    original = copy.deepcopy(render["request"])
    assert original["visual_storytelling"] is True
    assert video_render.process(render["id"]) == "COMPLETED"
    assert acquired == [URL] and engine.uploads == 1
    submitted = engine.posts[0]
    assert submitted["video_form"] == "object_showcase"
    assert submitted["visual_storytelling"] is True
    assert submitted["image_materials"][0]["placements"] == [{"chapter_index": 0, "paragraph_index": 1}]
    saved = store.get_render(render["id"])
    assert saved["request"] == original
    assert saved["submission_request"] == submitted
    assert saved["visuals"]["state"] == "completed"


def test_capture_failure_downgrades_without_holding_or_retrying_the_video(enabled, monkeypatch):
    engine, acquired = enabled
    monkeypatch.setattr(video_visuals, "acquire", lambda *a, **k: (_ for _ in ()).throw(screenshots.ScreenshotError("验证码页面")))
    render = video_render.create(script())
    assert video_render.process(render["id"]) == "COMPLETED"
    assert not engine.uploads and "image_materials" not in engine.posts[0]
    saved = store.get_render(render["id"])
    assert saved["visuals"]["state"] == "degraded"
    assert "验证码" in saved["visuals"]["decisions"][0]["reason"]


@pytest.mark.parametrize('version', [1, 2, 3])
@pytest.mark.parametrize('required', [False, True])
def test_configured_picture_understanding_runs_without_an_explicit_v3_or_ocr_flag(enabled, monkeypatch, version, required):
    engine, _ = enabled
    monkeypatch.setattr(video_render, '_video_config', lambda: {'visual_materials': {
        'enabled': True, 'version': version, 'vision_section': 'vision_ai', 'vision_required': required}})
    checked = []
    def verify(raw, need, metadata, policy, **kwargs):
        checked.append(need['id'])
        return {'vision': {'state': 'passed', 'paragraph_ids': [need['id']]}}
    monkeypatch.setattr(video_visuals.image_quality, 'verify', verify)
    render = video_render.create(script())
    assert video_render.process(render['id']) == 'COMPLETED'
    assert checked == ['c1p2']


def test_lost_submit_reconciles_frozen_images_without_recapture(enabled):
    engine, acquired = enabled
    engine.drop_create_response = True
    render = video_render.create(script())
    assert video_render.process(render["id"]) == "COMPLETED"
    assert len(engine.posts) == engine.uploads == len(acquired) == 1
    assert video_render.process(render["id"]) == "COMPLETED"
    assert len(acquired) == 1


def test_explicit_empty_manual_choice_skips_and_fast_mixed_mode_acquires(enabled):
    engine, acquired = enabled
    row = script()
    version = store.get_version(row["id"], 1)
    screenshots.select(row["id"], version, "object_showcase", [], engine.client().base_url)
    render = video_render.create(row)
    assert render["visuals"]["state"] == "manual"
    assert video_render.process(render["id"]) == "COMPLETED"
    other = script()
    engine.statuses = ["COMPLETED"]
    fast = video_render.create(other, production_mode="fast")
    assert fast["visuals"]["state"] == "pending"
    assert video_render.process(fast["id"]) == "COMPLETED"
    assert acquired == [URL]
    assert engine.posts[-1]['production_mode'] == 'fast'
    assert engine.posts[-1]['video_form'] == 'information_cards'
    assert engine.posts[-1]['visual_storytelling'] is True
    assert engine.posts[-1]['image_materials'][0]['placements'] == [{'chapter_index': 0, 'paragraph_index': 1}]


def test_matrix_fast_setting_keeps_its_executor_when_automatic_images_arrive():
    request = {'visual_storytelling': True, 'chapters': [{'title': '功能', 'paragraphs': ['页面功能。']}],
               'production_context': {'matrix': {'id': 'app-check', 'settings': {
                   'production_mode': 'fast', 'video_form': 'information_cards'}}}}
    selected = {'video_form': 'object_showcase', 'production_mode': 'standard',
                'image_materials': [{'image_id': 'existing', 'placements': [{'chapter_index': 0, 'paragraph_index': 0}]}]}
    result = visuals.attach(copy.deepcopy(request), selected)
    assert result['production_mode'] == 'fast' and result['video_form'] == 'information_cards'
    assert result['visual_style'] == 'editorial_light'
    assert result['image_materials'] == selected['image_materials']
    assert not visuals.fast_visuals({**request, 'visual_storytelling': False})


def test_recipe_keeps_its_executor_when_saved_or_automatic_pictures_arrive():
    request = {'production_recipe': 'mixed_tutorial', 'visual_storytelling': True,
               'chapters': [{'title': '转换', 'paragraphs': ['运行实际命令。']}]}
    selected = {'video_form': 'object_showcase', 'production_mode': 'standard',
                'image_materials': [{'image_id': 'input', 'placements': [{'chapter_index': 0, 'paragraph_index': 0}]}]}
    result = visuals.attach(copy.deepcopy(request), selected)
    assert result == {**request, 'image_materials': selected['image_materials']}
    assert selected['video_form'] == 'object_showcase'
    explicit = visuals.attach({**request, 'production_mode': 'standard', 'video_form': 'information_motion'}, selected)
    assert explicit['production_mode'] == 'standard' and explicit['video_form'] == 'information_motion'


def test_snapshot_tampering_is_refused_before_network_submit(enabled):
    engine, _ = enabled
    render = video_render.create(script())
    frozen = video_visuals.freeze(render, engine.client())
    payload = copy.deepcopy(frozen["submission_request"])
    payload["chapters"][0]["paragraphs"][0] = "被改变的讲稿"
    store.update_render(render["id"], submission_request=payload)
    assert video_render.process(render["id"]) == "FAILED"
    assert not engine.posts


def test_visual_selection_uses_supplied_links_without_rechecking_fact_confidence(video_env):
    row = script()
    version = store.get_version(row["id"], 1)
    store.save_fact_sheet("sheet", report_path="", sheet={"facts": [
        {"id": "F1", "claim": "Acme 支持截图功能", "sources": [URL], "confidence": "厂商自述"},
        {"id": "F2", "claim": "推导成本", "sources": [URL], "confidence": "推导", "derived_from": ["F1"]}]})
    version["meta"]["fact_sheet_id"] = "sheet"
    paragraph = version["sections"][0]["paragraphs"][1]
    paragraph.update(facts=["F1"], citations=["https://unrelated.example.com"])
    assert visuals.needs_for(version)[0]["urls"] == [URL]
    paragraph.update(facts=["F2"])
    assert visuals.needs_for(version)[0]["urls"] == [URL]
    paragraph.update(facts=["F1"], evidence="NEEDS_REVIEW")
    assert visuals.needs_for(version)[0]["urls"] == [URL]


def test_article_example_links_can_supply_uncited_paragraph_visuals_without_keywords(video_env):
    version = {"sections": [{"title": "实际用法", "paragraphs": [
        {"text": "MarkItDown 把这个文件变成 Markdown，先看输入与输出。", "citations": []},
        {"text": "最后总结这期内容。", "citations": []}]}]}
    linked = [{"title": "MarkItDown README", "url": "https://github.com/microsoft/markitdown"}]
    needs = visuals.needs_for(version, linked)
    assert len(needs) == 1 and needs[0]["paragraph_index"] == 0
    assert not needs[0].get("visual_subject")
    assert "输入" in needs[0]["visual_goal"]


def test_automatic_intent_does_not_infer_a_visual_object_from_title_or_publisher(video_env):
    version = {'sections': [
        {'title': 'Rosalind：访问范围', 'paragraphs': [{'text': 'OpenAI 发布 GPT-6 Rosalind，用于生命科学研究。',
            'citations': ['https://developers.openai.com/api/docs/changelog.md']}]},
        {'title': 'trust_remote_code 的影响', 'paragraphs': [{'text': 'Hugging Face 模型的 trust_remote_code 会执行作者的代码。',
            'citations': ['https://devbrief.dev/ai-security/2026-10-30/']}]}]}
    needs = visuals.needs_for(version)
    assert all(not n.get('visual_subject') for n in needs)
    from core.video.visual_matching import compatible
    unrelated = {'text': 'OpenAI product → OpenAI tunnel service → tunnel-client', 'visual_kind': 'image'}
    assert not compatible(needs[0], unrelated, 'Secure MCP Tunnel | OpenAI API')[0]


def test_publisher_domain_does_not_add_other_product_links(video_env):
    url = 'https://developers.openai.com/api/docs/changelog.md'
    version = {'sections': [
        {'title': '三条更新，三种用法', 'paragraphs': [{'text': '这一期涉及生命科学研究、跑 MCP 工具和团队试点。', 'citations': [url]}]},
        {'title': 'Rosalind 访问范围', 'paragraphs': [{'text': 'OpenAI 发布 Rosalind，用于生命科学研究。', 'citations': [url]}]}]}
    unrelated = {'title': 'Secure MCP Tunnel', 'url': 'https://developers.openai.com/api/docs/guides/secure-mcp-tunnels'}
    needs = visuals.needs_for(version, [unrelated])
    assert len(needs) == 2
    assert needs[1]['urls'] == [url]
    version['sections'][0]['title'] = 'MCP 工具调用'
    assert not visuals.needs_for(version)[0].get('visual_subject')


def test_inline_product_example_survives_a_separate_news_bibliography(video_env):
    from core.video.sources import visual_sources
    material = ('查看 [MarkItDown 示例](https://github.com/microsoft/markitdown)。\n\n'
                '## 参考来源\n1. 新闻报道 | 二手 | 持续更新 | https://news.example.org/story\n')
    linked = visual_sources(material)
    assert {s['url'] for s in linked} == {'https://github.com/microsoft/markitdown', 'https://news.example.org/story'}
    version = {'sections': [{'title': '用法', 'paragraphs': [{'text': 'MarkItDown 的输入和输出示例。', 'citations': []}]}]}
    assert visuals.needs_for(version, linked)[0]['urls'] == ['https://github.com/microsoft/markitdown']


def test_repeated_source_does_not_consume_all_distinct_page_slots(enabled, monkeypatch):
    engine, _ = enabled
    render = video_render.create(script())
    audit = render["visuals"]
    first = audit["needs"][0]
    audit["needs"] = [first, {**first, "id": "c1p3", "paragraph_index": 2, "urls": ["https://example.org/other"]}]
    audit["pages"] = [{"need_id": "earlier", "url": URL, "state": "failed"}]
    audit["policy"]["max_pages"] = 2
    calls = []
    def reject(need, url, policy, **kwargs):
        calls.append(url)
        raise screenshots.ScreenshotError("没有合适的区域")
    monkeypatch.setattr(video_visuals, "acquire", reject)
    store.update_render(render["id"], visuals=audit)
    video_visuals.prepare(store.get_render(render["id"]), engine.client())
    assert calls == [URL, "https://example.org/other"]


def test_each_paragraph_needs_its_own_picture_match_even_for_identical_subjects(enabled, monkeypatch):
    engine, acquired = enabled
    render = video_render.create(script())
    audit = render["visuals"]
    first = {**audit["needs"][0], 'visual_subject': 'acme'}
    audit["needs"] = [first, {**first, "id": "c1p3", "paragraph_index": 2}]
    audit["policy"]["version"] = 3
    store.update_render(render["id"], visuals=audit)
    monkeypatch.setattr(video_visuals.image_quality, "verify", lambda *a, **k: {
        "ocr_lines": 2, "vision": {"state": "passed", "paragraph_ids": ["c1p3"], "summary": "对应后面的功能示例"}})
    prepared, final = video_visuals.prepare(store.get_render(render["id"]), engine.client())
    assert prepared["image_materials"][0]["placements"] == [{"chapter_index": 0, "paragraph_index": 2}]
    assert acquired == [URL, URL]
    assert {d["need_id"]: d["status"] for d in final["decisions"]} == {"c1p2": "text", "c1p3": "accepted"}


def test_old_image_decision_cannot_place_a_picture_in_a_different_product_story(enabled, monkeypatch):
    engine, _ = enabled
    render = video_render.create(script())
    audit = render['visuals']
    first = {**audit['needs'][0], 'visual_subject': 'mcp'}
    audit['needs'] = [first, {**first, 'id': 'c1p3', 'paragraph_index': 2, 'visual_subject': 'rosalind', 'urls': []}]
    audit['policy']['version'] = 3
    store.update_render(render['id'], visuals=audit)
    monkeypatch.setattr(video_visuals.image_quality, 'verify', lambda *a, **k: {
        'ocr_lines': 2, 'vision': {'state': 'passed', 'paragraph_ids': ['c1p3'], 'summary': 'MCP diagram'}})
    prepared, final = video_visuals.prepare(store.get_render(render['id']), engine.client())
    assert not prepared.get('image_materials')
    assert all(row['status'] == 'text' for row in final['decisions'])


def test_completed_preparation_survives_restart_before_freezing_submission(enabled):
    engine, acquired = enabled
    render = video_render.create(script())
    prepared, audit = video_visuals.prepare(render, engine.client())
    assert prepared["image_materials"] and audit["state"] == "completed"
    frozen = video_visuals.freeze(store.get_render(render["id"]), engine.client())
    assert frozen["submission_request"] == prepared
    assert len(acquired) == engine.uploads == 1


def test_partial_capture_resumes_registration_even_when_page_budget_was_consumed(enabled, monkeypatch):
    engine, acquired = enabled
    render = video_render.create(script())
    audit = render["visuals"]
    audit["policy"]["max_pages"] = 1
    store.update_render(render["id"], visuals=audit)
    original = screenshots.register
    monkeypatch.setattr(screenshots, "register", lambda *a, **k: (_ for _ in ()).throw(SystemExit("simulated process exit")))
    with pytest.raises(SystemExit):
        video_visuals.prepare(store.get_render(render["id"]), engine.client())
    monkeypatch.setattr(screenshots, "register", original)
    resumed = video_visuals.freeze(store.get_render(render["id"]), engine.client())
    assert resumed["submission_request"]["image_materials"]
    assert len(acquired) == engine.uploads == 1


def test_cache_reuses_verified_bytes_for_another_render_and_reregisters_for_current_engine(enabled):
    engine, acquired = enabled
    first = video_render.create(script())
    assert video_render.process(first["id"]) == "COMPLETED"
    engine.statuses = ["COMPLETED"]
    second = video_render.create(script())
    assert video_render.process(second["id"]) == "COMPLETED"
    assert len(acquired) == 1 and engine.uploads == 2
    assert store.get_render(second["id"])["visuals"]["decisions"][0]["cached"]


def test_autopilot_uses_the_same_automatic_preparation_without_user_action(enabled, monkeypatch):
    from web.services import video_executor
    engine, acquired = enabled
    monkeypatch.setattr(video_render, "start", lambda render_id: video_render.process(render_id))
    outcome = video_executor.advance_auto(script(), {}, can_start_script=True, can_start_render=True)
    assert outcome["took"]["render"] and not outcome["hold"]
    assert acquired == [URL] and engine.posts[0]["image_materials"]


def test_deduplicated_picture_can_cover_multiple_paragraphs_without_duplicate_uuid(enabled):
    engine, acquired = enabled
    row = script()
    version = store.get_version(row["id"], 1)
    version["sections"][0]["paragraphs"][2] = {"text": "Acme 支持页面截图功能。", "citations": [URL], "evidence": "CITED"}
    store.update_version_audit(row["id"], 1, sections=version["sections"], meta=version["meta"])
    render = video_render.create(store.get_script(row["id"]))
    assert video_render.process(render["id"]) == "COMPLETED"
    images = engine.posts[0]["image_materials"]
    assert len(images) == engine.uploads == 1 and len(acquired) == 2
    assert images[0]["placements"] == [{"chapter_index": 0, "paragraph_index": 1}, {"chapter_index": 0, "paragraph_index": 2}]


def test_new_version_recomputes_placements_while_reusing_valid_source_bytes(enabled):
    engine, acquired = enabled
    row = script()
    first = video_render.create(row)
    assert video_render.process(first["id"]) == "COMPLETED"
    version = store.get_version(row["id"], 1)
    version["sections"][0]["paragraphs"].reverse()
    # Move the sourced paragraph to a different position, rather than inherit its old position.
    p = version["sections"][0]["paragraphs"].pop(1)
    version["sections"][0]["paragraphs"].append(p)
    store.add_version(row["id"], title=version["title"], sections=version["sections"], length_check={}, edit_type="EDITED")
    store.update_script(row["id"], status="APPROVED", approved_version=2)
    engine.statuses = ["COMPLETED"]
    second = video_render.create(store.get_script(row["id"]))
    assert video_render.process(second["id"]) == "COMPLETED"
    assert len(acquired) == 1
    assert engine.posts[0]["image_materials"][0]["placements"][0]["paragraph_index"] == 1
    assert engine.posts[1]["image_materials"][0]["placements"][0]["paragraph_index"] == 2


def test_page_budget_and_failed_first_source_do_not_block_render(enabled, monkeypatch):
    engine, acquired = enabled
    row = script()
    render = video_render.create(row)
    audit = render["visuals"]
    audit["policy"]["max_pages"] = 1
    audit["needs"][0]["urls"] = ["https://example.com/fail", URL]
    store.update_render(render["id"], visuals=audit)
    calls = []
    def reject(*args, **kwargs):
        calls.append(args[1]); raise screenshots.ScreenshotError("页面不可用")
    monkeypatch.setattr(video_visuals, "acquire", reject)
    assert video_render.process(render["id"]) == "COMPLETED"
    assert len(calls) == 1 and not engine.uploads
    assert store.get_render(render["id"])["visuals"]["state"] == "degraded"


def test_api_exposes_automatic_selection_and_explicit_manual_opt_out(enabled, client):
    from tests.test_video_scripts import _login, _csrf
    engine, _ = enabled
    row = script()
    render = video_render.create(row)
    assert video_render.process(render["id"]) == "COMPLETED"
    _login(client, "editor", "editor-pass-123")
    data = client.get(f"/api/video/scripts/{row['id']}/screenshots?version=1").json()
    assert data["automatic"]["state"] == "completed" and data["selection"]["origin"] == "automatic"
    assert data["selection"]["materials"][0]["placements"] == [{"chapter_index": 0, "paragraph_index": 1}]
    saved = client.put(f"/api/video/scripts/{row['id']}/image-materials", json={"version": 1, "materials": []}, headers=_csrf(client))
    assert saved.status_code == 200
    next_render = video_render.create(row)
    assert next_render["visuals"]["state"] == "manual"


@pytest.mark.parametrize("production", ["history_weekly", "science_daily", "flash_daily", "legacy_column"])
def test_migrated_domain_picture_goal_receives_frozen_column_presentation(video_env, production):
    context = {"presentation": {"preferred_visuals": ["档案图片", "关系图"],
                                "strategy": "让书页细节、当前人物与回信改变的处境一起可读。"}}
    row = store.create_script("deep_dive", {"title": "一封回信改变了什么"}, production=production,
                              content_context=context)
    sections = [{"title": "回信", "paragraphs": [
        {"text": "她可以用回信中的论证推进自己学习与写作的空间。", "citations": [URL]}]}]
    store.add_version(row["id"], title="一封回信改变了什么", sections=sections, length_check={}, edit_type="EDITED")
    row = store.get_script(row["id"])
    version = store.get_version(row["id"], 1)
    request = {"chapters": [{"title": "回信", "paragraphs": [sections[0]["paragraphs"][0]["text"]]}]}
    original = copy.deepcopy(request)
    without_direction = {**row, "content_context": {}}
    baseline = visuals.intent(without_direction, version, request, {"enabled": True})
    audit = visuals.intent(row, version, request, {"enabled": True})
    if production in {"history_weekly", "science_daily"}:
        goal = audit["needs"][0]["visual_goal"]
        assert context["presentation"]["strategy"] in goal
        assert all(item in goal for item in context["presentation"]["preferred_visuals"])
        assert "产品界面" not in goal
        assert audit["chapter_hash"] == baseline["chapter_hash"]
        assert audit["policy"] == baseline["policy"]
        assert [{k: v for k, v in n.items() if k != "visual_goal"} for n in audit["needs"]] == [
            {k: v for k, v in n.items() if k != "visual_goal"} for n in baseline["needs"]]
    else:
        assert audit == baseline
    assert request == original
    assert store.get_script(row["id"])["content_context"] == context
