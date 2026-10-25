"""Controlled HTML fixtures in real Chrome and native OCR; no production URL guard bypass.

This is deliberately separate from real_chain.py, which visits the real public site.
The page context is replaced only here so exact negative conditions are reproducible.
"""
import argparse
import json
import sys
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

RESEARCH = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(RESEARCH))
from core.video import image_quality, screenshots, store
from web.services.video_visual_acquire import acquire
from playwright.sync_api import sync_playwright

parser = argparse.ArgumentParser()
parser.add_argument("--output", type=Path, required=True)
out = parser.parse_args().output.resolve()
out.mkdir(parents=True, exist_ok=False)
store.use_state_dir(str(out / "state"))
policy = {"version": 3, "semantic_review": False, "max_model_calls": 0, "ocr_review": True}
claim = "Atlas Pro 每月价格为 20 美元。"
html_template = """<!doctype html><title>{title}</title><style>
body{{font:24px Arial;margin:60px;background:#fff;color:#222}}main{{width:920px}}
section{{padding:28px;border:1px solid #aaa}}h2{{font-size:36px}}p{{line-height:1.6}}
</style><main><section><h2>{heading}</h2><p id=price>{price}</p>
<p>This service offers a clear product plan for individual creators. Review the billing details and available features before choosing a subscription.</p>
</section></main>{extra}"""
cases = [
    ("monthly_price", claim, "Atlas Pricing", "Atlas Pro pricing", "$20 per month", "", True),
    ("amount_mismatch", claim, "Atlas Pricing", "Atlas Pro pricing", "$25 per month", "", False),
    ("currency_mismatch", claim, "Atlas Pricing", "Atlas Pro pricing", "€20 per month", "", False),
    ("annual_condition", claim, "Atlas Pricing", "Atlas Pro pricing", "$20 per month, billed annually", "", False),
    ("tier_mismatch", claim, "Atlas Pricing", "Atlas Basic pricing", "$20 per month", "", False),
    ("product_mismatch", claim, "Other Pricing", "Other Pro pricing", "$20 per month", "", False),
    ("version_mismatch", "Atlas v2.0 Pro 每月价格为 20 美元。", "Atlas v1.0 Pricing", "Atlas v1.0 Pro pricing", "$20 per month", "", False),
    ("login", claim, "Atlas Pricing", "Atlas Pro pricing", "$20 per month", "<input type=password aria-label=password>", False),
    ("challenge", claim, "Verify you are human", "Atlas Pro pricing", "$20 per month", "", False),
    ("occluded", claim, "Atlas Pricing", "Atlas Pro pricing", "$20 per month", '<div role=dialog style="position:fixed;inset:0;background:#eee;z-index:99">Accept all cookies to continue</div>', False),
    ("pixel_price_changed", claim, "Atlas Pricing", "Atlas Pro pricing", "$20 per month", "", False),
]
results = []
with sync_playwright() as pw:
    browser = pw.chromium.launch(channel="chrome", headless=True)
    try:
        for name, text, title, heading, price, extra, expected in cases:
            folder = out / name
            folder.mkdir()
            html = html_template.format(title=title, heading=heading, price=price, extra=extra)
            (folder / "fixture.html").write_text(html)
            page = browser.new_page(viewport={"width": 1440, "height": 900}, device_scale_factor=1)
            page.set_content(html)
            page.screenshot(path=str(folder / "page.png"))
            original_screenshot = page.screenshot
            if name == "pixel_price_changed":
                def changed_screenshot(**options):
                    page.locator("#price").evaluate("el => el.textContent = '$25 per month'")
                    return original_screenshot(**options)
                page.screenshot = changed_screenshot
            @contextmanager
            def fixture_page(*args, **kwargs):
                yield page
            need = {"kind": "pricing", "text": text, "claims": text}
            metadata = {}
            try:
                with patch.object(screenshots, "page_for_capture", fixture_page):
                    raw, metadata = acquire(need, "https://example.com/fixture", policy, remaining=90)
                (folder / "capture.png").write_bytes(raw)
                metadata["image_review"] = image_quality.verify(raw, need, metadata, policy, remaining=60)
                accepted, reason = True, "文字与真实像素复核通过"
            except screenshots.ScreenshotError as exc:
                accepted, reason = False, str(exc)
            finally:
                page.close()
            result = {"case": name, "expected_accepted": expected, "accepted": accepted, "reason": reason,
                      "passed": accepted == expected, "metadata": metadata}
            (folder / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
            results.append(result)
            print(json.dumps({k: result[k] for k in ("case", "accepted", "passed", "reason")}, ensure_ascii=False), flush=True)
    finally:
        browser.close()
(out / "qa.json").write_text(json.dumps({"kind": "controlled HTML, real Chrome and native OCR", "cases": results}, ensure_ascii=False, indent=2))
assert all(row["passed"] for row in results)
