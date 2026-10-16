# AutoVideo 上游工具对接指南

核对日期：2026-10-15。适用版本：当前 AutoVideo 本地版 0.1。

面向“发现主题 → 生成内容”的上游工具开发者。本文描述**当前已经实现的 HTTP 接口**；待开发能力单独列出，不应据此提前调用。

## 1. 接入方式与职责

```text
上游：发现主题、生成内容、保留内容来源
  ↓ 提交正文与可选制作偏好
AutoVideo：整理素材、组织讲稿、选择表现形式、配音、字幕、动画、基础检查
  ↓ 返回任务状态与文件地址
上游：跟踪任务、获取 MP4 / 字幕 / 讲稿、继续后续处理
```

网页与 API 使用同一任务系统。通过 API 创建的任务会出现在作品库，可以打开网页继续查看与修订。

- **当前推荐入口：HTTP API。** 不需要打开网页才能提交任务。
- 内部存在 `g2/run_g2.py` 命令行执行脚本，但没有正式的 `autovideo create/wait/download` CLI；对接方使用 HTTP 接口。
- 当前新建任务接收的是**内容材料**，AutoVideo 会组织和改写为讲稿。要求逐字采用最终讲稿时，先看第 9 节。
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
| `source_text` | 必填字符串；去除首尾空白后至少 50 个字符，不允许 NUL。保持事实、条件、数字、例子及必要说明清楚完整 |
| `title` | 可选；传入时长度 2–80。省略或为 `null` 时，服务端从正文截取任务标题；不要传空字符串 |
| `video_form` | 推荐显式传 `auto`；可指定 `process_diagram` 或 `information_motion` |
| `duration_mode` | 推荐显式传 `content`，按完整内容和实际配音组织章节与时长 |
| `voice_id` | 可选，默认 `zf_xiaoxiao`；以 `/api/voices` 返回的可用 ID 为准 |
| `video_speed` | 可选，默认 `1`；允许 `0.5`、`1`、`1.5`、`2`，会同步影响导出视频的音频、字幕和动画 |
| `creative_intent` | 可省略或传 `{}`；支持 `audience`、`purpose`、`tone`、`focus` 四个可选字符串，允许自定义文字 |
| `target_duration_sec` | 仅指定时长模式使用，整数 10–90，默认 60；`content` 模式可省略。即使在 `content` 模式传入，也仍需通过 10–90 的字段校验 |
| `input_document_id` | 可选，来自文档上传接口；直接传正文时省略 |
| `use_draw_things` | 当前两种主形式及自动选择使用默认 `false`；不需要传入 |
| `theme` | 可省略；当前只接受兼容值 `minimal_dark`。主形式的实际视觉样式由所选形式决定 |

**必须注意默认值兼容：** API 省略 `video_form` 时走历史 `knowledge_cards`；省略 `duration_mode` 时走 `fixed`。上游要获得当前网页的默认行为，应明确传入 `auto` 和 `content`。

`content` 模式没有套用指定时长模式的 2,000 字符 / 90 秒上限，但仍受模型上下文、本机资源和执行时限约束，不承诺任意长度。`fixed` 模式的正文最多 2,000 字符。

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
| `source_sha256` | 服务端保存正文的哈希；服务端会去除首尾空白 |
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
| `video.mp4` | 最终 H.264 / AAC MP4 |
| `captions.srt` | 中文字幕 |
| `narration.json` | 最终讲稿 JSON，正文可从 `segments[].text` 读取 |
| `qa.json` | 本次基础媒体检查记录 |
| `chapters.json` | 内容模式的章节和时间信息 |
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
- `mode: "narration"`：采用修订讲稿原文配音；要求父任务已有可读取讲稿且属于两种主形式。
- 可选传 `title` 和 `creative_intent`。省略偏好时继承，传 `{}` 时清空。
- 返回 HTTP 202 和**新的 `job_id`**、`parent_job_id`、`status_url`；父任务与旧成片保留。上游应跟踪新编号。

修订接口与重试接口不同：修订产生子任务，重试增加原任务的尝试次数。

## 9. 尚未实现的能力

| 能力 | 当前状态与对接决定 |
| --- | --- |
| 新建任务直接采用最终讲稿 | **未提供对应入口。** 当前新建正文会被组织/改写；不要擅自发送 `input_type: "narration"`。已有讲稿修订机制可供后续实现复用，但不作为新建的保证 |
| 正式产品 CLI | **未封装。** 上游使用 HTTP；未来 CLI 可作为同一 API 的客户端 |
| 服务端幂等提交 / 上游业务编号 | **未实现。** 当前由上游记录映射，并对提交结果未知作显式处理 |
| 完成回调 / Webhook | **未实现。** 当前轮询 |
| 跨机器鉴权与远程分发 | **未实现。** 当前按同机接入 |

若上游产出的是需要逐字使用的最终讲稿，先对齐这一需求，再由 AutoVideo 增加新建入口并更新契约。不要把“最终讲稿当材料提交”误认为“保证原文配音”。

## 10. 上游实现与联调清单

建议先完成一条闭环：

1. 配置 API 基址，完成健康、依赖及声音检查。
2. 生成一份真实内容材料，显式传 `auto` / `content` 创建任务。
3. 收到 202 后保存 `job_id`；等待并区分成功、失败和取消。
4. 下载 MP4、字幕及所需讲稿，核对文件哈希，记录输出。
5. 使用任务链接在网页打开同一条视频，确认可继续修订。

再验证必要恢复行为：

- 上游进程中断后，能用原 `job_id` 恢复查询，而不是重复提交。
- 材料关键冲突能进入待补充处理，不出现自动重试循环。
- 参数错误能保留具体错误信息；取消与等待超时不被当作成功。
- 同一内容已有映射时复用原任务；提交结果未知时进入对账处理。

本文只确定现有接口和建议的对接行为，没有新增 CLI、幂等、回调或最终稿入口。

实现核对来源：仓库 `g3/app.py`（HTTP 与请求校验）、`g3/store.py`（任务/版本/文件）、`g3/worker.py`（执行和错误）、`g3/README.md`（本地使用）。字段细节可结合运行实例的 OpenAPI 查阅。
