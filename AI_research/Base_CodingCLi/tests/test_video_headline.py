"""Title edits must be based on the final spoken words and cannot replace them."""
import copy
from types import SimpleNamespace

from core.video import headline, manuscript_workflow, platform_publish, publish, writer
from tests.test_video_editorial import Queue
from web.services import douyin as douyin_service


def test_title_selection_uses_final_narration_and_keeps_body_unchanged():
    sections = [{"title": "内部提纲", "paragraphs": [{"text": "断网以后，改动会先存在本机。", "review_reason": "内部审稿意见"}]}]
    before = copy.deepcopy(sections)
    client = Queue({"candidates": [{"title": "断网改的文档，联网后还能找回来", "reason": "改动保存的具体处境",
                                    "quotes": ["断网以后，改动会先存在本机。"]}], "choice": 0, "reason": "比抽象的工作台更具体"})
    result = headline.finish(client, title="给文档补工作台", sections=sections, column_style="普通观众")
    assert result["applied"] and result["title"] == "断网改的文档，联网后还能找回来"
    assert sections == before
    request = client.calls[0][-1]["content"]
    assert "内部审稿意见" not in request and "内部提纲" not in request


def test_title_with_fabricated_evidence_retains_original_and_records_failure():
    client = Queue({"candidates": [{"title": "效率翻倍", "reason": "收益", "quotes": ["实测效率翻倍"]}], "choice": 0, "reason": "收益具体"})
    result = headline.finish(client, title="文档支持离线编辑", sections=[{"paragraphs": [{"text": "支持离线保存。"}]}], column_style="快讯")
    assert not result["applied"] and result["title"] == "文档支持离线编辑" and result["error"]


def test_title_selection_can_keep_original_without_inventing_alternatives():
    client = Queue({"candidates": [], "choice": -1, "reason": "现有标题已兑现看点"})
    result = headline.finish(client, title="文档支持离线编辑", sections=[{"paragraphs": [{"text": "支持离线保存。"}]}], column_style="快讯")
    assert not result["applied"] and "error" not in result


def test_headline_multi_platform_outputs_and_episode_rules():
    sections = [{"title": "第一条", "paragraphs": [{"text": "Gemini 4 Hydrogen 现在切只省 25%，等到 12 月才能省 75%。"}]}]
    client = Queue({
        "candidates": [{
            "angle": "账单数字",
            "title": "Gemini 4 Hydrogen 账单实测：现在切只省 25%，12 月才省 75%",
            "douyin_title": "Gemini 4 现在切只省 25%，12 月才省 75%",
            "cover_headline": "现在只省25%？",
            "reason": "大牌实体前置且数字反差清晰",
            "quotes": ["Gemini 4 Hydrogen 现在切只省 25%，等到 12 月才能省 75%。"],
        }],
        "choice": 0,
        "reason": "单押本期最炸一条",
    })
    result = headline.finish(
        client, title="模型省钱更新", sections=sections, column_style="AI 快讯",
        topic_title="Gemini 4 Hydrogen 定价变化", is_episode=True, column_key="flash-nr",
    )
    assert result["applied"] is True
    assert result["is_episode"] is True
    assert result["title"] == "Gemini 4 Hydrogen 账单实测：现在切只省 25%，12 月才省 75%"
    assert result["douyin_title"] == "Gemini 4 现在切只省 25%，12 月才省 75%"
    assert result["cover_headline"] == "现在只省25%？"
    assert "整期多条目合集视频" in client.calls[0][0]["content"]


def test_headline_rejects_invented_numbers_in_title_and_short_variants():
    sections = [{"paragraphs": [{"text": "ChatGPT 订阅费每月 20 美元，不含 API 额度。"}]}]
    bad_main = Queue({
        "candidates": [{
            "title": "ChatGPT 每月 99 美元还不送 API 额度",
            "reason": "价格冲突",
            "quotes": ["ChatGPT 订阅费每月 20 美元，不含 API 额度。"],
        }],
        "choice": 0,
        "reason": "价格醒目",
    })
    res_bad = headline.finish(bad_main, title="ChatGPT 订阅说明", sections=sections, column_style="获客")
    assert not res_bad["applied"] and res_bad["title"] == "ChatGPT 订阅说明" and "99" in res_bad["error"]

    bad_short = Queue({
        "candidates": [{
            "title": "ChatGPT 订阅费每月 20 美元，别以为包含了 API 额度",
            "douyin_title": "这是一个故意超过三十个字长度限制的抖音短标题用来测试是否会被过滤掉",
            "cover_headline": "每月88美元？",
            "reason": "账单反差",
            "quotes": ["ChatGPT 订阅费每月 20 美元，不含 API 额度。"],
        }],
        "choice": 0,
        "reason": "主标题符合事实",
    })
    res_short = headline.finish(bad_short, title="ChatGPT 订阅说明", sections=sections, column_style="获客")
    assert res_short["applied"] is True
    assert res_short["douyin_title"] == ""
    assert res_short["cover_headline"] == ""


def test_focused_author_runs_headline_polish_when_enabled(monkeypatch):
    draft = writer.WrittenScript(
        "初稿标题",
        [{"title": "开场", "paragraphs": [{"text": "OpenAI 把 20 美元订阅费返成额度，但 Codex 不算。"}]}],
        {}, {},
    )
    monkeypatch.setattr(writer, "write_for_column", lambda **kw: draft)
    monkeypatch.setattr(manuscript_workflow, "revise_passage", lambda d, **kw: d)
    monkeypatch.setattr(manuscript_workflow, "LLMClient", lambda *a, **kw: SimpleNamespace(usage={"total_tokens": 120}))
    monkeypatch.setattr(headline, "finish", lambda client, **kw: {
        "prompt_version": headline.PROMPT_VERSION,
        "before": kw["title"],
        "title": "OpenAI 把 20 美元订阅费返成 API 额度，但 Codex 不算",
        "douyin_title": "OpenAI 返 20 美元额度，Codex 却不算",
        "cover_headline": "返$20但Codex不算",
        "applied": True,
        "is_episode": kw["is_episode"],
    })
    out = manuscript_workflow.write(
        mode="focused", profile=None, headline_polish=True,
        report="底稿", topic={"title": "OpenAI 订阅返额度"}, llm_config=None,
        column=SimpleNamespace(key="flash-nr", script_style="快讯", target_minutes=3, voice_id=None),
    )
    assert out.title == "OpenAI 把 20 美元订阅费返成 API 额度，但 Codex 不算"
    assert out.meta["headline"]["douyin_title"] == "OpenAI 返 20 美元额度，Codex 却不算"
    assert out.meta["headline"]["usage"]["total_tokens"] == 120


def test_publish_and_douyin_prefer_polished_titles():
    column = SimpleNamespace(name="AI 快讯", packaging={}, package={})
    script = {"id": "s1", "current_version": 1, "approved_version": 1,
              "topic": {"title": "GPT-6.2 选型", "content_mode": "episode"}}
    version = {
        "version": 1,
        "title": "GPT-6.2 Astra、Sol、Ultrafast 怎么选：订阅额度和 API 账单别花错",
        "sections": [{"title": "第一节", "paragraphs": [{"text": "正文内容。", "citations": []}]}],
        "meta": {"headline": {
            "douyin_title": "GPT-6.2 三档别花错：订阅和 API 账单拆解",
            "cover_headline": "GPT-6.2三档别花错",
        }},
    }
    pack = publish.build(script, version, column, "", None)
    assert pack["douyin_title"] == "GPT-6.2 三档别花错：订阅和 API 账单拆解"
    assert pack["cover"]["headline"] == "GPT-6.2三档别花错"

    adapted = platform_publish.adapt(pack, "douyin")
    assert adapted["payload"]["title"] == "GPT-6.2 三档别花错：订阅和 API 账单拆解"

    fitted = douyin_service._fit_douyin_auto_pack({
        "title": version["title"],
        "douyin_title": pack["douyin_title"],
        "description": "简介正文",
        "tags": ["GPT", "AI"],
    })
    assert fitted["title"] == "GPT-6.2 三档别花错：订阅和 API 账单拆解"
    assert fitted["description"].startswith(version["title"])
