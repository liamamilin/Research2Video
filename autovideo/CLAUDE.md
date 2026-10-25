# AutoVideo — Claude 工作说明

@AGENTS.md

上面导入的 `AGENTS.md` 是目录结构、构建/测试命令、代码风格、提交规范和安全要求的唯一来源，这里不重复，只补 Claude 开工和压缩时要用的内容。

## 开工先读（按顺序）
1. `RELEASE_STATUS.md` — 当前阶段、已验收范围、已知限制。
2. `.goal-anchor/STATE.md`（实际状态）和 `.goal-anchor/GOAL.md`（目标基准）。`GOAL.md` 是受保护的基准，不要擅自改；完成一项工作后，把结果同步到 `STATE.md` / `PLAN.md` / `RELEASE_STATUS.md`。
3. 按任务读规格：`03_VIDEO_IR_CONTRACT.md` 是现行数据契约；各功能的验收证据在 `g3/*_ACCEPTANCE.md`。`15–24` 号文档是 MVP 后的方案，不等于授权；`AGENT_FIRST_TASK.md` 是历史任务，不是待办。
4. 先 `git status`：工作树里常有用户未提交的修改，保留原状，不要回退，也不要顺手提交。

## 约定
- 用中文回复；代码标识符和提交信息用英文，沿用历史提交的祈使句风格。
- 验收只认真实证据：真实 MP4、完整解码、QA 结果、日志。离线测试通过不等于生成和渲染没问题。保留既有任务产物和失败的 attempt。
- 真实生成会调用 DeepSeek（花钱）、本机 Draw Things 和 Kokoro，而且很慢。先跑离线 pytest；只有改动涉及生成或渲染时才跑真实任务，并说明理由。不要扩大到 `GOAL.md` 范围约束之外的方向（多供应商、分发沙箱、视频模型、3D 等），扩范围由用户决定。
- `.env` 里有 `DEEPSEEK_API_KEY`：不要打印、不要提交、不要写进文档或回复。

## 别踩的坑
- 仓库约 15 GB，几乎都是被忽略的运行数据和依赖（`g3/data/` 约 12 GB，`g0/vendor/`、`g0/.venv/`、`g0/.hf-cache/`、各处 `node_modules/`）。不要从根目录整体 `grep -r` / `find`；用 `git grep`、`git ls-files`，或显式排除这些目录。
- 只运行注册过的 Remotion 组件；模型输出必须按 Video IR v0.2 和动作注册表校验，绝不执行模型生成的代码。
- 改 `g1/renderer/src/Root.tsx` 后，`g2` 的渲染器源码要重新生成；改前端后要重建并提交 `g3/web/static/app.js` 和 `app.css`（命令见 `AGENTS.md`）。

## 压缩说明 (Compact instructions)
压缩这段对话时，按优先级保留：
1. 用户当前的要求，以及他做过的每个决定（验收口径、范围取舍、否决的方案）。
2. 正在做的功能，和它对应的规格、验收文档路径。
3. 所有证据标识：任务 ID、attempt、样片和报告路径、时长/帧数、QA 结果；跑过的测试命令和通过数量。
4. 创建或修改的文件路径及原因；尚未提交的文件清单；做到一半的事和下一步。
5. 真实生成的次数和费用估算；已知限制和尚未验证的部分。
6. 需要同步到 `STATE.md`、`PLAN.md`、`RELEASE_STATUS.md` 的待办。
省略：中文规格原文、任务日志的长输出、文件列表（磁盘上有）、任何密钥。
