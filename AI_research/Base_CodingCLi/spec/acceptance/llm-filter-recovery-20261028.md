# 写稿过滤结果的兜底与原任务恢复

2026-10-28，原文稿 `2e6607dd4f01425cbe3831bb5731a7b5` 的调研成功，写稿返回 `finish_reason=content_filter`，版本停在 0。

## 根因与修改

- 已配置 `deepseek-v4.1-flash → minimax-m3`，但过滤结果原先只抛出普通 LLMError，不进入模型切换分支。
- 使用独立错误类型识别过滤完成状态，将未完成的回复丢弃，再按已配置顺序使用原请求。每个被过滤模型只尝试一次；全部失败时保留尝试模型和错误。已通过 on_delta 展示正文的调用不重播。
- 流式工具参数若随过滤被截断，按过滤结果处理，不把残缺参数执行为工具调用。取消、参数错误、工具协议错误和原有重试逻辑保持。
- 写稿版本元数据保存实际模型和模型切换记录，设置页说明同步更新。

## 真实 workflow 验证

通过生产入口 `video_executor.rerun(..., 'write', ...)` 重跑原任务，没有重做调研或手工修改正文。主模型再次返回过滤结果，自动切换到 minimax-m3，67.612 秒后生成 v1。报告路径与 SHA-256 保持一致，原失败记录和数据库备份已保留。

原主题已经启用 auto_video，将其从 attention 恢复到正常继续状态后，应用自动定稿 v1 并创建成片任务 `fbe51979f28b41e5aa61a820cf7433d9`，AutoVideo 接受为 `d4235b7d-3110-4316-b906-941b79eea6e7`。任务已完成并回传成片，1920×1080，H.264 视频及 AAC 音频，380.47 秒、31,889,782 字节。未对任何平台投稿。

390 项相关后端测试、22 项设置页测试、生产构建、相关 ESLint、Python 编译及 git diff --check 通过。构建保留既有大型 YAML 编辑器分包提示。

首次空闲重启 `8478454e73ed439e909abf66d43c4ac8` 因持续有工作在途而超时，未发送重启信号。待工作结束后，通过相同的 restart_gate 完成维护 `7516786fb19e41fa97594bedfa275180`，在线实例 `1a4b8587b9ce4880a297c89b6f86eccc` 健康检查通过，修复已加载。首次失败记录另存 activation-before-retry.json/log。

证据目录：`state/validation/llm-filter-recovery-20261028/`，包含原状态与数据库备份、原报告摘要、真实模型切换日志、workflow-recovery.json、recovered-workflow-ui.png、media-check.json、测试和构建日志，以及维护日志与 activation.json。
