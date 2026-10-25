# 02｜系统架构

## 1. 组件边界

```text
React UI
   | REST/stream or polling
FastAPI API + Orchestrator
   |-- SQLite (jobs, stages, assets, metrics)
   |-- Local Job Workspace (/data/jobs/<job_id>)
   |-- LLM Provider Adapter  ---> cloud/local LLM
   |-- TTS Provider Adapter  ---> cloud/local TTS
   |-- Alignment Adapter     ---> provider aligner/ASR
   |-- Quality Rules         ---> report
   `-- Render Worker Adapter --> Node.js Remotion Worker
                                     `-- ffmpeg / ffprobe
```

- **FastAPI**：任务创建、状态管理、阶段编排、资产元数据、失败恢复、对外接口。不要执行实际视频逐帧渲染。
- **Remotion/Node**：将已经验证的 Video IR 映射为现有动画组件，预览帧/合成/导出。无权改写原始用户文本。
- **LLM/TTS 适配器**：为外部依赖提供统一请求、结果、超时、错误类型。
- **SQLite**：本地单机状态源；文件存储大素材。以后扩展多 worker 时再讨论队列/Redis。

## 2. 建议目录（可依据现有工程调整）

```text
apps/
  web/                 # React/TypeScript UI
  api/                 # Python FastAPI 与任务流程
  renderer/            # Node/Remotion Composition 和组件
packages/
  contracts/           # JSON Schema / TypeScript 类型
  fixtures/            # 测试文本、可授权音频、期待报告
data/                   # Git 忽略，job 工作区
  jobs/<uuid>/
    input.json
    script.json
    storyboard.json
    video-ir.json
    assets/
    render/
    qa/
    manifest.json
```

## 3. 状态与可靠性

- `jobs` 表记录 ID、输入摘要、状态、active_stage、version、failure_code、created_at、updated_at、attempt_count。
- `stages` 记录开始结束时间、输入 hash、产出 artifact URI、执行状态和错误摘要。
- `assets` 记录逻辑 ID、相对路径、文件 hash、类型、来源、许可证或 provider 元数据。
- 单 worker 通过 SQLite 事务 claim 待执行任务；不能用“先查询再写入”作为并发保护。
- 所有产出先写 `*.tmp`，验证成功再原子替换；manifest 最后写出。
- 任务重跑为新 `attempt`，不直接覆盖已完成制品；可基于输入 hash 复用阶段成果。

## 4. 系统内部标准契约

- `Video IR v0.2`（参见 `03_VIDEO_IR_CONTRACT.md`）为新实现的正式接口。新实现不得混用历史 v0.1 Schema。
- 所有绝对路径由后端生成，不允许外部输入任意文件系统路径。
- 由服务端检验 IR 中的 `component` 与 `asset_id` 属于白名单/已存在资产。
- 数据字段变动需要 schema 版本变更、迁移或兼容说明，禁止悄悄更名。

## 5. 安全边界

- LLM 只能输出 JSON 数据，不能直接写仓库、执行 shell、读取密钥。
- 禁止把任意用户 URL 传给 renderer；V0.1 仅使用服务端生成/批准的本地素材。
- 远端请求要限制超时、重试、尺寸和响应大小；响应不得保存明文 API Key。
- FFmpeg/Remotion 子进程使用固定命令模板、固定 workspace、参数数组和硬超时。
- 控制渲染并行度，预防资源耗尽；密钥仅在后端环境变量。
- 独立测试工作区，禁止测试触碰真实生产素材目录。

## 6. 可观测性

- 每次运行生成 `trace_id`、结构化日志、阶段耗时、模型信息、token/音频长度/估算费用（可得时）。
- 一键导出完整 `manifest.json`，但必须脱敏，不能包含 API Key 和用户原文之外的隐私。
- 运行日志不是直接等价于 QA 成功证据：需要实际可打开的文件和 ffprobe/截图报告。

## 7. 视觉动作受控执行边界（设计修订）

- 新增 `Component Registry`：每个组件限定允许的动作、target 与 props Schema。
- `Video IR Compiler` 在服务端把“绑定旁白段的 Beat”解析为确切帧位置，renderer 只接收验证过的执行数据。
- 新的动作仍是数据，不能以“需要更好视觉效果”为由允许 LLM 输出可执行源码；后续探索自由代码仅限另行审批的实验沙箱。
