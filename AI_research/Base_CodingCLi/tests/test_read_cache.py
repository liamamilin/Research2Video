"""Tests for video store db_revision cache and read_cache invalidation."""
from __future__ import annotations

from core.video import store
from web.services import read_cache


def test_db_revision_and_current_versions_cache_invalidate_on_write(tmp_path):
    prev = store.db_path()
    try:
        store.set_db_path(str(tmp_path / "video.db"))
        store.init_db()
        script = store.create_script("flash", {"title": "初始标题"})
        store.add_version(script["id"], title="v1", sections=[{"title": "一", "paragraphs": [{"text": "甲"}]}],
                          length_check={}, meta={}, edit_type="generate")

        rev1 = store.db_revision()
        first = store.current_versions([script["id"]])
        assert first[script["id"]]["title"] == "v1"
        assert store.db_revision() == rev1

        # Adding v2 writes through connect(write=True), bumping db_revision immediately.
        store.add_version(script["id"], title="v2-新标题", sections=[{"title": "一", "paragraphs": [{"text": "乙"}]}],
                          length_check={}, meta={}, edit_type="manual")
        rev2 = store.db_revision()
        assert rev2 != rev1
        second = store.current_versions([script["id"]])
        assert second[script["id"]]["title"] == "v2-新标题"
    finally:
        store.set_db_path(prev)


def test_read_cache_hits_and_invalidates():
    read_cache.invalidate("test_ns")
    calls = {"n": 0}

    def compute():
        calls["n"] += 1
        return {"count": calls["n"]}

    assert read_cache.cached("test_ns", ("k1",), 15.0, compute) == {"count": 1}
    assert read_cache.cached("test_ns", ("k1",), 15.0, compute) == {"count": 1}
    assert read_cache.cached("test_ns", ("k2",), 15.0, compute) == {"count": 2}
    read_cache.invalidate("test_ns")
    assert read_cache.cached("test_ns", ("k1",), 15.0, compute) == {"count": 3}
