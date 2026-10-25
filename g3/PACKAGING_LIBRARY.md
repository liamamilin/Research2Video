# 视频包装与辅助素材库

2026-10-23 实现。组件注册、默认设置在 `g2/packaging_library/catalog.json`，API `GET /api/packaging` 返回目录。

## 五类组件

| 槽位 | 组件 | 使用方式 |
| --- | --- | --- |
| intro | stinger v1 | 正文前的独立片头，默认 1.5 秒 |
| outro | end_card v1 | 正文后的独立片尾，默认 2.5 秒 |
| badge | corner_badge v1 | 正文角标，信息动效和流程图共用 |
| source_card | source_card v1 | 正文结束、片尾之前的来源卡，默认 4.5 秒 |
| sound | synth_sting v1 | 片头片尾提示音，沿用校验哈希的本地声音资产 |

固定帧模板位于 `g2/packaging_library/templates.py`；角标是可信 React 组件 `g1/renderer/src/Packaging.tsx`。声音文件及来源、授权、SHA-256 继续由 `g2/package_kit/kit.json` 管理，组件库引用其 ID。模型不能提供执行代码、模板路径、声音路径或来源卡文字。

## 配置与默认

`POST /api/jobs` 不传包装设置时，默认继承 `common`，五个组件均启用。没有 `sources` 时来源卡状态为 `no_sources`，不会生成空白卡或编造来源。正文时长之外增加包装时长。

```json
{
  "packaging": {
    "preset": "common",
    "recipe": "flash",
    "label": "快讯",
    "accent": "#1f7a4d",
    "intro": {"seconds": 1.5, "subtitle": "10月23日"},
    "outro": {"seconds": 3, "lines": ["面向开发者的 AI 快讯"]},
    "source_card": {"enabled": true},
    "sound": {"enabled": true}
  },
  "sources": [{"title": "官方公告", "url": "https://example.com/news", "date": "2026-10-23"}]
}
```

这些字段与原有素材/讲稿字段一起提交。覆盖顺序是通用默认 → 配方设置 → 本次制作设置；嵌套槽位按字段合并。`label` 是默认的片头标题、片尾标题和角标文字，也可用 `intro.title`、`outro.title`、`badge.text` 分别覆盖。单独关闭用例如 `intro: {enabled: false}`；整套关闭用 `packaging: {enabled: false}`。未知组件、错误类别、额外字段、非法时长和超长文字在创建任务前拒绝。

旧 `package` 请求仍接受，按显式提供的片段注册到组件库，不自动补齐旧调用方未请求的片段；它与新 `packaging` 互斥。现有历史任务和输出保持原样。旧视频修订若已有相同来源卡，保留原画面并记录 in_content，不重复附加；来源不一致时明确失败，要求通过原稿/讲稿修订更新。

## 入口和继承

- 创作页：通用默认，五类开关和栏目名称；设置随本地草稿保存。
- API：素材、文档、定稿讲稿统一在任务保存入口解析。
- 研究系统：配方引用 `config/video_parts/packaging/common.yaml`，再覆盖栏目文字及参数；手动和自动出片共用 `video_render.create`。
- 讲稿/原稿修订、画面修订、声音速度修订、背景修订：继承父任务的包装快照与来源数据。
- 重试：沿用保存的设置；CLI `g2/run_g2.py` 同样解析通用默认。

每个新任务的 `input.json` 保存选择及 `packaging_resolved`，包含目录版本/哈希、五类组件 ID/版本、最终参数、来源卡状态和快照校验哈希。重试和修订复用该快照，通用默认的后续修改不会影响它。任务产物提供 `packaging-resolved.json`。

## 时间线与验收

成片顺序为片头 → 完整正文 → 来源卡（有数据且启用时）→ 片尾。来源卡不朗读，不替换正文场景，不进入 Video IR，不使用模型补写内容；标题、域名和日期只由请求中的 sources 生成。源域名显示上限 80 字符。

字幕和章节时间只增加片头偏移；片尾和来源卡不延长正文字幕。`video.mp4` 为最终成片，`content.mp4` 为纯正文，场景预览继续使用正文时间线。`package.json` 同时记录包装快照和每段偏移；`package-qa.json` 验证完整解码、逐帧格式、帧数、配音相关性和提示音响度。

真实验收脚本及记录见 `g3/acceptance/packaging-library-20261023/`。该脚本复用保存的真实讲稿、语音、IR，实际重渲染当前组件，不发模型请求；来源条目明确为验收数据。
