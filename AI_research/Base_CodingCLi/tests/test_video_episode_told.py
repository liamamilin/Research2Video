"""An episode drops items an earlier video already told in full (2026-11-03: the flash
episode retold yesterday's DeepSeek Search & Fetch price)."""
from core.video import episodes

PRICE = "https://api-docs.deepseek.com/quick_start/pricing#search--fetch-pricing"
GEMINI = "https://ai.google.dev/gemini-api/docs/pricing"

REPORT = f"""## 今日快讯

### 1.1 Gemini API 主力模型价格将在 12 月和 1 月翻倍
- **具体对象**：Gemini 4 Argon 输入 $2.00 → $4.00；Gemini 3.8 Flash 输入 $0.75 → $1.50。
- **证据来源**：{GEMINI}#enterprise

### 1.6 DeepSeek 的 Search & Fetch 从免费变按请求收费
- **具体对象**：Search 与 Fetch 按成功请求计费，$0.75 / 1,000 次。
- **使用场景**：一个 agent 每天 180 次请求，约 $0.135。
- **证据来源**：{PRICE}

### 1.7 OpenAI Agent Security 文档
- **具体对象**：命令、网络、沙箱、审批分层。
- **证据来源**：https://learn.chatgpt.com/docs/enterprise/agent-security
"""
ITEMS = [{"id": "a", "title": "Gemini API 主力模型价格将在 12 月和 1 月翻倍"},
         {"id": "b", "title": "DeepSeek 的 Search & Fetch 从免费变按请求收费"},
         {"id": "c", "title": "OpenAI Agent Security 文档"}]
YESTERDAY_REPORT = f"### 1.2 DeepSeek Search & Fetch\n- 证据：{PRICE}\n### 1.3 Gemini 4 Argon\n- 证据：{GEMINI}#standard\n"


def peer(text, report="output/flash/2026-11-02.md"):
    return {"script_id": "yesterday", "title": "今日快讯｜2026年11月02日", "text": text, "report": report}


def told(peers, reports=None):
    reports = reports or {"output/flash/2026-11-02.md": YESTERDAY_REPORT}
    return [c["title"] for c in episodes.covered(REPORT, ITEMS, peers, reports.__getitem__)]


def test_an_item_whose_entry_and_every_figure_an_earlier_video_told_is_dropped():
    said = "DeepSeek 搜索抓取开始收费：每 1,000 次成功请求 0.75 美元。Gemini 4 Argon 输入从 2 美元到 4 美元。"
    assert told([peer(said)]) == ["DeepSeek 的 Search & Fetch 从免费变按请求收费"]


def test_a_new_figure_keeps_the_item_as_an_update():
    # Gemini today adds 3.8 Flash's $0.75 → $1.50; yesterday only told Argon.
    said = "Gemini 4 Argon 输入从 2 美元到 4 美元，DeepSeek 0.75 美元。"
    assert "Gemini API 主力模型价格将在 12 月和 1 月翻倍" not in told([peer(said)])


def test_the_same_figures_from_another_page_or_entry_are_not_the_same_story():
    said = "每 1,000 次成功请求 0.75 美元。"
    other = {"output/flash/2026-11-02.md": "### 别的\n- 证据：https://api-docs.deepseek.com/quick_start/pricing#token-pricing\n"}
    assert told([peer(said)], other) == []


def test_worked_examples_and_links_do_not_count_as_figures():
    part = episodes.item_parts(REPORT, ITEMS)["DeepSeek 的 Search & Fetch 从免费变按请求收费"]
    assert episodes._figures(part) == {"0.75"}
    assert episodes._figures("见 https://x.com/a#:~:text=GPT-5.6%20responds") == set()


def test_an_item_without_figures_is_never_dropped_on_a_guess():
    assert "OpenAI Agent Security 文档" not in told([peer("命令、网络、沙箱、审批分层。")])


def test_dropped_items_leave_the_report_and_the_rest_stay():
    left = episodes.without(REPORT, ITEMS, {"DeepSeek 的 Search & Fetch 从免费变按请求收费"})
    assert "Search & Fetch" not in left and "Gemini 4 Argon" in left and "Agent Security" in left
