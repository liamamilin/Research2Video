# AutoVideo｜文本到视频自动生产引擎

**文档版本：V0.3-DESIGN-FINAL** · 2026-10-13 · **产品目标：V0.1；Video IR：v0.2** · MVP 已按本地单机 10–20 秒范围完成并验收；MVP 后 45、60、90 秒长时长样片均已真实生成并通过媒体 QA（见 `g3/LONG_FORM_ACCEPTANCE.md`）

## 一句话产品定义

用户只输入中文文本，系统自主完成内容提炼、讲稿、分镜、旁白、动画、字幕、渲染、自动质量检查，输出可观看的 MP4。V0.1 聚焦程序化知识解释视频，不依赖文生视频模型。

## 冻结的第一版边界

- 目标用户：单人、本地优先；输入仅文本。
- 视频类型：中文知识解释类，主要使用动效图文、示意图和图表，不做数字人。
- 标准交付：16:9、1920×1080、30fps、H.264 MP4 + AAC 音频；原始目标 45–90 秒、默认 60 秒。当前界面接受 10–90 秒，真实媒体验收覆盖 MVP 的 10–20 秒以及 45、60 和 90 秒端点样片；中间时长未逐一测试。
- 正常生产不要求人工参与；用户可查看生成过程、失败原因并重试。
- 技术底座：React + TypeScript 前端、FastAPI 工作流/API、Node/Remotion 渲染器、FFmpeg/ffprobe、结构化 Video IR、可替换的 LLM/TTS。
- 第一版只允许 **已注册的视觉组件与视觉动作（Visual Beats）**；禁止把 LLM 生成的任意 JS/TS 直接送入本机执行。
- 优先单机单 worker、SQLite 和本地文件存储，不提前上云或微服务。

## 最终交付说明

- `START_HERE.md`：开发入口、文件优先级与首个 Agent 任务。
- `RELEASE_STATUS.md`：当前冻结范围、实施状态和待验证问题。
- `BASELINE_SHA256.json`：MVP 核心规范的哈希基线；可移植测试据此保护核心文件。
- `AGENT_FIRST_TASK.md`：可直接复制给编码 Agent 的 G0 指令。
- `PACKAGE_MANIFEST.json`：本次交付的文件清单与哈希。

## 文档导航与阅读顺序

| 序号 | 文件 | 决定什么 |
|---|---|---|
| Agent | `AGENTS.md` | 自动识别的编码 Agent 基本纪律 |
| 00 | `00_PRODUCT_CHARTER.md` | 产品价值、输入输出、自动化边界 |
| 01 | `01_MVP_REQUIREMENTS.md` | 用户场景、必须有/不做的功能、非功能需求 |
| 02 | `02_SYSTEM_ARCHITECTURE.md` | 服务边界、存储、信任/运行保护 |
| 03 | `03_VIDEO_IR_CONTRACT.md` | IR v0.2、视觉意图/动作、锚点、兼容与验证 |
| 04 | `04_WORKFLOW_AND_RECOVERY.md` | 状态机、时间线、幂等、崩溃恢复 |
| 05 | `05_VISUAL_AND_AUDIO_SYSTEM.md` | 动态视觉表达、注册动作、主题 tokens、声音设计 |
| 06 | `06_PROVIDERS_AND_PROMPTS.md` | LLM/TTS 接口契约、输出约束、成本 |
| 07 | `07_UI_AND_API.md` | 页面、交互、HTTP 接口 |
| 08 | `08_QA_AND_ACCEPTANCE.md` | 自动检查、样片质量、可测指标 |
| 09 | `09_IMPLEMENTATION_PLAN.md` | 里程碑、前置依赖和阶段验收 |
| 10 | `10_AGENT_EXECUTION_BRIEF.md` | 发给编码 Agent 的工作指令 |
| 11 | `11_DECISION_LOG.md` | 冻结决策、开放问题、风险 |
| 12 | `12_EXPERIMENT_PROTOCOL.md` | A/B/C 技术路线比较方法 |
| 13 | `13_G1_TEST_MATRIX.md` | 下一阶段 IR、组件、视觉动作和真实渲染验收矩阵 |
| 14 | `14_DESIGN_AUDIT.md` | 上轮 V0.2 设计修订、已验证项和未完成风险 |
| 15 | `15_POST_MVP_PRODUCT_STRATEGY.md` | MVP 后产品价值、版本能力边界 |
| 16 | `16_POST_MVP_ROADMAP.md` | M0 + H1–H4 的假设、里程碑、阶段门 |
| 17 | `17_CREATIVE_ARCHITECTURE_EVOLUTION.md` | 未来多媒介导演、执行器与 IR 演进 |
| 18 | `18_VISUAL_LANGUAGE_AND_ASSET_SYSTEM.md` | 视觉语法、素材生产、统一风格与声音 |
| 19 | `19_AUTONOMOUS_QUALITY_LOOP.md` | 自动审片、局部修复和上限保护 |
| 20 | `20_EVALUATION_DATASET_AND_EXPERIMENTS.md` | 评测集、基准测试和实验纪律 |
| 21 | `21_PROVIDERS_COST_RIGHTS_AND_PROVENANCE.md` | 模型能力、成本、隐私、授权与来源 |
| 22 | `22_POST_MVP_USER_EXPERIENCE.md` | 未来三层交互、项目与局部重生成 |
| 23 | `23_POST_MVP_AGENT_EXECUTION_PROTOCOL.md` | 后续阶段授权、RFC 和 Agent 交接规范 |
| 24 | `24_ROADMAP_DESIGN_AUDIT.md` | 本轮增补审查、版本解释与未验证事项 |
| tests | `tests/test_design_contracts.py` | 文档契约自检，不等于视频 G1 验收 |
| schemas | `schemas/video-ir-v0.2.schema.json` + `schemas/components/*.schema.json` | 新的顶层与组件 props Schema |
| registry | `schemas/component-registry-v0.2.json` | 允许的组件视觉动作与目标类型 |
| examples | `examples/video-ir-beats-v0.2.json` | v0.2 结构示例；音轨是明确占位符，不可直接渲染 |
| archive | `schemas/video-ir-v0.1.schema.json`, `examples/video-ir-short.json` | 旧版仅作设计历史对照 |

## 文档优先级

1. `01_MVP_REQUIREMENTS.md` 的产品边界高于其他设计建议。
2. `03_VIDEO_IR_CONTRACT.md` + v0.2 Schema + 组件 Registry 决定新数据契约；示例是非真实媒体 fixture，不能冒充可渲染数据。
3. `09_IMPLEMENTATION_PLAN.md` 决定实施顺序，阶段验收未通过不得跨阶段宣称完成。
4. `11_DECISION_LOG.md` 的 FROZEN 条目如需改动，需记录原因、影响和迁移方案。
5. `15–24` 为 **FUTURE**：只对未来阶段设计提供参考，不能覆盖 `01`/`03`/`09` 的 MVP 实施约束；**不得自动执行 H1–H4**。

## 这轮设计修订的重点

- 旧 IR 只能表达“一镜头一种页面”；v0.2 增加 `visual_intent` 和绑定真实音频时间锚点的 `beats`，允许一个场景内有清楚的动作变化。
- **产品目标仍为 V0.1；G0 验证范围不变。** 这里的 v0.2 是数据契约与文档的版本，不是声称产品或 G1 已实现。
- 下一阶段 G1 的质量门槛追加“在实际渲染视频中看见有意义的 Beat 前后变化”，避免静态 PPT 式假成功。
- 13 号测试矩阵是 Agent 执行 G1 时必须遵循的验收要求，不意味着已经做出动态视觉系统。

## MVP 与后续阶段如何衔接

- **当前允许执行的顺序仍是 G0 → G1 → G2 → G3 → G4**；每阶段需真实验收与明确授权。
- **G4 后新增交接门 M0**：保存 MVP 基线、运行数据、成本和样片，不进行新功能实现。
- **未来 H1–H4**：H1 提高画面表现力，H2 混合多媒介，H3 有界自动审片/修复，H4 形成通用工作室；这些仅是研究与规划。
- `README`、`AGENTS`、`10`、`23` 如有看似不同的阶段建议，均以“**用户显式授权某一阶段后才能执行**”为准。

## 执行纪律

- 先完成 G0 技术验证，再投入完整 Web 产品化。
- 每阶段交付可运行代码、自动化测试、实机日志、失败项和提交记录。
- Mock 结果仅证实编排接口，不等于真实自动生成成功。
- 文档中出现的性能目标为验收候选，不是已达到的事实。
- 改变技术路线/扩张范围，先提案并等待明确批准。

## 参考资料（2026-10-13 核验）

- Remotion 文档：https://www.remotion.dev/docs
- Remotion Agent Skills：https://www.remotion.dev/docs/ai/skills
- Remotion 参数化视频：https://www.remotion.dev/docs/parameterized-rendering
- Remotion 渲染：https://www.remotion.dev/docs/render
- Remotion 字幕：https://www.remotion.dev/docs/captions
- Remotion AI SaaS 样板：https://www.remotion.dev/docs/ai/ai-saas-template
- Remotion 授权：https://www.remotion.dev/docs/license/pricing

**提醒：** Remotion 为 source-available/特定许可软件，并非可无条件再授权的通用开源库；上线或团队人数变化时应重新核验条款。

## Video brands

See [BRANDING.md](BRANDING.md) for the reusable corner mark, end card and sonic cue, the neutral Demo installer, and local-only brand profiles. In the research console use 设置 → 视频品牌.
