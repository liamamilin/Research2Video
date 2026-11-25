# 全项目代码审查（2026-11-15）

7 个只读审查子代理按模块分读剩余代码，主会话逐条回到代码与线上数据核实。
标注：**已核实** = 主会话读过代码路径（并在可能时查过数据）；**子代理判断** = 主会话未逐行复核，按子代理的追踪结论列出；**降级** = 核实后发现影响很小或尚未发生。

## 高 — 账号与助手安全

1. **已撤销的会话仍能创建永久 API 令牌**（已核实）
   `web/auth/routes.py:373` `create_token` 只用 `decode_token` 校验签名与有效期，未检查 `kind=="access"`、`is_revoked(jti)`、`matches_browser_session`（`web/deps.py` 的 `current_user` 都会检查）。拿到一个 refresh 令牌（"记住我" 30 天）的人，在注销、改密、撤销会话之后仍可生成新的 `air_` 令牌，撤销会话也收不回。同样写法见 :229 改密、:285 列会话、:311 撤销会话、:403 撤销令牌。
2. **助手可把 SMTP 登录密码发给任意服务器**（已核实）
   `assistant_safety.validate_patch` 不限制 `notifications.email` 的 `smtp_host`/`smtp_password_env`/`to_addrs`/`use_ssl`（`core/system_schema.py:66`）。被网页内容诱导时，可设为外部主机并让 `smtp_password_env` 指向任意环境变量（如 OPENCODE_API_KEY），再调用测试通知，`core/notify.py` 会用该值登录外部主机。与已修的 base_url 问题同类。
3. **助手可让研究报告写到磁盘任意位置**（已核实）
   `web/services/yaml_io.py:107` 输出校验以 job YAML 自带的 `runtime.output_root` 为根，引擎也认它（`core/engine.py:719-752`）。经助手 `operate_application` 改 job YAML，可覆盖项目里的 AGENTS.md/CLAUDE.md 等 `.md` 文件。
4. **助手可把服务器日志指向任意文件**（已核实）
   `logging.file` 未限制路径（`web/server.py:104-112`），配合重启工具会向任意文件追加日志、轮转时还会改名截断。
5. **助手可把报告发邮件/分享给模型指定的收件人**（子代理判断）
   `assistant_tools/reports.py` `distribute` 的 `recipients` 只要求含 `@`；`share` 生成公开链接并回给模型。

## 中 — 结果错误或成片质量

6. **"已报道"事件记忆丢失换源再报的新闻**（已核实，数据中 7 条）
   `core/events.py:454` 只按原始记录自己的 `last_seen` 过滤；换来源再报只更新重复来源行。原始记录超过 7 天后，事件从"已报道"清单消失，研究报告会把它当新内容重写。
7. **月度预算保护永远不会触发**（已核实）
   `core/budget.py` 只按 `ai.pricing` 计算模型费用，当前配置为 0，且不计搜索（Exa）费用；`monthly_usd_limit: 20` 实际不起作用。
8. **包装阶段即使不加片头片尾也重编码一次**（子代理统计：最近 200 条中 167 条）
   `g2/package_stage.py:274-340` 在无片头/片尾/来源卡时仍做一次 libx264 全片编码；成片经过 Remotion → 章节导航 → 包装 → 品牌 共 4 次有损编码，每次多 6–15 秒。
9. **AutoVideo 进度页显示最早一次尝试**（已核实，14 个多次尝试的任务受影响）
   `g3/web/src/main.tsx:425` 取 `attempts[length-1]`，而 `g3/store.py:695` 按 `attempt_no DESC` 返回，取到的是第一次尝试。
10. **研究前端 5 处时间显示早 8 小时**（已核实）
    `components/ThemeWorkbench.tsx:28,29,279,287`、`pages/Topics.tsx:418` 传入 `toISOString()`（UTC），`shortTime` 不做时区换算。
11. **一个坏配置会让所有定时任务停止入队**（子代理判断）
    `core/schedule_store.py:306-329` 在同一个事务里为所有到期计划调用 `production_policy.prepare()`；任一 job YAML 解析失败会回滚全部，之后每轮都失败。
12. **一份读不了的报告会停掉所有自动出片批次**（子代理判断）
    `core/video/auto.py:46-69` 的 `report_has_content` 无异常处理，`fire_auto` 在循环外，非 UTF-8 或被替换的报告会让整轮对账抛错。
13. **研究前端：动作刚完成就被旧的轮询结果覆盖**（子代理判断）
    `pages/VideoScriptView.tsx:123-130`：定稿/出片后，进行中的轮询可能把状态写回动作前，按钮重新可点，最长 30 秒。`lib/use-page-resource.ts:13` 在轮询进行中会丢弃动作后的刷新。
14. **研究前端：连点"加一套配置"会建两套生产配置**（子代理判断）
    `components/ProductionConfigs.tsx:353-358` 无防重；若两套都开自动制作，同一报告会出两份视频。

## 低 / 潜在

15. 报告被截断仍记为成功（`core/research.py:469-473` 轮数上限、`:642-644` pipeline 模式不检查 `finish_reason`/`report_problem`）——**降级**：14 天 3821 次运行未触发。
16. `read_webpage` 每次走付费 Exa 且不计数（`core/research.py:479-489`）——**降级**：14 天仅 20 次。
17. Exa 一次搜索多条查询只计 1 次，中途失败丢弃已付费结果（`core/search.py`）。
18. 截断重试时批次空间重复扣减提示长度，可能误报"无法分批"（`core/research.py:313-325`）。
19. `report_problem` 前 400 字含 "502/503/rate limit" 等会误判报告为错误（`core/research.py:64-92`）。
20. 每次读事件记忆都会改写 186 行（`core/events.py:98-146`），增加写锁争用。
21. 生产准入按报告而非按视频计数（`core/video/production_admission.py:130-144`）——当前全局 `daily_limit: null`，未生效。
22. 同一内容栏目两套 episode 配置可能各出一期（`core/video/auto.py:83-101`）——目前无此配置。
23. 写稿去掉问候后可能留下空段落（`core/video/writer.py:279-298`）。
24. 节目条目按标题子串匹配，"Claude" 可能匹配到 "Claude Code 发布"（`core/video/episodes.py:213-256`）。
25. 取消的批次对应的报告版本不再自动重试（`core/video/auto.py:90-92`）——可能是有意设计。
26. 暂停的流水线 LaunchAgent 改时间后会被重新加载；暂停在重启后失效；同秒删除两个会丢一个恢复项（`web/services/launchd_agents.py`）——两个流水线 plist 当前未安装。
27. 内容质检只比对音频前缀，音轨提前结束也能通过（`g2/run_g2.py:1061-1077`）——300 条成片未出现。
28. 换音色修订失败时可能把旧音色存进新音色的缓存（`g2/run_g2.py:687-693`）——当前只有一个 VoxCPM 音色，不可达。
29. g3：修订接口把配置错误报成 404"任务不存在"或 500；存储盘未连接时只报"无法创建工作区"；422 校验错误显示为"请求失败"（`g3/app.py:674-1025`、`g3/web/src/main.tsx:54`）。
30. g3：任务页轮询在任务结束后不停止（`main.tsx:220-227`）；草稿时长不恢复（`:129`）；`mark_completed` 不校验尝试编号（`store.py:579`）。
31. g1：explanation 预设下校验允许没有 layout 的 flow_diagram，渲染会崩（`g1/video_ir.py:218`）——生产路径总会附带 layout。
32. 助手维护等待无超时，挂住的任务会一直拒绝新的手动运行（`assistant_maintenance.py`）。
33. 研究前端：按视频账单切换时间范围不重置分页（`components/UsageAnalytics.tsx:88-114`）；任务页结束时多拉一次日志（`pages/JobDetail.tsx:261-265`）。

## 审查覆盖说明

子代理各自列出了读完与未读的文件；仍未读或只读了部分的有：`core/` 中的 social、experiments、theme_*、notify、health、tracking 等，`core/video/` 的 columns、adapt、audit、visual_* 等，`web/` 的 video_workflow 大部分、routes/video、config、matrices、production、pipeline、indexer，前端的部分页面，g2 的 production_plan、visual_language、pronunciation。
