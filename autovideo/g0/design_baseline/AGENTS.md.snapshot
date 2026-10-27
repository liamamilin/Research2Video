# Repository Guidelines

## Project Structure & Module Organization

AutoVideo generates Chinese narrated knowledge videos. Read `RELEASE_STATUS.md` for current scope and `README.md` for design references.

- `g3/`: FastAPI API, SQLite store, worker, and React sources in `web/src/`; committed bundles live in `web/static/`.
- `g1/`: Video IR validation and trusted Remotion components. `g2/` contains the generation pipeline and Draw Things client; its renderer source is regenerated from `g1/renderer/src/Root.tsx`.
- `schemas/` and `examples/`: Video IR v0.2 contracts, component schemas, registry, and examples.
- `g4/`: Golden Cases and media QA. `g0/` holds dependency setup and local media tools; `m0/` and `h1/` contain baseline and experiment records.
- Tests live in `tests/` and each module's `tests/`; runtime task assets and outputs live under ignored `g3/data/`.

## Build, Test, and Development Commands

Set up dependencies using `g0/README.md` and `g3/README.md`. Run from the repository root unless indicated.

```sh
python3 -m uvicorn g3.app:app --host 127.0.0.1 --port 8011
python3 -m pytest g4/tests g3/tests g2/tests g1/tests/test_video_ir_v0_2_runtime.py -q
python3 g0/verify_design_snapshot.py
```

These start the local app, run offline regressions, and verify the document package in an isolated snapshot. Build the UI from `g3/web/`:

```sh
NODE_PATH="../../g0/vendor/remotion-audiogram/node_modules" \
  ../../g0/vendor/remotion-audiogram/node_modules/.bin/esbuild src/main.tsx \
  --bundle --minify --format=esm --jsx=automatic --outdir=static --entry-names=app
```

Commit rebuilt `app.js` and `app.css` with frontend changes.

## Coding Style & Naming Conventions

Use four-space Python indentation and two-space TypeScript indentation. Use `snake_case` for Python functions and modules, `PascalCase` for React components, and `test_*.py` for tests. Match surrounding style; no formatter or linter is configured. Run `git diff --check` before submission.

## Testing Guidelines

Use pytest and add regression coverage for changed behavior, including failure paths. Generation or renderer changes also require a real MP4, media decode checks, logs, and QA results; offline tests do not prove model generation. Preserve existing task outputs and failed attempts.

## Commit & Pull Request Guidelines

Use concise imperative subjects, following history: `Add completed-job scene previews` or `Soften generated scene backgrounds`. Keep commits focused. PRs should describe resulting behavior, link relevant issues, list validation commands/results, and include screenshots or media evidence when applicable.

## Security & Configuration

Keep `DEEPSEEK_API_KEY` in ignored `.env` with mode `0600`. Exclude keys, model weights, binaries, and runtime data from Git. Bind services locally. Validate model JSON against Video IR v0.2 and the action registry; execute only trusted renderer components.
