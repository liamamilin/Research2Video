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


@pytest.mark.parametrize("ctx,label", [
    (context(), "今日快讯"),
    (context("ai_opensource", "AI 开源发现", "explainer"), "AI 开源发现"),
    (context("economy_daily", "变化怎样传到你", "news", "economy"), "变化怎样传到你"),
    (context("chatgpt_t09_updates", "T09 · 产品动态与热点", "news", "chatgpt_acquisition"), "ChatGPT 产品动态与热点"),
])
def test_programmes_are_titled_by_their_lead_items_not_column_and_date(ctx, label):
    original = topic()
    saved = copy.deepcopy(original)
    named = episodes.publication_topic(original, ctx)
    publication = named["episode"]["publication"]
    assert (publication["label"], publication["issue_date"], named["round"]) == (label, "2026-10-29", "2026-10-29")
    assert named["title"] == label  # stands in until the script is written
    assert episodes.publication_title(named, "Claude 涨价、Gemini 半价：这周的订阅变化") == "Claude 涨价、Gemini 半价：这周的订阅变化"
    assert episodes.publication_title(named, " ") == label
    assert original == saved


def test_delayed_programme_uses_frozen_issue_date_instead_of_completion_date():
    original=topic()
    original['episode']['issue']={'research_window':{'end':'2026-10-28T00:10:00+08:00'}}
    assert episodes.publication_topic(original,context())['episode']['publication']['issue_date']=='2026-10-28'


def test_published_column_label_wins_over_internal_topic_number():
    ctx = {**context("chatgpt_t01_product", "T01 · 内部名称", "explainer", "chatgpt_acquisition"),
           "render": {"packaging": {"label": "产品与能力认知"}}}
    named = episodes.publication_topic(topic(), ctx)
    assert named["episode"]["publication"]["label"] == "ChatGPT 产品与能力认知"
    assert episodes.greeting(named, ctx) == "大家好，欢迎收看ChatGPT 产品与能力认知。"
    renamed = {**ctx, "column_name": "后来换了一个名称", "render": {}}
    assert episodes.publication_topic(named, renamed) is named


def test_undated_report_does_not_invent_today_or_use_an_event_release_date():
    undated = {**topic(), "from_report": "output/report.md"}
    assert episodes.publication_topic(undated, context())["episode"]["publication"]["issue_date"] == ""
    assert episodes.publication_topic({**undated, "round": "2026-10-20"}, context())["episode"]["publication"]["issue_date"] == "2026-10-20"


def test_programmes_named_before_the_change_keep_their_dated_title():
    old = {**topic(), "episode": {"items": [], "publication": {"scheme": "column-date/v1", "title": "今日快讯｜2026年10月29日",
                                                              "label": "今日快讯", "issue_date": "2026-10-29"}}}
    assert episodes.publication_title(old, "具体事件") == "今日快讯｜2026年10月29日"
    deep = {**old, "episode": {"items": [], "publication": {**old["episode"]["publication"], "title": "AI 深度解读｜2026年10月29日",
                                                            "subject_subtitle": True}}}
    expected = "AI 深度解读｜2026年10月29日｜并行 Agent 的成本账"
    assert episodes.publication_title(deep, "并行 Agent 的成本账") == expected
    assert episodes.publication_title(deep, expected) == expected
    assert "整期总标题使用「今日快讯｜2026年10月29日」" in episodes.brief(old)


def test_existing_work_and_individual_titles_are_not_rewritten():
    old = topic()
    assert episodes.publication_title(old, "既有定稿标题") == "既有定稿标题"
    single = {**old, "content_mode": "individual"}
    assert episodes.publication_topic(single, context()) is single
    assert episodes.publication_title(single, "单条具体主题") == "单条具体主题"


def test_writer_keeps_its_own_title_and_asks_for_product_money_and_a_question(llm):
    llm.replies.append(reply("Kimi 降价一半、Claude 加了长任务：本周三条更新"))
    named = episodes.publication_topic(topic(), context())
    result = writer.write(report="本期有两项变化，分别讲述具体内容。", citation_check=None,
                          topic=named, column_style=episodes.brief(named), target_minutes=1, llm_config=None)
    assert result.title == "Kimi 降价一半、Claude 加了长任务：本周三条更新"
    assert "authored_title" not in result.meta
    assert [s["title"] for s in result.sections] == ["变化甲", "变化乙"]
    prompt = llm.calls[0][0]["content"] + llm.calls[0][1]["content"]
    assert writer.TITLE_RULE in prompt and writer.ENDING_RULE in prompt
    assert "不写栏目名「今日快讯」" in prompt and "沿用栏目命名" not in prompt


def test_normal_report_entry_saves_and_renders_the_same_public_title(client, column_env, llm):
    from tests.test_video_scripts import _login, _csrf, _wait
    from web.services import video_render

    llm.replies.append(reply("某工具降价到 5 美元：两项变化"))
    _login(client)
    path = "output/flash/2026-10-19_ai_flash_discovery.md"
    started = client.post("/api/production/flash/write", json={"report_path": path}, headers=_csrf(client))
    assert started.status_code == 202, started.text
    script = _wait(client, started.json()[0]["id"], {"DRAFT", "FAILED"})
    assert script["status"] == "DRAFT", script.get("error")
    assert script["version_shown"]["title"] == "某工具降价到 5 美元：两项变化"
    assert script["version_shown"]["meta"]["episode"]["publication"]["issue_date"] == "2026-10-19"
    approved = client.post(f"/api/video/scripts/{script['id']}/approve", json={}, headers=_csrf(client))
    assert approved.status_code == 200, approved.text
    rendered = video_render.create(store.get_script(script["id"]))
    assert rendered["title"] == rendered["request"]["title"] == "某工具降价到 5 美元：两项变化"
    assert all("2026年" not in paragraph for chapter in rendered["request"]["chapters"] for paragraph in chapter["paragraphs"])
