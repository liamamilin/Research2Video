# 文稿配方（Video recipes）

状态：已实现（2026-10-20）。替代 `config/video_matrix.yaml`（四个维度拼格子）和 `config/video_columns.yaml`。

## 为什么

以前一种文稿的做法分散在两处：矩阵里按 领域 × 受众 × 类型 × 形态 拼出来，其余散在代码里（选题字段在三个
提示词构造函数里各写一份、审计超时和风格严重程度按 `topic_kind == "roundup"` 判断、调研语言写死 zh、短稿换风格
被强行套进五步改编）。想要一种新做法，只能改配置里的“类型”再加格子，再改 golden。

现在和 Job 一样：**一份 YAML 文件就是一种文稿的完整做法**，看到的就是会运行的，直接改、复制复用。

## 文件

`config/video_recipes/<key>.yaml`，文件名就是配方名（也是文稿记录里的 `column_key`），小写字母、数字、下划线、
连字符。以 `_` 开头的文件不读。没有继承：新配方从已有配方复制（复制后 status 变成 experimental）。

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
  sources: [flash]              # 自动流水线从哪挑题：flash 快讯发现、opportunities 情报轮次；空 = 自动流水线不挑这个配方。手动建稿不受影响，可填任何题目
  fields: [核心角度, 目标受众]   # 写稿时给模型看的选题字段，按顺序；不写用写法的默认

research:
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
  audit:
    model: review               # review 用 system.yaml 的 review_ai / writer 用写稿模型
    style_severity: must        # 一次写稿的风格审计：must 必须改 / warn 只提醒
    timeout_seconds: 240        # 一次写稿的审计时间上限
  style: |                      # 写稿规范，进提示词
    …

output:
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

界面：侧边栏「文稿配方」（`/video-recipes`），左边列表，右边 YAML 编辑器，校验 / 保存 / 复制为新配方 / 删除。
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
