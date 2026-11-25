# 验收：信源→抽取→交付 Agent（2026-11-20）

试验目录：`state/validation/source-extract-opt`（外接盘 symlink）。

## 改动摘要

1. 统一页面读取：`HTTP → 匿名 Chrome → OpenCode CLI webfetch → 可选 Exa`（[`core/source_pages.py`](../../core/source_pages.py) `fetch_page`），供给池与 Job 预取共用。
2. OpenCode CLI：[`core/opencode_cli.py`](../../core/opencode_cli.py)；配置 `page_readers.opencode_cli`。声明源应尽量不靠 Exa；OpenCode 仍无法绕过对全自动客户端 403 的站点（实测 openai.com）。
3. OpenCode **调研 harness**：[`core/opencode_gather.py`](../../core/opencode_gather.py) + 模型探测 [`core/opencode_models.py`](../../core/opencode_models.py)。按 Job 的关键词 / 要求（可另写 `opencode_task`）自主 websearch + webfetch，产出材料包；成稿 Agent 仍写终稿。`webfetch_markdown` 仅作读页回退管道。Go 免费模型探测失败回退 `paid_models`。配置 `gather.opencode_cli`；默认 `apply_to: all`，单 Job 可用 `opencode_gather: false`。
4. fetch-only / 无 `source_supply`：[`follow_teaser_articles`](../../core/source_pages.py) 跟读 RSS/catalog 摘要链接（引擎 [`_follow_teaser_details`](../../core/engine.py)）。
5. Exa 401/402/429：客户端缓存 `contents_exhausted`，后续付费读跳过；本地 403/缺 Chrome 优先于额度报错露出。

## 对照证据

### 科学链 `chains/ai_science_updates`（隔离 `_fetch_sources_strict`）

产物：`probe/chains_ai_science_updates.json`。

| 项 | 结果 |
|----|------|
| catalog `openai.com/news/science/` | 失败；回执现为本地 **403**（不再被 Exa 402 额度信息盖住）；站点对直连/无头 Chrome 均拒绝，Exa 亦无额度 |
| RSS | 成功 10 条 |
| 正文补读 | 已触发（`正文补读` 记入材料块）；8 次跟读均失败并标注「未能补读」 |
| 摘要长度 | 中位约 **162** 字（与改前 teaser 同级） |

结论：回退链与跟读已接通，但 **openai.com 在本机无法不靠 Exa 读正文**。符合计划停止条件——不再加清洗/截断常数；下一步应补 Exa 额度，或给科学链换可直读的稳定 spec（例如已验证的官方 md/RSS 旁路），而不是继续扩抽取算法。

### 友好站点（GitHub releases）

同机：`fetch_page` / `follow_teaser_articles` 对 `github.com/anthropics/claude-code/releases` **跟读成功**（多段正文，非一句话 teaser）。说明统一读取与跟读在可公开站点有效。

### 快讯 `monitoring/ai_flash_discovery`

全量 `source_supply.collect` 在本轮因补读量大、部分源依赖付费读而超时中止，未落完整 flash 统计。代码路径与供给池共用同一 `read_page`；单测覆盖 Exa 额度降级与可选正文。

### 单测

`tests/test_source_page_reader.py`、`tests/test_source_supply.py` 通过。

## 状态划分

| 状态 | 说明 |
|------|------|
| 表达/抽取实验 | 统一回退 + fetch-only 跟读已落地 |
| 生产入口已验证（部分） | 科学链隔离抓取跑通；openai 正文仍受站点 403 + Exa 额度限制 |
| 未完成 | 快讯全量隔离对照；科学链换源或恢复 Exa 后的二次成材料验收 |

## 不宣称

未宣称「日常成片材料质量已提升」；openai 科学链在缺 Exa 时仍只有 RSS 短摘。
