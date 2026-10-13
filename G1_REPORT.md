# G1 验收报告

**Status: PASS** · 2026-10-13（Asia/Shanghai）

G1 的 Video IR v0.2 校验、六类固定组件 schema 检查、通信噪声真实音画 Golden Case、绝对时间线转换和 Beat 前后实际像素变化均已执行。PASS 仅表示本地 G1 验收门槛，不代表 G2 或产品化流程已完成。

## 交付物

| 产物 | 路径 |
|---|---|
| 中文有声视频 | `g1/artifacts/G1_通信噪声.mp4` |
| 可重放 Video IR v0.2 输入 | `g1/fixtures/communication-noise.video-ir.json` |
| 真实合成旁白及逐句音频 | `g1/fixtures/assets/communications_noise.wav`、`g1/fixtures/assets/segments/` |
| 短语对齐与字幕 | `g1/artifacts/alignment.json`、`g1/artifacts/captions.srt` |
| 编译后帧时间线与动作目录 | `g1/artifacts/compiled-timeline.json`、`g1/artifacts/action-catalog.json` |
| Beat 前后关键帧 | `g1/artifacts/keyframes/`、`g1/artifacts/contact-sheet.jpg` |
| ffprobe、完整解码及媒体 QA | `g1/artifacts/ffprobe.json`、`g1/artifacts/qa.json`、`g1/artifacts/decoded-audio.wav` |
| 文件哈希清单 | `g1/artifacts/manifest.json` |
| 命令、退出码和原始输出 | `g1/logs/commands.jsonl`、`g1/logs/*.log`、`g1/logs/execution.log` |

## 验收结果

- Video IR v0.2 顶层与六组件 Schema、组件 props、动作 registry、引用、目标、资产路径/hash、源文本 SHA-256、场景连续覆盖和中文字幕安全宽度在渲染前校验。非法输入由带错误码与字段路径的异常拒绝。
- 短语时间由五段分别生成的真实中文 Kokoro WAV 取实际 PCM 活动范围；只声明 phrase 级。30fps 全局绝对边界使用 `floor((ms*30+500)/1000)`，并验证 `150ms → 5 frames` 与 1001/2002/3003ms。
- **六个固定组件**均有 props schema 与静态 renderer 分支。Renderer 只接收通过 Python 校验的 JSON；没有把 IR 文本转换或执行为 JS/TS/shell。
- Golden Case 流程为 `reveal → activate_path → mark_disrupted`。实际渲染视频从连续帧中提取动作前后 PNG；局部区域变化率分别为：显现 `6.148%`、路径 `23.070%`、干扰 `25.981%`。画面含中文烧录字幕与 H.264/AAC 真实声音。
- 媒体：1920×1080，30/1 fps，539 帧，17.984 秒；视频与音频完整解码成功。AAC 解码音频和原始 TTS WAV 相关系数 `0.99995`，证明音轨来自本次真实合成语音。
- Validator 自动化结果：**18 passed**，最终日志 `logs/pytest-g1-06.log`；必须负例含未注册组件/动作、额外 props、title_card 非法动作、无效 path/target、scene/segment 引用错误、重复 ID、路径逃逸、符号链接、hash 错误、超宽字幕及图表长度不匹配。

## 真实执行命令

成功的最终验收命令：

```console
g0/.venv/bin/python -u g1/run_g1.py test
g0/.venv/bin/python -u g1/run_g1.py validate
g0/.venv/bin/python -u g1/run_g1.py render
g0/.venv/bin/python -u g1/run_g1.py verify
g0/.venv/bin/python -u g0/verify_design_snapshot.py
```

`commands.jsonl` 按顺序记录真实 Python、pytest、Remotion、ffprobe、FFmpeg 参数、工作目录、退出码和耗时。首次 `all` 调试、两次沙箱内 Chrome `SIGABRT` 以及之后通过自动审核的渲染重试也都保存在日志里。Remotion 使用项目锁定的 Remotion 4.0.535、Node.js 22.21.1 与 Chrome；TTS 使用 G0 本机已有模型文件（权重未复制进 Git），无云端请求。

## 风险与限制

- `alignment.method=provider, granularity=phrase` 表示独立 TTS utterance 的样本边界来源；没有词级 forced alignment 或逐词精度主张。
- 只完整渲染通信 `flow_diagram` Golden Case；其它五个组件验证了严格 props schema 与静态 renderer 分支，但没有各自制作单独的真实音频成片。
- 本机中文系统字体、macOS ARM64 FFmpeg 和 Chrome 固定路径仍需跨系统适配。Remotion 许可、Kokoro 代码与权重、字体许可的商业再分发仍须按项目实际部署形态复核。
- 未执行 G2，也未引入 LLM provider、云 TTS、图片/视频生成模型或任意生成代码。

## 文件变更与提交

变更文件包括 `.gitignore`、本报告、`g1/video_ir.py`（严格 validator/frame compiler）、`g1/run_g1.py`（TTS、受控渲染与 QA）、`g1/renderer/src/Root.tsx` 和 `index.ts`（六个固定组件）、`g1/tests/test_video_ir_v0_2_runtime.py`、`g1/README.md`，以及 `g1/fixtures/`、`g1/artifacts/`、`g1/logs/` 中列出的真实输入与证据。冻结的 00–14 设计包、Video IR v0.2 schema 与原始 G0 文件保持原样。Git commit 记录在交付消息与 `git log -1`。
