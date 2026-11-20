# 在用 Job 逐条阅读分析

核对方式：通读 Job YAML，并对照其 production / 配方 / `content_strategy` 频道；先写清这条线在做什么，再判断问题与原因。  
不按扫描命中表定罪。

范围：`production auto=1` 唯一 Job、十域 illustrated、discovery/system 代表，以及深挖/日报旁路 production。  
状态：**主链已全部读完并记录**（2026-11-20）。发现方法备忘仍见 [job-prompt-audit.md](job-prompt-audit.md)。

---

## 逐条记录

### 1. `monitoring/ai_flash_discovery`（AI 快讯发现）

**这条线在做什么**

- 角色：固定订阅源 + 官方页上的「本期值得讲的 AI 变化」发现器；`search: false`，初稿不联网。
- 输出：`topics.format: flash` 条目报告 → 选题池 alias `flash`。
- 成片：成对 production  
  - `flash_daily`：整期合辑，`reverify: false`  
  - `flash_each`：逐条短视频，带热度/多角度 `selection`，同样不再调研  
- 频道：`audience_channel: flash`，与 strategy 中「普通人钱/工作/生活」一致；另有 `question_material: question-v1` 选题后补材。

**Prompt 在要求什么**

- 前半：从已有材料挑「改能力/成本/门槛/操作」的变化；B 站热词有实质进展才优先成条；缺细节就留给补材或换题；允许「本次暂无新题材」。
- 格式：`### 1.1` + 一句话 / 日期 / 证据；正文要够写稿，不预写短口播。
- 尾段：普通人问题优先、材料四类不凑齐、按值得制作排序——与 `audience-v1` / strategy `flash` 频道同向。

**判断**

| 点 | 结论 | 原因 |
|----|------|------|
| 缺 prompt / 禁搜却让搜 | 无 | `search: false` 与「用已取得公告/订阅」一致 |
| 源与栏目 | 基本匹配 | 官方更新、雷达、新闻、B 站热词都服务「变化发现」；与 T09 可能抢官方更新源，但 T09 prompt 侧有分工意识，快讯侧用 B 站热词+泛 AI 变化，可并存 |
| 输出格式 | 匹配 | flash 条目字段与 `topics.format: flash`、配方 `sources: [flash]` 对齐 |
| production | 已收紧 | 日常只留 daily/each；旧扇出已归档 |
| Prompt 可改进 | 有、但不致命 | ① 中段多次强调「口播/标题留给作者」，边界说一次即可，把篇幅留给「对象+变化+依据」。② 尾部选题通则与 `content_strategy.flash` / audience 运行时重复，属横向问题，不是快讯独有坏逻辑。③ 英文 name「AI Flash Discovery」与中文产品并存，不影响研究。 |
| 是否该大改 | 否 | 结构清楚，主风险在抓取质量与成片节奏，不在 YAML 形态错误 |

**本条结论**：可用。优先观察真实报告是否「有热词无实质仍硬写」、补材是否接得上；prompt 只需小幅去重，不是重构对象。

---

### 2. `chains/ai_science_updates`（AI 科研进展）

**这条线在做什么**

- 来源：链路向导建出来的产品线（`config/chains/ai_science_updates.yaml`）；主源是 OpenAI 科学博客 catalog（`chain_source_mwlrtqz9`），另加 `cg_openai_news`；`search: false`。
- 输出：仍用 **flash 条目格式**（`### 1.1` + alias `ai_science_updates` + 前缀 `AAA`）。
- 成片：仅 `ai_science_updates_each`（单条、`reverify: false`、`auto: 1`），配方 `chain_ai_science_updates`（从快讯 quick_take 拷出来的短视频壳，连 description 都还是「抓住一个新变化…」）。
- 发布：在 `bilibili_auto_upload` 名单里；链配置里 `schedule_armed: false`。
- **没有** `audience_channel` / `content_strategy` / `question_material`；`content_strategy.yaml` 里也搜不到这条。

**Prompt 在要求什么**

- 一整段挤在单行字符串里：跟踪 AI 科研公开进展 → 讲清研究问题、AI 帮到哪一步、证据边界。
- **「首期优先从 OpenAI 科学博客选粒子物理或数学案例」**——启动试点话术，却仍留在日常 auto Job 里。
- 输出约定与「暂无新题材」与快讯同族。

**判断**

| 点 | 结论 | 原因 |
|----|------|------|
| 与快讯同构却瘦一截 | 是问题 | 同样 flash 条目 + 短视频出片，但缺 audience/补材管线；strategy 无频道，选题通则不会按频道注入 |
| 「首期」话术 | 应改 | 每个运行日都会被这句话拽向粒子物理/数学案例，即使博客本期是别的科学进展；这是启动残留，不是稳定编辑方针 |
| `keywords: []` | 小问题 | 禁搜下关键词不驱动抓取，但丢失领域锚点，也不利于和其它 Job 区分 |
| 源是否合理 | 窄但自洽 | 向导绑定 OpenAI 科学博客 + 一条 OpenAI 新闻；若产品就是「跟这个 catalog」，可以；若要「AI×科学」广度，源明显不够，那是产品范围问题不是笔误 |
| 配方文案 | 错位 | 配方 description/outro 仍像泛快讯「新变化」，没有「科研证据/适用范围」语义；Job 与配方叙事不一致 |
| production | 单键 each，合理 | 没有 daily 合辑；与「一条科研案例一期」匹配 |

**本条结论**：**值得改，且应优先于多数「漂移」项。**  
原因不是源声明行数，而是：试点话术锁死选题偏好 + 有 auto/上传却未接入 audience 管线 + 配方仍是快讯壳。  
改法方向：删掉「首期…粒子物理或数学」；写成稳定的科研案例标准（问题、AI 角色、证据边界、适用范围）；补 `audience_channel`（或显式文档化「本链不走 audience-v1」）；配方 description 改成科研进展语义；酌补 keywords。

---

### 3. `chatgpt_acquisition/t01_product`（T01 产品与能力认知）

**这条线在做什么**

- ChatGPT 获客矩阵第一格：回答「ChatGPT 到底能做什么？」，获客作用「吸引新用户」。
- `search: false`；源偏官方能力/学习/帮助/更新（`cg_capabilities`、`cg_learn_videos`、`cg_help`、`cg_release_notes`）；`source_history: false`。
- 输出 `topics.format: items`，alias `chatgpt_t01_product`，前缀 `CGA`；`audience_channel: t01` + `audience-v1` + `question_material`。
- 超时 7200：注释写明要读多日材料、可能分批——与「入门认知」源并不算极多，更像矩阵统一留长超时。

**Prompt 在要求什么**

- 矩阵公共框：获客题材研究 → 字段表（含操作与演示、画面建议、获客作用）。
- T01 专属：从官方演示挑一个普通人能懂的能力；重结果不重功能名；不能凭创作者标题补写。
- 尾段：频道行「不把功能表当选题」+ 全量 audience 通则 + 不预写口播。

**判断**

| 点 | 结论 | 原因 |
|----|------|------|
| 题材与源 | 匹配 | 能力/学习/帮助页支撑「能做什么」；不是比价或故障库 |
| 与下游分工 | 清楚 | 研究留材料与画面建议；口播交作者；有「暂无新题材」 |
| 「画面建议」在研究步 | 可讨论 | 矩阵统一字段；略提前做分镜，但不算错配 |
| 尾段与 strategy 重复 | 横向问题 | `选题与材料组织：T01…` 与 `content_strategy.channels.t01` 同义，再加长 audience 段 |
| 硬伤 | 无 | 频道、topics、禁搜、输出字段齐全 |

**本条结论**：矩阵里的**合格样板**。后面 T02–T09 以本条为参照，只记差异；差异大的（尤其尾段缺失或题材边界）单独展开。

---

### 4–11. ChatGPT 获客矩阵 T02–T09

以下均已逐文件打开。与 T01 **同构**的部分：矩阵公共头、items 字段表、`search: false`、`audience-v1` + `question_material`、各 `audience_channel: t0x`、独立 alias/prefix。下面只写**差异与判断**。

#### 4. `t02_scenarios`（应用场景与成果）

- **差异**：核心问题「能帮我做什么」；源为 Learn/Stories/How I AI/Business stories；频道行强调任务起点→产物。
- **判断**：与 T01 同级合格；源服务「场景案例」匹配。
- **结论**：可用。

#### 5. `t03_tutorials`（使用方法与教程）

- **差异**：`sources_limit: 16`；源含 Academy/Kevin/AI Advantage/prompts；强调 Learn 任务式演示 + 中文重写。
- **判断**：与多产品教程 Job 可能题材重叠，但本条锁定 ChatGPT；合格。
- **结论**：可用。

#### 6. `t04_testing`（实测与能力验证）

- **差异**：强调测试输入/观察点单列；频道行「公开案例不改称亲测」。
- **判断**：边界清楚，防虚假亲测；源偏创作者评测，需靠「待实测」字段约束。
- **结论**：可用；成片时核对是否误写成亲测。

#### 7. `t05_comparison`（AI 产品横向选择）— 有实质问题

- **差异**：超时 2400；`source_history` + `dossier_sources`；源面极宽（竞品定价/新闻/中文媒体/Reddit）；字段多「比较对象」；明确「自家档位归 T06」。
- **问题**：尾段只有短版「选题优先考虑…」，**缺少** T01 式「选题与材料组织：T05…」+ 全量 audience 三句 +「不预写口播」。频道 `t05` 在 strategy 有文案，但 Job 尾段没接上。
- **结论**：**应补齐尾段与 T01 对齐**；题材边界与源设计本身合理。

#### 8. `t06_value`（套餐与付费价值）— 同 T05 尾段问题

- **差异**：源为定价/Plus/更新/社区；强调价格币种地区渠道与页面日期；`source_history: context`。
- **问题**：与 T05 一样短尾，缺「选题与材料组织：T06…」及完整 audience/口播边界段。
- **结论**：**补齐尾段**；与 T05 边界（跨品牌 vs 自家档位）写清楚是优点，保留。

#### 9. `t07_subscription`（订阅与支付）

- **差异**：源为账单/地区/取消/支付问答；强调入口与排查分支；完整尾段在。
- **结论**：可用。

#### 10. `t08_troubleshooting`（使用问题与避坑）

- **差异**：源含 status/errors + 中文社区/Reddit；强调症状→下一步；完整尾段在。
- **结论**：可用。

#### 11. `t09_updates`（产品动态与热点）

- **差异**：与快讯共用更新/雷达源；明文分工（ChatGPT 用户能力 vs API 交 AI 栏）；成对 production `chatgpt_t09_updates` / `_each`；画面不要求双账号录屏；「暂无新题材」条件更严（含未采用旧材可补报）。
- **判断**：矩阵里最完整的一条；与快讯分工写在 prompt 里，合理。
- **结论**：可用，可作「动态类」样板。

**ChatGPT 族小结**：T01–T04、T07–T09 结构健康；**唯一配置级缺口是 T05/T06 尾段漂移**。横向重复 audience 长尾九份皆有（T05/T06 反而短），属统一减负议题，非单条坏逻辑。

---

### 12. `ai_media/ai_tool_tutorials`（AI 工具教程）

**在做什么**：多产品官方页/Learn 上每天最多 3 条可跟做教程；`topics` items + `daily_limit: 3`；`source_history: context` + `{reported_events}`。

**Prompt**：产品列表写死；「首批库存已覆盖」静态知识点清单 + 动态已报道；字段含系列/分类；禁止口播声称亲测。

**判断**

| 点 | 结论 | 原因 |
|----|------|------|
| 栏目目标 | 清楚 | 短教程、可见结果、最多三条 |
| 静态库存 | **问题** | 与 `{reported_events}` 重叠；会膨胀并挤占研究指令；属去重账本误放进 prompt |
| page: 源 | 合法 | 引擎支持；App Store/文档页符合「官方资料」 |
| 与 T03 | 可并存 | T03 单 ChatGPT；本条跨产品 |

**结论**：**规则留 prompt，静态「首批库存」外置或并入动态去重**；其余可用。

---

### 13. `ai_media/daily_opensource_radar`（开源雷达）

**在做什么**：开发者向；GitHub 新仓 / HF / TLDR / 雷达；整稿 `### 2.1`，无 topics；`audience_channel: ai_opensource`。

**判断**：源与「用途→环节→特点」研究头匹配；无 topics 是整稿路径预期。尾段 audience 重复属横向。  
**结论**：可用。

---

### 14. `ai_media/weekly_model_arena`（模型竞技场）

**在做什么**：榜单/价格/速度；`aa:models` 等；**本期只选一个主问题**（周观察句式）。

**判断**：明确反对「照榜播报」，与源类型匹配。  
**结论**：可用。

---

### 15. `ai_media/weekly_business_watch`（商业观察）

**在做什么**：收费/渠道/竞争；Rundown/Import AI/TLDR；单主问题周观察。

**判断**：要求讲成「产品与使用关系」而非融资标题党，合理。  
**结论**：可用。

---

### 16. `ai_media/weekly_deep_research`（深度研究）— 另有绑定问题

**在做什么**：description 写明「唯一联网」；`max_searches: 6`，未写 `search: false`（与其它 auto 不同，是故意的）；Import AI + HF 论文选题再补搜；单主问题；频道 `ai_deep`。

**绑定**：

- `ai_media_deep_dive`：`auto: 1`，recipe `deep_dive` ← **日常主键**
- `deep_dive`：`auto` 未开，同一 Job + 同一配方 ← **旁路重复**
- 配方 `deep_dive.yaml` 仍写 `topic.sources: [opportunities]`，而 opportunities Job **已归档**；因 `whole_report: true`，整稿主路径仍 usable，但选题浏览器会脏。

**判断**：Job 本身（可搜深挖）产品选择成立；问题在 **production 双键 + 配方残留归档源**。  
**结论**：Job prompt 可用；**应归档/停用无 auto 的 `deep_dive` production，并改配方 topic.sources**（清空或改为与本 Job 一致的整稿语义）。

---

### 17–26. 十域日报 `*_daily`（均已打开）

共性：`search: false`；无 `topics`（整稿）；`audience-v1` + 全量尾段；production 多走 `topic_digest` 类整稿。

| Job | 研究头形态 | 判断 |
|-----|------------|------|
| `company/company_daily` | 厚：「公司这一招」 | 合格 |
| `consumer/consumer_daily` | 厚：「一处体验变化」 | 合格；源含评测/召回，匹配 |
| `culture/culture_daily` | 厚：「读懂一件作品」 | 合格 |
| `economy/economy_daily` | 厚：「变化怎样传到你」 | **厚版样板**；另挂 `ai-non_tech-deep_dive-long-2`（auto=0）旁路 |
| `geography/geography_daily` | 厚：「一个地方怎样运转」 | 合格 |
| `history/history_daily` | 厚：「回到那个现场」 | 合格 |
| `industry/industry_daily` | 厚：「产业链的一处变化」 | 合格 |
| `science/science_daily` | 厚：「科学新理解」 | 合格 |
| `software/software_daily` | **瘦**：通用「根据素材…发现值得讲的新事情」+ 一句软件关注点 | **应加厚**，对齐同族 |
| `tech/tech_daily` | **瘦**：同上泛用头 + 一句技术场景 | **应加厚**；另挂 `ai-non_tech-headline-short`（auto=0）旁路 |

**日报族结论**：8 条厚版可用；**tech/software 研究意图未进化到同族水平**，这是真问题。旁路 non_tech production 是绑定噪音，不是 Job prompt 本身。

---

### 27–36. 十域周报 `*_weekly`（均已打开）

共性：多数强调「一个主问题 / 勿新闻列表」；`search: false`；整稿无 topics。

| Job | 形态 | 判断 |
|-----|------|------|
| company/consumer/culture/economy/geography/history/industry/science `*_weekly` | 厚版域叙事 | 合格 |
| `consumer_weekly` / `industry_weekly` / `tech_weekly` 源为 `tag:…` 单行 | 声明少但 tag 会展开 | **不是**源过稀；见 job-config-issues |
| `software/software_weekly` | **偏短**：有域意图，但缺「下游作者收口」等完整骨架 | **应略补**到同族周报厚度 |
| `tech/tech_weekly` | **瘦**：仍是泛用「根据素材…」头 | **应加厚**，与 tech_daily 一并改 |

**周报族结论**：与日报同一病灶——**tech（及偏短的 software_weekly）落后于已定制的兄弟域**。

---

### 37–46. 插画 `*_illustrated`（均已打开）

共性：`knowledge: true` + `search: false`；运行时注入往期标题 + 选题参考（`knowledge_job`）；prompt 内 **10～16 道题目清单** 作兜底；配方 `illustrated_explainer`。

| Job | auto | description | 域方向（清单示例） |
|-----|------|-------------|-------------------|
| `science_illustrated` | **1** | 未写试点 | 身边科学原理（金属/木头冷热…） |
| `economy_illustrated` | **1** | 未写试点 | 财经机制（奶茶价格…） |
| `tech_illustrated` | 0 | 试点手动 | 身边技术原理（冬天电池…） |
| `software_illustrated` | 0 | 试点手动 | 软件道理（下载 99%…） |
| `company/consumer/culture/geography/history/industry` illustrated | 0 | 试点手动 | 各域种子题 |

**选题优先级（读自 prompt）**：源灵感 → 文内清单 → 自拟。  
**为何别扭**：清单是冷启动种子，却长得像栏目题库；与主题分类/工作台平行，未接上。  
**science/economy 已 auto**，仍用同一套种子结构，身份比「试点」更重。

**结论（十条共同）**：

1. 知识路径本身（不联网、单题底稿、避重）**设计成立**。  
2. **文内长题库应外置或缩成风格示例**；prompt 只留选题标准。  
3. **产品收口**：8 个 auto=0 试点是保留手动、升级，还是归档，需拍板。  
4. 尾段仍塞日报式 audience 长段，与 knowledge 单题略重复，可随横向减负处理。

---

### 47. 旁路与基础设施 Job（已抽读代表 + 对照目录）

#### `source_discovery/*`（12，含 tech 等）

- 代表 `source_discovery/tech.yaml`：短 prompt「按矩阵栏目定位发现和接入长期来源」；`source_discovery: tech`；手动排期。
- **判断**：扩池旁路，不是主报告写法；形态正常。
- **结论**：不按内容 Job 标准打磨 prompt；另审「是否还有人定期跑」。

#### `topic_discovery/*`（10：science/industry/…/chatgpt_acquisition/ai_media；无 tech 文件）

- 代表 `topic_discovery/science.yaml`：动态读栏目做分类主题发现；极短 prompt。
- **判断**：主题工作台上游；靠代码而非长 prompt。
- **结论**：同上，非本轮内容 prompt 主战场。

#### `system/material_collection`

- `source_collection: true`，不调研究模型；prompt 一句「按生产策略积累」。
- **结论**：系统任务，无内容 prompt 问题。

#### 旁路 production（读绑定，非独立 Job）

| 键 | 挂在 | 问题 |
|----|------|------|
| `deep_dive`（auto 关） | `weekly_deep_research` | 与 `ai_media_deep_dive` 重复；配方 `opportunities` 残留 |
| `ai-non_tech-headline-short` | `tech_daily` | auto=0 旧键，改日报时噪音 |
| `ai-non_tech-deep_dive-long-2` | `economy_daily` | 同上 |

---

## 总表（读完后）

| 优先级 | Job / 族 | 问题本质 |
|--------|-----------|----------|
| P0 | `chains/ai_science_updates` | 首期话术锁选题 + 无 audience 管线 + 配方仍是快讯壳 |
| P0 | `deep_dive` 配方 + 双 production | 归档源 `opportunities` 残留；深挖 Job 扇出 |
| P1 | 插画十条 | 文内题库当栏目；science/economy 已 auto 需先收口策略 |
| P1 | ChatGPT T05/T06 | 尾段相对矩阵样板缺失 |
| P1 | `tech_daily` / `tech_weekly` / `software_daily`（及偏短 `software_weekly`） | 研究头未进化到同族厚版 |
| P2 | `ai_tool_tutorials` | 静态已覆盖清单应外置 |
| P2 | 日报旁路 non_tech production | 绑定噪音 |
| — | 快讯、T01–T04/T07–T09、多数十域厚版、ai_media 开源/arena/business | 结构可用；主风险在抓取与成片，不在 YAML 形态 |
| — | source/topic_discovery、material_collection | 旁路/系统，另册 |

---

## 读法约定

每条：**在做什么 → Prompt/配置要求 → 判断 → 结论**。  
同构族逐文件打开后，用「同构 + 差异」书写；本文件 ChatGPT / 插画 / 十域均已点开对应 YAML，不是关键词扫描代替阅读。

进度：主内容 Job 与旁路代表 **已读完并记录**（2026-11-20）。

---

## 处理计划（2026-11-20）

按「先修会脏数据 / 锁死选题的，再对齐矩阵漂移，最后减噪音」执行。本轮**做配置与 prompt 修正**；不扩新校验闸门，不成片验收（成片另开）。

| 步 | 优先级 | 动作 | 完成判据 |
|----|--------|------|----------|
| A | P0 | 科学链：去「首期」话术；补 keywords / `audience_channel` / strategy；配方文案改科研语义 | Job+strategy+配方一致，无启动期锁题 |
| B | P0 | `deep_dive` 配方去掉 `opportunities`；归档无 auto 的 `deep_dive` production，日常只留 `ai_media_deep_dive` | catalog 无 unknown opportunities；单 auto 键 |
| C | P1 | T05/T06 尾段对齐 T01（频道行 + audience + 不预写口播） | 与 T01 尾段结构一致 |
| D | P1 | 加厚 `tech_daily` / `tech_weekly` / `software_daily`；补全 `software_weekly` 收口 | 开头为域定制「为『…』」式，不再泛用套话 |
| E | P1 | 插画（先 science/economy auto）：长题库改为 3 道「风格示例」+ 明确非题库上限；试点八条同样收短 | prompt 不再像 12 题栏目上限 |
| F | P2 | 教程去掉静态「首批库存」段，保留 `{reported_events}` | prompt 无静态已覆盖表 |
| G | P2 | 归档 `ai-non_tech-headline-short`、`ai-non_tech-deep_dive-long-2`；测例夹具按需拷回 | 顶层 production 无这两旁路 |
| H | — | 更新本文件 / job-config-issues「已处理」；跑 production / topic 相关测例 | 相关测例通过 |

**本轮不做**：批量删除全体 Job 的 audience 长尾（需对照跑报告）；插画接主题工作台；改抓取质量本身。

### 执行记录

| 步 | 状态 | 说明 |
|----|------|------|
| A | 已做 | `jobs/chains/ai_science_updates.yaml` 重写；strategy 增 `ai_science`；配方文案改科研语义 |
| B | 已做 | `deep_dive` 配方 `sources: []`；`deep_dive` production 进 archive；日常 `ai_media_deep_dive` |
| C | 已做 | T05/T06 尾段对齐 T01 |
| D | 已做 | tech/software daily·weekly 研究头加厚 |
| E | 已做 | 十条 illustrated 均改为 3 道风格示例 |
| F | 已做 | 教程去掉静态首批库存 |
| G | 已做 | non_tech 两条旁路 production 进 archive |
| H | 已做 | 相关测例 111 passed；archive README / job-config-issues 已更新 |
