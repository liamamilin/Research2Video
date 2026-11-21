# Job Prompt / 栏目设计审计

核对日期：2026-11-20。  
范围：`jobs/` 下**未归档** Job（约 69 个）；重点看 production 绑定与 `auto: 1`（约 38 个唯一 Job）。  
背景：[job-config-issues.md](job-config-issues.md)、[job-generation-pipelines.md](job-generation-pipelines.md)、[illustrated-explainer.md](illustrated-explainer.md)、[config-simplification.md](config-simplification.md)。

本文件回答两件事：

1. **怎样才能发现**「科技插画塞了 12 道题」这类问题（不只靠偶发阅读）。  
2. **按同一套方法重扫一遍**后，当前还有哪些同类或潜在问题。

---

## 1. 为何字段扫库抓不住这类问题

上一轮 [job-config-issues.md](job-config-issues.md) 主要看：缺不缺 `prompt` / `domain` / `audience_channel`、源声明是否过稀、production 扇出等**结构字段**。

「12 题清单」在字段上完全合法：有 prompt、有 `knowledge: true`、有 1～2 个源、有 production。它的问题是**栏目设计与实现错位**：

- 看起来像「栏目只能讲这 12 题」；  
- 实际意图是冷启动**种子兜底**；  
- 真正可持续的选题能力（主题分类 / 工作台）没有接到这条 Job 上。

因此需要在字段扫描之外，加一层**行为与一致性审计**。

---

## 2. 发现方法（可重复）

### 2.1 先定「在用」再定「形态」

| 层 | 怎么判 | 用途 |
|----|--------|------|
| auto | `config/production/*.yaml` 且 `auto: 1` | 日常自动出片，优先打磨 |
| bound | 有 production 但 `auto: 0` | 手动/试点，看是否还该留 |
| idle | 无 production、非排期主链 | 可归档或暂不理 |

同一次审计里，**不要用日报标准去量图解**，也不要用图解标准去量快讯。

### 2.2 六类嗅探（对 prompt 与声明做规则扫描）

对每个未归档 Job 读 `prompt` + 关键字段，专门找「合法但别扭」的模式：

| 编号 | 嗅探 | 在找什么 | 「12 题清单」命中？ |
|------|------|----------|-------------------|
| H1 | 文内题库 | prompt 里出现 `题目清单` / `题库`，或 ≥8 条「为什么/怎么…？」编号列表 | **是（10 个插画 Job）** |
| H2 | 静态去重库存 | `已覆盖以下` / `首批库存` 等写死在 prompt 的已做清单 | 教程 Job |
| H3 | 启动期话术 | `首期` / `试点` / `暂时` / `TODO` 仍在 description 或 prompt | 科学链「首期」；多数插画「试点」 |
| H4 | 兄弟漂移 | 同矩阵多 Job：开头句式、尾段标记、频道行是否齐套 | ChatGPT T05/T06；tech/software daily |
| H5 | 管线缺件 | `auto` 却无 `content_strategy` / `audience_channel`；`keywords: []` | 科学链 |
| H6 | 产品约束打架 | `search: false` 却要求联网搜；`language: zh` 却整段英文角色设定 | 本轮未见硬伤 |

辅助信号（不单独定罪）：

- `knowledge: true` + 源 ≤2 + 文内大题库 → 高度疑似「种子当栏目」。  
- description 写「试点」但已挂 production → 身份未收口。  
- prompt 极短（如 &lt;400 字）却在 auto 主链 → 相对同族偏薄。  
- 同一段 audience 套话在 Job / `content_strategy` / `audience-v1` 重复 → 挤占研究指令。

### 2.3 必做的人工对照（规则扫不到的）

规则只能标黄；下面几步用来判定「设计如此」还是「该改」：

1. **读优先级句子**：选题是「源 → 清单 → 自拟」，还是「只能从清单选」。  
2. **对照运行时注入**：如 `core/knowledge_job.py` 已注入「往期已讲」「选题参考」，prompt 是否还在重复造轮子。  
3. **对照设计稿 / 其它入口**：插画设计稿已指向主题分类库，Job 是否仍内嵌种子。  
4. **看 production 身份**：`auto: 0` 试点 vs `auto: 1` 日常；描述是否还写「试点」。  
5. **同族抽样读开头 5 行**：厚版「为『…』整理底稿」vs 瘦版「发现值得讲的新事情」。  
6. **用真实报告抽查**：同一 Job 连续几期是否反复撞清单前几题、是否空转「自拟」。

### 2.4 刻意不靠什么

- 不靠「源声明行数」判断图解好坏（图解源少是预期）。  
- 不靠「有没有 `topics:`」判断十域日报缺字段（整稿路径可以没有）。  
- 不新建复杂管理系统；把本文件当检查单，必要时用一小段扫描脚本复跑 H1–H6。

---

## 3. 本轮扫描结果摘要

未归档 Job ≈ **69**；production 绑定 ≈ **46**；其中 `auto: 1` 唯一 Job ≈ **38**。

| 嗅探 | 命中 | 严重度（对报告→视频） |
|------|------|----------------------|
| H1 文内题库（插画 10 域） | 10 | 中～高（尤其 science/economy 已 auto） |
| H2 教程静态库存 | 1 | 中（维护与挤占） |
| H3 启动期话术 | 科学链「首期」+ 8 个插画「试点」 | 高（链）/ 低～中（试点标识） |
| H4 兄弟漂移 | T05/T06 尾段；tech/software daily；tech weekly；software weekly 偏短 | 中～高 |
| H5 管线缺件 | 仅 `chains/ai_science_updates` | 高 |
| H6 约束打架 | 0 | — |

字段级硬伤（缺 prompt、英文 stub、禁搜却指令搜索）本轮**仍未见**。

---

## 4. 分项结论

### 4.1 插画 Job 的「题目清单」（本次典型问题）

**涉及**：十域全部 `*_illustrated.yaml`（`company`…`tech`）。  
**其中 auto=1**：`science/science_illustrated`、`economy/economy_illustrated`。  
**其余**：production `auto: 0`，description 多仍写「试点，手动运行」。

**设计意图（代码 + 设计稿）**：

1. `knowledge: true` + `search: false`：不联网，凭模型知识写单题底稿。  
2. 运行时注入往期标题避重；有源时注入「选题参考」（外文问答只作灵感）。  
3. prompt 内 10～16 道题 = **源不合适时的种子兜底**；清单用完则自拟。  
4. 出片走 `illustrated_explainer`；设计见 [illustrated-explainer.md](illustrated-explainer.md)。

**为何感觉「栏目有问题」**：

- 题库写在 prompt 里，观感像栏目上限，而不是种子。  
- 与设计稿已有的主题分类 / 题目生成入口**平行**，没有接到可持续选题。  
- 十域各维护一份近同构 prompt + 不同清单，复制漂移成本高。  
- science/economy 已 `auto: 1`，却仍用同一套「试点种子」结构。

**建议（未改代码，仅结论）**：

- 短期：prompt 只保留选题标准；清单外置或缩成 3～5 个「风格示例」。  
- 中期：冷启动后改接主题分类 / 工作台，或明确「仅用源灵感 + 自拟」，删文内题库。  
- 产品上先定：8 个 `auto: 0` 试点是保留手动、升级 auto，还是归档。

### 4.2 教程静态「已覆盖」清单

`ai_media/ai_tool_tutorials`（auto）prompt 内嵌「首批库存已覆盖以下知识点」，另有 `{reported_events}` 动态块。

- **意图**：去重，避免换标题重做同一操作。  
- **问题**：静态段会随时间膨胀，挤占研究指令；与动态块职责重叠。  
- **建议**：规则留 prompt，库存只走动态/外部记录。

### 4.3 科学链偏薄且仍「首期」

`chains/ai_science_updates`（auto）：

- prompt ≈ 268 字，含「首期优先从 OpenAI 科学博客…」；  
- `keywords: []`；无 `audience_channel` / `content_strategy`；  
- 输出却用 flash 条目格式。

相对快讯 / ChatGPT，这是 **auto 主链上最明显的缺件 + 启动期话术残留**。

### 4.4 同矩阵 prompt 漂移

| 族 | 现象 |
|----|------|
| ChatGPT T01–T09 | T05/T06 只有短尾「选题优先考虑…」，缺「选题与材料组织 / 候选排序 / 不预写口播」 |
| 十域 daily | economy 等为厚版「为『…』整理底稿」；**tech/software 仍为泛用开头** |
| 十域 weekly | 多数厚版；**tech_weekly 泛用**；**software_weekly 明显更短** |

漂移不一定立刻毁片，但会造成「同矩阵有的域讲得深、有的域像万能套话」。

### 4.5 三重选题套话（横向）

多数 auto Job 在 prompt 末尾重复一段与 `content_strategy` + `audience-v1` 同义的长说明（约 280 字级）。  
快讯 / T05 / T06 / 科学链用短尾或无尾，反而说明**长尾并非运行时必需**。

优化方向：Job 只留领域研究意图 + 输出格式；选题通则交给 strategy。批量删除前应用 1 条 ChatGPT + 1 条 daily + 快讯做报告厚度对照。

### 4.6 本轮未当成缺陷的

- 图解源只有 1～2 个：`knowledge` 预期形态。  
- 十域 daily/weekly 无 `topics:`：整稿路径预期。  
- `weekly_deep_research` 可搜：产品声明的例外。  
- Flash 与 T09 分工、T05 vs T06、daily vs weekly：**不应合并**。

---

## 5. 建议处理顺序

1. **科学链**：去「首期」、补频道/strategy 或文档化独立管线，加厚证据与范围规则。  
2. **插画身份收口**：先定 science/economy（已 auto）题库策略；再决定 8 个试点保留 / 升级 / 归档。  
3. **ChatGPT T05/T06 尾段对齐** + **tech/software daily（及偏旧 weekly）加厚**。  
4. **教程库存外置**。  
5. **（可选）删 Job 内 audience 长尾**，用真实报告验证后再批量。

---

## 6. 以后怎么定期发现

每次改矩阵、加栏目、或觉得「某条线报告变空」时，按下面清单过一遍（约半小时量级）：

1. 列出 auto / bound / idle。  
2. 跑 H1–H6（可用临时脚本：扫 `题目清单`、编号问句 ≥8、`首期|试点`、同目录开头句式分组、auto 缺 strategy）。  
3. 对 H1/H2 命中项做 §2.3 人工对照（优先级句、运行时注入、设计稿、auto 身份）。  
4. 对每个矩阵抽 2 个兄弟 Job 对比开头与尾段标记。  
5. 结论写入本文件或 [job-config-issues.md](job-config-issues.md)「已处理 / 新发现问题」，避免只在聊天里说过。

**发现原则**：字段齐全只说明「能跑」；还要问——

- 选题从哪来、用完怎么办？  
- 文内列表是种子、题库，还是去重账本？  
- 同矩阵是否进化不齐？  
- 启动期话术是否还留在日常路径？  
- prompt 是否在做本该由 strategy / 运行时注入做的事？

---

## 7. 与其它文档的关系

| 文档 | 分工 |
|------|------|
| [job-config-issues.md](job-config-issues.md) | 在用 Job 配置问题清单（字段、扇出、源误解等） |
| **本文件** | Prompt / 栏目设计行为审计 + 发现方法 |
| [illustrated-explainer.md](illustrated-explainer.md) | 插画产品设计与试点记录 |
| [config-simplification.md](config-simplification.md) | 配置层减负与归档策略 |

---

## 8. 其它潜在问题（超出「文内题库」之外）

下面是 2026-11-20 在 H1–H6 之外，用绑定 / 配方 / strategy / 运行时可解析性再扫一轮的结果。  
**仍是潜伏风险**：多数不导致立刻挂掉，但会造成「改 A 影响谁不清楚」或选题页脏数据。

### 8.1 怎么再发现一层

| 层 | 做法 |
|----|------|
| 绑定扇出 | 按 Job 聚合 `production.load_all`，≥2 条时核对是否成对（daily/each）还是历史重复 |
| 配方 ↔ Job | `recipes.catalog` 的 `topic_sources` 是否仍在 `topic_store.sources`（归档 Job 后最容易漏） |
| strategy 覆盖 | auto Job 是否有 `audience_channel`；production 键是否在 `content_strategy.productions` |
| 搜索默认 | auto 且未写 `search: false`、又非 knowledge → 默认可能联网（仅 deep 预期如此） |
| 试点身份 | description「试点」+ production `auto: 0` 长期并存 → 产品未收口 |
| 旁路入口 | `topic_discovery/*`、`source_discovery/*` 是否仍有人跑、结果是否进入主链 |

### 8.2 本轮额外命中

| 问题 | 证据 | 风险 | 建议 |
|------|------|------|------|
| **深度配方仍指向已归档选题源** | `config/video_recipes/deep_dive.yaml` → `sources: [opportunities]`；opportunities Job 已在 `jobs/archive/`；选题浏览器会出现 `unknown_sources` | 中：整份报告 `whole_report` 主路径仍可用，但条目/浏览器路径脏；新人易以为深度仍吃 PAI opportunities | 改配方 `topic.sources` 对齐 `weekly_deep_research`（或清空并只走 whole） |
| **同一深挖 Job 双 production** | `ai_media/weekly_deep_research` ← `ai_media_deep_dive`（auto=1）+ `deep_dive`（auto=0），配方同为 `deep_dive` | 中：类似快讯扇出噪音；手动键与自动键职责不清 | 归档或停用无 auto 的 `deep_dive` 键，或改成明确手动别名并写进文档 |
| **日报 Job 挂着旧 non_tech production** | `tech/tech_daily` ← `tech_daily` + `ai-non_tech-headline-short`（auto=0）；`economy/economy_daily` ← `economy_daily` + `ai-non_tech-deep_dive-long-2`（auto=0） | 低～中：改日报 prompt/源时多一条旁路；测试/UI 可能仍露出旧键 | 与快讯旧键同策：不用则进 `production/archive/` |
| **科学链未进 strategy 映射** | Job 无 `audience_channel`；`ai_science_updates_each` 不在 `content_strategy.productions` | 中：audience/补材路径可能绕开频道规则 | 补 channel + strategy，或标明「链独立、不走 audience-v1」 |
| **8 个插画试点未收口** | 除 science/economy 外，其余 illustrated `auto: 0`，description 仍写试点，却保留 production | 低～中：配置面噪音；与已 auto 的两域结构相同（含文内题库） | 产品决定：升级 / 保持手动 / 归档 |
| **配方实验变体仍引用 opportunities** | catalog 中 `ai-non_tech-deep_dive-long`（experimental）`topic_sources: [opportunities]` | 低：实验格 | 随 deep_dive 一并改或下线 |
| **教程/雷达使用扩展源前缀** | `page:`、`aa:models`、`gh_new:`、`hf_papers:daily` 等 | — | **不是缺陷**（引擎支持）；字段扫描若只认 `source:/tag:` 会误报 |

### 8.3 明确「看起来像问题、本轮不追」

| 现象 | 原因 |
|------|------|
| `weekly_deep_research` 未写 `search: false` | 产品上唯一联网深挖，`max_searches: 6` |
| 十域/ai_media 无 `topics:` | 整份报告路径 |
| 图解源 1～2 个 | knowledge 预期 |
| `topic_discovery` / `source_discovery` 大量 manual | 扩池/主题旁路，不是主报告写法；另审「是否还有人跑」即可 |
| production `usable` 全绿 | 不表示配方 topic_sources 与归档 Job 一致（deep_dive 即例） |

### 8.4 和 §4–§5 合并后的潜伏优先级

1. 科学链 strategy/频道缺件 +「首期」话术（§4.3 + §8.2）  
2. deep_dive 配方 `opportunities` 残留 + 双 production（§8.2）  
3. 插画文内题库 + 试点身份收口（§4.1 + §8.2）  
4. ChatGPT T05/T06、tech/software 漂移（§4.4）  
5. 日报旁路旧 non_tech production、教程静态库存（§8.2 / §4.2）
