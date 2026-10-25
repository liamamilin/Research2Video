# 包装组件库验收（2026-10-23）

## 交付

- 五类已注册组件：片头、片尾、角标、来源卡、提示音；通用默认和配方逐字段覆盖。
- 9 个配方文件（16 个受众展开栏目）引用共用包装方案；研究系统手动/自动出片、创作页、素材/文档/讲稿 API、CLI、重试和修订接入同一解析入口。
- 新任务保存版本、目录哈希和最终参数；关闭包装/单个组件须明确设置。无来源时记录 `no_sources`。
- 来源卡在完整正文之后，不占用旁白场景；旧卡复用时防重复，来源冲突明确失败且不可原样重试。

配置说明见 [../../PACKAGING_LIBRARY.md](../../PACKAGING_LIBRARY.md)，研究系统的配置见 `AI_research/Base_CodingCLi/spec/video-packaging.md`。

## 真实媒体

通过 `verify.py` 复用已有真实语音与 IR，实际调用当前 Remotion 组件、Pillow 帧模板和 FFmpeg 重新渲染。没有新增模型调用或新配音；来源条目明确为组件验收数据。

| 形式 | 正文 | 片头 | 来源卡 | 片尾 | 成片 | 配音相关系数 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 流程图解 | 28.133 秒 | 1.5 秒 | 4.5 秒 | 2.5 秒 | 36.633 秒 | 0.999931 |
| 信息动效 | 35.833 秒 | 1.5 秒 | 4.5 秒 | 2.5 秒 | 44.333 秒 | 0.999835 |

两条成片均通过完整音视频解码、逐帧格式、帧数/时长、片头偏移后的配音相关性、提示音响度检查；字幕与章节按片头时长偏移。已检查片头/片尾、正文角标、来源卡的抽帧；深色流程图角标使用浅色文字以保持可读性。

- [流程图解 MP4](../../data/packaging-library-20261023/diagram-2/artifacts/package/video.mp4)
- [信息动效 MP4](../../data/packaging-library-20261023/information-2/artifacts/package/video.mp4)
- [机器结果](results.json)
- [流程图解抽帧](diagram-contact-sheet.jpg)、[信息动效抽帧](information-contact-sheet.jpg)
- [创作页包装设置截图](ui.png)：验证默认五类启用、总开关、栏目名称编辑和刷新后的草稿恢复。

原素材、旧成片以及第一次验收输出均保留。`information-1` 是最初复用含历史内嵌来源卡的验收输出，不作为最终样片；最终使用不含旧来源卡的 `information-2`。该发现促成旧卡防重复检查。

## 回归

- AutoVideo 主回归：435 passed、1 skipped；另追加的现代包装修订继承 API 测试：4 passed。
- 研究系统后端完整回归：1786 passed、3 skipped、1 deselected。排除项是既有 `test_shipped_recipes_come_out_exactly_as_they_were_written_out_per_audience`：历史受众快照与本次开始时已有的写稿/调研提示词、审计设置不一致；已用开始时保存的配置复核，未覆盖那些改动。
- 研究系统前端：48 个测试文件、329 项通过；TypeScript/Vite 构建及 ESLint 完成（1 条原有 hooks warning）。
- 两仓库 `git diff --check` 通过；研究任务配置校验通过。
- 旧冻结文档清单验证 `g0/verify_design_snapshot.py` 仍在 `AGENTS.md` 字节数校验失败；本次未修改该文件或重写原始清单。该问题不属于运行代码回归。

首次完整检查运行期间工作区有其他改动和 UI 构建，出现了载入旧代码/缺少短暂构建目录的失败；对应测试在代码稳定后重新通过。上述完整后端与运行代码结果来自最终重跑。

## 生效

确认没有运行中的研究、文稿、出片任务后，通过原 AutoVideo 启动器和研究网关正常重载服务。组件库接口应返回 5 类组件，研究服务健康检查为 `ok`。具体核验记录见 [activation.json](activation.json)。本次的隔离 UI 验收服务已停止。
