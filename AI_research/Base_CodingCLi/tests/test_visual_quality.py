import json
from io import BytesIO

import pytest
from PIL import Image, ImageDraw

from core.llm import ChatResponse
from core.video import image_quality, screenshots, visual_materials
from web.services import video_visual_acquire as acquire


def pixels(blank=False):
    image = Image.new("RGB", (800, 400), "white")
    if not blank:
        draw = ImageDraw.Draw(image)
        for y in range(20, 380, 45): draw.rectangle((30, y, 770, y + 12), fill="black")
    out = BytesIO(); image.save(out, "PNG")
    return out.getvalue()


NEED = {"text": "Acme Pro 月付20美元每月。", "claims": "Acme Pro 月付20美元每月。", "kind": "pricing"}
TEXT = "Acme Pro monthly pricing $20 per month. Screenshot feature available."
META = {"title": "Acme pricing", "quality": {"region_text": TEXT}}


def lines(text=TEXT, height=.05):
    return [{"text": text, "confidence": .95, "height": height}]


def test_blank_or_unreadable_images_are_rejected_before_registration(monkeypatch):
    policy = visual_materials.policy({"enabled": True, "version": 3, "ocr_review": True})
    with pytest.raises(screenshots.ScreenshotError, match="空白"):
        image_quality.verify(pixels(blank=True), NEED, META, policy, remaining=15)
    monkeypatch.setattr(image_quality, "recognize", lambda *a, **k: lines(height=.002))
    with pytest.raises(screenshots.ScreenshotError, match="过小"):
        image_quality.verify(pixels(), NEED, META, policy, remaining=15)


def test_ocr_checks_the_visible_price_not_just_dom_text(monkeypatch):
    policy = visual_materials.policy({"enabled": True, "version": 3, "ocr_review": True})
    monkeypatch.setattr(image_quality, "recognize", lambda *a, **k: lines(TEXT.replace("$20", "$25")))
    with pytest.raises(screenshots.ScreenshotError, match="金额"):
        image_quality.verify(pixels(), NEED, META, policy, remaining=15)
    monkeypatch.setattr(image_quality, "recognize", lambda *a, **k: lines())
    result = image_quality.verify(pixels(), NEED, META, policy, remaining=15)
    assert result["state"] == "passed" and result["vision"]["state"] == "skipped"


def test_semantic_model_cannot_select_an_invented_candidate_or_quote(monkeypatch):
    candidates = [{"id": "trusted-1", "text": TEXT}]

    class Model:
        def __init__(self, config): pass
        def close(self): pass
        def chat(self, *a, **k): return ChatResponse(content=json.dumps({"candidate_id": "invented", "usable": True, "quote": TEXT, "reason": "okay"}))

    monkeypatch.setattr(acquire, "LLMClient", Model)
    monkeypatch.setattr(acquire, "get_settings", lambda: type("S", (), {"paths": type("P", (), {"config_dir": "not-used"})()})())
    monkeypatch.setattr(acquire, "load_system_config", lambda path: {"ai": {"model": "test", "base_url": "https://model.test"}})
    policy = visual_materials.policy({"enabled": True, "semantic_review": True})
    chosen, review = acquire.review_candidates(NEED, candidates, policy, remaining=20)
    assert chosen == candidates[0] and review["state"] == "unavailable"
    assert policy["_model_calls"] == 1


def test_semantic_rejection_is_respected_and_model_budget_is_bounded(monkeypatch):
    class Model:
        def __init__(self, config): pass
        def close(self): pass
        def chat(self, *a, **k): return ChatResponse(content=json.dumps({"candidate_id": "trusted-1", "usable": False, "quote": "", "reason": "wrong context"}))
    monkeypatch.setattr(acquire, "LLMClient", Model)
    monkeypatch.setattr(acquire, "get_settings", lambda: type("S", (), {"paths": type("P", (), {"config_dir": "not-used"})()})())
    monkeypatch.setattr(acquire, "load_system_config", lambda path: {"ai": {"model": "test", "base_url": "https://model.test"}})
    candidates = [{"id": "trusted-1", "text": TEXT}]
    policy = visual_materials.policy({"enabled": True, "semantic_review": True, "max_model_calls": 1})
    with pytest.raises(screenshots.ScreenshotError, match="不对应"):
        acquire.review_candidates(NEED, candidates, policy, remaining=20)
    chosen, review = acquire.review_candidates(NEED, candidates, policy, remaining=20)
    assert chosen == candidates[0] and review["state"] == "skipped" and policy["_model_calls"] == 1


def test_optional_vision_interface_sends_pixels_and_respects_a_rejection(monkeypatch):
    from core import llm, config
    from web import settings
    seen = []
    class Model:
        def __init__(self, config): pass
        def close(self): pass
        def chat(self, messages, **kwargs):
            seen.append(messages)
            return ChatResponse(content='{"usable":false,"reason":"遮挡","visible_text":""}')
    monkeypatch.setattr(llm, "LLMClient", Model)
    monkeypatch.setattr(config, "load_system_config", lambda *a: {"vision_ai": {"model": "vision", "base_url": "https://model.test"}})
    monkeypatch.setattr(settings, "get_settings", lambda: type("S", (), {"paths": type("P", (), {"config_dir": "not-used"})()})())
    policy = visual_materials.policy({"enabled": True, "vision_section": "vision_ai"})
    with pytest.raises(screenshots.ScreenshotError, match="遮挡"):
        image_quality.multimodal_review(pixels(), NEED, policy, remaining=20)
    assert seen[0][1]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")


@pytest.mark.parametrize("change,expected", [
    ({"paragraph_ids": ["invented"]}, "未完成"),
    ({"paragraph_ids": ["c1p2"]}, "当前讲解"),
    ({"focus": {"x1": 900, "y1": 10, "x2": 200, "y2": 900}}, "未完成"),
])
def test_image_understanding_cannot_invent_positions_or_apply_invalid_focus(monkeypatch, change, expected):
    from core import llm, config
    from web import settings
    decision = {"usable": True, "reason": "内容对应", "summary": "月付价格表", "visible_text": "$20 per month",
                "paragraph_ids": ["c1p1"], "focus": None, **change}
    class Model:
        def __init__(self, config): pass
        def close(self): pass
        def chat(self, *a, **k): return ChatResponse(content=json.dumps(decision))
    monkeypatch.setattr(llm, "LLMClient", Model)
    monkeypatch.setattr(config, "load_system_config", lambda *a: {"vision_ai": {"model": "vision", "base_url": "https://model.test"}})
    monkeypatch.setattr(settings, "get_settings", lambda: type("S", (), {"paths": type("P", (), {"config_dir": "not-used"})()})())
    policy = visual_materials.policy({"enabled": True, "vision_section": "vision_ai", "vision_required": True})
    policy["_paragraphs"] = [{"id": "c1p2", **NEED}]
    with pytest.raises(screenshots.ScreenshotError, match=expected):
        image_quality.multimodal_review(pixels(), {"id": "c1p1", **NEED}, policy, remaining=30)


def test_required_vision_does_not_silently_accept_ocr_only_cache_or_budget_exhaustion():
    from datetime import datetime, timezone
    from core.video import visual_cache
    policy = visual_materials.policy({"enabled": True, "version": 3, "vision_section": "vision_ai", "vision_required": True, "max_model_calls": 0})
    with pytest.raises(screenshots.ScreenshotError, match="预算"):
        image_quality.multimodal_review(pixels(), NEED, policy, remaining=30)
    record = {"captured_at": datetime.now(timezone.utc).isoformat(), "quality": {"version": 3, "image": {"ocr_lines": 4}}}
    assert not visual_cache.reusable(record, NEED, policy)


def test_understanding_key_changes_when_text_or_paragraph_position_changes():
    first = image_quality.understanding_key({"id": "c1p1", **NEED})
    assert first != image_quality.understanding_key({"id": "c1p2", **NEED})
    assert first != image_quality.understanding_key({"id": "c1p1", **NEED, "text": "改稿后的讲解"})


def test_verbose_english_ocr_description_does_not_discard_a_relevant_image():
    result = image_quality.VisionDecision.model_validate({
        "usable": True, "reason": "The visible product interface is relevant. " * 12,
        "visible_text": "Request credentials and approve the browser session. " * 30,
        "summary": "网页中的授权操作界面", "paragraph_ids": ["c1p1"], "focus": None})
    assert result.usable and len(result.visible_text) == 300 and len(result.reason) == 240
    assert result.paragraph_ids == ["c1p1"]


def test_model_acceptance_cannot_override_a_visible_price_contradiction(monkeypatch):
    from core import llm, config
    from web import settings
    class Model:
        def __init__(self, config): pass
        def close(self): pass
        def chat(self, *a, **k):
            return ChatResponse(content=json.dumps({'usable': True, 'reason': '改成25美元即可', 'visible_text': 'Acme Pro $25 per month', 'paragraph_ids': ['current']}))
    monkeypatch.setattr(llm, 'LLMClient', Model)
    monkeypatch.setattr(config, 'load_system_config', lambda *a: {'vision_ai': {'model': 'vision', 'base_url': 'https://model.test'}})
    monkeypatch.setattr(settings, 'get_settings', lambda: type('S', (), {'paths': type('P', (), {'config_dir': 'not-used'})()})())
    policy = visual_materials.policy({'enabled': True, 'vision_section': 'vision_ai', 'vision_required': True})
    with pytest.raises(screenshots.ScreenshotError, match='价格条件不符'):
        image_quality.multimodal_review(pixels(), NEED, policy, remaining=30)
