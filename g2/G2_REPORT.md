# G2 验收报告：DeepSeek 真实生成与成片验证

**结果：PASS** · **运行：** `20261013T085511Z` · **日期：** 2026-10-13

G2 使用真实 DeepSeek `deepseek-flash` 生成中文旁白与分镜，通过本地约束后用 Kokoro 中文语音和固定 Remotion 组件生成了 Case A《通信噪声如何影响信息传输》。API 请求启用了 thinking 和 high reasoning；请求使用 JSON mode。最终视频、源 IR、提示词、脱敏后的响应、费用记录、渲染日志和验收数据均保存在独立运行目录中。API 密钥从本地 `.env` 读取，该文件被 Git 忽略。

## 验收结果

- 真实 DeepSeek 调用 3 次，mock 调用 0 次；模型输出通过 NarrationPlan、StoryboardPlan 和 Video IR v0.2 校验。播放器只执行登记组件，没有运行模型生成的代码。
- Kokoro-82M v1.0 `zf_xiaoxiao` 生成普通话配音，实测 17.433 秒；最终 MP4 为 17.451 秒、1920×1080、30 fps、H.264，含 48 kHz 双声道 AAC 和烧录字幕。
- 视频全流解码通过，523 帧与 IR 时间线一致；解码 AAC 与源 TTS 的相关性为 0.99994。
- 3 个场景含 5 个短语级 Beat。路径启动、信道受扰等画面变化均通过前后帧采样检查，观察到的像素变化比例为 1.68%–3.79%。
- 成片接触表经人工目视检查，三场内容与中文字幕可读。

## 产物

- 视频：[`G2_CaseA_通信噪声.mp4`](runs/20261013T085511Z/artifacts/G2_CaseA_通信噪声.mp4)
- 验收数据：[`qa.json`](runs/20261013T085511Z/artifacts/qa.json)、[`manifest.json`](runs/20261013T085511Z/artifacts/manifest.json)、[`model-cost.json`](runs/20261013T085511Z/artifacts/model-cost.json)
- 视觉抽帧：[`contact-sheet.jpg`](runs/20261013T085511Z/artifacts/contact-sheet.jpg)、[`keyframes/`](runs/20261013T085511Z/artifacts/keyframes/)
- 生成记录：[`commands.jsonl`](runs/20261013T085511Z/logs/commands.jsonl)、[`llm_calls.jsonl`](runs/20261013T085511Z/logs/llm_calls.jsonl)、[`remotion-render.log`](runs/20261013T085511Z/logs/remotion-render.log)
- IR 与可复核输入：[`case-a.video-ir.json`](runs/20261013T085511Z/fixtures/case-a.video-ir.json)、[`narration-plan.json`](runs/20261013T085511Z/artifacts/narration-plan.json)、[`storyboard-plan.json`](runs/20261013T085511Z/artifacts/storyboard-plan.json)

## 失败尝试与费用

此前三次 DeepSeek 运行均保留在 `runs/`：`20261013T084224Z` 的分镜响应达到 token 上限；`20261013T084638Z` 的 Beat 指向不存在的 `line:1`；`20261013T084930Z` 有两次截断，最后一次偏移超出 schema。根据各次 API usage 和官方高峰未命中缓存价格计算，四次 DeepSeek 运行（共 14 个 API 调用）的保守费用上界合计为 **$0.1014027**；最终通过运行的上界为 **$0.0181128**。实际收费可能更低，模型账单尚未独立核对；本地计算成本未计量。价格依据为 [DeepSeek 官方价格表](https://api-docs.deepseek.com/quick_start/pricing/)。

为修复失败，本地分镜校验增加了组件目标存在性检查和可用目标反馈；分镜输出约束收紧为三场景、最长三条 Beat，以减少高推理输出截断风险。此前的请求、响应（不包含 `reasoning_content`）、验证失败和 token usage 都有独立日志。

## 变更与验证

- 新增 `g2/` 执行器、Case A 素材、NarrationPlan / StoryboardPlan schema、固定 renderer、测试和独立运行产物；更新 `.gitignore` 忽略 `.env`。
- 已运行：`g0/.venv/bin/python -u g2/run_g2.py all`，最终运行 `20261013T085511Z`，所有阶段通过。
- 已运行：`g0/.venv/bin/python -m pytest g2/tests/test_g2.py g1/tests/test_video_ir_v0_2_runtime.py -q --disable-warnings`，**30 passed**。
- 已运行：`python3 -m py_compile g2/run_g2.py g2/tests/test_g2.py` 和 `git diff --check`，均通过。

## 风险与范围

旁白来源由逐字原文引用校验；该流程没有独立语义评审模型。字幕与 TTS 使用真实 WAV PCM 活动检测进行短语级对齐，不提供词级精度。DeepSeek 官方接口文档确认本次使用的 Chat Completions endpoint、JSON Output 和 thinking 参数：[API](https://api-docs.deepseek.com/api/create-chat-completion/)、[Thinking Mode](https://api-docs.deepseek.com/guides/thinking_mode/)。

本报告完成 G2；后续里程碑未启动。
