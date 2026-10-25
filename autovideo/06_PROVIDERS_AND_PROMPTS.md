# 06｜模型适配器与提示协议

## 1. Provider 接口（概念）

```python
class LLMProvider(Protocol):
    async def generate_json(self, *, system: str, user: str, schema: dict, max_tokens: int) -> dict: ...

class TTSProvider(Protocol):
    async def synthesize(self, *, text: str, voice_id: str, output_path: str) -> "TTSResult": ...

class Aligner(Protocol):
    async def align(self, *, text: str, audio_path: str) -> list["SpeechSegment"]: ...
```

Provider 名称、模型版本、参数与成本记录在 manifest；密钥只留在后端。

## 2. LLM 两阶段输出

**阶段 A：NarrationPlan**

- 输入：原文、目标时长、语言、观众定位（默认普通成年人）。
- 输出：中心信息、必要知识点、舍弃/压缩说明、旁白稿、事实来源片段标识、潜在歧义。
- 约束：不添加原文没有的新事实；类比标注为解释性表达；全文保持连贯口语。

**阶段 B：StoryBoardPlan**

- 输入：经过验证的旁白、组件目录及 props schema、目标主题。
- 输出：3–8 个有序场景；每个场景有 `purpose`、`component`、`props`、对齐的旁白段 ID、关键动画意图。
- 约束：必须选择已有组件；不得返回 JS/HTML/任意远程链接；不能制造超出输入的知识结论。

最终 `Video IR` 由后端根据真实 TTS 时间戳编译，而不是直接信任 LLM 输出的毫秒值。

## 3. 模型输出守卫

- 强制 JSON schema 或 Pydantic 验证；验证失败带结构化错误重试，不接受“看起来差不多”的文本。
- 明确区分模型 `UNKNOWN` / 配额不足 / 网络错误 / 结构错误。
- Prompt 版本固定并记录，输入中的“忽略上面的指令”等被当作素材数据，而不是执行命令。
- 供应商不可用时，真实任务返回清晰错误；Mock 只能用于测试或明确的 Dry Run，不可冒充真实出片。

## 4. 真实声画资源策略

- 至少实现一个可以端到端调用的真实 LLM provider 和一个真实中文 TTS provider；其他 provider 未来扩展。
- 语音对齐：优先使用准确返回时间戳的 provider，若无则本地/云端 aligner；失败不得伪造精确时间戳。
- V0.1 无外部视频模型依赖；音乐/音效可来自授权本地库，资产必须有 license/source 元数据。
- 配置预检应发现缺密钥、无中文音色、余额/权限不足等阻断条件。

## 5. 成本和中断

- 单视频建任务时设置预算和请求数/重试数上限；未能可靠估算的供应商项目要显示“费用估计不可得”。
- 若有价格表则按版本记录估算/实际；不能把未知收费记为 0。
- 每阶段输出独立 artifact，避免后续渲染失败后重新调用昂贵 TTS/LLM。

## 6. 不提前绑定单一模型供应商

具体云 LLM、TTS provider 由 G0 阶段根据可用 API、中文效果、费用和用户现有密钥确定。配置能力要支持替换，但不为每个供应商提前写一套复杂插件系统。

## 7. StoryboardPlan v0.2 语义能力（开发前新增）

LLM 的输出需要同时表达：

1. `purpose`：镜头准备解释什么；
2. `visual_intent`：`viewer_understands`、`before`、`after`，给出观众应当看到的状态变化；
3. `component + props`：仅选择注册组件及可校验对象；
4. `beats`：明确触发的旁白 segment ID、`start|end` 相对边界、允许动作和合法 target。

**禁止**让 LLM 自由输出像素级坐标、CSS、JavaScript、复杂关键帧、音频实际毫秒时间。发生组件表达能力不足时让模型重选组件/拆分场景；仍不能表达就显式报告，不要制造看似成功的静态页。

LLM 提供的 `before/after` 是供 QA 比较的**语义目标**，不应被 Renderer 当作执行性自然语言指令。需要精确对齐某个词时，aligner 必须支持词级时间戳；只有短语级证据就只对齐短语边界。
