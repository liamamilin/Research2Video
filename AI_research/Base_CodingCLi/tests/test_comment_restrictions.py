"""流量受影响 from a promotion in the comments: detect, delete the owner's link comments, pin a guide."""

import pytest

from core.platform_store import PlatformStore
from web.services import bilibili, comment_restrictions as cr, platform_manager as pm

OWNER = "382769913"
PROMO = {"reason": "您的稿件含有商业推广信息，且投稿时没有进行商业声明", "title": "商业推广信息未录入", "position": "评论区"}


def archive(bvid, *, limited=True, problems=(PROMO,)):
    return {"bvid": bvid, "title": f"title {bvid}", "limit_state": limited, "problems": list(problems)}


class Fake:
    def __init__(self, comments=(), *, closed=False, post_error=None):
        self.comments = list(comments)
        self.closed, self.post_error = closed, post_error
        self.deleted, self.posted, self.pinned = [], [], []
        self.listing = []

    def archives(self, pages):
        return {"archives": self.listing, "complete": True}

    def owned_video(self, bvid, uid):
        return {"aid": 1, "bvid": bvid}

    def own_comments(self, info, mid):
        if self.closed:
            raise bilibili.BilibiliRejected("closed", code=12002)
        return [c for c in self.comments if c["rpid"] not in self.deleted]

    def delete_comment(self, info, rpid):
        self.deleted.append(rpid)

    def post_comment(self, info, text):
        if self.post_error:
            raise self.post_error
        self.posted.append(text)
        return "900"

    def pin_comment(self, info, rpid):
        self.pinned.append(rpid)


@pytest.fixture
def env(tmp_path, monkeypatch):
    db = PlatformStore(str(tmp_path))
    monkeypatch.setattr(pm, "store", lambda: db)
    monkeypatch.setattr(cr, "_last_scan", 0.0)
    fake = Fake([{"rpid": "1", "message": "订阅GPT，https://prodclub.xyz/r/PBU6W6 ，支持支付宝"},
                 {"rpid": "2", "message": "看我主页 https://space.bilibili.com/382769913"},
                 {"rpid": "3", "message": "感谢观看"}])
    monkeypatch.setattr(pm, "client", lambda expected_uid="": fake)
    return db, fake


def guide(db, enabled=True):
    db.save_settings({"restricted_comment": {"enabled": enabled, "pin": True,
                                             "text": "私信我发「GPT」 https://message.bilibili.com/#/whisper/mid382769913"}})


def test_only_a_comment_promotion_limit_counts():
    assert cr.promotion_limited(archive("BV1"))
    assert not cr.promotion_limited(archive("BV1", limited=False))  # open_appeal alone is not a limit
    assert not cr.promotion_limited(archive("BV1", problems=[{**PROMO, "position": "视频"}]))
    assert not cr.promotion_limited(archive("BV1", problems=[{"reason": "画面不适", "title": "", "position": "评论区"}]))


def test_outside_links_exclude_bilibili_hosts():
    assert cr.outside_link("买这里 https://prodclub.xyz/r/x")
    assert not cr.outside_link("https://message.bilibili.com/#/whisper/mid1 或 https://b23.tv/abc")
    assert not cr.outside_link("没有链接")


def test_swap_deletes_only_outside_links_then_pins_guide(env):
    db, fake = env
    guide(db)
    fake.listing = [archive("BV1lim"), archive("BV1ok", limited=False)]
    cr.tick(OWNER)
    assert fake.deleted == ["1"] and fake.posted and fake.pinned == ["900"]
    [row] = cr.restrictions(OWNER)
    assert row["bvid"] == "BV1lim" and row["status"] == "SWAPPED" and row["guide_rpid"] == "900"
    assert [c["rpid"] for c in row["deleted"]] == ["1"]
    cr.tick(OWNER, force=True)  # rescanning the same limit does not act twice
    assert fake.posted == [fake.posted[0]] and fake.deleted == ["1"]


def test_guide_disabled_only_cleans(env):
    db, fake = env
    guide(db, enabled=False)
    fake.listing = [archive("BV1lim")]
    cr.tick(OWNER)
    assert fake.deleted == ["1"] and not fake.posted
    assert cr.restrictions(OWNER)[0]["status"] == "CLEANED"


def test_closed_comments_are_recorded(env):
    db, fake = env
    fake.closed = True
    fake.listing = [archive("BV1lim")]
    cr.tick(OWNER)
    assert cr.restrictions(OWNER)[0]["status"] == "CLOSED" and not fake.deleted


def test_uncertain_guide_post_is_never_retried(env):
    db, fake = env
    guide(db)
    fake.post_error = RuntimeError("timeout")
    fake.listing = [archive("BV1lim")]
    cr.tick(OWNER)
    assert cr.restrictions(OWNER)[0]["status"] == "UNKNOWN"
    fake.post_error = None
    cr.tick(OWNER, force=True)
    assert not fake.posted


def test_lifted_limit_is_marked_inactive(env):
    db, fake = env
    fake.listing = [archive("BV1lim")]
    cr.tick(OWNER)
    fake.listing = [archive("BV1lim", limited=False)]
    cr.tick(OWNER, force=True)
    assert not cr.restrictions(OWNER)[0]["active"]


def test_guide_with_outside_link_is_refused():
    with pytest.raises(bilibili.BilibiliError):
        cr.clean_settings({"enabled": True, "text": "https://prodclub.xyz/r/x"})
    assert cr.clean_settings({"enabled": True, "text": "私信我"})["pin"] is True
