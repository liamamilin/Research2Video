# AutoVideo 上游工具对接指南

核对日期：2026-10-17。适用版本：当前 AutoVideo 本地版 0.1。

面向“发现主题 → 生成内容”的上游工具开发者。本文描述**当前已经实现的 HTTP 接口**；待开发能力单独列出，不应据此提前调用。

## 1. 接入方式与职责

```text
上游：发现主题、生成内容、保留内容来源
  ↓ 提交素材正文，或已审定的分章讲稿；附可选制作偏好
AutoVideo：（素材模式）整理素材、组织讲稿；选择表现形式、配音、字幕、动画、基础检查
  ↓ 返回任务状态与文件地址
上游：跟踪任务、获取 MP4 / 字幕 / 讲稿、继续后续处理
```

网页与 API 使用同一任务系统。通过 API 创建的任务会出现在作品库，可以打开网页继续查看与修订。

- **当前推荐入口：HTTP API。** 不需要打开网页才能提交任务。
- 内部存在 `g2/run_g2.py` 命令行执行脚本，但没有正式的 `autovideo create/wait/download` CLI；对接方使用 HTTP 接口。
- 新建任务有两种输入（第 3 节）：`input_mode: "material"`（默认）提交**内容材料**，AutoVideo 组织和改写为讲稿；`input_mode: "narration"` 提交**已审定的分章讲稿**，AutoVideo 逐字配音，只规划画面。
- 上游负责主题选择、内容事实与出处。AutoVideo 的基础检查不构成外部事实核验。

## 2. 服务地址与启动条件

当前本机地址：

```text
API 基址：http://127.0.0.1:8011
网页：   http://127.0.0.1:8011/#new
接口文档：http://127.0.0.1:8011/docs
OpenAPI：http://127.0.0.1:8011/openapi.json
```

先按**同一台机器上的进程**对接。上游运行在远程机器或容器时，其 `127.0.0.1` 不一定指向 AutoVideo；这需要另行约定连接方式。当前服务没有远程鉴权方案，且限制访问 Host；不要直接把本地地址替换成公网地址便视为远程接入完成。

AutoVideo 的维护者负责启动本地服务、配置 DeepSeek 密钥和本地依赖。上游不需要持有或传入 DeepSeek 密钥。正文会由 AutoVideo 发送给 DeepSeek；文档原件、语音合成及视频渲染在本机处理。

启动前检查：

| 请求 | 判断 |
| --- | --- |
| `GET /api/health` | `status == "ok"` 表示应用可响应；`worker_primary == true` 表示当前实例持有制作 worker |
| `GET /api/config/status` | `ready == true` 表示生成依赖预检通过；`checks` / `blockers` 提供诊断信息 |
| `GET /api/voices` | 查看 `items` 中的声音及 `available`，使用可用声音的 `id` |

健康检查不等于生成配置就绪；配置预检也不保证后续模型请求一定成功。当前只有一个制作 worker，任务串行处理，排队属于正常状态。

## 3. 创建视频任务

### 请求

`POST /api/jobs`，`Content-Type: application/json`。

以下是可直接作为联调材料的有效示例；实际使用时替换标题和正文：

```json
{
  "title": "怎样记录清楚一次会议",
  "source_text": "会议记录的目的是让未参会的同事也能理解结果。先交代必要背景，再区分已经确定的决定、需要执行的行动和仍待确认的问题。行动要写清负责人和下一步，未确定的日期应明确标注为待定。",
  "video_form": "auto",
  "duration_mode": "content",
  "voice_id": "zf_xiaoxiao",
  "video_speed": 1,
  "creative_intent": {
    "audience": "团队同事",
    "purpose": "知识讲解",
    "tone": "简洁自然",
    "focus": "区分决定、行动和待确认信息"
  }
}
```

将以上 JSON 保存为 `autovideo-request.json`，执行：

```sh
AV_BASE_URL='http://127.0.0.1:8011'
curl --fail --silent --show-error \
  -H 'Content-Type: application/json' \
  --data-binary @autovideo-request.json \
  "$AV_BASE_URL/api/jobs"
```

### 按原稿配音（narration 模式）

讲稿已在上游审定、要求逐字配音时，传 `input_mode: "narration"` 与 `chapters`，**不传** `source_text`：

```json
{
  "title": "crewai eval --models 的每次运行成本与延迟",
  "input_mode": "narration",
  "chapters": [
    {
      "title": "开场：你要的那个数字，其实已经内置了",
      "paragraphs": [
        "你在给 crewAI 的 eval --models 做模型横评，为的是拿数字去申请预算。",
        "好消息是：从 crewAI 1.15.27 起，crewai eval --models 自带每次运行的成本与耗时报告。"
      ]
    },
    {
      "title": "三个必须实测的口径：时间、计价、格式",
      "paragraphs": [
        "LiteLLM 在 completion 上返回 response_cost，再用 cost_per_token 和 completion_cost 对照模型成本表折算成美元。"
      ]
    }
  ],
  "video_form": "auto",
  "duration_mode": "content",
  "voice_id": "zf_xiaoxiao"
}
```

此模式的约定：

- **配音与字幕逐字采用讲稿。** AutoVideo 只把段落切成适合字幕的短句（按句末标点，过长句在逗号处、其次在空格处断开），并把连续空白规整为一个空格；不改写措辞、不加过渡句、不删字。
- **强制校验。** 分句后、生成 Video IR 后各核对一次：配音分段、Video IR 旁白、`captions.srt` 和章节标题必须逐字还原讲稿。任何不一致都使任务以 `NARRATION_TEXT_MISMATCH` 失败（不可重试），`error_message` 指出章节、位置及讲稿与配音两侧文字；不会静默输出改动过的版本。
- **章节标题取自 `chapters[].title`**，原样用于画面章节标签与 `chapters.json`；标题本身不朗读。
- **画面仍自动规划。** `video_form: "auto"` 时只让模型为整片选择 `information_motion` 或 `process_diagram`（不读改、不剔除讲稿内容）；场景、卡片、流程图与动画节拍按每章讲稿自动生成。
- 时长由讲稿和实际配音决定，因此必须传 `duration_mode: "content"`。
- **来源卡（可选 `sources`）。** `narration` 模式可附 `sources: [{title, url, date}]`，片尾出现一张「来源」卡，每条显示标题、域名和发布日期；不传则没有来源卡。约定：
  - 1–3 条；`title` 1–40 字符、单行；`url` 必须是带域名的 `http`/`https` 网址（不含空白，≤300 字符），画面只显示去掉 `www.` 的域名；`date` 为存在的 `YYYY-MM-DD`；不接受其他字段。只用于 `narration` 模式，且 `video_form` 必须是 `information_motion` 或 `auto`（`auto` 选成流程图时不出现来源卡）。
  - **来源文字不是旁白，也不进入旁白。** 它既不朗读，也不参与「画面文字贴口播」的核对：来源卡的文字由请求里的 `sources` 在本地生成（模型不写），生成后必须与请求数据逐字一致，否则以 `INFORMATION_SOURCE_TEXT` 失败。这条豁免只覆盖来源卡本身：来源标题里的数字和术语**不会**被加入旁白的核对依据，其他场景的数字与命令、版本号等仍必须出自旁白。
  - 来源卡占用片尾一句旁白的画面（因为场景必须逐段覆盖旁白）：最后一个场景有两句以上且最后一句没有被它的节拍引用时，取走最后一句；最后一个场景只有一句且是文字类场景（标题、要点、对比）时，由来源卡替换；数据类场景（数字、价格、图表）不会被替换，改为从章内较前、能让出一句的场景取。都不行时不出来源卡，原因写入 `artifacts/source-card.json`（任务仍成功）。
  - 来源标题或域名过长、条数过多导致排不下时，任务以 `INFORMATION_SOURCE_CARD` 失败，`error_message` 说明原因；缩短标题后重新提交。
  - 修订任务（讲稿修订、场景修订）不继承 `sources`；来源卡场景不能被重排，其文字不能编辑。
- **短视频大字号。** 讲稿全文 ≤ 560 字（约 60–120 秒）时，信息动效整片的文字放大到 1.3 倍（IR 里 `presentation.density: "large"`），每屏容量相应变小，规划器会写得更短。更长的视频沿用原字号。
- 任务保存的原稿 `source.txt` 由章节生成：章内段落以换行连接、章与章之间空一行；`source_sha256` 对应这份文本。

### 包装套件（可选 `package`：片头与片尾）

一条视频 = 内容部分 + 非内容部分。非内容部分（片头 stinger、片尾 end_card）**不进 Video IR**：IR v0.2 要求每个场景至少有一个旁白段，音频 QA 要求成片音轨与配音的相关系数不低于 0.98。内容视频照常渲染并通过原有的媒体 QA、原话校验（这些一律不动），然后由独立的 `PACKAGE` 阶段把片头、片尾接在它前后，成片就是打包后的视频。请求里没有 `package` 时行为与以前完全一样（`PACKAGE` 阶段显示「已跳过」，不产生新文件）。

```json
"package": {
  "accent": "#1f7a4d",
  "badge": "快讯",
  "intro": {"template": "stinger", "title": "快讯", "subtitle": "2026-10-19", "seconds": 1.5, "sound": "synth_sting"},
  "outro": {"template": "end_card", "title": "快讯", "lines": ["来源见简介"], "seconds": 3, "sound": "synth_sting"}
}
```

- **角标 `badge`（可选）**：短文字，≤ 12 字单行，写栏目名（如「快讯」）。带 `package` 的任务，内容画面左上角的角标改由套件提供：有 `badge` 时显示「色点 + badge 文字」，色点和文字用 `accent`；没传 `badge` 时只显示 `accent` 色点，不写产品名和「知识讲解」。**没有 `package` 的任务保持原样**（显示「AUTOVIDEO 知识讲解」）。目前只改了简洁信息动效（`information_motion`）版式的角标；旧版式和流程图解版式（左上角「AUTOVIDEO / G1 · Video IR 0.2」等）不变。角标作为渲染参数传给渲染器，不进 Video IR。\n- **字段**：`accent` 必填，`#rrggbb`；`intro`、`outro` 各自可选，**至少给一个**（例如知识点栏目只要片尾）。模板名只认 `stinger`（片头）和 `end_card`（片尾）；`seconds` 在 0.5–5 之间，按 30 fps 取整到帧；`title` ≤ 12 字；`subtitle`（仅片头）≤ 24 字；`lines`（仅片尾）最多 3 行、每行 ≤ 30 字；`sound` 可选，是套件里提示音的名字（小写字母、数字、下划线，必须在套件里存在，目前只有 `synth_sting`）。多余字段、错位字段（如片尾的 `subtitle`）一律以 422 拒绝，任务不会创建。不带 `sound` 时片头片尾是静音的。
- **文字只来自请求**：模板用固定版式（片头：主色底、白字标题加副标题；片尾：浅底、主色竖条、标题与说明行）逐帧绘制，不经过模型，文字过长时只缩字号、不截断。日期等动态内容由上游写进 `subtitle`，AutoVideo 不替换 `{date}`。
- **提示音**：套件目录 `g2/package_kit/` 里的声音是带来源和授权的资产（`kit.json` 记录 `source`、`license`、`author`、`sha256`）。第一版 `synth_sting` 由确定性脚本 `synth_sounds.py` 合成（原创，无授权问题，音质有限）。换成自己的音乐文件只需要换 `sounds/` 里的文件并更新 `kit.json` 的元数据和 sha256，流程不变；打包时校验 sha256，文件与元数据不符会拒绝。
- **响度**：提示音按内容视频**实际播放**的人声有效电平（50 ms 窗口，高于 −45 dBFS 才计入）自动降低 6 dB 混入，并在片头/片尾片段内裁短、淡出，不会渗进内容。成片检查要求提示音至少比人声低 3 dB 且不削波，否则以 `PACKAGE_SEAM_LOUDNESS` 失败。
- **产物**：`video.mp4` 是**打包后的成片**；`content.mp4` 是内容视频本身（未打包，也是原话校验、内容 QA 针对的那个文件）。时间线：`captions.srt`、`chapters.json` 已加上片头长度，与 `video.mp4` 对齐；内容时间线的版本另存为 `content-captions.srt`、`content-chapters.json`。`package.json` 记录片头/内容/片尾的帧数与偏移（`offset_ms` 是片头时长）、提示音元数据与每项检查结果；`package-qa.json` 是检查报告；`package-contact-sheet.jpg` 是接触表。上游发布时间戳时用 `chapters.json`（已含偏移）。网页播放器播放打包后的成片，章节和场景跳转自动加上片头偏移。
- **打包后的检查（全部通过才算完成）**：完整解码；帧数等于片头 + 内容 + 片尾，时长误差不超过一帧；尺寸、帧率、采样率、声道与内容视频一致且全片没有中途变化；内容部分的音轨相关系数按片头长度错开后重算，仍须 ≥ 0.98；接缝处提示音响度。失败码：`PACKAGE_DECODE_FAILED`、`PACKAGE_DURATION_MISMATCH`、`PACKAGE_FORMAT_MISMATCH`、`PACKAGE_AUDIO_MISMATCH`、`PACKAGE_SEAM_LOUDNESS`；另有 `PACKAGE_CONFIG_INVALID`、`PACKAGE_CONTENT_MISSING`、`PACKAGE_RENDER_FAILED`、`PACKAGE_COMMAND_FAILED`、`PACKAGE_UNVERIFIED`（请求了打包但没有通过的检查报告，绝不发布未验证的成片）。`PACKAGE_CONFIG_INVALID`、`PACKAGE_CONTENT_MISSING`、`PACKAGE_FORMAT_MISMATCH`、`PACKAGE_UNVERIFIED` 的 `retryable` 为 `false`，其余可重试。阶段名 `PACKAGE`（页面显示「加片头片尾」），排在 `QUALITY_CHECK` 之后；历史任务没有这一阶段。
- **重试与修订**：重试沿用保存的请求，会重新打包。讲稿修订、原稿修订、画面修订、声音与速度修订、场景背景重生成都**继承父任务的 `package`**，并在新内容视频上重新打包（章节、字幕按新内容重算偏移）；这一点与 `sources` 不同。场景预览取自 `content.mp4`，不受片头影响。
- 提示音只加在片头、片尾；**不做背景音乐垫底**，也不做竖屏。

### 响应

成功返回 **HTTP 202**，表示任务已接受，尚未完成。以下编号与时间为示例：

```json
{
  "job_id": "00000000-0000-4000-8000-000000000001",
  "status": "QUEUED",
  "created_at": "2026-10-15T12:00:00.000+00:00",
  "status_url": "/api/jobs/00000000-0000-4000-8000-000000000001"
}
```

**收到响应后立即保存 `job_id`，再开始等待。** 每次重新 `POST /api/jobs` 都会创建新任务。

### 字段约定

| 字段 | 当前约定 |
| --- | --- |
| `input_mode` | 可选，默认 `material`。`material`：`source_text` 是素材，AutoVideo 组织讲稿；`narration`：`chapters` 是定稿讲稿，逐字配音 |
| `source_text` | `material` 模式必填；去除首尾空白后至少 50 个字符，不允许 NUL。保持事实、条件、数字、例子及必要说明清楚完整。`narration` 模式不得传入 |
| `chapters` | `narration` 模式必填，`material` 模式不得传入。非空数组，按顺序；每项只含 `title`（1–32 字符、单行）与 `paragraphs`（非空字符串数组，每段去除首尾空白后不能为空，不允许 NUL）。不接受 `visual_cues` 等其他字段 |
| `sources` | 可选，仅 `narration` 模式：片尾来源卡，1–3 项，每项 `title`（1–40 字符、单行）、`url`（带域名的 http/https）、`date`（`YYYY-MM-DD`）。详见上文「来源卡」 |
| `package` | 可选：片头（`stinger`）和片尾（`end_card`）加主色与提示音，出片后单独打包；任何模式都可用。详见上文「包装套件」 |
| `title` | 可选；传入时长度 2–80。省略或为 `null` 时，服务端从正文截取任务标题；不要传空字符串 |
| `video_form` | 推荐显式传 `auto`；可指定 `process_diagram` 或 `information_motion` |
| `duration_mode` | 推荐显式传 `content`，按完整内容和实际配音组织章节与时长；`narration` 模式必须为 `content` |
| `voice_id` | 可选，默认 `zf_xiaoxiao`；以 `/api/voices` 返回的可用 ID 为准 |
| `video_speed` | 可选，默认 `1`；允许 `0.5`、`1`、`1.5`、`2`，会同步影响导出视频的音频、字幕和动画 |
| `creative_intent` | 可省略或传 `{}`；支持 `audience`、`purpose`、`tone`、`focus` 四个可选字符串，允许自定义文字 |
| `target_duration_sec` | 仅指定时长模式使用，整数 10–90，默认 60；`content` 模式可省略。即使在 `content` 模式传入，也仍需通过 10–90 的字段校验 |
| `input_document_id` | 可选，来自文档上传接口；直接传正文时省略；`narration` 模式不得传入 |
| `use_draw_things` | 当前两种主形式及自动选择使用默认 `false`；不需要传入 |
| `theme` | 可省略；当前只接受兼容值 `minimal_dark`。主形式的实际视觉样式由所选形式决定 |

**必须注意默认值兼容：** API 省略 `video_form` 时走历史 `knowledge_cards`；省略 `duration_mode` 时走 `fixed`。上游要获得当前网页的默认行为，应明确传入 `auto` 和 `content`。

`content` 模式没有套用指定时长模式的 2,000 字符 / 90 秒上限，但仍受模型上下文、本机资源和执行时限约束，不承诺任意长度。`fixed` 模式的正文最多 2,000 字符。省略 `title` 时，两种模式都从正文开头截取任务标题；`narration` 模式建议显式传入视频标题。

### 画面中的技术文字

命令、参数、代码、文件名和版本号在字幕和画面中按原样显示：`--models` 的两个连字符之间留有可见间隔（不再被看成 `–models`），`v1.15.24`、`eval_crew.py`、`comparison['models']` 等英文与符号片段在能放下时不在中间换行。反引号原样显示在字幕中，但不朗读。模型生成的卡片、流程图标签若改动这类片段（如把 `--models` 写成 `–models` 或 `-models`，把 `1.15.27` 写成 `v1.15.27`），会被本地校验拒绝并要求重写，两种输入模式都适用。

请求禁止未知字段。当前不能额外传 `content_id`、`input_type`、`callback_url`、`metadata` 等未实现字段；会返回 422。制作要求应放在 `creative_intent`，研究来源与上游业务元数据由上游保存。

## 4. 查询进度与等待结果

`GET /api/jobs/{job_id}`，成功返回 HTTP 200。

主要字段：

| 字段 | 用途 |
| --- | --- |
| `status` | 整个任务的当前状态，是判断结束的依据 |
| `active_stage` | 当前执行阶段；用于进度描述，不是百分比 |
| `attempt_count` | 此任务的尝试次数 |
| `error_code` / `error_message` | 失败原因；成功或无错误时可为 `null` |
| `retryable` | 当前失败是否允许通过重试接口重新执行 |
| `cancel_requested` | 已请求取消，尚可能在停止进程 |
| `resolved_video_form` | 自动选择最终采用的形式；分析完成前可为 `null` |
| `input_analysis` | 素材整理、选型理由和待确认问题；初期可为 `null` |
| `source_sha256` | 服务端保存正文的哈希；服务端会去除首尾空白。`narration` 模式为章节生成的原稿文本的哈希 |
| `input_mode` | `material` 或 `narration`；历史任务为 `material` |
| `parent_job_id` | 修订任务对应的父版本，没有时为 `null` |
| `attempts` | 历次尝试及阶段记录，**按尝试编号倒序，最新项在第 0 项** |

状态处理：

| `status` | 上游行为 |
| --- | --- |
| `QUEUED` | 已入队，继续等待 |
| `RUNNING` | 正在制作，继续等待 |
| `COMPLETED` | 本次已完成，可以获取文件列表 |
| `FAILED` | 本次已结束，读取错误并按第 6 节处理 |
| `CANCELLED` | 已取消，停止等待，不当作成功 |

阶段可能因复用而跳过。不要要求所有阶段均为 `SUCCEEDED` 才认为完成，也不要仅凭某一阶段结束或进程退出推断成功。

当前没有完成回调或 Webhook，使用轮询即可。可从约 5 秒一次开始，间隔由上游调整。一次 HTTP 请求的超时与整项视频制作的等待时限分别配置；长内容生成可能持续数十分钟，不要把短片时长当成任务执行超时。

上游中断、刷新或重启后，使用已经保存的 `job_id` 继续查询，不重新创建任务。上游自身等待超时也不代表 AutoVideo 已取消制作；需要停止时调用取消接口。

人工查看地址：`http://127.0.0.1:8011/#job/{job_id}`。

## 5. 获取输出文件

完成后请求 `GET /api/jobs/{job_id}/artifacts`。

响应形状如下；文件大小、哈希和编号仅为示意：

```json
{
  "items": [
    {
      "name": "video.mp4",
      "content_type": "video/mp4",
      "bytes": 123456,
      "sha256": "示意值，实际为64位十六进制SHA-256",
      "download_url": "/api/jobs/00000000-0000-4000-8000-000000000001/artifacts/video.mp4"
    }
  ]
}
```

常用文件：

| `name` | 用途 |
| --- | --- |
| `video.mp4` | 最终 H.264 / AAC MP4；带 `package` 时是打包后的成片 |
| `content.mp4` | 仅带 `package`：内容视频本身（无片头片尾） |
| `captions.srt` | 中文字幕；带 `package` 时已加片头偏移，与 `video.mp4` 对齐（内容时间线版本为 `content-captions.srt`） |
| `package.json` / `package-qa.json` / `package-contact-sheet.jpg` | 仅带 `package`：打包记录（含 `offset_ms`）、检查报告、接触表 |
| `narration.json` | 最终讲稿 JSON，正文可从 `segments[].text` 读取；`chapters[]` 含章节标题与所含段落 |
| `narration-check.json` | `narration` 模式与讲稿修订：逐字校验结果（`status: "PASS"`、章节数、段数、已核对的 Video IR / 字幕 / 章节标题） |
| `authored-narration.json` | `narration` 模式与讲稿修订：讲稿来源、`model_rewrite: false` 及分句后的首次逐字校验 |
| `qa.json` | 本次基础媒体检查记录 |
| `chapters.json` | 内容模式的章节和时间信息；带 `package` 时已加片头偏移（内容时间线版本为 `content-chapters.json`） |
| `input-brief.json` | 输入整理、来源和形式选择记录 |

以 `items` 实际返回为准，部分辅助文件不适用于所有任务。未完成时文件列表为空；空列表不能被解释为成功。

`download_url` 是相对地址，应与 API 基址拼接后下载，不应尝试读取 AutoVideo 内部工作目录。建议下载至临时文件，完成后核对 `bytes` / SHA-256，再作为上游的最终产物保存。

示例：将 `AV_JOB_ID` 换成真实返回值。

```sh
AV_JOB_ID='00000000-0000-4000-8000-000000000001'
curl --fail --silent --show-error "$AV_BASE_URL/api/jobs/$AV_JOB_ID"
curl --fail --silent --show-error "$AV_BASE_URL/api/jobs/$AV_JOB_ID/artifacts"
curl --fail --silent --show-error \
  "$AV_BASE_URL/api/jobs/$AV_JOB_ID/artifacts/video.mp4" \
  --output autovideo-result.mp4
```

下载地址对应此任务当前已完成的尝试。上游应保存实际下载文件、`job_id`、`attempt_count` 和文件哈希；若后续任务重试或被人工修订，不要只靠一个下载 URL 标识历史成片。

## 6. 错误、重试与取消

区分**提交请求失败**与**已接受任务执行失败**。

### HTTP 请求失败

常见业务错误形状：

```json
{
  "detail": {
    "code": "CONFIG_NOT_READY",
    "message": "服务配置未完成；请检查本地生成依赖。"
  }
}
```

Pydantic 参数校验的 422 响应则可能是 `detail` 数组，包含 `loc`、`msg` 等字段。上游应兼容两种错误形状，并保留 HTTP 状态与错误响应体。

| HTTP 状态 | 常见含义与处理 |
| --- | --- |
| 422 | 字段、枚举、长度或组合错误；修正请求后再提交 |
| 503 | 本地依赖、密钥或声音模型未就绪；处理配置后再尝试 |
| 404 | 任务或文件不存在；核对编号及请求地址 |
| 409 | 当前状态不允许操作、关联文件或制品完整性异常；读取具体错误码 |
| 500 | 创建任务工作区等内部操作失败；保留信息并排查 |

### 任务执行失败

- `FAILED` 且 `error_code == "INPUT_REQUIRES_CLARIFICATION"`：素材关键事实需要确认。读取 `input_analysis.issues`，修正素材后创建修订版；**不要重复执行同一份冲突材料**。网页显示“待补充”，但原始任务 `status` 仍是 `FAILED`。
- `FAILED` 且 `error_code == "NARRATION_TEXT_MISMATCH"`：`narration` 模式下配音或字幕未能逐字还原讲稿，`retryable` 为 `false`。保留 `error_message` 并反馈 AutoVideo 维护者；不要改用素材模式提交同一份定稿来绕过。
- 其他 `FAILED`：读取 `error_code` / `error_message`。仅在 `retryable == true` 且上游重试策略允许时，调用 `POST /api/jobs/{job_id}/retry`，返回 HTTP 202。
- 重试沿用**同一个 `job_id`**，创建新的 attempt，旧尝试保留。重新查询状态，不另建重复任务。
- 配置和材料错误应先处理原因。`retryable` 是接口允许重试的信号，不是保证下一次成功，也不应触发无上限循环。

取消使用 `POST /api/jobs/{job_id}/cancel`，仅适用于 `QUEUED` / `RUNNING`。运行中的任务可能先返回 `cancel_requested=true`；继续查询，直到终态，不能假设请求返回即已停止。

## 7. 业务编号与防重复提交

当前没有服务端幂等键、`content_id` 字段或 `Idempotency-Key` 语义；即使发送同样正文，多次创建请求也会产生多个任务。

上游保存一份映射即可，存储方式由上游现有系统决定：

```text
上游内容编号 + 内容版本 + 制作设置
→ 提交正文及其哈希
→ AutoVideo job_id
→ 当前状态 / attempt_count
→ 输出文件与文件哈希
```

对同一内容版本和制作设置，应先查上游映射，有任务编号就恢复查询。上游的并发执行也应共享这份映射，避免两个执行器同时提交。

**创建请求超时或连接中断而未收到 `job_id` 时，结果可能未知。** 任务可能已经创建；不要直接无限重发 POST。上游应标记为“提交结果待核对”，可用下列查询辅助查找，再按提交时间、标题及 `source_sha256` 对账：

```text
GET /api/jobs?limit=50&offset=0
GET /api/jobs?limit=50&offset=50
GET /api/jobs?q=标题关键词
```

`q` 应进行 URL 编码，可按标题或任务编号查询。列表返回 `items` / `total` / `limit` / `offset`；`limit` 最大 50。对账只是恢复辅助手段，**当前不能保证创建请求恰好执行一次**。需要无歧义的自动恢复时，应先由 AutoVideo 补齐幂等提交协议，再更新本文。

## 8. 可选：上传文档与内容修订

### 上传本地文档

`POST /api/input-documents`，multipart 字段名为 `file`。支持 `.docx`、文字型 `.pdf`、`.txt`、`.md`，单文件最大 32 MiB。扫描件不支持 OCR。

响应含 `document_id`、`text`、`filename`、`warnings` 等字段。随后新建任务时传：

- `source_text`：提取正文，或上游修改后的正文。
- `input_document_id`：上传返回的编号。

上传本身不会创建视频任务；正文仍需满足新建接口规则。原始文档中的图片、复杂排版等不保证转成视频内容。已有正文的上游优先直接提交正文。

### 创建内容修订版

`POST /api/jobs/{job_id}/revisions`，要求父任务已为 `COMPLETED` / `FAILED` / `CANCELLED`。

```json
{
  "mode": "source",
  "text": "会议记录的目的是让未参会的同事也能理解结果。先交代必要背景，再区分已经确定的决定、需要执行的行动和仍待确认的问题。行动要写清负责人和下一步，未确定的日期应明确标注为待定。"
}
```

- `mode: "source"`：修改材料，重新组织讲稿与画面。
- `mode: "narration"`：采用修订讲稿原文配音，同样执行逐字校验；要求父任务已有可读取讲稿且属于两种主形式。修订讲稿是一段整体文字，生成单章，章节标题取任务标题；需要保留分章标题时，用 `narration` 模式新建任务。
- 可选传 `title` 和 `creative_intent`。省略偏好时继承，传 `{}` 时清空。
- 返回 HTTP 202 和**新的 `job_id`**、`parent_job_id`、`status_url`；父任务与旧成片保留。上游应跟踪新编号。

修订接口与重试接口不同：修订产生子任务，重试增加原任务的尝试次数。

## 9. 尚未实现的能力

| 能力 | 当前状态与对接决定 |
| --- | --- |
| 正式产品 CLI | **未封装。** 上游使用 HTTP；未来 CLI 可作为同一 API 的客户端 |
| 服务端幂等提交 / 上游业务编号 | **未实现。** 当前由上游记录映射，并对提交结果未知作显式处理 |
| 完成回调 / Webhook | **未实现。** 当前轮询 |
| 跨机器鉴权与远程分发 | **未实现。** 当前按同机接入 |

新建任务直接采用最终讲稿已实现（2026-10-17）：使用第 3 节的 `input_mode: "narration"` 与 `chapters`，复用讲稿修订的逐字配音机制，并增加分章标题、自动选型和强制逐字校验。把最终讲稿当作 `source_text` 以素材模式提交，仍会被组织和改写，不保证原文配音。

## 10. 上游实现与联调清单

建议先完成一条闭环：

1. 配置 API 基址，完成健康、依赖及声音检查。
2. 生成一份真实内容材料，显式传 `auto` / `content` 创建任务；已审定讲稿用 `input_mode: "narration"` 与 `chapters` 提交。
3. 收到 202 后保存 `job_id`；等待并区分成功、失败和取消。
4. 下载 MP4、字幕及所需讲稿，核对文件哈希，记录输出。
5. 使用任务链接在网页打开同一条视频，确认可继续修订。

再验证必要恢复行为：

- 上游进程中断后，能用原 `job_id` 恢复查询，而不是重复提交。
- 材料关键冲突能进入待补充处理，不出现自动重试循环。
- 参数错误能保留具体错误信息；取消与等待超时不被当作成功。
- 同一内容已有映射时复用原任务；提交结果未知时进入对账处理。
- `narration` 模式：下载 `narration.json` 后，将各段 `text` 与提交的段落比对（忽略空白差异），并检查 `narration-check.json` 的 `status` 为 `PASS`。

本文只确定现有接口和建议的对接行为，没有新增 CLI、幂等或回调。

实现核对来源：仓库 `g3/app.py`（HTTP 与请求校验）、`g3/store.py`（任务/版本/文件）、`g3/worker.py`（执行和错误）、`g2/authored_script.py`（按原稿配音与逐字校验）、`g3/README.md`（本地使用）。字段细节可结合运行实例的 OpenAPI 查阅。
