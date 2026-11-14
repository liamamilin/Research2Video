# Repository Guidelines

## Project Structure & Module Organization

This monorepo contains the research console and the video generation engine. Run Git commands from the workspace root; run build and test commands in the relevant module and follow its local `AGENTS.md`.

- `autovideo/`: Chinese narrated video generation. `g1/` provides Video IR validation and trusted Remotion components; `g2/` runs generation; `g3/` contains FastAPI and React (`web/src/`). Contracts and fixtures live in `schemas/` and `examples/`; tests live in `tests/` and module-level `tests/`; `g4/` handles media acceptance.
- `AI_research/Base_CodingCLi/`: Research reports and video workflows. Python logic lives in `core/`, API code in `web/`, React/TypeScript in `ui/src/`, and backend tests in `tests/`. YAML jobs and settings live in `jobs/` and `config/`.

## Build, Test, and Development Commands

Install dependencies using project READMEs; run commands in these directories:

| Directory | Command | Purpose |
| --- | --- | --- |
| `autovideo/` | `python3 -m uvicorn g3.app:app --host 127.0.0.1 --port 8011` | Start the local application. |
| `autovideo/` | `python3 -m pytest tests g4/tests g3/tests g2/tests g1/tests/test_video_ir_v0_2_runtime.py -q` | Run contract and offline regression tests. |
| `AI_research/Base_CodingCLi/` | `python run_web.py serve --reload` | Start the backend with reload. |
| `AI_research/Base_CodingCLi/` | `bash scripts/check.sh` | Compile, test, validate configuration, and check/build the installed UI. |
| `AI_research/Base_CodingCLi/ui/` | `npm run dev` | Start Vite. |
| `AI_research/Base_CodingCLi/ui/` | `npm run build` | Type-check and build production assets. |

Build AutoVideo's frontend with vendored esbuild (see `autovideo/g3/README.md`); commit rebuilt `web/static/app.js` and `app.css`.

## Coding Style & Naming Conventions

Use four-space Python indentation, type annotations, and `snake_case` names. Use two-space TypeScript indentation and `PascalCase` React components. The research UI uses ESLint (`npm run lint`). Run `git diff --check` before submission.

## Testing Guidelines

Use pytest for Python and Vitest/React Testing Library for the research UI (`npm test`). Name Python tests `test_*.py`; colocate UI tests as `*.test.ts` or `*.test.tsx`. Cover changed behavior and failures; no coverage threshold is configured. Isolate databases and files; mock providers and scheduler installation. Renderer changes require real MP4 output, decode checks, logs, and QA evidence.

## Commit & Pull Request Guidelines

Use concise imperative subjects; history also includes `feat(scope): ...` and `fix(scope): ...`. Keep commits focused. PRs should explain behavior, link relevant issues, record validation, and include screenshots or video evidence for visual changes.

## Security & Configuration

Keep credentials in ignored `.env` files; exclude databases, model weights, and generated data. Preserve outputs and attempts. Validate model output; execute only trusted renderer components.

## 当前产品重点与协作偏好

- 现阶段优先把上游内容用视频展示好：画面表达、重点引导、旁白同步、观看节奏及各矩阵的自动化接入。
- 在视频呈现环节，将上游给出的讲稿、数据、计算结果和结论作为确定的输入。例如上游说“价格从20变成25，增加5”，直接设计相应画面。
- 默认不主动扩展出处追溯、事实核实、重新计算、内容审查、防错闸门或人工确认流程；这些约束性工作目前不是产品重点。用户明确要求时再处理。
- 方案讨论和开发验证围绕所请求的功能、实际画面效果及自动流程展开，避免将任务范围转向额外的内容校验。
- 视频打磨坚持成片目标：让观众更容易看清重点、理解内容、跟上旁白和节奏。组件、图形形式、布局算法与测试通过率只是手段，不能代替实际观看效果。
- 先从真实视频找观看问题，再用相同内容和旁白比较表达办法。效果没有改善、引入新的观看负担或实现过于复杂时，停止该办法并换方向；允许使用更简单的画面，不要求把所有信息放在同一屏。
- 只接入成片证据支持的改进，并用不同内容结构验证。保留未成功的尝试和取舍依据，不为当前几个样例持续增加特例，也不把局部可读性改善宣称为整片质量或涨粉已验证。

## 视频效果任务的执行与完成判定

以下约束用于 Agent 的开发过程，不给产品新增审核、人工确认或内容校验流程。由 Agent 自行执行，不要求用户反复提醒或批准常规试验。

- **开工锁定结果。** 在本轮既有任务记录或验收报告中，用简短条目记录：真实日常视频及问题时间段、观众受到的具体影响、正常生产入口及实际启用配置、该入口自动可得的输入、本轮成功证据。目标写观看效果，不把实现某组件、算法或素材系统写成最终目标；不得为方便宣布完成而降低验收范围。
- **先核对生产可达性。** 区分代码存在、配置启用、日常入口执行和实际成片受益。开工时检查依赖是否真实可用；没有自动来源的手选图片、手写分镜、隔离渲染注入只能算实验输入。自动找素材不是默认必做项，先比较现有输入能支持的更简单表达。
- **限制单条路线投入。** 每个候选办法开始前记录预期观看收益、最小对照和本次试验的停止点。同一办法连续两轮没有明确收益，或为继续它必须新增原计划外系统时，先停止扩展，比较简化、换表达或放弃；有新证据才继续，并记录理由。允许目标暂未达成，不以已投入工作量为理由延续路线。
- **用证据触发纠偏。** 每轮真实视频对照后、扩大开发范围前以及恢复长任务时，核对：改善了哪段观看体验、正常入口能否自动用上、是否引入退步、有没有更简单办法。进展只需说明发现、证据和采用/放弃决定；不以测试数量、动效数量或组件完成度替代收益。原方案失效时主动换路，不把选择技术手段的责任交还用户。
- **完成必须有端到端证据。** 从日常使用的业务入口和实际生产配置生成，输入仅使用该流程本来能自动取得的内容；不得靠额外手工准备素材、改讲稿或分镜通过验收。可以在隔离环境运行以避免影响发布，但不得绕过关键生产步骤。至少包括一个未参与调试、结构不同的真实选题，保留前后视频、运行记录及人工介入清单；原声片段用于局部对照，整片主张必须检查整片。
- **实验成果与任务完成分开。** 分别标明“表达实验”“已接入但端到端未验证”“生产入口已验证”。服务不可用或只有手工/保存计划重放时，保留有用成果并说明未验证部分，不将效果目标标记完成。关闭的能力不得描述为日常自动能力；结论被新证据推翻时同步更正验收报告，不仅在聊天中收回。

这些是持久执行约定，不是能自动判定视频好坏的技术锁；文件和测试只能提供证据，最终仍需核对实际成片。优先复用现有任务记录与验收目录，不为落实约定另建复杂管理系统。
