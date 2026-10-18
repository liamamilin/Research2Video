# G3 本地应用

G3 提供本机 FastAPI + SQLite 服务和 React 界面，把已通过 G2 实测的生成流水线放进单 worker 队列。只绑定 `127.0.0.1`，不接受任意文件路径或生成代码；任务源文会存入独立任务工作区。生成讲稿与分镜时，源文会发送给 DeepSeek API；语音合成和视频渲染在本机执行。

## 配置与启动

1. 在项目根目录 `.env` 中设置 `DEEPSEEK_API_KEY=...`（权限应为仅当前用户可读）。密钥不会出现在 API 响应或日志中。
2. 确保 G0 的 Kokoro、Remotion 和浏览器依赖可用。若当前 Python 环境没有 FastAPI，按 `g3/requirements.txt` 在隔离环境安装后启动。前端构建：

   ```sh
   cd g3/web
   NODE_PATH="../../g0/vendor/remotion-audiogram/node_modules" \
     ../../g0/vendor/remotion-audiogram/node_modules/.bin/esbuild src/main.tsx \
       --bundle --minify --format=esm --jsx=automatic --outdir=static --entry-names=app
   ```

3. 在项目根目录创建隔离 Python 环境并启动服务（G2 worker 会单独调用 G0 Python 环境）：

   ```sh
   cd ../..
   python3 -m venv g3/.venv
   g3/.venv/bin/pip install -r g3/requirements.txt
   g3/.venv/bin/python -m uvicorn g3.app:app --host 127.0.0.1 --port 8000
   ```

4. 在浏览器打开 `http://127.0.0.1:8000/`。默认数据目录为 `g3/data/`，可通过 `AUTOVIDEO_DATA_DIR` 指定独立的本地数据位置。服务首次启动会将上次停止时仍在运行的尝试记录为可重试失败，不会伪报为运行中或完成。

### React 构建

项目仓库保留构建后的 `web/static/app.js` 和 `app.css`，以便 Python 服务无需运行 Node 即可启动。开发者依赖来自 G0 的 vendored npm 包；`package.json` 记录兼容版本，构建不需要联网。

## 服务行为

- SQLite `BEGIN IMMEDIATE` 原子领取队列任务，进程文件锁阻止第二个 worker 同时工作。
- 每个任务源文和每次尝试都使用单独工作目录；重试保留前次日志和报告。
- 取消会终止当前流水线进程组；重启恢复会检查并回收仅匹配本项目 G2 命令的孤儿进程。
- 服务只公开完成且 QA 为 PASS 的产物；下载前核验路径归属与 SHA-256。
- 配置预检只返回各依赖是否就绪，不返回 API 密钥内容。
- 指定时长短片可设置为 10–90 秒；历史 MVP 范围为 10–20 秒，45、60 和 90 秒样片真实生成并通过媒体 QA，见 [`LONG_FORM_ACCEPTANCE.md`](LONG_FORM_ACCEPTANCE.md)。中间时长未逐一实测。服务仍为单机、单 worker、深色知识图解和“小晓”普通话旁白。

### 可选：Draw Things 本地场景图

新建页可选择为 3 个分镜分别生成一张背景图，再由固定 Remotion renderer 叠加已登记图解，并在每个场景里执行限幅推近和平移。图像只进入当前任务工作区；Video IR v0.2 和组件动作 Registry 不变。

此功能通过 Draw Things 应用内置的 HTTP API 工作，不下载或另装图像服务。先在 Draw Things「设置 → 高级」开启 HTTP API 服务器并选择 HTTP（默认端口 7860），再在 AutoVideo 页点击「重新检测」。生成时沿用应用当前加载的模型、采样器、步数和指导设置；AutoVideo 只提交每个分镜的画面意图、1280×720 尺寸和固定负面提示词。连接目标固定为 `127.0.0.1`，拒绝远程主机。

AutoVideo 在创建时检测本地 API；未连接时，勾选场景图的任务不会进入队列。关闭选项仍使用原有的纯程序化图解流程。源文仍会发送给 DeepSeek 生成讲稿和分镜；发送给 Draw Things 的是从分镜规划出的本机背景图提示词。

任务成功完成后，详情页会显示三张场景背景预览。服务端先验证已登记清单文件的 SHA-256，再读取当前尝试工作区内的固定 PNG 路径，并核验图片签名、尺寸和 SHA-256；未完成任务不公开预览，图片与清单哈希不一致时请求失败。

## 测试

```sh
python3 -m pytest g3/tests g2/tests g1/tests/test_video_ir_v0_2_runtime.py -q
```

测试不调用外部模型；真实生成验收需要通过 Web 界面提交任务，产物和执行日志保存在 `g3/data/jobs/<job-id>/attempts/<attempt>/g2-run/artifacts/`。固定 G4 Golden Case 输入和可重复的发布验收流程见 [`../g4/README.md`](../g4/README.md)。

## 流程动态图解入口

在根目录执行 `npm ci --prefix g2/layout --ignore-scripts --no-audit --no-fund`，重启服务后，新建页默认选择“流程动态图解”。API 通过 `video_form: "process_diagram"` 选择该形式，不能同时启用 `use_draw_things`。省略该字段的旧客户端仍走 `knowledge_cards`；历史任务迁移为原模式，任务源文、尝试和成片保留。字段会随重试及服务重启保留。

生成过程中使用同一个 worker、TTS、Video IR 和可信渲染器；原文到步骤/分支的来源记录、布局与旁白对应记录可在完成页下载。不适合流程的素材会报告原因，不凭空补步骤。


## 完整材料自动分章

流程动态图解和简洁信息动效均可选择“按内容自动组织”（网页默认）：粘贴完整中文材料，系统先建立整篇纲要，再逐章生成旁白和图解，按真实配音合成一条连续视频。此模式不套用原 2,000 字符输入上限或 90 秒成片上限；不承诺任意长度，实际仍受模型上下文、本机资源和执行时限约束。旧客户端的 `knowledge_cards` 与背景图继续使用指定时长短片模式。

API 示例（`source_text` 填入实际材料）：

```json
{
  "source_text": "至少 50 字符的完整中文材料……",
  "video_form": "process_diagram",
  "duration_mode": "content"
}
```

`duration_mode` 缺省为 `fixed`，兼容旧客户端和历史任务。`content` 模式忽略兼容字段 `target_duration_sec`，不通过改写到固定秒数或剪断配音来限制长度。指定时长模式仍支持 10–90 秒、最多 2,000 字符。

完成页显示章节时间和跳转按钮，可下载整片 MP4、字幕、最终讲稿、整篇纲要及章节/知识点覆盖记录。结构性覆盖表示原文单元被纳入规划、规划知识点被旁白引用，不等于自动证明语义无误。章节模式按所选形式生成流程图或信息动效，不把普通讲解强行转为因果链。

## 两种表现形式

网页新建页提供两种形式：

- **流程动态图解**：`video_form: "process_diagram"`，适合步骤、分支和明确关系；现有可用版本已收尾。
- **简洁信息动效**：`video_form: "information_motion"`，适合观点、方法、清单和对比；浅色大标题与卡片随讲解展开。

两种形式均支持 `duration_mode: "content"` 及原指定时长选项。信息动效无需 ELKjs 或 Draw Things；不能同时启用背景图选项。完成页可下载信息分镜、来源及动效时机记录，并沿用章节跳转和视频下载。

API 的历史默认值 `knowledge_cards` 保持兼容；旧任务、场景图片和单张背景修订入口保留。该旧模式不再作为新建页第三种主形式展示。

## 自动选择与输入准备

网页默认“自动选择（推荐）”，它是两种既有形式的选择方式。API 使用 `video_form: "auto"`；省略字段仍沿用历史默认值，保持旧客户端兼容。`video_form` 保存用户请求，`resolved_video_form` 保存实际选择，`input_analysis` 返回理由和问题摘要。两种形式仍可手动指定。

自动/手动的新形式均先经过轻量输入准备：保留原始 `input/source.txt`，按原句单元整理来源，可明确排除逐字重复和粘贴噪声；每个原始单元都必须被保留或有排除记录。内容排序与来源引用记录在 `input-brief.json`，整理文本在 `prepared-source.txt`，完成后可下载。近似重复但条件不同的句子不会按逐字重复规则删除。

缺失、歧义、冲突会提示；只有无法可靠继续的关键问题才停止并显示需确认事项（`INPUT_REQUIRES_CLARIFICATION`），不会猜测事实生成视频。修正材料后可新建任务。普通排版问题和非必要背景不足不设审批关卡。此层仅处理当前中文文本输入，不是外部事实核验、文件解析或万能纠错系统。

## 内容修订与创作偏好

完成、失败或取消任务可在详情页打开“内容修订与版本”。修改原稿会重新组织讲解；修改讲稿则逐字采用修订文字（仅作字幕分句），按完整配音决定时长并沿用原形式。新版关联原任务，旧版不覆盖。两种当前主形式支持直接讲稿模式；旧知识卡片可通过原稿修订。

新建页/修订页的“创作偏好”可完全留空，支持受众、用途、语气和重点的建议及自定义输入。字段随任务保存，修订时默认继承，浏览器也记住最近输入；填写偏好不是生成前置条件。

API：`GET /api/jobs/{id}/editable-content`、`GET /api/jobs/{id}/revisions`、`POST /api/jobs/{id}/revisions`。POST 传 `mode: "source" | "narration"` 和 `text`，可另传标题及 `creative_intent`；省略偏好时继承父任务，显式传空对象时清空。新建任务也支持可省略的 `creative_intent`，包含 `audience`、`purpose`、`tone`、`focus` 四个字符串。结果与证据见 `CONTENT_REVISION_ACCEPTANCE.md`。

## 按原稿配音（API）

上游已审定的讲稿可直接新建任务：`POST /api/jobs` 传 `input_mode: "narration"`、`chapters: [{"title", "paragraphs"}]`、`duration_mode: "content"`，`video_form` 为 `auto`、`information_motion` 或 `process_diagram`，不传 `source_text`。配音与字幕逐字采用讲稿，只做分句和空白规整；章节标题取自请求；画面规划（`auto` 时含形式选择）仍自动完成。分句后及生成 Video IR 后各校验一次，讲稿、Video IR 旁白、字幕或章节标题不一致时任务以 `NARRATION_TEXT_MISMATCH` 失败且不可重试。省略 `input_mode` 时仍是原素材模式。字段与示例见根目录 `UPSTREAM_INTEGRATION.md` 第 3 节，真实验收见 `NARRATION_MODE_ACCEPTANCE.md`。

命令、参数、代码和版本号在字幕和画面中按原样显示：连续连字符之间留出间隔（`--models` 不再看成 `–models`），这类片段能放下时不在中间换行；模型生成的卡片或流程图文字改动它们时会被拒绝重写。

## 本地文档输入

新建页可上传 `.docx`、文字型 `.pdf`、`.txt` 或 `.md`。服务在本机提取正文并填入原素材编辑框；可直接点击“开始生成”，也可先修改正文，没有额外必经确认。原有粘贴输入和 API 保持兼容。上传本身不调用模型，提交生成后仅正文进入既有 DeepSeek 链路；原文件保存在本机。

- Word 按正文段落和表格顺序提取；图片、页眉页脚、批注、脚注和文本框不自动转录，页面显示说明。未接受的修订会明确提示，避免静默选错正文。
- PDF 按页提取文字；扫描页没有文字层时提示暂不支持 OCR。图片/图表不转录，复杂分栏/表格顺序可能需要在正文中调整。加密文件需先解除密码。
- TXT/Markdown 支持 UTF-8（含 BOM）、带 BOM 的 UTF-16，以及 GB18030 兼容的中文编码。Markdown 作为正文材料进入原有内容准备层。
- 上传文件最大 32 MiB，Word 展开内容最大 64 MiB，用于限制本机解析资源占用；这不是视频时长限制。不会截断文件或提取结果。提取后沿用既有正文至少 50 字符和指定时长模式的输入规则；完整文档使用“按内容自动组织”。

API：`POST /api/input-documents`，用 multipart 的 `file` 字段上传，返回 `document_id`、`text`、文件信息和提示。随后向原 `POST /api/jobs` 提交 `source_text`，可附加 `input_document_id` 关联原文档。`source_text` 可与提取结果不同，以用户编辑后的正文制作；任务保存修改标记与独立哈希。

上传记录位于 `g3/data/input-documents/<id>/`；创建任务时将原文件、原始提取正文、来源记录复制到 `g3/data/jobs/<id>/input/document/`，不依赖临时上传目录。详情页展示来源并可下载原文件，下载前校验 SHA-256。`GET /api/jobs/{id}/input-document` 读取来源；`GET /api/jobs/{id}/input-document/original` 下载。无文件输入的历史任务返回空来源；后续修订仍通过父任务链接保留原文档出处。

文件损坏、不支持格式、乱码或超出解析范围会明确报错，不创建视频任务；页面保留原有正文。来源文件被改变或丢失时不继续关联该文件。记录默认保存在忽略的本地数据目录，当前未增加清理管理界面。验收见 `INPUT_DOCUMENTS_ACCEPTANCE.md`。

## 场景修订、声音与导出速度

两种当前主形式完成后，详情页显示场景预览。点击某幕可定位播放，并选择直接修改屏幕文字，或用一句要求让 AI 重排该幕。直接文字修改不调用模型；AI 仅规划所选一幕，沿用对应的旁白段。原稿、旁白音频、其他场景及原版本保留。当前重新合成整片，但不会重新生成未修改的场景内容。

“声音与导出速度”支持切换本地声音和 0.5 / 1 / 1.5 / 2 倍速。只改速度时复用正常速度音频，保持音调，并同步重算字幕、场景和动画触发；导出 MP4 实际生效。反复修改倍速取自保留的正常速度音频，恢复 1 倍直接取回该音频。声音变化时按原讲稿重新配音，按实测新语音重排时间，不改写讲稿或强行截断。新建视频也提供这些选择，内容修订默认继承。

- Kokoro：8 种普通话男女声，沿用现有本机模型。
- Qwen3-TTS：Vivian、Serena、福叔普通话，以及 Dylan 北京口音、Eric 四川口音；这是预置声音的口音，不是自动翻译成方言文稿。页面支持同文试听。
- Qwen3-TTS 是可选本机推理，不新增常驻服务；模型未就绪时相应选项禁用，Kokoro 保持可用。当前没有接入粤语或自定义声音克隆。

已验证 Qwen3-TTS 的环境是本机 macOS 26.6.2 / Apple Silicon，MLX 量化版本；不代表所有 Mac 系统均已验证。可选安装：

```sh
python3 -m venv g3/data/voice-runtime
g3/data/voice-runtime/bin/pip install -r g3/voice-requirements.txt
g3/data/voice-runtime/bin/python - <<'PYTHON'
from huggingface_hub import snapshot_download
snapshot_download('mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-4bit',
    revision='08c72cad5e2fd0f41730c8bd1f28149585e46361',
    local_dir='g3/data/voice-models/qwen3-0.6b-4bit')
PYTHON
g0/.venv/bin/python g2/voice_preview.py qwen_eric
# 仅在上一步成功生成 sample.wav 后启用这个本地模型：
touch g3/data/voice-models/qwen3-0.6b-4bit/.ready
```

语音模型和样音位于忽略的 `g3/data/`；模型推理使用本地文件并关闭模型网络下载。源文仍按原有流程交给 DeepSeek 规划，配音不发送给新的云服务。

### VoxCPM2（`voxcpm_anchor`，原稿模式的推荐声音）

VoxCPM2（2B，8 位量化，Apache-2.0，约 3 GB）没有内置说话人。`g2/voxcpm_voice.py` 让每一句都以同一段 5 秒参考音
（`g2/voice_assets/voxcpm2_anchor.wav`，内容是 `g2/voices.py` 的 `VOXCPM_ANCHOR_TEXT`）为准，并接着上一句往下读，每章开头
回到参考音。去掉参考音，句与句之间音色会变；只接上一句，会逐渐变成另一个人。要换音色，换这段参考音和它的文字即可。

- 读的文字和字幕分开处理：`g2/tts_text.py` 把小数写成中文读法（“Opus 5.5”读“五点五”，不读成 five point five），
  “2 万 / 2 倍 / 2 次”这类孤立的 2 读“两”（不是“二”），字幕和原话校验仍是原文。
- **接续方式 `chain`**（`AUTOVIDEO_G2_VOXCPM_CHAIN`，默认 `guarded`）：接着上一句读会把语速一路放大（C4 v10 实测每章从约 4.6
  加快到约 6.9 字/秒，下一章开头又掉回去）。`guarded` 在上一句的语速偏离参考音超过 12% 时改用参考音；`anchor` 每句只用参考音
  （最稳，语速离散度最低）；`chain` 是原来会漂的做法。
- 每句生成后按语速检查（每秒 2.8–9.5 个字单位，少于 6 个字的短句不查），超出范围换随机种子重新生成，最多三次；结果写入
  `voice-report.json`（每句的秒数、语速、尝试次数、用的是参考音还是上一句）。
- **句间停顿按内容变化**（`g2/pauses.py`）：模型自己带的头尾静音每句 0–0.4 秒不等，先统一裁成固定边距，再按规则加停顿——问句后多停、
  长句后换口气、很短的引子句后留一拍、分号后和长句中间切开处几乎不停、换段落停得更久、换章最久。每个间隔写在 alignment.json 的
  `gap_after_ms`。AREC 的 `core/video/length.py` 用同一套规则算时长（两边共用一张 GOLDEN 对照表，改要一起改）。
- 默认扩散 10 步（实时倍率约 1）；`AUTOVIDEO_G2_VOXCPM_STEPS=25` 更稳，约慢 2.7 倍。

安装（与 Qwen 共用 `g3/data/voice-runtime`）：

```sh
g3/data/voice-runtime/bin/python - <<'PYTHON'
from huggingface_hub import snapshot_download
snapshot_download('mlx-community/VoxCPM2-8bit', revision='d52725898a0675703f7f9ddc5a4d1a3cdbb99032',
    local_dir='g3/data/voice-models/voxcpm2-8bit')
PYTHON
g0/.venv/bin/python g2/voice_preview.py voxcpm_anchor
touch g3/data/voice-models/voxcpm2-8bit/.ready
```

`model.safetensors` 的 SHA-256 为 `c07c59aca742fe8f53384a3da4d8a3b3fd98ddc77066dd4bd35b51e08c34a72f`。

API：`GET /api/voices`、`GET /api/voices/{voice_id}/sample`；新建任务可传 `voice_id` 与 `video_speed`（默认小晓、1 倍）。`GET /api/jobs/{id}/scenes` 返回可改字段与预览；`POST /api/jobs/{id}/visual-revisions` 传 `scene_id`，以及 `changes` 或 `instruction` 两者之一；`POST /api/jobs/{id}/media-revisions` 传 `voice_id`、`video_speed` 的至少一项。所有修订生成独立子任务，原任务版本列表统一列出。

验收与实际视频、样音、调用记录见 `SCENE_MEDIA_ACCEPTANCE.md`。

## 创作工作台与作品库

创作台默认空白输入，可主动载入示例；素材、标题、上传文件关联和成片设置自动保存在当前浏览器。声音/时长和创作偏好按需展开。完成页以播放器配合画面、讲稿、声音、版本标签，章节和技术记录可展开查看。刷新后恢复创作草稿；场景编辑草稿只在当前页面标签切换时保留。

作品库使用分页、标题/编号搜索与状态筛选。`GET /api/jobs` 保留 `limit` / `offset`，新增可选 `q` 和 `status`，返回全量 `summary`。`NEEDS_INPUT` 筛选素材待补充，`FAILED` 筛选其他执行失败；记录原始状态保留兼容。详情见 `FRONTEND_REFRESH_ACCEPTANCE.md`。

## 上游内容工具接入

开发者对接请直接阅读 [AutoVideo 上游工具对接指南](../UPSTREAM_INTEGRATION.md)：包含当前 HTTP 请求示例、任务状态、成片下载、失败恢复和业务编号映射，并明确标注尚未实现的最终稿新建、CLI、幂等与回调能力。

## 程序坞启动

本机已有 `~/Applications/AutoVideo.app` 轻量启动器并固定到程序坞。点击后自动启动或复用本地服务，再打开网页；后台制作独立于浏览器运行。重建与日志说明见 [本机启动器](launcher/README.md)。
