# 真实视频的叙事表达实验：2026-11-11

验收结论更正（2026-11-11）：本轮状态为 **已接入但端到端未验证**，不能算“日常自动工作流视频效果改善”目标完成。教程验证使用预先导入图片和保存计划重放；录音笔图片来自爱范儿原文，之前的试片脚本预先登记了素材。当前生产配置 `video.visual_materials.enabled: false`，未证明日常自动任务无需额外人工准备即可复现收益。此前聊天中的完成声明收回，保留下述实验和技术检查作为局部证据。

本轮实现了一项有条件的教程编译改善，并对科普表达做了三种替代实验。衡量标准是画面是否帮助理解当前旁白，而不是运动量、换页次数或组件复杂度。

最终采用：真实素材首次出现时保留展示；同一张图再次出现、原有编排已经明确安排对比说明时，保留对比说明。修复了编译环节把已有对比一律覆盖成重复截图的问题。

科普的积木疏密对照，比扩大旧图、继续添加标签更直接地展示“重量相同、占用空间不同”。它目前是手工表达实验，没有接入自动生成，也没有通过不同科普主题的泛化验证。

## 真实问题与实验取舍

| 样例与区间 | 原有问题 | 尝试及结果 | 决定 |
| --- | --- | --- | --- |
| MarkItDown，原片约 20.37–59.03 秒 | 从网页实例转入“样式与结构”解释后，仍停留在原截图 | 全部采用原生图形：首个真实输入也被文字卡替换；有上下文的交接：先展示网页，再用原有左右对比解释差异 | 放弃全替换，保留有条件的对比交接 |
| 同片，原片约 99.20–159.50 秒 | 已经展示结果，讲解其价值时仍重复同一结果截图 | 先展示实际输出，再说明普通转换与保留表格的区别，随后保留真实输入/输出双图 | 采用；首次素材及双图证据仍在 |
| 同片结尾回顾 | 初版同时放开流程图，间接改变回顾选图，丢失操作命令 | 收敛到对比组件后，回顾恢复命令，并搭配输出解释 | 放弃流程图交接，没有继续修补回顾排序算法 |
| 微信输入法录音笔，原片约 136.43–164.70 秒 | 旁白比较零散开会与集中开会，画面仍为手机截图 | 同一规则呈现既有两类条件对比，之后回到原素材 | 未做样例专用调整，验证了另一真实内容结构中的收益 |
| 冰为什么浮起来，原片约 73.66–115.12 秒 | 旁白依次讲结晶、空隙、体积、密度及积木类比；一张插画无法逐项解释 | 放大已有示意：仍是静态标签表；分阶段比较/流程：跟得上讲解，但机制仍靠文字；积木疏密对照：直接展示同样数量占用不同空间 | 前者放弃，第二种仅局部有效，第三种保留为后续方向，尚不推广 |

以上是基于实际导出视频、沿时间抽帧与旁白/字幕对齐的编辑判断。已提供原声连续片段供播放复核；没有进行观众实验，不能据此宣称留存或涨粉提升。

## 原声连续视频

所有链接均为本地生成文件，原任务及原视频没有覆盖。教程复用真实保存的编排，不是为验证规则重新手写计划。

- 教程上下文，38.67 秒：[原版](/Users/milin/2026/软件开发/视频workflow/autovideo/g3/data/validation/narrative-expression-20261111/context-tutorial/before/video.mp4)、[全替换实验，放弃](/Users/milin/2026/软件开发/视频workflow/autovideo/g3/data/validation/narrative-expression-20261111/context-tutorial/all_native/video.mp4)、[保留上下文后的对比](/Users/milin/2026/软件开发/视频workflow/autovideo/g3/data/validation/narrative-expression-20261111/context-tutorial/release/video.mp4)。最终生产编译在这一渲染区间的场景和提示时刻与该视频一致。
- 教程结果到回顾，60.30 秒：[原版](/Users/milin/2026/软件开发/视频workflow/autovideo/g3/data/validation/narrative-expression-20261111/result-tutorial/before/video.mp4)、[最终版本](/Users/milin/2026/软件开发/视频workflow/autovideo/g3/data/validation/narrative-expression-20261111/final-tutorial/after/video.mp4)。初版流程图交接保留在 `result-tutorial/after`，已放弃，不作为最终交付。
- 不同真实教程，28.27 秒：[原版](/Users/milin/2026/软件开发/视频workflow/autovideo/g3/data/validation/narrative-expression-20261111/holdout-recorder/before/video.mp4)、[自动改善后](/Users/milin/2026/软件开发/视频workflow/autovideo/g3/data/validation/narrative-expression-20261111/holdout-recorder/after/video.mp4)。与最终生产编译的完整渲染参数一致。
- 科普同一章节，41.47 秒：[原版](/Users/milin/2026/软件开发/视频workflow/autovideo/g3/data/validation/narrative-expression-20261111/science-study/before/video.mp4)、[放大示意，放弃](/Users/milin/2026/软件开发/视频workflow/autovideo/g3/data/validation/narrative-expression-20261111/science-study/large_diagram/video.mp4)、[分阶段说明，部分有效](/Users/milin/2026/软件开发/视频workflow/autovideo/g3/data/validation/narrative-expression-20261111/science-study/stages/video.mp4)。
- 科普该章节最后 15.13 秒：[积木疏密对照实验](/Users/milin/2026/软件开发/视频workflow/autovideo/g3/data/validation/narrative-expression-20261111/science-study/packing/video.mp4)。对应上述章节的 26.33 秒至结尾，使用同一旁白；没有改变原片讲稿。图中明确标注类比示意、不按比例。

## 自动接入与边界

生产修改只有 `g2/material_expression.py`、`g2/explanation.py` 的对应选择逻辑和提示，以及专门测试。本轮没有修改生产渲染器、换页算法、截图裁剪或增加模型调用。

条件同时满足才交接：仅一张素材；它已实际显示；计划已选 `comparison`；没有明确指定图片区域讲解。首次出现、多图对照、区域导览和普通文字占位仍走已有素材展示路径。赋给图表但未实际展示的图片不算已经看过。没有产品名、主题关键词、视频 ID 或失败案例名单。

流程图交接在回顾场景中造成副作用，收益尚不足以支持扩大范围，本轮撤回。保留已有比较并不保证每个上游计划都有好的比较；未安排比较的长静止片段仍可能存在。录音笔片段后半段仍使用手机截图，未宣称整段全部解决。

科普积木组件只存在于验收实验目录，运行时仅注入隔离的渲染快照。它不是受支持的生产组件，不能把单个手写类比当作通用科普能力。下一轮若继续，应该先用不同机制的真实讲稿检验对象对照表达是否仍有收益，再决定是否值得抽象；不以增加这一个积木组件的功能为目标。

## 验证

- 最终代码：完整离线回归 **1087 passed，1 skipped，49.79 秒**，命令和结果存于 `tests.json`；`git diff --check` 通过。
- **12 个 MP4**：1920×1080、预期帧数、有音轨，完整解码通过，保留各自 `render.log`、`decode.log`、参数、源计划和 SHA-256。
- 教程各组：原旁白、字幕、完整场景时间边界不变；相同连续区间音频相关性大于 0.999。科普各 41.47 秒版本使用逐字节一致的原始 PCM 切片；重新分配视觉章节，不改旁白和字幕时间。
- 积木短段：源 PCM、参数和截取帧区间一致。初始直接比较两次独立编码的波形得 0.96258，未达到 0.99；保留失败记录。检查得到整数采样偏移为 0，10 毫秒 RMS 包络相关性为 0.99998，限定在一个 8 kHz 采样内的对齐后相关性为 0.99482（偏移约 0.333 采样）。这反映分数采样边界及独立编码差异，不报告为逐样本相同。
- 视觉复核覆盖上下文展示、比较转入、真实双图、结尾回顾，以及录音笔不同选择条件和科普类比的开头/强调/结尾。最终回顾显示转换命令和结果说明，初版丢失命令的问题已消除。
- 本轮渲染的是从真实整片选出的连续段落，没有重新渲染每条完整影片，也未验证上线后的观众表现。

机器记录见 [verification.json](/Users/milin/2026/软件开发/视频workflow/autovideo/g3/acceptance/narrative-expression-20261111/verification.json)，重放脚本为 `replay.py`、`science_trial.py`、`packing_trial.py`，媒体核验为 `verify.py`。`expression_candidate.py` 保存初版含流程图的试验方案；最终生产范围以 `g2/material_expression.py` 和 `production-manifest.json` 为准。

## 编排服务与未完成能力

用户将“只做本地”更正为同意后，已按授权向现有 DeepSeek 服务尝试发送选定科普章节的讲稿及必要上下文。服务返回 **HTTP 402**，没有得到新编排；未发送原音频或整套项目，也未换其他供应商。之后继续本地表达实验。科普分阶段与积木方案均明确标为手工研究，不能据此宣称外部编排已成功或自动科普能力已完成。

本轮实验结果是：教程改动已进入编译路径，并在另一已有素材的真实样例重放中看到局部收益；科普完成不同方法的对照与取舍。日常生产入口的端到端效果尚未验证，科普自动生成与跨样例验证也未完成。后续应从正常自动输入出发验证可行表达，不预设必须建设自动素材获取系统。
