# 按原稿配音（narration 模式）：真实产品验收

日期：2026-10-17。触发：上游（AI_research）在交给 AutoVideo 之前已审定讲稿，素材模式的改写会破坏定稿。上一条 crewAI 成片（VOD 任务，素材模式）保留约 92% 原文，但引入三处问题：重复的章节过渡（“前一步确认了数字是版本自带的……”）、暗示不存在的前一期（“接着上回说”）、技术细节被压成一个字（“用 cost_per_token 和 completion_cost 对照” → “按”）。另外字幕和卡片把 `--models` 显示得像 `–models`。

## 实现

- `POST /api/jobs` 新增 `input_mode: "narration"` 与 `chapters: [{title, paragraphs}]`（不传 `source_text`，必须 `duration_mode: "content"`，形式为 `auto` / `information_motion` / `process_diagram`）。省略 `input_mode` 仍是素材模式。
- 复用讲稿修订的逐字配音路径（`g2/authored_script.py`），扩展为多章：章节标题取自请求；`auto` 只让模型选整片形式（`narration-form` 阶段，不读改讲稿）；场景、卡片、流程图与节拍仍自动规划。
- 逐字校验两次：分句后（`authored-narration.json`），生成 Video IR 后再核对 Video IR 旁白、`captions.srt` 与章节标题（`narration-check.json`，并写入 `qa.json` 的 `narration_text`）。不一致时以 `NARRATION_TEXT_MISMATCH` 失败，不可重试，信息指出章节、字位置与两侧文字。
- 技术文字：渲染器在连续连字符之间加 0.12em 间隔，英文/符号片段能放下时整体换行；文字适配测量同步。模型生成的卡片与流程图文字若改动命令、参数、代码、文件名或版本号，本地校验拒绝并要求重写（两种输入模式都适用）。反引号保留在字幕中但不朗读。

## 真实验收

### crewAI 讲稿（自动选型 → 简洁信息动效）

- 来源：VOD `GET /api/v1/drafts/38b8cb6f-…`（5 节、14 段），章节标题来自其 outline `15091c9a-…`；请求见 `crewai/request.json`。
- 最终任务 `5da0511b-3e54-49d9-8ed5-1cada246d618`：**COMPLETED**，自动选为 `information_motion`；5 章、25 场景、63 段；**353.920 秒 / 10,617 帧**，1920×1080 / 30 fps，H.264 + AAC 48 kHz 双声道，42,487,084 bytes，SHA-256 `7387b143…39d1bc`。
- 媒体 QA **PASS**：完整解码通过，解码音轨与 TTS 相关系数 0.99994；`narration_text.status = PASS`。
- 独立核对（`crewai/media-summary.json`，经 HTTP 下载全部文件并核对 SHA-256，未调用 AutoVideo 代码）：
  - 配音文本去除空白后与提交讲稿**完全相同**（逐字比例 1.0；VOD 素材模式成片为 0.9196）；字幕 63 条与配音段逐条相同；5 个章节标题与请求逐字相同。
  - 三处旧问题均不存在：无“前一步确认了数字是版本自带的”、无“接着上回说”；“用 cost_per_token 和 completion_cost 对照”完整保留（该句超过 80 字，在“completion_cost”后的空格处分成两条字幕）。
  - `--models`（5 处）、`cost_per_token`、`completion_cost`、`_print_comparison`、`comparison['models']`、`eval_crew.py`、`v1.15.24`、`v1.15.28`、`1.15.27` 在字幕中出现次数与讲稿相同；字幕中没有讲稿以外的类连字符写法。25 个场景的卡片文字中，所有技术片段都能在讲稿中逐字找到。
- 模型调用 7 次（1 次选型 + 5 章分镜 + 1 次常规重试 `INFORMATION_CUE_TARGETS`），保守费用上界约 $0.048。
- 抽帧：`crewai/caption-*.png`、`crewai/scene-*.png`；`crop-models-before-material-mode.png` 与 `crop-models-after.png` 对照 `--models` 的显示。

最终 crewAI 任务生成之后又改了两处：`wrap_label` 换行保留空格（只用于流程图）、技术文字触发条件不再包含 `/`（只放宽卡片校验）。二者不改变信息动效的配音、字幕或排版；流程图任务 `9e1cc0bc` 使用最终代码，`implementation-hashes.json` 为最终代码哈希。

首轮任务 `db1b9dc3-16db-47af-9354-94e5355ae937`（353.835 秒）同样逐字通过，但一张卡片把讲稿中的 `1.15.27` 写成 `v1.15.27`（`crewai-first-run/scene-c3-s2-v-prefix.png`）。当时的技术文字校验有意容忍 `v` 前缀；按“版本号按原样显示”的要求改为严格匹配后重跑，得到上面的最终任务。

### 流程动态图解（手动指定）

- 两章短讲稿，含 `pip install --upgrade crewai`、`crewai eval --models`、`1.15.27`；请求见 `process-diagram/request.json`。
- 任务 `9e1cc0bc-fa37-4916-820a-e0a95f2322bf`：**COMPLETED**，3 个流程图场景，**28.139 秒 / 844 帧**，1,934,509 bytes，SHA-256 `ce5cd3c6…a8c504d9`；媒体 QA PASS，相关系数 0.99994；逐字核对、字幕、章节标题全部通过，无重试。
- 该讲稿生成的节点标签未含 `--` 参数；为验证 SVG 文字路径，另用新渲染器对本任务 props 注入 `pip install --upgrade` 标签与标题渲染单帧（`process-diagram/svg-still-injected-upgrade-label.png`，非成片画面）。

### 失败记录（保留）

- `27cb7bd4`：`INVALID_COMPONENT_PROPS`——模型给边取了 27 字符的 id，流程图规划 schema 允许 48 字符，但 `flow_diagram` 组件的 layout 只允许 24 字符。此不一致早于本次改动，与输入模式无关；`schemas/components/flow_diagram.schema.json` 受 `PACKAGE_MANIFEST.json` 保护，现有测试又断言 25 字符 id 合法，取舍留给维护者，本次未修改。另：worker 只解析 `CODE: 信息` 格式，g1 校验器的 `CODE at 路径: 信息` 被记为 `GENERATION_FAILED`。
- `a305b5c6`：`DIAGRAM_LAYOUT_LABEL`——本次改动引入的缺陷（节点标签换行时去掉了空格，与 Video IR 要求的原文拼接不符），已修复并加回归测试；同一任务中 `cost/time` 被技术文字校验误判，已把 `/` 排除出触发条件。
- 详见 `failed-attempts/`。

## 离线回归

`runtime-tests.log`：`python3 -m pytest g4/tests g3/tests g2/tests g1/tests/test_video_ir_v0_2_runtime.py -q` 210 项通过（新增 43 项：逐字校验的改写/插入/删字/章节错位/空白规则、错误信息、最终 IR/字幕/章节标题复核、自动选型与手动选型、API 字段与 422 组合、OpenAPI、worker 锁定讲稿与不可重试、技术文字校验、换行测量）。`design-tests.log`：`python3 g0/verify_design_snapshot.py` 45 份文件哈希匹配、23 项通过；`git diff --check` 通过。`tests/test_final_release.py` 在工作目录直接运行时会因忽略的运行数据失败，此问题早于本次改动。`implementation-hashes.json` 记录验收时的实现文件哈希。

## 边界

逐字校验比较的是文字；配音读音（英文与代码由 Kokoro 中文 G2P 处理）、画面观感和卡片措辞仍需真实观看判断。本次 crewAI 与短流程讲稿各跑通一次，不代表所有题材一次通过。
