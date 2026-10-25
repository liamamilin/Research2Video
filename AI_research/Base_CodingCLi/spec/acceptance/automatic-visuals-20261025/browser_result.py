"""Show the actual completed real-chain ledger in the real built UI, without editing images."""
import argparse
import json
import os
import sys
import threading
import time
from pathlib import Path
from uuid import uuid4

RESEARCH = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(RESEARCH))
parser = argparse.ArgumentParser()
parser.add_argument("--run-dir", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
args = parser.parse_args()
run = args.run_dir.resolve()
out = args.output.resolve()
out.mkdir(parents=True, exist_ok=False)
os.environ["AI_RESEARCH_WEB_CONFIG"] = str(run / "web.yaml")
os.environ["AREC_NO_AUTOPILOT_LOOP"] = "1"
os.environ["AI_RESEARCH_ENV"] = "development"
from core.video import store
from web.auth import db as users
from web.auth.password import hash_password
from web.indexer import db as index
from web.server import create_app
from playwright.sync_api import sync_playwright
import uvicorn

store.use_state_dir(str(run / "state"))
store.init_db()
render = json.loads((run / "render.json").read_text())
users._DB_PATH_OVERRIDE = str(out / "users.db")
users.init_db()
password = uuid4().hex
users.create_user("acceptance", hash_password(password), "admin")
index._DB_PATH_OVERRIDE = str(out / "reports.db")
index.init_db()
server = uvicorn.Server(uvicorn.Config(create_app(), host="127.0.0.1", port=18769,
                                     lifespan="off", log_level="warning"))
thread = threading.Thread(target=server.run, daemon=True)
thread.start()
for _ in range(100):
    if server.started:
        break
    time.sleep(.05)
assert server.started
try:
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        context = browser.new_context(viewport={"width": 1480, "height": 1100}, locale="zh-CN")
        login = context.request.post("http://127.0.0.1:18769/api/auth/login", data={"username": "acceptance", "password": password})
        assert login.status == 200
        response = context.request.get(f"http://127.0.0.1:18769/api/video/scripts/{render['script_id']}/screenshots?version=1")
        assert response.status == 200, response.text()
        overview = response.json()
        assert overview["selection"]["origin"] == "automatic"
        assert overview["selection"]["materials"][0]["placements"] == [{"chapter_index": 0, "paragraph_index": 1}]
        page = context.new_page()
        errors, writes = [], []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.on("request", lambda req: writes.append({"method": req.method, "url": req.url})
                if req.method in {"POST", "PUT", "PATCH", "DELETE"} and ("/screenshots" in req.url or "/image-materials" in req.url) else None)
        page.goto(f"http://127.0.0.1:18769/video/{render['script_id']}?tab=video")
        toggle = page.get_by_role("button", name="网页截图与段落配图", exact=False)
        toggle.click()
        page.get_by_label("自动配图结果", exact=True).wait_for()
        page.get_by_text("自动配图已用于本次制作；手动修改后保存仅影响下次制作", exact=True).wait_for()
        assert page.get_by_label("使用截图：Screenshots | Playwright Python", exact=True).is_checked()
        assert page.get_by_label("画面与来源 · 第2段", exact=False).is_checked()
        assert not page.get_by_label("画面与来源 · 第1段", exact=False).is_checked()
        assert page.get_by_role("button", name="确认并保存段落配图", exact=True).is_disabled()
        has_vision = any((((d.get("quality") or {}).get("image") or {}).get("vision") or {}).get("state") == "passed"
                         for d in render["visuals"].get("decisions", []))
        if has_vision:
            page.get_by_text("查看各段处理结果", exact=True).click()
            assert page.get_by_text("图片内容：", exact=False).count() >= 1
            assert page.get_by_text("聚焦建议：", exact=False).count() >= 1
        panel = toggle.locator("xpath=..")
        panel.screenshot(path=str(out / "automatic-panel.png"))
        page.screenshot(path=str(out / "completed-page.png"), full_page=True)
        assert not errors, errors
        assert not writes, writes
        (out / "qa.json").write_text(json.dumps({"status": "PASS", "render_id": render["id"], "origin": "automatic",
            "image_writes": writes, "page_errors": errors, "placements": overview["selection"]["materials"][0]["placements"],
            "no_capture_or_confirm_click": True}, ensure_ascii=False, indent=2))
        browser.close()
        print("Completed real-chain ledger → UI automatic selection without capture/confirm: PASS", flush=True)
finally:
    server.should_exit = True
    thread.join(10)
