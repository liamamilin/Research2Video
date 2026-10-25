"""Realistic reference-heavy copies, fact boundaries and non-publishing draft operations."""
from copy import deepcopy

import pytest

from core.social.publish_text import combined_text, default_comment
from core.video import platform_publish as copy, store
from tests.test_platforms import platform_env, render  # noqa: F401
from tests.test_video_scripts import _csrf, _login
from web.services import distributions, platform_manager


def pack():
    refs = [{"title": f"来源 {i}", "url": f"https://example.com/research/{i}/" + "a" * 65, "date": "", "cited": i < 5} for i in range(20)]
    body = "在固定硬件的测试中，样机运行了10小时。这个结果不代表所有设备，也不能用来推断日常混合使用的表现。"
    return {"title": "编程 Agent 正在基础设施化：本地 125 tok/s、断线续跑、密码不给它看",
            "description": body + "\n\n参考来源（文稿引用）：\n" + "\n".join(f"· {r['title']} {r['url']}" for r in refs),
            "tags": ["一个知识点", "开发者", "AI", "额外话题"], "references": refs,
            "content": {"required": [body], "chapters": "00:00 开场\n00:20 测试条件\n00:40 结论边界"}}


@pytest.mark.parametrize("platform", ["bilibili", "douyin", "kuaishou", "xiaohongshu", "weixin_channels"])
def test_reference_heavy_pack_fits_without_losing_conditions_or_originals(platform):
    original = pack(); before = deepcopy(original)
    result = copy.adapt(original, platform)
    assert result["check"]["valid"], result["check"]["issues"]
    assert original == before
    assert original["content"]["required"][0] in result["payload"]["description"]
    assert result["references"] == original["references"]
    assert len(result["payload"]["tags"]) <= copy.limits(platform)["tag_limit"]
    for ref in original["references"]:
        assert ref["url"] in result["source_description"]
    assert "…" not in result["payload"]["description"]


def test_manual_description_keeps_its_caveat_in_the_next_paragraph():
    manual = {"title": "样机测试", "description": "性能翻倍。" + "说明。" * 180 + "\n\n仅限这次样机测试，不能推广。", "tags": []}
    result = copy.adapt(pack(), "kuaishou", manual)
    assert result["payload"]["description"] == manual["description"]
    assert not result["check"]["valid"]


def test_manual_prose_with_exact_known_reference_suffix_can_reflow_only_the_references():
    original = pack(); manual = {k: original[k] for k in ("title", "description", "tags")}
    manual["description"] = manual["description"].replace("在固定硬件", "只在固定硬件", 1)
    result = copy.adapt(original, "kuaishou", manual)
    assert result["check"]["valid"]
    assert result["payload"]["description"].startswith("只在固定硬件")
    assert "不代表所有设备" in result["payload"]["description"]


def test_long_title_with_a_qualification_is_not_shortened_into_an_unconditional_claim():
    original = pack(); original["title"] = "性能提升50%：仅限指定硬件的内部测试，尚未独立复核"
    result = copy.adapt(original, "xiaohongshu")
    assert result["payload"]["title"] == original["title"]
    assert not result["check"]["valid"]


def test_sources_that_cannot_fit_are_kept_whole_and_reported():
    original = pack(); original["references"] = [{"title": "极长来源", "url": "https://example.com/" + "x" * 600}]
    result = copy.adapt(original, "kuaishou")
    assert not result["check"]["valid"]
    assert result["references"][0]["url"].endswith("x" * 600)
    assert "主要来源未能" in " ".join(result["check"]["issues"])


def test_exact_wire_count_includes_separators_tags_emoji_and_duplicate_caption():
    p = {"title": "标题😀", "description": "内" * 488, "tags": ["科普"]}
    check = copy.inspect("kuaishou", p)
    assert check["counts"]["combined"] == 498
    assert check["counts"]["description_budget"] == 490
    assert len(combined_text("kuaishou", "标题", "标题", [])) == 2
    assert combined_text("douyin", "独立标题", "简介", ["科技"]) == "简介 #科技"


def test_empty_default_comment_is_disabled_but_text_is_not_created_or_moved():
    assert default_comment({"enabled": True, "text": "", "pin": True}) == {"enabled": False, "text": "", "pin": False}
    assert default_comment({"enabled": True, "text": "我的评论", "pin": False})["enabled"]


def test_unknown_reference_tail_is_not_silently_removed_from_manual_content():
    original = pack()
    manual = {k: original[k] for k in ("title", "description", "tags")}
    manual["description"] += "\n我手动补充的限定：不能推广。"
    result = copy.adapt(original, "kuaishou", manual)
    assert result["payload"]["description"] == manual["description"]
    assert not result["check"]["valid"]


def test_draft_can_preserve_overlimit_text_and_empty_opt_in_comment_with_history(platform_env, client):
    row = render(); _login(client)
    initial = {"title": "原稿", "description": "长" * 2500, "tags": ["科技"], "tid": 17,
               "first_comment": {"enabled": True, "text": "", "pin": False}}
    adapted = {**initial, "description": "新的短简介", "first_comment": {"enabled": False, "text": "", "pin": False}}
    url = "/api/video/distributions/bilibili/draft"
    response = client.put(url, headers=_csrf(client), json={"render_id": row["id"], "account_uid": "123", "payload": adapted, "previous_payload": initial})
    assert response.status_code == 200, response.text
    versions = client.get(f"/api/video/distributions/bilibili/history/{row['id']}").json()["versions"]
    assert [v["payload"]["description"] for v in versions] == ["新的短简介", initial["description"]]
    assert not platform_manager.store().script_uploads("123", row["script_id"])
    with pytest.raises(platform_manager.Error):
        platform_manager.enqueue(row["id"], {**initial, "account_uid": "123"})


def test_adaptation_preview_is_read_only_and_does_not_call_a_model_or_enqueue(platform_env, client, monkeypatch):
    row = render(); _login(client)
    monkeypatch.setattr("core.llm.LLMClient", lambda *a, **k: pytest.fail("No model needed"))
    monkeypatch.setattr(platform_manager, "column_for", lambda *a: object())
    monkeypatch.setattr("web.services.video_render.publish_pack", lambda *a, **k: pack())
    response = client.post("/api/video/distributions/kuaishou/adapt", headers=_csrf(client),
                           json={"render_id": row["id"], "payload": {k: pack()[k] for k in ("title", "description", "tags")}})
    assert response.status_code == 200, response.text
    assert response.json()["check"]["valid"]
    assert store.distribution_draft(row, "kuaishou", "123") is None
    assert not platform_manager.store().script_uploads("123", row["script_id"])


def test_draft_http_rejects_unknown_provider_fields_without_leaking_them(platform_env, client, monkeypatch):
    row = render(); _login(client)
    monkeypatch.setattr(distributions, "accounts", lambda: {"kuaishou": {"connected": True, "uid": "123"}})
    response = client.put("/api/video/distributions/kuaishou/draft", headers=_csrf(client), json={
        "render_id": row["id"], "account_uid": "123", "payload": {"title": "标题", "cookie": "do-not-echo"}})
    assert response.status_code == 409
    assert "do-not-echo" not in response.text


def test_history_is_not_visible_under_another_account(platform_env, monkeypatch):
    row = render()
    distributions.save_draft(row["id"], "bilibili", "123", {"title": "我的稿件", "description": "私有草稿", "tags": []})
    monkeypatch.setattr(platform_manager, "account", lambda: {"connected": True, "uid": "other"})
    assert distributions.history(row["id"], "bilibili")["versions"] == []
