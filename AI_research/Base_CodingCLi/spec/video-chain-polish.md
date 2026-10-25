# 视频链路打磨

目标：根据素材，产出目标观众愿意看、听得懂、想继续看的视频。达到还不错即可，以实际自动生成的成稿和成片判断。

## 在用范围

2026-10-24 的范围是 11 个未归档矩阵、26 个启用研究任务（25 个属于在用矩阵，另有一个未归组的独立游戏任务）、32 套关联视频配置和九个可用配方。归档矩阵的历史配置和产物保留。开发期间暂停了 25 个研究周期任务及一个平台数据采集任务；既有视频批次原本已暂停。

原启停状态和排期：`state/scheduler_backup/video-chain-polish-20261024-232116/manifest.json`。全部修改和验证完成，且用户没有改变安排后，恢复本次暂停的计划。通过 `ScheduleStore.toggle(id, True)` 恢复到未来时刻，暂停期间不补跑。

## 当前改动

- 配方的简短创作目的实际进入作者请求，解决名字不同、作者却收到近似任务的问题。生成版本保存当时的方向；视频请求沿用它和该期标题，避免交接时再次丢失。
- 不同研究任务保留各自要探索的事情。公共研究步骤准备能展开讲述的具体材料；视频研究模板使用真实选题字段和来源。
- RSS 优先读取已有全文字段，Atom 支持嵌套 XHTML 正文。订阅正文、发布说明及论文摘要使用 3000 字的输入资源容量，替代截到 400/700 字或直接忽略全文。标题、链接及发布时间保持来源数据。
- 研究引擎保存实际抓取、检索与文件读取的原始资料快照。默认作者使用原始资料，避免把中间报告的创作备忘带进口播；旧报告无快照时仍可生成。文稿版本保存素材引用，发布包附上同一来源链接。
- 快速卡片可使用重点、对比、步骤或要点布局。屏幕文字保持模型原稿，按真实排版空间检查；可选旁白段引用控制画面展开，时间来自已测量配音。
- 标准信息动效默认由模型设计画面与旁白覆盖区间，编译器按实际配音生成常规展开，避免机械定位编号不全导致整章重试；句内位置为估算，旧精确定位输入仍支持。流程图及视觉场景继续使用受控组件与对应定位。没有新增内容打分或自动审校改写循环。

- 正式视频服务与验收工具统一使用一小时基础执行预算，封装另有既有余量，避免较长的真实自动稿仅在宽松验收参数下通过。

- 验收清单包含未归组的启用研究任务，排除归档矩阵和历史 `video/` 派生任务。验收工具复用正式提交入口，保留 API 的幂等保护。

## 实验与回溯

记录目录：`output/video-chain-polish-20261024/`。`inventory.json` 是启动时的清单，`live-inventory.json` 是当前解析结果，`checkpoints/00-before/` 保存两个项目的原有源文件、哈希及未提交状态。不要用 Git HEAD 覆盖用户已有修改。

`source-snapshot.json` 保存实际抓取的公开订阅资料；`source-health.json` 是读取结果，不代表报道事实或观看价值已经被验证。`layout-previews/` 是沿用旧模型屏幕文字的本地排版预览，不是新文稿或最终样片。

每个真实实验保存输入、版本、调用记录和结果。路径无效时可回到之前节点比较，不将一篇稿的问题自动变成永久公共规则。正文只由 workflow 生成。

## 逐项生成

`scripts/eval_video_chain.py` 使用正式研究引擎、文稿 workflow、自动定稿逻辑、视频请求构造和 AutoVideo 工作进程。状态与输出放在 `state/validation/`，避免被正式报告索引收录，尝试目录只新增，测试通知不外发；不改变正式任务和周期设置。早期验证曾误用正式 workspace，产生事件去重记忆写入，发现后已停止该进程；现在每个样本使用独立 workspace，事件记录未冒险删除。

```sh
../.AI_research/bin/python scripts/eval_video_chain.py inventory --output state/validation/chain-review
../.AI_research/bin/python scripts/eval_video_chain.py reports --only history/history_daily --output state/validation/chain-review
../.AI_research/bin/python scripts/eval_video_chain.py scripts --only topic_digest --output state/validation/chain-review
../.AI_research/bin/python scripts/eval_video_chain.py media --only topic_digest --output state/validation/chain-review --video-provider research
```

后三步调用外部模型，须在本次外发范围已获授权后执行。`research` 选项只为隔离视频样本使用现有研究模型配置；不修改 AutoVideo 全局模型。省略 `--only` 按顺序运行该阶段全部在用任务或九个配方。`--mode fast` 另外覆盖快速卡片；默认遵循当前配置。

## 验收状态

本轮已完成。26 个在用 jobs 的真实报告、九个未手改自动稿与完整 MP4、32 套生产配置解析已核对。九个样片完成真实 HTTP 回传、文件哈希与发布包验证，媒体 QA 和实际抽帧通过。正式研究服务、AutoVideo 与周期执行器已重载；26 个原启用计划已恢复到未来排期，暂停期间不补跑，原停用批次保持停用。

验收产物实际位于 `state/validation/video-chain-polish-20261024/`，`output/video-chain-polish-20261024/` 保留兼容链接。详情见其中的 `REPORT.md`、`SAMPLES.md` 与 `acceptance-final.json`。运行回归通过；冻结设计包清单的既有不匹配单独保留。抽帧与媒体检查不代表真实观众留存，继续以实际观看反馈迭代。
