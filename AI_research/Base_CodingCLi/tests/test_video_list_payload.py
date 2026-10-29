"""List projections preserve status/filter identity and leave full detail records intact."""
from copy import deepcopy

from web.services.video_list_payload import compact
from tests.test_video_scripts import video_env  # noqa: F401


def test_compact_lists_drop_large_source_snapshots_without_mutation():
    pub = {"id": "p", "platform": "kuaishou", "url": "https://example.com/work", "version": 1,
           "account_uid": "a", "publish_pack": {"description": "large source" * 1000}}
    row = {"id": "s", "status": "APPROVED", "topic": {"id": None, "title": "主题", "theme_research": {"sources": ["large"]}},
           "length_check": {"estimated_seconds": 300, "status": "OK", "sections": [{"title": "detail"}]},
           "publications": [pub], "distributions": [{"status": "PUBLISHED", "platform": "kuaishou",
               "current_account": True, "account_uid": "a", "publications": [pub]}]}
    original = deepcopy(row)
    small = compact(row)
    assert small["status"] == "APPROVED"
    assert small["length_check"]["estimated_seconds"] == 300
    assert small["topic"] == {"id": None, "title": "主题"}
    assert small["distributions"][0]["status"] == "PUBLISHED"
    assert small["distributions"][0]["account_uid"] == "a"
    assert "publications" not in small["distributions"][0]
    assert "publish_pack" not in small["publications"][0]
    assert row == original


def test_compact_list_route_is_opt_in(video_env):
    from core.video import store
    from web.routes.video import list_scripts
    script = store.create_script("deep_dive", {"title": "主题", "theme_research": {"sources": ["large"]}})
    assert list_scripts(user={})[0]["topic"]["theme_research"]["sources"] == ["large"]
    small = list_scripts(user={}, compact=True)[0]
    assert small["id"] == script["id"] and small["topic"]["title"] == "主题"
    assert "theme_research" not in small["topic"]
