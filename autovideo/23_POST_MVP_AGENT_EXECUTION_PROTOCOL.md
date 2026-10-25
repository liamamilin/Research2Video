# 23｜MVP 后 Agent 执行协议与阶段授权

> **禁止误读**：本文件为未来阶段的工作协议；**不是“立即开始 H1”指令**。当前仍受 `AGENTS.md` 和 `10_AGENT_EXECUTION_BRIEF.md` 的 G0 起步约束。

## 1. 权限顺序

1. 用户明确授权的当轮阶段工作。
2. `AGENTS.md` 与 `10_AGENT_EXECUTION_BRIEF.md` 的安全与保护约束。
3. `01_MVP_REQUIREMENTS.md` 和 `09_IMPLEMENTATION_PLAN.md` 的 G0–G4 产品边界。
4. 新增 `15–23` 号未来设计文档只用于规划；M0/H1–H4 均不属于自动可执行任务。

阶段未经授权，不得以“文档在仓库里”为理由实施。

## 2. 任一阶段启动前

- 证明前置 Gate 已通过：G4→M0，M0→H1，H1→H2，H2→H3，H3→H4。
- 读取真实项目状态、未提交修改、已有测试/资产和有效凭据；先作 checkpoint，不破坏原项目。
- 写一页 `STAGE_PLAN.md`：目标假设、范围、要改变的模块、风险/权利、真实测试方案、费用预算、退出标准。
- 标记不属于本阶段的工作，不得因“顺手”提前开发。
- 若必须变更 schema 或 renderer 安全边界，先写 RFC，用户批准后才实施。

## 3. 实施纪律

- 按 “一条真实可用路径 → 负例/边界测试 → 局部质量评估 → 小步扩展” 的顺序开发。
- 不允许 Mock 成功代替真实 LLM、TTS、图片、视频或渲染验收。
- 任何模型生成代码仅在具备已实测安全隔离的实验 sandbox 内运行；不得在主仓库直接执行。
- 供应商接入时记录版本、成本、权限和异常处理，不泄漏密钥。
- 一旦跨阶段扩张、预算越界或需要访问外部生产数据，停下并报告，不自作主张。

## 4. 每阶段报告格式

```text
Stage: M0 | H1 | H2 | H3 | H4
Status: PASS | PARTIAL | FAIL | STOP/PIVOT
Objective / Hypothesis:
Changes:
Measured evidence: (actual MP4/frames/audio, reports, commands)
Baseline comparison: (including sample size and failures)
Cost / Latency / Provider versions:
Security / rights review:
Unverified assumptions:
Rollback point / Commit:
Recommendation: proceed / repair / stop / RFC
```

没有真实测量值就写 `NOT MEASURED`；禁止推测性填入 PASS。

## 5. 架构 RFC 最低字段

- **Proposal**：什么变化，用户得到什么具体差异？
- **Evidence**：哪个样片/失败/评价支持变化？
- **Scope**：哪些服务、Schema、测试与数据受影响？
- **Alternatives**：不做、复用现有组件、更小修复的比较。
- **Security & Licensing**：可执行代码、模型输出、权限、密钥、素材权利。
- **Compatibility**：旧作品如何消费，schema 如何迁移或拒绝。
- **Experiment**：真实样片与负例如何验证。
- **Rollback & Exit**：退回既有稳定版本的具体办法。

## 6. 交接给 Agent 的后续指令模板（未来才使用）

```
本轮只执行 AutoVideo <阶段名>。在读完现有阶段报告、
README、AGENTS.md、15–23 的相关文档后：
1. 检查前置 Gate 证据和当前工作区；发现前置未过，停止实施。
2. 提交 STAGE_PLAN：范围、验收、风险、预算、恢复点。
3. 在授权范围内交付可运行代码及真实样片。
4. 输出独立阶段报告，列真实测试与失败，不扩张到下一阶段。
```

## 7. 停止条件

任何关键事实未经核验、无法保证数据隔离、模型授权不清楚、测试可能写入生产数据、供应商成本未知且超预算、跨 Gate 需求出现时，都应停止危险动作并交付明确阻断项。确保**项目不被后续实验搞坏**优先于赶进度。
