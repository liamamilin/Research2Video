# G2 真实模型自主出片

`run_g2.py all` 使用 DeepSeek `deepseek-flash` 生成 NarrationPlan 和 StoryboardPlan，再用 Kokoro 中文 TTS、PCM 短语边界对齐、Video IR v0.2 validator 和 G1 固定组件渲染器生成视频。模型只返回 JSON 并经过本地 schema、来源引用和组件/动作校验；renderer 只执行注册组件。请求/响应（不含 Authorization 或思考内容）、耗时、token 和费用上界保存在每次运行的证据目录。`.env` 中配置 `DEEPSEEK_API_KEY`；key 不会写入日志或产物。

通信噪声 Case A 使用三场结构；中间场以 Beat 展示发送、传输路径启动和信道受扰。其它主题可用 comparison、bar_chart 等已注册组件；类别和数值必须来源于输入，并由 G4 Golden Case QC 检查。

运行环境沿用 G0/G1 本机依赖：

```console
g0/.venv/bin/python -u g2/run_g2.py all
```

真实调用只请求 `https://api.deepseek.com/chat/completions` 的 `deepseek-flash`，启用 thinking 和 high reasoning effort，以 JSON mode 返回。每次响应上限为 100,000 completion tokens，每个指定时长短片最多 16 次实际 API 请求；HTTP 和网络失败也计数。按当前高峰未命中价计算，所有 16 次请求都用满 token 上限时的费用上界约为 $1.968。这只是防失控的宽松上限，正常调用远低于该数值。指定时长模式支持 10–90 秒，旁白字数随时长伸缩；TTS 实测不在目标时长 ±2 秒时，最多改写并重测两次。无 key、认证失败或服务不可用时清晰失败，不回退为 mock。

G4 三个真实案例的运行、独立 ffprobe/解码、抽帧、布局风险信号和案例语义验收见 [`../g4/README.md`](../g4/README.md)。

## 流程动态图解

安装布局依赖：`npm ci --prefix g2/layout --ignore-scripts --no-audit --no-fund`。设置 `AUTOVIDEO_G2_VIDEO_FORM=process_diagram` 后运行原有 `all` 入口，或在 Web 新建页选择“流程动态图解”。模型提供来源引用、节点、边和旁白引用；ELKjs 0.12.0 自动布局，注册的 `flow_diagram` 按真实短语与估算的短语内部位置展开和高亮。不会运行模型生成代码；不适合流程的素材返回明确说明。图解模式当前使用程序化画面，不能组合场景背景图。

过程证据另存 `diagram-plan.json`、`diagram-layout.json`、`diagram-timing.json`，通过原 Video IR、媒体 QA 和任务下载链路交付。原有知识卡片模式和已完成任务仍可使用。

运行时只为携带有效自动布局数据的 `flow_diagram` 兼容更多 beat 和完整短语内的正向偏移，仍校验每个动作的注册目标、场景范围与所属短语。原始 MVP schema 和规范保持基线原样；旧组件仍按原规则验证。新任务将渲染源码快照保存于自己的运行目录，便于复核与重试。


## 内容驱动的章节成片

同时设置 `AUTOVIDEO_G2_DURATION_MODE=content` 与 `AUTOVIDEO_G2_VIDEO_FORM=process_diagram`（或 `information_motion`），使用原 `all` 入口。`content_video.py` 按原文单元规划完整纲要，逐章生成带知识点/来源引用的讲稿，再逐章调用对应形式的分镜规划器；全片共用一条实测 PCM 时间线，不做固定目标时长拟合或截断。

章节数和旁白段数由内容决定。原文单元需完整有序分配到章节，章节知识点需被旁白引用，图解需完整有序覆盖全部旁白，且场景不能跨章。章节之间使用稍长停顿，画面标示当前章节。新增 `chapters.json`、`content-outline-plan.json` 和 `final-narration-plan.json`，所有实际请求、自动纠错和失败尝试继续保留。

冻结 MVP 契约不变；受控运行时仅在明确存在并校验通过 `content_structure`、且全部为已注册自动布局图解或显式 `information_motion` 展示组件时，扩展整片时长和场景数。资产、引用、动作、字幕和全帧覆盖仍需校验。结构覆盖不能证明所有语义均正确。

此模式的模型调用预算按实际章节数伸缩；每阶段重试次数仍有限。渲染等待按实测帧数伸缩，worker 在原启动/规划等待基础上增加实测渲染工作量的等待时间。这些是防止挂起的执行保护，不是建议的视频时长；不承诺无限长度或生产级可靠性。

内容模式重试时，仅复用前次尝试中原始模型输入完全一致、且重新通过当前校验的规划；`reused-plans.json` 保留来源与哈希，复用不记作新 API 调用。原请求、响应及失败记录保留。章节目前按原文顺序组织；乱序素材的跨章节重排尚未实现。

## 简洁信息动效

设置 `AUTOVIDEO_G2_VIDEO_FORM=information_motion`，可与 `AUTOVIDEO_G2_DURATION_MODE=content` 一起使用。`information_motion.py` 按内容生成场景，复用注册的 `title_card`、`concept_reveal` 与 `comparison`；标题、要点卡片和对比两侧按旁白引用依次淡入、轻移。无需新增模型服务或布局依赖。

此形式采用浅色排版，不能组合 Draw Things 背景；旧 `knowledge_cards` 与历史背景修订仍保留兼容。分镜必须有原文引文、完整有序旁白覆盖与每个可见元素的有效 cue。固定画面文字容量检查失败时，模型需缩短屏上措辞或拆成更多场景，旁白不因此截断。

分镜规划只把每段旁白的 `text` 发给模型，不再附带该段的 `source_quotes`。旁白是改写后的文本，与所引素材原句常有细微差别（例如素材「并在 Gemini 应用中上线…」对旁白「Gemini 应用同时上线…」）；同时给出两者会让模型把素材原句抄进 `cue.quote`，而 cue 必须落在该段旁白内，于是被本地校验拒绝。`cue.quote` 的校验失败信息会指出场景、目标、错误引文、疑似来源与该段旁白全文，重试反馈才有可修正的依据。

原始 MVP schema 不变；运行时通过 `presentation: {"preset": "information_motion"}` 明确启用 `minimal_light` 主题及三个注册组件，只支持 reveal 动作，仍校验资产、目标和所属短语范围。场景数量随内容决定。`information-plan.json` 与 `information-timing.json` 保存来源及出现时机；短语内时机是字符位置估算，不声明词级对齐。

## 输入准备与自动选型

`AUTOVIDEO_G2_VIDEO_FORM=auto` 在出片前调用 `input_preparation.py`，复用现有 DeepSeek 结构化生成和本地校验，选择 `process_diagram` 或 `information_motion`，然后进入原生成链路。手动指定这两种形式时仍整理输入，但保留用户选择；旧 `knowledge_cards` 路径保持兼容。

输入准备不重写原文句子：每个来源单元有保留/排序或明确排除记录，逐字重复必须引用实际保留的相同单元，问题引用必须存在于原文。通过后的 `PREPARED_SOURCE` 用于纲要、讲稿及分镜，Video IR 仍保留原始输入。模型分类和噪声判断并非语义正确性的证明，原文、整理结果及实际请求都保留可核对。

## 用户修订讲稿

Web 修订任务由 worker 设置 `AUTOVIDEO_G2_LOCK_NARRATION=1`，`authored_script.py` 保留用户文字与标点，仅将长句分成字幕可容纳的短语；跳过模型讲稿重写和输入去重。仍使用真实配音、经过校验的分镜规划及媒体 QA，不执行自由生成代码。`authored-narration.json` 保存作者与文本哈希。

worker 通过受控输入 JSON 向模型阶段传递可选 `creative_intent`；为空时不改变原请求。复用规划时将偏好一起比较，偏好不同不会复用旧模型输入。用户修订讲稿是本版本制作依据，原材料与原视频由父任务保留。
