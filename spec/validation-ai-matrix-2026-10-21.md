# AI 矩阵 27 个 Job 全量验证（2026-10-21 晚到 10-22 凌晨）

做法：用 CLI 按依赖顺序、每次两个地跑完 27 个 Job，同时给其中 25 个临时配了 `auto: 1` 的生产配置，让应用自动写稿、自动出片。结束时已把这 25 套的 `auto` 关掉（`config/production/` 里这 25 个文件未提交，`flash`、`point_short` 是原有的，没动）。

## 结果

- **研究**：27/27 退出码 0，用时 246–1153 秒。
- **出片**：8 个完成（`pai_00`、`pai_01`、`monitoring_ai`、`practice_ai_agents_practice`、`practice_ai_agents_practice_search`、`research_ai_agent_coding_tool_research`、`research_ai_agents_survey_direct`、`research_ai_survey`），全部 1920×1080，内容 1.5–3.5 分钟，成片 QA、包装 QA 都 PASS；片尾按配方带上（2.5–3 秒，无片头）。
- **9 个被挂起**：7 个出片失败（`DeepSeek API returned HTTP 402`，账户余额不足），2 个写稿失败（`research_ai`、`research_ai_automation_systems_research`）。
- **另有 5 个条目（`pai_02`、`pai_06`、`pai_08`、`pai_09`、`monitoring_ai_cn`）和 3 个未开始（`pai_03/04/05`）**，见下面的问题 2。

## 发现的问题

1. **DeepSeek 402**：AutoVideo 出片要调 DeepSeek，余额用完后所有渲染失败。需要用户充值，充值后挂起的条目里文稿已定稿的会自动继续（`reopen_approved`）。
2. **自动流水线对旧条目不公平**：`tick()` 按开始时间倒序处理批次（`ap.list_runs` 的 `ORDER BY started_at DESC`），新批次总先抢到「每次最多 2 个写稿」的名额。`pai_02`、`pai_06`（脚本已失败）和 `pai_08`、`pai_09`（草稿等审计）从 10-21 20:30 到 10-22 01:47 都没被重试，重试计数始终是空的。批次一多，老的会一直饿着。**已修**：改成按开始时间从老到新处理。
3. **改编 JSON 写不稳**：`pai_02`、`pai_06`、`analysis_ai_trends`、`research_ai`、`research_ai_automation_systems_research` 的写稿都因「模型两次都没有给出可用的 JSON」失败（报告长，要一次返回 400–700 行 JSON；用的是 `minimax-m3.1-flash-free`）。**已修**（见下）。
4. 周计划留下的 `flash` 和 `point_short` 条目也在占写稿名额。

## 还没做

- 逐个看新配方（报告速览、本期头条、行动清单、深度长文等）的稿子质量；8 个成片没有人看过。
- 产出页里按 矩阵 › Job › 报告 的归属和 Job 行的「最新报告已写 N 篇」没有逐个核对。
- 5 个失败/饿着的条目、3 个未开始的，要等问题 1–3 处理后重跑。

## 修复（2026-10-22）

- `core/video/adapt/common.py`：读 JSON 时，对解析不了的回复补上两个元素间漏掉的逗号、去掉多余的结尾逗号（只改解析不了的，能解析的原样不动）；失败时重试 3 次（原来 2 次），重试的温度压到 0.2；回复空白时不把空回复塞回对话；被截断（`finish_reason=length`）时让模型更精简地重新输出。`JSON_TRIES = 3`。
- `web/services/video_autopilot.py` `tick()`：批次按开始时间从老到新处理，先到先得。
- 测试：`test_a_reply_missing_one_comma_or_with_one_extra_is_still_read`、`test_a_json_reply_gets_three_tries_and_the_later_ones_are_calmer`、`test_an_older_batch_gets_the_free_slot_before_a_newer_one`；`test_video_scripts.py` 里两个「审计失败」测试给了三次回复。
- 没有用真实模型重跑过：上面 5 个失败的写稿还没重试，是否真的修好要看重跑。

## AutoVideo 的模型服务可配置（2026-10-22）

出片那一步 402 来自 AutoVideo 自己的 DeepSeek 账户，和 AREC 的模型服务是两回事。AutoVideo 现在可以在它的 `.env` 里选服务（`AUTOVIDEO_LLM_PROVIDER`：`deepseek` 默认、`opencode`、`opendesign`、`custom`，见 autovideo 仓库 `g2/llm_provider.py`，提交 703e41d）。

- 用 Open Design 的 `deepseek-v4-flash` 真实出了一个视频（5 章，191 秒，1920×1080，QA 通过），6 次模型调用，成本上限约 0.066 美元。
- opencode 的免费模型 `minimax-m3.1-flash-free` 没能验证：同一把 key 的请求今天上午起一直返回 401「Upstream request failed: Invalid credential」，之前几次是 200 与 401 交替。AutoVideo 加 opencode 时要带 `x-opencode-session` 头，否则是 400。
- AutoVideo 服务（8011）现在是用环境变量临时指向 Open Design 启动的，没有写进 `.env`，下次重启会回到 DeepSeek。

### 在设置页里选（2026-10-22）

设置页「系统配置」里 AI 模型、审校模型下面多了一张「视频模型」卡：服务商下拉（和上面同一份预设）、模型 ID、免费模型按钮、接口地址、密钥环境变量，按钮是「测试视频模型」「保存并应用」「恢复默认」。

- 保存走 `PUT /api/video/model`（仅管理员，`web/routes/video.py`），AREC 转给 AutoVideo 的 `PUT /api/config/llm`，AutoVideo 把选择写进 `g3/data/llm-settings.json`；出片子进程（`run_g2`）每次启动都读这个文件，所以保存之后新出的片用新模型，正在出的不受影响。优先级：设置页 > 环境变量 > `.env` > 默认（DeepSeek）。
- 密钥不经过浏览器，也不复制：AREC 只发「变量名 + 自己 .env 的路径」，AutoVideo 按名字去那个文件读。密钥缺了，卡片上会提示去「API 密钥」卡填。
- 地址不在 AutoVideo 内置的三家里就按普通 OpenAI 兼容服务处理（不开思考模式，价格上限按保守值），密钥变量留空表示不需要密钥（如本机 ollama）。
- 「测试」只发一个 30 token 的请求，不保存；服务商返回的错误原样显示（例：Open Design 上 `minimax-m3.1-flash-free` 会得到 404 model_not_found）。
- 验证：AutoVideo 在 8012 端口另起一个空数据目录的实例，用 AREC 的 `.env` 里的 Open Design 密钥真实测试，`deepseek-v4-flash` 返回可用、`minimax-m3.1-flash-free` 返回 404。8011 的正式实例没动，需要重启才有这些接口，重启后默认会回到 DeepSeek，要用设置页重新选一次。
- 21 个因 402 挂起的条目还没重新出片：AREC 里它们的出片失败次数已满，不会自己重试。
