# 数据分析（设计，2026-10-31 修订，还没做）

放在侧栏「高级」里，新增一个页面「数据分析」，两个 tab：

- **用量与耗时**（新做）：一条视频从调研到成片，每个环节花了多长时间、用了哪个模型、多少 token，可选地折算成钱。
- **视频数据**（从「平台管理」搬过来）：哔哩哔哩的播放、点赞、收藏等，现有 `VideoAnalytics` 组件整体搬，不改逻辑。

10-22 的旧稿偏「看板 + 告警」，这版改为先把记录层做对；搜索失败告警、预算联动、平台数据与成本的对照都不在第一期。

## 1. 现状：数据有，但散在四处

| 环节 | 现在记在哪 | 缺什么 |
|---|---|---|
| 研究 Job | `state/history/*.jsonl`，每次运行一条合计（`usage`、`duration_seconds`） | 没有分环节；搜索只记成功的次数 |
| 写稿、审校、选题池 | 文稿版本 meta 的 `usage` / `audit_usage` / 计划 `usage`，只有合计 | 没有耗时 |
| 出片（AutoVideo） | 每条成片目录里的 `model-cost.json`（每次调用的 token、耗时、模型、单价快照）、`execution-log.txt`（每步耗时）、`tts-metrics.json` | AREC 没读 |
| 助手、问答 | `state/history/__usage__.jsonl` | 与视频链路无关 |

另外 `web/services/theme_usage.py` 在读取时临时拼接一部分，没有统一存储。

共同的缺口：没有「一次调用一行」的记录；没有「属于哪条视频、哪条链路」的编号；没有每个环节的结束时间；失败和模型兜底没有留痕。

## 2. 记录层：一个记录点 + 一个自动带上归属的上下文

不去改全项目约 19 处 `LLMClient(...)` 调用点。

1. **Token**：挂在 `LLMClient._record_usage`。再补两条路径：接口没返回用量时的估算（标记 `estimated`）、模型兜底切换（记 `fell_back_from`）。
2. **耗时和归属**：`track(stage=..., script_id=..., ...)` 上下文管理器。进入时把归属放进 `contextvars`，退出时写一条环节记录（开始、结束、耗时、成败）。块里任何 LLM 或搜索调用自动带上同一份归属。
3. **只包入口**：调研、选题池、改写、审校、文章直出、出片提交与完成。
4. **搜索**：`SearchClient` 同样在一处记，服务商、次数、成败。只记，不告警、不拦截。
5. **出片只摄入不插桩**：出片完成时读一次 `model-cost.json`、`execution-log.txt`、`generation-schedule.json`、`tts-metrics.json`。每章的调用名（`c3-explanation`、`chapter-c5-diagram`）归并为：叙述形式、讲解、图表、信息、卡片编辑，否则按环节统计会碎成几十行。

注意：

- `contextvars` 不会自动进入新线程。`web/services/video_workflow.py` 里写稿、重写、改稿都是 `threading.Thread`，所以 `track` 要写在线程函数里面；出片和发布的工作进程显式传编号。
- 写库一律 best effort：捕获异常、写日志、继续，不能影响主流程。
- 「等人定稿」是独立环节，不计入生产耗时。

## 3. 数据表

`state/telemetry.db`（SQLite，WAL，只追加）。现有 `__usage__.jsonl`、`history` 不动，「预算」页继续读它们，新库稳定后再决定是否切换。

**spans**（环节）：`id`、`script_id`、`render_id`、`stage`（调研 / 选题池 / 写稿 / 审校 / 等定稿 / 排队 / 分镜 / TTS / 编译 / 渲染 / 校验 / 打包）、`start`、`end`、`status`。

**calls**（一次调用一行）：`id`、`ts`、`kind`（llm / search）、`provider`、`model`、`prompt_tokens`、`completion_tokens`、`reasoning_tokens`、`cache_read_tokens`、`estimated`、`error_code`、`fell_back_from`、`reported_cost_usd`（服务商返回了才有）、`duration_s`、`span_id`。

两张表共有的**归属字段**，用来比较不同链路：`matrix`、`column_key`、`recipe`、出片 `form`（如 `information_motion`）、写稿路径（直出 / 改写）、`script_id`、`render_id`、`batch_id`、`item_id`、`job`、`run_ts`。

**历史回填**（一次性）：`history/*.jsonl`、253 份 `model-cost.json`、版本 meta 里已有的 usage。每行带 `source_key`，保证回填与实时记录不重复计算。

## 4. 价格与折算

记的是**模型和 token**；钱是可选的折算，页面上有开关，并且每个数字标明来源：

| 来源 | 说明 |
|---|---|
| 服务商返回 | 研究侧历史里已有 `cost_usd`（约 1042 条运行里有 153 条）；这是实际花费，优先用 |
| 成片里的单价快照 | `model-cost.json` 的 `pricing_snapshot`，是调用当时的单价 |
| 价格表 | `config/pricing.yaml`，按「服务商 + 模型」记输入 / 输出 / 缓存命中每百万 token 的单价，带 `effective_from`，历史按当时价格算 |
| 未定价 | 价格表里没有就显示「未定价」，只给 token，不猜 |

价格取决于「服务商 + 模型」，不只是模型名：同一个 `deepseek-v4.1-flash`，在 opencode 上快照单价是 0，别的服务商可能收费。目前实际在用的是 `deepseek-v4.1-flash`、`deepseek-flash`、`longcat-2.5-preview-free`、`minimax-m3.1-flash-free`、`minimax-m3`。第一期实现时，公开标价能查到的我查并在表里写明来源和日期；中转站、预览版查不到的标「未定价」，由你补。

折算金额在读取时按价格表算，不写死在事件里；改价格即可重算。

## 5. 页面

**用量与耗时 tab**：

- 范围选择（时间、矩阵、栏目），顶部合计：token（输入 / 输出）、调用次数、耗时、折算金额（可选）。
- **按视频**：每条视频一行，展开看各环节的耗时、模型、token；等定稿的时间单列。
- **按链路对比**：按栏目 / 形式 / 写稿路径的环节耗时中位数和 token 均值。
- **按模型**：调用次数、token、失败与兜底次数。

**视频数据 tab**：搬自「平台管理」，需要一份账号连接状态来判断是否显示；「平台管理」里的 `视频数据` tab 去掉，`/platforms?tab=data` 改为跳到新页面。

## 6. 分期

1. **记录层**：库、`track`、LLM / 搜索挂钩、出片摄入、历史回填、价格表、只读 API。
2. **接入入口**：调研、写稿、审校、选题池、出片的入口补上 `track`。
3. **页面**：「数据分析」页，用量与耗时 tab，搬入视频数据 tab。
4. **之后**：成本与播放的对照、搜索失败提示、预算页切换到新库。

## 7. 已定与待定

已定：放「高级」；记模型和 token；金额可选折算并标明来源；视频数据搬进同一页。

待定（不影响第一期）：看板对所有登录用户可见，还是仅管理员；事件保留多久。
