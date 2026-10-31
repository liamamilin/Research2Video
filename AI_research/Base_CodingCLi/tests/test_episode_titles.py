"""Public programme identity is separate from event headlines and spoken words."""
import copy
import json

import pytest

from core.video import episodes, store, writer
from tests.test_content_columns import column_env  # noqa: F401
from tests.test_production import prod_env  # noqa: F401
from tests.test_video_scripts import video_env, llm, research, autovideo  # noqa: F401


def context(key="ai_flash", name="AI 快讯", kind="news", matrix="ai_media"):
    return {"column": key, "column_name": name, "type": kind, "matrix": matrix}


def topic():
    return {"title": "其中一项更新", "content_mode": "episode",
            "from_report": "output/flash/2026-10-29_ai_flash_discovery.md",
            "fields": {"发布日期": "2026-10-28"}, "episode": {"items": []}}


def reply(title="某工具的单项更新"):
    return json.dumps({"title": title, "sections": [
        {"title": "变化甲", "episode_role": "item", "paragraphs": [{"text": "第一项变化保留自己的具体解释。"}]},
        {"title": "变化乙", "episode_role": "item", "paragraphs": [{"text": "第二项内容继续单独讲述。"}]},
    ]}, ensure_ascii=False)


@pytest.mark.parametrize("ctx,expected", [
    (context(), "今日快讯｜2026年10月29日"),
    (context("ai_opensource", "AI 开源发现", "explainer"), "AI 开源发现｜2026年10月29日"),
    (context("ai_business", "AI 产业观察", "explainer"), "AI 产业观察｜2026年10月29日"),
    (context("economy_daily", "变化怎样传到你", "news", "economy"), "变化怎样传到你｜2026年10月29日"),
    (context("chatgpt_t09_updates", "T09 · 产品动态与热点", "news", "chatgpt_acquisition"), "ChatGPT 产品动态与热点｜2026年10月29日"),
])
def test_columns_use_their_programme_name_and_report_date(ctx, expected):
    original = topic()
    saved = copy.deepcopy(original)
    named = episodes.publication_topic(original, ctx)
    assert named["title"] == episodes.publication_title(named, "首条事件标题") == expected
    assert named["round"] == "2026-10-29"
    assert original == saved


def test_published_column_label_wins_over_internal_topic_number():
    ctx = {**context("chatgpt_t01_product", "T01 · 内部名称", "explainer", "chatgpt_acquisition"),
           "render": {"packaging": {"label": "产品与能力认知"}}}
    named = episodes.publication_topic(topic(), ctx)
    assert named["title"] == "ChatGPT 产品与能力认知｜2026年10月29日"
    renamed = {**ctx, "column_name": "后来换了一个名称", "render": {}}
    assert episodes.publication_topic(named, renamed) is named


def test_undated_report_does_not_invent_today_or_use_an_event_release_date():
    undated = {**topic(), "from_report": "output/report.md"}
    assert episodes.publication_topic(undated, context())["title"] == "今日快讯"
    assert episodes.publication_topic({**undated, "round": "2026-10-20"}, context())["title"].endswith("2026年10月20日")


def test_deep_programme_keeps_the_subject_as_subtitle_without_duplicate_prefix():
    named = episodes.publication_topic(topic(), context("ai_deep_research", "AI 深度解读", "deep_research"))
    expected = "AI 深度解读｜2026年10月29日｜并行 Agent 的成本账"
    assert episodes.publication_title(named, "并行 Agent 的成本账") == expected
    assert episodes.publication_title(named, expected) == expected


def test_existing_work_and_individual_titles_are_not_rewritten():
    old = topic()
    assert episodes.publication_title(old, "既有定稿标题") == "既有定稿标题"
    single = {**old, "content_mode": "individual"}
    assert episodes.publication_topic(single, context()) is single
    assert episodes.publication_title(single, "单条具体主题") == "单条具体主题"


def test_writer_sets_public_title_without_adding_dates_to_narration(llm):
    llm.replies.append(reply())
    named = episodes.publication_topic(topic(), context())
    result = writer.write(report="本期有两项变化，分别讲述具体内容。", citation_check=None,
                          topic=named, column_style=episodes.brief(named), target_minutes=1, llm_config=None)
    assert result.title == "今日快讯｜2026年10月29日"
    assert result.meta["authored_title"] == "某工具的单项更新"
    assert [s["title"] for s in result.sections] == ["变化甲", "变化乙"]
    assert [p["text"] for s in result.sections for p in s["paragraphs"]] == [
        "第一项变化保留自己的具体解释。", "第二项内容继续单独讲述。"]
    assert len(llm.calls) == 1 and "不要求讲述者念日期" in llm.calls[0][1]["content"]


def test_normal_report_entry_saves_and_renders_the_same_public_title(client, column_env, llm):
    from tests.test_video_scripts import _login, _csrf, _wait
    from web.services import video_render

    llm.replies.append(reply())
    _login(client)
    path = "output/flash/2026-10-19_ai_flash_discovery.md"
    started = client.post("/api/production/flash/write", json={"report_path": path}, headers=_csrf(client))
    assert started.status_code == 202, started.text
    script = _wait(client, started.json()[0]["id"], {"DRAFT", "FAILED"})
    assert script["status"] == "DRAFT", script.get("error")
    assert script["topic"]["title"] == script["version_shown"]["title"] == "模型横评｜2026年10月19日"
    assert script["version_shown"]["meta"]["episode"]["publication"]["issue_date"] == "2026-10-19"
    approved = client.post(f"/api/video/scripts/{script['id']}/approve", json={}, headers=_csrf(client))
    assert approved.status_code == 200, approved.text
    rendered = video_render.create(store.get_script(script["id"]))
    assert rendered["title"] == rendered["request"]["title"] == "模型横评｜2026年10月19日"
    assert all("2026年" not in paragraph for chapter in rendered["request"]["chapters"] for paragraph in chapter["paragraphs"])
