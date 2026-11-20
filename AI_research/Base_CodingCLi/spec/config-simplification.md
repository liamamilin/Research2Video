# 配置减负计划

核对日期：2026-11-20。背景见端到端总览与配置层分析讨论。

## 目标

降低「改一条产品线要扇出多文件」的无效复杂度，**不**合并 Job / 生产 / 配方三大主干。

## 原则

1. 先挪闲置、再改契约；有测试或历史成片引用的先盘点再动。  
2. 归档用子目录（加载器只扫顶层），不删磁盘证据。  
3. 每批改动可回滚，并跑相关 pytest。

## 配置层去留（摘要）

| 保留 | 原因 |
|------|------|
| Job / production / recipe | 研究、开关、写法三分职责清楚 |
| joint_planning | 合辑+单条同分批的真实需求 |
| matrices（薄） | 归属与调度 |
| 发布排除 / 策略窗（薄） | 独立开关 |

| 减负对象 | 方向 |
|----------|------|
| 成对 production + joint 第三处 | 声明合一（高影响，下一波） |
| 栏目双轨（半数 production 无栏目） | 契约二选一（高影响） |
| 十域复制 | 参数化生成（中） |
| 观众文案多处重复 | 单一事实来源（中） |
| 闲置 production / planned 栏目 | 归档（低风险，本轮） |
| theme_* 与日报混放 | 文档分家（低） |

## 本轮已做（批次 A）

1. 归档验收用 production：`deep_dive-2/3/4` → `config/production/archive/`  
2. 归档 planned 空栏目 6 个 → `config/content_columns/archive/`  
3. 测试改用仍在用的 `deep_dive` 作手动对照  

主目录 production 约 73→70；configured 栏目去掉 6 个空壳。

## 下一批候选（未动）

仍停在顶层、但**非 auto / 非合辑单条 holdout**、且多为实验或旧形态（动之前查测试与 `video.db`）：

- 旧形态：`flash`（已被 daily/each 取代，但测试/UI 仍大量引用 `flash` 键——**不能先挪**）  
- `point_short`、`ai-non_tech-*`、`ai-developer-roundup-long`：测试夹具，需先改测试再归档  
- `pai_*`、`analysis_*`、`research_ai*`、`actionable_*`：历史矩阵验证残留，脚本量极少  
- `*_illustrated` 且 `auto: 0`：手动图解，是否保留看产品是否还用手触发  

**不要归档：** `flash_each` / `chatgpt_t09_updates_each` / `ai_science_updates_each` / auto 的 illustrated —— 无栏目但在跑。

## 建议顺序

1. ~~归档明确闲置~~（本轮）  
2. 合辑+单条配置合一（快讯试点）  
3. 栏目契约：强制或降为 overlay  
4. 文案单一来源 + 归档测试夹具型旧 production  
5. 十域模板化  

## 验证

```bash
cd AI_research/Base_CodingCLi
python3 -m pytest tests/test_narration_stance.py tests/test_joint_planning.py -q
```
