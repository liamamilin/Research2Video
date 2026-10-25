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
