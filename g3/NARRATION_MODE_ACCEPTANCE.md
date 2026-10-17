# 按原稿配音验收

日期：2026-10-17。状态：**PASS（真实成片）**，证据目录 `g3/acceptance/narration-mode-20261017/`。

## 范围

`POST /api/jobs` 的 `input_mode: "narration"`：上游提交已审定的分章讲稿（`chapters[].title` / `chapters[].paragraphs`），AutoVideo 逐字配音、字幕逐字显示、章节标题照用，只自动规划画面；`auto` 时仅由模型选择整片形式。分句后与生成 Video IR 后各做一次逐字校验，不一致即 `NARRATION_TEXT_MISMATCH`（不可重试）。命令、参数、代码和版本号在字幕与画面中按原样显示。接口字段见 `UPSTREAM_INTEGRATION.md` 第 3 节。

## 结果

| 讲稿 | 任务 | 形式 | 时长 | 结论 |
| --- | --- | --- | --- | --- |
| crewAI eval 成本与耗时（5 章，VOD 草稿 38b8cb6f） | `5da0511b` | auto → information_motion | 353.920 秒 | 配音与字幕逐字等于讲稿；章节标题一致；`--models` 等技术文字正确显示；媒体 QA PASS |
| 升级 crewAI 并跑最小 sweep（2 章） | `9e1cc0bc` | process_diagram | 28.139 秒 | 同上；无重试 |

对照：同一讲稿此前以素材模式制作的成片保留 91.96% 原文，并出现重复过渡、“接着上回说”和技术细节缩写；narration 模式为 100%，三处问题均不存在。

## 已知限制

- 流程图规划 schema（id 48 字符）与 `flow_diagram` 组件 layout（24 字符）不一致，模型偶尔取长 id 会在编译时失败（任务 `27cb7bd4`）；早于本次改动，未修改受清单保护的组件 schema。
- 逐字校验不判断读音；英文与代码的朗读质量、画面观感仍需人工观看。
