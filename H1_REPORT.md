# H1 报告：视觉表现力与丰富素材对照实验

**状态：PARTIAL** · **日期：2026-10-13** · **授权：用户明确授权进入 H1**

**恢复点：** `b111c09`（M0 基线通过）

**实验定义：** `h1/experiment.json` · **最终评测版本：** v3

## 结论

已完成 M0 固定组件 B 与 AI 背景图加受控镜头运动 C 的六组真实配对片段。所有 12 段样片均通过完整音视频解码；每组使用相同源音频、字幕、IR 场景和时长，解码后 PCM 音频完全一致。当前尚无两名独立评议者的评分，因此无法判断 C 是否提升视觉成熟度或是否满足非退化要求。H1 暂为 **PARTIAL**。

路线 A（自由 Remotion 代码）没有执行。Docker daemon 不可用；macOS `sandbox-exec` 已弃用；`ulimit -v 2097152` 返回 `setrlimit failed: invalid argument`。本机无法验证网络、密钥、文件访问与内存限制均有效，因此没有运行自由或模型生成的代码。

## 样片与技术验证

从 M0 Golden-3 的三个主题中各选择两个场景，共六组。B 复用 M0 固定组件渲染；C 在同一冻结输入上叠加六张 AI 生成背景图、固定组件及受限镜头移动。Video IR v0.2、旁白、字幕和场景边界保持不变；生产 renderer 与 schema 未修改。

最终 C 全长视频如下：

| 案例 | 时长 | 帧数 | 渲染耗时 | 文件大小 | 全流解码 |
|---|---:|---:|---:|---:|---|
| case-a | 19.968 秒 | 599 | 43.857 秒 | 7,255,066 字节 | PASS |
| case-b | 17.835 秒 | 535 | 39.006 秒 | 4,733,238 字节 | PASS |
| case-c | 19.477 秒 | 584 | 42.517 秒 | 2,850,732 字节 | PASS |

最终六组配对片段均为 1920×1080、30 fps、H.264/AAC、48 kHz 双声道。逐组比较结果为 6/6 同帧数、同片段时长、解码 PCM 哈希完全相同、音频相关系数 1.0。完整媒体证据和 SHA-256 位于 `h1/runs/PAIR_MEDIA_MANIFEST_v3.json`；渲染命令和日志位于 `h1/runs/case-*/render-command-v3.json`、`render-v3.log`。

首次及第二次视觉 QA 版也保留在 `h1/runs/`。检查发现背景层遮挡与字幕层级问题，最终 v3 已修正并生成评测包；不应使用更早版本评分。

## 素材、成本与权利

通过内置 image_gen 生成六张背景图，6 次调用、0 次重试，合计 10,444,884 字节。提示词、素材哈希和来源记录见 `h1/asset-manifest.json`、`h1/prompts.json`。工具没有提供模型/版本或单资产账单信息：版本为 `NOT_EXPOSED_BY_TOOL`，直接边际费用为 `NOT_MEASURED_TOOL_DOES_NOT_EXPOSE_BILLING`。本轮没有新增 DeepSeek 或 TTS 调用。

素材仅用于本实验。OpenAI 使用条款说明，用户在法律允许范围内拥有输出；这不等于对第三方权利完成独立清查，输出也未保证唯一。参见[OpenAI Terms of Use](https://openai.com/policies/terms-of-use/)。资产清单记录了这项限制；当前不据此宣称素材已取得商业发行许可。

## 人工评测与缺口

可供评议者使用的最终包为 `h1/review/BLIND_REVIEW_PACKAGE_v3.json`，表格为 `h1/review/BLIND_REVIEW_FORM.md`，编码样片在 `h1/review/materials-v3/`，路线映射密钥为实验者专用文件 `h1/review/H1_REVIEW_KEY_v3.json`。请两名独立评议者分别完成全部六组的五维 0–5 分数、偏好和观察，再锁定评分后揭示路线映射。不要把 review key 发给评议者。

包中只隐藏了 B/C 标签并随机编码 left/right；素材本身有可能暴露 AI 背景处理差异，因此这是盲编码比较，不是严格双盲。本轮操作者已查看内部样片，不能作为独立盲评者。收到两份表后，需报告各维度的配对差值与一致性；在此之前不得声称 C 有质量提升或已满足 H1 PASS。

## 检查与产物

- H1 adapter 单元测试：4 passed。
- G0–G4 设计及路线契约测试：19 passed。
- Python 脚本语法编译、JSON 解析、12 段全流解码、六组音频/时长/帧数比较、`git diff --check`：均通过。
- 资产适配器在 manifest、相对路径和哈希验证后使用内容寻址缓存；未请求网络。生产 renderer 与 Video IR schema 未更改。

逐条命令、运行环境与输出摘要见 `h1/EXECUTION_LOG.md`。H1 只有在人工评测完成并确认视觉提升、且其他质量维度无明显退化后，才可改为 PASS。
