# 矩阵

状态：**第 3 期已做**（2026-10-20）。设计见 `spec/product-redesign.md` 第 2.1 节和第 4 节第 4、8 条。

一个矩阵是一组研究 Job，共用一个**领域**和一套**关键词**。例如「AI 矩阵」「医疗矩阵」。矩阵只负责把 Job 归在一起，并在运行时给它们领域和关键词。运行、停用、改提示词还在各 Job 页，定时还在周期调度页。

## 文件

每个矩阵一个文件：`config/matrices/<英文名>.yaml`。

```yaml
name: 医疗矩阵
domain: 医疗健康            # 提示词里的 {domain}
keywords: [新药审批, 医保政策] # 成员 Job 的 {keywords} 先放这些，再放 Job 自己的
jobs:
  - medical/flash_discovery
  - job: monitoring/ai_flash_discovery
    copy_as: _flash_discovery   # 换关键词复制时，用这个模板重新建（原来的提示词写死了 AI）
```

- **成员只记在矩阵文件里**。Job 放进或移出矩阵时，Job 文件一个字都不改。
- 一个 Job 只能属于一个矩阵。两个矩阵写了同一个 Job 时，按文件名排在后面的那个会标错，那一行不算数。
- 视频流水线生成的 Job（`jobs/video/`、`jobs/vod/`）不能放进矩阵。
- 英文名用小写字母开头，只含小写字母、数字、下划线，最多 24 个字符。

## 运行时

研究引擎跑一个成员 Job 时（`core/matrices.py` 的 `for_run`，在 `run_job` 里调用）：

- `{keywords}` 和搜索兜底用的关键词：先放矩阵的，再放 Job 自己的、没重复的（第 4 条：Job 能追加、不能删）。
- `{domain}`：矩阵的领域。不在矩阵里的 Job，`{domain}` 就是 Job 的名字。
- **事件记忆**：别的矩阵的 Job 记下的来源，不会出现在这个 Job 的「已报道」列表里。只有别的矩阵记过的才去掉，同时被自己这边记过的照样列出。不在任何矩阵里的 Job，还和以前一样读全部。

这一步只在运行时做，Job 编辑页读的还是 Job 文件本身，所以保存 Job 时不会把矩阵的关键词写进去。

## AI 矩阵

2026-10-20 把现有的 27 个 AI Job 归成了 `config/matrices/ai.yaml`：情报轮次的 10 个阶段、快讯发现，以及 monitoring、research、analysis、actionable、practice 下的 AI Job。`research/独立游戏` 不是 AI，没放进来。

- **关键词先空着**。AI Job 的关键词还写在各自的文件里。如果放进矩阵，所有 AI Job 的提示词都会变。
- **提示词逐字不变**，有测试保证（以后有意改了某个 Job 的提示词，跑 `python scripts/snapshot_job_prompts.py` 重新拍一份）：`tests/fixtures/job_prompt_hashes.json` 是加矩阵之前，每个 Job 渲染出的提示词和兜底关键词的哈希（固定日期，事件记忆和行动结果用占位）。`tests/test_matrices.py` 每次都按运行时的方式重新渲染，再和这些哈希对一遍。事件记忆也一样：现在只有 AI 一个矩阵，AI Job 什么都不会被去掉。

## 换关键词复制

矩阵页的「换关键词复制」：填新矩阵的名字、英文名、领域和关键词，选带上哪些 Job。

- **只有写了 `copy_as` 的成员能复制**。新 Job 用那个模板重新建，不是照抄原来的 Job 文件。原来的提示词写死了 AI 的，照抄只是把医疗的词塞进 AI 的提示词里。别的成员会列成「暂不能复制」。
  - 现在只有快讯发现有：`jobs/_templates/_flash_discovery.yaml`，是不分领域的快讯写法，2026-10-20 用医疗关键词试跑过。
  - 复制出来的 Job 也带着 `copy_as`，所以可以再复制一次。
- 新 Job 放在 `jobs/<新英文名>/<模板名>.yaml`，名字是「<领域> <模板标签>」。
  - 输出：`output/<新英文名>/{date}_<模板名>.md`，不会和原来的报告撞。
  - 不带关键词：关键词来自矩阵。
  - `enabled: true`，周期是手动，没有定时计划：**后台不会跑它，也不花钱**，点运行才跑。只有在命令行直接跑 `python run.py`、不带 Job 名时，它会和别的启用的 Job 一起跑。
- 原来的 Job 是选题来源的，新 Job 也是，并且自动配好：
  - `alias` 是「<新英文名>_<模板名>」；
  - `prefix` 是新英文名的首字母加原来的编号字母（医疗 → `MF`），已经被占了就换一个；
  - `refresh: false`：自动流水线不会自己启动它。
  - 这样不会和 AI 快讯的 F1、F2 撞（`spec/video-topics.md`）。
- 要么全部建好，要么一个都不建。中途失败的话，已经写出来的 Job 文件会删掉。

## 接口

| 方法 | 路径 | 谁能用 | 做什么 |
|---|---|---|---|
| GET | `/api/matrices` | 所有人 | 全部矩阵和成员（名字、启用、周期计划、上次运行、选题来源），加上不在任何矩阵里的 Job |
| GET | `/api/matrices/{key}` | 所有人 | 一个矩阵 |
| POST | `/api/matrices` | 编辑 | 新建（`key`、`name`、`domain`、`keywords`，可带 `jobs`） |
| PUT | `/api/matrices/{key}` | 编辑 | 改名字、领域、关键词。带 `jobs` 就是整份成员名单，留下的成员保留 `copy_as`；不带就不动成员 |
| DELETE | `/api/matrices/{key}` | 编辑 | 只删分组，Job 都还在 |
| POST | `/api/matrices/{key}/copy` | 编辑 | 换关键词复制（`key`、`name`、`domain`、`keywords`、`jobs`） |

## 还没做

- 成员 Job 的每月搜索预估和矩阵每月上限（第 7 条）。
- 整个矩阵一起暂停（第 2.5 节）。
- 运行记录页签（第 2.4 节）。
- 别的内容类型的不分领域模板。现在只有快讯能复制。
- 生产配置（第 4 期）。复制出来的快讯 Job 的条目会导进选题表，但选题页只显示配方 `topic.sources` 里写了的来源，而现在的配方只写了 AI 的。第 4 期之前，要写成视频就在一份单条配方的 `topic.sources` 里加上新来源（如 `medical_flash_discovery`）。
