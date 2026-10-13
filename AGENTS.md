# AutoVideo Agent Rules (Design documents V0.3; target product V0.1)

1. Read `README.md` and `AGENTS.md`, then documents `00` through `14` before changing MVP code; `15`–`24` are future research/reference only.
2. Only start **G0 technical verification** unless the user explicitly approves a later gate.
3. Audit the current working tree; protect existing user code, data, and unrelated projects.
4. Do not run LLM-authored JS/TS or shell directly. In V0.1, LLM proposes JSON; renderer executes allowlisted components.
5. A green mock suite is not evidence of real model generation. Report real media artifacts and verified media streams.
6. For each milestone report: PASS/PARTIAL/FAIL, changed files, commands executed, tests, artifact paths, risks, and commit.
7. Stop after G0, report results, and request user approval before G1.

See `10_AGENT_EXECUTION_BRIEF.md` for detailed instructions.

- 数据契约采用 **Video IR v0.2**，参阅 `schemas/video-ir-v0.2.schema.json`、组件子 schemas、动作 registry。`schemas/video-ir-v0.1.schema.json` 仅存档。
- G0 仅做真实音画样片验证；不要因新设计文档存在就跳到 G1。G1 应遵循 `13_G1_TEST_MATRIX.md`，真实验证 Beat 前后画面变化。

## Future-plan guard

- `15`–`24` are planning documents only. Their presence does not authorize M0/H1/H2/H3/H4 or any new provider integration, schema changes, video model integration, generated-code execution, or multi-user product work.
- Continue G0 first. Follow `09_IMPLEMENTATION_PLAN.md` for G1–G4 only upon explicit user approval.
- After G4 is actually accepted, require `M0_BASELINE.md` and explicit authorization before H1.
- Never treat product versions (V0.2/V0.3/V0.4/V1.0), design-document V0.3, and Video IR v0.2 as interchangeable.
