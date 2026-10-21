# 文稿配方（Video recipes）

状态：已实现（2026-10-20）。替代 `config/video_matrix.yaml`（四个维度拼格子）和 `config/video_columns.yaml`。

2026-10-21 起，配方也是生产配置的「写法」：一套生产配置就是挂在矩阵某个 Job 上的一个配方，见 `spec/production.md`。配方本身没变。

## 为什么

以前一种文稿的做法分散在两处：矩阵里按 领域 × 受众 × 类型 × 形态 拼出来，其余散在代码里（选题字段在三个
提示词构造函数里各写一份、审计超时和风格严重程度按 `topic_kind == "roundup"` 判断、调研语言写死 zh、短稿换风格
被强行套进五步改编）。想要一种新做法，只能改配置里的“类型”再加格子，再改 golden。

现在和 Job 一样：**一份 YAML 文件就是一种文稿的完整做法**，看到的就是会运行的，直接改、复制复用。

## 文件

`config/video_recipes/<key>.yaml`，文件名就是配方名（也是文稿记录里的 `column_key`），小写字母、数字、下划线、
连字符。以 `_` 开头的文件不读。新配方从已有配方复制（复制后 status 变成 experimental）。

调研和出片这两段可以不写在配方里，而是引用共用部件（见下面「共用部件」）：`research: {template: …}`、
`output: {use: …}`。配方里同一段再写的字段优先于部件，所以“大体照部件、个别字段不同”直接在配方里写那个字段。

```yaml
name: 快讯                      # 显示名
description: …                  # 一句话说明，进提示词
status: enabled                 # enabled 出过片验证过 / experimental 试验 / blocked 停用（要写 reason）
label: …                        # 可选，列表里的名字；不写就用 tags 的类型 · 受众 · 形态
tags:                           # 只用于选题页的网格摆放（类型 × 受众），不影响运行
  domain: {key: ai, name: AI 与软件}
  audience: {key: developer, name: 开发者}
  type: {key: quick_take, name: 快讯}
  format: {key: short, name: B 站横屏短}
audience: 开发者                 # 选题没写目标受众时用它
target_minutes: 1.25            # 调研提示词、字数预算、长度检查都按它

topic:
  kind: single                  # single 一条选题 / roundup 几条快讯合成一期 / comparison 两个对象对比
  sources: [flash]              # 自动流水线从哪挑题：声明了 topics: 的 Job 的 alias 或路径（flash、opportunities…），或 manual；空 = 自动流水线不挑这个配方。手动建稿不受影响。见 spec/video-topics.md
  fields: [核心角度, 目标受众]   # 写稿时给模型看的选题字段，按顺序；不写用写法的默认

research:
  template: quick_take_developer  # 引用调研模板（下面几项由模板给，配方里写了就以配方为准）；不引用就整段自己写：
  mode: job                     # job 每篇文稿跑一个研究 job / none 不调研，选题必须自带报告（盘点、从长稿切）
  timeout_seconds: 1500
  language: zh
  prompt: |                     # 调研提示词，占位符见下
    …

writing:
  pipeline: single              # single 一次写稿 + 审计 / adapt 五步改编（只给 3 分钟以上的稿子）
  default_style: {depth: intermediate, tone: news}
  allowed_tones: [news, concise]
  sections: [2, 4]
  paragraph_units: [20, 90]
  required_roles: [钩子, 行动]
  revise_rounds: 2              # 只对 adapt：审校后的修改轮数，0–5
  pause_after_plan: false       # 只对 adapt：方案定好后停下等你确认再写稿；默认 false，批次的文稿从不停（spec/video-steps.md）
  audit:
    model: review               # review 用 system.yaml 的 review_ai / writer 用写稿模型
    style_severity: must        # 一次写稿的风格审计：must 必须改 / warn 只提醒
    timeout_seconds: 240        # 一次写稿的审计时间上限
  style: |                      # 写稿规范，进提示词
    …

output:
  use: motion                   # 引用出片部件（config/video_parts/output/motion.yaml），下面写的字段优先
  target: autovideo             # 现在只有 autovideo
  voice_id: voxcpm_anchor
  video_form: information_motion
  allowed_forms: [information_motion]
  creative_intent: {purpose: 知识讲解}
  source_card: true
  package: {…}                  # 片头片尾，见 spec/video-short-forms.md
  recheck: true                 # 发布前可手动复核
```

不认识的字段直接报错（打错字不会被悄悄忽略）。

### 调研提示词的占位符

- `{topic.<字段>}`：选题里的任意字段，如 `{topic.变化}`、`{topic.发布日期}`；`{topic.标题}` 是标题。选题没有这个字段时写「（未提供）」。
- 旧名字照旧可用：`{topic_title}` `{topic_angle}`（核心角度）`{topic_audience}` `{topic_why}` `{topic_outline}`
  `{topic_change}` `{topic_date}` `{topic_sources}`（来源网址）`{topic_subjects}` `{topic_dimensions}` `{topic_minutes}`。
- `{date}` `{date_7d_ago}` `{language}` 由 Job 运行时填（和普通 Job 一样）。

## 共用部件

几个配方共用的一段放在单独的文件里，改一处，引用它的配方一起变。

| 种类 | 配方里怎么写 | 文件 |
|---|---|---|
| 调研模板 | `research: {template: <名字>}` | `jobs/_templates/video_research/<名字>.yaml` |
| 出片部件 | `output: {use: <名字>}` | `config/video_parts/output/<名字>.yaml` |
| 写稿部件 | `writing: {use: <名字>}` | `config/video_parts/writing/<名字>.yaml`（现在还没有） |
| 给谁看 | `audiences: [developer, non_tech]` | `config/video_parts/audience/<名字>.yaml`，见下面「给谁看」 |

- **调研模板就是 Job 模板的写法**：`name`、`description`、`language`、`runtime.timeout_seconds`、`prompt`。每篇文稿的调研
  Job 由它生成（`mode` 固定为 job）。它放在 `_templates` 的子目录里，不出现在「新建 Job」的模板列表：prompt 里有
  `{topic.…}`、`{topic_title}` 这类只有建文稿时才有的占位符，单独建 Job 填不上。
- **出片 / 写稿部件**是配方那一段的字段，合并是浅合并：配方里写了的键整个替换部件里的同名键（比如 `package` 整个替换）。
  部件里写了那一段不认识的字段会报错。
- 现在的部件：4 个调研模板（快讯 `quick_take`、一个知识点 `one_point`、深度解读 `deep_dive`，都不分受众；对比 `comparison_developer`
  还只有开发者）；2 个出片部件 `motion`（信息动效）、`auto_form`（自动选形态）；2 份观众档案 `developer`、`non_tech`。
  盘点不调研，没有调研模板。
- 文稿版本的 `meta.cell` 除了配方文件的 `sha256`，还记 `resolved_sha256`（拼好部件之后的内容）和 `uses`（引用了哪些部件
  及其哈希）。只改了部件时，从这两项能看出来。
- 引用的部件不存在或坏了，只有引用它的配方标“有错”，其他配方照常。

### 给谁看

「给谁看」散在每句话里，不能整段拿出来换。所以配方写成不分受众的，受众的话写在一份**观众档案**里，配方在要放的地方写占位符：

```yaml
# config/video_parts/audience/non_tech.yaml
name: 普通用户
status: experimental          # 可选：这个受众还没验证过，用它的栏目也是 experimental
depth: intro                  # {{audience.depth}}
creative_intent: {purpose: 知识讲解, audience: …}   # 可选：替换出片部件里的 creative_intent，配方自己写的更优先
text:                         # 配方按名字插进去的文字，名字用小写字母、数字、下划线
  impact: 谁受影响、每月多花或少花多少、要不要换工具或改设置
  writing_rules: |
    读者会用 ChatGPT、Claude 这类 AI 工具，但不写代码……
```

配方里写 `audiences: [developer, non_tech]`（第一个是默认），文字里用：

- `{{audience}}`：档案的 `name`。`{{audience.depth}}`、`{{audience.<text 里的名字>}}`：对应的字。
- 单独占一行的 `{{audience.<名字>}}` 会换成那段文字的各行；这段是空的，就整行去掉，不留空行。开发者档案的 `writing_rules`、`research_rules` 都是空的，所以开发者的提示词和从前逐字一样。
- 花括号是两层，和 `{topic_title}`、`{date}` 这类运行时才填的占位符分得开。写错名字、写了档案里没有的字，配方建不起来。
- 写了 `audiences` 就不要再写 `audience`，`tags` 里也不写 `audience`（由档案填），但要写全 `domain`、`type`、`format`。

**每个受众是一个栏目。** 第一个受众用文件自己的名字（`flash`）；其余的用 tags 拼出来的格子名，受众换成它自己（`ai-non_tech-quick_take-short`）。所以凡是按栏目名查栏目的地方（改稿、审计、出片、发布标签、自动流水线、生产配置）都不用改，已有文稿的 `column_key` 也照旧能解析。`recipes.expanded()` 给出全部栏目，`recipes.load()` 仍是一个文件一条。栏目的 status 取配方和档案里更不稳的那个。同名的栏目已经有文件时，文件优先，受众那一版不生成。

### 时长

`target_minutes` 是配方自己的时长。配方可以再写 `minutes_range: [最短, 最长]`（分钟，半分钟一档，自己的时长要在里面），表示它的文字没有写死多长，生产配置可以把时长改成范围里的数。没写，配置就不能改它的时长。改了时长的栏目叫 `<栏目名>-m8`、`<栏目名>-m7p5`，除了 `target_minutes` 和名字，设置和原栏目一模一样；它们不在栏目列表里，`recipes.find` 按名字现生成。保存配方时范围两头各要能建起来（比如五步改编的栏目不能短于 3 分钟）。

### 调研可以不做

`research: {optional: true}`：这份配方的条目已经被它来自的报告核实过，生产配置可以关掉重新调研。关掉的栏目叫 `<栏目名>-nr`，调研换成「只用报告里这一条的那一节和它引用的来源」（`core/video/excerpt.py`），其余设置不变。没写 `optional` 的配方不能关。保存配方时「不调研」那一版也要能建起来。

保存配方时每个受众那一版都要能建起来；保存档案时，用它的每个配方的每一版都要重建，有一个建不起来就不存。

调研的那一句「读者是不写代码的普通用户，优先官方帮助页……」现在是 `non_tech` 档案的 `research_rules`，为的是让普通用户那 3 个栏目的调研提示词和从前逐字一样。`spec/product-redesign.md` 第 5 条的意思是调研不分受众，这一句留着是过渡，删掉它是下一步单独的一件事。

拆分前后对比：8 个配方解析出的 Column、调研 Job 的提示词、写稿和出片请求逐字一致（同一个脚本拆分前后各导出一次比对）。

## 加载与校验

- `core/video/recipes.py` 读每个文件，`to_flat` 转成 `columns.build` 的扁平设置，得到 `Column`；下游（写稿、审计、出片、
  自动流水线）只看 `Column`，所以没改。
- 一个文件坏了只影响它自己：它在列表里标“有错”，不能建文稿，其他配方照常可用。
- 文稿版本的 `meta.cell` 记录用的是哪个配方：`recipe`、文件内容的 `sha256`、`cell`（四个 tags 拼成的 id）、`status`。
  以前的版本记的是矩阵格子和各维度 version，照旧可读。
- 组合检查：`adapt` 要求 `target_minutes` ≥ 3；`research.mode: job` 必须有 prompt；`none` 的配方建文稿时选题必须带报告
  （否则 422 `report_required`）。一次写稿的配方没有「换风格」（以前会被强行走五步改编，75 秒的快讯过不了改编的
  3–5 个理解目标）。

## 接口与界面

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/api/video/recipes` | 配方列表：状态、错误、写法、调研方式、时长、用它的文稿数 |
| GET | `/api/video/recipes/{key}` | 文件原文和解析结果 |
| POST | `/api/video/recipes/{key}/validate` | 校验一段内容，不写盘 |
| PUT | `/api/video/recipes/{key}` | 保存：先校验（422），再比 mtime（409），备份旧文件到 `state/backups/video_recipe_<key>_<时间>.yaml`，原子写 |
| POST | `/api/video/recipes` | 新建：`{key, copy_from}` 复制已有配方，或 `{key, content}` |
| DELETE | `/api/video/recipes/{key}` | 删除（先备份）；有文稿用它时拒绝（409），改成 blocked 即可 |
| GET | `/api/video/recipes/parts` | 所有部件：种类、名字、路径、哪些配方在用（`used_by`） |
| GET | `/api/video/recipes/parts/{kind}/{name}` | 部件原文；`kind` 是 research / output / writing |
| PUT | `/api/video/recipes/parts/{kind}/{name}` | 保存：用新内容重建每个在用的配方，有一个建不起来就 422 并列出原因；比 mtime（409）；备份到 `state/backups/video_part_<kind>_<name>_<时间>.yaml` |
| POST | `/api/video/recipes/parts/{kind}` | 新建：`{name, copy_from}` 复制同种类的已有部件 |

界面：侧边栏「文稿配方」（`/video-recipes`），左边列表，右边 YAML 编辑器，校验 / 保存 / 复制为新配方 / 删除。
配方上方列出它引用的部件（可点开）；列表下半是「共用部件」，点开（`/video-recipes/parts/<kind>/<name>`）可以编辑、
复制，并列出在用的配方。
`/api/video/cells`、`/api/video/columns`、`/api/video/coverage` 的形状不变，数据来自配方。

保存只影响之后新写的文稿；正在写的文稿开始时已经读过配方。

## 迁移

8 份配方由矩阵的组合结果机械导出（一次性脚本，没有手抄）。导出前后对比：每个配方的 Column 字段、研究 job 的提示词、
一次写稿的请求、改编方案的请求、片头片尾请求逐字一致；新增的字段（topic.fields、audit、research.language 等）
写的都是原来代码里的默认值。`tests/golden/video_cells.json` 只多了新增字段。

## 还写在代码里的（下一期再说）

- 写稿、改编（拆事实 / 定方案 / 写稿 / 审校）、审计的**系统提示词**：它们规定模型输出的 JSON 结构，代码按这个结构解析，
  并带 `PROMPT_VERSION`；放开编辑一改就可能解析不了。配方里能改的是栏目相关的文字（调研提示词、写稿规范）。
- 写稿流程只有两种（`single`、`adapt`），还不能自由拼步骤（比如“定方案后停下等人确认”“只重写不重定方案”）。
- 改编的常量：理解目标 3–5 个、事实上限每 100 字一条（最少 8 条）——系统提示词里写着同样的数字，要一起改。
- 出口只有 AutoVideo 横屏；竖屏、只要文稿 / Markdown 还没有。
