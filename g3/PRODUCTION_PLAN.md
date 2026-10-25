# 统一制作计划与快速信息卡片

2026-10-23：制作计划和快速卡片已接入既有任务、版本、包装、媒体 QA、作品与下载链路。此阶段只新增快速卡片与一种浅色编辑风格；不改变旧配方的默认出片方式。音乐与更多风格的扩展仍按各自阶段推进。

## 配置入口

原 `POST /api/jobs` 保留，新增字段：

| 字段 | 行为 |
| --- | --- |
| `production_recipe` | 沿用已有 AI 快讯、操作教程、原理科普制作配方 |
| `production_mode` | `standard` 为原有执行路线；`fast` 选择已支持的快速路线 |
| `video_form` | 新增 `information_cards`，与旧 `knowledge_cards` 保持独立 |
| `visual_style` | 标准路线使用 `legacy`；快速卡片使用 `editorial_light` |
| `production_context.content_recipe` | 上游文稿配方身份与显式设置，含 `id`、可选 `version`、`settings` |
| `production_context.matrix` | 矩阵／生产配置身份与显式设置，结构同上 |
| `content_notices` | 可选核实提示：`chapter_index` 从 0 起；`null` 为全片提示，`message` 为显示文案 |

上下文的 `settings` 接受形式、档位、风格、声音／倍速、时长、创作偏好和包装。制作配方身份放在顶层，不在不同层中隐式按名称匹配。

覆盖顺序：通用默认 → 制作配方 → 文稿配方 → 矩阵 → 本次显式选择。嵌套字段合并，列表整体替换，显式关闭与空字符串保持原意。未知字段、无效风格、缺少素材和不兼容的显式形式会拒绝，不静默更换。

仅传 `production_mode: fast` 且没有显式形式时，自动选择信息卡片和对应风格；若显式指定流程图等不兼容形式，则返回错误。快速档位不接入轮播图片。已配置的背景音乐沿用共用混音阶段，具体能力及独立验收见 [音乐混音](MUSIC_MIX.md)；本轮快速卡片样片未启用背景音乐。

示例为正式 API 合同：

```json
{
  "title": "缓存的用途与限制",
  "input_mode": "narration",
  "duration_mode": "content",
  "chapters": [
    {"title": "缓存边界", "paragraphs": ["缓存可以减少重复读取，但可能过期，使用时仍需检查更新条件。"]}
  ],
  "production_recipe": "science_explainer",
  "production_mode": "fast",
  "video_form": "information_cards",
  "visual_style": "editorial_light",
  "production_context": {
    "content_recipe": {"id": "research:topic_digest", "settings": {"packaging": {"label": "专题速览"}}},
    "matrix": {"id": "technology", "settings": {"packaging": {"sound": {"enabled": false}}}}
  }
}
```

`GET /api/production/capabilities` 返回当前支持组合。`POST /api/production/resolve` 接受相同创建请求，返回 `preview: true` 和解析计划，不创建任务、不冻结媒体资产、不调用模型；实际任务提交时完成资产冻结。调用方不得注入 `production_plan` 或已解析快照。

## 冻结、重试与兼容

新任务保存 schema v1 的 `production_plan`：完整有效设置、逐字段配置来源、风格参数、包装与组件版本、制作配方哈希、执行器身份／版本、输出规格、原文哈希和计划哈希。任务详情及下载产物均可查看。

worker 对照数据库和保存的输入验证计划；计划缺失、内容／声音／包装被改动时在启动生成前失败。标准任务与快速任务共用幂等提交、取消、attempt、日志与 SHA 下载验证。

重试保留原快照。相同显式请求在制作配方默认更新后仍返回原任务。旧提交哈希保留兼容判断；新设置必须用新的提交标识。旧任务新增列为空，不强制补写或重放。

讲稿、声音和倍速修订从父版本快照派生，记录父计划哈希。快速卡片支持讲稿修订、声音与倍速修订；单场景自由重排未开放。讲稿改动后，原有章节核实提示转为全片待核对提醒，不猜测新段落的证据状态。

`production-execution.json` 记录实际执行的形式、执行器、编码器、后备尝试与耗时，补充请求中的 `auto` 选择。标准视频仍使用原可信 Remotion 组件及现有 Video IR；快速卡片通过明确的运行时 presentation 扩展复用受控文字组件合同，不修改冻结的 v0.2 schema。

## 快速卡片如何执行

定稿模式复用现有逐字分句、真实 TTS、短语对齐与章节编译。每张卡片引用相应旁白段，正文直接取定稿原文，不经过模型摘要，不人工编写页面要点，因此保留数字、单位、限定条件和否定。

Pillow 按冻结的风格与包装强调色绘制卡片；原生 FFmpeg 合成。字幕滤镜前先补成 30 fps，避免静态输入导致字幕冻结；字幕格式控制字符转义，中文按像素换行并避免常见句末标点出现在行首。中文字体缺失或文字超出安全区域时明确失败。

优先使用本机 H.264 VideoToolbox，失败时保留尝试并改用 libx264。配音、字幕和章节使用原有时间线。正文只绘制一次角标；片头、来源卡、片尾和提示音仍由共用包装阶段处理，快速执行器不内嵌另一份包装。片头偏移只由该阶段计算。

现有 IR、完整解码、真实配音相关性和逐字字幕检查继续执行，另从每条字幕的真实视频中取帧检查文字像素。核实提醒显示于正文独立区域，来源卡只使用请求中已有来源。

## 研究系统与矩阵

定稿页新增“快速卡片出片”，普通出片入口保留。它发送完整定稿、原声音、包装、来源和核实提示，明确选择快速卡片，不修改文稿配方或矩阵默认。

文稿配方 `output` 可引用 `production_recipe`、`production_mode`、`visual_style`；新增共享出片部件 `fast_cards`，既有 `motion` 部件保持。显式引用示例：

```yaml
output:
  use: fast_cards
  packaging:
    use: common
    label: 专题速览
```

矩阵中某个 Job 的生产配置可通过 `render` 覆盖；旧编辑器保存其他设置时会保留该块：

```yaml
job: research/technology
recipe: topic_digest
render:
  production_mode: fast
  video_form: information_cards
  visual_style: editorial_light
  packaging:
    sound:
      enabled: false
```

研究系统传递显式上下文，最终默认和能力解析归 AutoVideo；上游不复制渲染选型算法。自动、手动与失败恢复继续通过原有共同出片服务，下载制作计划和实际执行记录，与原作品一并保存。

## 验收与性能口径

真实 MP4、日志、字幕／音轨检查、版本修订与正式两服务交接见 [验收记录](acceptance/production-plan-fast-cards-20261023/README.md)。

区分完整定稿出片、新配音、已有资产合成及单次修订。独立样片的 29 秒不作为全新文稿出片承诺。当前快速路线已减少浏览器渲染与画面规划开销，长稿仍主要耗时于本机配音；20 条批次吞吐尚未压测，不按单片耗时线性宣称并发性能。

配音与回传进一步优化见 [实测记录](acceptance/voice-transfer-speed-20261023/README.md)：同稿全新配音的完整等待为 321 秒，命中全部章节配音缓存时为 90 秒。缓存绑定语音模型、参考音频、合成设置和整章有序文本；配音／模型／文字变化或 WAV 损坏时重新生成。章节缓存不改变正文、字幕、包装和媒体 QA。
