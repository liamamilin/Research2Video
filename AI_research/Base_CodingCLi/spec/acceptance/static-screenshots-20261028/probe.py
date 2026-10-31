"""Inspect real public source candidates with the production screenshot service."""
import json
import argparse
import sys
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from core.video import screenshots, visual_matching, visual_materials
from web.services.video_visual_acquire import REGIONS

out = ROOT / "state/validation/static-screenshots-20261028" / ("probe-" + uuid4().hex[:8])
out.mkdir(parents=True)
cases = {
    "product": ("https://docs.n8n.io/try-it-out/quickstart/", "n8n 把工作流中的节点连接起来，编辑器展示触发器、处理步骤和输出。"),
    "opensource": ("https://github.com/microsoft/markitdown", "MarkItDown 的 README 展示如何安装和调用，把输入文件转换成 Markdown。"),
    "documentation": ("https://playwright.dev/python/docs/screenshots#element-screenshot", "Playwright 使用 locator 定位页面元素，再调用 screenshot 保存这一个元素。"),
}
parser = argparse.ArgumentParser()
parser.add_argument('--case', choices=cases)
args = parser.parse_args()
if args.case:
    cases = {args.case: cases[args.case]}
for name, (url, text) in cases.items():
    need = {"id": "c1p1", "text": text, "claims": text, "kind": "feature",
            "visual_subject": visual_materials.source_subject(url), "visual_goal": visual_materials.visual_goal(text, "", url)}
    try:
        with screenshots.page_for_capture(url, timeout_seconds=40) as page:
            snapshot = page.evaluate(REGIONS.read_text(), "probe-" + uuid4().hex[:8])
            (out / (name + ".json")).write_text(json.dumps(snapshot, ensure_ascii=False, indent=2))
            page.screenshot(path=str(out / (name + ".png")), animations="disabled")
            ranked = visual_matching.rank(need, snapshot)
            print(json.dumps({"case": name, "out": str(out), "title": snapshot["title"], "candidates": len(ranked),
                "best": [{k: c.get(k) for k in ("text", "box", "score", "visual_kind", "anchor")} for c in ranked[:4]]}, ensure_ascii=False), flush=True)
    except Exception as exc:
        print(json.dumps({"case": name, "error": str(exc)}, ensure_ascii=False), flush=True)
