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

模型接口返回临时服务错误（500、502–504、520–524）时，同一请求、同一模型最多额外重试两次，分别等待 2、4 秒。每次失败和重试都单独保留请求/响应与 `logs/http-retries.jsonl` 记录，并计入现有调用次数及保守费用上限；预算不足或连续失败就停止。401/403、402、429 和输入错误不采用这项重试；原有 402 显式备用模型策略保留，切换模型同样保留独立调用记录。

## 简洁信息动效

设置 `AUTOVIDEO_G2_VIDEO_FORM=information_motion`，可与 `AUTOVIDEO_G2_DURATION_MODE=content` 一起使用。`information_motion.py` 按内容生成场景，复用注册的 `title_card`、`concept_reveal`、`comparison` 与 `bar_chart`，另有只用于此形式的 `big_number`；标题、要点卡片、对比两侧、条形和大数字按旁白引用依次淡入、轻移。无需新增模型服务或布局依赖。

`big_number`：一个被旁白强调的关键数字，props 为 `title`（与 headline 一致）、`value`（数字加单位，至少含一个数字，如「25 倍」）、`label`、可选 `note`；cue 目标依次是 `title`、`line:0`（value）、`line:1`（label）、`line:2`（note）。`bar_chart`：2 到 6 个同单位数字的并列条形，沿用已注册的 `categories` / `values` / `unit` / `baseline_zero`，cue 目标是 `bar:0`…；图的标题是场景 headline，随 `bar:0` 出现。两者的数字都必须原样取自旁白或素材（本地校验 `INFORMATION_NUMBERS`，不换算、不估算；「半」读作 0.5），屏上文字容量按各自的面板检查。

此形式采用浅色排版，不能组合 Draw Things 背景；旧 `knowledge_cards` 与历史背景修订仍保留兼容。分镜必须有原文引文、完整有序旁白覆盖与每个可见元素的有效 cue。固定画面文字容量检查失败时，模型需缩短屏上措辞或拆成更多场景，旁白不因此截断。

分镜规划只把每段旁白的 `text` 发给模型，不再附带该段的 `source_quotes`。旁白是改写后的文本，与所引素材原句常有细微差别（例如素材「并在 Gemini 应用中上线…」对旁白「Gemini 应用同时上线…」）；同时给出两者会让模型把素材原句抄进 `cue.quote`，而 cue 必须落在该段旁白内，于是被本地校验拒绝。`cue.quote` 的校验失败信息会指出场景、目标、错误引文、疑似来源与该段旁白全文，重试反馈才有可修正的依据。

`price_change`（只用于信息动效，登记在扩展注册表）：旧到新的价格/数值变化，画面主角是「5 美元 → 4 美元」。props 为 `title`（与 headline 一致）、可选 `unit`（计量口径，如「每百万 token」）、`rows`（1–2 行，每行 `label`、`before`、`after`、可选 `change` 与 `direction`）；`change` 只写数字和百分号（如「20%」），正负号和箭头由渲染器按 `direction` 画，模型不写。cue 目标是 `title`、`line:0`、`line:1`（每行一个）。`before`/`after`/`change` 里的数字同样要通过 `INFORMATION_NUMBERS`（出自旁白；「两成」读作 20），并检查 `direction` 与新旧值一致。`source_card`：片尾来源卡，模型不能规划，只由请求的 `sources` 在本地生成并在编译时校验逐字一致（见 `UPSTREAM_INTEGRATION.md`「来源卡」）；它是屏上文字核对唯一的豁免，且豁免是单向的。

短视频字号：讲稿全文 ≤ 560 字时 IR 带 `presentation.density: "large"`，渲染器把信息动效的文字放大到 1.3 倍（`Root.tsx` 的 `S()` 与 `information_motion.scaled()` 取整一致），`validate_text_fit` 按同一尺寸检查，每屏容量因此变小。

原始 MVP schema 不变；运行时通过 `presentation: {"preset": "information_motion"}` 明确启用 `minimal_light` 主题及五个组件（`big_number` 登记在扩展注册表 `schemas/component-registry-extensions.json`，由 `g1/video_ir.load_registry()` 与冻结的 v0.2 注册表合并；冻结文件的哈希不变，所以 big_number 的 cue 目标沿用已有的 `title` 与 `line:N` 名称），只支持 reveal 动作，仍校验资产、目标和所属短语范围。场景数量随内容决定。`information-plan.json` 与 `information-timing.json` 保存来源及出现时机；短语内时机是字符位置估算，不声明词级对齐。

## 输入准备与自动选型

`AUTOVIDEO_G2_VIDEO_FORM=auto` 在出片前调用 `input_preparation.py`，复用现有 DeepSeek 结构化生成和本地校验，选择 `process_diagram` 或 `information_motion`，然后进入原生成链路。手动指定这两种形式时仍整理输入，但保留用户选择；旧 `knowledge_cards` 路径保持兼容。

输入准备不重写原文句子：每个来源单元有保留/排序或明确排除记录，逐字重复必须引用实际保留的相同单元，问题引用必须存在于原文。通过后的 `PREPARED_SOURCE` 用于纲要、讲稿及分镜，Video IR 仍保留原始输入。模型分类和噪声判断并非语义正确性的证明，原文、整理结果及实际请求都保留可核对。

## 用户修订讲稿

Web 修订任务由 worker 设置 `AUTOVIDEO_G2_LOCK_NARRATION=1`，`authored_script.py` 保留用户文字与标点，仅将长句分成字幕可容纳的短语；跳过模型讲稿重写和输入去重。仍使用真实配音、经过校验的分镜规划及媒体 QA，不执行自由生成代码。`authored-narration.json` 保存作者与文本哈希。

worker 通过受控输入 JSON 向模型阶段传递可选 `creative_intent`；为空时不改变原请求。复用规划时将偏好一起比较，偏好不同不会复用旧模型输入。用户修订讲稿是本版本制作依据，原材料与原视频由父任务保留。

## 包装与辅助素材组件库（2026-10-23）

新任务的片头、片尾、角标、来源卡和提示音统一由 `g2/packaging_library/` 注册与解析，省略配置继承 `common`，可用 `packaging` 按配方/任务覆盖或显式关闭。来源卡现在是完整正文之后的独立片段，支持流程图，原有叙述绑定来源卡仅保留历史兼容。CLI、API 和所有修订入口共用保存的配置快照。详见 [../g3/PACKAGING_LIBRARY.md](../g3/PACKAGING_LIBRARY.md)。

## 出片耗时优化（2026-10-23）

内容模式的讲稿已经定稿，配音不再改写文字，因此配音与分镜现在同时执行；两者都完成且通过校验后，才编译实测时间线。指定时长模式仍先完成配音和时长拟合，再按最终讲稿规划分镜。模型请求内容、重试次数和费用上限保持原规则，只有分镜线程会调用模型。

macOS 的简洁信息动效采用实测更快的单路渲染：同一份 35.83 秒 IR 的 1/2/4/6 路渲染分别耗时 68.25/79.87/100.59/106.72 秒。其他形式和平台保留两路上限；低 CPU 或内存机器会下调。分辨率 1920×1080、30 fps、H.264 CRF 18、AAC 和全部媒体检查保持一致。

每次任务新增 `artifacts/generation-schedule.json`（阶段重叠和实际耗时）及 `artifacts/render-resources.json`（实际资源配置和画质参数）。CLI 可用 `AUTOVIDEO_G2_PARALLEL_PLANNING=0` 恢复顺序执行，或用 `AUTOVIDEO_G2_RENDER_CONCURRENCY=2` 指定渲染并发；默认值为按已验证形式选取的 `auto`。无效并发配置明确失败，不自动重试。

测量方法、历史耗时、实片和本地回放验收见 [`generation-speed-20261023`](../g3/acceptance/generation-speed-20261023/README.md)。

## 创作形式第二阶段（2026-10-23）

`AUTOVIDEO_G2_VIDEO_FORM` 新增 `kinetic_typography`、`image_slideshow`、`chat_dialogue`。素材整理/逐字讲稿、实际配音、分章、字幕、包装和 QA 复用原链路，由 `creative_forms.py` 把最终讲稿编译为对应已注册组件；图片只接受冻结且哈希匹配的资产，或来自原本机 Draw Things API 的校验输出。形式与配方入口、图片请求和版本边界见 [CREATIVE_FORMS.md](../g3/CREATIVE_FORMS.md)。

## 背景音乐第三阶段（2026-10-23）

`music_library/` 管理注册素材、配置范围和冻结哈希；`music_mix.py` 在原正文／包装验收之后独立混音，复制原视频流，保留未混音视频和分轨。`music-revision` 复用父版本已验证的原视频和旁白；换曲始终从未混音文件开始。标准 Remotion 与快速 FFmpeg 卡片共用该阶段。启用音乐必须有任务目录内的冻结 `asset.wav` 及记录；不直接使用远程 URL 或任意路径。主要参数、输出和 QA 标准见 [MUSIC_MIX.md](../g3/MUSIC_MIX.md)。

## AI 音乐第四阶段（2026-10-23）

`music_engine/` 提供独立、离线的 ACE-Step MPS 单次推理适配，校验固定源码／权重并创建任务私有检查点视图；不运行参考工程旧试验入口。Web 音乐任务与视频共享单 worker 排队，生成成功后通过既有音乐导入校验入库，后续视频继续使用已冻结素材。推理参数、模型路径、生命周期和验证边界见 [MUSIC_GENERATION.md](../g3/MUSIC_GENERATION.md)。

## 重点画面与共享短句字幕验收（2026-10-24）

`information_cards` 将主画面与原旁白分开：按原稿段落分组，规划器只返回原文候选短语编号；程序解析为逐字引用并去除包含关系的重复重点，普通页最多三点，概览列出原章节标题。`card-editorial.json` 保存原文、引用、完整归属及限制句。Video IR 仍要求旁白覆盖一次、按序、位于所属场景，画面重点必须出自所属旁白或原概览标题；其他创作形式保留逐句卡片规则。

字幕通过本机 Qwen3 ForcedAligner 对已有 PCM 和原文做对齐，再按语义标点拆成短句，不重新合成或按字数估算时间。`caption-alignment.json` 保存模型来源、权重和音轨哈希、逐词时间及逐字覆盖区间；SRT、烧录 ASS 与该计划一致。字幕中点和空白间隔对照独立渲染的预期字形，黑块、错误文字和遗漏会失败。英文单词、数字单位保持完整，浅色角标自动使用深色字。

短句接近上限时为尾句保留至少一段可读文字，必要时优先使用较早的标点，避免把「交易」拆成「交」和单独 179ms 的「易。」。逐字覆盖、对齐来源、时间范围与最短 200ms 的检查继续保留。数字来源校验识别紧邻中文的写法（如「超过61.5万平方公里」），同时排除 ASCII 标识符内的数字后缀；未在旁白/素材出现的数值仍然拒绝。

快速卡片、信息动效与流程图的新片及修订均需要 `g3/data/voice-runtime/bin/python` 中已有的 MLX Audio，以及固定权重目录 `g3/data/voice-models/qwen3-forced-aligner-0.6b-8bit/`。当前配置为 `mlx-community/Qwen3-ForcedAligner-0.6B-8bit`，revision `0e1a68e91d815300c7c9754b2a7639378b23db15`，`model.safetensors` SHA-256 为 `be19ef8ac4326d032e7673342930b14c2df30bd68c1632493b0f563e30829f91`。仅下载 JSON、词表和 safetensors，不下载或执行远程 Python；运行时 `HF_HUB_OFFLINE=1`。目录内 `autovideo-model-source.json` 含 `repo`、`revision` 和 `code` 来源说明。缺少运行时或模型时返回 `CAPTION_ALIGNER_NOT_CONFIGURED`，不会退回估算字幕。接口依据 [MLX Audio 官方说明](https://github.com/Blaizzy/mlx-audio/blob/main/mlx_audio/stt/models/qwen3_asr/README.md)。

信息动效与流程图通过同一 `caption-alignment.json` 向可信 Remotion 渲染器提供独立短句轨道，逐条校验原文覆盖与 SRT 一致性；不改变旁白、场景触发或 Video IR 合约。历史输出未保存短句轨道时保持原字幕。快速卡片另做上述 ASS 字形像素验收；普通动画检查实际渲染轨道并抽查真实解码画面，不冒充同一种像素验收。其他创意形式不在本轮实片验收范围。

换声音或倍速后重新对齐短字幕；画面修订保留其他页面与原限制句。共同包装和配乐阶段沿用。本轮同一定稿 31 张逐句卡片改为 12 张重点卡，31 条字幕改为 53 条；原 PCM 与最终成片解码音轨均与旧版完全相同，真实出片和 29 份回传文件已验证。完整等待 166 秒，其中重点规划 87.47 秒、字幕对齐约 4.7 秒；这轮以表达质量为目标，不作为纯提速或批量吞吐测量。证据见 [verification.json](../g3/acceptance/card-quality-20261023/verification.json)。

## 观众文案与限定标签（2026-10-23）

新快速卡片把来源与测试条件附在对应重点旁，不再自动复制整段限制句。重点和标签逐字对应原文证据，否定、推测、范围和测试场景不得在提炼时丢失；数字前的指标标题已在同页显示时不重复。来源名使用中性标签，连接句保留主体与列表上下文。

含“材料未提供”等明确内部研究备注的原稿会在分镜模型调用前报 `CARD_EDITORIAL_SOURCE_GAP`，需先修订上游稿件。历史 `card-editorial` v1 的声音/速度修订仍保留旧限定句布局，新的 v2 记录及新提炼页使用逐项短标签。验收见 `g3/acceptance/audience-quality-20261023/`。
