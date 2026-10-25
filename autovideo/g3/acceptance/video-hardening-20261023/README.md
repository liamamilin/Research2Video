# 文稿到视频：提交、恢复和执行稳定性验收

日期：2026-10-23。已应用到本机 AutoVideo 与 AI Research 服务，见 [deployment.json](deployment.json)。原任务、历史成片与失败尝试保留。

## 本轮修复

| 缺口 | 修复后的行为 |
| --- | --- |
| 创建响应丢失时，仅按标题、正文哈希和时间查找，可能认错同稿的另一条任务 | 每次研究端出片保存独立 `idempotency_key`；AutoVideo 用事务和唯一索引保证同标识、同请求只创建一个任务。不同设置复用标识返回 409。按标识精确对账，并等待尚未提交完的记录；历史多条匹配不猜选 |
| 恢复扫描只看心跳，多个进程可能同时跟踪同一任务 | 提交、轮询、复制全过程持有跨进程文件锁，进程退出后自动释放 |
| 复制后续文件失败时，重试会重复传输已完成的大视频 | 每次先核对本地文件大小和 SHA-256，匹配才复用；缺少有效校验元数据则拒绝；替换失败保留旧完整文件 |
| 远端已完成、复制失败后，自动重试可能重新生成整个视频 | 自动流程暂停并说明原因；手动重跑在版本和全部制作设置一致时继续复制原视频。修改音色或包装等设置则创建新任务；提交结果不明也不会自动另建任务 |
| 外接盘生成期间掉线或本地磁盘写入失败 | 下载前重新检查挂载；不重建掉线盘路径。写入异常归类为可继续复制的 `DOWNLOAD_FAILED` |
| 每 350 ms 重读整个 worker.log，半行被当作已读，可能漏报结束和具体错误 | 按新增字节增量读取，保留未完整的行及 UTF-8 字节；进程结束时读取最后一行。并行阶段的协议记录整行写入 |
| 子命令超时丢失部分输出与结构化错误；监控崩溃可能遗留生成进程 | 子命令输出实时写日志，超时保留输出并记录 `COMMAND_TIMEOUT`；worker 监控异常时终止所管理的生成进程 |
| 前端重新构建时，资源目录短暂消失会使研究端后端启动失败 | 静态资源允许暂时缺失，返回 404；API 可继续启动，资源写回后无需重启就能正常访问 |

## 回归结果

- AutoVideo 运行时回归：**494 passed, 1 skipped**，见 [autovideo-tests.log](autovideo-tests.log)。命令：`python3 -m pytest g4/tests g3/tests g2/tests g1/tests/test_video_ir_v0_2_runtime.py -q`。
- 研究端后端：**1816 passed, 3 skipped, 1 deselected**，见 [research-tests.log](research-tests.log)。单独排除此前已知的配方请求快照差异 `test_shipped_recipes_come_out_exactly_as_they_were_written_out_per_audience`；该项单独复查仍失败，详见 [recipe-snapshot-known-failure.log](recipe-snapshot-known-failure.log)，本轮未改写该快照。
- 研究端 `compileall`、配置校验、UI TypeScript/Vite 生产构建通过；两个仓库 `git diff --check` 通过。
- 收尾针对静态资源构建间隙的修复与出片恢复复查：**184 passed**，见 [focused-final-tests.log](focused-final-tests.log)。此前同一复查遇到 4 个 `ui/dist/assets` 暂时消失导致的启动错误，已复现并修复，测试不修改真实构建目录。
- AutoVideo 冻结设计包检查仍因历史 `AGENTS.md` 清单不符失败；`tests/test_final_release.py` 的全目录清单检查会包含运行数据、依赖和 Git 文件，不属于运行时回归。本轮未修改冻结设计包清单。
- 首次沙箱回归中的本机端口权限失败，已通过沙箱外离线测试解决；上述通过计数来自重跑结果。

新增回归覆盖并发 12 次同标识提交只入队一次、重启后复用原任务、不同音色/文稿/包装冲突、响应丢失和延迟提交、历史任务歧义、并发恢复、复制断线和磁盘写入失败、挂载中途消失、文件同长度损坏、半行及分段 UTF-8 日志、超时部分输出和监控异常清理。

## 真实媒体验收

复用保存的真实旁白音频和分镜，重新执行渲染、完整解码、字幕/时间线检查与包装检查，没有新增模型或 TTS 调用。原输入没有改动。

- 1080p、30 fps、CRF 18、AAC；正文 1075 帧、约 35.84 秒；完整包装 1330 帧、约 44.33 秒。
- 正文音轨与 TTS 相关系数 **0.999955**；加片头后的正文音轨相关系数 **0.999835**。
- 正文 QA 与包装 QA 均 PASS，完整解码、格式一致、帧数和接缝提示音检查通过。
- 渲染日志实时落盘；本次渲染 68.539 秒。该数字只记录本次验收，不能解释为新的同稿完整出片提速比例。

结果：[media.json](media.json)、[qa.json](qa.json)、[package-qa.json](package-qa.json)、[media.log](media.log)。新输出保存在 `g3/data/video-hardening-20261023/media-1/`，详细命令和日志位于其 `logs/`。

```sh
python3 g3/acceptance/generation-speed-20261023/benchmark.py \
  --source-run g3/data/packaging-library-20261023/information-2 \
  --output g3/data/video-hardening-20261023/media-NEW --concurrency auto
```

## 两应用接入故障验收

[verify_handoff.py](verify_handoff.py) 在隔离目录中使用真实 AutoVideo API/SQLite 和研究端的提交、对账、下载代码；视频使用已验收的真实 MP4。注入两次故障：服务端入队后丢失创建响应；视频下载完成后，下一个 QA 文件下载中断。

最终结果：**1 条任务、1 次创建、1 次精确对账、MP4 仅下载 1 次、QA 文件下载 2 次，研究端 COMPLETED**。保存的视频与原始 MP4 的 SHA-256 完全一致，避免重复传输 4,241,738 字节。见 [handoff.json](handoff.json) 和 [handoff.log](handoff.log)。该验收覆盖传输恢复，不把注入故障或复用媒体描述为真实模型生成。

```sh
python3 g3/acceptance/video-hardening-20261023/verify_handoff.py \
  --research-root /absolute/path/to/AI_research/Base_CodingCLi \
  --media g3/data/packaging-library-20261023/information-2/artifacts/package/video.mp4 \
  --output g3/data/video-hardening-20261023/handoff-NEW
```

实现版本哈希见 [implementation-sha256.json](implementation-sha256.json)。收尾时同一工作区的创作资源功能继续修改了 `g3/store.py`、`g3/app.py`、`g2/run_g2.py`，这些改动已保留；基于当时最新文件再跑本轮相关回归 **36 passed**，并用刚重新渲染的真实 MP4 再跑两应用接入故障验收 PASS。见 [final-revalidation.json](final-revalidation.json)、[autovideo-final-focused.log](autovideo-final-focused.log)、[handoff-final.json](handoff-final.json)。原始验收哈希不被后续版本覆盖。

本轮侧重消除故障后的重复生成、重复传输和状态误报；未降低画质，也未宣称正常完整出片又减少了某个百分比。
