# 配置减负计划

核对日期：2026-11-20。背景见端到端总览与配置层分析讨论。

## 目标

降低「改一条产品线要扇出多文件」的无效复杂度，**不**合并 Job / 生产 / 配方三大主干。

## 原则

1. 先挪闲置、再改契约；有测试或历史成片引用的先盘点再动。  
2. 归档用子目录（加载器只扫顶层），不删磁盘证据。  
3. 每批改动可回滚，并跑相关 pytest。  
4. **禁止合并 production 身份**：合辑与单条（如 `flash_daily` / `flash_each`，以及 ChatGPT T09 成对）是同报告上的两条产品线，须保持独立 production 键；`video.db`、批次、出片与统计都靠该键区分。不得为「少一个 YAML」做身份合并或迁库。

## 配置层去留（摘要）

| 保留 | 原因 |
|------|------|
| Job / production / recipe | 研究、开关、写法三分职责清楚 |
| joint_planning | 合辑+单条同分批的真实需求 |
| 成对 production 双键（flash / T09） | 拆法、热度、栏目、稿件身份不同；扇出可接受 |
| matrices（薄） | **仅**成员归属、暂停、月搜上限、事件避重、视频默认；不再向报告注入关键词/领域 |
| 发布排除 / 策略窗（薄） | 独立开关 |
| `sources.yaml` | 多 Job 共享信源注册表 |
| `system.yaml` → `research.mode` | 引擎全局默认 |

### 研究侧（信息源 → 报告）心智模型

```text
Job          = 这份研究怎么跑、写出什么报告（主配置；含 domain / keywords / audience_channel）
sources.yaml = 共享信源注册表
system.yaml  = 引擎全局默认
排期 / 链路  = 何时触发、是否接视频（不改正文写法）
```

| 研究侧对象 | 去留 | 说明 |
|------------|------|------|
| Job | **必留** | 报告写法的唯一事实来源 |
| `sources.yaml` | **必留** | 共享注册表，不内联进每个 Job |
| 排期 / 链路 / `research.mode` | **必留** | 触发与引擎行为，非题目定义 |
| matrices | **变薄** | 分组与运维；`domain`/`keywords` 已收回成员 Job |
| `content_strategy.yaml` 的 `channels:` | **保留** | 共享受众文案库 |
| `content_strategy.yaml` 的 `jobs:` 映射 | **收回 Job** | Job 写 `audience_channel`；映射仅兼容回退 |
| `source_supply/` | **特例** | 多 Job 共用自动池时才需要；不是人人必懂 |
| `production_policy` | **特例** | 仅冻结期次窗的 Job；普通监测可忽略 |

| 减负对象 | 方向 | 优先级 |
|----------|------|--------|
| 闲置 production / planned 栏目 | 归档 | 已做批次 A；下一批继续 |
| 研究侧矩阵注入 / 频道映射 | 收回 Job（批次 B0–B2） | **进行中** |
| 栏目双轨（半数 production 无栏目） | 契约二选一 | **高**（视频侧） |
| 历史残留 `pai_*` / `analysis_*` 等 | 清引用后归档 | **高** |
| 观众文案多处重复 | 单一事实来源 | 中 |
| 十域复制 | 参数化生成 | 中 |
| theme_* 与日报混放 | 文档分家 | 低 |
| 成对配置「声明写一处、运行仍双键」 | 可选薄封装 | **低 / 可选**（非身份合并） |

## 快讯成对：明确不做身份合一

- `flash_daily`：整期合辑；栏目 `ai_flash` 只绑这一侧。  
- `flash_each`：每条一期 + 热度/多角度门槛；可挂说明型 Job Profile。  
- 配对：`config/joint_planning.yaml` 的 `pairs`。  

二者共用 Job、配方、`auto`、`reverify`，但**不是同一配置的两种写法**。  
若将来做「声明合一」，加载后仍须注册稳定的 `flash_daily` / `flash_each`（及 T09 成对）两个键；在频繁同时改两对共用字段之前不必开做。

## 本轮已做（批次 A）

1. 归档验收用 production：`deep_dive-2/3/4` → `config/production/archive/`  
2. 归档 planned 空栏目 6 个 → `config/content_columns/archive/`  
3. 测试改用仍在用的 `deep_dive` 作手动对照  
4. 成对配置：文档定为保留双键，取消「硬合并」高优项  

主目录 production 约 73→70；configured 栏目去掉 6 个空壳。

## 研究侧批次 B（信息源 → 报告）

1. **B0** 锁定上表心智模型（本文 + 总览 §0 + `chain-research` / `matrices`）  
2. **B1** 矩阵变薄：成员 Job 自带 `domain` / `keywords`；`for_run` 不再合并矩阵关键词；矩阵只做分组与避重  
3. **B2** Job 显式 `audience_channel`；`job_channel` 优先读 Job，映射表仅回退  
4. **B3** `source_supply` / `production_policy` 标成特例（见上表）  
5. **B4** `education` 无研究 Job → `archived: true`（主题库文件保留；主题生成测试改为跳过已归档矩阵）。`ai` 原本已归档。  
6. **Job 归档（在用优先）**：非 auto / 非活跃产品矩阵 / 非启用排期的 Job 移入 `jobs/archive/`（含 `video/*` 生成物与旧 PAI/分析 Job）；挂空 Job 的 production 同步进 `config/production/archive/`；`deep_dive` 改绑 `ai_media/weekly_deep_research`。  

在用 Job 的配置问题清单见 [job-config-issues.md](job-config-issues.md)。

## 快讯扇出收紧（2026-11-20）

`monitoring/ai_flash_discovery` 日常只留 `flash_daily` / `flash_each`。  
旧键 `flash`、`point_short`、`ai-developer-roundup-long`、`ai-non_tech-one_point-short`、`ai-non_tech-quick_take-short` → `config/production/archive/`；`prod_env` 测例临时拷回。配方键 `flash` / 选题源 `flash` 仍在用，未动。

## 下一批候选（视频侧 / 未动）

仍停在顶层、但**非 auto / 非合辑单条 holdout**、且多为实验或旧形态（动之前查测试与 `video.db`）：

- `pai_*`、`analysis_*`、`research_ai*`、`actionable_*`：历史矩阵验证残留，脚本量极少（多数已在 archive）  
- `*_illustrated` 且 `auto: 0`：手动图解，是否保留看产品是否还用手触发  

**不要归档：** `flash_daily` / `flash_each` / `chatgpt_t09_updates_each` / `ai_science_updates_each` / auto 的 illustrated —— 无栏目但在跑。

## 建议顺序

1. ~~归档明确闲置~~（批次 A）  
2. ~~合辑+单条身份合一~~（**否决**；可选声明封装降为低优）  
3. ~~研究侧矩阵/频道收回 Job~~（批次 B）  
4. ~~快讯旧 production 扇出~~（日常仅 daily/each）  
5. 栏目契约：强制或降为 overlay  
6. 清引用后归档 `pai_*` / `analysis_*` 等历史残留  
7. 文案单一来源  
8. 十域模板化  

## 验证

```bash
cd AI_research/Base_CodingCLi
python3 -m pytest tests/test_narration_stance.py tests/test_joint_planning.py tests/test_matrices.py tests/test_audience_expansion.py -q
```
