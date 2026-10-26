# 视频矩阵三层重构：首轮开发与验收

日期：2026-10-26。设计依据：[视频内容架构](../video-content-architecture.md)。

## 交付范围

领域决定研究什么、服务谁；具名栏目决定持续交付的价值；制作配方决定画面如何组合。研究 Job、文稿配方和生产绑定继续承担原有执行职责。

| 层级 | 已实现 |
| --- | --- |
| 领域 | 10 个领域；复用现有 9 个主要配置，教育标为待接入；来源与运维分组可单独查看 |
| 共享类型 | 快讯、解读、横评、教程、深度研究、专题系列 |
| 具名栏目 | 32 个定义，25 个关联已有生产绑定，7 个待配置；AI 五个核心任务已经映射 |
| 写稿 | 新稿在工作线程启动前保存栏目与业务／来源矩阵快照；共享目标和结构进入现有写稿入口 |
| 制作 | 栏目默认值接入现有继承链；增加混合讲解、混合比较、混合步骤三个通用配方 |
| 界面 | 默认栏目入口、编辑与绑定、最近内容、共享制作配方库；研究来源继续管理原 Job |

迁移栏目使用 `source_compatible`，不主动改变原制作默认值。新栏目可选择业务领域继承。任务显式设置和单次选择继续覆盖上层默认。栏目画面偏好进入已有 `creative_intent`，不向引擎传入不支持的新结构。

历史稿件通过明确的生产绑定显示关联栏目，但没有回填历史身份。新稿身份及版本快照独立保存，后续编辑栏目不会重写已存版本。

## 兼容性

- [147 个 Job／生产配置基线](content-columns-20261026/baseline.json)与[最终比较](content-columns-20261026/config-compatibility.json)：内容及 mtime 均未改变。
- [14 个原矩阵比较](content-columns-20261026/matrix-compatibility.json)：除新增业务元信息外，原有研究字段保持。
- [原三个制作配方比较](content-columns-20261026/recipe-compatibility.json)：ID 与定义保持。
- 保留 `column_key`、既有文稿与成片、研究周期、生产配置启用时间以及 `(job, report path)` 自动批次去重。栏目编辑不重放旧报告。
- 跨领域稿件的展示归属使用业务矩阵；既有来源记忆继续使用研究来源矩阵。生产绑定移除后，保存的来源身份仍可读取；来源分组移除后，保存的栏目制作设置仍可解析。

## 实际写稿与制作请求

隔离状态目录：`state/acceptance/content-columns-20261026/`，自动生产及发布循环关闭。使用演示上游报告，以原 `ai_media_arena_digest` 生产绑定和 `report_digest` 文稿入口生成三对象比较稿。

- 文稿：`47545c7e72a04a46a22b503f258a0400`，栏目 `ai_model_review`，业务／来源矩阵均为 `ai_media`。
- 轻量、均衡、极速三个方案按成本、响应时间和处理位置比较，给出对应适用条件；没有经过旧的双对象选题入口。
- [实际文稿](content-columns-20261026/comparison-version.json)、[身份快照](content-columns-20261026/comparison-script.json)、[运行日志](content-columns-20261026/writer-log.txt)保留。
- [实际制作请求](content-columns-20261026/comparison-render-request.json)使用栏目默认的 `mixed_comparison`；真实 AutoVideo `/api/production/resolve` 已[接受并解析](content-columns-20261026/comparison-render-plan.json)。该长稿只做计划解析，没有加入渲染队列。

## 三支真实混合视频

输出根目录：`/Volumes/个人资料/2026/视频生成工作流/AI创作/混合渲染样片/2026-10-26_栏目重构验收/`。

| 样片 | 制作配方 | 包装成片时长 | 引擎任务 |
| --- | --- | --- | --- |
| 涨价以后，钱花在哪里 | mixed_explainer | 73.83 秒 | 8f5bdd5d-bdcb-40b8-b436-1f0ca3ab797b |
| 一份材料的两条处理路径 | mixed_tutorial | 71.07 秒 | d0a38384-2017-459b-b80e-91d3cd9e1d03 |
| 便宜和快，怎么一起看 | mixed_comparison | 81.37 秒 | aa5285f7-4d4d-4b76-a054-70da9219e238 |

三个任务均为 `hybrid_cards` 第五版，1920×1080、30 fps，无渲染回退；每支均同时包含轻量与运动画面。内容片与包装片共 6 个 MP4 完整解码通过，53 张场景、过程及拼接抽帧已视觉检查。

第一支检查截图局部聚焦、20→25 与 +5、月度趋势和跨图回顾；第二支检查双截图主次、流程分支和本地／云端区别；第三支检查三轮分组柱图、成本／响应散点及对象配色延续。抽帧未见文字遮挡、数据错位或拼接黑帧。素材与数值都是演示输入。

每支目录保留 `input.json / result.json / video.mp4 / content.mp4 / qa.json / production-execution.json / frames.json / contact.png`、字幕与完整解码日志。[媒体验收汇总](content-columns-20261026/media-verification.json)。

## 回归结果

- 研究端全量：3151 passed、3 skipped；21 failed、8 errors 中，本地端口／进程相关项目在允许监听后重跑为 **59 passed、2 skipped**。余下两个快讯快照失败，在未改动的 Git HEAD 临时副本上复现，[基线输出](content-columns-20261026/baseline-golden-tests.txt)。
- 本次最终兼容回归：**81 passed**，覆盖矩阵、栏目、来源记忆、输出归属、默认值、删除绑定／分组回退、轻量读取。
- 生产及自动流程相关回归：**102 passed**；栏目编辑不改 mtime／自动候选，原批次恢复及去重继续通过。
- 前端全量：**577 passed**；TypeScript、ESLint 和生产构建通过。现有大分块提示仍存在。
- AutoVideo 全量：**842 passed、1 skipped**；两个受沙盒端口权限影响的启动器项目重跑 **2 passed**。另一个失败为原设计包清单已过期，原 `AGENTS.md / RELEASE_STATUS.md` 与其记录不符；未借本次功能改写历史设计包清单。
- `run.py --validate` 和 `git diff --check` 通过。原有快讯快照及设计清单差异不是本次引入，未改动这些基线来掩盖失败。

原始定向回归、界面与服务测试日志位于同名证据目录。正式页面进行浏览与配方库检查，栏目保存操作使用隔离数据库。重载前核对研究任务为空；发布进程通过独立 worker 锁继续运行，视频引擎保持运行。

正式服务已完成重载与原有报告索引初始化，`/api/matrices/ai_media?reports=false` 返回 200；界面已经验证无超时提示。[栏目页面截图](content-columns-20261026/live-columns.png)与[制作配方库截图](content-columns-20261026/live-recipes.png)保留。隔离验收服务已关闭，配置、数据库及生成稿件继续保留。

## 保留的后续能力

教育领域正式来源、AI 实测与教程供稿、系列期次及连续选题、跨 Job 多报告合成，以及动态地图／时间轴专用组件仍未接通。页面以待配置状态说明，不能把配置登记视作已自动生产。
