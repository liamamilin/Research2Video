"""Activity bookkeeping must not delay response bodies or block unrelated requests."""

import asyncio
import threading

import httpx
from starlette.background import BackgroundTask
from starlette.responses import JSONResponse
from starlette.routing import Route

from core import activity
from web import server


def test_slow_activity_write_runs_after_response_and_keeps_liveness_responsive(web_env, monkeypatch):
    entered, release, finished, delivered = (threading.Event() for _ in range(4))
    previous_background = []

    def slow_activity():
        entered.set()
        release.wait(timeout=3)
        finished.set()

    monkeypatch.setattr(activity, "use_state_dir", lambda value: None)
    monkeypatch.setattr(activity, "mark_activity", slow_activity)
    app = server.create_app()

    async def response(request):
        return JSONResponse({"ok": True}, background=BackgroundTask(previous_background.append, "kept"))

    app.router.routes.insert(0, Route("/api/performance-response", response))

    async def observed(scope, receive, send):
        async def capture(message):
            if scope["path"] == "/api/performance-response" and message["type"] == "http.response.body" \
                    and not message.get("more_body", False):
                delivered.set()
            await send(message)

        await app(scope, receive, capture)

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=observed), base_url="http://test") as client:
            # The middleware uses cookie presence; no real authentication or external service is needed.
            request = asyncio.create_task(client.get("/api/performance-response", headers={"Cookie": "ai_research_access=test"}))
            try:
                assert await asyncio.to_thread(entered.wait, 2)
                assert not finished.is_set(), "activity I/O blocked the event loop until its timeout"
                assert delivered.is_set(), "the API response was held behind activity bookkeeping"
                probe = await asyncio.wait_for(client.get("/api/live"), timeout=1)
                assert probe.status_code == 200
            finally:
                release.set()
                await request

    asyncio.run(exercise())
    assert previous_background == ["kept"]


def test_activity_failure_does_not_fail_the_api_response(web_env, monkeypatch):
    def unavailable():
        raise OSError("activity disk unavailable")

    monkeypatch.setattr(activity, "use_state_dir", lambda value: None)
    monkeypatch.setattr(activity, "mark_activity", unavailable)
    app = server.create_app()
    app.router.routes.insert(0, Route("/api/performance-response", lambda request: JSONResponse({"ok": True})))

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/performance-response", headers={"Cookie": "ai_research_access=test"})
            assert response.status_code == 200 and response.json() == {"ok": True}

    asyncio.run(exercise())
