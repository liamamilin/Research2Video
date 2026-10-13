# AutoVideo｜最终开发文档入口

**发布状态：V0.3-DESIGN-FINAL（设计规范冻结）**  
**产品目标：V0.1 MVP** · **现行 Video IR：v0.2** · **当前工程阶段：G0 尚未执行验收**

## 你拿到的是什么

这是一套可交给编码 Agent 的完整规格，包含 25 篇编号文档、JSON Schema、组件注册表、示例、测试和 MVP 后的演进计划。**它不是已完成的应用，也不是视频样片集。**

## 如何阅读

1. 首先读 `README.md`、`AGENTS.md`、`RELEASE_STATUS.md`。
2. 当前研发只需遵守 `00–14`，其中 `01`（产品边界）、`03`（数据契约）、`08`（质量验收）、`09`（阶段计划）和 `10`（Agent 指令）最重要。
3. `15–24` 是 MVP 之后的演进方案，用于理解长期方向，**不授权提前开发**。
4. `schemas/video-ir-v0.2.schema.json` 与 `schemas/component-registry-v0.2.json` 是 G1 起的现行设计契约；旧 v0.1 文件仅作历史存档。
5. 从 `AGENT_FIRST_TASK.md` 启动 G0，不因文档完整而跳过真实技术验证。

## 文档优先级与决策规则

- 当前产品范围：`01_MVP_REQUIREMENTS.md`。
- 当前 Video IR：`03_VIDEO_IR_CONTRACT.md`、Schema 与 Registry。
- 实施顺序：`09_IMPLEMENTATION_PLAN.md`；只有用户显式授权才能进入下一阶段。
- 验收证据：`08_QA_AND_ACCEPTANCE.md` 及对应阶段测试矩阵。
- 决策版本：`11_DECISION_LOG.md`（FROZEN-DESIGN 意味着仅设计冻结）。
- 遇到冲突：停止冲突部分，列出差异并请求决定，不得自主扩大范围。

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
