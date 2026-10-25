"""Offline video-channel contract tests; no provider accounts or posting."""
import asyncio
import stat
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from core.social.adapter import AdapterError
from core.social.adapters import weixin_channels as channels


OWNER = {"uid": "v2_0123456789abcdef@finder", "name": "测试视频号"}
AUTH = channels.BASE + channels.AUTH_PATH
FINAL = channels.BASE + "/micro/content/cgi-bin/mmfinderassistant-bin/post/post_create"


def identity_data(**extra):
    return {"errCode": 0, "data": {"finderUser": {
        "finderUsername": OWNER["uid"], "nickname": OWNER["name"], **extra,
    }, "userAttr": {"username": "admin-secret", "nickname": "管理员"}, "token": "secret"}}


def test_identity_uses_publishing_channel_not_administrator_or_finder_list():
    assert channels._parse_identity(identity_data(sessionid="secret", uniqId="editable-handle")) == OWNER
    for alternate in (
        {"userAttr": {"username": OWNER["uid"], "nickname": OWNER["name"]}},
        {"finderList": [{"finderUsername": OWNER["uid"], "nickname": OWNER["name"]}]},
    ):
        with pytest.raises(AdapterError):
            channels._parse_identity({"errCode": 0, "data": alternate})


@pytest.mark.parametrize("payload", [
    None, [], {}, {"errCode": False}, {"errCode": "0"}, {"errCode": 0.0},
    {"errCode": 1, "data": identity_data()["data"]}, {"errCode": 0, "data": []},
    {"errCode": 0, "data": {"finderUser": []}},
    identity_data(finderUsername=123), identity_data(finderUsername=""),
    identity_data(finderUsername="a\nsecret"), identity_data(nickname=None),
    identity_data(nickname=" "), identity_data(nickname="x\nsecret"),
])
def test_identity_rejects_inexact_or_missing_identity(payload):
    with pytest.raises(AdapterError):
        channels._parse_identity(payload)


class Response:
    def __init__(self, url=AUTH, data=None, *, status=200, method="POST"):
        self.url, self.data, self.status = url, data, status
        self.request = SimpleNamespace(method=method)
        self.finished_count = 0

    async def json(self):
        if isinstance(self.data, Exception):
            raise self.data
        return self.data

    async def finished(self):
        self.finished_count += 1


class IdentityPage:
    def __init__(self, responses=(), failure=False):
        self.responses, self.failure = responses, failure
        self.listeners = {}
        self.closed = False
        self.url = "about:blank"

    def on(self, name, callback):
        self.listeners[name] = callback

    def remove_listener(self, name, callback):
        assert self.listeners.pop(name) is callback

    async def goto(self, url, **options):
        self.url = url
        if self.failure:
            raise RuntimeError("network secret")
        for response in self.responses:
            self.listeners["response"](response)

    async def close(self):
        self.closed = True


class Context:
    def __init__(self, page):
        self.page = page

    async def new_page(self):
        return self.page


def test_identity_observes_exact_provider_response_and_closes_temporary_page():
    page = IdentityPage([
        Response("https://channels.weixin.qq.com.evil.test" + channels.AUTH_PATH, identity_data()),
        Response(data={"errCode": 0, "data": {"userAttr": {"username": "secret"}}}),
        Response(data=identity_data()),
    ])
    assert asyncio.run(channels.WeixinChannelsAdapter().identity(Context(page))) == OWNER
    assert page.closed and not page.listeners
    assert page.url == channels.BASE + "/platform"


@pytest.mark.parametrize("responses", [
    [], [Response(data=identity_data(), status=401)],
    [Response(data=ValueError("cookie secret"))],
    [Response(url=AUTH + "/unexpected", data=identity_data())],
    [Response(data={"errCode": 0, "data": {"sessionid": "secret"}})],
])
def test_identity_never_uses_cookie_or_redirect_as_login_evidence(monkeypatch, responses):
    monkeypatch.setattr(channels, "IDENTITY_TIMEOUT", 0.001)
    page = IdentityPage(responses)
    with pytest.raises(AdapterError) as caught:
        asyncio.run(channels.WeixinChannelsAdapter().identity(Context(page)))
    assert "secret" not in str(caught.value)
    assert page.closed and not page.listeners


def test_identity_navigation_failure_is_sanitized_and_cleans_up():
    page = IdentityPage(failure=True)
    with pytest.raises(AdapterError) as caught:
        asyncio.run(channels.WeixinChannelsAdapter().identity(Context(page)))
    assert "secret" not in str(caught.value)
    assert page.closed and not page.listeners


def test_prepare_login_returns_real_official_login_page_for_worker_polling():
    page = IdentityPage()
    result = asyncio.run(channels.WeixinChannelsAdapter().prepare_login(Context(page)))
    assert result is page and page.url == channels.BASE and not page.closed


def test_prepare_login_closes_failed_page():
    page = IdentityPage(failure=True)
    with pytest.raises(AdapterError):
        asyncio.run(channels.WeixinChannelsAdapter().prepare_login(Context(page)))
    assert page.closed


class QR:
    def __init__(self, count=1, visible=True):
        self.number, self.visible = count, visible
        self.captures = 0

    async def count(self):
        return self.number

    async def is_visible(self):
        return self.visible

    async def screenshot(self, *, path, **kwargs):
        self.captures += 1
        Path(path).write_bytes(b"fake-isolated-qr")


class QRPage:
    def __init__(self, frame_url, qr, url=channels.BASE + "/platform"):
        self.url = url
        self.frames = [SimpleNamespace(url=frame_url, locator=lambda selector: qr)]
        self.qr = qr

    def locator(self, selector):
        assert "login-qrcode-wrap" in selector  # No generic img/data-image fallback.
        return self.qr


@pytest.mark.parametrize("url", [
    "https://open.weixin.qq.com/connect/qrconnect?appid=public-app-id",
    channels.BASE + "/platform/login-for-iframe?dark_mode=true",
])
def test_qr_captures_only_visible_official_login_element(tmp_path, url):
    qr = QR()
    destination = tmp_path / "qr" / "login.png"
    assert asyncio.run(channels.WeixinChannelsAdapter().capture_qr(QRPage(url, qr), destination))
    assert qr.captures == 1 and destination.read_bytes() == b"fake-isolated-qr"
    assert stat.S_IMODE(destination.stat().st_mode) == 0o600


@pytest.mark.parametrize("url", [
    "https://open.weixin.qq.com.evil.test/connect/qrconnect",
    "http://open.weixin.qq.com/connect/qrconnect",
    "https://open.weixin.qq.com:444/connect/qrconnect",
    "https://open.weixin.qq.com/not-login",
    "https://channels.weixin.qq.com/platform/post/list",
    "https://evil.test/platform/login-for-iframe",
])
def test_qr_refuses_account_images_and_untrusted_frames(tmp_path, url):
    qr = QR()
    assert not asyncio.run(channels.WeixinChannelsAdapter().capture_qr(QRPage(url, qr), tmp_path / "qr.png"))
    assert qr.captures == 0


@pytest.mark.parametrize("qr", [QR(count=0), QR(count=2), QR(visible=False)])
def test_qr_does_not_capture_hidden_or_ambiguous_elements(tmp_path, qr):
    assert not asyncio.run(channels.WeixinChannelsAdapter().capture_qr(
        QRPage("https://open.weixin.qq.com/connect/qrconnect", qr), tmp_path / "qr.png"))


@pytest.mark.parametrize("path", channels.SUBMIT_PATHS)
def test_submit_matches_only_exact_final_mutation(path):
    adapter = channels.WeixinChannelsAdapter()
    assert adapter.is_submit(channels.BASE + path)
    assert adapter.is_submit(channels.BASE + path + "?_aid=public-id")


@pytest.mark.parametrize("url", [
    "http://channels.weixin.qq.com/cgi-bin/mmfinderassistant-bin/post/post_create",
    "https://channels.weixin.qq.com.evil.test/cgi-bin/mmfinderassistant-bin/post/post_create",
    "https://channels.weixin.qq.com:444/cgi-bin/mmfinderassistant-bin/post/post_create",
    "https://user:secret@channels.weixin.qq.com/cgi-bin/mmfinderassistant-bin/post/post_create",
    FINAL + "/", FINAL + "#fake", "https://finderassistancea.video.qq.com/completepartuploaddfs",
    AUTH, channels.BASE + "/platform/post/create", "not-a-url",
])
def test_submit_guard_never_matches_upload_login_ui_or_other_origins(url):
    assert not channels.WeixinChannelsAdapter().is_submit(url)


@pytest.mark.parametrize("data", [
    None, [], {}, {"errCode": False}, {"errCode": "0"}, {"errCode": 0.0},
    {"errCode": 0}, {"errCode": 0, "data": {"baseResp": {"errcode": 0}}},
    {"errCode": 0, "exportId": "17123456789012345678"},
    {"errCode": 0, "data": {"id": 17123456789012345678}},
    {"errCode": -1, "errMsg": "secret raw response"},
    {"errCode": 0, "data": {"baseResp": {"errcode": -1, "errmsg": "secret"}}},
])
def test_receipt_never_accepts_idless_success_or_guessed_work_ids(data):
    with pytest.raises(AdapterError) as caught:
        channels.WeixinChannelsAdapter().submission_receipt(data)
    assert "secret" not in str(caught.value)


@pytest.mark.parametrize("data", [
    {"errCode": 300002, "errMsg": "secret"},
    {"errCode": 0, "data": {"baseResp": {"errcode": 300002, "errmsg": "secret"}}},
])
def test_only_documented_validation_rejection_confirms_no_submission(data):
    assert channels.WeixinChannelsAdapter().submission_receipt(data) == {
        "accepted": False, "confirmed_not_submitted": True, "code": "300002",
    }


class PendingResponse:
    def __init__(self, response):
        self.value = self._value(response)

    async def _value(self, response):
        return response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        # Consume the coroutine if click failed, matching Playwright's cleanup.
        if args[0] is not None:
            self.value.close()


class Button(QR):
    def __init__(self, *, failure=False, disabled=False, **kwargs):
        super().__init__(**kwargs)
        self.failure, self.disabled = failure, disabled
        self.clicks = 0

    async def is_disabled(self):
        return self.disabled

    async def click(self, **options):
        self.clicks += 1
        if self.failure:
            raise TimeoutError("uncertain provider response")


class SubmitPage:
    def __init__(self, button):
        self.button = button
        self.response = Response(FINAL)

    def get_by_role(self, role, *, name, exact):
        assert (role, name, exact) == ("button", "发表", True)
        return self.button

    def expect_response(self, predicate, **kwargs):
        assert predicate(self.response)
        assert not predicate(Response(FINAL, method="GET"))
        assert not predicate(Response(AUTH))
        return PendingResponse(self.response)


def test_publish_click_is_exactly_once_and_waits_for_final_network_response():
    button = Button()
    page = SubmitPage(button)
    asyncio.run(channels._submit_once(page))
    assert button.clicks == 1 and page.response.finished_count == 1


def test_uncertain_click_failure_does_not_click_again_or_use_evaluate_fallback():
    button = Button(failure=True)
    with pytest.raises(TimeoutError):
        asyncio.run(channels._submit_once(SubmitPage(button)))
    assert button.clicks == 1


@pytest.mark.parametrize("button", [Button(count=0), Button(count=2), Button(visible=False), Button(disabled=True)])
def test_missing_ambiguous_or_disabled_publish_button_never_clicks(button):
    with pytest.raises(AdapterError):
        asyncio.run(channels._submit_once(SubmitPage(button)))
    assert button.clicks == 0


def test_upload_uses_injected_playwright_and_instance_overrides_only(tmp_path, monkeypatch):
    storage = tmp_path / "isolated-storage.json"
    storage.write_text("{}")
    guarded = object()
    events = []
    button = Button()
    class Field:
        async def count(self):
            return 1

        async def fill(self, value):
            events.append(("title", value))

    class Page(SubmitPage):
        def locator(self, selector):
            assert "填写短标题" in selector
            return Field()

    class UpstreamVideo:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)
            events.append(("args", kwargs))

        async def validate_base_args(self):
            raise AssertionError("original cookie_auth would launch an unguarded browser")

        async def submit_publish(self, page):
            raise AssertionError("original submit would retry up to 20 times")

        async def upload(self, playwright):
            assert playwright is guarded
            await self.validate_base_args()
            await self.set_short_title(Page(button), self.title)
            await self.submit_publish(Page(button))

    module = ModuleType("uploader.tencent_uploader.main")
    module.TencentVideo = UpstreamVideo
    original_validation = UpstreamVideo.validate_base_args
    original_submit = UpstreamVideo.submit_publish
    monkeypatch.setitem(sys.modules, module.__name__, module)
    asyncio.run(channels.WeixinChannelsAdapter().upload(guarded, {
        "title": "标题测试完整保留", "description": "简介", "tags": ["AI"],
        "video_path": "/isolated/video.mp4", "cover": "/isolated/cover.png",
    }, storage))
    assert button.clicks == 1
    assert events[0][1]["headless"] is True
    assert events[0][1]["publish_date"] == 0
    assert events[0][1]["is_draft"] is False
    assert events[0][1]["thumbnail_path"] == "/isolated/cover.png"
    assert ("title", "标题测试完整保留") in events
    assert UpstreamVideo.validate_base_args is original_validation
    assert UpstreamVideo.submit_publish is original_submit


@pytest.mark.parametrize("payload", [
    {"title": "短"}, {"title": "超" * 17}, {"title": 123},
    {"title": "视频号标题测试", "description": []},
    {"title": "视频号标题测试", "tags": "AI"},
    {"title": "视频号标题测试", "tags": [" "]},
    {"title": "视频号标题测试", "tags": [123]},
    {"title": "视频号标题测试", "tags": ["标签"] * 11},
    {"title": "视频号标题测试", "description": "长" * 999},
])
def test_invalid_package_stops_before_importing_or_opening_uploader(payload, monkeypatch, tmp_path):
    def forbidden(name):
        raise AssertionError("invalid payload reached provider")
    monkeypatch.setattr(channels.importlib, "import_module", forbidden)
    with pytest.raises(AdapterError):
        channels.WeixinChannelsAdapter().validate_payload(payload)
    with pytest.raises(AdapterError):
        asyncio.run(channels.WeixinChannelsAdapter().upload(object(), payload, tmp_path / "never-read.json"))


def test_prequeue_validation_counts_exact_upstream_text_format():
    adapter = channels.WeixinChannelsAdapter()
    assert adapter.title_min == 6 and adapter.combined_limit == 1000
    payload = {"title": "标题需要六个字", "description": "长" * 987, "tags": ["AI"]}
    # Seven title characters + two Enters + '#AI ' + 987 description = 1000.
    adapter.validate_payload(payload)
    with pytest.raises(AdapterError, match="1000"):
        adapter.validate_payload({**payload, "description": payload["description"] + "长"})


def test_public_check_remains_explicitly_unsupported_and_does_not_open_browser(tmp_path):
    adapter = channels.WeixinChannelsAdapter()
    assert not adapter.supports_public_check
    work_id = "17123456789012345678"
    result = asyncio.run(adapter.inspect_work(object(), tmp_path / "never-read.json", OWNER["uid"], work_id))
    assert result["work_id"] == work_id
    assert result["uid"] == OWNER["uid"]
    assert result["public"] is False and result["owned"] is False
    assert "token" not in result and "url" not in result


def test_missing_upstream_has_actionable_safe_error(tmp_path, monkeypatch):
    def absent(name):
        raise ImportError("private /path/to/storage secret")
    monkeypatch.setattr(channels.importlib, "import_module", absent)
    with pytest.raises(AdapterError) as caught:
        asyncio.run(channels.WeixinChannelsAdapter().upload(object(), {
            "title": "视频号标题测试", "description": "简介", "tags": [],
        }, tmp_path / "never-read.json"))
    assert "未安装" in str(caught.value) and "secret" not in str(caught.value)


@pytest.mark.parametrize("value", [None, 17123456789012345678, "", "  "])
def test_inspection_requires_string_work_id_without_numeric_rounding(tmp_path, value):
    with pytest.raises(AdapterError):
        asyncio.run(channels.WeixinChannelsAdapter().inspect_work(
            object(), tmp_path / "never-read.json", OWNER["uid"], value))
