"""Offline publication links, account targets and cross-platform distribution tests."""
from datetime import datetime, timezone
import json

import pytest

from core.social.adapter import AdapterError
from core.video import store
from core.video.publication_identity import identify
from tests.test_platforms import BV, platform_env, render
from tests.test_video_scripts import _csrf, _login
from web.services import distributions, douyin, social_publishing as social, video_render


WORKS = {
    "kuaishou": "3xm9abc123defgh",
    "xiaohongshu": "65f1a2b3c4d5e6f7890abcde",
    "weixin_channels": "",
}
LINKS = {
    "kuaishou": "https://www.kuaishou.com/short-video/3xm9abc123defgh",
    "xiaohongshu": "https://www.xiaohongshu.com/explore/65f1a2b3c4d5e6f7890abcde",
    "weixin_channels": "https://weixin.qq.com/sph/AbcD_123xyz",
}
ACCOUNTS = {
    "kuaishou": {"connected": True, "uid": "KS_creator_A123", "name": "测试快手账号"},
    "xiaohongshu": {"connected": True, "uid": "65aa1122334455667788abcd", "name": "测试小红书账号"},
    "weixin_channels": {"connected": True, "uid": "v2_0123456789abcdef@finder", "name": "测试视频号账号"},
}
DOUYIN_WORK = "7700436961890209034"


@pytest.fixture
def social_env(platform_env, monkeypatch):
    profiles = {platform: dict(profile) for platform, profile in ACCOUNTS.items()}
    monkeypatch.setattr(social, "account", lambda platform: dict(profiles[platform]))
    monkeypatch.setattr(social, "runtime", lambda: {"ready": True})
    monkeypatch.setattr(social, "tick", lambda platform: None)  # Never spawn a real platform worker.
    monkeypatch.setattr(douyin, "account", lambda: {"connected": True, "uid": "456", "name": "测试抖音账号"})
    return profiles


def next_render(first):
    version = store.add_version(first["script_id"], title="后续版本", sections=[], length_check={}, edit_type="EDITED", meta={})
    row = store.create_render(first["script_id"], version, title="后续版本", source_text="新版内容", source_sha256="b" * 64, request={})
    store.update_render(row["id"], status="COMPLETED", completed_at=datetime.now(timezone.utc).isoformat(), artifacts=[{"name": "video.mp4"}])
    folder = video_render.files_dir(row["id"])
    folder.mkdir(parents=True)
    (folder / "video.mp4").write_bytes(b"isolated-new-version")
    return store.get_render(row["id"])


@pytest.mark.parametrize("platform,raw", [
    ("kuaishou", "http://m.kuaishou.com/short-video/3xm9abc123defgh/?shareToken=discard-me"),
    ("kuaishou", LINKS["kuaishou"] + "?xsec_token=discard-me#ignored"),
    ("xiaohongshu", "http://www.xiaohongshu.com/discovery/item/65f1a2b3c4d5e6f7890abcde/?xsec_token=private-token&xsec_source=pc_share"),
    ("xiaohongshu", LINKS["xiaohongshu"] + "?xsec_token=private-token"),
    ("weixin_channels", LINKS["weixin_channels"] + "/?share=discard-me"),
])
def test_new_platform_work_links_are_canonical_without_query_secrets(platform, raw):
    work_id, url = identify(platform, raw, strict=True)
    assert (work_id, url) == (WORKS[platform], LINKS[platform])
    assert "token" not in url and "discard-me" not in url


@pytest.mark.parametrize("platform,url", [
    (target, url) for target in LINKS for source, url in LINKS.items() if source != target
] + [
    ("kuaishou", f"https://www.bilibili.com/video/{BV}"),
    ("xiaohongshu", f"https://www.douyin.com/video/{DOUYIN_WORK}"),
    ("bilibili", LINKS["kuaishou"]), ("douyin", LINKS["xiaohongshu"]),
])
def test_foreign_platform_links_cannot_create_a_work_for_another_platform(platform, url):
    with pytest.raises(ValueError):
        identify(platform, url, strict=True)


@pytest.mark.parametrize("platform,bad", [
    ("kuaishou", "https://www.kuaishou.com.evil.test/short-video/3xm9abc123defgh"),
    ("kuaishou", "https://user:secret@www.kuaishou.com/short-video/3xm9abc123defgh"),
    ("xiaohongshu", "https://xiaohongshu.com.evil.test/explore/65f1a2b3c4d5e6f7890abcde"),
    ("xiaohongshu", "https://user:secret@www.xiaohongshu.com/explore/65f1a2b3c4d5e6f7890abcde"),
    ("weixin_channels", "https://weixin.qq.com.evil.test/sph/AbcD_123xyz"),
    ("weixin_channels", "https://user:secret@weixin.qq.com/sph/AbcD_123xyz"),
    ("weixin_channels", "https://channels.weixin.qq.com/platform/post/list"),
])
def test_manual_work_links_reject_credentials_lookalike_hosts_and_account_pages(platform, bad):
    with pytest.raises(ValueError):
        identify(platform, bad, strict=True)


@pytest.mark.parametrize("platform,url", list(LINKS.items()) + [
    ("bilibili", f"https://www.bilibili.com/video/{BV}"),
    ("douyin", f"https://www.douyin.com/video/{DOUYIN_WORK}"),
])
def test_work_links_reject_even_empty_url_userinfo(platform, url):
    raw = url.replace("https://", "https://@", 1)
    with pytest.raises(ValueError):
        identify(platform, raw, strict=True)


def test_xhs_security_token_is_not_a_work_id_and_is_not_reflected():
    fake_id = "a" * 24
    with pytest.raises(ValueError):
        identify("xiaohongshu", f"https://www.xiaohongshu.com/?xsec_token={fake_id}", strict=True)
    work_id, url = identify("xiaohongshu", LINKS["xiaohongshu"] + f"?xsec_token={fake_id}", strict=True)
    assert work_id == WORKS["xiaohongshu"] and fake_id not in url


def test_wechat_share_code_is_preserved_as_url_without_fabricating_provider_id():
    assert identify("weixin_channels", LINKS["weixin_channels"], strict=True) == ("", LINKS["weixin_channels"])


@pytest.mark.parametrize("platform", LINKS)
def test_manual_api_accepts_exact_nonnumeric_account_and_historical_version(social_env, client, platform):
    original = render()
    newer = next_render(original)
    _login(client)
    endpoint = f"/api/video/scripts/{original['script_id']}/publications"
    body = {"platform": platform, "account_uid": social_env[platform]["uid"], "url": LINKS[platform],
            "version": 1, "render_id": original["id"], "published_on": "2026-10-24"}
    response = client.post(endpoint, headers=_csrf(client), json=body)
    assert response.status_code == 201, response.text
    publication = response.json()
    assert publication["account_uid"] == social_env[platform]["uid"]
    assert publication["account_name"] == social_env[platform]["name"]
    assert publication["work_id"] == WORKS[platform] and publication["url"] == LINKS[platform]
    assert publication["version"] == 1 and publication["render_id"] == original["id"]
    assert not publication["confirmed_at"]  # Manual bookkeeping is not API verification.
    assert store.get_script(original["script_id"])["current_version"] == 2
    rows = distributions.overview([store.get_script(original["script_id"])])[original["script_id"]]
    old = next(row for row in rows if row["platform"] == platform and row["version"] == 1 and row["current_account"])
    current = next(row for row in rows if row["platform"] == platform and row["version"] == newer["version"] and row["current_account"])
    assert old["status"] == "PUBLISHED" and not old["can_prepare"]
    assert current["status"] == "UNPUBLISHED" and current["can_prepare"]


@pytest.mark.parametrize("platform", LINKS)
def test_manual_api_rejects_stale_or_different_platform_account(social_env, client, platform):
    row = render()
    _login(client)
    body = {"platform": platform, "account_uid": social_env[platform]["uid"] + "-different", "url": LINKS[platform], "version": 1}
    response = client.post(f"/api/video/scripts/{row['script_id']}/publications", headers=_csrf(client), json=body)
    assert response.status_code == 422 and not store.list_publications(row["script_id"])


@pytest.mark.parametrize("platform,kind", [(platform, kind) for platform in LINKS for kind in ("credentials", "evil_host")])
def test_manual_api_rejects_untrusted_work_urls_without_echoing_credentials(social_env, client, platform, kind):
    row = render()
    _login(client)
    raw = LINKS[platform].replace("https://", "https://user:private-url-secret@", 1) if kind == "credentials" else LINKS[platform].replace(".com/", ".com.evil.test/")
    if platform == "weixin_channels" and kind == "evil_host":
        raw = raw.replace("weixin.qq.com/", "weixin.qq.com.evil.test/")
    response = client.post(f"/api/video/scripts/{row['script_id']}/publications", headers=_csrf(client), json={
        "platform": platform, "account_uid": social_env[platform]["uid"], "url": raw, "version": 1,
    })
    assert response.status_code == 422 and not store.list_publications(row["script_id"])
    assert "private-url-secret" not in response.text


@pytest.mark.parametrize("platform", LINKS)
def test_manual_api_cannot_associate_new_render_with_old_version(social_env, client, platform):
    row = render()
    newer = next_render(row)
    _login(client)
    response = client.post(f"/api/video/scripts/{row['script_id']}/publications", headers=_csrf(client), json={
        "platform": platform, "account_uid": social_env[platform]["uid"], "url": LINKS[platform],
        "version": 1, "render_id": newer["id"],
    })
    assert response.status_code == 422 and not store.list_publications(row["script_id"])


def test_xhs_manual_record_and_detail_do_not_return_share_security_token(social_env, client):
    row = render()
    _login(client)
    token = "private-test-xsec-token"
    response = client.post(f"/api/video/scripts/{row['script_id']}/publications", headers=_csrf(client), json={
        "platform": "xiaohongshu", "account_uid": social_env["xiaohongshu"]["uid"], "version": 1,
        "url": LINKS["xiaohongshu"] + f"?xsec_token={token}&xsec_source=pc_share",
    })
    assert response.status_code == 201, response.text
    assert token not in response.text and "xsec_token" not in response.text
    detail = client.get(f"/api/video/scripts/{row['script_id']}")
    assert detail.status_code == 200
    assert token not in detail.text and "xsec_token" not in detail.text


def test_same_work_history_can_merge_unknown_account_without_cross_account_reassignment(social_env):
    row = render()
    for platform in LINKS:
        unknown = store.add_publication(row["script_id"], version=1, platform=platform, url=LINKS[platform], published_on="2026-10-24")
        associated = store.add_publication(row["script_id"], version=1, platform=platform, url=LINKS[platform], published_on="2026-10-24",
                                           account_uid=social_env[platform]["uid"], account_name=social_env[platform]["name"])
        assert associated["id"] == unknown["id"]
        assert associated["account_uid"] == social_env[platform]["uid"]
        with pytest.raises(ValueError, match="另一个平台账号"):
            store.add_publication(row["script_id"], version=1, platform=platform, url=LINKS[platform], published_on="2026-10-24", account_uid="other-owner")
    assert len(store.list_publications(row["script_id"])) == 3


def test_identical_work_id_strings_on_different_platforms_never_merge(social_env):
    row = render()
    shared_id = WORKS["xiaohongshu"]
    made = []
    for platform, url in (
        ("kuaishou", f"https://www.kuaishou.com/short-video/{shared_id}"),
        ("xiaohongshu", LINKS["xiaohongshu"]),
    ):
        made.append(store.add_publication(row["script_id"], version=1, platform=platform, account_uid="same-owner-string", url=url, published_on="2026-10-24"))
    assert made[0]["id"] != made[1]["id"]
    assert {record["platform"] for record in store.list_publications(row["script_id"])} == {"kuaishou", "xiaohongshu"}
    assert {record["work_id"] for record in made} == {shared_id}


def test_platform_account_and_version_drafts_remain_independent(social_env):
    old = render()
    for platform in LINKS:
        distributions.save_draft(old["id"], platform, social_env[platform]["uid"], {
            "title": f"{social_env[platform]['name']}发布包", "description": platform, "tags": ["测试"],
        })
    newer = next_render(old)
    platform = "xiaohongshu"
    owner = social_env[platform]["uid"]
    distributions.save_draft(newer["id"], platform, owner, {"title": "新版小红书发布包", "description": "新版", "tags": []})
    assert distributions.preview_draft(newer, platform)["draft"]["payload"]["title"] == "新版小红书发布包"
    for target in LINKS:
        saved = distributions.preview_draft(old, target)["draft"]
        assert saved["payload"]["description"] == target
        assert saved["render_id"] == old["id"]
    social_env[platform] = {"connected": True, "uid": "abcdef112233445566778899", "name": "另一个小红书账号"}
    assert distributions.preview_draft(old, platform)["draft"] is None
    with pytest.raises(ValueError, match="账号已改变"):
        distributions.save_draft(old["id"], platform, owner, {"title": "过期页面标题", "description": "", "tags": []})
    assert store.distribution_draft(old, platform, owner)["payload"]["description"] == platform


def test_platform_key_is_part_of_target_even_when_uids_are_identical(social_env):
    row = render()
    for platform in LINKS:
        social_env[platform]["uid"] = "same-nonnumeric-uid"
        distributions.save_draft(row["id"], platform, "same-nonnumeric-uid", {"title": "平台独立发布包", "description": platform, "tags": []})
    store.add_publication(row["script_id"], version=1, platform="kuaishou", account_uid="same-nonnumeric-uid", url=LINKS["kuaishou"], published_on="2026-10-24")
    targets = distributions.overview([store.get_script(row["script_id"])])[row["script_id"]]
    current = {target["platform"]: target for target in targets if target["current_account"] and target["platform"] in LINKS}
    assert current["kuaishou"]["status"] == "PUBLISHED" and not current["kuaishou"]["can_prepare"]
    for platform in ("xiaohongshu", "weixin_channels"):
        assert current[platform]["status"] == "DRAFT" and current[platform]["can_prepare"]
        assert distributions.preview_draft(row, platform)["draft"]["payload"]["description"] == platform


def test_bili_and_douyin_published_video_still_enters_all_new_platform_queues(social_env):
    row = render()
    for platform, owner, url in (
        ("bilibili", "123", f"https://www.bilibili.com/video/{BV}"),
        ("douyin", "456", f"https://www.douyin.com/video/{DOUYIN_WORK}"),
    ):
        store.add_publication(row["script_id"], version=1, platform=platform, account_uid=owner, url=url, published_on="2026-10-24")
    tasks = {}
    for platform in LINKS:
        assert [item["render_id"] for item in social.candidates(platform)] == [row["id"]]
        tasks[platform] = social.enqueue(platform, row["id"], {
            "account_uid": social_env[platform]["uid"], "title": "平台独立发布包", "description": "单视频跨平台", "tags": ["AI"],
        })
        assert tasks[platform]["status"] == "QUEUED"
        assert "video_path" not in json.dumps(tasks[platform])
        assert social.candidates(platform) == []
    assert len({task["id"] for task in tasks.values()}) == 3
    assert len(store.list_publications(row["script_id"])) == 2  # Enqueue is never a publication.
    targets = distributions.overview([store.get_script(row["script_id"])])[row["script_id"]]
    states = {target["platform"]: target["status"] for target in targets if target["current_account"]}
    assert states == {"bilibili": "PUBLISHED", "douyin": "PUBLISHED", **{platform: "QUEUED" for platform in LINKS}}


def test_publication_duplicate_guard_applies_only_to_the_same_platform_account_version(social_env):
    old = render()
    owner = social_env["kuaishou"]["uid"]
    store.add_publication(old["script_id"], version=1, platform="kuaishou", account_uid=owner, url=LINKS["kuaishou"], published_on="2026-10-24")
    with pytest.raises(AdapterError, match="已有发布记录"):
        social.enqueue("kuaishou", old["id"], {"account_uid": owner, "title": "平台独立发布包", "description": "", "tags": []})
    newer = next_render(old)
    assert social.enqueue("kuaishou", newer["id"], {"account_uid": owner, "title": "平台新版发布包", "description": "", "tags": []})["status"] == "QUEUED"
    social_env["kuaishou"] = {"connected": True, "uid": "other-ks-owner", "name": "新快手账号"}
    assert social.enqueue("kuaishou", old["id"], {"account_uid": "other-ks-owner", "title": "另一个账号发布包", "description": "", "tags": []})["status"] == "QUEUED"


def test_draft_api_reloads_platform_specific_account_target(social_env, client):
    row = render()
    _login(client)
    for platform in LINKS:
        response = client.put(f"/api/video/distributions/{platform}/draft", headers=_csrf(client), json={
            "render_id": row["id"], "account_uid": social_env[platform]["uid"],
            "payload": {"title": "平台独立发布包", "description": platform, "tags": []},
        })
        assert response.status_code == 200, response.text
    response = client.get(f"/api/video/scripts/{row['script_id']}")
    assert response.status_code == 200
    targets = {target["platform"]: target for target in response.json()["distributions"] if target["current_account"]}
    for platform in LINKS:
        assert targets[platform]["status"] == "DRAFT" and targets[platform]["account_uid"] == social_env[platform]["uid"]
        assert social.store(platform).list(kind="UPLOAD") == []


@pytest.mark.parametrize("platform", LINKS)
def test_new_platform_drafts_reject_credentials_and_file_paths(social_env, platform):
    row = render()
    for extra in ({"video_path": "/private/secret.mp4"}, {"storage_state": "/private/account.json"}, {"cookie": "secret"}):
        with pytest.raises(AdapterError, match="发布包字段或长度不正确"):
            distributions.save_draft(row["id"], platform, social_env[platform]["uid"], {"title": "平台独立发布包", "description": "", "tags": [], **extra})
    assert distributions.preview_draft(row, platform)["draft"] is None
