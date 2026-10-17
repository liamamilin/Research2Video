# 视频文稿（Video scripts）

AI_research 负责一条视频从选题到**定稿**的全部内容工作；定稿之后交给 AutoVideo
成片和修订。本文件描述第一版：一个栏目「深度解读」，走通 选题 → 深度研究 →
结构化文稿 → 审稿定稿 → 交给 AutoVideo。

替代的是 video-opportunity-desk（VOD）里的同一段流程。从 VOD 迁来的部分：
按目标时长分配的长度预算、段落级证据标注、可编辑有版本的文稿、AutoVideo
提交台账。VOD 的机会发现不迁：选题来自本系统的内容机会（P7）或手工输入。

## 流程

```
选题（P7 内容机会 / 手工）
  → 研究：为这篇文稿生成一个 job（jobs/video/），由研究 agent 跑出带引用的报告
  → 写稿：一次模型调用，把报告改写成结构化文稿（分节、分段、每段引用）
  → 审稿：编辑段落（每次保存是新版本，重新测量长度）
  → 定稿：锁定一个版本
  → 出片：把定稿交给 AutoVideo，跟踪任务，下载成片并校验
```

研究复用现有 job 机制，因此日志、状态、报告索引、引用核对都和其他 job 一样，
在「Jobs」页能看到。写稿是一步纯转换，不再联网检索。

写稿有两种方式，由栏目的 `writer` 决定：`legacy` 一次写稿；`adapt` 五步改编（事实底稿 →
讲述方案 → 按风格写稿 → 审校 → 人工审稿），见 `spec/video-adaptation.md`。验收通过前
「深度解读」仍是 `legacy`；任何已有文稿都可以「换个风格」，用改编生成新版本。

## 栏目

`config/video_columns.yaml`。一个栏目 = 研究提示词 + 写稿规范 + 时长 + 视频形式。
第一版只有 `deep_dive`（深度解读）：6 分钟、按证据分级讲清一个问题。

## 引用规则

研究报告的引用已由 `core/provenance.py` 核对：报告里的网址是否来自本次检索。
文稿的每一段只能引用 **源报告里出现过、且核对时匹配上检索结果** 的网址。

| 段落 | 标注 |
|---|---|
| 引用了至少一个可信网址 | `CITED` |
| 没有引用（开场、过渡、总结） | `UNCITED` |
| 引用了不可信的网址（报告外、或核对未匹配） | 这些引用被去掉，标 `NEEDS_REVIEW` |
| 模型自己标为需核实 | `NEEDS_REVIEW` |

报告本身不是证据，文稿里不能写 "根据报告"。

## 长度

和 VOD 一致：目标时长 × 语速 = 朗读单位预算。汉字、拉丁词、数字各算一个单位，
标点不算。实测 AutoVideo 中文约 4.3 单位/秒（三次成片：4.3、4.3、4.36）。
偏离预算超过 15% 标记 OVER / UNDER，不截断；审稿页显示偏离的百分比。

## 存储

`state/video.db`（SQLite）：

- `scripts`：一篇文稿。栏目、选题、状态、研究 job、源报告、当前版本、定稿版本。
- `script_versions`：每个版本的分节分段 JSON、长度检查、来源（生成 / 编辑）。
- `renders`：交给 AutoVideo 的每次出片：提交的文本与哈希、AutoVideo job id、
  状态、已下载并校验的文件。AutoVideo 没有幂等键，这张表就是台账。

状态：`RESEARCHING → WRITING → DRAFT → APPROVED`，或 `FAILED`（带原因）。换风格时
`DRAFT/APPROVED → WRITING → DRAFT`，`stage` 显示改编进行到哪一步；失败或被重启打断时
回到原来的状态，之前的版本都在。

`fact_sheets`：每份研究报告一张事实底稿（按报告内容的 sha256），所有风格共用。

## 接口

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | `/api/video/columns` | 栏目列表 |
| GET | `/api/video/styles` | 深浅档位和语气 |
| GET | `/api/video/topics` | 最新一轮 P7 内容机会，标出已写过的 |
| POST | `/api/video/scripts` | 按选题新建文稿并开始研究（202） |
| GET | `/api/video/scripts` | 文稿列表 |
| GET | `/api/video/scripts/{id}` | 文稿详情：当前版本、版本列表、出片记录 |
| PUT | `/api/video/scripts/{id}` | 保存编辑，生成新版本（重跑规则检查） |
| POST | `/api/video/scripts/{id}/restyle` | 用同一份研究按另一种风格重写（202） |
| GET | `/api/video/scripts/{id}/facts` | 某个版本依据的事实底稿 |
| POST | `/api/video/scripts/{id}/approve` | 定稿 |
| POST | `/api/video/scripts/{id}/render` | 把定稿交给 AutoVideo（202） |
| GET | `/api/video/renders/{id}/files/{name}` | 下载成片文件 |

## 交给 AutoVideo

定稿按「按原稿配音」提交（`input_mode: "narration"`，见
`autovideo/UPSTREAM_INTEGRATION.md`）：每一节是一章，章标题只上画面、不朗读
（最多 32 字），段落逐字配音。AutoVideo 只规划画面；配音、字幕或章节有任何一字
与定稿不符，任务以 `NARRATION_TEXT_MISMATCH` 失败，不会静默改写。引用和审核
备注不提交。

提交、轮询、对账、下载校验沿用 VOD 已验证的做法。对账用的哈希按 AutoVideo
的规则计算：章内段落以换行连接、章与章之间空一行。每条出片记录带心跳，服务重启
时只接手心跳已停的出片，避免两个进程同时跟踪同一个任务。
