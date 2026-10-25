# 大文件上外接盘 + 接入 AI创作（Creator Studio）

记录于 2026-10-21，评估完成，**等 27 个 AI 矩阵 Job 的验证跑完再做**（它们正在写 `autovideo/g3/data/jobs`、`state/`）。

## 背景

- 本机数据盘已用 95%，只剩约 22G。验证全跑完每个视频约 180MB，还要再吃几 G。
- 外接盘：`/Volumes/个人资料/2026/视频生成工作流/`（已有 `AI创作/`，含 Creator Studio、music-pilot）。
- 结论：**不整体迁移项目**（迁移要改 3 个 launchd plist、venv 脚本头、启动器 app、Claude 会话记忆目录，还有外接盘掉线风险）。代码、git、`state/*.db`、venv 留本机，只搬大数据。

## 目标目录（2026-10-21 用户指定，已建空目录）

`/Volumes/个人资料/2026/视频生成工作流/视频workflow数据/`，下面按来源分：

| 子目录 | 放什么 |
|---|---|
| `ollama模型/` | 暂不用（Ollama 模型决定不移动，目录留空） |
| `autovideo/jobs`、`autovideo/voice-models`、`autovideo/package-acceptance` | `autovideo/g3/data/` 下对应子目录，原位置留软链 |
| `AREC成片副本/` | AREC `state/video_renders`（配置项指向这里）和 `state/backups` |
| `归档/` | `autovideo/g0`、`g2/runs` 等旧实验 |

只建了空目录，**还没搬任何东西**，等 27 个 Job 验证跑完。

## 一、大文件搬外接盘

| 内容 | 大小 | 做法 |
|---|---|---|
| ~~`~/.ollama` 模型~~ | 27G | **用户 10-21 决定：Ollama 模型先不管，不移动。** `qwen3.8:27b-mlx`（27.8B，18G）、`qwen3.5:9b-mlx`（9.4B，8.9G）AREC 没在用，但保持原样；`bge-m3` 在用。以后想腾空间再议 |
| `autovideo/g3/data/jobs` | 12G | 软链到外接盘 |
| `autovideo/g3/data/voice-models` | 4.6G | 软链 |
| `autovideo/g3/data/package-acceptance` | 0.4G | 软链 |
| `autovideo/g0`、`g2/runs` 旧实验 | 约 3G | 归档过去 |
| AREC `state/video_renders` | 405M | 加配置项指向外接盘（路径来自 `web/services/video_render.py` 的 `video_renders` 目录），每个新视频都会增长 |
| AREC `state/backups` | 155M | 一并搬 |

- AutoVideo 支持 `AUTOVIDEO_DATA_DIR`，但 sqlite（`jobs.sqlite3`）和 `voice-runtime` venv 也在 `data/` 里，所以只软链大子目录，sqlite 和 venv 留本机。
- 预计腾出约 20G（不含 Ollama）。
- 搬 AutoVideo 前先停 uvicorn（8011），搬完再启动。
- **盘掉线处理**：AREC 在渲染、写成片前检查外接盘是否挂载；没挂就进报警面板，不要半路失败。成片写入失败要能重试。
- 外接盘上跑 launchd 服务需要「完全磁盘访问」，但我们只放数据，服务本体在本机，通常不会碰到。

## 二、接入 AI创作

它不是现成出片引擎：31 类模板（17 视频 F01–F17、14 音乐 MF01–MF14），多数是「上游制作包」，要人工去别处制作。

真正能本地生产的只有两处：
- **F08 动态排版**：确定性 FFmpeg 渲染，有 `POST /api/f08/render`（43128 端口，要先取 `/api/session` token）和命令行 `tool/f08-pilot/pilot render`，可选 VoxCPM2 配音。
- **MF07 纯音乐**：ACE-Step 本地引擎，模型 9.4G（`music-pilot/models`）。原 7 次额度已用完，文档写「新执行未获批准」——是项目自设限制，需要用户解除。

不接：F01–F05 等需真实素材或生成视频的形式（没有生产能力）；F10、F15 与 AutoVideo 已有的信息图/流程图重叠。

重复：Creator Studio 和 AutoVideo 都用 VoxCPM2（AutoVideo 的 `voxcpm2-8bit` 3G），各存一份。

### 接入顺序

1. **F08 试点**：`core/video/recipes.py` 的 `OUTPUT_TARGETS` 现在只有 `autovideo`，加 `creator_f08`；做「本期头条」类 15–30 秒短视频。先跑通一个 Job，和 AutoVideo 的成片比质量。F08 渲染不用大模型，成本低，适合每天自动出。
2. **配乐**：给 AutoVideo 成片配背景乐。AutoVideo 设计文档（`05_VISUAL_AND_AUDIO_SYSTEM.md`）写了「音乐弱于人声、旁白时压低」，**是否已实现未确认**。要先确认：解除 MF07 的 7 次限制、ACE-Step 在本机单首耗时。
3. 方式是松耦合：AREC 通过命令行或 HTTP 调外接盘上的 Creator Studio，不合并代码。外接盘不在时这条出口不可用（与上面的掉线检查一起做）。

### 31 种形式的参考价值（2026-10-21 评估）

分类来自 `AI创作/tool/template-content-video-complete`（F01–F17 视频）和 `template-content-music-complete`（MF01–MF14 音乐）。示例都是 960×540、12 秒、无声的程序绘制演示，艺术验收全部「待人工」；多数模板的生产路线是 `manual_handoff`。**只取参考，不全盘采用。**

- 视频里贴合我们内容（调研报告变视频）的：F10 信息图动画（数字、对比、流程）、F08 动态排版、F15 界面演示、F16 聊天对话体、F12 图片轮播（要有图）。勉强可用：F09、F13、F14。其余 9 种（镜头叙事、人物口播、角色动画、定格动画、混剪等）要真实素材或生成视频，不做。
- 音乐里人声类 5 种（MF01–04、MF13）不用；可作配乐类别的是 MF08 持续铺底、MF09 渐变层叠、MF10 环境声融合、MF11 音乐短句、MF14 音乐转场。

借鉴三点：
1. **补「视觉形式」维度**：配方现在只有受众、时长、`output: motion`，缺「这期用图表还是排版」。用这套分类当词汇表（即缺口清单里的「配置级视频形式设置」）。
2. **模板写法**：固定结构 + 必填的自然语言简报 + 按形式的验收清单；把「文件存在 / 技术解码通过 / 艺术与连播验收」分开记录，借来改渲染后的把关（现在只看渲染完成）。
3. **原文保留、不静默删改**：与脚本审计一致，不用另学。

**接入前先做一件事**：核对 AutoVideo 现有视觉形式（信息图、流程图、动态文字）与这 17 种的对应表，避免重复造轮子（目前没核对）。对照表做完，再定配方里「视觉形式」字段取哪几个值。

## 做的顺序

1. 搬 `g3/data/jobs`、`voice-models` 等 AutoVideo 大目录（约 17G；Ollama 不动）。
2. 成片副本目录配置 + 盘掉线检查。
3. 做 AutoVideo 形式与 F01–F17 的对照表，定「视觉形式」字段。
4. F08 试点。
5. 配乐（等用户决定是否解除 7 次限制）。
