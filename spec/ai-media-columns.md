# AI 视频栏目：少数 Job，直接抓取，少搜索（2026-10-22）

原来 AI 矩阵有 27 个 Job，一轮约 $30 的 Exa（每个 Job 约 56 次请求，每次取 20 条结果带正文，约 $0.02/次）。10-21 的验证里 24 个 Job 因搜索额度用完，没联网就写了报告还标成功。重新设计：**少数 Job，订阅源直接抓取，只有必须搜索的 Job 才搜索。**

## 5 个 Job（都在 `ai` 矩阵里；矩阵里另外 26 个旧 Job 没有周期，不会自己运行）

| 栏目 | Job | 周期 | 读什么 | 搜索 | 视频 |
|---|---|---|---|---|---|
| AI 快讯 | `monitoring/ai_flash_discovery` | 每天 05:30 | TLDR AI（最近 3 期）、The Rundown RSS、Hugging Face 热门模型 | 不搜 | `flash` ×3（`reverify: false`），一份报告拆 5–10 条 |
| AI 开源雷达 | `ai_media/daily_opensource_radar` | 每天 05:45 | GitHub 近 7 天新项目（`llm`、`agent`）、HF 热门模型、HF 每日论文、TLDR | 不搜 | `report_digest` ×1 |
| AI 模型竞技场 | `ai_media/weekly_model_arena` | 每周一 06:00 | Artificial Analysis 排行榜、HF 热门模型、Rundown | 不搜 | `report_digest` ×1 |
| AI 商业观察 | `ai_media/weekly_business_watch` | 每周一 06:20 | Rundown、Import AI、TLDR | 不搜 | `report_digest` ×1 |
| AI 深度研究 | `ai_media/weekly_deep_research` | 每周一 06:40 | Import AI、HF 每日论文，再搜 | **最多 6 次**（`max_searches: 6`） | `deep_dive` ×1 |

生产配置在 `config/production/`：`flash_daily`（`flash` 配方，auto 3，`reverify: false`）、`ai_media_*`（auto 1）。原来的 `flash` 和 `point_short` 配置自动关了：`flash` 每条要重新调研，`point_short` 的配方不允许跳过调研，都是每条搜 4–20 次。

## 机制

- **`search: false`**（Job 顶层）：只读 `sources:`，不联网搜索。`ResearchAgent.run_fetch_only`：一次模型调用，不给搜索工具，也不规划查询；搜索客户端换成 `_NoSearch`，任何搜索都直接报错。
- **抓取失败要响**：`sources.fetch_all_strict`——某个源返回 200 但解析不出条目也算失败；所有源都没抓到则抛 `SourcesUnavailableError`，运行中止，不会让模型凭记忆写。部分源失败时，提示词里写明哪些没抓到，报告不得写成覆盖了它们。
- **用量**：`usage.fetched`（每个源的条数）；抓取型 Job 的 `searches` 是 0，这是正常的，判断「没抓到」看 `fetched`。
- **引用核对**：抓到的所有 URL 都算「检索到」。
- **订阅源种类**（`core/sources.py`）：`rss:`、`github:`、`arxiv:`、`hn:`，新增 `tldr:ai`、`hf_papers:daily`、`hf_models:trending`、`gh_new:关键词`、`aa:models`。解析器的测试用 `tests/fixtures/sources/` 里存的真实页面。
- **Job 自己限制搜索**：Job 顶层 `max_searches: N`。

## 取舍与没做的

- 这些简报是二手来源。价格、版本号这类硬数字，提示词要求照抄材料并标「仅二手来源 / 厂商自述」；没有再点开链接的一手核对（那要抓指定网址，不用搜索，可以以后加）。
- 周更的「商业观察」没有年度报告（Stanford AI Index、State of AI）：它们一年一版，不是周更来源。
- The Batch 没有 RSS，网页解析没做；Futurepedia 价值偏低，There's An AI For That 返回 403，都没接。
- 开源雷达、竞技场、商业观察现在是整份报告出一条 `report_digest`。一份拆成多条要把报告改成 `###` 小标题条目并声明 `topics:`，后做。
- 旧的 26 个 Job 配置保留，没有周期，不会自己运行。
