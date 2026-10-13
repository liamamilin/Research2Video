# AutoVideo｜最终开发文档入口

**文档发布：V0.3-DESIGN-FINAL**

**产品目标：V0.1 MVP** · **现行 Video IR：v0.2** · **当前状态：MVP 已验收；本地产品功能迭代中**

## 你拿到的是什么

本仓库包含原始产品规格，以及 G0–G4 的真实验收产物、已实现应用和后续研究记录。MVP 已按本地单机 10–20 秒范围完成并由用户验收；当前状态和限制以 `RELEASE_STATUS.md` 及对应阶段报告为准。

## 如何阅读

1. 先读 `RELEASE_STATUS.md`、`README.md` 和 `AGENTS.md`，确认当前阶段、授权和已知限制。
2. 按具体任务阅读 `00–14` 中对应规格；Video IR v0.2、组件 schema 和 Registry 是现行数据契约，v0.1 仅作历史存档。
3. `15–24` 记录 MVP 后的方案与审查，不构成自动授权；按用户当前明确指示推进。
4. `AGENT_FIRST_TASK.md` 是初始化时的 G0 首轮任务记录，G0 已执行，不要重复将其当作当前待办。

## 文档优先级与决策规则

- 当前产品范围：`01_MVP_REQUIREMENTS.md`。
- 当前 Video IR：`03_VIDEO_IR_CONTRACT.md`、Schema 与 Registry。
- 实施顺序：`09_IMPLEMENTATION_PLAN.md`；只有用户显式授权才能进入下一阶段。
- 验收证据：`08_QA_AND_ACCEPTANCE.md` 及对应阶段测试矩阵。
- 决策版本：`11_DECISION_LOG.md` 记录原设计冻结；当前工程完成度以阶段报告和 `RELEASE_STATUS.md` 为准。
- 遇到状态或范围冲突：以最新阶段验收报告及用户明确授权为准，并同步修正文档，避免把历史首轮指令误作当前状态。

## 本轮无需构建的功能

AI 视频大模型、自由 LLM 代码运行、复杂 3D、通用剪辑器、社交发布、多用户、分布式渲染、自动化云部署。未来方向在 `15–24`。

## 本包可验证的范围

在安装 `pytest`、`jsonschema` 后，在本目录运行：

```bash
python -m pytest tests -q
```

测试只检验**规范与示例一致性**，不等于 G0–G4 工程验收；真实成片必须通过 Remotion/TTS/ffprobe 的实际操作证明。

## 研发阶段

```text
G0 (真实 10–20s 有声技术样片)
  ↓ 用户批准
G1 (Video IR + 受控渲染器)
  ↓ 用户批准
G2 (真实 LLM + TTS 自主出片)
  ↓ 用户批准
G3 (Web 产品化 + 任务/恢复)
  ↓ 用户批准
G4 (质量验收 + MVP 基线)
  ↓ 用户批准
M0 (冻结真实基准)
  ↓ 用户批准
H1–H4 (视觉增强 → 混合媒介 → 自主 QA → 工作室)
```
