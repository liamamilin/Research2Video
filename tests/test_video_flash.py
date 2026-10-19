"""Flash-news topics: parsing a discovery report (or a radar report), numbering and lookup."""

from __future__ import annotations

from core.video import flash, topics

DISCOVERY = """\
### 0. 本期概览
窗口 2026-10-12 至 2026-10-19，收录 2 条。

### 1. 条目

### 1.1 Opus 5.5 输入价降到 4 美元
- **一句话**：Opus 5.5 输入价降到每百万 token 4 美元
- **类别**：价格变化
- **主体**：Anthropic
- **发布日期**：2026-10-14
- **变化**：输入 $5 → $4，输出 $25 → $20（每百万 token）
- **对开发者的影响**：长任务的账单降两成。
- **可做的动作**：重算一遍月度预算。
- **可信度**：已证实
- **评分**：9/10
- **证据来源**：[1][2]

### 1.2 某框架 2.0 弃用旧接口
- **一句话**：2.0 起旧的回调接口不再维护
- **发布日期**：2026-10-16
- **变化**：无
- **评分**：6/10
- **证据来源**：[2]

### 2. 未收录但值得留意
线索 | 为什么没收 | 缺什么证据

### 3. 来源清单
1. Opus 5.5 定价 | 一手 | 2026-10-14 | https://example.com/pricing
2. 发布说明 | 一手 | 2026-10-16 | https://example.com/releases/2.0.
"""

RADAR = """\
### 1.1 Anthropic Claude Opus 5.5 发布：$4/$20
- **分类**：新模型发布 / 定价
- **发布日期**：2026-09-22
- **变更内容**：Opus 5.5 正式发布，官方定价 **$4/百万输入 token**。
  - 输入 $5 → $4
  - 输出 $25 → $20
- **旧/新情况**：Opus 前代维持 $5/$25。
- **实际影响**：Anthropic 首次把 Opus 级能力压到 $4/$20。
- **推荐行动**：加入候选池做 A/B 对照。
- **评分**：9/10
- **证据来源**：https://platform.example.com/models/opus-5-5 ；https://simon.example.com/2026/Sep/22/opus

### 1.2 GPT-6 Sol 降价
- **评分**：10/10
- **证据来源**：https://openai.example.com/index/gpt-6
"""


def test_a_discovery_report_becomes_numbered_topics_with_resolved_sources():
    items = flash.parse(DISCOVERY, "2026-10-19")
    assert [t.id for t in items] == ["2026-10-19/F1", "2026-10-19/F2"]
    first = items[0]
    assert first.title == "Opus 5.5 输入价降到 4 美元" and first.score == 9
    assert first.fields["来源网址"].splitlines() == ["https://example.com/pricing", "https://example.com/releases/2.0"]
    assert first.fields["来源明细"].splitlines()[0] == "Opus 5.5 定价 | 一手 | 2026-10-14 | https://example.com/pricing"
    assert items[1].fields["来源网址"] == "https://example.com/releases/2.0"


def test_the_headings_around_the_items_are_not_items():
    assert [t.title for t in flash.parse(DISCOVERY, "2026-10-19")] == ["Opus 5.5 输入价降到 4 美元", "某框架 2.0 弃用旧接口"]


def test_the_placeholders_the_research_prompt_fills_come_from_the_item():
    fields = flash.parse(DISCOVERY, "2026-10-19")[0].fields
    assert fields["核心角度"] == "Opus 5.5 输入价降到每百万 token 4 美元"
    assert fields["为什么有人在乎"] == "长任务的账单降两成。"
    assert fields["建议大纲"] == "重算一遍月度预算。"
    assert fields["目标受众"] == "开发者"
    assert fields["变化"].startswith("输入 $5 → $4")


def test_a_radar_item_is_read_the_same_way():
    first, second = flash.parse(RADAR, "2026-09-29")
    assert first.fields["核心角度"].startswith("Opus 5.5 正式发布，官方定价 $4/百万输入 token。")
    assert "输入 $5 → $4" in first.fields["核心角度"] and "输出 $25 → $20" in first.fields["核心角度"]  # nested bullets stay with their field
    assert first.fields["变化"] == "Opus 前代维持 $5/$25。"
    assert first.fields["为什么有人在乎"].startswith("Anthropic 首次")
    assert first.fields["来源网址"].splitlines() == ["https://platform.example.com/models/opus-5-5", "https://simon.example.com/2026/Sep/22/opus"]
    assert second.score == 10


def test_an_item_that_cites_nothing_known_has_no_urls():
    report = "### 1.1 标题\n- **证据来源**：[9]\n\n### 3. 来源清单\n1. 某页 | 一手 | 2026-10-01 | https://example.com/a\n"
    assert flash.parse(report, "2026-10-19")[0].fields["来源网址"] == ""


def test_the_newest_report_is_used_and_a_topic_is_found_by_its_id(tmp_path):
    base = tmp_path / "flash"
    base.mkdir()
    (base / "2026-10-12_ai_flash_discovery.md").write_text("### 1.1 旧的\n- **评分**：5/10\n", encoding="utf-8")
    (base / "2026-10-19_ai_flash_discovery.md").write_text(DISCOVERY, encoding="utf-8")
    (base / "notes.md").write_text("### 1.1 不是报告\n", encoding="utf-8")
    date, items = flash.load(str(tmp_path))
    assert date == "2026-10-19" and len(items) == 2
    found = topics.find(str(tmp_path), "2026-10-19/F2")
    assert found is not None and found.title == "某框架 2.0 弃用旧接口"
    assert topics.find(str(tmp_path), "2026-10-19/F9") is None
    assert topics.find(str(tmp_path), "2026-10-12/F1").title == "旧的"


def test_no_report_means_no_topics(tmp_path):
    assert flash.load(str(tmp_path)) == (None, [])
