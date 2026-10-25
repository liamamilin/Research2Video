# 03｜Video IR v0.2 数据契约（设计修订）

> 产品目标仍是 **AutoVideo V0.1**。本文件是开发前的 IR v0.2 设计基线；替代旧版 IR v0.1 用于**新实现**。此前无生产数据，无需历史数据迁移；旧 Schema 保留供对照，禁止混用。**本文不是已实现声明。**

## 1. 为什么旧 IR 不够

旧版 `scene = component + props + 起止时间` 只能回答“这个镜头放什么”，不能表达“镜头内部依次发生什么”。一个有流程图、但信号永远不运动的镜头，技术上可渲染，解释力却很弱。因此需要在保持受约束渲染的基础上添加两个层次：

- **Visual Intent（视觉意图）**：观众应该注意/理解什么、画面状态怎样变化。它是供导演和 QA 使用的语义说明，不由渲染器解释成任意代码。
- **Visual Beat（视觉动作）**：一次有明确含义、目标对象和触发时机的可观察变化。由注册组件解释成受限动画，不是任意关键帧或 JS 命令。

> 从“镜头里有什么”升级为“镜头为什么存在、镜头里发生哪些有意义的变化”。

## 2. 处理链：语义计划与可执行 IR 分离

```text
原文
  ↓ 结构化内容提取、事实锚点
NarrationPlan（旁白及内容来源）
  ↓ 拆成有稳定 ID 的语义短语
NarrationSegments（无猜测时间戳）
  ↓ 真实 TTS + 时间对齐
AlignedNarration（真实音频 + 有依据的时标）
  ↓ StoryboardPlan：Visual Intent + Visual Beats + 注册组件
Video IR Compiler（解析锚点、资源、时间边界）
  ↓ JSON Schema + props/action/引用/时间验证
Video IR v0.2（仅结构化数据）
  ↓ Remotion 已注册组件 + 固定声音合成策略
MP4 + SRT + manifest + 质量报告
```

- LLM 可以提出“激活一段信号路径”，但不能注入 CSS、JS、HTML、SVG markup、文件路径或任意动画代码。
- **StoryBoardPlan 不等于 Video IR**：LLM 不填写最终绝对 `start_ms`；编译器从已对齐的旁白和策略派生。
- 组件的可用动作与合法 `target_id` 均由注册表约束。没有组件支持的表达，模型必须改用其他已注册表达或明确报告 `UNSUPPORTED_VISUAL_INTENT`。

## 3. 权威文件

- `schemas/video-ir-v0.2.schema.json`：顶层结构。
- `schemas/components/*.schema.json`：六个组件的 props 结构。
- `schemas/component-registry-v0.2.json`：组件允许哪些动作、作用于哪类 target。
- `examples/video-ir-beats-v0.2.json`：展示句首触发、镜头内多个动作和真实资源占位符。
- 旧的 `schemas/video-ir-v0.1.schema.json` / `examples/video-ir-short.json`：历史样例，**不应作为新实现的正式输入**。

JSON Schema 验证**必要但不充分**；还要独立做交叉引用、文件、时序和语义动作校验。

## 4. 字段语义

### `video`, `source`, `style`, `assets`

沿用 v0.1 基本语义。`video.duration_ms` 是全片计划音画长度，最终的**帧数**由统一边界转换函数派生。`assets` 只能引用工作空间内文件，必须在运行时检查真实路径、哈希及媒体类型；`../`、符号链接逃逸和未知 URI 均拒绝。

### `narration`

- `text`：完整中文旁白文本。
- `audio_asset_id`：真实 TTS 或合法 fixture 音轨的 ID。
- `alignment`：`method: provider | forced_aligner`、`granularity: phrase | word`；这些是**来源说明**，不能当作客观准确度保证。
- `segments[]`：稳定 `id`、`text`、`start_ms`、`end_ms`。段落边界须由实际音频对齐得出；纯估算不能填进可执行 IR。

语音对齐如仅提供**短语级**时，允许在短语边界触发动作；不得声称某个内部汉字或词已经准确同步。需要词级高亮时必须具有词级时间证据。

### `scenes[]`

- `id`, `start_ms`, `duration_ms`：由编译器生成的场景时间窗口。
- `purpose`：镜头一句话目的，保留便于搜索。
- `visual_intent`：`viewer_understands`（观众应该理解什么）、`before` 和 `after`（前后画面状态），供审查用。
- `component`、`props`：来自组件库、经组件级 Schema 校验的数据。
- `narration_segment_ids`：当前镜头覆盖的旁白段 ID，按语音顺序。
- **`beats[]`**：按实际触发时机排序的视觉动作，每项包含：
  - `id`：全片唯一的动作 ID。
  - `action`：注册表许可动作，例如 `reveal`、`highlight`、`activate_path`、`mark_disrupted`。
  - `target_id`：组件声明的可寻址目标，如 `scene`、`node:sender`、`path:main`、`line:0`。
  - `trigger`：`{segment_id, boundary: start|end, offset_ms}`。相对真实语音边界，而不是 LLM 猜测绝对秒数。

`beats` 是**离散语义动作**；每个动作的缓动、持续帧数、视觉细节由组件的设计系统定义，不能通过模型上传自定义关键帧代码。后续如果需要新的视觉动作，先增加组件能力、contract、fixture 和测试，再暴露给 LLM。

### `audio_mix`（可选）

旁白始终为主轨。可选的内置授权音乐与音效统一登记为 `asset_id`；音乐音量/ducking、音效到 `beat_id` 的绑定由受约束混音策略执行。不提供音乐时仍可成功出片。

## 5. 时基：两种单位，但只有一个计算来源

**信息来源以毫秒为单位**（真实音频和语音对齐）；**Remotion 渲染以整数帧为单位**。

实现必须：

1. 使用统一函数 `frameAt(ms) = floor((ms * fps + 500) / 1000)`（`ms` 为非负整数） 处理**全部绝对边界**；明确使用 half-up 整数帧舍入，禁止 Python `round()` 与 JavaScript `Math.round()` 在 .5 情况下出现差异；不可每段先将持续毫秒数各自四舍五入再累加。
2. 场景边界采用半开区间 `[start_frame, end_frame)`；`duration_frames = end_frame - start_frame`。
3. 每个 beat 由所引 `segment_id` 的真实 `start_ms/end_ms + offset_ms` 派生**绝对**时间后再调用 `frameAt`；若动作落在自己镜头之外则拒绝，不静默挪动。
4. 所有 scene 连续覆盖 `[0, total_frames)`，无空洞、交叠；尾帧依据音频尾音/收尾留白政策而定，并且实际媒体可核对。
5. 同一帧多个动作依 `beats[]` 稳定顺序执行；冲突 target 动作应判定冲突或提供预先规定的解析规则。

## 6. 运行时必须验证的跨字段不变量

| 验证器 | 必要约束 | 错误示例 |
|---|---|---|
| Schema | 禁止未知字段，数据类型与范围正确 | `beats` 里塞 HTML |
| Component props | `flow_diagram` 的 nodes/edges/path 结构合法 | 引用不存在的 node |
| Action registry | `component + action + target_id` 合法 | title_card 激活路径 |
| Reference | segment、asset、beat ID 唯一且存在 | trigger 指向丢失的旁白段 |
| Time | beat 在自身 scene 内、各 scene 覆盖完整时间线 | 事件触发晚于镜头结束 |
| Content | 视觉文本与讲稿无明显冲突、有来源映射 | 图上数值与输入不一致 |
| Asset | 真实存在、hash 匹配、路径被沙箱限制 | 相对路径越界 |

**严格策略**：无效 IR 直接拒绝并返回可定位的 `field_path` 和 `error_code`；模型有限重试修复，不做隐式“智能纠正”。

## 7. 与六个基础组件的边界

- 一个场景先采用**一个主组件**；字幕、全局水印（如有）等由系统统一叠加。
- 组件内部可以有多个被声明的、按顺序触发的动作，所以“一个主组件”不等于“静态一页”。
- V0.1 不提供任意嵌套层、自由画布、任意 CSS 属性、通用 3D 摄像机或用户脚本。
- `visual_intent` 描述期望理解结果，不是“允许渲染器根据自然语言自由生成画面”的后门。

## 8. 变更与演进

- 旧 v0.1 的 Schema 仅归档；**新工程只消费 v0.2**，并保留独立 version gate。
- 产品 G0 仍可采用最简实验代码，证明真正的声画链路；**G1** 必须用 v0.2 完整契约，不能将实验样片视为全局 IR 实现。
- 如果将来加入图片、视频模型、复杂镜头运动，仍应保留 `visual_intent → beats → 可控执行` 的边界。经评审后可引入新的表达载体，而不是无限堆积预设页模板。
