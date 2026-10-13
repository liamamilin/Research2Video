# 13｜G1 组件与视觉动作测试矩阵（设计要求）

> 本文件规定 Agent 下一阶段的验收；**不表示已经执行真实视频测试**。

## 1. 测试分层

| 层级 | 输入 | 必须验证 | 允许的证据 |
|---|---|---|---|
| L1 结构 | JSON 数据 | 顶层 / 六组件 Schema 正反例 | 自动化单测 |
| L2 语义 | scene + props + beats | 对象 ID、动作合法性、旁白段关联、目标类型 | 单测、负例 |
| L3 时间 | 真实对齐时间戳 | scene 连续、beat 时间点、30fps 边界、无漂移 | 帧索引断言 |
| L4 画面 | 真实 fixture 媒体 | beat 前后实际可观察状态变化 | 渲染帧、视频 |
| L5 内容 | Golden Case | 图形值和文本语义与输入一致 | 输入对照、评审记录 |

## 2. 正例

- `flow_diagram`：两节点以上、合法路径、先 reveal，再 activate_path，后 mark_disrupted；动作响应旁白 n1/n2。
- `comparison`：明确左右两组对照值，能够高亮其中一侧或两者差异。
- `bar_chart`：有单位、柱子数与类别数相等，图形显示数值匹配。
- `concept_reveal`：标题 + 两行内容，合法 line 索引被高亮，超长内容触发布局保护。
- `timeline`：事件 ID 可唯一寻址，按输入顺序依次出现。
- `title_card`：标题展示、强调、退出；字幕区不被覆盖。

## 3. 必须拒绝的负例

1. 未知 `component` / 额外 props / 未知 `action`。
2. `title_card` 上 `activate_path`。
3. `flow_diagram` 的 path 指向不存在的 edge。
4. beat 的 `target_id=node:missing` 指向不存在节点。
5. beat `segment_id` 存在，但不属于当前 scene。
6. 指向实际存在但属于其他 scene 的 Beat 时间点。
7. 同一场景内同 ID beat 重复，或场景 ID/旁白 ID 重复。
8. 中文长文本渲染超出安全区域或覆盖烧录字幕。
9. 同一音频/时间线的 frameAt 结果不可复现。
10. 本地 `assets` 路径包含逃逸/符号链接，或 hash 不匹配。

## 4. 帧边界演练

在 30fps 测试输入包含 1001ms、2002ms、3003ms 等不能整除一帧的时间；用**所有绝对边界**的同一个 half-up `frameAt(ms) = floor((ms*30+500)/1000)` 函数转换，并专门检查 `150ms → 5 frames`（防 Python `round(4.5)==4`）。断言每镜头 `start_frame = previous.end_frame`，而不是靠四舍五入每段 `duration_ms` 再相加。

## 5. 交付物与质量门槛

- G1 报告至少附：可重放 fixtures、validator 测试报告、关键帧 PNG（前/动作中/后）、真实渲染 MP4 与 ffprobe 结果、组件可用动作目录。
- 如果任意一个必测负例被放行，或者 Golden Case A 的“传输→干扰”状态不可辨，G1 只能写 `PARTIAL` 或 `FAIL`。
- 测试中可以使用**真实可授权 fixture 音频**替代每次在线 TTS，但不能用没有音频的 Mock IR 冒充实际渲染验收。
