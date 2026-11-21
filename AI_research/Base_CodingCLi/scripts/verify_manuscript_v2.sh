#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ -x ".venv/bin/python" ]]; then
  PYTHON=".venv/bin/python"
elif [[ -x "venv/bin/python" ]]; then
  PYTHON="venv/bin/python"
elif command -v python >/dev/null 2>&1; then
  PYTHON="python"
else
  PYTHON="python3"
fi

echo "[verify_manuscript_v2] repo=$ROOT_DIR python=$PYTHON"

"$PYTHON" - <<'PY'
import time
from core.video import store
from web.services import video_workflow

CASES = [
    (
        "ai-non_tech-deep_dive-long",
        "output/flash/2026-11-21_ai_flash_discovery_2.md",
        "Anthropic 数学家与 Claude 合作宣称证明黎曼猜想：AI 参与百年数学难题",
        "monitoring/ai_flash_discovery",
    ),
    (
        "ai-developer-comparison-long",
        "output/flash/2026-11-21_ai_flash_discovery_2.md",
        "Claude 新模型层级与 API 定价：Fable 5.2 / Opus 5.5 / Sonnet 5.5 及缓存定价",
        "monitoring/ai_flash_discovery",
    ),
    (
        "theme_research",
        "output/history/2026-11-21_history_daily.md",
        "罗伯特·斯莫尔斯：1862年在查尔斯顿港夺取邦联运输船CSS Planter的逃亡行动",
        "history/history_daily",
    ),
    (
        "theme_research",
        "output/history/2026-11-21_history_daily.md",
        "湖北云梦老胡墩墓地：秦汉时期 600 余枚竹简与从未见过的漆器形制",
        "history/history_daily",
    ),
]

for idx, (col, report, title, job) in enumerate(CASES, 1):
    t0 = time.monotonic()
    print(f"\n[{idx}/{len(CASES)}] 开始写稿：{col} | {title[:32]}", flush=True)
    topic = {
        "title": title,
        "from_report": report,
        "from_item": title,
        "from_item_title": title,
        "test_sample": True,
        "fields": {},
    }
    s = store.create_script(
        col,
        topic,
        created_by="prompt-quality-validation",
        content_context={"job": job},
    )
    status = video_workflow.run(s["id"], started_by="prompt-quality-validation")
    ver = store.get_version(s["id"], 1) if status != "FAILED" else None
    meta = (ver or {}).get("meta") or {}
    mw = meta.get("manuscript_workflow") or {}
    pol = meta.get("polish") or {}
    elapsed = time.monotonic() - t0
    print(
        f"  -> id={s['id']} status={status} elapsed={elapsed:.1f}s "
        f"mw={mw.get('result')} polish_changes={len(pol.get('changes') or [])} "
        f"problems={len(meta.get('problems') or [])} "
        f"title={(ver or {}).get('title', '')}",
        flush=True,
    )

print("\n[verify_manuscript_v2] 4 篇实跑验收已完成。", flush=True)
PY
