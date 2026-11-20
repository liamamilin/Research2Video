# 在用 Job 配置问题清单

核对日期：2026-11-20。范围：归档不用 Job 之后，仍在日常路径上的 Job（约 49 个；其中 production `auto` 38 个）。  
背景：[config-simplification.md](config-simplification.md)、[chain-research.md](chain-research.md)。

**判定「在用」**：production `auto=1`，或活跃产品矩阵成员，或 schedules 中 `enabled` 的 Job。已迁入 `jobs/archive/` 的不在此列、不再打磨。

---

## 结论摘要

字段级硬伤已经很少：`domain` / `audience_channel` / prompt / 输出路径在生产 Job 上基本齐全。  
**快讯 production 扇出已收紧**（只留 `flash_daily` / `flash_each`）。  
字段扫库抓不住的一类问题（如插画 prompt 内嵌 10～16 道「题目清单」、同矩阵 prompt 漂移）见 **[job-prompt-audit.md](job-prompt-audit.md)**（含发现方法 + 2026-11-20 全库重扫）。  
（曾把四周报 `sources: [tag:…]` 误判为「只有 1 个源」——`tag:` 会在 `sources.yaml` 展开为十几个实际源，见下「如何查看来源」。）

---

## 如何查看 Job 的来源配置

概览页**不展示** `sources` 列表。看配置请：

1. 打开 Job：`/jobs/<路径>`（如 `/jobs/tech%2Ftech_weekly`）  
2. 切到 **「YAML 配置」** 页签  
3. 找 `sources:` / `search:` 字段  

常见写法：

| YAML 写法 | 含义 |
|-----------|------|
| `source:ieee_spectrum` | 引用 `config/sources.yaml` 里 id 为 `ieee_spectrum` 的一条 |
| `tag:tech` | 引用注册表里 **带 `tech` 标签的全部源**（一条声明 → 多个实际源） |
| `rss:https://…` | 内联 RSS，不经注册表 id |

要看 `tag:tech` 展开成哪些源：打开前端 **信息源库**（`/sources`），或直接搜 `config/sources.yaml` 里 `tags:` 含 `tech` 的条目。

**展开数量核对（2026-11-20）**：

| Job | YAML | 展开后实际源数 |
|-----|------|----------------|
| `tech/tech_weekly` | `[tag:tech]` | 19 |
| `industry/industry_weekly` | `[tag:industry]` | 16 |
| `consumer/consumer_weekly` | `[tag:consumer]` | 18 |
| `geography/geography_weekly` | `[tag:geography]` | 14 |

因此「四周报源过稀」**撤销为配置缺陷**；若周报仍差，应查抓取质量 / 采集窗 / prompt，而不是先加声明行。

---

## P0 — 真问题

### 1. 快讯 Job 挂了过多 production — 已处理（2026-11-20）

`monitoring/ai_flash_discovery` 日常只保留 `flash_daily` / `flash_each`。旧键已迁入 `config/production/archive/`（`flash`、`point_short`、`ai-developer-roundup-long`、`ai-non_tech-one_point-short`、`ai-non_tech-quick_take-short`）。测试夹具经 `prod_env` 临时拷回。

---

## P1 — 设计如此，打磨时勿当缺字段

### 1. 约 23 个 auto Job 无 `topics:`（整份报告路径）

典型：十域 `*_daily` / `*_weekly`，以及多数 `ai_media/*` digest。  
靠 audience 整理整稿，不拆条导入 topics 表。与快讯 / ChatGPT（有 `topics`）是两套选题语义。

**打磨重点**：整份报告是否好用、audience 结果是否贴旁白；不要为「对齐」空补 `topics:`。

涉及（抽样）：

- `ai_media/daily_opensource_radar`、`weekly_model_arena`、`weekly_business_watch`
- `science|company|economy|…|tech` 的 daily / weekly

### 2. `ai_media/weekly_deep_research` 允许搜索

`search` 未关（默认可搜）+ `max_searches: 6`；栏目说明为唯一联网深挖。属产品选择，除非改为纯订阅源再动。

### 4. 图解 Job 源少

`*_illustrated` 走 `knowledge`，1–2 个源是预期形态；勿用日报源数量标准衡量。

### 5. 插画「题目清单」写在 prompt 里（设计实现问题）

十域 `*_illustrated` 均在 prompt 内嵌 10～16 道种子题；science/economy 已 `auto: 1`，其余多为 `auto: 0` 试点。  
意图是冷启动兜底，观感却像栏目题库上限，且未接到主题分类。详见 [job-prompt-audit.md §4.1](job-prompt-audit.md)。

---

## 已较干净（本轮扫库未见硬伤）

| 线 | 状态 |
|----|------|
| ChatGPT T01–T09 | 频道、源、prompt 形态整齐；T09 成对 production 正常 |
| 快讯字段 | `search: false`、多源、supply、`audience_channel: flash`、`topics` 齐全 |
| 科学链 `chains/ai_science_updates` | 有 topics；单独产品线 |
| 教程 `ai_media/ai_tool_tutorials` | 源较充足、有 topics |
| 十域周报 `tag:…` | 展开后 14–19 个实际源，**不是**声明过稀 |

未见（在用集合内）：缺 prompt、矩阵成员缺 domain、`audience-v1` 无频道、question 无 audience、明显未知的 `source:` 引用。

---

## 在用 Job 盘点（便于对照）

### production `auto`（38）

- 快讯：`monitoring/ai_flash_discovery`
- AI 栏目：`ai_media/ai_tool_tutorials`、`daily_opensource_radar`、`weekly_model_arena`、`weekly_business_watch`、`weekly_deep_research`
- ChatGPT：`chatgpt_acquisition/t01`…`t09`
- 科学链：`chains/ai_science_updates`
- 十域 daily/weekly（及部分 illustrated，如 science/economy illustrated）

### 其它在用（非主报告或手动）

- 启用排期的 `source_discovery/*`、`system/material_collection`（扩池 / 素材，不是主报告写法）
- 矩阵内手动图解等 `auto=0` 但仍属活跃域的 Job

---

## 建议打磨顺序

1. ~~收紧快讯旧 production 绑定~~（已完成）  
2. 按 [job-prompt-audit.md](job-prompt-audit.md) 顺序处理：科学链 → 插画题库身份 → T05/T06 与 tech/software 漂移 → 教程库存  
3. 按一条产品线深挖 **实际抓取质量 + 成片**（建议快讯或 ChatGPT）  
4. 十域周报若观感差：查抓取/采集窗/整理，而不是先加 YAML 行数  

每步完成后在本文件更新「已处理」与证据（对照报告 / 成片），避免把整份报告路径或 `tag:` 展开误标成配置缺陷。

---

## 已处理

| 日期 | 项 | 说明 |
|------|-----|------|
| 2026-11-20 | 不用 Job 归档 | 约 106 个迁入 `jobs/archive/`；挂空 Job 的 production 进 `config/production/archive/`；见 [config-simplification.md](config-simplification.md) 批次说明 |
| 2026-11-20 | 更正「四周报源过稀」 | `tag:` 展开后源充足；补充「如何查看来源」 |
| 2026-11-20 | 快讯 production 扇出 | 旧 5 个键进 `config/production/archive/`；日常仅 `flash_daily` / `flash_each`；相关测例与夹具已对齐 |
| 2026-11-20 | Prompt 行为审计 | 新增 [job-prompt-audit.md](job-prompt-audit.md)：发现方法 H1–H6 + 全库重扫；标出插画题库、科学链、矩阵漂移等 |
| 2026-11-20 | 逐 Job 阅读分析 | 主链全部通读结论见 [job-by-job-analysis.md](job-by-job-analysis.md) |
| 2026-11-20 | 按计划处理配置问题 | 科学链 / deep_dive 配方与双键 / T05·T06 尾段 / tech·software 加厚 / 插画题库收成示例 / 教程静态库存 / 旁路 production 归档；见 [job-by-job-analysis.md 处理计划](job-by-job-analysis.md) |
