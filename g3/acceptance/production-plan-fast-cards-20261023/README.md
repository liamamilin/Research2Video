# 统一制作计划与快速卡片验收

2026-10-23。本轮完成统一解析、冻结计划、快速卡片执行器和研究系统共同出片链路；保留旧形式与旧任务。操作合同见 [PRODUCTION_PLAN.md](../../PRODUCTION_PLAN.md)。没有批量修改现有矩阵默认。

## 正式两服务出片

《续航手机最抢眼，五件新品变化速览》定稿 v1，从研究系统的共用出片服务创建快速任务，经正式 AutoVideo worker、本机全新 VoxCPM 配音、原生卡片渲染、共用包装与媒体 QA，再由原下载服务核对 SHA 后回传研究系统。未复用已有旁白，未人工编写卡片要点，未调用分镜模型，未启用背景音乐。

- Script：`e118dc7db5f34c3ba90af0285b4688f5`，render：`768de6dbf8b6465eaf38a946cffbceee`。
- AutoVideo：`2e6d0b88-3a2e-42c0-b67c-9b30af4e7c4d`，attempt 1，COMPLETED。
- 成片：`AI_research/Base_CodingCLi/state/video_renders/768de6dbf8b6465eaf38a946cffbceee/video.mp4`（工作区相对路径，运行时文件不提交）。212.9 秒，1920×1080、30 fps、H.264/AAC。
- 7 章、31 张自动卡片，完整定稿原文哈希与上一轮普通成片相同。逐字旁白、字幕、完整解码、音轨相关性 0.99995、31 条真实字幕取帧和包装 QA 全部 PASS；25 个下载文件与两端登记的大小／SHA 完全一致。
- 来源卡、片头、片尾与提示音由共用包装阶段执行；正文角标只绘制一次。耳机章节沿用原稿 NEEDS_REVIEW 提醒，不把审计未核实的内容改成已核实。

计时从持久化记录计算，见 [live-run.json](live-run.json)：

| 相同计时口径 | 上轮普通版 | 本轮快速版 | 减少 |
| --- | --- | --- | --- |
| AutoVideo 开始执行到完成（含配音、QA、包装） | 645.118 秒 | 336.692 秒 | 47.81% |
| 研究系统创建到下载完成 | 646 秒 | 453 秒 | 29.88% |

本次真实 TTS 243.79 秒、卡片渲染 28.47 秒、正文 QA 15.61 秒、包装及其 QA 48.06 秒。执行完成到研究系统完成存在约 116 秒额外等待，当前记录无法再细分轮询、恢复与下载；不能把这段都归因于编码或网络。已有独立冷启动完整样片为 339.81 秒，结果接近引擎本轮执行时间。旧版与新版布局和包装不同，因此不是只改变编码器的严格 A/B 实验。未做 20 条批次吞吐压测。

证据：[播放页面](completed-fast.png)、[创建入口](create-fast.png)、[正文 QA](live-qa.json)、[包装 QA](live-package-qa.json)、[逐字检查](live-narration-check.json)、[冻结计划](live-production-plan.json)、[实际执行](live-production-execution.json)、[日志](live-execution-log.txt)、[下载哈希](live-artifacts.json)。

## 隔离真实媒体与故障修复

隔离数据目录 `g3/data/production-plan-fast-cards-20261023/` 保留全部尝试与输出，复现脚本 [run_acceptance.py](run_acceptance.py)。

| 案例 | 任务 | 结果 |
| --- | --- | --- |
| 两张卡片、全新配音 | `7f9f8bd0-b2a5-4269-a884-00ec72634728` | 10.38 秒完成；成片 11.367 秒 |
| 完整定稿、全新 VoxCPM 配音 | `11ead6cc-b991-4d2e-b09e-f54410aabd37` | 339.81 秒完成；成片 212.9 秒 |
| 1.5 倍速修订 | `3db1f6e1-e0c2-4e9c-beff-ea19402010c2` | attempt 1 因未读取冻结计划失败；修复共用 render 入口后，同任务 attempt 2 完成，父视频保留 |
| 沙箱无本机 Metal 设备 | `6af20785-d36e-453f-89ca-4914aca994f2` | 原始失败保留；本机运行时重新创建完整验收样片 |

另修复静态输入导致字幕停在首句、中文句末标点换至行首、worker 未读出数据库计划、默认更新破坏幂等提交和早期 v1 音乐字段布局不兼容的问题。兼容只允许原布局中的空保留字段，真实背景音乐设置变化仍拒绝。15 份当前可用的保存计划已用现代码重新校验，均通过。

## 回归与边界

- AutoVideo：`pytest tests g4/tests g3/tests g2/tests g1/tests/test_video_ir_v0_2_runtime.py -q -k 'not test_manifest_hashes_and_file_coverage'`，**608 passed、1 skipped、1 deselected**，见 [日志](regression-verified.txt)。
- 随后工作区的音乐模块有更新，补跑制作计划、生产 API 和音乐生成的共同兼容检查，**47 passed**，见 [日志](regression-final-compatibility.txt)。最终相关源文件指纹见 [source-sha256.json](source-sha256.json)。
- 未排除时，**607 passed、1 skipped、1 failed**；失败为既有历史文档清单覆盖测试，把当前 `.git`、虚拟环境、运行数据等一起算入冻结清单。见 [原始结果](regression-final.txt)，未重写清单掩盖差异。
- 研究后端：**1835 passed、3 skipped、1 deselected**。排除的旧受众 golden 测试存在审计等级、调研提示词和写作风格的既有差异；本轮只兼容新增生产默认字段，未覆盖旧样本以隐藏差异。
- 研究 UI：52 文件、363 测试通过；TypeScript、ESLint、生产构建通过。AutoVideo 使用已有类型依赖单独 TypeScript 检查与 vendored esbuild 构建通过；两仓库 `git diff --check` 通过，研究配置校验通过。
- 原作品和失败尝试保留。正式服务更新前备份并验证历史计数不变；本轮完整样片新增一条作品，现有音乐工作另有独立新增记录。
- 最终在视频和音乐队列均空闲时平滑更新 AutoVideo：**171 条历史任务、2,140 条制品大小／SHA 记录全部保留**，新服务健康且持有主 worker。见 [service-final.json](service-final.json)。
- 背景音乐沿用同时开发的共用混音阶段，其独立验收见 [音乐记录](../music-mix-20261023/README.md)。本任务没有新增音乐生成算法或更多视觉风格。

复核真实交接：`python3 g3/acceptance/production-plan-fast-cards-20261023/verify_acceptance.py`。它只读应用数据库与运行时文件，刷新本目录小型证据，不创建新视频。
