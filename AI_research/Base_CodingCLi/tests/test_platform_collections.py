"""Published Bilibili videos join their 合集 by recipe or matrix; fake APIs only, nothing reaches the platform."""
import json
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from tests.test_platforms import BV, PAYLOAD, platform_env, render  # noqa: F401
from web.services import bilibili, platform_manager as manager

FLASH, DEEP, DAILY = 101, 202, 303
RULES = {"collections": {"by_recipe": {"flash-nr": FLASH}, "by_matrix": {"ai_media": DEEP, "tech": DAILY}}}


class FakeApi:
    def __init__(self, *, state=0, verify_code=0, fail_add=None):
        self.state, self.verify_code, self.fail_add = state, verify_code, fail_add
        self.sections = {FLASH: 11, DEEP: 22, DAILY: 33}
        self.members: dict[int, set[int]] = {11: set(), 22: set(), 33: set()}
        self.added: list[tuple[int, list[dict]]] = []

    def owned_video(self, bvid, uid):
        return {"aid": 9001, "cid": 77, "title": "测试视频", "state": self.state}

    def season_sections(self):
        return dict(self.sections)

    def section_aids(self, section):
        return set(self.members[section])

    def season_episode_state(self, aids):
        return {aid: {"cid": 77, "code": self.verify_code, "msg": "稿件审核中" if self.verify_code else ""} for aid in aids}

    def add_to_season(self, section, episodes):
        self.added.append((section, episodes))
        self.members[section].update(e["aid"] for e in episodes)
        if self.fail_add:
            raise self.fail_add


@pytest.fixture
def api(platform_env, monkeypatch):  # noqa: F811
    fake = FakeApi()
    monkeypatch.setattr(manager, "client", lambda expected_uid="": fake)
    manager.store().save_settings(RULES)
    return fake


def published():
    task = manager.enqueue(render()["id"], dict(PAYLOAD))
    manager.store().update_upload(task["id"], status="PUBLISHED", bvid=BV)
    return manager.store().get_upload(task["id"])


@pytest.mark.parametrize("column_key,keys,expected", [
    ("flash-nr", ["ai_media"], FLASH),          # recipe beats the matrix: every 快讯 goes together
    ("topic_digest", ["tech", "ai_media"], DAILY),  # most specific matrix first
    ("deep_dive", ["ai_media"], DEEP),
    ("topic_digest", ["history"], 0),
    ("local_video", [], 0),
])
def test_season_follows_recipe_then_matrix(monkeypatch, column_key, keys, expected):
    monkeypatch.setattr(manager, "_matrix_keys", lambda script: list(keys))
    assert manager.season_for({"column_key": column_key}, RULES) == expected


def test_published_video_joins_once_and_rereads_before_adding(api, monkeypatch):
    monkeypatch.setattr(manager, "_matrix_keys", lambda script: ["ai_media"])
    task = published()
    assert [t["id"] for t in manager.store().collection_tasks("123", retry_after=600)] == [task["id"]]
    manager.process_collection(task)
    row = manager.store().get_upload(task["id"])
    assert (row["collection_status"], row["collection_season"]) == ("DONE", DEEP)
    assert api.added == [(22, [{"aid": 9001, "cid": 77, "title": "测试视频"}])]
    assert manager.store().collection_tasks("123", retry_after=600) == []
    manager.process_collection(row)  # a repeat finds it in the section and adds nothing
    assert len(api.added) == 1


def test_unmapped_video_is_left_alone(api, monkeypatch):
    monkeypatch.setattr(manager, "_matrix_keys", lambda script: ["history"])
    task = published()
    manager.process_collection(task)
    assert manager.store().get_upload(task["id"])["collection_status"] == "NONE" and api.added == []


def test_preset_season_wins_over_rules(api, monkeypatch):
    monkeypatch.setattr(manager, "_matrix_keys", lambda script: ["history"])
    task = published()
    manager.store().set_collection(task["id"], status="WAITING", season=DAILY)
    manager.process_collection(manager.store().get_upload(task["id"]))
    assert manager.store().get_upload(task["id"])["collection_status"] == "DONE" and api.added[0][0] == 33


@pytest.mark.parametrize("blocker", [{"state": -6}, {"verify_code": 21071}])
def test_not_yet_open_or_refused_video_waits_then_gives_up(api, monkeypatch, blocker):
    monkeypatch.setattr(manager, "_matrix_keys", lambda script: ["tech"])
    for name, value in blocker.items():
        setattr(api, name, value)
    task = published()
    manager.process_collection(task)
    row = manager.store().get_upload(task["id"])
    assert row["collection_status"] == "WAITING" and row["collection_attempts"] == 1 and row["collection_error"]
    assert manager.store().collection_tasks("123", retry_after=600) == []  # not due again yet
    assert len(manager.store().collection_tasks("123", retry_after=-1)) == 1
    monkeypatch.setattr(manager, "COLLECTION_MAX_ATTEMPTS", 2)
    manager.process_collection(row)
    assert manager.store().get_upload(task["id"])["collection_status"] == "FAILED" and api.added == []


def test_uncertain_add_is_confirmed_on_the_next_pass_without_a_second_add(api, monkeypatch):
    monkeypatch.setattr(manager, "_matrix_keys", lambda script: ["tech"])
    api.fail_add = bilibili.BilibiliError("B 站连接失败，操作结果可能尚未确认。")
    task = published()
    manager.process_collection(task)
    assert manager.store().get_upload(task["id"])["collection_status"] == "WAITING"
    api.fail_add = None
    manager.process_collection(manager.store().get_upload(task["id"]))
    assert manager.store().get_upload(task["id"])["collection_status"] == "DONE" and len(api.added) == 1


def test_missing_season_fails_visibly(api, monkeypatch):
    monkeypatch.setattr(manager, "_matrix_keys", lambda script: ["tech"])
    del api.sections[DAILY]
    task = published()
    manager.process_collection(task)
    row = manager.store().get_upload(task["id"])
    assert row["collection_status"] == "FAILED" and str(DAILY) in row["collection_error"]


def test_reupload_after_removal_rejoins_the_same_season(api, monkeypatch):
    task = published()
    db = manager.store()
    db.set_collection(task["id"], status="DONE", season=DEEP)
    assert db.mark_removed(task["id"], "123", "已删除") and db.requeue_removed(task["id"], "123")
    db.update_upload(task["id"], status="PUBLISHED", bvid="BV1yy411c7mD")
    row = db.get_upload(task["id"])
    assert (row["collection_status"], row["collection_season"]) == ("", DEEP)
    assert [t["id"] for t in db.collection_tasks("123", retry_after=600)] == [task["id"]]


def test_worker_tick_runs_the_collection_step(api, monkeypatch):
    monkeypatch.setattr(manager, "_matrix_keys", lambda script: ["tech"])
    monkeypatch.setattr(manager, "candidates", lambda: [])
    task = published()
    manager.tick()
    assert manager.store().get_upload(task["id"])["collection_status"] == "DONE"


def test_season_requests_match_the_creator_center(monkeypatch):
    seen = []

    def respond(request):
        seen.append(request)
        path = request.url.path
        if path.endswith("/season/add"):
            return httpx.Response(200, json={"code": 0, "data": 555})
        if path.endswith("/verify/batch"):
            return httpx.Response(200, json={"code": 0, "data": {"ep_audits": {"9001": {"cid": 77, "code": 0, "msg": ""}}}})
        if path.endswith("/seasons"):
            return httpx.Response(200, json={"code": 0, "data": {"total": 1, "seasons": [
                {"season": {"id": 555}, "sections": {"sections": [{"id": 1}, {"id": 2}]}}]}})
        return httpx.Response(200, json={"code": 0, "data": {}})

    original = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs))
    api = bilibili.BilibiliClient({"cookie_info": {"cookies": [{"name": "bili_jct", "value": "csrf-x"},
                                                                {"name": "DedeUserID", "value": "123"}]}})
    assert api.create_season("快讯", "https://example.invalid/c.png") == 555
    assert api.season_sections() == {555: 2}
    assert api.season_episode_state([9001]) == {9001: {"cid": 77, "code": 0, "msg": ""}}
    api.add_to_season(2, [{"aid": 9001, "cid": 77, "title": "测试视频"}])
    create, _, verify, add = seen
    assert create.method == "POST" and parse_qs(urlparse(str(create.url)).query)["csrf"] == ["csrf-x"]
    assert json.loads(create.content) == {"title": "快讯", "cover": "https://example.invalid/c.png", "desc": ""}
    assert parse_qs(urlparse(str(verify.url)).query)["aids"] == ["9001"]
    assert add.url.path == "/x2/creative/web/season/section/episodes/add"
    assert json.loads(add.content) == {"sectionId": 2, "episodes": [
        {"title": "测试视频", "aid": 9001, "cid": 77, "charging_pay": 0, "member_first": 0}]}
