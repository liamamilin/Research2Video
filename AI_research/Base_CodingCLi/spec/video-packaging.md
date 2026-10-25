# 视频包装与辅助素材

2026-10-23：各配方引用 AutoVideo 的五类组件（片头、片尾、角标、来源卡、提示音），通用默认与正文制作分开。

## 配置

共享参数位于 `config/video_parts/packaging/common.yaml`。完整通用默认、注册组件版本及渲染代码由 AutoVideo 的组件库维护；这里的共享 YAML 只写需要覆盖的参数。共享方案可以在「文稿配方 → 共用部件 → 包装方案」编辑，保存前检查所有引用配方。

每个配方的 `output.packaging` 引用共享方案，再覆盖自己的文字和参数：

```yaml
output:
  use: motion
  packaging:
    use: common
    label: 快讯
    intro:
      subtitle: '{date}'
    outro:
      seconds: 3
      lines: ['面向{{audience}}的 AI 快讯']
```

嵌套槽位逐字段合并。覆盖顺序为引擎通用默认 → 共用 YAML → 配方。`intro/outro/badge/source_card/sound` 都可用 `enabled: false` 单独关闭，`packaging.enabled: false` 关闭整套。`label` 默认用于片头、片尾标题和角标；标题可单独覆盖。片头、片尾、来源卡时长范围 0.5–5 秒。

`{date}` 只从选题发布日期/报告日期填写，没有日期则省略副标题；`{{audience}}` 继续由受众部件填写。新配方没有包装字段时也继承引擎通用默认。旧 `output.package` 保留显式配置兼容，不能与新 `packaging` 同时设置。

## 出片与来源

所有手动、自动及重跑出片都经 `web/services/video_render.create`，发送同一套 `packaging` 选择和配方 ID；AutoVideo 的素材、文档、API 和 CLI 也接入组件库。来源卡默认启用，研究报告有可用来源时发送 `sources`，没有来源时引擎明确记录 `no_sources`。来源卡对信息动效、流程图都适用，在完整正文后单独展示，不替换正文场景或改动旁白。

每条任务保存解析后的参数、组件 ID/版本和目录哈希；重试、讲稿/画面/声音等修订继承该快照。最终成片、字幕和章节使用包含片头偏移的时间线，包装详情在 `packaging-resolved.json` 和 `package.json`。已有视频保持原文件，可通过新出片应用新方案。
