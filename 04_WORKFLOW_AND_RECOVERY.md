# 04｜生成工作流与失败恢复

## 1. 生产有向流程

```text
VALIDATE_INPUT → PLAN → WRITE_SCRIPT → GENERATE_TTS → ALIGN_AUDIO
      → BUILD_VIDEO_IR → VALIDATE_IR → RENDER_PREVIEW
      → RENDER_FINAL → MECHANICAL_QA → SEMANTIC_QA → COMPLETE
```

说明：初版可将 `RENDER_PREVIEW` 作为低分辨率或采样帧，不需要单独导出整条视频；`SEMANTIC_QA` 是辅助评估，不可虚构它等价于绝对审美保证。

## 2. 为什么声音先于最终时间线

- LLM 先把输入文本转为精简、准确的旁白稿。
- TTS 产生真实 WAV/MP3 并读取真实时长。
- 对齐器生成句/短语时间戳，计算字幕区间与关键动画锚点。
- 再为镜头分配开始/结束帧，画面以真实音频为基准，不凭空估时。
- 若旁白时长超出目标范围，返回 `DURATION_OUT_OF_RANGE`，允许有限次数重写再合成，不单纯倍速挤压。

## 3. 状态机（持久化）

- 顶层状态：`QUEUED` → `RUNNING` → `COMPLETED` 或 `FAILED` / `CANCELLED`。
- 阶段状态：`PENDING`、`RUNNING`、`SUCCEEDED`、`FAILED`、`SKIPPED`。
- 只有全部必要门槛通过才允许 `COMPLETED`；任何阶段失败必须记录 `error_code`、`stage`、`retryable`。
- 超时、用户取消和资源耗尽均要进入明确终态，不允许静默卡住。

## 4. 幂等与恢复

- 后端以事务领取任务；一个任务同一时间只允许一个 active attempt。
- 退出后启动恢复扫描：对遗留 `RUNNING` 标记为 `INTERRUPTED` 并按可恢复阶段重新排队或失败，不宣称后台进程仍运行。
- 已成功输出的视频永久保留，重试创建新的 attempt 并在 UI 展示版本。
- 同阶段复用依据：输入内容 hash + provider/model/version + prompt version +配置 +已有制品 hash 完全匹配。否则必须重新生成。
- 不允许运行中覆盖早期 successful 输出，避免重试破坏用户成片。

## 5. 重试预算（建议默认）

- 网络超时/临时 5xx：指数退避，最多 2 次重试（具体以供应商速率限制调整）。
- LLM JSON 不合法：允许 2 次基于验证错误的修复，不允许跳过校验。
- 同一场景布局/字幕错误：最多 2 次重生成；超限则失败并给出可复现报告。
- 成本上限、配额不足、授权错误等不可重试异常：立即停止。

## 6. 故障演练

强制测试在每个阶段后终止进程、重新启动、恢复或报告失败；确保已有 MP4、素材、密钥配置不被改坏。

## 7. v0.2 视觉动作编译补充（开发前新增）

- StoryboardPlan 包含镜头视觉意图、组件 props、`beats` 与旁白 segment ID 锚点；**不允许模型臆造最终毫秒绝对时间**。
- `ALIGN_AUDIO` 后才把 `trigger(segment_id, boundary, offset_ms)` 转为绝对毫秒，再使用统一 `frameAt()` 转成整数帧。
- `VALIDATE_IR` 必须顺序执行：顶层 Schema → 组件 props Schema → 注册动作/目标 → 交叉引用 → 帧时间线/冲突 → 资源隔离验证。
- 组件选择或 beat 不被支持时应返回 `UNSUPPORTED_VISUAL_INTENT` 或 `INVALID_BEAT_TARGET`，允许有限次数设计层修复，不能跳过动作假装生成成功。
- `RENDER_PREVIEW` 应增加 beat 的前/后状态关键帧；镜头内音画变化属于 QA 对象。
