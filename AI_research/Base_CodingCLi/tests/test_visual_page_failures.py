"""Page failures are reusable; paragraph-specific mismatches are not."""
import copy

import pytest

from core.video import screenshots, store
from tests.test_video_scripts import video_env  # noqa: F401
from tests.test_video_visuals import enabled, script, URL  # noqa: F401
from web.services import video_render, video_visuals


@pytest.mark.parametrize("page_wide, expected_calls", [(True, 1), (False, 3)])
def test_shared_url_failures_respect_scope_and_resume(enabled, monkeypatch, page_wide, expected_calls):
    engine, _ = enabled
    render = video_render.create(script())
    audit = copy.deepcopy(render["visuals"])
    need = audit["needs"][0]
    audit["needs"] = [{**need, "id": f"need-{i}"} for i in range(3)]
    store.update_render(render["id"], visuals=audit)
    calls = []

    def fail(*args, **kwargs):
        calls.append(URL)
        error = screenshots.PageUnavailable if page_wide else screenshots.ScreenshotError
        raise error("页面正文尚未加载" if page_wide else "此段落没有合适区域")

    monkeypatch.setattr(video_visuals, "acquire", fail)
    _, result = video_visuals.prepare(store.get_render(render["id"]), engine.client())
    assert len(calls) == expected_calls
    assert len(result["decisions"]) == 3
    assert result["state"] == "degraded"
    assert result.get("page_failures_reused", 0) == (2 if page_wide else 0)
    # A process interruption after a persisted page failure must not retry it
    # for the following paragraphs in the same render.
    if page_wide:
        result.update(state="running", decisions=[])
        store.update_render(render["id"], visuals=result)
        video_visuals.prepare(store.get_render(render["id"]), engine.client())
        assert len(calls) == 1
